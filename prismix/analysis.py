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
from .config import MODELS, ffmpeg_exe

log = logging.getLogger(__name__)
Report = Callable[[str, float, str], None]

BTC_REPO = "puar-playground/btc-chord"
BTC_REVISION = "d436f2f664f5107cd987774279b8ce171846e376"  # pinned: the repo ships code we execute
SCHEMA = 2  # 2: chords decoded from probabilities (see 'soft decoding'); older charts are recomputed on demand

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

    return _segments(per_beat)


def _segments(per_beat: list[tuple[int, str] | None]) -> list[dict]:
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


# ───────────────────────── chords: soft decoding ─────────────────────────
#
# The stock BTC predict() takes the arg-max of each 10 s window independently, so every window edge is a
# blind spot and one wrong frame is a wrong chord. Instead the network's probabilities are
#   1. computed on overlapping windows (50 %) and blended with a triangular weight,
#   2. averaged over +/-1 semitone shifted copies of the spectrogram (shifting 2 CQT bins = 1 semitone,
#      the labels are rotated back), which cancels much of the model's key bias,
#   3. pooled per beat, and
#   4. decoded with Viterbi where changing chord is cheap on a downbeat and expensive off the beat.

_FRAME = 10.0 / 108  # seconds per BTC frame (the model's own time unit)
_WIN = 108
_N_CLASSES = 170
_NO_CHORD, _UNKNOWN = 169, 168
_SEMITONE_SHIFTS = (-1, 0, 1)
_SWITCH_DOWNBEAT = 2.0  # Viterbi cost (nats) of changing chord on a downbeat ...
_SWITCH_OFFBEAT = 3.5  # ... and on any other beat
_BATCH = 16


def _shift_bins(feat: np.ndarray, bins: int) -> np.ndarray:
    """Move CQT content up by `bins` rows, filling the vacated rows with the spectrogram floor."""
    if not bins:
        return feat
    out = np.full_like(feat, feat.min())
    if bins > 0:
        out[bins:] = feat[:-bins]
    else:
        out[:bins] = feat[-bins:]
    return out


def _tuning_bins(wav: np.ndarray, sr: int) -> int:
    """CQT rows (half semitones) to move the spectrogram so a detuned recording lines up with A=440."""
    import librosa

    try:
        dev = float(librosa.estimate_tuning(y=wav, sr=sr, bins_per_octave=12))  # in semitones, [-0.5, 0.5)
    except Exception:  # noqa: BLE001  (silence, too short): leave as is
        return 0
    return -1 if dev > 0.25 else 1 if dev < -0.25 else 0


