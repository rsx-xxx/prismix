// Puts the page in the system's media controls: lock screen, notification shade (MIUI included), media keys,
// macOS Now Playing and Windows' media overlay. Stems play through Web Audio, which browsers do not count as
// media, so a silent looping <audio> stands in for them. Inside the Android app the native shell draws the
// notification instead (a foreground service keeps it and the audio alive with the screen off).

const droid = window.Android;
const native = () => window.pywebview?.api?.now_playing ? window.pywebview.api : null; // the macOS desktop shell

/** One second of 16-bit audio whose only signal is a 1-LSB, 50 Hz square wave (about -90 dB, inaudible). Firefox/Zen and WebKit
 *  give media controls only to audio that actually produces samples, so exact silence would never reach Now Playing. */
function silence() {
  const rate = 8000, n = rate * 2;
  const b = new DataView(new ArrayBuffer(44 + n));
  const tag = (o, s) => [...s].forEach((c, i) => b.setUint8(o + i, c.charCodeAt(0)));
  tag(0, 'RIFF'); b.setUint32(4, 36 + n, true); tag(8, 'WAVEfmt ');
  b.setUint32(16, 16, true); b.setUint16(20, 1, true); b.setUint16(22, 1, true);
  b.setUint32(24, rate, true); b.setUint32(28, rate * 2, true); b.setUint16(32, 2, true); b.setUint16(34, 16, true);
  tag(36, 'data'); b.setUint32(40, n, true);
  for (let i = 0; i < rate; i++) b.setInt16(44 + i * 2, Math.floor(i / 80) % 2 ? 1 : -1, true); // 80-sample halves = 50 Hz
  return URL.createObjectURL(new Blob([b], { type: 'audio/wav' }));
}

/**
 * `ctl` = { play(), pause(), seek(seconds), step(seconds) }. Returns `update({title, artist, art, playing, pos, dur, rate})`
 * (cheap to call every frame) and `clear()`.
 */
export function createMedia(ctl) {
  const ms = 'mediaSession' in navigator ? navigator.mediaSession : null;
  let anchor = null;
  let sent = null;
  let at = 0; // performance.now() of the last position push

  const act = (op, arg) => {
    if (op === 'play') ctl.play();
    else if (op === 'pause') ctl.pause();
    else if (op === 'seek') ctl.seek(arg);
    else if (op === 'step') ctl.step(arg);
    else if (op === 'toggle') (sent?.playing ? ctl.pause() : ctl.play());
  };
  window.__media = (op, arg) => act(op, +arg || 0); // called by the Android shell

  if (ms && !droid?.media) {
    const set = (name, fn) => { try { ms.setActionHandler(name, fn); } catch { /* not supported here */ } };
    set('play', () => act('play'));
    set('pause', () => act('pause'));
    set('seekto', (d) => act('seek', d.seekTime));
    set('seekbackward', (d) => act('step', -(d.seekOffset || 10)));
    set('seekforward', (d) => act('step', d.seekOffset || 10));
    set('stop', () => act('pause'));
  }

  // Hardware media keys (F8 on a Mac) reach a plain browser tab as key events when the system does not route them to Now Playing.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'MediaPlayPause') act('toggle');
    else if (e.key === 'MediaTrackNext') act('step', 10);
    else if (e.key === 'MediaTrackPrevious') act('step', -10);
    else return;
    e.preventDefault();
  });

  let lock = null;
  let wantAwake = false;
  async function awake(on) {
    wantAwake = on;
    if (!('wakeLock' in navigator)) return;
    if (on && !lock) {
      try {
        lock = await navigator.wakeLock.request('screen');
        lock.addEventListener('release', () => { lock = null; });
      } catch { /* denied or hidden: retried when the page is shown */ }
    } else if (!on && lock) { lock.release().catch(() => {}); lock = null; }
  }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && wantAwake) awake(true); });

  function hold(playing) {
    awake(playing);
    if (droid?.media) return;
    anchor ??= Object.assign(new Audio(silence()), { loop: true });
    if (playing && anchor.paused) anchor.play().catch(() => { /* needs a tap first; the next one will do */ });
    else if (!playing && !anchor.paused) anchor.pause();
  }

  function update(m) {
    if (!m || !m.title) return;
    const now = performance.now();
    const same = sent && sent.title === m.title && sent.playing === m.playing && sent.rate === m.rate;
    // A steady clock is extrapolated by the system; only tell it again after a seek, a pause or a tempo change.
    const drift = sent ? Math.abs(m.pos - (sent.pos + (sent.playing ? ((now - at) / 1000) * sent.rate : 0))) : Infinity;
    if (same && drift < 1.5 && now - at < 8000) return; // the odd repeat revives a notification the system dropped
    const fresh = !sent || sent.title !== m.title || sent.art !== m.art;
    sent = { title: m.title, art: m.art, playing: m.playing, rate: m.rate, pos: m.pos };
    at = now;
    hold(m.playing);
    if (droid?.media) {
      const ok = droid.media(m.title, m.artist || '', m.playing, Math.round(m.pos * 1000), Math.round(m.dur * 1000), m.rate, new URL(m.art || '', location.href).href);
      if (ok === false) sent = null; // the shell could not start its service: try again on the next tick
      return;
    }
    native()?.now_playing(m.title, m.artist || '', m.playing, m.pos, m.dur, m.rate || 1, m.art ? new URL(m.art, location.href).href : '');
    if (!ms) return;
    if (fresh) {
      ms.metadata = new MediaMetadata({
        title: m.title,
        artist: m.artist || '',
        album: 'Prismix',
        artwork: m.art ? [{ src: new URL(m.art, location.href).href, sizes: '480x360', type: 'image/jpeg' }] : [],
      });
    }
    ms.playbackState = m.playing ? 'playing' : 'paused';
    try {
      ms.setPositionState({ duration: Math.max(m.dur, 1), playbackRate: m.rate || 1, position: Math.min(Math.max(m.pos, 0), m.dur) });
    } catch { /* a position outside the duration */ }
  }

  function clear() {
    sent = null;
    hold(false);
    if (droid?.mediaStop) droid.mediaStop();
    native()?.now_playing_stop();
    if (ms) { ms.metadata = null; ms.playbackState = 'none'; }
  }

  return { update, clear };
}
