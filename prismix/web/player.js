// Multi-stem player with two engines that share one clock. At normal tempo and key the stems play as plain
// AudioBufferSources: no DSP, sample-locked, cheap enough for any phone. Once tempo or key changes, one Signalsmith
// Stretch node per stem (WASM phase-vocoder with formant handling) takes over so the two move independently without
// the "robot" artefacts of naive resampling. Its copy of the audio is only made while it is needed.
import SignalsmithStretch from './vendor/SignalsmithStretch.mjs';

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export class StemPlayer extends EventTarget {
  constructor() {
    super();
    this.ctx = null;
    this.stems = []; // {name, node, gain, volume, muted, solo}
    this.duration = 0;
    this.tempo = 1;
    this.semitones = 0;
    this.chordLock = true; // locked: chords and tab follow the audio's key; unlocked: only chordKey moves them
    this.chordKey = 0; // unlocked: the key the chords and tab are read in (semitones from the original); the sound does not move
    this.cents = 0;
    this.formants = true;
    this.loop = null; // {a, b} seconds
    this.following = false; // rehearsal follow: the stretch engine stays on so the rate can track a live drummer
    this.playing = false;
    this._anchor = { pos: 0, t: 0 }; // input position `pos` is heard at context time `t`
    this._latency = 0.1;
    this._rate = 1; // speed of the engine that is actually sounding (tempo only once the stretch engine runs)
    this._engine = null; // 'direct' | 'stretch' while playing
    this._stretchOk = false;
    this._raf = 0;
  }

  /** Fetch + decode every stem. onProgress(fraction 0..1). */
  async load(meta, baseUrl, onProgress = () => {}) {
    this.dispose();
    this.meta = meta;
    this.duration = meta.duration;
    this.ctx = new AudioContext({ sampleRate: meta.sampleRate, latencyHint: 'playback' });
    const ctx = this.ctx;
    // A context the OS suspended mid-song (call, lock screen) is woken as soon as we are visible again.
    this._wakeUp = () => { if (this.playing && ctx.state !== 'running' && !document.hidden) ctx.resume().catch(() => {}); };
    ctx.addEventListener('statechange', this._wakeUp);
    document.addEventListener('visibilitychange', this._wakeUp);
    this.master = this.ctx.createGain(); // user volume
    this.out = this.ctx.createGain(); // silences this device when another one plays the room's sound
    this.out.gain.value = this.held ? 0 : 1;
    this.out.connect(this.ctx.destination);
    if (this.sinkId) ctx.setSinkId?.(this.sinkId).catch(() => { this.sinkId = ''; });
    this.master.connect(this.out);
    this.duck = this.ctx.createGain(); // short fades around seek/pause to avoid clicks
    this.duck.connect(this.master);

    // Per stem: bytes fetched / bytes expected (9/10 of its share), then decoding (the last 1/10). A stem whose size is
    // unknown (chunked or relayed responses carry no Content-Length) is guessed from its siblings, and never reads 100% before it is done.
    const n = meta.stems.length;
    const loaded = new Array(n).fill(0);
    const total = new Array(n).fill(0);
    const done = new Array(n).fill(false);
    const decoded = new Array(n).fill(false);
    const report = () => {
      const known = total.filter((t) => t > 0);
      const guess = known.length ? known.reduce((x, y) => x + y, 0) / known.length : Math.max(1, (meta.duration || 200) * 32000);
      let sum = 0;
      for (let i = 0; i < n; i++) {
        const frac = done[i] ? 1 : Math.min(loaded[i] / (total[i] || guess), 0.97);
        sum += decoded[i] ? 1 : frac * 0.9;
      }
      onProgress(sum / n);
    };
    report();

    this.stems = await Promise.all(meta.stems.map(async (s, i) => {
      const res = await fetch(`${baseUrl}/${s.file}`);
      if (!res.ok) throw new Error(`Could not load ${s.name} (${res.status})`);
      // Content-Length of an encoded response is the compressed size, not what the reader yields: only trust plain bodies.
      total[i] = res.headers.get('content-encoding') ? 0 : Number(res.headers.get('content-length')) || 0;
      const reader = res.body.getReader();
      const chunks = [];
      for (;;) {
        const { done: end, value } = await reader.read();
        if (end) break;
        chunks.push(value);
        loaded[i] += value.length;
        report();
      }
      done[i] = true;
      report();
      const buf = await this.ctx.decodeAudioData(await new Blob(chunks).arrayBuffer());
      decoded[i] = true;
      report();
      const gain = this.ctx.createGain();
      gain.connect(this.duck);
      return { name: s.name, buf, node: null, gain, src: null, volume: 1, muted: s.name === 'vocals', solo: false }; // vocals start silent: the band plays them
    }));

    this._latency = 0.1; // settled by the first stretch preparation
    this._anchor = { pos: 0, t: this.ctx.currentTime };
    this._tick();
  }

  /** Seconds between the audio graph and the speakers (large on Bluetooth), as far as the browser reports it. */
  get outLatency() {
    const c = this.ctx;
    return c ? Math.min(0.6, (c.outputLatency || 0) + (c.baseLatency || 0)) : 0;
  }

  get position() {
    if (!this.ctx) return 0;
    return this._positionAt(this.ctx.currentTime);
  }

  _positionAt(t) {
    const { pos, t: t0 } = this._anchor;
    if (!this.playing) return pos;
    let p = pos + Math.max(0, t - t0) * this._rate;
    if (this.loop && p >= this.loop.b) {
      const len = this.loop.b - this.loop.a;
      p = this.loop.a + ((p - this.loop.a) % len);
    }
    return clamp(p, 0, this.duration);
  }

  get pitch() { return this.semitones + this.cents / 100; }

  _each(fn) { this.stems.forEach((s) => s.node && fn(s.node, s)); }

  /** Run the stretch node of every stem that can be heard; muted ones stay idle, since each running node costs real DSP. */
  _scheduleStretch(params) {
    this._each((n, s) => {
      s.live = this.isAudible(s);
      if (s.live) n.schedule(params); else n.stop();
    });
  }

  _idleStretch(when) { this._each((n, s) => { n.stop(when); s.live = false; }); }

  /** A stem became audible or silent while the stretch engine is running: start or park its node to match. */
  _syncStretch() {
    if (!this.playing || this._engine !== 'stretch') return;
    const now = this.ctx.currentTime;
    const t = now + this._latency;
    this._each((n, s) => {
      const audible = this.isAudible(s);
      if (audible && !s.live) {
        n.schedule({ ...this._params(), output: t, input: this._positionAt(t), active: true });
        s.live = true;
      } else if (!audible && s.live) {
        n.stop(now + 0.06); // after the gain has faded out
        s.live = false;
      }
    });
  }

  _params() {
    return {
      rate: this.tempo,
      semitones: this.pitch,
      formantCompensation: this.formants,
      loopStart: this.loop ? this.loop.a : 0,
      loopEnd: this.loop ? this.loop.b : 0,
    };
  }

  get _needsStretch() { return this.tempo !== 1 || this.pitch !== 0 || this.following; }

  /** Hand each stem to the stretch engine (copies the audio one stem at a time to keep peak memory low). */
  _prepStretch() {
    if (this._stretchOk) return Promise.resolve();
    this._prep ??= (async () => {
      for (const s of this.stems) {
        if (!s.node) { // the WASM engine is only instantiated once tempo or key is first changed
          s.node = await SignalsmithStretch(this.ctx); // default options: 1 in, 2-ch out (0-input config yields silence)
          s.node.connect(s.gain);
          s.node.stop(); // idle until scheduled
        }
        const chans = [0, 1].map((c) => {
          const out = new Float32Array(s.buf.length);
          s.buf.copyFromChannel(out, Math.min(c, s.buf.numberOfChannels - 1));
          return out;
        });
        await s.node.addBuffers(chans, chans.map((c) => c.buffer));
      }
      this._latency = (await this.stems[0]?.node?.latency()) || 0.1;
      this._stretchOk = true;
    })().finally(() => { this._prep = null; });
    return this._prep;
  }

  _dropStretch() {
    if (!this._stretchOk) return;
    this._stretchOk = false;
    for (const s of this.stems) { try { s.node?.dropBuffers(); } catch { /* node gone */ } }
  }

  _startDirect(t, pos) {
    const ctx = this.ctx;
    const lp = this.loop;
    for (const s of this.stems) {
      const src = ctx.createBufferSource();
      src.buffer = s.buf;
      if (lp) { src.loop = true; src.loopStart = lp.a; src.loopEnd = lp.b; }
      src.connect(s.gain);
      src.start(t, lp && (pos < lp.a || pos >= lp.b) ? lp.a : pos);
      s.src = src;
    }
  }

  _stopDirect(when) {
    for (const s of this.stems) {
      if (!s.src) continue;
      try { s.src.stop(when); } catch { /* already ended */ }
      const old = s.src;
      old.onended = () => old.disconnect();
      s.src = null;
    }
  }

  /** Make the sounding engine match tempo/key/loop, without a gap: the old one ends exactly where the new one begins. */
  _retune() {
    if (!this.ctx) return;
    const stretch = this._needsStretch;
    if (stretch && !this._stretchOk) {
      this._prepStretch().then(() => this._retune(), () => { this.tempo = 1; this.semitones = 0; this.cents = 0; this._emit('tune'); });
      if (this.playing) return; // keep sounding on the direct engine until the stretch one is ready
    }
    if (!this.playing) { this._rate = stretch && this._stretchOk ? this.tempo : 1; return; }
    const t = this.ctx.currentTime + 0.02;
    const pos = this._positionAt(t);
    this._anchor = { pos, t };
    if (stretch) {
      this._stopDirect(t);
      this._rate = this.tempo;
      this._engine = 'stretch';
      const params = { ...this._params(), output: t, input: pos, active: true };
      this._scheduleStretch(params);
    } else {
      this._idleStretch(t);
      this._stopDirect(t);
      this._rate = 1;
      this._engine = 'direct';
      this._startDirect(t, pos);
      this._dropStretch();
    }
  }

  /** Resume a suspended context (autoplay policy, phone call, background); false if it stays blocked. */
  async _wake() {
    const ctx = this.ctx;
    if (ctx.state === 'running') return true;
    await Promise.race([ctx.resume().catch(() => {}), new Promise((r) => setTimeout(r, 800))]);
    return ctx.state === 'running';
  }

  /**
   * Start playing. `at` is the audio-graph time (as heardAt() gives it) at which the current position must sound: it lines
   * the music up with an outside beat such as the end of a count-in. If that moment is already too close, the start moves on to match.
   */
  async play({ at = null } = {}) {
    if (!this.ctx || this.playing) return;
    const ctx = this.ctx;
    if (this._needsStretch) await this._prepStretch().catch(() => {});
    if (!(await this._wake()) || ctx !== this.ctx || this.playing) return this._emit('blocked'); // never claim to play while silent
    if (this._anchor.pos >= this.duration - 0.05) this._anchor.pos = this.loop?.a ?? 0;
    const stretch = this._needsStretch && this._stretchOk;
    let t = ctx.currentTime + (stretch ? this._latency : 0.05);
    let pos = this._anchor.pos;
    if (at != null) {
      if (at >= t) t = at;
      else pos = clamp(pos + (t - at) * (stretch ? this.tempo : 1), 0, this.duration);
    }
    this.duck.gain.cancelScheduledValues(ctx.currentTime);
    this.duck.gain.setValueAtTime(0, ctx.currentTime);
    this.duck.gain.setTargetAtTime(1, t - 0.01, 0.01);
    this._anchor = { pos, t };
    this.playing = true;
    this._engine = stretch ? 'stretch' : 'direct';
    this._rate = stretch ? this.tempo : 1;
    if (stretch) {
      const params = { ...this._params(), output: t, input: pos, active: true };
      this._scheduleStretch(params);
    } else this._startDirect(t, pos);
    this._emit('state');
  }

  pause() {
    if (!this.ctx || !this.playing) return;
    const pos = this.position;
    const now = this.ctx.currentTime;
    this.duck.gain.setTargetAtTime(0, now, 0.012);
    this._stopDirect(now + 0.06);
    this._idleStretch(now + 0.06);
    this.playing = false;
    this._engine = null;
    this._anchor = { pos, t: now };
    this._emit('state');
  }

  toggle() { return this.playing ? this.pause() : this.play(); }

  seek(seconds) {
    if (!this.ctx) return;
    const pos = clamp(seconds, 0, this.duration);
    if (!this.playing) {
      this._anchor = { pos, t: this.ctx.currentTime };
      this._emit('seek');
      return;
    }
    // brief duck so the jump does not click
    const now = this.ctx.currentTime;
    const t = now + 0.05;
    this.duck.gain.cancelScheduledValues(now);
    this.duck.gain.setTargetAtTime(0, now, 0.008);
    this.duck.gain.setTargetAtTime(1, t, 0.012);
    this._anchor = { pos, t };
    if (this._engine === 'stretch') {
      const params = { ...this._params(), output: t, input: pos, active: true };
      this._scheduleStretch(params);
    } else {
      this._stopDirect(t);
      this._startDirect(t, pos);
    }
    this._emit('seek');
  }

  /**
   * Rehearsal follow: let the rate be driven live (by a drummer's hits) instead of by the tempo buttons. Resolves once the
   * stretch engine is ready, so the caller can then arm a count-in knowing playback can start sample-accurately.
   */
  async setFollowing(on) {
    this.following = !!on;
    this._retune();
    if (this.following && this.ctx) await this._prepStretch().catch(() => { this.following = false; });
    this._emit('follow');
  }

  /** Change the speed while playing, glitch-free: only the stretch engine's rate moves, pitch and position carry on. */
  follow(rate) {
    if (!this.ctx) return;
    rate = clamp(rate, 0.5, 1.5);
    if (Math.abs(rate - this.tempo) < 0.0004) return;
    this.tempo = rate;
    if (this.playing && this._engine === 'stretch') {
      const t = this.ctx.currentTime + 0.02;
      const pos = this._positionAt(t);
      this._anchor = { pos, t };
      this._rate = rate;
      this._scheduleStretch({ ...this._params(), output: t, input: pos, active: true });
    } else if (!this.playing) this._rate = this._stretchOk ? rate : 1;
    this._emit('rate'); // not 'tune': that one saves the song's setup, and this changes several times a second
  }

  /** Audio-graph time that is being heard at the performance.now() instant `ms` (a MIDI or microphone event's timestamp). */
  heardAt(ms) {
    const c = this.ctx;
    const ts = c.getOutputTimestamp?.();
    if (ts && ts.performanceTime > 0) return ts.contextTime + (ms - ts.performanceTime) / 1000;
    return c.currentTime + (ms - performance.now()) / 1000 - this.outLatency; // Safari: no output timestamp
  }

  /** Song position heard at the performance.now() instant `ms`. */
  positionHeardAt(ms) { return this._positionAt(this.heardAt(ms)); }

  setTempo(rate) { this.tempo = clamp(rate, 0.5, 1.5); this._retune(); this._emit('tune'); }
  get chordShift() { return this.chordLock ? this.semitones : this.chordKey; }
  /** Locking snaps the chords back to the audio's key; unlocking keeps them where they are now. */
  setChordLock(on) {
    on = !!on;
    if (on === this.chordLock) return;
    if (!on) this.chordKey = this.semitones;
    this.chordLock = on;
    this._emit('tune');
  }
  setChordKey(n) { this.chordKey = clamp(Math.round(n), -12, 12); this._emit('tune'); }
  setSemitones(n) { this.semitones = clamp(Math.round(n), -12, 12); this._retune(); this._emit('tune'); }
  setCents(c) { this.cents = clamp(Math.round(c), -50, 50); this._retune(); this._emit('tune'); }
  setFormants(on) { this.formants = !!on; this._retune(); this._emit('tune'); }

  setLoop(a, b) {
    this.loop = a != null && b != null && b - a > 0.2 ? { a, b } : null;
    this._retune();
    this._emit('loop');
  }

  // ── mixing ──
  _applyGains() {
    if (!this.ctx) return;
    const anySolo = this.stems.some((s) => s.solo);
    const t = this.ctx.currentTime;
    for (const s of this.stems) {
      const audible = anySolo ? s.solo : !s.muted;
      s.gain.gain.setTargetAtTime(audible ? s.volume : 0, t, 0.015);
    }
    this._syncStretch();
    this._emit('mix');
  }

  isAudible(s) { return this.stems.some((x) => x.solo) ? s.solo : !s.muted; }

  setMuted(name, on) { this._stem(name).muted = on; this._applyGains(); }
  setSolo(name, on) { this._stem(name).solo = on; this._applyGains(); }
  setVolume(name, v) { this._stem(name).volume = clamp(v, 0, 1.5); this._applyGains(); }
  /** Route this computer's sound to another output ('' = system default). Resolves false where the engine cannot. */
  async setSink(id) {
    this.sinkId = id || '';
    if (!this.ctx) return true;
    if (!this.ctx.setSinkId) return !id;
    try { await this.ctx.setSinkId(this.sinkId); return true; } catch { this.sinkId = ''; return false; }
  }
  setHeld(on) { this.held = !!on; if (this.out) this.out.gain.setTargetAtTime(on ? 0 : 1, this.ctx.currentTime, 0.02); }
  setMaster(v) { this.master.gain.setTargetAtTime(clamp(v, 0, 1), this.ctx.currentTime, 0.02); this.masterLevel = v; }

  clearSolo() { for (const s of this.stems) s.solo = false; this._applyGains(); }
  unmuteAll() { for (const s of this.stems) s.muted = false; this._applyGains(); }

  mixState() { return this.stems.map((s) => ({ name: s.name, muted: s.muted, solo: s.solo })); }
  setMixState(state) {
    for (const s of this.stems) {
      const m = state.find((x) => x.name === s.name);
      if (m) { s.muted = m.muted; s.solo = m.solo; }
    }
    this._applyGains();
  }
  muteAll() {
    for (const s of this.stems) { s.solo = false; s.muted = true; }
    this._applyGains();
  }

  /** Mute exactly the named stems (empty = everything audible); clears solos. */
  setMuteSet(names) {
    for (const s of this.stems) { s.solo = false; s.muted = names.includes(s.name); }
    this._applyGains();
  }

  _stem(name) { return this.stems.find((s) => s.name === name); }

  _tick = () => {
    this._raf = requestAnimationFrame(this._tick);
    if (this.playing && !this.loop && this.position >= this.duration - 0.03) {
      this.pause();
      this._anchor = { pos: 0, t: this.ctx.currentTime };
      this._emit('ended');
    }
    this._emit('frame');
  };

  _emit(type) { this.dispatchEvent(new Event(type)); }

  dispose() {
    cancelAnimationFrame(this._raf);
    for (const s of this.stems) { try { s.src?.stop(); s.node?.stop(); s.node?.disconnect(); } catch { /* closing */ } }
    this._stretchOk = false;
    this._engine = null;
    this.stems = [];
    this.playing = false;
    this.loop = null;
    if (this._wakeUp) { document.removeEventListener('visibilitychange', this._wakeUp); this._wakeUp = null; }
    if (this.ctx) { this.ctx.close().catch(() => {}); this.ctx = null; }
  }
}
