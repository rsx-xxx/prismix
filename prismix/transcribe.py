"""Lyrics from the audio itself, for songs nobody has published (covers, small artists): Whisper on the vocal stem.

English and Russian only. The speech model is downloaded on first use (about 1.6 GB for `whisper-large-v3-turbo`, far
fewer invented words than the small models on sung Russian) and cached by huggingface_hub; override it with PRISMIX_WHISPER_MODEL.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading

import numpy as np

from . import library, vocab
from .config import MODELS, ffmpeg_exe

MODEL_ID = os.environ.get("PRISMIX_WHISPER_MODEL", "openai/whisper-large-v3-turbo")
LANGS = ("en", "ru")
RATE = 16000
WINDOW = 30 * RATE

# Whisper invents these when it hears music instead of speech
_JUNK = re.compile(
    r"субтитр|редактор субтитров|продолжение следует|спасибо за (просмотр|внимание)|подписывайтесь|"
    r"thanks? for watching|subtitles? by|please subscribe|amara\.org|dimatorzok", re.I)

# A "word" that cannot be one: no vowel at all, or a run of the same letter, or absurdly long
_VOWELS = re.compile(r"[aeiouyаеёиоуыэюя]", re.I)


def _plausible(word: str) -> bool:
    w = re.sub(r"[^\w]", "", word)
    if not w or w.isdigit():
        return True
    return len(w) <= 24 and not re.search(r"(.)\1{3,}", w) and (len(w) <= 2 or bool(_VOWELS.search(w)))


_lock = threading.Lock()
_loaded: tuple | None = None


class TranscribeError(RuntimeError):
    """Message is safe to show to the user."""


def _audio(video_id: str) -> np.ndarray:
    """Mono 16 kHz float32: the vocal stem if the track was split (far fewer mistakes), else the whole mix."""
    src = next((p for m in MODELS if (p := library.model_dir(video_id, m) / "vocals.flac").is_file()), None)
    src = src or library.find_source(video_id)
    if not src:
        raise TranscribeError("The audio of this track is not available")
    cmd = [ffmpeg_exe(), "-v", "error", "-nostdin", "-i", str(src), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "f32le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0 or not out.stdout:
        raise TranscribeError("Could not read the audio of this track")
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


def unload() -> None:
    global _loaded
    _loaded = None


def _load():
    global _loaded
    if _loaded is None:
        import torch
        from transformers import WhisperForConditionalGeneration, WhisperProcessor

        processor = WhisperProcessor.from_pretrained(MODEL_ID)
        model = WhisperForConditionalGeneration.from_pretrained(MODEL_ID, use_safetensors=True, torch_dtype=torch.float32)
        _loaded = (processor, model.eval())
    return _loaded


def loudest_window(audio: np.ndarray) -> np.ndarray:
    """The 30 s stretch with the most sound: where a vocal stem is most likely to be singing."""
    if len(audio) <= WINDOW:
        return audio
    hop = 5 * RATE
    starts = range(0, len(audio) - WINDOW + 1, hop)
    best = max(starts, key=lambda s: float(np.sqrt(np.mean(audio[s:s + WINDOW] ** 2))))
    return audio[best:best + WINDOW]


def detect_language(audio: np.ndarray, processor, model) -> str:
    """English or Russian, whichever Whisper finds likelier (other languages are not considered)."""
    import torch

    feats = processor(loudest_window(audio), sampling_rate=RATE, return_tensors="pt").input_features
    tok = processor.tokenizer
    start = tok.convert_tokens_to_ids("<|startoftranscript|>")
    with torch.no_grad():
        logits = model(input_features=feats, decoder_input_ids=torch.tensor([[start]])).logits[0, -1]
    ids = [tok.convert_tokens_to_ids(f"<|{c}|>") for c in LANGS]
    return LANGS[int(torch.argmax(logits[ids]))]


def clean(chunks: list[dict]) -> list[dict]:
    """Whisper chunks -> [{'t', 'text'}]: drop empty, invented and stuck-in-a-loop lines."""
    out: list[dict] = []
    for c in chunks:
        text = re.sub(r"\s+", " ", c.get("text") or "").strip()
        start = (c.get("timestamp") or (None,))[0]
        if not text or start is None or _JUNK.search(text):
            continue
        text = " ".join(w for w in text.split() if _plausible(w))  # non-existing words are dropped, never shown
        if not text:
            continue
        if len(out) >= 2 and text.lower() == out[-1]["text"].lower() == out[-2]["text"].lower():
            continue  # a line repeated three times in a row is almost always the model looping
        out.append({"t": round(float(start), 2), "text": text})
    return out


def _segments(out, tokenizer) -> list[dict]:
    """generate(return_segments=True) -> [{'text', 'timestamp': (start, end)}]; short inputs come back as plain ids."""
    if isinstance(out, dict) and "segments" in out:
        segs = [x for per_item in out["segments"] for x in per_item]
        return [{"text": tokenizer.decode(x["tokens"], skip_special_tokens=True),
                 "timestamp": (float(x["start"]), float(x["end"]))} for x in segs]
    ids = out["sequences"] if isinstance(out, dict) else out
    text = tokenizer.decode(ids[0], skip_special_tokens=True, decode_with_timestamps=False)
    return [{"text": text, "timestamp": (0.0, None)}]


def _looped(text: str) -> bool:
    """Whisper's stuck-record failure: one word repeated through the whole line."""
    words = [w for w in re.findall(r"\w+", text.lower())]
    return len(words) >= 6 and max(words.count(w) for w in set(words)) / len(words) > 0.6


