# Installing Grabbit

## Docker Compose (recommended)

```yaml
services:
  grabbit:
    image: ghcr.io/plattze/grabbit:latest
    container_name: grabbit
    restart: unless-stopped
    ports:
      - "127.0.0.1:8080:8080"   # localhost only; the reverse proxy is the public face
    volumes:
      - ./config:/config
      - ./downloads:/downloads
    environment:
      - TZ=Etc/UTC
```

```bash
docker compose up -d
docker compose logs grabbit   # <-- copy the one-time admin API key from here
```

Open `http://localhost:8080`, paste the admin key, and you're in.

Optionally drop a [`config.yaml`](../config.example.yaml) into `./config/` —
everything has sane defaults, so start without one and add settings as needed.

## First steps

1. **Create a submit key** (web UI → API keys) for the Chrome extension and scripts.
   Keep the admin key for management only.
2. **Queue something:**
   ```bash
   curl -X POST http://localhost:8080/api/downloads \
     -H "Authorization: Bearer <submit-key>" \
     -H "Content-Type: application/json" \
     -d '{"urls": ["https://example.com/album/123"]}'
   ```
3. **Put it behind your proxy** — copy a config from [`deploy/`](deploy/) (nginx,
   Caddy, Traefik; each includes WebSocket support and an internal-only variant).
   When mounting under a sub-path, set `server.root_path` (or `GRABBIT_ROOT_PATH`).

## Engine channels

The download engine (gallery-dl) is pinned to its latest tagged release in the
published image (`stable` channel). If a site fix has landed upstream but isn't
tagged yet, build a `dev`-channel image:

```bash
docker build --build-arg ENGINE_CHANNEL=dev -t grabbit:dev .
```

Switching channels changes only the engine version — no Grabbit code differs.

## API docs

Interactive OpenAPI docs are at `/api/docs` (auth required for the endpoints).
Prometheus metrics at `/metrics`; liveness at `/api/health`.
