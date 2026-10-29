// Staff notation for the parts that are not guitar or bass: keys and other pitched instruments on a treble, bass or grand staff,
// drums on a percussion staff. Every bar is built as a list of items in half-staff-space units (`hs`), so a row of bars can share
// one height; painting turns the items into absolutely positioned spans (`left` is the position in the bar, in percent).
import { fifthsFor, useFlats } from './theory.js';

const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};

export const SCORE_KINDS = new Set(['keys', 'other', 'drums']);

const NW = 2.7; // notehead width
const STEM = 7; // stem length
const TREBLE_TOP = 38; // diatonic step of the top line (F5); a step is a letter on a line or space, C4 = 28
const BASS_TOP = 26; // A3
const SHARP_SPELL = [[0, 0], [0, 1], [1, 0], [1, 1], [2, 0], [3, 0], [3, 1], [4, 0], [4, 1], [5, 0], [5, 1], [6, 0]];
const FLAT_SPELL = [[0, 0], [1, -1], [1, 0], [2, -1], [2, 0], [3, 0], [4, -1], [4, 0], [5, -1], [5, 0], [6, -1], [6, 0]];
const SHARP_ORDER = [3, 0, 4, 1, 5, 2, 6]; // F C G D A E B
const FLAT_ORDER = [6, 2, 5, 1, 4, 0, 3];
const SHARP_STEPS = [38, 35, 39, 36, 33, 37, 34]; // where the signature sits on the treble staff
const FLAT_STEPS = [34, 37, 33, 36, 32, 35, 31];
const CYMBALS = new Set([26, 42, 44, 46, 49, 51, 52, 53, 55, 57, 59]);
const FEET = new Set([35, 36, 44]);
const SIGN = { '-1': '♭', 0: '♮', 1: '♯' };

const RESTS = {
  1: { y: 2, h: 1, cls: 'r1' },
  2: { y: 3, h: 1, cls: 'r2' },
  4: { y: 1, h: 6, svg: '<svg viewBox="0 0 10 24" aria-hidden="true"><path d="M3 1 L7 7 L3 12 L7 17 Q3 17 3.5 22" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>' },
  8: { y: 2, h: 5, svg: '<svg viewBox="0 0 10 20" aria-hidden="true"><circle cx="3" cy="5" r="2" fill="currentColor"/><path d="M4 6 Q8 6 8.5 2 M8.5 2 L4 19" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>' },
  16: { y: 1, h: 7, svg: '<svg viewBox="0 0 10 28" aria-hidden="true"><circle cx="3" cy="5" r="2" fill="currentColor"/><circle cx="2" cy="11" r="2" fill="currentColor"/><path d="M4 6 Q8 6 8.5 2 M3 12 Q7 12 7.5 8 M8.5 2 L3 26" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>' },
};

const FLAG_H = 4.6;
const FLAG_UP = '<svg viewBox="0 0 10 20" preserveAspectRatio="none" aria-hidden="true"><path d="M0 0 C1 7 9 8 8 17 C7 12 4 10 0 10 Z" fill="currentColor"/></svg>';
const FLAG_DN = '<svg viewBox="0 0 10 20" preserveAspectRatio="none" aria-hidden="true"><path d="M0 20 C1 13 9 12 8 3 C7 8 4 10 0 10 Z" fill="currentColor"/></svg>';

const beamsOf = (v) => (v <= 1 / 32 ? 3 : v <= 1 / 16 ? 2 : v <= 1 / 8 ? 1 : 0);

// A length in whole notes -> the plain note value under it and whether it is dotted.
function value(span) {
  let base = 1;
  while (base > 1 / 32 && base > span * 1.01) base /= 2;
  return { v: base, dotted: span > base * 1.25 };
}

// Written pitch of a stored note, spelled in the song's key.
function spell(midi, flats) {
  const [letter, alter] = (flats ? FLAT_SPELL : SHARP_SPELL)[((midi % 12) + 12) % 12];
  return { step: 7 * (Math.floor(midi / 12) - 1) + letter, letter, alter };
}

