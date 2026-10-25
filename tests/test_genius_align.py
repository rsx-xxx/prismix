import numpy as np

from prismix import align, genius

PAGE = """<html><head><title>Faience – Knife (It Feels Like I Start over Again) Lyrics | Genius Lyrics</title></head><body>
<div data-lyrics-container="true"><div data-exclude-from-selection="true">12 Contributors</div>[Припев]<br/>Первая строка<br/>Вторая строка<br/><br/>[Куплет 1]<br/><i>Третья</i> строка<br/>Четвёртая строка</div>
<div class="x">Other text</div></body></html>"""


def test_clean_title_strips_noise():
    assert genius.clean_title("Song (feat. X) [Official Video] | Label") == "Song"
    assert genius.clean_title("Песня (Official Audio)") == "Песня"


def test_slug():
    assert genius.slugs("Faience", "Knife (It Feels Like)") == ["Faience-Knife-It-Feels-Like-lyrics"]


def test_page_title_and_parse():
    assert genius.page_title(PAGE) == ("Faience", "Knife (It Feels Like I Start over Again)")
    lines = genius.parse_lyrics(PAGE)
    texts = [ln["text"] for ln in lines]
    assert texts == ["Первая строка", "Вторая строка", "", "Третья строка", "Четвёртая строка"]
    assert lines[0]["sec"] == "Припев" and lines[3]["sec"] == "Куплет 1"


def test_verify_scores_right_page_higher():
    assert genius.verify(PAGE, "Faience", "Knife It Feels Like I Start over Again") > genius.MIN_SCORE
    assert genius.verify(PAGE, "Metallica", "One") < genius.MIN_SCORE


def test_romanize():
    assert align.romanize("Привет,") == "privet"
    assert align.romanize("Don't") == "don't"


def test_viterbi_places_tokens():
    em = np.full((12, 4), -5.0, dtype=np.float32)
    em[:, 0] = -0.1
    for t, k in [(2, 1), (5, 2), (8, 3)]:
        em[t, k], em[t, 0] = -0.01, -6
    assert align.viterbi(em, [1, 2, 3]).tolist() == [2, 5, 8]


def test_place_words_into_lines():
    words = [(0, 0, 2), (0, 2, 3), (1, 3, 4)]
    out = align.place(np.array([0, 5, 10, 20]), words, 3, 0.1, 5.0)
    assert out[0]["ws"] == [0.0, 1.0] and out[0]["t"] == 0.0
    assert out[1]["t"] == 2.0 and out[2] is None
