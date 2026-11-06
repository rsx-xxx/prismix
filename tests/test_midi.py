import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

from prismix import midi


class FakePort:
    def __init__(self, cb):
        self.cb = cb
        self.closed = False

    def close(self):
        self.closed = True


def test_session_opens_the_wanted_port_forwards_only_note_ons_and_follows_the_plug():
    async def run():
        loop = asyncio.get_running_loop()
        names = ["TD-17"]
        opened = []

        def open_port(name, cb):
            port = FakePort(cb)
            opened.append((name, port))
            return port

        s = midi.MidiSession(loop, names=lambda: list(names), open_port=open_port)
        assert s.scan() and s.ports == ["TD-17"] and s.open == ""  # nothing asked for yet
        s.use("TD-17")
        assert s.open == "TD-17"
        cb = opened[0][1].cb
        cb(SimpleNamespace(type="note_on", note=36, velocity=100, channel=9))
        cb(SimpleNamespace(type="note_on", note=36, velocity=0, channel=9))  # a note-on at velocity 0 is a note-off
        cb(SimpleNamespace(type="note_off", note=36, velocity=64, channel=9))
        cb(SimpleNamespace(type="clock"))  # system messages have no note at all
        hit = await asyncio.wait_for(s.queue.get(), 1)
        assert (hit["n"], hit["v"], hit["type"]) == (36, 100, "hit") and hit["s"] > 0
        assert s.queue.empty()
        names.clear()  # pulled out
        assert s.scan() and s.open == "" and opened[0][1].closed
        names.append("TD-17")  # plugged back in: reopened without the page asking again
        assert s.scan() and s.open == "TD-17" and len(opened) == 2
        s.close()
        assert opened[1][1].closed

    asyncio.run(run())


def test_midi_socket_answers_clock_pings_and_lists_ports(tmp_path, monkeypatch):
    from prismix import library, server

    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(server, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(midi, "input_names", lambda: [])
    monkeypatch.setattr(midi, "RESCAN", 0.05)
    with TestClient(server.create_app()) as c, c.websocket_connect("/ws/midi") as ws:
        assert ws.receive_json() == {"type": "ports", "ports": [], "open": ""}
        ws.send_json({"type": "ping", "c": 12.5})
        pong = ws.receive_json()
        assert pong["type"] == "pong" and pong["c"] == 12.5 and pong["s"] > 0
