import { StemPlayer } from './player.js';
import { createPlaylists } from './playlists.js';
import { createPlaylistView } from './playlist-view.js';
import { createVideo } from './video.js';
import { createScore } from './studio-score.js';
import { createLyrics } from './lyrics.js';
import { createTab } from './studio-tab.js';
import { createBlocks } from './blocks.js';
import { createSources } from './sources.js';
import { linkScroll, linkFollow } from './scroll.js';
import { createMarkers } from './markers.js';
import { createSplit } from './split.js';
import { initCheatsheet } from './cheatsheet.js';
import { getAccidentals, setAccidentals } from './theory.js';
import { createBand } from './band.js';
import { createNotes } from './notes.js';
import { createJoin } from './join.js';
import { createCloud } from './cloud.js';
import { createQueue } from './queue.js';
import { createMedia } from './media.js';
import { initLinks } from './links.js';

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const fmt = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
const thumb = (id) => `/media/${id}/thumb.jpg`;
const isUpload = (id) => /^u-[0-9a-f]{9}$/.test(id);
const ytThumb = (id) => (isUpload(id) ? thumb(id) : `https://i.ytimg.com/vi/${id}/mqdefault.jpg`);
/** Cover that falls back to the YouTube thumbnail when the saved file is missing. */
const cover = (img, id) => {
  img.addEventListener('error', () => { if (!isUpload(id) && !img.src.includes('ytimg')) img.src = ytThumb(id); }, { once: true });
  img.src = thumb(id);
  return img;
};
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

const ICON = {
  play: '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg>',
  pause: '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="14" height="14"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return res.json();
}

const state = {
  plist: null, models: [], defaultModel: 'htdemucs_ft', library: [], current: null, token: 0, streams: new Set(),
};
const STEM_ICONS = [[/vocal|voice|sing/, 'mic'], [/drum|perc/, 'drum'], [/bass/, 'wave'], [/guitar/, 'guitar'], [/piano|key|synth/, 'piano'], [/other|fx|rest/, 'spark']];
const stemIcon = (name) => STEM_ICONS.find(([re]) => re.test(name.toLowerCase()))?.[1] ?? 'note';
const player = new StemPlayer();
window.stemPlayer = player; // debug handle
const score = createScore({ player, api, notice: (m) => notice(m), onSetup: () => saveSetup() });
const tabView = createTab({ player, chordsIn: (j) => score.chordsIn(j) });
document.addEventListener('tab:seek', (e) => player.seek(e.detail));
const lyrics = createLyrics({ player, api, chords: () => score.timeline() });
const sources = createSources({
  api,
  onChange: (id) => { if (state.current?.id === id) { lyrics.open(id); score.retab(id); } },
  onTab: (id) => { if (state.current?.id === id) tabView.open(id); },
});
const blocks = createBlocks();
createSplit();
const markers = createMarkers({ player, api, bars: () => score.barChords() });
// The box beside the lyrics is either Chords or Tabs; one proxy follows whichever is showing.
const sidePane = () => (blocks.side() === 'tabs' && !$('tabview').hidden ? tabView.pane() : score.pane());
const right = {
  root: $('panes'), anchor: 0.4,
  box: () => sidePane().box(), active: () => sidePane().active(), hold: () => sidePane().hold(),
  following: () => sidePane().following(), track: () => sidePane().track(),
};
linkScroll(lyrics.pane(), right); // scrolling one box moves the other to the same moment
const followBoth = linkFollow(lyrics.pane(), right, player); // …and while playing, the lit line and the lit bar share one height
const video = createVideo({ player, notice: (m) => notice(m) });

const plPicker = createPlaylists({
  api, library: () => state.library, notice: (m) => notice(m),
  startProcess: (id) => startProcess(id, $('quality').value),
  saveAll: (name, items) => plview.save(name, items),
  importUrl: (url) => plview.importUrl(url),
  openTrack: (id) => { const it = state.library.find((i) => i.id === id); if (it) location.hash = `#/v/${id}/${it.models.find((m) => m !== 'mix') || it.models[0]}`; },
});

const plview = createPlaylistView({
  api, library: () => state.library, notice: (m) => notice(m), quality: () => $('quality').value,
  open: (id, plId) => openFrom(id, plId),
  cloudMark: (id) => cloud.mark(id),
});
function openFrom(id, plId) {
  const it = state.library.find((i) => i.id === id);
  state.plist = plId || null;
  if (!it) {
    const tag = document.querySelector(`#pp-list .pp-row[data-id="${id}"] .pl-tag`);
    if (tag) { tag.textContent = 'Starting…'; tag.className = 'pl-tag p'; }
    startProcess(id, $('quality').value, $('task').value, true).then(() => plview.refresh());
    return;
  }
  location.hash = `#/v/${id}/${it.models.includes(state.defaultModel) ? state.defaultModel : it.models[0]}`;
}

// ── notices ──
function notice(msg) {
  const n = $('notice');
  n.textContent = msg || '';
  n.hidden = !msg;
}

// ── omnibox ──
$('task').addEventListener('change', () => { $('quality').hidden = $('task').value === 'chords'; try { localStorage.setItem('stems:task', $('task').value); } catch { /* ignore */ } });
$('omni').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('q').value.trim();
  if (!text) return;
  notice('');
  location.hash = '#/';
  if (/^(https?:\/\/open\.spotify\.com\/(intl-[a-z-]+\/)?(album|playlist|artist)\/|spotify:(album|playlist|artist):)/i.test(text)) {
    $('results').hidden = true;
    hidePreview();
    plPicker.openUrl(text);
  } else if (/^(https?:\/\/|www\.|(m\.|music\.)?youtu|spotify:)/i.test(text) || /^[\w-]{11}$/.test(text)) {
    $('results').hidden = true;
    hidePreview();
    await startProcess(text, $('quality').value);
  } else {
    await runSearch(text);
  }
});

const SEARCH_PAGE = 10;
let searchRun = 0;

function resultRow(it, box) {
  const pic = el('button', { className: 'pic', type: 'button', title: 'Preview', ariaLabel: `Preview ${it.title}` },
    el('img', { src: ytThumb(it.id), alt: '', loading: 'lazy' }));
  const info = el('div', { className: 'info' }, el('div', { className: 't', textContent: it.title }),
    el('div', { className: 's', textContent: `${it.channel}${it.duration ? ' · ' + fmt(it.duration) : ''}` }));
  const go = el('button', { className: 'btn small go', type: 'button', textContent: inLibrary(it.id) ? 'Open' : 'Split' });
  const row = el('div', { className: 'result' }, pic, info, go);
  pic.addEventListener('click', () => showPreview(it, true));
  info.addEventListener('click', () => showPreview(it, true));
  go.addEventListener('click', () => { box.hidden = true; hidePreview(); startProcess(it.id, $('quality').value); });
  return row;
}

