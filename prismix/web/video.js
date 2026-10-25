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

export function createVideo({ player, notice }) {
  const panel = $('video');
  const pref = (k, d) => { try { return localStorage.getItem(`stems:video-${k}`) ?? d; } catch { return d; } };
  const store = (k, v) => { try { localStorage.setItem(`stems:video-${k}`, v); } catch { /* ignore */ } };
  let yt = null, videoId = null, opening = false, timer = 0, ready = false;
  let mode = pref('mode', 'float');

  function setMode(m) {
    mode = m;
    store('mode', m);
    panel.classList.toggle('theater', m === 'theater');
    panel.classList.toggle('float', m !== 'theater');
    $('video-mode').textContent = m === 'theater' ? 'Float' : 'Theater';
    if (m === 'theater') { panel.style.left = panel.style.top = ''; }
  }

  async function open() {
    if (!videoId || opening) return;
    opening = true;
    panel.hidden = false;
    $('video-btn').setAttribute('aria-pressed', 'true');
    store('open', '1');
    setMode(mode);
    try {
      const YT = await youtubeApi();
      if (!yt) {
        await new Promise((ok) => {
          yt = new YT.Player('yt', {
            videoId, host: 'https://www.youtube-nocookie.com',
            playerVars: { controls: 0, disablekb: 1, rel: 0, playsinline: 1, modestbranding: 1, iv_load_policy: 3, fs: 0, origin: location.origin },
            events: { onReady: () => { ready = true; ok(); }, onError: () => notice('This video cannot be embedded. Open it on YouTube to watch.') },
          });
        });
      } else if (yt.getVideoData?.().video_id !== videoId) {
        yt.cueVideoById(videoId, player.position);
      }
      yt.mute();
      start();
    } catch (err) {
      notice(err.message);
      close();
    } finally {
      opening = false;
    }
  }

  function close() {
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
  $('video-mode').addEventListener('click', () => setMode(mode === 'theater' ? 'float' : 'theater'));

  // drag the floating panel by its header
  $('video-head').addEventListener('pointerdown', (e) => {
    if (mode === 'theater' || e.target.closest('button')) return;
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
      videoId = id;
      if (!same && ready) { yt.cueVideoById(id, 0); yt.pauseVideo(); }
      $('video-btn').hidden = false;
      if (pref('open', '0') === '1') open(); else panel.hidden = true;
    },
    hide() {
      panel.hidden = true;
      stop();
      if (ready) yt.pauseVideo();
      $('video-btn').hidden = true;
    },
  };
}
