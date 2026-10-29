"""Synced lyrics from LRCLIB (https://lrclib.net): free, no key, community-written LRC files.

Only artist, title and length are sent. When nothing is published (a cover, a small artist) the lyrics are
recognised from the vocals instead (see transcribe.py). The result is cached as `library/<id>/lyrics.json`.

Covers are the hard case: the uploader is not the original artist, so the title and the description are mined for
who wrote the song ("cover of X", "original by X", "кавер на песню X", "Song: ...").
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time

import httpx

from . import align, genius, library, mashup, pipeline, transcribe, uploads

log = logging.getLogger(__name__)
API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "prismix (personal use; https://lrclib.net/docs)"}
TIMEOUT = 25
SCHEMA = 3
AUDIO_REV = 3  # bumped when finding published lyrics or recognising the audio got better
MIN_TITLE_COVERAGE = 0.6
MAX_CANDIDATES = 14

_STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_ALT = re.compile(
    r"[\(\[][^)\]]*(караоке|karaoke|cover|live|clip|клип|remix|instrumental|official|unofficial|неофициальн|официальн|"
    r"audio|аудио|video|видео|lyric|текст|слова|visuali[sz]er|remaster|hd|4k|mv|full|session|version|версия)[^)\]]*[\)\]]", re.I)
_ANY_BRACKET = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")


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
    if info.get("id") and (known := library.spotify_hint(info["id"])):  # the Spotify link named the song
        return known
    if channel in ("", "Uploaded file") and info.get("match"):
        artist, title = uploads._split_youtube_title(info["match"])
    else:
        head = (info.get("title") or "").split("|")[0]  # 'Artist - Song | Official Audio | 2024 | Label'
        artist, title = uploads._split_youtube_title({"title": head, "channel": channel})
    return artist.strip(), re.sub(r"\s+", " ", _ALT.sub(" ", title)).strip(" -_.")


# ───────────────────────── who is the original artist? ─────────────────────────

_COVER = re.compile(r"\b(cover|covered|кавер\w*|tribute|перепе\w+|ремейк|remake)\b", re.I)
_COVER_BRACKET = re.compile(r"[\(\[\{]([^)\]}]*(?:cover|кавер|tribute|перепе)[^)\]}]*)[\)\]\}]", re.I)
_COVER_FILLER = re.compile(
    r"\b(cover|covered|кавер\w*|version|версия|of|by|from|на|песню|песни|песня|трек|song|original|оригинал\w*|исполнении|в|"
    r"acoustic|акустика|live|guitar|гитаре|piano|пианино|my|мой|our|a|the)\b", re.I)
_TRAIL_COVER = re.compile(r"\s*[-–—|:·,]?\s*\b(?:cover|кавер)\b.*$", re.I)
_LEAD = re.compile(r"^\s*(?:the\s+)?(?:original\s+)?(?:song|track|group|band|artist|песн[юиея]|трек|группы|исполнителя)?\s*(?:by|от)?\s+(?=\S)", re.I)
_URL = re.compile(r"https?://\S+|www\.\S+")
_DASH = re.compile(r"\s+[-–—]\s+")
_MARKER = re.compile(
    r"(?:originally\s+(?:performed\s+|written\s+|sung\s+)?by|original\s+(?:song|track|artist|version|performer)?\s*(?:by)?|"
    r"оригинал(?:ьн\w+)?(?:\s+(?:песня|трек|исполнитель|автор|название))?|"
    r"cover\s+(?:of|version\s+of|from)|(?:кавер|перепев)\s+на(?:\s+(?:песню|трек|песни))?|tribute\s+to|"
    r"song\s+by|(?:песня|слова\s+и\s+музыка|автор)\s*[:：-])\s*[:：\-–—]?\s*(.+)", re.I)
_TITLE_LINE = re.compile(r"^\s*(?:song|track|title|песня|трек|название)\s*[:：]\s*(.+)$", re.I | re.M)
_ARTIST_LINE = re.compile(r"^\s*(?:artist|singer|исполнитель|оригинал(?:ьный)? исполнитель)\s*[:：]\s*(.+)$", re.I | re.M)
_PROVIDED = re.compile(r"Provided to YouTube by [^\n]+\n+\s*(.+?) · (.+?)(?: · [^\n]+)?\n", re.I)


def is_cover(info: dict) -> bool:
    return bool(_COVER.search(f"{info.get('title', '')} {' '.join(info.get('tags') or [])}")
                or _COVER.search((info.get("description") or "")[:300]))


def _name(text: str) -> str:
    text = _URL.sub(" ", text or "")
    text = re.sub(r"#\w+|[«»“”\"'`*_]+|\(.*?official.*?\)", " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -–—_.,:;|·•")
    return text[:80] if re.search(r"\w", text) else ""


def _pair(text: str) -> tuple[str, str] | None:
    """'Artist - Title' / '"Title" by Artist' -> (artist, title)."""
    parts = _DASH.split(text, maxsplit=1)
    if len(parts) == 2 and all(_name(p) for p in parts):
        return _name(parts[0]), _name(parts[1])
    m = re.match(r"\s*[«“\"]?(.+?)[»”\"]?\s+(?:by|от)\s+(.+)$", text, re.I)
    if m and _name(m.group(1)) and _name(m.group(2)):
        return _name(m.group(2)), _name(m.group(1))
    return None


def _from_description(desc: str, base_title: str) -> list[tuple[str | None, str | None]]:
    """(artist, title) guesses from what the uploader wrote; either side may be None = 'ask by the other'."""
    out: list[tuple[str | None, str | None]] = []
    desc = _URL.sub(" ", desc or "")
    if m := _PROVIDED.search(desc + "\n"):
        out.append((_name(m.group(2).split(",")[0]), _name(m.group(1))))
    for m in _MARKER.finditer(desc):
        value = m.group(1).split("\n")[0][:140]
        value = _LEAD.sub("", value)
        value = re.split(r"[.!?](?:\s|$)|\s[,;(]\s*", value)[0]
        if pair := _pair(value):
            out.append(pair)
        elif _name(value):
            out.append((_name(value), base_title or None))
            out.append((None, _name(value)))
    artist = _ARTIST_LINE.search(desc)
    title = _TITLE_LINE.search(desc)
    if artist or title:
        out.append((_name(artist.group(1)) if artist else None, _name(title.group(1)) if title else base_title or None))
    return [(a, t) for a, t in out if a or t]


def candidates(info: dict) -> list[tuple[str | None, str]]:
    """Ordered (artist, title) guesses; artist None means 'search by title alone'."""
    artist, title = guess(info)
    cover = is_cover(info)
    head = (info.get("title") or "").split("|")[0]
    bracket_artists = []
    for m in _COVER_BRACKET.finditer(head):
        rest = _name(_COVER_FILLER.sub(" ", m.group(1)))
        if rest:
            bracket_artists.append(rest)
    base = _name(_TRAIL_COVER.sub("", _COVER_BRACKET.sub(" ", head)))
    base_pair = _pair(base)
    base_title = base_pair[1] if base_pair else title or base
    if not cover and not base_pair:
        base_title = title or base

    found: list[tuple[str | None, str | None]] = []
    found += _from_description(info.get("description") or "", base_title)
    found += [(a, base_title) for a in bracket_artists]
    found.append((artist, title))
    if cover and base_pair:  # 'Performer - Song (cover)': the performer is not the artist, so ask by title alone
        found += [(base_pair[0], base_pair[1]), (None, base_pair[1]), (base_pair[1], base_pair[0])]
    found.append((None, base_title or title))
    # 'Artist A - Artist B - Song' or a title with a stray dash: every dash-separated piece may be the real name
    parts = [p for p in (_name(x) for x in _DASH.split(_TRAIL_COVER.sub("", head))) if p]
    if len(parts) >= 2:
        found += [(None, p) for p in reversed(parts)]
        found += [(p, parts[-1]) for p in parts[:-1]]
    # whatever sits in brackets is more often a tag than part of the name: try the bare title last
    found += [(a, _ANY_BRACKET.sub("", t).strip(" -_.")) for a, t in ((artist, title), (None, base_title or title))
              if _ANY_BRACKET.search(t or "")]
    if cover:
        found += [(t, base_title or title) for t in (info.get("tags") or [])[:6] if len(t) > 2]

    seen, out = set(), []
    for a, t in found:
        t = t or base_title or title
        key = (frozenset(uploads._tokens(a or "")), frozenset(uploads._tokens(t or "")))
        if not t or key in seen or not uploads._tokens(t):
            continue
        seen.add(key)
        out.append((a or None, t))
    return out[:MAX_CANDIDATES]


def pick(title: str, duration: float, hits: list[dict], *, min_coverage: float = MIN_TITLE_COVERAGE,
         slack: float = 0.05) -> dict | None:
    """Best search hit: the wanted title words present, similar length, synced preferred."""
    want = uploads._tokens(title)
    if not want:
        return None
    best, best_key = None, None
    for h in hits:
        coverage = len(want & uploads._tokens(h.get("trackName") or "")) / len(want)
        if coverage < min_coverage or not (h.get("syncedLyrics") or h.get("plainLyrics")):
            continue
        dur = h.get("duration")
        gap = abs(dur - duration) if dur and duration else None
        if gap is not None and gap > max(6 if slack <= 0.05 else 15, duration * slack):
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


def _fetch(client: httpx.Client, artist: str | None, title: str, duration: float, *, loose: bool) -> dict | None:
    """One (artist, title) guess against LRCLIB. `loose` (covers) forgives a different song length."""
    slack = 0.12 if loose else 0.05
    if artist and duration:
        hit = _get(client, "get", artist_name=artist, track_name=title, duration=round(duration))
        if isinstance(hit, dict) and _entry_or_none(hit):
            return hit
    searches = [{"track_name": title, "artist_name": artist} if artist else {"track_name": title}, {"q": f"{artist or ''} {title}".strip()}]
    for params in searches:
        hits = _get(client, "search", **params)
        if isinstance(hits, list) and (found := pick(title, duration, hits, min_coverage=0.6 if artist else 0.8, slack=slack)):
            return found
    return None


def _search(query: str, duration: float) -> dict | None:
    """The user's own query: no guessing, just the best hit."""
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        hits = _get(client, "search", q=query)
        return pick(query, duration, hits, slack=0.12) if isinstance(hits, list) else None


