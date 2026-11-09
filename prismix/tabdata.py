"""Guitar and bass tablature from Songsterr or a Soundslice link, aligned bar by bar with the recording.

Nothing is transcribed here. The best matching song is looked up on Songsterr (its site API, no key),
its pitched parts (every guitar, bass, keys and other melodic part) are downloaded, and the tab's bars are matched
against the bars and chords we detected. A tab that does not fit the recording is dropped, so a song without a trustworthy tab has none.
A Soundslice slice the user links is read from the same notation data its public player loads, and goes through the
same fit. The result is cached in `library/<id>/tabdata.json`; hand edits live beside it in `tabedits.json`,
so a re-download never loses them.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import math
import re
import urllib.parse
from difflib import SequenceMatcher

import httpx

from . import analysis, chordify, library, tabfill, tabs

log = logging.getLogger(__name__)

SEARCH = "https://www.songsterr.com/api/songs"
META = "https://www.songsterr.com/api/meta/{id}"
PAGE = "https://www.songsterr.com/a/wsa/x-{kind}-tab-s{id}"
PART = "https://dqsljvtekg760.cloudfront.net/{id}/{rev}/{image}/{part}.json"
UA = "Mozilla/5.0 (compatible; prismix)"
MIN_TITLE = 0.6
MIN_SCORE = 0.5  # mean bar similarity (0..1) of the matched bars
WEAK_BAR = 0.35  # a matched bar below this chord similarity is flagged as differing from the recording
MIN_COVERAGE = 0.6  # share of the tab's playing bars that found a bar in the recording
CANDIDATES = 5
MIN_ARTIST = 0.5  # a transposed tab by an artist this unlike the track's needs a very close fit
SURE_FIT = 0.75
SLICE_DATA = "https://www.soundslice.com/slices/{code}/scoredata/"
SLICE_URL = re.compile(r"^(?:https?://)?(?:www\.)?soundslice\.com/slices/([A-Za-z0-9]{3,12})(?:[/?#]|$)")
FLAGS = "tshxm"  # a note's flags: tie, slide, hammer/pull, dead, palm mute
FORMAT = 5  # 5: parts are named by their instrument, not the player; 4: drum parts ([staff line, MIDI note] per hit); 3: notes carry flags (tie, slide, hammer, dead, palm mute), beats let-ring and tempo
KINDS = ("guitar", "bass", "keys", "other", "drums")
MAX_PARTS = {"guitar": 6, "bass": 3, "keys": 4, "other": 5, "drums": 2}
MAX_BARS, MAX_STRINGS = 1500, 12
_KEYS = re.compile(r"piano|keys|keyboard|organ|harpsichord|clav|celesta|accordion|harmonium|rhodes|synth|pad\b")
_KEY = re.compile(r"^(guitar|bass|keys|other|drums)([1-9]\d?)?$")


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


def classify(instrument: str, name: str = "") -> str | None:
    """guitar | bass | keys | other for a pitched part, drums for a kit; None for vocals."""
    ins, nm = (instrument or "").casefold(), (name or "").casefold()
    if "vocal" in nm or "voice" in ins:
        return None
    if "drum" in ins or "percussion" in ins:
        return "drums"
    if "bass" in ins:
        return "bass"
    if "guitar" in ins:
        return "guitar"
    if _KEYS.search(ins):
        return "keys"
    return "bass" if "bass" in nm else "guitar" if "guitar" in nm else "other"


def part_key(kind: str, n: int) -> str:
    return kind if n == 1 else f"{kind}{n}"


def _slice_kind(track: dict) -> str | None:
    if track.get("smp") or track.get("midi", 0) >= 1000:  # drums and other sampled kits
        return None
    kind = classify(track.get("i") or "", track.get("name") or "")
    return None if kind == "drums" else kind


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
    """Every pitched part of a slice that has notes: guitar, guitar2, bass, keys, other, ..."""
    parts: dict[str, dict] = {}
    count = dict.fromkeys(KINDS, 0)
    for i, t in enumerate(data.get("tracks", [])):
        kind = _slice_kind(t)
        if not kind or count[kind] >= MAX_PARTS[kind] or not t.get("pitches"):
            continue
        bars = compact_slice(data, i)
        if any(b["n"] for b in bars):
            count[kind] += 1
            parts[part_key(kind, count[kind])] = {
                "kind": kind, "name": " ".join((t.get("name") or kind).split()), "tuning": t["pitches"], "bars": bars,
            }
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
    gapfill(parts, fitted, structure)
    return add_source(video_id, parts, fitted, {
        "source": "soundslice", "origin": f"soundslice:{code}", "url": f"https://www.soundslice.com/slices/{code}/",
        "label": " ".join((head.get("name") or "").split()) or code,
        "artist": " ".join((head.get("a") or "").split()), "title": " ".join((head.get("name") or "").split()),
    })


# ───────────────────────── lookup ─────────────────────────

def candidates(info: dict) -> list[dict]:
    """Songs whose title and artist fit this track, best first."""
    found: dict[int, tuple[float, dict]] = {}
    queries = tabs.guess_query(info)
    # A cover or a re-upload names an artist Songsterr does not know: the title alone still finds the song,
    # and the bar-by-bar fit rejects a different song with the same name.
    queries += [("", t) for t in dict.fromkeys(t for _, t in queries)]
    from . import lyrics  # late: lyrics pulls in the audio stack

    if lyrics.is_cover(info):  # "Song (cover)", "кавер на …": the description and tags name the original
        for artist, title in lyrics.candidates(info):
            pair = (tabs._norm(artist or ""), tabs._norm(title))
            if pair[1] and pair not in queries:
                queries.append(pair)
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


def artist_match(info: dict, song: dict) -> float:
    """How well the song's artist fits the names this track could carry; 0.5 when it names none."""
    known = [a for a, _ in tabs.guess_query(info) if a]
    if not known:
        return 0.5
    name = tabs._norm(song["artist"])
    return max(SequenceMatcher(None, name, a).ratio() for a in known)


