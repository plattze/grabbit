# Grabbit

A modern, self-hosted, web-native download manager.

Grabbit replaces heavyweight JDownloader-over-noVNC deployments with a single always-on
container: queue a URL from anywhere and it downloads in the background.

## Features

- **Queue anything** — paste URLs into the web UI, hit the REST API, or right-click →
  "Send to Grabbit" from the Chrome extension. Per-URL validation at submit time.
- **Real web UI** — live queue with WebSocket progress, job controls
  (pause/resume/retry/cancel), stats, and API-key management. No JVM, no VNC.
- **Always-on & durable** — SQLite-backed queue survives restarts and resumes
  interrupted jobs automatically.
- **Engine channels** — the download engine is pinned to the **stable** channel by
  default; switch to **dev** when you need an extractor fix that isn't in a tagged
  release yet. Zero app-code difference.
- **Reverse-proxy first** — ships with nginx/Caddy/Traefik configs, works under a
  sub-path, WebSocket included. Honors `X-Forwarded-*` only from trusted proxies.
- **Secure by default** — mandatory scoped API keys (scrypt-hashed, shown once),
  SSRF guards for private/link-local addresses, strict CSP, zero telemetry.
- **Homelab-friendly** — Prometheus `/metrics`, JSON logs with rotation (or fully
  disabled), one small `config.yaml`.
- **AI-agent ready** — optional [MCP server](docs/mcp.md) (`grabbit-mcp`) exposes
  `queue_download` / `list_downloads` / `download_status`; disable with one flag.
- **Escape JDownloader** — [`grabbit-import-jd`](docs/import-jdownloader.md)
  migrates an existing JDownloader queue, packages becoming sub-folders.

> **Status: active development.** The GHCR image is private for now — build from
> source until the public launch.

## Quickstart (from source)

```bash
git clone https://github.com/plattze/grabbit.git && cd grabbit
docker compose up -d --build
docker compose logs grabbit    # copy the one-time admin API key
```

Open http://localhost:8080, paste the key. Full guide: [docs/install.md](docs/install.md).

## Docs

- [Install & deploy](docs/install.md) · [Configuration](docs/configuration.md)
- [Reverse-proxy configs](docs/deploy/) (nginx / Caddy / Traefik)
- [Chrome extension](docs/extension.md) · [MCP server](docs/mcp.md) ·
  [JDownloader import](docs/import-jdownloader.md)
- [Build spec / design](docs/SPEC.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Why not fork JDownloader?

The hard, constantly-rotting part of a download manager is the per-site extractors.
Grabbit deliberately contains **zero** extractor code and never will — it delegates to
a community-maintained engine behind a clean adapter. When a site breaks, the fix is an
engine version bump — or a patch upstream — never a fork. See
[docs/SPEC.md](docs/SPEC.md) for the full reasoning.

## Backlog (post-v1)

- Per-host fallback engine (cyberdrop-dl) behind the same adapter.

## License

[GPL-3.0](LICENSE). Grabbit depends on an external GPL-2.0 download engine; its source
is not vendored here.
