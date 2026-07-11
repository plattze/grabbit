"""REST API routes."""

from __future__ import annotations

import asyncio
import logging
import shutil
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import __version__
from .auth import require_admin, require_submit
from .logging_setup import redact_url
from .models import ApiKeyCreated, ApiKeyInfo, Job, JobState, KeyScope, SubmitRequest, SubmitResult
from .ssrf import SSRFError, check_url

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


@router.delete("/downloads/{job_id}", status_code=204)
async def cancel(job_id: int, request: Request,
                 key: ApiKeyInfo = Depends(require_submit)) -> None:
    st = _state(request)
    job = await _get_job_or_404(request, job_id)
    if job.state in (JobState.QUEUED, JobState.ACTIVE, JobState.PAUSED):
        await st.db.set_state(job_id, JobState.CANCELLED)
        st.workers.cancel_job(job_id)
        st.hub.publish({"type": "state", "job_id": job_id, "state": JobState.CANCELLED.value})
    else:
        # terminal job: remove from history
        await st.db.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await st.db.conn.commit()


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
    }


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


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