def tone_mass(parts: dict[str, dict], shift_t: int, tones: set[int]) -> float:
    """Share of the tab's pitch content (in the recording's key) that is made of the reference chords' tones."""
    total = [0.0] * 12
    for v in _tab_chroma(_lead_parts(parts)):
        total = [a + b for a, b in zip(total, v, strict=True)]
    whole = sum(total)
    return sum(total[(pc + shift_t) % 12] for pc in tones) / whole if whole else 0.0


def rank_candidate(parts: dict[str, dict], fitted: dict, artist: float, ref: dict | None) -> float:
    """One number to compare fitted candidates by: bar fit, then who plays it, whether it needed transposing,
    and how well it sticks to the chords Chordify hears."""
    rank = fitted["similarity"] * fitted["coverage"] * (0.7 + 0.3 * artist) * (0.92 if fitted["shift"] else 1.0)
    if ref:
        t = -fitted["shift"] % 12
        rank *= 0.6 + 0.4 * tone_mass(parts, t, chordify.tones(ref))
    return rank


def _kind(track: dict) -> str | None:
    if track.get("isVocalTrack") or track.get("isEmpty"):
        return None
    return classify(track.get("instrument") or "", track.get("name") or "")


def pick_tracks(tracks: list[dict]) -> dict[str, int]:
    """Part key -> track index (the part id on Songsterr is the track index), most played first within a kind:
    {"guitar": 1, "guitar2": 0, "bass": 2, "keys": 5}."""
    by_kind: dict[str, list[tuple[int, int]]] = {k: [] for k in KINDS}
    for i, t in enumerate(tracks):
        if k := _kind(t):
            by_kind[k].append((t.get("views", 0), i))
    picks: dict[str, int] = {}
    for kind in KINDS:
        ranked = sorted(by_kind[kind], key=lambda x: (-x[0], x[1]))[:MAX_PARTS[kind]]
        for n, (_, i) in enumerate(ranked, 1):
            picks[part_key(kind, n)] = i
    return picks


def _image(song_id: int) -> str:
    page = _get(PAGE.format(kind="bass", id=song_id)).decode("utf-8", "replace")
    m = re.search(r'"image":"([^"]+)"', page)
    if not m:
        raise TabDataError("Songsterr page has no tab data")
    return m.group(1)


# ───────────────────────── conversion ─────────────────────────

