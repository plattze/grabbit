# Build Spec — "Grabbit" (working name): a modern, self-hosted, web-native download manager

> **This document is a build spec / handoff prompt for a frontier coding model (Fable 5).**
> It describes a **brand-new open-source project** — its own Git repository, its own
> directory, deployed on the maintainer's own hardware (NOT tied to any existing server).
> The goal is a genuinely better self-hosted alternative to JDownloader: an always-on,
> web-native download manager that is **an engine-backed product**, using the
> community-maintained **gallery-dl** as its download engine — the same way Tube Archivist /
> MeTube use yt-dlp, or Jellyfin uses ffmpeg. We depend on the engine; we do **not** fork or
> reimplement it.

---

## 1. Motivation & context

This replaces a `jlesage/jdownloader-2` deployment — a Java Swing desktop app streamed over
noVNC. It's heavy (JVM + virtual desktop + VNC just to queue links) and its "web UI" is a
screen-share, not a real web app. Representative real-world usage that shaped this design:

- ~50,000 queued files across ~1,200 packages.
- **No** premium hoster accounts, **no** proxies, **no** packagizer/rules — default settings.
- Host mix ~90% file-host / album mirrors: `bunkr.*`, `cyberdrop`, `gofile`, `pixeldrain`,
  `simpcity` (XenForo forums), `cyberfile`, plus their signed-CDN backends.

**Key insight driving the design:** the valuable, hard, constantly-rotting part of a download
manager is the *extractors* — the per-site code that turns an album/thread URL into real,
signed file URLs. Sites rotate domains and markup weekly specifically to break scrapers.
gallery-dl already maintains these daily (canonical repo `codeberg.org/mikf/gallery-dl`,
installable from PyPI; verified: a bunkr/extractor 403 fix landed within a day). We treat
gallery-dl as a **commodity engine** and spend all our effort on the *product* around it.

### Why "engine-backed product," not "fork" and not "wrapper"

- **Forking gallery-dl is rejected.** A fork must either track upstream's daily commits
  forever (a wrapper with merge pain) or diverge and lose the ability to merge fixes (re-owning
  the scraper treadmill — the exact failure mode of trying to clone JDownloader). No third
  option. Instead: depend on a pinned release, and contribute fixes upstream if needed.
- **This is not "just a wrapper."** Jellyfin/HandBrake wrap ffmpeg; Tube Archivist/MeTube/
  Pinchflat wrap yt-dlp — all are real, beloved products. gallery-dl is a great *engine* but a
  weak *product* (CLI/TUI, run-and-exit, no server, no queue, no web UI, no multi-submit). That
  product gap is our entire opportunity and where "better than gallery-dl" is earned.

---

## 2. Goals

1. **Web server, always-on, behind a reverse proxy.** Primary deployment posture is an
   HTTP service published behind nginx/Traefik/Caddy in a homelab (§7). It starts, runs
   forever, survives restarts, and resumes in-flight work.
2. **Docker-native.** One container (or a tiny compose). No JVM, no VNC.
3. **Queue-anything.** Submit a URL from anywhere and it downloads in the background — the
   JDownloader experience that's actually used.
4. **Web-native UI.** A real single-page app for queue + status + live progress.
5. **Engine release-channel pinning.** The gallery-dl engine can be pinned to the **stable**
   channel (latest tagged PyPI release — the default) or the **dev** channel (upstream master
   snapshot), switchable via config with no app-code changes (§6, §9).
6. **Chrome-extension submittable.** Manifest V3 extension: right-click → "Send to Grabbit",
   plus a toolbar popup.
7. **Private & secure from the ground up** (§8): authenticated, no telemetry, SSRF-safe,
   safe defaults for reverse-proxy deployment.
8. **Simple config** with log rotation and a switch to disable logging entirely (§9).
9. **Decoupled engine** behind an adapter so gallery-dl is swappable/upgradable and a second
   engine can be added per-host later (§6).
10. **Proper published open-source project on GitHub under GPLv3** (§11).

## 2a. Non-goals (explicitly out of scope)

- ❌ Forking gallery-dl or vendoring its source. Depend on a pinned release only.
- ❌ Writing our own site extractors / scraper plugins.
- ❌ Reproducing JDownloader's plugin ecosystem, packagizer, captcha solvers, linkgrabber
  rules, or premium-account management.
