from prismix import original


def test_mp4_selector_prefers_h264_and_caps_height():
    sel = original.video_selector("mp4", 720)
    assert sel.startswith("bv*[ext=mp4][vcodec^=avc1][height<=720]+ba[ext=m4a]")
    assert sel.endswith("/b[height<=720]")


def test_selector_without_cap_has_no_filter():
    assert "height" not in original.video_selector("mkv", None)
    assert original.video_selector("webm", None).startswith("bv*[ext=webm]+ba[ext=webm]")


def test_safe_name_strips_path_characters():
    assert original._safe('a/b:c*"d') == "a b c d"
    assert original._safe("///") == "track"
