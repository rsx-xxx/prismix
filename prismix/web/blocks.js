// The studio is five blocks (video, stems, lyrics, chords, tabs): hide any of them and see which one is being followed.

const $ = (id) => document.getElementById(id);
const KEY = 'stems:blocks-hidden';
const SIDE_KEY = 'stems:side';
const NAMES = { video: 'Video', stems: 'Stems', lyrics: 'Lyrics', chords: 'Chords', tabs: 'Tabs' };
const nodes = { video: $('video'), stems: $('stems'), lyrics: $('lyrics'), chords: $('score'), tabs: $('tabview') };
const own = ['stems', 'lyrics', 'chords', 'tabs']; // video visibility belongs to video.js

export function createBlocks() {
  let hidden = new Set();
  try { hidden = new Set(JSON.parse(localStorage.getItem(KEY) || '[]').filter((k) => own.includes(k))); } catch { /* ignore */ }
  const chips = {};
  // Chords and Tabs share the box beside the lyrics: one of them is shown, the other is swapped out.
  let side = localStorage.getItem(SIDE_KEY) === 'tabs' ? 'tabs' : 'chords';
  const tabsOn = () => !nodes.tabs.hidden;
  const isOut = (id) => tabsOn() && (id === 'chords' || id === 'tabs') && id !== side;
  const setSide = (id) => {
    side = id;
    try { localStorage.setItem(SIDE_KEY, id); } catch { /* ignore */ }
    hidden.delete(id);
    apply();
  };

  const videoShown = () => !nodes.video.hidden;
  const shown = (id) => (id === 'video' ? videoShown() : !hidden.has(id) && !isOut(id));
  const setVideo = (on) => { if (on !== videoShown() && !$('video-btn').hidden) $('video-btn').click(); };

  function apply() {
    own.forEach((id) => { nodes[id].classList.toggle('off', hidden.has(id)); nodes[id].classList.toggle('out', isOut(id)); });
    for (const w of swaps) { w.hidden = !tabsOn(); [...w.children].forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.side === side))); }
    try { localStorage.setItem(KEY, JSON.stringify([...hidden])); } catch { /* ignore */ }
    sync();
    document.dispatchEvent(new CustomEvent('blocks:change'));
  }

  function toggle(id) {
    if (id === 'video') { setVideo(!videoShown()); return; }
    if ((id === 'chords' || id === 'tabs') && isOut(id)) { setSide(id); return; }
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
    for (const id of ['stems', 'lyrics', 'chords', 'tabs']) {
      const n = nodes[id];
      if (n.hidden || n.offsetParent === null) continue;
      const r = n.getBoundingClientRect();
      const a = Math.max(0, Math.min(r.bottom, bottom) - Math.max(r.top, top)) * r.width;
      if (a > area) { area = a; best = id; }
    }
    return best;
  }
  function sync() {
    const nowOut = own.filter((id) => nodes[id].classList.contains('out')).join();
    if (nowOut !== own.filter(isOut).join()) { apply(); return; }
    const lead = leader();
    const visible = Object.keys(nodes).filter((k) => k !== 'video' && !nodes[k].hidden && !nodes[k].classList.contains('off') && !nodes[k].classList.contains('out'));
    // lyrics and chords read as one block when both are on screen, so they are framed together
    const both = visible.includes('lyrics') && visible.includes('chords') && (lead === 'lyrics' || lead === 'chords');
    const framed = (id) => visible.length > 1 && (id === lead || (both && (id === 'lyrics' || id === 'chords')));
    $('panes').classList.toggle('solo', ['lyrics', 'chords', 'tabs'].filter((k) => nodes[k].offsetParent !== null).length < 2);
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

  // the Chords | Tabs switch in both heads
  const swaps = [];
  for (const id of ['chords', 'tabs']) {
    const w = Object.assign(document.createElement('span'), { className: 'seg-btns swap', hidden: true });
    w.setAttribute('role', 'group');
    w.setAttribute('aria-label', 'Show chords or tabs');
    for (const k of ['chords', 'tabs']) {
      const b = Object.assign(document.createElement('button'), { type: 'button', className: 'btn small ghost', textContent: NAMES[k] });
      b.dataset.side = k;
      b.addEventListener('click', () => setSide(k));
      w.append(b);
    }
    nodes[id].querySelector('.block-head').append(w);
    swaps.push(w);
  }

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

  return { toggle, showAll, side: () => side };
}
