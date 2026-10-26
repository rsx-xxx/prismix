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
const SONG = 'application/x-prismix-song';
const itemOf = (s) => ({ id: s.id, title: s.title, channel: s.channel, duration: s.duration });
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
      card.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes(SONG)) { e.preventDefault(); card.classList.add('drop'); } });
      card.addEventListener('dragleave', () => card.classList.remove('drop'));
      card.addEventListener('drop', async (e) => {
        e.preventDefault();
        card.classList.remove('drop');
        const song = lib(e.dataTransfer.getData(SONG));
        if (!song) return;
        if (p.items.some((i) => i.id === song.id)) { notice(`Already in "${p.name}"`); return; }
        try { await api(`/api/playlists/${p.id}/items`, JSON_POST('POST', [itemOf(song)])); await load(); notice(`Added to "${p.name}"`); } catch (err) { notice(err.message); }
      });
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
        el('img', { src: art(it.id), alt: '', loading: 'lazy', draggable: false }),
        el('span', { className: 'pl-meta' }, el('b', { textContent: it.title || it.id }),
          el('small', { className: 'muted', textContent: [it.channel, it.duration ? fmt(it.duration) : ''].filter(Boolean).join(' · ') })),
        el('span', { className: 'pl-tag', textContent: '' }));
      go.addEventListener('click', () => open(it.id, id));
      const rm = el('button', { type: 'button', className: 'icon-btn', ariaLabel: 'Remove from playlist', title: 'Remove from playlist', textContent: '×' });
      rm.addEventListener('click', async () => {
        try { await api(`/api/playlists/${id}/items/${it.id}`, { method: 'DELETE' }); await load(); show(id); } catch (e) { notice(e.message); }
      });
      const grip = el('span', { className: 'pp-grip', role: 'button', tabIndex: 0, ariaLabel: 'Drag to reorder', title: 'Drag to move: reorder, or drop on another playlist or Library (Alt+↑/↓ from the keyboard)' }, el('i'), el('i'), el('i'));
      grip.addEventListener('pointerdown', (e) => { if (e.button === 0) { e.preventDefault(); drag(e, row, p); } });
      grip.addEventListener('keydown', (e) => {
        if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
        e.preventDefault();
        move(p, n, e.key === 'ArrowUp' ? -1 : 1);
      });
      row.addEventListener('pointerdown', (e) => {
        if (e.pointerType !== 'mouse' || e.button !== 0 || !e.target.closest('.pp-go')) return;
        const x = e.clientX, y = e.clientY;
        const probe = (ev) => {
          if (Math.hypot(ev.clientX - x, ev.clientY - y) < 6) return;
          stop();
          addEventListener('click', (c) => { c.stopPropagation(); c.preventDefault(); }, { capture: true, once: true });
          drag(ev, row, p);
        };
        const stop = () => { removeEventListener('pointermove', probe); removeEventListener('pointerup', stop); };
        addEventListener('pointermove', probe);
        addEventListener('pointerup', stop);
      });
      row.append(grip, go, rm);
      return row;
    }));
    poll();
  }

  async function move(p, n, d) {
    if (n + d < 0 || n + d >= p.items.length) return;
    const ids = p.items.map((i) => i.id);
    [ids[n], ids[n + d]] = [ids[n + d], ids[n]];
    await saveOrder(p, ids);
  }

  async function saveOrder(p, ids) {
    try { await api(`/api/playlists/${p.id}`, JSON_POST('PATCH', { order: ids })); await load(); } catch (e) { notice(e.message); }
    show(p.id);
  }

  /** Move a song out of this playlist: into another one, or back to the library alone. */
  async function moveTo(p, vid, target) {
    const it = p.items.find((i) => i.id === vid);
    try {
      if (target !== 'lib') await api(`/api/playlists/${target}/items`, JSON_POST('POST', [it]));
      await api(`/api/playlists/${p.id}/items/${vid}`, { method: 'DELETE' });
      await load();
      notice(target === 'lib' ? 'Taken out of the playlist; it stays in the library' : `Moved to "${find(target)?.name}"`);
    } catch (e) { notice(e.message); }
    show(p.id);
  }

  /** Drag a song by its row: a copy follows the pointer, the others make room live, the order is saved on release. */
  function drag(e, row, p) {
    const list = $('pp-list');
    const box = row.getBoundingClientRect();
    const dy = e.clientY - box.top;
    const ghost = row.cloneNode(true);
    ghost.classList.add('pp-ghost');
    Object.assign(ghost.style, { width: `${box.width}px`, left: `${box.left}px`, top: `${box.top}px` });
    document.body.append(ghost);
    row.classList.add('drag');
    document.body.classList.add('dragging');
    const bar = el('div', { className: 'pp-targets' },
      ...lists.filter((x) => x.id !== p.id).map((x) => el('span', { className: 'pp-target', textContent: x.name })),
      el('span', { className: 'pp-target', textContent: 'Library (take out)' }));
    [...lists.filter((x) => x.id !== p.id).map((x) => x.id), 'lib'].forEach((id, i) => { bar.children[i].dataset.target = id; });
    document.body.append(bar);
    const hit = (ev) => document.elementFromPoint(ev.clientX, ev.clientY)?.closest('.pp-target');
    const renumber = () => list.querySelectorAll('.pp-n').forEach((n, i) => { n.textContent = String(i + 1); });
    let y = e.clientY, scroll = 0, raf = 0;
    const settle = () => {
      for (const other of list.children) {
        if (other === row) continue;
        const r = other.getBoundingClientRect();
        if (y < r.top || y > r.bottom) continue;
        list.insertBefore(row, y > r.top + r.height / 2 ? other.nextSibling : other);
        renumber();
        break;
      }
    };
    const tick = () => {
      if (scroll) { window.scrollBy(0, scroll); settle(); }
      raf = requestAnimationFrame(tick);
    };
    const move = (ev) => {
      y = ev.clientY;
      ghost.style.top = `${y - dy}px`;
      const t = hit(ev);
      bar.querySelectorAll('.pp-target').forEach((c) => c.classList.toggle('on', c === t));
      scroll = y < 90 ? -16 : y > innerHeight - 130 ? 16 : 0;
      if (!t) settle();
    };
    const end = (ev) => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', end);
      removeEventListener('pointercancel', end);
      cancelAnimationFrame(raf);
      const t = ev.type === 'pointerup' ? hit(ev) : null;
      ghost.remove(); bar.remove();
      row.classList.remove('drag');
      document.body.classList.remove('dragging');
      if (t) { moveTo(p, row.dataset.id, t.dataset.target); return; }
      const ids = [...list.children].map((r) => r.dataset.id);
      if (ids.every((id, i) => id === p.items[i].id)) renumber(); else saveOrder(p, ids);
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', end);
    addEventListener('pointercancel', end);
    raf = requestAnimationFrame(tick);
    move(e);
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
        const tag = row.querySelector('.pl-tag, .pl-dot');
        if (!s || s.ready) { tag.className = 'pl-dot'; tag.title = 'Ready'; tag.textContent = ''; return; }
        busy = true;
        tag.className = 'pl-tag'; tag.title = '';
        const queued = s.job?.stage === 'queued';
        tag.classList.add(s.job ? (queued ? 'q' : 'p') : 'n');
        tag.textContent = s.job ? (s.job.stage === 'queued' ? 'Queued' : `${Math.round(s.job.progress * 100)}%`) : 'Not split yet';
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
    const item = itemOf(song);
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

  return { refresh: () => { clearTimeout(timer); poll(); }, load, show, stop, pick, save, importUrl, find, all: () => lists, lib };
}
