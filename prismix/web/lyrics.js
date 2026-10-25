// Spotify-style lyrics: the current line is lit and kept centred, click a line to jump to it.
// Lines come from LRCLIB through the server (first open of a track only).

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const LEAD = 0.25; // light a line slightly early: reading is faster than singing
const PAUSE_MS = 4000; // after the user scrolls by hand, leave the view alone this long

export function createLyrics({ player, api }) {
  let cur = null; // { id, data, nodes: HTMLElement[], active: number }
  let token = 0;
  let manualUntil = 0;
  const hidden = () => { try { return localStorage.getItem('stems:lyrics-hidden') === '1'; } catch { return false; } };
  const body = $('ly-body');

  const note = (text, withSearch) => {
    const kids = [el('p', { className: 'muted', textContent: text })];
    if (withSearch) {
      const form = el('form', { className: 'ly-search' });
      const input = el('input', { type: 'text', placeholder: 'Artist and song title', 'aria-label': 'Search lyrics', spellcheck: false });
      form.append(input, el('button', { className: 'btn small', type: 'submit', textContent: 'Search' }));
      form.addEventListener('submit', (e) => { e.preventDefault(); if (input.value.trim() && cur) load(cur.id, input.value.trim()); });
      kids.push(form);
    }
    body.replaceChildren(el('div', { className: 'ly-note' }, ...kids));
  };

  async function load(id, query) {
    const mine = ++token;
    note('Finding lyrics…');
    try {
      const data = await api(`/api/lyrics/${id}${query ? `?q=${encodeURIComponent(query)}` : ''}`);
      if (mine !== token) return;
      render(id, data);
    } catch (err) {
      if (mine === token) note(err.message, true);
    }
  }

  function render(id, data) {
    const nodes = data.lines.map((ln) => {
      const n = el('button', { type: 'button', className: `ly-line${ln.text ? '' : ' gap'}`, textContent: ln.text || '♪' });
      if (data.synced && ln.t != null) n.addEventListener('click', () => { manualUntil = 0; player.seek(ln.t); });
      else n.disabled = true;
      return n;
    });
    cur = { id, data, nodes, active: -2 };
    $('ly-credit').textContent = `${data.artist} – ${data.title} · ${data.synced ? 'synced' : 'not synced'} · LRCLIB`;
    body.replaceChildren(el('div', { className: `ly-lines${data.synced ? '' : ' plain'}` }, ...nodes));
    body.scrollTop = 0;
    frame(player.position);
  }

  function frame(pos) {
    if (!cur?.data.synced) return;
    const { lines } = cur.data;
    let lo = 0, hi = lines.length - 1, i = -1;
    while (lo <= hi) { // last line with t <= pos + LEAD
      const mid = (lo + hi) >> 1;
      if (lines[mid].t <= pos + LEAD) { i = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (i === cur.active) return;
    cur.active = i;
    cur.nodes.forEach((n, k) => {
      n.classList.toggle('now', k === i);
      n.classList.toggle('past', k < i);
    });
    center(i);
  }

  function center(i) {
    const n = cur.nodes[i];
    if (!n || Date.now() < manualUntil || body.offsetParent === null) return;
    body.scrollTo({ top: n.offsetTop - body.clientHeight / 2 + n.offsetHeight / 2, behavior: 'smooth' });
  }

  ['wheel', 'touchmove'].forEach((t) => body.addEventListener(t, () => { manualUntil = Date.now() + PAUSE_MS; }, { passive: true }));
  player.addEventListener('seek', () => { manualUntil = 0; });

  function setHidden(h) {
    $('lyrics').classList.toggle('collapsed', h);
    $('ly-toggle').textContent = h ? 'Show' : 'Hide';
    try { localStorage.setItem('stems:lyrics-hidden', h ? '1' : '0'); } catch { /* ignore */ }
    if (!h && cur) { cur.active = -2; frame(player.position); }
  }
  $('ly-toggle').addEventListener('click', () => setHidden(!$('lyrics').classList.contains('collapsed')));

  return {
    open(id) {
      close();
      $('lyrics').hidden = false;
      setHidden(hidden());
      cur = { id, data: { synced: false, lines: [] }, nodes: [], active: -2 };
      load(id);
    },
    close,
    frame,
    toggle: () => $('ly-toggle').click(),
  };

  function close() {
    token++;
    cur = null;
    $('lyrics').hidden = true;
    body.replaceChildren();
    $('ly-credit').textContent = '';
  }
}
