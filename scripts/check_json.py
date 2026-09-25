#!/usr/bin/env python3
"""Parse every repository JSON file to catch malformed schemas/workflows early."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {".git", "node_modules", ".venv", "models", "outputs", "renders"}


def main() -> int:
    errors: list[str] = []
    checked = 0

    for path in ROOT.rglob("*.json"):
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        checked += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{path.relative_to(ROOT)}: {exc}")

    if errors:
        print("Invalid JSON:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"OK: parsed {checked} JSON file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