async function runSearch(q) {
  const box = $('results');
  const run = ++searchRun;
  hidePreview();
  box.hidden = false;
  box.onscroll = null;
  box.scrollTop = 0;
  box.replaceChildren(el('p', { className: 'muted', textContent: 'Searching…' }));
  const seen = new Set();
  let offset = 0, done = false, loading = false;
  const more = async (first) => {
    if (loading || done) return;
    loading = true;
    const tail = el('p', { className: 'muted', textContent: 'Loading more…' });
    if (!first) box.append(tail);
    try {
      const items = await api(`/api/search?q=${encodeURIComponent(q)}&offset=${offset}&limit=${SEARCH_PAGE}`);
      if (run !== searchRun) return;
      offset += SEARCH_PAGE;
      const fresh = items.filter((it) => !seen.has(it.id));
      fresh.forEach((it) => seen.add(it.id));
      if (items.length < SEARCH_PAGE || (!fresh.length && !first)) done = true;
      if (first && !fresh.length) { box.replaceChildren(el('p', { className: 'muted', textContent: 'Nothing found.' })); return; }
      tail.remove();
      if (first) box.replaceChildren();
      box.append(...fresh.map((it) => resultRow(it, box)));
      if (done) box.append(el('p', { className: 'muted', textContent: 'End of results.' }));
    } catch (err) {
      if (run !== searchRun) return;
      tail.remove();
      if (first) { box.hidden = true; notice(err.message); } else { done = true; notice(err.message); }
    } finally {
      loading = false;
    }
    if (run === searchRun && !done && box.scrollHeight <= box.clientHeight + 40) more(false);
  };
  box.onscroll = () => { if (box.scrollTop + box.clientHeight >= box.scrollHeight - 160) more(false); };
  await more(true);
}

// ── YouTube preview ──
const inLibrary = (id) => state.library.some((i) => i.id === id);

function ytId(text) {
  text = text.trim();
  if (/^[\w-]{11}$/.test(text)) return text;
  try {
    const u = new URL(/^[a-z]+:\/\//i.test(text) ? text : `https://${text}`);
    const host = u.hostname.replace(/^(www|m|music)\./, '');
    let id = null;
    if (host === 'youtu.be') id = u.pathname.slice(1).split('/')[0];
    else if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
      id = u.pathname === '/watch' ? u.searchParams.get('v') : (u.pathname.match(/^\/(?:shorts|embed|live|v)\/([^/?]+)/) || [])[1];
    }
    return id && /^[\w-]{11}$/.test(id) ? id : null;
  } catch { return null; }
}

function showPreview(it, autoplay) {
  typedPreview = false;
  $('pv-media').pause();
  $('pv-media').hidden = true;
  $('pv-frame').hidden = false;
  const frame = $('pv-frame');
  const src = `https://www.youtube-nocookie.com/embed/${it.id}?rel=0&modestbranding=1${autoplay ? '&autoplay=1' : ''}`;
  if (frame.dataset.id !== it.id || autoplay) { frame.src = src; frame.dataset.id = it.id; }
  $('pv-title').textContent = it.title || 'YouTube video';
  $('pv-sub').textContent = it.channel ? `${it.channel}${it.duration ? ' · ' + fmt(it.duration) : ''}` : '';
  const split = $('pv-split');
  split.textContent = inLibrary(it.id) ? 'Open in studio' : 'Split into stems';
  split.onclick = () => { hidePreview(); $('results').hidden = true; startProcess(it.id, $('quality').value); };
  $('preview').hidden = false;
}

function hidePreview() {
  const media = $('pv-media');
  media.pause();
  media.removeAttribute('src');
  media.removeAttribute('poster');
  media.hidden = true;
  $('pv-frame').hidden = false;
  const frame = $('pv-frame');
  frame.removeAttribute('src');
  delete frame.dataset.id;
  $('preview').hidden = true;
}

$('pv-close').addEventListener('click', hidePreview);
let previewTimer;
let typedPreview = false; // preview opened by typing a link (vs. clicking a search result)
$('q').addEventListener('input', () => {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    const id = ytId($('q').value);
    if (id) {
      if (location.hash !== '#/' && location.hash !== '') location.hash = '#/';
      $('results').hidden = true;
      const known = state.library.find((i) => i.id === id);
      showPreview({ id, title: known?.title, channel: known?.channel, duration: known?.duration }, false);
      typedPreview = true;
    } else if (typedPreview) {
      typedPreview = false;
      hidePreview();
    }
  }, 250);
});

// ── local files ──
function sendFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append('file', file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* not json */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error('Upload failed: lost connection to the server'));
    xhr.send(body);
  });
}

function showUploadPreview(it) {
  typedPreview = false;
  const frame = $('pv-frame');
  frame.removeAttribute('src');
  frame.hidden = true;
  const media = $('pv-media');
  media.poster = thumb(it.id);
  media.src = `/media/${it.id}/source${it.ext}`;
  media.hidden = false;
  $('pv-title').textContent = it.title;
  $('pv-sub').textContent = `${it.channel}${it.duration ? ' · ' + fmt(it.duration) : ''}`;
  const split = $('pv-split');
  split.textContent = 'Split into stems';
  split.onclick = () => { hidePreview(); startProcess(it.id, $('quality').value); };
  $('preview').hidden = false;
}

async function uploadFile(file) {
  if (!file) return;
  notice('');
  location.hash = '#/';
  $('results').hidden = true;
  hidePreview();
  const box = $('job');
  const msg = el('span', { textContent: 'Uploading' });
  const pct = el('span', { textContent: '0%' });
  const bar = el('i');
  box.replaceChildren(el('div', { className: 'body' },
    el('div', { className: 't', textContent: file.name }),
    el('div', { className: 'bar' }, bar), el('div', { className: 'row' }, msg, pct)));
  box.hidden = false;
  try {
    const it = await sendFile(file, (f) => {
      bar.style.width = `${Math.round(f * 100)}%`;
      pct.textContent = `${Math.round(f * 100)}%`;
      if (f >= 1) msg.textContent = 'Reading tags and finding a cover';
    });
    box.hidden = true;
    await loadLibrary();
    const known = state.library.find((i) => i.id === it.id);
    if (known?.models.length) { location.hash = `#/v/${it.id}/${known.models.find((m) => m !== 'mix') || known.models[0]}`; return; }
    showUploadPreview(it);
  } catch (err) {
    box.hidden = true;
    notice(err.message);
  }
}

$('up-btn').addEventListener('click', () => $('up-file').click());
$('up-file').addEventListener('change', (e) => { const f = e.target.files[0]; e.target.value = ''; uploadFile(f); });
let dragDepth = 0;
const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragenter', (e) => { if (hasFiles(e)) { dragDepth++; document.body.classList.add('dropping'); } });
window.addEventListener('dragleave', (e) => { if (hasFiles(e) && --dragDepth <= 0) { dragDepth = 0; document.body.classList.remove('dropping'); } });
window.addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
window.addEventListener('drop', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  dragDepth = 0;
  document.body.classList.remove('dropping');
  uploadFile(e.dataTransfer.files[0]);
});

async function startProcess(urlOrId, model, tasks = $('task').value, urgent = false) {
  try {
    const r = await api('/api/process', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: urlOrId, model, tasks, urgent }),
    });
    if (r.cached) { location.hash = `#/v/${r.videoId}/${r.model}`; return; }
    trackJob(r.job);
  } catch (err) {
    notice(err.message);
  }
}

