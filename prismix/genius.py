"""Lyrics text from Genius (https://genius.com): the most accurate public text, no timings.

Genius' own search endpoint sits behind a captcha, so a page is found in two cheaper ways: the URL Genius builds from
artist and title (`Artist-title-lyrics`) is tried first, then a web search (DuckDuckGo) for `artist title genius lyrics`.
Every candidate page is verified against the wanted artist and title with fuzzy matching before its text is used.
The text is the template that lyrics.py then times against the audio (align.py) or against LRCLIB.
"""
from __future__ import annotations

import html
import logging
import re
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import quote, unquote, urlparse

import httpx

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
TIMEOUT = 15
MIN_SCORE = 0.62

_NOISE_WORDS = (
    r"official(?:\s+(?:music\s+)?(?:video|audio|lyric\s+video|clip|visuali[sz]er))?|lyrics?(?:\s+video)?|lyric\s+video|"
    r"audio|video|clip|клип|премьера(?:\s+клипа)?|текст|слова|караоке|karaoke|hd|hq|4k|remaster(?:ed)?(?:\s+\d{4})?|"
    r"live|session|remix|radio\s+edit|full\s+album|visuali[sz]er|mv|m/v|topic|cover|кавер|акустика|acoustic"
)
_BRACKET = re.compile(rf"[\(\[\{{][^)\]}}]*\b(?:{_NOISE_WORDS})\b[^)\]}}]*[\)\]\}}]", re.I)
_FEAT = re.compile(r"[\(\[]?\b(?:feat|ft|featuring|при уч)\b\.?[^)\]]*[\)\]]?", re.I)
_TRAIL_NOISE = re.compile(rf"\s*[-–—|:·,]?\s*\b(?:{_NOISE_WORDS})\b\s*$", re.I)
_SPACES = re.compile(r"\s+")


def clean_title(title: str) -> str:
    """'Song (feat. X) [Official Video] | Label' -> 'Song'."""
    t = (title or "").split("|")[0]
    t = _BRACKET.sub(" ", t)
    t = _FEAT.sub(" ", t)
    for _ in range(3):
        t = _TRAIL_NOISE.sub("", t)
    t = re.sub(r"[«»“”\"`]+", "", t)
    return _SPACES.sub(" ", t).strip(" -–—_.,:;|·•")


def clean_artist(artist: str) -> str:
    a = re.sub(r"\s*[-–—]\s*topic$|vevo$|\s+official$", "", artist or "", flags=re.I)
    a = _FEAT.sub(" ", a)
    return _SPACES.sub(" ", a).strip(" -–—_.,:;|·•")


def norm(s: str) -> str:
    """Lower-case letters and digits only, accents folded: the common ground for fuzzy comparison."""
    s = unicodedata.normalize("NFKD", s or "").lower().replace("ё", "е").replace("й", "и")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return _SPACES.sub(" ", re.sub(r"[^\w ]+|_", " ", s)).strip()


def similarity(a: str, b: str) -> float:
    """0..1: the better of character-level similarity and word overlap, so word order and filler do not sink a match."""
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    overlap = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
    return max(SequenceMatcher(None, a, b).ratio(), overlap * 0.95)


def _slug_part(s: str) -> str:
    s = s.replace("&", "and")
    s = re.sub(r"[’'`.,!?()\[\]\"“”:;/*]+", "", s)
    return re.sub(r"[^\w]+", "-", s, flags=re.U).strip("-")


def slugs(artist: str | None, title: str) -> list[str]:
    """The URL path Genius would give this song: words joined by hyphens, symbols dropped, '-lyrics' appended."""
    t = _slug_part(title)
    return [f"{_slug_part(artist)}-{t}-lyrics"] if artist and t and _slug_part(artist) else []


# ───────────────────────── page parsing ─────────────────────────

