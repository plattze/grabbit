Build and ship all open backlog items, one at a time, following the full pipeline below for each item.

## 1. Load the backlog

Run `/backlog` to get the current list. Only items in `backlog/new/` and `backlog/in-progress/` are candidates — spec M5 mentions or GitHub issues without a backlog file are surfaced for awareness, not built.

## 2. Pick the next item (priority order)

1. Anything already in `in-progress/` (finish what's started)
2. Defects (`**Type:** defect`), lowest number first
3. Features, lowest number first

## 3. For each item, complete this full cycle before starting the next

### a) Start
- Move the item file from `new/` to `in-progress/` (`mv`, unchanged — see backlog README)

### b) Implement
- Write the code to fix or build the item
- Run the checks: `pytest -q`, `ruff check grabbit tests`, `mypy grabbit`
- If the change touches the web UI: `cd web && npm run build`
- If it touches the Docker image, engine invocation, or startup: `docker build` and smoke-test the container (submit a URL, check `/api/health`)

### c) Commit
- Stage only the files you changed (no `git add -A`)
- Update `CHANGELOG.md` under an `## [Unreleased]` section (create it above the latest release if missing)
- Clear commit message explaining what and why
- Include `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

### d) Push and verify CI
- `git push origin main`
- Wait for the CI workflow on main to pass (`gh run list --repo plattze/grabbit --workflow ci.yml --branch main`). Diagnose and fix failures before moving on.

### e) Release the item as its own version
Every item ships as its own release — one item = one version (e.g. one feature = `0.2.1`, the next = `0.2.2`).
- Pick the next version: bump **patch** for a feature or defect; bump minor only if the maintainer has said this item is a milestone-level release.
- Stamp `CHANGELOG.md`: rename `## [Unreleased]` to the version + today's date.
- Bump the version in `pyproject.toml` and `grabbit/__init__.py`.
- Commit the release (`Release vX.Y.Z`), tag `vX.Y.Z`, and push commit + tag: `git push origin main vX.Y.Z`.
- Wait for the release workflow to succeed (`gh run list --repo plattze/grabbit --workflow release.yml`) — it publishes the GHCR Docker image. Diagnose and fix failures before moving on.

### f) Complete the backlog item
- Append a `## Shipped` line (date — version tag + commit, one-line summary) to the item file
- Move it from `in-progress/` to `completed/` (`mv`, otherwise unchanged)

### g) Move to the next item
- Go back to step 3 with the next item in priority order until none remain

## Important rules

- **Never batch**: one item = one full implement → check → commit → push → CI-green → release → backlog-move cycle. Never roll two items into one version.
- **Stop on failure**: if tests or CI fail, fix before moving on. Do not skip items.
- **Respect project scope**: no site extractors, no vendored gallery-dl (see CONTRIBUTING.md). If an item would violate that, flag it to the maintainer instead of building it.
- **Releases are automatic per item**: tagging and publishing happen as part of each item's cycle — no need to ask first. The GHCR image stays private until public launch; publishing a private image is expected during active dev.
