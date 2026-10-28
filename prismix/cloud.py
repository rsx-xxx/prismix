"""Band Cloud client: sign in as the admin, publish processed songs, read the committed library.

The cloud is a private store with one admin token (publish) and read-only member tokens. Songs stay invisible
until their commit. This module holds the credential in the user data folder, talks to the API with it, and
proxies reads so the desktop page never needs the token.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

from . import analysis, export, library, tabdata, tagging
from .config import DATA_DIR, MODELS

CRED_FILE = DATA_DIR / "band_cloud.json"
PROBE_ID = "AAAAAAAAAAA"  # a well-formed id that is never published: the admin check only reads the status code
PART_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
STEM_FORMAT = "m4a128"  # AAC plays in every phone browser; 128k keeps a six-stem song near 25 MB
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TIMEOUT = httpx.Timeout(30.0, read=600.0)


class CloudError(Exception):
    """A failure whose message is safe to show to the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse_credential(text: str, url: str = "") -> tuple[str, str]:
    """(origin, token) from an invite link, a link plus token, or a bare token with the address typed separately."""
    text, url = text.strip(), url.strip()
    token = text
    base = url
    if "://" in text:
        parts = urlsplit(text)
        token = (parse_qs(parts.query).get("t") or [""])[0].strip()
        base = base or f"{parts.scheme}://{parts.netloc}"
    if not base:
        raise CloudError("Type the cloud address")
    if "://" not in base:
        base = "https://" + base
    parts = urlsplit(base)
    host = parts.hostname or ""
    if not host or parts.scheme not in ("https", "http") or (parts.scheme == "http" and host not in LOCAL_HOSTS):
        raise CloudError("The cloud address must be https")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
        raise CloudError("That token does not look right")
    return f"{parts.scheme}://{parts.netloc}", token


def valid_path(rel: str) -> bool:
    parts = rel.split("/")
    return 1 <= len(parts) <= 3 and all(PART_RE.match(p) for p in parts)


# ── credential ──

def load_credential() -> tuple[str, str] | None:
    try:
        data = json.loads(CRED_FILE.read_text())
        return data["url"], data["token"]
    except (OSError, ValueError, KeyError):
        return None


def save_credential(url: str, token: str) -> None:
    CRED_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CRED_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"url": url, "token": token}, f)
    os.replace(tmp, CRED_FILE)


def clear_credential() -> None:
    CRED_FILE.unlink(missing_ok=True)


# ── API ──

@dataclass
class Cloud:
    url: str
    token: str
    transport: httpx.BaseTransport | None = None  # tests plug a fake server in here

    def __post_init__(self) -> None:
        self.http = httpx.Client(base_url=self.url, headers={"Authorization": f"Bearer {self.token}"}, timeout=TIMEOUT,
                                 transport=self.transport)

    def close(self) -> None:
        self.http.close()

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(method, path, **kw)
        except httpx.HTTPError:
            raise CloudError("The cloud is not answering", 502) from None
        if r.status_code == 401:
            raise CloudError("The cloud refused this token", 401)
        if r.status_code == 403:
            raise CloudError("This token cannot publish: use the admin token", 403)
        if r.status_code == 429:
            raise CloudError("Too many failed attempts, try again in a few minutes", 429)
        return r

    def index(self) -> list[dict]:
        r = self.request("GET", "/api/v1/index.json")
        if r.status_code != 200:
            raise CloudError(f"The cloud answered {r.status_code}", 502)
        return r.json().get("songs", [])

    def manifest(self, song_id: str) -> dict | None:
        r = self.request("GET", f"/api/v1/songs/{song_id}/manifest.json")
        return r.json() if r.status_code == 200 else None

    def verify_admin(self) -> None:
        """Raises unless the token may publish. An empty commit is refused as malformed for the admin and as forbidden for a member."""
        self.index()
        r = self.http.post(f"/api/v1/songs/{PROBE_ID}/commit", json={})
        if r.status_code == 403:
            raise CloudError("That is a member token. Publishing needs the admin token", 403)
        if r.status_code != 400:
            raise CloudError(f"The cloud answered {r.status_code}", 502)

    def put_file(self, song_id: str, rel: str, data: bytes, sha: str) -> None:
        r = self.request("PUT", f"/api/v1/songs/{song_id}/files/{rel}", content=data, headers={"X-Sha256": sha})
        if r.status_code != 200:
            raise CloudError(f"Upload of {rel} failed ({r.status_code})", 502)

    def commit(self, song_id: str, body: dict) -> dict:
        r = self.request("POST", f"/api/v1/songs/{song_id}/commit", json=body)
        if r.status_code == 409:
            raise CloudError("The cloud did not receive every file, try again", 409)
        if r.status_code != 200:
            raise CloudError(f"Commit failed ({r.status_code})", 502)
        return r.json()

    def delete(self, song_id: str) -> bool:
        return self.request("DELETE", f"/api/v1/songs/{song_id}").status_code == 200


