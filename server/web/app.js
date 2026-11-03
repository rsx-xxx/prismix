// Band cloud player: browse the committed library, stream with seeking, mix stems, download.
import {
  ApiError, audible, bundleUrl, drifted, fileUrl, filterSongs, fmtBytes, fmtTime, joinUrl, loadIndex, loadManifest,
  neighbour, parseInvite, sortSongs, tracksOf,
} from './lib.js';

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};

const PLAY = '▶';
const PAUSE = '⏸';
const state = {
  songs: [], shown: [], song: null, manifest: null, tracks: null,
  stemsMode: false, muted: new Set(), solo: new Set(), players: [], dragging: false, seq: 0,
};

// ── views ──
function show(view) {
  $('login').hidden = view !== 'login';
  $('library').hidden = view !== 'library';
}

function needLogin(message) {
  state.songs = [];
  stop();
  closeSheet();
  $('mini').hidden = true;
  $('login-msg').textContent = message || 'This device is not signed in, or the invite was revoked. Paste the invite link you were sent.';
  show('login');
}

$('login-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const token = parseInvite($('invite').value);
  if (!token) { $('login-err').textContent = 'That does not look like an invite link.'; return; }
  location.href = joinUrl(token);
});

// ── library ──
async function refresh() {
  $('status').textContent = 'Loading…';
  try {
    state.songs = sortSongs(await loadIndex());
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return needLogin();
    if (e instanceof ApiError && e.status === 429) return needLogin('Too many attempts. Try again in a few minutes.');
    $('status').textContent = navigator.onLine ? 'Could not load the song list.' : 'You are offline.';
    show('library');
    return;
  }
  show('library');
  render();
}

function render() {
  state.shown = filterSongs(state.songs, $('search').value);
  $('status').textContent = state.songs.length ? (state.shown.length ? '' : 'No song matches.') : 'No songs published yet.';
  $('songs').replaceChildren(...state.shown.map((s) => {
    const open = el('button', { type: 'button', className: 'row-main' },
      el('img', { alt: '', loading: 'lazy', src: fileUrl(s.id, 'cover.jpg') }),
      el('span', { className: 'row-text' },
        el('b', { textContent: s.title || s.id }),
        el('small', { className: 'muted', textContent: `${s.artist || ''} · ${fmtTime(s.duration)}` })));
    open.firstChild.addEventListener('error', (ev) => { ev.target.style.visibility = 'hidden'; }, { once: true });
    open.addEventListener('click', () => play(s));
    const dl = el('a', { className: 'icon', href: fileUrl(s.id, 'mix.m4a'), download: `${[s.artist, s.title].filter(Boolean).join(' - ') || s.id}.m4a`, ariaLabel: `Download ${s.title}`, textContent: '⬇' });
    return el('li', { className: state.song?.id === s.id ? 'row playing' : 'row' }, open, dl);
  }));
}

$('search').addEventListener('input', render);
$('refresh').addEventListener('click', refresh);

// ── transport: one audio element for the mix, or one per stem kept in step ──
const master = () => state.players[0];

function build(urls) {
  stop();
  state.players = urls.map((url) => {
    const a = new Audio();
    a.preload = 'auto';
    a.src = url;
    return a;
  });
  const m = master();
  const on = (type, fn) => m.addEventListener(type, (e) => { if (state.players[0] === m) fn(e); });
  const followers = () => state.players.slice(1);
  on('timeupdate', () => { sync(); paint(); });
  on('durationchange', paint);
  on('play', () => { followers().forEach((a) => a.play().catch(() => {})); paint(); });
  on('pause', () => { followers().forEach((a) => a.pause()); paint(); });
  on('waiting', () => followers().forEach((a) => a.pause()));
  on('playing', () => { if (!m.paused) followers().forEach((a) => a.play().catch(() => {})); });
  on('seeked', sync);
  on('ended', () => step(1, true));
  on('error', () => {
    $('error').textContent = navigator.onLine ? 'This song cannot be played.' : 'You are offline.';
    // a 401 shows up only as a media error, so ask the API whether the credential is still good
    loadIndex().catch((e) => { if (e instanceof ApiError && e.status === 401) needLogin(); });
  });
  applyMute();
}

function sync() {
  const m = master();
  if (!m) return;
  for (const a of state.players.slice(1)) if (drifted(m.currentTime, a.currentTime)) a.currentTime = m.currentTime;
}

function stop() {
  for (const a of state.players) { a.pause(); a.removeAttribute('src'); a.load(); }
  state.players = [];
}

function applyMute() {
  if (!state.stemsMode) return;
  const on = audible(state.tracks.stems.map((s) => s.name), state.muted, state.solo);
  state.players.forEach((a, i) => { a.muted = !on[i]; });
}

