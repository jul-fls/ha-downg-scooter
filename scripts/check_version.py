"""Validate the manifest version and, for releases, its matching tag."""

import json
import os
from pathlib import Path
import re


MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "downg_scooter"
    / "manifest.json"
)
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def check_version(tag: str = "") -> str:
    """Return a valid manifest version and verify an optional release tag."""
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Invalid manifest version: {version!r}")
    if tag and tag != f"v{version}":
        raise ValueError(f"Tag {tag!r} does not match manifest version v{version}")
    return version


if __name__ == "__main__":
    print(f"DownG Scooter v{check_version(os.environ.get('RELEASE_TAG', ''))}")
