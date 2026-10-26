// External links open in Zen on the host machine (the server launches it); remote clients and failures fall back to a normal tab.
export function initLinks() {
  document.addEventListener('click', (e) => {
    const a = e.target.closest?.('a[href]');
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    let url;
    try { url = new URL(a.href, location.href); } catch { return; }
    if (!/^https?:$/.test(url.protocol) || url.origin === location.origin) return;
    e.preventDefault();
    fetch(`/api/open-link?url=${encodeURIComponent(url.href)}`, { method: 'POST' })
      .then((r) => { if (!r.ok) throw new Error(r.status); })
      .catch(() => window.open(url.href, '_blank', 'noopener'));
  });
}
