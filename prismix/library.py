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


_KARAOKE_RE = re.compile(r"\s*[(\[]\s*(?:караоке|karaoke)\s*[)\]]\s*", re.I)


def clean_title(title: str) -> str:
    """A song title without the "(Караоке)" tag karaoke uploads carry; never empties the title."""
    cleaned = re.sub(r"\s{2,}", " ", _KARAOKE_RE.sub(" ", title)).strip(" -–—")
    return cleaned or title


def read_info(video_id: str) -> dict | None:
    info = _read_json(video_dir(video_id) / "info.json")
    if info and isinstance(info.get("title"), str):
        info["title"] = clean_title(info["title"])
    return info


def rename(video_id: str, title: str) -> dict | None:
    """Give an uploaded file its own title. Tags found for the old title no longer apply."""
    info = _read_json(video_dir(video_id) / "info.json")
    if not info or not info.get("upload"):
        return None
    info["title"] = title.strip()
    (video_dir(video_id) / "info.json").write_text(json.dumps(info, ensure_ascii=False))
    (video_dir(video_id) / "tags.json").unlink(missing_ok=True)
    return read_info(video_id)


SETUP_INTS = {"semitones": (-12, 12), "chordKey": (-12, 12), "capo": (0, 11)}


def clean_setup(raw: object) -> dict:
    """The part of a song's playing setup worth sharing: the audio key, the chords-only shift, capo and guitar tuning."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, (lo, hi) in SETUP_INTS.items():
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
            out[key] = v
    t = raw.get("tuning")
    if isinstance(t, str) and re.fullmatch(r"[a-z0-9:,]{1,40}", t):
        out["tuning"] = t
    return out


def read_setup(video_id: str) -> dict:
    return clean_setup(_read_json(video_dir(video_id) / "setup.json"))


def write_setup(video_id: str, setup: object) -> dict:
    clean = clean_setup(setup)
    (video_dir(video_id) / "setup.json").write_text(json.dumps(clean))
    return clean


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


def stamp(video_id: str, stage: str, schema: int) -> None:
    """Record in info.json which Prismix version produced a stage, so outdated results can be found and redone."""
    from . import __version__

    info = read_info(video_id)
    if info is None:
        return
    info["prismix"] = __version__
    info.setdefault("stages", {})[stage] = schema
    (video_dir(video_id) / "info.json").write_text(json.dumps(info, ensure_ascii=False))


def delete(video_id: str) -> bool:
    d = video_dir(video_id)
    if not d.is_dir():
        return False
    shutil.rmtree(d)
    return True
