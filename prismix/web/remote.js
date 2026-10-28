// Band mode, phone side. Follows the host's player over a WebSocket (server clock as the shared
// timeline), shows lyrics (optionally with chords above the syllables they land on) or the bar chart,
// and can drive the host, hear it locally, or go solo.
import { chordName, chordPaint, keyName, useFlats, getAccidentals, setAccidentals, simplify } from './theory.js';
import { createMedia } from './media.js';
import { describe } from './band.js';
import { timeLine, splitAt } from './lyrics.js';
import { createSocket } from './socket.js';
import { createNotes } from './notes.js';

const $ = (id) => document.getElementById(id);
const LS = 'prismix.remote';
// The Android app answers https://offline.prismix itself, from songs kept on the phone: no host, solo only.
const CLOUD = location.hostname === 'cloud.prismix'; // the app's Band Cloud: same page, data from the cloud
const OFFLINE = location.hostname === 'offline.prismix' || CLOUD;
const store = (() => {
  try {
    const mine = JSON.parse(localStorage.getItem(LS)) || {};
    // each origin has its own localStorage: the app keeps one copy of the settings for both
    if (OFFLINE) return { ...mine, ...JSON.parse(window.Android?.stashed?.() || '{}'), pin: '' };
    return mine;
  } catch { return {}; }
})();
const save = () => {
  localStorage.setItem(LS, JSON.stringify(store));
  try { window.Android?.stash?.(JSON.stringify({ ...store, pin: undefined })); } catch { /* the copy is a convenience */ }
};
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  n.append(...kids);
  return n;
};
const droid = window.Android; // bridge from the native shell, absent in a plain browser

function floorIndex(arr, x) {
  let lo = 0, hi = arr.length - 1, r = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid] <= x) { r = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return r;
}

// The hub's PIN rides along on every same-origin request, including the player's stem fetches.
const rawFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  if (store.pin && typeof input === 'string' && input.startsWith('/')) {
    init = { ...init, headers: { ...init.headers, 'x-band-token': store.pin } };
  }
  return rawFetch(input, init);
};
const getJson = async (url) => {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json();
};

// ── state ──
const S = {
  offset: 0, // server clock minus local clock, ms
  rtt: null,
  host: null, // last state broadcast by the host
  want: null, // id/model of the song last requested from the host
  fail: null, // {id, model, text} when that song could not be loaded
  hostOnline: false,
  track: null, // {id, model, meta, structure, chords[], chordT[], barT[], lyrics}
  mode: OFFLINE ? 'solo' : store.mode || 'follow', // follow | listen | solo
  view: store.view || 'lyrics', // lyrics | chords
  withChords: store.chords ?? false,
  tabChords: store.tabChords ?? true, // chords above the tab staff
  part: store.part || null, // guitar | bass, which tab to show
  simple: store.simple ?? false, // simpler chord names, on this phone only
  locked: false,
  local: null, // StemPlayer, created on demand
  localTrack: null,
  lat: store.lat || 0, // ms
  solo: { tempo: 1, semitones: 0, chord: 0 },
};
const serverNow = () => Date.now() + S.offset;

