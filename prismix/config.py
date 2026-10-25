from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))  # running from the PyInstaller desktop build
ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent
WEB_DIR = Path(__file__).resolve().parent / "web"


def _user_data_dir() -> Path:
    """The bundle is read-only, so the desktop build keeps its library in the user's profile."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Prismix"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Prismix"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "prismix"


DATA_DIR = Path(os.environ.get("PRISMIX_DATA", _user_data_dir() if FROZEN else ROOT / "data")).resolve()
LIBRARY_DIR = DATA_DIR / "library"

# Hard cap so a pasted 10-hour stream does not lock the worker for hours.
MAX_DURATION_SEC = int(os.environ.get("PRISMIX_MAX_DURATION", 20 * 60))
SAMPLE_RATE = 44100
PEAK_BUCKETS = 1200


@dataclass(frozen=True)
class ModelSpec:
    id: str
    label: str
    hint: str
    stems: tuple[str, ...]
    legacy: bool = False  # still opens cached results, but is no longer offered for new splits


# Whitelist: the model id is used in file paths and passed to the loader.
MODELS: dict[str, ModelSpec] = {
    "htdemucs_ft": ModelSpec(
        "htdemucs_ft", "Best", "Highest quality, ~4x slower",
        ("vocals", "drums", "bass", "other"),
    ),
    "bs_roformer_sw": ModelSpec(
        "bs_roformer_sw", "Deep (6 stems)",
        "BS-RoFormer: far fewer artifacts on loud mixes, slower, ~700 MB download on first use",
        ("vocals", "drums", "bass", "guitar", "piano", "other"),
    ),
    "htdemucs_6s": ModelSpec(
        "htdemucs_6s", "6 stems (old)", "An older split kept for this track; Deep replaces it",
        ("vocals", "drums", "bass", "guitar", "piano", "other"), legacy=True,
    ),
    # no separation: the whole mix as one track, for bars and chords only
    "mix": ModelSpec("mix", "Chords only", "No separation, just bars, chords and key", ("mix",)),
}
DEFAULT_MODEL = "htdemucs_ft"
DEFAULT_CHANNEL = "@rsx-orp"  # whose public playlists the playlist dialog opens with
MIX_MODEL = "mix"


def cookies_file() -> Path | None:
    """Optional YouTube cookies (Netscape format) for age-gated / bot-checked videos."""
    explicit = os.environ.get("PRISMIX_COOKIES")
    candidates = [Path(explicit)] if explicit else [
        DATA_DIR / "cookies.txt",
        ROOT / "cookies.txt",
        ROOT / "www.youtube.com_cookies.txt",
    ]
    return next((p for p in candidates if p.is_file()), None)


def js_runtime() -> dict[str, dict]:
    """yt-dlp needs a JS runtime to solve YouTube's signature challenges."""
    bundled = ROOT / "bin" / ("deno.exe" if sys.platform == "win32" else "deno")
    if bundled.is_file():
        return {"deno": {"path": str(bundled)}}
    for name in ("deno", "node", "bun"):
        path = shutil.which(name)
        if path:
            return {name: {"path": path}}
    # nvm installs are not on PATH for non-interactive shells
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        for node in sorted(nvm.glob("*/bin/node"), reverse=True):
            return {"node": {"path": str(node)}}
    return {"deno": {}}


def ffmpeg_exe() -> str:
    """Bundled static ffmpeg: independent of whatever (possibly broken) system install exists."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"
