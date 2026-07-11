"""Shared fixtures: temp config, mocked gallery-dl CLI, app client."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from grabbit.app import create_app
from grabbit.config import Config
from grabbit.engine import GalleryDLEngine
from grabbit.models import KeyScope

# A fake gallery-dl: prints one "downloaded" path per line, creates the files.
MOCK_GALLERY_DL = """\
#!/usr/bin/env python3
import os, sys, time

args = sys.argv[1:]
url = args[-1]

if "--dump-json" in args:
    print('[[3, "%s/file1.jpg", {"filename": "file1", "extension": "jpg"}]]' % url)
    sys.exit(0)

dest = "."
if "--directory" in args:
    dest = args[args.index("--directory") + 1]
elif "--destination" in args:
    # Like real gallery-dl: --destination keeps the extractor's directory
    # structure under the target, --directory flattens into it exactly.
    dest = os.path.join(args[args.index("--destination") + 1], "album")
os.makedirs(dest, exist_ok=True)

if "fail" in url:
    print("error: extractor blew up", file=sys.stderr)
    sys.exit(4)

n = 20 if "slow" in url else 3
for i in range(n):
    p = os.path.join(dest, f"file{i}.jpg")
    with open(p, "w") as f:
        f.write("x" * 100)
    print(p, flush=True)
    if "slow" in url:
        time.sleep(0.5)
sys.exit(0)
"""


@pytest.fixture
def mock_engine_binary(tmp_path: Path) -> str:
    binary = tmp_path / "mock-gallery-dl"
    binary.write_text(MOCK_GALLERY_DL)
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return str(binary)


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    dl = tmp_path / "downloads"
    dl.mkdir()
    return Config.model_validate({
        "data_dir": str(tmp_path / "config"),
        "downloads": {"dest": str(dl), "max_concurrent": 2, "max_per_host": 1},
        "logging": {"enabled": False},
    })


@pytest.fixture
async def app(cfg: Config, mock_engine_binary: str, monkeypatch):
    # Hide the real gallery_dl library so supports() uses its http(s) fallback
    # (URL support decisions then rest with the mocked CLI, like a real
    # engine-version mismatch would).
    monkeypatch.setitem(sys.modules, "gallery_dl", None)
    engine = GalleryDLEngine(binary=mock_engine_binary)
    application = create_app(cfg, engine=engine)
    # httpx's ASGITransport does not drive lifespan; run it explicitly.
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_key(app) -> str:
    created = await app.state.db.create_key("test-admin", KeyScope.ADMIN)
    return created.token


@pytest.fixture
async def submit_key(app) -> str:
    created = await app.state.db.create_key("test-submit", KeyScope.SUBMIT)
    return created.token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
