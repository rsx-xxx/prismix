"""Band cloud client: sign in with an invite link, publish processed songs, read the committed library.

The cloud is a private store. Every invite link lets its holder play and publish; removing a song stays with the
cloud's admin. Songs stay invisible until their commit. This module holds the credential in the user data folder,
talks to the API with it, and proxies reads so the desktop page never needs the token.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

from . import analysis, export, library, pipeline, tabdata, tagging
from .config import DATA_DIR, DEFAULT_MODEL, LIBRARY_DIR, MODELS, SAMPLE_RATE

CRED_FILE = DATA_DIR / "band_cloud.json"
PART_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
STEM_FORMAT = "m4a128"  # AAC plays in every phone browser; 128k keeps a six-stem song near 25 MB
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
TIMEOUT = httpx.Timeout(30.0, read=600.0)


class CloudError(Exception):
    """A failure whose message is safe to show to the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse_credential(text: str) -> tuple[str, str]:
    """(origin, token) from an invite link: https://host/join?t=... or prismix://join?o=https://host&t=..."""
    text = text.strip()
    parts = urlsplit(text)
    if parts.scheme not in ("https", "http", "prismix"):
        raise CloudError("Paste the invite link")
    query = parse_qs(parts.query)
    token = (query.get("t") or [""])[0].strip()
    base = (query.get("o") or [""])[0].strip() if parts.scheme == "prismix" else f"{parts.scheme}://{parts.netloc}"
    base_parts = urlsplit(base)
    host = base_parts.hostname or ""
    if not host or base_parts.scheme not in ("https", "http") or (base_parts.scheme == "http" and host not in LOCAL_HOSTS):
        raise CloudError("The cloud address must be https")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
        raise CloudError("That link does not look right")
    return f"{base_parts.scheme}://{base_parts.netloc}", token


# What each known cloud is called and how it looks. The cloud itself only has an address, so this is keyed by it.
BANDS = {"band.deltaclans.win": {"name": "TigerKiss", "logo": "tigerkiss.png"}}


def band_for(host: str) -> dict:
    return BANDS.get(host, {"name": "Band cloud", "logo": ""})


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
            raise CloudError("The cloud refused this link", 401)
        if r.status_code == 403:
            raise CloudError("Only the cloud admin can do that", 403)
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


def connect(link: str) -> Cloud:
    origin, token = parse_credential(link)
    cloud = Cloud(origin, token)
    try:
        cloud.index()  # a refused link fails here, before it is saved
    except CloudError:
        cloud.close()
        raise
    return cloud


def accept_link(link: str) -> None:
    """Sign in from a clicked or pasted invite link."""
    cl = connect(link)
    save_credential(cl.url, cl.token)
    cl.close()


def current() -> Cloud | None:
    cred = load_credential()
    return Cloud(*cred) if cred else None


# ── downloading: a cloud song becomes an ordinary library song ──

def _fetch(cloud: Cloud, song_id: str, entry: dict, dest) -> None:
    """Streams one cloud file to disk and checks it against the manifest's hash."""
    digest = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with cloud.http.stream("GET", f"/files/{song_id}/{entry['path']}") as r:
            if r.status_code == 401:
                raise CloudError("The cloud refused this link", 401)
            if r.status_code != 200:
                raise CloudError(f"Download of {entry['path']} failed ({r.status_code})", 502)
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    digest.update(chunk)
                    f.write(chunk)
    except httpx.HTTPError:
        raise CloudError("The cloud is not answering", 502) from None
    if digest.hexdigest() != entry["sha256"]:
        raise CloudError(f"{entry['path']} arrived damaged, try again", 502)