def _fetch_source(src: tuple[str | None, str], duration: float) -> dict | None:
    """The lyrics source a user named for a mashup: its times belong to another recording, so any length goes."""
    artist, title = src
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        return _fetch(client, artist, title, duration, loose=True) or (
            _fetch(client, None, title, duration, loose=True) if artist else None)


def _guess_and_fetch(info: dict, duration: float) -> dict | None:
    loose = is_cover(info)
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        for artist, title in candidates(info):
            if hit := _fetch(client, artist, title, duration, loose=loose):
                return hit
    return None


MIN_HEARD_MATCH = 0.45
MAX_ORIGINALS = 12


def _words(text: str) -> list[str]:
    return genius.norm(text).split()


def heard_match(heard: list[str], hit: dict) -> float:
    """Share of the recognised words that the candidate's lyrics contain (0..1); order and misheard words are forgiven."""
    have = set(_words(_STAMP.sub(" ", hit.get("plainLyrics") or hit.get("syncedLyrics") or "")))
    return sum(w in have for w in heard) / len(heard) if heard and have else 0.0


def _originals(client: httpx.Client, info: dict) -> list[dict]:
    """LRCLIB entries for the song's title alone (any artist, any length): possible originals of a cover."""
    seen, out = set(), []
    for _, title in candidates(info):
        key = frozenset(uploads._tokens(title))
        if key in seen:
            continue
        seen.add(key)
        want = uploads._tokens(title)
        for params in ({"track_name": title}, {"q": title}):
            hits = _get(client, "search", **params)
            for h in hits if isinstance(hits, list) else []:
                if (h.get("syncedLyrics") or h.get("plainLyrics")) and want & uploads._tokens(h.get("trackName") or "") \
                        and len(want & uploads._tokens(h.get("trackName") or "")) / len(want) >= MIN_TITLE_COVERAGE \
                        and all(h.get("id") != o.get("id") for o in out):
                    out.append(h)
    return out[:MAX_ORIGINALS]


