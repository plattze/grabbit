"""Engine adapter: the narrow seam between Grabbit and its download engine.

Nothing outside this module may invoke gallery-dl. The download path uses the
gallery-dl *CLI* (its stable public contract); the only library touchpoint is
extractor pattern-matching in supports(), which needs no network and has been
stable across gallery-dl releases.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass
class FileRef:
    url: str
    filename: str | None = None


@dataclass
class EngineOpts:
    dest: Path
    retries: int = 3
    rate_limit: str | None = None
    filename_template: str | None = None
    cookies_file: Path | None = None
    keep_dirs: bool = True
    # Keep the download time as the files' mtime instead of letting the
    # engine restore the source's original timestamp from metadata.
    reset_mtime: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class ProgressEvent:
    files_done: int
    current_file: str | None = None


@dataclass
class DownloadResult:
    success: bool
    files_done: int
    error: str | None = None


class Engine(Protocol):
    def supports(self, url: str) -> bool: ...
    async def probe(self, url: str) -> list[FileRef]: ...
    async def download(self, url: str, opts: EngineOpts,
                       on_progress: Callable[[ProgressEvent], None],
                       job_id: int = 0) -> DownloadResult: ...


class GalleryDLEngine:
    """gallery-dl via its CLI. `binary` is overridable for tests (mock CLI)."""

    def __init__(self, binary: str = "gallery-dl") -> None:
        self.binary = binary
        self._procs: dict[int, asyncio.subprocess.Process] = {}

    def supports(self, url: str) -> bool:
        if not url.lower().startswith(("http://", "https://")):
            return False
        try:
            from gallery_dl import extractor
            return extractor.find(url) is not None
        except ImportError:
            # Library unavailable (e.g. mocked-CLI test env): accept http(s),
            # the CLI itself rejects unsupported URLs at download time.
            return True
        except Exception:
            return False

    async def probe(self, url: str) -> list[FileRef]:
        proc = await asyncio.create_subprocess_exec(
            self.binary, "--dump-json", "--no-download", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise EngineError(err.decode(errors="replace").strip() or "probe failed")
        files: list[FileRef] = []
        try:
            for entry in json.loads(out):
                # gallery-dl JSON entries: [3, url, metadata] are file URLs
                if isinstance(entry, list) and len(entry) >= 2 and entry[0] == 3:
                    meta = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
                    name = meta.get("filename")
                    ext = meta.get("extension")
                    files.append(FileRef(
                        url=entry[1],
                        filename=f"{name}.{ext}" if name and ext else name,
                    ))
        except json.JSONDecodeError as e:
            raise EngineError(f"unparseable engine output: {e}") from e
        return files

    def _build_args(self, url: str, opts: EngineOpts) -> list[str]:
        # --destination keeps the extractor's directory structure (album/gallery
        # names) under dest; --directory is gallery-dl's "exact location" flag,
        # which flattens everything into dest.
        dest_flag = "--destination" if opts.keep_dirs else "--directory"
        args = [self.binary, dest_flag, str(opts.dest), "--retries", str(opts.retries)]
        if opts.keep_dirs:
            # Every extractor's directory template leads with {category} (the
            # site/domain). Empty it via keywords so gallery-dl — which drops
            # empty path segments — writes dest/<package>/… instead of
            # dest/<domain>/<package>/…, keeping only the meaningful level.
            args += ["-o", 'keywords={"category": ""}']
        if opts.rate_limit:
            args += ["--limit-rate", opts.rate_limit]
        if opts.cookies_file:
            args += ["--cookies", str(opts.cookies_file)]
        if opts.filename_template:
            args += ["-o", f"filename={opts.filename_template}"]
        if opts.reset_mtime:
            # Disable gallery-dl's mtime-from-metadata handling; files then
            # keep their write time (= download time), which the staging
            # move preserves into dest.
            args += ["-o", "mtime=false"]
        args += ["--no-colors", url]
        return args

    async def download(self, url: str, opts: EngineOpts,
                       on_progress: Callable[[ProgressEvent], None],
                       job_id: int = 0) -> DownloadResult:
        opts.dest.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *self._build_args(url, opts),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # own process group, so cancel kills children too
        )
        self._procs[job_id] = proc
        files_done = 0
        stderr_tail: list[str] = []

        async def read_stderr() -> None:
            assert proc.stderr is not None
            async for raw in proc.stderr:
                line = raw.decode(errors="replace").rstrip()
                if line:
                    stderr_tail.append(line)
                    del stderr_tail[:-10]

        stderr_task = asyncio.create_task(read_stderr())
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if not line:
                    continue
                # Each stdout line is a downloaded path; "# path" means skipped-existing.
                files_done += 1
                current = line.removeprefix("# ").strip()
                on_progress(ProgressEvent(files_done=files_done, current_file=current))
            await proc.wait()
        finally:
            await stderr_task
            self._procs.pop(job_id, None)

        if proc.returncode == 0:
            return DownloadResult(success=True, files_done=files_done)
        err = "; ".join(stderr_tail[-3:]) or f"engine exited with code {proc.returncode}"
        return DownloadResult(success=False, files_done=files_done, error=err)

    def cancel(self, job_id: int) -> bool:
        proc = self._procs.get(job_id)
        if proc and proc.returncode is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
            return True
        return False


class EngineError(Exception):
    pass
