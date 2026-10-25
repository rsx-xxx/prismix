// Pick a song from your YouTube playlists (Google sign-in) or from any public playlist URL.

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const SCOPE = 'https://www.googleapis.com/auth/youtube.readonly';
const TOKEN_KEY = 'stems:google-token';

let gisLoaded;
function loadGis() {
  return (gisLoaded ??= new Promise((ok, fail) => {
    const s = Object.assign(document.createElement('script'), {
      src: 'https://accounts.google.com/gsi/client', onload: ok,
      onerror: () => { gisLoaded = null; fail(new Error('Could not load Google sign-in. Check your connection.')); },
    });
    document.head.append(s);
  }));
}

// Starter picks for practising with stems: each is a YouTube search, so nothing here goes stale.
const STARTERS = [
  ['Guitar-driven classics', 'classic rock greatest hits official audio'],
  ['Acoustic singalongs', 'acoustic songs everyone knows official audio'],
  ['Bass-line favourites', 'songs with great bass lines official audio'],
  ['Piano ballads', 'best piano ballads official audio'],
  ['Русский рок', 'русский рок лучшие песни'],
];

export function createPlaylists({ api, library, startProcess, openTrack, notice }) {
  const dlg = $('playlists'), body = $('pl-body');
  let clientId = '';
  let token = null;
  try { const t = JSON.parse(sessionStorage.getItem(TOKEN_KEY)); if (t && t.exp > Date.now()) token = t; } catch { /* none */ }

  async function google(path, params = {}) {
    const url = new URL(`https://www.googleapis.com/youtube/v3/${path}`);
    Object.entries(params).forEach(([k, v]) => v !== undefined && url.searchParams.set(k, v));
    const r = await fetch(url, { headers: { Authorization: `Bearer ${token.value}` } });
    if (r.status === 401) { signOut(); throw new Error('Google session expired. Sign in again.'); }
    if (!r.ok) throw new Error(r.status === 403 ? 'YouTube API access is not enabled for this Google Cloud project.' : `YouTube API error ${r.status}`);
    return r.json();
  }

  async function signIn() {
    await loadGis();
    await new Promise((ok, fail) => {
      window.google.accounts.oauth2.initTokenClient({
        client_id: clientId, scope: SCOPE,
        callback: (res) => {
          if (res.error) { fail(new Error(res.error_description || 'Google sign-in was cancelled')); return; }
          token = { value: res.access_token, exp: Date.now() + (Number(res.expires_in) - 60) * 1000 };
          sessionStorage.setItem(TOKEN_KEY, JSON.stringify(token));
          ok();
        },
        error_callback: (e) => fail(new Error(e.type === 'popup_closed' ? 'Sign-in window was closed' : 'Google sign-in failed')),
      }).requestAccessToken();
    });
  }

  function signOut() {
    if (token) window.google?.accounts?.oauth2?.revoke?.(token.value);
    token = null;
    sessionStorage.removeItem(TOKEN_KEY);
    home();
  }

  const status = (text) => body.replaceChildren(el('p', { className: 'muted', textContent: text }));
  const fail = (err) => { body.replaceChildren(el('p', { className: 'notice', textContent: err.message }), el('button', { className: 'btn', type: 'button', textContent: 'Back', onclick: home })); };

  function row(item, thumb) {
    const have = library().some((i) => i.id === item.id);
    const r = el('button', { type: 'button', className: 'pl-row' },
      el('img', { src: thumb || `https://i.ytimg.com/vi/${item.id}/mqdefault.jpg`, alt: '', loading: 'lazy' }),
      el('span', { className: 'pl-meta' }, el('b', { textContent: item.title }), el('small', { className: 'muted', textContent: item.channel || '' })),
      have ? el('span', { className: 'pl-tag', textContent: 'In library' }) : '');
    r.addEventListener('click', () => { dlg.close(); have ? openTrack(item.id) : startProcess(item.id); });
    return r;
  }

  function showItems(title, items, more) {
    const list = el('div', { className: 'pl-list' }, ...items.map((i) => row(i)));
    const back = el('button', { className: 'btn small ghost', type: 'button', textContent: '← Playlists', onclick: home });
    const kids = [el('div', { className: 'pl-bar' }, back, el('b', { textContent: title })), list];
    if (more) {
      const b = el('button', { className: 'btn', type: 'button', textContent: 'Load more' });
      b.addEventListener('click', async () => {
        b.disabled = true;
        try { const next = await more(); next.items.forEach((i) => list.append(row(i))); next.more ? (b.disabled = false) : b.remove(); } catch (e) { notice(e.message); b.disabled = false; }
      });
      kids.push(b);
    }
    body.replaceChildren(...kids);
  }

  async function openPlaylist(id, title) {
    status('Loading…');
    let page;
    const load = async () => {
      const r = await google('playlistItems', { part: 'snippet,contentDetails', playlistId: id, maxResults: 50, pageToken: page });
      page = r.nextPageToken;
      const items = r.items
        .filter((x) => x.snippet.title !== 'Private video' && x.snippet.title !== 'Deleted video')
        .map((x) => ({ id: x.contentDetails.videoId, title: x.snippet.title, channel: (x.snippet.videoOwnerChannelTitle || '').replace(/ - Topic$/, '') }));
      return { items, more: !!page };
    };
    try {
      const first = await load();
      showItems(title, first.items, first.more ? load : null);
    } catch (e) { fail(e); }
  }

  async function mine(box) {
    box.replaceChildren(el('p', { className: 'muted', textContent: 'Loading your playlists…' }));
    try {
      const [ch, lists] = await Promise.all([google('channels', { part: 'contentDetails', mine: true }), loadAll()]);
      const liked = ch.items?.[0]?.contentDetails?.relatedPlaylists?.likes;
      if (liked) lists.unshift({ id: liked, title: 'Liked videos', count: null });
      box.replaceChildren(el('div', { className: 'pl-grid' }, ...lists.map((p) => {
        const b = el('button', { type: 'button', className: 'pl-card' },
          p.thumb ? el('img', { src: p.thumb, alt: '', loading: 'lazy' }) : el('div', { className: 'pl-ph' }),
          el('b', { textContent: p.title }), el('small', { className: 'muted', textContent: p.count == null ? '' : `${p.count} videos` }));
        b.addEventListener('click', () => openPlaylist(p.id, p.title));
        return b;
      })));
    } catch (e) { box.replaceChildren(el('p', { className: 'notice', textContent: e.message })); }
  }

  async function loadAll() {
    const out = [];
    let page;
    do {
      const r = await google('playlists', { part: 'snippet,contentDetails', mine: true, maxResults: 50, pageToken: page });
      r.items.forEach((p) => out.push({ id: p.id, title: p.snippet.title, count: p.contentDetails.itemCount, thumb: p.snippet.thumbnails?.medium?.url }));
      page = r.nextPageToken;
    } while (page && out.length < 200);
    return out;
  }

  async function bySearch(title, query) {
    status('Searching…');
    try {
      showItems(title, await api(`/api/search?q=${encodeURIComponent(query)}`), null);
    } catch (e) { fail(e); }
  }

  /** Mixes made from what is already in the library (top artists), then fixed starter picks. */
  function suggested() {
    const counts = new Map();
    for (const i of library()) {
      const a = (i.channel || '').replace(/ - Topic$/, '').trim();
      if (a && a !== 'Uploaded file') counts.set(a, (counts.get(a) || 0) + 1);
    }
    const mixes = [...counts].sort((p, q) => q[1] - p[1]).slice(0, 4)
      .map(([a]) => [`More like ${a}`, `${a} best songs official audio`]);
    const chip = ([title, query]) => el('button', { type: 'button', className: 'btn small', textContent: title, onclick: () => bySearch(title, query) });
    const kids = [];
    if (mixes.length) kids.push(el('p', { className: 'muted', textContent: 'From your library' }), el('div', { className: 'pl-chips' }, ...mixes.map(chip)));
    kids.push(el('p', { className: 'muted', textContent: 'Good to start with' }), el('div', { className: 'pl-chips' }, ...STARTERS.map(chip)));
    return el('div', { className: 'pl-suggest' }, ...kids);
  }

  async function byUrl(url) {
    status('Reading playlist…');
    try {
      const r = await api(`/api/playlist?url=${encodeURIComponent(url)}`);
      showItems(r.title, r.items, null);
    } catch (e) { fail(e); }
  }

  function home() {
    const form = el('form', { className: 'pl-url' });
    const input = el('input', { type: 'text', placeholder: 'Paste a playlist or mix link', 'aria-label': 'Playlist link', spellcheck: false });
    form.append(input, el('button', { className: 'btn primary', type: 'submit', textContent: 'Open' }));
    form.addEventListener('submit', (e) => { e.preventDefault(); if (input.value.trim()) byUrl(input.value.trim()); });
    const kids = [];
    if (clientId) {
      if (token) {
        const box = el('div');
        body.replaceChildren(
          el('div', { className: 'pl-bar' }, el('b', { textContent: 'Your YouTube' }), el('button', { className: 'btn small ghost', type: 'button', textContent: 'Sign out', onclick: signOut })),
          suggested(), box, el('p', { className: 'muted', textContent: 'Or open any public playlist or mix:' }), form);
        mine(box);
        return;
      }
      const go = el('button', { className: 'btn primary', type: 'button', textContent: 'Sign in with Google' });
      go.addEventListener('click', async () => { go.disabled = true; try { await signIn(); home(); } catch (e) { notice(e.message); go.disabled = false; } });
      kids.push(el('div', { className: 'pl-signin' }, el('p', { className: 'muted', textContent: 'Read-only access to your playlists, so you can pick songs from them. Nothing is stored on the server.' }), go));
    } else {
      kids.push(el('p', { className: 'muted', textContent: 'Paste any public playlist or mix link. To pick from your own playlists after signing in, set PRISMIX_GOOGLE_CLIENT_ID (see the README).' }));
    }
    body.replaceChildren(suggested(), ...kids, form);
  }

  $('pl-btn').addEventListener('click', async () => {
    dlg.showModal();
    if (!clientId) { try { clientId = (await api('/api/config')).googleClientId || ''; } catch { /* offline */ } }
    home();
  });
  $('pl-close').addEventListener('click', () => dlg.close());
}
