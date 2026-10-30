from prismix import autotab, tabdata

STRUCT = {"beats": [i * 0.5 for i in range(8)], "bars": [0, 4]}  # two 4/4 bars at 120 bpm, a beat is 0.5 s


def test_notes_land_on_the_bar_grid():
    bars = autotab.to_bars([(40, 0.0, 0.5), (45, 1.0, 1.5), (52, 2.0, 3.0)], STRUCT, "bass")
    assert [b["len"] for b in bars] == [1.0, 1.0]
    assert [n[0] for n in bars[0]["n"]] == [0.0, 0.5]
    assert bars[0]["n"][0][1] == 0.25
    assert [n[0] for n in bars[1]["n"]] == [0.0]


def test_a_chord_shares_one_beat_and_each_pitch_its_own_string():
    bars = autotab.to_bars([(64, 0.0, 1.0), (59, 0.0, 1.0), (55, 0.0, 1.0)], STRUCT, "guitar")
    (beat,) = bars[0]["n"]
    strings = [s for s, _ in beat[2]]
    assert len(set(strings)) == 3
    assert sorted(autotab.TUNINGS["guitar"][s] + f for s, f in beat[2]) == [55, 59, 64]


def test_blips_and_out_of_range_notes():
    assert not any(b["n"] for b in autotab.to_bars([(60, 0.0, 0.01)], STRUCT, "keys"))
    bars = autotab.to_bars([(100, 0.0, 0.5)], STRUCT, "bass")  # far above a bass: folded down an octave at a time
    (s, f), = bars[0]["n"][0][2]
    assert 28 <= autotab.TUNINGS["bass"][s] + f <= 71


def test_keys_cover_the_whole_keyboard_within_the_editor_fret_limit():
    for pitch in (21, 60, 108):
        (s, f), = autotab._strings("keys", [pitch])
        assert autotab.TUNINGS["keys"][s] + f == pitch and 0 <= f <= 36


def test_auto_parts_follow_the_tabs_bar_map(tmp_path, monkeypatch):
    monkeypatch.setattr(tabdata.library, "video_dir", lambda v: tmp_path)
    tab = {"found": True, "v": tabdata.FORMAT, "source": "songsterr", "map": [1, None, 0],
           "tracks": {"guitar": {"kind": "guitar", "name": "G", "tuning": [64], "bars": [{"len": 1.0, "n": []}] * 3}}}
    tabdata._save("v", tab)
    rec = [{"len": 1.0, "n": [[0.0, 0.25, [[0, 1]]]]}, {"len": 1.0, "n": []}]
    got = tabdata.add_auto("v", {"keys": autotab.part("keys", "Keys", rec)}, 2)
    keys = got["tracks"]["keys"]["bars"]
    assert [bool(b["n"]) for b in keys] == [False, False, True] and got["tracks"]["keys"]["auto"]
