# Changelog

All notable changes to Grabbit are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.4] - 2026-07-12

### Fixed
- Renaming a completed download now works when the job has no detected output
  directory — files that landed flat in the destination root (`keep_dirs:
  false`, single-file downloads) are gathered into the requested directory.
  Each job now records its downloaded file paths at completion to make this
  possible; jobs finished before this release (no recorded files, no
  directory) still return 409. The web UI shows the Rename button on all
  non-cancelled jobs accordingly.

## [0.2.3] - 2026-07-11

### Added
- Merge completed downloads from the history view (new
  `POST /api/downloads/merge`): select two or more finished jobs with the new
  checkboxes and a "Merge into one folder" action appears. It prompts for a
  folder name (prefilled with the first selection's) and moves every selected
  job's files into that directory; filename collisions are kept with a
  `" (2)"` suffix. The original history entries remain, all pointing at the
  merged directory.

## [0.2.2] - 2026-07-11

### Added
- Rename a job's output directory from the web UI (new
  `POST /api/downloads/{id}/rename`). The rename prompt prefills the current
  directory name (tracked per job as `dir_name`, detected from the engine's
  output paths). Renaming a running job is recorded and applied when the
  download completes; renaming a finished job moves the directory immediately.
  Renaming onto an existing directory merges into it. Directory-level only —
  individual files cannot be renamed.

## [0.2.1] - 2026-07-11

### Added
- Downloads now keep the source's directory names (album/gallery structure)
  as subdirectories under `dest` instead of flattening every file into it.
  On by default — including for existing installs; restore the old flat
  layout with `downloads.keep_dirs: false` or `GRABBIT_KEEP_DIRS=false`.
  The preserved structure survives the `incomplete/` → complete staging move.

## [0.2.0] - 2026-07-11

### Added
- MCP server (`grabbit-mcp`, stdio) so AI agents can queue downloads natively:
  `queue_download`, `list_downloads`, `download_status` — a thin wrapper over
  the REST API using submit-scoped key auth. Ships in the Docker image and as
  the `mcp` extra (`pip install "grabbit[mcp]"`). Disable with
  `mcp.enabled: false` or `GRABBIT_MCP_ENABLED=false`.
- JDownloader queue importer (`grabbit-import-jd`): parses `downloadList*.zip`,
  maps packages to dest sub-folders, and submits through the normal REST
  validation path (per-URL rejection reasons, rate-limit aware, `--dry-run`).
- Settings page in the web UI (admin scope) backed by `GET /api/settings`:
  shows the running configuration (server, downloads, engine, logging,
  metrics, MCP) as read-only — values come from config.yaml / env / Docker.
  The response shape reserves an `editable` section for future settings.
- "Install Chrome plugin" on the web UI home page: downloads the extension
  zipped from `/api/extension.zip` (admin scope), preconfigured with the
  server URL and a freshly minted submit-scoped API key baked into
  `preconfig.json` — the extension applies it on install, zero manual setup.
  New `server.public_url` / `GRABBIT_PUBLIC_URL` config for deployments behind
  a reverse proxy (also surfaced in `/api/stats`).
- Optional sabnzbd-style staging layout (`downloads.incomplete_dir` /
  `GRABBIT_INCOMPLETE_DIR`): active jobs download into
  `<incomplete_dir>/job-<id>/` and move to `dest` on completion (atomic rename
  on the same filesystem), so files under `dest` are always complete. Cancelled
  jobs' staged partials are cleaned up; paused jobs keep theirs for resume.

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

[0.2.0]: https://github.com/plattze/grabbit/releases/tag/v0.2.0
[0.1.0]: https://github.com/plattze/grabbit/releases/tag/v0.1.0
