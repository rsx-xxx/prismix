// Song sections (intro / verse / chorus / bridge / outro). When the lyrics came from Genius, its [Verse 1] / [Chorus] headers
// name the parts. Otherwise they are guessed from synced lyrics: runs of lines that are sung again are the chorus, what is sung
// once is a verse. Chords alone are too noisy to name parts, but a repeated progression still marks parts that belong
// together, so without usable lyrics the song is cut into letter-named parts (A, B, C) by its chord loops.

/** kind -> [full word, medium abbreviation, one-letter form, symbol] */
export const WORDS = {
  intro: ['Intro', 'Int.', 'I', '▸'],
  verse: ['Verse', 'Vers.', 'V', '≡'],
  prechorus: ['Pre-Chorus', 'Pre.', 'P', '↗'],
  chorus: ['Chorus', 'Chor.', 'C', '★'],
  refrain: ['Refrain', 'Refr.', 'R', '★'],
  bridge: ['Bridge', 'Brdg.', 'B', '⌒'],
  outro: ['Outro', 'Out.', 'O', '◂'],
  inst: ['Instrumental', 'Inst.', '♪', '♪'],
  other: ['Part', 'Pt.', 'P', '•'],
};
const KINDS = [
  [/pre[- ]?chorus|пре[- ]?припев/i, 'prechorus'], [/chorus|refrain|hook|припев/i, 'chorus'], [/verse|куплет/i, 'verse'],
  [/bridge|бридж|переход/i, 'bridge'], [/intro|вступлен/i, 'intro'], [/outro|coda|ending|финал|концовк/i, 'outro'],
  [/instrumental|solo|interlude|break|инструментал|соло|проигрыш/i, 'inst'],
];
/** 'Verse 1: Artist' / '[Chorus]' -> 'verse' / 'chorus'; unknown headers stay 'other' (kept by name). */
export const kindOf = (name) => KINDS.find(([re]) => re.test(name))?.[1] ?? 'other';

/** The text that fits `px` pixels at ~6.4px per character: full word, abbreviation, letter, or just the symbol. */
export function fitLabel(sec, px) {
  const [full, mid, one, sym] = WORDS[sec.kind] ?? WORDS.other, n = sec.n ?? '';
  if (sec.kind === 'other' && sec.raw) return px >= (sec.raw.length + 5) * 6.4 + 10 ? `Part ${sec.raw}` : px >= sec.raw.length * 6.4 + 10 ? sec.raw : '';
  for (const t of [`${full} ${n}`.trim(), `${mid}${n}`, `${one}${n}`]) if (px >= t.length * 6.4 + 10) return t;
  return px >= 16 ? sym : '';
}

/** Give every part its number (per kind, only when the kind occurs more than once) and its full label. */
function name(secs) {
  const count = {};
  secs.forEach((s) => { count[s.kind] = (count[s.kind] || 0) + 1; });
  const seen = {};
  for (const s of secs) {
    seen[s.kind] = (seen[s.kind] || 0) + 1;
    if (count[s.kind] > 1 && s.kind !== 'other') s.n = seen[s.kind];
    s.label = s.kind === 'other' && s.raw ? s.raw : `${WORDS[s.kind][0]}${s.n ? ` ${s.n}` : ''}`;
  }
  return secs;
}

/** Parts named by the lyrics' own headers (`sec` on the first line of each part), or null when there are none. */
function fromHeaders(sung, duration) {
  if (!sung.some((l) => l.sec)) return null;
  const median_ = median(sung.slice(1).map((l, i) => l.t - sung[i].t));
  const parts = [];
  sung.forEach((l, i) => {
    if (l.sec || !parts.length) parts.push({ kind: l.sec ? kindOf(l.sec) : 'verse', raw: l.sec, start: l.t, end: l.t, last: i });
    else Object.assign(parts.at(-1), { end: l.t, last: i });
  });
  const out = [];
  if (parts[0].start >= 8 && parts[0].kind !== 'intro') out.push({ start: 0, end: parts[0].start, kind: 'intro' });
  parts.forEach((p, i) => {
    const next = parts[i + 1], lastEnd = p.end + Math.min(6, median_ * 1.2);
    if (next && next.start - lastEnd > 14) {
      out.push({ ...p, end: lastEnd }, { start: lastEnd, end: next.start, kind: 'inst' });
    } else out.push({ ...p, end: next ? next.start : lastEnd });
  });
  const tail = out.at(-1);
  if (duration && duration - tail.end > 12 && tail.kind !== 'outro') out.push({ start: tail.end, end: duration, kind: 'outro' });
  else if (duration) tail.end = Math.max(tail.end, duration);
  return name(merge(out));
}

const norm = (s) => s.toLowerCase().replace(/[^\p{L}\p{N} ]+/gu, '').replace(/\s+/g, ' ').trim();
const median = (a) => { const s = [...a].sort((x, y) => x - y); return s.length ? s[s.length >> 1] : 0; };

/** Jaccard similarity of two sets of normalised lines. */
function similarity(a, b) {
  const A = new Set(a), B = new Set(b);
  let n = 0;
  for (const x of A) if (B.has(x)) n++;
  return n / (A.size + B.size - n || 1);
}

const tokens = (s) => new Set(s.split(' ').filter((w) => w.length > 1));
/** Same line sung again: equal text, or nearly (a changed word or two). */
const alike = (a, b) => a.length > 2 && b.length > 2 && (a === b || similarity([...tokens(a)], [...tokens(b)]) >= 0.75);

