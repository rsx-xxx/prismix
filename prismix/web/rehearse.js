// Rehearsal follow, wiring: the drum kit's MIDI hits (read by the app server, see midi.py) steer the stems' speed through
// follow.js. With the drum stem muted and this on, the band keeps playing against a drummer who rushes or drags, and four
// sticks clicked while the stems are stopped start them at his tempo, on his downbeat.
import { beatGrid, Follower, countIn, noteWeight } from './follow.js';
import { Bleed, OnsetPicker } from './mic-beat.js';

const KEY = 'rehearse';

/**
 * The microphone as a drummer: band loudness from a worklet, the stems' own loudness (tapped from the player) taken out of it,
 * and the hits picked from what remains. `onHit(perf, weight)` gets the hit's performance.now() time.
 */
async function openMic({ player, onHit }) {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
  const ctx = new AudioContext({ latencyHint: 'interactive' });
  const stop = () => { stream.getTracks().forEach((t) => t.stop()); ctx.close().catch(() => {}); tap.stop(); };
  const tap = { stop() {}, ctx: null };
  try {
    await ctx.audioWorklet.addModule(new URL('./mic-worklet.js', import.meta.url));
    const meter = new AudioWorkletNode(ctx, 'band-meter', { numberOfOutputs: 0 });
    ctx.createMediaStreamSource(stream).connect(meter);
    const inLatency = ((stream.getAudioTracks()[0]?.getSettings?.().latency || 0) + (ctx.baseLatency || 0)) * 1000;
    const bleed = new Bleed(), picker = new OnsetPicker();
    const perfOf = (c, t) => {
      const ts = c.getOutputTimestamp?.();
      return ts && ts.performanceTime > 0 ? ts.performanceTime + (t - ts.contextTime) * 1000 : performance.now() + (t - c.currentTime) * 1000;
    };
    // the stems' loudness, measured where they leave the player; re-tapped when a new song makes a new audio context
    const follow = async () => {
      const pc = player.ctx;
      if (!pc || tap.ctx === pc) return;
      tap.ctx = pc;
      try {
        await pc.audioWorklet.addModule(new URL('./mic-worklet.js', import.meta.url));
        const node = new AudioWorkletNode(pc, 'band-meter', { numberOfOutputs: 0 });
        player.master.connect(node);
        node.port.onmessage = (e) => bleed.push(perfOf(pc, e.data.t), e.data.b);
        tap.stop = () => { try { player.master.disconnect(node); } catch { /* the context is gone */ } };
      } catch { tap.ctx = null; }
    };
    meter.port.onmessage = (e) => {
      follow();
      const t = perfOf(ctx, e.data.t) - inLatency;
      const hit = picker.feed(t, bleed.residual(t, e.data.b));
      if (hit) onHit(hit.t, hit.weight);
    };
    await ctx.resume();
  } catch (err) {
    stop();
    throw err;
  }
  return { stop };
}

