"""Guitar and bass tablature from Songsterr or a Soundslice link, aligned bar by bar with the recording.

Nothing is transcribed here. The best matching song is looked up on Songsterr (its site API, no key),
its best guitar and bass parts are downloaded, and the tab's bars are matched against the bars and chords
we detected. A tab that does not fit the recording is dropped, so a song without a trustworthy tab has none.
A Soundslice slice the user links is read from the same notation data its public player loads, and goes through the
same fit. The result is cached in `library/<id>/tabdata.json`.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import re
import urllib.parse
from difflib import SequenceMatcher

import httpx

from . import analysis, library, tabs

log = logging.getLogger(__name__)

SEARCH = "https://www.songsterr.com/api/songs"
META = "https://www.songsterr.com/api/meta/{id}"
PAGE = "https://www.songsterr.com/a/wsa/x-{kind}-tab-s{id}"
PART = "https://dqsljvtekg760.cloudfront.net/{id}/{rev}/{image}/{part}.json"
UA = "Mozilla/5.0 (compatible; prismix)"
MIN_TITLE = 0.6
MIN_SCORE = 0.5  # mean bar similarity (0..1) of the matched bars
MIN_COVERAGE = 0.6  # share of the tab's playing bars that found a bar in the recording
CANDIDATES = 3
SLICE_DATA = "https://www.soundslice.com/slices/{code}/scoredata/"
SLICE_URL = re.compile(r"^(?:https?://)?(?:www\.)?soundslice\.com/slices/([A-Za-z0-9]{3,12})(?:[/?#]|$)")
_SLICE_KINDS = {"guitar": "guitar", "bass-guitar": "bass"}


class TabDataError(RuntimeError):
    pass


def _get(url: str) -> bytes:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
        r.raise_for_status()
        return r.content
    except httpx.HTTPError as e:
        raise TabDataError(f"Could not reach Songsterr: {e}") from e


def _json(url: str):
    raw = _get(url)
    if raw[:2] == b"\x1f\x8b":  # the part files are gzip without a Content-Encoding header
        raw = gzip.decompress(raw)
    try:
        return json.loads(raw)
    except ValueError as e:
        raise TabDataError("Songsterr sent something unreadable") from e


# ───────────────────────── soundslice ─────────────────────────

def slice_code(url: str) -> str:
    m = SLICE_URL.match((url or "").strip())
    if not m:
        raise TabDataError("That is not a Soundslice link (https://www.soundslice.com/slices/...)")
    return m.group(1)


def _slice_json(url: str):
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise TabDataError(f"Could not read that Soundslice page (is the slice public?): {e}") from e
    except ValueError as e:
        raise TabDataError("Soundslice sent something unreadable") from e


def _slice_kind(track: dict) -> str | None:
    if track.get("smp") or track.get("midi", 0) >= 1000:  # drums and other sampled kits
        return None
    kind = _SLICE_KINDS.get(track.get("i") or "")
    if kind:
        return kind
    name = (track.get("name") or "").casefold()
    return "bass" if "bass" in name else "guitar" if "guitar" in name else None


def compact_slice(data: dict, track: int) -> list[dict]:
    """One Soundslice track -> the same bars as `compact`. A note's `string` 0 is the highest string, like Songsterr."""
    rhythm = {r["id"]: r["val"] * (1.5 if r.get("dots") == 1 else 1.75 if r.get("dots") == 2 else 1) for r in data.get("rhythms", [])}
    out, sig = [], [4, 4]
    for m in data.get("bars", []):
        sig = m.get("time") or sig
        length = sig[0] / sig[1]
        notes, voices = [], (m.get("tracks") or [])[track:track + 1]
        for t in voices:
            for v in t.get("voices", []):
                at = 0.0
                for b in v:
                    dur = rhythm.get(b.get("r"), 0.0)
                    frets = [[n["string"], n["fret"]] for n in b.get("notes", []) if "string" in n and "fret" in n]
                    if frets:
                        notes.append([round(at / length, 4), round(dur / length, 4), frets])
                    at += dur
        notes.sort(key=lambda x: x[0])
        bar = {"len": round(length, 4), "n": notes}
        if m.get("time"):
            bar["sig"] = sig
        if m.get("s"):
            bar["mark"] = str(m["s"]).strip()
        out.append(bar)
    return out


