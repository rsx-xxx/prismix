import socket
import threading
import time
import types

import pytest
import uvicorn
from fastapi.testclient import TestClient

from prismix import client, lan, library, server


def test_parse_address():
    assert client.parse_address("prismix.local") == ("prismix.local", [443, 8443])
    assert client.parse_address("192.168.1.5:8443") == ("192.168.1.5", [8443])
    assert client.parse_address("https://prismix.local/remote?ip=192.168.1.5") == ("prismix.local", [443, 8443])
    with pytest.raises(client.ConnectError):
        client.parse_address("  ")


def test_wants():
    assert client.wants("/ws") and client.wants("/api/library") and client.wants("/media/x/a.flac")
    assert client.wants("/remote/hello")
    assert not client.wants("/api/client") and not client.wants("/api/update") and not client.wants("/remote.js")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A real TLS host on loopback, the way a second desktop serves the band."""
    monkeypatch.setattr(lan, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path / "lib")
    monkeypatch.setattr(server, "LIBRARY_DIR", tmp_path / "lib")
    (tmp_path / "lib" / "dQw4w9WgXcQ").mkdir(parents=True)
    (tmp_path / "lib" / "dQw4w9WgXcQ" / "thumb.jpg").write_bytes(b"jpeg")
    (tmp_path / "lib" / "dQw4w9WgXcQ" / "htdemucs_6s").mkdir()
    (tmp_path / "lib" / "dQw4w9WgXcQ" / "htdemucs_6s" / "meta.json").write_text('{"id": "dQw4w9WgXcQ", "stems": [{"file": "a.flac"}]}')
    (tmp_path / "lib" / "dQw4w9WgXcQ" / "htdemucs_6s" / "a.flac").write_bytes(b"x")
    (tmp_path / "lib" / "dQw4w9WgXcQ" / "info.json").write_text('{"id": "dQw4w9WgXcQ", "title": "T", "duration": 5, "added": 1}')
    crt, key = lan.ensure_cert("127.0.0.1", "")
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(server.create_app(), host="127.0.0.1", port=port, ssl_certfile=str(crt),
                                        ssl_keyfile=str(key), log_level="warning", log_config=None))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield port
    srv.should_exit = True


def test_join_relays_a_host(host, monkeypatch, tmp_path):
    # the host runs in this process too, so give the client side its own identity
    monkeypatch.setattr(client, "lan", types.SimpleNamespace(host_id=lambda: "me", HTTPS_PORT=lan.HTTPS_PORT, SERVICE=lan.SERVICE))
    monkeypatch.setattr(server.band_mod, "is_loopback", lambda h: True)
    with TestClient(server.create_app()) as c:
        assert c.get("/api/client").json() == {"connected": False}
        assert c.post("/api/client/connect", json={"host": "127.0.0.1:1"}).status_code == 400
        st = c.post("/api/client/connect", json={"host": f"127.0.0.1:{host}"}).json()
        assert st["connected"] and st["host"] == f"127.0.0.1:{host}"
        assert c.get("/", follow_redirects=False).headers["location"] == "/remote"
        assert [i["id"] for i in c.get("/api/library").json()] == ["dQw4w9WgXcQ"]
        assert c.get("/media/dQw4w9WgXcQ/thumb.jpg").content == b"jpeg"
        assert c.post("/api/library", json={}).status_code == 405
        with c.websocket_connect("/ws") as ws:
            ws.send_json({"t": "hello", "name": "Desk", "role": "guitar"})
            assert ws.receive_json()["t"] == "welcome"
        assert c.post("/api/client/disconnect").json() == {"connected": False}
        assert c.get("/api/client").json() == {"connected": False}


def test_join_refuses_cross_site(monkeypatch):
    monkeypatch.setattr(server.band_mod, "is_loopback", lambda h: True)
    with TestClient(server.create_app()) as c:
        r = c.post("/api/client/connect", json={"host": "x"}, headers={"origin": "https://evil.example"})
        assert r.status_code == 403
