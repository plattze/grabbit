"""Preconfigured Chrome-extension download (/api/extension.zip)."""

from __future__ import annotations

import io
import json
import zipfile

from .conftest import auth


async def test_extension_zip_preconfigured(app, client, admin_key):
    keys_before = {k.id for k in await app.state.db.list_keys()}
    r = await client.get("/api/extension.zip", headers=auth(admin_key))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "manifest.json" in names
    assert "background.js" in names

    pre = json.loads(zf.read("preconfig.json"))
    assert pre["host"].startswith("http")
    assert not pre["host"].endswith("/")

    # A fresh submit-scoped key was minted, and the baked token verifies
    new = [k for k in await app.state.db.list_keys() if k.id not in keys_before]
    assert len(new) == 1
    assert new[0].scope.value == "submit"
    verified = await app.state.db.verify_key(pre["apiKey"])
    assert verified is not None and verified.id == new[0].id


async def test_extension_zip_uses_public_url(app, client, admin_key):
    app.state.cfg.server.public_url = "https://nas.example.home/grabbit/"
    r = await client.get("/api/extension.zip", headers=auth(admin_key))
    pre = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("preconfig.json"))
    assert pre["host"] == "https://nas.example.home/grabbit"


async def test_extension_zip_requires_admin(client, submit_key):
    r = await client.get("/api/extension.zip", headers=auth(submit_key))
    assert r.status_code == 403


async def test_extension_zip_requires_auth(client):
    r = await client.get("/api/extension.zip")
    assert r.status_code in (401, 403)
