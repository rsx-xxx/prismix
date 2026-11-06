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


def test_drum_hits_become_kit_lanes():
    bars = autotab.to_bars([(36, 0.0, 0.1), (42, 0.0, 0.1), (38, 0.5, 0.6), (48, 1.0, 1.1)], STRUCT, "drums")
    first, second, third = bars[0]["n"]
    assert sorted(m for _, m in first[2]) == [36, 42] and second[2] == [[1.5, 38]]
    assert third[2] == [[0.5, 50]]  # the lowest toms share a lane with the next one up


def test_fingering_keeps_one_position_and_follows_the_hint():
    from prismix import autotab

    # without a hint the line stays low on the neck
    held = autotab.finger("guitar", [[64], [67], [69], [71], [74]], [None] * 5)
    assert max(f for chord in held for _, f in chord) <= 10
    # the same notes played around the 12th fret when a published tab holds the hand there
    high = autotab.finger("guitar", [[64], [67], [69]], [12.0] * 3)
    assert min(f for chord in high for _, f in chord) >= 5


def test_fingering_never_stacks_two_notes_on_a_string():
    from prismix import autotab

    (chord,) = autotab.finger("guitar", [[40, 47, 52, 56, 59, 64]], [None])
    assert len({s for s, _ in chord}) == len(chord) == 6


def test_agreement_tells_a_matching_bar_from_a_different_one():
    from prismix import autotab

    tab = {(0, 0), (2, 4), (4, 7)}
    assert autotab.agreement(tab, {(0, 0), (3, 4), (4, 7)}) == 1.0  # a note off by one eighth still counts
    assert autotab.agreement(tab, {(0, 2), (2, 9)}) == 0.0
    assert autotab.agreement(tab, set()) == 0.0 and autotab.agreement(set(), set()) is None


def test_check_flags_bars_the_recording_plays_differently(monkeypatch, tmp_path):
    from prismix import analysis, autotab, tabdata

    structure = {"beats": [float(i) / 2 for i in range(17)], "bars": [0, 4, 8, 12]}  # four bars of 4 beats at 120 bpm
    store = {"tab": {
        "found": True, "source": "songsterr", "url": "u", "map": [0, 1, 2, 3],
        "tracks": {"guitar": {"kind": "guitar", "name": "G", "tuning": autotab.TUNINGS["guitar"], "bars": [
            {"len": 1.0, "n": [[0.0, 0.25, [[0, 0]]]]},  # E4 on beat 1 of every bar
            {"len": 1.0, "n": [[0.0, 0.25, [[0, 0]]]]},
            {"len": 1.0, "n": [[0.0, 0.25, [[0, 0]]]]},
            {"len": 1.0, "n": [[0.0, 0.25, [[0, 0]]]]},
        ]}},
    }}
    monkeypatch.setattr(analysis, "read_structure", lambda v: structure)
    monkeypatch.setattr(tabdata, "read", lambda v: store["tab"])
    monkeypatch.setattr(tabdata, "_raw", lambda v: store["tab"])
    monkeypatch.setattr(tabdata, "_save", lambda v, d: store.__setitem__("tab", d))
    monkeypatch.setattr(autotab, "stem_file", lambda v, s: "x" if s == "guitar" else None)
    # the stem plays E4 in bars 0, 1 and 3 but a G4 in bar 2 (a mashup went its own way there)
    notes = [(64, 0.0, 0.4), (64, 2.0, 2.4), (67, 4.0, 4.4), (64, 6.0, 6.4)]
    monkeypatch.setattr(autotab, "transcribe_stem", lambda path, size, drums=False: notes)
    tab = autotab.check("v", "small")
    assert [b.get("weak", 0) for b in tab["tracks"]["guitar"]["bars"]] == [0, 0, 1, 0]
    fix = tab["tracks"]["guitar2"]
    assert fix["auto"] and [bool(b["n"]) for b in fix["bars"]] == [False, False, True, False]
    assert autotab.check("v", "small")["tracks"].keys() == {"guitar", "guitar2"}  # a second check replaces, never piles up
