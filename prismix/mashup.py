"""Covers that borrow from two songs: the music of one, the words of another.

The user names a source for the lyrics and a source for the chords ("Artist - Title" each), kept in `info.json` under
`mashup`. Lyrics then come from the lyrics source and are always laid onto these vocals (see lyrics.py); online chords
come from the chords source, in whatever key it was played, and only where they agree with what was heard (see tabs.py).
"""
from __future__ import annotations

import json
import os
import re

from . import library

KINDS = {"lyrics": ("lyrics.json",), "chords": ("tabs.json", "tabdata.json")}
_DASH = re.compile(r"\s+[-–—]\s+")


def parse(text: str | None) -> tuple[str | None, str] | None:
    """'Artist - Title' -> (artist, title); a bare 'Title' -> (None, title)."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return None
    parts = _DASH.split(text, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 and all(parts) else (None, text)


def get(info: dict | None) -> dict[str, str]:
    return {k: v for k, v in ((info or {}).get("mashup") or {}).items() if k in KINDS and v}


def source(info: dict | None, kind: str) -> tuple[str | None, str] | None:
    return parse(get(info).get(kind))


def save(video_id: str, sources: dict[str, str | None]) -> dict[str, str]:
    """Set (or, with an empty value, clear) the given sources; results derived from a changed source are dropped."""
    info = library.read_info(video_id)
    if info is None:
        raise ValueError("unknown video")
    now = get(info)
    new = dict(now)
    for kind, text in sources.items():
        if kind not in KINDS:
            continue
        text = re.sub(r"\s+", " ", text or "").strip()[:200]
        if text:
            new[kind] = text
        else:
            new.pop(kind, None)
    for kind, files in KINDS.items():
        if new.get(kind) != now.get(kind):
            for name in files:
                (library.video_dir(video_id) / name).unlink(missing_ok=True)
    if new != now:
        if new:
            info["mashup"] = new
        else:
            info.pop("mashup", None)
        path = library.video_dir(video_id) / "info.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(info, ensure_ascii=False))
        os.replace(tmp, path)
    return new
