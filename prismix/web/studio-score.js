// Bars and chords for the open track: ruler, chart, fingering diagrams, capo, chart PDF.
import { GUITAR_TUNINGS, bestCapo, capoSavings, chordName, chordPaint, keyName, shapeTuning, tuningById, tuningFromPitches, tuningSavings, useFlats, simplify, voicing, voicingCount } from './theory.js';
import { chartPdf } from './score.js';
import { cancelGlide, glide, offsetIn, lockstep, anchorAt } from './scroll.js';

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

export function createScore({ player, api, notice, onSetup }) {
  /** @type {null | {id:string, model:string, meta:object, data:null|{structure:object, raw:object}, bars:number[], beats:number[]}} */
  let cur = null;
  let busy = null; // analysis job in flight
  let renderToken = 0;
  let last = { bar: -2, beat: -2, chord: -2 };
  const flag = (k, dflt) => { try { const v = localStorage.getItem(`stems:sc-${k}`); return v === null ? dflt : v === '1'; } catch { return dflt; } };
  const opts = { easy: flag('easy', false), simple: flag('simple', false), useTab: flag('useTab', true), hover: flag('hover', true) };
  const loadCapoMode = () => { try { return localStorage.getItem('stems:sc-capoMode') || 'off'; } catch { return 'off'; } };
  let capoMode = loadCapoMode(); // 'off' | 'auto' | '1'..'7'
  const alts = new Map(); // chord shape -> which alternative fingering is showing
  const remember = (k) => { try { localStorage.setItem(`stems:sc-${k}`, opts[k] ? '1' : '0'); } catch { /* ignore */ } };

  const shift = () => player.semitones + player.chordKey;
  let tuning = tuningById('std'); // how the guitar is tuned; shapes are read through it
  const shape = () => shapeTuning(tuning);
  /** Semitones the written shapes sit from the sounding chords: capo and a down-tuned guitar both lower what you fret. */
  const shapeShift = () => shift() - capoFret() + shape().down;
  const flats = () => cur?.data && useFlats(cur.data.structure.key, shapeShift());
  const shown = (c) => (opts.simple ? simplify(c) : c);
  const name = (c) => chordName(shown(c), shapeShift(), flats());
  const capoFret = () => {
    if (!cur?.data || capoMode === 'off') return 0;
    return capoMode === 'auto' ? capoInfo().capo : Number(capoMode);
  };
  /** Smart capo suggestion for the current key and chord set (cached until key/simplify changes). */
  function capoInfo() {
    cur.capo ??= bestCapo(tally().map(({ c, len }) => ({ c, w: len })), shift() + shape().down, opts.simple, shape().open);
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
  const shapeKey = (c) => { const x = shown(c); return `${tuning.id}|${(x.r + shapeShift() + 120) % 12}:${x.q}`; };
  const beatSec = (b) => {
    const beats = cur.beats;
    if (b <= 0) return beats[0] + b * (beats[1] - beats[0]);
    if (b >= beats.length - 1) return beats.at(-1) + (b - beats.length + 1) * (beats.at(-1) - beats.at(-2));
    const i = Math.floor(b);
    return beats[i] + (beats[i + 1] - beats[i]) * (b - i);
  };

  // ── lifecycle ──
  const get = async (url) => {
    const r = await fetch(url);
    return r.ok ? r.json() : null;
  };
  async function open(id, model, meta, token, stillCurrent) {
    close();
    cur = { id, model, meta, data: null, bars: [], beats: [] };
    $('score').hidden = false;
    const [structure, tabs] = await Promise.all([get(`/media/${id}/structure.json`), get(`/media/${id}/tabs.json`)]);
    if (!stillCurrent(token) || !cur || cur.id !== id) return;
    cur.tabs = tabs?.marks ? tabs : null;
    if (structure) setData(structure);
    renderAll();
    if (structure && !cur.tabs && !lookedUp.has(id)) autoTabs(id);
    tabTuning(id);
  }

  /** A six-string guitar track in the song's tab tells how it is tuned; offered in the tuning picker. */
  async function tabTuning(id) {
    try {
      const d = await get(`/api/tabdata/${id}`);
      if (!cur || cur.id !== id) return;
      const g = Object.values(d?.tracks ?? {}).find((t) => t.kind !== 'bass' && t.kind !== 'drums' && t.tuning?.length === 6 && t.tuning[0] > 36);
      const t = g && tuningFromPitches(g.tuning);
      if (t && t.id !== 'std') { cur.tabTuning = t; if (cur.data) renderAll(); }
    } catch { /* no tab: nothing to suggest */ }
  }

  /** Online chords are looked up by themselves, once per track per session; with "Use tab chords" on they replace the detected ones. */
  const lookedUp = new Set();
  async function autoTabs(id) {
    lookedUp.add(id);
    try {
      const t = await api('/api/tabs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ videoId: id, url: null }) });
      if (!cur || cur.id !== id || cur.tabs) return;
      cur.tabs = t;
      applyTabs();
      renderAll();
    } catch { /* no chord sheet online: the detected chords stay */ }
  }

  window.addEventListener('accidentals', () => { if (cur?.data) renderAll(); });

  /** The chords source changed: drop the sheet in use and look the new one up. */
  function retab(id) {
    if (!cur || cur.id !== id) return;
    cur.tabs = null;
    applyTabs();
    renderAll();
    lookedUp.delete(id);
    autoTabs(id);
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
    $('ahead').hidden = true;
    $('ahead').replaceChildren();
    ahead = [];
    $('sc-body').replaceChildren();
    $('fing').replaceChildren();
    $('fing').hidden = true;
    fingChip.hidden = true;
    document.dispatchEvent(new Event('chords:change'));
  }

  /** Chords as the reader sees them (transposed, simplified, repeats merged) with their times, for the lyrics to anchor. */
  function timeline() {
    if (!cur?.data) return [];
    const out = [];
    cur.data.structure.chords.forEach((c, ci) => {
      const label = name(c);
      if (out.length && out.at(-1).label === label) return;
      const p = chordPaint(shown(c), shift());
      out.push({ t: beatSec(c.b), ci, label, h: p.hue, l: p.l, c: p.chroma });
    });
    return out;
  }

  /** One entry per bar: its start time and the chord sounding at its start (untransposed, for finding repeated progressions). */
  function barChords() {
    if (!cur?.data) return [];
    const { chords, bars } = cur.data.structure;
    let k = 0;
    return bars.map((b, i) => {
      while (k + 1 < chords.length && chords[k + 1].b <= b) k++;
      const c = chords[k];
      return { t: cur.bars[i], key: c ? `${c.r}${c.q}` : '' };
    });
  }

  /** Chord names sounding in bar j with where each starts (0..1 of the bar), as shown (transposed, capo applied). */
  function chordsIn(j) {
    if (!cur?.data) return [];
    const { chords, bars } = cur.data.structure;
    if (j < 0 || j >= bars.length) return [];
    const b0 = bars[j], b1 = j + 1 < bars.length ? bars[j + 1] : b0 + (j ? b0 - bars[j - 1] : 4);
    const k = chords.findLastIndex((c) => c.b <= b0);
    const out = k >= 0 ? [{ f: 0, name: name(chords[k]) }] : [];
    for (const c of chords) {
      if (c.b <= b0 || c.b >= b1) continue;
      const n = name(c);
      if (n !== out.at(-1)?.name) out.push({ f: (c.b - b0) / (b1 - b0), name: n });
    }
    return out;
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
  async function analyze(force = false) {
    if (!cur || busy) return;
    const { id, model } = cur;
    const body = $('sc-body');
    const bar = el('i');
    const msg = el('span', { className: 'muted', textContent: 'Queued' });
    body.replaceChildren(el('div', { className: 'sc-cta' }, el('div', { className: 'bar' }, bar), msg));
    try {
      const r = await api('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ videoId: id, model, force: force === true }) });
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
    const dur = player.duration;
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
    if (beat !== last.beat) countdown(beat);
    last = { bar: bi, beat, chord, beatPos: beat };
    if (bi < 0) { $('where').textContent = ''; return; }
    const inBar = Math.floor(beat - structure.bars[bi]) + 1;
    const bar = bi + (structure.bars[0] > 0 ? 0 : 1);
    $('where').replaceChildren(el('span', { textContent: `Bar ${bar} · ${inBar}` }));
  }
  // Follow playback only until the user scrolls by hand; play or seek picks it up again.
  let follow = true;
  const setFollow = (on) => { follow = on; };
  // only scrolling the chart by hand stops following; moving around the page never does
  ['wheel', 'touchmove'].forEach((t) => $('sc-body').addEventListener(t, (e) => { if (e.target.closest('.chart')) { setFollow(false); cancelGlide(e.target.closest('.chart')); } }, { passive: true }));
  player.addEventListener('seek', () => setFollow(true));
  let wasPlaying = false;
  player.addEventListener('state', () => { if (player.playing && !wasPlaying) setFollow(true); wasPlaying = player.playing; });
  let holdUntil = 0;
  function markBar(bi) {
    const cells = $('sc-body').querySelectorAll('.cell');
    cells.forEach((c) => c.classList.toggle('now', Number(c.dataset.bar) === bi));
    const now = $('sc-body').querySelector('.cell.now'), chart = now?.closest('.chart');
    if (!now || lockstep.on || !follow || Date.now() < holdUntil || chart.offsetParent === null) return;
    // keep the current bar and the next two rows inside the chart, moving only when they would fall out
    const row = now.offsetHeight + 8, rel = offsetIn(chart, now);
    if (rel >= 0 && rel + row * 3 <= chart.clientHeight) return;
    glide(chart, chart.scrollTop + rel - row);
  }
  /** Light up the chord being played: its slot in the chart and its fingering diagram. */
  function markChord(ci) {
    renderAhead(ci);
    const root = $('studio');
    root.querySelectorAll('.chart .on, .ly-lines .ch.on, #sing .ch.on').forEach((n) => n.classList.remove('on'));
    if (ci < 0) return;
    root.querySelectorAll(`.chart [data-ci="${ci}"]`).forEach((n) => n.classList.add('on'));
    // the lyrics show merged chords only: light the latest one at or before this chord
    const shownCis = [...root.querySelectorAll('.ly-lines [data-ci], #sing [data-ci]')].map((n) => Number(n.dataset.ci));
    const lyHit = shownCis.findLast((x) => x <= ci);
    if (lyHit !== undefined) root.querySelectorAll(`.ly-lines [data-ci="${lyHit}"], #sing [data-ci="${lyHit}"]`).forEach((n) => n.classList.add('on'));
  }
  // ── "now and next" strip in the dock: always on screen, so upcoming chords never hide below the fold ──
  const AHEAD = 8;
  let ahead = [];
  function renderAhead(ci) {
    paintNow(ci);
    const box = $('ahead');
    const chords = cur?.data?.structure.chords;
    box.hidden = !chords?.length;
    if (box.hidden) { ahead = []; return; }
    const first = Math.max(0, ci);
    const items = [];
    for (let i = first; i < chords.length && items.length < AHEAD; i++) {
      if (items.length && name(chords[i]) === name(chords[i - 1])) continue; // simplified chords can repeat
      const b = el('button', { type: 'button', className: i === ci ? 'cur' : '', title: 'Jump to this chord' },
        el('b', { textContent: name(chords[i]) }), el('small'));
      paint(b, chords[i]);
      b.addEventListener('click', () => player.seek(beatSec(chords[i].b)));
      items.push({ c: chords[i], node: b, small: b.lastChild });
    }
    ahead = items;
    box.replaceChildren(...items.map((x) => x.node));
    countdown(last.beatPos ?? 0);
  }
  /** Beats until each upcoming chord, refreshed on every beat. */
  function countdown(beat) {
    ahead.forEach(({ c, small }, k) => { small.textContent = k === 0 ? '' : `in ${Math.max(1, Math.round(c.b - beat))}`; });
  }
  /** Flag a chord as agreeing with, or differing from, the aligned tab. */
  function tabMark(node, ci) {
    const m = cur.tabs?.marks?.[ci];
    if (!m) return;
    node.classList.add(m.ok ? 'tab-ok' : 'tab-diff');
    if (!m.ok) node.title = opts.useTab ? `Detected ${chordName(cur.data.raw.chords[ci], shapeShift(), flats())}; tab says ${m.name}` : `Tab says ${m.name}`;
  }
  document.addEventListener('blocks:change', () => { if (cur?.data) refresh(); });
  document.addEventListener('lyrics:line', () => { if (cur?.data) markChord(last.chord); });
  const refresh = () => { last = { bar: -2, beat: -2, chord: -2 }; frame(player.position); };

  // ── panel ──
  function renderAll() {
    if (!cur) return;
    renderTabs();
    renderRuler();
    renderBody();
    refresh();
    document.dispatchEvent(new Event('chords:change'));
  }

  function renderTabs() {
    const has = !!cur.data;
    $('sc-pdf').hidden = !has;
  }

  let metaText = '';
  function renderBody() {
    if (!cur) return;
    const body = $('sc-body');
    renderToken++;
    if (!cur.data) {
      $('sc-meta').textContent = '';
      const go = el('button', { className: 'btn primary', type: 'button', textContent: 'Analyze this track' });
      go.addEventListener('click', () => analyze());
      body.replaceChildren(el('div', { className: 'sc-cta' },
        el('h3', { textContent: 'Bars and chords' }),
        el('p', { className: 'muted', textContent: 'Detects the beat, bars, key and chords. Takes about 15 seconds.' }),
        go));
      return;
    }
    const { structure } = cur.data;
    const k = structure.key, s = shift();
    const parts = [
      k && `${keyName(k, 0)}${s ? ` → ${keyName(k, s)}` : ''}`,
      capoFret() > 0 && `Capo ${capoFret()}`,
      `${Math.round(structure.bpm * player.tempo)} BPM`, `${structure.timeSig[0]}/${structure.timeSig[1]}`,
    ].filter(Boolean);
    metaText = parts.join(' · ');
    $('sc-meta').replaceChildren(...parts.map((t, i) => el('span', { className: i === 0 && k ? 'pill key' : 'pill', textContent: t })))
    const src = cur.tabs;
    if (opts.useTab && src?.url) { // say where the chord names come from, with the page itself
      $('sc-meta').append(el('a', { className: 'pill', href: src.url, target: '_blank', rel: 'noopener', title: 'The chord sheet these chords are matched to', textContent: `Chords: ${[src.artist, src.title].filter(Boolean).join(' - ') || 'Songsterr'} ↗` }));
    }
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

  // lyric lines by time, so each bar can sit in the row of the line being sung
  let lyricLines = null;
  const wideLayout = () => matchMedia('(min-width: 900px)').matches && !$('lyrics').hidden && !$('lyrics').classList.contains('off');
  const lyricKids = () => [...document.querySelectorAll('#lyrics .ly-lines > *')];
  const alignedMode = () => !!lyricLines?.synced && wideLayout() && lyricKids().length >= lyricLines.lines.length + 2;

  /** Row index per bar: the last row (intro, sung line, ♪ break, outro) that has started by the middle of the bar. */
  function rowOfBar(i) {
    const bars = cur.bars;
    const a = bars[i], b = bars[i + 1] ?? a + 2;
    if (a == null) return 0;
    const mid = (a + b) / 2;
    let row = 0; // intro
    lyricKids().forEach((k, r) => { if (k.dataset.t != null && Number(k.dataset.t) <= mid) row = r; });
    return row;
  }

  function alignRows() {
    const chart = $('sc-body').querySelector('.chart.aligned');
    if (!chart || chart.offsetParent === null) return; // swapped out for the tab, which lays the lyric rows out itself
    const kids = lyricKids(), rows = [...chart.children];
    kids.forEach((k, i) => { k.style.minHeight = ''; k.style.paddingBlock = ''; if (rows[i]) rows[i].style.minHeight = ''; });
    kids.forEach((k, i) => {
      const r = rows[i];
      if (!r) return;
      r.hidden = k.offsetParent === null;
      if (r.hidden || !k.classList.contains('ly-line') || k.classList.contains('gap')) return; // intro, ♪ breaks and outro keep their own height
      // a line whose bars run far longer than its words (a long instrumental) is not stretched: the chords scroll on alone
      r.dataset.free = r.offsetHeight > Math.max(k.offsetHeight * 2.5, 150) ? '1' : '';
      if (r.dataset.free) return;
      const h = Math.max(k.offsetHeight, r.offsetHeight);
      k.style.minHeight = r.style.minHeight = `${h}px`;
    });
  }
  let alignQueued = false;
  const queueAlign = () => { if (alignQueued) return; alignQueued = true; requestAnimationFrame(() => { alignQueued = false; alignRows(); }); };
  document.addEventListener('lyrics:data', (e) => { lyricLines = e.detail; if (cur?.data) renderChart($('sc-body')); });
  document.addEventListener('blocks:change', queueAlign);
  document.addEventListener('layout:change', () => { if (cur?.data) renderChart($('sc-body')); });
  addEventListener('resize', () => { if (cur?.data) { const was = !!$('sc-body').querySelector('.chart.aligned'); if (was !== alignedMode()) renderChart($('sc-body')); else queueAlign(); } });
  new ResizeObserver(queueAlign).observe(document.querySelector('#lyrics .ly-body') || document.body);

  function renderChart(body) {
    const { structure } = cur.data;
    const first = structure.bars[0] > 0 ? 0 : 1;
    const aligned = alignedMode();
    const cells = chartRows().map((list, i) => {
      const cell = el('button', { type: 'button', className: 'cell', title: 'Play from this bar' },
        el('small', { textContent: String(i + first) }),
        ...list.map(({ c, ci, carried }) => { const sp = el('span', { className: carried ? 'carried' : '', textContent: name(c) }); sp.dataset.ci = String(ci); paint(sp, c); tabMark(sp, ci); return sp; }));
      cell.dataset.bar = String(i);
      if (cur.bars[i] != null) cell.dataset.t = String(cur.bars[i]);
      cell.addEventListener('click', (e) => (e.shiftKey ? (anchor === null ? (anchor = i, barLoop(i, i)) : (barLoop(Math.min(anchor, i), Math.max(anchor, i)), anchor = null)) : player.seek(cur.bars[i])));
      return cell;
    });
    let grid;
    if (aligned) {
      const rows = lyricKids().map((k) => {
        const r = el('div', { className: 'arow' });
        if (k.dataset.t != null) r.dataset.t = k.dataset.t;
        if (!k.classList.contains('ly-line') || k.classList.contains('gap')) r.dataset.free = '1';
        return r;
      });
      cells.forEach((c, i) => rows[rowOfBar(i)].append(c));
      grid = el('div', { className: 'chart aligned' }, ...rows);
    } else grid = el('div', { className: 'chart' }, ...cells);
    body.replaceChildren(grid);
    $('fing').replaceChildren(fingHead(), fingBlock('Now and next', 'fg-now', nowNext()), fingBlock('Settings', 'fg-cfg', fingerOpts()), fingBlock('All chords', 'fg-all', diagrams()));
    syncFing();
    if (aligned) alignRows();
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

  // The fingering panel is for a guitar: it is hidden while the tab on screen is a bass, drums, keys or anything else, and a
  // chip in the block bar (or the × in the panel) shows or hides it for the guitar.
  const fingChip = Object.assign(el('button', { type: 'button', className: 'chip', textContent: 'Fingering', title: 'Show or hide the chord fingerings' }), { hidden: true });
  fingChip.addEventListener('click', () => { fingOn = !fingOn; try { localStorage.setItem('stems:sc-fingShow', fingOn ? '1' : '0'); } catch { /* ignore */ } syncFing(); });
  let fingOn = flag('fingShow', true);
  function fingIsGuitar() {
    const t = $('tabview');
    const onScreen = !t.hidden && !t.classList.contains('off') && !t.classList.contains('out');
    return !onScreen || (t.dataset.kind || 'guitar') === 'guitar';
  }
  function syncFing() {
    const has = !!cur?.data && fingIsGuitar();
    const bar = $('blockbar'), eye = bar.querySelector('.btn');
    if (eye && fingChip.nextElementSibling !== eye) bar.insertBefore(fingChip, eye);
    fingChip.hidden = !has;
    fingChip.setAttribute('aria-pressed', String(fingOn));
    $('fing').hidden = !has || !fingOn;
    document.dispatchEvent(new Event('fing:change'));
  }
  document.addEventListener('blocks:change', syncFing);
  document.addEventListener('tab:kind', syncFing);
  function fingHead() {
    const x = el('button', { type: 'button', className: 'icon-btn', textContent: '×', title: 'Hide fingerings', ariaLabel: 'Hide fingerings' });
    x.addEventListener('click', () => fingChip.click());
    return el('div', { className: 'fg-head' }, el('b', { textContent: 'Fingering' }), x);
  }

  function fingBlock(title, cls, body, open = true) {
    const d = el('details', { className: `fg-block ${cls}`, open }, el('summary', { textContent: title }), body);
    const k = `stems:fg2-${cls}`;
    try { if (localStorage.getItem(k) != null) d.open = localStorage.getItem(k) === '1'; } catch { /* ignore */ }
    d.addEventListener('toggle', () => { try { localStorage.setItem(k, d.open ? '1' : '0'); } catch { /* ignore */ } });
    return d;
  }

  /** The chord being played and the one after it, big enough to read at a glance; kept current by renderAhead. */
  function nowNext() {
    const box = el('div', { className: 'fg-pair' });
    nowBox = box;
    paintNow(last.chord);
    return box;
  }
  let nowBox = null;
  function paintNow(ci) {
    if (!nowBox || !cur?.data) return;
    const chords = cur.data.structure.chords, first = Math.max(0, ci);
    const picks = [];
    for (let i = first; i < chords.length && picks.length < 2; i++) if (!picks.length || name(chords[i]) !== name(chords[picks[0]])) picks.push(i);
    nowBox.replaceChildren(...picks.map((i, k) => {
      const svg = fingerSvg(chords[i]);
      return el('figure', { className: k ? 'next' : 'cur' }, svg || el('span', { className: 'muted', textContent: '–' }), paint(el('figcaption', { textContent: name(chords[i]) }), chords[i]));
    }));
  }

  function fingerOpts() {
    const toggle = (key, text, hint) => {
      const input = el('input', { type: 'checkbox', checked: opts[key] });
      input.addEventListener('change', () => { opts[key] = input.checked; remember(key); if (key === 'simple') cur.capo = null; renderAll(); });
      return el('label', { className: 'switch', title: hint }, input, el('span'), text);
    };
    const row = el('div', { className: 'fing-opts' },
      toggle('easy', 'Easiest fingering', 'Pick the voicing with the fewest fingers and no stretch'),
      toggle('simple', 'Simpler chords', 'Play 7ths and 6ths as plain major/minor'),
      toggle('hover', 'Fingering on hover or tap', 'Point at a chord (or tap it on a phone) to see how to play it'));
    row.append(chordKeyPicker(), capoPicker(), tuningPicker());
    return row;
  }

  /** Move the chords and tab alone, the audio stays in its key. */
  function chordKeyPicker() {
    const k = player.chordKey;
    const sel = el('select', { 'aria-label': 'Chord key', title: 'Read the chords in another key without changing the sound' });
    for (let v = -12; v <= 12; v++) {
      const key = cur.data?.structure.key;
      const label = `${v > 0 ? '+' : ''}${v}${key ? ` · ${keyName(key, player.semitones + v)}` : ''}`;
      sel.append(el('option', { value: String(v), textContent: v === 0 ? `Chord key: with the audio${key ? ` (${keyName(key, player.semitones)})` : ''}` : `Chord key: ${label}`, selected: v === k }));
    }
    sel.addEventListener('change', () => player.setChordKey(+sel.value));
    return sel;
  }

  function capoPicker() {
    const info = capoInfo();
    const weighted = tally().map(({ c, len }) => ({ c, w: len }));
    const saved = info.all ?? capoSavings(weighted, shift() + shape().down, opts.simple, shape().open);
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

  /** Guitar tuning: presets plus whatever the tab says, with a plain-language note and a nudge when another tuning plays easier. */
  function tuningPicker() {
    const list = [...GUITAR_TUNINGS];
    if (cur.tabTuning && !list.some((t) => t.id === cur.tabTuning.id)) list.push(cur.tabTuning);
    if (!list.some((t) => t.id === tuning.id)) list.push(tuning);
    const weighted = tally().map(({ c, len }) => ({ c, w: len }));
    const saved = new Map(tuningSavings(weighted, shift(), opts.simple).map(({ t, saved: v }) => [t.id, v]));
    const sel = el('select', { ariaLabel: 'Guitar tuning', title: 'How your guitar is tuned. The diagrams and chord names follow it, the sound does not change' });
    for (const t of list) {
      const v = saved.get(t.id);
      const tag = t.id === cur.tabTuning?.id ? ' · from the tab' : v > 0.02 ? ` · ${Math.round(v * 100)}% easier` : '';
      sel.append(el('option', { value: t.id, textContent: `Tuning: ${t.name}${tag}`, selected: t.id === tuning.id }));
    }
    sel.addEventListener('change', () => setTuning(list.find((t) => t.id === sel.value)));
    const box = el('span', { className: 'capo-pick' }, sel);
    const { down } = shape();
    box.append(el('span', { className: 'muted', textContent: tuning.id === 'std' ? '' : down > 0
      ? `${tuning.hint}. Fret the same shapes as in standard tuning, but ${down === 1 ? 'a half step' : `${down} semitones`} lower than written. Shown in ${tuning.name} shapes.`
      : tuning.hint }));
    const best = [...saved].filter(([id]) => id !== tuning.id).sort((a, b) => b[1] - a[1])[0];
    const gain = best ? best[1] - (saved.get(tuning.id) ?? 0) : 0;
    if (best && gain > 0.15 && tuning.id === 'std') {
      const t = tuningById(best[0]);
      const use = el('button', { type: 'button', className: 'btn small ghost', textContent: `${t.name} would be ${Math.round(gain * 100)}% easier. Use it` });
      use.addEventListener('click', () => setTuning(t));
      box.append(use);
    }
    return box;
  }

  function setTuning(t) {
    tuning = t;
    if (cur) cur.capo = null;
    alts.clear();
    renderAll();
    onSetup?.();
  }

  /** Fingering diagrams for the most-played chords; click one to try another fingering. */
  /** The fingering of one chord as an SVG, the same one the list shows; null when the chord has no shape. */
  function fingerSvg(c) {
    const v = voicing(c, shapeShift(), { easy: opts.easy, alt: alts.get(shapeKey(c)) || 0, open: shape().open });
    return v ? diagram(v) : null;
  }

  /** Every chord of the song, most played first. */
  function diagrams() {
    const wrap = el('div', { className: 'diagrams' });
    const open = shape().open, sh = shapeShift();
    for (const { c } of tally()) {
      const key = shapeKey(c);
      const svg = fingerSvg(c);
      if (!svg) continue;
      const n = voicingCount(c, sh, open);
      const fig = el('figure', { title: n > 1 ? 'Click for another fingering' : '' }, svg, paint(el('figcaption', { textContent: name(c) }), c));
      fig.dataset.shape = key;
      if (n > 1) {
        fig.classList.add('alt');
        fig.addEventListener('click', () => { alts.set(key, ((alts.get(key) || 0) + 1) % Math.min(n, 12)); renderBody(); });
      }
      wrap.append(fig);
    }
    return wrap;
  }

  // ── fingering on hover (mouse) or tap (touch), on any chord in the lyrics, the chart or the dock ──
  const tip = el('div', { id: 'fg-tip', hidden: true, role: 'tooltip' });
  document.body.append(tip);
  const tipChord = (n) => { const ci = n?.dataset?.ci; return ci != null ? cur?.data?.structure.chords[+ci] : null; };
  function showTip(n) {
    const c = opts.hover && tipChord(n);
    const svg = c && fingerSvg(c);
    if (!svg) { tip.hidden = true; return; }
    tip.replaceChildren(svg, paint(el('b', { textContent: name(c) }), c));
    tip.hidden = false;
    const r = n.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
    tip.style.top = `${r.top - h - 8 < 8 ? r.bottom + 8 : r.top - h - 8}px`;
  }
  const CHORD_NODE = '.ly-lines .ch[data-ci], #sing .ch[data-ci], .chart .cell span[data-ci]';
  document.addEventListener('pointerover', (e) => { if (e.pointerType === 'mouse') showTip(e.target.closest?.(CHORD_NODE)); });
  document.addEventListener('pointerout', (e) => { if (e.pointerType === 'mouse' && e.target.closest?.(CHORD_NODE)) tip.hidden = true; });
  document.addEventListener('pointerdown', (e) => {
    if (e.pointerType === 'mouse') return;
    const n = e.target.closest?.(CHORD_NODE);
    if (n) showTip(n); else tip.hidden = true;
  }, true);

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
        title: title(), sub: metaText,
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
  /** Tuning id kept with the song (shared through the cloud); a tab's own tuning is offered, never forced. */
  const tuningState = {
    get: () => tuning.id,
    set(id) {
      tuning = (typeof id === 'string' && id.startsWith('custom:'))
        ? tuningFromPitches(id.slice(7).split(',').map(Number)) ?? tuningById('std')
        : tuningById(id);
      if (cur) cur.capo = null;
      if (cur?.data) renderAll();
    },
  };
  new ResizeObserver(() => { if (cur?.data) requestAnimationFrame(renderRuler); }).observe($('lane-col'));

  return {
    tuning: tuningState, open, close, retab, frame, tuned, timeline, barChords, chordsIn, refreshRuler: renderRuler,
    // scroll-link adapter: chart rows keyed by song time
    pane: () => ({
      root: $('score'), anchor: 0.4,
      box: () => $('sc-body').querySelector('.chart'),
      active: () => !!cur?.data && !$('score').classList.contains('off') && $('sc-body').querySelector('.chart')?.offsetParent !== null,
      hold: () => { holdUntil = Date.now() + 4000; setFollow(false); },
      following: () => follow && Date.now() >= holdUntil,
      resume: () => {
        setFollow(true); holdUntil = 0;
        const now = $('sc-body').querySelector('.cell.now'), chart = now?.closest('.chart');
        if (now && chart.offsetParent !== null) glide(chart, chart.scrollTop + offsetIn(chart, now) + now.offsetHeight / 2 - anchorAt(chart, 0.4));
      },
      away: () => {
        const now = $('sc-body').querySelector('.cell.now'), chart = now?.closest('.chart');
        if (!now || chart.offsetParent === null) return false;
        const m = offsetIn(chart, now) + now.offsetHeight / 2;
        return m < 0 || m > chart.clientHeight;
      },
      track: () => $('sc-body').querySelector('.chart.aligned')
        ? [...$('sc-body').querySelectorAll('.arow')].flatMap((r) => (r.dataset.free
          ? [...r.querySelectorAll('.cell')].map((c) => ({ y: c.offsetTop + c.offsetHeight / 2, t: cur.bars[Number(c.dataset.bar)] })).filter((p) => p.t != null)
          : r.dataset.t ? [{ y: r.offsetTop + r.offsetHeight / 2, t: Number(r.dataset.t) }] : []))
        : [...$('sc-body').querySelectorAll('.cell')].map((c) => ({ y: c.offsetTop + c.offsetHeight / 2, t: cur.bars[Number(c.dataset.bar)] })).filter((p) => p.t != null),
    }),
    syncSeed: () => { lastShift = shift(); lastTempo = player.tempo; },
  };
}
