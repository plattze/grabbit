"""JDownloader queue importer: downloadList*.zip → Grabbit.

Parses a JDownloader saved download list (`cfg/downloadList*.zip`) and submits
every link through a running Grabbit instance's REST API — the normal
validation path, so unsupported hosts get per-URL rejection reasons exactly as
if submitted by hand. Each JDownloader package becomes a dest sub-folder.

Usage:
    grabbit-import-jd downloadList1737436800000.zip \\
        --url http://localhost:8080 --api-key grb_... [--dest-prefix jd] [--dry-run]

Stdlib-only; submit rate limits (429) are honored by waiting and retrying,
so importing a large queue may take a while.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# DownloadLinkStorable URL fields, most user-meaningful first.
_URL_KEYS = ("contentUrl", "browserUrl", "originUrl", "url")

Submit = Callable[[list[str], str], list[dict]]


@dataclass
class Package:
    name: str
    urls: list[str]


def _safe_dest(name: str) -> str:
    """Package name → safe relative sub-path (the API rejects '..' and '/...')."""
    name = name.strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name)
    name = name.replace("..", "_")
    name = name.strip(". ")
    return name or "jdownloader-import"


def parse_download_list(path: Path) -> list[Package]:
    """Parse a downloadList*.zip into packages with their link URLs.

    Zip layout: entry `NN` is a package JSON (FilePackageStorable), entry
    `NN_MM` is a link JSON (DownloadLinkStorable) belonging to package NN.
    Unknown entries (e.g. `extraInfo`) are ignored.
    """
    packages: dict[str, dict] = {}
    links: dict[str, list[str]] = defaultdict(list)
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            entry = info.filename
            if not entry or not entry[0].isdigit():
                continue
            try:
                data = json.loads(zf.read(info))
            except (ValueError, UnicodeDecodeError):
                continue
            if "_" in entry:
                for key in _URL_KEYS:
                    url = data.get(key)
                    if isinstance(url, str) and url.startswith(("http://", "https://")):
                        links[entry.split("_", 1)[0]].append(url)
                        break
            elif isinstance(data, dict):
                packages[entry] = data

    result: list[Package] = []
    for idx in sorted(packages, key=int):
        urls = list(dict.fromkeys(links.get(idx, [])))  # dedupe, keep order
        if not urls:
            continue
        name = packages[idx].get("name") or f"package-{idx}"
        result.append(Package(name=_safe_dest(str(name)), urls=urls))
    return result


def import_packages(packages: list[Package], submit: Submit, dest_prefix: str = "",
                    batch_size: int = 100, log: Callable[[str], None] = print,
                    ) -> tuple[int, int, list[dict]]:
    """Submit all package URLs in batches; returns (accepted, rejected, rejections)."""
    accepted = rejected = 0
    rejections: list[dict] = []
    total = sum(len(p.urls) for p in packages)
    done = 0
    for pkg in packages:
        dest = f"{dest_prefix}/{pkg.name}" if dest_prefix else pkg.name
        for i in range(0, len(pkg.urls), batch_size):
            chunk = pkg.urls[i:i + batch_size]
            for result in submit(chunk, dest):
                if result.get("accepted"):
                    accepted += 1
                else:
                    rejected += 1
                    rejections.append(result)
            done += len(chunk)
            log(f"[{done}/{total}] {pkg.name}: "
                f"{accepted} accepted, {rejected} rejected so far")
    return accepted, rejected, rejections


def _http_submit(base_url: str, api_key: str) -> Submit:
    def submit(urls: list[str], dest: str) -> list[dict]:
        body = json.dumps({"urls": urls, "dest": dest}).encode()
        while True:
            req = urllib.request.Request(
                f"{base_url}/api/downloads", data=body, method="POST",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = int(e.headers.get("Retry-After") or 10)
                    print(f"rate limited; waiting {wait}s ...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                detail = e.read().decode(errors="replace")[:200]
                raise SystemExit(f"Grabbit API error {e.code}: {detail}") from e
            except urllib.error.URLError as e:
                raise SystemExit(f"cannot reach Grabbit at {base_url}: {e.reason}") from e
    return submit


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="grabbit-import-jd",
        description="Import a JDownloader downloadList*.zip into Grabbit.")
    parser.add_argument("zip_path", type=Path, help="path to downloadList*.zip")
    parser.add_argument("--url", default=os.environ.get("GRABBIT_URL", "http://localhost:8080"),
                        help="Grabbit base URL (env: GRABBIT_URL)")
    parser.add_argument("--api-key", default=os.environ.get("GRABBIT_API_KEY", ""),
                        help="submit-scoped API key (env: GRABBIT_API_KEY)")
    parser.add_argument("--dest-prefix", default="",
                        help="parent sub-folder for all imported packages")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse and print what would be submitted, submit nothing")
    args = parser.parse_args(argv)

    if not args.zip_path.is_file():
        raise SystemExit(f"not a file: {args.zip_path}")
    packages = parse_download_list(args.zip_path)
    total = sum(len(p.urls) for p in packages)
    print(f"parsed {len(packages)} package(s), {total} link(s)")

    if args.dry_run:
        for pkg in packages:
            print(f"  {pkg.name}/ ({len(pkg.urls)} links)")
            for url in pkg.urls[:3]:
                print(f"    {url}")
            if len(pkg.urls) > 3:
                print(f"    ... and {len(pkg.urls) - 3} more")
        return

    if not args.api_key:
        raise SystemExit("--api-key (or GRABBIT_API_KEY) is required")
    submit = _http_submit(args.url.rstrip("/"), args.api_key)
    accepted, rejected, rejections = import_packages(
        packages, submit, dest_prefix=args.dest_prefix)

    print(f"\ndone: {accepted} accepted, {rejected} rejected")
    if rejections:
        print("rejected URLs:")
        for r in rejections:
            print(f"  {r.get('url')}  ({r.get('reason')})")


if __name__ == "__main__":
    main()
