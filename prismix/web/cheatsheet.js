// Shortcut cheatsheet: a corner pop-up toggled by tapping ⌘ (Ctrl elsewhere) or `?`.
const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
const MOD = MAC ? 'Meta' : 'Control';

export const GROUPS = [
  ['Playback', [
    [['Space'], 'Play / pause'],
    [['←', '→'], 'Jump 5 s', ['Shift', '15 s']],
    [['0'], 'Back to start'],
  ]],
  ['Stems', [
    [['1', '–', '6'], 'Mute / unmute', ['Shift', 'solo']],
    [['X'], 'Clear solos, then mutes'],
  ]],
  ['Loop', [
    [['A', 'B', 'C'], 'Start / end / clear'],
    [['Shift', '+ drag'], 'Select on waveform'],
  ]],
  ['Tempo & key', [
    [['[', ']'], 'Tempo −/+ 5%'],
    [[',', '.'], 'Key −/+ 1 semitone'],
    [['R'], 'Reset tempo', ['Shift', 'reset key']],
  ]],
  ['View', [
    [['V'], 'Video'],
    [['H'], 'Chords'],
    [['L'], 'Lyrics'],
    [['T'], 'Tabs'],
    [['F'], 'Follow playback'],
  ]],
  ['Other', [
    [['D'], 'Download'],
    [['?'], 'This sheet'],
  ]],
];

const kbd = (t) => Object.assign(document.createElement('kbd'), { textContent: t });

function render(grid) {
  grid.replaceChildren(...GROUPS.map(([title, rows]) => {
    const g = document.createElement('section');
    g.append(Object.assign(document.createElement('h4'), { textContent: title }));
    for (const [keys, label, alt] of rows) {
      const r = document.createElement('div');
      r.className = 'cheat';
      const k = document.createElement('span');
      k.className = 'keys';
      k.append(...keys.map(kbd));
      const l = document.createElement('span');
      l.append(label);
      if (alt) l.append(' · ', kbd(alt[0]), ` ${alt[1]}`);
      r.append(k, l);
      g.append(r);
    }
    return g;
  }));
}

/** Tap ⌘ (Ctrl elsewhere) on its own, or press `?`, to show or hide the sheet; Esc, × or a click elsewhere also hides it. */
export function initCheatsheet(root, blocked = () => false) {
  const grid = root.querySelector('#cheats-grid');
  render(grid);
  let lone = false; // the modifier went down with nothing else pressed since
  const open = () => { root.hidden = false; };
  const close = () => { root.hidden = true; };
  const toggle = () => (root.hidden ? open() : close());

  window.addEventListener('keydown', (e) => {
    if (e.key === MOD) { if (!e.repeat) lone = true; return; }
    lone = false;
    if (e.key === 'Escape' && !root.hidden) { close(); e.stopPropagation(); }
  }, true);
  window.addEventListener('keyup', (e) => {
    if (e.key !== MOD || !lone) return;
    lone = false;
    if (!blocked()) toggle();
  });
  window.addEventListener('blur', () => { lone = false; });
  window.addEventListener('pointerdown', (e) => { lone = false; if (!root.hidden && !root.contains(e.target) && !e.target.closest('#help-btn')) close(); }, true);
  root.querySelector('#cheats-close').addEventListener('click', close);
  return { toggle, isOpen: () => !root.hidden };
}