def find_original(info: dict, heard_lines: list[dict]) -> tuple[dict, float] | None:
    """The original a cover was made from: LRCLIB entries found by title alone, scored against the words
    recognised in the audio. Synced entries win ties. None when nothing is convincing."""
    heard = [w for ln in heard_lines for w in _words(ln.get("text") or "")]
    if len(heard) < 8:
        return None
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        scored = [(heard_match(heard, h), bool(h.get("syncedLyrics")), h) for h in _originals(client, info)]
    scored = [s for s in scored if s[0] >= MIN_HEARD_MATCH]
    if not scored:
        return None
    score, _, hit = max(scored, key=lambda s: (round(s[0], 2), s[1]))
    return hit, score


MIN_WORDS_MATCH = 0.6  # share of the recognised words a text must contain to be taken for the same song
MAX_WEB_PAGES = 5


def _allow_words(info: dict) -> set[str]:
    """Words that are real for this song though a dictionary may not list them: its title and artist."""
    artist, title = guess(info)
    return set(_words(f"{artist or ''} {title} {info.get('title') or ''}"))


def _library_texts(skip: str) -> list[dict]:
    """Published lyrics of other library songs (found by name, so trusted): possible texts of this recording."""
    out = []
    for item in library.list_library():
        if item["id"] == skip:
            continue
        try:
            data = json.loads((library.video_dir(item["id"]) / "lyrics.json").read_text())
        except (OSError, ValueError):
            continue
        if data.get("source") in ("lrclib", "genius") and sum(1 for ln in data.get("lines", []) if ln.get("text")) >= 4:
            out.append({"url": data.get("web"), "artist": data.get("artist"), "title": data.get("title"),
                        "lines": [{"text": ln["text"], **({"sec": ln["sec"]} if ln.get("sec") else {})}
                                  for ln in data["lines"] if ln.get("text")]})
    return out


