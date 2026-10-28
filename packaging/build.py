"""Build the desktop app with PyInstaller: dist/Prismix/ (Windows) or dist/Prismix.app (macOS).

    uv run --group desktop python packaging/build.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
SEP = os.pathsep

# Packages that load data files, native libraries or plugins PyInstaller cannot see by itself.
COLLECT_ALL = [
    "demucs", "beat_this", "librosa", "soundfile", "pylibrb", "imageio_ffmpeg", "yt_dlp", "yt_dlp_ejs",
    "transformers", "huggingface_hub", "einops", "rotary_embedding_torch", "beartype", "numba", "llvmlite",
    "lazy_loader", "webview", "uvicorn", "certifi", "zeroconf", "segno",
]
HIDDEN = ["uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto", "uvicorn.lifespan.on", "multipart"]
EXCLUDE = ["tkinter", "matplotlib", "IPython", "pytest", "torch.utils.tensorboard", "sklearn", "onnxruntime", "tensorflow", "pandas"]


def main() -> None:
    version = os.environ.get("PRISMIX_VERSION", "0.0.0").lstrip("v")
    args = [
        str(ROOT / "packaging" / "entry.py"),
        "--name", "Prismix", "--noconfirm", "--clean", "--windowed",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
        "--add-data", f"{ROOT / 'prismix' / 'web'}{SEP}prismix/web",
        "--paths", str(ROOT),
        "--osx-bundle-identifier", "com.rsx.prismix",
        "--copy-metadata", "prismix",  # __version__ is read from the package metadata
    ]
    for pkg in COLLECT_ALL:
        args += ["--collect-all", pkg]
    for mod in HIDDEN:
        args += ["--hidden-import", mod]
    for mod in EXCLUDE:
        args += ["--exclude-module", mod]
    deno = shutil.which("deno")
    if deno:  # yt-dlp needs a JS runtime to solve YouTube's challenges; users should not have to install one
        args += ["--add-binary", f"{deno}{SEP}bin"]
    else:
        print("warning: deno not found, YouTube downloads will need a system Node/Deno", file=sys.stderr)
    icon = ROOT / "packaging" / ("icon.icns" if sys.platform == "darwin" else "icon.ico")
    if icon.is_file():
        args += ["--icon", str(icon)]
    (ROOT / "build").mkdir(exist_ok=True)
    os.environ["PRISMIX_BUILD_VERSION"] = version
    PyInstaller.__main__.run(args)
    if sys.platform == "darwin":
        register_scheme(ROOT / "dist" / "Prismix.app")


def register_scheme(app: Path) -> None:
    """Let prismix://join links from the cloud open this app (PyInstaller's CLI cannot add URL types)."""
    import plistlib

    path = app / "Contents" / "Info.plist"
    info = plistlib.loads(path.read_bytes())
    info["CFBundleURLTypes"] = [{"CFBundleURLName": "com.rsx.prismix", "CFBundleURLSchemes": ["prismix"]}]
    path.write_bytes(plistlib.dumps(info))


if __name__ == "__main__":
    main()
