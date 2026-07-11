"""GET /api/settings — read-only config surface for the UI."""

from __future__ import annotations

from .conftest import auth


async def test_settings_shape(client, admin_key, cfg):
    r = await client.get("/api/settings", headers=auth(admin_key))
    assert r.status_code == 200
    body = r.json()
    ro = body["read_only"]
    assert ro["downloads"]["dest"] == str(cfg.downloads.dest)
    assert ro["downloads"]["max_concurrent"] == cfg.downloads.max_concurrent
    assert ro["engine"]["channel"] == "stable"
    assert ro["logging"]["enabled"] is False
    assert ro["mcp"]["enabled"] is True
    assert body["editable"] == {}


async def test_settings_requires_admin(client, submit_key):
    r = await client.get("/api/settings", headers=auth(submit_key))
    assert r.status_code == 403


async def test_settings_requires_auth(client):
    r = await client.get("/api/settings")
    assert r.status_code in (401, 403)
