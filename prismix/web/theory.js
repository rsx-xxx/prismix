// Pitch spelling, chord naming and guitar voicings. Pure functions, no DOM.

export const mod12 = (n) => ((n % 12) + 12) % 12;

const SHARP = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
const FLAT = ['C', 'D♭', 'D', 'E♭', 'E', 'F', 'G♭', 'G', 'A♭', 'A', 'B♭', 'B'];
// major-key tonic pitch class -> signature (sharps positive, flats negative)
const MAJOR_FIFTHS = [0, -5, 2, -3, 4, -1, 6, 1, -4, 3, -2, 5];

/** Key signature (fifths) for a tonic pitch class + mode, shifted by `semitones`. */
export function fifthsFor(key, semitones = 0) {
  if (!key) return 0;
  const tonic = mod12(key.tonic + semitones + (key.mode === 'minor' ? 3 : 0));
  return MAJOR_FIFTHS[tonic];
}

let accidentals = (() => { try { return localStorage.getItem('stems:accidentals') || 'auto'; } catch { return 'auto'; } })();
/** 'auto' follows the key signature; 'flat' and 'sharp' force one spelling everywhere. */
export const getAccidentals = () => accidentals;
export function setAccidentals(v) {
  accidentals = v === 'flat' || v === 'sharp' ? v : 'auto';
  try { localStorage.setItem('stems:accidentals', accidentals); } catch { /* ignore */ }
}
export const useFlats = (key, semitones = 0) => (accidentals === 'auto' ? fifthsFor(key, semitones) < 0 : accidentals === 'flat');

export const keyName = (key, semitones = 0) => {
  if (!key) return '';
  const pc = mod12(key.tonic + semitones);
  const names = useFlats(key, semitones) ? FLAT : SHARP;
  return `${names[pc]}${key.mode === 'minor' ? ' minor' : ' major'}`;
};

export const prefersFlats = (fifths) => fifths < 0;

const QUALITY_SUFFIX = {
  maj: '', min: 'm', 7: '7', maj7: 'maj7', min7: 'm7', dim: 'dim', aug: 'aug', sus2: 'sus2', sus4: 'sus4',
  min6: 'm6', maj6: '6', dim7: 'dim7', m7b5: 'm7♭5', mmaj7: 'm(maj7)',
};

/**
 * Colour of a chord (sounding root, so it follows transposition), in OKLCH so lightness is perceptual.
 * Hue: position on the circle of fifths, so closely related chords get neighbouring hues and a
 * progression reads as a short walk round the wheel. Lightness: major-family bright, minor-family
 * darker, diminished darkest. Chroma: 7ths richer, suspended and diminished muted.
 */
export function chordPaint(c, semitones = 0) {
  const hue = ((mod12(c.r + semitones) * 7) % 12) * 30 + 20;
  const q = c.q;
  const minor = q.startsWith('min') || q === 'm7b5' || q === 'mmaj7';
  const dim = q === 'dim' || q === 'dim7' || q === 'm7b5';
  const l = dim ? 0.52 : minor ? 0.62 : q === 'aug' ? 0.68 : 0.78;
  const chroma = dim || q.startsWith('sus') ? 0.08 : q === '7' || q.endsWith('7') ? 0.16 : 0.14;
  return { hue, l, chroma };
}

export function chordName(c, semitones = 0, flats = false) {
  return (flats ? FLAT : SHARP)[mod12(c.r + semitones)] + (QUALITY_SUFFIX[c.q] ?? '');
}

export const CHORD_TONES = {
  maj: [0, 4, 7], min: [0, 3, 7], 7: [0, 4, 7, 10], maj7: [0, 4, 7, 11], min7: [0, 3, 7, 10], dim: [0, 3, 6],
  aug: [0, 4, 8], sus2: [0, 2, 7], sus4: [0, 5, 7], min6: [0, 3, 7, 9], maj6: [0, 4, 7, 9], dim7: [0, 3, 6, 9],
  m7b5: [0, 3, 6, 10], mmaj7: [0, 3, 7, 11],
};

// ── spelling ──
const STEPS = ['C', 'D', 'E', 'F', 'G', 'A', 'B'];
const STEP_PC = [0, 2, 4, 5, 7, 9, 11];
const SHARP_ORDER = ['F', 'C', 'G', 'D', 'A', 'E', 'B'];

/** Alteration of each letter in a key signature. */
export function keySignature(fifths) {
  const alter = Object.fromEntries(STEPS.map((s) => [s, 0]));
  if (fifths > 0) SHARP_ORDER.slice(0, fifths).forEach((s) => { alter[s] = 1; });
  if (fifths < 0) [...SHARP_ORDER].reverse().slice(0, -fifths).forEach((s) => { alter[s] = -1; });
  return alter;
}