function signature(fifths) {
  const alter = new Array(7).fill(0);
  const n = Math.abs(fifths);
  for (let i = 0; i < n; i++) alter[(fifths > 0 ? SHARP_ORDER : FLAT_ORDER)[i]] = fifths > 0 ? 1 : -1;
  return alter;
}

// Key signature that suits the notes best: fewest out-of-scale pitch classes, ties go to the detected key, then to the
// accidentals setting. The stored key can be off (detection, transposed sources), the notes are what has to be spelled.
function fitFifths(mids, key) {
  const want = fifthsFor(key, 0);
  const hist = new Array(12).fill(0);
  for (const m of mids) hist[((m % 12) + 12) % 12]++;
  let best = want, bestCost = Infinity;
  for (let f = -7; f <= 7; f++) {
    const scale = new Set([0, 2, 4, 5, 7, 9, 11].map((d) => (d + 7 * f + 1200) % 12)); // major scale on fifths f
    let cost = 0;
    for (let pc = 0; pc < 12; pc++) if (!scale.has(pc)) cost += hist[pc];
    cost = cost * 100 + Math.abs(f) * 2 + (f === want ? 0 : 1);
    if (cost < bestCost) { bestCost = cost; best = f; }
  }
  return mids.length ? best : want;
}

export function scoreLayout(p, key) {
  const drums = p.kind === 'drums';
  const mids = [];
  if (!drums) for (const b of p.bars) for (const beat of b.n) for (const [s, f] of beat[2]) if (p.tuning[s] != null) mids.push(p.tuning[s] + f);
  const fifths = drums ? 0 : fitFifths(mids, key);
  const lay = { drums, fifths, flats: fifths < 0 || (fifths === 0 && useFlats(key, 0)), p, sig: signature(fifths), bars: [] };
  if (drums) {
    lay.staves = [{ y0: 0, T: 0 }];
  } else {
    mids.sort((a, b) => a - b);
    const median = mids[mids.length >> 1] ?? 72;
    const low = mids.filter((m) => m < 60).length, high = mids.length - low;
    lay.grand = p.kind === 'keys' && low > mids.length * 0.08 && high > mids.length * 0.08;
    lay.clef = lay.grand ? 'grand' : median >= 55 ? 'treble' : 'bass';
    lay.top = lay.clef === 'bass' ? BASS_TOP : TREBLE_TOP;
    lay.staves = lay.grand ? [{ y0: 0, T: TREBLE_TOP }, { y0: TREBLE_TOP - BASS_TOP, T: BASS_TOP }] : [{ y0: 0, T: lay.top }];
  }
  let lo = 0, hi = 8;
  for (const st of lay.staves) { lo = Math.min(lo, st.y0); hi = Math.max(hi, st.y0 + 8); }
  p.bars.forEach((tb, i) => {
    const items = buildBar(tb, lay);
    lay.bars[i] = items;
    for (const it of items) { lo = Math.min(lo, it.y); hi = Math.max(hi, it.y + (it.h ?? 0)); }
  });
  lay.ymin = Math.floor(lo) - 1;
  lay.height = Math.ceil(hi) + 1 - lay.ymin;
  return lay;
}

// Rests that fill a gap [from, to) given in whole notes, largest aligned value first.
function restsFor(from, to) {
  const out = [];
  let at = from;
  while (to - at > 1 / 32 + 1e-6 && out.length < 8) {
    const room = to - at;
    let v = 1;
    while (v > 1 / 16 && (v > room + 1e-6 || Math.abs(at / v - Math.round(at / v)) > 1e-3)) v /= 2;
    out.push({ at, v });
    at += v;
  }
  return out;
}

