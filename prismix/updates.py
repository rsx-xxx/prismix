"""Check GitHub for a newer release than the running build."""
from __future__ import annotations

import contextlib
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

API = "https://api.github.com/repos/" + REPO.removeprefix("https://github.com/") + "/releases?per_page=30"
TTL = 6 * 3600  # GitHub allows 60 unauthenticated calls an hour; one every few hours is plenty
RETRY = 15 * 60  # after a failed check (offline, rate limited) try again sooner

# The release asset that installs on this OS; the phone app is not offered here.
CASK = "prismix"
_ASSET = {"win32": "Prismix-Setup.exe", "darwin": "Prismix.dmg"}

_cache: dict = {"at": 0.0, "ttl": TTL, "data": None}


def parse(v: str) -> tuple[int, ...] | None:
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", v.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def newer(latest: str, current: str) -> bool:
    a, b = parse(latest), parse(current)
    return bool(a and b and a > b)


def _pick(releases: list) -> dict:
    """Highest versioned release. GitHub's "latest" flag is unusable: each master build moves it to the rolling 'latest' tag."""
    versioned = [r for r in releases if not r.get("draft") and not r.get("prerelease") and parse(r.get("tag_name", ""))]
    return max(versioned, key=lambda r: parse(r["tag_name"]), default={})


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
            data = _summarize(_pick(r.json()))
        ttl = TTL
    except (httpx.HTTPError, ValueError) as e:
        log.info("update check failed: %s", e)
        data = _cache["data"] or {"available": False, "latest": None, "url": f"{REPO}/releases", "download": None}
        ttl = RETRY
    _cache.update(at=now, ttl=ttl, data=data)
    return data


# ── installing ──
_ALLOWED = ("https://github.com/" + REPO.removeprefix("https://github.com/") + "/releases/download/",)
_job: dict = {"state": "idle", "progress": 0.0, "percent": 0.0, "message": "", "error": None, "ready": None}
_staged: dict = {"path": None, "version": None}
_lock = threading.Lock()
BUSY_POLL = 30  # while work is running, look again this often before offering the restart


def installable() -> bool:
    """Only a packaged desktop build can replace itself; a source checkout is updated with git."""
    return bool(getattr(sys, "frozen", False)) and sys.platform in _ASSET


def status() -> dict:
    return {**_job, "installable": installable()}


def _step(percent: float, message: str, **state) -> None:
    """Overall progress (0-1) and a short note on what is happening, for the settings panel."""
    _job.update(percent=round(percent, 3), message=message, **state)


