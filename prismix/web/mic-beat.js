// Following a live drummer by microphone. The microphone hears the drummer and also the stems coming out of the speakers, so
// two things are needed: take the stems' own sound out of what the microphone heard (the stems' loudness is known exactly),
// and pick the drummer's hits out of what is left. The hits then drive the same follower as a MIDI kit (rehearse.js).
//
// The maths here has no audio or DOM, so the tests can drive it with simulated signals.

const BANDS = 3;
const W = [1, 0.8, 0.6]; // the low band (kick) says the most about the beat, the high band (hats) the least

/**
 * Removes the stems' bleed from the microphone, band by band: the microphone hears each band of the stems at some fixed
 * fraction `g` (distance, volume), found as the lowest ratio the microphone/stems loudness reaches while the stems play. A
 * drummer only ever adds to the microphone, so he raises the ratio and does not move the lower edge.
 */
export class Bleed {
  constructor() {
    this.g = new Array(BANDS).fill(0.3);
    this.ref = []; // [t, bands] of the stems' recent loudness, oldest first
    this.floor = 0.002; // below this the stems are silent in that band: no estimate is made
  }

  /** Stems loudness at time `t` (ms, performance.now clock). */
  push(t, bands) {
    this.ref.push([t, bands]);
    while (this.ref.length > 400 && this.ref[0][0] < t - 1000) this.ref.shift();
  }

  _at(t) {
    const r = this.ref;
    if (!r.length) return null;
    let i = r.length - 1;
    while (i > 0 && r[i][0] > t) i--;
    return t - r[i][0] > 60 ? null : r[i][1]; // no reading within 60 ms: the stems are not playing
  }

  /** The microphone's bands at time `t` with the stems' share taken away. */
  residual(t, mic) {
    const ref = this._at(t);
    if (!ref) return mic;
    return mic.map((m, i) => {
      if (ref[i] > this.floor) {
        const ratio = m / ref[i];
        this.g[i] += (ratio - this.g[i]) * (ratio < this.g[i] ? 0.2 : 0.0015); // follows the lower edge down fast, up very slowly
      }
      return Math.max(0, m - this.g[i] * 1.15 * ref[i]); // a little more than the estimate, so leftovers do not read as hits
    });
  }
}

/** Finds onsets in a stream of band loudness (one frame about every 5 ms). */
export class OnsetPicker {
  constructor({ refractory = 80, rise = 0.9 } = {}) {
    this.refractory = refractory;
    this.rise = rise; // ln of the jump in loudness that counts as a hit: 0.9 is about 2.5 times louder
    this.lv = [];
    this.mean = 0;
    this.dev = 0.3;
    this.noise = 0.01;
    this.pend = null; // the best candidate of the current rise, confirmed once the rise is over
    this.last = -1e9;
    this.prev = 0;
  }

  /** One frame; `t` in ms. Returns {t, weight} when a hit has just been confirmed. */
  feed(t, bands) {
    const v = bands.reduce((s, b, i) => s + b * W[i], 0);
    const lv = Math.log(v + 1e-5);
    this.lv.push(lv);
    if (this.lv.length > 4) this.lv.shift();
    this.noise += (v - this.noise) * (v < this.noise ? 0.05 : 0.0005); // the quiet level of the room, followed slowly
    const odf = this.lv.length === 4 ? Math.max(0, lv - Math.min(this.lv[0], this.lv[1], this.lv[2])) : 0;
    this.mean += (odf - this.mean) * 0.01;
    this.dev += (Math.abs(odf - this.mean) - this.dev) * 0.01;
    const thr = Math.max(this.rise, this.mean + 3 * this.dev);
    let out = null;
    if (this.pend && odf < this.pend.odf * 0.8) { // the rise has peaked: that was a hit
      if (this.pend.t - this.last >= this.refractory) {
        out = { t: this.pend.t, weight: this.pend.low ? 1 : 0.6 };
        this.last = this.pend.t;
      }
      this.pend = null;
    }
    if (odf > thr && v > Math.max(0.008, 2.5 * this.noise)) {
      if (!this.pend || odf > this.pend.odf) this.pend = { t, odf, low: bands[0] * 1.5 > bands[1] + bands[2] };
    }
    this.prev = odf;
    return out;
  }
}
