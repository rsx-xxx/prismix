from prismix import tagging


def test_split_artist_and_title():
    assert tagging._split("Daft Punk - Get Lucky (Official Audio)", "VEVO") == ("Daft Punk", "Get Lucky")
    assert tagging._split("Get Lucky [Official Video]", "Daft Punk - Topic") == ("Daft Punk", "Get Lucky")


def test_offline_falls_back_to_youtube_tags(tmp_path, monkeypatch):
    from prismix import library

    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    d = tmp_path / "abcdefghijk"
    d.mkdir()
    (d / "info.json").write_text('{"title": "Song", "channel": "Band"}')
    tags, cover = tagging.for_track("abcdefghijk")
    assert tags["title"] == "Song" and tags["artist"] == "Band" and cover is None


def test_fallback_tags_drop_youtube_noise(tmp_path, monkeypatch):
    from prismix import library

    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(tagging, "ONLINE", False)
    d = tmp_path / "abcdefghijk"
    d.mkdir()
    (d / "info.json").write_text('{"title": "Get Lucky (Official Audio)", "channel": "Daft Punk - Topic"}')
    tags, _ = tagging.for_track("abcdefghijk")
    assert tags["title"] == "Get Lucky" and tags["artist"] == "Daft Punk"
    assert tagging.file_stem(tags, "x") == "Daft Punk - Get Lucky"


def test_picture_tag_is_a_flac_picture_block(tmp_path):
    import base64

    jpg = tmp_path / "c.jpg"
    jpg.write_bytes(b"\xff\xd8\xff" + b"0" * 20)
    args = tagging.picture_tag(jpg)
    block = base64.b64decode(args[1].split("=", 1)[1])
    assert args[0] == "-metadata" and block[:4] == b"\x00\x00\x00\x03" and block.endswith(jpg.read_bytes())
