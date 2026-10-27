import pytest
from fastapi.testclient import TestClient

from prismix import band as band_mod
from prismix import library, server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(server, "LIBRARY_DIR", tmp_path)
    app = server.create_app()
    with TestClient(app) as c:
        c.app = app
        yield c


def lan(monkeypatch):
    monkeypatch.setattr(band_mod, "is_loopback", lambda h: False)


def test_host_state_reaches_remote_and_cmd_reaches_host(client):
    with client.websocket_connect("/ws") as host, client.websocket_connect("/ws") as phone:
        host.send_json({"t": "hello", "host": True, "name": "Desk"})
        assert host.receive_json()["host"] is True
        host.receive_json()  # roster
        phone.send_json({"t": "hello", "name": "Ann", "role": "singer"})
        w = phone.receive_json()
        assert w["t"] == "welcome" and w["hostOnline"]
        host.send_json({"t": "state", "playing": True, "pos": 3.5})
        while (m := phone.receive_json())["t"] != "state":
            pass
        assert m["pos"] == 3.5 and "s" in m
        phone.send_json({"t": "cmd", "op": "tempo", "v": 0.9})
        while (m := host.receive_json())["t"] != "cmd":
            pass
        assert (m["op"], m["v"], m["from"]) == ("tempo", 0.9, "Ann")


def test_ping_pong(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"t": "hello"})
        ws.receive_json()
        ws.send_json({"t": "ping", "c": 42})
        while (m := ws.receive_json())["t"] != "pong":
            pass
        assert m["c"] == 42 and m["s"] > 0


def test_lock_blocks_remote_commands(client, monkeypatch):
    client.post("/api/band", json={"locked": True})
    with client.websocket_connect("/ws") as host:
        host.send_json({"t": "hello", "host": True})
        host.receive_json()
        lan(monkeypatch)
        with client.websocket_connect("/ws") as phone:
            phone.send_json({"t": "hello"})
            phone.receive_json()
            phone.send_json({"t": "cmd", "op": "play"})
            while phone.receive_json()["t"] != "locked":
                pass


def test_pin_required_on_lan(client, monkeypatch):
    client.post("/api/band", json={"token": "1234"})
    lan(monkeypatch)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"t": "hello", "token": "nope"})
        assert ws.receive_json() == {"t": "denied", "why": "pin"}
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"t": "hello", "token": "1234"})
        assert ws.receive_json()["t"] == "welcome"


def test_lan_http_is_read_only(client, monkeypatch):
    lan(monkeypatch)
    assert client.get("/api/library").status_code == 200
    assert client.post("/api/process", json={"url": "x"}).status_code == 403
    assert client.get("/api/band").status_code == 403
    client.app.state.band.configure(token="9")
    assert client.get("/api/library").status_code == 401
    assert client.get("/api/library", headers={"x-band-token": "9"}).status_code == 200


def test_qr_scales_with_css():
    from prismix.server import band_qr

    svg = band_qr("https://prismix.local:8443/remote")
    assert "viewBox=" in svg and "width=" not in svg.split(">")[0]


def test_host_name_is_ours(monkeypatch):
    from prismix import lan

    monkeypatch.setattr(lan, "_name", "")
    monkeypatch.delenv("PRISMIX_HOST_NAME", raising=False)
    monkeypatch.setattr(lan, "_taken", lambda host: False)
    assert lan.host_name() == "prismix.local"
    monkeypatch.setattr(lan, "_name", "")
    monkeypatch.setattr(lan, "_taken", lambda host: True)
    monkeypatch.setattr(lan.socket, "gethostname", lambda: "Jfx_VCK9174.lan")
    assert lan.host_name() == "prismix-jfx-vck9174.local"
    monkeypatch.setattr(lan, "_name", "")


def test_app_qr_uses_the_app_scheme(client):
    client.app.state.band.share_url = "https://prismix.local:8443/remote?ip=192.168.1.5"
    s = client.get("/api/band").json()
    assert s["qr"] and s["appQr"] and s["qr"] != s["appQr"]


