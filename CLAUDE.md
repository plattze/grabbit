# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Grabbit is a self-hosted, web-native download manager: a FastAPI backend + React SPA in one Docker container, wrapping gallery-dl as an external download engine (the way MeTube wraps yt-dlp). The GHCR image is private until public launch — install docs and README assume build-from-source for now.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd web && npm install

# Backend (run from repo root)
pytest                          # all tests (mocked engine, no network)
pytest tests/test_api.py -k submit   # single file / test
ruff check grabbit tests
mypy grabbit

# Frontend (run from web/)
npm run build                   # tsc -b && vite build (also the CI type check)
npm run dev                     # Vite dev server

# Run the server locally
GRABBIT_CONFIG=./config.example.yaml GRABBIT_DATA_DIR=./tmp GRABBIT_DEST=./tmp/dl python -m grabbit
```

CI (`.github/workflows/ci.yml`) requires all of: ruff, mypy, pytest, `npm run build`, and a Docker image build.

## Architecture

Request/job flow: `api.py` (REST routes under `/api`, all bearer-auth'd) → `db.py` (SQLite/aiosqlite job queue, WAL) → `worker.py` (`WorkerPool` dispatch loop pulls QUEUED jobs, enforces global + per-host concurrency semaphores) → `engine.py` (runs the gallery-dl CLI as a subprocess) → progress flows back via `events.py` (`EventHub`) to WebSocket subscribers and the SPA.

Key seams and invariants:

- **`engine.py` is the only module allowed to touch gallery-dl.** The download path uses the gallery-dl *CLI* (its stable contract), not the library; the one library call is extractor matching in `supports()`. The `Engine` Protocol exists so alternative engines (e.g. cyberdrop-dl, backlogged) can slot in behind the same adapter.
- **No extractor code, ever.** Site breakage is fixed by an engine version bump or an upstream patch — PRs adding scrapers are declined (see CONTRIBUTING.md). Never vendor gallery-dl source (GPLv2/GPLv3 mixing).
- **Engine channels**: `stable` (pinned PyPI release) vs `dev` (upstream master), selected at Docker build time via `ARG ENGINE_CHANNEL` and mirrored in runtime config. Zero app-code difference between channels.
- **Durability**: jobs live in SQLite; `WorkerPool.start()` requeues jobs interrupted by a restart. A pause/cancel that races job completion wins over the engine result (`worker._run_job`).
- **Auth**: mandatory scoped API keys (`admin` / `submit`), scrypt-style hashed in DB, shown once at creation; a bootstrap admin key is created on first start and logged. Per-key submit rate limiting in `auth.py`.
- **Security posture**: SSRF checks on submitted URLs (`ssrf.py`, advisory — the extractor allowlist is the primary control), `X-Forwarded-*` honored only from `trusted_proxies` (middleware in `app.py`), strict CSP, URL token redaction before logging (`logging_setup.redact_url`).
- **Config**: `config.yaml` + `GRABBIT_*` env overrides (`config.py`). Keep the config surface small; new options need a documented reason.
- **SPA serving**: `web/dist` is copied to `grabbit/static/` in the Docker build and mounted at `/` last, so `/api` and `/metrics` win.

## Testing conventions

Engine interactions are tested against a mock gallery-dl CLI script (`tests/conftest.py::MOCK_GALLERY_DL`) — it fakes downloads, and URLs containing `fail` or `slow` trigger those behaviors. The `app` fixture hides the real `gallery_dl` library so `supports()` falls back to accepting http(s). Lifespan is driven explicitly because httpx's ASGITransport doesn't. No live-network tests in CI.

## Docs & conventions

- `docs/SPEC.md` is the build spec / design rationale; milestone history is in git (M1–M4).
- The README intentionally does not name gallery-dl — it markets Grabbit as a modern web-native downloader; engine attribution lives in SPEC.md, CONTRIBUTING.md, and install docs.
- Changelog follows Keep a Changelog in `CHANGELOG.md`.
