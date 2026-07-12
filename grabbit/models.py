"""Domain models and API schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobState(StrEnum):
    QUEUED = "queued"
    ACTIVE = "active"
    PAUSED = "paused"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class Job(BaseModel):
    id: int
    url: str
    host: str
    state: JobState
    dest: str
    files_total: int = 0
    files_done: int = 0
    bytes_done: int = 0
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    # Output directory of this job's files, relative to dest/<dest sub-path>;
    # "" until detected (or when files land flat in the destination root).
    dir_name: str = ""
    # Rename requested while the job was still running; applied at completion.
    rename_to: str | None = None
    # Pinned: the source is re-checked forever for new files (worker requeues
    # the job every downloads.pin_recheck_minutes after it finishes).
    pinned: bool = False


class SubmitRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)
    dest: str | None = None
    options: dict[str, Any] | None = None


class SubmitResult(BaseModel):
    url: str
    accepted: bool
    job_id: int | None = None
    reason: str | None = None


class KeyScope(StrEnum):
    SUBMIT = "submit"
    ADMIN = "admin"


class ApiKeyInfo(BaseModel):
    id: int
    name: str
    scope: KeyScope
    created_at: datetime
    last_used_at: datetime | None = None


class ApiKeyCreated(ApiKeyInfo):
    token: str  # shown exactly once


def utcnow() -> datetime:
    return datetime.now(UTC)
