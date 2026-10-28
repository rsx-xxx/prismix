// Band Cloud on the desktop: sign in with an invite link, publish processed songs, and play the committed library.
export function createCloud({ $, el, api, notice, fmt }) {
  const state = { signedIn: false, host: '', songs: [], jobs: new Map(), playing: null };
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

  function render() {
    $('cloud-btn').textContent = state.signedIn ? 'Cloud' : 'Cloud sign-in';
    const sec = $('cloud-section');
    sec.hidden = !state.signedIn;
    if (!state.signedIn) { stop(); return; }
    $('cloud-host').textContent = state.host;
    $('cloud-count').textContent = state.songs.length ? `${state.songs.length} song${state.songs.length > 1 ? 's' : ''}` : '';
    $('cloud-empty').hidden = state.songs.length > 0;
    $('cloud-list').replaceChildren(...state.songs.map((s) => {
      const play = el('button', { className: 'btn small', type: 'button', textContent: state.playing === s.id ? 'Pause' : 'Play' });
      play.addEventListener('click', () => toggle(s));
      return el('li', { className: 'cloud-row' },
        el('img', { className: 'cloud-cover', alt: '', loading: 'lazy', src: fileUrl(s.id, 'cover.jpg') }),
        el('div', { className: 'cloud-text' },
          el('b', { textContent: s.title || s.id }),
          el('span', { className: 'muted', textContent: `${s.artist || ''} · ${fmt(s.duration)} · ${(s.bytes / 1048576).toFixed(1)} MB` })),
        play);
    }));
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
    const stop = el('button', { className: 'pub', type: 'button', textContent: '✕', title: 'Cancel the upload', ariaLabel: 'Cancel the upload' });
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
    } catch (e) { notice(e.message); } finally { stop.remove(); button.disabled = false; button.textContent = '↑'; }
  }

  // ── sign-in ──
  function openLogin() {
    $('cloud-err').textContent = '';
    $('cloud-link').value = '';
    $('cloud-signed').hidden = !state.signedIn;
    $('cloud-form').hidden = state.signedIn;
    $('cloud-who').textContent = state.host;
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

  return { refresh, publish, get signedIn() { return state.signedIn; } };
}