function buildBar(tb, lay) {
  const { p, drums } = lay;
  // beats at the same spot become one chord
  const merged = new Map();
  for (const [pos, dur, notes] of tb.n) {
    const k = Math.round(pos * 1000);
    const m = merged.get(k) || { pos, dur, notes: [] };
    m.dur = Math.min(m.dur, dur);
    for (const [s, f, flags = ''] of notes) {
      if (drums) m.notes.push({ y: s * 2, midi: f, flags });
      else if (p.tuning[s] != null) m.notes.push({ midi: p.tuning[s] + f, flags });
    }
    merged.set(k, m);
  }
  const beats = [...merged.values()].filter((b) => b.notes.length).sort((a, b) => a.pos - b.pos);
  // one list per voice: a staff each on a grand staff, hands and feet on a drum staff, otherwise one
  const lists = lay.staves.map(() => []);
  if (drums) {
    lists.push([]);
    for (const b of beats) {
      const up = b.notes.filter((n) => !FEET.has(n.midi)), down = b.notes.filter((n) => FEET.has(n.midi));
      if (up.length) lists[0].push({ ...b, notes: up });
      if (down.length) lists[1].push({ ...b, notes: down });
    }
  } else {
    for (const b of beats) {
      const notes = b.notes.map((n) => ({ ...n, ...spell(n.midi, lay.flats) }));
      const stave = (n) => (lay.grand && n.midi < 60 ? 1 : 0);
      for (let s = 0; s < lists.length; s++) {
        const here = notes.filter((n) => stave(n) === s);
        if (here.length) lists[s].push({ ...b, notes: here });
      }
    }
  }
  const items = [];
  lists.forEach((list, vi) => {
    const stave = lay.staves[Math.min(vi, lay.staves.length - 1)];
    voice(list, vi, stave, tb, lay, items);
  });
  return items;
}