// Progress lives in the queue dock; this only reacts to the end of a job. Each job has its own stream,
// so starting another song never drops the first one.
function trackJob(job) {
  const es = new EventSource(`/api/jobs/${job.id}/events`);
  state.streams.add(es);
  const end = () => { es.close(); state.streams.delete(es); };
  es.onmessage = async (ev) => {
    const j = JSON.parse(ev.data);
    if (j.stage === 'error') {
      end(); queue.refresh(); if (j.message !== 'Cancelled') notice(j.message || 'Processing failed');
    } else if (j.stage === 'done') {
      end(); queue.refresh();
      await loadLibrary();
      if (state.streams.size === 0) location.hash = `#/v/${j.videoId}/${j.model}`;
    }
  };
  es.onerror = () => { if (es.readyState === EventSource.CLOSED) { end(); queue.refresh(); } };
  queue.refresh();
}

// ── library ──
async function loadLibrary() {
  state.library = await api('/api/library');
  cloud.setLocal(state.library.map((i) => i.id));
  renderLibrary();
}

function renderLibrary() {
  const lib = $('library');
  $('empty').hidden = state.library.length > 0;
  $('lib-count').textContent = state.library.length ? `${state.library.length} track${state.library.length > 1 ? 's' : ''}` : '';
  lib.replaceChildren(...state.library.map((it) => {
    const open = it.models.includes(state.defaultModel) ? state.defaultModel : it.models[0];
    const card = el('div', { className: 'card', role: 'link', tabIndex: 0, title: it.title },
      cover(el('img', { className: 'cover', alt: '', loading: 'lazy' }), it.id),
      el('span', { className: 'dur', textContent: fmt(it.duration) }),
      el('div', { className: 'meta' },
        el('div', { className: 'title', textContent: it.title }),
        el('div', { className: 'sub', textContent: it.channel }),
        el('div', { className: 'badges' }, ...it.models.map((m) => el('span', { className: 'badge', textContent: modelLabel(m) })))));
    const del = el('button', { className: 'del', type: 'button', innerHTML: ICON.trash, title: 'Remove from library', ariaLabel: 'Remove from library' });
    del.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`Remove "${it.title}" and its cached stems?`)) return;
      try { await api(`/api/library/${it.id}`, { method: 'DELETE' }); await loadLibrary(); } catch (err) { notice(err.message); }
    });
    const add = el('button', { className: 'add', type: 'button', textContent: '+', title: 'Add to playlist', ariaLabel: 'Add to playlist' });
    add.addEventListener('click', async (e) => { e.stopPropagation(); await plview.load(); plview.pick(it); });
    const pub = cloud.badge(it, open);
    card.append(add, del, pub);
    dragSong(card, it.id);
    const go = () => { state.plist = null; location.hash = `#/v/${it.id}/${open}`; };
    card.addEventListener('click', go);
    card.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    return card;
  }));
}

// The whole card is the drag handle: the cover image must not start its own (picture-only) drag, and the ghost is the card itself.
function dragSong(card, id) {
  card.draggable = true;
  card.querySelectorAll('img').forEach((i) => { i.draggable = false; });
  card.addEventListener('dragstart', (e) => {
    e.dataTransfer.setData('application/x-prismix-song', id);
    e.dataTransfer.effectAllowed = 'copy';
    const r = card.getBoundingClientRect();
    const ghost = card.cloneNode(true);
    ghost.classList.add('drag-ghost');
    ghost.style.width = `${r.width}px`;
    document.body.append(ghost);
    e.dataTransfer.setDragImage(ghost, e.clientX - r.left, e.clientY - r.top);
    requestAnimationFrame(() => { ghost.remove(); card.classList.add('lifted'); document.body.classList.add('song-drag'); });
  });
  card.addEventListener('dragend', () => { card.classList.remove('lifted'); document.body.classList.remove('song-drag'); });
}

const modelLabel = (id) => state.models.find((m) => m.id === id)?.label || id;

// ── router ──
window.addEventListener('hashchange', route);

