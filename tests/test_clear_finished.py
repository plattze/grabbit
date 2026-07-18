"""Bulk clear of finished downloads: POST /api/downloads/clear-finished."""

from __future__ import annotations

import asyncio

from .conftest import auth

GOOD_URL = "https://example.com/album"


async def wait_for_state(client, key, job_id, state, timeout=10.0):
    async with asyncio.timeout(timeout):
        while True:
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            if r.json()["state"] == state:
                return r.json()
            await asyncio.sleep(0.05)


async def _submit(client, key, url):
    r = await client.post("/api/downloads", json={"urls": [url]}, headers=auth(key))
    return r.json()[0]["job_id"]


async def test_clear_finished_removes_done_jobs(client, submit_key):
    a = await _submit(client, submit_key, "https://example.com/a")
    b = await _submit(client, submit_key, "https://example.com/b")
    await wait_for_state(client, submit_key, a, "done")
    await wait_for_state(client, submit_key, b, "done")

    r = await client.post("/api/downloads/clear-finished", headers=auth(submit_key))
    assert r.status_code == 200 and r.json()["removed"] == 2

    r = await client.get("/api/downloads", headers=auth(submit_key))
    assert r.json() == []


async def test_clear_finished_keeps_pinned(client, submit_key):
    pinned = await _submit(client, submit_key, "https://example.com/a")
    plain = await _submit(client, submit_key, "https://example.com/b")
    await wait_for_state(client, submit_key, pinned, "done")
    await wait_for_state(client, submit_key, plain, "done")
    await client.post(f"/api/downloads/{pinned}/pin",
                      json={"pinned": True}, headers=auth(submit_key))

    r = await client.post("/api/downloads/clear-finished", headers=auth(submit_key))
    assert r.json()["removed"] == 1

    r = await client.get("/api/downloads", headers=auth(submit_key))
    ids = [j["id"] for j in r.json()]
    assert ids == [pinned]


async def test_clear_finished_keeps_active_and_queued(client, submit_key):
    # A slow job stays active; a done job is cleared. Nothing terminal-but-active.
    done = await _submit(client, submit_key, "https://example.com/a")
    await wait_for_state(client, submit_key, done, "done")
    slow = await _submit(client, submit_key, "https://example.com/slow/x")
    await wait_for_state(client, submit_key, slow, "active")

    r = await client.post("/api/downloads/clear-finished", headers=auth(submit_key))
    assert r.json()["removed"] == 1

    r = await client.get("/api/downloads", headers=auth(submit_key))
    ids = [j["id"] for j in r.json()]
    assert ids == [slow]


async def test_clear_finished_clears_cancelled(client, submit_key):
    cancelled = await _submit(client, submit_key, "https://example.com/slow/x")
    await wait_for_state(client, submit_key, cancelled, "active")
    await client.delete(f"/api/downloads/{cancelled}", headers=auth(submit_key))
    await wait_for_state(client, submit_key, cancelled, "cancelled")

    r = await client.post("/api/downloads/clear-finished", headers=auth(submit_key))
    assert r.json()["removed"] == 1
    r = await client.get("/api/downloads", headers=auth(submit_key))
    assert r.json() == []


async def test_clear_finished_keeps_errored(client, submit_key):
    # Errored jobs have not finished — they may still be retried — so they are
    # kept, while a done job alongside is cleared.
    errored = await _submit(client, submit_key, "https://example.com/fail")
    done = await _submit(client, submit_key, "https://example.com/ok")
    await wait_for_state(client, submit_key, errored, "error")
    await wait_for_state(client, submit_key, done, "done")

    r = await client.post("/api/downloads/clear-finished", headers=auth(submit_key))
    assert r.json()["removed"] == 1

    r = await client.get("/api/downloads", headers=auth(submit_key))
    ids = [j["id"] for j in r.json()]
    assert ids == [errored]


async def test_clear_finished_requires_auth(client):
    r = await client.post("/api/downloads/clear-finished")
    assert r.status_code in (401, 403)
