"""Render the current mix (or individual stems) with tempo/key changes and encode it.

Stretching uses Rubber Band's R3 engine in offline mode with formant preservation: the
slowest option, and the cleanest for vocals and complex mixes. Mixing happens before the
stretch for "mix" exports, so the stretcher sees one signal instead of several.
"""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import library, tagging
from .config import SAMPLE_RATE, ffmpeg_exe

# id -> (extension, mime, ffmpeg codec args)
FORMATS: dict[str, tuple[str, str, list[str]]] = {
    "wav": ("wav", "audio/wav", ["-c:a", "pcm_s24le"]),
    "flac": ("flac", "audio/flac", ["-c:a", "flac", "-compression_level", "8"]),
    "mp3": ("mp3", "audio/mpeg", ["-c:a", "libmp3lame", "-q:a", "0"]),
    "m4a": ("m4a", "audio/mp4", ["-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"]),
    "m4a128": ("m4a", "audio/mp4", ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]),  # band cloud
    "ogg": ("ogg", "audio/ogg", ["-c:a", "libvorbis", "-q:a", "7"]),
    "opus": ("opus", "audio/ogg", ["-c:a", "libopus", "-b:a", "160k"]),
}
LOSSY = {"mp3", "m4a", "m4a128", "ogg", "opus"}
# Leave headroom so lossy encoders do not clip on inter-sample peaks.
PEAK_CEILING = {"lossy": 0.89, "lossless": 0.98}


class ExportError(ValueError):
    """Bad request; the message is safe to show."""


@dataclass
class ExportSpec:
    video_id: str
    model: str
    stems: list[str]
    separate: bool = False
    tempo: float = 1.0
    semitones: float = 0.0
    format: str = "flac"
    volumes: dict[str, float] = field(default_factory=dict)


