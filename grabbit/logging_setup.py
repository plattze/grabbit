"""Logging: JSON/text format, size-based rotation, full disable switch."""

from __future__ import annotations

import gzip
import json
import logging
import logging.handlers
import os
import shutil
from datetime import UTC, datetime

from .config import LoggingConfig

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING,
           "error": logging.ERROR}

# Query params that carry signed-URL tokens; redacted before anything is logged.
_TOKEN_PARAMS = ("token", "sig", "signature", "expires", "key", "auth", "x-amz-signature")


def redact_url(url: str) -> str:
    """Strip known token query params from a URL for safe logging."""
    if "?" not in url:
        return url
    base, _, query = url.partition("?")
    kept = []
    for pair in query.split("&"):
        name = pair.partition("=")[0].lower()
        kept.append(f"{name}=REDACTED" if name in _TOKEN_PARAMS else pair)
    return f"{base}?{'&'.join(kept)}"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class _GzipRotatingHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that gzips rotated files."""

    def rotation_filename(self, default_name: str) -> str:
        return default_name + ".gz"

    def rotate(self, source: str, dest: str) -> None:
        with open(source, "rb") as sf, gzip.open(dest, "wb") as df:
            shutil.copyfileobj(sf, df)
        os.remove(source)


def setup_logging(cfg: LoggingConfig, data_dir) -> None:
    root = logging.getLogger()
    root.setLevel(_LEVELS[cfg.level])
    root.handlers.clear()

    if not cfg.enabled:
        root.addHandler(logging.NullHandler())
        # Silence uvicorn's default handlers too.
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            lg = logging.getLogger(name)
            lg.handlers.clear()
            lg.addHandler(logging.NullHandler())
            lg.propagate = False
        return

    formatter: logging.Formatter
    if cfg.format == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)-5s %(name)s: %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    log_file = cfg.file or (data_dir / "grabbit.log")
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        cls: type[logging.handlers.RotatingFileHandler] = (
            _GzipRotatingHandler if cfg.rotation.compress
            else logging.handlers.RotatingFileHandler)
        fh = cls(
            log_file,
            maxBytes=cfg.rotation.max_size_mb * 1024 * 1024,
            backupCount=cfg.rotation.max_files,
        )
        fh.setFormatter(formatter)
        root.addHandler(fh)
    except OSError:  # e.g. read-only volume: stderr logging still works
        root.warning("cannot open log file %s; file logging disabled", log_file)
