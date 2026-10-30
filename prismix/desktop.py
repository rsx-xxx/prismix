"""Desktop entry point: serve on a free local port and show the UI in a native window.

Falls back to the default browser when no native webview is available. Closing the window quits.
"""
from __future__ import annotations

import logging
import re
import socket
import sys
import threading
import time
import urllib.request

import uvicorn

from .config import DATA_DIR

TITLE = "Prismix"
SCHEME = "prismix://"
_window = None
_pending: list[str] = []
_handler = None  # the Apple event handler must outlive main()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait(url: str, timeout: float = 60) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("The Prismix server did not start")


def selftest() -> None:
    """Import everything the pipeline needs, so a broken bundle fails the build instead of a user's first split."""
    import importlib

    for name in ("torch", "demucs.pretrained", "demucs.apply", "beat_this.inference", "librosa", "pylibrb",
                 "transformers", "huggingface_hub", "yt_dlp", "imageio_ffmpeg", "soundfile", "einops",
                 "rotary_embedding_torch", "prismix.pipeline", "prismix.transcribe", "prismix.analysis", "prismix.export",
                 "muscriptor", "guitarpro", "mido", "prismix.autotab", "prismix.tabfiles"):  # auto-transcription and tab files
        importlib.import_module(name)
    import librosa
    import numpy as np
    from transformers import WhisperForConditionalGeneration, WhisperProcessor  # noqa: F401  (lyrics recognition)
    librosa.cqt(np.random.randn(22050).astype("float32"), sr=22050, n_bins=24)  # the chord model's feature path
    from .config import WEB_DIR, ffmpeg_exe, js_runtime

    assert (WEB_DIR / "index.html").is_file(), "web assets missing"
    assert ffmpeg_exe(), "ffmpeg missing"
    print("selftest ok; js runtime:", list(js_runtime()))


def song_hash(link: str) -> str | None:
    """prismix://song/<id>?m=<model>&t=<sec> becomes the page's own route, or None for any other link."""
    from urllib.parse import parse_qs, urlsplit

    u = urlsplit(link)
    if u.scheme != "prismix" or u.netloc != "song":
        return None
    vid = u.path.strip("/")
    q = parse_qs(u.query)
    model = q.get("m", ["htdemucs_ft"])[0]
    if not re.fullmatch(r"[\w-]{11}", vid) or not re.fullmatch(r"\w+", model):
        return None
    at = q.get("t", [""])[0]
    return f"#/v/{vid}/{model}" + (f"?t={at}" if re.fullmatch(r"\d+(\.\d+)?", at) else "")


def open_link(link: str) -> None:
    """A clicked prismix://join link: sign in to the cloud, then reload the page. Held until the window exists."""
    import json

    if _window is None:
        _pending.append(link)
        return
    from . import cloud

    song = song_hash(link)
    if song:
        _window.evaluate_js(f"location.hash = {json.dumps(song)}")
        return
    try:
        cloud.accept_link(link)
        _window.evaluate_js("location.reload()")
    except cloud.CloudError as e:
        _window.evaluate_js(f"alert({json.dumps(str(e))})")
    except Exception:
        logging.getLogger(__name__).exception("could not open the link")


def _watch_links() -> None:
    """macOS delivers a clicked prismix:// link to the running app as an Apple event; Windows starts the app with it as an argument."""
    for arg in sys.argv[1:]:
        if arg.startswith(SCHEME):
            _pending.append(arg)
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSAppleEventManager, NSObject

        def code(c: bytes) -> int:
            return int.from_bytes(c, "big")

        class LinkHandler(NSObject):
            def handleURL_withReply_(self, event, reply):  # noqa: N802 - Objective-C selector
                link = event.paramDescriptorForKeyword_(code(b"----")).stringValue()
                if link and link.startswith(SCHEME):
                    open_link(str(link))

        global _handler
        _handler = LinkHandler.alloc().init()
        NSAppleEventManager.sharedAppleEventManager().setEventHandler_andSelector_forEventClass_andEventID_(
            _handler, b"handleURL:withReply:", code(b"GURL"), code(b"GURL"))
    except Exception:
        logging.getLogger(__name__).exception("link handler unavailable")


def _window_ready(url: str = "") -> None:
    if url:
        from . import menubar

        menubar.install(url)
    while _pending:
        open_link(_pending.pop(0))


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        filename=DATA_DIR / "prismix.log")
    from .server import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info", log_config=None))
    threading.Thread(target=server.run, daemon=True).start()
    url = f"http://127.0.0.1:{port}"
    _wait(f"{url}/api/config")
    from . import lan

    lan_server = lan.start(app)
    _watch_links()
    try:
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        global _window
        from .nowplaying import NowPlaying

        api = NowPlaying()
        _window = webview.create_window(TITLE, url, width=1280, height=840, min_size=(900, 600), js_api=api)
        api.window = _window
        webview.start(_window_ready, url)
    except Exception:
        logging.getLogger(__name__).exception("native window unavailable, opening the browser")
        from .browser import open_url

        open_url(url)
        try:
            while server.started and not server.should_exit:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    server.should_exit = True
    if lan_server:
        lan_server.should_exit = True


if __name__ == "__main__":
    main()
