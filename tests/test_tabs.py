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