/** midi -> {step, alter, octave}, spelled to suit the key. */
export function spell(midi, fifths) {
  const pc = mod12(midi);
  const sig = keySignature(fifths);
  for (const s of STEPS) { // diatonic spelling first
    if (mod12(STEP_PC[STEPS.indexOf(s)] + sig[s]) === pc) return place(midi, s, sig[s]);
  }
  const useFlats = fifths < 0;
  for (const s of STEPS) {
    const base = STEP_PC[STEPS.indexOf(s)];
    if (!useFlats && base + 1 === pc) return place(midi, s, 1);
    if (useFlats && base - 1 === pc) return place(midi, s, -1);
    if (useFlats && base === 0 && pc === 11) return place(midi, 'C', -1);
  }
  return place(midi, 'C', 0);
}

function place(midi, step, alter) {
  // octave number belongs to the letter, so B♯/C♭ need a nudge
  const base = STEP_PC[STEPS.indexOf(step)] + alter;
  return { step, alter, octave: Math.floor((midi - base) / 12) - 1 };
}

// ── guitar ──
export const TUNINGS = {
  guitar: [40, 45, 50, 55, 59, 64], // E2 A2 D3 G3 B3 E4, low -> high
  bass: [28, 33, 38, 43],
};
export const MAX_FRET = 22;

/** Guitar tunings, low -> high. Ordered for the picker: standard first, then what guitarists actually retune to. */
export const GUITAR_TUNINGS = [
  { id: 'std', name: 'Standard', hint: 'E A D G B E', open: [40, 45, 50, 55, 59, 64] },
  { id: 'dropd', name: 'Drop D', hint: 'D A D G B E: only the low string goes down a whole step', open: [38, 45, 50, 55, 59, 64] },
  { id: 'eb', name: 'Half step down', hint: 'Eb Ab Db Gb Bb Eb: every string down a half step, the shapes stay the same', open: [39, 44, 49, 54, 58, 63] },
  { id: 'd', name: 'Whole step down', hint: 'D G C F A D: every string down a whole step, the shapes stay the same', open: [38, 43, 48, 53, 57, 62] },
  { id: 'dropdb', name: 'Drop Db (half step down + drop)', hint: 'Db Ab Db Gb Bb Eb', open: [37, 44, 49, 54, 58, 63] },
  { id: 'dropc', name: 'Drop C', hint: 'C G C F A D', open: [36, 43, 48, 53, 57, 62] },
  { id: 'dadgad', name: 'DADGAD', hint: 'D A D G A D', open: [38, 45, 50, 55, 57, 62] },
  { id: 'opend', name: 'Open D', hint: 'D A D F# A D', open: [38, 45, 50, 54, 57, 62] },
  { id: 'openg', name: 'Open G', hint: 'D G D G B D', open: [38, 43, 50, 55, 59, 62] },
  { id: 'opene', name: 'Open E', hint: 'E B E G# B E', open: [40, 47, 52, 56, 59, 64] },
];
export const tuningById = (id) => GUITAR_TUNINGS.find((t) => t.id === id) ?? GUITAR_TUNINGS[0];

/** The preset (or a custom entry) for six open-string pitches read from a tab; null for anything but a six-string guitar. */
export function tuningFromPitches(pitches) {
  if (!Array.isArray(pitches) || pitches.length !== 6) return null;
  const hit = GUITAR_TUNINGS.find((t) => t.open.every((m, i) => m === pitches[i]));
  if (hit) return hit;
  const names = pitches.map((m) => SHARP[mod12(m)]).join(' ');
  return { id: `custom:${pitches.join(',')}`, name: names, hint: 'As written in the tab', open: [...pitches] };
}

/**
 * How to read a tuning in chord shapes. `down` is how far the whole guitar is tuned below standard (judged by the high string),
 * so a shape fingered in standard sounds `down` semitones lower; `open` is the tuning left once that lowering is taken out
 * (standard for a tuned-down guitar, drop D for drop C, ...).
 */
export function shapeTuning(t) {
  const down = TUNINGS.guitar[5] - t.open[5];
  return { down, open: t.open.map((m) => m + down) };
}

/** Candidate (string, fret) pairs for a pitch, string index 0 = lowest. */
export function positions(midi, tuning) {
  const out = [];
  tuning.forEach((open, s) => {
    const fret = midi - open;
    if (fret >= 0 && fret <= MAX_FRET) out.push([s, fret]);
  });
  return out;
}

/** Fold a pitch into the instrument's playable range. */
export function fold(midi, tuning) {
  const lo = tuning[0], hi = tuning[tuning.length - 1] + MAX_FRET;
  let m = midi;
  while (m < lo) m += 12;
  while (m > hi) m -= 12;
  return m;
}

