"""Local audio/video files as library items. Same layout as a YouTube download, id = u-<hash>."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from . import library, tabs
from .config import LIBRARY_DIR, MAX_DURATION_SEC, ffmpeg_exe

log = logging.getLogger(__name__)

EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aif", ".aiff",
    ".webm", ".mp4", ".m4v", ".mov", ".mkv", ".m4b",
}
MAX_BYTES = int(os.environ.get("PRISMIX_MAX_UPLOAD_MB", 500)) * 2**20
ITUNES = "https://itunes.apple.com/search"
ART_HOSTS = (".mzstatic.com",)
MIN_ART_MATCH = 0.6


class UploadError(ValueError):
    """Message is safe to show to the user."""


def is_upload(video_id: str) -> bool:
    return bool(re.fullmatch(r"u-[0-9a-f]{9}", video_id))


def _ffmpeg(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "info", *args],
        capture_output=True, text=True, errors="replace", timeout=timeout,
    )


def probe(path: Path) -> dict:
    """Duration, tags and stream kinds from ffmpeg's own report."""
    r = _ffmpeg("-i", str(path), "-f", "ffmetadata", "-")
    err = r.stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    tags: dict[str, str] = {}
    for line in r.stdout.splitlines()[1:]:
        k, sep, v = line.partition("=")
        if sep and k.strip().lower() in {"title", "artist", "album", "album_artist"}:
            tags.setdefault(k.strip().lower(), v.strip())
    return {
        "audio": bool(re.search(r"Stream #.*Audio:", err)),
        "video": bool(re.search(r"Stream #.*Video:", err)),
        "cover": "attached pic" in err,
        "duration": duration,
        "tags": tags,
    }


def _clean_name(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"^\s*\d{1,3}[\s.)-]+(?=\D)", "", stem)  # leading track number
    return re.sub(r"\s+", " ", stem).strip() or "Untitled"


JUNK = re.compile(
    r"[\(\[][^)\]]*\b(official|video|audio|lyrics?|hd|4k|remaster(ed)?|free download|mp3)\b[^)\]]*[\)\]]"
    r"|[\(\[][^)\]]*\d\s?kbps[^)\]]*[\)\]]"
    r"|\b(www\.\S+|y2mate\S*|\d{2,3}\s?kbps)\b",
    re.I,
)
SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "vs"}


def _smart_case(s: str) -> str:
    """Title-case only text that is all lower or all upper; leave deliberate casing alone."""
    if s != s.lower() and s != s.upper():
        return s
    words = s.lower().split()
    return " ".join(
        w if (i and w in SMALL_WORDS) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def pretty_name(filename: str) -> tuple[str, str]:
    """(artist, title) read from a file name: no track number, junk or underscores, sensible casing."""
    name = JUNK.sub(" ", _clean_name(filename))
    name = re.sub(r"\s+", " ", name).strip(" -_.") or "Untitled"
    parts = re.split(r"\s+[-–—]\s+", name, maxsplit=1)
    if len(parts) == 2 and all(parts):
        return _smart_case(parts[0].strip()), _smart_case(parts[1].strip())
    return "", _smart_case(name)


# ───────────────────────── YouTube match ─────────────────────────

MIN_COVERAGE = 0.8
ALT_VERSION = {"live", "cover", "remix", "karaoke", "instrumental", "reaction", "slowed", "nightcore", "tutorial"}


def _tokens(s: str) -> set[str]:
    return set(tabs._norm(s).split())


def pick_match(artist: str, title: str, duration: float, candidates: list[dict]) -> dict | None:
    """Most likely original among search results: every wanted word present, similar length, not an alt version."""
    want = _tokens(f"{artist} {title}")
    if not want:
        return None
    best, best_key = None, None
    for c in candidates:
        have = _tokens(f"{c.get('title', '')} {c.get('channel', '')}")
        coverage = len(want & have) / len(want)
        if coverage < MIN_COVERAGE:
            continue
        dur = c.get("duration")
        gap = abs(dur - duration) if dur and duration else None
        if gap is not None and gap > max(8, duration * 0.06):
            continue
        extra = len((have & ALT_VERSION) - want)
        key = (extra == 0, coverage, -(gap if gap is not None else 999))
        if best_key is None or key > best_key:
            best, best_key = c, key
    return best


def find_on_youtube(artist: str, title: str, duration: float) -> dict | None:
    """Best YouTube match for a file, or None (also when offline). Only artist/title text leaves the machine."""
    from . import pipeline

    q = f"{artist} {title}".strip()
    if not q:
        return None
    try:
        found = pipeline.search(q, limit=8)
    except Exception as e:  # network, yt-dlp, JS runtime: matching is a bonus, never block the upload
        log.info("YouTube match skipped: %s", e)
        return None
    return pick_match(artist, title, duration, found)


# ───────────────────────── preview image ─────────────────────────

def _frame(src: Path, dest: Path, info: dict) -> bool:
    """Embedded cover art, or a still from the video track."""
    seek = [] if info["cover"] else ["-ss", f"{info['duration'] * 0.1:.2f}"]
    r = _ffmpeg(*seek, "-i", str(src), "-map", "0:v:0", "-frames:v", "1",
                "-vf", "scale='min(640,iw)':-2", "-y", str(dest))
    return r.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _itunes_results(term: str, entity: str) -> list[dict]:
    q = urlencode({"term": term, "entity": entity, "limit": 10})
    try:
        return httpx.get(f"{ITUNES}?{q}", timeout=10).json().get("results", [])
    except (httpx.HTTPError, ValueError):
        return []


def _itunes_art(result: dict | None) -> bytes | None:
    art = (result or {}).get("artworkUrl100")
    if not art:
        return None
    art = re.sub(r"/\d+x\d+bb\.", "/600x600bb.", art)
    host = urlparse(art).hostname or ""
    if urlparse(art).scheme != "https" or not host.endswith(ART_HOSTS):
        return None
    try:
        r = httpx.get(art, timeout=15)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    return r.content if 0 < len(r.content) <= 3_000_000 else None


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, tabs._norm(a), tabs._norm(b)).ratio()


