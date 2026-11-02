"""Saved playlists: ordered lists of library songs, kept in one JSON file next to the library."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid

from . import library

_lock = threading.Lock()
MAX_NAME = 80
MAX_ITEMS = 500
TOMBSTONE_TTL = 90 * 86400  # a deleted playlist is remembered this long so the deletion reaches every device


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


def _live(items: list[dict]) -> list[dict]:
    return [p for p in items if not p.get("deleted")]


def _find(items: list[dict], pid: str) -> dict | None:
    return next((p for p in _live(items) if p["id"] == pid), None)


def list_all() -> list[dict]:
    with _lock:
        return _live(_read())


def get(pid: str) -> dict | None:
    return next((p for p in list_all() if p["id"] == pid), None)


def _sane(p) -> dict | None:
    """One playlist (or deletion marker) from another device, cleaned; None when it is unusable."""
    if not isinstance(p, dict) or not re.fullmatch(r"[0-9a-f]{10}", str(p.get("id") or "")):
        return None
    updated = p.get("updated")
    out = {"id": p["id"], "updated": float(updated) if isinstance(updated, (int, float)) else 0.0}
    if p.get("deleted"):
        return {**out, "deleted": True}
    items = _unique([c for i in p.get("items") or [] if isinstance(i, dict) and (c := _clean(i))])
    return {**out, "name": _name(p.get("name") or ""), "source": str(p.get("source") or "")[:400], "items": items[:MAX_ITEMS]}


def merge(a: list[dict], b: list[dict], now: float | None = None) -> list[dict]:
    """The union of two copies of the playlists. A playlist, or its deletion, that was changed last wins; deletion
    markers older than the TTL are dropped. Both sides run the same rule, so they converge."""
    now = time.time() if now is None else now
    best: dict[str, dict] = {}
    for p in map(_sane, [*a, *b]):
        if p and (p["id"] not in best or p["updated"] > best[p["id"]]["updated"]):
            best[p["id"]] = p
    keep = [p for p in best.values() if not p.get("deleted") or now - p["updated"] < TOMBSTONE_TTL]
    return sorted(keep, key=lambda p: (p["updated"], p["id"]))


def snapshot() -> list[dict]:
    """Every playlist plus deletion markers: what is exchanged with the cloud."""
    with _lock:
        return _read()


def absorb(remote: list[dict]) -> list[dict]:
    """Folds the cloud's copy into the local file and returns the merged result."""
    with _lock:
        mine = _read()
        merged = merge(mine, remote)
        if merged != mine:
            _write(merged)
        return merged


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
        pl = _find(all_, pid)
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
        pl = _find(all_, pid)
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
        pl = _find(all_, pid)
        if pl is None:
            return None
        pl["items"] = [i for i in pl["items"] if i["id"] != vid]
        pl["updated"] = time.time()
        _write(all_)
        return pl


def delete(pid: str) -> bool:
    """Leaves a deletion marker, so a device that still has the playlist does not bring it back."""
    with _lock:
        all_ = _read()
        if _find(all_, pid) is None:
            return False
        _write([p for p in all_ if p["id"] != pid] + [{"id": pid, "deleted": True, "updated": time.time()}])
        return True
