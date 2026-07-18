"""Auto-retry: a global loop periodically requeues failed downloads."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from grabbit.app import create_app
from grabbit.config import Config
from grabbit.db import Database
from grabbit.engine import GalleryDLEngine
from grabbit.models import JobState, KeyScope

from .conftest import auth


def _cfg(tmp_path: Path, **downloads) -> Config:
    dl = tmp_path / "downloads"
    dl.mkdir()
    return Config.model_validate({
        "data_dir": str(tmp_path / "config"),
        "downloads": {"dest": str(dl), "max_concurrent": 2, "max_per_host": 1,
                      # keep the title backfill from probing during these tests
                      "resolve_titles": False, **downloads},
        "logging": {"enabled": False},
    })


async def _make_app(cfg, mock_engine_binary, monkeypatch):
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    engine = GalleryDLEngine(binary=mock_engine_binary)
    return create_app(cfg, engine=engine)


async def _seed_error(db: Database, url: str = "https://example.com/x") -> int:
    """Insert a job already sitting in ERROR, as if a download had failed."""
    job = await db.create_job(url, "example.com", "")
    await db.set_state(job.id, JobState.ERROR, error="boom")
    return job.id


async def _get(client, key, job_id):
    r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
    return r.json()


# ~0.9 s between retry passes: brisk enough to observe, gentle enough not to
# stampede the mock engine with subprocesses.
FAST_RETRY = 0.015


async def test_failed_job_is_auto_retried(tmp_path, mock_engine_binary, monkeypatch):
    cfg = _cfg(tmp_path, auto_retry=True, auto_retry_minutes=FAST_RETRY)
    app = await _make_app(cfg, mock_engine_binary, monkeypatch)
    async with app.router.lifespan_context(app):
        key = (await app.state.db.create_key("t", KeyScope.SUBMIT)).token
        # A good URL: after the retry requeues it, it completes.
        job_id = await _seed_error(app.state.db, "https://example.com/good")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            async with asyncio.timeout(15):
                while (await _get(client, key, job_id))["state"] != "done":
                    await asyncio.sleep(0.1)


async def test_auto_retry_disabled_leaves_failures(tmp_path, mock_engine_binary, monkeypatch):
    cfg = _cfg(tmp_path, auto_retry=False, auto_retry_minutes=FAST_RETRY)
    app = await _make_app(cfg, mock_engine_binary, monkeypatch)
    async with app.router.lifespan_context(app):
        key = (await app.state.db.create_key("t", KeyScope.SUBMIT)).token
        job_id = await _seed_error(app.state.db, "https://example.com/good")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await asyncio.sleep(1.5)  # several would-be retry intervals
            assert (await _get(client, key, job_id))["state"] == "error"


async def test_auto_retry_skips_pinned(tmp_path, mock_engine_binary, monkeypatch):
    # A pinned failed job is left to the pin loop; the auto-retry pass with a
    # very slow pin recheck must not touch it.
    cfg = _cfg(tmp_path, auto_retry=True, auto_retry_minutes=FAST_RETRY,
               pin_recheck_minutes=1000)
    app = await _make_app(cfg, mock_engine_binary, monkeypatch)
    async with app.router.lifespan_context(app):
        key = (await app.state.db.create_key("t", KeyScope.SUBMIT)).token
        job_id = await _seed_error(app.state.db, "https://example.com/good")
        await app.state.db.update_job(job_id, pinned=1)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            await asyncio.sleep(1.5)  # several auto-retry intervals
            job = await _get(client, key, job_id)
            assert job["state"] == "error" and job["pinned"] is True


async def test_list_failed_unpinned_excludes_pinned_and_nonerror(tmp_path):
    """DB layer: only unpinned ERROR jobs are auto-retry candidates."""
    db = Database(tmp_path / "config" / "grabbit.db")
    await db.open()
    err = await _seed_error(db, "https://example.com/a")
    pinned_err = await _seed_error(db, "https://example.com/b")
    await db.update_job(pinned_err, pinned=1)
    done = (await db.create_job("https://example.com/c", "example.com", "")).id
    await db.set_state(done, JobState.DONE)

    ids = [j.id for j in await db.list_failed_unpinned()]
    await db.close()
    assert ids == [err]


@pytest.mark.parametrize("env,field,expected", [
    ("GRABBIT_AUTO_RETRY", "auto_retry", False),
    ("GRABBIT_AUTO_RETRY_MINUTES", "auto_retry_minutes", 30.0),
])
def test_auto_retry_env_overrides(monkeypatch, env, field, expected):
    from grabbit.config import load_config
    monkeypatch.setenv(env, "false" if field == "auto_retry" else "30")
    cfg = load_config(Path("/nonexistent.yaml"))
    assert getattr(cfg.downloads, field) == expected
