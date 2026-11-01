import { decimate, detect, rms } from './pitch.js';

// Runs on the audio thread: gathers the microphone into a sliding window and reports a pitch about 20 times a second.
const WINDOW = 8192;
const HOP = 2048;

class PitchProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(WINDOW);
    this.fill = 0;
    this.since = 0;
    this.factor = Math.max(1, Math.round(sampleRate / 24000));
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      if (this.fill < WINDOW) this.buf[this.fill++] = ch[i];
      else { this.buf.copyWithin(0, 1); this.buf[WINDOW - 1] = ch[i]; }
    }
    this.since += ch.length;
    if (this.fill === WINDOW && this.since >= HOP) {
      this.since = 0;
      const level = rms(this.buf);
      const low = decimate(this.buf, this.factor);
      const hit = level > 0.004 ? detect(low, sampleRate / this.factor) : null;
      this.port.postMessage({ level, hz: hit?.hz ?? 0, clarity: hit?.clarity ?? 0 });
    }
    return true;
  }
}
registerProcessor('pitch-processor', PitchProcessor);
