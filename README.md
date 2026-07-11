# Grabbit

A modern, self-hosted, web-native download manager — an engine-backed product built on
[gallery-dl](https://codeberg.org/mikf/gallery-dl), the same way MeTube wraps yt-dlp or
Jellyfin wraps ffmpeg.

Grabbit replaces heavyweight JDownloader-over-noVNC deployments with a single always-on
container: queue a URL from anywhere and it downloads in the background.

> **Status: pre-alpha.** Nothing works yet — this repo currently holds the
> [build spec](docs/SPEC.md). Watch/star if you're interested.

## Planned features (v1)

- **Always-on web service** — FastAPI + SQLite in one container, designed to sit behind a
  reverse proxy (nginx/Traefik/Caddy), survive restarts, and resume in-flight work.
- **Queue anything** — REST API to submit one or many URLs; per-URL validation against
  gallery-dl's extractors at submit time.
- **Web UI** — real single-page app (React + Vite) with live progress over WebSocket.
- **Chrome extension** — right-click → "Send to Grabbit".
- **Engine channels** — pin gallery-dl to the **stable** channel (tagged PyPI release,
  default) or the **dev** channel (upstream master snapshot) via config.
- **Secure by default** — mandatory scoped API keys, SSRF protection, no telemetry.
- **Homelab-friendly** — Prometheus `/metrics`, JSON logs with rotation (or disabled
  entirely), simple `config.yaml`.

## Backlog (post-v1)

- MCP server so AI agents can queue downloads natively (REST works in the meantime).
- JDownloader queue importer.
- Per-host fallback engine (cyberdrop-dl) behind the same adapter.

## License

[GPL-3.0](LICENSE). Grabbit depends on gallery-dl (GPL-2.0) as an external engine; its
source is not vendored here.
