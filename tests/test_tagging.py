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
