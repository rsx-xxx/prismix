import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "prismix" / "web"
NODE = shutil.which("node")

# The stems play on every beat and the microphone hears them at 40 % beside the drummer, who plays the off-beats.
SIM = """
import { Bleed, OnsetPicker } from %r;
let seed = 3; const rnd = () => (seed = (seed * 1664525 + 1013904223) %% 4294967296) / 4294967296;
const pulse = (t, t0, amp, dec) => (t >= t0 ? amp * Math.exp(-(t - t0) / dec) : 0);
function run(stems) {
  const bleed = new Bleed(), pick = new OnsetPicker(), hits = [];
  for (let t = 0; t < 20000; t += 5.33) {
    let ref = 0.003, mic = 0.003 * (1 + rnd());
    if (stems) for (let k = 0; k * 500 <= t; k++) ref += pulse(t, k * 500 + 7, 0.3, 80);
    mic += 0.4 * ref * (1 + 0.1 * rnd());
    for (let k = 0; k * 500 + 250 <= t; k++) mic += pulse(t, k * 500 + 250 + (rnd() - 0.5) * 12, 0.25, 60);
    bleed.push(t, [ref, ref * 0.5, ref * 0.2]);
    const h = pick.feed(t, bleed.residual(t, [mic, mic * 0.5, mic * 0.2]));
    if (h) hits.push(h.t);
  }
  return hits.filter((t) => t > 4000);
}
const score = (hits) => ({ total: hits.length, drummer: hits.filter((t) => Math.abs((t %% 500) - 250) < 40).length });
console.log(JSON.stringify({ alone: score(run(false)), withStems: score(run(true)) }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_microphone_finds_the_drummer_and_not_the_stems(tmp_path):
    f = tmp_path / "t.mjs"
    f.write_text(SIM % (WEB / "mic-beat.js").as_uri())
    got = json.loads(subprocess.run([NODE, f], capture_output=True, text=True, check=True).stdout)
    for run in (got["alone"], got["withStems"]):
        assert run["drummer"] >= 30 and run["total"] <= run["drummer"] + 1  # 32 hits to find; the stems' own beats are not hits


def test_page_ids_are_unique():
    # Two elements sharing an id made the Drummer button silently control the wrong thing
    for page in ("index.html", "remote.html"):
        ids = re.findall(r'\sid="([^"]+)"', (WEB / page).read_text())
        assert len(ids) == len(set(ids)), [i for i in set(ids) if ids.count(i) > 1]
