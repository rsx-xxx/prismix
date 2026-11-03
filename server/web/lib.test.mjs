import test from 'node:test';
import assert from 'node:assert/strict';
import * as L from './lib.js';

const TOKEN = 'A'.repeat(43);
const SHA = 'a'.repeat(64);

test('parseInvite takes links and bare tokens', () => {
  assert.equal(L.parseInvite(`https://band.example/join?t=${TOKEN}`), TOKEN);
  assert.equal(L.parseInvite(`  /join?t=${TOKEN}  `), TOKEN);
  assert.equal(L.parseInvite(TOKEN), TOKEN);
  assert.equal(L.parseInvite('https://band.example/join'), null);
  assert.equal(L.parseInvite('short'), null);
  assert.equal(L.parseInvite(''), null);
  assert.equal(L.parseInvite(null), null);
});

test('joinUrl encodes the token', () => {
  assert.equal(L.joinUrl('a b'), '/join?t=a%20b');
});

test('fileUrl pins the hash and rejects bad paths', () => {
  assert.equal(L.fileUrl('dQw4w9WgXcQ', 'stems/vocals.m4a', SHA), `/files/dQw4w9WgXcQ/stems/vocals.m4a?v=${SHA}`);
  assert.equal(L.fileUrl('dQw4w9WgXcQ', 'mix.m4a'), '/files/dQw4w9WgXcQ/mix.m4a');
  assert.equal(L.fileUrl('dQw4w9WgXcQ', 'mix.m4a', 'nothex'), '/files/dQw4w9WgXcQ/mix.m4a');
  for (const rel of ['../x.m4a', 'a/b/c/d.m4a', '.hidden', 'a//b', '']) assert.throws(() => L.fileUrl('dQw4w9WgXcQ', rel));
  assert.throws(() => L.fileUrl('short', 'mix.m4a'));
});

test('bundleUrl validates the id', () => {
  assert.equal(L.bundleUrl('dQw4w9WgXcQ'), '/api/v1/songs/dQw4w9WgXcQ/bundle.zip');
  assert.throws(() => L.bundleUrl('../etc'));
});

test('formatting', () => {
  assert.equal(L.fmtTime(65), '1:05');
  assert.equal(L.fmtTime(3725), '1:02:05');
  assert.equal(L.fmtTime(-3), '0:00');
  assert.equal(L.fmtTime(NaN), '0:00');
  assert.equal(L.fmtBytes(2 * 1048576), '2.0 MB');
  assert.equal(L.fmtBytes(10), '1 KB');
});

const songs = [
  { id: 'a', artist: 'Zed', title: 'Alpha' },
  { id: 'b', artist: 'Abba', title: 'Waterloo' },
  { id: 'c', artist: 'Abba', title: 'Dancing Queen' },
];

test('sortSongs orders by artist then title without mutating', () => {
  assert.deepEqual(L.sortSongs(songs).map((s) => s.id), ['c', 'b', 'a']);
  assert.equal(songs[0].id, 'a');
});

test('filterSongs matches every word in any order', () => {
  assert.deepEqual(L.filterSongs(songs, 'queen abba').map((s) => s.id), ['c']);
  assert.equal(L.filterSongs(songs, '').length, 3);
  assert.equal(L.filterSongs(songs, 'nope').length, 0);
});

test('neighbour wraps around', () => {
  assert.equal(L.neighbour(songs, 'a', 1), 1);
  assert.equal(L.neighbour(songs, 'a', -1), 2);
  assert.equal(L.neighbour(songs, 'c', 1), 0);
  assert.equal(L.neighbour(songs, 'x', 1), -1);
  assert.equal(L.neighbour([], 'a', 1), -1);
});

test('tracksOf finds mix, stems and cover', () => {
  const m = {
    stems: ['vocals', 'drums', 'ghost'],
    files: [{ path: 'mix.m4a' }, { path: 'stems/vocals.m4a' }, { path: 'stems/drums.m4a' }, { path: 'cover.jpg' }],
  };
  const t = L.tracksOf(m);
  assert.equal(t.mix.path, 'mix.m4a');
  assert.deepEqual(t.stems.map((s) => s.name), ['vocals', 'drums']);
  assert.equal(t.cover.path, 'cover.jpg');
  assert.deepEqual(L.tracksOf({}), { mix: null, stems: [], cover: null });
  assert.equal(L.tracksOf({ files: [{ path: 'mix.opus' }] }).mix.path, 'mix.opus');
});

test('drifted and audible', () => {
  assert.equal(L.drifted(10, 10.1), false);
  assert.equal(L.drifted(10, 10.3), true);
  const stems = ['v', 'd', 'b'];
  assert.deepEqual(L.audible(stems, new Set(['d']), new Set()), [true, false, true]);
  assert.deepEqual(L.audible(stems, new Set(['d']), new Set(['d'])), [false, true, false]);
});

const reply = (status, body) => async (path, opts) => ({ ok: status < 400, status, json: async () => body, path, opts });

test('fetchJson sends the cookie and throws ApiError on failure', async () => {
  let seen;
  const f = async (p, o) => { seen = [p, o]; return { ok: true, status: 200, json: async () => ({ ok: 1 }) }; };
  assert.deepEqual(await L.fetchJson('/x', f), { ok: 1 });
  assert.equal(seen[1].credentials, 'same-origin');
  await assert.rejects(L.fetchJson('/x', reply(401)), (e) => e instanceof L.ApiError && e.status === 401);
});

test('loadIndex and loadManifest hit the API paths', async () => {
  const paths = [];
  const f = async (p) => { paths.push(p); return { ok: true, status: 200, json: async () => ({ songs: [{ id: 'a' }] }) }; };
  assert.deepEqual(await L.loadIndex(f), [{ id: 'a' }]);
  await L.loadManifest('dQw4w9WgXcQ', f);
  assert.deepEqual(paths, ['/api/v1/index.json', '/api/v1/songs/dQw4w9WgXcQ/manifest.json']);
  assert.deepEqual(await L.loadIndex(reply(200, {})), []);
  assert.throws(() => L.loadManifest('bad', f));
});
