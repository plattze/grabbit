"""One-time domain-level flatten migration (item 014).

Old layout: dest/<domain>/<package>/file. The flatten change makes gallery-dl
write dest/<package>/file directly; this migration brings existing DONE jobs
into line, driven from DB records only (never a dest scan).
"""

from __future__ import annotations

import json

import pytest

from grabbit.db import Database
from grabbit.models import JobState
from grabbit.worker import SCHEMA_VERSION, migrate_flatten_domain_dirs


async def _done_job(db: Database, url: str, dest: str, dir_name: str,
                    file_paths: list[str]) -> int:
    job = await db.create_job(url, "bunkr.example", dest)
    assert job is not None
    await db.update_job(job.id, state=JobState.DONE.value, dir_name=dir_name,
                        file_paths=json.dumps(file_paths))
    return job.id


@pytest.fixture
async def db(cfg) -> Database:
    d = Database(cfg.db_path)
    await d.open()
    yield d
    await d.close()


def _make(root, rel_paths):
    for rel in rel_paths:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


async def test_two_level_job_is_flattened(cfg, db):
    dest = cfg.downloads.dest
    _make(dest, ["bunkr.example/My Album/a.jpg", "bunkr.example/My Album/b.jpg"])
    job_id = await _done_job(
        db, "https://bunkr.example/a/xyz", "", "bunkr.example",
        ["bunkr.example/My Album/a.jpg", "bunkr.example/My Album/b.jpg"])

    n = await migrate_flatten_domain_dirs(cfg, db)
    assert n == 1

    # Files moved up one level; the domain dir is gone.
    assert (dest / "My Album" / "a.jpg").is_file()
    assert (dest / "My Album" / "b.jpg").is_file()
    assert not (dest / "bunkr.example").exists()

    job = await db.get_job(job_id)
    assert job.dir_name == "My Album"
    assert await db.get_job_files(job_id) == ["My Album/a.jpg", "My Album/b.jpg"]


async def test_migration_is_idempotent(cfg, db):
    dest = cfg.downloads.dest
    _make(dest, ["bunkr.example/Album/a.jpg"])
    await _done_job(db, "https://bunkr.example/a/1", "", "bunkr.example",
                    ["bunkr.example/Album/a.jpg"])

    assert await migrate_flatten_domain_dirs(cfg, db) == 1
    assert await db.get_schema_version() == SCHEMA_VERSION
    # A second run (e.g. next restart) does nothing.
    assert await migrate_flatten_domain_dirs(cfg, db) == 0
    assert (dest / "Album" / "a.jpg").is_file()


async def test_renamed_job_left_untouched(cfg, db):
    """A user-renamed two-level job: dir_name diverges from file_paths (rename
    never rewrites paths), so the migration must not strip a level."""
    dest = cfg.downloads.dest
    _make(dest, ["Renamed/pkg/a.jpg"])
    job_id = await _done_job(
        db, "https://bunkr.example/a/1", "", "Renamed",
        # file_paths still lead with the original domain, not "Renamed"
        ["bunkr.example/pkg/a.jpg"])

    assert await migrate_flatten_domain_dirs(cfg, db) == 0
    job = await db.get_job(job_id)
    assert job.dir_name == "Renamed"  # unchanged
    assert (dest / "Renamed" / "pkg" / "a.jpg").is_file()


async def test_already_flat_job_left_untouched(cfg, db):
    """A one-level job (dest/<package>/file) is already in the new layout."""
    dest = cfg.downloads.dest
    _make(dest, ["Album/a.jpg"])
    job_id = await _done_job(db, "https://bunkr.example/a/1", "", "Album",
                             ["Album/a.jpg"])

    assert await migrate_flatten_domain_dirs(cfg, db) == 0
    job = await db.get_job(job_id)
    assert job.dir_name == "Album"
    assert (dest / "Album" / "a.jpg").is_file()


async def test_flatten_merges_on_collision(cfg, db):
    """If dest/<package> already exists, colliding files get a (2) suffix."""
    dest = cfg.downloads.dest
    (dest / "Album").mkdir()
    (dest / "Album" / "a.jpg").write_text("existing")
    _make(dest, ["bunkr.example/Album/a.jpg"])
    await _done_job(db, "https://bunkr.example/a/1", "", "bunkr.example",
                    ["bunkr.example/Album/a.jpg"])

    assert await migrate_flatten_domain_dirs(cfg, db) == 1
    assert (dest / "Album" / "a.jpg").read_text() == "existing"
    assert (dest / "Album" / "a (2).jpg").is_file()
    assert not (dest / "bunkr.example").exists()


async def test_job_with_dest_subpath_flattened_under_it(cfg, db):
    """The domain level is dropped under the job's dest sub-path, not the root."""
    dest = cfg.downloads.dest
    _make(dest, ["albums/bunkr.example/Album/a.jpg"])
    job_id = await _done_job(db, "https://bunkr.example/a/1", "albums",
                             "bunkr.example", ["bunkr.example/Album/a.jpg"])

    assert await migrate_flatten_domain_dirs(cfg, db) == 1
    assert (dest / "albums" / "Album" / "a.jpg").is_file()
    assert not (dest / "albums" / "bunkr.example").exists()
    job = await db.get_job(job_id)
    assert job.dir_name == "Album"
