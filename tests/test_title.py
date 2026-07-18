"""Download title resolution: engine probe, per-job resolve, and backfill."""

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

GOOD_URL = "https://example.com/a/OaplNHfk"


async def wait_for_state(client, key, job_id, state, timeout=10.0):
    async with asyncio.timeout(timeout):
        while True:
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            if r.json()["state"] == state:
                return r.json()
            await asyncio.sleep(0.05)


async def wait_for_title(client, key, job_id, timeout=10.0):
    async with asyncio.timeout(timeout):
        while True:
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            if r.json().get("title"):
                return r.json()["title"]
            await asyncio.sleep(0.05)


# -- engine layer -----------------------------------------------------------

async def test_resolve_title_reads_album_name(mock_engine_binary):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    assert await engine.resolve_title(GOOD_URL) == "Phone1"


async def test_resolve_title_none_when_absent(mock_engine_binary):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    assert await engine.resolve_title("https://example.com/notitle/x") is None


async def test_resolve_title_none_on_failure(mock_engine_binary):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    assert await engine.resolve_title("https://example.com/fail") is None


def test_extract_title_prefers_directory_over_url():
    dump = (b'[[3, "u", {"title": "from-url"}],'
            b' [2, {"album_name": "from-dir"}]]')
    assert GalleryDLEngine._extract_title(dump) == "from-dir"


def test_extract_title_falls_back_to_url_meta():
    dump = b'[[3, "u", {"title": "just-a-title"}]]'
    assert GalleryDLEngine._extract_title(dump) == "just-a-title"


def test_extract_title_handles_garbage():
    assert GalleryDLEngine._extract_title(b"not json") is None
    assert GalleryDLEngine._extract_title(b"{}") is None
    assert GalleryDLEngine._extract_title(b'[[2, {}]]') is None


# -- per-job resolution -----------------------------------------------------

async def test_job_gets_title_on_download(client, submit_key):
    r = await client.post("/api/downloads", json={"urls": [GOOD_URL]}, headers=auth(submit_key))
    job_id = r.json()[0]["job_id"]
    assert await wait_for_title(client, submit_key, job_id) == "Phone1"


async def test_title_does_not_change_dir_name(client, submit_key):
    r = await client.post("/api/downloads", json={"urls": [GOOD_URL]}, headers=auth(submit_key))
    job_id = r.json()[0]["job_id"]
    await wait_for_state(client, submit_key, job_id, "done")
    await wait_for_title(client, submit_key, job_id)
    r = await client.get(f"/api/downloads/{job_id}", headers=auth(submit_key))
    # Directory on disk stays the mock's "album"; the title is separate.
    assert r.json()["dir_name"] == "album"
    assert r.json()["title"] == "Phone1"


async def test_no_title_left_null(client, submit_key):
    r = await client.post("/api/downloads", json={"urls": ["https://example.com/notitle/x"]},
                          headers=auth(submit_key))
    job_id = r.json()[0]["job_id"]
    await wait_for_state(client, submit_key, job_id, "done")
    await asyncio.sleep(0.3)
    r = await client.get(f"/api/downloads/{job_id}", headers=auth(submit_key))
    assert r.json()["title"] is None


# -- startup backfill -------------------------------------------------------

@pytest.fixture
def bf_cfg(tmp_path: Path) -> Config:
    dl = tmp_path / "downloads"
    dl.mkdir()
    return Config.model_validate({
        "data_dir": str(tmp_path / "config"),
        "downloads": {"dest": str(dl), "max_concurrent": 2, "max_per_host": 1},
        "logging": {"enabled": False},
    })


async def test_backfill_resolves_existing_untitled_jobs(bf_cfg, mock_engine_binary, monkeypatch):
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    # Seed a finished job with no title, as if downloaded before the feature.
    db = Database(bf_cfg.db_path)
    await db.open()
    job = await db.create_job(GOOD_URL, "example.com", "")
    await db.set_state(job.id, JobState.DONE)
    await db.close()

    engine = GalleryDLEngine(binary=mock_engine_binary)
    app = create_app(bf_cfg, engine=engine)
    async with app.router.lifespan_context(app):
        key = (await app.state.db.create_key("t", KeyScope.SUBMIT)).token
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            assert await wait_for_title(client, key, job.id, timeout=15) == "Phone1"


async def test_resolve_titles_off_skips(tmp_path, mock_engine_binary, monkeypatch):
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    dl = tmp_path / "downloads"
    dl.mkdir()
    cfg = Config.model_validate({
        "data_dir": str(tmp_path / "config"),
        "downloads": {"dest": str(dl), "max_concurrent": 2, "max_per_host": 1,
                      "resolve_titles": False},
        "logging": {"enabled": False},
    })
    engine = GalleryDLEngine(binary=mock_engine_binary)
    app = create_app(cfg, engine=engine)
    async with app.router.lifespan_context(app):
        key = (await app.state.db.create_key("t", KeyScope.SUBMIT)).token
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post("/api/downloads", json={"urls": [GOOD_URL]}, headers=auth(key))
            job_id = r.json()[0]["job_id"]
            await wait_for_state(client, key, job_id, "done")
            await asyncio.sleep(0.3)
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            assert r.json()["title"] is None
