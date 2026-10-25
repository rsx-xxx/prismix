// Bars and chords for the open track: ruler, chart, fingering diagrams, capo, chart PDF.
import { bestCapo, capoSavings, chordName, chordPaint, keyName, fifthsFor, simplify, voicing, voicingCount } from './theory.js';
import { chartPdf } from './score.js';

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const SVGNS = 'http://www.w3.org/2000/svg';

/** Last index i with arr[i] <= x, or -1. */
function floorIndex(arr, x) {
  let lo = 0, hi = arr.length - 1, r = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= x) { r = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return r;
}

export function createScore({ player, api, notice }) {
  /** @type {null | {id:string, model:string, meta:object, data:null|{structure:object, raw:object}, bars:number[], beats:number[]}} */
  let cur = null;
  let busy = null; // analysis job in flight
  let renderToken = 0;
  let last = { bar: -2, beat: -2, chord: -2 };
  const flag = (k, dflt) => { try { const v = localStorage.getItem(`stems:sc-${k}`); return v === null ? dflt : v === '1'; } catch { return dflt; } };
  const opts = { show: flag('show', true), easy: flag('easy', false), simple: flag('simple', false), useTab: flag('useTab', false) };
  const loadCapoMode = () => { try { return localStorage.getItem('stems:sc-capoMode') || 'off'; } catch { return 'off'; } };
  let capoMode = loadCapoMode(); // 'off' | 'auto' | '1'..'7'
  const alts = new Map(); // chord shape -> which alternative fingering is showing
  const remember = (k) => { try { localStorage.setItem(`stems:sc-${k}`, opts[k] ? '1' : '0'); } catch { /* ignore */ } };

  const shift = () => player.semitones;
  const flats = () => cur?.data && fifthsFor(cur.data.structure.key, shift() - capoFret()) < 0;
  const shown = (c) => (opts.simple ? simplify(c) : c);
  const name = (c) => chordName(shown(c), shift() - capoFret(), flats());
  const capoFret = () => {
    if (!cur?.data || capoMode === 'off') return 0;
    return capoMode === 'auto' ? capoInfo().capo : Number(capoMode);
  };
  /** Smart capo suggestion for the current key and chord set (cached until key/simplify changes). */
  function capoInfo() {
    cur.capo ??= bestCapo(tally().map(({ c, len }) => ({ c, w: len })), shift(), opts.simple);
    return cur.capo;
  }
  /** Tint a node with the colour of chord c (CSS reads --h, --l, --c). */
  const paint = (node, c) => {
    const p = chordPaint(shown(c), shift());
    node.style.setProperty('--h', p.hue);
    node.style.setProperty('--l', p.l);
    node.style.setProperty('--c', p.chroma);
    node.classList.add('chord');
    return node;
  };
  const shapeKey = (c) => { const x = shown(c); return `${(x.r + shift() - capoFret() + 120) % 12}:${x.q}`; };
  const beatSec = (b) => {
    const beats = cur.beats;
    if (b <= 0) return beats[0] + b * (beats[1] - beats[0]);
    if (b >= beats.length - 1) return beats.at(-1) + (b - beats.length + 1) * (beats.at(-1) - beats.at(-2));
    const i = Math.floor(b);
    return beats[i] + (beats[i + 1] - beats[i]) * (b - i);
  };

  // ── lifecycle ──
  async function open(id, model, meta, token, stillCurrent) {
    close();
    cur = { id, model, meta, data: null, bars: [], beats: [] };
    $('score').hidden = false;
    const get = async (url) => {
      const r = await fetch(url);
      return r.ok ? r.json() : null;
    };
    const [structure, tabs] = await Promise.all([get(`/media/${id}/structure.json`), get(`/media/${id}/tabs.json`)]);
    if (!stillCurrent(token) || !cur || cur.id !== id) return;
    cur.tabs = tabs?.marks ? tabs : null;
    if (structure) setData(structure);
    renderAll();
  }

  function close() {
    renderToken++;
    cur = null;
    last = { bar: -2, beat: -2, chord: -2 };
    $('score').hidden = true;
    $('ruler').replaceChildren();
    $('ruler').hidden = true;
    $('ctl-ruler').hidden = true;
    $('where').textContent = '';
    $('sc-body').replaceChildren();
  }

  /** Effective structure: detected chords, with the tab's chords swapped in where they differ when asked. */
  function applyTabs() {
    const { raw } = cur.data, marks = cur.tabs?.marks;
    cur.data.structure = !(opts.useTab && marks) ? raw : {
      ...raw, chords: raw.chords.map((c, i) => (marks[i] && !marks[i].ok ? { ...c, r: marks[i].r, q: marks[i].q } : c)),
    };
    cur.capo = null;
  }

  function setData(structure) {
    cur.data = { structure, raw: structure };
    applyTabs();
    cur.beats = structure.beats;
    cur.bars = structure.bars.map((b) => beatSec(b));
  }

  // ── analysis job ──
  async function analyze() {
    if (!cur || busy) return;
    const { id, model } = cur;
    const body = $('sc-body');
    const bar = el('i');
    const msg = el('span', { className: 'muted', textContent: 'Queued' });
    body.replaceChildren(el('div', { className: 'sc-cta' }, el('div', { className: 'bar' }, bar), msg));
    try {
      const r = await api('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ videoId: id, model }) });
      if (!r.cached) {
        await new Promise((ok, fail) => {
          const es = new EventSource(`/api/jobs/${r.job.id}/events`);
          busy = es;
          es.onmessage = (ev) => {
            const j = JSON.parse(ev.data);
            bar.style.width = `${Math.round(j.progress * 100)}%`;
            msg.textContent = j.message || j.stage;
            if (j.stage === 'error') { es.close(); fail(new Error(j.message || 'Analysis failed')); }
            if (j.stage === 'done') { es.close(); ok(); }
          };
          es.onerror = () => { if (es.readyState === EventSource.CLOSED) fail(new Error('Lost connection to the server')); };
        });
      }
      busy = null;
      if (cur?.id === id && cur.model === model) await open(id, model, cur.meta, 0, () => true);
    } catch (err) {
      busy?.close();
      busy = null;
      notice(err.message);
      if (cur) renderAll();
    }
  }

  // ── ruler above the waveforms ──
  function renderRuler() {
    const ruler = $('ruler');
    const on = !!cur?.data && player.duration > 0;
    ruler.hidden = $('ctl-ruler').hidden = !on;
    if (!on) return;
    const w = $('lane-col').clientWidth;
    if (!w) return;
    const dur = player.duration, { structure } = cur.data;
    const px = (t) => (t / dur) * w;
    const avgBar = dur / Math.max(1, cur.bars.length);
    let every = 1;
    while (px(avgBar) * every < 30) every *= 2;
    const kids = [];
    cur.bars.forEach((t, i) => {
      const tick = el('i', { className: 'bar-tick' + (i % every ? ' minor' : '') });
      tick.style.left = `${(t / dur) * 100}%`;
      if (!(i % every)) tick.append(el('b', { textContent: String(i + (cur.data.structure.bars[0] > 0 ? 0 : 1)) }));
      kids.push(tick);
    });
    let end = -1;
    for (const [ci, c] of structure.chords.entries()) {
      const t = beatSec(c.b), x = px(t), label = name(c);
      if (x < end + 3) continue;
      const s = el('span', { className: 'ruler-chord', textContent: label });
      s.dataset.ci = String(ci);
      paint(s, c);
      tabMark(s, ci);
      s.style.left = `${(t / dur) * 100}%`;
      kids.push(s);
      end = x + label.length * 7.5 + 8;
    }
    ruler.replaceChildren(...kids);
  }

  const barLoop = (a, b) => player.setLoop(cur.bars[a], b + 1 < cur.bars.length ? cur.bars[b + 1] : player.duration);
  let anchor = null;
  $('ruler').addEventListener('pointerdown', (e) => {
    if (!cur?.data) return;
    e.stopPropagation();
    const r = $('ruler').getBoundingClientRect();
    const t = ((e.clientX - r.left) / r.width) * player.duration;
    const i = Math.max(0, floorIndex(cur.bars, t));
    if (e.shiftKey) {
      if (anchor === null) { anchor = i; barLoop(i, i); } else { barLoop(Math.min(anchor, i), Math.max(anchor, i)); anchor = null; }
    } else {
      anchor = null;
      player.seek(cur.bars[i]);
    }
  });

  // ── transport readout ──
  function frame(pos) {
    if (!cur?.data) return;
    const bi = floorIndex(cur.bars, pos);
    const beat = floorIndex(cur.beats, pos);
    const { structure } = cur.data;
    const chord = structure.chords.findLastIndex((c) => c.b <= Math.max(0, beat));
    if (bi !== last.bar) markBar(bi);
    if (chord !== last.chord) markChord(chord);
    if (bi === last.bar && beat === last.beat && chord === last.chord) return;
    last = { bar: bi, beat, chord };
    if (bi < 0) { $('where').textContent = ''; return; }
    const inBar = Math.floor(beat - structure.bars[bi]) + 1;
    const bar = bi + (structure.bars[0] > 0 ? 0 : 1);
    $('where').replaceChildren(el('span', { textContent: `Bar ${bar} · ${inBar}` }), chord >= 0 ? el('b', { textContent: name(structure.chords[chord]) }) : '');
  }
  // Follow playback only until the user scrolls by hand; play or seek picks it up again.
  let follow = true;
  const setFollow = (on) => { follow = on; $('sc-follow').checked = on; };
  ['wheel', 'touchmove'].forEach((t) => window.addEventListener(t, () => setFollow(false), { passive: true }));
  $('sc-follow').addEventListener('change', (e) => { follow = e.target.checked; if (follow) refresh(); });
  player.addEventListener('seek', () => setFollow(true));
  let wasPlaying = false;
  player.addEventListener('state', () => { if (player.playing && !wasPlaying) setFollow(true); wasPlaying = player.playing; });
  function markBar(bi) {
    const cells = $('sc-body').querySelectorAll('.cell');
    cells.forEach((c) => c.classList.toggle('now', Number(c.dataset.bar) === bi));
    const now = $('sc-body').querySelector('.cell.now');
    if (now && follow && player.playing) now.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
  /** Light up the chord being played: its slot in the ruler and chart, and its fingering diagram. */
  function markChord(ci) {
    const root = $('studio');
    root.querySelectorAll('.ruler-chord.on, .chart .on, .diagrams .on').forEach((n) => n.classList.remove('on'));
    if (ci < 0) return;
    // the ruler skips labels that would overlap, so fall back to the nearest earlier one
    const rulerHit = [...$('ruler').querySelectorAll('.ruler-chord')].findLast((n) => Number(n.dataset.ci) <= ci);
    rulerHit?.classList.add('on');
    root.querySelectorAll(`.chart [data-ci="${ci}"]`).forEach((n) => n.classList.add('on'));
    const fig = root.querySelector(`.diagrams [data-shape="${shapeKey(cur.data.structure.chords[ci])}"]`);
    fig?.classList.add('on');
    const chip = root.querySelector('.chart .cell.now .on');
    if (chip && follow && player.playing) chip.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }
  /** Flag a chord as agreeing with, or differing from, the aligned tab. */
  function tabMark(node, ci) {
    const m = cur.tabs?.marks?.[ci];
    if (!m) return;
    node.classList.add(m.ok ? 'tab-ok' : 'tab-diff');
    if (!m.ok) node.title = opts.useTab ? `Detected ${chordName(cur.data.raw.chords[ci], shift() - capoFret(), flats())}; tab says ${m.name}` : `Tab says ${m.name}`;
  }
  const refresh = () => { last = { bar: -2, beat: -2, chord: -2 }; frame(player.position); };

  // ── panel ──
  function renderAll() {
    if (!cur) return;
    renderTabs();
    renderRuler();
    renderBody();
    refresh();
  }

  function renderTabs() {
    const has = !!cur.data;
    $('sc-show-row').hidden = $('sc-pdf').hidden = !has;
    $('sc-show').checked = opts.show;
    $('studio').classList.toggle('no-chords', !opts.show);
  }

  function renderBody() {
    if (!cur) return;
    const body = $('sc-body');
    renderToken++;
    if (!cur.data) {
      $('sc-meta').textContent = '';
      const go = el('button', { className: 'btn primary', type: 'button', textContent: 'Analyze this track' });
      go.addEventListener('click', analyze);
      body.replaceChildren(el('div', { className: 'sc-cta' },
        el('h3', { textContent: 'Bars and chords' }),
        el('p', { className: 'muted', textContent: 'Detects the beat, bars, key and chords. Takes about 15 seconds.' }),
        go));
      return;
    }
    const { structure } = cur.data;
    const k = structure.key, s = shift();
    $('sc-meta').textContent = [
      k && `${keyName(k, 0)}${s ? ` → ${keyName(k, s)}` : ''}`,
      capoFret() > 0 && `Capo ${capoFret()}`,
      `${Math.round(structure.bpm * player.tempo)} BPM`, `${structure.timeSig[0]}/${structure.timeSig[1]}`, `${structure.bars.length} bars`,
    ].filter(Boolean).join(' · ');
    renderChart(body);
  }

  const title = () => cur.meta.title;

  // chord chart: one cell per bar
  function chartRows() {
    const { structure } = cur.data;
    const n = structure.bars.length;
    const rows = [];
    for (let i = 0; i < n; i++) {
      const a = structure.bars[i], b = i + 1 < n ? structure.bars[i + 1] : Infinity;
      const inside = structure.chords.map((c, ci) => ({ c, ci })).filter(({ c }) => c.b >= a && c.b < b);
      const ci = structure.chords.findLastIndex((c) => c.b < a);
      const list = [];
      if ((!inside.length || inside[0].c.b > a) && ci >= 0) list.push({ c: structure.chords[ci], ci, carried: true });
      inside.forEach(({ c, ci: i2 }) => list.push({ c, ci: i2, carried: false }));
      rows.push(list);
    }
    return rows;
  }

  function renderChart(body) {
    const { structure } = cur.data;
    const first = structure.bars[0] > 0 ? 0 : 1;
    const grid = el('div', { className: 'chart' }, ...chartRows().map((list, i) => {
      const cell = el('button', { type: 'button', className: 'cell', title: 'Play from this bar' },
        el('small', { textContent: String(i + first) }),
        ...list.map(({ c, ci, carried }) => { const sp = el('span', { className: carried ? 'carried' : '', textContent: name(c) }); sp.dataset.ci = String(ci); paint(sp, c); tabMark(sp, ci); return sp; }));
      cell.dataset.bar = String(i);
      cell.addEventListener('click', (e) => (e.shiftKey ? (anchor === null ? (anchor = i, barLoop(i, i)) : (barLoop(Math.min(anchor, i), Math.max(anchor, i)), anchor = null)) : player.seek(cur.bars[i])));
      return cell;
    }));
    body.replaceChildren(tabSource(), fingerOpts(), diagrams(), grid);
    markBar(last.bar);
    markChord(last.chord);
  }

  /** Weighted distinct chords of the song (by time played), most played first. */
  function tally() {
    const { structure } = cur.data;
    const t = new Map();
    structure.chords.forEach((c, i) => {
      const len = (structure.chords[i + 1]?.b ?? c.b + c.d) - c.b;
      const x = shown(c), key = `${(x.r + shift() + 120) % 12}:${x.q}`;
      const e = t.get(key) || { c: x, len: 0 };
      e.len += len;
      t.set(key, e);
    });
    return [...t.values()].sort((p, q) => q.len - p.len);
  }

  function fingerOpts() {
    const toggle = (key, text, hint) => {
      const input = el('input', { type: 'checkbox', checked: opts[key] });
      input.addEventListener('change', () => { opts[key] = input.checked; remember(key); if (key === 'simple') cur.capo = null; renderAll(); });
      return el('label', { className: 'switch', title: hint }, input, el('span'), text);
    };
    const row = el('div', { className: 'fing-opts' },
      toggle('easy', 'Easiest fingering', 'Pick the voicing with the fewest fingers and no stretch'),
      toggle('simple', 'Simpler chords', 'Play 7ths and 6ths as plain major/minor'));
    row.append(capoPicker());
    return row;
  }

  function capoPicker() {
    const info = capoInfo();
    const weighted = tally().map(({ c, len }) => ({ c, w: len }));
    const saved = info.all ?? capoSavings(weighted, shift(), opts.simple);
    const sel = el('select', { ariaLabel: 'Capo' });
    const add = (value, label) => sel.append(el('option', { value, textContent: label, selected: capoMode === value }));
    add('off', 'Capo: off');
    add('auto', info.capo ? `Capo: auto (fret ${info.capo})` : 'Capo: auto (none needed)');
    for (let k = 1; k <= 7; k++) add(String(k), `Capo ${k}${saved[k] > 0.02 ? ` · ${Math.round(saved[k] * 100)}% easier` : saved[k] < -0.02 ? ' · harder' : ''}`);
    sel.addEventListener('change', () => {
      capoMode = sel.value;
      try { localStorage.setItem('stems:sc-capoMode', capoMode); } catch { /* ignore */ }
      renderAll();
    });
    const box = el('span', { className: 'capo-pick' }, sel);
    const active = capoFret();
    if (active) {
      box.append(el('span', { className: 'muted', textContent: `Play these shapes with the capo on fret ${active}; it sounds in the original key` }));
    } else if (info.capo && capoMode === 'off') {
      const use = el('button', { type: 'button', className: 'btn small ghost', textContent: `Capo ${info.capo} would be ${Math.round(info.saved * 100)}% easier. Use it` });
      use.addEventListener('click', () => { capoMode = String(info.capo); try { localStorage.setItem('stems:sc-capoMode', capoMode); } catch { /* ignore */ } renderAll(); });
      box.append(use);
    }
    return box;
  }

  /** Online chord sheet (Songsterr): look it up, show how well it agrees, link to it. */
  function tabSource() {
    const box = el('div', { className: 'tab-src' });
    const t = cur.tabs;
    if (t) {
      const link = el('a', { href: t.url, target: '_blank', rel: 'noopener noreferrer', textContent: `${t.artist} – ${t.title} on Songsterr ↗` });
      const diff = t.marks.filter((m) => m && !m.ok).length;
      const sw = el('input', { type: 'checkbox', checked: opts.useTab });
      sw.addEventListener('change', () => { opts.useTab = sw.checked; remember('useTab'); applyTabs(); renderAll(); });
      box.append(
        el('span', {}, 'Chords aligned with ', link, ` · ${Math.round(t.similarity * 100)}% match`,
          t.transpose ? ` · tab is ${Math.abs(t.transpose)} semitone${Math.abs(t.transpose) > 1 ? 's' : ''} ${t.transpose > 0 ? 'lower' : 'higher'}` : ''),
        el('span', { className: 'tab-legend muted' }, el('i', { className: 'tab-ok' }, 'agrees'), el('i', { className: 'tab-diff' }, `differs (${diff})`)),
        el('label', { className: 'switch', title: 'Replace chords that differ with the tab\'s' }, sw, el('span'), 'Use tab chords'));
      return box;
    }
    const go = el('button', { type: 'button', className: 'btn small ghost', textContent: 'Check against online chords' });
    const msg = el('span', { className: 'muted', textContent: 'Finds a Songsterr chord sheet and marks where it agrees' });
    const paste = el('input', { type: 'url', placeholder: 'or paste a Songsterr chords link', hidden: true, ariaLabel: 'Songsterr link' });
    const run = async (url) => {
      go.disabled = true;
      msg.textContent = 'Searching…';
      try {
        cur.tabs = await api('/api/tabs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ videoId: cur.id, url: url || null }) });
        applyTabs();
        renderAll();
      } catch (err) {
        msg.textContent = err.message;
        paste.hidden = false;
        go.disabled = false;
      }
    };
    go.addEventListener('click', () => run(paste.value.trim() || null));
    paste.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(paste.value.trim()); });
    box.append(go, msg, paste);
    return box;
  }

  /** Fingering diagrams for the most-played chords; click one to try another fingering. */
  function diagrams() {
    const wrap = el('div', { className: 'diagrams' });
    const cap = capoFret();
    for (const { c } of tally().slice(0, 10)) {
      const key = shapeKey(c);
      const v = voicing(c, shift() - cap, { easy: opts.easy, alt: alts.get(key) || 0 });
      if (!v) continue;
      const n = voicingCount(c, shift() - cap);
      const fig = el('figure', { title: n > 1 ? 'Click for another fingering' : '' }, diagram(v), paint(el('figcaption', { textContent: name(c) }), c));
      fig.dataset.shape = key;
      if (n > 1) {
        fig.classList.add('alt');
        fig.addEventListener('click', () => { alts.set(key, ((alts.get(key) || 0) + 1) % Math.min(n, 12)); renderBody(); });
      }
      wrap.append(fig);
    }
    return wrap;
  }

  function diagram({ frets, base, fingers, barre }) {
    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', '0 0 56 74');
    svg.setAttribute('width', '64');
    const add = (tag, attrs) => {
      const n = document.createElementNS(SVGNS, tag);
      Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
      svg.append(n);
      return n;
    };
    const X = (s) => 8 + s * 8, Y = (f) => 14 + f * 12;
    for (let f = 0; f <= 4; f++) add('line', { x1: X(0), x2: X(5), y1: Y(f), y2: Y(f), stroke: 'currentColor', 'stroke-width': f === 0 && base === 1 ? 3 : 1, opacity: f === 0 && base === 1 ? 1 : 0.5 });
    for (let s = 0; s < 6; s++) add('line', { x1: X(s), x2: X(s), y1: Y(0), y2: Y(4), stroke: 'currentColor', 'stroke-width': 1, opacity: 0.5 });
    if (barre) {
      const y = Y(barre.fret - base + 1) - 6;
      add('rect', { x: X(barre.from) - 3.6, y: y - 3.6, width: X(barre.to) - X(barre.from) + 7.2, height: 7.2, rx: 3.6, fill: 'currentColor' });
    }
    frets.forEach((f, s) => {
      if (f < 0) { const t = add('text', { x: X(s), y: 9, 'text-anchor': 'middle', 'font-size': 9, fill: 'currentColor', opacity: 0.6 }); t.textContent = '×'; }
      else if (f === 0) add('circle', { cx: X(s), cy: 6, r: 2.4, fill: 'none', stroke: 'currentColor', 'stroke-width': 1 });
      else {
        const cy = Y(f - base + 1) - 6;
        if (!(barre && f === barre.fret)) add('circle', { cx: X(s), cy, r: 3.8, fill: 'currentColor' });
        const t = add('text', { x: X(s), y: cy + 2.6, 'text-anchor': 'middle', 'font-size': 7, 'font-weight': 700, class: 'finger' });
        t.textContent = String(fingers[s]);
      }
    });
    if (base > 1) { const t = add('text', { x: 0, y: Y(1) - 2, 'font-size': 8, fill: 'currentColor', opacity: 0.7 }); t.textContent = String(base); }
    return svg;
  }

  // ── PDF ──
  const save = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    el('a', { href: url, download: filename }).click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
  const fileBase = () => `${title().replace(/[\\/:*?"<>|]+/g, ' ').trim()}${shift() ? ` (${shift() > 0 ? '+' : ''}${shift()})` : ''}`;

  async function pdf() {
    if (!cur?.data) return;
    const btn = $('sc-pdf');
    const text = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Preparing…';
    try {
      const { structure } = cur.data;
      save(await chartPdf({
        title: title(), sub: $('sc-meta').textContent,
        bars: chartRows().map((l) => l.map(({ c }) => name(c))), first: structure.bars[0] > 0 ? 0 : 1,
      }), `${fileBase()} - chords.pdf`);
    } catch (err) {
      notice(err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = text;
    }
  }

  $('sc-pdf').addEventListener('click', pdf);
  $('sc-show').addEventListener('change', (e) => { opts.show = e.target.checked; remember('show'); renderAll(); });

  // re-render when the key changes (debounced: dragging the slider fires many)
  let tuneTimer, lastShift = 0, lastTempo = 1;
  function tuned() {
    if (!cur?.data) return;
    if (shift() === lastShift && player.tempo === lastTempo) return;
    lastShift = shift();
    lastTempo = player.tempo;
    clearTimeout(tuneTimer);
    tuneTimer = setTimeout(() => { if (cur) cur.capo = null; renderAll(); }, 250);
  }
  new ResizeObserver(() => { if (cur?.data) requestAnimationFrame(renderRuler); }).observe($('lane-col'));

  return {
    open, close, frame, tuned, refreshRuler: renderRuler,
    syncSeed: () => { lastShift = shift(); lastTempo = player.tempo; },
  };
}
