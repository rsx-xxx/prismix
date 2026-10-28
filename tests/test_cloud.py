import hashlib
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from prismix import cloud, export, library, server, tagging

VID = "dQw4w9WgXcQ"
TOKEN = "A" * 43


class FakeCloud:
    """Just enough of the band cloud API: a bearer check, uploads kept in memory, and a commit that checks them."""

    def __init__(self, admin=True):
        self.admin = admin
        self.files: dict[str, bytes] = {}
        self.committed: dict | None = None
        self.puts: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": "unauthorized"})
        path = req.url.path
        if path == "/api/v1/index.json":
            return httpx.Response(200, json={"songs": [{"id": VID, "title": "T"}]})
        if req.method in ("PUT", "POST", "DELETE") and not self.admin:
            return httpx.Response(403, json={"error": "forbidden"})
        if req.method == "PUT":
            rel = path.split("/files/", 1)[1]
            if hashlib.sha256(req.content).hexdigest() != req.headers["x-sha256"]:
                return httpx.Response(422)
            self.files[rel] = req.content
            self.puts.append(rel)
            return httpx.Response(200, json={})
        if req.method == "POST" and path.endswith("/commit"):
            body = json.loads(req.content)
            if not isinstance(body.get("files"), list):
                return httpx.Response(400, json={"error": "files must be a list"})
            if any(self.files.get(f["path"]) is None for f in body["files"]):
                return httpx.Response(409, json={"missing": []})
            self.committed = body
            return httpx.Response(200, json={"id": VID, "rev": 1})
        if path.endswith("manifest.json"):
            if not self.committed:
                return httpx.Response(404)
            return httpx.Response(200, json={**self.committed, "rev": 1})
        if path.startswith("/files/"):
            data = self.files.get(path.split("/", 3)[3], b"0123456789")
            rng = req.headers.get("range")
            if rng:
                a, b = rng.removeprefix("bytes=").split("-")
                chunk = data[int(a): int(b) + 1 if b else None]
                return httpx.Response(206, stream=httpx.ByteStream(chunk), headers={"content-range": f"bytes {a}-{int(a) + len(chunk) - 1}/{len(data)}"})
            return httpx.Response(200, stream=httpx.ByteStream(data))
        return httpx.Response(404)


def cloud_for(fake):
    return cloud.Cloud("https://band.test", TOKEN, transport=httpx.MockTransport(fake))


def test_parse_credential():
    assert cloud.parse_credential(TOKEN, "band.test") == ("https://band.test", TOKEN)
    assert cloud.parse_credential(f"https://band.test/join?t={TOKEN}") == ("https://band.test", TOKEN)
    assert cloud.parse_credential(f"  https://band.test:8443/join?t={TOKEN}  ")[0] == "https://band.test:8443"
    assert cloud.parse_credential(TOKEN, "http://localhost:9000")[0] == "http://localhost:9000"
    for url, tok in [("http://band.test", TOKEN), ("", TOKEN), ("band.test", "short"), ("band.test", "bad token!" * 5),
                     ("ftp://band.test", TOKEN)]:
        with pytest.raises(cloud.CloudError):
            cloud.parse_credential(tok, url)


def test_valid_path():
    assert cloud.valid_path("mix.m4a") and cloud.valid_path("stems/vocals.m4a") and cloud.valid_path("a/b/c.json")
    for bad in ["", "../x", "a/b/c/d.m4a", ".hidden", "a//b", "a b.m4a", "x" * 129]:
        assert not cloud.valid_path(bad)


def test_credential_file_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "CRED_FILE", tmp_path / "band_cloud.json")
    assert cloud.load_credential() is None
    cloud.save_credential("https://band.test", TOKEN)
    assert cloud.load_credential() == ("https://band.test", TOKEN)
    assert (tmp_path / "band_cloud.json").stat().st_mode & 0o077 == 0
    cloud.clear_credential()
    assert cloud.load_credential() is None


def test_verify_admin_accepts_admin_and_refuses_member_or_bad_token():
    cloud_for(FakeCloud(admin=True)).verify_admin()
    with pytest.raises(cloud.CloudError, match="member"):
        cloud_for(FakeCloud(admin=False)).verify_admin()
    with pytest.raises(cloud.CloudError, match="refused"):
        cloud.Cloud("https://band.test", "B" * 43, transport=httpx.MockTransport(FakeCloud())).verify_admin()


def seed_song(tmp_path, monkeypatch, stems=("vocals", "drums")):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    d = tmp_path / VID / "htdemucs_ft"
    d.mkdir(parents=True)
    (tmp_path / VID / "info.json").write_text(json.dumps({"id": VID, "title": "Song", "channel": "Band", "duration": 12.5, "added": 1}))
    (tmp_path / VID / "thumb.jpg").write_bytes(b"jpg")
    (d / "meta.json").write_text(json.dumps({"id": VID, "stems": [{"name": n, "file": f"{n}.flac"} for n in stems]}))
    for n in stems:
        (d / f"{n}.flac").write_bytes(b"x")
    monkeypatch.setattr(tagging, "for_track", lambda vid: ({}, None))

    def fake_render(spec):
        assert spec.format == cloud.STEM_FORMAT
        return ("+".join(spec.stems)).encode() * 10, "n.m4a", "audio/mp4"

    monkeypatch.setattr(export, "render", fake_render)


