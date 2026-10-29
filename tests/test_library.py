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


def test_stamp_records_version_and_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    (tmp_path / VID).mkdir()
    (tmp_path / VID / "info.json").write_text(json.dumps({"title": "T"}))
    library.stamp(VID, "structure", 3)
    info = library.read_info(VID)
    assert info["title"] == "T" and info["stages"] == {"structure": 3} and info["prismix"]
    library.stamp("zzzzzzzzzzz", "structure", 3)  # unknown item: no-op


def test_tab_kinds_one_per_instrument(tmp_path, monkeypatch):
    import json

    from prismix import library

    monkeypatch.setattr(library, "video_dir", lambda _id: tmp_path)
    (tmp_path / "tabdata.json").write_text(json.dumps({"found": True, "tracks": {"guitar": {"kind": "guitar"}, "guitar2": {"kind": "guitar"}, "drums": {}, "bass": {"kind": "bass"}}}))
    assert library.tab_kinds("abcdefghijk") == ["guitar", "bass", "drums"]
