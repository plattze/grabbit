"""Configuration loading: config.yaml + environment-variable overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    bind: str = "0.0.0.0"
    port: int = 8080
    root_path: str = ""
    trusted_proxies: list[str] = Field(default_factory=lambda: ["127.0.0.1"])
    # Externally reachable base URL (e.g. https://nas.example.home/grabbit).
    # Cannot be inferred behind a reverse proxy; used to preconfigure the
    # Chrome extension. null = fall back to the browser's own origin.
    public_url: str | None = None


class DownloadsConfig(BaseModel):
    dest: Path = Path("/downloads")
    incomplete_dir: Path | None = None  # staging dir; null = write directly into dest
    max_concurrent: int = 5
    max_per_host: int = 2
    filename_template: str | None = None
    cookies_file: Path | None = None
    # Preserve the source's directory structure (album/gallery names) under
    # dest instead of flattening every file into it.
    keep_dirs: bool = True
    # How often pinned jobs re-check their source for new files (minutes).
    pin_recheck_minutes: float = 60


class EngineConfig(BaseModel):
    name: str = "gallery-dl"
    channel: Literal["stable", "dev"] = "stable"
    retries: int = 3
    rate_limit: str | None = None  # e.g. "2M" bytes/s


class LogRotationConfig(BaseModel):
    max_size_mb: int = 50
    max_files: int = 5
    compress: bool = True


class LoggingConfig(BaseModel):
    enabled: bool = True
    level: Literal["debug", "info", "warn", "error"] = "info"
    format: Literal["json", "text"] = "json"
    file: Path | None = None  # default: <data_dir>/grabbit.log when enabled
    rotation: LogRotationConfig = Field(default_factory=LogRotationConfig)


class SecurityConfig(BaseModel):
    require_auth: bool = True
    update_check: bool = False


class MetricsConfig(BaseModel):
    enabled: bool = True


class McpConfig(BaseModel):
    enabled: bool = True


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    downloads: DownloadsConfig = Field(default_factory=DownloadsConfig)
    engine: EngineConfig = Field(default_factory=EngineConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)
    data_dir: Path = Path("/config")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "grabbit.db"


_ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "GRABBIT_PORT": ("server", "port"),
    "GRABBIT_ROOT_PATH": ("server", "root_path"),
    "GRABBIT_PUBLIC_URL": ("server", "public_url"),
    "GRABBIT_DEST": ("downloads", "dest"),
    "GRABBIT_INCOMPLETE_DIR": ("downloads", "incomplete_dir"),
    "GRABBIT_KEEP_DIRS": ("downloads", "keep_dirs"),
    "GRABBIT_PIN_RECHECK_MINUTES": ("downloads", "pin_recheck_minutes"),
    "GRABBIT_DATA_DIR": ("data_dir",),
    "GRABBIT_ENGINE_CHANNEL": ("engine", "channel"),
    "GRABBIT_LOG_LEVEL": ("logging", "level"),
    "GRABBIT_LOG_ENABLED": ("logging", "enabled"),
    "GRABBIT_MCP_ENABLED": ("mcp", "enabled"),
}


def load_config(path: Path | None = None) -> Config:
    """Load config.yaml (if present) and apply env-var overrides."""
    raw: dict = {}
    candidate = path or Path(os.environ.get("GRABBIT_CONFIG", "/config/config.yaml"))
    if candidate.is_file():
        with open(candidate) as f:
            raw = yaml.safe_load(f) or {}

    for env, keys in _ENV_OVERRIDES.items():
        val = os.environ.get(env)
        if val is None:
            continue
        node = raw
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = val

    return Config.model_validate(raw)