async function route() {
  const pm = location.hash.match(/^#\/p\/(\w+)$/);
  if (pm) { showHome(true); await plview.show(pm[1]); return; }
  plview.stop();
  $('home').classList.remove('inpl');
  const m = location.hash.match(/^#\/v\/([\w-]{11})\/(\w+)$/);
  if (!m) { showHome(); return; }
  await openStudio(m[1], m[2]);
}

function showHome(inPlaylist = false) {
  state.token++;
  video.hide(); // gives the stems back if the video's sound was on, so saved prefs are the real mix
  $('nt-toggle').hidden = true;
  if (state.current) { savePrefs(); player.dispose(); state.current = null; }
  score.close();
  tabView.close();
  lyrics.close();
  markers.close();
  video.hide();
  $('studio').hidden = true;
  $('dock').hidden = true;
  $('home').hidden = false;
  $('home').classList.toggle('inpl', inPlaylist);
  document.title = 'Stems — split, mute, transpose';
  loadLibrary().then(() => plview.load()).catch((e) => notice(e.message));
}

// ── studio ──
/** What is shared about how the song is played: audio key, chords-only shift, guitar tuning. */
const currentSetup = () => ({ semitones: player.semitones, chordKey: player.chordKey, tuning: score.tuning.get() });
let setupTimer;
/** Saves the setup on this computer and, for cloud songs, for everybody; waits for the sliders to settle. */
function saveSetup() {
  const c = state.current;
  if (!c) return;
  clearTimeout(setupTimer);
  setupTimer = setTimeout(() => {
    if (state.current !== c) return;
    const now = currentSetup();
    if (JSON.stringify(now) === JSON.stringify(c.setup)) return;
    c.setup = now;
    api(`/api/library/${c.id}/setup`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(now) }).catch(() => { /* not shared this time */ });
  }, 800);
}
const prefKey = (id) => `stems:${id}`;
function savePrefs() {
  const c = state.current;
  if (!c || !player.stems.length) return;
  const prefs = {
    tempo: player.tempo, semitones: player.semitones, cents: player.cents, formants: player.formants,
    mute: player.stems.filter((s) => s.muted).map((s) => s.name),
    solo: player.stems.filter((s) => s.solo).map((s) => s.name),
    vol: Object.fromEntries(player.stems.map((s) => [s.name, s.volume])),
  };
  try { localStorage.setItem(prefKey(c.id), JSON.stringify(prefs)); } catch { /* storage full or disabled */ }
}
function loadPrefs(id) {
  try { return JSON.parse(localStorage.getItem(prefKey(id))) || {}; } catch { return {}; }
}

/** Uploaded files can be renamed right in the title; songs from YouTube/Spotify keep their names. */
function setTitle(id, title) {
  const h = $('th-title');
  h.textContent = title;
  h.contentEditable = isUpload(id) ? 'plaintext-only' : 'false';
  h.title = isUpload(id) ? 'Click to rename' : '';
  h.classList.toggle('renamable', isUpload(id));
  h.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); h.blur(); } else if (e.key === 'Escape') { h.textContent = title; h.blur(); } e.stopPropagation(); };
  h.onblur = async () => {
    const next = h.textContent.replace(/\s+/g, ' ').trim();
    if (!isUpload(id) || !next || next === title) { h.textContent = title; return; }
    try {
      const info = await api(`/api/library/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: next }) });
      title = info.title;
      h.textContent = title;
      document.title = `${title} — Stems`;
      const entry = state.library.find((i) => i.id === id);
      if (entry) entry.title = title;
      if (state.current?.id === id) state.current.meta.title = title;
    } catch (err) { h.textContent = title; notice(err.message); }
  };
}

async function openStudio(id, model) {
  const token = ++state.token;
  video.hide(); // gives the stems back if the video's sound was on, so saved prefs are the real mix
  if (state.current) { savePrefs(); player.dispose(); state.current = null; }
  score.close();
  tabView.close();
  lyrics.close();
  markers.close();
  video.hide();
  $('home').hidden = true;
  $('studio').hidden = false;
  $('dock').hidden = true;
  $('mixer').classList.add('busy');
  $('loading').hidden = false;
  $('loading-bar').style.width = '0%';
  $('loading-text').textContent = 'Loading stems…';

  let meta;
  try {
    meta = await api(`/api/library/${id}/${model}`);
  } catch (err) {
    if (token !== state.token) return;
    location.hash = '#/';
    notice(err.message);
    return;
  }
  if (token !== state.token) return;

  const entry = state.library.find((i) => i.id === id);
  const available = entry?.models || [model];
  cover($('th-cover'), id);
  setTitle(id, meta.title);
  $('th-sub').textContent = `${meta.channel || ''} · ${fmt(meta.duration)}${plNow() ? ` · ${plNow().name}` : ''}`;
  document.title = `${meta.title} — Stems`;
  renderModelSwitch(id, model, available);
  renderMixer(meta);

  try {
    await player.load(meta, `/media/${id}/${model}`, (f) => {
      if (token === state.token) $('loading-bar').style.width = `${Math.round(f * 100)}%`;
    });
  } catch (err) {
    if (token !== state.token) return;
    location.hash = '#/';
    notice(err.message);
    return;
  }
  if (token !== state.token) { player.dispose(); return; }
  state.current = { id, model, meta };
  $('nt-toggle').hidden = false;
  stickers.load();
  const setup = await api(`/api/library/${id}/setup`).catch(() => ({}));
  if (token !== state.token) { player.dispose(); return; }
  applyPrefs({ ...loadPrefs(id), ...(setup.semitones != null && { semitones: setup.semitones }) });
  player.setChordKey(setup.chordKey ?? 0);
  score.tuning.set(setup.tuning ?? 'std');
  state.current.setup = currentSetup();
  band.publish();
  $('loading').hidden = true;
  $('mixer').classList.remove('busy');
  $('dock').hidden = false;
  syncDock();
  syncPlaylistNav();
  syncMixer();
  syncFrame();
  if (isUpload(id)) video.hide(); else video.setTrack(id);
  score.syncSeed();
  score.open(id, model, meta, token, (t) => t === state.token);
  lyrics.open(id);
  sources.setTrack(id);
  tabView.open(id);
  markers.open(id);
}

api('/api/version').then((v) => {
  const a = $('ver');
  a.textContent = `v${v.version}`;
  a.href = v.url;
  a.title = `Release notes for Prismix ${v.version}`;
  a.hidden = false;
}).catch(() => { /* the version label is optional */ });

api('/api/update').then((u) => {
  if (!u.available) return;
  const a = $('upd');
  a.hidden = false;
  $('upd-none').hidden = true;
  $('settings-btn').dataset.update = '1';
  $('settings-btn').title = `Settings · update to v${u.latest} available`;
  a.textContent = `Update to v${u.latest}`;
  if (!u.installable) {
    a.href = u.download || u.url;
    a.title = u.download ? 'Download the new installer, then run it' : 'Open the release page';
    return;
  }
  // Packaged app: the server downloads the release by itself; the user only picks when to restart.
  a.removeAttribute('target');
  a.href = u.url;
  const label = { downloading: 'Downloading', installing: 'Installing', restarting: 'Restarting' };
  let busy = false;
  const fail = (why) => { busy = false; a.textContent = 'Update failed, open download'; a.href = u.download || u.url; a.target = '_blank'; a.title = why; };
  const show = (st) => {
    a.textContent = st.state === 'ready' ? `Restart to update to v${u.latest}` : st.state === 'downloading' && st.progress ? `Downloading ${Math.round(st.progress * 100)}%` : `${label[st.state] || `Update to v${u.latest}`}${label[st.state] ? '…' : ''}`;
    a.title = st.state === 'ready' ? 'The update is downloaded. Restart Prismix to finish installing it' : 'Download and install the update, then restart Prismix';
  };
  const poll = async () => {
    const st = await api('/api/update').catch(() => null);
    if (!st) { a.textContent = 'Restarting…'; return setTimeout(poll, 800); } // server gone: the app is swapping itself
    if (st.state === 'error') return fail(st.error);
    show(st);
    if (st.state === 'ready' && !busy) { setTimeout(poll, 10000); return; }
    setTimeout(poll, st.state === 'idle' ? 10000 : 600);
  };
  a.addEventListener('click', async (e) => {
    if (a.target === '_blank') return; // after a failure it is a plain download link
    e.preventDefault();
    if (busy) return;
    busy = true;
    const ready = a.textContent.startsWith('Restart');
    a.textContent = 'Starting…';
    const r = await fetch(ready ? '/api/update/restart' : '/api/update/install', { method: 'POST' });
    if (r.status === 409) { busy = false; a.textContent = 'Restart to update'; notice((await r.text().then((t) => { try { return JSON.parse(t).detail; } catch { return t; } }))); return; }
    if (!r.ok) return fail(await r.text());
    poll();
  });
  poll();
}).catch(() => { /* the update notice is optional */ });

function renderModelSwitch(id, current, available) {
  const box = $('th-models');
  box.replaceChildren(...state.models.filter((m) => (m.legacy ? m.id === current : available.includes(m.id) || m.id !== 'mix')).map((m) => {
    const have = available.includes(m.id);
    const b = el('button', { type: 'button', className: have ? '' : 'todo', textContent: m.label, title: m.hint });
    b.setAttribute('aria-pressed', String(m.id === current));
    b.addEventListener('click', async () => {
      if (m.id === current) return;
      if (have) { location.hash = `#/v/${id}/${m.id}`; return; }
      location.hash = '#/';
      await startProcess(id, m.id, 'all');
    });
    return b;
  }));
}

const PRESETS = [
  { label: 'All', mute: () => [] },
  { label: 'No vocals', mute: () => ['vocals'] },
  { label: 'Vocals only', mute: (names) => names.filter((n) => n !== 'vocals') },
  { label: 'No drums', mute: () => ['drums'] },
  { label: 'No bass', mute: () => ['bass'] },
  { label: 'Drums & bass', mute: (names) => names.filter((n) => !['drums', 'bass'].includes(n)) },
];