export function createRehearse({ player, onChange = () => {} }) {
  const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } })();
  const store = () => localStorage.setItem(KEY, JSON.stringify({ port: saved.port, trim: saved.trim, src: saved.src }));
  let trim = Number(saved.trim) || 0; // seconds the stems sound ahead of the drummer's felt beat on this kit and speakers

  let on = false;
  let ws = null;
  let retry = 0;
  let beats = [];
  let grid = null;
  let follower = null;
  let ports = [];
  let open = '';
  let samples = []; // clock-sync {rtt, offset} pairs; the one with the shortest round trip is the most trustworthy
  let sync = 0;
  let counts = []; // graph-time of recent hits while waiting for a count-in
  let status = '';
  let src = ['auto', 'mic', 'midi'].includes(saved.src) ? saved.src : 'auto'; // how the drummer is heard
  let mic = null; // the open microphone, while it is wanted
  let micError = '';
  let starting = false; // the next 'playing' state is our own count-in start, which already set the follower up

  const setStatus = (s) => { if (s !== status) { status = s; onChange(); } };

  // ── clock: server monotonic ms -> this page's performance.now() ms ──
  const offset = () => (samples.length ? samples.reduce((a, b) => (b.rtt < a.rtt ? b : a)).offset : null);
  function ping() { if (ws?.readyState === 1) ws.send(JSON.stringify({ type: 'ping', c: performance.now() })); }
  function startSync() {
    samples = [];
    let n = 0;
    clearInterval(sync);
    sync = setInterval(() => {
      ping();
      if (++n === 10) { clearInterval(sync); sync = setInterval(ping, 20_000); } // quick burst at connect, then a slow refresh
    }, 60);
  }

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${proto}://${location.host}/ws/midi`);
    ws.onopen = () => { retry = 0; startSync(); };
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === 'pong') {
        const now = performance.now();
        const rtt = now - m.c;
        samples.push({ rtt, offset: m.s - (m.c + rtt / 2) });
        if (samples.length > 16) samples.shift();
      } else if (m.type === 'ports') {
        ports = m.ports;
        open = m.open;
        if (!open && ports.length) {
          const want = ports.includes(saved.port) ? saved.port : ports.length === 1 ? ports[0] : '';
          if (want) use(want);
        }
        status = ''; // recomputed
        syncMic();
        onChange();
      } else if (m.type === 'hit') hit(m);
    };
    ws.onclose = () => {
      clearInterval(sync);
      ws = null;
      ports = [];
      open = '';
      if (on) setTimeout(connect, Math.min(5000, 500 * 2 ** retry++));
      onChange();
    };
  }

  function use(port) {
    saved.port = port;
    store();
    if (ws?.readyState === 1) ws.send(JSON.stringify({ type: 'use', port }));
  }

  // ── hits ──
  function hit(m) {
    const o = offset();
    if (o == null || src === 'mic' || micWanted()) return; // a MIDI kit is plugged in but the microphone was asked for
    onHit(m.s - o, noteWeight(m.n, m.v));
  }

  /** A drummer's hit, from the kit or the microphone: `perf` is its performance.now() time, `w` how much it says about the beat. */
  function onHit(perf, w) {
    if (!w || !on || !player.ctx) return;
    if (!grid) return setStatus('no beat grid for this song');
    if (player.playing) return steer(perf, w);
    // stopped: a count-in starts the stems
    const t = player.heardAt(perf);
    counts = counts.filter((x) => t - x < 3);
    counts.push(t);
    const c = countIn(counts);
    if (!c) return setStatus(`count in… ${counts.length}`);
    counts = [];
    start(c);
  }

  function steer(perf, w) {
    if (!follower) return;
    const pos = player.positionHeardAt(perf) - trim * player.tempo;
    const r = follower.hit(pos, w);
    if (r.accepted) player.follow(r.rate);
    const ms = Math.round(follower.err * grid.beatLength(pos) * 1000);
    setStatus(follower.locked ? `in time · ${ms > 0 ? '+' : ''}${ms} ms` : 'finding his tempo…');
  }

  /** Four clicks heard: set the stems to his tempo and start them on the beat after the last click. */
  async function start({ period, last }) {
    const from = player.position;
    let k = beats.findIndex((b) => b >= from - 0.05); // the first beat at or after where the stems are cued
    if (k < 0) k = 0;
    const len = grid.beatLength(beats[k]);
    let rate = len / period;
    if (rate > 1.4) rate *= 0.5; // counted in eighths
    else if (rate < 0.6) rate *= 2; // counted in half notes
    if (rate < 0.6 || rate > 1.4) return setStatus('count-in tempo out of range');
    player.seek(beats[k]);
    player.follow(rate);
    follower = new Follower(grid);
    follower.reset(rate);
    setStatus('starting…');
    starting = true;
    await player.play({ at: last + period + trim }); // ahead by `trim` means the stems must start that much later
  }

  player.addEventListener('state', () => {
    counts = [];
    if (!on) return;
    if (player.playing) {
      if (starting) starting = false;
      else if (grid) { follower = new Follower(grid); follower.reset(player.tempo); } // started by hand: follow from its current speed
    } else setStatus(open ? 'waiting for a count-in' : '');
  });

  // ── public ──
  // the microphone is used when asked for, or in Auto when no MIDI kit is plugged in
  const micWanted = () => on && (src === 'mic' || (src === 'auto' && !!ws && ws.readyState === 1 && !open && !ports.length));

  async function syncMic() {
    const want = micWanted();
    if (want && !mic) {
      micError = '';
      try {
        const m = await openMic({ player, onHit });
        if (!micWanted()) { m.stop(); return; }
        mic = m;
      } catch (e) {
        micError = e?.name === 'NotAllowedError' ? 'microphone blocked: allow it for this page' : 'no microphone available';
      }
      onChange();
    } else if (!want && mic) {
      mic.stop();
      mic = null;
      onChange();
    }
  }

  async function setOn(v) {
    on = !!v;
    if (on) {
      follower = null;
      await player.setFollowing(true);
      if (src !== 'mic' && !ws) connect();
      status = '';
      syncMic();
    } else {
      await player.setFollowing(false);
      ws?.close();
      ws = null;
      follower = null;
      syncMic();
    }
    onChange();
  }

  function setSource(v) {
    if (!['auto', 'mic', 'midi'].includes(v)) return;
    src = saved.src = v;
    store();
    status = '';
    if (on) {
      if (src === 'mic') { ws?.close(); ws = null; } else if (!ws) connect();
      syncMic();
    }
    onChange();
  }

  /** The song changed: `list` is its analysed beat times in seconds (empty if not analysed). */
  function setBeats(list) {
    beats = list || [];
    grid = beats.length > 8 ? beatGrid(beats) : null;
    follower = null;
    counts = [];
    onChange();
  }

  /** Take the stems' present average lead as the kit's and speakers' latency, so "in time" means in time to the ear. */
  function trimNow() {
    const lead = follower?.lead(player.position);
    if (lead == null) return false;
    trim += lead;
    saved.trim = trim;
    store();
    follower.reset(follower.rate);
    return true;
  }

  return {
    get on() { return on; },
    get ports() { return ports; },
    get open() { return open; },
    get trim() { return trim; },
    get connected() { return ws?.readyState === 1; },
    get src() { return src; },
    get micOn() { return !!mic; },
    get canTrim() { return !!follower && follower.locked; },
    get status() {
      if (!on) return '';
      if (micError && micWanted()) return micError;
      if (!mic) {
        if (src === 'mic') return 'opening the microphone…';
        if (!this.connected) return 'connecting…';
        if (!open) return ports.length ? 'choose the drum kit' : 'looking for a MIDI kit or a microphone…';
      }
      return status || (player.playing ? 'listening…' : 'waiting for a count-in');
    },
    setOn, setSource, setBeats, use, trimNow,
  };
}
