"""SQLite persistence (aiosqlite, WAL): jobs and API keys."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from pathlib import Path

import aiosqlite

from .models import ApiKeyCreated, ApiKeyInfo, Job, JobState, KeyScope, utcnow

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    host TEXT NOT NULL,
    state TEXT NOT NULL,
    dest TEXT NOT NULL,
    files_total INTEGER NOT NULL DEFAULT 0,
    files_done INTEGER NOT NULL DEFAULT 0,
    bytes_done INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url_open ON jobs(url)
    WHERE state IN ('queued', 'active', 'paused');

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
"""

_OPEN_STATES = (JobState.QUEUED, JobState.ACTIVE, JobState.PAUSED)


def _hash_key(token: str, salt: str) -> str:
    return hashlib.scrypt(token.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1).hex()


def _row_to_job(row: aiosqlite.Row) -> Job:
    return Job(
        id=row["id"], url=row["url"], host=row["host"], state=JobState(row["state"]),
        dest=row["dest"], files_total=row["files_total"], files_done=row["files_done"],
        bytes_done=row["bytes_done"], error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
    )


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not opened"
        return self._conn

    async def open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # -- jobs ------------------------------------------------------------

    async def create_job(self, url: str, host: str, dest: str) -> Job | None:
        """Insert a queued job; returns None if the URL is already open (idempotent)."""
        now = utcnow().isoformat()
        try:
            cur = await self.conn.execute(
                "INSERT INTO jobs (url, host, state, dest, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (url, host, JobState.QUEUED, dest, now, now),
            )
        except aiosqlite.IntegrityError:
            return None
        await self.conn.commit()
        return await self.get_job(cur.lastrowid)  # type: ignore[arg-type]

    async def get_job(self, job_id: int) -> Job | None:
        cur = await self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cur.fetchone()
        return _row_to_job(row) if row else None

    async def find_open_job(self, url: str) -> Job | None:
        cur = await self.conn.execute(
            "SELECT * FROM jobs WHERE url = ? AND state IN (?, ?, ?)",
            (url, *(s.value for s in _OPEN_STATES)),
        )
        row = await cur.fetchone()
        return _row_to_job(row) if row else None

    async def list_jobs(self, state: JobState | None = None,
                        limit: int = 100, offset: int = 0) -> list[Job]:
        if state:
            cur = await self.conn.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (state.value, limit, offset),
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        return [_row_to_job(r) for r in await cur.fetchall()]

    async def update_job(self, job_id: int, **fields) -> None:
        fields["updated_at"] = utcnow().isoformat()
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))
        await self.conn.commit()

    async def set_state(self, job_id: int, state: JobState, error: str | None = None) -> None:
        finished = utcnow().isoformat() if state in (
            JobState.DONE, JobState.ERROR, JobState.CANCELLED) else None
        await self.update_job(job_id, state=state.value, error=error, finished_at=finished)

    async def requeue_interrupted(self) -> int:
        """On startup: put jobs that were active when we died back in the queue."""
        cur = await self.conn.execute(
            "UPDATE jobs SET state = ?, updated_at = ? WHERE state = ?",
            (JobState.QUEUED, utcnow().isoformat(), JobState.ACTIVE),
        )
        await self.conn.commit()
        return cur.rowcount

    async def stats(self) -> dict[str, int]:
        cur = await self.conn.execute("SELECT state, COUNT(*) AS n FROM jobs GROUP BY state")
        return {row["state"]: row["n"] for row in await cur.fetchall()}

    # -- api keys ----------------------------------------------------------

    async def count_keys(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) AS n FROM api_keys")
        row = await cur.fetchone()
        return row["n"] if row else 0

    async def create_key(self, name: str, scope: KeyScope) -> ApiKeyCreated:
        token = secrets.token_urlsafe(32)  # 256 bits
        salt = secrets.token_hex(16)
        now = utcnow()
        cur = await self.conn.execute(
            "INSERT INTO api_keys (name, scope, key_hash, salt, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, scope.value, _hash_key(token, salt), salt, now.isoformat()),
        )
        await self.conn.commit()
        return ApiKeyCreated(
            id=cur.lastrowid, name=name, scope=scope, created_at=now,  # type: ignore[arg-type]
            token=token)

    async def verify_key(self, token: str) -> ApiKeyInfo | None:
        cur = await self.conn.execute("SELECT * FROM api_keys")
        for row in await cur.fetchall():
            expected = row["key_hash"]
            actual = _hash_key(token, row["salt"])
            if hmac.compare_digest(expected, actual):
                await self.conn.execute(
                    "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                    (utcnow().isoformat(), row["id"]))
                await self.conn.commit()
                return ApiKeyInfo(
                    id=row["id"], name=row["name"], scope=KeyScope(row["scope"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    last_used_at=datetime.fromisoformat(row["last_used_at"])
                    if row["last_used_at"] else None,
                )
        return None

    async def list_keys(self) -> list[ApiKeyInfo]:
        cur = await self.conn.execute("SELECT * FROM api_keys ORDER BY id")
        return [
            ApiKeyInfo(
                id=r["id"], name=r["name"], scope=KeyScope(r["scope"]),
                created_at=datetime.fromisoformat(r["created_at"]),
                last_used_at=datetime.fromisoformat(r["last_used_at"])
                if r["last_used_at"] else None,
            )
            for r in await cur.fetchall()
        ]

    async def delete_key(self, key_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
        await self.conn.commit()
        return cur.rowcount > 0
