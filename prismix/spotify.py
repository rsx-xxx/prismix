"""Spotify links -> YouTube videos.

Spotify's audio is DRM-protected and its Web API is closed to new apps (Premium owner, five users), so only the
public embed page is read: titles, artists and lengths of a track, album, playlist or artist. Each song is then matched
to a YouTube video by length and name, the same way the rest of the app gets its audio. No account or key is needed.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import ssl
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from . import library, pipeline

log = logging.getLogger(__name__)

KINDS = ("track", "album", "playlist", "artist")
HOSTS = {"open.spotify.com", "play.spotify.com"}
_ID = r"[A-Za-z0-9]{22}"
_URL_RE = re.compile(rf"^/(?:intl-[a-z-]+/)?(?:embed/)?(?P<kind>{'|'.join(KINDS)})/(?P<id>{_ID})")
_URI_RE = re.compile(rf"^spotify:(?P<kind>{'|'.join(KINDS)}):(?P<id>{_ID})$")
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
MAX_TRACKS = 100  # what the embed page lists
WORKERS = 6

# words that mark a different recording than the studio one, unless the Spotify title has them too
_ALTERNATE = ("live", "cover", "remix", "karaoke", "instrumental", "sped up", "slowed", "reverb", "8d", "nightcore",
              "mashup", "tutorial", "lesson", "acoustic", "reaction", "piano", "guitar", "lofi", "lo fi", "bass boosted",
              "ringtone", "tiktok", "loop", "full album", "interview", "behind the scenes", "making of")
CONFIDENT = 22  # a result this good ends the search; weaker ones try the next query
LIMIT = 8


def is_spotify(text: str) -> bool:
    return parse(text) is not None


def parse(text: str) -> tuple[str, str] | None:
    """(kind, id) of a Spotify link or URI, else None."""
    text = text.strip()
    if m := _URI_RE.match(text):
        return m["kind"], m["id"]
    if "://" not in text:
        text = "https://" + text
    try:
        url = urlparse(text)
    except ValueError:
        return None
    if (url.hostname or "").lower() not in HOSTS:
        return None
    m = _URL_RE.match(url.path)
    return (m["kind"], m["id"]) if m else None


def _context() -> ssl.SSLContext:
    """TLS with certifi's CA bundle when present: python.org and frozen builds ship no system roots."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _page(kind: str, sid: str) -> dict:
    req = urllib.request.Request(f"https://open.spotify.com/embed/{kind}/{sid}",
                                 headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=_context()) as r:
            html = r.read().decode("utf-8", "replace")
        data = json.loads(_NEXT_RE.search(html).group(1))
        return data["props"]["pageProps"]["state"]["data"]["entity"]
    except Exception as e:  # noqa: BLE001 - network, markup or JSON: all mean "cannot read it"
        log.warning("spotify: %s/%s unreadable: %s", kind, sid, e)
        raise pipeline.PipelineError("Could not read that Spotify link. It may be private or unavailable.") from e


def tracks(kind: str, sid: str) -> dict:
    """{"title", "tracks": [{"id", "title", "artist", "duration"}]} (duration in seconds)."""
    ent = _page(kind, sid)
    if kind == "track":
        artist = ", ".join(a.get("name", "") for a in ent.get("artists") or [])
        rows = [{"uri": ent.get("uri", ""), "title": ent.get("title") or ent.get("name"), "subtitle": artist,
                 "duration": ent.get("duration")}]
        name = f"{artist} – {rows[0]['title']}" if artist else rows[0]["title"]
    else:
        rows = ent.get("trackList") or []
        name = ent.get("title") or ent.get("name") or "Spotify"
        if kind == "artist":
            name = f"{name} · top tracks"
    out = [{"id": str(r.get("uri", "")).rsplit(":", 1)[-1], "title": r.get("title") or "",
            "artist": r.get("subtitle") or "", "duration": (r.get("duration") or 0) / 1000}
           for r in rows[:MAX_TRACKS] if r.get("title") and r.get("entityType", "track") == "track"]
    if not out:
        raise pipeline.PipelineError("That Spotify link has no songs, or it is private.")
    return {"title": name, "tracks": out}


def _words(s: str) -> str:
    return " " + re.sub(r"[^\w]+", " ", s.lower()).strip() + " "


