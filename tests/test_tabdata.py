import pytest

from prismix import tabdata

TUNING = [64, 59, 55, 50, 45, 40]  # string 0 is the highest


def _part():
    def beat(s, f, d=(1, 4)):
        return {"duration": list(d), "notes": [{"string": s, "fret": f}]}

    return {"measures": [
        {"signature": [4, 4], "marker": {"text": "Intro"}, "voices": [{"beats": [beat(5, 0), beat(5, 3), {"duration": [1, 4], "rest": True, "notes": []}, beat(4, 0, (1, 8))]}]},
        {"voices": [{"beats": [{"duration": [1, 1], "rest": True, "notes": [{"rest": True}]}]}]},
    ]}


def test_compact_positions_and_rests():
    bars = tabdata.compact(_part())
    assert bars[0]["mark"] == "Intro" and bars[0]["sig"] == [4, 4]
    assert [n[0] for n in bars[0]["n"]] == [0.0, 0.25, 0.75]
    assert bars[1]["n"] == []


def test_pick_tracks_prefers_most_played():
    tracks = [
        {"instrument": "Distortion Guitar", "views": 5},
        {"instrument": "Acoustic Guitar", "views": 9},
        {"instrument": "Electric Bass", "views": 1},
        {"instrument": "Drums", "views": 99},
        {"instrument": "Voice", "views": 99, "isVocalTrack": True},
    ]
    assert tabdata.pick_tracks(tracks) == {"guitar": 1, "guitar2": 0, "bass": 2}


def test_pick_tracks_keeps_every_pitched_part():
    tracks = [
        {"instrument": "Electric Piano 1", "name": "Keys (RH)", "views": 4},
        {"instrument": "Recorder", "name": "Recorders", "views": 8},
        {"instrument": "Tenor Sax", "name": "Robert Plant | Lead Vocals", "views": 90},
        {"instrument": "Overdriven Guitar", "views": 7},
        {"instrument": "Organ", "views": 9},
        {"instrument": "Drums", "views": 99},
    ]
    assert tabdata.pick_tracks(tracks) == {"guitar": 3, "keys": 4, "keys2": 0, "other": 1}


def _structure(roots):
    beats = [i * 0.5 for i in range(4 * len(roots))]
    return {
        "beats": beats, "bars": [4 * i for i in range(len(roots))],
        "chords": [{"b": 4 * i, "d": 4, "r": r, "q": "maj"} for i, r in enumerate(roots)],
    }


def _tab(roots):
    # one root note per bar on the low strings
    def note(r):
        return {"len": 1.0, "n": [[0.0, 0.25, [[5, (r - 40) % 12]]], [0.5, 0.25, [[4, (r - 45) % 12 + 7]]]]}
    return {"bars": [note(r) for r in roots], "tuning": TUNING}


def test_fit_accepts_matching_progression():
    roots = [0, 5, 7, 0, 5, 7, 0, 5]
    got = tabdata.fit({"guitar": _tab(roots)}, _structure(roots))
    assert got and got["map"] == list(range(8))


def test_fit_rejects_other_song():
    got = tabdata.fit({"guitar": _tab([1, 6, 8, 1, 6, 8, 1, 6])}, _structure([0, 5, 7, 0, 5, 7, 0, 5]))
    assert got is None


def test_network_failure_is_not_cached(monkeypatch, tmp_path):
    from prismix import analysis, library
    monkeypatch.setattr(tabdata, "read", lambda v: None)
    monkeypatch.setattr(library, "read_info", lambda v: {"title": "T", "uploader": "A"})
    monkeypatch.setattr(analysis, "read_structure", lambda v: _structure([0, 5, 7, 0]))
    monkeypatch.setattr(tabdata, "candidates", lambda i: [{"songId": 1}])
    saved = []
    monkeypatch.setattr(tabdata, "_save", lambda v, d: saved.append(d))

    def boom(song):
        raise tabdata.TabDataError("offline")

    monkeypatch.setattr(tabdata, "_download", boom)
    assert tabdata.lookup("x") == {"found": False}
    assert saved == []