def slice_parts(data: dict) -> dict[str, dict]:
    """The first guitar and the first bass part of a slice that have any notes."""
    parts: dict[str, dict] = {}
    for i, t in enumerate(data.get("tracks", [])):
        kind = _slice_kind(t)
        if not kind or kind in parts:
            continue
        bars = compact_slice(data, i)
        if any(b["n"] for b in bars):
            parts[kind] = {"name": " ".join((t.get("name") or kind).split()), "tuning": t["pitches"], "bars": bars}
    return parts


def import_soundslice(video_id: str, url: str) -> dict:
    """Read the linked slice, fit it to the recording, cache and return it."""
    code = slice_code(url)
    info = library.read_info(video_id)
    structure = analysis.read_structure(video_id)
    if not info or not structure:
        raise TabDataError("Analyse the track first")
    head = _slice_json(SLICE_DATA.format(code=code))
    if urllib.parse.urlsplit(head.get("d") or "").hostname != "files.soundslice.com":
        raise TabDataError("That Soundslice slice has no notation to read")
    parts = slice_parts(_slice_json(head["d"]))
    if not parts:
        raise TabDataError("That slice has no guitar or bass tab")
    fitted = fit(parts, structure)
    if not fitted:
        raise TabDataError("That tab does not look like this recording")
    result = {
        "found": True, "source": "soundslice", "songId": code, "url": f"https://www.soundslice.com/slices/{code}/",
        "artist": " ".join((head.get("a") or "").split()), "title": " ".join((head.get("name") or "").split()), **fitted,
        "tracks": {k: {"name": p["name"], "tuning": p["tuning"], "bars": p["bars"]} for k, p in parts.items()},
    }
    _save(video_id, result)
    return result


# ───────────────────────── lookup ─────────────────────────

def candidates(info: dict) -> list[dict]:
    """Songs whose title and artist fit this track, best first."""
    found: dict[int, tuple[float, dict]] = {}
    queries = tabs.guess_query(info)
    # A cover or a re-upload names an artist Songsterr does not know: the title alone still finds the song,
    # and the bar-by-bar fit rejects a different song with the same name.
    queries += [("", t) for t in dict.fromkeys(t for _, t in queries)]
    for artist, title in queries:
        url = f"{SEARCH}?{urllib.parse.urlencode({'pattern': f'{artist} {title}'.strip(), 'size': 8})}"
        try:
            songs = json.loads(_get(url))
        except ValueError:
            continue
        for s in songs:
            if not s.get("hasPlayer") or not s.get("tracks"):
                continue
            t = SequenceMatcher(None, tabs._norm(s["title"]), title).ratio()
            a = SequenceMatcher(None, tabs._norm(s["artist"]), artist).ratio() if artist else 0.5
            if t < MIN_TITLE:
                continue
            score = t * 0.7 + a * 0.3
            if score > found.get(s["songId"], (0, None))[0]:
                found[s["songId"]] = (score, s)
    return [s for _, s in sorted(found.values(), key=lambda x: -x[0])][:CANDIDATES]


def _kind(track: dict) -> str | None:
    name = (track.get("instrument") or "").casefold()
    if track.get("isVocalTrack") or track.get("isEmpty") or "drum" in name:
        return None
    if "bass" in name:
        return "bass"
    return "guitar" if "guitar" in name else None


def pick_tracks(tracks: list[dict]) -> dict[str, int]:
    """Index of the most played guitar and bass part (the part id on Songsterr is the track index)."""
    best: dict[str, tuple[int, int]] = {}
    for i, t in enumerate(tracks):
        k = _kind(t)
        if k and t.get("views", 0) >= best.get(k, (-1, 0))[0]:
            best[k] = (t.get("views", 0), i)
    return {k: i for k, (_, i) in best.items()}


