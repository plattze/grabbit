"""REST API routes."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import shutil
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field

from . import __version__
from .auth import require_admin, require_submit
from .logging_setup import redact_url
from .models import (
    ApiKeyCreated,
    ApiKeyInfo,
    Job,
    JobState,
    KeyScope,
    SubmitRequest,
    SubmitResult,
    utcnow,
)
from .ssrf import SSRFError, check_url
from .worker import cleanup_staging, gather_into_dir, merge_dirs, rename_dir

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def _state(request: Request):
    return request.app.state


@router.post("/downloads", response_model=list[SubmitResult], status_code=201)
async def submit(req: SubmitRequest, request: Request,
                 key: ApiKeyInfo = Depends(require_submit)) -> list[SubmitResult]:
    st = _state(request)
    if not st.rate_limiter.allow(key.id):
        raise HTTPException(status_code=429, detail="submit rate limit exceeded")

    if req.dest and (".." in req.dest or req.dest.startswith("/")):
        raise HTTPException(status_code=400, detail="dest must be a relative sub-path")

    results: list[SubmitResult] = []
    for url in req.urls:
        url = url.strip()
        try:
            check_url(url)
        except SSRFError as e:
            results.append(SubmitResult(url=url, accepted=False, reason=str(e)))
            continue
        if not st.engine.supports(url):
            results.append(SubmitResult(
                url=url, accepted=False, reason="no extractor supports this URL"))
            continue
        host = urlparse(url).hostname or ""
        job = await st.db.create_job(url, host, req.dest or "")
        if job is None:  # already queued/active — idempotent accept
            existing = await st.db.find_open_job(url)
            results.append(SubmitResult(
                url=url, accepted=True, job_id=existing.id if existing else None,
                reason="already queued"))
            continue
        results.append(SubmitResult(url=url, accepted=True, job_id=job.id))
        st.hub.publish({"type": "state", "job_id": job.id, "state": job.state.value})
        log.info("queued job %d: %s", job.id, redact_url(url))
    st.workers.notify()
    return results


@router.get("/downloads", response_model=list[Job])
async def list_downloads(request: Request, state: JobState | None = None,
                         limit: int = 100, offset: int = 0,
                         key: ApiKeyInfo = Depends(require_submit)) -> list[Job]:
    return await _state(request).db.list_jobs(state=state, limit=min(limit, 500), offset=offset)


@router.get("/downloads/{job_id}", response_model=Job)
async def get_download(job_id: int, request: Request,
                       key: ApiKeyInfo = Depends(require_submit)) -> Job:
    job = await _state(request).db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


async def _get_job_or_404(request: Request, job_id: int) -> Job:
    job = await _state(request).db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.post("/downloads/{job_id}/pause", response_model=Job)
async def pause(job_id: int, request: Request,
                key: ApiKeyInfo = Depends(require_submit)) -> Job:
    st = _state(request)
    job = await _get_job_or_404(request, job_id)
    if job.state not in (JobState.QUEUED, JobState.ACTIVE):
        raise HTTPException(status_code=409, detail=f"cannot pause a {job.state} job")
    await st.db.set_state(job_id, JobState.PAUSED)
    st.workers.cancel_job(job_id)  # partial files remain; resume re-runs with skip-existing
    st.hub.publish({"type": "state", "job_id": job_id, "state": JobState.PAUSED.value})
    return await _get_job_or_404(request, job_id)


@router.post("/downloads/{job_id}/resume", response_model=Job)
async def resume(job_id: int, request: Request,
                 key: ApiKeyInfo = Depends(require_submit)) -> Job:
    st = _state(request)
    job = await _get_job_or_404(request, job_id)
    if job.state != JobState.PAUSED:
        raise HTTPException(status_code=409, detail=f"cannot resume a {job.state} job")
    await st.db.set_state(job_id, JobState.QUEUED)
    st.hub.publish({"type": "state", "job_id": job_id, "state": JobState.QUEUED.value})
    st.workers.notify()
    return await _get_job_or_404(request, job_id)


@router.post("/downloads/{job_id}/retry", response_model=Job)
async def retry(job_id: int, request: Request,
                key: ApiKeyInfo = Depends(require_submit)) -> Job:
    st = _state(request)
    job = await _get_job_or_404(request, job_id)
    if job.state not in (JobState.ERROR, JobState.CANCELLED, JobState.DONE):
        raise HTTPException(status_code=409, detail=f"cannot retry a {job.state} job")
    await st.db.update_job(job_id, state=JobState.QUEUED.value, error=None, finished_at=None)
    st.hub.publish({"type": "state", "job_id": job_id, "state": JobState.QUEUED.value})
    st.workers.notify()
    return await _get_job_or_404(request, job_id)


class PinRequest(BaseModel):
    pinned: bool


@router.post("/downloads/{job_id}/pin", response_model=Job)
async def pin(job_id: int, req: PinRequest, request: Request,
              key: ApiKeyInfo = Depends(require_submit)) -> Job:
    """Pin or unpin a job.

    A pinned job's source URL is re-checked forever: after each run finishes,
    the worker requeues it every downloads.pin_recheck_minutes, and the
    engine's skip-existing behavior downloads only files added since.
    Unpinning stops the monitoring; the job simply rests in its final state.
    """
    st = _state(request)
    job = await _get_job_or_404(request, job_id)
    if req.pinned and job.state == JobState.CANCELLED:
        raise HTTPException(status_code=409, detail="cannot pin a cancelled job")
    await st.db.update_job(job_id, pinned=int(req.pinned))
    st.hub.publish({"type": "state", "job_id": job_id, "state": job.state.value})
    return await _get_job_or_404(request, job_id)


class RenameRequest(BaseModel):
    name: str


def _validate_dir_name(name: str) -> str:
    name = name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="invalid directory name")
    return name


@router.post("/downloads/{job_id}/rename", response_model=Job)
async def rename(job_id: int, req: RenameRequest, request: Request,
                 key: ApiKeyInfo = Depends(require_submit)) -> Job:
    """Rename a job's output directory.

    Running job: recorded and applied when the download completes (the engine
    process and the staging move both keep writing to the original name).
    Finished job: the directory is renamed on disk immediately.
    """
    st = _state(request)
    name = _validate_dir_name(req.name)
    job = await _get_job_or_404(request, job_id)

    if job.state in (JobState.QUEUED, JobState.ACTIVE, JobState.PAUSED):
        await st.db.update_job(job_id, rename_to=name)
        return await _get_job_or_404(request, job_id)

    base = st.cfg.downloads.dest / job.dest if job.dest else st.cfg.downloads.dest
    if not job.dir_name:
        # Files landed flat in the destination root (keep_dirs off, single
        # files, or a pre-0.2.2 job): gather the job's recorded files into
        # the requested directory instead of renaming one.
        rel_files = await st.db.get_job_files(job_id)
        existing = [rel for rel in rel_files if (base / rel).is_file()]
        if not existing:
            raise HTTPException(status_code=409,
                                detail="job has no output directory to rename")
        await asyncio.to_thread(gather_into_dir, base, existing, base / name)
        await st.db.update_job(job_id, dir_name=name)
    elif name != job.dir_name:
        src = base / job.dir_name
        if not src.is_dir():
            raise HTTPException(status_code=409,
                                detail=f"directory no longer exists: {job.dir_name}")
        await asyncio.to_thread(rename_dir, src, base / name)
        await st.db.update_job(job_id, dir_name=name)
    return await _get_job_or_404(request, job_id)


class MergeRequest(BaseModel):
    job_ids: list[int] = Field(min_length=2, max_length=100)
    name: str


@router.post("/downloads/merge", response_model=list[Job])
async def merge(req: MergeRequest, request: Request,
                key: ApiKeyInfo = Depends(require_submit)) -> list[Job]:
    """Merge the output directories of several completed jobs into one.

    Files from every selected job's directory move into dest/<name> (created
    if needed; filename collisions get a " (2)" suffix). The original job
    records stay in history, all pointing at the merged directory.
    """
    st = _state(request)
    name = _validate_dir_name(req.name)
    if len(set(req.job_ids)) != len(req.job_ids):
        raise HTTPException(status_code=400, detail="duplicate job ids")

    jobs: list[Job] = []
    for job_id in req.job_ids:
        job = await _get_job_or_404(request, job_id)
        if job.state != JobState.DONE:
            raise HTTPException(status_code=409, detail=f"job {job_id} is {job.state}, not done")
        if not job.dir_name:
            raise HTTPException(status_code=409,
                                detail=f"job {job_id} has no output directory")
        jobs.append(job)

    def base(job: Job) -> Path:
        return st.cfg.downloads.dest / job.dest if job.dest else st.cfg.downloads.dest

    sources = [base(j) / j.dir_name for j in jobs]
    missing = [str(s) for s in sources if not s.is_dir()]
    if missing:
        raise HTTPException(status_code=409,
                            detail=f"directory no longer exists: {missing[0]}")

    target = base(jobs[0]) / name
    await asyncio.to_thread(merge_dirs, sources, target)
    for job in jobs:
        await st.db.update_job(job.id, dir_name=name, dest=jobs[0].dest)
    return [await _get_job_or_404(request, j.id) for j in jobs]


@router.delete("/downloads/{job_id}", status_code=204)
async def cancel(job_id: int, request: Request,
                 key: ApiKeyInfo = Depends(require_submit)) -> None:
    st = _state(request)
    job = await _get_job_or_404(request, job_id)
    if job.state in (JobState.QUEUED, JobState.ACTIVE, JobState.PAUSED):
        await st.db.set_state(job_id, JobState.CANCELLED)
        st.workers.cancel_job(job_id)
        st.hub.publish({"type": "state", "job_id": job_id, "state": JobState.CANCELLED.value})
        if job.state != JobState.ACTIVE:
            # No engine process is writing; safe to drop staged partials now.
            # (An active job's worker cleans up after its process exits.)
            cleanup_staging(st.cfg, job_id)
    else:
        # terminal job: remove from history (plus any staging leftovers)
        await st.db.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await st.db.conn.commit()
        cleanup_staging(st.cfg, job_id)


@router.get("/stats")
async def stats(request: Request, key: ApiKeyInfo = Depends(require_submit)) -> dict:
    st = _state(request)
    counts = await st.db.stats()
    disk = shutil.disk_usage(st.cfg.downloads.dest)
    return {
        "queue": counts,
        "active": counts.get(JobState.ACTIVE.value, 0),
        "queued": counts.get(JobState.QUEUED.value, 0),
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "version": __version__,
        # For extension auto-config; null = client falls back to its own origin.
        "public_url": st.cfg.server.public_url,
    }


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/settings")
async def settings(request: Request, key: ApiKeyInfo = Depends(require_admin)) -> dict:
    """Current server configuration for the UI settings page.

    Everything here comes from config.yaml / GRABBIT_* env / the Docker
    deployment at startup, so it is all read-only from the web UI; the shape
    leaves room for editable sections later.
    """
    cfg = _state(request).cfg
    return {
        "version": __version__,
        # All values below are set at deploy time and not editable at runtime.
        "read_only": {
            "server": {
                "port": cfg.server.port,
                "root_path": cfg.server.root_path or None,
                "public_url": cfg.server.public_url,
                "trusted_proxies": cfg.server.trusted_proxies,
            },
            "downloads": {
                "dest": str(cfg.downloads.dest),
                "incomplete_dir":
                    str(cfg.downloads.incomplete_dir) if cfg.downloads.incomplete_dir else None,
                "max_concurrent": cfg.downloads.max_concurrent,
                "max_per_host": cfg.downloads.max_per_host,
                "filename_template": cfg.downloads.filename_template,
                "keep_dirs": cfg.downloads.keep_dirs,
                "pin_recheck_minutes": cfg.downloads.pin_recheck_minutes,
                "reset_mtime": cfg.downloads.reset_mtime,
                "cookies_file":
                    str(cfg.downloads.cookies_file) if cfg.downloads.cookies_file else None,
            },
            "engine": {
                "name": cfg.engine.name,
                "channel": cfg.engine.channel,
                "retries": cfg.engine.retries,
                "rate_limit": cfg.engine.rate_limit,
            },
            "logging": {
                "enabled": cfg.logging.enabled,
                "level": cfg.logging.level,
                "format": cfg.logging.format,
            },
            "metrics": {"enabled": cfg.metrics.enabled},
            "mcp": {"enabled": cfg.mcp.enabled},
            "data_dir": str(cfg.data_dir),
        },
        "editable": {},  # standing home for future runtime-editable settings
    }


def _extension_dir() -> Path | None:
    """Bundled extension source: package data (Docker) or repo checkout (dev)."""
    for candidate in (Path(__file__).parent / "extension",
                      Path(__file__).parent.parent / "extension"):
        if (candidate / "manifest.json").is_file():
            return candidate
    return None


@router.get("/extension.zip")
async def extension_zip(request: Request,
                        key: ApiKeyInfo = Depends(require_admin)) -> Response:
    """Download the Chrome extension, preconfigured for this server.

    Mints a fresh submit-scoped API key and bakes it — together with the
    server's public URL — into a preconfig.json inside the zip; the extension
    reads it on install, so no manual setup is needed. Admin scope, because
    it creates a key.
    """
    ext_dir = _extension_dir()
    if ext_dir is None:
        raise HTTPException(status_code=404, detail="extension not bundled in this build")
    st = _state(request)

    host = st.cfg.server.public_url or str(request.base_url)
    host = host.rstrip("/")
    created = await st.db.create_key(
        f"chrome-extension ({utcnow():%Y-%m-%d %H:%M})", KeyScope.SUBMIT)
    log.info("minted submit key %d for extension download (by key %d)", created.id, key.id)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ext_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(ext_dir))
        zf.writestr("preconfig.json", json.dumps({"host": host, "apiKey": created.token}))
    return Response(
        content=buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="grabbit-extension.zip"'},
    )


# -- API keys (admin) --------------------------------------------------------

class KeyCreateRequest(BaseModel):
    name: str
    scope: KeyScope = KeyScope.SUBMIT


@router.get("/keys", response_model=list[ApiKeyInfo])
async def list_keys(request: Request,
                    key: ApiKeyInfo = Depends(require_admin)) -> list[ApiKeyInfo]:
    return await _state(request).db.list_keys()


@router.post("/keys", response_model=ApiKeyCreated, status_code=201)
async def create_key(req: KeyCreateRequest, request: Request,
                     key: ApiKeyInfo = Depends(require_admin)) -> ApiKeyCreated:
    return await _state(request).db.create_key(req.name, req.scope)


@router.delete("/keys/{key_id}", status_code=204)
async def delete_key(key_id: int, request: Request,
                     key: ApiKeyInfo = Depends(require_admin)) -> None:
    if key_id == key.id:
        raise HTTPException(status_code=409, detail="cannot delete the key you are using")
    if not await _state(request).db.delete_key(key_id):
        raise HTTPException(status_code=404, detail="key not found")


# -- WebSocket ---------------------------------------------------------------

@router.websocket("/ws")
async def ws_events(ws: WebSocket) -> None:
    # Token via query param (browser WebSocket API can't set headers).
    token = ws.query_params.get("token", "")
    db = ws.app.state.db
    if await db.verify_key(token) is None:
        await ws.close(code=4401, reason="invalid API key")
        return
    await ws.accept()
    hub = ws.app.state.hub
    queue = hub.subscribe()
    try:
        while True:
            event = await queue.get()
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
        pass
    finally:
        hub.unsubscribe(queue)