def _text_match(heard: list[str], lines: list[dict]) -> float:
    return heard_match(heard, {"plainLyrics": " ".join(ln["text"] for ln in lines if ln.get("text"))})


def find_by_words(video_id: str, heard_lines: list[dict]) -> dict | None:
    """Published lyrics of this recording found by what is sung, when the title led nowhere: first the lyrics already
    in the library (other uploads, covers, karaoke cuts of the same song), then a web search for the sung lines.
    The text must contain most of the recognised words. {'url', 'artist', 'title', 'lines', 'score'} or None."""
    heard = [w for ln in heard_lines for w in _words(ln.get("text") or "")]
    if len(heard) < 8:
        return None
    local = [(_text_match(heard, c["lines"]), c) for c in _library_texts(video_id)]
    if local and (best := max(local, key=lambda x: x[0]))[0] >= MIN_WORDS_MATCH:
        return {**best[1], "score": round(best[0], 2)}
    sung = sorted((ln["text"] for ln in heard_lines if len(_words(ln["text"])) >= 4), key=len, reverse=True)[:2]
    if not sung:
        return None
    found, seen = None, set()
    try:
        with httpx.Client(headers={"User-Agent": genius.UA, "Accept-Language": "en,ru;q=0.8"}, timeout=genius.TIMEOUT,
                          follow_redirects=True) as client:
            urls: list[str] = []
            for q in (f'"{sung[0]}" lyrics genius', f'"{" ".join(sung)[:120]}" genius lyrics'):
                urls += [u for u in genius.search_urls(client, q) if u not in urls]
            for url in urls[:MAX_WEB_PAGES]:
                if url in seen or not (r := genius._get(client, url)):
                    continue
                seen.add(url)
                lines = [ln for ln in genius.parse_lyrics(r.text) if ln["text"]]
                score = _text_match(heard, lines)
                if score >= MIN_WORDS_MATCH and (found is None or score > found["score"]):
                    got = genius.page_title(r.text) or ("", "")
                    found = {"url": url, "artist": got[0], "title": got[1], "lines": genius.parse_lyrics(r.text), "score": round(score, 2)}
    except Exception as e:  # a bonus lookup: any failure leaves the recognised text as it is
        log.info("lookup by words failed: %s", e)
    return found


def _entry_or_none(hit: dict) -> dict | None:
    try:
        return _entry(hit)
    except LyricsError:
        return None


def _enrich(video_id: str, info: dict) -> dict:
    """Library items saved before descriptions were kept get theirs now (once; failures are not retried)."""
    if "description" in info:
        return info
    try:
        info = {**info, **pipeline.fetch_extra(video_id)}
    except Exception as e:  # offline, removed video, ...: lyrics can still be found from the title
        log.info("no description for %s: %s", video_id, e)
        info = {**info, "description": "", "tags": []}
    path = library.video_dir(video_id) / "info.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False))
    os.replace(tmp, path)
    return info


