#!/usr/bin/env python3
"""Band cloud: auth gate and publish API for a read-mostly song store.

Caddy serves every static byte and asks this service, through forward_auth,
whether a request may proceed. Uploads, commits, deletes and bundle.zip are
proxied here directly. Stdlib only, so the container needs no pip install.

Layout under BAND_DATA:
  index.json                 member-visible song list, rewritten atomically
  songs/<id>/manifest.json   committed manifest
  songs/<id>/<path>          committed files
  songs/<id>/.zipinfo.json   CRC32 per file, for streaming bundle.zip
  staging/<id>/<path>        uploads awaiting commit, never served
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import http.server
import json
import logging
import os
import re
import secrets
import shutil
import socketserver
import struct
import sys
import tempfile
import threading
import time
import zlib
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

SONG_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
SEGMENT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_EXT = {".opus", ".m4a", ".mp3", ".flac", ".json", ".jpg", ".png", ".txt"}
RESERVED_PATHS = {"manifest.json"}
MAX_DEPTH = 3
MAX_FILES = 512
MAX_UPLOAD = 300 * 1024 * 1024
MAX_JSON_BODY = 1024 * 1024
MAX_MEMBERS = 500
INVITES_PER_HOUR = 20
# Bundles are plain (non-zip64) archives; offsets must fit in 32 bits.
MAX_BUNDLE = 0xFFFF0000
COOKIE_NAME = "band_session"
COOKIE_MAX_AGE = 365 * 24 * 3600
CHUNK = 1024 * 1024

log = logging.getLogger("bandauth")


class BadPath(ValueError):
    pass


class CommitError(Exception):
    def __init__(self, status, payload):
        super().__init__(payload.get("error", "commit failed"))
        self.status = status
        self.payload = payload


# ---------------------------------------------------------------- validation


def valid_song_id(song_id):
    return isinstance(song_id, str) and bool(SONG_ID_RE.match(song_id))


def validate_rel_path(rel):
    """Return path segments for a song-relative file path or raise BadPath."""
    if not isinstance(rel, str) or not rel:
        raise BadPath("empty path")
    if len(rel) > 400 or rel.startswith("/") or "\\" in rel or "\x00" in rel or "%" in rel:
        raise BadPath("illegal characters")
    parts = rel.split("/")
    if len(parts) > MAX_DEPTH:
        raise BadPath("path too deep")
    for part in parts:
        if part in ("", ".", "..") or not SEGMENT_RE.match(part):
            raise BadPath("illegal segment")
    suffix = os.path.splitext(parts[-1])[1]
    if suffix not in ALLOWED_EXT:
        raise BadPath("extension not allowed")
    return parts


def safe_join(base, parts):
    """Join under base, refusing any existing symlink on the way down."""
    base = Path(base)
    current = base
    if current.is_symlink():
        raise BadPath("symlink")
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise BadPath("symlink")
    resolved = current.resolve(strict=False)
    if not resolved.is_relative_to(base.resolve(strict=False)):
        raise BadPath("escapes song directory")
    return current


def parse_public_path(path):
    """Classify a request path. Returns (kind, song_id, rel) or raises BadPath."""
    if "%" in path or "\\" in path or "\x00" in path or "//" in path:
        raise BadPath("illegal characters")
    if any(seg in (".", "..") for seg in path.split("/")):
        raise BadPath("dot segment")
    if path == "/api/v1/index.json":
        return "index", None, None
    if path == "/api/v1/playlists.json":
        return "playlists", None, None
    if path == "/api/v1/members":
        return "members", None, None
    match = re.match(r"^/api/v1/songs/([^/]+)/(manifest\.json|bundle\.zip|commit)$", path)
    if match:
        if not valid_song_id(match.group(1)):
            raise BadPath("bad song id")
        kind = {"manifest.json": "manifest", "bundle.zip": "bundle", "commit": "commit"}[match.group(2)]
        return kind, match.group(1), None
    match = re.match(r"^/api/v1/songs/([^/]+)/files/(.+)$", path)
    if match:
        if not valid_song_id(match.group(1)):
            raise BadPath("bad song id")
        validate_rel_path(match.group(2))
        return "upload", match.group(1), match.group(2)
    match = re.match(r"^/api/v1/songs/([^/]+)$", path)
    if match:
        if not valid_song_id(match.group(1)):
            raise BadPath("bad song id")
        return "song", match.group(1), None
    match = re.match(r"^/files/([^/]+)/(.+)$", path)
    if match:
        if not valid_song_id(match.group(1)):
            raise BadPath("bad song id")
        validate_rel_path(match.group(2))
        return "file", match.group(1), match.group(2)
    if path.startswith("/api/") or path.startswith("/files/"):
        raise BadPath("unknown api path")
    if any(seg.startswith(".") for seg in path.split("/") if seg):
        raise BadPath("hidden path")
    return "web", None, None


# ------------------------------------------------------------------- helpers


def sha256_hex(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def file_digest(path):
    sha, crc, size = hashlib.sha256(), 0, 0
    with open(path, "rb") as handle:
        while block := handle.read(CHUNK):
            sha.update(block)
            crc = zlib.crc32(block, crc)
            size += len(block)
    return sha.hexdigest(), crc & 0xFFFFFFFF, size


def atomic_write_json(path, payload, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with _suppress():
            os.unlink(tmp)
        raise


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return exc[0] is not None and issubclass(exc[0], OSError)


# -------------------------------------------------------------------- tokens


class TokenStore:
    """Hashed tokens in one JSON file; reloaded when the CLI changes it."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._stamp = None
        self._data = {"admin_sha256": "", "members": []}

    def _load(self):
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._stamp, self._data = None, {"admin_sha256": "", "members": []}
            return
        stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if stamp != self._stamp:
            self._data = json.loads(self.path.read_text() or "{}")
            self._data.setdefault("admin_sha256", "")
            self._data.setdefault("members", [])
            self._stamp = stamp

    def _save(self):
        atomic_write_json(self.path, self._data, mode=0o600)
        self._stamp = None

    def lookup(self, token):
        """Return (name, role) for a valid token, else None. Constant-time per entry."""
        if not isinstance(token, str) or not TOKEN_RE.match(token):
            return None
        digest = sha256_hex(token)
        with self._lock:
            self._load()
            admin = self._data.get("admin_sha256") or ""
            members = list(self._data["members"])
        found = None
        if admin and hmac.compare_digest(digest, admin):
            found = ("admin", "admin")
        for member in members:
            if hmac.compare_digest(digest, member.get("sha256", "")) and found is None:
                found = (member["name"], "member")
        return found

    def add_member(self, name, invited_by=None):
        if not NAME_RE.match(name or ""):
            raise ValueError("name must match [A-Za-z0-9_.-]{1,32}")
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._load()
            if any(m["name"] == name for m in self._data["members"]):
                raise ValueError(f"member {name!r} already exists; revoke it first")
            if len(self._data["members"]) >= MAX_MEMBERS:
                raise ValueError("member limit reached; revoke unused invites")
            entry = {"name": name, "sha256": sha256_hex(token), "created": int(time.time())}
            if invited_by:
                entry["invited_by"] = invited_by
            self._data["members"].append(entry)
            self._save()
        return token

    def invite(self, label, invited_by):
        """A member token for someone the caller invites. The label is only a hint for the admin; the name stays unique."""
        base = re.sub(r"[^A-Za-z0-9_.-]", "", label or "")[:24] or "guest"
        for _ in range(5):
            name = f"{base}-{secrets.token_hex(3)}"
            try:
                return name, self.add_member(name, invited_by)
            except ValueError as exc:
                if "already exists" not in str(exc):
                    raise
        raise ValueError("could not pick a free name")

    def revoke(self, name):
        with self._lock:
            self._load()
            before = len(self._data["members"])
            self._data["members"] = [m for m in self._data["members"] if m["name"] != name]
            changed = len(self._data["members"]) != before
            if changed:
                self._save()
        return changed

    def members(self):
        with self._lock:
            self._load()
            return [{"name": m["name"], "created": m.get("created")} for m in self._data["members"]]

    def has_admin(self):
        with self._lock:
            self._load()
            return bool(self._data.get("admin_sha256"))

    def set_admin_hash(self, digest):
        if not SHA256_RE.match(digest or ""):
            raise ValueError("admin hash must be 64 lowercase hex characters")
        with self._lock:
            self._load()
            if self._data.get("admin_sha256") != digest:
                self._data["admin_sha256"] = digest
                self._save()

    def rotate_admin(self):
        token = secrets.token_urlsafe(32)
        self.set_admin_hash(sha256_hex(token))
        return token