def download(cloud: Cloud, song_id: str, progress=lambda step, done, total: None) -> dict:
    """Fetch a committed song and lay it out like a locally processed one, so it opens in the full studio.
    The phone-sized AAC stems stand in for the lossless ones; nothing needs processing on this computer."""
    manifest = cloud.manifest(song_id)
    if not manifest:
        raise CloudError("That song is not in the cloud", 404)
    model = manifest.get("model") if manifest.get("model") in MODELS else DEFAULT_MODEL
    if library.video_dir(song_id).exists():  # already in the library under any model: never overwritten or merged
        return {"id": song_id, "model": model}
    files = {f["path"]: f for f in manifest.get("files", []) if valid_path(f.get("path", "")) and isinstance(f.get("sha256"), str)}
    names = [n for n in manifest.get("stems", []) if PART_RE.match(n)]
    # A single-stem song has only its mix, which is then the one stem.
    parts = [(n, f"stems/{n}.m4a") for n in names] or [("mix", "mix.m4a")]
    if any(rel not in files for _, rel in parts):
        raise CloudError("The cloud copy of this song is incomplete", 502)

    tmp = LIBRARY_DIR / f"{song_id}.partial"  # not a video id, so the library list never shows it
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        wanted = [rel for _, rel in parts] + [r for r in ("cover.jpg", "structure.json", "lyrics.json", "tab.json") if r in files]
        for i, rel in enumerate(wanted):
            progress(rel, i, len(wanted))
            _fetch(cloud, song_id, files[rel], tmp / "files" / rel)
        progress("finish", len(wanted), len(wanted))
        src = tmp / "files"
        mdir = tmp / model
        mdir.mkdir(parents=True)
        samples = {}
        for name, rel in parts:
            (src / rel).replace(mdir / f"{name}.m4a")
            samples[name] = pipeline.decode(mdir / f"{name}.m4a")
        scale = max(float(abs(s).max()) for s in samples.values()) or 1.0
        entries = [{"name": n, "file": f"{n}.m4a", "peaks": [int(round(float(v) * 255)) for v in pipeline.peaks(s) / scale]}
                   for n, s in samples.items()]
        duration = float(manifest.get("duration") or 0) or len(next(iter(samples.values()))[0]) / SAMPLE_RATE
        info = {"id": song_id, "title": library.clean_title(manifest.get("title") or song_id), "channel": manifest.get("artist") or "",
                "duration": duration, "added": int(time.time()), "fromCloud": True}
        for rel, name in (("cover.jpg", "thumb.jpg"), ("structure.json", "structure.json"), ("lyrics.json", "lyrics.json"), ("tab.json", "tabdata.json")):
            if (src / rel).is_file():
                (src / rel).replace(tmp / name)
        shutil.rmtree(src)
        (tmp / "info.json").write_text(json.dumps(info, ensure_ascii=False))
        if setup := library.clean_setup(manifest.get("setup")):
            (tmp / "setup.json").write_text(json.dumps(setup))
        (mdir / "meta.json").write_text(json.dumps({**info, "model": model, "sampleRate": SAMPLE_RATE, "stems": entries}, ensure_ascii=False))
        final = library.video_dir(song_id)
        if final.exists():  # the song is already here under another model: keep what it has, add this model
            for item in tmp.iterdir():
                if not (final / item.name).exists():
                    item.replace(final / item.name)
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tmp.rename(final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"id": song_id, "model": model}


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
        "title": library.clean_title(found.get("title") or info.get("title", "")),
        "artist": found.get("artist") or info.get("channel", ""),
        "duration": float(info.get("duration") or 0),
        "model": model,
        "stems": [s["name"] for s in meta.get("stems", [])] if len(meta.get("stems", [])) > 1 else [],
        "files": files,
        "setup": library.read_setup(video_id),
    })


def push_setup(video_id: str, setup: dict) -> bool:
    """Share a changed playing setup with the cloud copy of the song, if there is one. Nothing is rendered or uploaded:
    the committed manifest is sent again with the new setup. Quiet when offline or not signed in."""
    cloud = current()
    if not cloud:
        return False
    try:
        manifest = cloud.manifest(video_id)
        if not manifest:
            return False
        keep = ("id", "title", "artist", "duration", "model", "stems", "files")
        cloud.commit(video_id, {**{k: manifest[k] for k in keep if k in manifest}, "setup": setup})
        return True
    except (CloudError, httpx.HTTPError):
        return False
    finally:
        cloud.close()


class Publisher:
    """Runs publishes in the background and remembers how each one went, so the page can poll."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, cloud: Cloud, video_id: str, model: str = "", kind: str = "publish") -> str:
        with self.lock:
            for jid, j in self.jobs.items():
                if j["videoId"] == video_id and j["kind"] == kind and j["state"] == "running":
                    return jid
            self.jobs = {k: v for k, v in self.jobs.items() if v["state"] == "running" or time.time() - v["at"] < 600}
            jid = uuid.uuid4().hex[:12]
            job = self.jobs[jid] = {"id": jid, "kind": kind, "videoId": video_id, "model": model, "state": "running", "step": "", "done": 0, "total": 0, "at": time.time(),
                                    "stop": threading.Event()}
        threading.Thread(target=self._run, args=(job, cloud), daemon=True).start()
        return jid

    def _run(self, job: dict, cloud: Cloud) -> None:
        def progress(step, done, total):
            if job["stop"].is_set():
                raise CloudError("Cancelled")  # files already sent stay invisible: a song shows up only at its commit
            job.update(step=step, done=done, total=total)

        try:
            if job["kind"] == "download":
                job["result"] = download(cloud, job["videoId"], progress)
            else:
                job["result"] = publish(cloud, job["videoId"], job["model"], progress)
            job["state"] = "done"
        except (CloudError, export.ExportError, ValueError) as e:
            job.update(state="error", error=str(e))
        except Exception:  # noqa: BLE001 - never leave the page polling a job that died
            job.update(state="error", error="Publishing failed")
        finally:
            job["at"] = time.time()
            cloud.close()

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if not job or job["state"] != "running":
            return False
        job["stop"].set()
        return True

    def get(self, jid: str) -> dict | None:
        return self.jobs.get(jid)
