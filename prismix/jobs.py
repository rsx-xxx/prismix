"""Single-worker job queue. Separation saturates the GPU, so jobs run one at a time."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from . import analysis, pipeline

log = logging.getLogger(__name__)

# Share of the overall progress bar each stage owns.
STAGES = {
    "queued": (0.0, 0.0),
    "download": (0.0, 0.15),
    "decode": (0.15, 0.18),
    "model": (0.18, 0.20),
    "separate": (0.20, 0.70),
    "encode": (0.70, 0.74),
    "analyze": (0.74, 1.0),
    "done": (1.0, 1.0),
}


@dataclass
class Job:
    video_id: str
    model: str
    kind: str = "split"  # "split" (separate + analyse), "stems" (separate only) "analyze" or "reanalyze" (existing track)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    stage: str = "queued"
    progress: float = 0.0
    message: str = "Waiting in queue"
    error: str | None = None
    created: float = field(default_factory=time.time)
    version: int = 0

    @property
    def finished(self) -> bool:
        return self.stage == "done" or self.error is not None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "videoId": self.video_id,
            "model": self.model,
            "kind": self.kind,
            "stage": "error" if self.error else self.stage,
            "progress": round(self.progress, 4),
            "message": self.error or self.message,
            "error": self.error,
        }

    def update(self, stage: str, frac: float, message: str) -> None:
        lo, hi = (0.0, 1.0) if stage == "analyze" and self.kind in ("analyze", "reanalyze") else STAGES.get(
            stage, (self.progress, self.progress))
        self.stage = stage
        self.progress = max(self.progress, lo + (hi - lo) * frac)
        self.message = message
        self.version += 1


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._worker: asyncio.Task | None = None

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="prismix-worker")

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self, video_id: str) -> Job | None:
        return next((j for j in self._jobs.values() if j.video_id == video_id and not j.finished), None)

    def submit(self, video_id: str, model: str, kind: str = "split") -> Job:
        for job in self._jobs.values():  # same song + model already in flight: share it
            if (job.video_id, job.model) == (video_id, model) and not job.finished:
                return job
        job = Job(video_id, model, kind)
        self._jobs[job.id] = job
        self._prune()
        self._queue.put_nowait(job)
        return job

    def _prune(self) -> None:
        done = sorted((j for j in self._jobs.values() if j.finished), key=lambda j: j.created)
        for job in done[:-30]:
            del self._jobs[job.id]

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._queue.get()
            job.update("analyze" if job.kind in ("analyze", "reanalyze") else "download", 0.0, "Starting")
            try:
                await loop.run_in_executor(None, self._execute, job)
            except (pipeline.PipelineError, analysis.AnalysisError) as e:
                job.error = str(e)
            except Exception:
                log.exception("job %s failed", job.id)
                job.error = "Unexpected error while processing (see server log)"
            job.version += 1

    @staticmethod
    def _execute(job: Job) -> None:
        if job.kind in ("analyze", "reanalyze"):
            analysis.analyze(job.video_id, job.model, job.update, force=job.kind == "reanalyze")
        else:
            pipeline.process(job.video_id, job.model, job.update, analyse=job.kind != "stems")
        job.update("done", 1.0, "Ready")
