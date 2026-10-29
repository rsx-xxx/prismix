// The band cloud on the desktop: sign in with an invite link, publish processed songs, and download the band's songs into the library.
export function createCloud({ $, el, api, notice, fmt, onChange, onDownloaded, lookup }) {
  const state = { signedIn: false, host: '', band: { name: 'Band cloud', logo: '' }, songs: [], playing: null, local: new Set(), syncing: false };
  const audio = $('cloud-audio');

  const post = (path, body) => api(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) });
  const fileUrl = (id, rel) => `/api/cloud/files/${id}/${rel}`;

  async function refresh() {
    try { Object.assign(state, await api('/api/cloud')); } catch { state.signedIn = false; }
    state.songs = [];
    if (state.signedIn) {
      try { state.songs = (await api('/api/cloud/index')).songs; } catch (e) {
        if (/refused/i.test(e.message)) { state.signedIn = false; }
        notice(e.message);
      }
    }
    render();
  }

  const logoUrl = () => (state.band.logo ? `/${state.band.logo}` : '');
  const inCloud = new Set();
  const ICON_CLOUD = (arrow, filled) => `<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8.5a4.75 4.75 0 0 1-.5 9.5Z" fill="${filled ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>${arrow === 'up' ? `<path d="M12 16v-5m0 0-2 2m2-2 2 2" fill="none" stroke="${filled ? 'var(--bg, #000)' : 'currentColor'}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>` : arrow === 'down' ? '<path d="M12 10v5m0 0-2-2m2 2 2-2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' : ''}</svg>`;

  // Older uploads may lack cover.jpg: fall back to the YouTube thumbnail instead of a blank box.
  function cover(s) {
    const img = el('img', { className: 'cloud-cover', alt: '', loading: 'lazy', src: fileUrl(s.id, 'cover.jpg') });
    img.addEventListener('error', () => { img.onerror = null; img.src = `https://i.ytimg.com/vi/${s.id}/mqdefault.jpg`; }, { once: true });
    return img;
  }

  function render() {
    inCloud.clear();
    state.songs.forEach((s) => inCloud.add(s.id));
    const btn = $('cloud-btn');
    btn.replaceChildren(...(state.signedIn && state.band.logo ? [el('img', { className: 'band-logo', alt: '', src: logoUrl() })] : []),
      document.createTextNode(state.signedIn ? state.band.name : 'Band cloud sign-in'));
    const side = $('side-cloud');
    if (side) {
      side.dataset.icon ||= side.querySelector('svg').outerHTML;
      const label = side.querySelector('span');
      side.querySelector('svg, img').replaceWith(...(state.signedIn && state.band.logo ? [el('img', { className: 'side-avatar', alt: '', src: logoUrl() })] : [new DOMParser().parseFromString(side.dataset.icon, 'text/html').body.firstChild]));
      label.textContent = state.signedIn ? state.band.name : 'Band cloud';
    }
    $('cloud-title').textContent = state.band.logo ? `${state.band.name} cloud` : state.band.name;
    $('cloud-logo').hidden = !state.band.logo;
    $('cloud-logo').src = logoUrl();
    const sec = $('cloud-section');
    const only = state.songs.filter((s) => !state.local.has(s.id));
    sec.hidden = !state.signedIn || only.length === 0;
    if (!state.signedIn) stop();
    $('cloud-count').textContent = only.length ? `${only.length} song${only.length > 1 ? 's' : ''}` : '';
    $('cloud-list').replaceChildren(...only.map((s) => {
      const play = el('button', { className: 'btn small ghost', type: 'button', textContent: state.playing === s.id ? 'Pause' : 'Listen' });
      play.addEventListener('click', () => toggle(s));
      const get = el('button', { className: 'btn small', type: 'button', innerHTML: ICON_CLOUD('down'), title: 'Download into your library', ariaLabel: 'Download into your library' });
      get.addEventListener('click', () => download(s, get));
      const del = el('button', { className: 'btn small ghost danger', type: 'button', textContent: '✕', title: 'Delete from the cloud', ariaLabel: 'Delete from the cloud' });
      del.addEventListener('click', () => remove(s));
      return el('li', { className: 'cloud-row' },
        cover(s),
        el('div', { className: 'cloud-text' },
          el('b', { textContent: s.title || s.id }),
          el('span', { className: 'muted', textContent: `${s.artist || ''} · ${fmt(s.duration)} · ${(s.bytes / 1048576).toFixed(1)} MB` })),
        play, get, del);
    }));
    const { up, down } = plan();
    const sb = $('cloud-sync');
    sb.hidden = !state.signedIn;
    if (!state.syncing) sb.querySelector('span').textContent = up.length || down.length ? `Sync${up.length ? ` ↑${up.length}` : ''}${down.length ? ` ↓${down.length}` : ''}` : 'In sync';
    onChange?.();
  }

  // Which library songs the cloud has, and the cloud button each library card shows.
  function setLocal(ids) {
    const next = new Set(ids);
    const same = next.size === state.local.size && [...next].every((i) => state.local.has(i));
    state.local = next;
    if (!same) render();
  }
  // The cloud button of one song, by where it lives: on this computer only → Upload (always visible);
  // on both → a white filled cloud (shown on hover); in the cloud only (a playlist entry) → Download.
  function mark(id) {
    if (!state.signedIn) return null;
    const local = lookup?.(id);
    const has = inCloud.has(id);
    if (!local && !has) return null;
    const song = state.songs.find((x) => x.id === id);
    const kind = !local ? 'get' : has ? 'on' : 'up';
    const b = el('button', { className: `pub ${kind}`, type: 'button', innerHTML: ICON_CLOUD(kind === 'up' ? 'up' : kind === 'get' ? 'down' : '', kind === 'on') });
    b.title = b.ariaLabel = kind === 'on' ? `In ${state.band.name}, click to delete it from there` : kind === 'up' ? `Upload to ${state.band.name}` : 'Download into your library';
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      if (kind === 'up') publish(local.item, local.model, b);
      else if (kind === 'get' && song) download(song, b);
      else if (kind === 'on' && song) remove(song);
    });
    return b;
  }
  const badge = (item) => mark(item.id);

  function toggle(s) {
    if (state.playing === s.id) { stop(); render(); return; }
    audio.src = fileUrl(s.id, 'mix.m4a');
    audio.play().catch((e) => notice(`Cannot play: ${e.message}`));
    state.playing = s.id;
    $('cloud-now').textContent = s.title || s.id;
    $('cloud-player').hidden = false;
    render();
  }

  function stop() {
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    state.playing = null;
    $('cloud-player').hidden = true;
  }
  audio.addEventListener('ended', () => { state.playing = null; render(); });

  // ── publishing and downloading: both are background jobs on the computer, polled until they settle ──
  async function runJob(path, body, onStep, every, cancelBtn) {
    const { job } = await post(path, body);
    if (cancelBtn) cancelBtn.addEventListener('click', (e) => { e.stopPropagation(); cancelBtn.disabled = true; post(`/api/cloud/publish/${job}/cancel`).catch(() => {}); });
    for (;;) {
      const j = await api(`/api/cloud/publish/${job}`);
      onStep(j.state === 'running' && j.total ? `${j.done}/${j.total}` : '…');
      if (j.state === 'done') return;
      if (j.state === 'error') throw new Error(j.error);
      await new Promise((r) => setTimeout(r, every));
    }
  }

  async function publish(item, model, button) {
    if (!state.signedIn) { openLogin(); return; }
    button.disabled = true;
    const cancel = el('button', { className: 'pub stop', type: 'button', textContent: '✕', title: 'Cancel the upload', ariaLabel: 'Cancel the upload' });
    button.after(cancel);
    try {
      await runJob('/api/cloud/publish', { videoId: item.id, model }, (t) => { button.textContent = t; }, 700, cancel);
      await refresh();
    } catch (e) { if (e.message !== 'Cancelled') notice(e.message); } finally { cancel.remove(); onChange?.(); }
  }

  /** Remove a song from the cloud for everyone, after a confirmation. */
  async function remove(s) {
    if (!confirm(`Delete "${s.title || s.id}" from ${state.band.name}? It disappears for the whole band.`)) return;
    try {
      await api(`/api/cloud/songs/${s.id}`, { method: 'DELETE' });
      if (state.playing === s.id) stop();
      await refresh();
    } catch (e) { notice(e.message); }
  }

  async function download(s, button) {
    button.disabled = true;
    try {
      await runJob('/api/cloud/download', { videoId: s.id }, (t) => { button.textContent = t; }, 500);
      await onDownloaded?.();
    } catch (e) { notice(e.message); button.disabled = false; render(); }
  }

  // ── sync everything: upload what the cloud lacks, download what this computer lacks (never a song already in the library) ──
  function plan() {
    const up = [...state.local].filter((id) => !inCloud.has(id));
    const down = state.songs.filter((s) => !state.local.has(s.id));
    return { up, down };
  }

  async function syncAll() {
    if (state.syncing || !state.signedIn) return;
    const { up, down } = plan();
    const total = up.length + down.length;
    if (!total) { notice(`Everything is in sync with ${state.band.name}`, 'ok'); return; }
    state.syncing = true;
    const btn = $('cloud-sync');
    btn.disabled = true;
    let n = 0, failed = 0;
    const step = () => { btn.querySelector('span').textContent = `Syncing ${Math.min(++n, total)}/${total}…`; };
    try {
      for (const id of up) {
        step();
        const l = lookup?.(id);
        if (!l) continue;
        try { await runJob('/api/cloud/publish', { videoId: id, model: l.model }, () => {}, 700); } catch (e) { failed++; if (e.message !== 'Cancelled') notice(`${l.item.title}: ${e.message}`); }
      }
      for (const s of down) {
        step();
        if (state.local.has(s.id)) continue;
        try { await runJob('/api/cloud/download', { videoId: s.id }, () => {}, 500); } catch (e) { failed++; notice(`${s.title || s.id}: ${e.message}`); }
      }
    } finally {
      state.syncing = false;
      btn.disabled = false;
      await onDownloaded?.();
      notice(failed ? `Synced with ${failed} problem${failed > 1 ? 's' : ''}` : `Synced with ${state.band.name}`, failed ? 'error' : 'ok');
    }
  }
  $('cloud-sync').addEventListener('click', syncAll);

  // ── sign-in ──
  function openLogin() {
    $('cloud-err').textContent = '';
    $('cloud-link').value = '';
    $('cloud-signed').hidden = !state.signedIn;
    $('cloud-form').hidden = state.signedIn;
    $('cloud-who').textContent = `${state.band.name} (${state.host})`;
    $('cloud-dialog').showModal();
  }
  $('cloud-btn').addEventListener('click', openLogin);
  $('cloud-close').addEventListener('click', () => $('cloud-dialog').close());
  $('cloud-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const err = $('cloud-err');
    err.textContent = 'Checking…';
    try {
      await post('/api/cloud/login', { link: $('cloud-link').value });
      $('cloud-link').value = '';
      $('cloud-dialog').close();
      await refresh();
    } catch (x) { err.textContent = x.message; }
  });
  $('cloud-out').addEventListener('click', async () => {
    await post('/api/cloud/logout');
    $('cloud-dialog').close();
    await refresh();
  });

  return { refresh, publish, badge, mark, setLocal, get signedIn() { return state.signedIn; } };
}
