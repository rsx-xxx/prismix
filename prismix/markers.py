"""Where the music really starts, for the timeline.

Two independent signals, cached as `library/<id>/markers.json`:
- energy: the first moment the instrumental stems (everything but vocals) are sustainedly audible. A spoken
  intro separates into the vocals stem, so it does not count;
- SponsorBlock (https://sponsor.ajay.app, community-made): non-music, sponsor and intro segments of a YouTube video.
  Only a 4-character hash prefix of the video id is sent, never the id itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

import httpx
import numpy as np

from . import library, uploads
from .config import MODELS

log = logging.getLogger(__name__)
SCHEMA = 1
SB_API = "https://sponsor.ajay.app/api/skipSegments"
SB_CATEGORIES = ["sponsor", "selfpromo", "intro", "interaction", "music_offtopic", "preview"]
NON_MUSIC = {"sponsor", "selfpromo", "interaction", "music_offtopic"}
HOP = 0.25  # seconds per energy frame
SUSTAIN = 3.0  # music must hold this long to count as the start
BELOW_PEAK_DB = 20.0  # audible = within this many dB of the track's loud level
RETRY_SB = 3600


def leading_end(segments: list[dict], slack: float = 3.0) -> float:
    """End of the chain of segments that begins at (about) the start of the video; 0 if none does."""
    pos = 0.0
    changed = True
    while changed:
        changed = False
        for s in segments:
            if s["s"] <= pos + slack and s["e"] > pos:
                pos = s["e"]
                changed = True
    return pos


def onset(power: np.ndarray, hop: float = HOP) -> float:
    """First time `power` (one value per frame) is sustainedly within BELOW_PEAK_DB of its loud level."""
    if not len(power) or not np.any(power > 0):
        return 0.0
    db = 10 * np.log10(power + 1e-12)
    loud = np.percentile(db[db > -80], 80) if np.any(db > -80) else 0.0
    ok = db > loud - BELOW_PEAK_DB
    need = max(1, int(SUSTAIN / hop))
    run = np.convolve(ok.astype(float), np.ones(need), mode="valid") / need
    hits = np.flatnonzero(run >= 0.9)
    return float(hits[0] * hop) if len(hits) else 0.0


def _frame_power(path, hop: float = HOP) -> np.ndarray:
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    n = int(sr * hop)
    frames = len(mono) // n
    return (mono[: frames * n].reshape(frames, n) ** 2).mean(axis=1)


def energy_start(video_id: str) -> float | None:
    """Music start from the separated stems, or None when no stems are on disk."""
    for model in MODELS:
        d = library.model_dir(video_id, model)
        files = sorted(d.glob("*.flac"))
        if not files:
            continue
        inst = [f for f in files if f.stem != "vocals"]
        parts = [_frame_power(f) for f in (inst or files)]
        n = min(len(p) for p in parts)
        start = onset(np.sum([p[:n] for p in parts], axis=0))
        # with a vocals-only track the instrumental stems are near-silent noise: use the whole mix then
        return start
    return None


def sponsorblock(video_id: str) -> list[dict]:
    prefix = hashlib.sha256(video_id.encode()).hexdigest()[:4]
    r = httpx.get(f"{SB_API}/{prefix}", params={"categories": json.dumps(SB_CATEGORIES), "actionTypes": json.dumps(["skip", "full"])},
                  timeout=8, headers={"User-Agent": "prismix (personal use)"})
    if r.status_code == 404:
        return []
    r.raise_for_status()
    out = []
    for video in r.json():
        if video.get("videoID") != video_id:
            continue
        for s in video.get("segments", []):
            a, b = s.get("segment", (0, 0))
            if b > a and s.get("category") in SB_CATEGORIES and s.get("actionType") != "full":
                out.append({"s": round(float(a), 2), "e": round(float(b), 2), "cat": s["category"]})
    return sorted(out, key=lambda x: x["s"])


def get(video_id: str) -> dict:
    """Cached markers: {start, energy, sb: [{s, e, cat}], sbOk}. `start` is where the music begins, in seconds."""
    path = library.video_dir(video_id) / "markers.json"
    cached = library._read_json(path) or {}
    if cached.get("schema") != SCHEMA:
        cached = {}
    changed = False
    if "energy" not in cached:
        cached["energy"] = energy_start(video_id)
        changed = True
    youtube = not uploads.is_upload(video_id)
    stale = not cached.get("sbOk") and time.time() - cached.get("sbTried", 0) > RETRY_SB
    if youtube and stale:
        cached["sbTried"] = time.time()
        try:
            cached["sb"], cached["sbOk"] = sponsorblock(video_id), True
        except (httpx.HTTPError, ValueError) as e:
            log.info("SponsorBlock unavailable: %s", e)
            cached.setdefault("sb", [])
            cached["sbOk"] = False
        changed = True
    elif not youtube:
        cached.update(sb=[], sbOk=True)
    if changed:
        cached["schema"] = SCHEMA
        path.write_text(json.dumps(cached))
    sb = cached.get("sb", [])
    start = max(cached.get("energy") or 0.0, leading_end([s for s in sb if s["cat"] in NON_MUSIC]))
    return {"start": round(start, 2) if start >= 2 else 0.0, "energy": cached.get("energy"),
            "sb": [s for s in sb if s["cat"] in NON_MUSIC], "sbOk": cached.get("sbOk", False)}
