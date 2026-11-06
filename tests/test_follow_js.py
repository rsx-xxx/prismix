import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "prismix" / "web"
NODE = shutil.which("node")

# A simulated drummer: eighth-note kick/snare/hat with 7 ms of human jitter, ghost fills, and a tempo that speeds up then slows.
# The "player" is a stem clock that obeys the follower's rate; we measure how far it sits from the drummer's beat.
SIM = """
import { beatGrid, Follower } from %r;

let seed = 7;
const rnd = () => (seed = (seed * 1664525 + 1013904223) %% 4294967296) / 4294967296;
const gauss = () => Math.sqrt(-2 * Math.log(rnd() + 1e-9)) * Math.cos(2 * Math.PI * rnd());

const BPM = 120;
const beats = Array.from({ length: 800 }, (_, i) => 1 + (i * 60) / BPM);
const grid = beatGrid(beats);

function run({ tempoAt, jitter = 0.007, startOffset = 0, fills = true, dur = 150 }) {
  let pos = beats[0] + startOffset; // stems heard position (song seconds)
  const f = new Follower(grid);
  f.reset(tempoAt(0)); // a count-in tells it the starting tempo
  let rate = f.rate;
  let drumBeat = 0; // drummer's position in beats, advancing with his own tempo
  let t = 0;
  const errs = [];
  const dt = 0.001;
  let hitBeat = 0;
  while (t < dur) {
    const tempo = tempoAt(t); // drummer's speed relative to the recording
    drumBeat += (dt * tempo * BPM) / 60;
    pos += dt * rate;
    t += dt;
    if (drumBeat >= hitBeat) {
      const fill = fills && rnd() < 0.12;
      // when he actually strikes: human jitter, and a fill note lands anywhere between the grid points
      const early = gauss() * jitter + (fill ? ((rnd() - 0.5) * 0.4 * 60) / (BPM * tempo) : 0);
      f.hit(pos + early * rate, 1);
      rate = f.rate;
      const aheadBeats = ((pos - beats[0]) * BPM) / 60 - hitBeat; // how far the stems are past his beat
      errs.push({ t, ms: ((aheadBeats * 60) / BPM) * 1000, rate });
      hitBeat += 0.5;
    }
  }
  return errs;
}

const steady = run({ tempoAt: () => 1.12 });
const ramp = run({ tempoAt: (t) => 1 + 0.15 * Math.sin((t / 150) * Math.PI) });
const late = run({ tempoAt: () => 0.9, startOffset: 0.08 });
const tail = (a, from) => a.filter((x) => x.t > from);
const rms = (a) => Math.sqrt(a.reduce((s, x) => s + x.ms * x.ms, 0) / a.length);
const maxStep = (a) => a.slice(1).reduce((m, x, i) => Math.max(m, Math.abs(x.rate - a[i].rate)), 0);
console.log(JSON.stringify({
  steady: { rms: rms(tail(steady, 30)), rate: tail(steady, 30).at(-1).rate },
  ramp: { rms: rms(tail(ramp, 20)), step: maxStep(ramp) },
  late: { rms: rms(tail(late, 40)), rate: tail(late, 40).at(-1).rate },
}));
"""

MISC = """
import { beatGrid, Follower, countIn, noteWeight } from %r;
const beats = Array.from({ length: 100 }, (_, i) => i * 0.5);
const grid = beatGrid(beats);
// locked on a steady drummer, then a burst of fills: tempo must not be dragged away
const f = new Follower(grid);
for (let i = 0; i < 40; i++) f.hit(i * 0.25, 1);
const locked = f.locked;
let s = 3; const r = () => (s = (s * 16807) %% 2147483647) / 2147483647;
for (let i = 0; i < 12; i++) f.hit(10 + i * 0.25 + 0.06 + r() * 0.03, 1); // 6 ms..: inside the net, lands on the grid
const before = f.tempo;
for (let i = 0; i < 6; i++) f.hit(12 + i * 0.31, 1); // scattered fill notes
console.log(JSON.stringify({
  locked, drift: Math.abs(f.tempo - before),
  count: countIn([1, 1.5, 2.0, 2.5]),
  uneven: countIn([1, 1.5, 2.2, 2.5]),
  slow: countIn([1, 3, 5, 7]),
  kick: noteWeight(36, 100), tom: noteWeight(45, 100), ghost: noteWeight(38, 10),
  grid: [grid.toBeat(1.25), grid.toSec(5.5), grid.toBeat(-0.5), grid.toBeat(60)],
}));
"""


def _run(script, tmp_path):
    f = tmp_path / "t.mjs"
    f.write_text(script % (WEB / "follow.js").as_uri())
    return json.loads(subprocess.run([NODE, f], capture_output=True, text=True, check=True).stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_follower_locks_onto_a_drummer_who_speeds_up_and_slows_down(tmp_path):
    got = _run(SIM, tmp_path)
    assert abs(got["steady"]["rate"] - 1.12) < 0.02  # found his tempo
    assert got["steady"]["rms"] < 12  # ms from his beat, with 7 ms of human jitter and fills thrown in
    assert got["ramp"]["rms"] < 25  # tempo moving +-15% over the song
    assert got["ramp"]["step"] <= 0.0025 + 1e-9  # never a tempo jump the band could hear
    assert abs(got["late"]["rate"] - 0.9) < 0.02 and got["late"]["rms"] < 25  # started 80 ms out of phase


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_fills_do_not_drag_the_tempo_and_helpers_behave(tmp_path):
    got = _run(MISC, tmp_path)
    assert got["locked"] and got["drift"] < 0.01
    assert got["count"]["period"] == 0.5 and got["count"]["last"] == 2.5
    assert got["uneven"] is None and got["slow"] is None
    assert got["kick"] == 1 and got["tom"] == 0 and got["ghost"] == 0
    assert got["grid"] == [2.5, 2.75, -1.0, 120.0]