# ---------------------------------------------------------------- rate limit


class RateLimiter:
    """Sliding window of failed attempts per client IP."""

    def __init__(self, limit=10, window=300.0, clock=time.monotonic, max_keys=10000):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self._hits = {}
        self._lock = threading.Lock()

    def _prune(self, key, now):
        hits = self._hits.get(key)
        if hits is None:
            return None
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if not hits:
            del self._hits[key]
            return None
        return hits

    def blocked(self, key):
        with self._lock:
            hits = self._prune(key, self.clock())
            return hits is not None and len(hits) >= self.limit

    def fail(self, key):
        with self._lock:
            now = self.clock()
            if len(self._hits) >= self.max_keys:
                for stale in list(self._hits)[: self.max_keys // 10]:
                    self._prune(stale, now)
            self._hits.setdefault(key, deque()).append(now)


# ------------------------------------------------------------------- library


class Library:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.songs = self.root / "songs"
        self.staging = self.root / "staging"
        self.index_path = self.root / "index.json"
        self.lock = threading.Lock()
        self.songs.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self.regenerate_index()

    def live_manifest(self, song_id):
        path = self.songs / song_id / "manifest.json"
        if path.is_symlink() or not path.is_file():
            return None
        return json.loads(path.read_text())

    def put_file(self, song_id, rel, stream, length, expected_sha):
        """Stream an upload into staging. Returns the file size. Raises on mismatch."""
        if not valid_song_id(song_id):
            raise BadPath("bad song id")
        parts = validate_rel_path(rel)
        if rel in RESERVED_PATHS:
            raise BadPath("reserved path")
        song_dir = self.staging / song_id
        song_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_join(song_dir, parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        safe_join(song_dir, parts)
        fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
        sha, remaining = hashlib.sha256(), length
        try:
            with os.fdopen(fd, "wb") as handle:
                while remaining > 0:
                    block = stream.read(min(CHUNK, remaining))
                    if not block:
                        raise CommitError(400, {"error": "short body"})
                    handle.write(block)
                    sha.update(block)
                    remaining -= len(block)
                handle.flush()
                os.fsync(handle.fileno())
            if not hmac.compare_digest(sha.hexdigest(), expected_sha):
                raise CommitError(422, {"error": "sha256 mismatch", "path": rel})
            os.chmod(tmp, 0o644)
            os.replace(tmp, dest)
        except BaseException:
            with _suppress():
                os.unlink(tmp)
            raise
        return length

    def _parse_manifest(self, song_id, body):
        if not isinstance(body, dict):
            raise CommitError(400, {"error": "manifest must be an object"})
        if body.get("id", song_id) != song_id:
            raise CommitError(400, {"error": "manifest id does not match url"})
        files = body.get("files")
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            raise CommitError(400, {"error": f"files must be a list of 1..{MAX_FILES} entries"})
        seen, clean = set(), []
        for entry in files:
            if not isinstance(entry, dict):
                raise CommitError(400, {"error": "file entry must be an object"})
            rel, digest, size = entry.get("path"), entry.get("sha256"), entry.get("bytes")
            try:
                validate_rel_path(rel)
            except BadPath as exc:
                raise CommitError(400, {"error": f"bad path: {exc}", "path": rel}) from None
            if rel in RESERVED_PATHS or rel in seen:
                raise CommitError(400, {"error": "duplicate or reserved path", "path": rel})
            if not isinstance(digest, str) or not SHA256_RE.match(digest):
                raise CommitError(400, {"error": "sha256 must be 64 lowercase hex", "path": rel})
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise CommitError(400, {"error": "bytes must be a non-negative integer", "path": rel})
            seen.add(rel)
            clean.append({"path": rel, "sha256": digest, "bytes": size})
        stems = body.get("stems", [])
        if not isinstance(stems, list):
            raise CommitError(400, {"error": "stems must be a list"})
        models = body.get("models")
        model = body.get("model")
        if models is None:
            models = [model] if model else []
        if not isinstance(models, list):
            raise CommitError(400, {"error": "models must be a list"})
        duration = body.get("duration")
        if duration is not None and not isinstance(duration, (int, float)):
            raise CommitError(400, {"error": "duration must be a number"})
        return {
            "files": clean,
            "model": model,
            "models": models,
            "stems": stems,
            "title": str(body.get("title") or song_id)[:300],
            "artist": str(body.get("artist") or "")[:300],
            "duration": duration,
        }

    def commit(self, song_id, body, now=None):
        if not valid_song_id(song_id):
            raise BadPath("bad song id")
        parsed = self._parse_manifest(song_id, body)
        with self.lock:
            staged_dir, live_dir = self.staging / song_id, self.songs / song_id
            old = self.live_manifest(song_id)
            sources, missing, mismatch, crcs = {}, [], [], {}
            for entry in parsed["files"]:
                parts = entry["path"].split("/")
                chosen = None
                for base in (staged_dir, live_dir):
                    try:
                        candidate = safe_join(base, parts)
                    except BadPath:
                        continue
                    if candidate.is_file() and not candidate.is_symlink():
                        chosen = candidate
                        break
                if chosen is None:
                    missing.append(entry["path"])
                    continue
                digest, crc, size = file_digest(chosen)
                if size != entry["bytes"] or not hmac.compare_digest(digest, entry["sha256"]):
                    mismatch.append(entry["path"])
                    continue
                sources[entry["path"]] = chosen
                crcs[entry["path"]] = crc
            if missing or mismatch:
                raise CommitError(409, {"error": "manifest does not match uploads", "missing": missing, "mismatch": mismatch})

            rev = int((old or {}).get("rev", 0)) + 1
            manifest = {
                "id": song_id,
                "rev": rev,
                "title": parsed["title"],
                "artist": parsed["artist"],
                "duration": parsed["duration"],
                "updated": int(now if now is not None else time.time()),
                "model": parsed["model"],
                "models": parsed["models"],
                "stems": parsed["stems"],
                "files": parsed["files"],
            }
            build = Path(tempfile.mkdtemp(prefix=f".{song_id}.new.", dir=self.songs))
            try:
                for rel, src in sources.items():
                    target = build.joinpath(*rel.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(src, target)
                    except OSError:
                        shutil.copy2(src, target)
                atomic_write_json(build / ".zipinfo.json", crcs)
                atomic_write_json(build / "manifest.json", manifest)
                os.chmod(build, 0o755)
                for sub in build.rglob("*"):
                    if sub.is_dir():
                        os.chmod(sub, 0o755)
                if live_dir.exists():
                    retired = Path(tempfile.mkdtemp(prefix=f".{song_id}.old.", dir=self.songs))
                    os.replace(live_dir, retired / "song")
                    os.replace(build, live_dir)
                    shutil.rmtree(retired, ignore_errors=True)
                else:
                    os.replace(build, live_dir)
            except BaseException:
                shutil.rmtree(build, ignore_errors=True)
                raise
            shutil.rmtree(staged_dir, ignore_errors=True)
            self.regenerate_index()
        return manifest

    def delete(self, song_id):
        if not valid_song_id(song_id):
            raise BadPath("bad song id")
        with self.lock:
            live_dir, staged_dir = self.songs / song_id, self.staging / song_id
            existed = live_dir.exists() or staged_dir.exists()
            if live_dir.exists():
                retired = Path(tempfile.mkdtemp(prefix=f".{song_id}.del.", dir=self.songs))
                os.replace(live_dir, retired / "song")
                shutil.rmtree(retired, ignore_errors=True)
            shutil.rmtree(staged_dir, ignore_errors=True)
            self.regenerate_index()
        return existed

    def regenerate_index(self):
        songs = []
        for child in sorted(self.songs.iterdir()) if self.songs.exists() else []:
            if not valid_song_id(child.name) or child.is_symlink() or not child.is_dir():
                continue
            try:
                manifest = json.loads((child / "manifest.json").read_text())
            except (OSError, ValueError):
                continue
            songs.append(
                {
                    "id": child.name,
                    "title": manifest.get("title", child.name),
                    "artist": manifest.get("artist", ""),
                    "duration": manifest.get("duration"),
                    "updated": manifest.get("updated"),
                    "rev": manifest.get("rev"),
                    "bytes": sum(f.get("bytes", 0) for f in manifest.get("files", [])),
                    "models": manifest.get("models", []),
                }
            )
        songs.sort(key=lambda s: (-(s["updated"] or 0), s["id"]))
        atomic_write_json(self.index_path, {"songs": songs})
        return songs

    def bundle_plan(self, song_id):
        """Return (entries, total_length) for a stored zip, or None if not committed."""
        manifest = self.live_manifest(song_id)
        if manifest is None:
            return None
        song_dir = self.songs / song_id
        crcs = json.loads((song_dir / ".zipinfo.json").read_text())
        mtime = manifest.get("updated") or int(time.time())
        entries = []
        for entry in manifest["files"]:
            path = safe_join(song_dir, entry["path"].split("/"))
            entries.append((f"{song_id}/{entry['path']}", path, entry["bytes"], crcs[entry["path"]]))
        manifest_path = song_dir / "manifest.json"
        _, crc, size = file_digest(manifest_path)
        entries.append((f"{song_id}/manifest.json", manifest_path, size, crc))
        return entries, zip_length(entries), mtime


# ----------------------------------------------------------------- stored zip


def _dos_time(epoch):
    t = time.gmtime(max(epoch, 315532800))
    return (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2), ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday


def zip_length(entries):
    names = sum(len(name.encode()) for name, *_ in entries)
    return sum(e[2] for e in entries) + 30 * len(entries) + 46 * len(entries) + 2 * names + 22


def write_stored_zip(out, entries, mtime):
    """Write a stored (uncompressed) zip with sizes and CRCs known up front."""
    dtime, ddate = _dos_time(mtime)
    offset, central = 0, []
    for name, path, size, crc in entries:
        raw = name.encode()
        header = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0x0800, 0, dtime, ddate, crc, size, size, len(raw), 0)
        out.write(header + raw)
        written = 0
        with open(path, "rb") as handle:
            while block := handle.read(CHUNK):
                out.write(block)
                written += len(block)
        if written != size:
            raise OSError(f"{name} changed while streaming")
        central.append(
            struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0x0800, 0, dtime, ddate, crc, size, size, len(raw), 0, 0, 0, 0, 0o100644 << 16, offset)
            + raw
        )
        offset += len(header) + len(raw) + size
    cd = b"".join(central)
    out.write(cd)
    out.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(cd), offset, 0))


