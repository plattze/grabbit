"""Pinned downloads: POST /api/downloads/{id}/pin + periodic source recheck."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from grabbit.app import create_app
from grabbit.config import Config
from grabbit.engine import GalleryDLEngine
from grabbit.models import KeyScope

from .conftest import auth

GOOD_URL = "https://example.com/album"


async def wait_for_state(client, key, job_id, state, timeout=10.0):
    async with asyncio.timeout(timeout):
        while True:
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            if r.json()["state"] == state:
                return r.json()
            await asyncio.sleep(0.05)


async def _submit(client, key, url=GOOD_URL):
    r = await client.post("/api/downloads", json={"urls": [url]}, headers=auth(key))
    return r.json()[0]["job_id"]


async def test_pin_toggle(client, submit_key):
    job_id = await _submit(client, submit_key)
    await wait_for_state(client, submit_key, job_id, "done")

    r = await client.post(f"/api/downloads/{job_id}/pin",
                          json={"pinned": True}, headers=auth(submit_key))
    assert r.status_code == 200 and r.json()["pinned"] is True

    r = await client.post(f"/api/downloads/{job_id}/pin",
                          json={"pinned": False}, headers=auth(submit_key))
    assert r.status_code == 200 and r.json()["pinned"] is False


async def test_pin_cancelled_job_409(client, submit_key):
    job_id = await _submit(client, submit_key, "https://example.com/slow/x")
    await client.delete(f"/api/downloads/{job_id}", headers=auth(submit_key))
    await wait_for_state(client, submit_key, job_id, "cancelled")
    r = await client.post(f"/api/downloads/{job_id}/pin",
                          json={"pinned": True}, headers=auth(submit_key))
    assert r.status_code == 409


async def test_pin_requires_auth(client):
    r = await client.post("/api/downloads/1/pin", json={"pinned": True})
    assert r.status_code in (401, 403)


async def test_pinned_jobs_sort_first(client, submit_key):
    first = await _submit(client, submit_key, "https://example.com/a")
    second = await _submit(client, submit_key, "https://example.com/b")
    await wait_for_state(client, submit_key, first, "done")
    await wait_for_state(client, submit_key, second, "done")

    await client.post(f"/api/downloads/{first}/pin",
                      json={"pinned": True}, headers=auth(submit_key))
    r = await client.get("/api/downloads", headers=auth(submit_key))
    ids = [j["id"] for j in r.json()]
    assert ids.index(first) < ids.index(second)


@pytest.fixture
def pin_cfg(tmp_path: Path) -> Config:
    dl = tmp_path / "downloads"
    dl.mkdir()
    return Config.model_validate({
        "data_dir": str(tmp_path / "config"),
        "downloads": {"dest": str(dl), "max_concurrent": 2, "max_per_host": 1,
                      # ~0.06 s between rechecks so the test observes one quickly
                      "pin_recheck_minutes": 0.001},
        "logging": {"enabled": False},
    })


@pytest.fixture
async def pin_app(pin_cfg, mock_engine_binary, monkeypatch):
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    engine = GalleryDLEngine(binary=mock_engine_binary)
    application = create_app(pin_cfg, engine=engine)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def pin_client(pin_app):
    transport = ASGITransport(app=pin_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def pin_key(pin_app) -> str:
    return (await pin_app.state.db.create_key("t", KeyScope.SUBMIT)).token


async def test_pinned_job_is_rechecked(pin_client, pin_key, pin_cfg):
    job_id = await _submit(pin_client, pin_key)
    first = await wait_for_state(pin_client, pin_key, job_id, "done")

    r = await pin_client.post(f"/api/downloads/{job_id}/pin",
                              json={"pinned": True}, headers=auth(pin_key))
    assert r.json()["pinned"] is True

    # The pin loop requeues the job and it completes again.
    async with asyncio.timeout(15):
        while True:
            r = await pin_client.get(f"/api/downloads/{job_id}", headers=auth(pin_key))
            job = r.json()
            if job["state"] == "done" and job["finished_at"] != first["finished_at"]:
                break
            await asyncio.sleep(0.05)
    assert job["pinned"] is True  # still pinned after the recheck
    assert len(list((pin_cfg.downloads.dest / "album").glob("*.jpg"))) == 3


async def test_recheck_honors_renamed_directory(pin_client, pin_key, pin_cfg):
    job_id = await _submit(pin_client, pin_key)
    first = await wait_for_state(pin_client, pin_key, job_id, "done")

    r = await pin_client.post(f"/api/downloads/{job_id}/rename",
                              json={"name": "My Album"}, headers=auth(pin_key))
    assert r.status_code == 200
    await pin_client.post(f"/api/downloads/{job_id}/pin",
                          json={"pinned": True}, headers=auth(pin_key))

    async with asyncio.timeout(15):
        while True:
            r = await pin_client.get(f"/api/downloads/{job_id}", headers=auth(pin_key))
            job = r.json()
            if job["state"] == "done" and job["finished_at"] != first["finished_at"]:
                break
            await asyncio.sleep(0.05)
    # New files from the recheck end up in the renamed directory, not "album".
    assert job["dir_name"] == "My Album"
    assert not (pin_cfg.downloads.dest / "album").exists()
    assert len(list((pin_cfg.downloads.dest / "My Album").glob("*.jpg"))) == 3


async def test_unpinned_job_is_not_rechecked(pin_client, pin_key):
    job_id = await _submit(pin_client, pin_key)
    first = await wait_for_state(pin_client, pin_key, job_id, "done")
    await asyncio.sleep(0.5)  # several recheck intervals
    r = await pin_client.get(f"/api/downloads/{job_id}", headers=auth(pin_key))
    assert r.json()["state"] == "done"
    assert r.json()["finished_at"] == first["finished_at"]
