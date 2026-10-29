// Notes, shared by the desktop studio and the phone. A note is pinned to a time in the song, so it is drawn on
// whatever sits at that time in the view at hand: a word in the lyrics, a bar in the chords, a spot in a tab bar.
// The views tag their cells with times (data-t / data-e on words and chord cells, data-t0 / data-t1 on tab and phone bars).
const S = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const SPK = '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5z" fill="currentColor"/>';
// name, colour, glyph: each note has its own colour, so a count or a dynamic reads at a glance
export const ICONS = {
  down: ['Quieter', '#4da3ff', S(`${SPK}<path d="M15.5 9.5a3.5 3.5 0 0 1 0 5"/>`)],
  up: ['Louder', '#ff9f43', S(`${SPK}<path d="M15.5 9.5a3.5 3.5 0 0 1 0 5M18 7a7 7 0 0 1 0 10"/>`)],
  mute: ['Silence', '#9b8cff', S(`${SPK}<path d="M16 9.5l5 5M21 9.5l-5 5"/>`)],
  bang: ['Watch out', '#ff5d73', S('<path d="M12 4.5v9.5"/><circle cx="12" cy="19" r="1.2" fill="currentColor"/>')],
  talk: ['Remark', '#2ed3b7', S('<path d="M4.5 5.5h15v10.5h-8.5l-4 3.5V16h-2.5z" fill="currentColor" fill-opacity=".25"/>')],
  cresc: ['Crescendo', '#5ad66f', S('<path d="M20 6.5L5 12l15 5.5"/>')],
  dim: ['Diminuendo', '#ff7ac6', S('<path d="M4 6.5L19 12 4 17.5"/>')],
  play: ['Play', '#3ddc84', S('<path d="M8 5.5v13l11-6.5z" fill="currentColor"/>')],
  pause: ['Pause', '#ffd24d', S('<path d="M8 5.5v13M16 5.5v13" stroke-width="3.4"/>')],
  hold: ['Hold', '#b57bff', S('<path d="M4 16a8 7 0 0 1 16 0"/><circle cx="12" cy="16" r="1.4" fill="currentColor"/>')],
  ...Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8].map((n) => [String(n), [`Count ${n}`, `hsl(${(n * 47 + 190) % 360} 80% 58%)`, `<b>${n}</b>`]])),
};
const TARGETS = {
  lyrics: '.sy[data-t], .ly-brk[data-t], .ly-instr[data-t]',
  bars: '[data-t0], .cell[data-t]',
};
const GAP = 1.5; // a note this far past the last word belongs to the silence, not to the word

const el = (tag, props = {}, ...kids) => { const n = Object.assign(document.createElement(tag), props); n.append(...kids); return n; };
const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const num = (v) => (v == null ? NaN : Number(v));

/**
 * @param {object} o
 * @param {() => string|null} o.id      the song on screen
 * @param {(msg: object) => boolean} o.send  band socket send
 * @param {() => HTMLElement[]} o.roots  containers whose cells carry times
 * @param {HTMLElement} o.button         toggles note mode
 */