def _download(url: str, dest: Path) -> None:
    """Fetch the installer; counts for the first 80% of the whole update."""
    with httpx.stream("GET", url, follow_redirects=True, timeout=30, headers={"User-Agent": "prismix"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        began = last = time.monotonic()
        with dest.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                done += len(chunk)
                _job["progress"] = done / total if total else 0.0
                now = time.monotonic()
                if now - last >= 0.25:
                    last = now
                    rate = done / max(now - began, 0.001) / 1e6
                    size = f"{done / 1e6:.0f} of {total / 1e6:.0f} MB" if total else f"{done / 1e6:.0f} MB"
                    _step(0.8 * _job["progress"], f"Downloading {size} · {rate:.1f} MB/s")


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


_BREW = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")


def _brew() -> str | None:
    return next((b for b in _BREW if os.access(b, os.X_OK)), None)


def brew_managed() -> bool:
    """True when Homebrew installed this copy: its cask record exists, so brew's bookkeeping must be what updates it."""
    brew = _brew()
    return bool(brew and sys.platform == "darwin" and any((Path(brew).parents[1] / "Caskroom" / CASK).glob("*")))


def _brew_upgrade() -> bool:
    """`brew update` then upgrade this one cask (never other packages). One retry when another brew holds the lock."""
    env = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", "HOMEBREW_NO_ENV_HINTS": "1", "HOMEBREW_NO_INSTALL_CLEANUP": "1"}
    for attempt in (1, 2):
        out = ""
        for cmd in (["update"], ["upgrade", "--cask", CASK, "--greedy"]):
            _step(0.3 if cmd[0] == "update" else 0.65, "Refreshing Homebrew" if cmd[0] == "update" else "Upgrading Prismix with Homebrew")
            r = subprocess.run([_brew(), *cmd], env=env, capture_output=True, text=True, timeout=900)
            out = r.stdout + r.stderr
            if r.returncode != 0:
                break
        else:
            return True
        log.warning("brew attempt %d failed: %s", attempt, out[-400:])
        if attempt == 1 and "lock" in out.lower():
            time.sleep(8)
            continue
        return False
    return False


def _reopen_after_exit() -> None:
    subprocess.Popen(["/bin/bash", "-c", f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.3; done; open {_bundle()!s}"],
                     start_new_session=True, close_fds=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _run(url: str, quit_app) -> None:
    try:
        if brew_managed():
            _step(0.1, "Starting Homebrew", state="installing", progress=1.0, error=None)
            if _brew_upgrade():
                _step(0.95, "Restarting Prismix", state="restarting", progress=1.0)
                _reopen_after_exit()
                time.sleep(1.5)
                quit_app()
                return
            log.warning("brew could not update Prismix; installing from the release instead")
        _step(0.02, "Connecting to GitHub", state="downloading", progress=0.0, error=None)
        d = Path(tempfile.mkdtemp(prefix="prismix-update-"))
        dest = d / url.rsplit("/", 1)[-1]
        _download(url, dest)
        _step(0.85, "Preparing the installer", state="installing")
        _hand_over(dest)
        _step(0.95, "Restarting Prismix", state="restarting", progress=1.0)
        time.sleep(1.5)  # let the page see "restarting" before the window goes away
        quit_app()
    except Exception as e:  # noqa: BLE001 - shown to the user; the manual download link still works
        log.exception("update install failed")
        _job.update(state="error", error=str(e) or e.__class__.__name__)


def _guard(busy) -> None:
    why = busy()
    if why:
        raise RuntimeError(f"Prismix is busy ({why}). Restart to update when it finishes.")


def install(quit_app, busy=lambda: None) -> dict:
    """Update the running app now: through Homebrew when brew installed it, else from the project's own releases."""
    if not installable():
        raise RuntimeError("Updating in place works in the installed app only")
    _guard(busy)
    if _staged["path"]:
        return restart(quit_app, busy)
    if _job["state"] in ("downloading", "installing", "restarting"):
        return status()
    info = check(force=True)
    url = info.get("download")
    if not info.get("available") or not url or not url.startswith(_ALLOWED):
        raise RuntimeError("No newer release to install")
    threading.Thread(target=_run, args=(url, quit_app), daemon=True).start()
    _step(0.0, "Starting", state="downloading", progress=0.0, error=None)
    return status()


def stage(info: dict) -> bool:
    """Download the newer release in the background so installing it later is instant. True when it is ready."""
    url = info.get("download")
    if not info.get("available") or not url or not url.startswith(_ALLOWED) or not installable():
        return False
    with _lock:
        if _staged["version"] == info["latest"] and _staged["path"] and Path(_staged["path"]).is_file():
            return True
        if _job["state"] in ("downloading", "installing", "restarting"):
            return False
        try:
            if brew_managed():  # brew fetches and swaps in one step, nothing to pre-download
                _staged.update(path="brew", version=info["latest"])
            else:
                _step(0.0, "Starting", state="downloading", progress=0.0, error=None)
                dest = Path(tempfile.mkdtemp(prefix="prismix-update-")) / url.rsplit("/", 1)[-1]
                _download(url, dest)
                _staged.update(path=str(dest), version=info["latest"])
            _step(0.8, "Downloaded, ready to restart", state="ready", progress=1.0, ready=info["latest"])
            return True
        except Exception as e:  # noqa: BLE001 - the manual install button still works
            log.warning("background update download failed: %s", e)
            _step(0.0, "", state="idle", progress=0.0, error=None)
            return False


def restart(quit_app, busy=lambda: None) -> dict:
    """Install the staged update and relaunch. Refuses while stems are processing or the cloud is syncing."""
    if not _staged["path"]:
        raise RuntimeError("No update is ready yet")
    _guard(busy)
    threading.Thread(target=_apply, args=(quit_app,), daemon=True).start()
    _step(0.85, "Installing the update", state="installing", progress=1.0, error=None)
    return status()


def _apply(quit_app) -> None:
    try:
        if _staged["path"] == "brew":
            if not _brew_upgrade():
                raise RuntimeError("Homebrew could not update Prismix")
            _reopen_after_exit()
        else:
            _hand_over(Path(_staged["path"]))
        _step(0.95, "Restarting Prismix", state="restarting", progress=1.0)
        time.sleep(1.5)
        quit_app()
    except Exception as e:  # noqa: BLE001
        log.exception("update install failed")
        _staged.update(path=None, version=None)
        _job.update(state="error", error=str(e) or e.__class__.__name__)


def _ask_mac(version: str) -> bool:
    """Native macOS alert with a Restart button; True when the user chose it."""
    script = (f'display alert "Prismix {version} is ready" message "The update is downloaded. Restart Prismix to finish installing it." '
              'buttons {"Later", "Restart Now"} default button "Restart Now" cancel button "Later"')
    try:
        return subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=3600).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def auto(busy, quit_app, interval: float = TTL) -> None:
    """Background loop: look for a release, download it quietly, then offer the restart once nothing is running."""
    declined: set[str] = set()

    def loop() -> None:
        while True:
            try:
                info = check(force=True)
                if stage(info):
                    version = info["latest"]
                    while busy():
                        time.sleep(BUSY_POLL)
                    if sys.platform == "darwin" and version not in declined:
                        declined.add(version)  # ask once per version; the Settings entry stays for later
                        if _ask_mac(version):
                            with contextlib.suppress(RuntimeError):  # work started while the alert was open: retry from Settings
                                restart(quit_app, busy)
            except Exception:  # noqa: BLE001 - never let the updater loop die
                log.exception("update loop failed")
            time.sleep(interval if not _staged["path"] else BUSY_POLL * 10)

    if installable():
        threading.Thread(target=loop, name="prismix-update", daemon=True).start()