def _web_text(info: dict, query: str | None) -> dict | None:
    """The song's words from Genius, trying the guessed (artist, title) pairs in order; never raises."""
    if query:
        pairs: list[tuple[str | None, str]] = [(None, query)]
    elif src := mashup.source(info, "lyrics"):
        pairs = [src]
    else:
        pairs = [(a, t) for a, t in candidates(info) if a][:4] or [(None, t) for _, t in candidates(info)[:1]]
    try:
        with httpx.Client(headers={"User-Agent": genius.UA, "Accept-Language": "en,ru;q=0.8"}, timeout=genius.TIMEOUT,
                          follow_redirects=True) as client:
            for artist, title in pairs:
                if page := genius.fetch(artist, title, client=client):
                    return page
    except Exception as e:  # the web text is a bonus: any failure falls through to the other sources
        log.info("genius lookup failed: %s", e)
    return None


def _label_sections(lines: list[dict], web: list[dict]) -> list[dict]:
    """Copy Genius' [Chorus]/[Verse] labels onto timed lines that match a web line (in order, fuzzy)."""
    labelled = [(genius.norm(w["text"]), w["sec"]) for w in web if w.get("sec") and w["text"]]
    if not labelled:
        return lines
    texts = [genius.norm(w["text"]) for w in web if w["text"]]
    secs = {genius.norm(w["text"]): w["sec"] for w in web if w.get("sec") and w["text"]}
    pos, out = 0, []
    for ln in lines:
        n = genius.norm(ln["text"])
        hit = next((k for k in range(pos, min(pos + 6, len(texts))) if n and genius.similarity(n, texts[k]) >= 0.8), None)
        if hit is not None:
            pos = hit + 1
            if texts[hit] in secs:
                ln = {**ln, "sec": secs[texts[hit]]}
        out.append(ln)
    return out


def _timed_from_web(video_id: str, web: list[dict]) -> list[dict] | None:
    """Genius words laid onto the vocals (forced alignment); None when the audio cannot be aligned."""
    spoken = [w for w in web if w["text"]]
    try:
        timing = align.align(video_id, [w["text"] for w in spoken])
    except align.AlignError as e:
        log.info("alignment failed for %s: %s", video_id, e)
        return None
    out = []
    for w, tm in zip(spoken, timing, strict=True):
        if tm:
            out.append({**tm, "text": w["text"], **({"sec": w["sec"]} if w.get("sec") else {})})
    return out if len(out) >= 3 else None


def _store(video_id: str, result: dict) -> dict:
    path = library.video_dir(video_id) / "lyrics.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, path)
    return result


_lock = threading.Lock()  # one lookup at a time: recognising a song is heavy and a second tab must not repeat it


DRIFT = 0.4  # seconds: a published LRC that trails or leads the vocals by more than this belongs to another take