- ❌ Public-internet exposure with no auth. (Reverse-proxy deployment is first-class, but auth
  is always on.)
- ❌ Auto-migrating an existing JDownloader queue (offer a separate importer later; §12).
- ⏸ **MCP server (backlogged, not v1).** An AI-agent tool surface (`queue_download` etc.) is
  desirable later but deferred to the backlog (§12, M5). AI agents can use the documented REST
  API in the meantime. Design the REST core so an MCP layer can be added without refactoring.

---

## 3. Architecture

```
     Chrome extension        Web UI / curl / API clients / AI agents (REST)
        │  POST /api/downloads      │
        └──────────────┬────────────┘
                       ▼
        ┌─────────────────────────────────────────────────────┐
        │  reverse proxy (nginx/Traefik/Caddy)  — TLS, auth-opt │
        └───────────────────────┬─────────────────────────────┘
                                ▼
              ┌─────────────────────────────────────────────┐
              │  FastAPI app  (single always-on process)     │
              │  ├─ REST API (auth, validation, rate-limit)  │
              │  ├─ static web UI (SPA)                      │
              │  ├─ /metrics (Prometheus), JSON logs         │
              │  └─ SQLite (queue + history + api keys)      │
              └───────────────────────┬─────────────────────┘
                                      │ dispatches jobs
                                      ▼
              ┌─────────────────────────────────────────────┐
              │  Async worker pool (asyncio)                 │
              │  ├─ Engine adapter  ─► GalleryDLEngine       │
              │  │     (gallery-dl, stable|dev channel)      │
              │  ├─ per-job progress + concurrency limits    │
              │  ├─ per-host rate limits                     │
              │  └─ writes to configured download dir        │
              └─────────────────────────────────────────────┘
```

Single process, single container: REST API, web UI, and worker pool share one
asyncio loop. SQLite (`aiosqlite`) is the durable store — no external DB.

---

## 4. Tech stack (and rationale)

| Layer | Choice | Why |
|---|---|---|
| Language / runtime | **Python 3.12** | The engine (gallery-dl) is Python; staying in Python keeps the engine adapter simple and lets us contribute fixes upstream. |
| API framework | **FastAPI + Uvicorn** | Async, typed (Pydantic v2), auto OpenAPI, WebSocket, easy behind a reverse proxy. |
| Download engine | **gallery-dl** (canonical repo Codeberg) — **stable** channel (latest tagged PyPI release, default) or **dev** channel (upstream master snapshot), selected via config | Covers 100% of the target hosts; daily upstream extractor maintenance. Dev channel picks up extractor fixes before they're tagged. |
| Engine invocation | **CLI contract** (`gallery-dl` subprocess: `--dump-json`, structured progress) with a thin optional library path | The library API is not a stable public contract; the CLI is. Isolates us from internal churn and makes upgrades a version bump. (§6) |
| Fallback engine (future) | **cyberdrop-dl** (specialist for bunkr/cyberdrop) | Added behind the same adapter, per-host, only if a host gets flaky. Not built in v1. |
| Persistence | **SQLite** (`aiosqlite`, WAL) | Zero-ops, durable, survives restarts. |
| Live progress | **WebSocket** | Push progress/state to the web UI. |
| Metrics | **Prometheus** `/metrics` | Homelab observability out of the box. |
| MCP (backlog) | **`mcp` Python SDK** | Deferred to M5; AI agents use the REST API until then. |
| Web UI | **React + Vite + TypeScript**, built to static assets served by FastAPI | Modern SPA, no separate server; works under a proxied sub-path. |
| Chrome extension | **Manifest V3 + TypeScript** | Right-click + popup submit. |
| Packaging | **Docker** multi-stage (build UI → slim Python runtime), non-root | Docker-native goal. |

> ⚠️ **Language note.** "Modern language" + "reuse gallery-dl (Python)" pull together toward
> **Python** here, because the engine is Python. A Go/Rust core is possible but would only ever
> talk to gallery-dl as a subprocess anyway (which the CLI-contract design already does) — so
> Python removes a language boundary for no lost capability. If the maintainer strongly prefers
> Go/Rust for the *service*, the CLI-contract engine design in §6 ports cleanly; revise §4 only.

---

## 5. HTTP API (the core everything builds on)

