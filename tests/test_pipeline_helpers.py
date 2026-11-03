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


def test_redo_splits_again_only_at_a_different_quality(tmp_path, monkeypatch):
    import json

    import numpy as np

    from prismix import library, pipeline
    from prismix.quality import QUALITIES

    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    vid = "dQw4w9WgXcQ"
    mdir = tmp_path / vid / "htdemucs_6s"
    mdir.mkdir(parents=True)
    (tmp_path / vid / "info.json").write_text(json.dumps({"id": vid, "title": "T"}))
    (mdir / "meta.json").write_text(json.dumps({"id": vid, "model": "htdemucs_6s", "quality": "standard", "stems": []}))
    (mdir / "keep.json").write_text("{}")
    runs = []
    monkeypatch.setattr(pipeline, "download", lambda v, r: {"id": v, "title": "T"})
    monkeypatch.setattr(library, "find_source", lambda v: tmp_path / "s.wav")
    monkeypatch.setattr(pipeline, "decode", lambda p: np.ones((2, 44100), dtype="float32"))
    monkeypatch.setattr(pipeline, "separate", lambda w, m, r, q: runs.append(q.id) or {"drums": w})
    monkeypatch.setattr(pipeline, "encode_flac", lambda s, d: d.write_bytes(b"x"))
    noop = lambda *a: None  # noqa: E731
    pipeline.process(vid, "htdemucs_6s", noop, analyse=False)
    assert runs == []  # a plain request keeps what is there
    meta = pipeline.process(vid, "htdemucs_6s", noop, analyse=False, redo=QUALITIES["max"])
    assert runs == ["max"] and meta["quality"] == "max"
    assert (mdir / "keep.json").is_file() and (mdir / "drums.flac").is_file()
    pipeline.process(vid, "htdemucs_6s", noop, analyse=False, redo=QUALITIES["max"])
    assert runs == ["max"]  # already at that quality