def _image(song_id: int) -> str:
    page = _get(PAGE.format(kind="bass", id=song_id)).decode("utf-8", "replace")
    m = re.search(r'"image":"([^"]+)"', page)
    if not m:
        raise TabDataError("Songsterr page has no tab data")
    return m.group(1)


# ───────────────────────── conversion ─────────────────────────

def compact(part: dict) -> list[dict]:
    """Songsterr part -> [{"len": whole notes, "sig": [n, d] when it changes, "mark": section, "n": [[pos, dur, [[string, fret]...]]]}].

    `pos` and `dur` are fractions of the bar, so they map straight onto the recording's bar time.
    """
    out, sig = [], [4, 4]
    for m in part.get("measures", []):
        sig = m.get("signature") or sig
        length = sig[0] / sig[1]
        notes, at = [], 0.0
        for v in m.get("voices", []):
            at = 0.0
            for b in v.get("beats", []):
                if b.get("graceNote"):
                    continue
                dur = b["duration"][0] / b["duration"][1]
                frets = [[n["string"], n["fret"]] for n in b.get("notes", []) if not n.get("rest") and "fret" in n and "string" in n]
                if frets and not b.get("rest"):
                    notes.append([round(at / length, 4), round(dur / length, 4), frets])
                at += dur
        notes.sort(key=lambda x: x[0])
        bar = {"len": round(length, 4), "n": notes}
        if m.get("signature"):
            bar["sig"] = sig
        if (m.get("marker") or {}).get("text"):
            bar["mark"] = m["marker"]["text"]
        out.append(bar)
    return out


def _chroma(bar: dict, tuning: list[int]) -> list[float]:
    v = [0.0] * 12
    for _, dur, frets in bar["n"]:
        for s, f in frets:
            if 0 <= s < len(tuning):
                v[(tuning[s] + f) % 12] += max(dur, 0.03)
    return v


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _bar_chroma(structure: dict) -> list[list[float]]:
    """Chord triads of the recording, weighted by how long they sound, one vector per bar."""
    bars = structure["bars"]
    nb = len(structure["beats"])
    out = []
    for j, start in enumerate(bars):
        end = bars[j + 1] if j + 1 < len(bars) else nb
        v = [0.0] * 12
        for c in structure["chords"]:
            lap = min(end, c["b"] + c["d"]) - max(start, c["b"])
            if lap <= 0:
                continue
            third = {"min": 3, "dim": 3, "sus2": 2, "sus4": 5}.get(tabs.triad(c["q"]), 4)
            fifth = {"dim": 6, "aug": 8}.get(tabs.triad(c["q"]), 7)
            for iv in (0, third, fifth):
                v[(c["r"] + iv) % 12] += lap * (1.5 if iv == 0 else 1.0)
        out.append(_unit(v))
    return out


def _dot(a: list[float], b: list[float], t: int) -> float:
    return sum(a[(i + t) % 12] * b[i] for i in range(12))


