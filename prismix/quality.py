"""Separation quality presets, the saved choice, and size/time estimates for this machine.

Quality only changes how thoroughly the model runs (test-time shifts and window overlap, which average away
artifacts), never the stem files: they stay 16-bit FLAC, so size is the same at every level and only time grows.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from functools import lru_cache

from . import library
from .config import DATA_DIR, MODELS

SETTINGS_FILE = DATA_DIR / "settings.json"
_lock = threading.Lock()


@dataclass(frozen=True)
class Quality:
    id: str
    label: str
    hint: str
    shifts: int       # htdemucs: random-shift passes averaged (0 = a single plain pass)
    overlap: float    # htdemucs: window overlap
    roformer: int     # BS-RoFormer: multiplier on the model's own window overlap


QUALITIES: dict[str, Quality] = {q.id: q for q in (
    Quality("fast", "Fast", "A single light pass: quickest, a little more bleed between stems", 0, 0.10, 1),
    Quality("standard", "Standard", "Balanced: the default", 1, 0.25, 1),
    Quality("high", "High", "Averages more passes: cleaner stems, about 3× the time", 2, 0.50, 2),
    Quality("max", "Max", "Most thorough: the cleanest stems, several times slower", 3, 0.50, 4),
)}
DEFAULT_QUALITY = "standard"
BASE = QUALITIES[DEFAULT_QUALITY]

# Seconds of work per second of music at Standard, per model class and device, when nothing was measured yet.
_PRIOR = {"cuda": (0.04, 0.12), "mps": (0.25, 0.7), "cpu": (1.6, 5.0)}  # (htdemucs bag, roformer)
_FLAC_MB_PER_MIN_STEM = 2.2  # 16-bit stereo 44.1 kHz, typical music
TYPICAL_SEC = 240


def _read() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(SETTINGS_FILE)


def current() -> Quality:
    return QUALITIES.get(_read().get("quality"), BASE)


def set_current(quality_id: str) -> Quality:
    if quality_id not in QUALITIES:
        raise ValueError("Unknown quality")
    with _lock:
        _write({**_read(), "quality": quality_id})
    return QUALITIES[quality_id]


def cost(q: Quality, model: str) -> float:
    """Work relative to Standard for the same song."""
    if model == "bs_roformer_sw":
        return q.roformer / BASE.roformer
    passes = lambda x: max(x.shifts, 1) / (1 - x.overlap)  # noqa: E731
    return passes(q) / passes(BASE)


@lru_cache(maxsize=1)
def device() -> str:
    from .pipeline import pick_device

    try:
        return pick_device()
    except Exception:
        return "cpu"


def _prior(model: str, dev: str) -> float:
    demucs, roformer = _PRIOR.get(dev, _PRIOR["cpu"])
    rtf = roformer if model == "bs_roformer_sw" else demucs
    if dev == "cpu":
        rtf *= 8 / max(os.cpu_count() or 8, 1)  # the table assumes 8 cores
    return rtf


def record_speed(model: str, dev: str, q: Quality, seconds: float, audio_sec: float) -> None:
    """Remember how fast this machine really is (normalised to Standard) so estimates converge on it."""
    if audio_sec < 20 or seconds <= 0:
        return
    rtf = seconds / audio_sec / cost(q, model)
    key = f"{model}|{dev}"
    with _lock:
        data = _read()
        speed = data.setdefault("speed", {})
        old = speed.get(key)
        speed[key] = round(rtf if old is None else 0.6 * old + 0.4 * rtf, 4)
        _write(data)


def _rtf(model: str, dev: str) -> tuple[float, bool]:
    measured = (_read().get("speed") or {}).get(f"{model}|{dev}")
    return (measured, True) if measured else (_prior(model, dev), False)


def _flac_mb_per_min_stem(model: str) -> float:
    """Average over this library's finished splits of the model, else a typical value."""
    total = secs = 0.0
    for d in library.LIBRARY_DIR.glob(f"*/{model}"):
        meta = library._read_json(d / "meta.json")
        if not meta or not meta.get("duration") or not meta.get("stems"):
            continue
        try:
            total += sum((d / s["file"]).stat().st_size for s in meta["stems"])
        except OSError:
            continue
        secs += meta["duration"] * len(meta["stems"])
    return total / 1e6 / (secs / 60) if secs > 120 else _FLAC_MB_PER_MIN_STEM


def estimates(model: str, seconds: float = TYPICAL_SEC) -> dict:
    """Per-quality size and time for a song of `seconds` on this machine."""
    dev = device()
    rtf, measured = _rtf(model, dev)
    stems = len(MODELS[model].stems)
    size_mb = _flac_mb_per_min_stem(model) * stems * seconds / 60
    return {
        "model": model,
        "device": dev,
        "cores": os.cpu_count() or 1,
        "measured": measured,
        "seconds": seconds,
        "sizeMB": round(size_mb, 1),
        "levels": [
            {"id": q.id, "label": q.label, "hint": q.hint,
             "cost": round(cost(q, model), 1),
             "timeSec": round(rtf * cost(q, model) * seconds + 8)}  # + download/decode/encode overhead
            for q in QUALITIES.values()
        ],
    }
