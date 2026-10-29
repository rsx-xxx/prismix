// Band mode, host side: publishes the player's state to the hub so phones can follow it,
// applies the commands phones send, and drives the Band panel (QR, roster, lock, PIN).
import { createSocket } from './socket.js';

const LS = 'prismix.band';

const signed = (v) => `${v > 0 ? '+' : ''}${v}`;
/** "Anna: key +2" for a `changed` message. */
export function describe(m) {
  const what = {
    tempo: `tempo ${Math.round(m.v * 100)}%`,
    pitch: `key ${signed(m.v)}`,
    cents: `fine tune ${signed(m.v)}`,
    chordkey: `chord key ${signed(m.v)}`,
    load: 'song',
  }[m.op];
  return what ? `${m.who}: ${what}` : '';
}

export function createBand({ player, current, open, onNotes, $ }) {
  let timer = 0;
  let status = { clients: [], locked: false, token: false, url: '', qr: '' };
  const saved = () => { try { return JSON.parse(localStorage.getItem(LS)) || {}; } catch { return {}; } };

  const snapshot = () => {
    const c = current();
    const q = player.loop;
    return {
      t: 'state',
      id: c?.id || null,
      model: c?.model || null,
      playing: player.playing,
      pos: Math.max(0, player.position - player.outLatency * player.tempo), // what the room hears
      tempo: player.tempo,
      semitones: player.semitones,
      chordKey: player.chordKey,
      cents: player.cents,
      master: Number($('master').value) / 100,
      loop: q ? { a: q.a, b: q.b } : null,
      muted: player.stems.filter((s) => s.muted).map((s) => s.name),
      solo: player.stems.filter((s) => s.solo).map((s) => s.name),
      volumes: Object.fromEntries(player.stems.map((s) => [s.name, s.volume])),
    };
  };

  function publish() { net.send(snapshot()); }

  const commands = {
    play: () => player.play(),
    pause: () => player.pause(),
    seek: (v) => player.seek(+v),
    tempo: (v) => player.setTempo(+v),
    pitch: (v) => player.setSemitones(+v),
    cents: (v) => player.setCents(+v),
    chordkey: (v) => player.setChordKey(+v),
    loop: (v) => (v ? player.setLoop(+v.a, +v.b) : player.setLoop(null, null)),
    mute: (v) => player._stem(v?.name) && player.setMuted(v.name, !!v.on),
    solo: (v) => player._stem(v?.name) && player.setSolo(v.name, !!v.on),
    master: (v) => { const m = Math.min(1, Math.max(0, +v)); $('master').value = String(Math.round(m * 100)); $('master').dispatchEvent(new Event('input')); },
    volume: (v) => player._stem(v?.name) && player.setVolume(v.name, +v.v),
    load: (v) => { if (/^[\w-]{1,64}$/.test(v?.id) && /^[\w.-]{1,64}$/.test(v?.model)) location.hash = `#/v/${v.id}/${v.model}`; },
  };

  let noteTimer = 0;
  /** Say, for a moment, who changed what: the host sees what phones do, as phones see each other. */
  function note(text) {
    let n = document.getElementById('band-note');
    if (!n) { n = document.createElement('div'); n.id = 'band-note'; n.setAttribute('role', 'status'); document.body.append(n); }
    n.textContent = text;
    n.classList.add('on');
    clearTimeout(noteTimer);
    noteTimer = setTimeout(() => n.classList.remove('on'), 2600);
  }

  // 4002: another window on this computer took over as host; reconnecting would start a tug of war
  const net = createSocket({
    url: () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`,
    fatal: [4002, 4403],
    onopen: () => {
      net.send({ t: 'hello', host: true, name: 'Host' });
      const s = saved();
      post({ token: s.token ?? '', locked: !!s.locked }).catch(() => {});
      publish();
    },
    onmessage: (m) => {
      if (m.t === 'cmd') commands[m.op]?.(m.v);
      else if (m.t === 'changed') note(describe(m));
      else if (m.t === 'notes' || m.t === 'noteerr') onNotes?.(m);
      else if (m.t === 'roster' || m.t === 'welcome') { Object.assign(status, m); player.setHeld?.(m.speaker != null); render(); }
    },
    onclose: () => { status = { ...status, clients: [], speaker: null }; player.setHeld?.(false); render(); },
  });
  setInterval(() => net.send({ t: 'ping', c: 0 }), 4000); // the hub answers: silence means the link is dead

  async function post(body) {
    const r = await fetch('/api/band', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) { note('Could not save the Band settings'); return; }
    status = { ...status, ...(await r.json()) };
    render();
  }

  // ── panel ──
  let kind = 'app';
  $('band-tabs').onclick = (e) => { const k = e.target.dataset?.kind; if (k) { kind = k; render(); } };
  function render() {
    const dlg = $('band');
    const qr = kind === 'app' ? status.appQr : status.qr;
    $('band-qr').innerHTML = qr || '';
    $('band-qr').hidden = !qr;
    $('band-hint').textContent = kind === 'app'
      ? 'Scan with the phone camera and the Prismix app opens by itself, or use Scan in the app.'
      : 'Phones on the same Wi-Fi scan this code. The first time, the browser warns about the certificate: choose Advanced, then continue.';
    document.querySelectorAll('#band-tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.kind === kind)));
    $('band-url').textContent = status.url?.replace(/\?ip=.*$/, '') || 'No LAN address found. Connect this computer to the band Wi-Fi (sharing is off with --no-share).';
    $('band-lock').checked = !!status.locked;
    if (document.activeElement !== $('band-name')) $('band-name').value = status.name || '';
    const list = $('band-clients');
    list.replaceChildren(...status.clients.map((c) => {
      const li = document.createElement('li');
      const who = document.createElement('span');
      who.textContent = `${c.name} · ${c.role}${c.synced ? '' : ' · solo'}${c.rtt != null ? ` · ${Math.round(c.rtt)} ms` : ''}`;
      const kick = document.createElement('button');
      kick.className = 'btn small ghost';
      kick.textContent = 'Kick';
      kick.onclick = () => net.send({ t: 'admin', kick: c.id });
      li.append(who, kick);
      return li;
    }));
    if (!status.clients.length) list.innerHTML = '<li class="muted">Nobody connected yet</li>';
    const out = $('band-out');
    out.replaceChildren(new Option('This computer', ''), ...status.clients.map((c) => new Option(c.name, String(c.id))));
    out.value = status.speaker != null ? String(status.speaker) : '';
    const sp = status.clients.find((c) => c.id === status.speaker);
    $('band-hint').textContent += sp ? ` ${sp.name} is playing the sound, so this computer is silent.` : '';
    $('band-btn').dataset.count = status.clients.length || '';
    $('band-btn').title = `Band: ${status.clients.length} connected`;
    if (!dlg.open) return;
  }

  $('band-btn').addEventListener('click', () => { render(); $('band').showModal(); });
  $('band-close').addEventListener('click', () => $('band').close());
  $('band-out').addEventListener('change', (e) => net.send({ t: 'admin', speaker: e.target.value === '' ? null : +e.target.value }));
  $('band-lock').addEventListener('change', (e) => {
    localStorage.setItem(LS, JSON.stringify({ ...saved(), locked: e.target.checked }));
    net.send({ t: 'admin', locked: e.target.checked });
  });
  $('band-pin').addEventListener('change', (e) => {
    const token = e.target.value.trim();
    localStorage.setItem(LS, JSON.stringify({ ...saved(), token }));
    post({ token });
  });
  $('band-name').addEventListener('change', (e) => post({ name: e.target.value.trim() }));
  $('band-pin').value = saved().token || '';

  ['state', 'seek', 'tune', 'loop', 'mix'].forEach((t) => player.addEventListener(t, publish));
  timer = setInterval(() => { if (player.playing) publish(); }, 2000);
  return { publish, send: (m) => net.send(m), stop() { clearInterval(timer); net.stop(); } };
}
