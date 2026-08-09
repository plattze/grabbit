Show the current backlog.

The backlog is file-per-item under `/home/ubuntu/.claude/projects/-code-grabbit/memory/backlog/` (see its `README.md`): `new/` = not started, `in-progress/` = being worked, `completed/` = shipped.

1. Read every item in `new/` and `in-progress/` (skip `completed/` unless asked).
2. Also list the M5 backlog from `docs/SPEC.md` §12 (MCP server, JDownloader importer, CyberdropDLEngine) and open GitHub issues (`gh issue list --repo plattze/grabbit`), noting any that overlap with backlog items.
3. Present: **in-progress** first, then **new** (defects before features), then spec/GitHub extras — with item number, title, and one-line problem for each.