function renderMixer(meta) {
  const names = meta.stems.map((s) => s.name);
  // presets
  $('presets').replaceChildren(...PRESETS.filter((p) => p.label === 'All' || names.includes('vocals')).map((p) => {
    const b = el('button', { type: 'button', className: 'chip', textContent: p.label });
    b.dataset.preset = p.label;
    b.addEventListener('click', () => { player.clearSolo(); player.setMuteSet(p.mute(names)); });
    return b;
  }));
  // rows
  const ctl = $('ctl-col');
  const lanes = $('lane-col');
  ctl.querySelectorAll('.stem').forEach((n) => n.remove());
  lanes.querySelectorAll('.lane').forEach((n) => n.remove());
  meta.stems.forEach((s, i) => {
    const mute = el('button', { type: 'button', className: 'tog', textContent: 'M', title: 'Mute' });
    mute.dataset.kind = 'mute';
    const solo = el('button', { type: 'button', className: 'tog', textContent: 'S', title: 'Solo' });
    solo.dataset.kind = 'solo';
    const vol = el('input', { type: 'range', min: 0, max: 150, value: 100, ariaLabel: `${s.name} volume` });
    const row = el('div', { className: 'stem' },
      el('div', { className: 'name' }, s.name, i < 6 ? el('kbd', { textContent: String(i + 1) }) : ''),
      el('div', { className: 'btns' }, mute, solo), vol);
    row.dataset.stem = s.name;
    row.querySelector('.name').dataset.ico = stemIcon(s.name);
    mute.addEventListener('click', () => player.setMuted(s.name, !player._stem(s.name).muted));
    solo.addEventListener('click', () => player.setSolo(s.name, !player._stem(s.name).solo));
    vol.addEventListener('input', () => { setPct(vol); player.setVolume(s.name, vol.value / 100); });
    setPct(vol);
    ctl.append(row);

    const base = el('canvas', { className: 'base' });
    const lit = el('canvas', { className: 'lit' });
    const lane = el('div', { className: 'lane' }, base, lit);
    lane.dataset.stem = s.name;
    lanes.insertBefore(lane, $('shade'));
    requestAnimationFrame(() => { drawPeaks(base, s.peaks); drawPeaks(lit, s.peaks); });
  });
}

function setPct(range) {
  const pct = ((range.value - range.min) / (range.max - range.min)) * 100;
  range.style.setProperty('--pct', `${pct}%`);
}

function drawPeaks(canvas, peaks) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const g = canvas.getContext('2d');
  g.scale(dpr, dpr);
  g.fillStyle = getComputedStyle(canvas.parentElement).getPropertyValue('--c').trim() || '#8af';
  const bar = 3, gap = 1.5, n = Math.floor(w / (bar + gap));
  for (let i = 0; i < n; i++) {
    const p = peaks[Math.min(peaks.length - 1, Math.floor((i / n) * peaks.length))] / 255;
    const bh = Math.max(2, p * (h - 14));
    g.beginPath();
    g.roundRect(i * (bar + gap), (h - bh) / 2, bar, bh, 1.5);
    g.fill();
  }
}

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    const meta = state.current?.meta;
    if (!meta) return;
    document.querySelectorAll('.lane').forEach((lane, i) => {
      lane.querySelectorAll('canvas').forEach((c) => drawPeaks(c, meta.stems[i].peaks));
    });
  }, 150);
});

function applyPrefs(p) {
  if (p.tempo) player.setTempo(p.tempo);
  if (p.semitones != null) player.setSemitones(p.semitones);
  if (p.cents != null) player.setCents(p.cents);
  if (p.formants != null) player.setFormants(p.formants);
  for (const s of player.stems) {
    if (p.vol?.[s.name] != null) s.volume = p.vol[s.name];
    s.muted = !!p.mute?.includes(s.name);
    s.solo = !!p.solo?.includes(s.name);
  }
  player._applyGains();
  player.setMaster(Number($('master').value) / 100);
  $('master-out').textContent = `${$('master').value}%`;
  player.stems.forEach((s) => {
    const v = document.querySelector(`.stem[data-stem="${s.name}"] .vol`);
    if (v) { v.value = Math.round(s.volume * 100); setPct(v); }
  });
}

// ── reflect player state into the UI ──
function syncMixer() {
  const names = player.stems.map((s) => s.name);
  for (const s of player.stems) {
    const audible = player.isAudible(s);
    const row = document.querySelector(`.stem[data-stem="${s.name}"]`);
    const lane = document.querySelector(`.lane[data-stem="${s.name}"]`);
    row?.classList.toggle('silent', !audible);
    lane?.classList.toggle('silent', !audible);
    row?.querySelector('[data-kind=mute]').setAttribute('aria-pressed', String(s.muted));
    row?.querySelector('[data-kind=solo]').setAttribute('aria-pressed', String(s.solo));
  }
  const anySolo = player.stems.some((s) => s.solo);
  const muted = player.stems.filter((s) => s.muted).map((s) => s.name);
  document.querySelectorAll('#presets .chip').forEach((chip) => {
    const p = PRESETS.find((x) => x.label === chip.dataset.preset);
    const want = p.mute(names);
    const on = !anySolo && want.length === muted.length && want.every((n) => muted.includes(n));
    chip.setAttribute('aria-pressed', String(on));
  });
  savePrefs();
}
player.addEventListener('mix', syncMixer);

function syncDock() {
  $('play').innerHTML = player.playing ? ICON.pause : ICON.play;
  $('play').setAttribute('aria-label', player.playing ? 'Pause' : 'Play');
  $('t-total').textContent = fmt(player.duration);
  $('tempo').value = Math.round(player.tempo * 100);
  $('tempo-out').textContent = `${Math.round(player.tempo * 100)}%`;
  $('tempo-out').classList.toggle('changed', Math.round(player.tempo * 100) !== 100);
  $('tempo-reset').hidden = Math.round(player.tempo * 100) === 100;
  $('key').value = player.semitones;
  $('key-out').textContent = player.semitones > 0 ? `+${player.semitones}` : String(player.semitones);
  $('key-out').classList.toggle('changed', player.semitones !== 0);
  $('key-reset').hidden = player.semitones === 0;
  $('cents').value = player.cents;
  $('cents-out').textContent = `${player.cents > 0 ? '+' : ''}${player.cents} ct`;
  $('formants').checked = player.formants;
  const loop = player.loop;
  $('loop-clear').hidden = !loop;
  $('loop-a').classList.toggle('active', !!loop);
  $('loop-b').classList.toggle('active', !!loop);
  const lr = $('loop-region'), sl = $('seek-loop');
  lr.hidden = sl.hidden = !loop;
  if (loop) {
    const a = (loop.a / player.duration) * 100, w = ((loop.b - loop.a) / player.duration) * 100;
    lr.style.left = sl.style.left = `${a}%`;
    lr.style.width = sl.style.width = `${w}%`;
  }
}
['state', 'tune', 'loop'].forEach((t) => player.addEventListener(t, () => { syncDock(); if (t !== 'state') savePrefs(); }));
player.addEventListener('frame', syncFrame);
player.addEventListener('tune', () => { score.tuned(); saveSetup(); });

