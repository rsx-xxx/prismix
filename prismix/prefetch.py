"""Fills in, in the background, what a library song is missing: chords and structure, lyrics and tabs.
Songs open instantly when everything is already on disk; a song that lacks something is topped up here."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from . import analysis, library, lyrics, tabdata
from .jobs import JobManager

log = logging.getLogger(__name__)

RETRY_AFTER = 24 * 3600  # a step that found nothing (no lyrics published, no tab, no network) is tried again after this long;
# the attempt is written beside the song so a restart of the app does not start the same work over
IDLE_SCAN = 20  # seconds between looks at the library while nothing is missing

STEPS = ("chords", "lyrics", "tabs")


def _marker(video_id: str):
    return library.video_dir(video_id) / "prefetch.json"


def _attempts(video_id: str) -> dict[str, float]:
    try:
        data = json.loads(_marker(video_id).read_text())
        return {k: float(v) for k, v in data.items() if k in STEPS}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def _remember(video_id: str, step: str) -> None:
    """Note on disk that this step was tried now and left nothing behind."""
    data = _attempts(video_id) | {step: time.time()}
    path = _marker(video_id)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        log.info("could not record the prefetch attempt for %s", video_id)


def missing(video_id: str) -> list[str]:
    """Which of the steps still have nothing cached for this song."""
    out = []
    if analysis.read_structure(video_id) is None:
        out.append("chords")
    try:
        cached = json.loads((library.video_dir(video_id) / "lyrics.json").read_text())
        if cached.get("version") != lyrics.SCHEMA:
            raise ValueError
    except (OSError, ValueError):
        out.append("lyrics")
    if tabdata.read(video_id) is None:
        out.append("tabs")
    return out


class Prefetcher:
    def __init__(self, jobs: JobManager) -> None:
        self._jobs = jobs
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._failed: dict[tuple[str, str], float] = {}  # in-memory copy of the markers, so a scan does not reread every file
        self._state: dict = {"active": False, "total": 0, "done": 0, "current": None}

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="prismix-prefetch")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def kick(self) -> None:
        self._wake.set()

    def status(self) -> dict:
        return dict(self._state)

    def _todo(self) -> list[tuple[str, str, str]]:
        now = time.time()
        work = []
        for item in library.list_library():
            vid = item["id"]
            tried = _attempts(vid)
            for step in missing(vid):
                if max(self._failed.get((vid, step), 0), tried.get(step, 0)) + RETRY_AFTER > now:
                    continue
                work.append((vid, item["models"][0], step))
        return work

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                work = await loop.run_in_executor(None, self._todo)
            except Exception:
                log.exception("prefetch scan failed")
                work = []
            if not work:
                self._state = {"active": False, "total": 0, "done": 0, "current": None}
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), IDLE_SCAN)
                continue
            total = len(work)
            titles = {v: (await loop.run_in_executor(None, library.read_info, v) or {}).get("title") for v in {w[0] for w in work[:40]}}
            for n, (vid, model, step) in enumerate(work):
                info = await loop.run_in_executor(None, library.read_info, vid) or {}
                self._state = {
                    "active": True, "total": total, "done": n,
                    "current": {"id": vid, "title": info.get("title") or vid, "step": step, "model": model},
                    "next": [{"id": v, "title": titles.get(v) or v, "step": st, "model": m} for v, m, st in work[n + 1:n + 13]],
                }
                try:
                    await self._fetch(loop, vid, model, step)
                    if step in await loop.run_in_executor(None, missing, vid):
                        self._note(vid, step)  # nothing stored (offline, say): not again right away
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.info("prefetch %s for %s: %s", step, vid, e)
                    self._note(vid, step)

    def _note(self, vid: str, step: str) -> None:
        self._failed[(vid, step)] = time.time()
        _remember(vid, step)

    async def _fetch(self, loop: asyncio.AbstractEventLoop, vid: str, model: str, step: str) -> None:
        if step == "chords":
            job = self._jobs.submit(vid, model, "analyze")
            while not job.finished:
                await asyncio.sleep(1)
            if job.error:
                raise RuntimeError(job.error)
        elif step == "lyrics":
            await loop.run_in_executor(None, lyrics.lookup, vid)
        elif step == "tabs":
            if analysis.read_structure(vid) is None:
                raise RuntimeError("chords are not ready")  # retried after RETRY_AFTER, or on the next start
            await loop.run_in_executor(None, tabdata.lookup, vid)
