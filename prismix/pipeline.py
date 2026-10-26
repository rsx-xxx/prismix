"""download (yt-dlp) -> decode (ffmpeg) -> separate (demucs) -> encode FLAC (ffmpeg)."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from . import analysis, library
from .config import (
    MAX_DURATION_SEC,
    MIX_MODEL,
    MODELS,
    PEAK_BUCKETS,
    SAMPLE_RATE,
    cookies_file,
    ffmpeg_exe,
    js_runtime,
)

log = logging.getLogger(__name__)

# (stage, 0..1 within stage, human message)
Report = Callable[[str, float, str], None]


class PipelineError(RuntimeError):
    """Error with a message that is safe to show to the user."""


# ───────────────────────── download ─────────────────────────

def _ydl_opts(**extra) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 5,
        "socket_timeout": 30,
        "ffmpeg_location": ffmpeg_exe(),
        "js_runtimes": js_runtime(),
        **extra,
    }
    if cookies := cookies_file():
        opts["cookiefile"] = str(cookies)
    return opts


def search(query: str, limit: int = 8, offset: int = 0) -> list[dict]:
    """`limit` results after the first `offset` ones (yt-dlp has no paging, so earlier ones are fetched and dropped)."""
    import yt_dlp

    # yt-dlp rewrites its cookie file on close, so concurrent searches each get a private copy
    with tempfile.TemporaryDirectory() as tmp:
        opts = _ydl_opts(extract_flat=True)
        if src := opts.get("cookiefile"):
            copy = Path(tmp) / "cookies.txt"
            shutil.copyfile(src, copy)
            opts["cookiefile"] = str(copy)
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(f"ytsearch{offset + limit}:{query}", download=False)
    out = []
    for e in list((res or {}).get("entries", []))[offset:]:
        vid = e.get("id")
        if not vid or not library.VIDEO_ID_RE.match(vid):
            continue
        out.append({
            "id": vid,
            "title": e.get("title") or vid,
            "channel": e.get("channel") or e.get("uploader") or "",
            "duration": e.get("duration"),
        })
    return out


def _playlist_url(url: str) -> str:
    """Mixes (list=RD...) only resolve through a watch URL; youtu.be links need rewriting too."""
    u = urlparse(url)
    q = parse_qs(u.query)
    lst = (q.get("list") or [None])[0]
    vid = library.parse_video_id(url)
    if lst and vid and lst.startswith("RD"):
        return f"https://www.youtube.com/watch?v={vid}&list={lst}"
    if lst:
        return f"https://www.youtube.com/playlist?list={lst}"
    return url


def playlist(url: str, limit: int = 200) -> dict:
    """Flat listing of a public playlist or mix URL (no download)."""
    import yt_dlp

    url = _playlist_url(url)
    opts = _ydl_opts(extract_flat=True, noplaylist=False, playlistend=limit)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            res = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise PipelineError(str(e).replace("ERROR: ", "").splitlines()[0][:300]) from e
    items = []
    for e in (res or {}).get("entries") or []:
        vid = (e or {}).get("id")
        if vid and library.VIDEO_ID_RE.match(vid):
            items.append({
                "id": vid,
                "title": e.get("title") or vid,
                "channel": e.get("channel") or e.get("uploader") or "",
                "duration": e.get("duration"),
            })
    return {"title": (res or {}).get("title") or "Playlist", "items": items}


_channel_cache: dict[str, tuple[float, list[dict]]] = {}


def channel_playlists(handle: str, ttl: float = 3600) -> list[dict]:
    """Public playlists of a channel (id, title, count, thumb), cached for an hour. Empty when there are none."""
    import yt_dlp

    hit = _channel_cache.get(handle)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(extract_flat=True, noplaylist=False)) as ydl:
            res = ydl.extract_info(f"https://www.youtube.com/{handle}/playlists", download=False)
    except yt_dlp.utils.DownloadError as e:
        log.warning("channel playlists failed: %s", e)
        return []
    out = []
    for e in (res or {}).get("entries") or []:
        pid = (e or {}).get("id") or ""
        if not pid.startswith(("PL", "OL")):
            continue
        thumbs = e.get("thumbnails") or []
        out.append({
            "id": pid,
            "title": e.get("title") or pid,
            "count": e.get("playlist_count"),
            "thumb": thumbs[-1].get("url") if thumbs else None,
        })
    _channel_cache[handle] = (time.time(), out)
    return out


def download(video_id: str, report: Report) -> dict:
    """Fetch the best audio stream (no re-encode) unless already in the library."""
    import yt_dlp

    vdir = library.video_dir(video_id)
    info = library.read_info(video_id)
    if info and library.find_source(video_id):
        return info

    vdir.mkdir(parents=True, exist_ok=True)
    for stale in vdir.glob("source.*"):  # leftovers of an interrupted download
        stale.unlink(missing_ok=True)

    def hook(d: dict) -> None:
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = d.get("downloaded_bytes", 0) / total if total else 0.0
            report("download", min(frac, 1.0), "Downloading audio")

    opts = _ydl_opts(
        format="bestaudio/best",
        outtmpl=str(vdir / "source.%(ext)s"),
        progress_hooks=[hook],
    )
    url = f"https://www.youtube.com/watch?v={video_id}"
    report("download", 0.0, "Fetching video info")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            meta = ydl.extract_info(url, download=False)
            duration = meta.get("duration") or 0
            if meta.get("is_live"):
                raise PipelineError("Live streams are not supported")
            if duration > MAX_DURATION_SEC:
                raise PipelineError(
                    f"Video is {duration // 60} min long; the limit is {MAX_DURATION_SEC // 60} min"
                )
            ydl.process_ie_result(meta, download=True)
    except (yt_dlp.utils.YoutubeDLError, OSError) as e:  # network resets and extractor bugs, not just DownloadError
        msg = (str(e).replace("ERROR: ", "").splitlines() or [type(e).__name__])[0][:300]
        raise PipelineError(f"YouTube download failed: {msg}") from e

    if not library.find_source(video_id):
        raise PipelineError("Download finished but no audio file was produced")

    info = {
        "id": video_id,
        "title": meta.get("title") or video_id,
        "channel": meta.get("channel") or meta.get("uploader") or "",
        "duration": duration,
        "added": int(time.time()),
        **extra_meta(meta),
    }
    _save_thumbnail(meta, vdir / "thumb.jpg")
    (vdir / "info.json").write_text(json.dumps(info, ensure_ascii=False))
    return info


def extra_meta(meta: dict) -> dict:
    """Description and tags of a video: where uploaders say what a cover is a cover of."""
    return {
        "description": (meta.get("description") or "")[:4000],
        "tags": [str(t) for t in (meta.get("tags") or [])[:40]],
    }


def fetch_extra(video_id: str) -> dict:
    """extra_meta() for a video that is already in the library (older items were saved without it)."""
    import yt_dlp

    with yt_dlp.YoutubeDL(_ydl_opts(skip_download=True)) as ydl:
        return extra_meta(ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False) or {})


def _save_thumbnail(meta: dict, dest: Path) -> None:
    url = meta.get("thumbnail")
    if not url or not url.startswith("https://"):
        return
    try:
        with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310 (https only)
            dest.write_bytes(r.read(5_000_000))
    except Exception as e:  # cosmetic only
        log.warning("thumbnail fetch failed: %s", e)


# ───────────────────────── audio io ─────────────────────────

def decode(source: Path) -> np.ndarray:
    """Any media file -> float32 array (2, n) at SAMPLE_RATE."""
    cmd = [
        ffmpeg_exe(), "-v", "error", "-nostdin", "-i", str(source), "-vn",
        "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise PipelineError("Could not decode the downloaded audio")
    return np.frombuffer(proc.stdout, dtype=np.float32).reshape(-1, 2).T.copy()


def encode_flac(samples: np.ndarray, dest: Path) -> None:
    """(2, n) float32 -> 16-bit FLAC. 16-bit halves the size with no audible loss for playback."""
    cmd = [
        ffmpeg_exe(), "-v", "error", "-nostdin", "-y",
        "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", "pipe:0",
        "-c:a", "flac", "-sample_fmt", "s16", "-compression_level", "5", str(dest),
    ]
    data = np.ascontiguousarray(np.clip(samples, -1.0, 1.0).T, dtype=np.float32).tobytes()
    proc = subprocess.run(cmd, input=data, capture_output=True)
    if proc.returncode != 0:
        raise PipelineError(f"FLAC encoding failed: {proc.stderr.decode(errors='ignore')[-200:]}")


def peaks(samples: np.ndarray, buckets: int = PEAK_BUCKETS) -> np.ndarray:
    mono = np.abs(samples).max(axis=0)
    n = (len(mono) // buckets) * buckets
    if n == 0:
        return np.zeros(buckets, dtype=np.float32)
    return mono[:n].reshape(buckets, -1).max(axis=1)


# ───────────────────────── separation ─────────────────────────

def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@lru_cache(maxsize=2)
def _load_model(name: str):
    from demucs.pretrained import get_model

    model = get_model(name)
    model.eval()
    return model


def _run_demucs(model, wav, device: str, report: Report) -> np.ndarray:
    import torch
    from demucs.apply import apply_model

    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    mix = ((wav - mean) / std)[None]
    n_models = len(model.models) if hasattr(model, "models") else 1
    total = max(wav.shape[-1], 1)
    best = [0.0]

    def cb(d: dict) -> None:
        if d.get("state") != "end":
            return
        frac = (d["model_idx_in_bag"] + d["segment_offset"] / total) / n_models
        best[0] = max(best[0], min(frac, 1.0))
        report("separate", best[0], "Separating instruments")

    with torch.inference_mode():
        out = apply_model(
            model, mix, device=device, shifts=1, split=True, overlap=0.25,
            progress=False, callback=cb,
        )[0]
    return (out * std + mean).cpu().numpy()  # (stems, 2, n)


_STEM_ORDER = ("vocals", "drums", "bass", "guitar", "piano", "other")


def _stem_rank(name: str) -> int:
    return _STEM_ORDER.index(name) if name in _STEM_ORDER else len(_STEM_ORDER)


_ROFORMER = "bs_roformer_sw"


@lru_cache(maxsize=1)
def _load_roformer():
    import torch
    import yaml
    from bs_roformer import DEFAULT_MODEL, ensure_model_assets, get_model_from_config
    from bs_roformer.inference import SafeLoaderWithTuple
    from ml_collections import ConfigDict

    ckpt, conf = ensure_model_assets(DEFAULT_MODEL)  # downloads once, verifies the checksum
    with open(conf) as f:
        cfg = ConfigDict(yaml.load(f, Loader=SafeLoaderWithTuple))
    model = get_model_from_config("bs_roformer", cfg)
    model.load_state_dict(torch.load(ckpt, map_location="cpu"))
    model.eval()
    return model, cfg


def _run_roformer(model, cfg, wav, device: str, report: Report) -> tuple[list[str], np.ndarray]:
    """Overlap-add over fixed chunks with a fade window; reflect-padded edges."""
    import torch

    chunk = int(cfg.inference.chunk_size)
    step = chunk // int(cfg.inference.num_overlap)
    border = chunk - step
    n = wav.shape[-1]
    x = wav
    if n > 2 * border:
        x = torch.nn.functional.pad(x, (border, border), mode="reflect")
    else:
        x = torch.nn.functional.pad(x, (border, border))
    fade = torch.ones(chunk)
    fade[:border] = torch.linspace(0, 1, border)
    fade[-border:] = torch.linspace(1, 0, border)
    fade = fade.to(device)
    total = x.shape[-1]
    starts = list(range(0, max(total - chunk, 0) + step, step))
    out = torch.zeros(len(cfg.training.instruments), 2, total, device=device)
    norm = torch.zeros(total, device=device)
    model.to(device)
    with torch.inference_mode():
        for i, a in enumerate(starts):
            part = x[:, a:a + chunk]
            m = part.shape[-1]
            if m < chunk:
                part = torch.nn.functional.pad(part, (0, chunk - m))
            y = model(part[None].to(device))[0]
            w = fade[:m].clone()
            if a == 0:
                w[:border] = 1.0
            if a + chunk >= total:
                w[-border:] = 1.0
            out[..., a:a + m] += y[..., :m] * w
            norm[a:a + m] += w
            report("separate", (i + 1) / len(starts), "Separating instruments")
    res = (out / norm.clamp_min(1e-8))[..., border:border + n]
    return list(cfg.training.instruments), np.nan_to_num(res.cpu().numpy())


def _separate_roformer(wav: np.ndarray, report: Report) -> dict[str, np.ndarray]:
    import torch

    report("model", 0.0, "Loading model (first run downloads ~700 MB)")
    model, cfg = _load_roformer()
    device = pick_device()
    tensor = torch.from_numpy(wav)
    report("separate", 0.0, "Separating instruments")
    try:
        names, out = _run_roformer(model, cfg, tensor, device, report)
    except RuntimeError as e:
        if device == "cpu":
            raise PipelineError(f"Separation failed: {e}") from e
        log.warning("separation on %s failed (%s); retrying on CPU", device, e)
        names, out = _run_roformer(model, cfg, tensor, "cpu", report)
    stems = dict(zip(names, out, strict=True))
    return {k: stems[k] for k in sorted(stems, key=_stem_rank)}


def separate(wav: np.ndarray, model_name: str, report: Report) -> dict[str, np.ndarray]:
    import torch

    if model_name == _ROFORMER:
        return _separate_roformer(wav, report)

    report("model", 0.0, "Loading model (first run downloads weights)")
    model = _load_model(model_name)
    device = pick_device()
    tensor = torch.from_numpy(wav)
    report("separate", 0.0, "Separating instruments")
    try:
        out = _run_demucs(model, tensor, device, report)
    except RuntimeError as e:
        if device == "cpu":
            raise PipelineError(f"Separation failed: {e}") from e
        log.warning("separation on %s failed (%s); retrying on CPU", device, e)
        out = _run_demucs(model, tensor, "cpu", report)
    stems = dict(zip(model.sources, out, strict=True))
    return {k: stems[k] for k in sorted(stems, key=_stem_rank)}


# A stem this far under the full mix is residue the model could not place, not an instrument.
EMPTY_DB = -24.0


def _db(x: float) -> float:
    return 20 * float(np.log10(max(x, 1e-9)))


def is_empty(samples: np.ndarray, mix_rms: float) -> bool:
    """True when a stem is silence or leftover noise relative to the full mix."""
    return _db(float(np.sqrt((samples**2).mean()))) - _db(mix_rms) < EMPTY_DB


def drop_empty(stems: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    mix = sum(stems.values())
    mix_rms = float(np.sqrt((mix**2).mean()))
    kept = {k: v for k, v in stems.items() if not is_empty(v, mix_rms)}
    return kept or stems


def prune(video_id: str, model_name: str) -> list[str]:
    """Remove empty stems from an already-processed item. Returns the removed names."""
    meta = library.read_meta(video_id, model_name)
    if not meta:
        return []
    mdir = library.model_dir(video_id, model_name)
    stems = {s["name"]: decode(mdir / s["file"]) for s in meta["stems"]}
    keep = drop_empty(stems)
    gone = [n for n in stems if n not in keep]
    if not gone:
        return []
    meta["stems"] = [s for s in meta["stems"] if s["name"] in keep]
    for n in gone:
        (mdir / f"{n}.flac").unlink(missing_ok=True)
    (mdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    return gone


# ───────────────────────── orchestration ─────────────────────────

def process(video_id: str, model_name: str, report: Report, *, analyse: bool = True) -> dict:
    """Run the whole pipeline (idempotent: cached results are returned untouched)."""
    if model_name not in MODELS:
        raise PipelineError("Unknown model")
    if meta := library.read_meta(video_id, model_name):
        return meta

    info = download(video_id, report)

    report("decode", 0.0, "Decoding audio")
    source = library.find_source(video_id)
    wav = decode(source)
    duration = wav.shape[1] / SAMPLE_RATE

    if model_name == MIX_MODEL:
        stems = {MIX_MODEL: wav}
    else:
        stems = drop_empty(separate(wav, model_name, report))
    del wav

    final = library.model_dir(video_id, model_name)
    tmp = final.with_name(final.name + ".partial")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        scale = max(float(np.abs(s).max()) for s in stems.values()) or 1.0
        entries = []
        for i, (name, samples) in enumerate(stems.items()):
            report("encode", i / len(stems), f"Encoding {name}")
            encode_flac(samples, tmp / f"{name}.flac")
            p = peaks(samples) / scale
            entries.append({
                "name": name,
                "file": f"{name}.flac",
                "peaks": [int(round(float(v) * 255)) for v in p],
            })
        meta = {
            **info,
            "model": model_name,
            "duration": duration,
            "sampleRate": SAMPLE_RATE,
            "stems": entries,
        }
        (tmp / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
        shutil.rmtree(final, ignore_errors=True)
        tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if analyse:
        try:  # the split is already usable; notation is a bonus
            analysis.analyze(video_id, model_name, report)
        except Exception:
            log.exception("analysis of %s failed", video_id)
    report("done", 1.0, "Ready")
    return meta