// "Follow playback": appears once the playing position has scrolled out of view in lyrics, chords or tabs, or off the page.
const followBtn = (() => {
  const btn = $('follow');
  const panes = () => [lyrics.pane(), sidePane()].filter((p) => p.active());
  let next = 0;
  const away = () => {
    const r = $('panes').getBoundingClientRect();
    return panes().some((p) => p.away()) || (!!panes().length && r.bottom < 80);
  };
  const go = () => {
    panes().forEach((p) => p.resume());
    const r = $('panes').getBoundingClientRect(), bottom = innerHeight - ($('dock').hidden ? 0 : $('dock').offsetHeight);
    if (r.bottom < 120 || r.top > bottom - 120) $('panes').scrollIntoView({ block: 'center', behavior: 'smooth' });
    btn.hidden = true;
  };
  btn.addEventListener('click', go);
  return {
    go,
    frame() {
      if (performance.now() < next) return;
      next = performance.now() + 250;
      btn.hidden = !(away() && !!state.current);
    },
  };
})();

function syncFrame() {
  if (!player.duration) return;
  const pos = player.position;
  const pct = `${(pos / player.duration) * 100}%`;
  $('lane-col').style.setProperty('--p', pct);
  $('seek').style.setProperty('--sp', pct);
  $('seek').setAttribute('aria-valuenow', String(Math.floor(pos)));
  $('seek').setAttribute('aria-valuemax', String(Math.floor(player.duration)));
  $('t-now').textContent = fmt(pos);
  const heard = Math.max(0, pos - player.outLatency * player.tempo); // what is audible now, not what was just handed to the speakers
  score.frame(heard);
  lyrics.frame(heard);
  tabView.frame(heard);
  markers.frame(heard);
  followBoth(heard);
  followBtn.frame();
}

// ── dock wiring ──
$('play').addEventListener('click', () => player.toggle());
$('rew').addEventListener('click', () => player.seek(player.position - 5));
$('fwd').addEventListener('click', () => player.seek(player.position + 5));
$('tempo').addEventListener('input', (e) => player.setTempo(e.target.value / 100));
$('tempo-down').addEventListener('click', () => player.setTempo(Math.round(player.tempo * 20 - 1) / 20));
$('tempo-up').addEventListener('click', () => player.setTempo(Math.round(player.tempo * 20 + 1) / 20));
$('key').addEventListener('input', (e) => player.setSemitones(Number(e.target.value)));
$('key-down').addEventListener('click', () => player.setSemitones(player.semitones - 1));
$('key-up').addEventListener('click', () => player.setSemitones(player.semitones + 1));
$('cents').addEventListener('input', (e) => player.setCents(Number(e.target.value)));
$('formants').addEventListener('change', (e) => player.setFormants(e.target.checked));
$('master').addEventListener('input', (e) => { player.setMaster(e.target.value / 100); $('master-out').textContent = `${e.target.value}%`; band.publish(); });
$('tempo-reset').addEventListener('click', () => player.setTempo(1));
$('key-reset').addEventListener('click', () => player.setSemitones(0));
$('tempo-out').addEventListener('dblclick', () => player.setTempo(1));
$('key-out').addEventListener('dblclick', () => player.setSemitones(0));
$('reset-tune').addEventListener('click', () => { player.setTempo(1); player.setSemitones(0); player.setCents(0); });
$('back').addEventListener('click', () => { location.hash = state.plist ? `#/p/${state.plist}` : '#/'; });

// ── playlist navigation inside the studio ──
function plNow() { return state.plist ? plview.find(state.plist) : null; }
function plStep(d) {
  const p = plNow(), c = state.current;
  if (!p || !c) return;
  const i = p.items.findIndex((x) => x.id === c.id);
  const next = p.items[i + d];
  if (next) openFrom(next.id, p.id);
}
function syncPlaylistNav() {
  const p = plNow(), c = state.current;
  const i = p && c ? p.items.findIndex((x) => x.id === c.id) : -1;
  $('pl-prev').hidden = $('pl-next').hidden = i < 0;
  $('pl-prev').disabled = i <= 0;
  $('pl-next').disabled = i < 0 || i >= p.items.length - 1;
}
$('pl-prev').addEventListener('click', () => plStep(-1));
$('pl-next').addEventListener('click', () => plStep(1));

let loopA = null;
$('loop-a').addEventListener('click', () => { loopA = player.position; if (player.loop) player.setLoop(loopA, player.loop.b > loopA ? player.loop.b : null); });
$('loop-b').addEventListener('click', () => {
  const b = player.position;
  player.setLoop(loopA ?? Math.max(0, b - 8), b);
});
$('loop-clear').addEventListener('click', () => { loopA = null; player.setLoop(null, null); });

// seek bar
function pointerSeek(el_, e) {
  const r = el_.getBoundingClientRect();
  player.seek(clamp((e.clientX - r.left) / r.width, 0, 1) * player.duration);
}
const seek = $('seek');
seek.addEventListener('pointerdown', (e) => {
  seek.setPointerCapture(e.pointerId);
  pointerSeek(seek, e);
  const move = (ev) => pointerSeek(seek, ev);
  seek.addEventListener('pointermove', move);
  seek.addEventListener('pointerup', () => seek.removeEventListener('pointermove', move), { once: true });
});
seek.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowLeft') { player.seek(player.position - 5); e.preventDefault(); e.stopPropagation(); }
  if (e.key === 'ArrowRight') { player.seek(player.position + 5); e.preventDefault(); e.stopPropagation(); }
});

// waveform: click seeks, shift-drag selects a loop
const laneCol = $('lane-col');
laneCol.addEventListener('pointerdown', (e) => {
  if (!player.duration) return;
  const r = laneCol.getBoundingClientRect();
  const at = (ev) => clamp((ev.clientX - r.left) / r.width, 0, 1) * player.duration;
  laneCol.setPointerCapture(e.pointerId);
  if (!e.shiftKey) {
    player.seek(at(e));
    const move = (ev) => player.seek(at(ev));
    laneCol.addEventListener('pointermove', move);
    laneCol.addEventListener('pointerup', () => laneCol.removeEventListener('pointermove', move), { once: true });
    return;
  }
  const start = at(e);
  const move = (ev) => {
    const cur = at(ev);
    player.setLoop(Math.min(start, cur), Math.max(start, cur));
  };
  laneCol.addEventListener('pointermove', move);
  laneCol.addEventListener('pointerup', () => {
    laneCol.removeEventListener('pointermove', move);
    if (player.loop) loopA = player.loop.a;
  }, { once: true });
});

// ── export ──
const FORMATS = [
  { id: 'mp3', label: 'MP3', hint: 'Plays everywhere. High-quality VBR (~245 kbps).' },
  { id: 'm4a', label: 'M4A', hint: 'AAC 256 kbps. Best for Apple devices.' },
  { id: 'flac', label: 'FLAC', hint: 'Lossless, about half the size of WAV.' },
  { id: 'wav', label: 'WAV', hint: 'Lossless 24-bit. Biggest files, for editing in a DAW.' },
  { id: 'ogg', label: 'OGG', hint: 'Vorbis, good quality at small size.' },
  { id: 'opus', label: 'Opus', hint: '160 kbps; the most efficient lossy codec.' },
];
const exportState = { format: localStorage.getItem('stems:format') || 'mp3', picked: new Set(), source: 'mix' };
const ORIGINAL = {
  audio: [
    { id: 'original', label: 'As downloaded', hint: 'The audio stream exactly as YouTube serves it (usually Opus or AAC). No quality loss.' },
    ...FORMATS,
  ],
  video: [
    { id: 'mp4', label: 'MP4', hint: 'H.264 + AAC where available. Plays everywhere.' },
    { id: 'webm', label: 'WebM', hint: 'VP9 or AV1 + Opus. Often the sharpest picture.' },
    { id: 'mkv', label: 'MKV', hint: 'Takes whatever is best, with no conversion.' },
  ],
};
const HEIGHTS = [['', 'Best available'], ['1080', '1080p'], ['720', '720p'], ['480', '480p'], ['360', '360p']];
const orig = { kind: 'audio', audio: 'original', video: 'mp4', height: '' };