function voice(list, vi, stave, tb, lay, items) {
  const { drums } = lay;
  const len = tb.len;
  const down = drums && vi === 1;
  const yOf = (n) => (drums ? n.y : lay.top - n.step);
  const mid = drums ? 4 : stave.y0 + 4; // y of the middle line
  // note values: each note lasts until the next onset at most
  const evs = list.map((b, k) => {
    const next = list[k + 1];
    const span = Math.max(1 / 64, Math.min(b.dur, (next ? next.pos : 1) - b.pos) * len);
    const { v, dotted } = value(span);
    return { ...b, span, v, dotted, beams: beamsOf(v) };
  });

  // accidentals, bar by bar state per letter and octave
  if (!drums) {
    const state = new Map();
    for (const e of evs) {
      const accs = [];
      for (const n of [...e.notes].sort((a, b) => b.step - a.step)) {
        const cur = state.get(n.step) ?? lay.sig[n.letter];
        if (cur !== n.alter) { accs.push(n); state.set(n.step, n.alter); }
      }
      e.accs = accs;
    }
  }

  const ledgers = new Set();
  const ledger = (x, y) => {
    const key = `${Math.round(x * 1000)}_${y}`;
    if (ledgers.has(key)) return;
    ledgers.add(key);
    items.push({ cls: 'led', x, y, dx: -NW * 0.95, w: NW * 1.9, h: 0 });
  };
  const top0 = stave.y0, bot0 = stave.y0 + 8;

  // runs of beamed notes
  const group = (e) => Math.floor(e.pos * len * 4 + 1e-6);
  const runs = [];
  evs.forEach((e, k) => {
    const prev = evs[k - 1], run = runs.at(-1);
    if (e.beams && run && prev.beams && group(prev) === group(e) && Math.abs(prev.pos + prev.span / len - e.pos) < 1e-3) run.push(e);
    else runs.push([e]);
  });

  const last = new Map();
  for (const run of runs) {
    const ys = run.flatMap((e) => e.notes.map(yOf));
    const up = down ? false : drums ? true : ys.reduce((a, y) => a + (y - mid), 0) > 0; // y grows downward: notes below the middle line take stems up
    const dir = up ? 'up' : 'down';
    for (const e of run) {
      e.ys = e.notes.map(yOf);
      e.top = Math.min(...e.ys);
      e.bot = Math.max(...e.ys);
    }
    const beamY = up ? Math.min(...run.map((e) => e.top)) - STEM : Math.max(...run.map((e) => e.bot)) + STEM;
    const sdx = up ? NW / 2 - 0.15 : -NW / 2 + 0.15;
    for (const e of run) {
      e.dir = dir;
      // heads, displaced when a second sits against another head
      const order = [...e.notes].sort((a, b) => (up ? yOf(b) - yOf(a) : yOf(a) - yOf(b)));
      let prevY = null, prevShift = false;
      for (const n of order) {
        const y = yOf(n);
        const shift = prevY != null && Math.abs(y - prevY) <= 1.01 && !prevShift;
        prevShift = shift;
        prevY = y;
        const dx = shift ? (up ? NW : -NW) : 0;
        const open = e.v >= 1 / 2;
        const cross = drums && CYMBALS.has(n.midi);
        items.push({ cls: `nh${open ? ' open' : ''}${e.v >= 1 ? ' whole' : ''}${cross || n.flags.includes('x') ? ' cross' : ''}`, x: e.pos, y: y - 1, dx, h: 2, text: cross || n.flags.includes('x') ? '×' : '' });
        if (drums && n.midi === 46) items.push({ cls: 'ho', x: e.pos, y: y - 3.4, text: 'o', h: 2 });
        if (!drums) {
          // ledger lines between the note and its staff
          if (y < top0 - 1.5) for (let l = top0 - 2; l >= y - 0.5; l -= 2) ledger(e.pos, l);
          else if (y > bot0 + 1.5) for (let l = bot0 + 2; l <= y + 0.5; l += 2) ledger(e.pos, l);
        } else if (y < -1.5) for (let l = -2; l >= y - 0.5; l -= 2) ledger(e.pos, l);
        else if (y > 9.5) for (let l = 10; l <= y + 0.5; l += 2) ledger(e.pos, l);
        if (e.dotted) items.push({ cls: 'dot', x: e.pos, y: (y % 2 === 0 ? y - 1 : y) - 0.3, dx: NW / 2 + 0.9 + Math.max(0, dx), h: 0.6 });
        if (n.flags.includes('t') && last.has(n.midi)) {
          const from = last.get(n.midi);
          items.push({ cls: `arc${up ? ' below' : ''}`, x: from, y: up ? y + 1.1 : y - 2.3, wFrac: e.pos - from, h: 1.2 });
        }
        last.set(n.midi, e.pos);
      }
      (e.accs || []).forEach((n, i) => items.push({ cls: 'acc', x: e.pos, y: yOf(n) - 1.8, dx: -(NW / 2 + 1.6 + (i % 3) * 2.3), h: 3.6, text: SIGN[n.alter] }));
      // stem
      if (e.v < 1) {
        const from = up ? e.bot : e.top;
        const to = run.length > 1 ? beamY : up ? e.top - STEM : e.bot + STEM;
        items.push({ cls: 'sstem', x: e.pos, y: Math.min(from, to), dx: sdx, h: Math.abs(to - from) });
        e.stemEnd = to;
      }
    }
    // beams and flags
    run.forEach((e, k) => {
      if (e.v >= 1) return;
      const sdxE = sdx;
      const nxt = run[k + 1], prv = run[k - 1];
      for (let n = 1; n <= e.beams; n++) {
        const yb = (run.length > 1 ? beamY : e.stemEnd) + (up ? (n - 1) * 1.15 : -(n - 1) * 1.15) - (up ? 0 : 0.5);
        if (nxt && nxt.beams >= n) items.push({ cls: 'beam', x: e.pos, y: yb, dx: sdxE - 0.15, wFrac: nxt.pos - e.pos, w: 0.3, h: 0.55 });
        else if (run.length > 1 && !(prv && prv.beams >= n)) items.push({ cls: `beam stub ${prv ? 'l' : 'r'}`, x: e.pos, y: yb, dx: prv ? sdxE - 1.9 : sdxE - 0.15, w: 2.0, h: 0.55 });
        else if (run.length === 1) items.push({ cls: 'flag', x: e.pos, y: up ? e.stemEnd + (n - 1) * 1.3 : e.stemEnd - FLAG_H - (n - 1) * 1.3, dx: sdxE - 0.1, w: 2.4, h: FLAG_H, svg: up ? FLAG_UP : FLAG_DN });
      }
    });
  }

  // rests
  if (drums && vi === 1) return;
  const staffTop = drums ? 0 : stave.y0;
  const rest = (at, v, dur) => {
    const r = RESTS[v] || RESTS[16];
    items.push({ cls: `rest ${r.cls ?? ''}`.trim(), x: Math.min(0.97, (at + (dur ?? 0) / 2) / len), y: staffTop + r.y, h: r.h, svg: r.svg, w: v >= 2 ? 3.2 : 2.6, dx: v >= 2 ? -1.5 : -0.4 });
  };
  if (!evs.length) {
    items.push({ cls: `rest ${RESTS[1].cls}`, x: 0.5, y: staffTop + RESTS[1].y, h: 1, w: 3.2, dx: -1.6 });
    return;
  }
  let cursor = 0;
  for (const e of evs) {
    for (const r of restsFor(cursor, e.pos * len)) rest(r.at, r.v, r.v);
    cursor = Math.max(cursor, e.pos * len + e.span);
  }
  for (const r of restsFor(cursor, len)) rest(r.at, r.v, r.v);
}

