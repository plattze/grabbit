"""JDownloader importer: zip parsing and submission through the real API."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from httpx import ASGITransport, AsyncClient

from grabbit.jd_import import _safe_dest, import_packages, parse_download_list
from grabbit.models import JobState

from .conftest import auth


def make_download_list(path: Path, packages: dict[str, list[dict]]) -> Path:
    """Build a synthetic downloadList*.zip in JDownloader's layout."""
    with zipfile.ZipFile(path, "w") as zf:
        for idx, (name, links) in enumerate(packages.items()):
            zf.writestr(str(idx), json.dumps({"name": name}))
            for li, link in enumerate(links):
                zf.writestr(f"{idx}_{li}", json.dumps(link))
        zf.writestr("extraInfo", json.dumps({"format": 1}))
    return path


def test_parse_download_list(tmp_path):
    zip_path = make_download_list(tmp_path / "downloadList123.zip", {
        "My Album": [
            {"contentUrl": "https://example.com/a/1", "url": "https://cdn.example/1.jpg"},
            {"browserUrl": "https://example.com/a/2"},
            {"contentUrl": "https://example.com/a/1"},  # duplicate — deduped
        ],
        "Other/Pack..": [
            {"url": "https://example.org/x"},
        ],
        "No Links": [
            {"comment": "no url fields"},
        ],
    })
    packages = parse_download_list(zip_path)
    assert len(packages) == 2  # empty package dropped
    assert packages[0].name == "My Album"
    assert packages[0].urls == ["https://example.com/a/1", "https://example.com/a/2"]
    assert packages[1].name == "Other_Pack_"  # sanitized ('/' and '..' replaced)


def test_safe_dest():
    assert ".." not in _safe_dest("../etc")
    assert not _safe_dest("../etc").startswith("/")
    assert _safe_dest("a/b\\c") == "a_b_c"
    assert "?" not in _safe_dest("we|rd<name>: x?")
    assert _safe_dest("  ") == "jdownloader-import"


def test_import_packages_counts(tmp_path):
    zip_path = make_download_list(tmp_path / "dl.zip", {
        "P1": [{"contentUrl": "https://ok.example/1"},
               {"contentUrl": "https://bad.example/2"}],
    })
    packages = parse_download_list(zip_path)

    def fake_submit(urls, dest):
        assert dest == "jd/P1"
        return [{"url": u, "accepted": "ok." in u,
                 "reason": None if "ok." in u else "no extractor"} for u in urls]

    accepted, rejected, rejections = import_packages(
        packages, fake_submit, dest_prefix="jd", log=lambda s: None)
    assert (accepted, rejected) == (1, 1)
    assert rejections[0]["url"] == "https://bad.example/2"


async def test_import_end_to_end(app, submit_key, tmp_path):
    """Importer output submitted through the real API creates real jobs."""
    zip_path = make_download_list(tmp_path / "dl.zip", {
        "Album One": [{"contentUrl": "https://example.com/album1"}],
        "Album Two": [{"contentUrl": "http://10.0.0.1/private"}],  # SSRF-rejected
    })
    packages = parse_download_list(zip_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers=auth(submit_key)) as client:
        results: list[dict] = []
        accepted = rejected = 0
        rejections = []
        for pkg in packages:
            resp = await client.post("/api/downloads",
                                     json={"urls": pkg.urls, "dest": pkg.name})
            assert resp.status_code == 201
            for r in resp.json():
                results.append(r)
                if r["accepted"]:
                    accepted += 1
                else:
                    rejected += 1
                    rejections.append(r)

    assert accepted == 1
    assert rejected == 1
    assert "private" in rejections[0]["url"]

    job = await app.state.db.get_job(
        next(r["job_id"] for r in results if r["accepted"]))
    assert job.dest == "Album One"
    assert job.state in (JobState.QUEUED, JobState.ACTIVE, JobState.DONE)
