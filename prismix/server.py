from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote, urlparse

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
    export,
    lan,
    library,
    lyrics,
    markers,
    original,
    pipeline,
    playlists,
    spotify,
    tabdata,
    tabs,
    uploads,
)
from . import band as band_mod
from .band import Band
from .config import DEFAULT_CHANNEL, DEFAULT_MODEL, LIBRARY_DIR, MIX_MODEL, MODELS, WEB_DIR
from .jobs import JobManager

log = logging.getLogger(__name__)


class ProcessRequest(BaseModel):
    url: str
    model: str = DEFAULT_MODEL
    tasks: Literal["all", "stems", "chords"] = "all"


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


class OriginalRequest(BaseModel):
    video_id: str = Field(alias="videoId")
    kind: Literal["audio", "video"]
    format: str = "original"
    height: int | None = None


class MediaFiles(StaticFiles):
    """Stems never change once written, so let the browser cache them hard."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith(".flac"):
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

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        jobs.start()
        yield
        await jobs.stop()

    app = FastAPI(title="Prismix", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.band = band = Band()

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
    async def fresh_api(request: Request, call_next):
        # a phone browser or the app's WebView must never replay an old library list
        response = await call_next(request)
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.websocket("/ws")
    async def band_ws(ws: WebSocket):
        await band.serve(ws)

    @app.get("/api/band")
    def band_status():
        # the app's own scheme makes the camera open Prismix Band directly; https opens any phone's browser
        app_url = re.sub(r"^https:", "prismix:", band.share_url)
        return {**band.status(), "name": lan.display_name(), "url": band.share_url, "qr": band_qr(band.share_url), "appQr": band_qr(app_url)}

    @app.post("/api/band")
    def band_config(body: BandConfig):
        band.configure(token=body.token, locked=body.locked)
        if body.name is not None:
            lan.set_display_name(body.name)
        return band_status()

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

    @app.get("/api/version")
    def app_version():
        return {"version": __version__, "url": f"{REPO}/releases/tag/v{__version__}"}

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

    @app.get("/api/library/{video_id}/{model}")
    def get_track(video_id: str, model: str):
        try:
            meta = library.read_meta(video_id, model)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        if not meta:
            raise HTTPException(404, "Not processed yet")
        return meta

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
            job = jobs.submit(video_id, model, "analyze")
        else:
            job = jobs.submit(video_id, model, "stems" if req.tasks == "stems" else "split")
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

    @app.get("/api/tabdata/{video_id}")
    async def get_tabdata(video_id: str):
        # Guitar and bass tab for the Tab view; {"found": false} when there is none. Looked up once, then cached.
        try:
            return await asyncio.to_thread(tabdata.lookup, video_id)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        except tabdata.TabDataError:
            return {"found": False}

    @app.get("/api/lyrics/{video_id}")
    async def get_lyrics(video_id: str, q: str | None = Query(default=None, max_length=200), listen: bool = False):
        try:
            return await asyncio.to_thread(lyrics.lookup, video_id, (q or "").strip() or None, listen=listen)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        except lyrics.LyricsError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/markers/{video_id}")
    async def get_markers(video_id: str):
        if not library.VIDEO_ID_RE.match(video_id):
            raise HTTPException(404, "Not found")
        return await asyncio.to_thread(markers.get, video_id)

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