# ---------------------------------------------------------------------- http


class App:
    def __init__(self, data_dir, state_dir, limiter=None):
        self.library = Library(data_dir)
        self.tokens = TokenStore(Path(state_dir) / "tokens.json")
        self.limiter = limiter or RateLimiter()
        self.invites = RateLimiter(limit=INVITES_PER_HOUR, window=3600.0)  # per issuer: a leaked member token cannot mint tokens without end
        self.playlists_path = Path(state_dir) / "playlists.json"
        self.playlists_lock = threading.Lock()

    def playlists_read(self):
        """(rev, playlists) as stored; an empty store has rev ""."""
        try:
            raw = self.playlists_path.read_bytes()
            return hashlib.sha256(raw).hexdigest()[:24], json.loads(raw).get("playlists", [])
        except FileNotFoundError:
            return "", []

    def playlists_write(self, items, rev):
        """Stores `items` if the stored rev still equals `rev`; returns the new rev or None on a stale rev."""
        with self.playlists_lock:
            current, _ = self.playlists_read()
            if current != rev:
                return None
            atomic_write_json(self.playlists_path, {"playlists": items}, mode=0o600)
            return self.playlists_read()[0]


def make_handler(app):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = ""
        sys_version = ""

        def version_string(self):
            return "bandauth"

        def log_message(self, fmt, *args):
            return

        # -- plumbing

        def client_ip(self):
            forwarded = self.headers.get("X-Forwarded-For", "")
            return forwarded.split(",")[-1].strip() or self.client_address[0]

        def send_json(self, status, payload, headers=None):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            if self.close_connection:
                self.send_header("Connection", "close")  # say so: a client reusing this connection would hit a closed socket
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def credential(self):
            auth = self.headers.get("Authorization", "")
            if auth[:7].lower() == "bearer ":
                return auth[7:].strip()
            for part in self.headers.get("Cookie", "").split(";"):
                key, _, value = part.strip().partition("=")
                if key == COOKIE_NAME and value:
                    return value
            return None

        def authenticate(self):
            """Return ((name, role) | None, status). Counts failed credentials per IP."""
            ip = self.client_ip()
            if app.limiter.blocked(ip):
                return None, 429
            token = self.credential()
            if token is None:
                return None, 401
            who = app.tokens.lookup(token)
            if who is None:
                app.limiter.fail(ip)
                return None, 401
            return who, 200

        def deny(self, status, method, path):
            log.info("deny status=%s ip=%s method=%s path=%s", status, self.client_ip(), method, path)
            if self.command in ("PUT", "POST", "DELETE"):
                self.close_connection = True
            headers = {"WWW-Authenticate": 'Bearer realm="band"'} if status == 401 else {}
            if status == 429:
                headers["Retry-After"] = str(int(app.limiter.window))
            message = {401: "unauthorized", 403: "forbidden", 429: "too many failed attempts", 400: "bad path", 404: "not found", 405: "method not allowed"}
            self.send_json(status, {"error": message.get(status, "error")}, headers)

        # -- verbs

        def do_GET(self):
            self.route("GET")

        def do_HEAD(self):
            self.route("HEAD")

        def do_PUT(self):
            self.route("PUT")

        def do_POST(self):
            self.route("POST")

        def do_DELETE(self):
            self.route("DELETE")

        def route(self, method):
            path = urlsplit(self.path).path
            try:
                if path == "/healthz" and method in ("GET", "HEAD"):
                    return self.send_raw(200, b"ok", "text/plain")
                if path == "/join" and method == "GET":
                    return self.join()
                if path == "/_auth":
                    return self.forward_auth()
                return self.api(method, path)
            except BrokenPipeError:
                return None
            except Exception:
                log.exception("error method=%s path=%s", method, path)
                with _suppress():
                    self.send_json(500, {"error": "internal error"})

        def send_raw(self, status, body, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def join(self):
            ip = self.client_ip()
            if app.limiter.blocked(ip):
                return self.deny(429, "GET", "/join")
            token = (parse_qs(urlsplit(self.path).query).get("t") or [""])[0]
            who = app.tokens.lookup(token)
            if who is not None and who[1] == "admin":
                return self.send_json(403, {"error": "this is the admin token, not an invite: ask for a member link"})
            if who is None or who[1] != "member":
                app.limiter.fail(ip)
                return self.deny(401, "GET", "/join")
            log.info("join user=%s ip=%s", who[0], ip)
            if "android" in self.headers.get("User-Agent", "").lower():
                return self.join_android(token)
            return self.join_desktop(token)

        def join_desktop(self, token):
            """Desktop: hand the invite to the Prismix app; when it is not installed the cookie still opens the web player."""
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
            origin = "https://" + host
            link = f"prismix://join?o={quote(origin, safe='')}&t={quote(token, safe='')}"
            page = (
                "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Band Cloud</title><body style='font:16px sans-serif;background:#161412;color:#eee;padding:2em'>"
                f"<p>Opening Prismix…</p><p><a style='color:#ff9a3d' href=\"{html.escape(link)}\">Open in the app</a> · "
                "<a style='color:#ff9a3d' href=/>Use the web player</a></p>"
                "<script>"
                f"location.href={json.dumps(link)};"
                "var t=setTimeout(function(){location.replace('/')},2500);"
                "addEventListener('blur',function(){clearTimeout(t)});"
                "document.addEventListener('visibilitychange',function(){if(document.hidden)clearTimeout(t)});"
                "</script>"
            ).encode()
            self.send_response(200)
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={token}; Max-Age={COOKIE_MAX_AGE}; Path=/; HttpOnly; Secure; SameSite=Lax",
            )
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(page)

        def join_android(self, token):
            """Android: hand the invite to the Prismix app so it signs in on its own; the web player is the fallback."""
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
            origin = "https://" + host
            link = (
                f"intent://join?o={quote(origin, safe='')}&t={quote(token, safe='')}"
                f"#Intent;scheme=prismix;package=app.prismix.band;S.browser_fallback_url={quote(origin + '/', safe='')};end"
            )
            page = (
                "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Band Cloud</title><body style='font:16px sans-serif;background:#161412;color:#eee;padding:2em'>"
                f"<p>Opening Prismix…</p><p><a style='color:#ff9a3d' href=\"{html.escape(link)}\">Open in the app</a> · "
                "<a style='color:#ff9a3d' href=/>Use the web player</a></p>"
                f"<script>location.replace({json.dumps(link)})</script>"
            ).encode()
            self.send_response(200)
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={token}; Max-Age={COOKIE_MAX_AGE}; Path=/; HttpOnly; Secure; SameSite=Lax",
            )
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(page)

        def forward_auth(self):
            method = self.headers.get("X-Forwarded-Method", "GET").upper()
            path = urlsplit(self.headers.get("X-Forwarded-Uri", "/")).path
            decision = decide(app, method, path, *self.authenticate())
            status, who = decision
            if status != 200:
                return self.deny(status, method, path)
            log.info("access user=%s role=%s method=%s path=%s", who[0], who[1], method, path)
            self.send_response(200)
            self.send_header("X-Band-User", who[0])
            self.send_header("X-Band-Role", who[1])
            self.send_header("Content-Length", "0")
            self.end_headers()

        def api(self, method, path):
            status, who = decide(app, method, path, *self.authenticate())
            if status != 200:
                return self.deny(status, method, path)
            kind, song_id, rel = parse_public_path(path)
            if kind == "upload" and method == "PUT":
                return self.upload(who, song_id, rel)
            if kind == "commit" and method == "POST":
                return self.commit(who, song_id)
            if kind == "song" and method == "DELETE":
                existed = app.library.delete(song_id)
                log.info("delete user=%s song=%s existed=%s", who[0], song_id, existed)
                return self.send_json(200 if existed else 404, {"deleted": existed, "id": song_id})
            if kind == "bundle" and method in ("GET", "HEAD"):
                return self.bundle(who, song_id)
            if kind == "playlists" and method in ("GET", "HEAD"):
                rev, items = app.playlists_read()
                return self.send_json(200, {"rev": rev, "playlists": items}, {"ETag": f'"{rev}"'})
            if kind == "playlists" and method == "PUT":
                return self.put_playlists(who)
            if kind == "members" and method == "POST":
                return self.invite(who)
            return self.deny(404, method, path)

        def invite(self, who):
            """Any signed-in user may hand out a member link: it can play and publish, never delete."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self.send_json(411, {"error": "Content-Length required"})
            if length < 0 or length > 4096:
                self.close_connection = True
                return self.send_json(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send_json(400, {"error": "body is not valid JSON"})
            label = body.get("label") if isinstance(body, dict) else None
            if app.invites.blocked(who[0]):
                return self.deny(429, "POST", "/api/v1/members")
            try:
                name, token = app.tokens.invite(label if isinstance(label, str) else "", who[0])
            except ValueError as exc:
                return self.send_json(409, {"error": str(exc)})
            app.invites.fail(who[0])
            host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
            log.info("invite by=%s name=%s", who[0], name)
            return self.send_json(200, {"name": name, "link": f"https://{host}/join?t={token}"})

        def upload(self, who, song_id, rel):
            expected = (self.headers.get("X-Sha256") or "").strip().lower()
            self.close_connection = True
            if not SHA256_RE.match(expected):
                return self.send_json(400, {"error": "X-Sha256 header must be 64 hex characters"})
            if self.headers.get("Transfer-Encoding"):
                return self.send_json(411, {"error": "Content-Length required"})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self.send_json(411, {"error": "Content-Length required"})
            if length < 0 or length > MAX_UPLOAD:
                self.close_connection = True
                return self.send_json(413, {"error": "file too large"})
            try:
                app.library.put_file(song_id, rel, self.rfile, length, expected)
            except CommitError as exc:
                self.close_connection = exc.payload.get("error") == "short body"
                return self.send_json(exc.status, exc.payload)
            except BadPath as exc:
                self.close_connection = True
                return self.send_json(400, {"error": str(exc)})
            except OSError as exc:
                self.close_connection = True
                if exc.errno == 28:
                    return self.send_json(507, {"error": "storage quota exceeded"})
                raise
            self.close_connection = False
            log.info("upload user=%s song=%s path=%s bytes=%s", who[0], song_id, rel, length)
            return self.send_json(200, {"id": song_id, "path": rel, "sha256": expected, "bytes": length})

        def commit(self, who, song_id):
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self.send_json(411, {"error": "Content-Length required"})
            if length < 0 or length > MAX_JSON_BODY:
                self.close_connection = True
                return self.send_json(413, {"error": "manifest too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                return self.send_json(400, {"error": "manifest is not valid JSON"})
            try:
                manifest = app.library.commit(song_id, body)
            except CommitError as exc:
                return self.send_json(exc.status, exc.payload)
            log.info("commit user=%s song=%s rev=%s files=%s", who[0], song_id, manifest["rev"], len(manifest["files"]))
            return self.send_json(200, manifest)

        def put_playlists(self, who):
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self.send_json(411, {"error": "Content-Length required"})
            if length < 0 or length > MAX_JSON_BODY:
                self.close_connection = True
                return self.send_json(413, {"error": "playlists too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"null")
            except ValueError:
                return self.send_json(400, {"error": "playlists are not valid JSON"})
            items = body.get("playlists") if isinstance(body, dict) else None
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                return self.send_json(400, {"error": "body must be {\"playlists\": [objects]}"})
            match = (self.headers.get("If-Match") or "").strip()
            if match[:2] == "W/":
                match = match[2:]
            rev = app.playlists_write(items, match.strip('"'))
            if rev is None:
                current, _ = app.playlists_read()
                return self.send_json(412, {"error": "stale rev", "rev": current})
            log.info("playlists user=%s count=%s rev=%s", who[0], len(items), rev)
            return self.send_json(200, {"rev": rev, "playlists": items}, {"ETag": f'"{rev}"'})

        def bundle(self, who, song_id):
            plan = app.library.bundle_plan(song_id)
            if plan is None:
                return self.send_json(404, {"error": "not found"})
            entries, total, mtime = plan
            if total > MAX_BUNDLE:
                return self.send_json(413, {"error": "bundle exceeds 4 GiB; download files individually"})
            log.info("bundle user=%s song=%s bytes=%s", who[0], song_id, total)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(total))
            self.send_header("Content-Disposition", f'attachment; filename="{song_id}.zip"')
            self.send_header("Cache-Control", "private, no-cache")
            self.end_headers()
            if self.command != "HEAD":
                write_stored_zip(self.wfile, entries, mtime)

    return Handler


def decide(app, method, path, who, auth_status):
    """Authorization decision shared by forward_auth and direct API calls."""
    try:
        kind, _, _ = parse_public_path(path)
    except BadPath:
        return (400 if auth_status == 200 else auth_status), who
    if auth_status != 200:
        return auth_status, None
    writes = {("PUT", "upload"), ("POST", "commit"), ("DELETE", "song"), ("PUT", "playlists"), ("POST", "members")}
    if method in ("PUT", "POST", "DELETE"):
        if (method, kind) not in writes:
            return 405, who
        if method == "DELETE" and who[1] != "admin":
            return 403, who  # every member may publish; removing a song for everyone stays with the admin
        return 200, who
    if method not in ("GET", "HEAD"):
        return 405, who
    return 200, who


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(args):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    os.umask(0o022)
    app = App(args.data, args.state)
    server = Server((args.host, args.port), make_handler(app))
    log.info("listening on %s:%s", args.host, args.port)
    server.serve_forever()


# ----------------------------------------------------------------------- cli


def cli(argv=None):
    parser = argparse.ArgumentParser(prog="bandctl")
    parser.add_argument("--data", default=os.environ.get("BAND_DATA", "/data"))
    parser.add_argument("--state", default=os.environ.get("BAND_STATE", "/state"))
    parser.add_argument("--url", default=os.environ.get("BAND_PUBLIC_URL", "https://localhost"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    srv = sub.add_parser("serve")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8787)
    add = sub.add_parser("add-member")
    add.add_argument("name")
    sub.add_parser("list")
    rev = sub.add_parser("revoke")
    rev.add_argument("name")
    sub.add_parser("rotate-admin")
    sub.add_parser("set-admin-hash", help="read a sha256 hex digest from stdin")
    sub.add_parser("reindex")
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        return serve(args)
    os.umask(0o077)
    store = TokenStore(Path(args.state) / "tokens.json")
    if args.cmd == "add-member":
        token = store.add_member(args.name)
        print(f"{args.url.rstrip('/')}/join?t={token}")
    elif args.cmd == "list":
        for member in store.members():
            created = time.strftime("%Y-%m-%d %H:%M", time.gmtime(member["created"] or 0))
            print(f"{member['name']}\tcreated {created} UTC")
        print(f"admin token configured: {'yes' if store.has_admin() else 'no'}")
    elif args.cmd == "revoke":
        if not store.revoke(args.name):
            print(f"no member named {args.name!r}", file=sys.stderr)
            return 1
        print(f"revoked {args.name}")
    elif args.cmd == "rotate-admin":
        print(store.rotate_admin())
    elif args.cmd == "set-admin-hash":
        store.set_admin_hash(sys.stdin.read().strip())
        print("admin hash set")
    elif args.cmd == "reindex":
        os.umask(0o022)
        print(f"{len(Library(args.data).regenerate_index())} songs indexed")
    return 0


if __name__ == "__main__":
    sys.exit(cli())
