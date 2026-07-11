# syntax=docker/dockerfile:1

# --- frontend build (no-op until M2 adds web/; keep the stage for cache shape)
FROM node:22-slim AS ui
WORKDIR /build
COPY web* /build/web/
RUN if [ -f web/package.json ]; then \
        cd web && npm ci && npm run build; \
    else \
        mkdir -p web/dist; \
    fi

# --- python runtime
FROM python:3.12-slim AS runtime

# GRABBIT_ENGINE_CHANNEL=dev at *build* time installs gallery-dl from upstream
# master instead of the pinned PyPI release; runtime config mirrors the choice.
ARG ENGINE_CHANNEL=stable

RUN groupadd -r grabbit && useradd -r -g grabbit -d /app grabbit

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY grabbit/ ./grabbit/

RUN pip install --no-cache-dir . && \
    if [ "$ENGINE_CHANNEL" = "dev" ]; then \
        pip install --no-cache-dir -U --force-reinstall \
            "gallery-dl @ https://codeberg.org/mikf/gallery-dl/archive/master.tar.gz"; \
    fi

COPY --from=ui /build/web/dist/ ./grabbit/static/

RUN mkdir -p /config /downloads && chown -R grabbit:grabbit /config /downloads
VOLUME ["/config", "/downloads"]

USER grabbit
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,os; \
        urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"GRABBIT_PORT\",8080)}/api/health')" || exit 1

ENV GRABBIT_CONFIG=/config/config.yaml
CMD ["python", "-m", "grabbit"]
