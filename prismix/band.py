"""Band mode: a WebSocket hub that lets phones on the LAN follow and drive the host's player.

The host is the browser session on the desktop app (loopback). It publishes player state;
remotes send commands that the hub relays to it. The server clock is the shared timeline:
clients estimate their offset with ping/pong and extrapolate the host's position anchors.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import itertools
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

ROLES = ("singer", "guitar", "bass", "drums", "keys", "other")
CMD_OPS = {"play", "pause", "seek", "tempo", "pitch", "cents", "loop", "mute", "solo", "load"}
MAX_MSG = 16_384


def now_ms() -> float:
    return time.time() * 1000.0


def is_loopback(host: str | None) -> bool:
    return host in ("127.0.0.1", "::1", "localhost")


class Client:
    def __init__(self, ws: WebSocket, cid: int, name: str, role: str, host: bool):
        self.ws = ws
        self.id = cid
        self.name = name
        self.role = role
        self.host = host
        self.addr = ws.client.host if ws.client else ""
        self.since = now_ms()
        self.rtt: float | None = None
        self.synced = True

    def info(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "addr": self.addr,
            "host": self.host,
            "rtt": self.rtt,
            "synced": self.synced,
        }


class Band:
    def __init__(self) -> None:
        self.clients: dict[int, Client] = {}
        self.locked = False
        self.token = ""
        self.state: dict = {}
        self.speaker: int | None = None  # a phone that plays the room's sound; the host then stays silent
        self.share_url = ""
        self._ids = itertools.count(1)

    # ── settings ──
    def configure(self, *, token: str | None = None, locked: bool | None = None) -> None:
        if token is not None:
            self.token = token.strip()
        if locked is not None:
            self.locked = bool(locked)

    def status(self) -> dict:
        return {
            "locked": self.locked,
            "token": bool(self.token),
            "hostOnline": self.host is not None,
            "speaker": self.speaker,
            "clients": [c.info() for c in self.clients.values() if not c.host],
        }

    @property
    def host(self) -> Client | None:
        return next((c for c in self.clients.values() if c.host), None)

    def token_ok(self, given: str | None) -> bool:
        return not self.token or hmac.compare_digest(self.token, given or "")

    # ── fan-out ──
    async def _send(self, c: Client, msg: dict) -> None:
        try:
            await c.ws.send_text(json.dumps(msg, separators=(",", ":")))
        except Exception:  # noqa: BLE001 - peer vanished; the receive loop will clean up
            log.debug("send to client %s failed", c.id)

    async def broadcast(self, msg: dict, *, skip: Client | None = None) -> None:
        await asyncio.gather(*(self._send(c, msg) for c in list(self.clients.values()) if c is not skip))

    async def roster(self) -> None:
        await self.broadcast({"t": "roster", **self.status()})

    async def kick(self, cid: int) -> bool:
        c = self.clients.get(cid)
        if not c or c.host:
            return False
        await self._send(c, {"t": "kicked"})
        with contextlib.suppress(Exception):
            await c.ws.close(code=4003)
        return True

    # ── connection ──
    async def serve(self, ws: WebSocket) -> None:
        await ws.accept()
        local = is_loopback(ws.client.host if ws.client else None)
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
        except Exception:  # noqa: BLE001
            await ws.close(code=4000)
            return
        if hello.get("t") != "hello":
            await ws.close(code=4000)
            return
        is_host = local and bool(hello.get("host"))
        if not local and not self.token_ok(hello.get("token")):
            await ws.send_text(json.dumps({"t": "denied", "why": "pin"}))
            await ws.close(code=4001)
            return
        role = hello.get("role") if hello.get("role") in ROLES else "other"
        name = str(hello.get("name") or "").strip()[:24] or "Phone"
        c = Client(ws, next(self._ids), name, role, is_host)
        if is_host and (old := self.host) is not None:
            with contextlib.suppress(Exception):
                await old.ws.close(code=4002)
            self.clients.pop(old.id, None)
        self.clients[c.id] = c
        await self._send(c, {"t": "welcome", "id": c.id, "now": now_ms(), "state": self.state,
                             "host": is_host, "admin": local, **self.status()})
        await self.roster()
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_MSG:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                await self._handle(c, msg, local)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.exception("band client %s crashed", c.id)
        finally:
            self.clients.pop(c.id, None)
            if self.speaker == c.id:
                self.speaker = None
            await self.roster()

    async def _handle(self, c: Client, msg: dict, local: bool) -> None:
        kind = msg.get("t")
        if kind == "ping":
            await self._send(c, {"t": "pong", "c": msg.get("c"), "s": now_ms()})
        elif kind == "rtt":
            r = msg.get("v")
            c.rtt = float(r) if isinstance(r, (int, float)) else None
        elif kind == "sync":
            c.synced = bool(msg.get("v"))
            await self.roster()
        elif kind == "state" and c.host:
            msg["s"] = now_ms()
            self.state = msg
            await self.broadcast(msg, skip=c)
        elif kind == "cmd":
            if self.locked and not local:
                await self._send(c, {"t": "locked"})
                return
            if msg.get("op") in CMD_OPS and (h := self.host) is not None:
                await self._send(h, {"t": "cmd", "op": msg["op"], "v": msg.get("v"), "from": c.name})
        elif kind == "speaker" and not c.host:
            if msg.get("on"):
                self.speaker = c.id
            elif self.speaker == c.id:
                self.speaker = None
            await self.roster()
        elif kind == "admin" and local:
            if "locked" in msg:
                self.locked = bool(msg["locked"])
            if isinstance(msg.get("kick"), int):
                await self.kick(msg["kick"])
            await self.roster()