REST, JSON, versioned under `/api`. Must work correctly when served under a reverse-proxy
sub-path (honor `X-Forwarded-*`, configurable root path). All state-changing routes require
auth (§8).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/downloads` | Queue one or many URLs. Body `{ "urls":[...], "dest"?:str, "options"?:{...} }`. Validates each URL against a supported extractor; returns per-URL accepted/rejected. Idempotent for already-queued URLs. |
| `GET` | `/api/downloads` | List jobs (filter by state, pagination). |
| `GET` | `/api/downloads/{id}` | Job detail + per-file progress. |
| `POST` | `/api/downloads/{id}/pause`\|`/resume`\|`/retry` | Job control. |
| `DELETE` | `/api/downloads/{id}` | Cancel/remove (option to delete partial files). |
| `GET` | `/api/stats` | Queue depth, active count, throughput, free disk on target volume. |
| `GET` | `/api/health` | Liveness/readiness for the Docker healthcheck. |
| `GET` | `/metrics` | Prometheus metrics. |
| `WS` | `/api/ws` | Live progress + state events. |
| `GET`/`POST`/`DELETE` | `/api/keys[/{id}]` | API-key management (admin scope). |

- OpenAPI at `/api/docs`, gated behind auth outside dev.

---

## 6. Engine adapter (the robustness layer)

Define a narrow interface the rest of the app depends on — never call gallery-dl directly from
handlers or the worker:

```python
class Engine(Protocol):
    def supports(self, url: str) -> bool: ...          # is there an extractor for this URL?
    async def probe(self, url: str) -> list[FileRef]: ... # resolve to file list (no download)
    async def download(self, url: str, opts: EngineOpts,
                       on_progress: Callable) -> DownloadResult: ...
```

- **Release channels:** the engine version is controlled by `engine.channel` in config:
  - `stable` (default) — latest tagged gallery-dl release from PyPI, pinned in the lockfile
    and bumped by Dependabot/Renovate.
  - `dev` — a snapshot of upstream master (Codeberg archive / `pip install` from the repo),
    for picking up extractor fixes before they're tagged.
  - Channel selection changes only which gallery-dl gets installed/invoked — zero app-code
    difference. The Docker image ships stable; docs cover switching a deployment to dev.
- **v1 implementation: `GalleryDLEngine`**, invoking the `gallery-dl` **CLI**:
  - `supports()` / `probe()` via `gallery-dl --dump-json --no-download`.
  - `download()` streams gallery-dl's structured/progress output, mapped to `on_progress`
    events → WebSocket + DB.
  - A curated subset of gallery-dl options (concurrency, retries, rate limit, filename
    template, optional cookies file) is translated into gallery-dl config/flags. Do **not**
    surface all of gallery-dl's config — keep ours small (§9).
- **Why CLI over deep library imports:** gallery-dl's Python API is not a stable public
  contract; its CLI is. This makes `pip install -U gallery-dl` (or bumping the pinned version)
  a no-code upgrade and shields us from internal refactors. A thin library fast-path may be
  added later behind the same interface if profiling justifies it.
- **Swappability:** a future `CyberdropDLEngine` implements the same `Engine` and is selected
  per-host by a small router (e.g. bunkr/cyberdrop → cyberdrop-dl). Design the seam now; do not
  implement the second engine in v1.
- **Upstream-first:** when an extractor breaks, the fix path is "update the pinned gallery-dl"
  or "contribute a patch upstream" — never "patch our fork."

---

## 7. Deployment: web server behind a reverse proxy (primary posture)

- Ships as a long-running HTTP server (Uvicorn) in a container; **primary deployment is behind
  a reverse proxy** (nginx/Traefik/Caddy) that terminates TLS.
- **Bind:** configurable; container listens on `0.0.0.0:<port>` *inside its network namespace*
  but the compose/docs default publishes it only to the proxy (or to `127.0.0.1:<port>` on the
  host for a same-host proxy). Never assume direct public exposure.
- **Reverse-proxy correctness:** honor `X-Forwarded-For/Proto/Host`, support a configurable
  root path / sub-path mount, and make WebSocket upgrade work through the proxy (documented
  nginx `proxy_set_header Upgrade/Connection` snippet in `docs/`).
- Provide **ready-to-copy reverse-proxy configs** for nginx, Traefik labels, and Caddy in
  `docs/deploy/`, including a WebSocket-enabled example and an internal-only (LAN-restricted)
  example.
- Health/metrics endpoints suitable for uptime checks and Prometheus scraping.

---

## 8. Security model (built in from the ground up)

Threat model: an internet-adjacent (proxied) service that fetches arbitrary user-supplied
URLs and is reachable by browser extensions and AI agents. Priorities: no unauthenticated
access, no credential leakage, no SSRF, no telemetry.

**Authentication & authorization**
- **Mandatory API-key auth** on all mutating + listing endpoints (`Authorization: Bearer`).
  Constant-time comparison.
- Keys are random 256-bit tokens, shown once, **stored only as salted hashes** (argon2/scrypt).
- Scoped keys: `submit` (queue only) vs `admin` (keys/jobs/config). Extension + AI agents get
  `submit` keys.
- First-run bootstrap generates an admin key printed once to logs; **no default credentials**.
- Optional integration note: works cleanly behind proxy-level auth (e.g. an
  origin-verification header pattern or forward-auth), documented but not required.

**Input validation / SSRF**
- Every submitted URL must resolve to a **known extractor**; reject everything else. This is
  both UX and a security control — the service won't fetch arbitrary internal IPs, `file://`,
  `localhost`, or cloud-metadata endpoints.
