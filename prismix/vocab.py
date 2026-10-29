"""Does this word exist? Checks recognised lyrics against dictionaries instead of trusting the speech model.

Whisper happily writes words nobody has said. Every word is looked up in word-frequency lists (English and Russian,
`wordfreq`); an unknown one is repaired to the nearest real word (or to a word of a lyrics text we already trust) and
dropped when nothing fits. A line that is mostly invented is dropped whole.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

from wordfreq import zipf_frequency

MIN_ZIPF = 1.6          # roughly one word in 25 million: rare but real ("brightside" is 2.3)
FOREIGN_ZIPF = 3.0      # a word of the other language counts only when it is common there (English in a Russian song)
FIX_ZIPF = 3.0          # a repair must be a word people actually use
MIN_LINE_KNOWN = 0.5    # share of a line's words that must be real, or the line is invented
_ALPHABET = {"en": "abcdefghijklmnopqrstuvwxyz'", "ru": "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"}
_VOCAL = re.compile(r"^(?:[ao]+h*|o+h+|u+h+|e+h+|ee+|ye+a*h*|yo+|hey+|na+|la+|da+|mm+|hm+|ha+|w?o+a+h*|[оауэыи]+х*|н[аэо]+|л[аэ]+|да+|ай+|эй+|ой+)$")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.U)


def _lang_pair(lang: str) -> tuple[str, str]:
    return (lang, "en" if lang == "ru" else "ru")


@lru_cache(maxsize=50000)
def _zipf(word: str, lang: str) -> float:
    return zipf_frequency(word, lang)


def known(word: str, lang: str, allow: frozenset[str] | set[str] = frozenset()) -> bool:
    """A real word of the song's language (or common in the other one), a sung sound, or a trusted word."""
    w = word.lower().replace("’", "'").strip("'")
    if not w or w.isdigit() or w in allow or _VOCAL.match(w):
        return True
    own, other = _lang_pair(lang)
    return _zipf(w, own) >= MIN_ZIPF or _zipf(w, other) >= FOREIGN_ZIPF


def _edits(w: str, lang: str):
    letters = _ALPHABET.get(lang, _ALPHABET["en"])
    splits = [(w[:i], w[i:]) for i in range(len(w) + 1)]
    yield from (a + b[1:] for a, b in splits if b)
    yield from (a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1)
    yield from (a + c + b[1:] for a, b in splits if b for c in letters)
    yield from (a + c + b for a, b in splits for c in letters)


def repair(word: str, lang: str, trusted: frozenset[str] | set[str] = frozenset()) -> str | None:
    """The real word this misspelt one most likely is: a word of the trusted text first, else the most common word one
    edit away. None when nothing convincing exists."""
    w = word.lower().replace("’", "'").strip("'")
    if len(w) < 3:
        return None
    near = [(SequenceMatcher(None, w, t).ratio(), t) for t in trusted if abs(len(t) - len(w)) <= 2]
    near = [x for x in near if x[0] >= 0.75]
    if near:
        return max(near)[1]
    best, best_f = None, FIX_ZIPF
    for cand in set(_edits(w, lang)):
        if (f := _zipf(cand, lang)) > best_f:
            best, best_f = cand, f
    return best


def _match_case(src: str, fixed: str) -> str:
    return fixed.capitalize() if src[:1].isupper() else fixed


def vet(text: str, lang: str, *, allow: set[str] = frozenset(), trusted: set[str] = frozenset()) -> tuple[str, int, int]:
    """(cleaned line, words checked, words that were real from the start). The cleaned line is '' for an invented one."""
    checked = real = pos = 0
    pieces: list[str] = []
    for m in _WORD.finditer(text):
        pieces.append(text[pos:m.start()])
        pos = m.end()
        word = m.group(0)
        checked += 1
        if known(word, lang, allow | trusted):
            real += 1
            pieces.append(word)
        elif (fix := repair(word, lang, trusted)) is not None:
            pieces.append(_match_case(word, fix))
        else:
            pieces.append("")  # dropped
    pieces.append(text[pos:])
    if checked >= 3 and real / checked < MIN_LINE_KNOWN:
        return "", checked, real
    out = re.sub(r"\s+", " ", "".join(pieces)).strip()
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    return out, checked, real
