// Guitar and bass tab, staff notation for keys and other instruments, a drum score for drums (Songsterr or a Soundslice link, matched to the
// recording's bars on the server): bar by bar, with the chord above each bar,
// taking the place of the Chords box beside the lyrics and scrolling in step with them.
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
import { glide, cancelGlide, offsetIn, lockstep, anchorAt, stepY, settle, unaim, LEAD } from './scroll.js';
import { SCORE_KINDS, scoreLayout, paintScore, clefColumn } from './studio-staff.js';
const NOTES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
const BAR_MIN = 11; // rem a bar needs before a row takes one more
const PART_KEY = 'stems:tab-part';
const KINDS = ['guitar', 'bass', 'keys', 'other', 'drums'];
const NEXT = 4; // bars before a drum pattern ends when the one after it appears below

// Runs of repeated bars: one pattern of 1, 2 or 4 bars played over and over, shown once instead of scrolled past.
function segmentsOf(bars) {
  const sig = bars.map((b) => JSON.stringify([b.n, b.mark || '']));
  const out = [];
  for (let i = 0; i < sig.length;) {
    let best = { p: 1, len: 1 };
    for (const p of [1, 2, 3, 4, 6, 8]) {
      if (i + 2 * p > sig.length) continue;
      let len = p;
      while (i + len < sig.length && sig[i + len] === sig[i + len - p]) len++;
      if (len >= 2 * p && len > best.len) best = { p, len };
    }
    out.push({ first: i, ...best });
    i += best.len;
  }
  return out;
}
const GRID = 16; // editing slots per whole note
const kindOf = (k) => k.replace(/\d+$/, '');
const cap = (k) => k[0].toUpperCase() + k.slice(1);

function floorIndex(arr, x) {
  let lo = 0, hi = arr.length - 1, r = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= x) { r = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return r;
}

// Start and end second of every tab bar. Bars the recording match skipped (repeats, an intro) are spread over the gap around them.
function timeline(map, n, secs, recEnds) {
  const starts = new Array(n).fill(null), ends = new Array(n).fill(null);
  const anchors = [];
  for (let i = 0; i < n; i++) if (map[i] != null && secs[map[i]] != null) { starts[i] = secs[map[i]]; ends[i] = recEnds[map[i]]; anchors.push(i); }
  if (!anchors.length) return { starts: starts.map((_, i) => i), ends: starts.map((_, i) => i + 1) };
  const first = anchors[0], last = anchors.at(-1);
  for (let i = 0; i < first; i++) { starts[i] = starts[first]; ends[i] = starts[first]; }
  for (let i = last + 1; i < n; i++) { starts[i] = ends[last]; ends[i] = ends[last]; }
  for (let k = 0; k + 1 < anchors.length; k++) {
    const a = anchors[k], b = anchors[k + 1], gap = b - a - 1;
    if (!gap) continue;
    const t0 = ends[a], t1 = Math.max(t0, starts[b]);
    for (let m = 1; m <= gap; m++) { starts[a + m] = t0 + ((m - 1) / gap) * (t1 - t0); ends[a + m] = t0 + (m / gap) * (t1 - t0); }
  }
  for (let i = 1; i < n; i++) starts[i] = Math.max(starts[i], starts[i - 1]);
  return { starts, ends: ends.map((e, i) => Math.max(e, starts[i])) };
}

