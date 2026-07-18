"""Download byte totals and speed derivation (item 020)."""

from __future__ import annotations

import asyncio

from grabbit.engine import EngineOpts, GalleryDLEngine, ProgressEvent

from .conftest import auth


async def test_progress_reports_bytes_and_rate(mock_engine_binary, tmp_path):
    engine = GalleryDLEngine(binary=mock_engine_binary)
    events: list[ProgressEvent] = []
    result = await engine.download(
        "https://example.com/a", EngineOpts(dest=tmp_path / "out"),
        on_progress=events.append)
    assert result.success
    # Mock writes 100-byte files; three of them → cumulative 300 bytes.
    assert result.bytes_done == 300
    assert [e.bytes_done for e in events] == [100, 200, 300]
    # Every event carries a non-negative rate; at least one download was timed.
    assert all(e.bytes_per_sec >= 0 for e in events)
    assert any(e.bytes_per_sec > 0 for e in events)


async def test_bytes_done_persisted_and_broadcast(client, submit_key):
    r = await client.post("/api/downloads", json={"urls": ["https://example.com/a"]},
                          headers=auth(submit_key))
    job_id = r.json()[0]["job_id"]
    async with asyncio.timeout(10):
        while True:
            j = (await client.get(f"/api/downloads/{job_id}", headers=auth(submit_key))).json()
            if j["state"] == "done":
                break
            await asyncio.sleep(0.05)
    assert j["bytes_done"] == 300


async def test_missing_file_does_not_break_progress(mock_engine_binary, tmp_path, monkeypatch):
    # A path that can't be stat'd (e.g. already moved) contributes 0 bytes and
    # must not raise — progress keeps flowing.
    import os
    real_getsize = os.path.getsize

    def flaky_getsize(path):
        if "file1" in str(path):
            raise OSError("gone")
        return real_getsize(path)

    monkeypatch.setattr("grabbit.engine.os.path.getsize", flaky_getsize)
    engine = GalleryDLEngine(binary=mock_engine_binary)
    events: list[ProgressEvent] = []
    result = await engine.download(
        "https://example.com/a", EngineOpts(dest=tmp_path / "out"),
        on_progress=events.append)
    assert result.success
    # file1 missing → 100 + 0 + 100 = 200 (files0, file1, file2).
    assert result.bytes_done == 200
    assert len(events) == 3
