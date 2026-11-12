# ::ILANG [TYPE:source-code][ROLE:Cloudflare Pages direct upload]
# ::BOUNDARY{never:upload anything outside the built site directory}
# ::RULE{credentials⇒CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from the environment only}
"""Upload the built site/ directory to a Cloudflare Pages project with no extra dependency."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
PROJECT = os.environ.get("PAGES_PROJECT", "layover-ledger")
BRANCH = os.environ.get("PAGES_BRANCH", "master")


def collect() -> tuple[dict[str, str], dict[str, bytes]]:
    manifest: dict[str, str] = {}
    blobs: dict[str, bytes] = {}
    for path in sorted(SITE.rglob("*")):
        if not path.is_file():
            continue
        relative = "/" + path.relative_to(SITE).as_posix()
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()[:32]
        manifest[relative] = digest
        blobs[digest] = content
    if "/index.html" not in manifest:
        raise SystemExit("site/index.html is missing; run build.py first")
    return manifest, blobs


def upload(token: str, account: str, manifest: dict[str, str], blobs: dict[str, bytes]) -> dict:
    boundary = "----layover" + uuid.uuid4().hex
    body = bytearray()

    def field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode())
        body.extend(b"\r\n")

    field("branch", BRANCH)
    field("manifest", json.dumps(manifest, separators=(",", ":")))
    for digest, content in blobs.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{digest}"\r\n'.encode())
        body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
           f"/pages/projects/{PROJECT}/deployments")
    request = urllib.request.Request(url, data=bytes(body), method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    })
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise SystemExit(f"upload failed: HTTP {error.code} {detail[:600]}")


def main() -> int:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    if not token or not account:
        print("Set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID first.", file=sys.stderr)
        return 1
    manifest, blobs = collect()
    result = upload(token, account, manifest, blobs)
    if not result.get("success"):
        print(json.dumps(result.get("errors", []), ensure_ascii=False), file=sys.stderr)
        return 1
    deployment = result["result"]
    print(f"uploaded {len(manifest)} files ({len(blobs)} unique)")
    print("deployment:", deployment.get("short_id") or deployment.get("id"))
    print("url:", deployment.get("url"))
    for alias in deployment.get("aliases") or []:
        print("alias:", alias)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
