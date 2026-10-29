from prismix import lyrics, vocab


def test_known_words():
    assert vocab.known("brightside", "en", {"brightside"})
    assert vocab.known("love", "en")
    assert not vocab.known("xqzvnt", "en")


def test_vet_drops_invented_line_and_repairs_typo():
    assert vocab.vet("xqzvnt blorp zzkt", "en")[0] == ""
    text, checked, real = vocab.vet("I am lovve you", "en")
    assert "love" in text and checked == 4 and real == 3


def test_find_by_words_uses_library(monkeypatch):
    sung = [{"t": 1, "text": "coming out of my cage and i've been doing just fine"},
            {"t": 5, "text": "gotta gotta be down because i want it all"}]
    good = [{"text": "Coming out of my cage and I've been doing just fine"},
            {"text": "Gotta gotta be down because I want it all"}, {"text": "a"}, {"text": "b"}]
    monkeypatch.setattr(lyrics, "_library_texts", lambda skip: [
        {"url": None, "artist": "The Killers", "title": "Mr. Brightside", "lines": good}])
    got = lyrics.find_by_words("vid", sung)
    assert got and got["title"] == "Mr. Brightside" and got["score"] >= 0.9


def test_find_by_words_rejects_other_song(monkeypatch):
    sung = [{"t": 1, "text": "coming out of my cage and i've been doing just fine"}]
    other = [{"text": "completely different words about rivers and mountains"}] * 5
    monkeypatch.setattr(lyrics, "_library_texts", lambda skip: [{"url": None, "artist": "x", "title": "y", "lines": other}])
    monkeypatch.setattr(lyrics.genius, "search_urls", lambda c, q: [])
    assert lyrics.find_by_words("vid", sung) is None