- Additionally deny URLs resolving to private/link-local/loopback ranges even if an extractor
  matches (defense in depth).
- Strict Pydantic schemas; cap batch sizes.

**Web/proxy hardening**
- Strict CSP (no inline scripts beyond a hashed bootstrap), `X-Content-Type-Options`,
  `Referrer-Policy: no-referrer`, self-hosted assets only.
- Token-auth (not cookies) minimizes CSRF surface; if a cookie session is ever added, use
  SameSite=Strict + CSRF tokens.
- Trust `X-Forwarded-*` only from configured proxy IPs.

**Secrets, data, telemetry**
- Secrets via env/`.env` (documented `chmod 600`); never baked into the image or logged.
- Optional cookies file (for authed sites) mounted read-only; never logged.
- **Zero telemetry / no phone-home.** Any gallery-dl update check is off by default.

**Rate limiting**
- Per-key submit rate limits; per-host download concurrency caps (politeness + abuse control).

---

## 9. Configuration & logging (simple, rotatable, disable-able)

Single **`config.yaml`** (bind-mounted) + env-var overrides for secrets. Small surface:

```yaml
server:
  bind: 0.0.0.0          # inside container; publish via proxy/compose, not directly public
  port: 8080
  root_path: ""          # set when mounted under a proxy sub-path, e.g. "/grabbit"
  trusted_proxies: ["127.0.0.1"]
downloads:
  dest: /downloads
  max_concurrent: 5
  max_per_host: 2
  filename_template: "{category}/{title}/{filename}"
  cookies_file: null     # optional, read-only mount
engine:
  name: gallery-dl
  channel: stable        # stable (tagged PyPI release, default) | dev (upstream master snapshot)
  retries: 3
  rate_limit: null       # e.g. "2M" bytes/s; null = unlimited
logging:
  enabled: true          # <-- false disables file logging entirely
  level: info            # debug|info|warn|error
  format: json           # json|text   (JSON slots into Loki/ELK)
  rotation:
    max_size_mb: 50
    max_files: 5
    compress: true
security:
  require_auth: true     # not disableable in production builds
  update_check: false    # telemetry off
metrics:
  enabled: true          # /metrics
```

**Logging requirements (explicit)**
- **Disable switch:** `logging.enabled: false` → no log files (only fatal pre-config startup
  errors may hit stderr).
- **Rotation:** size-based, configurable `max_size_mb` / `max_files` / gzip `compress`
  (`RotatingFileHandler` or `concurrent-log-handler`).
- Configurable level; JSON format option for log aggregation.
- Never log secrets, API keys, cookies, or full signed CDN URLs with tokens (redact known
  token query params).

---

## 10. Persistence, resume, volumes

- **SQLite (WAL)** stores jobs, per-file items, states, timestamps, errors, api-key hashes,
  config snapshots. DB on a bind mount → survives container recreation.
- **Resume on startup:** reload queued/active jobs; mark interrupted in-flight jobs and
  requeue. gallery-dl's skip-existing handles partial sets.
- **Volumes (compose):**
  - `./config:/config` — config.yaml, SQLite DB, optional cookies (persistent).
  - `<host download dir>:/downloads` — output.
  - Optional `/etc/localtime:/etc/localtime:ro` + `TZ` env.

