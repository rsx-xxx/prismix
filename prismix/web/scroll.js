// Eased scrolling for inner panes. Native smooth scrolling restarts on every call and overshoots when lines change quickly.

const running = new WeakMap();

export function cancelGlide(box) {
  cancelAnimationFrame(running.get(box));
  running.delete(box);
}

/** Ease `box` to scrollTop `top` (clamped), over a duration that grows with the distance. */
export function glide(box, top) {
  top = Math.max(0, Math.min(top, box.scrollHeight - box.clientHeight));
  const from = box.scrollTop, dist = top - from;
  cancelGlide(box);
  if (Math.abs(dist) < 2) return;
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) { box.scrollTop = top; return; }
  const ms = Math.min(700, 260 + Math.abs(dist) * 0.7), t0 = performance.now();
  const step = (now) => {
    const p = Math.min(1, (now - t0) / ms);
    box.scrollTop = from + dist * (1 - (1 - p) ** 3);
    if (p < 1) running.set(box, requestAnimationFrame(step)); else running.delete(box);
  };
  running.set(box, requestAnimationFrame(step));
}

/** Where `node` sits inside the scrolled `box`, independent of offsetParent. */
export const offsetIn = (box, node) => node.getBoundingClientRect().top - box.getBoundingClientRect().top;

/** Piecewise-linear lookup over ascending anchors: the `to` value at `v` of `from`; null with fewer than two anchors. */
function lookup(points, from, to, v) {
  if (points.length < 2) return null;
  if (v <= points[0][from]) return points[0][to];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1], b = points[i];
    if (v <= b[from]) return b[from] === a[from] ? b[to] : a[to] + ((v - a[from]) / (b[from] - a[from])) * (b[to] - a[to]);
  }
  return points.at(-1)[to];
}
export const yToT = (points, y) => lookup(points, 'y', 't', y);
export const tToY = (points, t) => lookup(points, 't', 'y', t);

/**
 * Scroll two panes together by song time: wheel one and the other lands on the same moment.
 * A pane is { root (stable ancestor), box() (the scroller), track() -> [{y, t}] in content pixels,
 * hold() (stop auto-following for a while), active(), anchor (0..1 of its height) }.
 */
export function linkScroll(a, b) {
  let driver = null, until = 0;
  const arm = (pane) => () => { driver = pane; until = Date.now() + 800; };
  for (const pane of [a, b]) for (const t of ['wheel', 'touchmove', 'pointerdown']) pane.root.addEventListener(t, arm(pane), { passive: true, capture: true });
  const drive = (from, to) => from.root.addEventListener('scroll', (e) => {
    if (driver !== from || Date.now() > until || e.target !== from.box() || !from.active() || !to.active()) return;
    until = Date.now() + 800;
    const src = from.box(), dst = to.box();
    const t = yToT(from.track(), src.scrollTop + src.clientHeight * from.anchor);
    const y = t == null ? null : tToY(to.track(), t);
    if (y == null) return;
    to.hold();
    cancelGlide(dst);
    dst.scrollTop = y - dst.clientHeight * to.anchor;
  }, { passive: true, capture: true });
  drive(a, b);
  drive(b, a);
}

/** True while two side-by-side panes are driven together, so each pane's own auto-centering stays out of the way. */
export const lockstep = { on: false };

/**
 * Keep two side-by-side panes on the same moment while playing: the current lyric line and the current chord bar sit at
 * the same height. Panes also need following() (false after a manual scroll). Returns follow(pos), called every frame.
 */
export function linkFollow(a, b, player) {
  const cache = new Map();
  const track = (pane) => {
    const box = pane.box(), key = `${box.scrollHeight}/${box.clientHeight}`;
    const hit = cache.get(pane);
    if (hit?.key === key) return hit.points;
    const points = pane.track();
    cache.set(pane, { key, points });
    return points;
  };
  const beside = () => Math.abs(a.root.getBoundingClientRect().top - b.root.getBoundingClientRect().top) < 60;
  return function follow(pos) {
    const on = a.active() && b.active() && a.following() && b.following() && beside();
    lockstep.on = on;
    if (!on) return;
    for (const pane of [a, b]) {
      const box = pane.box(), y = tToY(track(pane), pos);
      if (y == null) continue;
      cancelGlide(box);
      const want = Math.max(0, Math.min(y - box.clientHeight * pane.anchor, box.scrollHeight - box.clientHeight));
      const d = want - box.scrollTop;
      box.scrollTop = player.playing && Math.abs(d) < 600 ? box.scrollTop + d * 0.3 : want;
    }
  };
}
