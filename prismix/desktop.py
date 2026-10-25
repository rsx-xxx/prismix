"""Desktop entry point: serve on a free local port and show the UI in a native window.

Falls back to the default browser when no native webview is available. Closing the window quits.
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import urllib.request

import uvicorn

from .config import DATA_DIR

TITLE = "Prismix"


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
                 "rotary_embedding_torch", "prismix.pipeline", "prismix.transcribe", "prismix.analysis", "prismix.export"):
        importlib.import_module(name)
    import librosa
    import numpy as np
    from transformers import WhisperForConditionalGeneration, WhisperProcessor  # noqa: F401  (lyrics recognition)
    librosa.cqt(np.random.randn(22050).astype("float32"), sr=22050, n_bins=24)  # the chord model's feature path
    from .config import WEB_DIR, ffmpeg_exe, js_runtime

    assert (WEB_DIR / "index.html").is_file(), "web assets missing"
    assert ffmpeg_exe(), "ffmpeg missing"
    print("selftest ok; js runtime:", list(js_runtime()))


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
    try:
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.create_window(TITLE, url, width=1280, height=840, min_size=(900, 600))
        webview.start()
    except Exception:
        logging.getLogger(__name__).exception("native window unavailable, opening the browser")
        import webbrowser

        webbrowser.open(url)
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