export function createNotes({ id, send, roots, button }) {
  let list = [];
  let mode = false;
  let draft = null; // {id?, t, icon, text, show, by?}
  let token = 0;
  let queued = 0;
  const obs = new MutationObserver(() => schedule());

  const pop = el('div', { id: 'nt-pop', hidden: true, role: 'dialog', ariaLabel: 'Note' });
  const grid = el('div', { className: 'nt-grid' });
  const text = el('textarea', { className: 'nt-text', maxLength: 500, rows: 3, placeholder: 'Comment (optional)' });
  const radio = (v, label) => {
    const i = el('input', { type: 'radio', name: 'nt-show', value: v });
    return el('label', {}, i, label);
  };
  const showRow = el('div', { className: 'nt-row' }, radio('always', ' Always show'), radio('hover', ' On hover'), radio('tap', ' Hidden'));
  const meta = el('div', { className: 'nt-meta' });
  const del = el('button', { type: 'button', className: 'btn ghost small nt-del', textContent: 'Delete' });
  const cancel = el('button', { type: 'button', className: 'btn ghost small', textContent: 'Cancel' });
  const save = el('button', { type: 'button', className: 'btn small', textContent: 'Save' });
  pop.append(grid, text, showRow, meta, el('div', { className: 'nt-row nt-act' }, del, el('span', { className: 'nt-sp' }), cancel, save));
  document.body.append(pop);

  const face = (k, name) => {
    const [, color, svg] = ICONS[k] || ICONS.talk;
    const b = el('button', { type: 'button', className: 'nt-i', title: name, ariaLabel: name });
    b.innerHTML = svg; // static markup above, never user text
    b.style.setProperty('--c', color);
    return b;
  };
  for (const k of Object.keys(ICONS)) {
    const b = face(k, ICONS[k][0]);
    b.dataset.icon = k;
    grid.append(b);
  }

  function paintPop() {
    grid.querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.icon === draft.icon)));
    pop.querySelectorAll('input[name="nt-show"]').forEach((r) => { r.checked = r.value === draft.show; });
    del.hidden = !draft.id;
  }
  function open(d) {
    clearTimeout(waiting);
    save.disabled = del.disabled = false;
    draft = { icon: 'talk', text: '', show: 'always', ...d };
    text.value = draft.text;
    meta.textContent = `${clock(draft.t)}${draft.by ? ` · ${draft.by}` : ''}`;
    paintPop();
    pop.hidden = false;
    if (!draft.id) text.focus({ preventScroll: true });
  }
  let waiting = 0;
  function close() { clearTimeout(waiting); pop.hidden = true; draft = null; }
  /** The panel closes when the host answers; silence means a host that predates notes. */
  function waitAnswer() {
    save.disabled = del.disabled = true;
    meta.textContent = 'Saving…';
    waiting = setTimeout(() => { save.disabled = del.disabled = false; meta.textContent = 'No answer from the host. Restart Prismix on the computer to update it.'; }, 3000);
  }

  grid.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (b && draft) { draft.icon = b.dataset.icon; paintPop(); }
  });
  pop.addEventListener('change', (e) => { if (draft && e.target.name === 'nt-show') draft.show = e.target.value; });
  cancel.onclick = close;
  save.onclick = () => {
    if (!draft) return;
    const note = { id: draft.id, t: draft.t, icon: draft.icon, text: text.value.trim(), show: draft.show };
    if (!send({ t: 'note', op: draft.id ? 'edit' : 'add', id: id(), note })) { meta.textContent = 'Not connected: try again in a moment.'; return; }
    waitAnswer();
  };
  del.onclick = () => {
    if (!draft?.id) return;
    if (!send({ t: 'note', op: 'del', id: id(), note: { id: draft.id } })) { meta.textContent = 'Not connected: try again in a moment.'; return; }
    waitAnswer();
  };

  // ── drawing ──
  function box(target, n, wide) {
    if (wide) {
      const b = el('span', { className: 'nt-box wide' });
      b.style.left = `${n.pct}%`;
      target.append(b);
      return b;
    }
    let b = target.querySelector(':scope > .nt-box');
    if (!b) { b = el('span', { className: 'nt-box' }); target.append(b); }
    return b;
  }
  function note(n) {
    const name = (ICONS[n.icon] || ICONS.talk)[0];
    const i = face(ICONS[n.icon] ? n.icon : 'talk', name);
    i.classList.add('nt');
    i.title = `${name}${n.text ? `: ${n.text}` : ''}${n.by ? ` (${n.by})` : ''}`;
    i.dataset.nid = n.id;
    const x = el('button', { type: 'button', className: 'nt-x', title: 'Delete sticker', ariaLabel: 'Delete sticker', textContent: '×' });
    x.dataset.nid = n.id;
    const wrap = el('span', { className: 'nt-w' }, i, x);
    if (n.text && n.show !== 'tap') wrap.append(el('span', { className: `nt-say${n.show === 'hover' ? ' on-hover' : ''}`, textContent: n.text }));
    wrap.style.setProperty('--c', (ICONS[n.icon] || ICONS.talk)[1]);
    return wrap;
  }
  const last = (items, t, of) => { let k = -1; for (let j = 0; j < items.length && of(items[j]) <= t; j++) k = j; return k; };

  function place(root) {
    const words = [...root.querySelectorAll(TARGETS.lyrics)].filter((n) => !n.closest('#sing'));
    const bars = [...root.querySelectorAll(TARGETS.bars)];
    for (const n of list) {
      const wi = last(words, n.t + 0.05, (w) => num(w.dataset.t));
      if (wi >= 0) {
        const w = words[wi];
        if (!(w.classList.contains('sy') && n.t > num(w.dataset.e) + GAP)) box(w, n).append(note(n));
      }
      const bi = last(bars, n.t, (b) => num(b.dataset.t0 ?? b.dataset.t));
      if (bi < 0) continue;
      const b = bars[bi];
      const t0 = num(b.dataset.t0 ?? b.dataset.t);
      const t1 = b.dataset.t1 != null ? num(b.dataset.t1) : num(bars[bi + 1]?.dataset.t0 ?? bars[bi + 1]?.dataset.t) || t0 + 20;
      if (n.t >= t1 + 0.01 && b.dataset.t1 != null) continue;
      const wide = b.classList.contains('tbar') || b.classList.contains('tb');
      box(b, { ...n, pct: Math.max(0, Math.min(94, ((n.t - t0) / Math.max(1e-6, t1 - t0)) * 100)) }, wide).append(note(n));
    }
  }
  function paint() {
    queued = 0;
    obs.disconnect();
    const rs = roots().filter(Boolean);
    for (const r of rs) r.querySelectorAll('.nt-box').forEach((b) => b.remove());
    if (list.length) for (const r of rs) place(r);
    for (const r of rs) obs.observe(r, { childList: true, subtree: true });
  }
  function schedule() { if (!queued) queued = requestAnimationFrame(paint); }

  // ── interaction ──
  const timeAt = (e, target = e.target) => {
    const cell = target?.closest?.('[data-t0]');
    if (cell) {
      const t0 = num(cell.dataset.t0), t1 = num(cell.dataset.t1);
      if (!(cell.classList.contains('tbar') || cell.classList.contains('tb')) || !(t1 > t0)) return t0;
      const r = cell.getBoundingClientRect();
      return t0 + Math.max(0, Math.min(1, (e.clientX - r.left) / Math.max(1, r.width))) * (t1 - t0);
    }
    const w = target?.closest?.('.sy[data-t], .ly-brk[data-t], .ly-instr[data-t], .cell[data-t]');
    return w ? num(w.dataset.t) : NaN;
  };

  // Drag a sticker onto another word, chord or tab bar: it keeps its icon and text and takes the new time.
  let drag = null;
  document.addEventListener('pointerdown', (e) => {
    const s = e.target.closest?.('.nt');
    if (!s || e.button > 0) return;
    drag = { s, x: e.clientX, y: e.clientY, ghost: null, id: s.dataset.nid };
  }, true);
  document.addEventListener('pointermove', (e) => {
    if (!drag) return;
    if (!drag.ghost) {
      if (Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 6) return;
      drag.ghost = drag.s.cloneNode(true);
      drag.ghost.className = 'nt-i nt-ghost';
      drag.ghost.style.setProperty('--c', drag.s.style.getPropertyValue('--c'));
      document.body.append(drag.ghost);
      drag.s.classList.add('nt-lifted');
      document.body.classList.add('nt-dragging');
    }
    e.preventDefault();
    drag.ghost.style.transform = `translate(${e.clientX - 18}px, ${e.clientY - 18}px)`;
  }, true);
  const endDrag = (e, drop) => {
    const d = drag;
    drag = null;
    if (!d?.ghost) return;
    d.ghost.remove();
    d.s.classList.remove('nt-lifted');
    document.body.classList.remove('nt-dragging');
    skipClick = true;
    setTimeout(() => { skipClick = false; }, 0);
    if (!drop) return;
    const at = document.elementFromPoint(e.clientX, e.clientY);
    const t = at && roots().some((r) => r?.contains(at)) ? timeAt(e, at) : NaN;
    const n = list.find((x) => x.id === d.id);
    if (n && Number.isFinite(t)) send({ t: 'note', op: 'edit', id: id(), note: { ...n, t: Math.round(t * 100) / 100 } });
  };
  let skipClick = false;
  document.addEventListener('pointerup', (e) => endDrag(e, true), true);
  document.addEventListener('pointercancel', (e) => endDrag(e, false), true);
  document.addEventListener('click', (e) => {
    if (skipClick) { e.stopPropagation(); e.preventDefault(); return; }
    const x = e.target.closest?.('.nt-x');
    if (x) {
      e.stopPropagation();
      e.preventDefault();
      send({ t: 'note', op: 'del', id: id(), note: { id: x.dataset.nid } });
      return;
    }
    const s = e.target.closest?.('.nt');
    if (s) {
      e.stopPropagation();
      e.preventDefault();
      const n = list.find((x) => x.id === s.dataset.nid);
      if (n) open({ ...n });
      return;
    }
    if (e.target.closest?.('#nt-pop')) return;
    if (!pop.hidden && !e.target.closest?.('.nt-btn')) close();
    if (!mode || !roots().some((r) => r?.contains(e.target))) return;
    const t = timeAt(e);
    if (!Number.isFinite(t)) return;
    e.stopPropagation();
    e.preventDefault();
    open({ t: Math.round(t * 100) / 100 });
  }, true);

  button.classList.add('nt-btn');
  button.addEventListener('click', () => {
    mode = !mode;
    document.body.classList.toggle('nt-mode', mode);
    button.setAttribute('aria-pressed', String(mode));
    if (!mode) close();
  });

  return {
    /** Fetch the song's notes; a failure (older host, offline) just means none. */
    async load() {
      const mine = ++token;
      const sid = id();
      list = [];
      paint();
      if (!sid) return;
      try {
        const r = await fetch(`/api/notes/${sid}`);
        const data = r.ok ? await r.json() : [];
        if (mine === token && Array.isArray(data)) { list = data; paint(); }
      } catch { /* none */ }
    },
    /** A `notes` message from the hub. */
    onMessage(m) {
      if (m.t === 'noteerr') { clearTimeout(waiting); save.disabled = del.disabled = false; if (draft) meta.textContent = m.error || 'Could not save'; return; }
      if (m.t === 'notes' && m.id === id() && Array.isArray(m.notes)) { list = m.notes; paint(); if (draft && waiting) close(); }
    },
    paint,
    close,
  };
}
