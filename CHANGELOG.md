# Changelog

All notable changes to Grabbit are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Downloads now show a human-readable title (e.g. the album/gallery name)
  instead of just the URL slug. Grabbit resolves it from the source with a
  fast metadata-only engine probe (`--dump-json --no-download --range 1`, so a
  large album doesn't stall resolving every file) and stores it per job as a
  new `title` field; the UI shows the title with the URL as a clickable
  subline. The on-disk directory name is never changed — the title is display
  metadata only. Existing downloads are backfilled once on startup (one job at
  a time, with a polite gap between probes). Best-effort throughout: a source
  with no title, a failed probe, or a timeout simply leaves the title unset and
  the slug is shown. Toggle with `downloads.resolve_titles` /
  `GRABBIT_RESOLVE_TITLES` (default on).

## [0.2.10] - 2026-07-18

### Added
- "Clear finished" button on the queue view (new
  `POST /api/downloads/clear-finished`): removes every finished
  (done/error/cancelled) download from history in one action, cleaning up any
  staging leftovers. Pinned downloads are kept even when finished — their source
  is still monitored — and active/queued/paused jobs are untouched. The button
  is disabled when there is nothing clearable and confirms before removing.

## [0.2.9] - 2026-07-18

### Changed
- The downloads table now fits its columns instead of cramming into the old
  960px layout: the app widens to 1500px and the table uses a fixed layout with
  stable per-column widths (host, status, files, directory, dates, actions),
  letting the URL column absorb the remaining width. Long error messages wrap
  and scroll within their cell instead of stretching the row. Completes the
  visual side of the sortable table added in 0.2.7.

## [0.2.8] - 2026-07-18

### Changed
- Downloads now land directly in their package directory (`dest/<package>/…`)
  instead of under a redundant site/domain parent (`dest/<domain>/<package>/…`).
  The domain level added no value. Implemented by emptying gallery-dl's
  `{category}` directory segment (it drops empty path segments), so files are
  written to the flattened path directly — pinned rechecks' skip-existing still
  works. A one-time startup migration flattens existing finished downloads,
  driven from the database (never a destination scan): it merges each
  `dest/<domain>/<package>` up into `dest/<package>` (reusing the rename/merge
  collision handling), updates the affected job records, and removes the emptied
  domain directory. User-renamed and already-flat jobs are left untouched, and
  the migration runs once (guarded by the SQLite schema version).

## [0.2.7] - 2026-07-12

### Changed
- The downloads list is now a proper table with sortable columns (URL, host,
  status, files, directory, added, finished) — click a header to sort, click
  again to reverse, a third time to restore the default order (newest first).
  Pinned jobs stay on top within any sort. A refresh button (🔄) above the
  list reloads it on demand. All per-row actions (pause/resume/retry/cancel,
  rename, pin, merge checkboxes, progress bar, error display) carry over.

## [0.2.6] - 2026-07-12

### Added
- Optional `downloads.reset_mtime` (env `GRABBIT_RESET_MTIME`, default
  `false`): stamp downloaded files with the download time instead of the
  source's original timestamp, so sorting by date modified in a file manager
  surfaces what's new. Implemented by disabling the engine's
  mtime-from-metadata handling; the reset timestamps survive the
  `incomplete/` → complete staging move. Off by default — files keep the
  source's original timestamps as before.

## [0.2.5] - 2026-07-12

### Added
- Pin a download to keep watching its source (new
  `POST /api/downloads/{id}/pin` and a Pin/Unpin button per job). A pinned
  job is re-queued every `downloads.pin_recheck_minutes` (default 60,
  env `GRABBIT_PIN_RECHECK_MINUTES`) after it finishes; the engine's
  skip-existing behavior means only files added at the source since the last
  run are downloaded. Rechecks respect the normal concurrency and per-host
  limits, land new files in the job's current (possibly renamed) directory,
  and pinned jobs sort to the top of the downloads list. Unpin to stop
  monitoring.

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