def test_slice_code_accepts_only_slice_links():
    assert tabdata.slice_code("https://www.soundslice.com/slices/PtNBc/?x=1") == "PtNBc"
    assert tabdata.slice_code("soundslice.com/slices/PtNBc") == "PtNBc"
    for bad in ("https://example.com/slices/PtNBc/", "https://www.soundslice.com/courses/1/", ""):
        with pytest.raises(tabdata.TabDataError):
            tabdata.slice_code(bad)


def test_slice_parts_converts_notes_and_skips_drums():
    data = {
        "tracks": [
            {"name": "\n Distortion Guitar\n", "midi": 30, "pitches": TUNING, "i": "guitar"},
            {"name": "Drums", "midi": 1000, "smp": 1, "pitches": [0] * 6},
        ],
        "rhythms": [{"id": 0, "val": 0.5, "dots": 1}, {"id": 1, "val": 0.25}],
        "bars": [
            {"time": [4, 4], "s": "Intro", "tracks": [
                {"voices": [[{"notes": [], "r": 0}, {"notes": [{"string": 5, "fret": 3}], "r": 1}]]},
                {"voices": [[{"notes": [{"string": 1, "fret": 42}], "r": 1}]]},
            ]},
        ],
    }
    parts = tabdata.slice_parts(data)
    assert list(parts) == ["guitar"] and parts["guitar"]["name"] == "Distortion Guitar"
    bar = parts["guitar"]["bars"][0]
    assert bar["mark"] == "Intro" and bar["n"] == [[0.75, 0.25, [[5, 3]]]]


def _cached(tmp_path, monkeypatch):
    from prismix import library
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    (tmp_path / "abcdefghijk").mkdir()
    base = {"found": True, "v": 2, "source": "songsterr", "map": [0], "tracks": {
        "guitar": {"kind": "guitar", "name": "G", "tuning": TUNING, "bars": [{"len": 1.0, "n": []}]},
        "keys": {"kind": "keys", "name": "K", "tuning": TUNING, "bars": [{"len": 1.0, "n": []}]},
    }}
    tabdata._save("abcdefghijk", base)
    return base


def test_edit_overlays_one_part_and_reverts(tmp_path, monkeypatch):
    _cached(tmp_path, monkeypatch)
    bars = [{"len": 1.0, "n": [[0.5, 0.25, [[5, 3]]], [0.0, 0.25, [[4, 2]]]]}]
    got = tabdata.save_edit("abcdefghijk", "keys", bars)
    assert got["tracks"]["keys"]["edited"] and got["tracks"]["keys"]["bars"][0]["n"][0][0] == 0.0
    assert "edited" not in got["tracks"]["guitar"]
    assert tabdata.read("abcdefghijk")["tracks"]["keys"]["name"] == "K"
    assert "edited" not in tabdata.revert_edit("abcdefghijk", "keys")["tracks"]["keys"]
    assert not (tmp_path / "abcdefghijk" / "tabedits.json").exists()


@pytest.mark.parametrize("bars", [
    [], [{"len": 1.0, "n": [[0.0, 0.25, [[6, 3]]]]}], [{"len": 1.0, "n": [[0.0, 0.25, [[0, 99]]]]}],
    [{"len": 0, "n": []}], [{"n": []}],
])
def test_edit_rejects_bad_tabs(tmp_path, monkeypatch, bars):
    _cached(tmp_path, monkeypatch)
    with pytest.raises(tabdata.TabDataError):
        tabdata.save_edit("abcdefghijk", "guitar", bars)


def test_edit_unknown_part(tmp_path, monkeypatch):
    _cached(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        tabdata.save_edit("abcdefghijk", "bass", [{"len": 1.0, "n": []}])


def test_old_cache_without_kinds_is_upgraded(tmp_path, monkeypatch):
    from prismix import library
    monkeypatch.setattr(library, "LIBRARY_DIR", tmp_path)
    (tmp_path / "abcdefghijk").mkdir()
    tabdata._save("abcdefghijk", {"found": True, "tracks": {"bass": {"name": "B", "tuning": [43], "bars": []}}})
    assert tabdata.read("abcdefghijk")["tracks"]["bass"]["kind"] == "bass"
