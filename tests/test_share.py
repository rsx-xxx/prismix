from prismix.desktop import song_hash


def test_song_link_becomes_route():
    assert song_hash("prismix://song/dQw4w9WgXcQ?m=htdemucs_ft&t=42") == "#/v/dQw4w9WgXcQ/htdemucs_ft?t=42"
    assert song_hash("prismix://song/dQw4w9WgXcQ") == "#/v/dQw4w9WgXcQ/htdemucs_ft"


def test_bad_links_are_ignored():
    assert song_hash("prismix://join?o=x&t=y") is None
    assert song_hash("prismix://song/short") is None
    assert song_hash("prismix://song/dQw4w9WgXcQ?m=../x") is None
    assert song_hash("prismix://song/dQw4w9WgXcQ?t=1;alert(1)") == "#/v/dQw4w9WgXcQ/htdemucs_ft"
