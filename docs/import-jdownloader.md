# Importing a JDownloader queue

`grabbit-import-jd` migrates a JDownloader download list into Grabbit. It
parses JDownloader's saved queue and submits every link through the normal
REST validation path — unsupported hosts are rejected per-URL with a reason,
and each JDownloader **package becomes a dest sub-folder**.

## Getting the queue file

JDownloader persists its queue as `downloadList*.zip` under the `cfg/`
directory of its installation (pick the newest one; JDownloader keeps a few
rotating copies).

## Usage

```bash
# See what would be imported (no server needed)
grabbit-import-jd downloadList1737436800000.zip --dry-run

# Import for real
grabbit-import-jd downloadList1737436800000.zip \
    --url http://localhost:8080 \
    --api-key grb_... \
    --dest-prefix jdownloader     # optional parent folder
```

`--url` and `--api-key` can also come from `GRABBIT_URL` / `GRABBIT_API_KEY`.
The key needs the `submit` scope.

## Notes

- Links are deduplicated within a package, and Grabbit itself accepts
  duplicate open URLs idempotently — re-running an interrupted import is safe.
- Submissions go in batches of 100 (the API maximum). The per-key submit rate
  limit (30 requests/min) is honored automatically: on 429 the importer waits
  and retries, so very large queues take a while — leave it running.
- Rejected URLs are listed at the end with reasons (e.g. a host gallery-dl has
  no extractor for). Nothing is retried automatically; handle those manually.
- Package names are sanitized into safe relative folder names.