const pitchLabel = () => {
  const p = player.pitch;
  return `${p > 0 ? '+' : ''}${Number.isInteger(p) ? p : p.toFixed(2)} st`;
};

function openExport() {
  if (!state.current) return;
  const names = player.stems.map((s) => s.name);
  const heard = player.stems.filter((s) => player.isAudible(s)).map((s) => s.name);
  exportState.picked = new Set(heard.length ? heard : names);
  const changes = [];
  if (Math.round(player.tempo * 100) !== 100) changes.push(`tempo ${Math.round(player.tempo * 100)}%`);
  if (player.pitch !== 0) changes.push(`key ${pitchLabel()}`);
  $('ex-summary').textContent = changes.length
    ? `Rendered with ${changes.join(' and ')}, same as you hear it.`
    : 'Original tempo and key. Change them in the player first to download a modified version.';
  $('ex-heard').textContent = heard.length === names.length ? 'All stems' : heard.length ? heard.join(' + ') : 'Nothing is audible';
  document.querySelector('input[name=scope][value=heard]').checked = true;
  $('ex-stems').replaceChildren(...names.map((n) => {
    const b = el('button', { type: 'button', className: 'chip', textContent: n });
    b.setAttribute('aria-pressed', String(exportState.picked.has(n)));
    b.addEventListener('click', () => {
      exportState.picked.has(n) ? exportState.picked.delete(n) : exportState.picked.add(n);
      b.setAttribute('aria-pressed', String(exportState.picked.has(n)));
      syncExport();
    });
    return b;
  }));
  renderFormats();
  setSource(exportState.source);
  $('ex-error').hidden = true;
  syncExport();
  $('export').showModal();
}

function setSource(src) {
  exportState.source = src;
  document.querySelectorAll('#ex-source button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.src === src)));
  $('ex-mix').hidden = src !== 'mix';
  $('ex-orig').hidden = src !== 'orig';
  if (src === 'orig') renderOriginal();
  syncExport();
}
document.querySelectorAll('#ex-source button').forEach((b) => b.addEventListener('click', () => setSource(b.dataset.src)));

function renderOriginal() {
  const upload = isUpload(state.current.id);
  const seg = (box, items, current, pick) => box.replaceChildren(...items.map((f) => {
    const b = el('button', { type: 'button', textContent: f.label, title: f.hint || '' });
    b.setAttribute('aria-pressed', String(f.id === current));
    b.addEventListener('click', () => { pick(f.id); renderOriginal(); });
    return b;
  }));
  seg($('eo-kind'), [{ id: 'audio', label: 'Audio' }, { id: 'video', label: 'Video' }], orig.kind, (k) => { orig.kind = k; });
  const list = orig.kind === 'video' && upload
    ? [{ id: 'original', label: 'Original file', hint: 'The file you added, untouched.' }]
    : ORIGINAL[orig.kind];
  const current = orig.kind === 'video' && upload ? 'original' : orig[orig.kind];
  seg($('eo-formats'), list, current, (f) => { orig[orig.kind] = f; });
  $('eo-hint').textContent = list.find((f) => f.id === current)?.hint || '';
  const quality = orig.kind === 'video' && !upload;
  $('eo-height-row').hidden = !quality;
  $('eo-height').replaceChildren(...HEIGHTS.map(([v, t]) => el('option', { value: v, textContent: t, selected: v === orig.height })));
}
$('eo-height').addEventListener('change', (e) => { orig.height = e.target.value; });

function renderFormats() {
  $('ex-formats').replaceChildren(...FORMATS.map((f) => {
    const b = el('button', { type: 'button', textContent: f.label });
    b.setAttribute('aria-pressed', String(f.id === exportState.format));
    b.addEventListener('click', () => { exportState.format = f.id; renderFormats(); });
    return b;
  }));
  $('ex-fmt-hint').textContent = FORMATS.find((f) => f.id === exportState.format).hint;
}

function exportSelection() {
  const scope = document.querySelector('input[name=scope]:checked').value;
  const all = player.stems.map((s) => s.name);
  if (scope === 'all') return all;
  if (scope === 'pick') return all.filter((n) => exportState.picked.has(n));
  return player.stems.filter((s) => player.isAudible(s)).map((s) => s.name);
}

function syncExport() {
  if (exportState.source === 'orig') { $('ex-go').disabled = false; return; }
  const scope = document.querySelector('input[name=scope]:checked').value;
  $('ex-stems').hidden = scope !== 'pick';
  const sel = exportSelection();
  $('ex-sep').disabled = sel.length < 2;
  if (sel.length < 2) $('ex-sep').checked = false;
  $('ex-go').disabled = sel.length === 0;
}
document.querySelectorAll('input[name=scope]').forEach((r) => r.addEventListener('change', syncExport));
$('export-btn').addEventListener('click', openExport);
$('ex-cancel').addEventListener('click', () => $('export').close());

$('export-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const c = state.current;
  if (c && exportState.source === 'orig') { await downloadOriginal(c); return; }
  const stems = exportSelection();
  if (!c || !stems.length) return;
  const scope = document.querySelector('input[name=scope]:checked').value;
  const go = $('ex-go');
  go.disabled = true;
  go.textContent = 'Rendering…';
  $('ex-error').hidden = true;
  try {
    const res = await fetch('/api/export', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        videoId: c.id, model: c.model, stems, separate: $('ex-sep').checked,
        tempo: player.tempo, semitones: player.pitch, format: exportState.format,
        // "as you hear it" keeps the mixer levels; the other scopes are untouched stems
        volumes: scope === 'heard' ? Object.fromEntries(player.stems.map((s) => [s.name, s.volume])) : {},
      }),
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    const name = decodeURIComponent((/filename\*=UTF-8''([^;]+)/.exec(res.headers.get('Content-Disposition') || '') || [])[1] || 'audio');
    const url = URL.createObjectURL(await res.blob());
    el('a', { href: url, download: name }).click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    try { localStorage.setItem('stems:format', exportState.format); } catch { /* ignore */ }
    $('export').close();
  } catch (err) {
    $('ex-error').textContent = err.message;
    $('ex-error').hidden = false;
  } finally {
    go.textContent = 'Download';
    syncExport();
  }
});

