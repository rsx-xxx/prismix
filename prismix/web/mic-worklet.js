// Runs on the audio thread: reports the loudness of three frequency bands (low: kick, mid: snare and body, high: hats and
// cymbals) every 256 samples, with the audio clock time of the end of that slice. Used on the microphone and on the stems.
const HOP = 256;

class BandMeter extends AudioWorkletProcessor {
  constructor() {
    super();
    this.n = 0;
    this.a1 = 1 - Math.exp((-2 * Math.PI * 180) / sampleRate); // one-pole low-passes at 180 Hz and 2.5 kHz split the bands
    this.a2 = 1 - Math.exp((-2 * Math.PI * 2500) / sampleRate);
    this.l1 = 0;
    this.l2 = 0;
    this.s = [0, 0, 0];
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      const x = ch[i];
      this.l1 += this.a1 * (x - this.l1);
      this.l2 += this.a2 * (x - this.l2);
      const low = this.l1, mid = this.l2 - this.l1, hi = x - this.l2;
      this.s[0] += low * low;
      this.s[1] += mid * mid;
      this.s[2] += hi * hi;
      if (++this.n === HOP) {
        this.port.postMessage({ t: currentTime + (i + 1 - ch.length) / sampleRate, b: this.s.map((v) => Math.sqrt(v / HOP)) });
        this.s = [0, 0, 0];
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor('band-meter', BandMeter);
