import json

from fastapi.testclient import TestClient

from prismix import library, server

VID = "dQw4w9WgXcQ"


def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(server, "LIBRARY_DIR", tmp_path)
    return TestClient(server.create_app())


def seed(tmp_path, model="htdemucs_6s"):
    d = tmp_path / VID / model
    d.mkdir(parents=True)
    (tmp_path / VID / "info.json").write_text(json.dumps({"id": VID, "title": "T", "duration": 5, "added": 1}))
    (d / "meta.json").write_text(json.dumps({"id": VID, "stems": [{"file": "a.flac"}]}))
    (d / "a.flac").write_bytes(b"x")


def test_cached_request_skips_job(tmp_path, monkeypatch):
    seed(tmp_path)
    with make_client(tmp_path, monkeypatch) as c:
        r = c.post("/api/process", json={"url": f"https://youtu.be/{VID}", "model": "htdemucs_6s", "tasks": "stems"})
        assert r.json() == {"cached": True, "videoId": VID, "model": "htdemucs_6s"}
        assert [i["id"] for i in c.get("/api/library").json()] == [VID]


def test_bad_inputs(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        assert c.post("/api/process", json={"url": "https://example.com"}).status_code == 400
        assert c.post("/api/process", json={"url": VID, "model": "nope"}).status_code == 400
        assert c.get("/api/library/..%2f/htdemucs_6s").status_code == 404


def test_delete(tmp_path, monkeypatch):
    seed(tmp_path)
    with make_client(tmp_path, monkeypatch) as c:
        assert c.delete(f"/api/library/{VID}").status_code == 200
        assert c.get("/api/library").json() == []


def test_analyze_unknown_is_404_and_complete_is_cached(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        assert c.post("/api/analyze", json={"videoId": VID, "model": "htdemucs_6s"}).status_code == 404
        seed(tmp_path)
        (tmp_path / VID / "structure.json").write_text('{"version": 1}')
        assert c.post("/api/analyze", json={"videoId": VID, "model": "htdemucs_6s"}).json() == {"cached": True}


def test_process_tasks_validation(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        assert c.post("/api/process", json={"url": VID, "model": "mix"}).status_code == 400
        assert c.post("/api/process", json={"url": VID, "tasks": "nope"}).status_code == 422


def test_chords_task_uses_mix_model(tmp_path, monkeypatch):
    seed(tmp_path, "mix")
    with make_client(tmp_path, monkeypatch) as c:
        r = c.post("/api/process", json={"url": VID, "tasks": "chords"}).json()
        assert r["model"] == "mix"


def test_config_and_playlist_host_check(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISMIX_GOOGLE_CLIENT_ID", "abc")
    with make_client(tmp_path, monkeypatch) as c:
        assert c.get("/api/config").json() == {"googleClientId": "abc"}
        assert c.get("/api/playlist", params={"url": "https://example.com/x"}).status_code == 400


def test_tabs_requires_analysis(tmp_path, monkeypatch):
    seed(tmp_path)
    with make_client(tmp_path, monkeypatch) as c:
        assert c.post("/api/tabs", json={"videoId": VID}).status_code == 422
        assert c.post("/api/tabs", json={"videoId": VID, "url": "https://example.com"}).status_code == 422
