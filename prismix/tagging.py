"""Tags and cover art for exported audio.

Title, artist, album, year, genre and a square cover come from the iTunes Search API (Apple's public catalogue, no
key). When nothing matches well, or the machine is offline, the YouTube title/channel and a square crop of the
video thumbnail are used. Results are cached next to the track so a song is looked up once.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from . import library
from .config import ffmpeg_exe

ONLINE = os.environ.get("PRISMIX_ONLINE_TAGS", "1") != "0"
API = "https://itunes.apple.com/search"
NOISE = re.compile(
    r"[\(\[][^)\]]*(official|video|audio|lyrics?|lyric video|hd|hq|4k|remaster(ed)?|live|clip|visuali[sz]er)[^)\]]*[\)\]]"
    r"|\b(official (music )?video|lyric video|full album)\b|\s-\s*topic$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", NOISE.sub(" ", text or "")).strip(" -–—|")


def _split(title: str, channel: str) -> tuple[str, str]:
    """'Artist - Song' titles are common; otherwise the channel is the artist."""
    t = _clean(title)
    for sep in (" - ", " – ", " — "):
        if sep in t:
            a, s = t.split(sep, 1)
            return a.strip(), s.strip()
    return _clean(channel), t


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _fetch(url: str, limit: int = 2_000_000) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Prismix"})
    with urllib.request.urlopen(req, timeout=6) as r:  # noqa: S310 - fixed https hosts
        return r.read(limit)


def lookup(title: str, channel: str) -> dict | None:
    artist, song = _split(title, channel)
    if not song:
        return None
    q = urllib.parse.urlencode({"term": f"{artist} {song}".strip(), "entity": "song", "limit": 8, "media": "music"})
    try:
        hits = json.loads(_fetch(f"{API}?{q}")).get("results", [])
    except (OSError, ValueError):
        return None
    best, score = None, 0.0
    for h in hits:
        s = _sim(song, h.get("trackName", "")) * 0.65 + max(_sim(artist, h.get("artistName", "")), 0.3 if not artist else 0) * 0.35
        for word in ("remix", "karaoke", "tribute", "instrumental", "cover", "live"):
            if word in h.get("trackName", "").lower() and word not in song.lower():
                s -= 0.15
        if s > score:
            best, score = h, s
    if not best or score < 0.62:
        return None
    art = (best.get("artworkUrl100") or "").replace("100x100bb", "1000x1000bb")
    return {
        "title": best.get("trackName") or song,
        "artist": best.get("artistName") or artist,
        "album": best.get("collectionName") or "",
        "date": (best.get("releaseDate") or "")[:10],
        "genre": best.get("primaryGenreName") or "",
        "track": f"{best.get('trackNumber')}/{best.get('trackCount')}" if best.get("trackNumber") else "",
        "cover_url": art,
    }


def _square(src: Path, dest: Path) -> bool:
    proc = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-nostdin", "-y", "-i", str(src), "-vf",
         "crop='min(iw,ih)':'min(iw,ih)',scale='min(1000,iw)':-2", "-frames:v", "1", "-q:v", "2", str(dest)],
        capture_output=True, check=False,
    )
    return proc.returncode == 0 and dest.is_file()


def for_track(video_id: str) -> tuple[dict[str, str], Path | None]:
    """(tags, cover.jpg or None). Never raises: tags are a nicety, not a reason to fail an export."""
    try:
        d = library.video_dir(video_id)
        info = library.read_info(video_id) or {}
        cache = d / "tags.json"
        cover = d / "cover.jpg"
        found = None
        if cache.is_file():
            found = json.loads(cache.read_text())
        elif ONLINE:
            found = lookup(info.get("title", ""), info.get("channel", "")) or {}
            if found.get("cover_url"):
                try:
                    cover.write_bytes(_fetch(found["cover_url"], 8_000_000))
                except OSError:
                    found.pop("cover_url", None)
            cache.write_text(json.dumps(found))
        found = found or {}
        if not cover.is_file() or not found.get("cover_url"):
            thumb = d / "thumb.jpg"
            if thumb.is_file() and not cover.is_file():
                _square(thumb, cover)
        tags = {k: found[k] for k in ("title", "artist", "album", "date", "genre", "track") if found.get(k)}
        if found.get("artist"):
            tags["album_artist"] = found["artist"]
        tags.setdefault("title", info.get("title", ""))
        tags.setdefault("artist", info.get("channel", ""))
        return tags, (cover if cover.is_file() else None)
    except Exception:  # noqa: BLE001
        return {}, None