/**
 * Assign strings to a sequence of onset groups (arrays of midi pitches) minimising hand
 * movement (Viterbi over per-group candidate fingerings). Returns [[{midi,string,fret}...]...].
 */
export function fret(groups, tuning) {
  const cands = groups.map((g) => fingerings(g, tuning));
  const cost = cands.map((c) => c.map(() => Infinity));
  const back = cands.map((c) => c.map(() => -1));
  cands[0]?.forEach((c, j) => { cost[0][j] = c.cost; });
  for (let i = 1; i < cands.length; i++) {
    cands[i].forEach((c, j) => {
      cands[i - 1].forEach((p, k) => {
        const v = cost[i - 1][k] + c.cost + Math.abs(c.pos - p.pos) * 0.6;
        if (v < cost[i][j]) { cost[i][j] = v; back[i][j] = k; }
      });
    });
  }
  const path = new Array(groups.length);
  let j = cost.length ? cost[cost.length - 1].indexOf(Math.min(...cost[cost.length - 1])) : -1;
  for (let i = groups.length - 1; i >= 0; i--) { path[i] = cands[i][j].notes; j = back[i][j]; }
  return path;
}

function fingerings(pitches, tuning) {
  const sorted = [...pitches].sort((a, b) => b - a).slice(0, tuning.length);
  const opts = sorted.map((m) => positions(m, tuning));
  const results = [];
  const used = new Set();
  const pick = [];
  const rec = (i) => {
    if (results.length > 400) return;
    if (i === sorted.length) { results.push(score(sorted, pick)); return; }
    for (const [s, f] of opts[i]) {
      if (used.has(s)) continue;
      used.add(s); pick.push([s, f]);
      rec(i + 1);
      used.delete(s); pick.pop();
    }
  };
  rec(0);
  if (!results.length) { // unreachable pitch: park on the nearest string
    return [{ cost: 50, pos: 0, notes: sorted.map((m) => ({ midi: m, string: 0, fret: 0 })) }];
  }
  return results.sort((a, b) => a.cost - b.cost).slice(0, 8);
}

function score(pitches, pick) {
  const fretted = pick.map(([, f]) => f).filter((f) => f > 0);
  const lo = fretted.length ? Math.min(...fretted) : 0;
  const hi = fretted.length ? Math.max(...fretted) : 0;
  const span = hi - lo;
  const pos = fretted.length ? (lo + hi) / 2 : 0;
  // prefer low positions, small stretches, and open strings
  const cost = (span > 4 ? (span - 4) * 6 : span) + pos * 0.25 - pick.filter(([, f]) => f === 0).length * 0.3;
  return { cost, pos, notes: pitches.map((m, i) => ({ midi: m, string: pick[i][0], fret: pick[i][1] })) };
}

// ── chord diagrams ──
/** Fingers for a shape: 1-4 per string (0 open, -1 muted), plus the barre if one is needed. Null if unplayable. */
export function fingering(frets) {
  const pressed = frets.map((f, s) => ({ f, s })).filter((x) => x.f > 0);
  if (!pressed.length) return { fingers: frets.map((f) => (f < 0 ? -1 : 0)), barre: null, count: 0 };
  const low = Math.min(...pressed.map((x) => x.f));
  const onLow = pressed.filter((x) => x.f === low);
  let barre = null;
  if (onLow.length >= 2) {
    const from = Math.min(...onLow.map((x) => x.s)), to = Math.max(...onLow.map((x) => x.s));
    const blocked = frets.some((f, s) => s > from && s < to && f >= 0 && f < low);
    if (!blocked && (onLow.length >= 3 || to - from >= 2 || pressed.length > 4)) barre = { fret: low, from, to };
  }
  const fingers = frets.map((f) => (f < 0 ? -1 : 0));
  let next = 1;
  if (barre) {
    for (let s = barre.from; s <= barre.to; s++) if (frets[s] === low) fingers[s] = 1;
    next = 2;
  }
  const rest = pressed.filter((x) => !(barre && x.f === low)).sort((a, b) => a.f - b.f || a.s - b.s);
  for (const x of rest) fingers[x.s] = Math.min(next++, 5);
  const count = new Set(fingers.filter((f) => f > 0)).size;
  return next > 5 ? null : { fingers, barre, count };
}

// What to play instead when the real chord is hard: drop the extensions.
const SIMPLER = { 7: 'maj', maj7: 'maj', maj6: 'maj', min7: 'min', min6: 'min', mmaj7: 'min', m7b5: 'dim', dim7: 'dim' };
export const simplify = (c) => (SIMPLER[c.q] ? { ...c, q: SIMPLER[c.q] } : c);

