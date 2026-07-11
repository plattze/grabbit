# Configuration reference

Grabbit reads `/config/config.yaml` (override the path with `GRABBIT_CONFIG`).
Every setting is optional; [config.example.yaml](../config.example.yaml) shows
all defaults.

## server

| Key | Default | Notes |
|---|---|---|
| `bind` | `0.0.0.0` | Interface inside the container. Publish via compose/proxy, don't expose directly. |
| `port` | `8080` | |
| `root_path` | `""` | Set when mounted under a proxy sub-path, e.g. `/grabbit`. |
| `trusted_proxies` | `["127.0.0.1"]` | Only these sources may set `X-Forwarded-*`; from anyone else the headers are stripped. |

## downloads

| Key | Default | Notes |
|---|---|---|
| `dest` | `/downloads` | Root output directory (volume). |
| `max_concurrent` | `5` | Simultaneous jobs overall. |
| `max_per_host` | `2` | Simultaneous jobs per host (politeness). |
| `filename_template` | `null` | gallery-dl filename template. |
| `cookies_file` | `null` | Netscape cookies file for authed sites, e.g. `/config/cookies.txt` (mount read-only). |

## engine

| Key | Default | Notes |
|---|---|---|
| `name` | `gallery-dl` | v1 supports gallery-dl only. |
| `channel` | `stable` | `stable` = tagged PyPI release; `dev` = upstream master snapshot. The image must be built for the channel (see [install.md](install.md)). |
| `retries` | `3` | Per-file retries. |
| `rate_limit` | `null` | Bytes/s cap, e.g. `"2M"`. |

## logging

| Key | Default | Notes |
|---|---|---|
| `enabled` | `true` | `false` writes no log files at all. |
| `level` | `info` | `debug` / `info` / `warn` / `error`. |
| `format` | `json` | `json` for Loki/ELK, `text` for humans. |
| `rotation.max_size_mb` | `50` | Size-based rotation threshold. |
| `rotation.max_files` | `5` | Rotated files kept. |
| `rotation.compress` | `true` | Gzip rotated files. |

Signed-URL token query params are redacted before URLs are logged.

## security / metrics / mcp

| Key | Default | Notes |
|---|---|---|
| `security.require_auth` | `true` | API-key auth on all mutating/listing endpoints. |
| `security.update_check` | `false` | No phone-home, ever. |
| `metrics.enabled` | `true` | Prometheus at `/metrics`. |
| `mcp.enabled` | `true` | `false` = the [`grabbit-mcp`](mcp.md) entrypoint refuses to start. Nothing listens either way unless you run `grabbit-mcp`. |

## Environment overrides

Handy for compose; env wins over the YAML file.

| Variable | Maps to |
|---|---|
| `GRABBIT_CONFIG` | config file path |
| `GRABBIT_PORT` | `server.port` |
| `GRABBIT_ROOT_PATH` | `server.root_path` |
| `GRABBIT_DEST` | `downloads.dest` |
| `GRABBIT_DATA_DIR` | data dir (SQLite DB, logs) |
| `GRABBIT_ENGINE_CHANNEL` | `engine.channel` |
| `GRABBIT_LOG_LEVEL` | `logging.level` |
| `GRABBIT_LOG_ENABLED` | `logging.enabled` |
| `GRABBIT_MCP_ENABLED` | `mcp.enabled` |

The MCP server itself is configured with `GRABBIT_MCP_URL` and
`GRABBIT_MCP_API_KEY` — see [mcp.md](mcp.md).
