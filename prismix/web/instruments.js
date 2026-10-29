// Small instrument glyphs for song lists: one per kind that has a tab, however many parts (lead, rhythm…) it has.
const S = (d) => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const KINDS = {
  guitar: ['Guitar tab', S('<path d="M14.5 9.5l6-6M18 3l3 3"/><path d="M13 7.5c-1.5-1-3.2-.4-3.8 1.1-.3.8-.9 1.3-1.7 1.4-2.6.4-4 3-3 5.4 1 2.5 3.8 4 6.3 3 1.7-.7 3-2.2 3.1-4 .1-.9.6-1.5 1.3-1.9 1.3-.7 1.6-2.4.5-3.300z"/><circle cx="9.5" cy="14.5" r=".9" fill="currentColor"/>')],
  bass: ['Bass tab', S('<path d="M15 9l5.5-5.500M18.5 2.500l3 3"/><path d="M13.5 8c-1.3-.8-2.8-.2-3.3 1.2-.5 1.3-1.5 1.8-2.6 2-2.3.5-3 3.2-1.6 5.2 1.4 2 4.3 2.7 6.1 1.3 1.2-1 1.6-2.3 1.7-3.4.1-1 .7-1.6 1.5-2.1 1.1-.8 1-2.3-.2-2.900z"/><path d="M8.5 15.500l2-2"/>')],
  keys: ['Keys tab', S('<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M8 18v-5M12 18v-5M16 18v-5M6.5 6v6M10 6v6M14 6v6M17.5 6v6" />')],
  drums: ['Drums tab', S('<ellipse cx="12" cy="13" rx="8" ry="3.5"/><path d="M4 13v4c0 1.9 3.6 3.5 8 3.500s8-1.6 8-3.500v-4M8 3.500l5 6.500M16 3.500l-5 6.5"/>')],
  other: ['Other tab', S('<path d="M9 18V6l10-2v12"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>')],
};

/** A row of instrument glyphs for the kinds in `kinds`; null when the song has no tabs. */
export function instrumentMarks(kinds) {
  const list = (kinds || []).filter((k) => KINDS[k]);
  if (!list.length) return null;
  const box = document.createElement('span');
  box.className = 'instr';
  for (const k of list) {
    const i = document.createElement('i');
    i.title = KINDS[k][0];
    i.setAttribute('role', 'img');
    i.setAttribute('aria-label', KINDS[k][0]);
    i.innerHTML = KINDS[k][1]; // static markup above
    box.append(i);
  }
  return box;
}
