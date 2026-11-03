// Pure helpers for the band cloud player: URL building, invite parsing, API calls. No DOM, so node can test them.
export const ID_RE = /^[A-Za-z0-9_-]{11}$/;
export const SEGMENT_RE = /^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$/;
export const TOKEN_RE = /^[A-Za-z0-9_-]{32,128}$/;
export const SHA_RE = /^[0-9a-f]{64}$/;

export class ApiError extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

/** Token from an invite link (`.../join?t=<token>`) or a bare pasted token; null when it does not look like one. */
export function parseInvite(text) {
  const raw = String(text || '').trim();
  let token = raw;
  if (raw.includes('://') || raw.startsWith('/') || raw.includes('?')) {
    try { token = new URL(raw, 'https://band.invalid').searchParams.get('t') || ''; } catch { return null; }
  }
  return TOKEN_RE.test(token) ? token : null;
}

export const joinUrl = (token) => `/join?t=${encodeURIComponent(token)}`;

/** `/files/<id>/<path>`, with `?v=<sha256>` when known so the browser can cache it for good. */
export function fileUrl(id, rel, sha) {
  const parts = String(rel).split('/');
  if (!ID_RE.test(id) || parts.length > 3 || !parts.every((p) => SEGMENT_RE.test(p))) throw new Error('bad file path');
  const v = sha && SHA_RE.test(sha) ? `?v=${sha}` : '';
  return `/files/${id}/${parts.map(encodeURIComponent).join('/')}${v}`;
}

export function bundleUrl(id) {
  if (!ID_RE.test(id)) throw new Error('bad song id');
  return `/api/v1/songs/${id}/bundle.zip`;
}

export function fmtTime(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

export function fmtBytes(n) {
  n = Number(n) || 0;
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

const label = (s) => `${s.artist || ''} ${s.title || ''}`.toLowerCase();

export function sortSongs(songs) {
  return [...songs].sort((a, b) => label(a).localeCompare(label(b)));
}

export function filterSongs(songs, query) {
  const words = String(query || '').toLowerCase().split(/\s+/).filter(Boolean);
  return songs.filter((s) => words.every((w) => label(s).includes(w)));
}

/** Index of the song `delta` steps from `id`, wrapping around; -1 when `id` is not in the list. */
export function neighbour(list, id, delta) {
  const i = list.findIndex((s) => s.id === id);
  return i < 0 || !list.length ? -1 : (i + delta + list.length) % list.length;
}

/** The full mix and the per-stem files a manifest lists, ready for playing or downloading. */
export function tracksOf(manifest) {
  const files = Array.isArray(manifest?.files) ? manifest.files : [];
  const byPath = new Map(files.map((f) => [f.path, f]));
  const mix = byPath.get('mix.m4a') || files.find((f) => /^mix\./.test(f.path)) || null;
  const stems = (manifest?.stems || [])
    .map((name) => ({ name, file: byPath.get(`stems/${name}.m4a`) }))
    .filter((s) => s.file && SEGMENT_RE.test(s.name));
  const cover = files.find((f) => /^cover\./.test(f.path)) || null;
  return { mix, stems, cover };
}

/** True when a follower stem has drifted far enough from the master to need a seek. */
export const drifted = (master, follower, tolerance = 0.15) => Math.abs(master - follower) > tolerance;

/** Which stems are audible: the soloed ones if any, otherwise every one that is not muted. */
export function audible(stems, muted, solo) {
  return stems.map((s) => (solo.size ? solo.has(s) : !muted.has(s)));
}

export async function fetchJson(path, fetchImpl = fetch) {
  const r = await fetchImpl(path, { credentials: 'same-origin', headers: { accept: 'application/json' } });
  if (!r.ok) throw new ApiError(r.status);
  return r.json();
}

export const loadIndex = async (fetchImpl) => ((await fetchJson('/api/v1/index.json', fetchImpl)).songs || []);

export function loadManifest(id, fetchImpl) {
  if (!ID_RE.test(id)) throw new Error('bad song id');
  return fetchJson(`/api/v1/songs/${id}/manifest.json`, fetchImpl);
}
