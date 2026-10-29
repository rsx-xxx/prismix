from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles

from . import (
    REPO,
    __version__,
    analysis,
    browser,
    client,
    cloud,
    export,
    lan,
    library,
    lyrics,
    markers,
    mashup,
    notes,
    original,
    pipeline,
    playlists,
    spotify,
    tabdata,
    tabs,
    updates,
    uploads,
)
from . import band as band_mod
from .band import Band
from .config import DEFAULT_CHANNEL, DEFAULT_MODEL, LIBRARY_DIR, MIX_MODEL, MODELS, WEB_DIR
from .jobs import JobManager
from .prefetch import Prefetcher

log = logging.getLogger(__name__)


class ProcessRequest(BaseModel):
    url: str
    model: str = DEFAULT_MODEL
    tasks: Literal["all", "stems", "chords"] = "all"
    urgent: bool = False


class PlaylistItem(BaseModel):
    id: str
    title: str = ""
    channel: str = ""
    duration: float = 0


class PlaylistCreate(BaseModel):
    name: str = Field(default="Playlist", max_length=200)
    items: list[PlaylistItem] = Field(default_factory=list, max_length=500)


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    order: list[str] | None = Field(default=None, max_length=500)


class PlaylistImport(BaseModel):
    url: str = Field(min_length=8, max_length=400)
    model: str = DEFAULT_MODEL
    tasks: Literal["all", "stems"] = "all"


class JoinRequest(BaseModel):
    host: str = Field(min_length=1, max_length=300)


class BandConfig(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    token: str | None = Field(default=None, max_length=32)
    locked: bool | None = None


def band_qr(url: str) -> str:
    """SVG QR for the phone link, empty when sharing is off or segno is missing."""
    if not url:
        return ""
    try:
        import io

        import segno
    except ImportError:
        return ""
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=1, border=2, xmldecl=False, svgns=True)
    svg = buf.getvalue().decode()
    # segno sizes the SVG in pixels without a viewBox, so CSS sizing would crop it instead of scaling it
    return re.sub(r'width="(\d+)" height="(\d+)"', r'viewBox="0 0 \1 \2"', svg, count=1)


class TabsRequest(BaseModel):
    video_id: str = Field(alias="videoId")
    url: str | None = Field(default=None, max_length=300)


class TabEditRequest(BaseModel):
    bars: list[dict] = Field(max_length=1500)


class MashupRequest(BaseModel):
    lyrics: str | None = Field(default=None, max_length=200)
    chords: str | None = Field(default=None, max_length=200)


class AnalyzeRequest(BaseModel):
    video_id: str = Field(alias="videoId")
    model: str
    force: bool = False


class ExportRequest(BaseModel):
    video_id: str = Field(alias="videoId")
    model: str
    stems: list[str] = Field(min_length=1, max_length=8)
    separate: bool = False
    tempo: float = 1.0
    semitones: float = 0.0
    format: str = "flac"
    volumes: dict[str, float] = {}


