"""incomplete/ → complete/ staging layout (downloads.incomplete_dir)."""

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
from grabbit.worker import _move_tree, cleanup_staging, staging_dir

from .conftest import auth


@pytest.fixture
def staged_cfg(tmp_path: Path) -> Config:
    dl = tmp_path / "downloads"
    dl.mkdir()
    return Config.model_validate({
        "data_dir": str(tmp_path / "config"),
        "downloads": {
            "dest": str(dl),
            "incomplete_dir": str(tmp_path / "incomplete"),
            "max_concurrent": 2, "max_per_host": 1,
        },
        "logging": {"enabled": False},
    })


@pytest.fixture
async def staged_app(staged_cfg, mock_engine_binary, monkeypatch):
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    engine = GalleryDLEngine(binary=mock_engine_binary)
    application = create_app(staged_cfg, engine=engine)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def staged_client(staged_app):
    transport = ASGITransport(app=staged_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def key(staged_app) -> str:
    return (await staged_app.state.db.create_key("t", KeyScope.SUBMIT)).token


async def _wait_state(client, key, job_id, states, timeout=10.0):
    async with asyncio.timeout(timeout):
        while True:
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            if r.json()["state"] in states:
                return r.json()
            await asyncio.sleep(0.05)


def test_staging_dir_disabled_by_default(cfg):
    assert cfg.downloads.incomplete_dir is None
    assert staging_dir(cfg, 1) is None


def test_move_tree_preserves_structure(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src / "sub").mkdir(parents=True)
    (src / "a.jpg").write_text("a")
    (src / "sub" / "b.jpg").write_text("b")
    (dst).mkdir()
    (dst / "a.jpg").write_text("old")  # gets overwritten (skip-existing re-run)
    _move_tree(src, dst)
    assert (dst / "a.jpg").read_text() == "a"
    assert (dst / "sub" / "b.jpg").read_text() == "b"
    assert not src.exists()


async def test_done_job_lands_in_dest(staged_app, staged_client, key, staged_cfg):
    r = await staged_client.post("/api/downloads", headers=auth(key),
                                 json={"urls": ["https://example.com/album"],
                                       "dest": "albums"})
    job_id = r.json()[0]["job_id"]
    await _wait_state(staged_client, key, job_id, ("done",))

    final = staged_cfg.downloads.dest / "albums"
    # keep_dirs: the source's directory name survives the staging move
    assert len(list((final / "album").glob("*.jpg"))) == 3
    # staging cleaned up
    assert not (staged_cfg.downloads.incomplete_dir / f"job-{job_id}").exists()


async def test_active_job_writes_to_staging_not_dest(staged_app, staged_client,
                                                     key, staged_cfg):
    r = await staged_client.post("/api/downloads", headers=auth(key),
                                 json={"urls": ["https://example.com/slow"]})
    job_id = r.json()[0]["job_id"]
    stage = staged_cfg.downloads.incomplete_dir / f"job-{job_id}"

    # While active: files appear in staging, dest stays clean
    async with asyncio.timeout(10):
        while not list(stage.rglob("*.jpg")) if stage.exists() else True:
            await asyncio.sleep(0.05)
    assert not list(staged_cfg.downloads.dest.rglob("*.jpg"))

    # Cancel; staged partials are removed
    await staged_client.delete(f"/api/downloads/{job_id}", headers=auth(key))
    await _wait_state(staged_client, key, job_id, ("cancelled",))
    async with asyncio.timeout(5):
        while stage.exists():
            await asyncio.sleep(0.05)
    assert not list(staged_cfg.downloads.dest.rglob("*.jpg"))


def test_cleanup_staging_noop_when_disabled(cfg):
    cleanup_staging(cfg, 42)  # must not raise


async def test_reset_mtime_survives_staging_move(tmp_path, mock_engine_binary,
                                                 monkeypatch):
    """downloads.reset_mtime applies to the final files in dest — the
    incomplete/ → complete move preserves the download-time timestamps."""
    dl = tmp_path / "downloads"
    dl.mkdir()
    cfg = Config.model_validate({
        "data_dir": str(tmp_path / "config"),
        "downloads": {
            "dest": str(dl),
            "incomplete_dir": str(tmp_path / "incomplete"),
            "reset_mtime": True,
            "max_concurrent": 2, "max_per_host": 1,
        },
        "logging": {"enabled": False},
    })
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    app = create_app(cfg, engine=GalleryDLEngine(binary=mock_engine_binary))
    async with app.router.lifespan_context(app):
        key = (await app.state.db.create_key("t", KeyScope.SUBMIT)).token
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://test") as client:
            r = await client.post("/api/downloads", headers=auth(key),
                                  json={"urls": ["https://example.com/album"]})
            job_id = r.json()[0]["job_id"]
            await _wait_state(client, key, job_id, ("done",))
    files = list(dl.rglob("*.jpg"))
    # Mock CLI backdates to mtime 1000000000 unless mtime=false is passed.
    assert files and all(f.stat().st_mtime > 1000000000 for f in files)
