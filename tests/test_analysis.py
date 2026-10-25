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
