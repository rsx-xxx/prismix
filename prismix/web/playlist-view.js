// Saved playlists on the home page: a row of playlist cards, a playlist page with its tracks,
// and the "add to playlist" picker used from library cards.

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const JSON_POST = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const fmt = (s) => { s = Math.max(0, Math.floor(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const art = (id) => (/^u-[0-9a-f]{9}$/.test(id) ? `/media/${id}/thumb.jpg` : `https://i.ytimg.com/vi/${id}/mqdefault.jpg`);

export function createPlaylistView({ api, library, open, notice, quality }) {
  let lists = [];
  let timer = null;
  let current = null; // id of the playlist page that is showing

  async function load() {
    try { lists = await api('/api/playlists'); } catch { lists = []; }
    renderCards();
    return lists;
  }

  function renderCards() {
    const sec = $('pl-sec');
    sec.hidden = !lists.length;
    $('pl-cards').replaceChildren(...lists.map((p) => {
      const first = p.items[0];
      const card = el('button', { type: 'button', className: 'plcard', title: p.name },
        first ? el('img', { src: art(first.id), alt: '', loading: 'lazy' }) : el('div', { className: 'pl-ph' }),
        el('b', { textContent: p.name }), el('small', { className: 'muted', textContent: `${p.items.length} song${p.items.length === 1 ? '' : 's'}` }));
      card.addEventListener('click', () => { location.hash = `#/p/${p.id}`; });
      return card;
    }));
  }

  const find = (id) => lists.find((p) => p.id === id);
  const lib = (id) => library().find((i) => i.id === id);

  async function show(id) {
    stop();
    if (!lists.length) await load();
    const p = find(id);
    if (!p) { location.hash = '#/'; return; }
    current = id;
    $('plpage').hidden = false;
    $('pp-name').textContent = p.name;
    $('pp-count').textContent = `${p.items.length} song${p.items.length === 1 ? '' : 's'}`;
    const rows = $('pp-list');
    rows.replaceChildren(...p.items.map((it, n) => {
      const row = el('div', { className: 'pp-row' });
      row.dataset.id = it.id;
      const go = el('button', { type: 'button', className: 'pp-go' },
        el('span', { className: 'pp-n', textContent: String(n + 1) }),
        el('img', { src: art(it.id), alt: '', loading: 'lazy' }),
        el('span', { className: 'pl-meta' }, el('b', { textContent: it.title || it.id }),
          el('small', { className: 'muted', textContent: [it.channel, it.duration ? fmt(it.duration) : ''].filter(Boolean).join(' · ') })),
        el('span', { className: 'pl-tag', textContent: '' }));
      go.addEventListener('click', () => open(it.id, id));
      const rm = el('button', { type: 'button', className: 'icon-btn', ariaLabel: 'Remove from playlist', title: 'Remove from playlist', textContent: '×' });
      rm.addEventListener('click', async () => {
        try { await api(`/api/playlists/${id}/items/${it.id}`, { method: 'DELETE' }); await load(); show(id); } catch (e) { notice(e.message); }
      });
      const up = el('button', { type: 'button', className: 'icon-btn', ariaLabel: 'Move up', title: 'Move up', textContent: '↑', disabled: n === 0 });
      up.addEventListener('click', () => move(p, n, -1));
      row.append(go, up, rm);
      return row;
    }));
    poll();
  }

  async function move(p, n, d) {
    const ids = p.items.map((i) => i.id);
    [ids[n], ids[n + d]] = [ids[n + d], ids[n]];
    try { await api(`/api/playlists/${p.id}`, JSON_POST('PATCH', { order: ids })); await load(); show(p.id); } catch (e) { notice(e.message); }
  }

  async function poll() {
    if (!current) return;
    let st;
    try { st = await api(`/api/playlists/${current}/status`); } catch { st = null; }
    if (!current) return;
    let busy = false;
    if (st) {
      document.querySelectorAll('#pp-list .pp-row').forEach((row) => {
        const s = st[row.dataset.id];
        const tag = row.querySelector('.pl-tag');
        if (!s || s.ready) { tag.textContent = 'Ready'; return; }
        busy = true;
        tag.textContent = s.job ? `${Math.round(s.job.progress * 100)}%` : 'Not split yet';
      });
      $('pp-status').textContent = busy ? 'Processing…' : '';
    }
    if (busy) timer = setTimeout(poll, 2500);
  }

  function stop() {
    current = null;
    clearTimeout(timer);
    $('plpage').hidden = true;
  }

  $('pp-process').addEventListener('click', async () => {
    if (!current) return;
    try {
      const r = await api(`/api/playlists/${current}/process?model=${encodeURIComponent(quality())}`, { method: 'POST' });
      notice(r.queued ? '' : 'Every song is already prepared.');
      clearTimeout(timer);
      poll();
    } catch (e) { notice(e.message); }
  });
  $('pp-rename').addEventListener('click', async () => {
    const p = find(current);
    const name = p && prompt('Playlist name', p.name);
    if (!name) return;
    try { await api(`/api/playlists/${p.id}`, JSON_POST('PATCH', { name })); await load(); show(p.id); } catch (e) { notice(e.message); }
  });
  $('pp-delete').addEventListener('click', async () => {
    const p = find(current);
    if (!p || !confirm(`Delete the playlist "${p.name}"? The songs stay in your library.`)) return;
    try { await api(`/api/playlists/${p.id}`, { method: 'DELETE' }); await load(); location.hash = '#/'; } catch (e) { notice(e.message); }
  });
  $('pp-back').addEventListener('click', () => { location.hash = '#/'; });

  // add-to-playlist picker
  const dlg = $('addpl');
  function pick(song) {
    const body = $('addpl-body');
    const done = async (fn) => { try { await fn(); await load(); dlg.close(); } catch (e) { dlg.close(); notice(e.message); } };
    const item = { id: song.id, title: song.title, channel: song.channel, duration: song.duration };
    const nw = el('form', { className: 'pl-url' });
    const input = el('input', { type: 'text', placeholder: 'New playlist name', 'aria-label': 'New playlist name', maxLength: 80 });
    nw.append(input, el('button', { className: 'btn primary', type: 'submit', textContent: 'Create' }));
    nw.addEventListener('submit', (e) => {
      e.preventDefault();
      done(() => api('/api/playlists', JSON_POST('POST', { name: input.value.trim() || 'Playlist', items: [item] })));
    });
    $('addpl-title').textContent = `Add "${song.title}"`;
    body.replaceChildren(
      ...lists.map((p) => {
        const has = p.items.some((i) => i.id === song.id);
        const b = el('button', { type: 'button', className: 'pl-row', disabled: has },
          el('span', { className: 'pl-meta' }, el('b', { textContent: p.name }), el('small', { className: 'muted', textContent: `${p.items.length} songs` })),
          has ? el('span', { className: 'pl-tag', textContent: 'Added' }) : '');
        b.addEventListener('click', () => done(() => api(`/api/playlists/${p.id}/items`, JSON_POST('POST', [item]))));
        return b;
      }), nw);
    dlg.showModal();
    input.focus();
  }
  $('addpl-close').addEventListener('click', () => dlg.close());

  /** Save songs (e.g. a YouTube playlist) as a playlist and queue every one for processing. */
  async function save(name, items) {
    const pl = await api('/api/playlists', JSON_POST('POST', { name, items }));
    await api(`/api/playlists/${pl.id}/process?model=${encodeURIComponent(quality())}`, { method: 'POST' });
    await load();
    location.hash = `#/p/${pl.id}`;
    return pl;
  }

  /** Import a YouTube playlist link in one go. */
  async function importUrl(url) {
    const pl = await api('/api/playlists/import', JSON_POST('POST', { url, model: quality() }));
    await load();
    location.hash = `#/p/${pl.id}`;
    return pl;
  }

  return { load, show, stop, pick, save, importUrl, find, all: () => lists, lib };
}