def vet_lines(lines: list[dict], lang: str, allow: set[str] = frozenset()) -> tuple[list[dict], float]:
    """Every word checked against the dictionaries: unknown ones are repaired or dropped, invented lines vanish.
    Returns (lines, share of the recognised words that were real to begin with)."""
    out, checked, real = [], 0, 0
    for ln in lines:
        if _looped(ln["text"]):
            continue
        text, n, ok = vocab.vet(ln["text"], lang, allow=allow)
        checked += n
        real += ok
        if text:
            out.append({**ln, "text": text})
    return out, (real / checked if checked else 0.0)


def transcribe(video_id: str, allow: set[str] = frozenset()) -> dict:
    """{'language': 'en'|'ru', 'lines': [{'t', 'text'}], 'quality'}. Slow: a minute or two on CPU for a typical song.
    `allow`: words that are real here though no dictionary lists them (the song's own title, names)."""
    audio = _audio(video_id)
    if float(np.sqrt(np.mean(audio ** 2))) < 1e-4:
        raise TranscribeError("This track has no audible vocals")
    try:
        with _lock:
            import torch

            processor, model = _load()
            lang = detect_language(audio, processor, model)
            if len(audio) > WINDOW:  # long-form: Whisper walks the song window by window and times every phrase
                inputs = processor(audio, sampling_rate=RATE, return_tensors="pt", truncation=False,
                                   padding="longest", return_attention_mask=True)
                if inputs.input_features.shape[-1] < 3000:  # tail shorter than one window: use the plain path
                    inputs = processor(audio, sampling_rate=RATE, return_tensors="pt")
            else:
                inputs = processor(audio, sampling_rate=RATE, return_tensors="pt")
            with torch.no_grad():
                out = model.generate(
                    **inputs, language=lang, task="transcribe", return_timestamps=True, return_segments=True,
                    condition_on_prev_tokens=False, temperature=(0.0, 0.2, 0.4),
                    compression_ratio_threshold=1.35, logprob_threshold=-1.0)
            chunks = _segments(out, processor.tokenizer)
    except TranscribeError:
        raise
    except Exception as e:  # download problems, out of memory, unsupported hardware
        raise TranscribeError(f"Could not recognise the lyrics ({type(e).__name__}). Check your connection and try again") from e
    lines, quality = vet_lines(clean(chunks), lang, allow)
    if len(lines) < 3:
        raise TranscribeError("No sung words were recognised in this track")
    return {"language": lang, "lines": lines, "quality": round(quality, 2)}
