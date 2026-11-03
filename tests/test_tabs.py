from prismix import tabs

SHEET = ["Am", "F", "C", "G"] * 4 + ["Am", "G", "F", "E"] * 2 + ["Am", "F", "C", "G"] * 2


def detected(names, shift=0, noisy=()):
    out = []
    for i, n in enumerate(names):
        r, q = tabs.parse_chord(n)
        if i in noisy:
            r, q = (r + 5) % 12, "min"
        out.append({"b": i * 4, "d": 4, "r": (r + shift) % 12, "q": q})
    return out


def test_parse_chord():
    assert tabs.parse_chord("F♯m7/C") == (6, "min7")
    assert tabs.parse_chord("Bb") == (10, "maj")
    assert tabs.parse_chord("Asus4") == (9, "sus4")
    assert tabs.parse_chord("N.C.") is None


def test_align_exact_and_transposed():
    r = tabs.align(detected(SHEET), SHEET)
    assert r["similarity"] > 0.95 and r["transpose"] == 0
    assert all(m and m["ok"] for m in r["marks"])
    r = tabs.align(detected(SHEET, shift=2), SHEET)
    assert r["transpose"] == 2 and r["similarity"] > 0.95


def test_align_flags_differences_and_rejects_other_songs():
    r = tabs.align(detected(SHEET, noisy={5}), SHEET)
    assert r["similarity"] > 0.8
    assert r["marks"][5] and not r["marks"][5]["ok"] and r["marks"][5]["name"] == SHEET[5]
    other = ["D", "A", "Bm", "G"] * 6
    r = tabs.align(detected(other), ["C", "Dm", "E", "F", "G"] * 4)
    assert r["marks"] is None or r["similarity"] < 0.6


def test_guess_query():
    q = tabs.guess_query({"title": "Nirvana - Come As You Are (Official Video)", "channel": "x"})
    assert q[0] == ("nirvana", "come as you are")


def test_find_song_drops_the_featured_artist_and_falls_back_to_the_title(monkeypatch):
    seen = []

    def fake_get(url, timeout=15):
        seen.append(url)
        if "depeche" in url:  # the full credit finds only tabs without chords
            return '[{"songId": 1, "artist": "Depeche Mode", "title": "Enjoy The Silence (feat. Mike Shinoda)", "hasChords": false}]'
        return '[{"songId": 2, "artist": "Depeche Mode", "title": "Enjoy The Silence", "hasChords": true}, {"songId": 3, "artist": "Anberlin", "title": "Enjoy The Silence", "hasChords": true}]'

    monkeypatch.setattr(tabs, "_get", fake_get)
    info = {"title": "Depeche Mode Feat. Mike Shinoda - Enjoy the Silence", "channel": "x"}
    assert tabs.find_song(info)["songId"] == 2
    assert tabs.main_artist("depeche mode feat mike shinoda") == "depeche mode"
