# MCP server

Grabbit ships an [MCP](https://modelcontextprotocol.io) server so AI agents can
queue and monitor downloads natively. It is a thin stdio wrapper over the REST
API — point it at a running Grabbit instance with a submit-scoped API key.

## Tools

| Tool | Purpose |
|---|---|
| `queue_download(urls, dest?)` | Submit URLs; per-URL accept/reject results |
| `list_downloads(state?, limit?)` | List jobs, optionally filtered by state |
| `download_status(job_id)` | Status of a single job |

## Setup

The `grabbit-mcp` entrypoint is included in the Docker image and in
`pip install "grabbit[mcp]"`. Configuration is via environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `GRABBIT_MCP_URL` | `http://localhost:8080` | Base URL of the Grabbit instance |
| `GRABBIT_MCP_API_KEY` | — (required) | A submit-scoped API key |

Example Claude Desktop / Claude Code config:

```json
{
  "mcpServers": {
    "grabbit": {
      "command": "docker",
      "args": ["exec", "-i", "grabbit", "grabbit-mcp"],
      "env": {
        "GRABBIT_MCP_API_KEY": "grb_..."
      }
    }
  }
}
```

Or, with grabbit installed locally (`pip install "grabbit[mcp]"`), set
`command` to `grabbit-mcp` and add `GRABBIT_MCP_URL` pointing at your server.

## Disabling

Set `mcp.enabled: false` in `config.yaml`, or `GRABBIT_MCP_ENABLED=false` in
the environment (see the commented line in `docker-compose.yml`) — the
`grabbit-mcp` entrypoint then refuses to start. The web app itself never
starts an MCP server; nothing listens unless you run `grabbit-mcp`.
