"""Look a song up on Songsterr and align its chord sheet with the chords we detected.

Songsterr has no official public API; this uses the same search endpoint and chord pages its
website uses, only when the user asks, once per track, and the result is cached in
`library/<id>/tabs.json`.
"""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.parse
from difflib import SequenceMatcher

import httpx

from . import analysis, library, mashup

log = logging.getLogger(__name__)

SEARCH = "https://www.songsterr.com/api/songs"
PAGE = "https://www.songsterr.com/a/wsa/x-chords-s{id}"
UA = "Mozilla/5.0 (compatible; prismix)"
MIN_TITLE_MATCH = 0.55
MIN_SIMILARITY = 0.35
MASH_REACH = 2

_ROOTS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_TRIAD = {
    "maj": "maj", "maj7": "maj", "7": "maj", "maj6": "maj",
    "min": "min", "min7": "min", "min6": "min", "mmaj7": "min",
    "dim": "dim", "dim7": "dim", "m7b5": "dim", "aug": "aug", "sus2": "sus2", "sus4": "sus4",
}
_SUFFIX = [  # longest first
    ("maj7", "maj7"), ("M7", "maj7"), ("Δ", "maj7"), ("m7b5", "m7b5"), ("ø", "m7b5"), ("dim7", "dim7"),
    ("dim", "dim"), ("°", "dim"), ("aug", "aug"), ("+", "aug"), ("sus2", "sus2"), ("sus4", "sus4"),
    ("sus", "sus4"), ("min7", "min7"), ("m7", "min7"), ("m6", "min6"), ("min", "min"), ("m", "min"),
    ("-", "min"), ("6", "maj6"), ("7", "7"), ("9", "7"), ("11", "7"), ("13", "7"),
]


class TabsError(RuntimeError):
    pass


def parse_chord(text: str) -> tuple[int, str] | None:
    """'F♯m7/C' -> (6, 'min7'); None for anything that is not a chord (N.C., ...)."""
    m = re.match(r"^([A-G])([#♯b♭]?)(.*?)(?:/[A-G][#♯b♭]?)?$", text.strip())
    if not m:
        return None
    root = _ROOTS[m.group(1)] + {"#": 1, "♯": 1, "b": -1, "♭": -1, "": 0}[m.group(2)]
    rest = m.group(3).replace("(", "").replace(")", "")
    for key, q in _SUFFIX:
        if rest.startswith(key):
            return root % 12, q
    return root % 12, "maj"


def triad(q: str) -> str:
    return _TRIAD.get(q, "maj")


# ───────────────────────── lookup ─────────────────────────

def _get(url: str, timeout: float = 15) -> str:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        return r.text
    except httpx.HTTPError as e:
        raise TabsError(f"Could not reach Songsterr: {e}") from e


def _norm(s: str) -> str:
    s = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", s)
    s = re.sub(r"\b(official|video|audio|lyrics?|clip|hd|4k|remaster(ed)?|\d{4})\b", " ", s, flags=re.I)
    return re.sub(r"[^\w]+", " ", s.casefold()).strip()


def guess_query(info: dict) -> list[tuple[str, str]]:
    """Possible (artist, title) readings of a YouTube title, best first."""
    title = re.sub(r"\s*\|.*$", "", info.get("title") or "")
    channel = re.sub(r"\s*-\s*Topic$|VEVO$", "", info.get("channel") or "").strip()
    if src := mashup.source(info, "chords"):  # the user named the song whose chords this cover plays
        artist, name = src
        return [(_norm(artist or ""), _norm(name))] if _norm(name) else []
    if info.get("id") and (known := library.spotify_hint(info["id"])):
        return [(_norm(known[0]), _norm(known[1]))]
    out = []
    parts = re.split(r"\s+[-–—]\s+", title, maxsplit=1)
    if len(parts) == 2:
        out += [(parts[0], parts[1]), (parts[1], parts[0])]
    out.append((channel, title))
    return [(_norm(a), _norm(t)) for a, t in out if _norm(t)]


def find_song(info: dict) -> dict:
    best, best_score = None, 0.0
    for artist, title in guess_query(info):
        url = f"{SEARCH}?{urllib.parse.urlencode({'pattern': f'{artist} {title}'.strip(), 'size': 8})}"
        try:
            songs = json.loads(_get(url))
        except ValueError:
            continue
        for s in songs:
            if not s.get("hasChords"):
                continue
            t = SequenceMatcher(None, _norm(s["title"]), title).ratio()
            a = SequenceMatcher(None, _norm(s["artist"]), artist).ratio() if artist else 0.5
            score = t * 0.7 + a * 0.3
            if t >= MIN_TITLE_MATCH and score > best_score:
                best, best_score = s, score
    if not best:
        raise TabsError("No chord sheet found on Songsterr for this title")
    return best


def song_id_from_url(url: str) -> int:
    m = re.search(r"songsterr\.com/.*-s(\d+)(?:[/?#]|$)", url)
    if not m:
        raise TabsError("That is not a Songsterr chord or tab link")
    return int(m.group(1))


