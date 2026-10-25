"""Musical analysis: beats, bars, chords and key.

Everything is stored in *beat units*: beat 0 is the first detected beat, beat 1 the next, and
so on, with positions in between interpolated linearly. That keeps the chord chart independent
of tempo drift and lets the client convert to seconds through the beat list.

    library/<id>/structure.json  computed once from the source: bpm, beats, bars, chords, key
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import numpy as np

from . import library
from .config import ffmpeg_exe

log = logging.getLogger(__name__)
Report = Callable[[str, float, str], None]

BTC_REPO = "puar-playground/btc-chord"
BTC_REVISION = "d436f2f664f5107cd987774279b8ce171846e376"  # pinned: the repo ships code we execute
SCHEMA = 1

_NOTE_NAMES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# BTC quality -> canonical quality used by the client
_QUALITY = {
    "maj": "maj", "min": "min", "7": "7", "maj7": "maj7", "min7": "min7", "dim": "dim",
    "aug": "aug", "sus2": "sus2", "sus4": "sus4", "min6": "min6", "maj6": "maj6",
    "dim7": "dim7", "hdim7": "m7b5", "minmaj7": "mmaj7",
}
_TRIADS = {
    "maj": (0, 4, 7), "min": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11),
    "min7": (0, 3, 7, 10), "dim": (0, 3, 6), "aug": (0, 4, 8), "sus2": (0, 2, 7),
    "sus4": (0, 5, 7), "min6": (0, 3, 7, 9), "maj6": (0, 4, 7, 9), "dim7": (0, 3, 6, 9),
    "m7b5": (0, 3, 6, 10), "mmaj7": (0, 3, 7, 11),
}
# Krumhansl-Kessler key profiles
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


class AnalysisError(RuntimeError):
    pass


# ───────────────────────── beat <-> time ─────────────────────────

class BeatMap:
    """Piecewise-linear map between seconds and beat units, extrapolated at both ends."""

    def __init__(self, beats: np.ndarray):
        self.t = np.asarray(beats, dtype=float)
        self.b = np.arange(len(self.t), dtype=float)
        self.head = float(self.t[1] - self.t[0])
        self.tail = float(self.t[-1] - self.t[-2])

    def to_beat(self, sec: float) -> float:
        if sec <= self.t[0]:
            return (sec - self.t[0]) / self.head
        if sec >= self.t[-1]:
            return self.b[-1] + (sec - self.t[-1]) / self.tail
        return float(np.interp(sec, self.t, self.b))


# ───────────────────────── chords ─────────────────────────

def parse_chord(label: str) -> tuple[int, str] | None:
    """'C#:min7' -> (1, 'min7'); None for no-chord / unknown."""
    if not label or label[0] not in _NOTE_NAMES:
        return None
    root_txt, _, qual = label.partition(":")
    pc = _NOTE_NAMES[root_txt[0]]
    for ch in root_txt[1:]:
        pc += 1 if ch == "#" else -1 if ch == "b" else 0
    quality = _QUALITY.get(qual.split("/")[0] or "maj")
    return (pc % 12, quality) if quality else None


def chords_on_beats(timeline: list[dict], beatmap: BeatMap, n_beats: int) -> list[dict]:
    """Snap a BTC timeline onto the beat grid, merging flicker shorter than a beat."""
    per_beat: list[tuple[int, str] | None] = []
    starts = np.array([c["start"] for c in timeline]) if timeline else np.array([])
    for i in range(n_beats):
        lo = beatmap.t[i]
        hi = beatmap.t[i + 1] if i + 1 < n_beats else lo + beatmap.tail
        mid = (lo + hi) / 2
        k = int(np.searchsorted(starts, mid, side="right")) - 1
        per_beat.append(parse_chord(timeline[k]["chord"]) if 0 <= k < len(timeline) and mid <= timeline[k]["end"] else None)

    # A chord seen on only one beat sandwiched between two others is usually noise.
    for i in range(1, n_beats - 1):
        if per_beat[i] != per_beat[i - 1] and per_beat[i - 1] == per_beat[i + 1]:
            per_beat[i] = per_beat[i - 1]

    out: list[dict] = []
    for i, ch in enumerate(per_beat):
        if out and out[-1].get("_c") == ch:
            out[-1]["d"] += 1
        else:
            out.append({"b": i, "d": 1, "_c": ch})
    result = []
    for seg in out:
        c = seg.pop("_c")
        if c:
            result.append({**seg, "r": c[0], "q": c[1]})
    return result


def detect_key(chords: list[dict]) -> dict | None:
    """Krumhansl-Schmuckler on a duration-weighted histogram of chord tones."""
    hist = np.zeros(12)
    for c in chords:
        for iv in _TRIADS.get(c["q"], (0, 4, 7)):
            hist[(c["r"] + iv) % 12] += c["d"] * (2.0 if iv == 0 else 1.0)
    if hist.sum() == 0:
        return None
    best = (-2.0, 0, "major")
    for tonic in range(12):
        for mode, prof in (("major", _MAJOR), ("minor", _MINOR)):
            score = float(np.corrcoef(hist, np.roll(prof, tonic))[0, 1])
            if score > best[0]:
                best = (score, tonic, mode)
    return {"tonic": best[1], "mode": best[2], "score": round(best[0], 3)}


# ───────────────────────── structure ─────────────────────────

def _bars(n_beats: int, beats: np.ndarray, downbeats: np.ndarray) -> tuple[list[int], int]:
    """Beat index of every bar start, and the time signature numerator.

    Downbeat trackers wobble in intros and breaks, so the meter is regularised: the most common
    bar length wins, and bars are laid on the most common downbeat phase.
    """
    idx = sorted({int(np.argmin(np.abs(beats - d))) for d in downbeats})
    if len(idx) < 2:
        return list(range(0, n_beats, 4)), 4
    num = int(min(max(Counter(np.diff(idx).tolist()).most_common(1)[0][0], 2), 12))
    phase = Counter(i % num for i in idx).most_common(1)[0][0]
    bars = list(range(phase, n_beats, num))
    return ([0, *bars] if phase else bars), num


def analyze_structure(source: Path, report: Report) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "source.wav"
        _to_wav(source, wav)
        return _structure_from_wav(wav, report)


def _structure_from_wav(source: Path, report: Report) -> dict:
    from huggingface_hub import snapshot_download
    from transformers import AutoModel

    report("analyze", 0.0, "Finding the beat")
    from beat_this.inference import File2Beats

    beats, downbeats = File2Beats(device="cpu", dbn=False)(str(source))
    if len(beats) < 8:
        raise AnalysisError("Could not find a steady beat in this track")
    beats = np.asarray(beats, dtype=float)
    bmap = BeatMap(beats)

    report("analyze", 0.25, "Recognising chords")
    path = snapshot_download(BTC_REPO, revision=BTC_REVISION)
    model = AutoModel.from_pretrained(path, trust_remote_code=True, device="cpu", revision=BTC_REVISION)
    timeline = model.predict(str(source))
    chords = chords_on_beats(timeline, bmap, len(beats))

    bars, num = _bars(len(beats), beats, np.asarray(downbeats, dtype=float))
    bpm = 60.0 / float(np.median(np.diff(beats)))
    return {
        "version": SCHEMA,
        "bpm": round(bpm, 1),
        "timeSig": [num, 4],
        "beats": [round(float(t), 3) for t in beats],
        "bars": bars,
        "key": detect_key(chords),
        "chords": chords,
    }


# ───────────────────────── audio ─────────────────────────

def _to_wav(src: Path, dest: Path, rate: int = 22050) -> None:
    proc = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-nostdin", "-y", "-i", str(src), "-ac", "1", "-ar", str(rate), str(dest)],
        capture_output=True, check=False,
    )
    if proc.returncode:
        raise AnalysisError("Could not decode audio for analysis")


def _midi_hz(m: int) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


# ───────────────────────── orchestration ─────────────────────────

def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")))
    os.replace(tmp, path)


def read_structure(video_id: str) -> dict | None:
    try:
        data = json.loads((library.video_dir(video_id) / "structure.json").read_text())
    except (OSError, ValueError):
        return None
    return data if data.get("version") == SCHEMA else None


def analyze(video_id: str, model: str, report: Report) -> None:
    """Idempotent: the structure is computed once per video and shared by all models."""
    if read_structure(video_id) is None:
        source = library.find_source(video_id)
        if not source:
            raise AnalysisError("The original download is gone; split the track again")
        structure = analyze_structure(source, report)
        _write(library.video_dir(video_id) / "structure.json", structure)
    report("analyze", 1.0, "Analysis ready")
