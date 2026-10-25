// The original video, muted, kept in step with the edited audio (tempo, loops, seeks) via the YouTube IFrame API.

const $ = (id) => document.getElementById(id);

let apiReady;
function youtubeApi() {
  return (apiReady ??= new Promise((ok, fail) => {
    if (window.YT?.Player) { ok(window.YT); return; }
    window.onYouTubeIframeAPIReady = () => ok(window.YT);
    const s = Object.assign(document.createElement('script'), { src: 'https://www.youtube.com/iframe_api', onerror: () => { apiReady = null; fail(new Error('Could not reach YouTube. The video needs an internet connection.')); } });
    document.head.append(s);
  }));
}

const NEXT = { float: 'Dock', side: 'Theater', theater: 'Float' };
const CYCLE = { float: 'side', side: 'theater', theater: 'float' };

export function createVideo({ player, notice }) {
  const panel = $('video');
  const pref = (k, d) => { try { return localStorage.getItem(`stems:video-${k}`) ?? d; } catch { return d; } };
  const store = (k, v) => { try { localStorage.setItem(`stems:video-${k}`, v); } catch { /* ignore */ } };
  let yt = null, videoId = null, opening = false, timer = 0, ready = false;
  let sound = false, saved = null, own = false; // the video's own audio replaces the stems while on
  let mode = pref('mode', innerWidth >= 1100 ? 'side' : 'float');

  function setMode(m) {
    mode = m;
    store('mode', m);
    ['float', 'side', 'theater'].forEach((k) => panel.classList.toggle(k, k === m));
    $('video-mode').textContent = NEXT[m];
    if (m === 'side') $('panes').prepend(panel);
    else if (panel.parentNode !== $('studio')) $('studio').insertBefore(panel, $('stems'));
    if (m !== 'float') { panel.style.left = panel.style.top = panel.style.right = panel.style.bottom = ''; }
  }

  const frame = document.querySelector('.video-frame');
  const dockEl = $('dock');
  new ResizeObserver(() => document.documentElement.style.setProperty('--dock-h', `${dockEl.offsetHeight || 112}px`)).observe(dockEl);
  const veil = (state, text = '') => {
    let v = frame.querySelector('.video-veil');
    if (!state) { v?.remove(); return; }
    if (!v) { v = document.createElement('div'); v.className = 'video-veil'; frame.append(v); }
    v.dataset.state = state;
    v.style.backgroundImage = `url(https://i.ytimg.com/vi/${videoId}/mqdefault.jpg)`;
    v.replaceChildren();
    const msg = Object.assign(document.createElement('span'), { textContent: text });
    v.append(msg);
    if (state === 'error') {
      v.append(Object.assign(document.createElement('a'), { href: `https://www.youtube.com/watch?v=${videoId}`, target: '_blank', rel: 'noopener', textContent: 'Open on YouTube', className: 'btn small' }));
    }
  };

  /** Create the player; resolves true once it is ready, false on an embed error or after a timeout. */
  function create(YT, host) {
    return new Promise((done) => {
      let over = false;
      const finish = (ok) => { if (!over) { over = true; clearTimeout(t); done(ok); } };
      const t = setTimeout(() => finish(false), 12000);
      const vars = { controls: 0, disablekb: 1, rel: 0, playsinline: 1, modestbranding: 1, iv_load_policy: 3, fs: 0 };
      if (location.protocol.startsWith('http')) vars.origin = location.origin;
      yt = new YT.Player('yt', {
        videoId, host, playerVars: vars,
        events: { onReady: () => { ready = true; finish(true); }, onError: () => finish(false) },
      });
    });
  }

  function drop() {
    try { yt?.destroy(); } catch { /* ignore */ }
    yt = null; ready = false;
    if (!document.getElementById('yt')) frame.append(Object.assign(document.createElement('div'), { id: 'yt' }));
  }

  async function open() {
    if (!videoId || opening) return;
    opening = true;
    panel.hidden = false;
    $('video-btn').setAttribute('aria-pressed', 'true');
    store('open', '1');
    setMode(mode);
    try {
      if (!yt) {
        veil('loading', 'Loading video…');
        const YT = await youtubeApi();
        let ok = await create(YT, 'https://www.youtube.com');
        if (!ok) { drop(); veil('loading', 'Still trying…'); ok = await create(YT, 'https://www.youtube-nocookie.com'); }
        if (!ok) { drop(); veil('error', 'The video could not be loaded here. It may not allow embedding, or YouTube is unreachable.'); return; }
        veil(null);
      } else if (yt.getVideoData?.().video_id !== videoId) {
        yt.cueVideoById(videoId, player.position);
      }
      sound && ready ? yt.unMute() : yt.mute();
      start();
    } catch (err) {
      drop();
      veil('error', err.message);
    } finally {
      opening = false;
    }
  }

  /** Video sound on = stems silent; off = the mix you had comes back. Turning a stem on also turns the video off. */
  function setSound(on, restore = true) {
    if (on === sound || (on && (!ready || panel.hidden))) return;
    sound = on;
    $('video-sound').setAttribute('aria-pressed', String(on));
    $('video-sound').textContent = on ? 'Sound on' : 'Sound off';
    $('video-sound').dataset.ico = on ? 'speaker' : 'mute';
    if (ready) { if (on) { yt.unMute(); yt.setVolume(100); } else yt.mute(); }
    own = true;
    if (on) { saved = player.mixState(); player.muteAll(); }
    else if (restore && saved) { player.setMixState(saved); saved = null; }
    own = false;
  }
  player.addEventListener('mix', () => { if (!own && sound && player.stems.some((s) => player.isAudible(s))) setSound(false, false); });
  $('video-sound').addEventListener('click', () => setSound(!sound));

  function close() {
    setSound(false);
    panel.hidden = true;
    $('video-btn').setAttribute('aria-pressed', 'false');
    store('open', '0');
    stop();
    if (ready) yt.pauseVideo();
  }

  const rates = () => (ready ? yt.getAvailablePlaybackRates?.() : null) || [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];

  /** Snap video to the audio clock now (after a seek, play or tempo change). */
  function snap() {
    if (!ready || panel.hidden) return;
    yt.seekTo(player.position, true);
    steer(0);
    player.playing ? yt.playVideo() : yt.pauseVideo();
  }

  // YouTube only offers fixed speeds, so alternate between the two closest ones to follow any tempo.
  function steer(drift) {
    const r = rates(), want = player.tempo;
    const lo = Math.max(...r.filter((x) => x <= want), r[0]);
    const hi = Math.min(...r.filter((x) => x >= want), r[r.length - 1]);
    let pick = Math.abs(want - lo) < Math.abs(hi - want) ? lo : hi;
    if (lo !== hi) pick = drift > 0.04 ? lo : drift < -0.04 ? hi : pick;
    if (yt.getPlaybackRate() !== pick) yt.setPlaybackRate(pick);
  }

  function tick() {
    if (!ready || panel.hidden || !player.playing) return;
    const drift = yt.getCurrentTime() - player.position;
    if (Math.abs(drift) > 0.5) yt.seekTo(player.position, true);
    else steer(drift);
    if (yt.getPlayerState() !== 1 && yt.getPlayerState() !== 3) yt.playVideo();
  }
  const start = () => { stop(); timer = setInterval(tick, 200); snap(); };
  const stop = () => clearInterval(timer);

  for (const t of ['state', 'seek']) player.addEventListener(t, snap);
  player.addEventListener('tune', () => { if (ready && !panel.hidden) steer(0); });
  player.addEventListener('loop', snap);

  $('video-btn').addEventListener('click', () => (panel.hidden ? open() : close()));
  $('video-close').addEventListener('click', close);
  $('video-mode').addEventListener('click', () => setMode(CYCLE[mode] ?? 'float'));

  // drag the floating panel by its header
  $('video-head').addEventListener('pointerdown', (e) => {
    if (mode !== 'float' || e.target.closest('button')) return;
    const r = panel.getBoundingClientRect();
    const dx = e.clientX - r.left, dy = e.clientY - r.top;
    const move = (ev) => {
      panel.style.left = `${Math.min(Math.max(0, ev.clientX - dx), innerWidth - 120)}px`;
      panel.style.top = `${Math.min(Math.max(0, ev.clientY - dy), innerHeight - 60)}px`;
      panel.style.right = panel.style.bottom = 'auto';
    };
    const up = () => { removeEventListener('pointermove', move); removeEventListener('pointerup', up); };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
  });

  return {
    /** Called when a track opens; reopens the panel if it was open last time. */
    setTrack(id) {
      const same = id === videoId;
      sound = false; saved = null; // new track, new mix: nothing to restore
      $('video-sound').setAttribute('aria-pressed', 'false');
      $('video-sound').textContent = 'Sound off';
      $('video-sound').dataset.ico = 'mute';
      if (ready) yt.mute();
      videoId = id;
      if (!same && ready) { yt.cueVideoById(id, 0); yt.pauseVideo(); }
      $('video-btn').hidden = false;
      if (pref('open', '0') === '1') open(); else panel.hidden = true;
    },
    hide() {
      setSound(false);
      panel.hidden = true;
      stop();
      if (ready) yt.pauseVideo();
      $('video-btn').hidden = true;
    },
  };
}
