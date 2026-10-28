// Spotify-style lyrics: the current line is lit and kept centred, click a line to jump to it.
// Lines come from LRCLIB or Genius through the server (first open of a track only); Genius text is timed against the audio, and when nothing is published the words are recognised from the audio.

import { glide, cancelGlide, offsetIn, lockstep } from './scroll.js';

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const SING_LEAD = 0.18; // output-latency estimates run short (Bluetooth, AirPlay), so colour syllables a touch ahead
const LEAD = 0.35; // light a line slightly early: reading is faster than singing
const MAX_WORD = 6; // seconds; no sung word or silence between words is longer
const BREAK_S = 6; // a pause between sung lines at least this long gets a ♪ row
const PAUSE_MS = Infinity; // after the user scrolls by hand, leave the view alone until play, a seek or the Follow button

const VOWELS = 'aeiouyаеёиоуыэюяіїєäöüéèêàâîôû';
const SYL = new RegExp(`[^${VOWELS}]*[${VOWELS}]+(?:[^${VOWELS}]+$)?`, 'gi');
/** Rough syllable split, good enough to say where in a word a chord lands. */
export function syllabify(word) {
  const m = word.match(SYL);
  if (!m) return [word];
  const used = m.join('').length;
  if (used < word.length) m[m.length - 1] += word.slice(used);
  return m;
}
const SING = 0.45; // seconds per syllable when a line has no end time of its own
const MAX_TAIL = 1.5; // a line is sung over at most (syllables × SING + this), the rest of its slot is rest
const PRONOUNCED = /[a-zа-яё']/i; // what align.py can place; other tokens have no word timing
/**
 * Split a line into words of syllables and give every syllable a start `t` and end `e`. With per-word times from forced
 * alignment (ws/we, one pair per pronounced word) a word's syllables share its own span by letter count; otherwise the line's
 * span is shared evenly.
 */
export function timeLine(text, t, next, ws, we) {
  const words = text.split(/(\s+)/).filter((w) => w !== '');
  const real = words.filter((w) => PRONOUNCED.test(w) && !/^\s+$/.test(w)).length;
  // word times that stretch over an instrumental (a word held for seconds, or a long silence between words) are alignment errors
  const smeared = !!ws && ws.some((a, k) => we?.[k] - a > MAX_WORD || (k > 0 && a - we[k - 1] > MAX_WORD));
  const exact = !!ws && ws.length === we?.length && ws.length === real && real > 0 && !smeared;
  const syl = [];
  let wi = 0, edge = t;
  const parts = words.map((w) => {
    if (/^\s+$/.test(w)) return { space: w };
    const pieces = syllabify(w).map((txt) => { const o = { txt }; syl.push(o); return o; });
    if (exact) {
      const hit = PRONOUNCED.test(w), a = hit ? ws[wi] : edge, b = hit ? we[wi] : edge;
      if (hit) wi++;
      edge = b;
      const total = pieces.reduce((n, o) => n + o.txt.length, 0);
      let at = a;
      pieces.forEach((o) => { o.t = at; at += ((b - a) * o.txt.length) / total; o.e = at; });
    }
    return { pieces };
  });
  if (exact) return { parts, syl, end: Math.min(Math.max(next ?? 0, we.at(-1)), we.at(-1) + MAX_TAIL) };
  const slot = next != null ? Math.max(0.5, next - t) : syl.length * SING + MAX_TAIL;
  const sung = Math.min(slot, syl.length * SING + MAX_TAIL);
  syl.forEach((o, k) => { o.t = t + (sung * k) / Math.max(1, syl.length); o.e = k + 1 < syl.length ? t + (sung * (k + 1)) / syl.length : t + sung; });
  return { parts, syl, end: t + Math.min(slot, sung + MAX_TAIL) };
}

/**
 * Cut a syllable in two where a chord starts inside it, at the letter the singer is on at that moment, so the chord
 * hangs over that letter instead of the whole syllable. Mutates `tl` (parts and syl) and keeps letter-linear times.
 */
export function splitAt(tl, times) {
  for (const t of times) {
    for (const p of tl.parts) {
      const i = p.pieces ? p.pieces.findIndex((o) => t > o.t + 0.03 && t < o.e - 0.03 && o.txt.length > 3) : -1;
      if (i < 0) continue;
      const o = p.pieces[i];
      const cut = Math.round((o.txt.length * (t - o.t)) / (o.e - o.t));
      if (cut < 2 || cut > o.txt.length - 2) break;
      const mid = o.t + ((o.e - o.t) * cut) / o.txt.length;
      p.pieces.splice(i, 1, { txt: o.txt.slice(0, cut), t: o.t, e: mid }, { txt: o.txt.slice(cut), t: mid, e: o.e });
      break;
    }
  }
  tl.syl = tl.parts.flatMap((p) => p.pieces || []);
  return tl;
}

export function createLyrics({ player, api, chords = () => [] }) {
  let cur = null; // { id, data, nodes: HTMLElement[], active: number }
  let token = 0;
  let manualUntil = 0;
  let holdUntil = 0;
  const body = $('ly-body');

  const note = (text, withSearch) => {
    const kids = [el('p', { className: 'muted', textContent: text })];
    if (withSearch) {
      const form = el('form', { className: 'ly-search' });
      const input = el('input', { type: 'text', placeholder: 'Artist and song title', 'aria-label': 'Search lyrics', spellcheck: false });
      const listen = el('button', { className: 'btn small ghost', type: 'button', textContent: 'Recognise from audio', title: 'Transcribe the vocals (English or Russian). Slow the first time.' });
      listen.addEventListener('click', () => { if (cur) load(cur.id, null, true); });
      form.append(input, el('button', { className: 'btn small', type: 'submit', textContent: 'Search' }), listen);
      form.addEventListener('submit', (e) => { e.preventDefault(); if (input.value.trim() && cur) load(cur.id, input.value.trim()); });
      kids.push(form);
    }
    body.replaceChildren(el('div', { className: 'ly-note' }, ...kids));
  };

  async function load(id, query, listen = false) {
    const mine = ++token;
    note(listen ? 'Listening to the vocals… this can take a minute or two the first time.' : 'Finding lyrics…');
    const slow = listen ? 0 : setTimeout(() => { if (mine === token) note('Nothing published yet. Listening to the vocals… this can take a minute or two.'); }, 6000);
    try {
      const qs = query ? `?q=${encodeURIComponent(query)}` : listen ? '?listen=1' : '';
      const data = await api(`/api/lyrics/${id}${qs}`);
      clearTimeout(slow);
      if (mine !== token) return;
      render(id, data);
    } catch (err) {
      clearTimeout(slow);
      if (mine === token) note(err.message, true);
    }
  }

  function render(id, data) {
    const nodes = data.lines.map((ln, i) => {
      const n = el('div', { className: `ly-line${ln.text ? '' : ' gap'}` });
      const ok = data.synced && ln.t != null;
      if (ok) { n.tabIndex = 0; n.setAttribute('role', 'button'); } else n.classList.add('dis');
      if (ln.t != null) n.dataset.t = String(ln.t);
      if (!ln.text) n.textContent = '♪';
      if (ok) {
        const go = () => { manualUntil = 0; player.seek(ln.t); };
        n.addEventListener('click', (e) => { if (!e.target.closest('.ch')) go(); });
        n.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
      }
      return n;
    });
    cur = { id, data, nodes, active: -2, timed: [], intro: el('div', { className: 'ly-instr' }), outro: el('div', { className: 'ly-instr' }) };
    const credit = $('ly-credit');
    if (data.source === 'audio') credit.replaceChildren(`recognised from audio (${data.language}), may contain mistakes`);
    else credit.replaceChildren(data.web ? el('a', { href: data.web, target: '_blank', rel: 'noopener', textContent: 'Genius' }) : 'LRCLIB');
    body.replaceChildren(el('div', { className: `ly-lines${data.synced ? '' : ' plain'}` }, ...(data.synced ? [cur.intro] : []), ...nodes, ...(data.synced ? [cur.outro] : [])));
    body.scrollTop = 0;
    annotate(); // adds the ♪ rows, so the chords can line up with the final set of rows
    document.dispatchEvent(new CustomEvent('lyrics:data', { detail: data }));
    frame(player.position);
  }

  function chip(c) {
    const b = el('b', { className: `ch chord${c.carry ? ' carry' : ''}`, textContent: c.label, title: 'Jump to this chord' });
    b.dataset.ci = String(c.ci);
    b.style.setProperty('--h', c.h); b.style.setProperty('--l', c.l); b.style.setProperty('--c', c.c);
    b.addEventListener('click', (e) => { e.stopPropagation(); manualUntil = 0; player.seek(c.t); });
    return b;
  }

  /** Lay out every line as words → syllables and hang each chord on the syllable it falls on. */
  function annotate() {
    if (!cur) return;
    const { data, nodes } = cur;
    const synced = data.synced;
    const cs = synced ? chords() : [];
    const lines = data.lines;
    cur.timed = lines.map((ln, i) => {
      if (!ln.text || !synced || ln.t == null) return null;
      let next = null;
      for (let k = i + 1; k < lines.length; k++) if (lines[k].t != null) { next = lines[k].t; break; }
      return timeLine(ln.text, ln.t, next, ln.ws, ln.we);
    });
    let ci = 0, sounding = null; // sounding: the last chord that started before the current line
    lines.forEach((ln, i) => {
      const tl = cur.timed[i], n = nodes[i];
      if (!tl) return;
      const lastLine = !cur.timed.slice(i + 1).some(Boolean);
      const firstLine = !cur.timed.slice(0, i).some(Boolean);
      const lo = firstLine ? ln.t - 0.15 : ln.t, hi = lastLine ? tl.end + 0.5 : tl.end;
      while (ci < cs.length && cs[ci].t < lo) sounding = cs[ci++]; // chords in an earlier gap belong to no lyric, but the last one may still ring
      const here = [];
      while (ci < cs.length && cs[ci].t < hi) here.push(cs[ci++]);
      if (sounding && (!here.length || here[0].t > lo + 0.4)) here.unshift({ ...sounding, carry: true }); // the line starts over a chord that began earlier
      sounding = here.findLast((c) => !c.carry) ?? sounding;
      splitAt(tl, here.filter((c) => !c.carry).map((c) => c.t));
      const pieces = tl.syl;
      pieces.forEach((o) => { o.ch = []; });
      for (const c of here) {
        let k = c.carry ? 0 : pieces.findLastIndex((o) => o.t <= c.t + 0.08); // the syllable being sung when the chord changes
        if (k < 0) k = 0;
        pieces[k].ch.push(c);
      }
      const kids = tl.parts.map((p) => {
        if (p.space) return document.createTextNode(p.space);
        return el('span', { className: 'w' }, ...p.pieces.map((o) => {
          const sy = el('span', { className: 'sy' });
          sy.dataset.t = String(o.t);
          sy.dataset.e = String(o.e);
          if (o.ch.length) {
            sy.classList.add('has-ch');
            sy.append(el('span', { className: 'chs' }, ...o.ch.map((c) => {
              return chip(c);
            })));
          }
          sy.append(el('span', { className: 'tx', textContent: o.txt }));
          return sy;
        }));
      });
      n.replaceChildren(...kids);
      n.classList.toggle('with-ch', here.length > 0);
    });
    const firstT = cur.timed.findIndex(Boolean), lastT = cur.timed.findLastIndex(Boolean);
    const intro = firstT < 0 ? [] : cs.filter((c) => c.t < lines[firstT].t - 0.15);
    const outro = lastT < 0 ? [] : cs.filter((c) => c.t >= cur.timed[lastT].end + 0.5);
    for (const [row, list] of [[cur.intro, intro], [cur.outro, outro]]) {
      row.replaceChildren(...(list.length ? [el('span', { className: 'ly-instr-mark', textContent: '♪' }), ...list.map(chip)] : []));
    }
    // an instrumental stretch between two sung lines is one small ♪ row; the chords scroll on while the lyrics wait there
    cur.brks ??= new Map();
    lines.forEach((ln, i) => {
      const tl = cur.timed[i], j = cur.timed.findIndex((x, k) => k > i && x);
      if (!tl) { // a rest the lyrics file already marks with ♪
        if (!ln.text && ln.t != null && j >= 0) cur.brks.set(i, { n: nodes[i], own: true, t0: ln.t, t1: lines[j].t });
        return;
      }
      const marked = lines.slice(i + 1, j < 0 ? i + 1 : j).some((x) => !x.text && x.t != null);
      if (j < 0 || marked || lines[j].t - tl.end < BREAK_S) { if (!cur.brks.get(i)?.own) cur.brks.get(i)?.n.remove(); cur.brks.delete(i); return; }
      let b = cur.brks.get(i);
      if (!b) { b = { n: el('div', { className: 'ly-brk', textContent: '♪' }) }; cur.brks.set(i, b); nodes[i].after(b.n); }
      b.t0 = tl.end; b.t1 = lines[j].t;
      b.n.dataset.t = String(b.t0);
    });
    const lastEnd = lastT < 0 ? null : cur.timed[lastT].end + 0.5;
    if (lastEnd == null) delete cur.outro.dataset.t; else cur.outro.dataset.t = String(lastEnd);
    cur.sy = nodes.map((n) => [...n.querySelectorAll('.sy')]);
    cur.sungTo = -1;
  }

  function frame(pos) {
    if (!cur?.data.synced) return;
    const { lines } = cur.data;
    let lo = 0, hi = lines.length - 1, i = -1;
    while (lo <= hi) { // last line with t <= pos + LEAD
      const mid = (lo + hi) >> 1;
      if (lines[mid].t <= pos + LEAD) { i = mid; lo = mid + 1; } else hi = mid - 1;
    }
    sing(pos);
    for (const b of cur.brks?.values() ?? []) if (!b.own) b.n.classList.toggle('now', pos >= b.t0 && pos < b.t1 - LEAD);
    if (i === cur.active) return;
    cur.active = i;
    cur.nodes.forEach((n, k) => {
      n.classList.toggle('now', k === i);
      n.classList.toggle('past', k < i);
    });
    cur.sy?.forEach((list, k) => { if (k !== i) list.forEach((n) => { n.classList.remove('sung', 'sounding'); n.style.removeProperty('--p'); }); });
    cur.sungTo = -2;
    dock(i);
    sing(pos);
    center(i);
  }

  /** Colour what has been sung in the lit line: whole syllables before the current one, and only the part of the current
   *  syllable that has sounded so far (--p, 0..100), so a word fills in as it is sung. */
  function sing(at) {
    const pos = at + SING_LEAD;
    const i = cur.active, sy = cur.sy?.[i];
    if (!sy?.length) return;
    let k = -1;
    for (let j = 0; j < sy.length; j++) if (Number(sy[j].dataset.t) <= pos) k = j; else break;
    const t0 = k < 0 ? 0 : Number(sy[k].dataset.t), t1 = k < 0 ? 0 : Number(sy[k].dataset.e);
    const p = k < 0 || pos >= t1 || t1 - t0 < 0.18 ? 100 : Math.max(0, Math.min(100, ((pos - t0) / Math.max(0.05, t1 - t0)) * 100));
    const step = Math.round(p / 2); // repaint in 2% steps, not on every frame
    if (k === cur.sungTo && i === cur.sungLine && step === cur.sungStep) return;
    cur.sungTo = k; cur.sungLine = i; cur.sungStep = step;
    for (const list of [sy, cur.dsy ?? []]) {
      list.forEach((n, j) => {
        n.classList.toggle('sung', j < k || (j === k && p >= 100));
        const on = j === k && p < 100;
        n.classList.toggle('sounding', on);
        if (on) n.style.setProperty('--p', String(p)); else n.style.removeProperty('--p');
      });
    }
  }

  /** Mirror the lit line (words and its chords) into the player dock, so it runs next to the transport. */
  function dock(i) {
    const box = $('sing'), n = cur.nodes[i];
    cur.dsy = null;
    const show = !!n && !!cur.timed[i] && (n.classList.contains('with-ch') || chords().length === 0);
    box.hidden = !show;
    $('dock').classList.toggle('has-sing', show);
    if (show) {
      const copy = n.cloneNode(true);
      copy.removeAttribute('role'); copy.removeAttribute('tabindex');
      copy.querySelectorAll('.nt-box').forEach((b) => b.remove()); // stickers stay in the lyrics block
      box.replaceChildren(...copy.childNodes);
      cur.dsy = [...box.querySelectorAll('.sy')];
    } else box.replaceChildren();
    document.dispatchEvent(new Event('lyrics:line'));
  }

  /** Keep the lit line in a band around 40% of the pane; only move (eased) once it leaves the band. */
  function center(i) {
    const n = cur.nodes[i];
    if (!n || lockstep.on || Date.now() < manualUntil || Date.now() < holdUntil || body.offsetParent === null) return;
    const view = body.clientHeight, mid = offsetIn(body, n) + n.offsetHeight / 2;
    if (mid > view * 0.3 && mid < view * 0.6) return;
    glide(body, body.scrollTop + mid - view * 0.4);
  }

  ['wheel', 'touchmove'].forEach((t) => body.addEventListener(t, () => { manualUntil = Date.now() + PAUSE_MS; cancelGlide(body); }, { passive: true }));
  player.addEventListener('seek', () => { manualUntil = 0; });
  let wasPlaying = false;
  player.addEventListener('state', () => { if (player.playing && !wasPlaying) manualUntil = 0; wasPlaying = player.playing; });
  document.addEventListener('blocks:change', () => { if (cur) { cur.active = -2; frame(player.position); } });
  document.addEventListener('chords:change', () => { if (cur?.data.synced && cur.nodes.length) { annotate(); cur.active = -2; frame(player.position); } });

  // Chords above the words (merged) or in their own box beside the lyrics (apart).
  let layout = 'apart';
  try { layout = localStorage.getItem('stems:layout') === 'merged' ? 'merged' : 'apart'; } catch { /* ignore */ }
  const applyLayout = () => {
    $('score').classList.toggle('merged', layout === 'merged');
    $('lyrics').classList.toggle('merged', layout === 'merged');
    const b = $('ly-layout');
    b.textContent = layout === 'merged' ? 'Side by side' : 'Chords above';
    b.setAttribute('aria-pressed', String(layout === 'merged'));
    document.dispatchEvent(new Event('layout:change'));
  };
  $('ly-layout').addEventListener('click', () => {
    layout = layout === 'merged' ? 'apart' : 'merged';
    try { localStorage.setItem('stems:layout', layout); } catch { /* ignore */ }
    applyLayout();
    if (cur) { cur.active = -2; frame(player.position); }
  });
  applyLayout();

  return {
    open(id) {
      close();
      $('lyrics').hidden = false;
      cur = { id, data: { synced: false, lines: [] }, nodes: [], active: -2 };
      load(id);
    },
    close,
    frame,
    pane: () => ({
      root: $('lyrics'), anchor: 0.4,
      box: () => body,
      active: () => !!cur?.data.synced && !$('lyrics').hidden && !$('lyrics').classList.contains('off') && body.offsetParent !== null,
      hold: () => { holdUntil = Date.now() + 4000; cancelGlide(body); },
      following: () => Date.now() >= manualUntil && Date.now() >= holdUntil,
      resume: () => {
        manualUntil = 0; holdUntil = 0;
        const n = cur?.nodes[cur.active];
        if (n) glide(body, body.scrollTop + offsetIn(body, n) + n.offsetHeight / 2 - body.clientHeight * 0.4);
      },
      away: () => {
        const n = cur?.nodes[cur.active];
        if (!n || body.offsetParent === null) return false;
        const m = offsetIn(body, n) + n.offsetHeight / 2;
        return m < 0 || m > body.clientHeight;
      },
      track: () => {
        const mid = (n) => offsetIn(body, n) + body.scrollTop + n.offsetHeight / 2;
        return (cur?.timed ?? []).flatMap((tl, i) => {
          const b = cur.brks?.get(i);
          if (!tl) return b?.own ? [{ y: mid(b.n), t: b.t0 }, { y: mid(b.n), t: Math.max(b.t0, b.t1 - 1.5) }] : [];
          const pts = [{ y: mid(cur.nodes[i]), t: cur.data.lines[i].t }];
          if (b) pts.push({ y: mid(b.n), t: b.t0 }, { y: mid(b.n), t: Math.max(b.t0, b.t1 - 1.5) });
          return pts;
        });
      },
    }),
  };

  function close() {
    token++;
    cur = null;
    document.dispatchEvent(new CustomEvent('lyrics:data', { detail: null }));
    $('sing').hidden = true;
    $('sing').replaceChildren();
    $('dock').classList.remove('has-sing');
    $('lyrics').hidden = true;
    body.replaceChildren();
    $('ly-credit').textContent = '';
  }
}
