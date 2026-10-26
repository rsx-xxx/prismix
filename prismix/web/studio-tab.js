// Guitar and bass tab (Songsterr or a Soundslice link, matched to the recording's bars on the server): bar by bar, with the chord above each bar,
// taking the place of the Chords box beside the lyrics and scrolling in step with them.
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
import { glide, cancelGlide, offsetIn, lockstep } from './scroll.js';
const NOTES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
const BAR_MIN = 11; // rem a bar needs before a row takes one more
const PART_KEY = 'stems:tab-part';

function floorIndex(arr, x) {
  let lo = 0, hi = arr.length - 1, r = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= x) { r = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return r;
}

export function createTab({ player, chordsIn = () => [] }) {
  const root = $('tabview'), body = $('tab-body'), credit = $('tab-credit'), pick = $('tab-part');
  let cur = null; // { id, tab, st, secs: bar start seconds, cells: [], row: -1 }
  let part = null;
  let holdUntil = 0;
  let width = 0;
  let follow = true;

  const beatSec = (beats, b) => {
    if (b <= 0) return beats[0] + b * (beats[1] - beats[0]);
    if (b >= beats.length - 1) return beats.at(-1) + (b - beats.length + 1) * (beats.at(-1) - beats.at(-2));
    const i = Math.floor(b);
    return beats[i] + (beats[i + 1] - beats[i]) * (b - i);
  };

  function close() {
    cur = null;
    root.hidden = true;
    body.replaceChildren();
    pick.replaceChildren();
    credit.replaceChildren();
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
    cur = { id, tab, st, secs: st.bars.map((b) => beatSec(st.beats, b)), cells: [], rows: [], row: -1 };
    if (!tab.tracks[part]) part = tab.tracks[localStorage.getItem(PART_KEY)] ? localStorage.getItem(PART_KEY) : Object.keys(tab.tracks)[0];
    root.hidden = false;
    render();
  }

  function render() {
    if (!cur) return;
    const { tab, st, secs } = cur;
    const p = tab.tracks[part];
    credit.replaceChildren(`${p.name.split('|').at(-1).trim() || part} · tab by `, el('a', { href: tab.url, target: '_blank', rel: 'noopener', textContent: tab.source === 'soundslice' ? 'Soundslice' : 'Songsterr' }));
    pick.replaceChildren(...Object.keys(tab.tracks).map((k) => {
      const b = el('button', { type: 'button', className: 'btn small ghost', textContent: k[0].toUpperCase() + k.slice(1) });
      b.setAttribute('aria-pressed', String(k === part));
      b.addEventListener('click', () => { part = k; try { localStorage.setItem(PART_KEY, k); } catch { /* ignore */ } render(); });
      return b;
    }));
    pick.hidden = Object.keys(tab.tracks).length < 2;

    const per = Math.max(1, Math.floor(body.clientWidth / (BAR_MIN * parseFloat(getComputedStyle(document.documentElement).fontSize))));
    width = body.clientWidth;
    const of = new Map(); // recording bar -> tab bar
    tab.map.forEach((j, i) => { if (j != null && !of.has(j) && i < p.bars.length) of.set(j, i); });
    const first = Math.min(...of.keys()), last = Math.max(...of.keys());
    const diffs = st.bars.slice(1).map((b, i) => b - st.bars[i]).sort((a, b) => a - b);
    const bpb = diffs.length ? diffs[diffs.length >> 1] : 4;
    cur.ends = secs.map((_, j) => (j + 1 < secs.length ? secs[j + 1] : beatSec(st.beats, st.bars[j] + bpb)));
    cur.cells = [];
    cur.rows = [];
    const strings = p.tuning.length;
    const box = el('div', { className: 'tab', style: `--strings:${strings}` });
    let row = null;
    for (let j = Math.max(0, first - 1); j <= Math.min(secs.length - 1, last + 1); j++) {
      if (!row || (cur.cells.filter(Boolean).length % per === 0)) {
        row = el('div', { className: 'trow', style: `--cols:${per}` },
          el('div', { className: 'strs', 'aria-hidden': 'true' }, ...p.tuning.map((m) => el('span', { textContent: NOTES[m % 12] }))));
        box.append(row);
        cur.rows.push({ row, first: j });
      }
      const tb = of.has(j) ? p.bars[of.get(j)] : null;
      const cell = el('div', { className: `tbar${tb ? '' : ' blank'}` }, el('span', { className: 'n', textContent: String(j + (st.bars[0] > 0 ? 0 : 1)) }));
      if (tb?.mark) cell.append(el('span', { className: 'tag', textContent: tb.mark }));
      const chs = el('div', { className: 'tch' });
      let before = j > 0 ? chordsIn(j - 1).at(-1)?.name : '';
      for (const c of chordsIn(j)) {
        chs.append(el('span', { className: c.name === before ? 'same' : '', textContent: c.name, style: `left:${c.f * 100}%` }));
        before = c.name;
      }
      cell.append(chs);
      const stf = el('div', { className: 'stf' });
      if (tb) {
        for (const [pos, , frets] of tb.n) for (const [s, f] of frets) {
          stf.append(el('span', { className: 'f', textContent: String(f), style: `left:${pos * 100}%;top:calc(${s} * var(--row))` }));
        }
      }
      stf.append(el('span', { className: 'cursor' }));
      cell.append(stf);
      cell.addEventListener('click', () => document.dispatchEvent(new CustomEvent('tab:seek', { detail: secs[j] })));
      row.append(cell);
      cur.cells[j] = cell;
    }
    body.replaceChildren(box);
    cur.row = -1;
    cur.now = -1;
  }

  const centre = (cell, soft) => {
    const r = cell.parentElement, want = r.offsetTop - body.clientHeight * 0.4 + r.offsetHeight / 2;
    soft ? glide(body, want) : body.scrollTo({ top: want });
  };

  function frame(pos) {
    if (!cur || root.hidden || root.classList.contains('off') || body.offsetParent === null) return;
    if (body.clientWidth !== width && Math.abs(body.clientWidth - width) > 40) render();
    const j = floorIndex(cur.secs, pos);
    const cell = cur.cells[j];
    if (j !== cur.now) {
      cur.cells[cur.now]?.classList.remove('now');
      cur.now = j;
      cell?.classList.add('now');
      if (cell && !lockstep.on && follow && Date.now() >= holdUntil) {
        const m = offsetIn(body, cell.parentElement) + cell.parentElement.offsetHeight / 2;
        if (m < body.clientHeight * 0.3 || m > body.clientHeight * 0.6) centre(cell, true);
      }
    }
    if (cell) cell.querySelector('.cursor').style.setProperty('--x', `${Math.min(1, Math.max(0, (pos - cur.secs[j]) / (cur.ends[j] - cur.secs[j]))) * 100}%`);
  }

  const manual = () => { holdUntil = Date.now() + 4000; follow = false; cancelGlide(body); };
  for (const ev of ['wheel', 'touchmove']) body.addEventListener(ev, manual, { passive: true });
  document.addEventListener('chords:change', () => { if (cur && !root.hidden) { const n = cur.now; render(); cur.now = -1; if (n >= 0) frame(cur.secs[n]); } });
  const resume = () => { follow = true; holdUntil = 0; };
  player.addEventListener('seek', resume);
  let wasPlaying = false;
  player.addEventListener('state', () => { if (player.playing && !wasPlaying) resume(); wasPlaying = player.playing; });

  return {
    open, close, frame,
    pane: () => ({
      root: $('tabview'), anchor: 0.4,
      box: () => body,
      active: () => !!cur && !root.hidden && !root.classList.contains('off') && body.offsetParent !== null,
      hold: manual,
      following: () => follow && Date.now() >= holdUntil,
      track: () => (cur?.rows ?? []).map(({ row, first }, i) => {
        const last = (cur.rows[i + 1]?.first ?? cur.cells.length) - 1;
        return { y: row.offsetTop + row.offsetHeight / 2, t: (cur.secs[first] + cur.ends[last]) / 2 };
      }),
      resume: () => { resume(); const c = cur?.cells[cur.now]; if (c) centre(c, true); },
      away: () => {
        const c = cur?.cells[cur.now];
        if (!c) return false;
        const m = offsetIn(body, c.parentElement) + c.parentElement.offsetHeight / 2;
        return m < 0 || m > body.clientHeight;
      },
    }),
  };
}
