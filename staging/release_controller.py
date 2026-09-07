#!/usr/bin/env python3
"""Small, testable helpers for the ephemeral staging deployment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

REPOSITORY = "ghcr.io/nvx-11/release-reliability-lab"
SOURCE = "https://github.com/NVX-11/release-reliability-lab"
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")


def normalize_image(value: str) -> str:
    """Accept only this project's complete immutable GHCR reference or digest."""
    digest = value
    prefix = f"{REPOSITORY}@"
    if value.startswith(prefix):
        digest = value[len(prefix) :]
    if not DIGEST_RE.fullmatch(digest):
        raise ValueError(
            f"image must be {REPOSITORY}@sha256:<64 lowercase hex characters> "
            "or the complete sha256 digest"
        )
    return f"{REPOSITORY}@{digest}"


def verify_metadata(document: dict[str, object], expected_ref: str) -> dict[str, str]:
    """Validate identity and required OCI labels from docker inspect JSON."""
    expected_ref = normalize_image(expected_ref)
    repo_digests = document.get("RepoDigests") or []
    labels = (document.get("Config") or {}).get("Labels") or {}  # type: ignore[union-attr]
    source = labels.get("org.opencontainers.image.source")
    revision = labels.get("org.opencontainers.image.revision")
    version = labels.get("org.opencontainers.image.version")
    if expected_ref not in repo_digests:
        raise ValueError("local image identity does not contain the requested digest")
    if source != SOURCE:
        raise ValueError(f"unexpected OCI source label: {source!r}")
    if not isinstance(revision, str) or not REVISION_RE.fullmatch(revision):
        raise ValueError("OCI revision must be a complete lowercase Git commit")
    if not isinstance(version, str) or not version or version == "unknown":
        raise ValueError("OCI application version is missing")
    return {"source": source, "revision": revision, "version": version}


def render_nginx(backend: str, digest: str, revision: str) -> str:
    if backend not in {"active", "candidate"}:
        raise ValueError("backend must be active or candidate")
    normalized = normalize_image(digest)
    digest = normalized.rsplit("@", 1)[1]
    if not REVISION_RE.fullmatch(revision):
        raise ValueError("release header revision must be a validated full Git commit")
    return f"""user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /var/run/nginx.pid;
events {{ worker_connections 128; }}
http {{
  access_log /var/log/nginx/access.log;
  upstream selected_release {{ server {backend}:8000; }}
  server {{
    listen 8080;
    add_header X-Release-Digest "{digest}" always;
    add_header X-Release-Revision "{revision}" always;
    location / {{
      proxy_hide_header X-Release-Digest;
      proxy_hide_header X-Release-Revision;
      proxy_pass http://selected_release;
      proxy_connect_timeout 2s;
      proxy_read_timeout 5s;
    }}
  }}
}}
"""


def verify_http(
    url: str,
    endpoint: str,
    expected_version: str | None = None,
    expected_digest: str | None = None,
    expected_revision: str | None = None,
) -> None:
    with urlopen(f"{url.rstrip('/')}{endpoint}", timeout=2) as response:
        if response.status != 200:
            raise ValueError(f"{endpoint} returned HTTP {response.status}")
        body = json.load(response)
        actual_digest = response.headers.get("X-Release-Digest")
        actual_revision = response.headers.get("X-Release-Revision")
    expected = {"status": "healthy"} if endpoint == "/health" else {"version": expected_version}
    if body != expected:
        raise ValueError(f"{endpoint} returned {body!r}, expected {expected!r}")
    if expected_digest is not None:
        normalized = normalize_image(expected_digest)
        if actual_digest != normalized.rsplit("@", 1)[1]:
            raise ValueError(f"stable route release digest was {actual_digest!r}")
    if expected_revision is not None:
        if not REVISION_RE.fullmatch(expected_revision) or actual_revision != expected_revision:
            raise ValueError(f"stable route release revision was {actual_revision!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    normalize = commands.add_parser("normalize")
    normalize.add_argument("image")
    metadata = commands.add_parser("metadata")
    metadata.add_argument("image")
    metadata.add_argument("inspect_json", type=Path)
    render = commands.add_parser("render")
    render.add_argument("backend", choices=("active", "candidate"))
    render.add_argument("digest")
    render.add_argument("revision")
    render.add_argument("output", type=Path)
    http = commands.add_parser("http")
    http.add_argument("url")
    http.add_argument("endpoint", choices=("/health", "/version"))
    http.add_argument("--version")
    http.add_argument("--digest")
    http.add_argument("--revision")
    args = parser.parse_args()
    try:
        if args.command == "normalize":
            print(normalize_image(args.image))
        elif args.command == "metadata":
            documents = json.loads(args.inspect_json.read_text())
            if not isinstance(documents, list) or len(documents) != 1:
                raise ValueError("docker inspect must describe exactly one image")
            result = verify_metadata(documents[0], args.image)
            print(json.dumps(result, separators=(",", ":")))
        elif args.command == "render":
            args.output.write_text(render_nginx(args.backend, args.digest, args.revision))
        else:
            verify_http(args.url, args.endpoint, args.version, args.digest, args.revision)
    except (ValueError, OSError, json.JSONDecodeError, URLError) as error:
        print(f"release controller: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
