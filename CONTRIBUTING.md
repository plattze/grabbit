# Contributing to Grabbit

Thanks for helping! A few ground rules keep the project healthy.

## Philosophy: engine-backed product

Grabbit deliberately does **not** contain site extractors. The
[gallery-dl](https://codeberg.org/mikf/gallery-dl) engine owns that problem.

- **A site is broken?** Update the pinned gallery-dl (or switch the deployment
  to the `dev` engine channel) — or contribute the fix *upstream* to gallery-dl.
- **PRs adding scrapers/extractors to this repo will be declined.** That's the
  treadmill this design exists to avoid.
- Never vendor gallery-dl source into this repo (GPLv2/GPLv3 mixing).

## Dev setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cd web && npm install && cd ..

pytest              # tests (mocked engine; no live network)
ruff check grabbit tests
mypy grabbit
cd web && npm run build
```

Run the server locally:

```bash
GRABBIT_CONFIG=./config.example.yaml GRABBIT_DATA_DIR=./tmp GRABBIT_DEST=./tmp/dl python -m grabbit
```

## Pull requests

- Green CI required (lint, types, tests, frontend build, Docker build).
- Tests accompany behavior changes. Engine interactions are tested against the
  mocked CLI in `tests/conftest.py` — live-network tests don't run in CI.
- Keep the config surface small; new options need a documented reason.
- Follow [Keep a Changelog](https://keepachangelog.com) in `CHANGELOG.md`.

## Security issues

See [SECURITY.md](SECURITY.md) — please don't open public issues for vulnerabilities.