function urlsFor(stemsMode) {
  const { mix, stems } = state.tracks;
  return stemsMode ? stems.map((s) => fileUrl(state.song.id, s.file.path, s.file.sha256)) : [fileUrl(state.song.id, mix.path, mix.sha256)];
}

async function play(song) {
  const seq = ++state.seq;
  state.song = song;
  state.manifest = null;
  state.stemsMode = false;
  state.muted.clear();
  state.solo.clear();
  $('error').textContent = '';
  openSheet(song);
  render();
  let manifest;
  try { manifest = await loadManifest(song.id); } catch (e) {
    if (seq !== state.seq) return;
    if (e instanceof ApiError && e.status === 401) return needLogin();
    $('error').textContent = 'Could not load this song.';
    return;
  }
  if (seq !== state.seq) return;
  const tracks = tracksOf(manifest);
  if (!tracks.mix) { $('error').textContent = 'This song has no audio.'; return; }
  state.manifest = manifest;
  state.tracks = tracks;
  build(urlsFor(false));
  paintSheet();
  try { await master().play(); } catch { /* the tap on a row counts as a gesture; if it is refused the play button is there */ }
  mediaSession();
}

function toggle() {
  const m = master();
  if (!m) { if (state.song) play(state.song); return; }
  if (m.paused) m.play().catch(() => {}); else m.pause();
}

function seekTo(t) {
  const m = master();
  if (!m) return;
  const d = m.duration || state.song?.duration || 0;
  const to = Math.min(Math.max(0, t), d || t);
  state.players.forEach((a) => { a.currentTime = to; });
  paint();
}

function step(delta, auto = false) {
  if (!state.song) return;
  const i = neighbour(state.shown.length ? state.shown : state.songs, state.song.id, delta);
  const list = state.shown.length ? state.shown : state.songs;
  if (i < 0 || (auto && list.length === 1)) return;
  play(list[i]);
}

function setStems(on) {
  const m = master();
  if (!m || !state.tracks) return;
  const at = m.currentTime;
  const wasPlaying = !m.paused;
  state.stemsMode = on;
  build(urlsFor(on));
  const go = () => {
    seekTo(at);
    if (wasPlaying) master().play().catch(() => {});
  };
  master().addEventListener('loadedmetadata', go, { once: true });
  paintSheet();
}

// ── sheet ──
function openSheet(song) {
  $('sheet').hidden = false;
  $('mini').hidden = true;
  $('title').textContent = song.title || song.id;
  $('artist').textContent = song.artist || '';
  const cover = $('cover');
  cover.style.visibility = 'visible';
  cover.src = fileUrl(song.id, 'cover.jpg');
  cover.onerror = () => { cover.style.visibility = 'hidden'; };
  $('mixer-box').hidden = true;
  $('downloads').replaceChildren();
  $('seek').value = 0;
  $('pos').textContent = '0:00';
  $('dur').textContent = fmtTime(song.duration);
}

function closeSheet() {
  $('sheet').hidden = true;
  if (state.song && state.players.length) {
    $('mini').hidden = false;
    $('mini-title').textContent = state.song.title || state.song.id;
    $('mini-artist').textContent = state.song.artist || '';
    $('mini-cover').src = fileUrl(state.song.id, 'cover.jpg');
  }
}

function paintSheet() {
  const { stems, mix } = state.tracks;
  $('mixer-box').hidden = stems.length < 2;
  $('stems-on').checked = state.stemsMode;
  const list = $('mixer');
  list.hidden = !state.stemsMode;
  list.replaceChildren(...stems.map((s) => {
    const mute = el('button', { type: 'button', className: 'chip', textContent: 'Mute' });
    const solo = el('button', { type: 'button', className: 'chip', textContent: 'Solo' });
    const chips = () => {
      mute.setAttribute('aria-pressed', String(state.muted.has(s.name)));
      solo.setAttribute('aria-pressed', String(state.solo.has(s.name)));
    };
    mute.addEventListener('click', () => { state.muted.has(s.name) ? state.muted.delete(s.name) : state.muted.add(s.name); chips(); applyMute(); });
    solo.addEventListener('click', () => { state.solo.has(s.name) ? state.solo.delete(s.name) : state.solo.add(s.name); chips(); applyMute(); });
    chips();
    return el('li', {}, el('span', { textContent: s.name }), mute, solo);
  }));
  const id = state.song.id;
  const name = [state.song.artist, state.song.title].filter(Boolean).join(' - ') || id;
  const link = (href, text, file) => el('li', {}, el('a', { href, download: file, textContent: text }));
  $('downloads').replaceChildren(
    link(fileUrl(id, mix.path), `Full mix (${fmtBytes(mix.bytes)})`, `${name}.m4a`),
    ...(stems.length > 1 ? [link(bundleUrl(id), `All stems and mix (${fmtBytes(state.song.bytes)}, zip)`, `${name}.zip`)] : []),
    ...stems.map((s) => link(fileUrl(id, s.file.path), `${s.name} (${fmtBytes(s.file.bytes)})`, `${name} - ${s.name}.m4a`)));
}

