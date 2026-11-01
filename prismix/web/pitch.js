// Monophonic pitch detection: McLeod Pitch Method (normalized square difference + peak picking + parabolic refinement).
// Pure functions so the AudioWorklet, the main-thread fallback and the tests share one implementation.

/** Average `factor` samples at a time (a box low-pass), which is enough to keep guitar and bass fundamentals intact. */
export function decimate(buf, factor) {
  if (factor <= 1) return buf;
  const out = new Float32Array(Math.floor(buf.length / factor));
  for (let i = 0; i < out.length; i++) {
    let s = 0;
    for (let k = 0; k < factor; k++) s += buf[i * factor + k];
    out[i] = s / factor;
  }
  return out;
}

export const rms = (buf) => {
  let s = 0;
  for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
  return Math.sqrt(s / buf.length);
};

/**
 * Fundamental of `buf` in Hz and how clear it is (0..1), or null when there is no steady pitch.
 * `minHz`/`maxHz` bound the search; the buffer must hold at least two periods of `minHz`.
 */
export function detect(buf, sampleRate, { minHz = 28, maxHz = 1200, k = 0.9 } = {}) {
  const n = buf.length;
  const tauMax = Math.min(Math.floor(sampleRate / minHz), n >> 1);
  const tauMin = Math.max(2, Math.floor(sampleRate / maxHz));
  if (tauMax <= tauMin + 2) return null;
  const nsdf = new Float32Array(tauMax + 1);
  // m(tau) = sum of x[j]^2 + x[j+tau]^2 over the window, updated incrementally
  const w = n - tauMax;
  let m = 0;
  for (let j = 0; j < w; j++) m += buf[j] * buf[j] + buf[j + tauMax] * buf[j + tauMax];
  for (let tau = tauMax; tau >= 0; tau--) {
    let r = 0;
    for (let j = 0; j < w; j++) r += buf[j] * buf[j + tau];
    nsdf[tau] = m > 0 ? (2 * r) / m : 0;
    if (tau > 0) m += buf[w + tau - 1] * buf[w + tau - 1] - buf[tau - 1] * buf[tau - 1];
  }
  // key maxima: the highest point of each positive lobe after the first downward zero crossing
  const peaks = [];
  let tau = 1;
  while (tau < tauMax && nsdf[tau] > 0) tau++;
  while (tau < tauMax) {
    while (tau < tauMax && nsdf[tau] <= 0) tau++;
    let best = -1;
    while (tau < tauMax && nsdf[tau] > 0) {
      if (best < 0 || nsdf[tau] > nsdf[best]) best = tau;
      tau++;
    }
    if (best >= tauMin && nsdf[best] > nsdf[best - 1] && nsdf[best] >= nsdf[best + 1]) peaks.push(best);
  }
  if (!peaks.length) return null;
  const top = Math.max(...peaks.map((p) => nsdf[p]));
  const pick = peaks.find((p) => nsdf[p] >= k * top);
  const [first, clarity] = refine(nsdf, pick);
  // the same peak a few periods out pins the period down far more tightly than the first one does
  const mult = Math.floor(tauMax / (first + 1));
  let period = first;
  if (mult > 1) {
    const at = Math.round(first * mult);
    let best = at;
    for (let t = at - 2; t <= at + 2; t++) if (nsdf[t] > nsdf[best]) best = t;
    if (best > 1 && best < tauMax && nsdf[best] >= nsdf[best - 1] && nsdf[best] >= nsdf[best + 1]) period = refine(nsdf, best)[0] / mult;
  }
  return { hz: sampleRate / period, clarity };
}

/** Parabolic interpolation around a peak: [fractional lag, interpolated height]. */
function refine(nsdf, p) {
  const a = nsdf[p - 1], b = nsdf[p], c = nsdf[p + 1];
  const d = a - 2 * b + c;
  const shift = d === 0 ? 0 : (a - c) / (2 * d);
  return [p + shift, Math.min(1, b - 0.25 * (a - c) * shift)];
}

/** MIDI number (fractional) of a frequency against a reference A4. */
export const toMidi = (hz, a4 = 440) => 69 + 12 * Math.log2(hz / a4);
export const toHz = (midi, a4 = 440) => a4 * 2 ** ((midi - 69) / 12);

const NAMES = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B'];
const FLATS = ['C', 'D♭', 'D', 'E♭', 'E', 'F', 'G♭', 'G', 'A♭', 'A', 'B♭', 'B'];
export const noteName = (midi, flats = false) => (flats ? FLATS : NAMES)[((midi % 12) + 12) % 12];
export const octaveOf = (midi) => Math.floor(midi / 12) - 1;

/** Nearest note of a frequency: {midi, cents} where cents is -50..50 off that note. */
export function nearest(hz, a4 = 440) {
  const f = toMidi(hz, a4);
  const midi = Math.round(f);
  return { midi, cents: (f - midi) * 100 };
}

/** Nearest of the given open-string MIDI notes (any octave error counts), with cents off it. */
export function nearestString(hz, open, a4 = 440) {
  const f = toMidi(hz, a4);
  let best = 0;
  for (let i = 1; i < open.length; i++) if (Math.abs(f - open[i]) < Math.abs(f - open[best])) best = i;
  return { index: best, cents: (f - open[best]) * 100 };
}
