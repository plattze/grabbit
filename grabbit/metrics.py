"""Prometheus metrics."""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)

registry = CollectorRegistry()

jobs_submitted = Counter("grabbit_jobs_submitted_total", "Jobs accepted into the queue",
                         registry=registry)
jobs_completed = Counter("grabbit_jobs_completed_total", "Jobs finished", ["state"],
                         registry=registry)
jobs_active = Gauge("grabbit_jobs_active", "Jobs currently downloading", registry=registry)
queue_depth = Gauge("grabbit_queue_depth", "Jobs waiting in queue", registry=registry)


def render() -> tuple[bytes, str]:
    return generate_latest(registry), CONTENT_TYPE_LATEST
