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


def _cands(**info):
    return list(lyrics.candidates(info))


def test_cover_detection():
    assert lyrics.is_cover({"title": "Numb (Linkin Park cover)"})
    assert lyrics.is_cover({"title": "Группа крови - кавер"})
    assert lyrics.is_cover({"title": "x", "tags": ["guitar cover"]})
    assert lyrics.is_cover({"title": "x", "description": "Cover of a song"})
    assert not lyrics.is_cover({"title": "Linkin Park - Numb"})


def test_cover_artist_from_description():
    got = _cands(title="Numb | acoustic cover", uploader="Some Guy",
                 description="Cover of the song by Linkin Park - Numb\nsubscribe https://x.y")
    assert ("Linkin Park", "Numb") in got


def test_cover_artist_from_russian_description():
    got = _cands(title="Группа крови (кавер)", uploader="Вася",
                 description="Кавер на песню Кино - Группа крови")
    assert ("Кино", "Группа крови") in got


def test_cover_title_alone_fallback():
    got = _cands(title="Hello (Adele cover)", uploader="Someone")
    assert (None, "Hello") in got
    assert ("Adele", "Hello") in got


def test_candidates_bounded_and_unique():
    got = lyrics.candidates({"title": "A - B (cover)", "description": "original by A - B\n" * 30, "tags": ["t1", "t2"] * 10})
    assert len(got) <= lyrics.MAX_CANDIDATES
    assert len(got) == len(set(got))


def test_heard_match_scores_shared_words():
    hit = {"plainLyrics": "Hello darkness my old friend\nI've come to talk with you again"}
    heard = ["hello", "darkness", "my", "old", "friend", "ive", "come", "to", "talk", "with", "you", "again"]
    assert lyrics.heard_match(heard, hit) > 0.8
    assert lyrics.heard_match(["completely", "different", "words", "about", "something", "else"], hit) < 0.2


def test_find_original_picks_best_matching_lyrics(monkeypatch):
    right = {"id": 1, "trackName": "Sound of Silence", "artistName": "Simon", "plainLyrics": "hello darkness my old friend i have come to talk with you again"}
    wrong = {"id": 2, "trackName": "Sound of Silence", "artistName": "Other", "plainLyrics": "dancing in the moonlight every single night forever"}
    monkeypatch.setattr(lyrics, "_originals", lambda client, info: [wrong, right])
    heard = [{"t": 0, "text": "hello darkness my old friend"}, {"t": 5, "text": "ive come to talk with you again"}]
    hit, score = lyrics.find_original({"title": "Sound of Silence (cover)"}, heard)
    assert hit["id"] == 1 and score > 0.8
    assert lyrics.find_original({"title": "x"}, [{"t": 0, "text": "too short"}]) is None


def test_unofficial_tag_is_not_part_of_the_title():
    info = {"title": "Noize MC - Жвачка (неофициальный).", "channel": "AUTHOR", "duration": 184}
    assert lyrics.guess(info) == ("Noize MC", "Жвачка")
    assert ("Noize MC", "Жвачка") in lyrics.candidates(info)


def test_dash_separated_title_tries_each_part_alone():
    found = _cands(title="Noize MC Lyapis Trubetskoy Capital - и Ляпис Трубецкой - Капитал", channel="Dario J")
    assert (None, "Капитал") in found


def test_looped_whisper_line_is_detected():
    from prismix import transcribe

    assert transcribe._looped("город город город город город город город")
    assert not transcribe._looped("мы идём по городу вместе с тобой")
