// Landmarks on the player's time scale: where the music starts (energy of the stems + SponsorBlock), where the lyrics start,
// and the song's sections. Drawn on the seek bar, over the stem lanes and as a labelled band above the seek bar.

import { fromLyrics, fromChords, fitLabel, WORDS } from './sections.js';

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const MIN = 2.5; // a start this close to 0:00 is not worth a marker

export function createMarkers({ player, api, bars = () => [] }) {
  let st = null; // { id, start, bad: [{s, e}], lyricsStart, sections, lines }
  let token = 0;
  const pct = (t) => `${Math.min(100, Math.max(0, (t / player.duration) * 100))}%`;
  const span = (a, b) => ({ left: pct(a), width: `${Math.max(0, ((b - a) / player.duration) * 100)}%` });

  const seekLayer = el('div', { className: 'mk mk-seek' });
  const laneLayer = el('div', { className: 'mk mk-lane' });
  $('seek').prepend(seekLayer);
  $('lane-col').insertBefore(laneLayer, $('playhead'));

  const flag = (cls, t, text) => {
    const f = el('i', { className: `mk-flag ${cls}`, title: `${text} · ${fmt(t)}` });
    f.style.left = pct(t);
    return f;
  };
  const place = (node, box) => { Object.assign(node.style, box); return node; };

  function draw() {
    const live = st && player.duration > 0;
    seekLayer.replaceChildren();
    laneLayer.replaceChildren();
    const band = $('secband');
    band.replaceChildren();
    band.hidden = !live || !st.sections.length;
    if (!live) return;
    const quiet = [...(st.start >= MIN ? [{ s: 0, e: st.start }] : []), ...st.bad];
    for (const q of quiet) {
      seekLayer.append(place(el('i', { className: 'mk-quiet' }), span(q.s, q.e)));
      laneLayer.append(place(el('i', { className: 'mk-quiet' }), span(q.s, q.e)));
    }
    if (st.start >= MIN) {
      seekLayer.append(flag('music', st.start, 'Music starts'));
      laneLayer.append(flag('music', st.start, 'Music starts'));
    }
    if (st.lyricsStart != null && st.lyricsStart >= MIN) {
      seekLayer.append(flag('lyrics', st.lyricsStart, 'Lyrics start'));
      laneLayer.append(flag('lyrics', st.lyricsStart, 'Lyrics start'));
    }
    st.sections.forEach((s) => {
      band.append(place(el('button', { type: 'button', className: 'sec', title: `${s.label} · ${fmt(s.start)}` }, el('span')), span(s.start, s.end)));
      band.lastChild.dataset.kind = s.kind;
      band.lastChild.addEventListener('click', () => player.seek(s.start));
    });
    fit();
    cur = -2;
    frame(player.position);
  }

  /** Each part names itself as far as its width allows: full word, abbreviation, letter, symbol, or just its colour. */
  function fit() {
    const band = $('secband'), w = band.clientWidth;
    if (!st?.sections.length || !w) return;
    [...band.children].forEach((node, i) => {
      const s = st.sections[i];
      node.firstChild.textContent = fitLabel(s, (s.end - s.start) / player.duration * w);
    });
  }

  let cur = -2;
  function frame(pos) {
    if (!st?.sections.length) return;
    const i = st.sections.findLastIndex((s) => s.start <= pos + 0.05 && pos < s.end);
    if (i === cur) return;
    cur = i;
    [...$('secband').children].forEach((n, k) => n.classList.toggle('cur', k === i));
  }

  function recompute() {
    if (!st) return;
    const synced = st.lines.filter((l) => l.t != null && l.text);
    st.lyricsStart = synced.length ? synced[0].t : null;
    const fromText = synced.length ? fromLyrics(st.lines, player.duration) : [];
    st.sections = fromText.length ? fromText : fromChords(bars(), player.duration);
    publish();
  }
  /** Tag chart cells and lyric lines: dim what precedes the music, label where sections and the lyrics begin. */
  function decorate() {
    const cells = [...document.querySelectorAll('#sc-body .cell[data-t]')];
    const lines = [...document.querySelectorAll('#ly-body .ly-line[data-t]')];
    for (const n of [...cells, ...lines]) { n.classList.remove('pre'); delete n.dataset.sec; delete n.dataset.sym; delete n.dataset.kind; delete n.dataset.ly; }
    if (!st) return;
    const at = (list, t) => list.findLast((n) => Number(n.dataset.t) <= t + 0.05);
    if (st.start >= MIN) for (const c of cells) if (Number(c.dataset.t) < st.start - 0.05) c.classList.add('pre');
    const tag = (n, s) => { if (n && !n.dataset.sec) { n.dataset.sec = s.label; n.dataset.sym = WORDS[s.kind][3]; n.dataset.kind = s.kind; } };
    for (const s of st.sections) { tag(at(cells, s.start), s); tag(lines.find((n) => Math.abs(Number(n.dataset.t) - s.start) < 0.05), s); }
    if (st.lyricsStart != null && st.lyricsStart >= MIN) { const c = at(cells, st.lyricsStart); if (c) c.dataset.ly = '1'; }
  }
  document.addEventListener('chords:change', () => { if (st && !st.lines.some((l) => l.t != null && l.text)) recompute(); else decorate(); });

  function publish() {
    draw();
    decorate();
    document.dispatchEvent(new CustomEvent('markers:change', { detail: st }));
  }

  document.addEventListener('lyrics:data', (e) => { if (st) { st.lines = e.detail?.synced ? e.detail.lines : []; recompute(); } });
  player.addEventListener('state', () => { if (st && player.duration && !st.laidOut) { st.laidOut = true; draw(); } });
  new ResizeObserver(fit).observe($('secband'));

  async function open(id) {
    close();
    const mine = ++token;
    st = { id, start: 0, bad: [], lyricsStart: null, sections: [], lines: [] };
    try {
      const m = await api(`/api/markers/${id}`);
      if (mine !== token || !st) return;
      st.start = m.start;
      st.bad = (m.sb ?? []).map((s) => ({ s: s.s, e: s.e })).filter((s) => s.s >= MIN || s.e - s.s > 1);
      st.laidOut = false;
      publish();
    } catch { /* markers are optional: the timeline just stays plain */ }
  }
  function close() {
    token++;
    st = null;
    draw();
    decorate();
    document.dispatchEvent(new CustomEvent('markers:change', { detail: null }));
  }
  return { open, close, frame, state: () => st };
}
