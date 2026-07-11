"""Queue lifecycle, auth scopes, SSRF rejection — through the HTTP API."""

from __future__ import annotations

import asyncio

from .conftest import auth

GOOD_URL = "https://example.com/album/1"


async def wait_for_state(client, key, job_id, target, timeout=10.0):
    async with asyncio.timeout(timeout):
        while True:
            r = await client.get(f"/api/downloads/{job_id}", headers=auth(key))
            job = r.json()
            if job["state"] == target:
                return job
            if job["state"] in ("error", "done", "cancelled") and job["state"] != target:
                raise AssertionError(f"job hit terminal state {job['state']}, wanted {target}")
            await asyncio.sleep(0.05)


# -- auth ---------------------------------------------------------------------

async def test_no_token_rejected(client):
    r = await client.post("/api/downloads", json={"urls": [GOOD_URL]})
    assert r.status_code == 401


async def test_bad_token_rejected(client):
    r = await client.get("/api/downloads", headers=auth("nope"))
    assert r.status_code == 401


async def test_submit_scope_cannot_manage_keys(client, submit_key):
    r = await client.get("/api/keys", headers=auth(submit_key))
    assert r.status_code == 403


async def test_admin_can_create_and_delete_keys(client, admin_key):
    r = await client.post("/api/keys", json={"name": "ext", "scope": "submit"},
                          headers=auth(admin_key))
    assert r.status_code == 201
    body = r.json()
    assert body["token"]  # shown once
    key_id = body["id"]

    r = await client.get("/api/keys", headers=auth(admin_key))
    assert any(k["id"] == key_id for k in r.json())
    # listing never exposes tokens or hashes
    assert all("token" not in k and "key_hash" not in k for k in r.json())

    r = await client.delete(f"/api/keys/{key_id}", headers=auth(admin_key))
    assert r.status_code == 204


async def test_health_is_public(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# -- SSRF / validation ---------------------------------------------------------

async def test_rejects_private_and_nonhttp_urls(client, submit_key):
    bad = [
        "http://127.0.0.1/admin",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.com/x",
    ]
    r = await client.post("/api/downloads", json={"urls": bad}, headers=auth(submit_key))
    assert r.status_code == 201
    assert all(not item["accepted"] for item in r.json())


async def test_rejects_absolute_dest(client, submit_key):
    r = await client.post("/api/downloads",
                          json={"urls": [GOOD_URL], "dest": "../../etc"},
                          headers=auth(submit_key))
    assert r.status_code == 400


async def test_batch_size_capped(client, submit_key):
    urls = [f"https://example.com/{i}" for i in range(101)]
    r = await client.post("/api/downloads", json={"urls": urls}, headers=auth(submit_key))
    assert r.status_code == 422


# -- queue lifecycle ------------------------------------------------------------

async def test_submit_download_completes(client, submit_key, cfg):
    r = await client.post("/api/downloads", json={"urls": [GOOD_URL]},
                          headers=auth(submit_key))
    assert r.status_code == 201
    result = r.json()[0]
    assert result["accepted"] and result["job_id"]

    job = await wait_for_state(client, submit_key, result["job_id"], "done")
    assert job["files_done"] == 3
    files = list(cfg.downloads.dest.rglob("*.jpg"))
    assert len(files) == 3
    # keep_dirs default: files live under the source's directory name
    assert all(f.parent != cfg.downloads.dest for f in files)


async def test_duplicate_submit_is_idempotent(client, submit_key):
    slow = "https://example.com/slow/album"
    r1 = await client.post("/api/downloads", json={"urls": [slow]}, headers=auth(submit_key))
    r2 = await client.post("/api/downloads", json={"urls": [slow]}, headers=auth(submit_key))
    id1 = r1.json()[0]["job_id"]
    body2 = r2.json()[0]
    assert body2["accepted"] and body2["job_id"] == id1
    # cleanup: cancel so teardown doesn't wait on the slow job
    await client.delete(f"/api/downloads/{id1}", headers=auth(submit_key))


async def test_failed_job_reports_error_and_retries(client, submit_key):
    r = await client.post("/api/downloads", json={"urls": ["https://example.com/fail/x"]},
                          headers=auth(submit_key))
    job_id = r.json()[0]["job_id"]
    job = await wait_for_state(client, submit_key, job_id, "error")
    assert "extractor blew up" in job["error"]

    r = await client.post(f"/api/downloads/{job_id}/retry", headers=auth(submit_key))
    assert r.status_code == 200
    assert r.json()["state"] == "queued"


async def test_cancel_running_job(client, submit_key):
    r = await client.post("/api/downloads", json={"urls": ["https://example.com/slow/2"]},
                          headers=auth(submit_key))
    job_id = r.json()[0]["job_id"]
    await wait_for_state(client, submit_key, job_id, "active")

    r = await client.delete(f"/api/downloads/{job_id}", headers=auth(submit_key))
    assert r.status_code == 204
    r = await client.get(f"/api/downloads/{job_id}", headers=auth(submit_key))
    assert r.json()["state"] == "cancelled"


async def test_stats(client, submit_key):
    r = await client.get("/api/stats", headers=auth(submit_key))
    assert r.status_code == 200
    body = r.json()
    assert "disk_free_bytes" in body and body["disk_free_bytes"] > 0


async def test_list_filter_and_pagination(client, submit_key):
    urls = [f"https://example.com/fail/{i}" for i in range(3)]
    r = await client.post("/api/downloads", json={"urls": urls}, headers=auth(submit_key))
    for item in r.json():
        await wait_for_state(client, submit_key, item["job_id"], "error")
    r = await client.get("/api/downloads?state=error&limit=2", headers=auth(submit_key))
    assert len(r.json()) == 2
