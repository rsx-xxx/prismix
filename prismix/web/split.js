// Lyrics | chords divider: drag to resize both columns proportionally; by default the width follows the longest lyric line.

const $ = (id) => document.getElementById(id);
const KEY = 'stems:split';
const MIN = 0.34, MAX = 0.6;

export function createSplit() {
  const panes = $('panes'), grip = $('split'), ly = $('lyrics');
  let manual = Number(localStorage.getItem(KEY)) || 0;
  const ctx = document.createElement('canvas').getContext('2d');

  const two = () => !panes.classList.contains('solo') && !ly.hidden && !ly.classList.contains('off') && getComputedStyle(panes).display === 'grid';
  const avail = () => {
    const f = $('fing'), fw = f.hidden || getComputedStyle(f).display === 'none' || getComputedStyle(f).gridColumnStart === '1' ? 0 : f.offsetWidth + 16;
    return panes.clientWidth - fw - 16;
  };

  function wanted() {
    const lines = [...ly.querySelectorAll('.ly-line:not(.gap)')];
    if (!lines.length) return null;
    const cs = getComputedStyle(lines[0]);
    ctx.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    let w = 0;
    for (const l of lines) w = Math.max(w, ctx.measureText(l.textContent).width);
    return (w * 1.06 + 40) / avail();
  }

  function layout() {
    grip.hidden = !two();
    if (!two()) { panes.style.removeProperty('--ly'); panes.style.removeProperty('--rc'); return; }
    const raw = manual || wanted() || 1 / 3;
    const p = Math.min(MAX, Math.max(MIN, raw));
    panes.style.setProperty('--ly', `${p}fr`);
    panes.style.setProperty('--rc', `${1 - p}fr`);
    grip.style.left = `${ly.offsetLeft + ly.offsetWidth + 8}px`;
  }
  const soon = () => requestAnimationFrame(layout);

  grip.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    grip.classList.add('on');
    const move = (ev) => {
      const r = panes.getBoundingClientRect();
      manual = Math.min(MAX, Math.max(MIN, (ev.clientX - r.left) / avail()));
      layout();
    };
    const up = () => {
      grip.classList.remove('on');
      grip.removeEventListener('pointermove', move);
      grip.removeEventListener('pointerup', up);
      grip.removeEventListener('pointercancel', up);
      try { localStorage.setItem(KEY, String(manual)); } catch { /* ignore */ }
    };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up);
    grip.addEventListener('pointercancel', up);
  });
  grip.addEventListener('dblclick', () => { manual = 0; try { localStorage.removeItem(KEY); } catch { /* ignore */ } layout(); });

  document.addEventListener('blocks:change', soon);
  document.addEventListener('layout:change', soon);
  document.addEventListener('lyrics:data', () => setTimeout(layout, 50));
  addEventListener('resize', soon);
  new ResizeObserver(soon).observe(panes);
  new MutationObserver(soon).observe($('ly-body'), { childList: true });
  new MutationObserver(soon).observe(panes, { attributes: true, attributeFilter: ['class'] });
  new MutationObserver(soon).observe($('fing'), { attributes: true, attributeFilter: ['hidden'] });
  layout();
  return { layout };
}
