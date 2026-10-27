"""Check GitHub for a newer release than the running build."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

from . import REPO, __version__

log = logging.getLogger(__name__)

API = "https://api.github.com/repos/" + REPO.removeprefix("https://github.com/") + "/releases/latest"
TTL = 6 * 3600  # GitHub allows 60 unauthenticated calls an hour; one every few hours is plenty
RETRY = 15 * 60  # after a failed check (offline, rate limited) try again sooner

# The release asset that installs on this OS; the phone app is not offered here.
_ASSET = {"win32": "Prismix-Setup.exe", "darwin": "Prismix.dmg"}

_cache: dict = {"at": 0.0, "ttl": TTL, "data": None}


def parse(v: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", v.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def newer(latest: str, current: str) -> bool:
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def _summarize(release: dict) -> dict:
    tag = release.get("tag_name", "")
    want = _ASSET.get(sys.platform)
    asset = next((a.get("browser_download_url") for a in release.get("assets", []) if a.get("name") == want), None)
    return {
        "available": newer(tag, __version__),
        "latest": tag.lstrip("v"),
        "url": release.get("html_url") or f"{REPO}/releases",
        "download": asset,
    }


def check(*, force: bool = False, client: httpx.Client | None = None) -> dict:
    """The latest published release and whether it is newer than this build. Cached; never raises."""
    now = time.monotonic()
    if not force and _cache["data"] is not None and now - _cache["at"] < _cache["ttl"]:
        return _cache["data"]
    try:
        with client or httpx.Client(timeout=8, headers={"Accept": "application/vnd.github+json", "User-Agent": "prismix"}) as c:
            r = c.get(API)
            r.raise_for_status()
            data = _summarize(r.json())
        ttl = TTL
    except (httpx.HTTPError, ValueError) as e:
        log.info("update check failed: %s", e)
        data = _cache["data"] or {"available": False, "latest": None, "url": f"{REPO}/releases", "download": None}
        ttl = RETRY
    _cache.update(at=now, ttl=ttl, data=data)
    return data


# ── installing ──
_ALLOWED = ("https://github.com/" + REPO.removeprefix("https://github.com/") + "/releases/download/",)
_job: dict = {"state": "idle", "progress": 0.0, "error": None}


def installable() -> bool:
    """Only a packaged desktop build can replace itself; a source checkout is updated with git."""
    return bool(getattr(sys, "frozen", False)) and sys.platform in _ASSET


def status() -> dict:
    return {**_job, "installable": installable()}


def _download(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=30, headers={"User-Agent": "prismix"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                done += len(chunk)
                _job["progress"] = done / total if total else 0.0


def _bundle() -> Path:
    """Prismix.app that is running (…/Prismix.app/Contents/MacOS/Prismix)."""
    return Path(sys.executable).resolve().parents[2]


def _hand_over(path: Path) -> None:
    """Start the platform installer detached; it waits for this process to exit, then swaps the app and reopens it."""
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                         creationflags=flags, close_fds=True)
        return
    app = _bundle()
    if not os.access(app.parent, os.W_OK):
        raise OSError(f"No permission to replace {app}. Open the download and drag it to Applications.")
    script = path.with_name("update.sh")
    script.write_text(f"""#!/bin/bash
while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done
MNT=$(mktemp -d)
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$1" >/dev/null || exit 1
if [ -d "$MNT/Prismix.app" ]; then
  rm -rf "$2.new" && ditto "$MNT/Prismix.app" "$2.new" && xattr -dr com.apple.quarantine "$2.new" 2>/dev/null
  rm -rf "$2" && mv "$2.new" "$2"
fi
hdiutil detach "$MNT" >/dev/null 2>&1
open "$2"
""")
    subprocess.Popen(["/bin/bash", str(script), str(path), str(app)], start_new_session=True, close_fds=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _run(url: str, quit_app) -> None:
    try:
        _job.update(state="downloading", progress=0.0, error=None)
        d = Path(tempfile.mkdtemp(prefix="prismix-update-"))
        dest = d / url.rsplit("/", 1)[-1]
        _download(url, dest)
        _job["state"] = "installing"
        _hand_over(dest)
        _job.update(state="restarting", progress=1.0)
        time.sleep(1.5)  # let the page see "restarting" before the window goes away
        quit_app()
    except Exception as e:  # noqa: BLE001 - shown to the user; the manual download link still works
        log.exception("update install failed")
        _job.update(state="error", error=str(e) or e.__class__.__name__)


def install(quit_app) -> dict:
    """Download the newest installer from the project's own releases and replace the running app with it."""
    if not installable():
        raise RuntimeError("Updating in place works in the installed app only")
    if _job["state"] in ("downloading", "installing", "restarting"):
        return status()
    info = check(force=True)
    url = info.get("download")
    if not info.get("available") or not url or not url.startswith(_ALLOWED):
        raise RuntimeError("No newer release to install")
    threading.Thread(target=_run, args=(url, quit_app), daemon=True).start()
    _job.update(state="downloading", progress=0.0, error=None)
    return status()
