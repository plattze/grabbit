"""Entry point: `python -m grabbit` or the `grabbit` console script."""

from __future__ import annotations

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    cfg = load_config()
    app = create_app(cfg)
    uvicorn.run(
        app,
        host=cfg.server.bind,
        port=cfg.server.port,
        proxy_headers=True,
        forwarded_allow_ips=",".join(cfg.server.trusted_proxies),
        log_config=None,  # our logging_setup owns handlers
        access_log=cfg.logging.enabled,
    )


if __name__ == "__main__":
    main()
