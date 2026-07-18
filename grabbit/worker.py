"""Async worker pool: pulls queued jobs, runs the engine, enforces concurrency limits."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from .config import Config
from .db import Database
from .engine import Engine, EngineOpts, ProgressEvent
from .events import EventHub
from .logging_setup import redact_url
from .models import JobState, utcnow

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


def gather_into_dir(base: Path, rel_paths: list[str], dst: Path) -> None:
    """Move loose files (paths relative to base) into dst.

    Renames a job that has no output directory (files landed flat in the
    destination root) by collecting its recorded files into one.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for rel in rel_paths:
        parts = Path(rel).parts
        if not parts or ".." in parts or Path(rel).is_absolute():
            continue
        src = base / rel
        if not src.is_file():
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(src, target)
        except OSError:
            shutil.move(str(src), str(target))


def merge_dirs(sources: list[Path], dst: Path) -> None:
    """Move files from each source dir into dst.

    Unlike _move_tree (skip-existing re-runs, overwrite is correct), merged
    jobs may legitimately contain equal filenames — collisions are kept by
    suffixing ' (2)', ' (3)', … Emptied source dirs are removed.
    """
    dst.mkdir(parents=True, exist_ok=True)
    for src in sources:
        if not src.is_dir() or src.resolve() == dst.resolve():
            continue
        for path in sorted(src.rglob("*")):
            if path.is_dir():
                continue
            target = dst / path.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            n = 2
            while target.exists():
                target = target.with_name(f"{target.stem} ({n}){target.suffix}")
                n += 1
            try:
                os.replace(path, target)
            except OSError:
                shutil.move(str(path), str(target))
        shutil.rmtree(src, ignore_errors=True)


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


# Bump when adding a one-time on-disk migration guarded by PRAGMA user_version.
# 1: flatten the domain-level parent out of the download layout (item 014).
SCHEMA_VERSION = 1


