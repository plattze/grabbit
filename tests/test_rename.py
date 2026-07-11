"""POST /api/downloads/{id}/rename — job output-directory rename."""

from __future__ import annotations

import asyncio

from .conftest import auth

GOOD_URL = "https://example.com/album"
SLOW_URL = "https://example.com/slow/album"


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


async def test_done_job_records_dir_name(client, submit_key, cfg):
    job_id = await _submit(client, submit_key)
    job = await wait_for_state(client, submit_key, job_id, "done")
    assert job["dir_name"] == "album"  # mock CLI writes into <dest>/album/


async def test_rename_after_completion_moves_directory(client, submit_key, cfg):
    job_id = await _submit(client, submit_key)
    await wait_for_state(client, submit_key, job_id, "done")

    r = await client.post(f"/api/downloads/{job_id}/rename",
                          json={"name": "My Album"}, headers=auth(submit_key))
    assert r.status_code == 200
    assert r.json()["dir_name"] == "My Album"
    assert not (cfg.downloads.dest / "album").exists()
    assert len(list((cfg.downloads.dest / "My Album").glob("*.jpg"))) == 3


async def test_rename_during_download_applies_at_completion(client, submit_key, cfg):
    job_id = await _submit(client, submit_key, SLOW_URL)

    r = await client.post(f"/api/downloads/{job_id}/rename",
                          json={"name": "Renamed Live"}, headers=auth(submit_key))
    assert r.status_code == 200
    assert r.json()["rename_to"] == "Renamed Live"

    job = await wait_for_state(client, submit_key, job_id, "done", timeout=30.0)
    assert job["dir_name"] == "Renamed Live"
    assert job["rename_to"] is None
    assert not (cfg.downloads.dest / "album").exists()
    assert list((cfg.downloads.dest / "Renamed Live").glob("*.jpg"))


async def test_rename_merges_into_existing_directory(client, submit_key, cfg):
    (cfg.downloads.dest / "Existing").mkdir()
    (cfg.downloads.dest / "Existing" / "keep.txt").write_text("k")

    job_id = await _submit(client, submit_key)
    await wait_for_state(client, submit_key, job_id, "done")
    r = await client.post(f"/api/downloads/{job_id}/rename",
                          json={"name": "Existing"}, headers=auth(submit_key))
    assert r.status_code == 200
    merged = cfg.downloads.dest / "Existing"
    assert (merged / "keep.txt").exists()
    assert len(list(merged.glob("*.jpg"))) == 3


async def test_rename_rejects_path_traversal(client, submit_key):
    job_id = await _submit(client, submit_key)
    await wait_for_state(client, submit_key, job_id, "done")
    for bad in ("../escape", "a/b", "a\\b", "", "  ", ".."):
        r = await client.post(f"/api/downloads/{job_id}/rename",
                              json={"name": bad}, headers=auth(submit_key))
        assert r.status_code == 400, bad


async def test_rename_unknown_job_404(client, submit_key):
    r = await client.post("/api/downloads/9999/rename",
                          json={"name": "x"}, headers=auth(submit_key))
    assert r.status_code == 404


async def test_rename_requires_auth(client):
    r = await client.post("/api/downloads/1/rename", json={"name": "x"})
    assert r.status_code in (401, 403)


async def test_db_migration_adds_rename_columns(tmp_path):
    """A pre-0.2.2 database (no dir_name/rename_to) opens and works."""
    import aiosqlite

    from grabbit.db import Database

    db_path = tmp_path / "old.db"
    conn = await aiosqlite.connect(db_path)
    await conn.executescript("""
        CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL, host TEXT NOT NULL, state TEXT NOT NULL,
            dest TEXT NOT NULL, files_total INTEGER NOT NULL DEFAULT 0,
            files_done INTEGER NOT NULL DEFAULT 0,
            bytes_done INTEGER NOT NULL DEFAULT 0, error TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT
        );
        INSERT INTO jobs (url, host, state, dest, created_at, updated_at)
        VALUES ('https://e.com/x', 'e.com', 'done', '', '2026-01-01', '2026-01-01');
    """)
    await conn.commit()
    await conn.close()

    db = Database(db_path)
    await db.open()
    job = await db.get_job(1)
    assert job is not None and job.dir_name == "" and job.rename_to is None
    await db.close()