---

## 11. Open-source project requirements (GitHub, GPLv3)

Ship a *real* project, not a snippet dump. New standalone repo.

- **License: GPLv3** (`LICENSE`, SPDX headers). gallery-dl is **GPLv2**; we *depend on* (not
  vendor) it. Document the interaction and keep gallery-dl a pinned dependency — do not copy
  its source into the repo. (If any gallery-dl code were ever vendored, GPLv2/GPLv3 mixing must
  be reviewed; avoid by depending only.)
- **Repo hygiene:** `README.md` (what/why/screenshots/quickstart), `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md` (Keep a Changelog), issue/PR templates,
  `.editorconfig`.
- **CI (GitHub Actions):** ruff lint + format check, type check (mypy/pyright), pytest
  (unit + integration), frontend build + lint, Docker build. Green required to merge.
- **Tests:** queue lifecycle; auth accept/reject/scopes; SSRF rejection; config parsing incl.
  logging-disabled + rotation; engine adapter against a **mocked** gallery-dl CLI (no live net
  in CI); reverse-proxy sub-path + `X-Forwarded-*` handling. Opt-in live smoke tests behind a
  flag.
- **Releases:** SemVer, tagged releases, multi-arch (amd64+arm64) image published to **GHCR**
  (`ghcr.io/<owner>/grabbit`).
- **Docs (`docs/`):** compose install; config reference (incl. engine channel switching);
  API reference (OpenAPI link); Chrome-extension install; **reverse-proxy configs
  (nginx/Traefik/Caddy, incl. WebSocket + internal-only)**; security notes.
- **Reproducible Docker:** multi-stage, pinned base + deps (`uv.lock`/`requirements.txt`),
  non-root user, `HEALTHCHECK`, small final image.
- **Supply chain:** Dependabot/renovate (incl. gallery-dl bumps), SBOM on release, no secrets
  in history.

---

## 12. Deliverables & milestones

**M1 — Core service (MVP):** FastAPI app, SQLite queue, `GalleryDLEngine` via CLI contract,
worker pool, config.yaml + logging (rotation + disable), REST API with API-key auth, Docker
image + compose, healthcheck, `/metrics`.
*Acceptance:* `POST /api/downloads` a bunkr album → files land in `/downloads`; survives a
mid-queue restart.

**M2 — Web UI:** React/Vite SPA served by FastAPI: submit box, live queue/progress over
WebSocket, job controls, stats, key management. Works under a proxy sub-path. Hardened CSP.

**M3 — Reverse-proxy + extension:** ship nginx/Traefik/Caddy configs and verify
WebSocket through a proxy; MV3 Chrome extension (options page for host + key, context-menu
"Send to Grabbit", popup with recent status).

**M4 — Open-source polish:** full repo hygiene, green CI, GHCR multi-arch release, docs site.

**M5 (backlog, optional):** **MCP server** (`queue_download`, `list_downloads`,
`download_status`, submit-scoped — built on the REST core); JDownloader `downloadList*.zip`
importer; `CyberdropDLEngine` behind the router for flaky hosts.

---

## 13. Acceptance criteria (v1 = M1–M4)

1. `docker compose up -d` → healthy, always-on container.
2. Submitting a supported URL via REST and the Chrome extension both enqueue and download.
3. Works correctly behind an nginx reverse proxy on a sub-path, including WebSocket progress.
4. Unsupported / private-range URLs rejected at submit time.
5. All mutating endpoints reject requests without a valid scoped API key.
6. `logging.enabled: false` → no log files; enabled logs rotate per config.
7. Queue survives restart and resumes.
8. No telemetry/outbound calls except to the requested download hosts.
9. Engine is swappable via the adapter; upgrading gallery-dl or switching between the
   stable/dev release channels needs no app code changes.
10. GPLv3 license, green CI, published GHCR image, tagged release.

---

## 14. Open decisions to confirm with the maintainer

- ~~**Project name**~~ — **decided: Grabbit** (repo `github.com/plattze/grabbit`).
- ~~**Language for the service**~~ — **decided: Python.**
- ~~**AI-agent surface**~~ — **decided: REST only for v1; MCP server backlogged to M5.**
- ~~**v1 scope of JDownloader queue import**~~ — **decided: defer to M5.**

