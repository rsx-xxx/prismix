"""Open links in Zen (https://zen-browser.app), falling back to the system default browser."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

_WINDOWS = ("Zen Browser/zen.exe", "Zen/zen.exe")
_MAC = ("/Applications/Zen.app", str(Path.home() / "Applications/Zen.app"))
_LINUX = ("zen-browser", "zen", "zen-bin")


def _windows_exe() -> str | None:
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        for rel in _WINDOWS:
            if base and (p := Path(base) / rel).is_file():
                return str(p)
    return shutil.which("zen")


def open_url(url: str) -> None:
    """Zen when installed, else whatever the system opens links with."""
    try:
        if sys.platform == "darwin" and any(Path(a).exists() for a in _MAC):
            subprocess.Popen(["open", "-a", next(a for a in _MAC if Path(a).exists()), url])
            return
        if sys.platform == "win32" and (exe := _windows_exe()):
            subprocess.Popen([exe, url])
            return
        if sys.platform.startswith("linux") and (exe := next(filter(None, map(shutil.which, _LINUX)), None)):
            subprocess.Popen([exe, url])
            return
    except OSError:
        pass
    webbrowser.open(url)
