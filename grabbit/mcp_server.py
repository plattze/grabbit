"""MCP server: exposes Grabbit's REST API as tools for AI agents.

Runs as a stdio MCP server (`grabbit-mcp`) and talks to a running Grabbit
instance over HTTP — it contains no download logic of its own. Configure with:

    GRABBIT_MCP_URL      base URL of the Grabbit instance (default http://localhost:8080)
    GRABBIT_MCP_API_KEY  a submit-scoped API key (required)

Disable with `mcp.enabled: false` in config.yaml or GRABBIT_MCP_ENABLED=false
(the entrypoint refuses to start). Requires the `mcp` extra:
pip install "grabbit[mcp]".
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

server = FastMCP(
    "grabbit",
    instructions=(
        "Queue and monitor downloads on a Grabbit server. Submit URLs with "
        "queue_download; each URL is validated individually and may be "
        "rejected (unsupported site, private address). Poll download_status "
        "or list_downloads to track progress."
    ),
)


def _make_client() -> httpx.AsyncClient:
    """Build the HTTP client from env config; overridden in tests."""
    base_url = os.environ.get("GRABBIT_MCP_URL", "http://localhost:8080")
    api_key = os.environ.get("GRABBIT_MCP_API_KEY", "")
    if not api_key:
        raise RuntimeError("GRABBIT_MCP_API_KEY is not set (needs a submit-scoped API key)")
    return httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30.0,
    )


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    async with _make_client() as client:
        resp = await client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            detail = ""
            with contextlib.suppress(ValueError):
                detail = resp.json().get("detail", "")
            raise RuntimeError(f"Grabbit API error {resp.status_code}: {detail or resp.text}")
        return resp.json() if resp.content else None


@server.tool()
async def queue_download(urls: list[str], dest: str | None = None) -> list[dict]:
    """Queue one or more URLs for download.

    Args:
        urls: HTTP(S) URLs to download (galleries, albums, media pages).
        dest: Optional sub-folder (relative path) under the download root.

    Returns one result per URL: {url, accepted, job_id, reason}. A rejected
    URL has accepted=false and a reason (e.g. unsupported site).
    """
    body: dict[str, Any] = {"urls": urls}
    if dest:
        body["dest"] = dest
    return await _request("POST", "/api/downloads", json=body)


@server.tool()
async def list_downloads(state: str | None = None, limit: int = 50) -> list[dict]:
    """List download jobs, newest first.

    Args:
        state: Optional filter: queued, active, paused, done, error, cancelled.
        limit: Maximum number of jobs to return (default 50).

    Returns jobs with id, url, state, files_done/files_total, and error.
    """
    params: dict[str, Any] = {"limit": limit}
    if state:
        params["state"] = state
    return await _request("GET", "/api/downloads", params=params)


@server.tool()
async def download_status(job_id: int) -> dict:
    """Get the current status of a single download job.

    Args:
        job_id: The job id returned by queue_download.
    """
    return await _request("GET", f"/api/downloads/{job_id}")


def main() -> None:
    from .config import load_config

    if not load_config().mcp.enabled:
        raise SystemExit(
            "grabbit-mcp is disabled (mcp.enabled: false in config.yaml or "
            "GRABBIT_MCP_ENABLED=false)")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