def _fret(n: dict) -> list:
    """[string, fret] plus a flag string when the note is tied (t), slid (s), hammered or pulled (h), dead (x) or palm-muted (m)."""
    flags = "".join(c for c, on in (
        ("t", n.get("tie")), ("s", n.get("slide")), ("h", n.get("hp")), ("x", n.get("dead")), ("m", n.get("palmMute")),
    ) if on)
    return [n["string"], n["fret"], flags] if flags else [n["string"], n["fret"]]


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
                frets = [_fret(n) for n in b.get("notes", []) if not n.get("rest") and "fret" in n and "string" in n]
                if frets and not b.get("rest"):
                    beat = [round(at / length, 4), round(dur / length, 4), frets]
                    extra = {}
                    if b.get("letRing"):
                        extra["lr"] = 1
                    bpm = (b.get("tempo") or {}).get("bpm")
                    if bpm:
                        extra["bpm"] = round(bpm)
                    notes.append(beat + [extra] if extra else beat)
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
    for _, dur, frets, *_ in bar["n"]:
        for s, f, *_ in frets:  # a note may carry technique flags after the fret
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


def _lead_parts(parts: dict[str, dict]) -> dict[str, dict]:
    """The parts the fit is judged on: the main guitar and bass (any single pitched part when there are neither)."""
    pitched = {k: p for k, p in parts.items() if p.get("kind") != "drums"}
    lead = {k: p for k, p in pitched.items() if k in ("guitar", "bass")}
    return lead or dict(list(pitched.items())[:1])


def merge_bars(bars: list[dict], offset: int) -> list[dict]:
    """Two tab bars become one: the tab counts the beat twice as fast as the recording's bar tracker (133 vs 66.7 BPM).

    The first `offset` bars stay single (a pickup); an odd bar at the end stays single too.
    """
    out = list(bars[:offset])
    rest = bars[offset:]
    for i in range(0, len(rest), 2):
        a = rest[i]
        if i + 1 >= len(rest):
            out.append(a)
            continue
        b = rest[i + 1]
        total = a["len"] + b["len"]
        notes = []
        for bar, shift in ((a, 0.0), (b, a["len"])):
            for pos, dur, *tail in bar["n"]:
                notes.append([round((shift + pos * bar["len"]) / total, 4), round(dur * bar["len"] / total, 4), *tail])
        merged = {"len": round(total, 4), "n": notes}
        for k in ("sig", "auto"):
            if k in a:
                merged[k] = a[k]
        if mark := a.get("mark") or b.get("mark"):
            merged["mark"] = mark
        if chords := [[round(c[0] * a["len"] / total, 4), c[1]] for c in a.get("ch", [])] + [
                [round((a["len"] + c[0] * b["len"]) / total, 4), c[1]] for c in b.get("ch", [])]:
            merged["ch"] = chords
        out.append(merged)
    return out


def split_bars(bars: list[dict]) -> list[dict]:
    """One tab bar becomes two: the tab counts the beat half as fast as the recording's bar tracker."""
    out = []
    for bar in bars:
        half = round(bar["len"] / 2, 4)
        halves = ({"len": half, "n": []}, {"len": half, "n": []})
        for pos, dur, *tail in bar["n"]:
            k = 1 if pos >= 0.5 - 1e-6 else 0
            at = round((pos - 0.5 * k) * 2, 4)
            halves[k]["n"].append([at, round(min(dur * 2, 1 - at), 4), *tail])
        if bar.get("mark"):
            halves[0]["mark"] = bar["mark"]
        for c in bar.get("ch", []):
            k = 1 if c[0] >= 0.5 - 1e-6 else 0
            halves[k].setdefault("ch", []).append([round((c[0] - 0.5 * k) * 2, 4), c[1]])
        if bar.get("auto"):
            halves[0]["auto"] = halves[1]["auto"] = bar["auto"]
        out.extend(halves)
    return out


def _tab_chroma(parts: dict[str, dict]) -> list[list[float]]:
    n = max(len(p["bars"]) for p in parts.values())
    tab = []
    for i in range(n):
        v = [0.0] * 12
        for p in parts.values():
            if i < len(p["bars"]):
                v = [a + b for a, b in zip(v, _unit(_chroma(p["bars"][i], p["tuning"])), strict=True)]
        tab.append(_unit(v))
    return tab


