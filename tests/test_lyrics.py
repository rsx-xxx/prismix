from prismix import lyrics


def test_parse_lrc_handles_multiple_stamps_and_fractions():
    out = lyrics.parse_lrc("[ar:x]\n[00:01.5] one\n[01:02.25][00:30.00]two\n[00:40.00]")
    assert [(round(x["t"], 2), x["text"]) for x in out] == [(1.5, "one"), (30.0, "two"), (40.0, ""), (62.25, "two")]


def test_guess_strips_karaoke_and_uses_channel():
    info = {"title": "Салют, Вера (Караоке)", "channel": "Берегите себя и лес"}
    assert lyrics.guess(info) == ("Берегите себя и лес", "Салют, Вера")


def test_guess_splits_artist_title_and_uses_match_for_untagged_uploads():
    assert lyrics.guess({"title": "Noize MC — Жвачка (Official Music Video)", "channel": "Noize MC"}) == ("Noize MC", "Жвачка")
    info = {"title": "x", "channel": "Uploaded file", "match": {"title": "Artist - Song (Official Video)", "channel": "c"}}
    assert lyrics.guess(info) == ("Artist", "Song")


def test_pick_prefers_synced_and_close_duration():
    hits = [
        {"trackName": "Салют, вера", "duration": 192, "plainLyrics": "a"},
        {"trackName": "Салют, вера", "duration": 193, "syncedLyrics": "[00:01.00] a"},
        {"trackName": "Салют, вера", "duration": 400, "syncedLyrics": "[00:01.00] a"},
        {"trackName": "Other song", "duration": 192, "syncedLyrics": "[00:01.00] a"},
    ]
    assert lyrics.pick("Салют, Вера", 192, hits) is hits[1]
    assert lyrics.pick("Nothing like it", 192, hits) is None