// ── appearance ──
const BARS = { light: '#f6f3ec', dark: '#121110' };
function applyLook() {
  const root = document.documentElement;
  root.style.setProperty('--fs', String(store.fs || 1));
  if (store.theme === 'light' || store.theme === 'dark') root.dataset.theme = store.theme; else delete root.dataset.theme;
  const dark = store.theme === 'dark' || (store.theme !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.querySelector('meta[name=theme-color]').content = dark ? BARS.dark : BARS.light;
  droid?.setBars?.(dark ? BARS.dark : BARS.light, !dark);
  document.querySelectorAll('#theme-seg button').forEach((b) => b.setAttribute('aria-checked', String((store.theme || 'auto') === b.dataset.theme)));
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyLook);
applyLook();

let seekDrag = false, seekHold = 0; // see the seek bar handlers

// ── connection + clock ──
let samples = [];
let welcomed = false;
const net = createSocket({
  url: () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`,
  fatal: [4001, 4003],
  auto: false,
  onopen: () => {
    net.send({ t: 'hello', name: store.name, role: store.role, token: store.pin });
    samples = [];
    for (let i = 0; i < 8; i++) setTimeout(ping, i * 120);
  },
  onmessage: onMessage,
  onclose: (e, fatal) => {
    S.hostOnline = false;
    if (fatal) { setLink('bad', 'Disconnected'); showJoin(e.code === 4001 ? 'Wrong PIN.' : 'The host removed you.', e.code === 4001); return; }
    setLink('bad', 'Reconnecting…');
    if (!welcomed) $('boot').querySelector('p').textContent = 'Looking for the host…';
  },
});
const ping = () => net.send({ t: 'ping', c: performance.now() });
setInterval(ping, 4000);

function setLink(state, text) {
  $('dot').className = `dot ${state}`;
  $('dot').title = text;
  $('net').textContent = text;
}

function onMessage(m) {
  if (m.t === 'welcome') {
    welcomed = true;
    $('boot').hidden = true;
    $('join').hidden = true;
    $('app').hidden = false;
    S.locked = m.locked;
    S.id = m.id;
    S.speaker = m.speaker;
    paintSpeaker();
    $('locked').hidden = !m.locked;
    if (m.state?.t) setHost(m.state);
    chrome();
    if (!S.shown) { S.shown = true; render(); } // a reconnect keeps the page as it is: no rebuild, no scroll reset
  } else if (m.t === 'denied') {
    showJoin('This host needs a PIN.', true);
  } else if (m.t === 'pong') {
    const rtt = performance.now() - m.c;
    samples.push({ rtt, off: m.s + rtt / 2 - Date.now() }); // min-RTT sample has the least queueing error
    samples = samples.slice(-12);
    const best = samples.reduce((a, b) => (b.rtt < a.rtt ? b : a));
    S.offset = best.off;
    S.rtt = best.rtt;
    net.send({ t: 'rtt', v: best.rtt });
    setLink(S.hostOnline ? 'live' : 'bad', `${S.hostOnline ? 'Connected' : 'Host offline'} · ${Math.round(best.rtt)} ms`);
  } else if (m.t === 'state') {
    setHost(m);
  } else if (m.t === 'roster') {
    S.locked = m.locked;
    S.hostOnline = m.hostOnline;
    const wasMe = S.speaker != null && S.speaker === S.id;
    S.speaker = m.speaker;
    paintSpeaker();
    if (S.speaker === S.id && S.mode !== 'listen' && !OFFLINE) setMode('listen'); // the host picked this phone
    else if (wasMe && S.speaker !== S.id && S.mode === 'listen') setMode('follow'); // the host took the sound back
    $('locked').hidden = !m.locked;
    if (!m.hostOnline) { $('sub').textContent = 'Host offline'; setLink('bad', 'Host offline'); }
  } else if (m.t === 'changed') {
    const text = describe(m);
    if (text) { notice(text); lastChange = performance.now(); }
  } else if (m.t === 'notes' || m.t === 'noteerr') {
    stickers.onMessage(m);
  } else if (m.t === 'locked') {
    $('locked').hidden = false;
  } else if (m.t === 'kicked') {
    showJoin('The host removed you.');
  }
}

let lastChange = -1e4; // when someone else's change was last announced
function setHost(m) {
  const before = S.host ? hostPos() : null;
  const wasPlaying = !!S.host?.playing;
  const prev = S.host;
  S.host = m;
  if (prev && prev.id === m.id && performance.now() - lastChange > 2000) { // the computer changed a shared setting itself
    const tell = (op, v) => notice(describe({ who: 'Host', op, v }));
    if (prev.tempo !== m.tempo) tell('tempo', m.tempo);
    else if (prev.semitones !== m.semitones) tell('pitch', m.semitones);
    else if ((prev.chordKey ?? 0) !== (m.chordKey ?? 0)) tell('chordkey', m.chordKey ?? 0);
  }
  S.hostOnline = true;
  // Compare with the last song asked for, not the last state: a failed song must not be retried on every update.
  const key = m.id ? `${m.id}/${m.model}` : null;
  if (key && key !== S.want && S.mode !== 'solo') { S.want = key; loadTrack(m.id, m.model); }
  // The host refreshes its state every couple of seconds; only a seek or a play/pause is worth a hard re-sync.
  syncMedia();
  if (S.mode === 'listen') syncLocal(before == null || wasPlaying !== !!m.playing || Math.abs(hostPos() - before) > 0.4);
}

const send = (op, v) => net.send({ t: 'cmd', op, v });

// Stickers go through the band socket: a phone is read-only over HTTP.
const stickers = createNotes({ id: () => S.track?.id || null, send: (m) => net.send(m), roots: () => [$('stage')], button: $('nt-btn') });
if (OFFLINE) $('nt-btn').hidden = true;

// ── the shared timeline ──
const hostPos = () => {
  const h = S.host;
  if (!h) return 0;
  let p = h.pos;
  if (h.playing) {
    p += ((serverNow() - h.s) / 1000) * h.tempo;
    if (h.loop && p >= h.loop.b) p = h.loop.a + ((p - h.loop.a) % (h.loop.b - h.loop.a));
  }
  return clamp(p, 0, S.track?.meta.duration ?? p);
};
// Offset estimates and the host's periodic state refresh move the raw position by tens of milliseconds. Slew a
// local clock towards it instead of following it, so lines and bars do not flicker at their boundaries.
const clock = { p: 0, at: 0, key: '' };
function smoothPos() {
  const raw = hostPos(), now = performance.now(), h = S.host;
  const key = `${S.track?.id}|${h?.playing}|${h?.tempo}`;
  const dt = Math.min((now - clock.at) / 1000, 0.25);
  let p = clock.p + (h?.playing ? dt * h.tempo : 0);
  const err = raw - p;
  if (key !== clock.key || clock.at === 0 || Math.abs(err) > 0.35) p = raw; else p += err * Math.min(1, dt * 4);
  Object.assign(clock, { p, at: now, key });
  return p;
}
const soloActive = () => S.mode === 'solo' && S.local;
const pos = () => (soloActive() ? Math.max(0, S.local.position - S.local.outLatency * S.local.tempo) : smoothPos());
const playing = () => (soloActive() ? S.local.playing : !!S.host?.playing);
const tempo = () => (S.mode === 'solo' ? S.solo.tempo : S.host?.tempo ?? 1);
const chordKey = () => (S.mode === 'solo' ? S.solo.chord : S.host?.chordKey ?? 0);
const semis = () => (S.mode === 'solo' ? S.solo.semitones : S.host?.semitones ?? 0);

// ── track data ──
/**
 * The analyser can hand back the same chord twice in a row, a one-beat blip between two equal chords, or a
 * change that lands a hair before the bar line. Fold those so a chord is written once, where it really starts.
 */
function tidyChords(st, simple = false) {
  const same = (a, b) => a && b && a.r === b.r && a.q === b.q;
  let cs = st.chords.map((c) => ({ ...(simple ? simplify(c) : c) }));
  cs = cs.filter((c, i) => !(c.d <= 1 && same(cs[i - 1], cs[i + 1])));
  const out = [];
  for (const c of cs) {
    const p = out.at(-1);
    if (same(p, c) && c.b - (p.b + p.d) <= 0.75) p.d = c.b + c.d - p.b; else out.push(c);
  }
  for (const c of out) {
    const nb = st.bars.find((b) => b > c.b);
    if (nb != null && nb - c.b <= 0.5 && c.d - (nb - c.b) >= 0.5) { c.d -= nb - c.b; c.b = nb; }
  }
  return out;
}

function buildChords(t) {
  t.chords = tidyChords(t.structure, S.simple);
  t.chordT = t.chords.map((c) => t.beatSec(c.b));
}

let loadToken = 0;
async function loadTrack(id, model) {
  const mine = ++loadToken;
  S.track = null;
  S.fail = null;
  stickers.load();
  $('title').textContent = 'Loading…';
  $('sub').textContent = '';
  render();
  try {
    const [meta, structure] = await Promise.all([
      getJson(`/api/library/${id}/${model}`),
      getJson(`/media/${id}/structure.json`).catch(() => null),
    ]);
    if (mine !== loadToken) return;
    const t = { id, model, meta, structure, lyrics: null, pending: true, tabDone: !structure };
    // The host may have to recognise or align the words first, which takes minutes: show the song meanwhile.
    getJson(`/api/lyrics/${id}`).then((lyrics) => {
      if (mine !== loadToken) return;
      t.lyrics = lyrics;
      t.pending = false;
      render();
    }).catch(() => { t.pending = false; if (mine === loadToken) render(); });
    if (structure) {
      getJson(`/api/tabdata/${id}`).then((d) => {
        if (mine !== loadToken) return;
        t.tabDone = true;
        if (!d?.found) { chrome(); if (S.view === 'tab') render(); return; }
        t.tab = d;
        for (const [k, p] of Object.entries(d.tracks)) p.kind ||= k.replace(/\d+$/, '');
        if (!S.part || !d.tracks[S.part]) S.part = firstOf(d, roleKey()) || Object.keys(d.tracks)[0];
        chrome();
        if (S.view === 'tab') render();
      }).catch(() => { t.tabDone = true; });
      const b = structure.beats;
      t.beatSec = (x) => {
        if (x <= 0) return b[0] + x * (b[1] - b[0]);
        if (x >= b.length - 1) return b.at(-1) + (x - b.length + 1) * (b.at(-1) - b.at(-2));
        const i = Math.floor(x);
        return b[i] + (b[i + 1] - b[i]) * (x - i);
      };
      t.barT = structure.bars.map(t.beatSec);
      buildChords(t);
    }
    S.track = t;
    stickers.load();
    $('title').textContent = meta.title;
    $('sub').textContent = meta.channel || '';
    $('t-dur').textContent = fmt(meta.duration);
    if (S.pl && !S.pl.items.some((x) => x.id === id)) S.pl = null;
    syncPlNav();
    render();
    if (S.mode !== 'follow') ensureLocal().then(claimSpeaker, () => {});
    if (store.autosave && !OFFLINE && !off.have.has(id)) keep(id, model);
  } catch (err) {
    if (mine !== loadToken) return;
    const code = +err.message;
    S.fail = {
      id, model,
      text: code === 404 ? 'The host has no ready copy of this song. It may still be processing, or it was removed.'
        : code === 401 ? 'The host asked for a PIN. Change your name or part in the menu to enter it.'
        : 'Could not reach the host. Check that both devices are on the same Wi-Fi.',
    };
    $('title').textContent = 'Song unavailable';
    render();
  }
}

// ── rendering ──
const flats = () => S.track?.structure && useFlats(S.track.structure.key, semis() + chordKey());
const cname = (c) => chordName(c, semis() + chordKey(), flats());
const hueOf = (c) => chordPaint(c, semis() + chordKey()).hue;
function pill(c, i) {
  const e = el('span', { className: 'chord', textContent: cname(c) });
  e.style.setProperty('--h', hueOf(c));
  nodes.chords.push({ el: e, c, i });
  return e;
}
function relabel() {
  for (const r of nodes.chords || []) {
    r.el.textContent = cname(r.c);
    r.el.style.setProperty('--h', hueOf(r.c));
  }
  fit();
}

// A chord wider than its syllable would push the rest of the word away. Let it hang over the next syllable
// when that one has no chord of its own, so words stay whole.
function fit() {
  if (S.view !== 'lyrics' || !nodes.lines) return;
  const sys = nodes.lines.map((n) => n.syl).filter((l) => l.length);
  for (const line of sys) for (const sy of line) { sy.el.style.marginRight = ''; }
  const plan = [];
  for (const line of sys) {
    line.forEach((sy, k) => {
      const chs = sy.el.firstElementChild;
      const nx = line[k + 1];
      if (!chs?.classList.contains('chs') || !nx) return;
      const tx = sy.el.querySelector('.tx');
      const over = chs.offsetWidth - tx.offsetWidth;
      if (over <= 0) return;
      if (nx.el.firstElementChild?.classList.contains('chs')) {
        return;
      }
      plan.push([sy.el, -Math.min(over, nx.el.querySelector('.tx').offsetWidth)]);
    });
  }
  for (const [e, m] of plan) e.style.marginRight = `${m}px`;
}

let last = {};
let nodes = { chords: [] }; // per-view handles for cheap per-frame updates
const scrollLock = { until: 0 };

const roleKey = () => (store.role === 'bass' ? 'bass' : store.role === 'keys' ? 'keys' : 'guitar');
const KINDS = ['guitar', 'bass', 'keys', 'other'];
const firstOf = (tab, kind) => Object.keys(tab.tracks).find((k) => tab.tracks[k].kind === kind);
const cap = (k) => k[0].toUpperCase() + k.slice(1);

function paintParts() {
  const tab = S.track?.tab, on = !!tab && S.view === 'tab';
  const kinds = on ? KINDS.filter((k) => firstOf(tab, k)) : [];
  const kind = tab?.tracks[S.part]?.kind;
  const mk = (text, data, pressed) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = text;
    Object.assign(b.dataset, data);
    b.setAttribute('aria-selected', String(pressed));
    return b;
  };
  $('tab-part').replaceChildren(...kinds.map((k) => mk(cap(k), { kind: k }, k === kind)));
  $('tab-part').hidden = kinds.length < 2;
  const same = on ? Object.keys(tab.tracks).filter((k) => tab.tracks[k].kind === kind) : [];
  $('tab-variant').replaceChildren(...same.map((k, i) => mk(String(i + 1), { part: k }, k === S.part)));
  $('tab-variant').hidden = same.length < 2;
}

function chrome() {
  if (S.view === 'tab' && S.track?.tabDone && !S.track.tab) S.view = 'lyrics'; // this song has no tab
  document.querySelectorAll('#tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.view === S.view)));
  const tab = !!S.track?.tab;
  $('tab-btn').hidden = !tab;
  paintParts();
  $('chord-sw').hidden = S.view === 'chords';
  $('chord-lbl').textContent = S.view === 'tab' ? 'Chords above tab' : 'Chords on lyrics';
  $('with-chords').checked = S.view === 'tab' ? S.tabChords : S.withChords;
}

function render() {
  last = { semis: semis() + chordKey() };
  nodes = { chords: [] };
  const stage = $('stage');
  stage.replaceChildren();
  stage.scrollTop = 0;
  aim.top = null;
  chrome();
  const t = S.track;
  if (!t && S.fail) {
    stage.append(el('div', { className: 'empty' },
      el('p', { textContent: S.fail.text }),
      el('button', { className: 'btn', type: 'button', textContent: 'Try again', onclick: () => loadTrack(S.fail.id, S.fail.model) })));
    return;
  }
  if (!t) { stage.append(el('p', { className: 'empty', textContent: S.host?.id ? 'Loading…' : 'Waiting for the host to open a song.' })); return; }
  if (S.view === 'chords') renderChart(stage, t); else if (S.view === 'tab' && t.tab) renderTab(stage, t); else renderLyrics(stage, t);
  fit();
  fitTab();
  update();
}

function renderLyrics(stage, t) {
  const ly = t.lyrics;
  if (!ly?.lines?.length) { stage.append(el('p', { className: 'empty', textContent: t.pending ? 'Finding the lyrics… the host may be listening to the song.' : t.structure ? 'No lyrics for this song. The Chords tab has the bars.' : 'No lyrics for this song.' })); return; }
  const withChords = S.withChords && ly.synced && !!t.chords;
  const box = el('div', { className: `ly${ly.synced ? '' : ' plain'}${withChords ? ' with-chords' : ''}` });
  nodes.lines = [];
  nodes.lineT = [];
  const lines = ly.lines;
  const cs = withChords ? t.chordT.map((tt, i) => ({ t: tt, i, c: t.chords[i] })) : [];
  let ci = 0;
  const instr = (label, list) => {
    if (!list.length) return;
    if (label) box.append(el('div', { className: 'sec', textContent: label }));
    box.append(el('div', { className: 'ln instr' }, el('span', { className: 'chs' }, ...list.map((k) => pill(k.c, k.i)))));
  };
  let sec = null;
  lines.forEach((ln, i) => {
    const text = ln.text || '♪';
    const row = el('div', { className: 'ln' });
    const node = { el: row, syl: [], t: ln.t ?? Infinity };
    if (ln.sec && ln.sec !== sec) box.append(el('div', { className: 'sec', textContent: ln.sec }));
    sec = ln.sec || sec;
    if (!ly.synced || ln.t == null) {
      row.textContent = text;
    } else {
      let next = null;
      for (let k = i + 1; k < lines.length; k++) if (lines[k].t != null) { next = lines[k].t; break; }
      const tl = timeLine(text, ln.t, next, ln.ws, ln.we);
      const lo = i === 0 ? ln.t - 0.15 : ln.t;
      const gap = [];
      while (ci < cs.length && cs[ci].t < lo) gap.push(cs[ci++]);
      instr(i === 0 ? 'Intro' : '', gap.length >= 1 && (i === 0 || ln.t - (lines[i - 1].e ?? lines[i - 1].t) > 2) ? gap : []);
      const here = [];
      while (ci < cs.length && cs[ci].t < tl.end) here.push(cs[ci++]);
      splitAt(tl, here.map((k) => k.t));
      const slots = tl.syl.map(() => []);
      for (const k of here) slots[Math.max(0, tl.syl.findLastIndex((o) => o.t <= k.t + 0.08))].push(k);
      let si = 0;
      for (const p of tl.parts) {
        if (p.space) continue;
        row.append(el('span', { className: 'w' }, ...p.pieces.map((o) => {
          const sy = el('span', { className: 'sy' });
          if (slots[si].length) {
            sy.append(el('span', { className: 'chs' }, ...slots[si].map((k) => { const pl = pill(k.c, k.i); if (k.carry) pl.classList.add('carry'); return pl; })));
          }
          const tx = el('span', { className: 'tx', textContent: o.txt });
          sy.append(tx);
          sy.dataset.t = String(o.t);
          sy.dataset.e = String(o.e);
          node.syl.push({ el: sy, t: o.t, e: o.e, s: 0 });
          si++;
          return sy;
        })));
      }
      node.end = tl.end;
    }
    nodes.lines.push(node);
    nodes.lineT.push(node.t);
    box.append(row);
  });
  if (withChords && ci < cs.length) instr('Outro', cs.slice(ci));
  stage.append(box);
}

// ── tab ──
// Songsterr's bars were matched to the recording's bars on the host, so a tab bar is drawn over exactly the
// stretch of the song it is played in: notes, chords and words all sit at their time inside the bar.
const TAB_BAR_MIN = 11; // rem a bar needs before a row takes one more

function tabWords(t) {
  const ly = t.lyrics;
  if (!ly?.synced) return [];
  const out = [];
  ly.lines.forEach((ln, i) => {
    if (!ln.text || ln.t == null) return;
    let next = null;
    for (let k = i + 1; k < ly.lines.length; k++) if (ly.lines[k].t != null) { next = ly.lines[k].t; break; }
    for (const p of timeLine(ln.text, ln.t, next, ln.ws, ln.we).parts) {
      if (p.pieces) out.push({ txt: p.pieces.map((o) => o.txt).join(''), t: p.pieces[0].t, e: p.pieces.at(-1).e });
    }
  });
  return out;
}

function renderTab(stage, t) {
  const tab = t.tab, st = t.structure, part = tab.tracks[S.part];
  if (!part || !st) return;
  const rows = Math.max(1, Math.floor(stage.clientWidth / (TAB_BAR_MIN * parseFloat(getComputedStyle(document.documentElement).fontSize))));
  const bars = st.bars;
  const diffs = bars.slice(1).map((b, i) => b - bars[i]).sort((a, b) => a - b);
  const bpb = diffs.length ? diffs[diffs.length >> 1] : 4;
  const endT = (j) => t.beatSec(bars[j + 1] ?? bars[j] + bpb);
  const of = new Map(); // recording bar -> tab bar
  tab.map.forEach((j, i) => { if (j != null && !of.has(j) && i < part.bars.length) of.set(j, i); });
  const first = [...of.keys()].reduce((a, b) => Math.min(a, b), Infinity);
  const last = [...of.keys()].reduce((a, b) => Math.max(a, b), -1);
  const words = tabWords(t);
  const strings = part.tuning.length;
  const box = el('div', { className: `tab${S.tabChords ? ' with-chords' : ''}`, style: `--strings:${strings}` });
  box.append(el('p', { className: 'tab-src' },
    `${part.name.split('|').at(-1).trim() || S.part} · tab by `,
    el('a', { href: tab.url, target: '_blank', rel: 'noopener', textContent: 'Songsterr' }),
    ' · original key'));
  nodes.bars = [];
  nodes.tabWords = [];
  nodes.tabCells = [];
  let row = null, rowLane = null, rowStart = 0;
  const wa = words.length ? floorIndex(t.barT, words[0].t) : Infinity, wz = words.length ? floorIndex(t.barT, words.at(-1).t) : -1;
  const from = Math.max(0, Math.min(first - 1, wa)), to = Math.min(bars.length - 1, Math.max(last + 1, wz));
  for (let j = from; j <= to; j++) {
    if (!row || (j - from) % rows === 0) {
      rowLane = el('div', { className: 'tly' });
      row = el('div', { className: 'trow', style: `--cols:${rows}` });
      rowStart = j;
      box.append(row, rowLane);
      rowLane.hidden = !words.length;
    }
    const b0 = t.barT[j], b1 = endT(j);
    const tb = of.has(j) ? part.bars[of.get(j)] : null;
    const cell = el('div', { className: `bar tb${tb ? '' : ' blank'}` });
    cell.append(el('span', { className: 'n', textContent: String(j + (bars[0] > 0 ? 0 : 1)) }));
    if (tb?.mark) cell.append(el('span', { className: 'sec-tag', textContent: tb.mark }));
    const stf = el('div', { className: 'stf' });
    if (tb) {
      for (const [pos, , frets] of tb.n) for (const [s, f] of frets) {
        const n = el('span', { className: 'f', textContent: String(f) });
        n.style.left = `${pos * 100}%`;
        n.style.top = `calc(${s} * var(--row))`;
        stf.append(n);
      }
    }
    stf.append(el('span', { className: 'cursor' }));
    if (S.tabChords && t.chords) {
      const lane = el('div', { className: 'tchs' });
      t.chords.forEach((c, i) => {
        if (c.b < bars[j] - 0.01 || c.b >= (bars[j + 1] ?? bars[j] + bpb) - 0.01) return;
        const pl = pill(c, i);
        pl.style.left = `${((c.b - bars[j]) / ((bars[j + 1] ?? bars[j] + bpb) - bars[j])) * 100}%`;
        lane.append(pl);
      });
      cell.append(lane);
    }
    cell.append(stf);
    row.append(cell);
    cell.dataset.t0 = String(b0);
    cell.dataset.t1 = String(b1);
    nodes.bars[j] = cell;
    nodes.tabCells[j] = [b0, b1];
    const col = j - rowStart;
    for (const w of words) {
      if (w.t < b0 - 1e-6 || w.t >= b1) continue;
      const sp = el('span', { className: 'tw', textContent: w.txt });
      sp.dataset.at = String((col + (w.t - b0) / (b1 - b0)) / rows);
      rowLane.append(sp);
      nodes.tabWords.push({ el: sp, t: w.t, e: w.e });
    }
  }
  box.append(el('div', { className: 'pad' }));
  stage.append(box);
  nodes.tabWords.sort((a, b) => a.t - b.t);
  nodes.tabWordT = nodes.tabWords.map((w) => w.t);
}

// Words are placed at their time; push right the ones that would overlap the previous word.
function fitTab() {
  if (S.view !== 'tab') return;
  document.querySelectorAll('.tly').forEach((lane) => {
    const w = lane.clientWidth;
    let edge = 0, level = 0;
    for (const sp of lane.children) {
      const sw = sp.offsetWidth;
      let x = Math.max(+sp.dataset.at * w, edge);
      if (x + sw > w && edge > 0) { level++; x = clamp(+sp.dataset.at * w, 0, Math.max(0, w - sw)); } // no room left on this line
      sp.style.left = `${x}px`;
      sp.style.top = `${level * 1.25}rem`;
      edge = x + sw + 4;
    }
    lane.style.height = `${(level + 1) * 1.25 + 0.1}rem`;
  });
}

// A bar is a row of chord slots sized by beats. A chord is labelled in the bar where it starts and only
// drawn as a tie where it carries on, so one chord is never written twice.
function renderChart(stage, t) {
  const st = t.structure;
  if (!st) { stage.append(el('p', { className: 'empty', textContent: 'No chords yet. Analyse the song on the host.' })); return; }
  const bars = st.bars;
  const first = bars[0] > 0 ? 0 : 1;
  const diffs = bars.slice(1).map((b, i) => b - bars[i]).sort((a, b) => a - b);
  const bpb = diffs.length ? diffs[diffs.length >> 1] : 4;
  const cs = t.chords;
  const endOf = (i) => (cs[i + 1] ? Math.min(cs[i].b + cs[i].d, cs[i + 1].b) : cs[i].b + cs[i].d);
  const secs = new Map();
  (t.lyrics?.lines || []).forEach((ln) => { if (ln.sec && t.lyrics.synced) secs.set(ln.t, ln.sec); });
  const secAt = (j) => { // a lyric section opening inside this bar
    for (const [tt, name] of secs) if (tt >= t.barT[j] - 0.3 && tt < (t.barT[j + 1] ?? Infinity) - 0.3) return name;
    return '';
  };
  const chart = el('div', { className: 'chart' });
  nodes.bars = [];
  bars.forEach((b0, j) => {
    const b1 = bars[j + 1] ?? b0 + bpb;
    const cell = el('div', { className: 'bar' }, el('span', { className: 'n', textContent: String(j + first) }));
    const tag = secAt(j);
    if (tag) cell.append(el('span', { className: 'sec-tag', textContent: tag }));
    cs.forEach((c, i) => {
      const s = Math.max(c.b, b0), e = Math.min(endOf(i), b1);
      if (e - s < 0.2) return;
      const slot = el('div', { className: 'seg-c' });
      slot.style.setProperty('--w', String(e - s));
      slot.style.setProperty('--h', hueOf(c));
      if (c.b >= b0 - 0.01) slot.append(pill(c, i)); else slot.append(el('span', { className: 'tie' }));
      cell.append(slot);
    });
    cell.dataset.t0 = String(t.beatSec(b0));
    cell.dataset.t1 = String(t.beatSec(b1));
    nodes.bars[j] = cell;
    chart.append(cell);
  });
  stage.append(chart);
}

let frameAt = performance.now();
function tick(now = performance.now()) {
  requestAnimationFrame(tick);
  glide(Math.min((now - frameAt) / 1000, 0.1));
  frameAt = now;
  update();
}

const setSy = (sy, s) => {
  if (sy.s === s) return;
  sy.s = s;
  sy.el.classList.toggle('done', s === 1);
  sy.el.classList.toggle('cur', s === 2);
  // Every syllable of the lit line is painted by the same gradient, so none of them switches rendering
  // (plain colour <-> clipped text) at the moment it is sung, which is what flashed.
  if (s !== 2) { sy.p = s === 1 ? 100 : 0; sy.el.style.setProperty('--p', `${sy.p}%`); }
};
const QUICK = 0.18; // a syllable this short is not worth a sweep: the clipped-text fill flashes when it comes and goes within a few frames
function paintLine(node, p) {
  for (const sy of node.syl) {
    if (p >= sy.e || (p >= sy.t && sy.e - sy.t < QUICK)) setSy(sy, 1);
    else if (p >= sy.t) {
      setSy(sy, 2);
      const step = Math.min(100, Math.round(((p - sy.t) / Math.max(0.05, sy.e - sy.t)) * 500) / 5); // 0.2% steps: finer than a pixel
      if (sy.p !== step) { sy.p = step; sy.el.style.setProperty('--p', `${step}%`); }
    } else setSy(sy, 0);
  }
}
// Scrolling is eased by hand each frame: restarting a native smooth scroll on every line change stutters.
const aim = { top: null, v: 0 };
function centre(node, frac) {
  if (Date.now() < scrollLock.until) return;
  aim.top = Math.max(0, node.offsetTop + node.offsetHeight / 2 - $('stage').clientHeight * frac);
}
function glide(dt) {
  const stage = $('stage');
  if (aim.top == null) return;
  if (Date.now() < scrollLock.until) { aim.top = null; return; }
  const d = aim.top - stage.scrollTop;
  // Critically damped spring: starts and stops gently and never leaves a tail of one-pixel steps.
  const w = 6.5;
  const x = -d, e = Math.exp(-w * dt);
  const tmp = (aim.v + w * x) * dt;
  const nx = (x + tmp) * e;
  aim.v = (aim.v - w * tmp) * e;
  if (Math.abs(d) < 0.4 && Math.abs(aim.v) < 4) { stage.scrollTop = aim.top; aim.top = null; aim.v = 0; return; }
  stage.scrollTop = aim.top + nx;
}
const LEAD = 0.25; // light a line slightly early: reading is faster than singing

function update() {
  if ($('app').hidden) return;
  const t = S.track;
  const p = pos();
  const dur = t?.meta.duration || 1;
  const pl = playing();
  if (last.pl !== pl) { $('play-i').setAttribute('href', pl ? '#i-pause' : '#i-play'); last.pl = pl; }
  if (!seekDrag && performance.now() > seekHold) { $('seek').value = String(Math.round((p / dur) * 1000)); $('seek').style.setProperty('--fill', `${(p / dur) * 100}%`); }
  const cur = fmt(p);
  if (last.cur !== cur) { $('t-cur').textContent = cur; last.cur = cur; }
  const tp = Math.round(tempo() * 100), sm = semis(), ck = chordKey();
  const st0 = t?.structure, bpm = st0?.bpm ? Math.round(st0.bpm * tp / 100) : 0, kn = st0?.key ? keyName(st0.key, sm) : '';
  const tuneKey = `${tp}|${sm}|${bpm}|${kn}|${ck}`;
  if (last.tune !== tuneKey) {
    last.tune = tuneKey;
    $('tune-v').textContent = `${tp}% · ${sm > 0 ? '+' : ''}${sm}`;
    $('tune-v').classList.toggle('changed', tp !== 100 || sm !== 0);
    $('t-out').textContent = bpm ? `${tp}% · ${bpm} BPM` : `${tp}%`; $('t-range').value = String(tp); $('t-range').style.setProperty('--fill', `${tp - 50}%`);
    $('c-out').textContent = ck ? `${ck > 0 ? '+' : ''}${ck}${st0?.key ? ` · ${keyName(st0.key, sm + ck)}` : ''}` : 'with the audio'; $('c-range').value = String(ck);
    $('k-out').textContent = `${sm > 0 ? '+' : ''}${sm}${kn ? ` · ${kn}` : ''}`; $('k-range').value = String(sm); $('k-range').style.setProperty('--fill', `${((sm + 12) / 24) * 100}%`);
  }
  if (!t) return;
  const st = t.structure;
  const bi = st ? floorIndex(t.barT, p) : -1;
  const ci = st ? floorIndex(t.chordT, p) : -1;
  const barNo = st && bi >= 0 ? bi + (st.bars[0] > 0 ? 0 : 1) : 0;
  const wb = barNo > 0 ? String(barNo) : '–', wc = ci >= 0 && bi >= 0 ? cname(t.chords[ci]) : '';
  if (last.wb !== wb) { $('w-bar').textContent = wb; last.wb = wb; }
  if (last.wc !== wc) { $('w-chord').textContent = wc; last.wc = wc; }
  if (last.semis !== sm + ck) { relabel(); last.semis = sm + ck; last.wc = null; }

  if (S.view === 'lyrics' && nodes.lines) {
    const synced = !!t.lyrics?.synced;
    if (!synced) return;
    // clock corrections can nudge the position back a few ms; a small step back is jitter, not a seek
    const q = pl && last.q !== undefined && p < last.q && last.q - p < 0.25 ? last.q : p;
    last.q = q;
    const li = floorIndex(nodes.lineT, q + LEAD);
    if (li !== last.li) {
      nodes.lines.forEach((n, k) => {
        n.el.classList.toggle('on', k === li);
        n.el.classList.toggle('past', k < li);
        if (k !== li && k !== li - 1) n.syl.forEach((sy) => setSy(sy, k < li ? 1 : 0));
      });
      last.li = li;
      if (li >= 0) centre(nodes.lines[li].el, 0.36); else aim.top = 0;
    }
    for (const k of [li - 1, li]) if (k >= 0) paintLine(nodes.lines[k], q);
    if (nodes.chords.length && last.ci !== ci) { nodes.chords.forEach((r) => r.el.classList.toggle('on', r.i === ci)); last.ci = ci; }
  } else if ((S.view === 'chords' || S.view === 'tab') && nodes.bars) {
    if (bi !== last.bi) {
      nodes.bars.forEach((n, j) => { n.classList.toggle('now', j === bi); n.classList.toggle('past', j < bi); });
      last.bi = bi;
      if (bi >= 0 && nodes.bars[bi]) centre(nodes.bars[bi], 0.3);
    }
    if (last.ci !== ci) { nodes.chords.forEach((r) => r.el.classList.toggle('on', r.i === ci)); last.ci = ci; }
    if (S.view === 'tab' && bi >= 0 && nodes.tabCells[bi]) {
      const [b0, b1] = nodes.tabCells[bi];
      const x = Math.round(clamp((p - b0) / (b1 - b0), 0, 1) * 500) / 5;
      if (last.cx !== x || last.cb !== bi) { nodes.bars[bi].style.setProperty('--x', `${x}%`); last.cx = x; last.cb = bi; }
      const wi = floorIndex(nodes.tabWordT, p + 0.05);
      if (wi !== last.tw) {
        nodes.tabWords.forEach((w, k) => { w.el.classList.toggle('done', k < wi); w.el.classList.toggle('cur', k === wi); });
        last.tw = wi;
      }
    }
  }
}
['touchstart', 'wheel', 'pointerdown'].forEach((ev) => $('stage').addEventListener(ev, () => { scrollLock.until = Date.now() + 4000; }, { passive: true }));

// Tap a lyric line or a bar to jump there, as on the desktop (follow modes ask the host).
$('stage').addEventListener('click', (e) => {
  if (!S.track) return;
  const row = e.target.closest('.ln'), bar = e.target.closest('.bar');
  let at = null;
  if (row && nodes.lines) at = nodes.lines.find((n) => n.el === row)?.t;
  else if (bar && nodes.bars) { const j = nodes.bars.indexOf(bar); if (j >= 0) at = S.track.barT[j]; }
  if (at == null || !Number.isFinite(at)) return;
  act.seek(Math.max(0, at - 0.05));
  if (S.mode === 'solo' && !S.local?.playing) S.local?.play();
});

// ── controls ──
// In follow modes a control asks the host; solo drives the local player.
const act = {
  toggle: () => (S.mode === 'solo' ? S.local?.toggle() : send(playing() ? 'pause' : 'play')),
  seek: (s) => {
    s = clamp(s, 0, S.track?.meta.duration ?? s);
    scrollLock.until = 0;
    if (S.mode === 'solo') S.local?.seek(s); else { if (S.host) { S.host = { ...S.host, pos: s, s: serverNow() }; clock.at = 0; } send('seek', s); }
  },
  tempo: (v) => {
    v = clamp(Math.round(v * 20) / 20, 0.5, 1.5);
    if (S.mode === 'solo') { S.solo.tempo = v; S.local?.setTempo(v); } else send('tempo', v);
  },
  chord: (v) => {
    v = clamp(Math.round(v), -12, 12);
    if (S.mode === 'solo') { S.solo.chord = v; relabel(); last.semis = null; } else send('chordkey', v);
  },
  key: (v) => {
    v = clamp(Math.round(v), -12, 12);
    if (S.mode === 'solo') { S.solo.semitones = v; S.local?.setSemitones(v); } else send('pitch', v);
  },
};
const throttle = (fn, ms) => { let t = 0; return (...a) => { const n = performance.now(); if (n - t >= ms) { t = n; fn(...a); } }; };
$('play').onclick = () => act.toggle();
$('back').onclick = () => act.seek(pos() - 5);
$('fwd').onclick = () => act.seek(pos() + 5);
$('t-down').onclick = () => act.tempo(tempo() - 0.05);
$('t-up').onclick = () => act.tempo(tempo() + 0.05);
$('t-reset').onclick = () => act.tempo(1);
$('t-range').oninput = throttle((e) => act.tempo(e.target.value / 100), 90);
$('t-range').onchange = (e) => act.tempo(e.target.value / 100);
$('k-down').onclick = () => act.key(semis() - 1);
$('k-up').onclick = () => act.key(semis() + 1);
$('c-reset').onclick = () => act.chord(0);
$('c-range').oninput = throttle((e) => act.chord(+e.target.value), 90);
$('c-range').onchange = (e) => act.chord(+e.target.value);
$('c-down').onclick = () => act.chord(chordKey() - 1);
$('c-up').onclick = () => act.chord(chordKey() + 1);
$('k-reset').onclick = () => act.key(0);
$('k-range').oninput = throttle((e) => act.key(+e.target.value), 90);
$('k-range').onchange = (e) => act.key(+e.target.value);
// The bar follows the finger while it is down and seeks once on release; touch WebViews do not focus a range input,
// so a drag flag (not activeElement) keeps the ticker from pulling the thumb back.
const seekStart = () => { seekDrag = true; };
const seekEnd = () => { if (!seekDrag) return; seekDrag = false; seekHold = performance.now() + 1200; };
$('seek').addEventListener('pointerdown', seekStart);
$('seek').addEventListener('touchstart', seekStart, { passive: true });
['pointerup', 'pointercancel', 'touchend', 'touchcancel'].forEach((ev) => $('seek').addEventListener(ev, seekEnd));
$('seek').oninput = (e) => { $('t-cur').textContent = fmt((e.target.value / 1000) * (S.track?.meta.duration || 0)); e.target.style.setProperty('--fill', `${e.target.value / 10}%`); };
$('seek').onchange = (e) => { seekDrag = false; seekHold = performance.now() + 1200; act.seek((e.target.value / 1000) * (S.track?.meta.duration || 0)); };
$('tabs').onclick = (e) => {
  const v = e.target.dataset?.view;
  if (!v) return;
  S.view = store.view = v;
  save();
  render();
};
$('simple').checked = S.simple;
$('simple').onchange = (e) => {
  S.simple = store.simple = e.target.checked;
  save();
  if (S.track?.structure) { buildChords(S.track); render(); }
};
$('with-chords').onchange = (e) => {
  if (S.view === 'tab') S.tabChords = store.tabChords = e.target.checked; else S.withChords = store.chords = e.target.checked;
  save();
  render();
};
$('tab-part').onclick = (e) => {
  const k = e.target.dataset?.kind, tab = S.track?.tab;
  if (!k || !tab || tab.tracks[S.part]?.kind === k) return;
  const last = store['part:' + k];
  pickPart(tab.tracks[last]?.kind === k ? last : firstOf(tab, k));
};
$('tab-variant').onclick = (e) => pickPart(e.target.dataset?.part);
function pickPart(v) {
  if (!v || v === S.part) return;
  store['part:' + S.track.tab.tracks[v].kind] = v;
  S.part = store.part = v;
  save();
  chrome();
  render();
}
let tabCols = 0;
addEventListener('resize', () => { // a rotated phone fits a different number of bars per row
  if (S.view !== 'tab') return;
  const c = Math.floor($('stage').clientWidth / (TAB_BAR_MIN * parseFloat(getComputedStyle(document.documentElement).fontSize)));
  if (c !== tabCols) { tabCols = c; render(); }
});

// ── sheet ──
const sheet = $('sheet');
const songs = $('songs');
let library = [];
const openSongs = () => { songs.showModal(); $('song-q').value = ''; loadLibrary(); loadPlaylists(); };
$('songs-btn').onclick = openSongs;
$('who')?.addEventListener('click', openSongs);
$('songs-close').onclick = () => songs.close();
songs.addEventListener('click', (e) => { if (e.target === songs) songs.close(); });
$('song-q').oninput = () => paintLibrary();
// ── press feedback: a ripple and a short tick on every tap ──
let toastTimer;
function notice(text) {
  let t = $('toast');
  if (!t) { t = el('div', { id: 'toast', role: 'status' }); t.popover = 'manual'; document.body.append(t); } // the top layer, above open sheets
  t.textContent = text;
  try { if (!t.matches(':popover-open')) t.showPopover(); } catch { /* no popover support: stays behind sheets */ }
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.classList.remove('on'); try { t.hidePopover(); } catch { /* not shown */ } }, 2600);
}
document.addEventListener('pointerdown', (e) => {
  const b = e.target.closest?.('button, [role=tab], [role=radio], .lib li, .pls li, .pls button');
  if (!b || b.disabled) return;
  if (droid?.tap) droid.tap(); else navigator.vibrate?.(8);
  const r = b.getBoundingClientRect();
  const size = Math.max(r.width, r.height) * 1.6;
  const dot = el('span', { className: 'rip' });
  Object.assign(dot.style, { width: `${size}px`, height: `${size}px`, left: `${e.clientX - r.left - size / 2}px`, top: `${e.clientY - r.top - size / 2}px` });
  b.append(dot);
  dot.addEventListener('animationend', () => dot.remove());
}, { passive: true });

// ── stem volumes (on the host in "Follow"; on this phone in "Hear here" and "Solo") ──
const mixer = $('mixer');
const mix = store.mix || {};
const mixOf = (n) => mix[n] || (mix[n] = { v: 1, m: false });
const mixMuted = () => Object.keys(mix).filter((n) => mix[n].m);
function applyMix() {
  const p = S.local;
  if (!p) return;
  for (const s of p.stems) p.setVolume(s.name, mixOf(s.name).v);
  if (S.mode === 'solo') p.setMuteSet(mixMuted());
}
// "Follow" has no sound on the phone, so the mixer drives the host's stems (the room hears it); the other modes
// shape this phone's own sound.
const mixRemote = () => S.mode === 'follow';
function paintMixer() {
  const names = (S.track?.meta.stems || []).map((s) => s.name);
  const remote = mixRemote();
  $('mix-note').textContent = !names.length ? 'No song yet.' : remote ? 'Changes apply to the host, so everyone hears them.' : 'Changes apply to the sound on this phone only.';
  $('mix-list').replaceChildren(...names.map((n) => {
    const m = remote ? { v: S.host?.volumes?.[n] ?? 1, m: !!S.host?.muted?.includes(n) } : mixOf(n);
    const out = el('output', { textContent: `${Math.round(m.v * 100)}%` });
    const range = el('input', { type: 'range', min: 0, max: 150, step: 5, value: Math.round(m.v * 100), ariaLabel: `${n} volume` });
    const btn = el('button', { type: 'button', className: 'mute', textContent: m.m ? 'Muted' : 'On', ariaPressed: String(m.m) });
    const row = el('div', { className: `mrow${m.m ? ' off' : ''}` }, el('b', { textContent: n[0].toUpperCase() + n.slice(1) }), btn, range, out);
    const setVol = throttle((v) => send('volume', { name: n, v }), 90);
    range.oninput = () => {
      m.v = range.value / 100; out.textContent = `${range.value}%`;
      if (remote) { setVol(m.v); return; }
      store.mix = mix; save(); applyMix();
    };
    range.onchange = () => { if (remote) send('volume', { name: n, v: range.value / 100 }); };
    btn.onclick = () => {
      m.m = !m.m; btn.textContent = m.m ? 'Muted' : 'On'; btn.ariaPressed = String(m.m); row.classList.toggle('off', m.m);
      if (remote) { send('mute', { name: n, on: m.m }); return; }
      store.mix = mix; save(); applyMix(); syncLocal(false);
    };
    return row;
  }));
}
$('mix-btn').onclick = () => { paintMixer(); mixer.showModal(); };
$('mixer-close').onclick = () => mixer.close();
mixer.addEventListener('click', (e) => { if (e.target === mixer) mixer.close(); });
$('mix-reset').onclick = () => {
  if (mixRemote()) { for (const s of S.track?.meta.stems || []) { send('volume', { name: s.name, v: 1 }); send('mute', { name: s.name, on: false }); } setTimeout(paintMixer, 300); return; }
  for (const k of Object.keys(mix)) delete mix[k]; store.mix = mix; save(); applyMix(); syncLocal(false); paintMixer(); };

// ── save to this phone (Android app only) ──
if (droid?.download) {
  $('save-group').hidden = false;
  const abs = (p) => new URL(withPin(p), location.href).href;
  const need = () => { if (!S.track) { notice('Pick a song first.'); return null; } return S.track; };
  const folder = (t) => t.meta.title || t.id;
  $('save-orig').onclick = () => { const t = need(); if (!t) return; droid.download(abs(`/api/dl/${t.id}/original.m4a`), folder(t)); notice('Saving the original…'); };
  $('save-stems').onclick = () => {
    const t = need();
    if (!t) return;
    for (const s of t.meta.stems) droid.download(abs(`/api/dl/${t.id}/${t.model}/${encodeURIComponent(s.name)}.m4a`), folder(t));
    notice(`Saving ${t.meta.stems.length} stems…`);
  };
}

const openSheet = () => { sheet.showModal(); showHostName(); modeUi(); $('me').textContent = `${store.name || 'You'} · ${ROLES[store.role] || ''}`; };
$('more').onclick = openSheet;
$('where').onclick = openSheet;
$('tune').onclick = openSheet;
$('sheet-close').onclick = () => sheet.close();
sheet.addEventListener('click', (e) => { if (e.target === sheet) sheet.close(); });
$('lat').value = String(S.lat);
$('lat-out').textContent = `${S.lat} ms`;
$('lat').oninput = (e) => { S.lat = +e.target.value; $('lat-out').textContent = `${S.lat} ms`; store.lat = S.lat; save(); syncLocal(true); };
$('resync').onclick = () => { samples = []; for (let i = 0; i < 8; i++) setTimeout(ping, i * 100); syncLocal(true); };
$('fs-down').onclick = () => { store.fs = clamp(Math.round(((store.fs || 1) - 0.1) * 10) / 10, 0.8, 1.6); save(); applyLook(); fit(); };
$('fs-up').onclick = () => { store.fs = clamp(Math.round(((store.fs || 1) + 0.1) * 10) / 10, 0.8, 1.6); save(); applyLook(); fit(); };
$('theme-seg').onclick = (e) => { const v = e.target.dataset?.theme; if (v) { store.theme = v; save(); applyLook(); } };
const paintAccidentals = () => document.querySelectorAll('#acc-seg button').forEach((b) => b.setAttribute('aria-checked', String(getAccidentals() === b.dataset.acc)));
$('acc-seg').onclick = (e) => { const v = e.target.dataset?.acc; if (v) { setAccidentals(v); paintAccidentals(); last.tune = null; relabel(); } };
paintAccidentals();
$('mode-seg').onclick = (e) => { const v = e.target.dataset?.mode; if (v) setMode(v); };
$('change-me').onclick = () => { sheet.close(); showJoin(); };
if (droid?.switchHost) { $('host-row').hidden = false; $('switch-host').onclick = () => droid.switchHost(); }
// The desktop app opens this page for a host it has joined: leaving goes back to its own library.
let joined = false;
if (/^(127\.0\.0\.1|localhost)$/.test(location.hostname) && !OFFLINE) {
  rawFetch('/api/client').then((r) => r.json()).then((c) => {
    if (!c.connected) return;
    joined = true;
    $('host-row').hidden = false;
    $('host-row').querySelector('span').textContent = 'Joined host';
    $('switch-host').textContent = 'Leave';
    $('switch-host').onclick = async () => { await rawFetch('/api/client/disconnect', { method: 'POST' }); location.href = '/'; };
  }).catch(() => { /* a plain browser tab on this computer */ });
}

async function showHostName() {
  $('host-name').textContent = location.hostname;
  try {
    const h = await (await rawFetch('/remote/hello')).json();
    $('host-name').textContent = joined ? h.name : `${h.name} · ${location.hostname}`;
  } catch { /* the address alone will do */ }
}

async function loadLibrary() {
  try {
    library = await getJson('/api/library');
    if (OFFLINE && !library.length) paintEmptyOffline(); else paintLibrary();
  } catch { $('lib').textContent = 'Could not load the library.'; }
}

// ── playlists: the host keeps them; the phone reads them and switches songs inside one ──
let lists = [];
let plView = null; // id of the playlist whose tracks are showing in the Songs sheet
let plTab = 'songs';
const withPin = (u) => (store.pin ? `${u}?k=${encodeURIComponent(store.pin)}` : u);

async function loadPlaylists() {
  try { lists = await getJson('/api/playlists'); } catch { lists = []; }
  if (S.pl) S.pl = lists.find((p) => p.id === S.pl.id) || null;
  paintPlaylists();
  syncPlNav();
}

function setSongTab(tab) {
  plTab = tab;
  document.querySelectorAll('#song-tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  $('lib').hidden = $('song-q').hidden = tab !== 'songs';
  $('pls').hidden = tab !== 'lists';
  if (tab === 'lists') plView = null;
  paintPlaylists();
}
$('song-tabs').onclick = (e) => { const t = e.target.dataset?.tab; if (t) setSongTab(t); };

function playSong(id, model, plist) {
  songs.close();
  if (plist !== undefined) S.pl = plist;
  if (S.mode === 'solo') { S.solo.tempo = 1; S.solo.semitones = 0; S.solo.chord = 0; loadTrack(id, model); } else send('load', { id, model });
  syncPlNav();
}
const modelOf = (id) => library.find((i) => i.id === id)?.models?.[0];

function paintPlaylists() {
  const box = $('pls');
  if (plTab !== 'lists') return;
  const pl = lists.find((p) => p.id === plView);
  if (!pl) {
    box.replaceChildren(...(lists.length ? [el('ul', { className: 'lib' }, ...lists.map((p) => {
      const img = el('img', { className: 'cover', loading: 'lazy', alt: '', src: p.items[0] ? withPin(`/media/${p.items[0].id}/thumb.jpg`) : '' });
      img.onerror = () => img.classList.add('none');
      const li = el('li', { className: S.pl?.id === p.id ? 'on' : '' }, img,
        el('span', { className: 'meta' }, el('b', { textContent: p.name }), el('small', { textContent: `${p.items.length} songs` })));
      li.onclick = () => { plView = p.id; paintPlaylists(); };
      return li;
    }))] : [el('p', { className: 'note', textContent: 'No playlists yet. Create one on the computer.' })]));
    return;
  }
  const back = el('button', { className: 'btn-line', type: 'button', textContent: '‹ Playlists' });
  back.onclick = () => { plView = null; paintPlaylists(); };
  const all = el('button', { className: 'btn-line', type: 'button', textContent: 'Save all' });
  all.onclick = () => pl.items.forEach((it) => { const m = modelOf(it.id); if (m && !off.have.has(it.id)) keep(it.id, m); });
  box.replaceChildren(el('div', { className: 'pl-top' }, back, el('b', { textContent: pl.name }), ...(droid?.offlineSave && !OFFLINE ? [all] : [])),
    el('ul', { className: 'lib' }, ...pl.items.map((it, n) => {
      const model = modelOf(it.id);
      const img = el('img', { className: 'cover', loading: 'lazy', alt: '', src: withPin(`/media/${it.id}/thumb.jpg`) });
      img.onerror = () => img.classList.add('none');
      const li = el('li', { className: `${S.track?.id === it.id ? 'on' : ''}${model ? '' : ' wait'}` }, img,
        el('span', { className: 'meta' }, el('b', { textContent: `${n + 1}. ${it.title || it.id}` }),
          el('small', { textContent: model ? `${it.channel || ''}${it.channel ? ' · ' : ''}${fmt(it.duration)}` : 'Not ready on the host yet' })));
      if (model) li.onclick = () => playSong(it.id, model, pl);
      li.append(...[keepBtn(it.id, model)].filter(Boolean));
      return li;
    })));
}

function plStep(d) {
  const i = S.pl && S.track ? S.pl.items.findIndex((x) => x.id === S.track.id) : -1;
  for (let j = i + d; i >= 0 && j >= 0 && j < S.pl.items.length; j += d) {
    const m = modelOf(S.pl.items[j].id);
    if (m) { playSong(S.pl.items[j].id, m); return; }
  }
}
function syncPlNav() {
  const i = S.pl && S.track ? S.pl.items.findIndex((x) => x.id === S.track.id) : -1;
  $('pl-prev').hidden = $('pl-next').hidden = i < 0;
  $('pl-prev').disabled = i <= 0;
  $('pl-next').disabled = i < 0 || i >= S.pl.items.length - 1;
}
$('pl-prev').onclick = () => plStep(-1);
$('pl-next').onclick = () => plStep(1);

// ── songs kept on this phone (Android app) ──
const off = { have: new Map(), busy: new Map() }; // id -> {bytes} | id -> 0..1
function refreshOffline() {
  try { off.have = new Map(JSON.parse(droid?.offlineList?.() || '[]').map((i) => [i.id, i])); } catch { off.have = new Map(); }
}
refreshOffline();
let armed = null, armTimer = 0;
function repaintKeep() {
  paintLibrary();
  paintPlaylists();
}
window.__offline = (id, p) => {
  if (p >= 1) { off.busy.delete(id); refreshOffline(); }
  else if (p < 0) off.busy.delete(id);
  else off.busy.set(id, p);
  repaintKeep();
};
function keep(id, model) {
  if (!droid?.offlineSave || off.busy.has(id)) return;
  off.busy.set(id, 0);
  droid.offlineSave(id, model, store.pin || '');
  repaintKeep();
}
function unkeep(id) {
  droid.offlineRemove(id);
  refreshOffline();
  armed = null;
  if (OFFLINE) { library = library.filter((i) => i.id !== id); if (!library.length) paintEmptyOffline(); }
  repaintKeep();
}
$('cache-grp').hidden = !droid?.offlineSave || OFFLINE;
$('autosave').checked = !!store.autosave;
$('autosave').onchange = (e) => {
  store.autosave = e.target.checked;
  save();
  if (store.autosave && S.track && !off.have.has(S.track.id)) keep(S.track.id, S.track.model);
};
function keepBtn(id, model) {
  if (CLOUD || !droid?.offlineSave || (!model && !off.have.has(id))) return null;
  const busy = off.busy.get(id);
  const saved = off.have.has(id);
  const text = busy !== undefined ? `${Math.round(busy * 100)}%` : armed === id ? 'Remove?' : saved ? 'Saved' : 'Save';
  const b = el('button', { type: 'button', className: `keep${saved ? ' on' : ''}${armed === id ? ' arm' : ''}`, textContent: text, disabled: busy !== undefined });
  b.onclick = (e) => {
    e.stopPropagation();
    if (!saved) { if (!OFFLINE) keep(id, model); return; }
    if (armed === id) { clearTimeout(armTimer); unkeep(id); return; } // confirm() does not work in the WebView
    armed = id;
    clearTimeout(armTimer);
    armTimer = setTimeout(() => { armed = null; repaintKeep(); }, 3000);
    repaintKeep();
  };
  return b;
}
function paintEmptyOffline() {
  $('lib').replaceChildren(el('p', { className: 'note', textContent: CLOUD ? 'No songs in the cloud yet. Publish them from Prismix on the computer.' : 'No songs saved yet. Connect to a host and tap Save next to a song.' }));
}

function offlineStart() {
  document.body.classList.add('offline');
  $('boot').hidden = true;
  $('join').hidden = true;
  if (!store.role) { showJoin(); return; }
  $('app').hidden = false;
  $('mode-seg').style.display = 'none';
  $('lat-row').style.display = 'none';
  $('song-tabs').style.display = 'none';
  $('title').textContent = CLOUD ? 'Band Cloud' : 'Offline songs';
  $('sub').textContent = CLOUD ? "Your band's library" : 'Saved on this phone';
  setLink(CLOUD ? 'ok' : 'bad', CLOUD ? 'Cloud' : 'Offline');
  S.mode = 'solo';
  chrome();
  render();
  modeUi();
  loadLibrary();
  if (!S.track) openSongs();
}

function paintLibrary() {
  const q = $('song-q').value.trim().toLowerCase();
  const items = library.filter((it) => !q || `${it.title} ${it.channel || ''}`.toLowerCase().includes(q));
  $('lib').replaceChildren(...items.map((it) => {
    const model = it.models?.[0];
    const img = el('img', { className: 'cover', loading: 'lazy', alt: '', src: `/media/${it.id}/thumb.jpg${store.pin ? `?k=${encodeURIComponent(store.pin)}` : ''}` });
    img.onerror = () => img.classList.add('none');
    const meta = el('span', { className: 'meta' }, el('b', { textContent: it.title }), el('small', { textContent: `${it.channel || ''}${it.channel ? ' · ' : ''}${fmt(it.duration)}` }));
    const li = el('li', { className: S.track?.id === it.id ? 'on' : '' }, img, meta);
    li.onclick = () => playSong(it.id, model, null);
    li.append(...[keepBtn(it.id, model)].filter(Boolean));
    return li;
  }));
}

// ── modes and local audio ──
const MODE_NOTE = {
  follow: 'The screen follows the host. Sound comes from the room.',
  listen: 'Sound plays here too, kept in step with the host.',
  solo: 'Plays only on this phone, on its own tempo and key.',
};
const claimSpeaker = () => { if (S.mode === 'listen' && S.local && S.speaker !== S.id) sendSpeaker(true); };
const sendSpeaker = (on) => net.send({ t: 'speaker', on });
function paintSpeaker() {
  $('spk-row').hidden = S.mode !== 'listen';
  $('spk').checked = S.speaker != null && S.speaker === S.id;
}
$('spk').onchange = (e) => sendSpeaker(e.target.checked);
function modeUi(extra) {
  document.body.classList.toggle('solo', S.mode === 'solo');
  document.querySelectorAll('#mode-seg button').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.mode === S.mode)));
  $('lat-row').hidden = S.mode !== 'listen';
  paintSpeaker();
  if (extra !== undefined) $('m-note').textContent = extra;
  else if (!$('m-note').dataset.busy) $('m-note').textContent = MODE_NOTE[S.mode];
}
async function setMode(mode) {
  const prev = S.mode;
  S.mode = mode;
  if (!OFFLINE) { store.mode = mode; save(); }
  if (mode !== 'listen' && S.speaker === S.id) sendSpeaker(false);
  modeUi();
  if (mode === 'follow') { S.local?.pause(); }
  else {
    if (mode === 'solo') {
      // start solo from what the room is doing now
      S.solo.tempo = S.host?.tempo ?? 1;
      S.solo.semitones = S.host?.semitones ?? 0;
    }
    try { await ensureLocal(); } catch (err) {
      S.mode = 'follow'; if (!OFFLINE) { store.mode = 'follow'; save(); }
      delete $('m-note').dataset.busy;
      modeUi(`Could not start sound on this phone: ${err?.message || err}`);
      return;
    }
    if (S.mode !== mode) return; // changed again while the audio loaded
    if (mode === 'solo' && S.local) {
      S.local.setTempo(S.solo.tempo); S.local.setSemitones(S.solo.semitones);
      S.local.seek(hostPos());
      S.local.setMuteSet(mixMuted());
      applyMix();
      if (prev === 'listen') S.local.pause();
    }
    if (mode === 'listen') { claimSpeaker(); syncLocal(true); }
  }
  last = { semis: semis() + chordKey() };
}

async function ensureLocal() {
  const t = S.track;
  const note = $('m-note');
  if (!t) return;
  if (!window.AudioWorklet) {
    S.mode = 'follow';
    sendSpeaker(false);
    modeUi('This browser cannot play the stems here (it needs a secure page). Open the host link over HTTPS or use the app.');
    return;
  }
  if (S.local && S.localTrack === `${t.id}/${t.model}`) return;
  const { StemPlayer } = await import('./player.js');
  S.local?.dispose();
  const p = new StemPlayer();
  note.dataset.busy = '1';
  note.textContent = 'Loading audio…';
  await p.load(t.meta, `/media/${t.id}/${t.model}`, (f) => { note.textContent = `Loading audio ${Math.round(f * 100)}%`; });
  delete note.dataset.busy;
  S.local = p;
  S.localTrack = `${t.id}/${t.model}`;
  applyMix();
  modeUi();
  p.addEventListener('ended', () => { if (S.mode === 'solo') last = {}; });
}

let lastFix = 0;
function syncLocal(force = false) {
  const p = S.local, h = S.host;
  if (S.mode !== 'listen' || !p || !h) return;
  if (p.tempo !== h.tempo) p.setTempo(h.tempo);
  if (p.semitones !== h.semitones) p.setSemitones(h.semitones);
  if (p.cents !== h.cents) p.setCents(h.cents);
  const off = h.solo?.length ? p.stems.map((s) => s.name).filter((n) => !h.solo.includes(n)) : h.muted || [];
  p.setMuteSet([...new Set([...off, ...mixMuted()])]);
  if (h.loop) p.setLoop(h.loop.a, h.loop.b); else if (p.loop) p.setLoop(null, null);
  const want = hostPos() + S.lat / 1000 * h.tempo; // where the room is, nudged by this phone's delay
  const ahead = (0.05 + p.outLatency) * h.tempo; // a seek lands this far later and is heard later still
  if (!h.playing) { if (p.playing) p.pause(); if (force) p.seek(want); return; }
  if (!p.playing) {
    p.seek(want + ahead + 0.1 * h.tempo);
    p.play();
    lastFix = performance.now();
    return;
  }
  const heard = p.position - p.outLatency * p.tempo;
  const err = heard - want;
  if ((force || Math.abs(err) > 0.08) && performance.now() - lastFix > 1500) {
    p.seek(want + ahead);
    lastFix = performance.now();
  }
}

// ── join ──
const ROLES = { singer: 'Vocals', guitar: 'Guitar', bass: 'Bass', keys: 'Keys', drums: 'Drums', other: 'Other' };
const withChordsRole = new Set(['guitar', 'bass', 'keys']);
let pickedRole = store.role || '';
function paintRoles() {
  document.querySelectorAll('#roles button').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.role === pickedRole)));
}
$('roles').onclick = (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  pickedRole = b.dataset.role;
  paintRoles();
};
async function showJoin(msg = '', needPin = false) {
  $('boot').hidden = true;
  $('app').hidden = true;
  $('join').hidden = false;
  $('j-err').textContent = msg;
  let pin = needPin || !!store.pin;
  if (!pin) { try { pin = (await (await rawFetch('/remote/hello')).json()).pin; } catch { /* the form still works */ } }
  $('j-pin-row').hidden = !pin;
  paintRoles();
}
$('j-name').value = store.name || droid?.deviceName?.() || '';
$('j-pin').value = store.pin || '';
$('join').onsubmit = (e) => {
  e.preventDefault();
  if (!pickedRole) { $('j-err').textContent = 'Pick your part first.'; return; }
  const changed = store.role !== pickedRole;
  store.name = $('j-name').value.trim() || droid?.deviceName?.() || 'Phone';
  store.role = pickedRole;
  store.pin = $('j-pin').value.trim();
  if (changed || store.chords == null) {
    store.chords = withChordsRole.has(pickedRole);
    store.view = pickedRole === 'drums' || pickedRole === 'other' ? 'chords' : 'lyrics';
  }
  save();
  S.view = store.view || 'lyrics';
  S.withChords = store.chords;
  $('j-err').textContent = '';
  if (OFFLINE) { offlineStart(); return; }
  welcomed = false;
  $('boot').hidden = false;
  $('join').hidden = true;
  net.reconnect();
};
if (OFFLINE) offlineStart();
else {
  if (store.name && store.role) net.reconnect(); else showJoin();
  save(); // hand the settings to the app for the offline page
}
requestAnimationFrame(tick);
window.__remote = S; // debugging and end-to-end tests

// ── system media controls (notification shade, lock screen, Now Playing) ──
const media = createMedia({
  play: () => { if (!playing()) act.toggle(); },
  pause: () => { if (playing()) act.toggle(); },
  seek: (s) => act.seek(s),
  step: (d) => act.seek(pos() + d),
});
function syncMedia() {
  const t = S.track;
  if (!t || !$('join').hidden) { media.clear(); return; }
  media.update({
    title: t.meta.title,
    artist: t.meta.channel,
    art: `/media/${t.id}/thumb.jpg${store.pin ? `?k=${encodeURIComponent(store.pin)}` : ''}`,
    playing: playing(),
    pos: pos(),
    dur: t.meta.duration,
    rate: tempo(),
  });
}
// A hidden WebView throttles page timers to ~1/min; timers inside a worker keep their pace.
(function ticker() {
  let n = 0;
  const run = () => { syncLocal(false); if (++n % 2 === 0) syncMedia(); };
  try {
    const url = URL.createObjectURL(new Blob(['setInterval(()=>postMessage(0),500)'], { type: 'text/javascript' }));
    new Worker(url).onmessage = run;
  } catch { setInterval(run, 500); }
})();
document.addEventListener('visibilitychange', () => { syncLocal(true); syncMedia(); });

// A phone only lets a page make sound after a touch: wake the player on the next one if it was started without.
addEventListener('pointerdown', () => { const c = S.local?.ctx; if (c?.state === 'suspended') c.resume().catch(() => {}); }, { capture: true });