def fetch_sheet(song_id: int) -> dict:
    """Chord sheet page -> {url, title, artist, chords: [name, ...] in playing order}."""
    page = _get(PAGE.format(id=song_id))
    if "data-chord=" not in page:
        raise TabsError("Songsterr has no chord sheet for that song")
    first = page.find("<h2>")
    body = page[first if first >= 0 else 0:]
    names = [html.unescape(n) for n in re.findall(r'data-chord="([^"]+)"', body)]
    canon = re.search(r'<link rel="canonical" href="([^"]+)"', page)
    meta = re.search(r'"artist":"([^"]*)".{0,200}?"title":"([^"]*)"', page)
    return {
        "url": canon.group(1) if canon else PAGE.format(id=song_id),
        "artist": meta.group(1) if meta else "", "title": meta.group(2) if meta else "",
        "chords": names,
    }


# ───────────────────────── alignment ─────────────────────────

def _collapse(seq: list[tuple[int, str]]) -> tuple[list[tuple[int, str]], list[int]]:
    """Merge consecutive equal triads; also return, per input item, its index in the result."""
    out, idx = [], []
    for r, q in seq:
        key = (r, triad(q))
        if not out or out[-1] != key:
            out.append(key)
        idx.append(len(out) - 1)
    return out, idx


def _fit(d: list[tuple[int, str]], ref: list[tuple[int, str]], t: int) -> tuple[float, list[int | None]]:
    """Align all of `d` against a window of `ref` shifted by `t`: (exact matches, ref index per d)."""
    n, m = len(d), len(ref)
    GAP, MISS, SAME_ROOT, HIT = -1.0, -1.0, 0.4, 2.0
    sc = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        sc[i][0], back[i][0] = i * GAP, 1
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            r, q = ref[j - 1]
            r = (r + t) % 12
            s = HIT if (r, q) == d[i - 1] else SAME_ROOT if r == d[i - 1][0] else MISS
            opts = ((sc[i - 1][j - 1] + s, 0), (sc[i - 1][j] + GAP, 1), (sc[i][j - 1] + GAP, 2))
            sc[i][j], back[i][j] = max(opts)
    j = max(range(m + 1), key=lambda k: sc[n][k])  # free trailing reference
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
    exact = sum(1 for k, p in enumerate(pair) if p is not None and ((ref[p][0] + t) % 12, ref[p][1]) == d[k])
    return exact, pair


def trusted(marks: list[dict | None], reach: int = MASH_REACH) -> list[dict | None]:
    """Keep a mark only if a chord within `reach` neighbours on either side agrees exactly: where the sheet's song
    and the recording part ways (another structure, another arrangement) the detected chords stand."""
    return [m if m and any(n and n["ok"] for n in marks[max(0, i - reach):i + reach + 1]) else None
            for i, m in enumerate(marks)]


def align(detected: list[dict], names: list[str], *, strict: bool = False) -> dict | None:
    """Match detected chords ({r, q}) against a sheet's chord names.

    Returns {similarity, transpose, marks} where marks has one entry per detected chord:
    None (no counterpart), or {"ok": bool, "name": sheet chord, "r": .., "q": ..} in the recording's key.
    """
    parsed = [p for p in (parse_chord(n) for n in names) if p]
    if len(detected) < 4 or len(parsed) < 4:
        return None
    d, didx = _collapse([(c["r"], c["q"]) for c in detected])
    ref = [(r, triad(q)) for r, q in parsed]
    best = max(((*_fit(d, ref, t), t) for t in range(12)), key=lambda x: x[0])
    exact, pair, t = best
    sim = exact / len(d)
    if sim < MIN_SIMILARITY:
        return {"similarity": round(sim, 3), "transpose": t, "marks": None}
    marks = []
    for k, c in enumerate(detected):
        p = pair[didx[k]]
        if p is None:
            marks.append(None)
            continue
        r, q = parsed[p]
        r = (r + t) % 12
        marks.append({"ok": (r, triad(q)) == (c["r"], triad(c["q"])), "name": names[p] if parse_chord(names[p]) else "", "r": r, "q": q})
    return {"similarity": round(sim, 3), "transpose": t if t <= 6 else t - 12, "marks": trusted(marks) if strict else marks}


# ───────────────────────── entry points ─────────────────────────

def _path(video_id: str):
    return library.video_dir(video_id) / "tabs.json"


def read(video_id: str) -> dict | None:
    try:
        return json.loads(_path(video_id).read_text())
    except (OSError, ValueError):
        return None


def lookup(video_id: str, url: str | None = None) -> dict:
    """Find (or use the given) Songsterr sheet, align it, cache and return the result."""
    info = library.read_info(video_id)
    structure = analysis.read_structure(video_id)
    if not info or not structure:
        raise TabsError("Analyse the track first")
    song_id = song_id_from_url(url) if url else find_song(info)["songId"]
    sheet = fetch_sheet(song_id)
    result = align(structure["chords"], sheet["chords"], strict=bool(mashup.source(info, "chords")))
    if result is None or result["marks"] is None:
        raise TabsError(
            "That sheet does not look like this recording"
            + (f" (only {round(result['similarity'] * 100)}% of the chords agree)" if result else ""))
    out = {"source": "songsterr", "songId": song_id, "url": sheet["url"], "artist": sheet["artist"], "title": sheet["title"], **result}
    path = _path(video_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False))
    tmp.replace(path)
    return out
