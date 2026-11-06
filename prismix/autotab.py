"""Notation written from the recording itself, for the instruments no published tab covers.

Songsterr and Soundslice only carry what someone transcribed. For a stem the tab lacks (a piano, a second guitar, a bass
line nobody wrote down) the separated stem is transcribed to notes by MuScriptor, an open-weight multi-instrument model
(an optional extra: `pip install prismix[autotab]`; its weights are CC-BY-NC, fine for a free study tool). The notes are
laid on the recording's own bar grid, so they line up with the chords and the player without any alignment step.

This is a machine transcription: it is marked `auto` everywhere and is less certain than a published tab.
"""
from __future__ import annotations

import logging
import statistics
import subprocess
import threading

import numpy as np

from . import library
from .config import MODELS, ffmpeg_exe

log = logging.getLogger(__name__)

RATE = 16000  # what the model reads
SIZES = ("small", "medium", "large")
STEM_KINDS = {"guitar": "guitar", "bass": "bass", "piano": "keys", "other": "other", "drums": "drums"}
TUNINGS = {
    "guitar": [64, 59, 55, 50, 45, 40],  # string 0 is the highest, as in the published tabs
    "bass": [43, 38, 33, 28],
    "keys": [84, 48, 12],  # three "strings" an octave-stack apart: any pitch has a fret of 0..36 on one of them
    "other": [84, 48, 12],
    "drums": [],
}
# General MIDI drum note -> staff line of the drum score (the lines the published drum tabs use)
DRUM_LINES = {
    35: 3.5, 36: 3.5, 37: 1.5, 38: 1.5, 39: 1.5, 40: 1.5, 41: 3.5, 42: -0.5, 43: 3, 44: 4.5, 45: 2.5, 46: -0.5, 47: 2, 48: 1,
    49: -1, 50: 0.5, 51: 0, 52: -1, 53: 0, 54: -0.5, 55: -1, 56: 0, 57: -1, 59: 0,
}
DRUM_FOLD = {41: 43, 45: 47, 48: 50}  # no separate line for the lowest toms: they share one with the next one up
RANGE = {"guitar": (40, 40 + 24 + 20), "bass": (28, 28 + 24 + 15)}
MAX_FRET = 24
MATCH = 0.45  # onset/pitch-class F1 of a published bar against the stem, below which the bar is called different from the recording
BEAM, SPAN, KEEP = 8, 4, 24  # fingering search: states kept, widest hand stretch in frets, candidate fingerings kept per chord
MIN_NOTE = 0.04  # seconds; shorter blips are the model's noise
POLY = {"guitar": 6, "bass": 2, "keys": 10, "other": 8, "drums": 6}
_lock = threading.Lock()
_loaded: tuple[str, object] | None = None


class AutoTabError(RuntimeError):
    """Message is safe to show to the user."""


def available() -> bool:
    try:
        import muscriptor  # noqa: F401
    except ImportError:
        return False
    return True


def unload() -> None:
    global _loaded
    _loaded = None


def _model(size: str):
    global _loaded
    if _loaded is None or _loaded[0] != size:
        try:
            from muscriptor import TranscriptionModel
        except ImportError as e:
            raise AutoTabError("Auto-transcription needs the optional 'autotab' extra (pip install 'prismix[autotab]')") from e
        _loaded = None
        try:
            _loaded = (size, TranscriptionModel.load_model(size))
        except Exception as e:  # the weights are gated: the message says how to accept the license and log in
            if type(e).__name__ != "ModelDownloadError":
                raise
            raise AutoTabError(str(e).split("See the")[0].strip()) from e
    return _loaded[1]


def stem_file(video_id: str, stem: str):
    return next((p for m in MODELS if (p := library.model_dir(video_id, m) / f"{stem}.flac").is_file()), None)