def test_publish_uploads_then_commits(tmp_path, monkeypatch):
    seed_song(tmp_path, monkeypatch)
    fake = FakeCloud()
    steps = []
    res = cloud.publish(cloud_for(fake), VID, "htdemucs_ft", lambda s, d, t: steps.append((s, d, t)))
    assert res["rev"] == 1
    assert sorted(fake.files) == ["cover.jpg", "mix.m4a", "stems/drums.m4a", "stems/vocals.m4a"]
    body = fake.committed
    assert (body["title"], body["artist"], body["duration"], body["model"]) == ("Song", "Band", 12.5, "htdemucs_ft")
    assert body["stems"] == ["vocals", "drums"]
    assert {f["path"]: f["bytes"] for f in body["files"]}["stems/drums.m4a"] == len(b"drums" * 10)
    assert steps[-1] == ("commit", 4, 4)


def test_republish_skips_unchanged_files(tmp_path, monkeypatch):
    seed_song(tmp_path, monkeypatch)
    fake = FakeCloud()
    cloud.publish(cloud_for(fake), VID, "htdemucs_ft")
    fake.puts.clear()
    cloud.publish(cloud_for(fake), VID, "htdemucs_ft")
    assert fake.puts == []


def test_publish_needs_a_processed_song(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    with pytest.raises(cloud.CloudError, match="not processed"):
        cloud.publish(cloud_for(FakeCloud()), VID, "htdemucs_ft")


def test_publisher_reports_errors_and_done(tmp_path, monkeypatch):
    seed_song(tmp_path, monkeypatch)
    pub = cloud.Publisher()
    jid = pub.start(cloud_for(FakeCloud()), VID, "htdemucs_ft")
    for _ in range(100):
        if pub.get(jid)["state"] != "running":
            break
        time.sleep(0.05)
    assert pub.get(jid)["state"] == "done"
    bad = pub.start(cloud_for(FakeCloud(admin=False)), VID, "htdemucs_ft")
    for _ in range(100):
        if pub.get(bad)["state"] != "running":
            break
        time.sleep(0.05)
    assert pub.get(bad)["state"] == "error" and "admin" in pub.get(bad)["error"]


# ── the desktop's own API ──

@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "CRED_FILE", tmp_path / "cred.json")
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path / "lib")
    monkeypatch.setattr(server, "LIBRARY_DIR", tmp_path / "lib")
    fake = FakeCloud()
    real = cloud.Cloud
    monkeypatch.setattr(cloud, "Cloud", lambda url, token, **kw: real(url, token, transport=httpx.MockTransport(fake)))
    with TestClient(server.create_app()) as c:
        c.fake = fake
        yield c


def test_login_flow_and_proxy(api):
    assert api.get("/api/cloud").json() == {"signedIn": False, "host": ""}
    assert api.get("/api/cloud/index").status_code == 401
    assert api.post("/api/cloud/login", json={"url": "band.test", "token": "short"}).status_code == 400
    assert api.post("/api/cloud/login", json={"url": "band.test", "token": "B" * 43}).status_code == 401
    r = api.post("/api/cloud/login", json={"url": "band.test", "token": TOKEN})
    assert r.json() == {"signedIn": True, "host": "band.test"}
    assert TOKEN not in json.dumps(api.get("/api/cloud").json())
    assert api.get("/api/cloud/index").json()["songs"][0]["id"] == VID
    r = api.get(f"/api/cloud/files/{VID}/mix.m4a", headers={"range": "bytes=2-5"})
    assert (r.status_code, r.content, r.headers["content-range"]) == (206, b"2345", "bytes 2-5/10")
    assert api.get(f"/api/cloud/files/{VID}/../x").status_code in (400, 404)
    assert api.get("/api/cloud/files/short/mix.m4a").status_code == 404
    assert api.post("/api/cloud/logout").json()["signedIn"] is False
    assert api.get("/api/cloud/index").status_code == 401


def test_publish_endpoint(api, tmp_path, monkeypatch):
    seed_song(tmp_path / "lib", monkeypatch)
    api.post("/api/cloud/login", json={"url": "band.test", "token": TOKEN})
    assert api.post("/api/cloud/publish", json={"videoId": VID, "model": "mix"}).status_code == 404
    job = api.post("/api/cloud/publish", json={"videoId": VID, "model": "htdemucs_ft"}).json()["job"]
    for _ in range(100):
        st = api.get(f"/api/cloud/publish/{job}").json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done" and st["result"]["rev"] == 1


def test_cloud_api_is_for_this_computer_only(api, monkeypatch):
    from prismix import band
    monkeypatch.setattr(band, "is_loopback", lambda h: False)
    for method, path in [("get", "/api/cloud"), ("get", "/api/cloud/index"), ("post", "/api/cloud/logout")]:
        assert getattr(api, method)(path).status_code == 403