_VOID = {"br", "img", "input", "meta", "link", "hr", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


class _Lyrics(HTMLParser):
    """Collects the text of every `data-lyrics-container` block, skipping excluded sub-blocks (contributors, teaser)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0  # >0 while inside a lyrics container
        self.skip = 0  # >0 while inside an excluded sub-block
        self.stack: list[tuple[str, bool, bool]] = []  # (tag, opened-container, opened-skip)
        self.out: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "br" and self.depth and not self.skip:
            self.out.append("\n")
        if tag in _VOID:
            return
        opened = a.get("data-lyrics-container") == "true"
        excl = bool(self.depth) and a.get("data-exclude-from-selection") == "true"
        self.stack.append((tag, opened, excl))
        if opened:
            self.depth += 1
        if excl:
            self.skip += 1

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for _, opened, excl in reversed(self.stack[i:]):
                    if opened:
                        self.depth -= 1
                        self.out.append("\n")
                    if excl:
                        self.skip -= 1
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.out.append(data)


_SECTION = re.compile(r"^\s*[\[\(]([^\]\)]{1,60})[\]\)]\s*$")
_JUNK_LINE = re.compile(r"^(?:you might also like|\d*\s*embed|see .* live|get tickets.*)$", re.I)


def page_title(page: str) -> tuple[str, str] | None:
    """(artist, title) from the page's <title>: 'Artist – Title Lyrics | Genius Lyrics'."""
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    if not m:
        return None
    t = html.unescape(m.group(1)).replace("\xa0", " ")
    t = re.sub(r"\s*\|\s*Genius.*$", "", t)
    t = re.sub(r"\s+Lyrics$", "", t, flags=re.I)
    parts = re.split(r"\s+[–—-]\s+", t, maxsplit=1)
    return (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else None


def parse_lyrics(page: str) -> list[dict]:
    """Page HTML -> [{'text': 'words', 'sec': 'Chorus'}]; blank lines kept as {'text': ''}, [Section] headers folded into 'sec'."""
    p = _Lyrics()
    p.feed(page)
    raw = "".join(p.out).replace("\xa0", " ")
    lines: list[dict] = []
    sec = None
    for ln in raw.splitlines():
        ln = ln.strip()
        if _JUNK_LINE.match(ln):
            continue
        if m := _SECTION.match(ln):
            sec = re.split(r"[:：]", m.group(1))[0].strip() or None
            if lines and lines[-1]["text"]:
                lines.append({"text": ""})
            continue
        if not ln:
            if lines and lines[-1]["text"]:
                lines.append({"text": ""})
            continue
        entry = {"text": ln}
        if sec:
            entry["sec"] = sec
            sec = None
        lines.append(entry)
    while lines and not lines[-1]["text"]:
        lines.pop()
    return lines


# ───────────────────────── finding the page ─────────────────────────

def _get(client: httpx.Client, url: str) -> httpx.Response | None:
    try:
        r = client.get(url)
    except httpx.HTTPError as e:
        log.info("genius fetch failed: %s", e)
        return None
    return r if r.status_code == 200 else None


def search_urls(client: httpx.Client, query: str) -> list[str]:
    """Genius song URLs from a web search for the query, in result order."""
    r = _get(client, f"https://html.duckduckgo.com/html/?q={quote(query)}")
    if not r:
        return []
    urls: list[str] = []
    for m in re.finditer(r'uddg=([^&"\']+)', r.text):
        u = unquote(m.group(1))
        host = urlparse(u).hostname or ""
        if host.endswith("genius.com") and u.endswith("-lyrics") and u not in urls:
            urls.append(u)
    return urls


def verify(page: str, artist: str | None, title: str) -> float:
    """How well a fetched page matches what we want: the title must match, a known artist only nudges the score
    (covers and 'Artist feat. X' pages list the artist differently)."""
    got = page_title(page)
    if not got:
        return 0.0
    g_artist, g_title = got
    ts = max(similarity(title, g_title), similarity(clean_title(title), clean_title(g_title)))
    if not artist:
        return ts * 0.9
    a_sim = similarity(artist, g_artist)
    return ts * (0.75 + 0.25 * a_sim) if a_sim < 0.5 else ts * (0.9 + 0.1 * a_sim)


def fetch(artist: str | None, title: str, *, client: httpx.Client | None = None) -> dict | None:
    """Best Genius page for (artist, title): {'url', 'artist', 'title', 'lines', 'score'} or None."""
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en,ru;q=0.8"}, timeout=TIMEOUT,
                                    follow_redirects=True)
    try:
        title = clean_title(title)
        artist = clean_artist(artist) if artist else None
        if not title:
            return None
        urls = [f"https://genius.com/{s}" for s in slugs(artist, title)]
        queries = [f"{artist or ''} {title} genius lyrics".strip()]
        best: dict | None = None
        tried: set[str] = set()
        stage = 0
        while True:
            for url in urls:
                if url in tried:
                    continue
                tried.add(url)
                r = _get(client, url)
                if not r:
                    continue
                score = verify(r.text, artist, title)
                if score < MIN_SCORE:
                    continue
                lines = parse_lyrics(r.text)
                if sum(1 for ln in lines if ln["text"]) < 4:
                    continue
                got = page_title(r.text) or ("", "")
                cand = {"url": url, "artist": got[0], "title": got[1], "lines": lines, "score": round(score, 3)}
                if best is None or score > best["score"]:
                    best = cand
                if score >= 0.9:
                    return best
            if best or stage >= len(queries):
                return best
            urls = search_urls(client, queries[stage])[:4]
            stage += 1
    finally:
        if own:
            client.close()