async def migrate_flatten_domain_dirs(cfg: Config, db: Database) -> int:
    """One-time: drop the domain-level parent from finished two-level jobs.

    Old layout put files under dest/<domain>/<package>/…; the flatten change
    (item 014) makes gallery-dl write dest/<package>/… directly. This brings
    existing DONE jobs in line, driven purely from DB records — never a dest
    scan — so non-Grabbit directories are never touched.

    Migrates only jobs whose recorded file_paths still lead with dir_name and
    carry a package level beneath it (>=3 parts). A user-renamed job's dir_name
    diverges from its (unrewritten) file_paths, so it is left untouched; so are
    already-flat one-level jobs. Returns the number of jobs migrated.
    """
    if await db.get_schema_version() >= SCHEMA_VERSION:
        return 0
    migrated = 0
    for job_id, dest, dir_name, file_paths in await db.list_done_with_paths():
        parts_list = [Path(p).parts for p in file_paths]
        # Every path must be <domain>/<package>/…file with domain == dir_name.
        if not parts_list or not all(
                len(p) >= 3 and p[0] == dir_name for p in parts_list):
            continue
        base = cfg.downloads.dest / dest if dest else cfg.downloads.dest
        domain_dir = base / dir_name
        packages = dict.fromkeys(p[1] for p in parts_list)  # ordered, unique
        for pkg in packages:
            src_pkg = domain_dir / pkg
            if src_pkg.is_dir():
                await asyncio.to_thread(merge_dirs, [src_pkg], base / pkg)
        # Strip the leading domain component from every recorded path.
        new_paths = [str(Path(*p[1:])) for p in parts_list]
        await db.update_job(job_id, dir_name=next(iter(packages)),
                            file_paths=json.dumps(new_paths))
        if domain_dir.is_dir():
            with contextlib.suppress(OSError):
                domain_dir.rmdir()  # only removes it when empty
        migrated += 1
        log.info("job %d flattened: dropped domain dir %r", job_id, dir_name)
    await db.set_schema_version(SCHEMA_VERSION)
    if migrated:
        log.info("flatten migration: %d job(s) updated", migrated)
    return migrated


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
        self._pin_task: asyncio.Task | None = None
        self._backfill_task: asyncio.Task | None = None
        self._retry_task: asyncio.Task | None = None

    async def start(self) -> None:
        requeued = await self.db.requeue_interrupted()
        if requeued:
            log.info("requeued %d interrupted job(s)", requeued)
        self._loop_task = asyncio.create_task(self._dispatch_loop())
        self._pin_task = asyncio.create_task(self._pin_loop())
        if self.cfg.downloads.resolve_titles:
            self._backfill_task = asyncio.create_task(self._backfill_titles())
        if self.cfg.downloads.auto_retry and self.cfg.downloads.auto_retry_minutes > 0:
            self._retry_task = asyncio.create_task(self._auto_retry_loop())

    async def stop(self) -> None:
        self._stopped = True
        self._wakeup.set()
        for task in (self._pin_task, self._backfill_task, self._retry_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
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

    async def _pin_loop(self) -> None:
        """Requeue pinned jobs whose last run is older than the recheck interval.

        A recheck is a normal queued run — it flows through the same dispatch
        loop (global + per-host semaphores), and the engine's skip-existing
        behavior means only files added at the source since the last run are
        downloaded.
        """
        interval = self.cfg.downloads.pin_recheck_minutes * 60.0
        poll = min(60.0, max(interval / 4, 0.05))
        while not self._stopped:
            await asyncio.sleep(poll)
            try:
                cutoff = (utcnow() - timedelta(seconds=interval)).isoformat()
                for job in await self.db.list_pinned_due(cutoff):
                    # Preserve a renamed directory: record it as a pending
                    # rename so the recheck's freshly detected directory is
                    # merged back into it at completion.
                    await self.db.update_job(
                        job.id, state=JobState.QUEUED.value, error=None,
                        finished_at=None, rename_to=job.dir_name or None)
                    self._publish(job.id, JobState.QUEUED)
                    log.info("pinned job %d recheck queued: %s",
                             job.id, redact_url(job.url))
                    self.notify()
            except Exception:
                log.exception("pin recheck loop error")

    async def _auto_retry_loop(self) -> None:
        """Periodically requeue every failed download in one pass.

        A single global loop — deliberately not a per-download timer, which
        would let several jobs from the same host retry at once and stampede
        it. Runs every downloads.auto_retry_minutes; each pass moves all
        unpinned ERROR jobs back to QUEUED and lets the normal dispatch loop
        (global + per-host semaphores) pace them. Pinned jobs are left to the
        pin loop.
        """
        interval = self.cfg.downloads.auto_retry_minutes * 60.0
        # Poll often enough to react to shutdown promptly, but only act once a
        # full interval has elapsed.
        poll = min(60.0, max(interval / 4, 0.05))
        elapsed = 0.0
        while not self._stopped:
            await asyncio.sleep(poll)
            elapsed += poll
            if elapsed < interval:
                continue
            elapsed = 0.0
            try:
                failed = await self.db.list_failed_unpinned()
                if not failed:
                    continue
                for job in failed:
                    await self.db.update_job(
                        job.id, state=JobState.QUEUED.value, error=None,
                        finished_at=None)
                    self._publish(job.id, JobState.QUEUED)
                    log.info("auto-retry queued job %d: %s",
                             job.id, redact_url(job.url))
                self.notify()
            except Exception:
                log.exception("auto-retry loop error")

    async def _maybe_resolve_title(self, job_id: int, url: str) -> None:
        """Resolve and store a display title for a job, if not already set.

        Best-effort and non-fatal: engine.resolve_title never raises, and a
        None result (source exposes no title) simply leaves the field unset.
        Skipped entirely when resolve_titles is off or the engine predates the
        feature. Never touches the on-disk directory name.
        """
        if not self.cfg.downloads.resolve_titles:
            return
        resolve = getattr(self.engine, "resolve_title", None)
        if resolve is None:
            return
        job = await self.db.get_job(job_id)
        if job is None or job.title:
            return
        try:
            title = await resolve(url)
        except Exception:
            log.debug("title resolve errored for job %d", job_id, exc_info=True)
            return
        if title:
            await self.db.update_job(job_id, title=title)
            self.hub.publish({"type": "state", "job_id": job_id,
                              "state": job.state.value, "title": title})
            log.info("job %d title resolved: %r", job_id, title)

    async def _backfill_titles(self) -> None:
        """One-time: resolve titles for finished jobs that never had one.

        Runs in the background after startup, one job at a time with a short
        gap between probes, so it never competes with active downloads or
        hammers a host. Errors on any single job are swallowed and skipped.
        """
        await asyncio.sleep(2.0)  # let startup settle before probing
        try:
            pending = await self.db.list_missing_title()
        except Exception:
            log.exception("title backfill: could not list jobs")
            return
        if not pending:
            return
        log.info("title backfill: %d job(s) to check", len(pending))
        for job in pending:
            if self._stopped:
                return
            await self._maybe_resolve_title(job.id, job.url)
            await asyncio.sleep(1.0)  # polite gap between metadata probes

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

            # Resolve a display title alongside the download (best-effort, never
            # blocks or fails the job); skipped if one is already set.
            loop.create_task(self._maybe_resolve_title(job_id, url))

            final_dest = self.cfg.downloads.dest / dest if dest else self.cfg.downloads.dest
            stage = staging_dir(self.cfg, job_id)
            job_root = stage or final_dest
            seen_dir = ""
            rel_files: list[str] = []

            def on_progress(ev: ProgressEvent) -> None:
                nonlocal seen_dir
                top = _top_dir(job_root, ev.current_file)
                if top and top != seen_dir:
                    seen_dir = top
                    loop.create_task(self.db.update_job(job_id, dir_name=top))
                if ev.current_file:
                    with contextlib.suppress(ValueError):
                        rel_files.append(str(Path(ev.current_file).relative_to(job_root)))
                loop.create_task(self._on_progress(job_id, ev))

            opts = EngineOpts(
                dest=stage or final_dest,
                retries=self.cfg.engine.retries,
                rate_limit=self.cfg.engine.rate_limit,
                filename_template=self.cfg.downloads.filename_template,
                cookies_file=self.cfg.downloads.cookies_file,
                keep_dirs=self.cfg.downloads.keep_dirs,
                reset_mtime=self.cfg.downloads.reset_mtime,
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
                await self.db.set_job_files(job_id, rel_files)
                await self._apply_pending_rename(job_id, final_dest)
                await self.db.update_job(job_id, files_done=result.files_done,
                                         files_total=result.files_done,
                                         bytes_done=result.bytes_done)
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
            return
        # No output directory (files landed flat in dest): gather the job's
        # recorded files into the requested directory instead.
        rel_files = await self.db.get_job_files(job_id)
        if rel_files:
            await asyncio.to_thread(
                gather_into_dir, final_dest, rel_files, final_dest / job.rename_to)
            await self.db.update_job(job_id, dir_name=job.rename_to, rename_to=None)
            log.info("job %d gathered %d file(s) into %r",
                     job_id, len(rel_files), job.rename_to)
        else:
            await self.db.update_job(job_id, rename_to=None)
            log.warning("job %d rename skipped: no directory %r", job_id, job.dir_name)

    async def _on_progress(self, job_id: int, ev: ProgressEvent) -> None:
        await self.db.update_job(
            job_id, files_done=ev.files_done, bytes_done=ev.bytes_done)
        self.hub.publish({
            "type": "progress", "job_id": job_id,
            "files_done": ev.files_done, "current_file": ev.current_file,
            "bytes_done": ev.bytes_done, "bytes_per_sec": ev.bytes_per_sec,
        })

    def _publish(self, job_id: int, state: JobState, **extra) -> None:
        self.hub.publish({"type": "state", "job_id": job_id, "state": state.value, **extra})
