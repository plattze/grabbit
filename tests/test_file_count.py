"""Live file-count probe: files_total populated up front for a running job."""

from __future__ import annotations

import asyncio
import sys

from httpx import ASGITransport, AsyncClient

from grabbit.app import create_app
from grabbit.config import Config
from grabbit.engine import GalleryDLEngine
from grabbit.models import KeyScope

from .conftest import auth

GOOD_URL = "https://example.com/a/OaplNHfk"


async def wait_for(client, key, job_id, pred, timeout=10.0):
    async with asyncio.timeout(timeout):
        while True:
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            body = r.json()
            if pred(body):
                return body
            await asyncio.sleep(0.05)


async def test_files_total_probed_while_active(client, submit_key):
    # A slow download stays active long enough for the up-front probe (which
    # the mock reports as one file) to land before completion.
    r = await client.post("/api/downloads", json={"urls": ["https://example.com/slow"]},
                          headers=auth(submit_key))
    job_id = r.json()[0]["job_id"]
    body = await wait_for(client, submit_key, job_id,
                          lambda b: b["state"] == "active" and b["files_total"] > 0)
    assert body["files_total"] == 1


async def test_files_total_finalized_on_done(client, submit_key):
    # At completion files_total is set to the real downloaded count (3), so the
    # finished row shows a truthful total regardless of the probe estimate.
    r = await client.post("/api/downloads", json={"urls": [GOOD_URL]}, headers=auth(submit_key))
    job_id = r.json()[0]["job_id"]
    body = await wait_for(client, submit_key, job_id, lambda b: b["state"] == "done")
    assert body["files_total"] == 3
    assert body["files_done"] == 3


async def test_count_files_off_skips(tmp_path, mock_engine_binary, monkeypatch):
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    dl = tmp_path / "downloads"
    dl.mkdir()
    cfg = Config.model_validate({
        "data_dir": str(tmp_path / "config"),
        "downloads": {"dest": str(dl), "max_concurrent": 2, "max_per_host": 1,
                      "count_files": False},
        "logging": {"enabled": False},
    })
    engine = GalleryDLEngine(binary=mock_engine_binary)
    app = create_app(cfg, engine=engine)
    async with app.router.lifespan_context(app):
        key = (await app.state.db.create_key("t", KeyScope.SUBMIT)).token
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post("/api/downloads", json={"urls": ["https://example.com/slow"]},
                                  headers=auth(key))
            job_id = r.json()[0]["job_id"]
            # While active (before completion finalizes it), files_total stays 0.
            await wait_for(client, key, job_id, lambda b: b["state"] == "active")
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            assert r.json()["files_total"] == 0
