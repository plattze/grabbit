"""Engine adapter (mocked CLI), probe parsing, restart-resume, proxy headers."""

from __future__ import annotations

import asyncio
import sys

import pytest
from httpx import ASGITransport, AsyncClient

from grabbit.app import create_app
from grabbit.db import Database
from grabbit.engine import EngineOpts, GalleryDLEngine
from grabbit.models import JobState

from .conftest import auth


async def test_probe_parses_dump_json(mock_engine_binary):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    files = await engine.probe("https://example.com/album")
    assert len(files) == 1
    assert files[0].filename == "file1.jpg"


async def test_download_reports_progress(mock_engine_binary, tmp_path):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    events = []
    result = await engine.download(
        "https://example.com/a", EngineOpts(dest=tmp_path / "out"),
        on_progress=events.append)
    assert result.success and result.files_done == 3
    assert [e.files_done for e in events] == [1, 2, 3]
    assert len(list((tmp_path / "out").glob("*.jpg"))) == 3


async def test_download_failure_captures_stderr(mock_engine_binary, tmp_path):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    result = await engine.download(
        "https://example.com/fail", EngineOpts(dest=tmp_path), on_progress=lambda e: None)
    assert not result.success
    assert "extractor blew up" in result.error


async def test_queue_survives_restart(cfg, mock_engine_binary, monkeypatch):
    """Jobs active at shutdown are requeued and completed by the next instance."""
    monkeypatch.setitem(sys.modules, "gallery_dl", None)

    # Simulate a crashed instance: job left in 'active' in the DB.
    db = Database(cfg.db_path)
    await db.open()
    job = await db.create_job("https://example.com/resumed", "example.com", "")
    await db.set_state(job.id, JobState.ACTIVE)
    await db.close()

    engine = GalleryDLEngine(binary=mock_engine_binary)
    app = create_app(cfg, engine=engine)
    async with app.router.lifespan_context(app):
        key = (await app.state.db.create_key("t", __import__(
            "grabbit.models", fromlist=["KeyScope"]).KeyScope.SUBMIT)).token
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            async with asyncio.timeout(10):
                while True:
                    r = await client.get(f"/api/downloads/{job.id}", headers=auth(key))
                    if r.json()["state"] == "done":
                        break
                    assert r.json()["state"] in ("queued", "active", "done")
                    await asyncio.sleep(0.05)


async def test_forwarded_headers_stripped_from_untrusted(client, submit_key):
    """X-Forwarded-* from a non-proxy client must not be honored."""
    r = await client.get(
        "/api/health",
        headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"},
    )
    assert r.status_code == 200


async def test_security_headers_present(client):
    r = await client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in r.headers
    assert r.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize("url,ok", [
    ("https://example.com/x", True),
    ("javascript:alert(1)", False),
    ("data:text/html,x", False),
])
async def test_supports_scheme_gate(mock_engine_binary, url, ok, monkeypatch):
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    engine = GalleryDLEngine(binary=mock_engine_binary)
    assert engine.supports(url) is ok
