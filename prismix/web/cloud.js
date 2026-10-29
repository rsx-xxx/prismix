// The band cloud on the desktop: sign in with an invite link, publish processed songs, and download the band's songs into the library.
export function createCloud({ $, el, api, notice, fmt, onChange, onDownloaded }) {
  const state = { signedIn: false, host: '', band: { name: 'Band cloud', logo: '' }, songs: [], playing: null, local: new Set() };
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

  function render() {
    inCloud.clear();
    state.songs.forEach((s) => inCloud.add(s.id));
    const btn = $('cloud-btn');
    btn.replaceChildren(...(state.signedIn && state.band.logo ? [el('img', { className: 'band-logo', alt: '', src: logoUrl() })] : []),
      document.createTextNode(state.signedIn ? state.band.name : 'Band cloud sign-in'));
    $('cloud-title').textContent = state.band.name;
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
      return el('li', { className: 'cloud-row' },
        el('img', { className: 'cloud-cover', alt: '', loading: 'lazy', src: fileUrl(s.id, 'cover.jpg') }),
        el('div', { className: 'cloud-text' },
          el('b', { textContent: s.title || s.id }),
          el('span', { className: 'muted', textContent: `${s.artist || ''} · ${fmt(s.duration)} · ${(s.bytes / 1048576).toFixed(1)} MB` })),
        play, get);
    }));
    onChange?.();
  }

  // Which library songs the cloud has, and the cloud button each library card shows.
  function setLocal(ids) {
    const next = new Set(ids);
    const same = next.size === state.local.size && [...next].every((i) => state.local.has(i));
    state.local = next;
    if (!same) render();
  }
  function badge(item, open) {
    const has = state.signedIn && inCloud.has(item.id);
    const b = el('button', { className: `pub${has ? ' on' : ''}`, type: 'button', innerHTML: ICON_CLOUD(has ? '' : 'up', has) });
    b.title = b.ariaLabel = has ? `In ${state.band.name}` : state.signedIn ? `Upload to ${state.band.name}` : 'Sign in to a band cloud to upload';
    b.addEventListener('click', (e) => { e.stopPropagation(); if (!has) publish(item, open, b); });
    return b;
  }

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

  // ── publishing ──
  async function publish(item, model, button) {
    if (!state.signedIn) { openLogin(); return; }
    button.disabled = true;
    const stop = el('button', { className: 'pub stop', type: 'button', textContent: '✕', title: 'Cancel the upload', ariaLabel: 'Cancel the upload' });
    button.after(stop);
    try {
      const { job } = await post('/api/cloud/publish', { videoId: item.id, model });
      stop.addEventListener('click', (e) => { e.stopPropagation(); stop.disabled = true; post(`/api/cloud/publish/${job}/cancel`).catch(() => {}); });
      for (;;) {
        const j = await api(`/api/cloud/publish/${job}`);
        button.textContent = j.state === 'running' && j.total ? `${j.done}/${j.total}` : '…';
        if (j.state === 'done') break;
        if (j.state === 'error') { if (j.error !== 'Cancelled') notice(j.error); break; }
        await new Promise((r) => setTimeout(r, 700));
      }
      await refresh();
    } catch (e) { notice(e.message); } finally { stop.remove(); onChange?.(); }
  }

  // ── downloading ──
  async function download(s, button) {
    button.disabled = true;
    try {
      const { job } = await post('/api/cloud/download', { videoId: s.id });
      for (;;) {
        const j = await api(`/api/cloud/publish/${job}`);
        button.textContent = j.state === 'running' && j.total ? `${j.done}/${j.total}` : '…';
        if (j.state === 'done') break;
        if (j.state === 'error') throw new Error(j.error);
        await new Promise((r) => setTimeout(r, 500));
      }
      await onDownloaded?.();
    } catch (e) { notice(e.message); button.disabled = false; render(); }
  }

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

  return { refresh, publish, badge, setLocal, get signedIn() { return state.signedIn; } };
}
