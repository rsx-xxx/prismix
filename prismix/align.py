"""Forced alignment: known words (Genius text) laid onto the vocal stem, so every line and word gets a time.

Recognition (transcribe.py) guesses the words and is weak at Russian; here the words are given and only their timing is
searched. The wav2vec2 MMS_FA model (Latin alphabet, 1200+ languages) emits per-frame letter probabilities; Cyrillic is
transliterated to Latin first. A CTC Viterbi pass then finds the most likely frame for every letter.
The model (about 1.2 GB) is downloaded on first use and cached by torch.hub.
"""
from __future__ import annotations

import re
import threading

import numpy as np

from . import transcribe

RATE = transcribe.RATE
CHUNK = 40 * RATE  # emissions are computed piece by piece to bound memory on long songs
OVERLAP = 2 * RATE

_CYR = dict(zip(
    "абвгдежзийклмнопрстуфхцчшщъыьэюяё",
    ["a", "b", "v", "g", "d", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f", "h", "ts",
     "ch", "sh", "sh", "", "y", "", "e", "yu", "ya", "yo"],
    strict=True))
_LATIN = re.compile(r"[a-z']")
_lock = threading.Lock()
_loaded: tuple | None = None


class AlignError(RuntimeError):
    """Message is safe to show to the user."""


def romanize(word: str) -> str:
    """'Привет, world!' -> 'privet' per word: lower-case a-z and ' only (what the model knows)."""
    out = []
    for ch in word.lower().replace("’", "'"):
        out.append(_CYR.get(ch) if ch in _CYR else ch if _LATIN.fullmatch(ch) else "")
    return "".join(x for x in out if x)


def tokenize(lines: list[str], vocab: dict[str, int]) -> tuple[list[int], list[tuple[int, int, int]]]:
    """Letters of all lines -> (token ids, [(line, first token, last token + 1) per word]). Unpronounceable words are skipped."""
    tokens: list[int] = []
    words: list[tuple[int, int, int]] = []
    for li, text in enumerate(lines):
        for w in text.split():
            letters = [vocab[c] for c in romanize(w) if c in vocab]
            if letters:
                words.append((li, len(tokens), len(tokens) + len(letters)))
                tokens += letters
    return tokens, words


def viterbi(emission: np.ndarray, tokens: list[int], blank: int = 0) -> np.ndarray:
    """CTC forced alignment. emission: (frames, classes) log-probs. Returns the frame each token starts at."""
    f, n = emission.shape[0], len(tokens)
    if n == 0 or f < n:
        raise AlignError("The audio is too short for these words")
    neg = -1e30
    trellis = np.full((f + 1, n + 1), neg, dtype=np.float32)
    trellis[0, 0] = 0.0
    tok_em = emission[:, tokens]  # (frames, n)
    stay_em = emission[:, blank]
    came_from_prev = np.zeros((f, n), dtype=bool)
    for t in range(f):
        stay = trellis[t, 1:] + np.maximum(stay_em[t], tok_em[t])  # waiting on a letter: blank or still voicing it
        move = trellis[t, :-1] + tok_em[t]
        came_from_prev[t] = move > stay
        trellis[t + 1, 1:] = np.where(came_from_prev[t], move, stay)
        trellis[t + 1, 0] = trellis[t, 0] + stay_em[t]
    starts = np.zeros(n, dtype=np.int64)
    j = n
    for t in range(f - 1, -1, -1):
        if j == 0:
            break
        if came_from_prev[t, j - 1]:
            starts[j - 1] = t
            j -= 1
    return starts


def place(starts: np.ndarray, words: list[tuple[int, int, int]], n_lines: int, sec_per_frame: float,
          total: float) -> list[dict | None]:
    """Token frames -> per line {'t', 'e', 'ws': [word starts], 'we': [word ends]}; None for lines without words."""
    per_line: list[list[tuple[float, float]]] = [[] for _ in range(n_lines)]
    for k, (li, a, b) in enumerate(words):
        t0 = float(starts[a]) * sec_per_frame
        nxt = float(starts[words[k + 1][1]]) * sec_per_frame if k + 1 < len(words) else total
        t1 = min(float(starts[b - 1] + 1) * sec_per_frame + 0.15, nxt)
        per_line[li].append((round(t0, 2), round(max(t1, t0 + 0.05), 2)))
    out: list[dict | None] = []
    for spans in per_line:
        if not spans:
            out.append(None)
            continue
        out.append({"t": spans[0][0], "e": spans[-1][1], "ws": [s for s, _ in spans], "we": [e for _, e in spans]})
    return out


def unload() -> None:
    global _loaded
    _loaded = None


def _load():
    global _loaded
    if _loaded is None:
        from torchaudio.pipelines import MMS_FA

        _loaded = (MMS_FA.get_model(with_star=False).eval(), MMS_FA.get_dict(star=None))
    return _loaded


def _emission(audio: np.ndarray, model) -> np.ndarray:
    import torch

    pieces = []
    step = CHUNK - OVERLAP
    for s in range(0, max(len(audio) - OVERLAP, 1), step):
        seg = audio[s:s + CHUNK]
        x = torch.from_numpy((seg - seg.mean()) / (seg.std() + 1e-7))[None]
        with torch.inference_mode():
            em, _ = model(x)
        em = torch.log_softmax(em, dim=-1)[0].numpy()
        frames_per_overlap = int(round(OVERLAP / (len(seg) / em.shape[0])))
        lead = frames_per_overlap // 2 if s else 0
        tail = frames_per_overlap // 2 if s + CHUNK < len(audio) else 0
        pieces.append(em[lead:em.shape[0] - tail])
    return np.concatenate(pieces)


def align(video_id: str, lines: list[str]) -> list[dict | None]:
    """Timing for each text line (same order and length as `lines`) from the vocal stem; None where a line has no words."""
    try:
        audio = transcribe._audio(video_id)
    except transcribe.TranscribeError as e:
        raise AlignError(str(e)) from e
    if float(np.sqrt(np.mean(audio ** 2))) < 1e-4:
        raise AlignError("This track has no audible vocals")
    try:
        with _lock:
            model, vocab = _load()
            tokens, words = tokenize(lines, vocab)
            if len(words) < 4:
                raise AlignError("Not enough words to align")
            em = _emission(audio, model)
    except AlignError:
        raise
    except Exception as e:  # download problems, out of memory, unsupported hardware
        raise AlignError(f"Could not align the lyrics ({type(e).__name__})") from e
    total = len(audio) / RATE
    starts = viterbi(em, tokens, blank=0)
    return place(starts, words, len(lines), total / em.shape[0], total)
