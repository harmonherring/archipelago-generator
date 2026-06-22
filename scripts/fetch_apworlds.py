#!/usr/bin/env python3
"""Download the apworlds listed in a manifest into a destination dir, with validation.

Used at image build time (see Dockerfile.generator) so no .apworld binaries are vendored
in the repo. Each manifest row is `name,url,sha256` (see apworlds.csv for the format).

Validation per entry: the download must be a valid zip whose filename is a lowercase
`*.apworld`; if a sha256 is given it must match; a missing `archipelago.json` inside is a
warning (it will stop working on AP 0.7+, but may still load on 0.6.x).

By default a single bad/rotted link is a warning and does not fail the build (resilient to
upstream churn) — but the run fails if NOTHING downloaded, or if --strict is passed.
Stdlib only.
"""
import argparse
import concurrent.futures
import csv
import hashlib
import io
import json
import os
import re
import sys
import urllib.request
import zipfile
from urllib.parse import urlparse

UA = "apworld-fetcher (+https://apworlds.gg)"


def read_manifest(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = next(csv.reader([line]))
            if parts[0].strip().lower() == "name":  # header
                continue
            name = parts[0].strip()
            url = parts[1].strip() if len(parts) > 1 else ""
            sha = parts[2].strip().lower() if len(parts) > 2 else ""
            if url:
                rows.append((name, url, sha))
    return rows


# matches a World subclass attribute like:  game = "Foo"  /  game: str = "Foo"
_GAME_RE = re.compile(rb'^\s{1,8}game\s*(?::[^\n=]+)?=\s*["\'](.+?)["\']', re.M)


def extract_game(zf):
    """The game's display name (what a YAML's `game:` must match). Prefer the
    archipelago.json manifest; fall back to the World.game attribute in source."""
    for n in zf.namelist():
        if n.endswith("archipelago.json"):
            try:
                data = json.loads(zf.read(n))
                if isinstance(data, dict) and data.get("game"):
                    return data["game"]
            except Exception:
                pass
    for n in zf.namelist():
        if n.endswith("__init__.py"):
            m = _GAME_RE.search(zf.read(n))
            if m:
                return m.group(1).decode("utf-8", "replace")
    return None


def fetch_one(name, url, sha, dest, timeout):
    """Return (name, status, message, fname, game). status is OK | WARN | FAIL."""
    fname = os.path.basename(urlparse(url).path)
    if not fname.lower().endswith(".apworld"):
        return (name, "FAIL", f"URL basename {fname!r} is not a .apworld", None, None)
    fname = fname.lower()  # frozen imports require lowercase names
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except Exception as e:
        return (name, "FAIL", f"download failed: {e}", None, None)
    if not data:
        return (name, "FAIL", "empty download", None, None)

    if sha:
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            return (name, "FAIL", f"sha256 mismatch (got {got})", None, None)

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return (name, "FAIL", "not a valid zip / .apworld", None, None)
    has_manifest = any(n.endswith("archipelago.json") for n in zf.namelist())
    game = extract_game(zf)

    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, fname), "wb") as out:
        out.write(data)

    size_kb = len(data) // 1024
    if not has_manifest:
        return (name, "WARN", f"{fname} ({size_kb} KB) — no archipelago.json", fname, game)
    return (name, "OK", f"{fname} ({size_kb} KB)", fname, game)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any entry fails or warns")
    args = ap.parse_args()

    rows = read_manifest(args.manifest)
    if not rows:
        print("no entries in manifest", file=sys.stderr)
        return 1
    print(f"fetching {len(rows)} apworld(s) -> {args.dest}")

    results = []
    index = {}  # game display name -> apworld filename (for per-job selection)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(fetch_one, n, u, s, args.dest, args.timeout)
                for (n, u, s) in rows]
        for fut in concurrent.futures.as_completed(futs):
            name, status, msg, fname, game = fut.result()
            print(f"  [{status:4}] {name}: {msg}")
            results.append((name, status, msg))
            if fname and game:
                index[game] = fname

    # Write the game->file index alongside the apworlds; the generator entrypoint uses it
    # to copy only the apworlds the uploaded YAMLs actually reference.
    with open(os.path.join(args.dest, "index.json"), "w") as f:
        json.dump(index, f, indent=0, sort_keys=True)
    no_game = sum(1 for _, s, _ in results if s != "FAIL") - len(index)
    print(f"index: {len(index)} games mapped ({no_game} downloaded but no game name found)")

    ok = sum(1 for _, s, _ in results if s == "OK")
    warn = sum(1 for _, s, _ in results if s == "WARN")
    fail = sum(1 for _, s, _ in results if s == "FAIL")
    print(f"\nsummary: {ok} ok, {warn} warn, {fail} fail (of {len(rows)})")

    if (ok + warn) == 0:
        print("ERROR: nothing downloaded", file=sys.stderr)
        return 1
    if args.strict and (fail or warn):
        print("ERROR: --strict and there were failures/warnings", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
