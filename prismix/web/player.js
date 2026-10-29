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
    this.chordKey = 0; // chords and tab are read this far from the audio's key; the sound does not move
    this.cents = 0;
    this.formants = true;
    this.loop = null; // {a, b} seconds
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
    this.master.connect(this.out);
    this.duck = this.ctx.createGain(); // short fades around seek/pause to avoid clicks
    this.duck.connect(this.master);

    const loaded = new Array(meta.stems.length).fill(0);
    const total = new Array(meta.stems.length).fill(1);
    const report = () => {
      const got = loaded.reduce((a, b) => a + b, 0);
      const sum = total.reduce((a, b) => a + b, 0);
      onProgress(Math.min(got / sum, 1));
    };

    this.stems = await Promise.all(meta.stems.map(async (s, i) => {
      const res = await fetch(`${baseUrl}/${s.file}`);
      if (!res.ok) throw new Error(`Could not load ${s.name} (${res.status})`);
      total[i] = Number(res.headers.get('content-length')) || 1;
      const reader = res.body.getReader();
      const chunks = [];
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        loaded[i] += value.length;
        report();
      }
      const buf = await this.ctx.decodeAudioData(await new Blob(chunks).arrayBuffer());
      const gain = this.ctx.createGain();
      gain.connect(this.duck);
      return { name: s.name, buf, node: null, gain, src: null, volume: 1, muted: false, solo: false };
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

  _params() {
    return {
      rate: this.tempo,
      semitones: this.pitch,
      formantCompensation: this.formants,
      loopStart: this.loop ? this.loop.a : 0,
      loopEnd: this.loop ? this.loop.b : 0,
    };
  }

  get _needsStretch() { return this.tempo !== 1 || this.pitch !== 0; }

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
      this._each((n) => n.schedule(params));
    } else {
      this._each((n) => n.stop(t));
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

  async play() {
    if (!this.ctx || this.playing) return;
    const ctx = this.ctx;
    if (this._needsStretch) await this._prepStretch().catch(() => {});
    if (!(await this._wake()) || ctx !== this.ctx || this.playing) return this._emit('blocked'); // never claim to play while silent
    if (this._anchor.pos >= this.duration - 0.05) this._anchor.pos = this.loop?.a ?? 0;
    const stretch = this._needsStretch && this._stretchOk;
    const t = ctx.currentTime + (stretch ? this._latency : 0.05);
    const pos = this._anchor.pos;
    this.duck.gain.cancelScheduledValues(ctx.currentTime);
    this.duck.gain.setValueAtTime(0, ctx.currentTime);
    this.duck.gain.setTargetAtTime(1, t - 0.01, 0.01);
    this._anchor = { pos, t };
    this.playing = true;
    this._engine = stretch ? 'stretch' : 'direct';
    this._rate = stretch ? this.tempo : 1;
    if (stretch) {
      const params = { ...this._params(), output: t, input: pos, active: true };
      this._each((n) => n.schedule(params));
    } else this._startDirect(t, pos);
    this._emit('state');
  }

  pause() {
    if (!this.ctx || !this.playing) return;
    const pos = this.position;
    const now = this.ctx.currentTime;
    this.duck.gain.setTargetAtTime(0, now, 0.012);
    this._stopDirect(now + 0.06);
    this._each((n) => n.stop(now + 0.06));
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
      this._each((n) => n.schedule(params));
    } else {
      this._stopDirect(t);
      this._startDirect(t, pos);
    }
    this._emit('seek');
  }

  setTempo(rate) { this.tempo = clamp(rate, 0.5, 1.5); this._retune(); this._emit('tune'); }
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
    this._emit('mix');
  }

  isAudible(s) { return this.stems.some((x) => x.solo) ? s.solo : !s.muted; }

  setMuted(name, on) { this._stem(name).muted = on; this._applyGains(); }
  setSolo(name, on) { this._stem(name).solo = on; this._applyGains(); }
  setVolume(name, v) { this._stem(name).volume = clamp(v, 0, 1.5); this._applyGains(); }
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
