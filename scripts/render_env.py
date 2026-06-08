"""Render a docker-compose .env file from versions.yml.

The compose file references images by ${OWNER}/${<SERVICE>_VERSION},
so before any `docker compose up` we materialise a .env that pins
those variables to whatever versions.yml says today. Keeping this in
a manifest (rather than baked into the compose) is what lets the
auto-PR-on-release flow propose a single-file diff that the human
reviews and merges.

Usage:
    OWNER=albertocasado uv run python scripts/render_env.py > .env

OWNER is required and is *not* taken from versions.yml -- it changes
when the project is forked or moves between users/orgs, and we do not
want a fork to silently keep pulling someone else's namespace.

Per-service overrides via env vars are honored, e.g.
    CATALOG_VERSION=0.2.0 uv run python scripts/render_env.py
This is what the e2e.yml workflow uses when a satellite's
repository_dispatch fires with a new version: it sets that one var
and renders, leaving the rest at the manifest's pinned values. Same
mechanism powers `workflow_dispatch` with per-service inputs.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "versions.yml"


def env_var_name(service: str) -> str:
    """Map ``sales-api`` -> ``SALES_API_VERSION`` (compose-friendly)."""
    return service.upper().replace("-", "_") + "_VERSION"


def main() -> int:
    owner = os.environ.get("OWNER")
    if not owner:
        print(
            "error: OWNER env var is required (GHCR namespace, "
            "ghcr.io/<owner>/*).",
            file=sys.stderr,
        )
        return 2

    raw = yaml.safe_load(MANIFEST.read_text())
    services = cast(dict[str, str], raw.get("services", {}))
    if not services:
        print(f"error: {MANIFEST} has no services map.", file=sys.stderr)
        return 2

    lines: list[str] = [f"OWNER={owner.lower()}"]
    for service, pinned in services.items():
        var = env_var_name(service)
        # Per-service env overrides win over the manifest; the workflow
        # uses this to inject a single newly-released version.
        value = os.environ.get(var, pinned)
        lines.append(f"{var}={value}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