def _decode(path) -> np.ndarray:
    """FLAC -> float32 array (channels, samples) at the library sample rate."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-nostdin", "-i", str(path), "-f", "f32le",
         "-ac", "2", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True, check=False,
    )
    if proc.returncode:
        raise ExportError("Could not read the stem")
    return np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).T.copy()


def stretch(audio: np.ndarray, tempo: float, semitones: float) -> np.ndarray:
    """Change tempo and pitch independently. audio: (2, n) float32."""
    if abs(tempo - 1) < 1e-4 and abs(semitones) < 1e-4:
        return audio
    from pylibrb import Option, RubberBandStretcher

    opts = (
        Option.PROCESS_OFFLINE | Option.ENGINE_FINER | Option.FORMANT_PRESERVED
        | Option.CHANNELS_TOGETHER | Option.WINDOW_STANDARD
    )
    rb = RubberBandStretcher(
        SAMPLE_RATE, audio.shape[0], opts,
        initial_time_ratio=1.0 / tempo, initial_pitch_scale=2.0 ** (semitones / 12.0),
    )
    n = audio.shape[1]
    rb.set_expected_input_duration(n)
    block = 1 << 15
    for start in range(0, n, block):  # offline mode: study first, then process
        rb.study(audio[:, start:start + block], final=start + block >= n)
    out: list[np.ndarray] = []
    for start in range(0, n, block):
        rb.process(audio[:, start:start + block], final=start + block >= n)
        while rb.available() > 0:
            out.append(rb.retrieve_available())
    while rb.available() > 0:
        out.append(rb.retrieve_available())
    return np.concatenate(out, axis=1) if out else audio[:, :0]


def _limit(audio: np.ndarray, fmt: str) -> np.ndarray:
    """Only ever turn the level down, and only as far as needed to avoid clipping."""
    ceiling = PEAK_CEILING["lossy" if fmt in LOSSY else "lossless"]
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    return audio * (ceiling / peak) if peak > ceiling else audio


COVER_FORMATS = {"mp3", "m4a", "flac"}


def cover_args(fmt: str, cover: Path | None) -> tuple[list[str], list[str]]:
    """(extra input args, output args) that attach `cover` as the picture, for formats that can carry one."""
    if not cover or fmt not in COVER_FORMATS:
        return [], []
    out = ["-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    if fmt == "mp3":
        out += ["-id3v2_version", "3"]
    return ["-i", str(cover)], out


def _encode(audio: np.ndarray, fmt: str, tags: dict[str, str], cover: Path | None = None) -> bytes:
    ext, _, codec = FORMATS[fmt]
    meta = [a for k, v in tags.items() if v for a in ("-metadata", f"{k}={v}")]
    cin, cout = cover_args(fmt, cover)
    # Output to a pipe is not seekable (breaks m4a/wav headers), so go through a temp file.
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / f"out.{ext}"
        pcm = np.ascontiguousarray(np.clip(audio.T, -1, 1), dtype="<f4").tobytes()
        proc = subprocess.run(
            [ffmpeg_exe(), "-v", "error", "-nostdin", "-y", "-f", "f32le", "-ac", "2",
             "-ar", str(SAMPLE_RATE), "-i", "-", *cin, *codec, *cout, *meta, str(dest)],
            input=pcm, capture_output=True, check=False,
        )
        if proc.returncode:
            raise ExportError("Encoding failed")
        return dest.read_bytes()


def _safe(text: str) -> str:
    return re.sub(r"[^\w\s.,()+%&'-]", "", text, flags=re.UNICODE).strip()[:80] or "track"


def describe(spec: ExportSpec) -> str:
    bits = []
    if abs(spec.semitones) >= 0.01:
        bits.append(f"{spec.semitones:+g} st")
    if abs(spec.tempo - 1) >= 0.005:
        bits.append(f"{round(spec.tempo * 100)}%")
    return f" [{', '.join(bits)}]" if bits else ""


def render(spec: ExportSpec) -> tuple[bytes, str, str]:
    """Returns (data, filename, mime)."""
    if spec.format not in FORMATS:
        raise ExportError("Unknown format")
    if not 0.5 <= spec.tempo <= 1.5 or not -12 <= spec.semitones <= 12:
        raise ExportError("Tempo or key out of range")
    try:
        meta = library.read_meta(spec.video_id, spec.model)
    except ValueError:
        meta = None
    if not meta:
        raise ExportError("That track has not been processed")
    available = {s["name"]: s for s in meta["stems"]}
    names = [n for n in dict.fromkeys(spec.stems) if n in available]
    if not names:
        raise ExportError("Pick at least one stem")

    ext, mime, _ = FORMATS[spec.format]
    title = meta.get("title", "track")
    base = _safe(title) + describe(spec)
    mdir = library.model_dir(spec.video_id, spec.model)
    found, cover = tagging.for_track(spec.video_id)
    title = found.get("title") or title
    tags = {**found, "title": title, "artist": found.get("artist") or meta.get("channel", ""),
            "comment": f"Stems: {', '.join(names)}; tempo {spec.tempo:.2f}x; {spec.semitones:+g} semitones"}

    def gain(name: str) -> float:
        return float(min(1.5, max(0.0, spec.volumes.get(name, 1.0))))

    stems = {n: _decode(mdir / available[n]["file"]) * gain(n) for n in names}

    if not spec.separate or len(names) == 1:
        length = max(a.shape[1] for a in stems.values())
        mix = np.zeros((2, length), dtype=np.float32)
        for a in stems.values():
            mix[:, : a.shape[1]] += a
        data = _encode(_limit(stretch(mix, spec.tempo, spec.semitones), spec.format), spec.format, tags, cover)
        label = "" if len(names) == len(available) else f" ({' + '.join(names)})"
        return data, f"{base}{label}.{ext}", mime

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # audio is already compressed
        for n, a in stems.items():
            out = _limit(stretch(a, spec.tempo, spec.semitones), spec.format)
            zf.writestr(f"{base} - {n}.{ext}", _encode(out, spec.format, {**tags, "title": f"{title} ({n})"}, cover))
    return buf.getvalue(), f"{base} - stems.zip", "application/zip"
