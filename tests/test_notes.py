import pytest
from fastapi.testclient import TestClient

from prismix import library, notes, server

VID = "abcdefghijk"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(server, "LIBRARY_DIR", tmp_path)
    (tmp_path / VID).mkdir()
    (tmp_path / VID / "info.json").write_text("{}")
    with TestClient(server.create_app()) as c:
        yield c


def test_add_edit_delete(client):
    items = notes.apply(VID, "add", {"t": 12.345, "icon": "up", "text": " loud \n", "show": "always"}, "Ann")
    n = items[0]
    assert (n["t"], n["icon"], n["text"], n["show"], n["by"]) == (12.35, "up", "loud", "always", "Ann")
    items = notes.apply(VID, "edit", {"id": n["id"], "text": "louder", "icon": "bogus"}, "Bob")
    assert items[0]["text"] == "louder" and items[0]["icon"] == "talk" and items[0]["by"] == "Ann"
    assert client.get(f"/api/notes/{VID}").json() == items
    assert notes.apply(VID, "del", {"id": n["id"]}, "Bob") == []
    assert notes.apply(VID, "del", {"id": n["id"]}, "Bob") == []  # already gone


@pytest.mark.parametrize("raw", [{"t": -1}, {"t": "x"}, {"t": 1e9}, {}])
def test_bad_time(client, raw):
    with pytest.raises(ValueError):
        notes.apply(VID, "add", raw, "Ann")


def test_unknown_song(client):
    with pytest.raises(ValueError):
        notes.apply("zzzzzzzzzzz", "add", {"t": 1}, "Ann")
    assert client.get("/api/notes/bad").status_code == 404


def test_sorted_by_time(client):
    notes.apply(VID, "add", {"t": 9}, "A")
    notes.apply(VID, "add", {"t": 3}, "A")
    assert [n["t"] for n in notes.load(VID)] == [3, 9]


def test_band_broadcast_even_when_locked(client):
    with client.websocket_connect("/ws") as host, client.websocket_connect("/ws") as phone:
        host.send_json({"t": "hello", "host": True, "name": "Desk"})
        phone.send_json({"t": "hello", "name": "Ann", "role": "singer"})
        host.send_json({"t": "admin", "locked": True})
        phone.send_json({"t": "note", "op": "add", "id": VID, "note": {"t": 5, "icon": "bang"}})
        while (m := host.receive_json())["t"] != "notes":
            pass
        assert m["id"] == VID and m["notes"][0]["by"] == "Ann"
        phone.send_json({"t": "note", "op": "add", "id": "bad", "note": {"t": 5}})
        while (m := phone.receive_json())["t"] != "noteerr":
            pass


def test_http_sticker_roundtrip(client):
    r = client.post(f"/api/notes/{VID}", json={"op": "add", "note": {"t": 2, "text": "hi"}})
    assert r.status_code == 200 and r.json()[0]["text"] == "hi"
    nid = r.json()[0]["id"]
    assert client.post(f"/api/notes/{VID}", json={"op": "del", "note": {"id": nid}}).json() == []
    assert client.post("/api/notes/not-an-id", json={"op": "add", "note": {}}).status_code == 404
