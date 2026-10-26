// Guitar and bass tab (Songsterr, matched to the recording's bars on the server): a block of its own, bar by bar, following playback.
const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
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

export function createTab() {
  const root = $('tabview'), body = $('tab-body'), credit = $('tab-credit'), pick = $('tab-part');
  let cur = null; // { id, tab, st, secs: bar start seconds, cells: [], row: -1 }
  let part = null;
  let holdUntil = 0;
  let width = 0;

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
    cur = { id, tab, st, secs: st.bars.map((b) => beatSec(st.beats, b)), cells: [], row: -1 };
    if (!tab.tracks[part]) part = tab.tracks[localStorage.getItem(PART_KEY)] ? localStorage.getItem(PART_KEY) : Object.keys(tab.tracks)[0];
    root.hidden = false;
    render();
  }

  function render() {
    if (!cur) return;
    const { tab, st, secs } = cur;
    const p = tab.tracks[part];
    credit.replaceChildren(`${p.name.split('|').at(-1).trim() || part} · tab by `, el('a', { href: tab.url, target: '_blank', rel: 'noopener', textContent: 'Songsterr' }));
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
    const strings = p.tuning.length;
    const box = el('div', { className: 'tab', style: `--strings:${strings}` });
    let row = null;
    for (let j = Math.max(0, first - 1); j <= Math.min(secs.length - 1, last + 1); j++) {
      if (!row || (cur.cells.filter(Boolean).length % per === 0)) { row = el('div', { className: 'trow', style: `--cols:${per}` }); box.append(row); }
      const tb = of.has(j) ? p.bars[of.get(j)] : null;
      const cell = el('div', { className: `tbar${tb ? '' : ' blank'}` }, el('span', { className: 'n', textContent: String(j + (st.bars[0] > 0 ? 0 : 1)) }));
      if (tb?.mark) cell.append(el('span', { className: 'tag', textContent: tb.mark }));
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

  function frame(pos, following = true) {
    if (!cur || root.hidden || root.classList.contains('off')) return;
    if (body.clientWidth !== width && Math.abs(body.clientWidth - width) > 40) render();
    const j = floorIndex(cur.secs, pos);
    const cell = cur.cells[j];
    if (j !== cur.now) {
      cur.cells[cur.now]?.classList.remove('now');
      cur.now = j;
      cell?.classList.add('now');
      if (cell && following && Date.now() >= holdUntil) {
        const r = cell.parentElement;
        body.scrollTo({ top: r.offsetTop - body.clientHeight * 0.3, behavior: 'smooth' });
      }
    }
    if (cell) cell.querySelector('.cursor').style.setProperty('--x', `${Math.min(1, Math.max(0, (pos - cur.secs[j]) / (cur.ends[j] - cur.secs[j]))) * 100}%`);
  }

  for (const ev of ['wheel', 'touchstart', 'pointerdown']) body.addEventListener(ev, () => { holdUntil = Date.now() + 4000; }, { passive: true });
  return { open, close, frame };
}
