"""Engine adapter: the narrow seam between Grabbit and its download engine.

Nothing outside this module may invoke gallery-dl. The download path uses the
gallery-dl *CLI* (its stable public contract); the only library touchpoint is
extractor pattern-matching in supports(), which needs no network and has been
stable across gallery-dl releases.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
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
    # Cumulative bytes downloaded so far and the current transfer rate
    # (bytes/second) since the previous event. gallery-dl's CLI reports only
    # completed file paths, so both are derived by statting each finished file.
    bytes_done: int = 0
    bytes_per_sec: float = 0.0


@dataclass
class DownloadResult:
    success: bool
    files_done: int
    error: str | None = None
    bytes_done: int = 0


# Metadata keys, most to least specific, that hold a source's display title.
# The first non-empty one wins. Extractors vary: lolisafe/bunkr use album_name,
# many galleries use title, some collections use subcategory-ish names.
_TITLE_KEYS = ("album_name", "title", "gallery_title", "playlist_title",
               "board_title", "subject", "name")


class Engine(Protocol):
    def supports(self, url: str) -> bool: ...
    async def probe(self, url: str) -> list[FileRef]: ...
    async def download(self, url: str, opts: EngineOpts,
                       on_progress: Callable[[ProgressEvent], None],
                       job_id: int = 0) -> DownloadResult: ...
    async def resolve_title(self, url: str, timeout: float = 30.0) -> str | None: ...


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

    async def resolve_title(self, url: str, timeout: float = 30.0) -> str | None:
        """Best-effort human-readable title for a URL (e.g. an album name).

        Runs a metadata-only `--dump-json --no-download` pass and reads the
        first non-empty title key from the extractor's Directory/Url metadata.
        Never raises: on any failure (timeout, non-zero exit, unparseable
        output, no title key) it returns None and the caller keeps the slug.
        """
        try:
            # --range 1 stops after the first item: the Directory metadata
            # (which carries the album/gallery title) is emitted up front, so
            # we avoid resolving every file's CDN URL — that can take minutes on
            # a large album and would blow the timeout, yielding no title.
            proc = await asyncio.create_subprocess_exec(
                self.binary, "--dump-json", "--no-download", "--range", "1", url,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            log.debug("title probe failed to start for %s: %s", url, e)
            return None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(Exception):
                await proc.wait()
            log.debug("title probe timed out for %s", url)
            return None
        if proc.returncode != 0:
            return None
        return self._extract_title(out)

    @staticmethod
    def _extract_title(dump_json: bytes) -> str | None:
        """Pull the best title key from gallery-dl --dump-json output.

        Output is a JSON list of messages: [2, meta] (Directory) and
        [3, url, meta] (Url). Directory metadata is preferred (it carries the
        album/gallery name); Url metadata is the fallback.
        """
        try:
            data = json.loads(dump_json)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, list):
            return None
        dir_meta: list[dict] = []
        url_meta: list[dict] = []
        for entry in data:
            if not isinstance(entry, list) or len(entry) < 2:
                continue
            if entry[0] == 2 and isinstance(entry[1], dict):
                dir_meta.append(entry[1])
            elif entry[0] == 3 and len(entry) >= 3 and isinstance(entry[2], dict):
                url_meta.append(entry[2])
        for meta in (*dir_meta, *url_meta):
            for key in _TITLE_KEYS:
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

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
        bytes_done = 0
        last_time = time.monotonic()
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
                # gallery-dl reports no byte counts, so derive size and rate by
                # statting the just-finished file. Skipped-existing lines still
                # count toward totals but contribute ~0 to the rate.
                size = 0
                with contextlib.suppress(OSError):
                    size = os.path.getsize(current)
                bytes_done += size
                now = time.monotonic()
                elapsed = now - last_time
                rate = size / elapsed if elapsed > 0 else 0.0
                last_time = now
                on_progress(ProgressEvent(
                    files_done=files_done, current_file=current,
                    bytes_done=bytes_done, bytes_per_sec=rate))
            await proc.wait()
        finally:
            await stderr_task
            self._procs.pop(job_id, None)

        if proc.returncode == 0:
            return DownloadResult(
                success=True, files_done=files_done, bytes_done=bytes_done)
        err = "; ".join(stderr_tail[-3:]) or f"engine exited with code {proc.returncode}"
        return DownloadResult(
            success=False, files_done=files_done, error=err, bytes_done=bytes_done)

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