def connect(url: str, token: str) -> Cloud:
    origin, token = parse_credential(token, url)
    cloud = Cloud(origin, token)
    try:
        cloud.verify_admin()
    except CloudError:
        cloud.close()
        raise
    return cloud


def current() -> Cloud | None:
    cred = load_credential()
    return Cloud(*cred) if cred else None


# ── publishing ──

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def song_files(video_id: str, model: str):
    """Yields (path, produce) for what a cloud song holds: the full mix, each stem, and a cover. `produce` renders lazily."""
    meta = library.read_meta(video_id, model)
    if not meta:
        raise CloudError("That song is not processed yet", 404)
    names = [s["name"] for s in meta["stems"]]
    if not all(PART_RE.match(n) for n in names):
        raise CloudError("Unusable stem name")

    def render(stems: list[str]):
        return lambda: export.render(export.ExportSpec(video_id, model, stems, format=STEM_FORMAT))[0]

    yield "mix.m4a", render(names)
    if len(names) > 1:
        for n in names:
            yield f"stems/{n}.m4a", render([n])
    _, cover = tagging.for_track(video_id)
    thumb = library.video_dir(video_id) / "thumb.jpg"
    art = cover if cover and cover.is_file() else thumb if thumb.is_file() else None
    if art:
        yield "cover.jpg", lambda: art.read_bytes()
    # What the phone's player needs for chords, lyrics and tab. Each is optional: a song without them still plays.
    if analysis.read_structure(video_id):
        yield "structure.json", lambda: (library.video_dir(video_id) / "structure.json").read_bytes()
    if (library.video_dir(video_id) / "lyrics.json").is_file():
        yield "lyrics.json", lambda: (library.video_dir(video_id) / "lyrics.json").read_bytes()
    try:
        tab = tabdata.lookup(video_id)
    except (tabdata.TabDataError, ValueError, OSError):
        tab = None
    if tab and tab.get("found"):
        yield "tab.json", lambda: json.dumps(tab, ensure_ascii=False, separators=(",", ":")).encode()


def publish(cloud: Cloud, video_id: str, model: str, progress=lambda step, done, total: None) -> dict:
    """Render, upload and commit one song. Files the cloud already has with the same hash are not sent again."""
    if model not in MODELS:
        raise CloudError("Unknown model")
    info = library.read_info(video_id) or {}
    meta = library.read_meta(video_id, model) or {}
    found, _ = tagging.for_track(video_id)
    previous = {f["path"]: f["sha256"] for f in (cloud.manifest(video_id) or {}).get("files", [])}
    plan = list(song_files(video_id, model))
    files = []
    for i, (rel, produce) in enumerate(plan):
        progress(rel, i, len(plan))
        data = produce()
        digest = sha256(data)
        if previous.get(rel) != digest:
            cloud.put_file(video_id, rel, data, digest)
        files.append({"path": rel, "sha256": digest, "bytes": len(data)})
    progress("commit", len(plan), len(plan))
    return cloud.commit(video_id, {
        "id": video_id,
        "title": found.get("title") or info.get("title", ""),
        "artist": found.get("artist") or info.get("channel", ""),
        "duration": float(info.get("duration") or 0),
        "model": model,
        "stems": [s["name"] for s in meta.get("stems", [])] if len(meta.get("stems", [])) > 1 else [],
        "files": files,
    })


class Publisher:
    """Runs publishes in the background and remembers how each one went, so the page can poll."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, cloud: Cloud, video_id: str, model: str) -> str:
        with self.lock:
            for jid, j in self.jobs.items():
                if j["videoId"] == video_id and j["state"] == "running":
                    return jid
            self.jobs = {k: v for k, v in self.jobs.items() if v["state"] == "running" or time.time() - v["at"] < 600}
            jid = uuid.uuid4().hex[:12]
            job = self.jobs[jid] = {"id": jid, "videoId": video_id, "model": model, "state": "running", "step": "", "done": 0, "total": 0, "at": time.time()}
        threading.Thread(target=self._run, args=(job, cloud), daemon=True).start()
        return jid

    def _run(self, job: dict, cloud: Cloud) -> None:
        def progress(step, done, total):
            job.update(step=step, done=done, total=total)

        try:
            job["result"] = publish(cloud, job["videoId"], job["model"], progress)
            job["state"] = "done"
        except (CloudError, export.ExportError, ValueError) as e:
            job.update(state="error", error=str(e))
        except Exception:  # noqa: BLE001 - never leave the page polling a job that died
            job.update(state="error", error="Publishing failed")
        finally:
            job["at"] = time.time()
            cloud.close()

    def get(self, jid: str) -> dict | None:
        return self.jobs.get(jid)