def fit(parts: dict[str, dict], structure: dict) -> dict | None:
    """Pick the transposition and bar mapping that fit best; None when the tab is not this recording.

    A tab written at double the tracked tempo has bars half as long as the recording's, at half the tempo twice as
    long: the same music counted differently. Pairs of its bars are then merged (or every bar split in two), in place,
    so every part lines up bar for bar.
    """
    lead = _lead_parts(parts)
    rec = _bar_chroma(structure)
    if len(rec) < 4 or not lead:
        return None
    ways = [(None, lambda b: b), ("merged", lambda b: merge_bars(b, 0)), ("merged1", lambda b: merge_bars(b, 1)),
            ("split", split_bars)]
    best = None
    for way, regroup in ways:
        vp = lead if way is None else {k: {**p, "bars": regroup(p["bars"])} for k, p in lead.items()}
        tab = _tab_chroma(vp)
        for t in range(12):
            sim, cover, pair = align(tab, rec, t)
            used = [j for j in pair if j is not None]
            dense = len(used) / (max(used) - min(used) + 1) if used else 0.0  # a reading that skips recording bars is a stretched one
            score = sim * cover * dense * (1.0 if way is None else 0.95)  # the plain reading wins a close call
            if best is None or score > best[0]:
                best = (score, sim, cover, pair, t, way, regroup)
    _, sim, cover, pair, t, way, regroup = best
    if sim < MIN_SCORE + (0.05 if t else 0) or cover < MIN_COVERAGE:
        log.info("tab rejected: similarity %.2f, coverage %.2f, transpose %d", sim, cover, t)
        return None
    if way is not None:
        log.info("tab bars differ in length from the recording's: regrouped (%s)", way)
        for p in parts.values():
            p["bars"] = regroup(p["bars"])
    # the tab is written in another key than the audio at hand (a karaoke cut, a lowered cover): follow the audio
    fitted = {"similarity": round(sim, 3), "coverage": round(cover, 3), "map": pair, "shift": -t if t <= 6 else 12 - t}
    if way is not None:
        fitted["regrouped"] = way
    return fitted


def mark_weak(parts: dict[str, dict], fitted: dict, structure: dict) -> None:
    """Flag (`weak`) the bars where the tab and the recording's chords disagree: a cover that changes the harmony,
    a section the original does not have. Run after gapfill (so the map fits the bar list) and before retune."""
    lead = _lead_parts(parts)
    rec = _bar_chroma(structure)
    if not lead or not rec:
        return
    t = -fitted["shift"] % 12
    tab = _tab_chroma(lead)
    weak = 0
    for i, j in enumerate(fitted["map"][:len(tab)]):
        if j is None or not any(tab[i]) or j >= len(rec):
            continue
        if _dot(tab[i], rec[j], t) < WEAK_BAR:
            weak += 1
            for p in parts.values():
                if i < len(p["bars"]):
                    p["bars"][i]["weak"] = 1
    fitted["weak"] = weak


def gapfill(parts: dict[str, dict], fitted: dict, structure: dict) -> None:
    """Fill the bars the tab left out from its own repeats (see tabfill); updates `fitted` with the new map."""
    fitted["map"], fitted["filled"] = tabfill.fill(parts, fitted["map"], _bar_chroma(structure))
    if fitted["filled"]["copied"] or fitted["filled"]["inserted"]:
        log.info("tab gaps filled: %s", fitted["filled"])


def retune(parts: dict[str, dict], semitones: int) -> None:
    """Move every note by `semitones` in place, an octave back where the fret would leave the neck."""
    if not semitones:
        return
    for p in parts.values():
        if p.get("kind") == "drums":
            continue
        for bar in p["bars"]:
            for note in bar["n"]:
                for fret in note[2]:
                    f = fret[1] + semitones
                    fret[1] = f + 12 if f < 0 else f - 12 if f > 24 else f


# ───────────────────────── entry points ─────────────────────────

def _path(video_id: str):
    return library.video_dir(video_id) / "tabdata.json"


def _edits_path(video_id: str):
    return library.video_dir(video_id) / "tabedits.json"


def _read_edits(video_id: str) -> dict:
    try:
        edits = json.loads(_edits_path(video_id).read_text())
    except (OSError, ValueError):
        return {}
    return edits if isinstance(edits, dict) else {}


def _raw(video_id: str) -> dict | None:
    try:
        return json.loads(_path(video_id).read_text())
    except (OSError, ValueError):
        return None


def _upgrade(tab: dict) -> dict:
    for key, p in tab.get("tracks", {}).items():
        p.setdefault("kind", _KEY.match(key).group(1) if _KEY.match(key) else "other")
    return tab


