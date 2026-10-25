"""Download the original track, untouched by stems, tempo or key: its audio in any format, or the video.

Audio comes from the stream already kept in the library (converted on request); video is fetched from YouTube
in the container and height asked for. Uploaded files are served from their stored source.
"""
from __future__ import annotations

import mimetypes
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import library
from .config import ffmpeg_exe
from .export import FORMATS, ExportError
from .pipeline import _ydl_opts

AS_IS = "original"
CONTAINERS = {"mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska"}
HEIGHTS = (2160, 1440, 1080, 720, 480, 360)


@dataclass
class Result:
    path: Path
    name: str
    mime: str
    tmp: Path | None  # temp dir to delete once the file is sent

    def cleanup(self) -> None:
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)


def _safe(title: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", " ", title).strip()[:120] or "track"


def video_selector(container: str, height: int | None) -> str:
    """yt-dlp format string that avoids re-encoding: codecs that fit the container, capped at `height`."""
    cap = f"[height<={height}]" if height else ""
    if container == "mp4":
        return (f"bv*[ext=mp4][vcodec^=avc1]{cap}+ba[ext=m4a]/b[ext=mp4]{cap}/bv*{cap}+ba/b{cap}")
    if container == "webm":
        return f"bv*[ext=webm]{cap}+ba[ext=webm]/bv*{cap}+ba/b{cap}"
    return f"bv*{cap}+ba/b{cap}"


def audio(video_id: str, fmt: str) -> Result:
    info = library.read_info(video_id)
    src = library.find_source(video_id)
    if not info or not src:
        raise ExportError("Original audio is not in the library")
    base = _safe(info.get("title") or video_id)
    if fmt == AS_IS:
        mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        return Result(src, f"{base}{src.suffix}", mime, None)
    if fmt not in FORMATS:
        raise ExportError("Unknown format")
    ext, mime, codec = FORMATS[fmt]
    tmp = Path(tempfile.mkdtemp(prefix=".orig-"))
    out = tmp / f"audio.{ext}"
    proc = subprocess.run(
        [ffmpeg_exe(), "-v", "error", "-nostdin", "-i", str(src), "-vn", "-map_metadata", "-1", *codec, str(out)],
        capture_output=True, check=False,
    )
    if proc.returncode or not out.is_file():
        shutil.rmtree(tmp, ignore_errors=True)
        raise ExportError("Could not convert the audio")
    return Result(out, f"{base}.{ext}", mime, tmp)


def video(video_id: str, container: str, height: int | None) -> Result:
    import yt_dlp

    info = library.read_info(video_id)
    if not info:
        raise ExportError("Unknown track")
    base = _safe(info.get("title") or video_id)
    if info.get("upload"):  # the file the user added is the original
        src = library.find_source(video_id)
        if not info.get("hasVideo") or not src:
            raise ExportError("This file has no video")
        return Result(src, f"{base}{src.suffix}", mimetypes.guess_type(src.name)[0] or "video/mp4", None)
    if container not in CONTAINERS:
        raise ExportError("Unknown format")
    if height is not None and height not in HEIGHTS:
        raise ExportError("Unknown quality")
    tmp = Path(tempfile.mkdtemp(prefix=".orig-"))
    opts = _ydl_opts(
        format=video_selector(container, height), merge_output_format=container,
        outtmpl=str(tmp / "video.%(ext)s"),
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    except yt_dlp.utils.DownloadError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        msg = str(e).replace("ERROR: ", "").splitlines()[0][:300]
        raise ExportError(f"YouTube download failed: {msg}") from e
    out = next(iter(sorted(tmp.glob("video.*"))), None)
    if not out:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ExportError("Download finished but no video file was produced")
    return Result(out, f"{base}{out.suffix}", CONTAINERS.get(out.suffix.lstrip("."), "video/mp4"), tmp)
