"""Print one image tag per line for each service in versions.yml.

Used by `make clean-images` to compute the exact ghcr.io tags we
should remove from the local Docker daemon. Same OWNER resolution as
render_env.py: env var wins, manifest's `owner:` field is the
fallback.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "versions.yml"


def main() -> int:
    raw = yaml.safe_load(MANIFEST.read_text())
    owner = os.environ.get("OWNER") or cast("str | None", raw.get("owner"))
    if not owner:
        print(
            "error: no OWNER env var and no `owner:` field in versions.yml.",
            file=sys.stderr,
        )
        return 2
    owner = owner.lower()
    services = cast(dict[str, str], raw.get("services", {}))
    for service, version in services.items():
        print(f"ghcr.io/{owner}/{service}:{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