def read(video_id: str) -> dict | None:
    """The cached tab with the user's edits laid over it (each edited part is flagged `edited`).

    A part the user brought in or edited that the band cloud holds the same copy of is flagged `synced`."""
    tab = _raw(video_id)
    if tab is None or not tab.get("found"):
        return tab
    _upgrade(tab)
    for key, edit in _read_edits(video_id).items():
        base = tab["tracks"].get(key, {})
        tab["tracks"][key] = {**base, **edit, "edited": True}
    synced = read_synced(video_id)
    if synced:
        for p in tab["tracks"].values():
            if _shared(tab, p) and _sig(p) in synced:
                p["synced"] = True
    return tab


_SHARED_SOURCES = ("file", "soundslice")


def _shared(tab: dict, part: dict) -> bool:
    """A part that is the user's own work (an imported file or slice, or an edit) rather than a web download or a transcription."""
    return isinstance(part, dict) and not part.get("auto") and (bool(part.get("edited")) or (part.get("source") or tab.get("source")) in _SHARED_SOURCES)


def shared_sigs(tab: dict | None) -> set[str]:
    """Signatures of the user's own parts of a tab: what the cloud copies carry between devices."""
    if not tab or not isinstance(tab.get("tracks"), dict):
        return set()
    return {_sig(p) for p in tab["tracks"].values() if _shared(tab, p)}


def _synced_path(video_id: str):
    return library.video_dir(video_id) / "tabsync.json"


def read_synced(video_id: str) -> set[str]:
    """The user's parts as they were when this computer last agreed with the cloud copy."""
    try:
        got = json.loads(_synced_path(video_id).read_text())
    except (OSError, ValueError):
        return set()
    return {x for x in got if isinstance(x, str)} if isinstance(got, list) else set()


def write_synced(video_id: str, sigs: set[str]) -> None:
    _write(_synced_path(video_id), sorted(sigs))


def adopt(video_id: str, theirs: dict) -> bool:
    """Make the cloud copy's tab this song's tab, edits included. False (nothing changed) when it is not a usable tab."""
    local = _raw(video_id)
    local_tracks = local["tracks"] if local and local.get("found") and isinstance(local.get("tracks"), dict) else {}
    tracks, edits = {}, {}
    try:
        for key, part in theirs["tracks"].items():
            if not _KEY.match(key) or not isinstance(part, dict) or not isinstance(part.get("tuning"), list):
                return False
            clean = {k: v for k, v in part.items() if k not in ("edited", "synced")}
            if part.get("edited"):
                edit = {k: clean[k] for k in ("kind", "name", "tuning", "bars") if k in clean}
                edit["bars"] = _clean_bars(edit.get("bars"), len(edit["tuning"]))
                edits[key] = edit
                mine = local_tracks.get(key)  # keep the web or file version under the edit when this computer has the same part
                same = isinstance(mine, dict) and mine.get("kind") == clean.get("kind") and mine.get("tuning") == clean.get("tuning")
                tracks[key] = mine if same else clean
            else:
                tracks[key] = clean
    except (TabDataError, KeyError, TypeError):
        return False
    _save(video_id, {**{k: v for k, v in theirs.items() if k != "tracks"}, "tracks": tracks})
    if edits:
        _write(_edits_path(video_id), edits)
    else:
        _edits_path(video_id).unlink(missing_ok=True)
    write_synced(video_id, shared_sigs(read(video_id)))
    return True


def _clean_bars(bars, strings: int) -> list[dict]:
    """Validate a part's bars from the client; raises TabDataError for anything that is not a tab."""
    if not isinstance(bars, list) or not 0 < len(bars) <= MAX_BARS:
        raise TabDataError("Bad tab")
    out = []
    for b in bars:
        try:
            length = float(b["len"])
            notes = []
            for pos, dur, frets, *rest in b["n"]:
                fl = []
                for s_, f, *fx in frets:
                    if not (isinstance(s_, int) and isinstance(f, int) and 0 <= s_ < strings and 0 <= f <= 36):
                        raise ValueError
                    flags = "".join(dict.fromkeys(c for c in str(fx[0]) if c in FLAGS)) if fx else ""
                    fl.append([s_, f, flags] if flags else [s_, f])
                if not fl or not 0 <= float(pos) < 1.0001:
                    raise ValueError
                beat = [round(float(pos), 4), round(max(0.0, float(dur)), 4), fl]
                extra = rest[0] if rest and isinstance(rest[0], dict) else {}
                keep = {}
                if extra.get("lr"):
                    keep["lr"] = 1
                if isinstance(extra.get("bpm"), int) and 20 <= extra["bpm"] <= 400:
                    keep["bpm"] = extra["bpm"]
                if extra.get("st") in ("u", "d"):
                    keep["st"] = extra["st"]
                notes.append(beat + [keep] if keep else beat)
        except (KeyError, TypeError, ValueError) as e:
            raise TabDataError("Bad tab") from e
        if not 0 < length <= 8:
            raise TabDataError("Bad tab")
        bar = {"len": round(length, 4), "n": sorted(notes, key=lambda x: x[0])}
        for k in ("sig", "mark", "auto"):
            if k in b:
                bar[k] = b[k]
        if isinstance(b.get("ch"), list):
            bar["ch"] = [[round(float(c[0]), 4), str(c[1])[:12]] for c in b["ch"] if len(c) == 2 and 0 <= float(c[0]) < 1.0001]
        out.append(bar)
    return out


