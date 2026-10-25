// Band mode, host side: publishes the player's state to the hub so phones can follow it,
// applies the commands phones send, and drives the Band panel (QR, roster, lock, PIN).
const LS = 'prismix.band';

export function createBand({ player, current, open, $ }) {
  let ws;
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
      cents: player.cents,
      loop: q ? { a: q.a, b: q.b } : null,
      muted: player.stems.filter((s) => s.muted).map((s) => s.name),
      solo: player.stems.filter((s) => s.solo).map((s) => s.name),
    };
  };

  function publish() {
    if (ws?.readyState === 1) ws.send(JSON.stringify(snapshot()));
  }

  const commands = {
    play: () => player.play(),
    pause: () => player.pause(),
    seek: (v) => player.seek(+v),
    tempo: (v) => player.setTempo(+v),
    pitch: (v) => player.setSemitones(+v),
    cents: (v) => player.setCents(+v),
    loop: (v) => (v ? player.setLoop(+v.a, +v.b) : player.setLoop(null, null)),
    mute: (v) => player._stem(v?.name) && player.setMuted(v.name, !!v.on),
    solo: (v) => player._stem(v?.name) && player.setSolo(v.name, !!v.on),
    load: (v) => { if (/^[\w-]{1,64}$/.test(v?.id) && /^[\w.-]{1,64}$/.test(v?.model)) location.hash = `#/v/${v.id}/${v.model}`; },
  };

  function connect() {
    ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`);
    ws.onopen = () => {
      ws.send(JSON.stringify({ t: 'hello', host: true, name: 'Host' }));
      const s = saved();
      post({ token: s.token ?? '', locked: !!s.locked });
      publish();
    };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.t === 'cmd') commands[m.op]?.(m.v);
      else if (m.t === 'roster' || m.t === 'welcome') { Object.assign(status, m); render(); }
    };
    ws.onclose = () => setTimeout(connect, 1500);
  }

  async function post(body) {
    const r = await fetch('/api/band', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    if (r.ok) { status = { ...status, ...(await r.json()) }; render(); }
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
      ? 'Scan with the phone camera and the Prismix Band app opens by itself, or use Scan in the app.'
      : 'Phones on the same Wi-Fi scan this code. The first time, the browser warns about the certificate: choose Advanced, then continue.';
    document.querySelectorAll('#band-tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.kind === kind)));
    $('band-url').textContent = status.url?.replace(/\?ip=.*$/, '') || 'No LAN address found. Connect this computer to the band Wi-Fi.';
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
      kick.onclick = () => ws.send(JSON.stringify({ t: 'admin', kick: c.id }));
      li.append(who, kick);
      return li;
    }));
    if (!status.clients.length) list.innerHTML = '<li class="muted">Nobody connected yet</li>';
    $('band-btn').dataset.count = status.clients.length || '';
    $('band-btn').title = `Band: ${status.clients.length} connected`;
    if (!dlg.open) return;
  }

  $('band-btn').addEventListener('click', () => { render(); $('band').showModal(); });
  $('band-close').addEventListener('click', () => $('band').close());
  $('band-lock').addEventListener('change', (e) => {
    localStorage.setItem(LS, JSON.stringify({ ...saved(), locked: e.target.checked }));
    ws.send(JSON.stringify({ t: 'admin', locked: e.target.checked }));
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
  connect();
  return { publish, stop() { clearInterval(timer); ws?.close(); } };
}
