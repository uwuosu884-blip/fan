#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cut the GitHub release for omen88d2.

Steps: build omen88d2-<ver>.tar.gz deterministically from the git tag
(git archive + gzip -n), create the GitHub release with the notes file as
the body, upload the tarball as the release asset.

Needs: git, gzip, and a token (fine-grained PAT with Contents:write on
the repo). The token is only needed for the API calls, not for --dry-run.

Usage:
  python3 mk_release.py --dry-run            # build tarball, validate only
  GITHUB_TOKEN=... python3 mk_release.py     # build + create release + upload
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

VERSION = "0.1.0"  # bump per release (tag v<VERSION>, notes, tarball)
OWNER_REPO = "uwuosu884-blip/fan"
TAG = f"v{VERSION}"
TITLE = f"omen88d2 v{VERSION}"
HERE = os.path.dirname(os.path.abspath(__file__))
NOTES = os.path.join(HERE, f"RELEASE_NOTES_v{VERSION}.md")
ASSET = os.path.join(HERE, f"omen88d2-{VERSION}.tar.gz")
API = "https://api.github.com"


def body_text() -> str:
    with open(NOTES) as f:
        lines = f.read().splitlines()
    if lines and lines[0].startswith("# "):  # title already in release name
        lines = lines[1:]
    return "\n".join(lines).strip() + "\n"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_tarball() -> str:
    """(Re)build the release tarball deterministically from the tag."""
    if not os.path.isdir(os.path.join(HERE, ".git")):
        raise SystemExit(f"not a git checkout: {HERE}")
    p1 = subprocess.run(
        ["git", "-C", HERE, "archive", "--format=tar",
         "--prefix=omen88d2/", TAG],
        capture_output=True)
    if p1.returncode != 0:
        raise SystemExit(f"git archive {TAG} failed: {p1.stderr.decode()[:300]}")
    p2 = subprocess.run(["gzip", "-n"], input=p1.stdout, capture_output=True)
    if p2.returncode != 0:
        raise SystemExit("gzip -n failed (is gzip installed?)")
    with open(ASSET, "wb") as f:
        f.write(p2.stdout)
    digest = sha256_of(ASSET)
    print(f"built {ASSET} ({os.path.getsize(ASSET)} bytes)\nsha256: {digest}")
    return digest


def api(method: str, url: str, token: str, data=None,
        ctype: str = "application/json"):
    r = urllib.request.Request(
        url, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    body = None
    if data is not None:
        r.add_header("Content-Type", ctype)
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
    try:
        with urllib.request.urlopen(r, data=body, timeout=60) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode()[:600]
        except Exception:
            detail = ""
        raise SystemExit(f"API {method} {url} -> HTTP {e.code}: {detail}")


def main() -> None:
    dry = "--dry-run" in sys.argv
    if not os.path.isfile(NOTES):
        raise SystemExit(f"notes missing: {NOTES}")
    payload = {"tag_name": TAG, "name": TITLE, "body": body_text(),
               "draft": False, "prerelease": False}
    build_tarball()
    print(f"payload body: {len(payload['body'])} chars")
    if dry:
        print("dry-run OK (no API calls made)")
        return
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise SystemExit("set the GITHUB_TOKEN env var")
    try:
        _, rel = api("POST", f"{API}/repos/{OWNER_REPO}/releases", token, payload)
        print("release created")
    except SystemExit as e:
        if "already_exists" in str(e):
            _, rel = api("GET", f"{API}/repos/{OWNER_REPO}/releases/tags/{TAG}", token)
            print("release already existed, reusing it")
        else:
            raise
    print("release:", rel.get("html_url"))
    upload_url = rel["upload_url"].split("{")[0]
    with open(ASSET, "rb") as f:
        asset_data = f.read()
    try:
        _, asset = api("POST", upload_url + "?name=" + os.path.basename(ASSET),
                       token, asset_data, "application/gzip")
        print("asset uploaded:", asset.get("browser_download_url"))
    except SystemExit as e:
        if "already_exists" in str(e):
            print("asset already attached, skipping upload")
        else:
            raise


if __name__ == "__main__":
    main()