export function createTab({ player, chordsIn = () => [] }) {
  const root = $('tabview'), body = $('tab-body'), credit = $('tab-credit'), pick = $('tab-part'), variants = $('tab-variant'), tools = $('tab-tools');
  let zoom = Number(localStorage.getItem('prismix.tabZoom')) || 1.25;
  const applyZoom = (d = 0) => {
    zoom = Math.min(2.5, Math.max(0.8, Math.round((zoom + d) * 20) / 20));
    body.style.setProperty('--z', String(zoom));
    try { localStorage.setItem('prismix.tabZoom', String(zoom)); } catch { /* ignore */ }
  };
  applyZoom();
  $('tab-zoom-in').addEventListener('click', () => { applyZoom(0.15); queueAlign(); });
  $('tab-zoom-out').addEventListener('click', () => { applyZoom(-0.15); queueAlign(); });
  let ro = null;
  let cur = null; // { id, tab, st, secs: bar start seconds, cells: [], row: -1 }
  let part = null;
  let holdUntil = 0;
  let width = 0;
  let follow = true;
  const isPat = () => !!cur && cur.tab.tracks[part]?.kind === 'drums' && localStorage.getItem('stems:tab-pattern') !== '0';
  let editing = false;
  let sel = null; // { i: tab bar, slot, s: string } while editing
  let typed = { at: 0, fret: 0 };
  const hist = []; // [{ key, bars }] snapshots for undo
  let saveTimer = 0;
  let status = '';

  const beatSec = (beats, b) => {
    if (b <= 0) return beats[0] + b * (beats[1] - beats[0]);
    if (b >= beats.length - 1) return beats.at(-1) + (b - beats.length + 1) * (beats.at(-1) - beats.at(-2));
    const i = Math.floor(b);
    return beats[i] + (beats[i + 1] - beats[i]) * (b - i);
  };

  function close() {
    flush();
    cur = null;
    editing = false;
    sel = null;
    hist.length = 0;
    root.hidden = true;
    body.replaceChildren();
    pick.replaceChildren();
    variants.replaceChildren();
    tools.replaceChildren();
    credit.replaceChildren();
    delete root.dataset.kind;
    document.dispatchEvent(new Event('tab:kind'));
  }

  async function open(id) {
    close();
    let tab, st;
    try {
      const [a, b] = await Promise.all([fetch(`/api/tabdata/${id}`), fetch(`/media/${id}/structure.json`)]);
      if (!a.ok || !b.ok) return;
      [tab, st] = [await a.json(), await b.json()];
    } catch { return; }
    if (!tab?.found || !st?.bars?.length) return;
    cur = { id, tab, st, secs: [], ends: [], rec: st.bars.map((b) => beatSec(st.beats, b)), cells: [], rows: [], row: -1 };
    if (!tab.tracks[part]) part = tab.tracks[localStorage.getItem(PART_KEY)] ? localStorage.getItem(PART_KEY) : Object.keys(tab.tracks)[0];
    for (const [k, t] of Object.entries(tab.tracks)) t.kind ||= kindOf(k);
    root.hidden = false;
    render();
  }
  // With synced lyrics beside it, every bar sits in the row of the lyric line being sung: one line, one row.
  let lyricLines = null;
  const lyricKids = () => [...document.querySelectorAll('#lyrics .ly-lines > *')];
  const alignedMode = () => !!lyricLines?.synced && matchMedia('(min-width: 900px)').matches && !$('lyrics').hidden
    && !$('lyrics').classList.contains('off') && lyricKids().length >= lyricLines.lines.length + 2;

  function alignRows() {
    const box = body.querySelector('.tab.aligned');
    if (!box || body.offsetParent === null) return;
    const kids = lyricKids(), rows = [...box.children];
    kids.forEach((k, i) => { k.style.minHeight = ''; k.style.paddingBlock = ''; if (rows[i]) rows[i].style.minHeight = ''; });
    kids.forEach((k, i) => {
      const r = rows[i];
      if (!r) return;
      r.hidden = k.offsetParent === null;
      if (r.hidden || !k.classList.contains('ly-line') || k.classList.contains('gap')) return; // intro, breaks and outro keep their own height
      // a line whose bars run far longer than its words (a long instrumental) is not stretched: the tab scrolls on alone
      r.dataset.free = r.offsetHeight > Math.max(k.offsetHeight * 6, 360) ? '1' : '';
      if (r.dataset.free) return;
      const h = Math.max(k.offsetHeight, r.offsetHeight);
      k.style.paddingBlock = `${(h - k.offsetHeight) / 2}px`; // the words sit mid-row, level with the staff
      r.style.minHeight = `${h}px`;
    });
  }
  let alignQueued = false;
  function queueAlign() { if (alignQueued) return; alignQueued = true; requestAnimationFrame(() => { alignQueued = false; alignRows(); }); }
  document.addEventListener('lyrics:data', (e) => { lyricLines = e.detail; if (cur && !root.hidden) render(); });
  document.addEventListener('layout:change', () => { if (cur && !root.hidden) render(); });
  document.addEventListener('blocks:change', queueAlign);
  addEventListener('resize', () => { if (cur && !root.hidden) { if (!!body.querySelector('.tab.aligned') !== alignedMode()) render(); else queueAlign(); } });
  new ResizeObserver(queueAlign).observe(document.querySelector('#lyrics .ly-body') || document.body);

  const announce = () => { root.dataset.kind = cur?.tab.tracks[part]?.kind || ''; document.dispatchEvent(new Event('tab:kind')); };

  function render() {
    if (!cur) return;
    const { tab, st, rec: secs } = cur;
    const p = tab.tracks[part];
    credit.replaceChildren(`${shortName(p, part)} · tab by `, el('a', { href: tab.url, target: '_blank', rel: 'noopener', textContent: tab.source === 'soundslice' ? 'Soundslice' : 'Songsterr' }), p.edited ? ' · edited' : '');
    paintPicker();
    paintTools();
    announce();

    width = body.clientWidth;
    const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
    const diffs = st.bars.slice(1).map((b, i) => b - st.bars[i]).sort((a, b) => a - b);
    const bpb = diffs.length ? diffs[diffs.length >> 1] : 4;
    const recEnds = secs.map((_, j) => (j + 1 < secs.length ? secs[j + 1] : beatSec(st.beats, st.bars[j] + bpb)));
    const { starts, ends } = timeline(tab.map, p.bars.length, secs, recEnds);
    cur.secs = starts;
    cur.ends = ends;
    cur.cells = [];
    cur.rows = [];
    const strings = p.tuning.length;
    const lay = SCORE_KINDS.has(p.kind) ? scoreLayout(p, st.key) : null;
    cur.lay = lay;
    const pat = isPat();
    const aligned = alignedMode() && !pat;
    const segs = pat ? segmentsOf(p.bars) : null;
    cur.segs = segs;
    cur.segOf = [];
    segs?.forEach((g, k) => { for (let i = g.first; i < g.first + g.len; i++) cur.segOf[i] = k; });
    const kids = aligned ? lyricKids() : [];
    const box = el('div', { className: aligned ? 'tab aligned' : pat ? 'tab pat' : 'tab', style: `--strings:${strings}` });
    const arows = kids.map((k) => {
      const r = el('div', { className: 'arow tarow' });
      if (k.dataset.t != null) r.dataset.t = k.dataset.t;
      if (!k.classList.contains('ly-line') || k.classList.contains('gap')) r.dataset.free = '1';
      box.append(r);
      return r;
    });
    let lineOf = 0; // the lyric row a bar belongs to: the last one that has started by the middle of the bar
    const rowOfBar = (i) => {
      const mid = (starts[i] + ends[i]) / 2;
      kids.forEach((k, r) => { if (k.dataset.t != null && Number(k.dataset.t) <= mid && r > lineOf) lineOf = r; });
      return lineOf;
    };
    let rowLine = -1;
    const need = (tb) => 3 + 1.5 * new Set(tb.n.map((b) => b[0])).size; // rem a bar wants: more notes, more room
    const avail = Math.max(10, body.clientWidth / rem - 2);
    let row = null, used = 0;
    p.bars.forEach((tb, i) => {
      const w = Math.max(need(tb), 5);
      const line = aligned ? rowOfBar(i) : -1;
      const seg = segs?.[cur.segOf[i]];
      if (pat ? i === seg.first : !row || line !== rowLine || (used + w > avail && used > 0)) {
        rowLine = line;
        row = el('div', { className: 'trow' }, lay ? clefColumn(lay)
          : el('div', { className: 'strs', 'aria-hidden': 'true' }, ...p.tuning.map((m) => el('span', { textContent: NOTES[m % 12] }))));
        (aligned ? arows[line] : box).append(row);
        cur.rows.push({ row, first: i, seg: cur.segOf[i] });
        used = 0;
      }
      used += w;
      const j = tab.map[i];
      const cell = el('div', { className: 'tbar', style: `flex:${w} 1 0` }, el('span', { className: 'n', textContent: String(i + 1) }));
      cell.dataset.t0 = String(starts[i]);
      cell.dataset.t1 = String(ends[i]);
      if (tb.auto) { cell.classList.add('auto'); cell.title = 'Filled in from a matching bar'; }
      if (tb.weak) { cell.classList.add('weak'); cell.title = 'This bar differs from the recording (a cover or another arrangement); trust the chords above it'; }
      if (tb.mark) cell.append(el('span', { className: 'tag', textContent: tb.mark }));
      const chs = el('div', { className: 'tch' });
      if (j != null && p.kind !== 'drums') {
        let before = j > 0 ? chordsIn(j - 1).at(-1)?.name : '';
        for (const c of chordsIn(j)) {
          chs.append(el('span', { className: c.name === before ? 'same' : '', textContent: c.name, style: `left:${c.f * 100}%` }));
          before = c.name;
        }
      }
      cell.append(chs, el('div', { className: 'fx' }), el('div', { className: lay ? 'stf score' : 'stf', style: lay ? `height:calc(${lay.height} * var(--hs))` : '' }));
      if (!lay) cell.append(el('div', { className: 'rh' }));
      cur.cells[i] = cell;
      cell.dataset.tb = String(i);
      paintStaff(cell, tb);
      cell.addEventListener('click', (e) => {
        if (editing && e.target.closest('.stf')) { pickSpot(cell, e); return; }
        document.dispatchEvent(new CustomEvent('tab:seek', { detail: starts[i] }));
      });
      if (!pat || i - seg.first < seg.p) row.append(cell);
    });
    body.replaceChildren(box);
    if (pat) body.prepend(el('div', { className: 'tab-count', 'aria-live': 'off' }));
    cur.seg = -1;
    cur.nowCell = null;
    if (aligned) alignRows();
    cur.row = -1;
    cur.now = -1;
    cur.shown = -2;
    declutter();
    if (!ro && window.ResizeObserver) { ro = new ResizeObserver(() => declutter()); ro.observe(body); }
  }

  // Chord names sit at their beat position; when two would collide (or run into the bar tag), hide the later one.
  function declutter() {
    for (const cell of cur?.cells || []) {
      if (!cell) continue;
      const spans = [...cell.querySelectorAll('.tch span')];
      spans.forEach((x) => { x.style.visibility = ''; });
      let edge = -Infinity;
      const tag = cell.querySelector('.tag');
      const limit = tag ? tag.offsetLeft - 4 : Infinity;
      for (const x of spans) {
        const l = x.offsetLeft, r = l + x.offsetWidth;
        if (l < edge || r > limit) { x.style.visibility = 'hidden'; continue; }
        edge = r + 4;
      }
    }
  }


  const shortName = (p, key) => p.name.split('|').at(-1).trim() || `${cap(p.kind || kindOf(key))}`;

  function paintPicker() {
    const keys = Object.keys(cur.tab.tracks);
    const kind = cur.tab.tracks[part].kind;
    const kinds = KINDS.filter((k) => keys.some((x) => cur.tab.tracks[x].kind === k));
    pick.replaceChildren(...kinds.map((k) => {
      const n = keys.filter((x) => cur.tab.tracks[x].kind === k).length;
      const b = el('button', { type: 'button', className: 'btn small ghost', textContent: k === 'other' ? 'Others' : cap(k) });
      b.setAttribute('aria-pressed', String(k === kind));
      b.addEventListener('click', () => {
        if (k === kind) return;
        const last = localStorage.getItem(`${PART_KEY}:${k}`);
        choose(cur.tab.tracks[last]?.kind === k ? last : keys.find((x) => cur.tab.tracks[x].kind === k));
      });
      return b;
    }));
    pick.hidden = kinds.length < 2;
    const same = keys.filter((x) => cur.tab.tracks[x].kind === kind);
    const names = same.map((k) => cur.tab.tracks[k].name.replaceAll(' | ', ' · ') || cap(kind));
    const sel = el('select', { className: 'tab-sel', ariaLabel: `${cap(kind)} part` });
    same.forEach((k, i) => {
      const t = cur.tab.tracks[k], dup = names.filter((x) => x === names[i]).length > 1;
      sel.append(el('option', { value: k, selected: k === part, textContent: `${dup ? `${i + 1}. ` : ''}${names[i]}${t.edited ? ' ✎' : ''}` }));
    });
    sel.addEventListener('change', () => choose(sel.value));
    variants.replaceChildren(sel);
    variants.hidden = same.length < 2;
  }

  function choose(k) {
    if (!k || k === part) return;
    flush();
    part = k;
    sel = null;
    hist.length = 0;
    if (SCORE_KINDS.has(cur.tab.tracks[k].kind)) editing = false;
    try { localStorage.setItem(PART_KEY, k); localStorage.setItem(`${PART_KEY}:${cur.tab.tracks[k].kind}`, k); } catch { /* ignore */ }
    render();
    resume();
    frame(player.position); // land on what is playing right now
  }

  function paintTools() {
    const p = cur.tab.tracks[part];
    const btn = (text, title, on) => {
      const b = el('button', { type: 'button', className: 'btn small ghost', textContent: text, title });
      b.addEventListener('click', on);
      return b;
    };
    const edit = btn(editing ? 'Done' : 'Edit', 'Edit this tab: click a spot on the staff, type a fret number. Arrows move, Backspace deletes, Ctrl/⌘+Z undoes. Changes are saved.', () => { editing = !editing; if (!editing) { flush(); sel = null; } render(); });
    edit.setAttribute('aria-pressed', String(editing));
    const kids = SCORE_KINDS.has(p.kind) ? [] : [edit];
    if (p.kind === 'drums') kids.unshift(btn(isPat() ? 'Scroll' : 'Patterns', 'Drums: show each repeated pattern once with a bar countdown, or scroll the whole chart', () => { localStorage.setItem('stems:tab-pattern', isPat() ? '0' : '1'); render(); }));
    if (editing) {
      const u = btn('Undo', 'Undo (Ctrl/⌘+Z)', undo);
      u.disabled = !hist.length;
      kids.push(u);
      if (p.edited) kids.push(btn('Original', 'Throw away your edits of this part', revert));
      kids.push(el('span', { className: 'muted tab-status', textContent: status }));
    }
    tools.replaceChildren(...kids);
    tools.hidden = false;
    body.classList.toggle('editing', editing);
  }

  // Whole-note value of a beat: its length in whole notes, undotted.
  const valueOf = (tb, beat) => {
    const v = beat[1] * tb.len;
    let base = 1;
    while (base > 1 / 64 && base > v * 1.01) base /= 2;
    return base;
  };

  const arc = (x1, x2, s, below) => el('span', { className: `arc${below ? ' below' : ''}`, style: `left:${x1 * 100}%;width:${Math.max(0.02, x2 - x1) * 100}%;top:calc(${s} * var(--row))` });

  function paintStaff(cell, tb) {
    const stf = cell.querySelector('.stf'), rh = cell.querySelector('.rh'), fx = cell.querySelector('.fx');
    stf.replaceChildren();
    rh?.replaceChildren();
    fx.replaceChildren();
    if (cur.lay) {
      for (const [pos,, , extra = {}] of tb?.n ?? []) if (extra.bpm) fx.append(el('span', { className: 'tempo', textContent: `♩ = ${extra.bpm}`, style: `left:${pos * 100}%` }));
      paintScore(stf, Number(cell.dataset.tb), cur.lay);
      return;
    }
    if (tb) {
      const last = new Map(); // string -> position of the note before, for ties and hammer-ons
      let ring = null;
      tb.n.forEach((beat, k) => {
        const [pos, dur, frets, extra = {}] = beat;
        for (const [s, f, flags = ''] of frets) {
          const dead = flags.includes('x'), tied = flags.includes('t');
          stf.append(el('span', { className: `f${tied ? ' tied' : ''}`, textContent: dead ? 'x' : tied ? `(${f})` : String(f), style: `left:${pos * 100}%;top:calc(${s} * var(--row))` }));
          if (flags.includes('s')) stf.append(el('span', { className: 'sl', style: `left:${(pos + 0.012) * 100}%;top:calc(${s} * var(--row))` }));
          if (tied && last.has(s)) stf.append(arc(last.get(s), pos, s, true));
          if (last.has(s) && flags.includes('h')) stf.append(arc(last.get(s), pos, s, false));
          last.set(s, pos);
        }
        if (extra.bpm) fx.append(el('span', { className: 'tempo', textContent: `♩ = ${extra.bpm}`, style: `left:${pos * 100}%` }));
        if (extra.lr) { ring ||= { from: pos }; ring.to = Math.min(1, pos + dur); } else if (ring) { drawRing(fx, ring); ring = null; }
      });
      if (ring) drawRing(fx, ring);
      paintRhythm(rh, tb);
    }
    if (editing && sel && tb && Number(cell.dataset.tb) === sel.i) {
      const slots = slotsOf(tb);
      stf.append(el('span', { className: 'selmark', style: `left:${(sel.slot / slots) * 100}%;top:calc(${sel.s} * var(--row))` }));
    }
    stf.append(el('span', { className: 'cursor' }));
  }

  const drawRing = (fx, r) => fx.append(el('span', { className: 'ring', textContent: 'let ring', style: `left:${r.from * 100}%;width:${(r.to - r.from) * 100}%` }));

  // Stems under the staff, joined by beams within a quarter-note group, like printed tab.
  function paintRhythm(rh, tb) {
    const beams = (b) => { const v = valueOf(tb, b); return v <= 1 / 32 ? 3 : v <= 1 / 16 ? 2 : v <= 1 / 8 ? 1 : 0; };
    const group = (b) => Math.floor(b[0] * tb.len * 4 + 1e-6);
    const beats = tb.n;
    beats.forEach((b, k) => {
      if (valueOf(tb, b) >= 1) return;
      const x = `${b[0] * 100}%`, nb = beams(b);
      rh.append(el('span', { className: 'rstem', style: `left:${x}` }));
      const dotted = b[1] * tb.len > valueOf(tb, b) * 1.25;
      if (dotted) rh.append(el('span', { className: 'dot', style: `left:${x}` }));
      const next = beats[k + 1], prev = beats[k - 1];
      const joinNext = next && beams(next) && nb && group(next) === group(b);
      const joinPrev = prev && beams(prev) && nb && group(prev) === group(b);
      for (let n = 1; n <= nb; n++) {
        if (joinNext && n <= Math.min(nb, beams(next))) rh.append(el('span', { className: 'beam', style: `left:${x};width:${(next[0] - b[0]) * 100}%;top:${15 - (n - 1) * 3}px` }));
        else if (!joinPrev || n > beams(prev)) rh.append(el('span', { className: 'beam stub', style: `left:${x};top:${15 - (n - 1) * 3}px` }));
      }
    });
  }

  // ── editing ──
  const slotsOf = (tb) => Math.max(1, Math.round(tb.len * GRID));
  const part_ = () => cur.tab.tracks[part];
  const cellOf = (i) => cur.cells.find((c) => c && c.dataset.tb === String(i));
  const repaint = (i) => { const c = cellOf(i); if (c) paintStaff(c, part_().bars[i]); };
  const beatAt = (tb, slot) => tb.n.findIndex((b) => Math.abs(b[0] - slot / slotsOf(tb)) < 0.4 / slotsOf(tb));

  function pickSpot(cell, e) {
    const i = Number(cell.dataset.tb), tb = part_().bars[i], r = cell.querySelector('.stf').getBoundingClientRect();
    const slots = slotsOf(tb);
    const slot = Math.min(slots - 1, Math.max(0, Math.round(((e.clientX - r.left) / r.width) * slots)));
    const s = Math.min(part_().tuning.length - 1, Math.max(0, Math.floor(((e.clientY - r.top) / r.height) * part_().tuning.length)));
    moveSel({ i, slot, s });
  }

  function moveSel(next) {
    const old = sel;
    sel = next;
    typed = { at: 0, fret: 0 };
    if (old && old.i !== next.i) repaint(old.i);
    repaint(next.i);
  }

  function remember() {
    hist.push({ key: part, bars: JSON.stringify(part_().bars) });
    if (hist.length > 100) hist.shift();
  }

  function changed(i) {
    part_().edited = true;
    repaint(i);
    queueSave();
    paintTools();
  }

  function setFret(fret) {
    const tb = part_().bars[sel.i], slots = slotsOf(tb);
    remember();
    let k = beatAt(tb, sel.slot);
    if (k < 0) {
      const pos = sel.slot / slots, nxt = tb.n.find((b) => b[0] > pos + 1e-6);
      tb.n.push([pos, Math.min(nxt ? nxt[0] - pos : 1, 2 / slots), []]);
      tb.n.sort((a, b) => a[0] - b[0]);
      k = beatAt(tb, sel.slot);
    }
    const frets = tb.n[k][2], ex = frets.find((x) => x[0] === sel.s);
    if (ex) ex[1] = fret; else frets.push([sel.s, fret]);
    changed(sel.i);
  }

  function clearNote() {
    const tb = part_().bars[sel.i], k = beatAt(tb, sel.slot);
    if (k < 0 || !tb.n[k][2].some((x) => x[0] === sel.s)) return;
    remember();
    tb.n[k][2] = tb.n[k][2].filter((x) => x[0] !== sel.s);
    if (!tb.n[k][2].length) tb.n.splice(k, 1);
    changed(sel.i);
  }

  function step(dSlot, dString) {
    const bars = part_().bars;
    let { i, slot, s } = sel;
    s = Math.min(part_().tuning.length - 1, Math.max(0, s + dString));
    slot += dSlot;
    if (slot < 0 && i > 0) { i--; slot = slotsOf(bars[i]) - 1; } else if (slot >= slotsOf(bars[i]) && i + 1 < bars.length) { i++; slot = 0; } else slot = Math.min(slotsOf(bars[i]) - 1, Math.max(0, slot));
    moveSel({ i, slot, s });
    cellOf(i)?.scrollIntoView({ block: 'nearest' });
  }

  function undo() {
    const h = hist.pop();
    if (!h || h.key !== part) { paintTools(); return; }
    part_().bars = JSON.parse(h.bars);
    part_().edited = true;
    queueSave();
    render();
  }

  async function revert() {
    if (!confirm('Throw away your edits of this part and go back to the downloaded tab?')) return;
    clearTimeout(saveTimer);
    try {
      const r = await fetch(`/api/tabdata/${cur.id}/tracks/${part}`, { method: 'DELETE' });
      if (!r.ok) throw new Error();
      cur.tab = await r.json();
      for (const [k, t] of Object.entries(cur.tab.tracks)) t.kind ||= kindOf(k);
      hist.length = 0;
      sel = null;
      status = '';
      render();
    } catch { status = 'Could not revert'; paintTools(); }
  }

  function queueSave() {
    status = 'Editing…';
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 700);
  }

  function flush() {
    if (!saveTimer || !cur) return;
    clearTimeout(saveTimer);
    saveTimer = 0;
    const { id } = cur, key = part, bars = part_().bars;
    status = 'Saving…';
    fetch(`/api/tabdata/${id}/tracks/${key}`, { method: 'PUT', keepalive: true, headers: { 'content-type': 'application/json' }, body: JSON.stringify({ bars }) })
      .then((r) => { status = r.ok ? 'Saved' : 'Not saved'; })
      .catch(() => { status = 'Not saved'; })
      .finally(() => { if (cur?.id === id && editing) { const s = tools.querySelector('.tab-status'); if (s) s.textContent = status; } });
  }

  document.addEventListener('keydown', (e) => {
    if (!editing || !sel || !cur || root.hidden || e.target.closest?.('input, textarea, select, [contenteditable]')) return;
    const mod = e.ctrlKey || e.metaKey;
    let used = true;
    if (mod && e.key.toLowerCase() === 'z') undo();
    else if (mod || e.altKey) used = false;
    else if (/^\d$/.test(e.key)) {
      const now = Date.now(), two = typed.fret * 10 + Number(e.key);
      const fret = now - typed.at < 900 && typed.fret > 0 && two <= 36 ? two : Number(e.key);
      typed = { at: now, fret };
      setFret(fret);
    } else if (e.key === 'Backspace' || e.key === 'Delete') clearNote();
    else if (e.key === 'ArrowLeft') step(-1, 0);
    else if (e.key === 'ArrowRight') step(1, 0);
    else if (e.key === 'ArrowUp') step(0, -1);
    else if (e.key === 'ArrowDown') step(0, 1);
    else if (e.key === 'Escape') { const i = sel.i; sel = null; repaint(i); } else used = false;
    if (used) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  addEventListener('beforeunload', flush);

  // Content-space y of a row's middle, measured from rects: offsetTop ignores the zoom the pane's children carry.
  const yIn = (n) => offsetIn(body, n) + body.scrollTop + n.getBoundingClientRect().height / 2;
  const centre = (cell, soft) => {
    const r = cell.parentElement, want = yIn(r) - anchorAt(body, 0.4);
    soft ? glide(body, want) : body.scrollTo({ top: want });
  };

  // Continuous follow: the pane drifts so the playing moment stays at the anchor, rows slide up as their time comes (no jumps).
  let trackKey = '', trackPts = null;
  function glideTo(pos) {
    const key = `${body.scrollHeight}/${body.clientHeight}/${cur.cells.length}`;
    if (key !== trackKey || !trackPts) { trackKey = key; trackPts = api.pane().track(); }
    const y = stepY(trackPts, pos + LEAD);
    if (y != null) settle(body, y - anchorAt(body, 0.4)); // the playing row holds one spot; the next slides in when its time comes
  }

  // Drums: a pattern that repeats stays put while a line sweeps its bars and a countdown runs; a few bars before it ends
  // the next pattern appears dimmed below, and takes its place when its time comes. Nothing scrolls.
  function patternTo(j) {
    const sg = cur.segOf[Math.max(j, 0)], seg = cur.segs[sg];
    const left = seg.first + seg.len - 1 - Math.max(j, 0);
    const nextOn = left < NEXT && !!cur.segs[sg + 1];
    const key = `${sg}:${nextOn}`;
    if (key !== cur.seg) {
      cur.seg = key;
      for (const { row, seg: rs } of cur.rows) {
        row.hidden = !(rs === sg || (nextOn && rs === sg + 1));
        row.classList.toggle('next', rs === sg + 1);
      }
    }
    const c = body.querySelector('.tab-count');
    if (c) {
      c.textContent = String(left + 1);
      // green on the first bar of the pattern, sliding to red on the last; a one-bar pattern is all last bar, so it is red
      c.style.setProperty('--cd', String(seg.len > 1 ? Math.round(145 - 120 * ((seg.len - 1 - left) / (seg.len - 1))) : 25));
      c.classList.add('counted');
    }
  }

  function frame(pos) {
    if (!cur || root.hidden || root.classList.contains('off') || body.offsetParent === null) return;
    if (body.clientWidth !== width && Math.abs(body.clientWidth - width) > 40) render();
    const j = floorIndex(cur.secs, pos);
    const sg = cur.segs?.[cur.segOf[j]];
    const cell = sg ? cur.cells[sg.first + ((j - sg.first) % sg.p)] : cur.cells[j];
    if (j !== cur.now) cur.now = j;
    if (cell !== cur.nowCell) {
      cur.nowCell?.classList.remove('now');
      cur.nowCell = cell;
      cell?.classList.add('now');
    }
    if (cur.segs) { if (cell && j !== cur.shown) { cur.shown = j; patternTo(j); } }
    else if (cell && !lockstep.on && follow && Date.now() >= holdUntil) glideTo(pos);
    if (cell) cell.querySelector('.cursor').style.setProperty('--x', `${Math.min(1, Math.max(0, (pos - cur.secs[j]) / Math.max(1e-6, cur.ends[j] - cur.secs[j]))) * 100}%`);
  }

  const manual = () => { holdUntil = Date.now() + 4000; follow = false; cancelGlide(body); unaim(body); };
  for (const ev of ['wheel', 'touchmove']) body.addEventListener(ev, manual, { passive: true });
  document.addEventListener('chords:change', () => { if (cur && !root.hidden) { const n = cur.now; render(); cur.now = -1; if (n >= 0) frame(cur.secs[n]); } });
  const resume = () => { follow = true; holdUntil = 0; unaim(body); };
  player.addEventListener('seek', resume);
  let wasPlaying = false;
  player.addEventListener('state', () => { if (player.playing && !wasPlaying) resume(); wasPlaying = player.playing; });

  const api = {
    open, close, frame,
    pane: () => ({
      root: $('tabview'), anchor: 0.4,
      box: () => body,
      active: () => !!cur && !root.hidden && !root.classList.contains('off') && body.offsetParent !== null && !cur.segs,
      hold: manual,
      following: () => follow && Date.now() >= holdUntil,
      track: () => {
        const point = ({ row, first }) => {
          return { y: yIn(row), t: cur.secs[first] };
        };
        const rows = cur?.rows ?? [];
        if (!body.querySelector('.tab.aligned')) return rows.map((x) => point(x));
        // aligned: a row of the lyrics' own height maps to the line's start, so both panes agree on every moment
        return [...body.querySelector('.tab').children].flatMap((r) => (r.hidden ? [] : r.dataset.free || !r.dataset.t
          ? rows.map((x, i) => [x, i]).filter(([x]) => x.row.parentElement === r).map(([x]) => point(x))
          : [{ y: yIn(r), t: Number(r.dataset.t) }]));
      },
      resume: () => { resume(); const c = cur?.cells[cur.now]; if (c) centre(c, true); },
      away: () => {
        const c = cur?.cells[cur.now];
        if (!c) return false;
        const m = offsetIn(body, c.parentElement) + c.parentElement.offsetHeight / 2;
        return m < 0 || m > body.clientHeight;
      },
    }),
  };
  return api;
}