export function paintScore(stf, i, lay) {
  stf.replaceChildren();
  const ymin = lay.ymin;
  for (const st of lay.staves) stf.append(el('span', { className: 'lines', style: `top:calc(${st.y0 - ymin} * var(--hs))` }));
  for (const it of lay.bars[i] || []) {
    const n = el('span', { className: `s ${it.cls}` });
    if (it.text) n.textContent = it.text;
    if (it.svg) n.innerHTML = it.svg; // constant markup from RESTS
    let css = `left:${(it.x * 100).toFixed(3)}%;top:calc(${(it.y - ymin).toFixed(2)} * var(--hs));`;
    if (it.dx) css += `margin-left:calc(${it.dx.toFixed(2)} * var(--hs));`;
    if (it.wFrac != null) css += `width:calc(${(it.wFrac * 100).toFixed(3)}% + ${(it.w ?? 0).toFixed(2)} * var(--hs));`;
    else if (it.w) css += `width:calc(${it.w.toFixed(2)} * var(--hs));`;
    if (it.h) css += `height:calc(${it.h.toFixed(2)} * var(--hs));`;
    n.style.cssText = css;
    stf.append(n);
  }
  stf.append(el('span', { className: 'cursor' }));
}

// Clef and key signature at the start of every row.
export function clefColumn(lay) {
  const col = el('div', { className: 'clefs', 'aria-hidden': 'true' });
  // drums: no percussion clef, the row starts at the bars
  if (lay.drums) { col.style.width = '0'; return col; }
  for (const st of lay.staves) col.append(el('span', { className: 'lines', style: `top:calc(${st.y0 - lay.ymin} * var(--hs))` }));
  const place = (cls, y, text, x, extra = '') => col.append(el('span', { className: `s ${cls}`, textContent: text, style: `left:calc(${x} * var(--hs));top:calc(${y - lay.ymin} * var(--hs));${extra}` }));
  const marks = [];
  const signs = lay.fifths > 0 ? SHARP_STEPS : FLAT_STEPS;
  for (let i = 0; i < Math.abs(lay.fifths); i++) marks.push(signs[i]);
  let width = 7;
  if (lay.drums) {
    col.append(el('span', { className: 's pbar', style: `left:calc(1.5 * var(--hs));top:calc(${2 - lay.ymin} * var(--hs));height:calc(4 * var(--hs))` }));
    col.append(el('span', { className: 's pbar', style: `left:calc(3.4 * var(--hs));top:calc(${2 - lay.ymin} * var(--hs));height:calc(4 * var(--hs))` }));
    width = 6;
  } else {
    lay.staves.forEach((st) => {
      const treble = st.T === TREBLE_TOP;
      place(treble ? 'clef g' : 'clef f', st.y0 + (treble ? 5.2 : 5.3), treble ? '𝄞' : '𝄢', 0.6);
      marks.forEach((step, i) => {
        const y = st.y0 + (st.T - (treble ? step : step - 14));
        place('ksig', y - 1.8, lay.fifths > 0 ? '♯' : '♭', 7 + i * 2.4);
      });
    });
    width = 7 + marks.length * 2.4 + 1;
  }
  col.style.width = `calc(${width} * var(--hs))`;
  col.style.height = `calc(${lay.height} * var(--hs))`;
  return col;
}
