"""Sign in to Hugging Face from the app, as `hf auth login` does: a code is confirmed in the browser, no token to copy.

The login is stored where huggingface_hub keeps it, so the model download (and the `hf` CLI) pick it up. The device-code
helpers are huggingface_hub internals; if a release moves them the app falls back to telling the user to run `hf auth login`.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)
_lock = threading.Lock()
_state: dict = {"status": "idle"}  # idle | waiting (url, code) | done | error (message)


class HFLoginError(RuntimeError):
    """Message is safe to show to the user."""


def logged_in() -> bool:
    from huggingface_hub import get_token

    return bool(get_token())


def state() -> dict:
    with _lock:
        out = dict(_state)
    if out["status"] == "idle" and logged_in():
        out["status"] = "done"
    return out


def _wait(info: dict) -> None:
    from huggingface_hub._login import _save_oauth_token
    from huggingface_hub.utils._oauth_device import poll_device_token

    try:
        _save_oauth_token(poll_device_token(info))
        result = {"status": "done"}
    except Exception as e:  # noqa: BLE001 - denied, expired or offline: all end the wait with a message
        log.info("Hugging Face login failed: %s", e)
        result = {"status": "error", "message": "The sign-in was not completed. Try again."}
    with _lock:
        _state.clear()
        _state.update(result)


def start() -> dict:
    """Ask for a code; the user confirms it at the returned url while a thread waits for the answer."""
    try:
        from huggingface_hub.utils._oauth_device import request_device_code
    except ImportError as e:
        raise HFLoginError("Sign in from a terminal instead: run `hf auth login`") from e
    with _lock:
        if _state["status"] == "waiting":
            return dict(_state)
    try:
        info = request_device_code()
    except Exception as e:  # noqa: BLE001
        raise HFLoginError("Could not reach Hugging Face") from e
    with _lock:
        _state.clear()
        _state.update({"status": "waiting", "url": info["verification_uri_complete"], "code": info["user_code"]})
        out = dict(_state)
    threading.Thread(target=_wait, args=(info,), daemon=True).start()
    return out
