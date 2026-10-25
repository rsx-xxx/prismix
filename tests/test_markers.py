import numpy as np

from prismix import markers


def test_leading_chain_follows_adjacent_segments():
    segs = [{"s": 0.5, "e": 20, "cat": "intro"}, {"s": 21, "e": 40, "cat": "sponsor"}, {"s": 200, "e": 220, "cat": "sponsor"}]
    assert markers.leading_end(segs) == 40
    assert markers.leading_end([{"s": 90, "e": 100, "cat": "sponsor"}]) == 0


def test_onset_skips_quiet_intro():
    power = np.concatenate([np.full(80, 1e-9), np.full(400, 0.01)])  # 20 s of near-silence, then music
    assert abs(markers.onset(power) - 20.0) <= 0.5


def test_onset_of_silence_is_zero():
    assert markers.onset(np.zeros(100)) == 0.0
