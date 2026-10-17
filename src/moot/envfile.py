"""Read simple KEY=VALUE settings from a `.env` file (used by the developer scripts)."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> list[str]:
    """Load KEY=VALUE lines into the environment and return the keys that were set.

    Blank lines and lines starting with # are ignored, quotes around values are removed,
    and variables that are already set in the environment are left unchanged.
    """
    if not path.exists():
        return []
    loaded = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
