// Classic script loaded in <head>: applies the saved theme before first paint, then wires the toggle.
(() => {
  const KEY = 'prismix-theme';
  const ORDER = ['auto', 'dark', 'light'];
  const LABEL = { auto: 'Theme: follow system', dark: 'Theme: dark', light: 'Theme: light' };
  const ICON = {
    auto: '<path d="M12 3a9 9 0 1 0 0 18V3Z" fill="currentColor"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/>',
    dark: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" fill="currentColor"/>',
    light: '<circle cx="12" cy="12" r="4.2" fill="currentColor"/><path d="M12 2.5v2.3M12 19.2v2.3M2.5 12h2.3M19.2 12h2.3M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
  };
  const read = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
  const get = () => (ORDER.includes(read()) ? read() : 'auto');
  const root = document.documentElement;
  const apply = (mode) => (mode === 'auto' ? root.removeAttribute('data-theme') : root.setAttribute('data-theme', mode));
  apply(get());

  addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('theme-btn');
    if (!btn) return;
    const paint = () => {
      const mode = get();
      btn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">${ICON[mode]}</svg>`;
      btn.title = btn.ariaLabel = LABEL[mode];
    };
    btn.addEventListener('click', () => {
      const next = ORDER[(ORDER.indexOf(get()) + 1) % ORDER.length];
      try { localStorage.setItem(KEY, next); } catch { /* storage blocked: theme lasts for this session only */ }
      apply(next);
      paint();
    });
    paint();
  });
})();
