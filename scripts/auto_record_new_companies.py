"""Convenience script to run auto‑record on a JSON file.

Usage:
    python -m scripts.auto_record_new_companies <observations.json>

The JSON file must contain a list of observation objects, each with at least a
`name` field and optional `country`, `city`, `website` keys.
"""

import json
import sys
from pathlib import Path

# Adjust import path for the repo root
repo_root = Path(__file__).resolve().parents[1]
sys.path.append(str(repo_root))

from collectors.companies.store import SqliteStore
from collectors.companies.auto_record import auto_record_new_companies

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m scripts.auto_record_new_companies <observations.json>")
        sys.exit(1)
    path = Path(sys.argv[1])
    observations = json.loads(path.read_text(encoding="utf-8"))
    store = SqliteStore()
    created = auto_record_new_companies(store, observations)
    print(f"Created {len(created)} new company record(s).")
    store.close()

if __name__ == "__main__":
    main()
