// The studio is four blocks (video, stems, lyrics, chords): hide any of them and see which one is being followed.

const $ = (id) => document.getElementById(id);
const KEY = 'stems:blocks-hidden';
const NAMES = { video: 'Video', stems: 'Stems', lyrics: 'Lyrics', chords: 'Chords' };
const nodes = { video: $('video'), stems: $('stems'), lyrics: $('lyrics'), chords: $('score') };
const own = ['stems', 'lyrics', 'chords']; // video visibility belongs to video.js

export function createBlocks() {
  let hidden = new Set();
  try { hidden = new Set(JSON.parse(localStorage.getItem(KEY) || '[]').filter((k) => own.includes(k))); } catch { /* ignore */ }
  const chips = {};

  const videoShown = () => !nodes.video.hidden;
  const shown = (id) => (id === 'video' ? videoShown() : !hidden.has(id));
  const setVideo = (on) => { if (on !== videoShown() && !$('video-btn').hidden) $('video-btn').click(); };

  function apply() {
    own.forEach((id) => nodes[id].classList.toggle('off', hidden.has(id)));
    try { localStorage.setItem(KEY, JSON.stringify([...hidden])); } catch { /* ignore */ }
    sync();
    document.dispatchEvent(new CustomEvent('blocks:change'));
  }

  function toggle(id) {
    if (id === 'video') { setVideo(!videoShown()); return; }
    hidden.has(id) ? hidden.delete(id) : hidden.add(id);
    apply();
  }
  function showAll() {
    hidden.clear();
    apply();
  }

  /** The block the user is looking at: the one with the most area on screen. */
  function leader() {
    const top = 0, bottom = innerHeight - ($('dock').hidden ? 0 : $('dock').offsetHeight);
    let best = null, area = 0;
    for (const id of ['stems', 'lyrics', 'chords']) {
      const n = nodes[id];
      if (n.hidden || n.offsetParent === null) continue;
      const r = n.getBoundingClientRect();
      const a = Math.max(0, Math.min(r.bottom, bottom) - Math.max(r.top, top)) * r.width;
      if (a > area) { area = a; best = id; }
    }
    return best;
  }
  function sync() {
    const lead = leader();
    const visible = Object.keys(nodes).filter((k) => k !== 'video' && !nodes[k].hidden && !nodes[k].classList.contains('off'));
    // lyrics and chords read as one block when both are on screen, so they are framed together
    const both = visible.includes('lyrics') && visible.includes('chords') && (lead === 'lyrics' || lead === 'chords');
    const framed = (id) => visible.length > 1 && (id === lead || (both && (id === 'lyrics' || id === 'chords')));
    for (const id of Object.keys(nodes)) {
      if (id !== 'video') nodes[id].classList.toggle('tracked', framed(id));
      const c = chips[id];
      c.setAttribute('aria-pressed', String(shown(id)));
      c.classList.toggle('lit', id !== 'video' && framed(id));
    }
  }

  // toolbar
  const bar = $('blockbar');
  for (const id of Object.keys(NAMES)) {
    const b = Object.assign(document.createElement('button'), { type: 'button', className: 'chip', textContent: NAMES[id], title: `Show or hide ${NAMES[id].toLowerCase()}` });
    b.addEventListener('click', () => toggle(id));
    chips[id] = b;
    bar.append(b);
  }
  const all = Object.assign(document.createElement('button'), { type: 'button', className: 'btn small ghost', title: 'Show every block', 'aria-label': 'Show every block' });
  all.dataset.ico = 'eye';
  all.addEventListener('click', showAll);
  bar.append(all);

  // per-block header buttons
  for (const id of Object.keys(NAMES)) {
    const head = nodes[id].querySelector('.block-head');
    if (id === 'video') continue; // the video block has its own close button
    const b = Object.assign(document.createElement('button'), {
      type: 'button', className: 'btn small ghost blk', title: `Hide ${NAMES[id].toLowerCase()}`,
    });
    b.setAttribute('aria-label', b.title);
    b.dataset.ico = 'eyeoff';
    b.addEventListener('click', () => toggle(id));
    head.append(b);
  }

  let queued = 0;
  const later = () => { if (!queued) queued = requestAnimationFrame(() => { queued = 0; sync(); }); };
  addEventListener('scroll', later, { passive: true });
  addEventListener('resize', later);
  new MutationObserver(later).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['hidden'] });
  apply();

  return { toggle, showAll };
}
