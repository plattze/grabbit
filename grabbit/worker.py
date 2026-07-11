"""Async worker pool: pulls queued jobs, runs the engine, enforces concurrency limits."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from collections import defaultdict
from pathlib import Path

from .config import Config
from .db import Database
from .engine import Engine, EngineOpts, ProgressEvent
from .events import EventHub
from .logging_setup import redact_url
from .models import JobState

log = logging.getLogger(__name__)


def staging_dir(cfg: Config, job_id: int) -> Path | None:
    """Per-job staging dir under downloads.incomplete_dir; None when disabled."""
    inc = cfg.downloads.incomplete_dir
    return inc / f"job-{job_id}" if inc else None


def cleanup_staging(cfg: Config, job_id: int) -> None:
    """Remove a job's staging leftovers (cancelled/deleted jobs)."""
    stage = staging_dir(cfg, job_id)
    if stage and stage.is_dir():
        shutil.rmtree(stage, ignore_errors=True)


def _top_dir(root: Path, file_path: str | None) -> str:
    """First directory component of file_path under root; '' if flat/unknown."""
    if not file_path:
        return ""
    try:
        rel = Path(file_path).relative_to(root)
    except ValueError:
        return ""
    return rel.parts[0] if len(rel.parts) > 1 else ""


def rename_dir(src: Path, dst: Path) -> None:
    """Rename a job's directory; merge into dst if it already exists."""
    if dst.is_dir():
        _move_tree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, dst)
        except OSError:
            shutil.move(str(src), str(dst))


def _move_tree(src: Path, dst: Path) -> None:
    """Move every file under src into dst, preserving relative paths.

    os.replace is atomic on the same filesystem; falls back to shutil.move
    (copy+delete) across filesystems. Empty staging tree is removed after.
    """
    for path in sorted(src.rglob("*")):
        if path.is_dir():
            continue
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(path, target)
        except OSError:
            shutil.move(str(path), str(target))
    shutil.rmtree(src, ignore_errors=True)