def _drifted(video_id: str, lines: list[dict]) -> list[dict] | None:
    """The words laid onto these vocals, when the published times sit off them by more than DRIFT (typically a
    karaoke or re-recorded cut of the same song); None when the times fit or nothing could be aligned."""
    spoken = [ln for ln in lines if ln.get("text") and ln.get("t") is not None]
    try:
        timing = align.align(video_id, [ln["text"] for ln in spoken])
    except align.AlignError as e:
        log.info("alignment failed for %s: %s", video_id, e)
        return None
    pairs = [(ln, tm) for ln, tm in zip(spoken, timing, strict=True) if tm]
    if len(pairs) < 3:
        return None
    gaps = sorted(tm["t"] - ln["t"] for ln, tm in pairs)
    if abs(gaps[len(gaps) // 2]) <= DRIFT:
        return None
    return [{**tm, "text": ln["text"], **({"sec": ln["sec"]} if ln.get("sec") else {})} for ln, tm in pairs]


def lookup(video_id: str, query: str | None = None, *, refresh: bool = False, listen: bool = False) -> dict:
    """Lyrics for a library item. `query` overrides the guessed artist and title; `listen` skips the
    databases and recognises the words from the audio."""
    path = library.video_dir(video_id) / "lyrics.json"
    with _lock:
        if not (refresh or query or listen):
            try:
                cached = json.loads(path.read_text())
                # audio-only lyrics saved before the title cleanup may have had a published version all along: look once more
                if cached.get("version") == SCHEMA and not (cached.get("source") == "audio" and cached.get("rev") != AUDIO_REV):
                    return cached
            except (OSError, ValueError):
                pass
        info = library.read_info(video_id)
        if not info:
            raise ValueError("unknown video")
        duration = float(info.get("duration") or 0)
        hit, heard, original = None, None, None
        mash = None if query or listen else mashup.source(info, "lyrics")
        if not listen:
            if not query:
                info = _enrich(video_id, info)
            try:
                if mash:
                    hit = _fetch_source(mash, duration)
                elif query:
                    hit = _search(query, duration)
                else:
                    if is_cover(info):  # the uploader is not the artist: check candidates against the sung words
                        try:
                            heard = transcribe.transcribe(video_id, _allow_words(info))
                            if found := find_original(info, heard["lines"]):
                                hit, score = found
                                original = {"artist": hit.get("artistName"), "title": hit.get("trackName"), "match": round(score, 2)}
                        except transcribe.TranscribeError as e:
                            log.info("cannot verify cover %s: %s", video_id, e)
                    hit = hit or _guess_and_fetch(info, duration)
            except httpx.HTTPError as e:
                if query:
                    raise LyricsError("Could not reach the lyrics service. Check your connection.") from e
                log.warning("lyrics service unreachable: %s", e)  # fall through to recognising the audio
        entry = _entry(hit) if hit else None
        artist, title = guess(info)
        if original and not mash:
            web = _web_text(info, f"{original['artist']} {original['title']}")
        else:
            web = None if listen else _web_text(info, query)
        # a cover or another cut: LRCLIB's times belong to the original recording, so lay the words onto these vocals
        if entry and not query and (mash or is_cover(info) or abs((hit.get("duration") or duration) - duration) > 1.5) and (
                timed := _timed_from_web(video_id, [{"text": ln["text"]} for ln in entry["lines"]])):
            entry = {"synced": True, "lines": timed, "aligned": True}
        if entry and entry["synced"] and not entry.get("aligned") and not query and (timed := _drifted(video_id, entry["lines"])):
            entry = {"synced": True, "lines": timed, "aligned": True}
        if entry and entry["synced"]:  # human-timed LRC wins on timing; Genius adds the section labels
            if web:
                entry["lines"] = _label_sections(entry["lines"], web["lines"])
            return _store(video_id, {
                "version": SCHEMA, "source": "lrclib", "lrclibId": hit.get("id"), "web": web["url"] if web else None,
                "artist": hit.get("artistName") or artist, "title": hit.get("trackName") or title, **entry,
                **({"original": original} if original else {}),
            })
        if entry and not entry["synced"] and not web and not query and (
                timed := _timed_from_web(video_id, [{"text": ln["text"]} for ln in entry["lines"]])):
            entry = {"synced": True, "lines": timed, "aligned": True}
            return _store(video_id, {
                "version": SCHEMA, "source": "lrclib", "lrclibId": hit.get("id"), "web": None,
                "artist": hit.get("artistName") or artist, "title": hit.get("trackName") or title, **entry,
                **({"original": original} if original else {}),
            })
        if web and not query and (timed := _timed_from_web(video_id, web["lines"])):
            return _store(video_id, {
                "version": SCHEMA, "source": "genius", "web": web["url"], "artist": web["artist"] or artist,
                "title": web["title"] or title, "synced": True, "lines": timed,
                **({"original": original} if original else {}),
            })
        if entry or web:
            lines = entry["lines"] if entry else [{"t": None, **w} for w in web["lines"]]
            return _store(video_id, {
                "version": SCHEMA, "source": "lrclib" if entry else "genius", "lrclibId": hit.get("id") if hit else None,
                "web": web["url"] if web else None, "artist": (hit or {}).get("artistName") or artist,
                "title": (hit or {}).get("trackName") or title, "synced": False, "lines": lines,
                **({"original": original} if original else {}),
            })
        if query:
            raise LyricsError("No lyrics found for this search")
        try:
            heard = heard or transcribe.transcribe(video_id, _allow_words(info))
        except transcribe.TranscribeError as e:
            raise LyricsError(f"No published lyrics found. {e}") from e
        artist, title = guess(info)
        # nothing by that name: what is sung may still be a song we know, under another name
        if (found := find_by_words(video_id, heard["lines"])) and (timed := _timed_from_web(video_id, found["lines"])):
            return _store(video_id, {
                "version": SCHEMA, "source": "genius", "web": found["url"], "artist": found["artist"] or artist,
                "title": found["title"] or title, "synced": True, "lines": timed, "matched": "words",
                "original": {"artist": found["artist"], "title": found["title"], "match": found["score"]},
            })
        return _store(video_id, {
            "version": SCHEMA, "source": "audio", "language": heard["language"], "artist": artist, "title": title,
            "synced": True, "lines": heard["lines"], "rev": AUDIO_REV, "quality": heard.get("quality"),
        })
