import argparse
import logging

import uvicorn

LOCAL_DOMAIN = "stems.localhost"


def main() -> None:
    parser = argparse.ArgumentParser(prog="prismix", description="Prismix server")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: local only)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.host in ("127.0.0.1", "localhost"):
        print(f"\n  Stems: http://{LOCAL_DOMAIN}:{args.port}\n")  # *.localhost resolves to loopback, no hosts entry
    uvicorn.run("prismix.server:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