const shapeCache = new Map();
/** All playable voicings of a chord (root pitch class + quality) on a guitar tuned to `open`, best first. Each has frets, base, fingers, barre, ease. */
export function voicings(c, semitones = 0, open = TUNINGS.guitar) {
  const root = mod12(c.r + semitones);
  const key = `${open.join(',')}|${root}:${c.q}`;
  if (shapeCache.has(key)) return shapeCache.get(key);
  const tones = new Set((CHORD_TONES[c.q] ?? CHORD_TONES.maj).map((i) => mod12(root + i)));
  const seen = new Map();
  for (let base = 0; base <= 10; base++) {
    const hi = base + 3;
    const per = open.map((o) => {
      const opts = [-1];
      if ((base === 0 || base === 1) && tones.has(mod12(o))) opts.push(0);
      if (base > 1 && tones.has(mod12(o))) opts.push(0);
      for (let f = Math.max(base, 1); f <= hi; f++) if (tones.has(mod12(o + f))) opts.push(f);
      return opts;
    });
    const frets = new Array(6).fill(-1);
    const rec = (s) => {
      if (s < 6) { for (const f of per[s]) { frets[s] = f; rec(s + 1); } return; }
      const sounding = frets.map((f, i) => (f < 0 ? null : mod12(open[i] + f)));
      const played = sounding.filter((x) => x !== null);
      if (played.length < 4) return;
      const lowest = sounding.findIndex((x) => x !== null);
      const cover = new Set(played);
      if (cover.size < Math.min(tones.size, 3)) return;
      const id = frets.join(',');
      if (seen.has(id)) return;
      const fg = fingering(frets);
      if (!fg) return;
      const pressed = frets.filter((f) => f > 0);
      const muted = frets.filter((f) => f < 0).length;
      const inner = frets.slice(lowest).filter((f) => f < 0).length;
      const bassOk = sounding[lowest] === root ? 0 : 3;
      const missing = tones.size - cover.size;
      const span = pressed.length ? Math.max(...pressed) - Math.min(...pressed) : 0;
      // musical quality (full, rooted) and playing effort (fingers, barre, stretch, height up the neck)
      const sound = missing * 6 + bassOk + muted * 0.5 + inner * 3.5;
      const effort = fg.count * 1.2 + (fg.barre ? 3.5 : 0) + span * 0.8 + (pressed.length ? Math.min(...pressed) * 0.35 : 0);
      seen.set(id, { frets: [...frets], base: base <= 1 ? 1 : base, ...fg, sound, effort, ease: sound + effort * 1.6, rank: sound + effort * 0.25 });
    };
    rec(0);
  }
  const list = [...seen.values()];
  shapeCache.set(key, list);
  return list;
}

/** One voicing: the best-sounding by default, the least effort with `easy`; `alt` steps through the alternatives. */
export function voicing(c, semitones = 0, { easy = false, alt = 0, open } = {}) {
  const list = [...voicings(c, semitones, open)].sort((a, b) => (easy ? a.ease - b.ease : a.rank - b.rank));
  return list[alt % Math.max(list.length, 1)] ?? null;
}

export const voicingCount = (c, semitones = 0, open) => voicings(c, semitones, open).length;

/** Relative effort saved (0-1, negative = worse) by a capo on each fret 0-7, weighting chords by time played. */
export function capoSavings(weighted, semitones, simple = false, open) {
  const cost = (capo) => weighted.reduce((sum, { c, w }) => {
    const v = voicing(simple ? simplify(c) : c, semitones - capo, { easy: true, open });
    return sum + w * (v ? v.ease : 30);
  }, 0);
  const base = Math.max(cost(0), 1);
  return Array.from({ length: 8 }, (_, k) => (base - cost(k)) / base);
}

/** The capo fret worth using, or 0. A fret must beat open shapes by a clear margin, and high frets pay a small penalty. */
export function bestCapo(weighted, semitones, simple = false, open) {
  const saved = capoSavings(weighted, semitones, simple, open);
  let best = { capo: 0, saved: 0, all: saved };
  for (let k = 1; k <= 7; k++) {
    if (saved[k] - 0.012 * k > 0.08 && saved[k] > best.saved + 0.04) best = { capo: k, saved: saved[k], all: saved };
  }
  return best;
}

/**
 * Tunings worth trying for these chords at this key, each with the share of effort it saves against playing them in standard
 * tuning (negative = harder). Tuning down is judged like a capo in reverse: the shapes move up, the sound stays.
 */
export function tuningSavings(weighted, semitones, simple = false) {
  const cost = (t) => {
    const { down, open } = shapeTuning(t);
    return weighted.reduce((sum, { c, w }) => {
      const v = voicing(simple ? simplify(c) : c, semitones + down, { easy: true, open });
      return sum + w * (v ? v.ease : 30);
    }, 0);
  };
  const base = Math.max(cost(GUITAR_TUNINGS[0]), 1);
  return GUITAR_TUNINGS.map((t) => ({ t, saved: (base - cost(t)) / base }));
}