def test_host_name_is_editable_and_announced(client, tmp_path, monkeypatch):
    from prismix import lan as lan_mod

    monkeypatch.setattr(lan_mod, "_identity_path", lambda: tmp_path / "host.json")
    first = client.get("/remote/hello").json()
    assert first["app"] == "prismix" and first["id"] and first["name"]
    assert client.post("/api/band", json={"name": "  Studio A "}).json()["name"] == "Studio A"
    again = client.get("/remote/hello").json()
    assert again["name"] == "Studio A" and again["id"] == first["id"]
    assert client.post("/api/band", json={"name": ""}).json()["name"] == first["name"]


def test_phone_speaker_is_announced_and_cleared_on_leave(client):
    with client.websocket_connect("/ws") as host, client.websocket_connect("/ws") as phone:
        host.send_json({"t": "hello", "host": True})
        phone.send_json({"t": "hello", "name": "Ann"})
        me = phone.receive_json()["id"]
        phone.send_json({"t": "speaker", "on": True})
        while (m := host.receive_json())["t"] != "roster" or m["speaker"] != me:
            pass
        phone.close()
        while (m := host.receive_json())["t"] != "roster" or m["speaker"] is not None:
            pass


def test_host_picks_the_device_that_plays(client):
    with client.websocket_connect("/ws") as host, client.websocket_connect("/ws") as phone:
        host.send_json({"t": "hello", "host": True})
        phone.send_json({"t": "hello", "name": "Ann"})
        me = phone.receive_json()["id"]
        host.send_json({"t": "admin", "speaker": me})
        while (m := host.receive_json())["t"] != "roster" or m["speaker"] != me:
            pass
        host.send_json({"t": "admin", "speaker": 9999})
        while (m := host.receive_json())["t"] != "roster" or m["speaker"] is not None:
            pass
        phone.close()


def test_shared_change_is_announced_to_everyone_else(client):
    with client.websocket_connect("/ws") as host, client.websocket_connect("/ws") as a, client.websocket_connect("/ws") as b:
        host.send_json({"t": "hello", "host": True})
        a.send_json({"t": "hello", "name": "Ann"})
        b.send_json({"t": "hello", "name": "Bob"})
        a.send_json({"t": "cmd", "op": "chordkey", "v": 2})
        while (m := b.receive_json())["t"] != "changed":
            pass
        assert (m["who"], m["op"], m["v"]) == ("Ann", "chordkey", 2)
        while (m := host.receive_json())["t"] != "changed":
            pass
        assert m["who"] == "Ann"


def test_cross_site_write_and_socket_refused_on_loopback(client, monkeypatch):
    monkeypatch.setattr(band_mod, "is_loopback", lambda h: True)
    evil = {"origin": "https://evil.example"}
    assert client.post("/api/open-link?url=https://example.com", headers=evil).status_code == 403
    assert client.get("/api/band", headers=evil).status_code == 200  # reads stay open
    with pytest.raises(Exception), client.websocket_connect("/ws", headers=evil):  # noqa: B017 - closed before accept
        pass


def test_same_origin():
    assert band_mod.same_origin(None, "127.0.0.1:8000")
    assert band_mod.same_origin("http://127.0.0.1:8000", "127.0.0.1:8000")
    assert not band_mod.same_origin("https://evil.example", "127.0.0.1:8000")


def test_stalled_client_does_not_block_broadcast(monkeypatch):
    import asyncio

    band = band_mod.Band()
    monkeypatch.setattr(band_mod, 'SEND_TIMEOUT', 0.05)

    class Stuck:
        client = None

        async def send_text(self, _):
            await asyncio.sleep(10)

        async def close(self, code=1000):
            pass

    class Fine(Stuck):
        got: list = []

        async def send_text(self, text):
            self.got.append(text)

    for i, ws in enumerate((Stuck(), Fine()), 1):
        band.clients[i] = band_mod.Client(ws, i, "x", "other", False)
    asyncio.run(asyncio.wait_for(band.broadcast({"t": "x"}), 2))
    assert Fine.got
