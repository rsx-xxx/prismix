// Menu-bar mini player: a loopback client of the Band hub. The main window owns the audio; this only mirrors and drives it.
import { createSocket } from './socket.js';
import { keyName } from './theory.js';

const $ = (id) => document.getElementById(id);
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const sign = (v) => `${v > 0 ? '+' : ''}${v}`;
const S = { host: null, at: 0, track: null, loading: '' };

const net = createSocket({
  url: () => `ws://${location.host}/ws`,
  onopen: () => net.send({ t: 'hello', name: 'Menu bar', role: 'other' }),
  onmessage: (m) => {
    if (m.t === 'welcome' && m.state?.id) take(m.state);
    else if (m.t === 'state') take(m);
    paint();
  },
  onclose: () => { S.host = null; paint(); },
});
const cmd = (op, v) => net.send({ t: 'cmd', op, v });

function take(m) {
  S.host = m;
  S.at = performance.now();
  const key = `${m.id}/${m.model}`;
  if (m.id && S.loading !== key) {
    S.loading = key;
    Promise.all([
      fetch(`/api/library/${m.id}/${m.model}`).then((r) => r.json()),
      fetch(`/media/${m.id}/structure.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ]).then(([meta, structure]) => { S.track = { meta, structure }; paint(); }).catch(() => { S.loading = ''; });
  }
}

const pos = () => {
  const h = S.host;
  return h ? h.pos + (h.playing ? ((performance.now() - S.at) / 1000) * h.tempo : 0) : 0;
};
const idle = (input) => document.activeElement !== input;

function paint() {
  const h = S.host, t = S.track;
  const on = !!(h?.id && t);
  $('off').hidden = on;
  $('app').hidden = !on;
  if (!on) return;
  $('title').textContent = t.meta.title;
  const cover = `/media/${h.id}/thumb.jpg`;
  if ($('cover').getAttribute('src') !== cover) $('cover').src = cover;
  const tp = Math.round(h.tempo * 100), st = t.structure;
  $('sub').textContent = t.meta.channel || '';
  $('play').textContent = h.playing ? '❚❚' : '▶';
  $('dur').textContent = fmt(t.meta.duration);
  $('t-out').textContent = `${tp}%`;
  $('bpm').textContent = st?.bpm ? `${Math.round((st.bpm * tp) / 100)} BPM` : '';
  $('k-out').textContent = sign(h.semitones);
  $('key').textContent = st?.key ? keyName(st.key, h.semitones) : '';
  if (idle($('master'))) { $('master').value = String(Math.round((h.master ?? 1) * 100)); $('master-out').textContent = `${$('master').value}%`; }
  const stems = $('stems');
  const names = (t.meta.stems || []).map((s) => s.name);
  if (stems.dataset.sig !== `${t.meta.id}|${names}`) {
    stems.dataset.sig = `${t.meta.id}|${names}`;
    stems.replaceChildren(...names.map((n) => {
      const row = document.createElement('div');
      row.className = 'stem';
      row.dataset.name = n;
      const label = document.createElement('span');
      label.textContent = n;
      const vol = Object.assign(document.createElement('input'), { type: 'range', min: 0, max: 150, step: 5, ariaLabel: `${n} volume` });
      vol.oninput = () => cmd('volume', { name: n, v: vol.value / 100 });
      const mute = Object.assign(document.createElement('button'), { type: 'button', textContent: 'M', title: 'Mute' });
      mute.onclick = () => cmd('mute', { name: n, on: mute.getAttribute('aria-pressed') !== 'true' });
      const solo = Object.assign(document.createElement('button'), { type: 'button', textContent: 'S', title: 'Solo' });
      solo.onclick = () => cmd('solo', { name: n, on: solo.getAttribute('aria-pressed') !== 'true' });
      row.append(label, vol, mute, solo);
      return row;
    }));
  }
  for (const row of stems.children) {
    const n = row.dataset.name, [, vol, mute, solo] = row.children;
    if (idle(vol)) vol.value = String(Math.round((h.volumes?.[n] ?? 1) * 100));
    mute.setAttribute('aria-pressed', String(!!h.muted?.includes(n)));
    solo.setAttribute('aria-pressed', String(!!h.solo?.includes(n)));
  }
}

setInterval(() => {
  if (!S.host || !S.track) return;
  const p = pos();
  $('cur').textContent = fmt(p);
  if (idle($('seek'))) $('seek').value = String(Math.round((p / (S.track.meta.duration || 1)) * 1000));
}, 250);

$('play').onclick = () => cmd(S.host?.playing ? 'pause' : 'play');
$('back').onclick = () => cmd('seek', Math.max(0, pos() - 5));
$('fwd').onclick = () => cmd('seek', pos() + 5);
$('seek').onchange = () => cmd('seek', ($('seek').value / 1000) * (S.track?.meta.duration || 0));
$('t-dn').onclick = () => cmd('tempo', Math.max(0.5, Math.round(((S.host?.tempo ?? 1) - 0.05) * 100) / 100));
$('t-up').onclick = () => cmd('tempo', Math.min(1.5, Math.round(((S.host?.tempo ?? 1) + 0.05) * 100) / 100));
$('k-dn').onclick = () => cmd('pitch', Math.max(-12, (S.host?.semitones ?? 0) - 1));
$('k-up').onclick = () => cmd('pitch', Math.min(12, (S.host?.semitones ?? 0) + 1));
$('master').oninput = () => { $('master-out').textContent = `${$('master').value}%`; cmd('master', $('master').value / 100); };
