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

RETRY_AFTER = 3600  # a song that failed (no lyrics published, no network) is tried again after this many seconds
IDLE_SCAN = 20  # seconds between looks at the library while nothing is missing

STEPS = ("chords", "lyrics", "tabs")


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
        self._failed: dict[tuple[str, str], float] = {}
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
            for step in missing(vid):
                if self._failed.get((vid, step), 0) + RETRY_AFTER > now:
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
            for n, (vid, model, step) in enumerate(work):
                info = await loop.run_in_executor(None, library.read_info, vid) or {}
                self._state = {
                    "active": True, "total": total, "done": n,
                    "current": {"id": vid, "title": info.get("title") or vid, "step": step},
                }
                try:
                    await self._fetch(loop, vid, model, step)
                    if step in await loop.run_in_executor(None, missing, vid):
                        self._failed[(vid, step)] = time.time()  # nothing stored (offline, say): not again right away
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.info("prefetch %s for %s: %s", step, vid, e)
                    self._failed[(vid, step)] = time.time()

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
