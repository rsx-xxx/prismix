import json

from fastapi.testclient import TestClient

from prismix import analysis, library, lyrics, prefetch, server, tabdata


def _song(tmp_path, monkeypatch, vid="abcdefghijk"):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    d = library.video_dir(vid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "info.json").write_text(json.dumps({"id": vid, "title": "T"}))
    return vid


def test_missing_lists_everything_for_a_bare_song(tmp_path, monkeypatch):
    assert prefetch.missing(_song(tmp_path, monkeypatch)) == ["chords", "lyrics", "tabs"]


def test_missing_skips_what_is_cached(tmp_path, monkeypatch):
    vid = _song(tmp_path, monkeypatch)
    monkeypatch.setattr(analysis, "read_structure", lambda v: {"version": analysis.SCHEMA})
    monkeypatch.setattr(tabdata, "read", lambda v: {"found": False})
    (library.video_dir(vid) / "lyrics.json").write_text(json.dumps({"version": lyrics.SCHEMA}))
    assert prefetch.missing(vid) == []


def test_prefetch_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(server, "LIBRARY_DIR", tmp_path)
    with TestClient(server.create_app()) as c:
        r = c.get("/api/prefetch")
    assert r.status_code == 200
    assert r.json()["active"] is False