def _transpose_perm(semitones: int) -> np.ndarray:
    """Index into the model's classes that rotates 'seen k semitones up' back to the true chord."""
    perm = np.arange(_N_CLASSES)
    for c in range(168):
        perm[c] = ((c // 14 + semitones) % 12) * 14 + c % 14
    return perm


def _blend(window_probs: list[tuple[int, np.ndarray]], n_frames: int) -> np.ndarray:
    acc = np.zeros((n_frames, _N_CLASSES))
    weight = np.zeros(n_frames)
    ramp = np.minimum(np.arange(1, _WIN + 1), np.arange(_WIN, 0, -1)).astype(float)
    for start, probs in window_probs:
        acc[start:start + _WIN] += probs * ramp[:, None]
        weight[start:start + _WIN] += ramp
    return acc / np.maximum(weight, 1e-9)[:, None]


def btc_posteriors(model, wav: np.ndarray, sr: int = 22050, shifts: tuple[int, ...] = _SEMITONE_SHIFTS) -> np.ndarray:
    """Chord probabilities per BTC frame, [T, 170]. `model` is a loaded BTCForChordRecognition."""
    import importlib

    import torch

    feat = importlib.import_module("btc_src.features").audio_to_features(wav, sr_target=sr)  # [144, T]
    n = feat.shape[1]
    base = _tuning_bins(wav, sr)
    padded = n + (-n) % _WIN + _WIN  # whole windows, plus one more so the hop-by-half grid reaches the end
    total = np.zeros((padded, _N_CLASSES))
    for k in shifts:
        x = (_shift_bins(feat, 2 * k + base).T - model._mean) / model._std
        x = np.pad(x, ((0, padded - n), (0, 0)))
        starts = list(range(0, padded - _WIN + 1, _WIN // 2))
        windows: list[tuple[int, np.ndarray]] = []
        for i in range(0, len(starts), _BATCH):
            chunk = starts[i:i + _BATCH]
            batch = torch.tensor(np.stack([x[s:s + _WIN] for s in chunk]), dtype=torch.float32).to(model._device)
            with torch.no_grad():
                hidden, _ = model.model.self_attn_layers(batch)
                probs = torch.softmax(model.model.output_layer.output_projection(hidden), -1).cpu().numpy()
            windows += [(s, p) for s, p in zip(chunk, probs, strict=True)]
        total += _blend(windows, padded)[:, _transpose_perm(k)]
    out = total[:n] / len(shifts)
    out[:, _NO_CHORD] += out[:, _UNKNOWN]
    out[:, _UNKNOWN] = 0.0
    return out


def pool_beats(probs: np.ndarray, beatmap: BeatMap, n_beats: int) -> np.ndarray:
    """Mean probability of the frames inside every beat, [n_beats, classes]."""
    times = (np.arange(len(probs)) + 0.5) * _FRAME
    edges = np.append(beatmap.t[:n_beats], beatmap.t[n_beats - 1] + beatmap.tail)
    cut = np.searchsorted(times, edges)
    out = np.empty((n_beats, probs.shape[1]))
    for i in range(n_beats):
        lo, hi = int(cut[i]), int(cut[i + 1])
        out[i] = probs[lo:hi].mean(axis=0) if hi > lo else probs[min(max(lo, 0), len(probs) - 1)]
    return out


def viterbi_beats(beat_probs: np.ndarray, downbeats: set[int]) -> list[int]:
    """Most likely chord per beat; every change costs, less on a downbeat."""
    emit = np.log(beat_probs + 1e-4)
    n, s = emit.shape
    back = np.zeros((n, s), dtype=np.int32)
    score = emit[0].copy()
    for i in range(1, n):
        cost = _SWITCH_DOWNBEAT if i in downbeats else _SWITCH_OFFBEAT
        best = int(np.argmax(score))
        switch = score[best] - cost
        stay = score >= switch
        back[i] = np.where(stay, np.arange(s), best)
        score = np.where(stay, score, switch) + emit[i]
    path = [int(np.argmax(score))]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    return path[::-1]


def chords_from_posteriors(probs: np.ndarray, beatmap: BeatMap, n_beats: int, downbeats: set[int]) -> list[dict]:
    labels = _voca_labels()
    path = viterbi_beats(pool_beats(probs, beatmap, n_beats), downbeats)
    return _segments([parse_chord(labels[c]) for c in path])


def _voca_labels() -> dict[int, str]:
    import importlib

    return importlib.import_module("btc_src.features").idx2voca_chord()


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


def analyze_structure(source: Path, report: Report, harmonic: np.ndarray | None = None) -> dict:
    """`harmonic`: the track without drums as 22.05 kHz mono. Chords are read from it when given."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "source.wav"
        _to_wav(source, wav)
        return _structure_from_wav(wav, report, harmonic)


def harmonic_mix(video_id: str, model: str, rate: int = 22050) -> np.ndarray | None:
    """Everything but the drums, summed from the separated stems: percussion only smears the chroma."""
    import librosa
    import soundfile as sf

    dirs = [library.model_dir(video_id, model), *(library.model_dir(video_id, m) for m in MODELS if m != model)]
    for d in dirs:
        stems = [f for f in sorted(d.glob("*.flac")) if f.stem != "drums"]
        if not stems or not (d / "drums.flac").exists():
            continue
        mix = None
        for f in stems:
            data, sr = sf.read(f, dtype="float32", always_2d=True)
            mono = data.mean(axis=1)
            mix = mono if mix is None else mix[:len(mono)] + mono[:len(mix)]
        return librosa.resample(mix, orig_sr=sr, target_sr=rate)
    return None


def _structure_from_wav(source: Path, report: Report, harmonic: np.ndarray | None = None) -> dict:
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
    import librosa

    wav = harmonic if harmonic is not None else librosa.load(str(source), sr=22050, mono=True)[0]
    bars, num = _bars(len(beats), beats, np.asarray(downbeats, dtype=float))
    chords = chords_from_posteriors(btc_posteriors(model, wav), bmap, len(beats), set(bars))
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


def analyze(video_id: str, model: str, report: Report, *, force: bool = False) -> None:
    """Idempotent: the structure is computed once per video and shared by all models; `force` recomputes it,
    keeping the old one if that fails."""
    if force or read_structure(video_id) is None:
        source = library.find_source(video_id)
        if not source:
            raise AnalysisError("The original download is gone; split the track again")
        structure = analyze_structure(source, report, harmonic_mix(video_id, model))
        _write(library.video_dir(video_id) / "structure.json", structure)
        library.stamp(video_id, "structure", SCHEMA)
    report("analyze", 1.0, "Analysis ready")


def is_stale(video_id: str) -> bool:
    return (library.video_dir(video_id) / "structure.json").is_file() and read_structure(video_id) is None
