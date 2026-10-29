"""Share the host over the LAN: a self-signed HTTPS listener next to the loopback one.

Phones need a secure context for the AudioWorklet that the player is built on, and a plain
http://192.168.x.x page does not get one. A self-signed certificate (accepted once per phone)
does.
"""
from __future__ import annotations

import datetime as dt
import getpass
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uvicorn

from .config import DATA_DIR

log = logging.getLogger(__name__)
HTTPS_PORT = 8443  # the Android app and old links use it
STANDARD_HTTPS, STANDARD_HTTP = 443, 80  # so that a bare https://prismix.local/ works


def _local_for(target: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((target, 1))  # UDP connect sends nothing, it only picks the route
            return s.getsockname()[0]
        except OSError:
            return ""


def lan_ip() -> str:
    """The address phones can reach. A VPN often owns the default route, so probe private ranges first."""
    if forced := os.environ.get("PRISMIX_LAN_IP"):
        return forced
    for target in ("192.168.0.1", "192.168.100.1", "10.0.0.1", "172.16.0.1", "8.8.8.8"):
        ip = _local_for(target)
        addr = ipaddress.ip_address(ip) if ip else None
        if addr and addr.is_private and not addr.is_loopback and addr not in ipaddress.ip_network("100.64.0.0/10"):
            return ip
    return ""


DEFAULT_NAME = "prismix"
_name = ""


def _taken(host: str) -> bool:
    """True when another machine on the network already answers to this mDNS name."""
    box: list[bool] = []

    def ask() -> None:
        try:
            socket.getaddrinfo(host, None, socket.AF_INET)
            box.append(True)
        except OSError:
            box.append(False)

    t = threading.Thread(target=ask, daemon=True)
    t.start()
    t.join(1.5)
    return bool(box and box[0])


def host_name() -> str:
    """The name phones open (`prismix.local`) instead of an address that changes with DHCP.

    Our own mDNS record, not the computer's, so the link reads the same on every machine. If another Prismix host
    already owns it, fall back to `prismix-<computer>.local`. `PRISMIX_HOST_NAME` overrides it.
    """
    global _name
    if _name:
        return _name
    if forced := os.environ.get("PRISMIX_HOST_NAME"):
        label = re.sub(r"[^a-z0-9-]+", "-", forced.lower().removesuffix(".local")).strip("-")
    else:
        label = DEFAULT_NAME
        if _taken(f"{label}.local"):
            mine = re.sub(r"[^a-z0-9-]+", "-", socket.gethostname().split(".")[0].lower()).strip("-")
            label = f"{label}-{mine}"[:40].strip("-")
    _name = f"{label}.local" if label else ""
    return _name


def ensure_cert(ip: str, name: str = ""):
    """(cert, key) paths valid for `ip` and `name`, regenerated whenever either changes."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    d = DATA_DIR / "band"
    d.mkdir(parents=True, exist_ok=True)
    crt, key, stamp = d / "cert.pem", d / "key.pem", d / "cert.ip"
    if crt.exists() and key.exists() and stamp.exists() and stamp.read_text() == f"{ip}|{name}":
        return crt, key
    k = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Prismix")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(
            [x509.IPAddress(ipaddress.ip_address(ip))] + ([x509.DNSName(name)] if name else [])), False)
        .sign(k, hashes.SHA256())
    )
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    key.chmod(0o600)
    stamp.write_text(f"{ip}|{name}")
    return crt, key


SERVICE = "_prismix._tcp.local."


def advertise(ip: str, port: int):
    """Announce the host over mDNS so the phone app finds it without typing an address.

    Returns `move(ip)`, which re-announces on a new address after the network changed.
    """
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        log.warning("band: the zeroconf package is missing, phones must be pointed at the host by hand")
        return lambda _ip: None
    name = socket.gethostname().split(".")[0] or "host"
    lock = threading.Lock()
    live: dict = {}

    def announce(addr: str) -> None:
        info = ServiceInfo(
            SERVICE, f"Prismix on {name}.{SERVICE}", addresses=[socket.inet_aton(addr)], port=port,
            properties={"path": "/remote", "host": name}, server=f"{host_name() or name + '.local'}.")
        with lock:
            if zc := live.pop("zc", None):
                try:
                    zc.unregister_all_services()
                    zc.close()
                except Exception:  # noqa: BLE001 - the old interface is already gone
                    log.debug("band: closing the old mDNS responder failed", exc_info=True)
            try:
                zc = Zeroconf(interfaces=[addr])
                zc.register_service(info)
                live["zc"] = zc
            except Exception:
                log.warning("band: mDNS announce failed", exc_info=True)

    def move(addr: str) -> None:
        threading.Thread(target=announce, args=(addr,), daemon=True, name="band-mdns").start()

    move(ip)
    return move


def _free(port: int, ip: str) -> bool:
    """True when this process may listen on `ip:port` (ports below 1024 need rights on some systems)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((ip, port))
            return True
        except OSError:
            return False


def _redirect_http(name: str, port: int, ip: str) -> ThreadingHTTPServer | None:
    """Plain http://prismix.local goes to the https page; a browser tries http first when only a name is typed."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            host = (self.headers.get("Host") or name).split(":")[0]
            self.send_response(308)
            self.send_header("Location", f"https://{host}{'' if port == 443 else f':{port}'}{self.path}")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_HEAD = do_GET

        def log_message(self, *args):
            pass

    try:
        srv = ThreadingHTTPServer((ip, STANDARD_HTTP), Handler)
    except OSError:
        return None
    threading.Thread(target=srv.serve_forever, daemon=True, name="band-http-redirect").start()
    return srv


def _share_url(name: str, ip: str, port: int, clean: bool) -> str:
    # the name survives a new DHCP lease; the address rides along for phones that cannot resolve .local
    if name:
        return f"https://{name if clean else f'{name}:{port}'}/remote?ip={ip}"
    return f"https://{ip}:{port}/remote"


def _watch_network(app, move, name: str, port: int, clean: bool, ip: str) -> None:
    """The Wi-Fi changes under a running host (sleep, roaming, a new DHCP lease): keep the link, QR and mDNS current."""
    def run() -> None:
        current = ip
        while True:
            time.sleep(8)
            try:
                now = lan_ip()
            except Exception:  # noqa: BLE001 - never let the watcher die
                continue
            if now and now != current:
                log.info("band: network address changed %s -> %s", current, now)
                current = now
                app.state.band.share_url = _share_url(name, now, port, clean)
                move(now)

    threading.Thread(target=run, daemon=True, name="band-network").start()


def start(app, port: int = HTTPS_PORT) -> uvicorn.Server | None:
    """Serve `app` over HTTPS on all interfaces in a daemon thread and publish the phone link."""
    ip = lan_ip()
    if not ip:
        log.warning("band: no LAN address, sharing is off")
        return None
    try:
        name = host_name()
        crt, key = ensure_cert(ip, name)
    except ImportError:
        log.warning("band: the cryptography package is missing, sharing is off")
        return None

    def serve(on: int, host: str = "0.0.0.0") -> uvicorn.Server:
        # lifespan off: the main server owns the job queue; a second startup on this thread's loop broke the shared asyncio events
        # a vanished phone is noticed within ~20 s instead of the TCP timeout
        server = uvicorn.Server(uvicorn.Config(
            app, host=host, port=on, ssl_certfile=str(crt), ssl_keyfile=str(key),
            log_level="warning", log_config=None, lifespan="off", ws_ping_interval=10, ws_ping_timeout=10))
        threading.Thread(target=server.run, daemon=True).start()
        return server

    server = serve(port)
    # The bare-name ports 80/443 listen on the LAN address only. Bound to every interface they would also take
    # 127.0.0.1:443 and answer for https://anything.localhost/, hiding other local apps behind this one.
    clean = bool(name) and _free(STANDARD_HTTPS, ip)
    std: dict = {}

    def standard(on: str) -> None:
        std["https"] = serve(STANDARD_HTTPS, on)
        std["http"] = _redirect_http(name, STANDARD_HTTPS, on)

    if clean:
        standard(ip)
    app.state.band.share_url = _share_url(name, ip, port, clean)
    advertised = advertise(ip, port)

    def move(now: str) -> None:
        advertised(now)
        if clean:  # the listeners follow the address
            std["https"].should_exit = True
            if std["http"]:
                std["http"].shutdown()
                std["http"].server_close()
            time.sleep(1)
            standard(now)

    _watch_network(app, move, name, port, clean, ip)
    log.info("band: phones open %s", app.state.band.share_url)
    return server


# ── who this host is, as phones list it ──

def _identity_path():
    return DATA_DIR / "band" / "host.json"


def _identity() -> dict:
    try:
        return json.loads(_identity_path().read_text())
    except (OSError, ValueError):
        return {}


def host_id() -> str:
    """A stable id, so a phone recognises this host after its name or address changed."""
    ident = _identity()
    if not ident.get("id"):
        ident["id"] = uuid.uuid4().hex[:12]
        _write(ident)
    return ident["id"]


def default_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no passwd entry in some containers
        user = ""
    machine = socket.gethostname().split(".")[0]
    return f"{user.capitalize()} · {machine}" if user and user.lower() != machine.lower() else machine


def display_name() -> str:
    return _identity().get("name") or default_name()


def set_display_name(name: str) -> str:
    ident = {**_identity(), "id": host_id()}
    ident["name"] = name.strip()[:40]
    _write(ident)
    return display_name()


def _write(ident: dict) -> None:
    path = _identity_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ident))
