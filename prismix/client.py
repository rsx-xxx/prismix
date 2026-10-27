"""Join another desktop's band as a client.

The window keeps loading the phone page (`/remote`) from this computer, so it is a secure context and the
audio engine works, while the data calls (`/api`, `/media`, `/ws`) are relayed to the host over HTTPS. The host's
self-signed certificate is fetched once on connect and is the only one trusted for that session.
"""
from __future__ import annotations

import asyncio
import contextlib
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx
from fastapi import Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from . import lan

PORTS = (443, lan.HTTPS_PORT)  # the clean name-only link, then the classic one
# Answered here even while connected: they describe this computer, not the host.
LOCAL_API = ("/api/client", "/api/update", "/api/version")
RELAYED = ("/api/", "/media/", "/remote/hello")
KEEP_REQUEST = ("range", "if-none-match", "if-modified-since", "x-band-token", "accept", "accept-encoding")
KEEP_RESPONSE = ("content-type", "content-length", "content-range", "accept-ranges", "content-encoding",
                 "cache-control", "etag", "last-modified")


class ConnectError(Exception):
    """A message fit to show in the Connect dialog."""


@dataclass
class Upstream:
    host: str
    port: int
    ctx: ssl.SSLContext
    hello: dict
    http: httpx.AsyncClient

    @property
    def base(self) -> str:
        return f"https://{self.host}:{self.port}"

    def status(self) -> dict:
        return {"connected": True, "name": self.hello.get("name"), "host": f"{self.host}:{self.port}",
                "version": self.hello.get("version")}


def parse_address(text: str) -> tuple[str, list[int]]:
    """`prismix.local`, `192.168.1.5:8443` or a whole phone link -> (host, ports to try)."""
    raw = text.strip()
    if not raw:
        raise ConnectError("Type the host's name or address")
    u = urlparse(raw if "://" in raw else f"https://{raw}")
    try:
        host, port = u.hostname, u.port
    except ValueError:
        raise ConnectError("That address does not look right") from None
    if not host:
        raise ConnectError("That address does not look right")
    return host, [port] if port else list(PORTS)


def _pinned(pem: str) -> ssl.SSLContext:
    # a plain context on purpose: create_default_context() is X509_STRICT on new Pythons and rejects a self-signed leaf
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(cadata=pem)
    ctx.check_hostname = False  # the certificate itself is the pin
    return ctx


def _handshake(host: str, ports: list[int]) -> tuple[int, ssl.SSLContext, dict]:
    last: Exception | None = None
    for port in ports:
        try:
            pem = ssl.get_server_certificate((host, port), timeout=4)
            ctx = _pinned(pem)
            with httpx.Client(verify=ctx, timeout=5) as c:
                r = c.get(f"https://{host}:{port}/remote/hello")
                r.raise_for_status()
                hello = r.json()
        except (OSError, ValueError, httpx.HTTPError) as e:
            last = e
            continue
        if hello.get("app") != "prismix":
            raise ConnectError("That is not a Prismix host")
        return port, ctx, hello
    raise ConnectError(f"Could not reach {host}" + (f" ({last})" if last else ""))


async def connect(text: str) -> Upstream:
    host, ports = parse_address(text)
    port, ctx, hello = await asyncio.to_thread(_handshake, host, ports)
    if hello.get("id") == lan.host_id():
        raise ConnectError("That is this computer")
    http = httpx.AsyncClient(verify=ctx, timeout=httpx.Timeout(30, connect=8))
    return Upstream(host, port, ctx, hello, http)


def discover(timeout: float = 2.0) -> list[dict]:
    """Prismix hosts announcing themselves on this network, this computer left out."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    found: dict[str, tuple[str, int]] = {}
    lock = threading.Lock()

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, 1500)
            addrs = info.parsed_addresses() if info else []
            if addrs and info.port:
                with lock:
                    found[name] = (addrs[0], info.port)

        update_service = remove_service = lambda self, *a: None  # noqa: E731

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, lan.SERVICE, Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    me = lan.host_id()
    hosts = []
    for ip, port in found.values():
        try:  # public metadata only, so the unknown certificate is not an issue here
            with httpx.Client(verify=False, timeout=3) as c:  # noqa: S501
                hello = c.get(f"https://{ip}:{port}/remote/hello").json()
        except (httpx.HTTPError, ValueError):
            continue
        if hello.get("app") == "prismix" and hello.get("id") != me:
            hosts.append({"id": hello.get("id"), "name": hello.get("name") or ip, "version": hello.get("version"),
                          "pin": bool(hello.get("pin")), "address": f"{ip}:{port}"})
    return sorted(hosts, key=lambda h: h["name"].lower())


def wants(path: str) -> bool:
    return path == "/ws" or (path.startswith(RELAYED) and not path.startswith(LOCAL_API))


async def forward(request: Request, up: Upstream) -> Response:
    """Relay a read-only request to the host and stream its answer back."""
    if request.method not in ("GET", "HEAD"):
        return Response("Read-only while joined to a host", status_code=405)
    url = up.base + quote(request.url.path, safe="/:@!$&'()*+,;=-._~")
    if request.url.query:
        url += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k in KEEP_REQUEST}
    try:
        r = await up.http.send(up.http.build_request(request.method, url, headers=headers), stream=True)
    except httpx.HTTPError:
        return Response("The host did not answer", status_code=502)
    out = {k: v for k, v in r.headers.items() if k in KEEP_RESPONSE}
    return StreamingResponse(r.aiter_raw(), status_code=r.status_code, headers=out,
                             background=BackgroundTask(r.aclose))


async def relay_ws(ws: WebSocket, up: Upstream) -> None:
    """Pipe the band socket to the host, close codes included (4001 wrong PIN, 4003 removed)."""
    from websockets.asyncio.client import connect as ws_connect

    await ws.accept()
    try:
        remote = await ws_connect(f"wss://{up.host}:{up.port}/ws", ssl=up.ctx, open_timeout=8, max_size=2**20)
    except Exception:  # noqa: BLE001 - the page retries on its own
        await ws.close(code=1013)
        return

    async def down() -> None:
        async for msg in remote:
            await ws.send_text(msg if isinstance(msg, str) else msg.decode())

    async def up_() -> None:
        while True:
            await remote.send(await ws.receive_text())

    tasks = [asyncio.ensure_future(down()), asyncio.ensure_future(up_())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        code = remote.close_code or 1011
        with contextlib.suppress(Exception):
            await remote.close()
        with contextlib.suppress(Exception):
            await ws.close(code=code if 3000 <= code < 5000 else 1000)
