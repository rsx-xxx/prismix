"""What the desktop app still needs allowed by macOS, one entry per button: the microphone (tuner, following a drummer) and,
when macOS is still holding the app back as a download from the internet, the Privacy & Security page to press Open Anyway.
Nothing is listed once it is allowed, and on other systems the list is empty."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

PANES = {
    "mic": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "unlock": "x-apple.systempreferences:com.apple.preference.security?General",
}
AUDIO = "soun"  # AVMediaTypeAudio


def _mic() -> str:
    """'granted', 'ask' (macOS has not asked yet), 'denied' (only System Settings can change it) or 'unknown'."""
    try:
        from AVFoundation import AVCaptureDevice

        return {3: "granted", 0: "ask", 1: "denied", 2: "denied"}.get(int(AVCaptureDevice.authorizationStatusForMediaType_(AUDIO)), "unknown")
    except Exception:  # noqa: BLE001 - no PyObjC (a plain Python install): nothing to ask
        return "unknown"


def _bundle() -> Path | None:
    for p in Path(sys.executable).resolve().parents:
        if p.suffix == ".app":
            return p
    return None


def quarantined(app: Path | None = None) -> bool:
    """The app still carries Gatekeeper's mark and the user has not yet approved it (flag 0x40)."""
    app = app or _bundle()
    if not app:
        return False
    try:
        out = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(app)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0:
        return False
    try:
        return not int(out.stdout.split(";")[0], 16) & 0x40
    except ValueError:
        return False


def state() -> list[dict]:
    """[{id, label, why, how}] for what is still missing; `how` is 'ask' (a system dialog) or 'settings' (the right page opens)."""
    if sys.platform != "darwin":
        return []
    out = []
    mic = _mic()
    if mic in ("ask", "denied"):
        out.append({"id": "mic", "label": "Microphone", "why": "The tuner and following a live drummer", "how": "ask" if mic == "ask" else "settings"})
    if quarantined():
        out.append({"id": "unlock", "label": "Unlock Prismix", "why": "macOS is holding the app back: press Open Anyway under Privacy & Security", "how": "settings"})
    return out


def grant(key: str) -> bool:
    """The button for `key`: the system dialog when macOS will still ask, otherwise the matching System Settings page."""
    if sys.platform != "darwin" or key not in PANES:
        return False
    if key == "mic" and _mic() == "ask":
        try:
            from AVFoundation import AVCaptureDevice

            AVCaptureDevice.requestAccessForMediaType_completionHandler_(AUDIO, lambda ok: log.info("microphone %s", "allowed" if ok else "refused"))
            return True
        except Exception:  # noqa: BLE001 - fall through to the settings page
            log.info("could not ask for the microphone")
    subprocess.Popen(["open", PANES[key]])  # noqa: S603,S607 - a fixed URL for System Settings
    return True
