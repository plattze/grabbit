# Changelog

All notable changes to Grabbit are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- MCP server (`grabbit-mcp`, stdio) so AI agents can queue downloads natively:
  `queue_download`, `list_downloads`, `download_status` — a thin wrapper over
  the REST API using submit-scoped key auth. Ships in the Docker image and as
  the `mcp` extra (`pip install "grabbit[mcp]"`). Disable with
  `mcp.enabled: false` or `GRABBIT_MCP_ENABLED=false`.

## [0.1.0] - 2026-07-11

### Added
- Core service: FastAPI app, SQLite (WAL) queue with restart resume, asyncio
  worker pool with global and per-host concurrency limits.
- gallery-dl engine adapter speaking the CLI contract, with `stable`/`dev`
  release channels selectable in config.
- Scoped API keys (`submit`/`admin`), scrypt-hashed, bootstrap admin key
  printed once on first run; per-key submit rate limiting.
- SSRF guards: extractor validation plus private/link-local/loopback denial.
- Web UI: submit box, live queue over WebSocket, job controls, stats, API-key
  management; works under a reverse-proxy sub-path.
- Chrome extension (MV3): context-menu send, popup, options page.
- Reverse-proxy configs for nginx, Caddy, and Traefik.
- Prometheus `/metrics`, JSON logs with rotation and a full disable switch.
- Docker image (non-root, healthcheck) and compose file.

[0.1.0]: https://github.com/plattze/grabbit/releases/tag/v0.1.0
