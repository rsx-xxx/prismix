"""Saved playlists: ordered lists of library songs, kept in one JSON file next to the library."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from . import library

_lock = threading.Lock()
MAX_NAME = 80
MAX_ITEMS = 500


def _file():
    return library.LIBRARY_DIR / ".playlists.json"


def _read() -> list[dict]:
    try:
        data = json.loads(_file().read_text("utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(items: list[dict]) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), "utf-8")
    os.replace(tmp, f)


def _clean(item: dict) -> dict | None:
    vid = str(item.get("id") or "")
    if not library.VIDEO_ID_RE.match(vid):
        return None
    dur = item.get("duration")
    return {
        "id": vid,
        "title": str(item.get("title") or "")[:200],
        "channel": str(item.get("channel") or "")[:120],
        "duration": dur if isinstance(dur, (int, float)) else 0,
    }


def _name(name: str) -> str:
    return " ".join(str(name).split())[:MAX_NAME] or "Playlist"


def list_all() -> list[dict]:
    with _lock:
        return _read()


def get(pid: str) -> dict | None:
    return next((p for p in list_all() if p["id"] == pid), None)


def create(name: str, items: list[dict] | None = None, source: str = "") -> dict:
    cleaned = _unique([c for i in items or [] if (c := _clean(i))])
    pl = {"id": uuid.uuid4().hex[:10], "name": _name(name), "source": source[:400],
          "items": cleaned[:MAX_ITEMS], "updated": time.time()}
    with _lock:
        _write([*_read(), pl])
    return pl


def _unique(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    return [i for i in items if not (i["id"] in seen or seen.add(i["id"]))]


def update(pid: str, *, name: str | None = None, order: list[str] | None = None) -> dict | None:
    """Rename, or reorder/trim to `order` (ids not in the playlist are ignored)."""
    with _lock:
        all_ = _read()
        pl = next((p for p in all_ if p["id"] == pid), None)
        if pl is None:
            return None
        if name is not None:
            pl["name"] = _name(name)
        if order is not None:
            by_id = {i["id"]: i for i in pl["items"]}
            pl["items"] = [by_id[i] for i in dict.fromkeys(order) if i in by_id]
        pl["updated"] = time.time()
        _write(all_)
        return pl


def add_items(pid: str, items: list[dict]) -> dict | None:
    with _lock:
        all_ = _read()
        pl = next((p for p in all_ if p["id"] == pid), None)
        if pl is None:
            return None
        have = {i["id"] for i in pl["items"]}
        for raw in items:
            c = _clean(raw)
            if c and c["id"] not in have and len(pl["items"]) < MAX_ITEMS:
                pl["items"].append(c)
                have.add(c["id"])
        pl["updated"] = time.time()
        _write(all_)
        return pl


def remove_item(pid: str, vid: str) -> dict | None:
    with _lock:
        all_ = _read()
        pl = next((p for p in all_ if p["id"] == pid), None)
        if pl is None:
            return None
        pl["items"] = [i for i in pl["items"] if i["id"] != vid]
        pl["updated"] = time.time()
        _write(all_)
        return pl


def delete(pid: str) -> bool:
    with _lock:
        all_ = _read()
        rest = [p for p in all_ if p["id"] != pid]
        if len(rest) == len(all_):
            return False
        _write(rest)
        return True
