"""Chordify's public song page as an independent reference: key, tempo and the chords it uses.

Only the page's embedded summary is read; the timed chord track sits behind an endpoint robots.txt disallows, so it
is not fetched. The summary is a second opinion on our own analysis and a yardstick to rank tab candidates by.
"""
import contextlib
import json
import logging
import re

import httpx

from . import library

log = logging.getLogger(__name__)

PAGE = "https://chordify.net/chords/youtube:{id}"
UA = "Mozilla/5.0 (compatible; prismix)"
_DATA = re.compile(r"window\.__INITIAL_DATA__\s*=\s*")
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_TONES = {"maj": (0, 4, 7), "min": (0, 3, 7)}


def pitch_class(name: str) -> int | None:
    """'F#' -> 6, 'Bb' -> 10."""
    if not name or name[0] not in _PC:
        return None
    return (_PC[name[0]] + name[1:].count("#") - name[1:].count("b")) % 12


def parse_chord(text: str) -> tuple[int, str] | None:
    """'F#:min' -> (6, 'min'); None for 'N' and anything else that is not a chord."""
    root, _, quality = text.partition(":")
    pc = pitch_class(root)
    if pc is None:
        return None
    return pc, "min" if quality.startswith("min") else "maj"


def parse(html: str) -> dict | None:
    m = _DATA.search(html)
    if not m:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(html[m.end():])
        abstract = data["state"]["player"]["songAbstract"]
        info = abstract.get("chordInfo") or {}
        chords = [c for c in map(parse_chord, abstract.get("chordSummary") or []) if c]
        key = parse_chord(info.get("derivedKey") or "")
    except (ValueError, KeyError, TypeError):
        return None
    if not chords:
        return None
    return {
        "key": {"tonic": key[0], "mode": key[1]} if key else None,
        "bpm": info.get("derivedBpm"),
        "chords": [{"r": r, "q": q} for r, q in chords],
    }


def tones(ref: dict) -> set[int]:
    """Every pitch class the reference's chords are made of."""
    return {(c["r"] + i) % 12 for c in ref["chords"] for i in _TONES[c["q"]]}


def fetch(video_id: str) -> dict | None:
    """The reference for a YouTube track, cached next to it ({} when Chordify has none); None when unreachable."""
    path = library.video_dir(video_id) / "chordify.json"
    try:
        return json.loads(path.read_text()) or None
    except (OSError, ValueError):
        pass
    try:
        r = httpx.get(PAGE.format(id=video_id), headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
    except httpx.HTTPError as e:
        log.info("chordify unreachable: %s", e)
        return None
    if r.status_code >= 500:
        return None
    ref = parse(r.text) if r.status_code == 200 else None
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(ref or {}))
    return ref