class UploadRename(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SongSetup(BaseModel):
    semitones: int | None = Field(default=None, ge=-12, le=12)
    chord_key: int | None = Field(default=None, alias="chordKey", ge=-12, le=12)
    capo: int | None = Field(default=None, ge=0, le=11)
    tuning: str | None = Field(default=None, max_length=40)


class CloudLogin(BaseModel):
    link: str = Field(max_length=2000)


class CloudPublish(BaseModel):
    video_id: str = Field(alias="videoId")
    model: str


class CloudDownload(BaseModel):
    video_id: str = Field(alias="videoId")


class OriginalRequest(BaseModel):
    video_id: str = Field(alias="videoId")
    kind: Literal["audio", "video"]
    format: str = "original"
    height: int | None = None


class MediaFiles(StaticFiles):
    """Stems never change once written, so let the browser cache them hard."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith((".flac", ".m4a")):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp


class WebFiles(StaticFiles):
    """The UI is served to phones that keep the page open for days: always revalidate."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def create_app() -> FastAPI:
    jobs = JobManager()
    prefetcher = Prefetcher(jobs)

    def busy() -> str | None:
        """Why the app must not restart right now, or None."""
        if any(not j.finished for j in jobs.listing()):
            return "splitting songs"
        if prefetcher.status().get("active"):
            return "preparing songs"
        if any(j["state"] == "running" for j in list(app.state.publisher.jobs.values())):
            return "syncing the band cloud"
        return None

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        jobs.start()
        prefetcher.start()
        updates.auto(busy, lambda: os._exit(0))
        yield
        await prefetcher.stop()
        await jobs.stop()

    _titles: dict[str, str | None] = {}
    app = FastAPI(title="Prismix", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.band = band = Band()
    app.state.upstream = None  # the host this desktop has joined, if any

    @app.middleware("http")
    async def lan_guard(request: Request, call_next):
        # Phones on the LAN may read the library and play it, never change or delete it.
        if not band_mod.is_loopback(request.client.host if request.client else None):
            path = request.url.path
            if path == "/" and request.method == "GET":  # a bare https://prismix.local/ is the phone page
                return RedirectResponse("/remote", status_code=307)
            if request.method not in ("GET", "HEAD", "OPTIONS") or path.startswith("/api/band"):
                return Response("Read-only for remote clients", status_code=403)
            if band.token and (path.startswith("/api/") or path.startswith("/media/")):
                given = request.headers.get("x-band-token") or request.query_params.get("k")
                if not band.token_ok(given):
                    return Response("PIN required", status_code=401)
        return await call_next(request)

    @app.middleware("http")
    async def no_cross_site(request: Request, call_next):
        # This computer's own API changes things (delete, open links, join a host): a web page open in another tab
        # must not be able to drive it, so a browser's cross-site write is refused. Non-browser callers send no Origin.
        if (request.method not in ("GET", "HEAD", "OPTIONS")
                and band_mod.is_loopback(request.client.host if request.client else None)
                and not band_mod.same_origin(request.headers.get("origin"), request.headers.get("host"))):
            return Response("Cross-site request", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def joined(request: Request, call_next):
        # Joined to another host: this window's own data calls go there; LAN visitors still get this computer.
        up = app.state.upstream
        if up and band_mod.is_loopback(request.client.host if request.client else None):
            path = request.url.path
            if path == "/" and request.method == "GET":
                return RedirectResponse("/remote", status_code=307)
            if client.wants(path):
                return await client.forward(request, up)
        return await call_next(request)

    @app.middleware("http")
    async def fresh_api(request: Request, call_next):
        # a phone browser or the app's WebView must never replay an old library list
        response = await call_next(request)
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.websocket("/ws")
    async def band_ws(ws: WebSocket):
        # A page on another site could otherwise connect to this computer's socket and act as the host
        if band_mod.is_loopback(ws.client.host if ws.client else None) \
                and not band_mod.same_origin(ws.headers.get("origin"), ws.headers.get("host")):
            await ws.close(code=4403)
            return
        up = app.state.upstream
        if up and band_mod.is_loopback(ws.client.host if ws.client else None):
            await client.relay_ws(ws, up)
            return
        await band.serve(ws)

    @app.get("/api/band")
    def band_status():
        # the app's own scheme makes the camera open the Prismix app directly; https opens any phone's browser
        app_url = re.sub(r"^https:", "prismix:", band.share_url)
        return {**band.status(), "name": lan.display_name(), "url": band.share_url, "qr": band_qr(band.share_url), "appQr": band_qr(app_url)}

    @app.post("/api/band")
    def band_config(body: BandConfig):
        band.configure(token=body.token, locked=body.locked)
        if body.name is not None:
            lan.set_display_name(body.name)
        return band_status()

    def own_window(request: Request) -> None:
        """Joining a host is for the window on this computer, and never for a page in another tab or site."""
        if not band_mod.is_loopback(request.client.host if request.client else None):
            raise HTTPException(403, "Only on this computer")
        if not band_mod.same_origin(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "Cross-site request")

    @app.get("/api/client")
    def client_status(request: Request):
        own_window(request)
        up = app.state.upstream
        return up.status() if up else {"connected": False}

    @app.get("/api/client/hosts")
    def client_hosts(request: Request):
        own_window(request)
        return client.discover()

    @app.post("/api/client/connect")
    async def client_connect(body: JoinRequest, request: Request):
        own_window(request)
        try:
            up = await client.connect(body.host)
        except client.ConnectError as e:
            raise HTTPException(400, str(e)) from None
        old, app.state.upstream = app.state.upstream, up
        if old:
            await old.http.aclose()
        return up.status()

    @app.post("/api/client/disconnect")
    async def client_disconnect(request: Request):
        own_window(request)
        old, app.state.upstream = app.state.upstream, None
        if old:
            await old.http.aclose()
        return {"connected": False}

    # ── Band cloud: the cloud credential lives on this computer, so only its own window may use these ──
    app.state.publisher = cloud.Publisher()

    def cloud_for(request: Request) -> cloud.Cloud:
        own_window(request)
        cl = cloud.current()
        if not cl:
            raise HTTPException(401, "Sign in to the band cloud first")
        return cl

    @app.get("/api/cloud")
    def cloud_status(request: Request):
        own_window(request)
        cred = cloud.load_credential()
        host = urlparse(cred[0]).netloc if cred else ""
        return {"signedIn": bool(cred), "host": host, "band": cloud.band_for(host)}

    @app.post("/api/cloud/login")
    def cloud_login(body: CloudLogin, request: Request):
        own_window(request)
        try:
            cloud.accept_link(body.link)
        except cloud.CloudError as e:
            raise HTTPException(e.status, str(e)) from None
        return cloud_status(request)

    @app.post("/api/cloud/logout")
    def cloud_logout(request: Request):
        own_window(request)
        cloud.clear_credential()
        return {"signedIn": False, "host": "", "band": cloud.band_for("")}

    @app.get("/api/cloud/index")
    def cloud_index(request: Request):
        cl = cloud_for(request)
        try:
            return {"songs": cl.index()}
        except cloud.CloudError as e:
            raise HTTPException(e.status, str(e)) from None
        finally:
            cl.close()

    @app.get("/api/cloud/files/{video_id}/{rel:path}")
    def cloud_file(video_id: str, rel: str, request: Request):
        cl = cloud_for(request)
        if not library.VIDEO_ID_RE.match(video_id) or not cloud.valid_path(rel):
            cl.close()
            raise HTTPException(404, "Not found")
        fwd = {k: v for k, v in request.headers.items() if k in ("range", "if-range")}
        try:
            r = cl.http.send(cl.http.build_request("GET", f"/files/{video_id}/{rel}", headers=fwd), stream=True)
        except Exception:  # noqa: BLE001
            cl.close()
            raise HTTPException(502, "The cloud is not answering") from None
        keep = ("content-type", "content-length", "content-range", "accept-ranges", "etag")
        return StreamingResponse(r.iter_raw(), status_code=r.status_code, headers={k: v for k, v in r.headers.items() if k in keep},
                                 background=BackgroundTask(lambda: (r.close(), cl.close())))

    @app.post("/api/cloud/publish")
    def cloud_publish(req: CloudPublish, request: Request):
        cl = cloud_for(request)
        try:
            ready = library.read_meta(req.video_id, req.model)
        except ValueError:
            ready = None
        if not ready:
            cl.close()
            raise HTTPException(404, "That song is not processed yet")
        return {"job": app.state.publisher.start(cl, req.video_id, req.model)}

    @app.post("/api/cloud/download")
    def cloud_download(req: CloudDownload, request: Request):
        cl = cloud_for(request)
        if not library.VIDEO_ID_RE.match(req.video_id):
            cl.close()
            raise HTTPException(404, "Not found")
        return {"job": app.state.publisher.start(cl, req.video_id, kind="download")}

    @app.get("/api/cloud/publish/{job_id}")
    def cloud_publish_status(job_id: str, request: Request):
        own_window(request)
        job = app.state.publisher.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        return {k: v for k, v in job.items() if k not in ("at", "stop")}

    @app.post("/api/cloud/publish/{job_id}/cancel")
    def cloud_publish_cancel(job_id: str, request: Request):
        own_window(request)
        if not app.state.publisher.cancel(job_id):
            raise HTTPException(404, "That upload already finished")
        return {"ok": True}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    @app.get("/remote/hello")
    def remote_hello():
        # Unauthenticated on purpose: lets the phone app tell a Prismix host from any other web server while scanning.
        return {"app": "prismix", "version": __version__, "id": lan.host_id(), "name": lan.display_name(), "domain": lan.host_name(), "pin": bool(band.token)}

    @app.get("/remote")
    def remote():
        return FileResponse(WEB_DIR / "remote.html", headers={"Cache-Control": "no-cache"})

    @app.get("/mini")
    def mini():
        return FileResponse(WEB_DIR / "mini.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/version")
    def app_version():
        return {"version": __version__, "url": f"{REPO}/releases/tag/v{__version__}"}

    @app.get("/api/update")
    def update_info():
        return {**updates.check(), **updates.status()}

    @app.post("/api/update/install")
    def update_install(request: Request):
        own_window(request)
        try:
            return updates.install(lambda: os._exit(0), busy)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    @app.post("/api/update/restart")
    def update_restart(request: Request):
        own_window(request)
        try:
            return updates.restart(lambda: os._exit(0), busy)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/api/models")
    def models():
        return {
            "default": DEFAULT_MODEL,
            "models": [
                {"id": m.id, "label": m.label, "hint": m.hint, "stems": list(m.stems), "legacy": m.legacy}
                for m in MODELS.values()
            ],
        }

    @app.get("/api/library")
    def list_library():
        return library.list_library()

    @app.get("/api/library/{video_id}/setup")
    def get_setup(video_id: str):
        try:
            return library.read_setup(video_id)
        except ValueError:
            raise HTTPException(404, "Not found") from None

    @app.put("/api/library/{video_id}/setup")
    async def put_setup(video_id: str, body: SongSetup):
        """Remember how the song is played (key, capo, tuning); songs in the Band cloud share it with everyone."""
        try:
            if not library.read_info(video_id):
                raise HTTPException(404, "Not found")
            setup = library.write_setup(video_id, body.model_dump(by_alias=True, exclude_none=True))
        except ValueError:
            raise HTTPException(404, "Not found") from None
        await asyncio.to_thread(cloud.push_setup, video_id, setup)
        return setup

    @app.get("/api/library/{video_id}/{model}")
    def get_track(video_id: str, model: str):
        try:
            meta = library.read_meta(video_id, model)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        if not meta:
            raise HTTPException(404, "Not processed yet")
        return meta

    @app.patch("/api/library/{video_id}")
    def rename_track(video_id: str, body: UploadRename):
        """Rename an uploaded file; songs from YouTube/Spotify keep their own titles."""
        try:
            info = library.rename(video_id, body.title)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        if not info or not info["title"]:
            raise HTTPException(404, "Only uploaded files can be renamed")
        return info

    @app.delete("/api/library/{video_id}")
    def delete_track(video_id: str):
        try:
            ok = library.delete(video_id)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        if not ok:
            raise HTTPException(404, "Not found")
        return {"ok": True}

    @app.post("/api/open-link")
    def open_link(url: str = Query(min_length=8, max_length=2000)):
        """Open a link on the host machine in Zen (remote clients never get here: the guard blocks their POSTs)."""
        if urlparse(url).scheme not in ("http", "https"):
            raise HTTPException(400, "Only web links")
        browser.open_url(url)
        return {"ok": True}

    @app.get("/api/config")
    def config():
        return {"googleClientId": os.environ.get("PRISMIX_GOOGLE_CLIENT_ID", "")}

    @app.get("/api/channel-playlists")
    async def channel_playlists():
        handle = os.environ.get("PRISMIX_CHANNEL", DEFAULT_CHANNEL)
        return {"handle": handle, "items": await asyncio.to_thread(pipeline.channel_playlists, handle)}

    @app.get("/api/playlist")
    async def playlist(url: str = Query(min_length=8, max_length=400)):
        host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
        if host not in library.YT_HOSTS and not spotify.is_spotify(url):
            raise HTTPException(400, "That does not look like a YouTube or Spotify playlist link")
        try:
            if spotify.is_spotify(url):
                return await asyncio.to_thread(spotify.resolve, url)
            return await asyncio.to_thread(pipeline.playlist, url if "://" in url else f"https://{url}")
        except pipeline.PipelineError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/search")
    async def search(q: str = Query(min_length=2, max_length=200), offset: int = Query(0, ge=0, le=200),
                     limit: int = Query(10, ge=1, le=30)):
        try:
            return await asyncio.to_thread(pipeline.search, q, limit, offset)
        except Exception as e:
            log.warning("search failed: %s", e)
            raise HTTPException(502, "YouTube search failed") from e

    @app.post("/api/process")
    def process(req: ProcessRequest):
        if req.model not in MODELS:
            raise HTTPException(400, "Unknown model")
        video_id = library.parse_video_id(req.url)
        if not video_id and spotify.is_spotify(req.url):
            try:
                video_id = spotify.video_id(req.url)
            except pipeline.PipelineError as e:
                raise HTTPException(400, str(e)) from e
        if not video_id:
            raise HTTPException(400, "That does not look like a YouTube or Spotify link")
        model = MIX_MODEL if req.tasks == "chords" else req.model
        if model == MIX_MODEL and req.tasks != "chords":
            raise HTTPException(400, "Unknown model")
        if library.read_meta(video_id, model):
            wants_analysis = req.tasks == "all" and not analysis.read_structure(video_id)
            if not wants_analysis:
                return {"cached": True, "videoId": video_id, "model": model}
            job = jobs.submit(video_id, model, "analyze", req.urgent)
        else:
            job = jobs.submit(video_id, model, "stems" if req.tasks == "stems" else "split", req.urgent)
        return {"cached": False, "videoId": video_id, "model": model, "job": job.snapshot()}

    def _start(video_id: str, model: str, tasks: str):
        """Queue a song unless it is already prepared; returns the job or None when cached."""
        if library.read_meta(video_id, model):
            if tasks != "all" or analysis.read_structure(video_id):
                return None
            return jobs.submit(video_id, model, "analyze")
        return jobs.submit(video_id, model, "stems" if tasks == "stems" else "split")

    @app.get("/api/playlists")
    def playlists_list():
        return playlists.list_all()

    @app.post("/api/playlists")
    def playlists_create(req: PlaylistCreate):
        return playlists.create(req.name, [i.model_dump() for i in req.items])

    @app.post("/api/playlists/import")
    async def playlists_import(req: PlaylistImport):
        if req.model not in MODELS or req.model == MIX_MODEL:
            raise HTTPException(400, "Unknown model")
        url = req.url if "://" in req.url else f"https://{req.url}"
        is_sp = spotify.is_spotify(url)
        if (urlparse(url).hostname or "").lower() not in library.YT_HOSTS and not is_sp:
            raise HTTPException(400, "That does not look like a YouTube or Spotify playlist link")
        try:
            data = await asyncio.to_thread(spotify.resolve if is_sp else pipeline.playlist, url)
        except pipeline.PipelineError as e:
            raise HTTPException(502, str(e)) from e
        if not data["items"]:
            raise HTTPException(404, "That playlist is empty or private")
        pl = playlists.create(data["title"] or "Playlist", data["items"], source=url)
        for it in pl["items"]:
            _start(it["id"], req.model, req.tasks)
        return pl

    @app.get("/api/playlists/{pid}/status")
    def playlist_status(pid: str):
        pl = playlists.get(pid)
        if not pl:
            raise HTTPException(404, "Not found")
        ready = {i["id"] for i in library.list_library()}
        out = {}
        for it in pl["items"]:
            job = jobs.active(it["id"])
            out[it["id"]] = ({"ready": True} if it["id"] in ready and not job
                             else {"ready": False, "job": job.snapshot() if job else None})
        return out

    @app.post("/api/playlists/{pid}/process")
    def playlist_process(pid: str, model: str = DEFAULT_MODEL, tasks: Literal["all", "stems"] = "all"):
        pl = playlists.get(pid)
        if not pl:
            raise HTTPException(404, "Not found")
        if model not in MODELS or model == MIX_MODEL:
            raise HTTPException(400, "Unknown model")
        queued = sum(1 for it in pl["items"] if _start(it["id"], model, tasks))
        return {"queued": queued}

    @app.patch("/api/playlists/{pid}")
    def playlists_patch(pid: str, req: PlaylistPatch):
        pl = playlists.update(pid, name=req.name, order=req.order)
        if not pl:
            raise HTTPException(404, "Not found")
        return pl

    @app.post("/api/playlists/{pid}/items")
    def playlists_add(pid: str, items: list[PlaylistItem]):
        pl = playlists.add_items(pid, [i.model_dump() for i in items])
        if not pl:
            raise HTTPException(404, "Not found")
        return pl

    @app.delete("/api/playlists/{pid}/items/{vid}")
    def playlists_remove(pid: str, vid: str):
        pl = playlists.remove_item(pid, vid)
        if not pl:
            raise HTTPException(404, "Not found")
        return pl

    @app.delete("/api/playlists/{pid}")
    def playlists_delete(pid: str):
        if not playlists.delete(pid):
            raise HTTPException(404, "Not found")
        return {"ok": True}

    @app.post("/api/analyze")
    def analyze(req: AnalyzeRequest):
        try:
            meta = library.read_meta(req.video_id, req.model)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        if not meta:
            raise HTTPException(404, "Not processed yet")
        if not req.force and analysis.read_structure(req.video_id):
            return {"cached": True}
        kind = "reanalyze" if req.force else "analyze"
        return {"cached": False, "job": jobs.submit(req.video_id, req.model, kind).snapshot()}

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        # Starlette has already spooled the body to disk; save() streams it from there
        def blocks():
            while block := file.file.read(1 << 20):
                yield block

        try:
            return await asyncio.to_thread(uploads.save, blocks(), file.filename or "upload")
        except uploads.UploadError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/tabs")
    async def find_tabs(req: TabsRequest):
        try:
            return await asyncio.to_thread(tabs.lookup, req.video_id, req.url)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        except tabs.TabsError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/tabdata")
    async def import_tabdata(req: TabsRequest):
        # Tab from a Soundslice link the user pasted; it replaces whatever tab the track had
        try:
            return await asyncio.to_thread(tabdata.import_soundslice, req.video_id, req.url or "")
        except ValueError:
            raise HTTPException(404, "Not found") from None
        except tabdata.TabDataError as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/tabdata/{video_id}")
    async def get_tabdata(video_id: str):
        # Guitar and bass tab for the Tab view; {"found": false} when there is none. Looked up once, then cached.
        try:
            return await asyncio.to_thread(tabdata.lookup, video_id)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        except tabdata.TabDataError:
            return {"found": False}

    @app.put("/api/tabdata/{video_id}/tracks/{key}")
    async def edit_tab_part(video_id: str, key: str, req: TabEditRequest):
        # The user's hand-edited bars for one part; kept beside the cached tab so a re-download never loses them
        try:
            return await asyncio.to_thread(tabdata.save_edit, video_id, key, req.bars)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        except tabdata.TabDataError as e:
            raise HTTPException(422, str(e)) from e

    @app.delete("/api/tabdata/{video_id}/tracks/{key}")
    async def revert_tab_part(video_id: str, key: str):
        try:
            return await asyncio.to_thread(tabdata.revert_edit, video_id, key)
        except ValueError:
            raise HTTPException(404, "Not found") from None

    @app.get("/api/lyrics/{video_id}")
    async def get_lyrics(video_id: str, q: str | None = Query(default=None, max_length=200), listen: bool = False):
        try:
            return await asyncio.to_thread(lyrics.lookup, video_id, (q or "").strip() or None, listen=listen)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        except lyrics.LyricsError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/mashup/{video_id}")
    def get_mashup(video_id: str):
        if not library.VIDEO_ID_RE.match(video_id) or not (info := library.read_info(video_id)):
            raise HTTPException(404, "Not found")
        return mashup.get(info)

    @app.put("/api/mashup/{video_id}")
    def put_mashup(video_id: str, req: MashupRequest):
        # Name the song the words come from and the song the chords come from; an empty value clears that source.
        if not library.VIDEO_ID_RE.match(video_id):
            raise HTTPException(404, "Not found")
        try:
            return mashup.save(video_id, req.model_dump(exclude_unset=True))
        except ValueError:
            raise HTTPException(404, "Not found") from None

    @app.get("/api/markers/{video_id}")
    async def get_markers(video_id: str):
        if not library.VIDEO_ID_RE.match(video_id):
            raise HTTPException(404, "Not found")
        return await asyncio.to_thread(markers.get, video_id)

    @app.get("/api/notes/{video_id}")
    async def get_notes(video_id: str):
        if not library.VIDEO_ID_RE.match(video_id):
            raise HTTPException(404, "Not found")
        return await asyncio.to_thread(notes.load, video_id)

    @app.post("/api/export")
    async def export_audio(req: ExportRequest):
        if req.model not in MODELS:
            raise HTTPException(400, "Unknown model")
        spec = export.ExportSpec(
            req.video_id, req.model, req.stems, req.separate, req.tempo, req.semitones,
            req.format, req.volumes,
        )
        try:
            data, name, mime = await asyncio.to_thread(export.render, spec)
        except (export.ExportError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        return Response(data, media_type=mime, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
        })

    @app.get("/api/dl/{video_id}/original.m4a")
    async def phone_original(video_id: str):
        try:
            res = await asyncio.to_thread(original.audio, video_id, "m4a")
        except (export.ExportError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        return FileResponse(res.path, media_type=res.mime, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(res.name)}",
            "Cache-Control": "no-store",
        }, background=BackgroundTask(res.cleanup))

    @app.get("/api/dl/{video_id}/{model}/{stem}.m4a")
    async def phone_stem(video_id: str, model: str, stem: str):
        if model not in MODELS:
            raise HTTPException(400, "Unknown model")
        spec = export.ExportSpec(video_id, model, [stem], format="m4a")
        try:
            data, name, mime = await asyncio.to_thread(export.render, spec)
        except (export.ExportError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        return Response(data, media_type=mime, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store",
        })

    @app.post("/api/original")
    async def original_media(req: OriginalRequest):
        def build() -> original.Result:
            if req.kind == "audio":
                return original.audio(req.video_id, req.format)
            return original.video(req.video_id, req.format, req.height)

        try:
            res = await asyncio.to_thread(build)
        except (export.ExportError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        return FileResponse(res.path, media_type=res.mime, headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(res.name)}",
            "Cache-Control": "no-store",
        }, background=BackgroundTask(res.cleanup))

    def _video_title(video_id: str) -> str | None:
        """Name of a queued song that has not been downloaded yet; looked up once in the background."""
        if video_id not in _titles:
            _titles[video_id] = None
            threading.Thread(target=_fetch_title, args=(video_id,), daemon=True).start()
        return _titles[video_id]

    def _fetch_title(video_id: str) -> None:
        try:
            r = httpx.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"}, timeout=8)
            r.raise_for_status()
            _titles[video_id] = library.clean_title(r.json()["title"])
        except (httpx.HTTPError, ValueError, KeyError):
            _titles[video_id] = ""  # offline or removed video: stop asking

    @app.get("/api/jobs")
    def jobs_list():
        titles = {i["id"]: i.get("title") for pl in playlists.list_all() for i in pl["items"]}
        out = []
        for j in jobs.listing():
            try:
                meta = library.read_meta(j.video_id, j.model)
            except ValueError:
                meta = None
            title = (meta or {}).get("title") or titles.get(j.video_id) or (library.read_info(j.video_id) or {}).get("title") or _video_title(j.video_id)
            out.append({**j.snapshot(), "title": title or ("Unknown song" if title == "" else "Loading title…")})
        return out

    @app.get("/api/prefetch")
    def prefetch_status():
        return prefetcher.status()

    @app.post("/api/jobs/{job_id}/promote")
    def jobs_promote(job_id: str):
        if not jobs.promote(job_id):
            raise HTTPException(404, "That job is not waiting any more")
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/cancel")
    def jobs_cancel(job_id: str):
        if not jobs.cancel(job_id):
            raise HTTPException(404, "That job already finished")
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request):
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Unknown job")

        async def stream():
            seen, idle = -1, 0
            while not await request.is_disconnected():
                if job.version != seen:
                    seen, idle = job.version, 0
                    yield f"data: {json.dumps(job.snapshot())}\n\n"
                    if job.finished:
                        return
                elif (idle := idle + 1) % 60 == 0:  # ~15 s: keep proxies from closing the stream
                    yield ": ping\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/media", MediaFiles(directory=LIBRARY_DIR), name="media")

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", WebFiles(directory=WEB_DIR), name="web")
    return app


app = create_app()