def align(tab: list[list[float]], rec: list[list[float]], t: int) -> tuple[float, float, list[int | None]]:
    """Monotone bar-to-bar match of tab bars (chroma, zero vector = silent) to recording bars.

    Returns (mean similarity of the matched bars, share of playing tab bars that matched, recording bar per tab bar).
    """
    n, m = len(tab), len(rec)
    GAP, BASE = -0.25, 0.4
    sc = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        sc[i][0], back[i][0] = i * GAP, 1
    for i in range(1, n + 1):
        silent = not any(tab[i - 1])
        for j in range(1, m + 1):
            s = 0.0 if silent else _dot(tab[i - 1], rec[j - 1], t) - BASE
            opts = ((sc[i - 1][j - 1] + s, 0), (sc[i - 1][j] + GAP, 1), (sc[i][j - 1] + GAP, 2))
            sc[i][j], back[i][j] = max(opts)
    j = max(range(m + 1), key=lambda k: sc[n][k])  # free trailing recording bars
    i, pair = n, [None] * n
    while i > 0:
        b = back[i][j]
        if b == 0:
            pair[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif b == 1:
            i -= 1
        else:
            j -= 1
    playing = [k for k, v in enumerate(tab) if any(v)]
    hit = [(k, pair[k]) for k in playing if pair[k] is not None]
    if not playing or not hit:
        return 0.0, 0.0, pair
    sim = sum(_dot(tab[k], rec[p], t) for k, p in hit) / len(hit)
    return sim, len(hit) / len(playing), pair


def fit(parts: dict[str, dict], structure: dict) -> dict | None:
    """Pick the transposition and bar mapping that fit best; None when the tab is not this recording."""
    rec = _bar_chroma(structure)
    if len(rec) < 4:
        return None
    n = max(len(p["bars"]) for p in parts.values())
    tab = []
    for i in range(n):
        v = [0.0] * 12
        for p in parts.values():
            if i < len(p["bars"]):
                v = [a + b for a, b in zip(v, _unit(_chroma(p["bars"][i], p["tuning"])), strict=True)]
        tab.append(_unit(v))
    best = max(((*align(tab, rec, t), t) for t in range(12)), key=lambda x: x[0] * x[1])
    sim, cover, pair, t = best
    if t != 0 or sim < MIN_SCORE or cover < MIN_COVERAGE:
        log.info("tab rejected: similarity %.2f, coverage %.2f, transpose %d", sim, cover, t)
        return None
    return {"similarity": round(sim, 3), "coverage": round(cover, 3), "map": pair}


# ───────────────────────── entry points ─────────────────────────

def _path(video_id: str):
    return library.video_dir(video_id) / "tabdata.json"


def read(video_id: str) -> dict | None:
    try:
        return json.loads(_path(video_id).read_text())
    except (OSError, ValueError):
        return None


def _save(video_id: str, data: dict) -> None:
    path = _path(video_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(path)


def _download(song: dict) -> dict[str, dict]:
    sid = song["songId"]
    meta = _json(META.format(id=sid))
    picks = pick_tracks(meta.get("tracks", []))
    if not picks:
        return {}
    image = _image(sid)
    parts = {}
    for kind, idx in picks.items():
        part = _json(PART.format(id=sid, rev=meta["revisionId"], image=image, part=idx))
        bars = compact(part)
        if any(b["n"] for b in bars):
            parts[kind] = {
                "name": part.get("name") or meta["tracks"][idx].get("name", ""),
                "tuning": part.get("tuning") or meta["tracks"][idx].get("tuning", []),
                "bars": bars,
            }
    return parts


def lookup(video_id: str, *, force: bool = False) -> dict:
    """Find, align and cache the tab. Always returns a result; {"found": False} when no tab fits."""
    if not force and (hit := read(video_id)) is not None:
        return hit
    info = library.read_info(video_id)
    structure = analysis.read_structure(video_id)
    if not info or not structure:
        raise TabDataError("Analyse the track first")
    result: dict = {"found": False}
    flaky = False  # a network failure must not be remembered as "no tab"
    for song in candidates(info):
        try:
            parts = _download(song)
        except (TabDataError, KeyError, ValueError, OSError) as e:
            log.info("songsterr %s skipped: %s", song.get("songId"), e)
            flaky = flaky or isinstance(e, TabDataError)
            continue
        if not parts:
            continue
        fitted = fit(parts, structure)
        if fitted:
            result = {
                "found": True, "source": "songsterr", "songId": song["songId"],
                "url": f"https://www.songsterr.com/a/wsa/x-tab-s{song['songId']}",
                "artist": song["artist"], "title": song["title"], **fitted,
                "tracks": {k: {"name": p["name"], "tuning": p["tuning"], "bars": p["bars"]} for k, p in parts.items()},
            }
            break
    if result["found"] or not flaky:
        _save(video_id, result)
    return result