def save_edit(video_id: str, key: str, bars) -> dict:
    """Keep the user's version of one part; returns the whole tab as the viewers now see it."""
    base = _raw(video_id)
    if not _KEY.match(key) or not base or not base.get("found"):
        raise ValueError("no such part")
    p = _upgrade(base)["tracks"].get(key)
    if not p:
        raise ValueError("no such part")
    edits = _read_edits(video_id)
    edits[key] = {"kind": p["kind"], "name": p["name"], "tuning": p["tuning"], "bars": _clean_bars(bars, len(p["tuning"]))}
    _write(_edits_path(video_id), edits)
    return read(video_id)


def revert_edit(video_id: str, key: str) -> dict | None:
    edits = _read_edits(video_id)
    if edits.pop(key, None) is not None:
        if edits:
            _write(_edits_path(video_id), edits)
        else:
            _edits_path(video_id).unlink(missing_ok=True)
    return read(video_id)


def _write(path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(path)


def _save(video_id: str, data: dict) -> None:
    _write(_path(video_id), data)


def add_auto(video_id: str, parts: dict[str, dict], nbars: int, flagged: dict[str, set[int]] | None = None) -> dict:
    """Keep machine-transcribed parts beside the published ones (or alone when the track has no tab); returns the tab.

    `flagged` {kind: tab bars} marks those bars of the published parts as differing from the recording; a part with an `origin`
    replaces the earlier part of the same origin."""
    raw = _raw(video_id)
    if not raw or not raw.get("found"):
        info = library.read_info(video_id) or {}
        raw = {
            "found": True, "v": FORMAT, "source": "auto", "url": "", "artist": info.get("uploader") or "",
            "title": info.get("title") or "", "similarity": 1.0, "coverage": 1.0, "map": list(range(nbars)),
            "shift": 0, "filled": {"copied": 0, "inserted": 0, "open": []}, "weak": 0, "tracks": {},
        }
    tracks = raw["tracks"]
    if raw["source"] != "auto":  # the tab's bar list is the shared one: take each recording bar the tab bar was matched to
        ref = next(iter(tracks.values()))["bars"]
        for part in parts.values():
            rec = part["bars"]
            part["bars"] = [
                dict(rec[j]) if j is not None and j < len(rec) else {"len": ref[i]["len"], "n": []}
                for i, j in enumerate(raw["map"])
            ]
    for key in [k for k, p in tracks.items() if p.get("origin") and p["origin"] in {x.get("origin") for x in parts.values()}]:
        del tracks[key]
    for kind, bars in (flagged or {}).items():
        tracks = {k: p for k, p in tracks.items() if p.get("origin") != f"check:{kind}" or kind in parts}
        raw["tracks"] = tracks
        for p in tracks.values():
            if p.get("kind") == kind and not p.get("auto"):
                for i in range(len(p["bars"])):
                    p["bars"][i].pop("weak", None)
                    if i in bars:
                        p["bars"][i]["weak"] = 1
    for kind, part in parts.items():
        n = 1
        while part_key(kind, n) in tracks:
            n += 1
        tracks[part_key(kind, n)] = part
    _save(video_id, raw)
    return read(video_id) or raw


def _sig(part: dict) -> str:
    """What makes a part the same music: its kind, tuning and notes (not flags such as `weak`, nor where it came from)."""
    bars = [{k: v for k, v in b.items() if k != "weak"} for b in part.get("bars", [])]
    blob = json.dumps([part.get("kind"), part.get("tuning"), bars], sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()


def _covers(a: dict, b: dict) -> bool:
    """Part `a` already holds all of part `b`: the same instrument, name and tuning, and every bar of `b` that has notes is the same in `a`.
    The same score laid on the bars twice differs only in what the second pass left empty (gap fills, section marks)."""
    if (a.get("kind"), a.get("name"), a.get("tuning")) != (b.get("kind"), b.get("name"), b.get("tuning")):
        return False
    ba, bb = a.get("bars", []), b.get("bars", [])
    if len(ba) != len(bb):
        return False
    return all(y.get("n") == x.get("n") for x, y in zip(ba, bb, strict=True) if y.get("n"))


def _notes(part: dict) -> int:
    return sum(len(b.get("n") or []) for b in part.get("bars", []))


def tidy(video_id: str) -> bool:
    """Drop parts that repeat another part (the same file imported more than once, by different routes). The fuller copy stays;
    a part the user edited is never dropped. True when something was removed."""
    raw = _raw(video_id)
    if not raw or not raw.get("found") or not isinstance(raw.get("tracks"), dict):
        return False
    edits = _read_edits(video_id)
    kept: dict[str, dict] = {}
    for key, part in raw["tracks"].items():
        if part.get("auto") or key in edits:
            kept[key] = part
            continue
        if any(k not in edits and not p.get("auto") and _covers(p, part) and _notes(p) >= _notes(part) for k, p in kept.items()):
            continue  # this one repeats an earlier, fuller part
        for k in [k for k, p in kept.items() if k not in edits and not p.get("auto") and _covers(part, p)]:
            del kept[k]  # an earlier part is contained in this one
        kept[key] = part
    if len(kept) == len(raw["tracks"]):
        return False
    raw["tracks"] = kept
    _save(video_id, raw)
    return True


def add_source(video_id: str, parts: dict[str, dict], fitted: dict, meta: dict) -> dict:
    """Keep a user-supplied score (a Soundslice slice, a file) beside the tab the track already has; returns the tab.

    Each new part is tagged with where it came from (`source`, `origin`, `label`, `url`). Its bars are laid on the tab's
    shared bar list through the recording's bars, so it stays in step with the audio and with every other part. The same
    origin imported again replaces its earlier parts. A track without a tab takes the score as its tab.

    `status` in the result: `new`, `updated` (the origin was imported before and its music changed) or `duplicate`
    (every part is already there, nothing is added).
    """
    for p in parts.values():
        p.update({"source": meta["source"], "origin": meta["origin"], "label": meta["label"], "url": meta.get("url", "")})
    raw = _raw(video_id)
    if not raw or not raw.get("found") or not raw.get("tracks"):
        _save(video_id, {
            "found": True, "v": FORMAT, "source": meta["source"], "songId": meta["origin"], "url": meta.get("url", ""),
            "artist": meta.get("artist", ""), "title": meta.get("title", ""), **fitted, "tracks": parts,
        })
        _edits_path(video_id).unlink(missing_ok=True)  # edits belonged to the tab this one replaces
        return {**(read(video_id) or {}), "added": list(parts), "status": "new"}
    tracks = raw["tracks"]
    ref = next(iter(tracks.values()))["bars"]
    at = {j: k for k, j in reversed(list(enumerate(fitted["map"]))) if j is not None}  # recording bar -> this score's bar
    for part in parts.values():
        own = part["bars"]
        part["bars"] = [
            dict(own[at[j]]) if j is not None and j in at and at[j] < len(own) else {"len": ref[i]["len"], "n": []}
            for i, j in enumerate(raw["map"])
        ]
    if all(any(_covers(t, p) for t in tracks.values()) for p in parts.values()):
        _save(video_id, raw)
        tidy(video_id)  # also removes copies an earlier import left behind
        return {**(read(video_id) or raw), "added": [], "status": "duplicate"}
    old = [k for k, p in tracks.items() if p.get("origin") == meta["origin"]]
    for key in old:
        del tracks[key]
        edits = _read_edits(video_id)
        if edits.pop(key, None) is not None:
            _write(_edits_path(video_id), edits)
    for p in tracks.values():
        p.setdefault("source", raw.get("source") or "songsterr")
        p.setdefault("url", raw.get("url") or "")
    added = []
    for part in parts.values():
        if any(_covers(t, part) for t in tracks.values()):
            continue  # this part is already there (another import brought it)
        n = 1
        while part_key(part["kind"], n) in tracks:
            n += 1
        added.append(part_key(part["kind"], n))
        tracks[added[-1]] = part
    _save(video_id, raw)
    return {**(read(video_id) or raw), "added": added, "status": "updated" if old else "new"}


def _download(song: dict) -> dict[str, dict]:
    sid = song["songId"]
    meta = _json(META.format(id=sid))
    picks = pick_tracks(meta.get("tracks", []))
    if not picks:
        return {}
    image = _image(sid)
    parts = {}
    for key, idx in picks.items():
        try:
            part = _json(PART.format(id=sid, rev=meta["revisionId"], image=image, part=idx))
            bars = compact(part)
        except (TabDataError, KeyError, ValueError) as e:  # one broken part must not lose the others
            log.info("songsterr %s part %s skipped: %s", sid, idx, e)
            continue
        kind = _KEY.match(key).group(1)
        tuning = [] if kind == "drums" else part.get("tuning") or meta["tracks"][idx].get("tuning", [])
        if any(b["n"] for b in bars) and (tuning or kind == "drums"):
            parts[key] = {
                "kind": kind,
                "name": " ".join((meta["tracks"][idx].get("instrument") or part.get("name") or meta["tracks"][idx].get("name") or "").split()),
                "tuning": tuning,
                "bars": bars,
            }
    return parts


def _refresh(video_id: str, hit: dict) -> dict:
    """A tab cached before every part was kept: download the song's parts again, keeping its bar mapping."""
    try:
        parts = _download({"songId": hit["songId"]})
    except (TabDataError, KeyError, ValueError, OSError) as e:
        log.info("tab refresh skipped: %s", e)
        return hit
    if not parts:
        return hit
    raw = {**_raw(video_id), "v": FORMAT, "tracks": parts}
    _save(video_id, raw)
    return read(video_id) or hit


def outdated(video_id: str) -> bool:
    """A downloaded tab made by an older format; lookup fetches it again. Tabs from files and transcriptions are the user's."""
    hit = _raw(video_id)
    return bool(hit and hit.get("found") and hit.get("source") == "songsterr" and hit.get("v", 1) < FORMAT)


def lookup(video_id: str, *, force: bool = False) -> dict:
    """Find, align and cache the tab. Always returns a result; {"found": False} when no tab fits."""
    tidy(video_id)
    if not force and (hit := read(video_id)) is not None:
        if hit.get("found") and hit.get("source") == "songsterr" and hit.get("v", 1) < FORMAT:
            return _refresh(video_id, hit)
        return hit
    info = library.read_info(video_id)
    structure = analysis.read_structure(video_id)
    if not info or not structure:
        raise TabDataError("Analyse the track first")
    result: dict = {"found": False}
    flaky = False  # a network failure must not be remembered as "no tab"
    ref = chordify.fetch(video_id)  # an independent second opinion on the chords; None when unreachable
    best = None
    for song in candidates(info):  # every candidate is scored, the best one becomes the template
        try:
            parts = _download(song)
        except (TabDataError, KeyError, ValueError, OSError) as e:
            log.info("songsterr %s skipped: %s", song.get("songId"), e)
            flaky = flaky or isinstance(e, TabDataError)
            continue
        if not parts:
            continue
        fitted = fit(parts, structure)
        if not fitted:
            continue
        artist = artist_match(info, song)
        if fitted["shift"] and artist < MIN_ARTIST and fitted["similarity"] < SURE_FIT:
            log.info("songsterr %s skipped: another artist, in another key, a loose fit", song["songId"])
            continue
        rank = rank_candidate(parts, fitted, artist, ref)
        log.info("songsterr %s (%s - %s): rank %.3f", song["songId"], song["artist"], song["title"], rank)
        if best is None or rank > best[0]:
            best = (rank, song, parts, fitted)
    if best:
        _, song, parts, fitted = best
        gapfill(parts, fitted, structure)
        mark_weak(parts, fitted, structure)
        retune(parts, fitted["shift"])
        result = {
            "found": True, "v": FORMAT, "source": "songsterr", "songId": song["songId"],
            "url": f"https://www.songsterr.com/a/wsa/x-tab-s{song['songId']}",
            "artist": song["artist"], "title": song["title"], **fitted,
            "tracks": parts,
        }
    if result["found"] or not flaky:
        _save(video_id, result)
        if result["found"] and not force:
            _edits_path(video_id).unlink(missing_ok=True)
    return read(video_id) or result
