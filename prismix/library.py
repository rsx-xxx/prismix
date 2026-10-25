"""On-disk cache. Layout:

    library/<video_id>/info.json          title, channel, duration, ...
    library/<video_id>/source.<ext>       original download (re-used for other models)
    library/<video_id>/thumb.jpg
    library/<video_id>/<model>/meta.json  written last => its presence means "complete"
    library/<video_id>/<model>/<stem>.flac
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import LIBRARY_DIR, MODELS

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YT_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}


def parse_video_id(text: str) -> str | None:
    """Extract a YouTube video id from a URL or a bare id. None if it is not one."""
    text = text.strip()
    if VIDEO_ID_RE.match(text):
        return text
    if "://" not in text:
        text = "https://" + text
    try:
        url = urlparse(text)
    except ValueError:
        return None
    host = (url.hostname or "").lower()
    if host not in YT_HOSTS:
        return None
    candidate = None
    if host.endswith("youtu.be"):
        candidate = url.path.lstrip("/").split("/")[0]
    elif url.path == "/watch":
        candidate = parse_qs(url.query).get("v", [None])[0]
    else:
        m = re.match(r"^/(?:shorts|embed|live|v)/([^/?]+)", url.path)
        candidate = m.group(1) if m else None
    return candidate if candidate and VIDEO_ID_RE.match(candidate) else None


def video_dir(video_id: str) -> Path:
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError("invalid video id")
    return LIBRARY_DIR / video_id


def model_dir(video_id: str, model: str) -> Path:
    if model not in MODELS:
        raise ValueError("unknown model")
    return video_dir(video_id) / model


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def read_info(video_id: str) -> dict | None:
    return _read_json(video_dir(video_id) / "info.json")


def read_meta(video_id: str, model: str) -> dict | None:
    """Stem metadata, only if every stem file is actually on disk."""
    d = model_dir(video_id, model)
    meta = _read_json(d / "meta.json")
    if not meta or not all((d / s["file"]).is_file() for s in meta.get("stems", [])):
        return None
    return meta


def find_source(video_id: str) -> Path | None:
    return next(iter(sorted(video_dir(video_id).glob("source.*"))), None)


def list_library() -> list[dict]:
    items = []
    if not LIBRARY_DIR.is_dir():
        return items
    for d in LIBRARY_DIR.iterdir():
        if not VIDEO_ID_RE.match(d.name):
            continue
        info = read_info(d.name)
        if not info:
            continue
        models = [m for m in MODELS if read_meta(d.name, m)]
        if models:
            analyzed = models if (d / "structure.json").is_file() else []
            items.append({**info, "models": models, "analyzed": analyzed})
    items.sort(key=lambda i: i.get("added", 0), reverse=True)
    return items


def delete(video_id: str) -> bool:
    d = video_dir(video_id)
    if not d.is_dir():
        return False
    shutil.rmtree(d)
    return True