def _audio(path):
    import torch

    cmd = [ffmpeg_exe(), "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0 or not out.stdout:
        raise AutoTabError("Could not read the audio of this stem")
    return torch.from_numpy(np.frombuffer(out.stdout, dtype=np.float32).copy()), RATE


def transcribe_stem(path, size: str = "medium", drums: bool = False) -> list[tuple[int, float, float]]:
    """(MIDI pitch, onset, offset) in seconds of every pitched note (or, with `drums`, every drum hit) the model hears in the file."""
    from muscriptor import NoteEndEvent, NoteStartEvent

    audio = _audio(path)
    starts: dict[int, NoteStartEvent] = {}
    ends: dict[int, float] = {}
    with _lock:
        for ev in _model(size).transcribe(audio):
            if isinstance(ev, NoteStartEvent):
                if (ev.instrument == "drums") == drums:
                    starts[ev.index] = ev
            elif isinstance(ev, NoteEndEvent):
                ends[ev.start_event_index] = ev.end_time
    return sorted(
        (ev.pitch, ev.start_time, ends.get(i, ev.start_time + 0.25) if not drums else ev.start_time + 0.1)
        for i, ev in starts.items()
    )


def _beat_pos(beats: list[float], t: float) -> float:
    """Time -> fractional beat index, extrapolating with the nearest beat length outside the tracked beats."""
    if len(beats) < 2:
        return 0.0
    if t <= beats[0]:
        return (t - beats[0]) / (beats[1] - beats[0])
    if t >= beats[-1]:
        return len(beats) - 1 + (t - beats[-1]) / (beats[-1] - beats[-2])
    lo, hi = 0, len(beats) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if beats[mid] <= t else (lo, mid)
    return lo + (t - beats[lo]) / (beats[hi] - beats[lo])


def _strings(kind: str, pitches: list[int]) -> list[list[int]]:
    """[string, fret] for each pitch: the lowest frets, one note per string, notes that fit nowhere dropped."""
    if kind == "drums":
        lanes = {DRUM_LINES.get(DRUM_FOLD.get(p, p)): p for p in pitches}
        return [[line, DRUM_FOLD.get(p, p)] for line, p in lanes.items() if line is not None]
    tuning = TUNINGS[kind]
    top = 36 if kind in ("keys", "other") else MAX_FRET
    lo, hi = RANGE.get(kind, (0, 127))
    used: set[int] = set()
    out = []
    for p in sorted(pitches, reverse=True):
        while kind in RANGE and p < lo:
            p += 12
        while kind in RANGE and p > hi:
            p -= 12
        opts = [(p - t, s) for s, t in enumerate(tuning) if 0 <= p - t <= top and s not in used]
        if not opts:
            continue
        fret, s = min(opts)
        used.add(s)
        out.append([s, fret])
    return out


def _fingerings(kind: str, pitches: list[int]) -> list[tuple[float, float | None, list[list[int]]]]:
    """Playable ways to hold `pitches`: (cost, hand position, [[string, fret]...]), cheapest first. A note that fits nowhere costs a lot."""
    tuning = TUNINGS[kind]
    lo, hi = RANGE[kind]
    ps = []
    for p in sorted(set(pitches), reverse=True)[: POLY[kind]]:
        while p < lo:
            p += 12
        while p > hi:
            p -= 12
        ps.append(p)
    out: list[tuple[float, float | None, list[list[int]]]] = []

    def walk(i: int, used: frozenset, held: list[list[int]], skipped: int) -> None:
        if i == len(ps):
            fretted = [f for _, f in held if f > 0]
            if fretted and max(fretted) - min(fretted) > SPAN:
                return
            pos = statistics.fmean(fretted) if fretted else None
            cost = 6.0 * skipped + 0.08 * (pos or 0) + (0.4 * (max(fretted) - min(fretted)) if fretted else 0)
            out.append((cost, pos, sorted(held)))
            return
        placed = False
        for st, t in enumerate(tuning):
            f = ps[i] - t
            if 0 <= f <= MAX_FRET and st not in used:
                placed = True
                walk(i + 1, used | {st}, held + [[st, f]], skipped)
        if not placed or len(ps) > len(tuning):
            walk(i + 1, used, held, skipped + 1)

    walk(0, frozenset(), [], 0)
    out.sort(key=lambda c: c[0])
    return out[:KEEP]


def finger(kind: str, chords: list[list[int]], hints: list[float | None]) -> list[list[list[int]]]:
    """[string, fret] groups for a run of chords, by beam search over hand movement: the whole line stays in one playable
    position instead of each note taking its own lowest fret. `hints` is where a published tab held the hand there, if any."""
    beam: list[tuple[float, float | None, list]] = [(0.0, None, [])]
    for pitches, hint in zip(chords, hints):
        opts = _fingerings(kind, pitches)
        if not opts:
            beam = [(c, pos, path + [[]]) for c, pos, path in beam]
            continue
        nxt = []
        for cost, pos, path in beam:
            for c, p, held in opts:
                move = 0.5 * abs(p - pos) if p is not None and pos is not None else 0.0
                lead = 0.3 * abs((p if p is not None else 0.0) - hint) if hint is not None else 0.0  # an open string is the nut, far from a high position
                nxt.append((cost + c + move + lead, p if p is not None else pos, path + [held]))
        nxt.sort(key=lambda b: b[0])
        beam = nxt[:BEAM]
    return beam[0][2]


def to_bars(notes: list[tuple[int, float, float]], structure: dict, kind: str, hints: dict[int, float] | None = None) -> list[dict]:
    """Note times -> bars of `[pos, dur, [[string, fret]...]]` on the recording's bar grid, sixteenth-note resolution."""
    beats, starts = structure["beats"], structure["bars"]
    if len(beats) < 2 or not starts:
        return []
    per_bar = [(starts[j + 1] if j + 1 < len(starts) else len(beats)) - s for j, s in enumerate(starts)]
    last = max(per_bar[-1], 1)
    per_bar[-1] = last
    bars: list[dict] = [{"len": round(n / 4, 4), "n": []} for n in per_bar]
    cells: list[dict[int, tuple[list[int], float]]] = [{} for _ in per_bar]  # per bar: grid step -> (pitches, longest length in steps)

    def bar_of(x: float) -> int:
        j = 0
        for k, s in enumerate(starts):
            if s <= x + 1e-6:
                j = k
            else:
                break
        return j

    for pitch, on, off in notes:
        if off - on < MIN_NOTE and kind != "drums":
            continue
        a, b = _beat_pos(beats, on), _beat_pos(beats, off)
        j = bar_of(a)
        if a < starts[0] - 0.5 or a >= starts[-1] + per_bar[-1]:
            continue
        steps = per_bar[j] * 4
        at = min(max(round((a - starts[j]) * 4), 0), steps - 1)
        length = max(1.0, min(round((b - a) * 4), steps - at))
        ps, ln = cells[j].get(at, ([], 0.0))
        if pitch not in ps:
            ps.append(pitch)
        cells[j][at] = (ps, max(ln, length))
    plan = []  # every chord in playing order: (bar, grid step, pitches, steps in the bar, steps until the next chord)
    for j, grid in enumerate(cells):
        steps = per_bar[j] * 4
        order = sorted(grid)
        for k, at in enumerate(order):
            ps, ln = grid[at]
            nxt = order[k + 1] if k + 1 < len(order) else steps
            plan.append((j, at, ps[: POLY[kind] * 2], steps, ln, nxt))
    if kind in RANGE:
        near = sorted(hints or {})
        hint = [(hints or {}).get(min(near, key=lambda b: abs(b - j))) if near and min(abs(b - j) for b in near) <= 8 else None for j, *_ in plan]
        held = finger(kind, [c[2] for c in plan], hint)
    else:
        held = [_strings(kind, c[2])[: POLY[kind]] for c in plan]
    for (j, at, _, steps, ln, nxt), frets in zip(plan, held):
        if frets:
            bars[j]["n"].append([round(at / steps, 4), round(min(ln, nxt - at) / steps, 4), frets])
    return bars


def part(kind: str, name: str, bars: list[dict]) -> dict:
    return {"kind": kind, "name": f"{name} (auto-transcribed)", "tuning": TUNINGS[kind], "bars": bars, "auto": 1}


def density(bars: list[dict]) -> float:
    played = [len(b["n"]) for b in bars if b["n"]]
    return statistics.fmean(played) if played else 0.0


def missing(video_id: str) -> dict[str, str]:
    """{kind: stem} of the separated stems whose instrument the cached tab does not have (all of them when there is no tab)."""
    from . import tabdata

    have = {p.get("kind") for p in ((tabdata.read(video_id) or {}).get("tracks") or {}).values()}
    return {k: s for s, k in STEM_KINDS.items() if k not in have and stem_file(video_id, s)}


def run(video_id: str, size: str = "medium", kinds: list[str] | None = None) -> dict:
    """Transcribe the stems the tab lacks and keep them with the tab; returns the tab ({"found": False} if nothing came out)."""
    from . import analysis, tabdata

    if size not in SIZES:
        raise AutoTabError("Unknown model size")
    structure = analysis.read_structure(video_id)
    if not structure:
        raise AutoTabError("Analyse the track first")
    todo = {k: s for k, s in missing(video_id).items() if kinds is None or k in kinds}
    if not todo:
        raise AutoTabError("Every instrument of this track already has notation, or the track is not split into stems yet")
    parts = {}
    try:
        for kind, stem in todo.items():
            notes = transcribe_stem(stem_file(video_id, stem), size, drums=kind == "drums")
            bars = to_bars(notes, structure, kind)
            if density(bars) > 0:
                parts[kind] = part(kind, kind.capitalize(), bars)
    finally:
        unload()
    if not parts:
        return {"found": False}
    return tabdata.add_auto(video_id, parts, len(structure["bars"]))


def _events(bars: list[dict], tuning: list[int]) -> set[tuple[int, int]]:
    """What a bar sounds like, without its fingering: (eighth of the bar, pitch class) of every note."""
    return {(int(n[0] * 8 + 1e-6), (tuning[s] + f) % 12) for b in bars for n in b["n"] for s, f in n[2] if s < len(tuning)}


def agreement(tab: set[tuple[int, int]], heard: set[tuple[int, int]]) -> float | None:
    """How well the notes a tab writes for one bar match the notes heard there (onset +-1 eighth, pitch class): F1, None if both are silent."""
    if not tab and not heard:
        return None
    if not tab or not heard:
        return 0.0
    def hit(e: tuple[int, int], other: set[tuple[int, int]]) -> bool:
        return any((e[0] + d, e[1]) in other for d in (-1, 0, 1))

    recall = sum(hit(e, heard) for e in tab) / len(tab)
    precision = sum(hit(e, tab) for e in heard) / len(heard)
    return 0.0 if recall + precision == 0 else 2 * recall * precision / (recall + precision)


def hints_of(tab: dict, kind: str) -> dict[int, float]:
    """Where the published tab holds the hand, by recording bar: the median fretted position of its parts of this kind."""
    out: dict[int, float] = {}
    for i, j in enumerate(tab["map"]):
        fr = [f for p in tab["tracks"].values() if p.get("kind") == kind and i < len(p["bars"]) for n in p["bars"][i]["n"] for _, f in n[2] if f > 0]
        if j is not None and fr:
            out[j] = statistics.median(fr)
    return out


def check(video_id: str, size: str = "medium") -> dict:
    """Hold every published part up to the recording. For each instrument whose stem exists, the stem is transcribed and each
    bar of the tab is compared with it. Bars that disagree are flagged, and the bars the recording plays differently are
    written out from the stem as a separate auto part, so a mashup or cover keeps the tab where it is right and has the
    recording's own notes where it is not. Returns the tab ({"found": False} when there is none)."""
    from . import analysis, tabdata

    if size not in SIZES:
        raise AutoTabError("Unknown model size")
    structure = analysis.read_structure(video_id)
    tab = tabdata.read(video_id)
    if not structure or not tab or not tab.get("found") or tab.get("source") == "auto":
        raise AutoTabError("There is no published tab to check yet")
    fixes, flagged = {}, {}
    try:
        for stem, kind in STEM_KINDS.items():
            mine = [p for p in tab["tracks"].values() if p.get("kind") == kind and not p.get("auto")]
            if kind == "drums" or not mine or not stem_file(video_id, stem):
                continue
            heard = to_bars(transcribe_stem(stem_file(video_id, stem), size), structure, kind, hints_of(tab, kind))
            bad, fix = set(), [{"len": b["len"], "n": []} for b in heard]
            for i, j in enumerate(tab["map"]):
                if j is None or j >= len(heard):
                    continue
                got = _events([heard[j]], TUNINGS[kind])
                want = set().union(*(_events([p["bars"][i]], p["tuning"]) for p in mine if i < len(p["bars"])))
                score = agreement(want, got)
                if score is not None and score < MATCH:
                    bad.add(i)
                    fix[j] = heard[j]
            flagged[kind] = bad
            if bad:
                fixes[kind] = part(kind, "Corrected", fix) | {"name": f"{kind.capitalize()} · corrected from the recording (auto)", "origin": f"check:{kind}"}
    finally:
        unload()
    return tabdata.add_auto(video_id, fixes, len(structure["bars"]), flagged)