def _itunes(artist: str, title: str) -> bytes | None:
    """Free iTunes Search API, no key. Only artist/title text leaves the machine."""
    best, score = None, 0.0
    for r in _itunes_results(f"{artist} {title}".strip(), "song")[:5]:
        s = _ratio(title, r.get("trackName", ""))
        if artist:
            s = 0.7 * s + 0.3 * _ratio(artist, r.get("artistName", ""))
        if s > score:
            best, score = r, s
    return _itunes_art(best) if score >= MIN_ART_MATCH else None


def _itunes_original(title: str) -> bytes | None:
    """The file may be a cover: art of the song with the same title by whoever released it first."""
    best, score = None, 0.0
    for r in _itunes_results(title, "song"):
        s = _ratio(title, r.get("trackName", ""))
        if s > score:
            best, score = r, s
    return _itunes_art(best) if score >= 0.9 else None


def _itunes_artist(artist: str) -> bytes | None:
    """Last resort before a waveform: the cover of a record by the band itself."""
    if not artist:
        return None
    for r in _itunes_results(artist, "album"):
        if _ratio(artist, r.get("artistName", "")) >= 0.85:
            return _itunes_art(r)
    return None


def _waveform(src: Path, dest: Path) -> bool:
    r = _ffmpeg("-i", str(src), "-filter_complex",
                "aformat=channel_layouts=mono,showwavespic=s=640x360:colors=0x8b7cf6",
                "-frames:v", "1", "-y", str(dest), timeout=300)
    return r.returncode == 0 and dest.is_file()


def make_preview(src: Path, dest: Path, info: dict, title: str, artist: str) -> str:
    """Best picture available, in order: cover art / video frame, online album art, waveform."""
    if info["video"]:
        try:
            if _frame(src, dest, info):
                return "cover" if info["cover"] else "frame"
        except subprocess.TimeoutExpired:
            pass
    queries = [(artist, title)] if artist else []
    queries += tabs.guess_query({"title": title, "channel": artist})
    for a, t in queries[:3]:
        if data := _itunes(a, t):
            dest.write_bytes(data)
            return "album"
    for find, arg in ((_itunes_original, title), (_itunes_artist, artist)):
        if arg and (data := find(arg)):
            dest.write_bytes(data)
            return "album"
    try:
        if _waveform(src, dest):
            return "waveform"
    except subprocess.TimeoutExpired:
        pass
    return "none"


def _split_youtube_title(match: dict) -> tuple[str, str]:
    """(artist, title) from a matched video: 'Artist - Title (Official Video)' or channel + cleaned title."""
    name = re.sub(r"\s+", " ", JUNK.sub(" ", match["title"])).strip(" -_.")
    parts = re.split(r"\s+[-–—]\s+", name, maxsplit=1)
    if len(parts) == 2 and all(parts):
        return parts[0].strip(), parts[1].strip()
    channel = re.sub(r"\s*-\s*Topic$|VEVO$", "", match.get("channel") or "").strip()
    return channel, name


# ───────────────────────── saving ─────────────────────────

def save(chunks: Iterator[bytes], filename: str) -> dict:
    """Store an uploaded file. Same bytes twice give the same item, so nothing is processed twice."""
    ext = Path(filename).suffix.lower()
    if ext not in EXTENSIONS:
        raise UploadError("Unsupported file type; use mp3, m4a, wav, flac, ogg, opus, mp4, mkv or similar")
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".upload-", dir=LIBRARY_DIR))
    try:
        src = tmp / f"source{ext}"
        digest, size = hashlib.sha256(), 0
        with src.open("wb") as f:
            for block in chunks:
                size += len(block)
                if size > MAX_BYTES:
                    raise UploadError(f"File is larger than {MAX_BYTES >> 20} MB")
                digest.update(block)
                f.write(block)
        if size == 0:
            raise UploadError("The file is empty")
        vid = "u-" + digest.hexdigest()[:9]
        if (existing := library.read_info(vid)) and library.find_source(vid):
            return existing

        info = probe(src)
        if not info["audio"]:
            raise UploadError("No audio found in that file")
        if info["duration"] > MAX_DURATION_SEC:
            raise UploadError(
                f"Track is {int(info['duration']) // 60} min long; the limit is {MAX_DURATION_SEC // 60} min")
        tags = info["tags"]
        artist = tags.get("artist") or tags.get("album_artist") or ""
        title = tags.get("title") or ""
        if not title:
            guess_artist, title = pretty_name(filename)
            artist = artist or guess_artist
        match = find_on_youtube(artist, title, info["duration"])
        if match and not tags.get("title"):
            m_artist, m_title = _split_youtube_title(match)
            artist, title = artist or m_artist, m_title or title
        preview = make_preview(src, tmp / "thumb.jpg", info, title, artist)
        record = {
            "id": vid,
            "title": title,
            "channel": artist or "Uploaded file",
            "duration": int(info["duration"]),
            "added": int(time.time()),
            "upload": True,
            "ext": ext,
            "hasVideo": info["video"] and not info["cover"],
            "preview": preview,
            "match": {k: match[k] for k in ("id", "title", "channel")} if match else None,
        }
        (tmp / "info.json").write_text(json.dumps(record, ensure_ascii=False))
        final = library.video_dir(vid)
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)
        return record
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
