"""MCP server tools, wired to the real app via ASGI transport."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

pytest.importorskip("mcp")

from grabbit import mcp_server  # noqa: E402
from grabbit.models import JobState  # noqa: E402

from .conftest import auth  # noqa: E402


@pytest.fixture
def mcp_wired(app, submit_key, monkeypatch):
    """Point the MCP server's HTTP client at the test app."""
    def make_client():
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers=auth(submit_key),
        )
    monkeypatch.setattr(mcp_server, "_make_client", make_client)
    return app


async def _wait_done(app, job_id: int, timeout: float = 5.0) -> None:
    async def poll():
        while True:
            job = await app.state.db.get_job(job_id)
            if job.state in (JobState.DONE, JobState.ERROR):
                return
            await asyncio.sleep(0.05)
    await asyncio.wait_for(poll(), timeout)


async def test_queue_download(mcp_wired):
    results = await mcp_server.queue_download(["https://example.com/album"])
    assert len(results) == 1
    assert results[0]["accepted"] is True
    assert results[0]["job_id"] is not None


async def test_queue_download_rejects_private(mcp_wired):
    results = await mcp_server.queue_download(["http://192.168.1.1/x"])
    assert results[0]["accepted"] is False
    assert results[0]["reason"]


async def test_download_status_and_list(mcp_wired):
    results = await mcp_server.queue_download(["https://example.com/thing"])
    job_id = results[0]["job_id"]
    await _wait_done(mcp_wired, job_id)

    status = await mcp_server.download_status(job_id)
    assert status["id"] == job_id
    assert status["state"] == "done"

    listed = await mcp_server.list_downloads(state="done")
    assert any(j["id"] == job_id for j in listed)


async def test_download_status_not_found(mcp_wired):
    with pytest.raises(RuntimeError, match="404"):
        await mcp_server.download_status(999999)


async def test_missing_api_key_errors(monkeypatch):
    monkeypatch.delenv("GRABBIT_MCP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GRABBIT_MCP_API_KEY"):
        mcp_server._make_client()


async def test_tools_registered():
    # The three M5-spec tools exist on the server
    names = {t.name for t in await mcp_server.server.list_tools()}
    assert {"queue_download", "list_downloads", "download_status"} <= names


async def test_disabled_via_config(cfg, monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("mcp:\n  enabled: false\n")
    monkeypatch.setenv("GRABBIT_CONFIG", str(cfg_file))
    with pytest.raises(SystemExit, match="disabled"):
        mcp_server.main()


async def test_disabled_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GRABBIT_CONFIG", str(tmp_path / "nonexistent.yaml"))
    monkeypatch.setenv("GRABBIT_MCP_ENABLED", "false")
    with pytest.raises(SystemExit, match="disabled"):
        mcp_server.main()
