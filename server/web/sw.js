// Keeps the player shell and the last song list available offline. Audio, images and downloads always go to the network.
const CACHE = 'band-shell-v1';
const SHELL = ['/', '/app.js', '/lib.js', '/app.css', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png', '/apple-touch-icon.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL.map((u) => new Request(u, { credentials: 'same-origin' })))).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

const save = (request, response) => {
  // never keep a refusal: a revoked member must not be served a stale list
  if (response.ok) caches.open(CACHE).then((c) => c.put(request, response.clone()));
  return response;
};

self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== location.origin || request.headers.has('range')) return;
  const path = url.pathname;
  if (path.startsWith('/files/') || path.endsWith('/bundle.zip') || path === '/join' || path === '/healthz') return;

  if (path.startsWith('/api/v1/')) {
    event.respondWith(fetch(request).then((r) => save(request, r)).catch(() => caches.match(request).then((hit) => hit || Response.error())));
    return;
  }
  if (request.mode === 'navigate' && path !== '/') return;
  const key = request.mode === 'navigate' ? '/' : request;
  event.respondWith(caches.match(key, { ignoreSearch: true }).then((hit) => {
    const fresh = fetch(request.mode === 'navigate' ? new Request('/', { credentials: 'same-origin' }) : request).then((r) => save(key, r));
    if (hit) { fresh.catch(() => {}); return hit; }
    return fresh;
  }));
});