async function downloadOriginal(c) {
  const go = $('ex-go');
  const upload = isUpload(c.id);
  const format = orig.kind === 'video' && upload ? 'original' : orig[orig.kind];
  go.disabled = true;
  go.textContent = orig.kind === 'video' ? 'Fetching video…' : 'Converting…';
  $('ex-error').hidden = true;
  try {
    const res = await fetch('/api/original', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ videoId: c.id, kind: orig.kind, format, height: orig.kind === 'video' && orig.height ? Number(orig.height) : null }),
    });
    if (!res.ok) {
      let msg = res.statusText;
      try { msg = (await res.json()).detail || msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    const name = decodeURIComponent((/filename\*=UTF-8''([^;]+)/.exec(res.headers.get('Content-Disposition') || '') || [])[1] || 'original');
    const url = URL.createObjectURL(await res.blob());
    el('a', { href: url, download: name }).click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    $('export').close();
  } catch (err) {
    $('ex-error').textContent = err.message;
    $('ex-error').hidden = false;
  } finally {
    go.textContent = 'Download';
    syncExport();
  }
}

// ── keyboard ──
function dispatch(e) {
  if (!state.current) return false;
  const step = e.shiftKey ? 15 : 5;
  const digit = /^Digit([1-6])$/.exec(e.code);
  const handlers = {
    Space: () => player.toggle(),
    ArrowLeft: () => player.seek(player.position - step),
    ArrowRight: () => player.seek(player.position + step),
    BracketLeft: () => $('tempo-down').click(),
    BracketRight: () => $('tempo-up').click(),
    Comma: () => $('key-down').click(),
    Period: () => $('key-up').click(),
    KeyA: () => $('loop-a').click(),
    KeyB: () => $('loop-b').click(),
    KeyC: () => $('loop-clear').click(),
    Digit0: () => player.seek(0),
    KeyD: () => openExport(),
    KeyR: () => (e.shiftKey ? $('key-reset').click() : $('tempo-reset').click()),
    KeyH: () => blocks.toggle('chords'),
    KeyL: () => blocks.toggle('lyrics'),
    KeyF: () => followBtn.go(),
    KeyT: () => blocks.toggle('tabs'),
    KeyV: () => $('video-btn').click(),
    KeyN: () => plStep(1),
    KeyP: () => plStep(-1),
    KeyX: () => (player.stems.some((s) => s.solo) ? player.clearSolo() : player.unmuteAll()),
  };
  if (digit) {
    const s = player.stems[Number(digit[1]) - 1];
    if (s) e.shiftKey ? player.setSolo(s.name, !s.solo) : player.setMuted(s.name, !s.muted);
    e.preventDefault();
    return true;
  }
  const fn = handlers[e.code];
  if (!fn) return false;
  e.preventDefault();
  fn();
  return true;
}

const stickers = createNotes({
  id: () => state.current?.id || null,
  send: (m) => band.send(m),
  roots: () => [$('ly-body'), $('sc-body'), $('tabview')],
  button: $('nt-toggle'),
});
const band = createBand({ player, current: () => state.current, onNotes: (m) => stickers.onMessage(m), $ });
createJoin({ $ });
const cloud = createCloud({
  $, el, api, notice, fmt, onChange: () => { renderLibrary(); plview.rerender(); },
  lookup: (id) => { const item = state.library.find((i) => i.id === id); return item && { item, model: item.models.includes(state.defaultModel) ? state.defaultModel : item.models[0] }; },
  onDownloaded: async () => { await loadLibrary(); await cloud.refresh(); } });
cloud.refresh();
const queue = createQueue({ $, api });
// Media keys, Now Playing and the Windows media overlay drive the studio player.
const media = createMedia({
  play: () => player.play(),
  pause: () => player.pause(),
  seek: (s) => player.seek(s),
  step: (d) => player.seek(Math.max(0, player.position + d)),
});
let mediaOn = false;
function syncMedia() {
  const c = state.current;
  if (!c) { if (mediaOn) { media.clear(); mediaOn = false; } return; }
  mediaOn = true;
  media.update({ title: c.meta.title, artist: c.meta.channel, art: `/media/${c.id}/thumb.jpg`, playing: player.playing, pos: player.position, dur: c.meta.duration, rate: player.tempo });
}
['state', 'seek', 'tune'].forEach((t) => player.addEventListener(t, syncMedia));
setInterval(syncMedia, 1000);
initLinks();
const cheats = initCheatsheet($('cheats'), () => $('export').open || $('playlists').open);

window.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = e.target.tagName;
  if (tag === 'INPUT' && e.target.type === 'text') return;
  if (tag === 'SELECT' || tag === 'TEXTAREA') return;
  if ($('export').open) return;
  if (e.key === '?') { cheats.toggle(); return; }
  dispatch(e);
});

// buttons fire their click on Space keyup; swallow it so Space only ever toggles playback
window.addEventListener('keyup', (e) => {
  if (e.code !== 'Space' || e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if ((t.tagName === 'INPUT' && t.type === 'text') || ['SELECT', 'TEXTAREA'].includes(t.tagName)) return;
  e.preventDefault();
});

// a panel hung under the button: no backdrop, closes on an outside click or Esc, the page behind stays usable
const settingsPanel = $('settings');
const closeSettings = () => { settingsPanel.hidden = true; };
$('settings-btn').addEventListener('click', () => {
  if (!settingsPanel.hidden) return closeSettings();
  const r = $('settings-btn').getBoundingClientRect(), box = settingsPanel.style;
  box.top = `${Math.round(r.bottom + 8)}px`;
  box.right = `${Math.max(8, Math.round(innerWidth - r.right))}px`;
  settingsPanel.hidden = false;
});
document.addEventListener('pointerdown', (e) => { if (!settingsPanel.hidden && !e.target.closest('#settings, #settings-btn')) closeSettings(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !settingsPanel.hidden) closeSettings(); });
$('settings-close').addEventListener('click', closeSettings);
$('acc-sel').value = getAccidentals();
$('acc-sel').addEventListener('change', () => { setAccidentals($('acc-sel').value); window.dispatchEvent(new Event('accidentals')); });
$('help-btn').addEventListener('click', () => { closeSettings(); cheats.toggle(); });
window.addEventListener('beforeunload', savePrefs);

// ── boot ──
(async function init() {
  try {
    const m = await api('/api/models');
    state.models = m.models;
    try { $('task').value = localStorage.getItem('stems:task') || 'all'; } catch { /* ignore */ }
    $('quality').hidden = $('task').value === 'chords';
    state.defaultModel = m.default;
    $('quality').replaceChildren(...m.models.filter((x) => x.id !== 'mix' && !x.legacy).map((x) => el('option', { value: x.id, textContent: x.label, title: x.hint, selected: x.id === m.default })));
  } catch (err) {
    notice(err.message);
  }
  await loadLibrary().catch(() => {});
  route();
})();

// The more-options popover closes on a click outside it or on Escape.
const more = document.querySelector('details.more');
document.addEventListener('pointerdown', (e) => { if (more?.open && !more.contains(e.target)) more.open = false; });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && more?.open) { more.open = false; e.stopPropagation(); } }, true);

// Pane scrollbars appear only while the user scrolls by hand, not while playback scrolls the panes.
{
  const timers = new WeakMap();
  const show = (e) => {
    const box = e.target.closest?.('.ly-body, .chart, .tab-body, .fing-side');
    if (!box) return;
    box.classList.add('manual');
    clearTimeout(timers.get(box));
    timers.set(box, setTimeout(() => box.classList.remove('manual'), 900));
  };
  for (const ev of ['wheel', 'touchmove', 'pointerdown', 'keydown']) document.addEventListener(ev, show, { passive: true, capture: true });
}
