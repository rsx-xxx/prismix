import json

import pytest

from prismix import library
from prismix.library import parse_video_id

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize("text", [
    f"https://www.youtube.com/watch?v={VID}&t=5",
    f"https://youtu.be/{VID}?si=x",
    f"https://m.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"music.youtube.com/watch?v={VID}",
    VID,
])
def test_parse_ok(text):
    assert parse_video_id(text) == VID


@pytest.mark.parametrize("text", [
    "", "hello", "https://evil.com/watch?v=" + VID, "https://youtube.com.evil.com/watch?v=" + VID,
    "https://www.youtube.com/watch?v=short",
])
def test_parse_rejects(text):
    assert parse_video_id(text) is None


def test_path_traversal_rejected():
    with pytest.raises(ValueError):
        library.video_dir("../etc")
    with pytest.raises(ValueError):
        library.model_dir(VID, "../../x")


def test_meta_requires_all_stem_files(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    d = tmp_path / VID / "htdemucs_6s"
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"stems": [{"file": "vocals.flac"}]}))
    assert library.read_meta(VID, "htdemucs_6s") is None
    (d / "vocals.flac").write_bytes(b"x")
    assert library.read_meta(VID, "htdemucs_6s")["stems"]
