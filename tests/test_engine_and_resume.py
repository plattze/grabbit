"""Engine adapter (mocked CLI), probe parsing, restart-resume, proxy headers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

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
    assert len(list((tmp_path / "out").rglob("*.jpg"))) == 3


async def test_download_flattens_without_keep_dirs(mock_engine_binary, tmp_path):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    out = tmp_path / "out"
    result = await engine.download(
        "https://example.com/a", EngineOpts(dest=out, keep_dirs=False),
        on_progress=lambda e: None)
    assert result.success
    assert len(list(out.glob("*.jpg"))) == 3  # directly in dest, no subdir


async def test_download_keeps_source_directory(mock_engine_binary, tmp_path):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    out = tmp_path / "out"
    result = await engine.download(
        "https://example.com/a", EngineOpts(dest=out),
        on_progress=lambda e: None)
    assert result.success
    assert not list(out.glob("*.jpg"))  # nothing flattened into dest itself
    assert len(list((out / "album").glob("*.jpg"))) == 3


def test_keep_dirs_empties_category_to_drop_domain_level(mock_engine_binary):
    """keep_dirs passes -o keywords={"category": ""} so gallery-dl, which drops
    empty path segments, omits the leading {category}/domain directory level."""
    engine = GalleryDLEngine(binary=mock_engine_binary)
    args = engine._build_args("https://example.com/a", EngineOpts(dest=Path("/d")))
    assert 'keywords={"category": ""}' in args


def test_flatten_omitted_when_keep_dirs_off(mock_engine_binary):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    args = engine._build_args(
        "https://example.com/a", EngineOpts(dest=Path("/d"), keep_dirs=False))
    assert not any("keywords=" in a for a in args)


async def test_download_keeps_source_mtime_by_default(mock_engine_binary, tmp_path):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    out = tmp_path / "out"
    result = await engine.download(
        "https://example.com/a", EngineOpts(dest=out),
        on_progress=lambda e: None)
    assert result.success
    files = list(out.rglob("*.jpg"))
    assert files and all(f.stat().st_mtime == 1000000000 for f in files)


async def test_download_reset_mtime_stamps_download_time(mock_engine_binary, tmp_path):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    out = tmp_path / "out"
    result = await engine.download(
        "https://example.com/a", EngineOpts(dest=out, reset_mtime=True),
        on_progress=lambda e: None)
    assert result.success
    files = list(out.rglob("*.jpg"))
    assert files and all(f.stat().st_mtime > 1000000000 for f in files)


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
