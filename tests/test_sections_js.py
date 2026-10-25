import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "prismix" / "web"
NODE = shutil.which("node")

SCRIPT = """
import { fromLyrics, fitLabel } from %r;
const L = []; let t = 12;
for (const [sec, n] of [['Verse 1', 4], ['Chorus', 4], ['Verse 2: X', 4], ['Chorus', 4], ['Bridge', 2], ['Chorus', 4]]) {
  for (let i = 0; i < n; i++) { L.push({ t, text: 'line ' + t, ...(i === 0 ? { sec } : {}) }); t += 4; }
}
const s = fromLyrics(L, t + 20);
console.log(JSON.stringify({ labels: s.map((x) => x.label), fit: [40, 60, 90].map((w) => fitLabel(s[2], w)), tiny: fitLabel(s[2], 10) }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_genius_headers_name_numbered_parts_that_shorten_with_width(tmp_path):
    f = tmp_path / "t.mjs"
    f.write_text(SCRIPT % (WEB / "sections.js").as_uri())
    out = subprocess.run([NODE, f], capture_output=True, text=True, check=True).stdout
    got = json.loads(out)
    assert got["labels"] == ["Intro", "Verse 1", "Chorus 1", "Verse 2", "Chorus 2", "Bridge", "Chorus 3", "Outro"]
    assert got["fit"] == ["C1", "Chor.1", "Chorus 1"]
    assert got["tiny"] == ""


CHORD_SCRIPT = """
import { fromChords } from %r;
const prog = { A: ['C', 'G', 'Am', 'F'], B: ['F', 'C', 'G', 'G'] };
const bars = []; let t = 0;
for (const k of 'AABAB') for (let r = 0; r < 2; r++) for (const key of prog[k]) { bars.push({ t, key }); t += 2; }
console.log(JSON.stringify({
  parts: fromChords(bars, t).map((x) => x.label),
  flat: fromChords(bars.map((b, i) => ({ ...b, key: 'x' + i })), t).length,
}));
"""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_repeated_chord_loops_become_letter_parts(tmp_path):
    f = tmp_path / "c.mjs"
    f.write_text(CHORD_SCRIPT % (WEB / "sections.js").as_uri())
    got = json.loads(subprocess.run([NODE, f], capture_output=True, text=True, check=True).stdout)
    assert got["parts"] == ["A", "B", "A", "B"]
    assert got["flat"] == 0
