// Multi-stem player. One Signalsmith Stretch node per stem (WASM phase-vocoder with
// formant handling) so tempo and key change independently without the "robot" artefacts of
// naive resampling. All nodes get identical schedules, so they stay sample-locked.
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
    this.cents = 0;
    this.formants = true;
    this.loop = null; // {a, b} seconds
    this.playing = false;
    this._anchor = { pos: 0, t: 0 }; // input position `pos` is heard at context time `t`
    this._latency = 0.1;
    this._raf = 0;
  }

  /** Fetch + decode every stem. onProgress(fraction 0..1). */
  async load(meta, baseUrl, onProgress = () => {}) {
    this.dispose();
    this.meta = meta;
    this.duration = meta.duration;
    this.ctx = new AudioContext({ sampleRate: meta.sampleRate, latencyHint: 'playback' });
    this.master = this.ctx.createGain(); // user volume
    this.master.connect(this.ctx.destination);
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
      const decoded = await this.ctx.decodeAudioData(await new Blob(chunks).arrayBuffer());
      const channels = [0, 1].map((c) => {
        const out = new Float32Array(decoded.length);
        decoded.copyFromChannel(out, Math.min(c, decoded.numberOfChannels - 1));
        return out;
      });
      const node = await SignalsmithStretch(this.ctx); // default options: 1 in, 2-ch out (0-input config yields silence)
      await node.addBuffers(channels, channels.map((c) => c.buffer));
      const gain = this.ctx.createGain();
      node.connect(gain).connect(this.duck);
      return { name: s.name, node, gain, volume: 1, muted: false, solo: false };
    }));

    this._latency = (await this.stems[0].node.latency()) || 0.1;
    this.stems.forEach((s) => s.node.stop());
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
    let p = pos + Math.max(0, t - t0) * this.tempo;
    if (this.loop && p >= this.loop.b) {
      const len = this.loop.b - this.loop.a;
      p = this.loop.a + ((p - this.loop.a) % len);
    }
    return clamp(p, 0, this.duration);
  }

  get pitch() { return this.semitones + this.cents / 100; }

  _each(fn) { this.stems.forEach((s) => fn(s.node, s)); }

  _params() {
    return {
      rate: this.tempo,
      semitones: this.pitch,
      formantCompensation: this.formants,
      loopStart: this.loop ? this.loop.a : 0,
      loopEnd: this.loop ? this.loop.b : 0,
    };
  }

  /** Re-apply tempo/key/loop to every node without interrupting playback. */
  _retune() {
    if (!this.playing) return;
    const t = this.ctx.currentTime + 0.02;
    this._anchor = { pos: this._positionAt(t), t };
    const params = { ...this._params(), output: t, active: true };
    this._each((n) => n.schedule(params));
  }

  async play() {
    if (!this.ctx || this.playing) return;
    await this.ctx.resume();
    if (this._anchor.pos >= this.duration - 0.05) this._anchor.pos = this.loop?.a ?? 0;
    const t = this.ctx.currentTime + this._latency;
    const pos = this._anchor.pos;
    this.duck.gain.cancelScheduledValues(this.ctx.currentTime);
    this.duck.gain.setValueAtTime(0, this.ctx.currentTime);
    this.duck.gain.setTargetAtTime(1, t - 0.01, 0.01);
    this._anchor = { pos, t };
    this.playing = true;
    const params = { ...this._params(), output: t, input: pos, active: true };
    this._each((n) => n.schedule(params));
    this._emit('state');
  }

  pause() {
    if (!this.ctx || !this.playing) return;
    const pos = this.position;
    const now = this.ctx.currentTime;
    this.duck.gain.setTargetAtTime(0, now, 0.012);
    this._each((n) => n.stop(now + 0.06));
    this.playing = false;
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
    const params = { ...this._params(), output: t, input: pos, active: true };
    this._each((n) => n.schedule(params));
    this._emit('seek');
  }

  setTempo(rate) { this.tempo = clamp(rate, 0.5, 1.5); this._retune(); this._emit('tune'); }
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
    for (const s of this.stems) { try { s.node.stop(); s.node.disconnect(); } catch { /* closing */ } }
    this.stems = [];
    this.playing = false;
    this.loop = null;
    if (this.ctx) { this.ctx.close().catch(() => {}); this.ctx = null; }
  }
}
