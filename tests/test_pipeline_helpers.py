import numpy as np

from prismix import pipeline


def test_drop_empty_keeps_loud_and_falls_back():
    t = np.linspace(0, 100, 44100)
    loud = np.sin(t)[None, :]
    quiet = loud * 0.001
    out = pipeline.drop_empty({"vocals": loud, "piano": quiet, "drums": loud})
    assert set(out) == {"vocals", "drums"}
    assert set(pipeline.drop_empty({"a": quiet})) == {"a"}


def test_playlist_url_rewrites_mixes():
    assert pipeline._playlist_url("https://youtu.be/abcdefghijk?list=RDabcdefghijk") == (
        "https://www.youtube.com/watch?v=abcdefghijk&list=RDabcdefghijk")
    assert pipeline._playlist_url("https://www.youtube.com/playlist?list=PL123") == (
        "https://www.youtube.com/playlist?list=PL123")
