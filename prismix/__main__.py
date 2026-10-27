import argparse
import logging
import socket
import threading
import time

import uvicorn

LOCAL_DOMAIN = "stems.localhost"


def _open_when_up(url: str, host: str, port: int) -> None:
    """Open the browser once the server accepts connections (it takes a few seconds to import torch)."""
    for _ in range(120):
        try:
            socket.create_connection((host, port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.5)
    else:
        return
    from .browser import open_url

    open_url(url)


def main() -> None:
    parser = argparse.ArgumentParser(prog="prismix", description="Prismix server")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: local only)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--share", action=argparse.BooleanOptionalAction, default=True,
                        help="also serve phones and other desktops on the LAN over HTTPS (band mode); --no-share turns it off")
    parser.add_argument("--open", action="store_true", help="open the page in the browser once the server is up")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.host in ("127.0.0.1", "localhost"):
        print(f"\n  Stems: http://{LOCAL_DOMAIN}:{args.port}\n")  # *.localhost resolves to loopback, no hosts entry
    if args.open:
        local = args.host in ("127.0.0.1", "localhost", "0.0.0.0")
        url = f"http://{LOCAL_DOMAIN if local else args.host}:{args.port}"
        threading.Thread(target=_open_when_up, args=(url, "127.0.0.1" if local else args.host, args.port), daemon=True).start()
    if args.share:
        from . import lan
        from .server import app

        if lan.start(app):
            print(f"\n  Phones: {app.state.band.share_url}\n")
    uvicorn.run("prismix.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