/** Per line, the id of the group of lines it repeats (a run of >= 2 lines that is sung again), or -1. */
function repeatGroups(texts) {
  const n = texts.length, parent = texts.map((_, i) => i);
  const find = (x) => (parent[x] === x ? x : (parent[x] = find(parent[x])));
  const flagged = new Array(n).fill(false);
  for (let d = 2; d < n; d++) { // d < 2: a line repeating itself or its neighbour is a refrain inside the line, not a chorus
    let run = 0;
    for (let i = 0; i + d <= n; i++) {
      const hit = i + d < n && alike(texts[i], texts[i + d]);
      if (hit) { run++; continue; }
      if (run >= 2 && run <= d) for (let k = i - run; k < i; k++) { flagged[k] = flagged[k + d] = true; parent[find(k + d)] = find(k); }
      run = 0;
    }
  }
  return texts.map((_, i) => (flagged[i] ? find(i) : -1));
}

export function fromLyrics(lines, duration) {
  const sung = lines.filter((l) => l.t != null && l.text);
  const named = fromHeaders(sung, duration);
  if (named) return named;
  if (sung.length < 8) return [];
  const g = repeatGroups(sung.map((l) => norm(l.text)));
  if (!g.some((x) => x >= 0)) return [];
  const gaps = sung.slice(1).map((l, i) => l.t - sung[i].t);
  const limit = Math.max(8, median(gaps) * 2.5);
  // consecutive lines of the same kind (repeated or not) form a section; a long silence also cuts one
  const segs = [];
  sung.forEach((l, i) => {
    const rep = g[i] >= 0, p = segs.at(-1);
    if (p && p.rep === rep && l.t - sung[i - 1].t <= limit) { p.lines.push(i); p.end = l.t; } else segs.push({ rep, lines: [i], start: l.t, end: l.t });
  });
  // repeated segments that share a line group are the same part; the most frequent is the chorus
  const parts = [];
  segs.filter((s) => s.rep).forEach((s) => {
    const ids = new Set(s.lines.map((i) => g[i]));
    const hit = parts.find((p) => [...ids].some((x) => p.ids.has(x)));
    hit ? (hit.segs.push(s), ids.forEach((x) => hit.ids.add(x))) : parts.push({ ids, segs: [s] });
  });
  parts.sort((a, b) => b.segs.length - a.segs.length);
  parts.forEach((p, k) => p.segs.forEach((s) => { s.kind = k === 0 ? 'chorus' : 'refrain'; }));
  let choruses = 0;
  segs.forEach((s, i) => {
    if (s.kind === 'chorus') choruses++;
    if (!s.rep) s.kind = choruses >= 2 && i < segs.length - 1 && segs.slice(i + 1).some((x) => x.kind === 'chorus') ? 'bridge' : 'verse';
  });
  const out = [];
  if (segs[0].start >= 8) out.push({ start: 0, end: segs[0].start, kind: 'intro' });
  segs.forEach((s, i) => {
    const next = segs[i + 1];
    const lastEnd = s.end + Math.min(6, median(gaps) * 1.2);
    if (next && next.start - lastEnd > 14) {
      out.push({ start: s.start, end: lastEnd, kind: s.kind }, { start: lastEnd, end: next.start, kind: 'inst' });
    } else out.push({ start: s.start, end: next ? next.start : lastEnd, kind: s.kind });
  });
  const tail = out.at(-1);
  if (duration && duration - tail.end > 12) out.push({ start: tail.end, end: duration, kind: 'outro' });
  else if (duration) tail.end = Math.max(tail.end, duration);
  return name(merge(out));
}

/** Join neighbours of the same kind (a chorus split by an instrumental stays two) unless the lyrics named them apart. */
function merge(secs) {
  const out = [];
  for (const s of secs) {
    const p = out.at(-1);
    if (p && p.kind === s.kind && !s.raw && !p.raw) p.end = s.end; else out.push({ ...s });
  }
  return out;
}

/**
 * Letter-named parts from the chords alone: bars are cut into blocks, blocks with (nearly) the same chords share a letter,
 * and neighbours with the same letter join. Returns [] unless some progression comes back, so a through-composed song gets none.
 * `bars`: [{ t, key }] one entry per bar (start time, chord at its start).
 */
export function fromChords(bars, duration) {
  if (bars.length < 16) return [];
  const size = bars.length >= 48 ? 8 : 4;
  const blocks = [];
  for (let i = 0; i < bars.length; i += size) blocks.push(bars.slice(i, i + size));
  if (blocks.at(-1).length < size / 2 && blocks.length > 1) blocks.at(-2).push(...blocks.pop());
  const same = (a, b) => {
    let hit = 0;
    for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i].key === b[i].key) hit++;
    return hit / Math.max(a.length, b.length);
  };
  const reps = [];
  const letters = blocks.map((b) => {
    let k = reps.findIndex((r) => same(r, b) >= 0.75);
    if (k < 0) k = reps.push(b) - 1;
    return k;
  });
  if (new Set(letters).size === letters.length || new Set(letters).size === 1) return [];
  const out = [];
  blocks.forEach((b, i) => {
    const raw = String.fromCharCode(65 + (letters[i] % 26));
    const end = i + 1 < blocks.length ? blocks[i + 1][0].t : duration || b.at(-1).t;
    const p = out.at(-1);
    if (p && p.raw === raw) p.end = end; else out.push({ start: i ? b[0].t : 0, end, kind: 'other', raw });
  });
  return name(out);
}
