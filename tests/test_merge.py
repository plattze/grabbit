"""POST /api/downloads/merge — combine completed jobs' directories."""

from __future__ import annotations

from .conftest import auth
from .test_rename import _submit, wait_for_state


async def _done_job(client, key, url):
    job_id = await _submit(client, key, url)
    await wait_for_state(client, key, job_id, "done")
    return job_id


async def test_merge_combines_directories(client, submit_key, cfg):
    # Two jobs; both write into <dest>/album — give the second its own dir first.
    a = await _done_job(client, submit_key, "https://example.com/one")
    await client.post(f"/api/downloads/{a}/rename", json={"name": "Part 1"},
                      headers=auth(submit_key))
    b = await _done_job(client, submit_key, "https://example.com/two")
    await client.post(f"/api/downloads/{b}/rename", json={"name": "Part 2"},
                      headers=auth(submit_key))

    r = await client.post("/api/downloads/merge",
                          json={"job_ids": [a, b], "name": "Combined"},
                          headers=auth(submit_key))
    assert r.status_code == 200
    assert all(j["dir_name"] == "Combined" for j in r.json())

    merged = cfg.downloads.dest / "Combined"
    # 3 files each with identical names -> collisions kept with " (2)" suffix
    assert len(list(merged.glob("*.jpg"))) == 6
    assert len(list(merged.glob("* (2).jpg"))) == 3
    assert not (cfg.downloads.dest / "Part 1").exists()
    assert not (cfg.downloads.dest / "Part 2").exists()


async def test_merge_into_first_jobs_own_directory(client, submit_key, cfg):
    a = await _done_job(client, submit_key, "https://example.com/one")
    await client.post(f"/api/downloads/{a}/rename", json={"name": "Keep"},
                      headers=auth(submit_key))
    b = await _done_job(client, submit_key, "https://example.com/two")
    await client.post(f"/api/downloads/{b}/rename", json={"name": "Absorb"},
                      headers=auth(submit_key))

    r = await client.post("/api/downloads/merge",
                          json={"job_ids": [a, b], "name": "Keep"},
                          headers=auth(submit_key))
    assert r.status_code == 200
    merged = cfg.downloads.dest / "Keep"
    assert len(list(merged.glob("*.jpg"))) == 6
    assert not (cfg.downloads.dest / "Absorb").exists()


async def test_merge_rejects_single_job(client, submit_key):
    a = await _done_job(client, submit_key, "https://example.com/one")
    r = await client.post("/api/downloads/merge",
                          json={"job_ids": [a], "name": "X"},
                          headers=auth(submit_key))
    assert r.status_code == 422


async def test_merge_rejects_unfinished_job(client, submit_key):
    a = await _done_job(client, submit_key, "https://example.com/one")
    r = await client.post("/api/downloads",
                          json={"urls": ["https://example.com/slow/x"]},
                          headers=auth(submit_key))
    running = r.json()[0]["job_id"]
    r = await client.post("/api/downloads/merge",
                          json={"job_ids": [a, running], "name": "X"},
                          headers=auth(submit_key))
    assert r.status_code == 409
    await client.delete(f"/api/downloads/{running}", headers=auth(submit_key))


async def test_merge_rejects_bad_name_and_duplicates(client, submit_key):
    a = await _done_job(client, submit_key, "https://example.com/one")
    b = await _done_job(client, submit_key, "https://example.com/two")
    r = await client.post("/api/downloads/merge",
                          json={"job_ids": [a, b], "name": "../evil"},
                          headers=auth(submit_key))
    assert r.status_code == 400
    r = await client.post("/api/downloads/merge",
                          json={"job_ids": [a, a], "name": "X"},
                          headers=auth(submit_key))
    assert r.status_code == 400


async def test_merge_requires_auth(client):
    r = await client.post("/api/downloads/merge", json={"job_ids": [1, 2], "name": "x"})
    assert r.status_code in (401, 403)
