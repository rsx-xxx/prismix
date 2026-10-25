from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import quote, urlparse

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.staticfiles import StaticFiles

from . import analysis, export, library, lyrics, pipeline, tabs, uploads
from .config import DEFAULT_MODEL, LIBRARY_DIR, MIX_MODEL, MODELS, WEB_DIR
from .jobs import JobManager

log = logging.getLogger(__name__)


class ProcessRequest(BaseModel):
    url: str
    model: str = DEFAULT_MODEL
    tasks: Literal["all", "stems", "chords"] = "all"


class TabsRequest(BaseModel):
    video_id: str = Field(alias="videoId")
    url: str | None = Field(default=None, max_length=300)


class AnalyzeRequest(BaseModel):
    video_id: str = Field(alias="videoId")
    model: str


class ExportRequest(BaseModel):
    video_id: str = Field(alias="videoId")
    model: str
    stems: list[str] = Field(min_length=1, max_length=8)
    separate: bool = False
    tempo: float = 1.0
    semitones: float = 0.0
    format: str = "flac"
    volumes: dict[str, float] = {}


class MediaFiles(StaticFiles):
    """Stems never change once written, so let the browser cache them hard."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if path.endswith(".flac"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
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

    @app.get("/api/models")
    def models():
        return {
            "default": DEFAULT_MODEL,
            "models": [
                {"id": m.id, "label": m.label, "hint": m.hint, "stems": list(m.stems)}
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

    @app.get("/api/config")
    def config():
        return {"googleClientId": os.environ.get("PRISMIX_GOOGLE_CLIENT_ID", "")}

    @app.get("/api/playlist")
    async def playlist(url: str = Query(min_length=8, max_length=400)):
        host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
        if host not in library.YT_HOSTS:
            raise HTTPException(400, "That does not look like a YouTube playlist link")
        try:
            return await asyncio.to_thread(pipeline.playlist, url if "://" in url else f"https://{url}")
        except pipeline.PipelineError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/search")
    async def search(q: str = Query(min_length=2, max_length=200)):
        try:
            return await asyncio.to_thread(pipeline.search, q)
        except Exception as e:
            log.warning("search failed: %s", e)
            raise HTTPException(502, "YouTube search failed") from e

    @app.post("/api/process")
    def process(req: ProcessRequest):
        if req.model not in MODELS:
            raise HTTPException(400, "Unknown model")
        video_id = library.parse_video_id(req.url)
        if not video_id:
            raise HTTPException(400, "That does not look like a YouTube link")
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

    @app.post("/api/analyze")
    def analyze(req: AnalyzeRequest):
        try:
            meta = library.read_meta(req.video_id, req.model)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        if not meta:
            raise HTTPException(404, "Not processed yet")
        if analysis.read_structure(req.video_id):
            return {"cached": True}
        return {"cached": False, "job": jobs.submit(req.video_id, req.model, "analyze").snapshot()}

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

    @app.get("/api/lyrics/{video_id}")
    async def get_lyrics(video_id: str, q: str | None = Query(default=None, max_length=200)):
        try:
            return await asyncio.to_thread(lyrics.lookup, video_id, (q or "").strip() or None)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        except lyrics.LyricsError as e:
            raise HTTPException(404, str(e)) from e

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

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
    return app


app = create_app()
