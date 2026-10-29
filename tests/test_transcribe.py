from prismix import transcribe


def test_clean_drops_junk_and_loops():
    chunks = [
        {"timestamp": (1.0, None), "text": " Hello world "},
        {"timestamp": (2.0, None), "text": "Thanks for watching!"},
        {"timestamp": (3.0, None), "text": "la la"}, {"timestamp": (4.0, None), "text": "la la"}, {"timestamp": (5.0, None), "text": "la la"}, {"timestamp": (6.0, None), "text": "la la"},
        {"timestamp": (7.0, None), "text": "Another line"},
    ]
    out = [c["text"] for c in transcribe.clean(chunks)]
    assert "Hello world" in out and "Another line" in out
    assert not any("watching" in t.lower() for t in out)
    assert out.count("la la") == 2


def test_clean_drops_impossible_words():
    out = [c["text"] for c in transcribe.clean([{"text": "холодно вчччччш мрткл дома", "timestamp": (1.0, 3.0)}])]
    assert out == ["холодно дома"]
