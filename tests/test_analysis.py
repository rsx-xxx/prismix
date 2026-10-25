import numpy as np

from prismix import analysis
from prismix.analysis import BeatMap


def test_parse_chord():
    assert analysis.parse_chord("C#:min7") == (1, "min7")
    assert analysis.parse_chord("Bb") == (10, "maj")
    assert analysis.parse_chord("N") is None


def test_beatmap_extrapolates():
    m = BeatMap(np.array([1.0, 1.5, 2.0]))
    assert m.to_beat(1.25) == 0.5
    assert m.to_beat(0.5) == -1.0
    assert m.to_beat(2.5) == 3.0


def test_chords_snap_and_drop_flicker():
    m = BeatMap(np.arange(8) * 0.5)
    tl = [
        {"start": 0.0, "end": 1.4, "chord": "C"},
        {"start": 1.4, "end": 1.9, "chord": "F"},  # one beat sandwiched between C
        {"start": 1.9, "end": 2.9, "chord": "C"},
        {"start": 2.9, "end": 4.0, "chord": "G"},
    ]
    out = analysis.chords_on_beats(tl, m, 8)
    assert [(c["b"], c["r"]) for c in out] == [(0, 0), (6, 7)]
    assert out[0]["d"] == 6


def test_detect_key_major():
    chords = [{"r": r, "q": q, "b": i, "d": 2} for i, (r, q) in enumerate([(2, "maj"), (7, "maj"), (9, "min"), (2, "maj"), (4, "min")])]
    assert analysis.detect_key(chords)["tonic"] == 2


def test_bars_regularised():
    beats = np.arange(16) * 0.5
    bars, num = analysis._bars(16, beats, np.array([0.0, 2.0, 4.0, 6.0]))
    assert num == 4 and bars == [0, 4, 8, 12]
    bars, num = analysis._bars(16, beats, np.array([1.0, 3.0, 5.0, 7.0]))
    assert bars[:3] == [0, 2, 6] and num == 4  # pickup bar before the first downbeat


def test_transpose_perm_rotates_roots_and_keeps_no_chord():
    perm = analysis._transpose_perm(1)
    assert perm[0 * 14 + 1] == 1 * 14 + 1  # C maj seen a semitone up reads as C# maj in the shifted copy
    assert perm[11 * 14 + 0] == 0 * 14 + 0  # B min wraps to C min
    assert perm[168] == 168 and perm[169] == 169
    assert sorted(perm) == list(range(170))


def test_viterbi_ignores_one_beat_flicker_but_follows_real_changes():
    probs = np.full((16, 170), 0.001)
    path = [5] * 8 + [9] * 8
    for i, c in enumerate(path):
        probs[i, c] = 0.9
    probs[3, 5], probs[3, 40] = 0.3, 0.6  # one beat of doubt, off the beat
    assert analysis.viterbi_beats(probs, {0, 8}) == path


def test_shift_bins_fills_with_floor():
    f = np.arange(12, dtype=float).reshape(6, 2)
    up = analysis._shift_bins(f, 2)
    assert (up[2:] == f[:-2]).all() and (up[:2] == f.min()).all()
    assert (analysis._shift_bins(f, -2)[:-2] == f[2:]).all()
