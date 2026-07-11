# Grabbit

A modern, self-hosted, web-native download manager — an engine-backed product built on
[gallery-dl](https://codeberg.org/mikf/gallery-dl), the same way MeTube wraps yt-dlp or
Jellyfin wraps ffmpeg.

Grabbit replaces heavyweight JDownloader-over-noVNC deployments with a single always-on
container: queue a URL from anywhere and it downloads in the background.

## Features

- **Queue anything** — paste URLs into the web UI, hit the REST API, or right-click →
  "Send to Grabbit" from the Chrome extension. Per-URL validation at submit time.
- **Real web UI** — live queue with WebSocket progress, job controls
  (pause/resume/retry/cancel), stats, and API-key management. No JVM, no VNC.
- **Always-on & durable** — SQLite-backed queue survives restarts and resumes
  interrupted jobs automatically.
- **Engine channels** — the gallery-dl engine is pinned to the **stable** channel
  (tagged PyPI release) by default; switch to **dev** (upstream master) when you need
  an extractor fix that isn't tagged yet. Zero app-code difference.
- **Reverse-proxy first** — ships with nginx/Caddy/Traefik configs, works under a
  sub-path, WebSocket included. Honors `X-Forwarded-*` only from trusted proxies.
- **Secure by default** — mandatory scoped API keys (scrypt-hashed, shown once),
  SSRF guards for private/link-local addresses, strict CSP, zero telemetry.
- **Homelab-friendly** — Prometheus `/metrics`, JSON logs with rotation (or fully
  disabled), one small `config.yaml`.

## Quickstart

```bash
mkdir grabbit && cd grabbit
curl -O https://raw.githubusercontent.com/plattze/grabbit/main/docker-compose.yml
docker compose up -d
docker compose logs grabbit    # copy the one-time admin API key
```

Open http://localhost:8080, paste the key. Full guide: [docs/install.md](docs/install.md).

## Docs

- [Install & deploy](docs/install.md) · [Configuration](docs/configuration.md)
- [Reverse-proxy configs](docs/deploy/) (nginx / Caddy / Traefik)
- [Chrome extension](docs/extension.md)
- [Build spec / design](docs/SPEC.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Why not fork JDownloader / gallery-dl?

The hard, constantly-rotting part of a download manager is the per-site extractors.
gallery-dl's community maintains those daily; Grabbit deliberately contains **zero**
extractor code and never will. When a site breaks, the fix is an engine version bump —
or a patch upstream — never a fork. See [docs/SPEC.md](docs/SPEC.md) for the full
reasoning.

## Backlog (post-v1)

- MCP server so AI agents can queue downloads natively (the REST API works today).
- JDownloader queue importer.
- Per-host fallback engine (cyberdrop-dl) behind the same adapter.

## License

[GPL-3.0](LICENSE). Grabbit depends on gallery-dl (GPL-2.0) as an external engine; its
source is not vendored here.
