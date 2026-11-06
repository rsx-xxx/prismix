// Rehearsal follow: turns a live drummer's hits into a playback rate for the stems, so a muted drum stem does not leave the
// band drifting against the room. Pure maths, no audio or DOM: the player, the MIDI feed and the tests all drive it the same way.
//
// A hit is compared with where the song's beat grid is at that instant. The residual against the nearest grid point is the
// phase error; a small loop turns it into a rate (a slow tempo estimate plus a bounded, smoothed phase correction), so the
// stems ease back onto the drummer's hits over a few beats instead of jumping. Hits further off the grid than eighth-note
// timekeeping (fills) are ignored once locked; a constant offset is simply pulled out by the rate, never by a seek.

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

/** Piecewise-linear map between seconds and beat units from the analysed beat times, extrapolated at both ends. */
export function beatGrid(beats) {
  const n = beats.length;
  const first = n > 1 ? beats[1] - beats[0] : 0.5;
  const last = n > 1 ? beats[n - 1] - beats[n - 2] : 0.5;
  const at = (sec) => { // binary search for the beat interval containing sec
    let lo = 0;
    let hi = n - 1;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (beats[mid] <= sec) lo = mid; else hi = mid - 1; }
    return lo;
  };
  return {
    toBeat(sec) {
      if (n < 2) return sec / first;
      if (sec <= beats[0]) return (sec - beats[0]) / first;
      if (sec >= beats[n - 1]) return n - 1 + (sec - beats[n - 1]) / last;
      const i = at(sec);
      return i + (sec - beats[i]) / (beats[i + 1] - beats[i]);
    },
    toSec(beat) {
      if (n < 2) return beat * first;
      if (beat <= 0) return beats[0] + beat * first;
      if (beat >= n - 1) return beats[n - 1] + (beat - (n - 1)) * last;
      const i = Math.floor(beat);
      return beats[i] + (beat - i) * (beats[i + 1] - beats[i]);
    },
    /** Seconds of song per beat around `sec`. */
    beatLength(sec) {
      if (n < 2) return first;
      if (sec <= beats[0]) return first;
      if (sec >= beats[n - 1]) return last;
      const i = at(sec);
      return beats[i + 1] - beats[i];
    },
  };
}

/**
 * How much a General MIDI drum note says about the beat. Kick and snare carry it; hats a little; toms and cymbals are
 * fills and accents that land anywhere, so they are ignored.
 */
export function noteWeight(note, velocity = 100) {
  if (velocity < 20) return 0; // ghost notes and cross-talk
  if (note === 35 || note === 36 || note === 38 || note === 40 || note === 37) return 1;
  if (note === 42 || note === 44) return 0.5;
  if (note === 46 || note === 51 || note === 59) return 0.3;
  return 0;
}

/**
 * Four evenly spaced hits are a count-in. Returns the beat period (seconds) and the time of the last hit, or null.
 * `times` are hit times in seconds, oldest first.
 */
export function countIn(times, count = 4, tolerance = 0.15) {
  if (times.length < count) return null;
  const t = times.slice(-count);
  const gaps = t.slice(1).map((x, i) => x - t[i]);
  const period = [...gaps].sort((a, b) => a - b)[gaps.length >> 1];
  if (period < 0.25 || period > 1.5) return null;
  if (gaps.some((g) => Math.abs(g - period) > period * tolerance)) return null;
  return { period, last: t[t.length - 1] };
}

export class Follower {
  /**
   * @param grid   beatGrid()
   * @param opts   sub: grid divisions per beat a hit may land on (2 = eighth notes); min/max: rate limits
   */
  constructor(grid, { sub = 2, min = 0.6, max = 1.4 } = {}) {
    this.grid = grid;
    this.sub = sub;
    this.min = min;
    this.max = max;
    this.tight = 0.6 / (2 * sub); // beats; once locked, further from a grid point than this is a fill, not timekeeping
    this.reset(1);
  }

  /** Start from a known tempo ratio (the stems' current rate, or the count-in's). */
  reset(rate) {
    this.tempo = clamp(rate, this.min, this.max); // the drummer's tempo as a rate of the song's
    this.rate = this.tempo; // what the player should run at right now
    this.err = 0; // smoothed phase error in beats, + = the stems are ahead of the drummer
    this.misses = 0;
    this.recent = []; // signed residuals (beats) of the latest accepted hits, for trimming
    this.locked = false;
    this.hits = 0;
  }

  /**
   * One drummer hit. `pos` is the song position (seconds) being heard at the instant of the hit, `weight` from noteWeight().
   * Returns {accepted, rate, err}; `rate` is the new rate for the stems.
   */
  hit(pos, weight = 1) {
    const b = this.grid.toBeat(pos);
    const g = Math.round(b * this.sub) / this.sub;
    const e = b - g;
    // still searching (start, or lock lost): every hit counts, the nearest grid point is the best guess of what was meant
    if (this.locked && Math.abs(e) > this.tight) return this._miss();
    this.misses = 0;
    this.hits++;
    this.recent.push(e);
    if (this.recent.length > 24) this.recent.shift();
    const w = clamp(weight, 0, 1);
    this.err += 0.45 * w * (e - this.err);
    // the tempo estimate moves slowly, the phase term pulls the remaining error out within a couple of beats
    this.tempo = clamp(this.tempo * (1 - 0.015 * w * this.err), this.min, this.max);
    const pull = clamp(0.22 * this.err, -0.04, 0.04);
    const target = clamp(this.tempo * (1 - pull), this.min, this.max);
    // never change speed by more than a quarter percent per hit, so no tempo wobble is audible
    this.rate += clamp(target - this.rate, -0.0025, 0.0025);
    this.locked = this.hits >= 6 && Math.abs(this.err) < 0.06;
    return { accepted: true, rate: this.rate, err: this.err };
  }

  _miss() {
    if (++this.misses >= 4) { this.locked = false; this.hits = 0; } // too many off the grid: widen the net again
    return { accepted: false, rate: this.rate, err: this.err };
  }

  /** The stems' average lead over the drummer in real seconds (+ = ahead) around song position `pos`, for "set as in sync". */
  lead(pos) {
    if (this.recent.length < 8) return null;
    const m = this.recent.reduce((x, y) => x + y, 0) / this.recent.length;
    return (m * this.grid.beatLength(pos)) / this.rate;
  }
}
