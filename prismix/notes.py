"""Notes: short remarks pinned to a moment of a song, for the whole band.

A sticker belongs to a time in the recording (`t`, seconds), not to a word, bar or tab: lyrics, chords and tab all sit on
the same timeline, so one sticker shows up in each of them. Kept per song in `library/<id>/notes.json`; phones change
them through the band socket (see band.py), which then tells everyone.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time

from . import library

SCHEMA = 1
MAX_NOTES = 300
MAX_TEXT = 500
MAX_T = 86_400.0
ICONS = {"down", "up", "mute", "bang", "talk", "cresc", "dim", "hold", "play", "pause", *map(str, range(1, 9))}
SHOW = ("tap", "always", "hover")
_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_lock = threading.Lock()


def _path(video_id: str):
    return library.video_dir(video_id) / "notes.json"


def load(video_id: str) -> list[dict]:
    data = library._read_json(_path(video_id)) or {}
    notes = data.get("notes") if data.get("schema") == SCHEMA else []
    return notes if isinstance(notes, list) else []


def _save(video_id: str, notes: list[dict]) -> None:
    path = _path(video_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"schema": SCHEMA, "notes": notes}, ensure_ascii=False))
    os.replace(tmp, path)


def _clean(raw: dict, by: str) -> dict:
    try:
        t = float(raw.get("t"))
    except (TypeError, ValueError):
        raise ValueError("bad time") from None
    if not 0 <= t <= MAX_T:
        raise ValueError("bad time")
    icon = raw.get("icon")
    show = raw.get("show")
    return {
        "t": round(t, 2),
        "icon": icon if icon in ICONS else "talk",
        "text": re.sub(r"\s+\n", "\n", str(raw.get("text") or "")).strip()[:MAX_TEXT],
        "show": show if show in SHOW else "tap",
        "by": by.strip()[:24] or "Someone",
    }


def apply(video_id: str, op: str, raw: dict, by: str) -> list[dict]:
    """Add, edit or delete one sticker and return the song's full list. Raises ValueError for anything unusable."""
    if not library.VIDEO_ID_RE.match(video_id) or library.read_info(video_id) is None:
        raise ValueError("unknown song")
    if not isinstance(raw, dict):
        raise ValueError("bad sticker")
    with _lock:
        notes = load(video_id)
        nid = raw.get("id")
        if op == "add":
            if len(notes) >= MAX_NOTES:
                raise ValueError("too many stickers")
            notes.append({"id": secrets.token_hex(4), **_clean(raw, by), "at": int(time.time())})
        else:
            if not isinstance(nid, str) or not _ID_RE.match(nid):
                raise ValueError("bad id")
            i = next((k for k, n in enumerate(notes) if n.get("id") == nid), -1)
            if i < 0:
                return notes  # already gone: someone else removed it first
            if op == "del":
                del notes[i]
            elif op == "edit":
                keep = {k: notes[i][k] for k in ("id", "at", "by") if k in notes[i]}
                notes[i] = {**_clean({**notes[i], **raw}, notes[i].get("by", by)), **keep, "edited": by.strip()[:24]}
            else:
                raise ValueError("bad op")
        notes.sort(key=lambda n: n["t"])
        _save(video_id, notes)
        return notes
