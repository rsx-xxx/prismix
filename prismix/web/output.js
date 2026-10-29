// "Play on" picker, in the spirit of Spotify Connect and Cast: this computer (with its audio outputs) or a phone that joined the Band.
const OK = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="m5 12 5 5 9-10" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const LS = 'prismix.sink';

export function createOutput({ player, band, $ }) {
  const pop = $('out-pop');
  const btn = $('out-btn');
  let outputs = [];

  const load = async () => {
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      outputs = all.filter((d) => d.kind === 'audiooutput' && d.deviceId !== 'default' && d.deviceId !== 'communications');
    } catch { outputs = []; }
  };

  function row(label, sub, active, onpick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.role = 'menuitemradio';
    b.setAttribute('aria-checked', String(active));
    b.className = `out-row${active ? ' on' : ''}`;
    const t = document.createElement('span');
    t.className = 'out-name';
    t.textContent = label;
    const s = document.createElement('small');
    s.textContent = sub;
    b.append(t, s);
    if (active) b.insertAdjacentHTML('beforeend', OK);
    b.onclick = () => { onpick(); setTimeout(paint, 60); close(); };
    return b;
  }

  function paint() {
    const st = band.status();
    const phone = st.speaker;
    const kids = [];
    const head = document.createElement('h3');
    head.textContent = 'Play on';
    kids.push(head);
    const here = phone == null;
    kids.push(row('This computer', player.sinkId ? 'Chosen output' : 'System output', here && !player.sinkId, () => { player.setSink(''); localStorage.removeItem(LS); band.setSpeaker(null); }));
    if (outputs.length && 'setSinkId' in AudioContext.prototype) {
      for (const d of outputs) {
        kids.push(row(d.label || 'Audio output', 'Output of this computer', here && player.sinkId === d.deviceId, async () => {
          band.setSpeaker(null);
          if (await player.setSink(d.deviceId)) localStorage.setItem(LS, d.deviceId);
        }));
      }
    }
    const devices = st.clients.filter((c) => !c.host);
    for (const c of devices) kids.push(row(c.name, `${c.role}${c.rtt != null ? ` · ${Math.round(c.rtt)} ms` : ''}`, phone === c.id, () => band.setSpeaker(c.id)));
    const note = document.createElement('p');
    note.className = 'out-note muted';
    note.textContent = devices.length
      ? 'A phone plays the sound while this computer stays silent.'
      : 'Phones and other computers on this Wi-Fi appear here after they scan the Band code. Speakers paired through the system (Bluetooth, AirPlay) show up as outputs.';
    kids.push(note);
    pop.replaceChildren(...kids);
    btn.classList.toggle('on', !here || !!player.sinkId);
  }

  const place = () => {
    const r = btn.getBoundingClientRect();
    pop.style.top = `${r.bottom + 8}px`;
    pop.style.right = `${Math.max(8, innerWidth - r.right)}px`;
  };
  async function open() {
    await load();
    paint();
    place();
    pop.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
  }
  function close() { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); }

  btn.addEventListener('click', (e) => { e.stopPropagation(); if (pop.hidden) open(); else close(); });
  document.addEventListener('click', (e) => { if (!pop.hidden && !pop.contains(e.target)) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !pop.hidden) { close(); btn.focus(); } });
  band.watch(() => { if (!pop.hidden) paint(); else btn.classList.toggle('on', band.status().speaker != null || !!player.sinkId); });
  navigator.mediaDevices?.addEventListener?.('devicechange', () => { if (!pop.hidden) load().then(paint); });

  const saved = localStorage.getItem(LS);
  if (saved) player.setSink(saved);
  return { open, close };
}
