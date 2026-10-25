"""Synced lyrics from LRCLIB (https://lrclib.net): free, no key, community-written LRC files.

Only artist, title and length are sent. A hit is cached as `library/<id>/lyrics.json`.
"""
from __future__ import annotations

import json
import os
import re
import time

import httpx

from . import library, uploads

API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "prismix (personal use; https://lrclib.net/docs)"}
TIMEOUT = 25
SCHEMA = 1
MIN_TITLE_COVERAGE = 0.6

_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_ALT = re.compile(
    r"[\(\[][^)\]]*(караоке|karaoke|cover|live|clip|клип|remix|instrumental)[^)\]]*[\)\]]", re.I)


class LyricsError(RuntimeError):
    """Message is safe to show to the user."""


def parse_lrc(text: str) -> list[dict]:
    """'[01:02.50] words' -> [{'t': 62.5, 'text': 'words'}], sorted by time; a line may carry several stamps."""
    out = []
    for raw in text.splitlines():
        stamps = list(_STAMP.finditer(raw))
        if not stamps:
            continue
        words = raw[stamps[-1].end():].strip()
        for m in stamps:
            frac = (m.group(3) or "0").ljust(3, "0")[:3]
            out.append({"t": int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / 1000, "text": words})
    out.sort(key=lambda x: x["t"])
    return out


def _plain(text: str) -> list[dict]:
    return [{"t": None, "text": ln.strip()} for ln in text.splitlines()]


def _entry(hit: dict) -> dict | None:
    if hit.get("instrumental"):
        raise LyricsError("This track is marked as instrumental")
    if hit.get("syncedLyrics"):
        lines = parse_lrc(hit["syncedLyrics"])
        if lines:
            return {"synced": True, "lines": lines}
    if hit.get("plainLyrics"):
        return {"synced": False, "lines": _plain(hit["plainLyrics"])}
    return None


def guess(info: dict) -> tuple[str, str]:
    """(artist, title) for a library item, from its tags or its YouTube title."""
    channel = (info.get("channel") or "").strip()
    if channel in ("", "Uploaded file") and info.get("match"):
        artist, title = uploads._split_youtube_title(info["match"])
    else:
        head = (info.get("title") or "").split("|")[0]  # 'Artist - Song | Official Audio | 2024 | Label'
        artist, title = uploads._split_youtube_title({"title": head, "channel": channel})
    return artist.strip(), re.sub(r"\s+", " ", _ALT.sub(" ", title)).strip(" -_.")


def pick(title: str, duration: float, hits: list[dict]) -> dict | None:
    """Best search hit: the wanted title words present, similar length, synced preferred."""
    want = uploads._tokens(title)
    if not want:
        return None
    best, best_key = None, None
    for h in hits:
        coverage = len(want & uploads._tokens(h.get("trackName") or "")) / len(want)
        if coverage < MIN_TITLE_COVERAGE or not (h.get("syncedLyrics") or h.get("plainLyrics")):
            continue
        dur = h.get("duration")
        gap = abs(dur - duration) if dur and duration else None
        if gap is not None and gap > max(6, duration * 0.05):
            continue
        key = (bool(h.get("syncedLyrics")), coverage, -(gap if gap is not None else 999))
        if best_key is None or key > best_key:
            best, best_key = h, key
    return best


def _get(client: httpx.Client, path: str, **params) -> object | None:
    """GET with a few retries: LRCLIB answers 503 'server is busy' now and then."""
    for attempt in range(4):
        r = client.get(f"{API}/{path}", params=params)
        if r.status_code == 404:
            return None
        if r.status_code in (429, 502, 503, 504) and attempt < 3:
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()


def _fetch(artist: str, title: str, duration: float, query: str | None) -> dict | None:
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        if not query and artist and title and duration:
            hit = _get(client, "get", artist_name=artist, track_name=title, duration=round(duration))
            if isinstance(hit, dict) and _entry_or_none(hit):
                return hit
        searches = [{"q": query}] if query else [
            {"track_name": title, "artist_name": artist} if artist else {"track_name": title},
            {"q": f"{artist} {title}".strip()},
        ]
        for params in searches:
            hits = _get(client, "search", **params)
            if isinstance(hits, list) and (found := pick(query or title, duration, hits)):
                return found
    return None


def _entry_or_none(hit: dict) -> dict | None:
    try:
        return _entry(hit)
    except LyricsError:
        return None


def lookup(video_id: str, query: str | None = None, *, refresh: bool = False) -> dict:
    """Cached lyrics for a library item. `query` overrides the guessed artist and title."""
    path = library.video_dir(video_id) / "lyrics.json"
    if not (refresh or query):
        try:
            cached = json.loads(path.read_text())
            if cached.get("version") == SCHEMA:
                return cached
        except (OSError, ValueError):
            pass
    info = library.read_info(video_id)
    if not info:
        raise ValueError("unknown video")
    artist, title = guess(info)
    try:
        hit = _fetch(artist, title, float(info.get("duration") or 0), query)
    except httpx.HTTPError as e:
        raise LyricsError("Could not reach the lyrics service. Check your connection.") from e
    entry = _entry(hit) if hit else None
    if not entry:
        raise LyricsError("No lyrics found for this track")
    result = {
        "version": SCHEMA, "source": "lrclib", "lrclibId": hit.get("id"),
        "artist": hit.get("artistName") or artist, "title": hit.get("trackName") or title, **entry,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, path)
    return result
