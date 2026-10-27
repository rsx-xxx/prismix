"""Check GitHub for a newer release than the running build."""
from __future__ import annotations

import logging
import re
import sys
import time

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