def score(want: dict, cand: dict) -> float:
    """How well a YouTube result fits a Spotify song: length, names, the artist's own channel, then recording type."""
    title, chan = _words(cand["title"]), _words(cand.get("channel") or "")
    wtitle = _words(want["title"])
    pts = 0.0
    if want["duration"] and cand.get("duration"):
        diff = abs(cand["duration"] - want["duration"])
        pts -= min(diff, 40) * 1.5 + (15 if diff > 40 else 0)
    main = _words(re.sub(r"\s*[(\[].*?[)\]]|\s+-\s+.*$", "", want["title"]))
    pts += 12 if main in title else -8
    artists = [_words(a) for a in want["artist"].split(",") if a.strip()]
    in_chan = any(a in chan for a in artists)
    topic = chan.strip().endswith("topic")  # YouTube's auto-generated studio uploads
    if in_chan or any(a in title for a in artists):
        pts += 10
    elif not topic:
        pts -= 6  # neither the artist's channel nor named in the title: probably someone else's upload
    pts += 6 if topic else 0
    pts += 4 if in_chan and not topic else 0  # the artist's own (or VEVO) channel
    pts += 3 if " official " in title else 0
    pts -= 25 * sum(1 for w in _ALTERNATE if f" {w} " in title and f" {w} " not in wtitle)
    return pts


def _queries(song: dict) -> list[str]:
    """Searches from most to least specific; later ones only run when the earlier ones found nothing convincing."""
    artist, title = song["artist"].split(",")[0].strip(), song["title"]
    plain = re.sub(r"\s*[(\[].*?[)\]]|\s+-\s+.*$", "", title).strip() or title
    out = [f"{artist} {title} official audio", f"{artist} - {title}", f"{artist} {plain} topic", f"{plain} {artist}"]
    return list(dict.fromkeys(q.strip() for q in out))


_cache: dict[str, dict] = {}
_lock = threading.Lock()


def _cache_file():
    return library.LIBRARY_DIR / library.SPOTIFY_MATCHES


def _load() -> None:
    if not _cache:
        with contextlib.suppress(OSError, ValueError):
            _cache.update(json.loads(_cache_file().read_text("utf-8")))


def match(song: dict) -> dict | None:
    """The best YouTube video for one Spotify song (remembered across runs), or None."""
    with _lock:
        _load()
        if hit := _cache.get(song["id"]):
            return hit
    pool: dict[str, dict] = {}
    best, best_score = None, float("-inf")
    for query in _queries(song):
        try:
            found = pipeline.search(query, limit=LIMIT)
        except Exception as e:  # noqa: BLE001 - one failed search must not sink a whole playlist
            log.warning("spotify: search %r failed: %s", query, e)
            continue
        for cand in found:
            if cand["id"] in pool:
                continue
            pool[cand["id"]] = cand
            if (pts := score(song, cand)) > best_score:
                best, best_score = cand, pts
        if best_score >= CONFIDENT:
            break
    if not best:
        return None
    if best_score < 0:
        log.info("spotify: weak match for %r (%.0f): %s", song["title"], best_score, best["title"])
    best = {**best, "title": f"{song['artist']} – {song['title']}" if song["artist"] else song["title"],
            "spotify": {"artist": song["artist"].split(",")[0].strip(), "title": song["title"]}}
    with _lock:
        _cache[song["id"]] = best
        try:
            _cache_file().parent.mkdir(parents=True, exist_ok=True)
            _cache_file().write_text(json.dumps(_cache, ensure_ascii=False), "utf-8")
        except OSError:
            pass
    return best


def resolve(url: str) -> dict:
    """{"title", "items"} in the shape of `pipeline.playlist`, every song matched to a YouTube video."""
    parsed = parse(url)
    if not parsed:
        raise pipeline.PipelineError("That does not look like a Spotify link")
    data = tracks(*parsed)
    with ThreadPoolExecutor(WORKERS) as pool:
        matched = list(pool.map(match, data["tracks"]))
    items = [m for m in matched if m]
    if not items:
        raise pipeline.PipelineError("No YouTube match found for those songs")
    return {"title": data["title"], "items": items}


def video_id(url: str) -> str:
    """The YouTube id for a Spotify track link (collections go through `resolve`)."""
    parsed = parse(url)
    if not parsed or parsed[0] != "track":
        raise pipeline.PipelineError("Open Spotify albums and playlists from Playlists; paste a single song link here")
    return resolve(url)["items"][0]["id"]