function paint() {
  const m = master();
  const playing = !!m && !m.paused;
  const d = (m && Number.isFinite(m.duration) && m.duration) || state.song?.duration || 0;
  const t = m ? m.currentTime : 0;
  for (const id of ['toggle', 'mini-toggle']) $(id).textContent = playing ? PAUSE : PLAY;
  $('pos').textContent = fmtTime(t);
  $('dur').textContent = fmtTime(d);
  if (!state.dragging) $('seek').value = d ? Math.round((t / d) * 1000) : 0;
  if ('mediaSession' in navigator && d && Number.isFinite(d)) {
    try { navigator.mediaSession.setPositionState({ duration: d, position: Math.min(t, d), playbackRate: m.playbackRate || 1 }); } catch { /* not supported */ }
    navigator.mediaSession.playbackState = playing ? 'playing' : 'paused';
  }
}

function mediaSession() {
  if (!('mediaSession' in navigator)) return;
  const s = state.song;
  const art = new URL(fileUrl(s.id, 'cover.jpg'), location.href).href;
  navigator.mediaSession.metadata = new MediaMetadata({ title: s.title || s.id, artist: s.artist || '', album: 'Band Cloud', artwork: [{ src: art, type: 'image/jpeg' }] });
  const set = (action, fn) => { try { navigator.mediaSession.setActionHandler(action, fn); } catch { /* unsupported action */ } };
  set('play', toggle);
  set('pause', toggle);
  set('previoustrack', () => step(-1));
  set('nexttrack', () => step(1));
  set('seekbackward', () => seekTo((master()?.currentTime || 0) - 10));
  set('seekforward', () => seekTo((master()?.currentTime || 0) + 10));
  set('seekto', (d) => seekTo(d.seekTime));
}

// ── Android shell (the app's WebView): saves through the member token, reports playback, handles back and lock-screen keys ──
const shell = window.Android;
if (shell?.cloudDownload) {
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[download]');
    if (!a) return;
    e.preventDefault();
    shell.cloudDownload(a.href, a.download);
  });
  let lastReport = 0;
  const report = () => {
    const s = state.song;
    const m = master();
    if (!s || !m || Date.now() - lastReport < 2000) return;
    lastReport = Date.now();
    const art = new URL(fileUrl(s.id, 'cover.jpg'), location.href).href;
    try { shell.media(s.title || s.id, s.artist || '', !m.paused, Math.round(m.currentTime * 1000), Math.round((m.duration || s.duration || 0) * 1000), m.playbackRate || 1, art); } catch { /* shell without media */ }
  };
  setInterval(report, 2000);
  window.__media = (op, arg) => {
    const m = master();
    if (op === 'play') { if (m?.paused) toggle(); } else if (op === 'pause') { if (m && !m.paused) toggle(); }
    else if (op === 'seek') seekTo(arg);
    else if (op === 'step') seekTo((m?.currentTime || 0) + arg);
    lastReport = 0;
  };
  window.__back = () => {
    if ($('sheet').hidden) return false;
    closeSheet();
    return true;
  };
}

$('toggle').addEventListener('click', toggle);
$('mini-toggle').addEventListener('click', toggle);
$('prev').addEventListener('click', () => step(-1));
$('next').addEventListener('click', () => step(1));
$('back10').addEventListener('click', () => seekTo((master()?.currentTime || 0) - 10));
$('fwd10').addEventListener('click', () => seekTo((master()?.currentTime || 0) + 10));
$('stems-on').addEventListener('change', (e) => setStems(e.target.checked));
$('sheet-close').addEventListener('click', closeSheet);
$('mini-open').addEventListener('click', () => { if (state.song) { $('sheet').hidden = false; $('mini').hidden = true; } });
const seek = $('seek');
seek.addEventListener('input', () => {
  state.dragging = true;
  const d = master()?.duration || state.song?.duration || 0;
  $('pos').textContent = fmtTime((seek.value / 1000) * d);
});
seek.addEventListener('change', () => {
  const d = master()?.duration || state.song?.duration || 0;
  state.dragging = false;
  seekTo((seek.value / 1000) * d);
});

// ── start ──
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
window.addEventListener('online', () => { if (!state.songs.length) refresh(); });
refresh();
