#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create the GitHub release for omen88d2 v0.1.0 and upload the tarball.

Usage:
  GITHUB_TOKEN=<fine-grained PAT, Contents:write on uwuosu884-blip/fan> \\
    python3 mk_release.py [--dry-run]

The tarball's sha256 is computed at publish time and substituted into the
SHA256_PLACEHOLDER in the release notes (avoids a circular reference, since
the notes ship inside the tarball).
"""
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

OWNER_REPO = "uwuosu884-blip/fan"
TAG = "v0.1.0"
TITLE = "omen88d2 v0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
NOTES = os.path.join(HERE, "RELEASE_NOTES_v0.1.0.md")
ASSET = os.path.join(HERE, "omen88d2-0.1.0.tar.gz")
API = "https://api.github.com"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def body_text(asset_sha: str) -> str:
    with open(NOTES) as f:
        lines = f.read().splitlines()
    if lines and lines[0].startswith("# "):  # title already in release name
        lines = lines[1:]
    text = "\n".join(lines).strip() + "\n"
    return text.replace("SHA256_PLACEHOLDER", asset_sha)


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
    if not os.path.isfile(ASSET):
        raise SystemExit(f"asset missing: {ASSET}")
    asset_sha = sha256_of(ASSET)
    payload = {"tag_name": TAG, "name": TITLE, "body": body_text(asset_sha),
               "draft": False, "prerelease": False}
    print(f"asset: {ASSET} ({os.path.getsize(ASSET)} bytes)")
    print(f"sha256: {asset_sha}")
    if "SHA256_PLACEHOLDER" in payload["body"]:
        raise SystemExit("placeholder substitution failed")
    if dry:
        print("dry-run OK")
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