class WorkerPool:
    def __init__(self, cfg: Config, db: Database, engine: Engine, hub: EventHub) -> None:
        self.cfg = cfg
        self.db = db
        self.engine = engine
        self.hub = hub
        self._wakeup = asyncio.Event()
        self._global_sem = asyncio.Semaphore(cfg.downloads.max_concurrent)
        self._host_sems: dict[str, asyncio.Semaphore] = defaultdict(
            lambda: asyncio.Semaphore(cfg.downloads.max_per_host))
        self._running: dict[int, asyncio.Task] = {}
        self._stopped = False
        self._loop_task: asyncio.Task | None = None

    async def start(self) -> None:
        requeued = await self.db.requeue_interrupted()
        if requeued:
            log.info("requeued %d interrupted job(s)", requeued)
        self._loop_task = asyncio.create_task(self._dispatch_loop())

    async def stop(self) -> None:
        self._stopped = True
        self._wakeup.set()
        if self._loop_task:
            await self._loop_task
        for task in list(self._running.values()):
            task.cancel()
        if self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)

    def notify(self) -> None:
        """Wake the dispatcher (called after a new job is queued or resumed)."""
        self._wakeup.set()

    def cancel_job(self, job_id: int) -> bool:
        """Kill a running engine process for this job, if any."""
        cancel = getattr(self.engine, "cancel", None)
        return bool(cancel and cancel(job_id))

    async def _dispatch_loop(self) -> None:
        while not self._stopped:
            try:
                started = await self._dispatch_ready()
            except Exception:
                log.exception("dispatch loop error")
                started = False
            if not started:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wakeup.wait(), timeout=5.0)
                self._wakeup.clear()

    async def _dispatch_ready(self) -> bool:
        """Start every queued job whose slots are free; True if any started."""
        queued = await self.db.list_jobs(state=JobState.QUEUED, limit=50)
        started = False
        for job in queued:
            if self._stopped or job.id in self._running:
                continue
            if self._global_sem.locked() or self._host_sems[job.host].locked():
                continue
            await self._global_sem.acquire()
            await self._host_sems[job.host].acquire()
            task = asyncio.create_task(self._run_job(job.id, job.url, job.host, job.dest))
            self._running[job.id] = task
            started = True
        return started

    async def _run_job(self, job_id: int, url: str, host: str, dest: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            await self.db.set_state(job_id, JobState.ACTIVE)
            self._publish(job_id, JobState.ACTIVE)
            log.info("job %d start: %s", job_id, redact_url(url))

            final_dest = self.cfg.downloads.dest / dest if dest else self.cfg.downloads.dest
            stage = staging_dir(self.cfg, job_id)
            job_root = stage or final_dest
            seen_dir = ""

            def on_progress(ev: ProgressEvent) -> None:
                nonlocal seen_dir
                top = _top_dir(job_root, ev.current_file)
                if top and top != seen_dir:
                    seen_dir = top
                    loop.create_task(self.db.update_job(job_id, dir_name=top))
                loop.create_task(self._on_progress(job_id, ev))

            opts = EngineOpts(
                dest=stage or final_dest,
                retries=self.cfg.engine.retries,
                rate_limit=self.cfg.engine.rate_limit,
                filename_template=self.cfg.downloads.filename_template,
                cookies_file=self.cfg.downloads.cookies_file,
                keep_dirs=self.cfg.downloads.keep_dirs,
            )
            result = await self.engine.download(url, opts, on_progress, job_id=job_id)

            # A pause/cancel that raced the finish wins over the engine result.
            # Paused: staged partials stay put — resume re-runs into the same
            # staging dir and skips existing files. Cancelled: drop them.
            current = await self.db.get_job(job_id)
            if current and current.state in (JobState.PAUSED, JobState.CANCELLED):
                if current.state == JobState.CANCELLED:
                    cleanup_staging(self.cfg, job_id)
                self._publish(job_id, current.state)
                return

            if result.success:
                if stage and stage.is_dir():
                    await asyncio.to_thread(_move_tree, stage, final_dest)
                await self._apply_pending_rename(job_id, final_dest)
                await self.db.update_job(job_id, files_done=result.files_done,
                                         files_total=result.files_done)
                await self.db.set_state(job_id, JobState.DONE)
                self._publish(job_id, JobState.DONE, files_done=result.files_done)
                log.info("job %d done: %d file(s)", job_id, result.files_done)
            else:
                await self.db.set_state(job_id, JobState.ERROR, error=result.error)
                self._publish(job_id, JobState.ERROR, error=result.error)
                log.warning("job %d failed: %s", job_id, result.error)
        except asyncio.CancelledError:
            self.cancel_job(job_id)
            raise
        except Exception as e:
            log.exception("job %d crashed", job_id)
            await self.db.set_state(job_id, JobState.ERROR, error=str(e))
            self._publish(job_id, JobState.ERROR, error=str(e))
        finally:
            self._running.pop(job_id, None)
            self._host_sems[host].release()
            self._global_sem.release()
            self._wakeup.set()

    async def _apply_pending_rename(self, job_id: int, final_dest: Path) -> None:
        """Apply a rename requested while the job was running (models.Job.rename_to)."""
        job = await self.db.get_job(job_id)
        if not job or not job.rename_to or job.rename_to == job.dir_name:
            if job and job.rename_to:
                await self.db.update_job(job_id, rename_to=None)
            return
        src = final_dest / job.dir_name if job.dir_name else None
        if src and src.is_dir():
            await asyncio.to_thread(rename_dir, src, final_dest / job.rename_to)
            await self.db.update_job(job_id, dir_name=job.rename_to, rename_to=None)
            log.info("job %d renamed dir %r -> %r", job_id, job.dir_name, job.rename_to)
        else:
            await self.db.update_job(job_id, rename_to=None)
            log.warning("job %d rename skipped: no directory %r", job_id, job.dir_name)

    async def _on_progress(self, job_id: int, ev: ProgressEvent) -> None:
        await self.db.update_job(job_id, files_done=ev.files_done)
        self.hub.publish({
            "type": "progress", "job_id": job_id,
            "files_done": ev.files_done, "current_file": ev.current_file,
        })

    def _publish(self, job_id: int, state: JobState, **extra) -> None:
        self.hub.publish({"type": "state", "job_id": job_id, "state": state.value, **extra})
