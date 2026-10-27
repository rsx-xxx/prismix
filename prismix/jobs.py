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


class Preempted(BaseException):
    """Raised inside a running job to hand the worker to an urgent one; BaseException so no stage swallows it."""


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
    rush: bool = False
    preempt: bool = False

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
        if self.preempt and stage != "analyze":  # analysis is short and a retry would skip it
            raise Preempted
        lo, hi = (0.0, 1.0) if stage == "analyze" and self.kind in ("analyze", "reanalyze") else STAGES.get(
            stage, (self.progress, self.progress))
        self.stage = stage
        self.progress = max(self.progress, lo + (hi - lo) * frac)
        self.message = message
        self.version += 1


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._pending: list[Job] = []
        self._wake = asyncio.Event()
        self._running: Job | None = None
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

    def submit(self, video_id: str, model: str, kind: str = "split", urgent: bool = False) -> Job:
        job = next((j for j in self._jobs.values()
                    if (j.video_id, j.model) == (video_id, model) and not j.finished), None)
        if not job:
            job = Job(video_id, model, kind)
            self._jobs[job.id] = job
            self._prune()
            self._pending.append(job)
            self._wake.set()
        if urgent:
            self._promote(job)
        return job

    def _rush_end(self) -> int:
        return next((i for i, j in enumerate(self._pending) if not j.rush), len(self._pending))

    def _promote(self, job: Job) -> None:
        """Run `job` next. A different running job is paused at its next progress report and requeued behind it."""
        if job is self._running:
            return
        if job in self._pending:
            self._pending.remove(job)
            job.rush = True
            self._pending.insert(self._rush_end(), job)
        if self._running and not self._running.preempt:
            self._running.preempt = True

    def _requeue(self, job: Job) -> None:
        job.preempt = False
        job.stage, job.progress, job.message = "queued", 0.0, "Paused, waiting for its turn"
        job.version += 1
        self._pending.insert(self._rush_end(), job)

    def _prune(self) -> None:
        done = sorted((j for j in self._jobs.values() if j.finished), key=lambda j: j.created)
        for job in done[:-30]:
            del self._jobs[job.id]

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            while not self._pending:
                self._wake.clear()
                await self._wake.wait()
            job = self._pending.pop(0)
            job.rush = False
            self._running = job
            job.update("analyze" if job.kind in ("analyze", "reanalyze") else "download", 0.0, "Starting")
            try:
                await loop.run_in_executor(None, self._execute, job)
            except Preempted:
                self._running = None
                self._requeue(job)
                continue
            except (pipeline.PipelineError, analysis.AnalysisError) as e:
                job.error = str(e)
            except Exception as e:
                log.exception("job %s failed", job.id)
                job.error = f"Unexpected error: {type(e).__name__}: {str(e).splitlines()[0][:200] if str(e) else 'no details'}"
            self._running = None
            job.version += 1

    @staticmethod
    def _execute(job: Job) -> None:
        if job.kind in ("analyze", "reanalyze"):
            analysis.analyze(job.video_id, job.model, job.update, force=job.kind == "reanalyze")
        else:
            pipeline.process(job.video_id, job.model, job.update, analyse=job.kind != "stems")
        job.update("done", 1.0, "Ready")
