import json

from fastapi.testclient import TestClient

from prismix import analysis, library, server

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
        (tmp_path / VID / "structure.json").write_text(f'{{"version": {analysis.SCHEMA}}}')
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


def test_version_links_to_its_release(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        v = c.get("/api/version").json()
        assert v["version"] and v["url"].endswith(f"/releases/tag/v{v['version']}")


def test_playlists_crud(tmp_path, monkeypatch):
    seed(tmp_path)
    with make_client(tmp_path, monkeypatch) as c:
        pl = c.post("/api/playlists", json={"name": "  Set   1 ", "items": [{"id": VID, "title": "T"}, {"id": "bad"}]}).json()
        assert pl["name"] == "Set 1" and [i["id"] for i in pl["items"]] == [VID]
        other = "aaaaaaaaaaa"
        assert len(c.post(f"/api/playlists/{pl['id']}/items", json=[{"id": other}, {"id": VID}]).json()["items"]) == 2
        assert c.patch(f"/api/playlists/{pl['id']}", json={"name": "B", "order": [other, VID]}).json()["items"][0]["id"] == other
        assert c.get(f"/api/playlists/{pl['id']}/status").json()[VID] == {"ready": True}
        assert c.post(f"/api/playlists/{pl['id']}/process?model=htdemucs_6s&tasks=stems").json() == {"queued": 1}
        assert len(c.delete(f"/api/playlists/{pl['id']}/items/{other}").json()["items"]) == 1
        assert c.delete(f"/api/playlists/{pl['id']}").status_code == 200
        assert c.get("/api/playlists").json() == []
        assert c.patch("/api/playlists/nope", json={"name": "x"}).status_code == 404


def test_queue_listing_endpoint(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as c:
        r = c.get("/api/jobs")
        assert r.status_code == 200 and r.json() == []
        assert c.post("/api/jobs/nope/promote").status_code == 404


def test_update_check_forces_a_fresh_look(monkeypatch):
    from prismix import updates

    seen = []
    monkeypatch.setattr(updates, "check", lambda force=False, client=None: seen.append(force) or {"available": False, "latest": "0.0.1", "url": "", "download": None})
    r = TestClient(server.create_app()).post("/api/update/check")
    assert r.status_code == 200 and seen == [True] and r.json()["available"] is False


def test_phone_may_edit_playlists_but_nothing_else(tmp_path, monkeypatch):
    from prismix import band
    monkeypatch.setattr(band, "is_loopback", lambda h: False)  # every caller is a phone on the LAN
    with make_client(tmp_path, monkeypatch) as c:
        pl = c.post("/api/playlists", json={"name": "Set"}).json()
        assert c.patch(f"/api/playlists/{pl['id']}", json={"name": "B"}).status_code == 200
        assert c.post(f"/api/playlists/{pl['id']}/items", json=[{"id": VID}]).status_code == 200
        assert c.delete(f"/api/playlists/{pl['id']}/items/{VID}").status_code == 200
        assert c.delete(f"/api/playlists/{pl['id']}").status_code == 200
        assert c.post("/api/playlists/import", json={"url": "https://youtube.com/playlist?list=x"}).status_code == 403
        assert c.post("/api/playlists/abcdef0123/process").status_code == 403
        assert c.delete(f"/api/library/{VID}").status_code == 403
        assert c.get("/api/band").status_code == 403


def test_quality_setting_roundtrip(tmp_path, monkeypatch):
    from prismix import quality

    monkeypatch.setattr(quality, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(quality, "device", lambda: "cpu")
    with make_client(tmp_path, monkeypatch) as c:
        r = c.get("/api/quality", params={"model": "htdemucs_ft"}).json()
        assert r["current"] == "standard"
        times = [lv["timeSec"] for lv in r["levels"]]
        assert times == sorted(times) and times[0] < times[-1]
        assert c.put("/api/quality", json={"quality": "high"}).json() == {"current": "high"}
        assert c.get("/api/quality").json()["current"] == "high"
        assert c.put("/api/quality", json={"quality": "nope"}).status_code == 400
        assert c.get("/api/quality", params={"model": "mix"}).status_code == 400


def test_quality_cost_and_speed(tmp_path, monkeypatch):
    from prismix import quality

    monkeypatch.setattr(quality, "SETTINGS_FILE", tmp_path / "settings.json")
    q = quality.QUALITIES
    assert quality.cost(q["standard"], "htdemucs_ft") == 1
    assert quality.cost(q["high"], "htdemucs_ft") > 2
    quality.record_speed("htdemucs_ft", "cpu", q["high"], 600, 200)
    assert quality._rtf("htdemucs_ft", "cpu")[1] is True
