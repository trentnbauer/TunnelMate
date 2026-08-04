"""Load/save the local state file that tracks resources this tool created.

The tool only ever creates, updates, or deletes Cloudflare resources it
recorded here itself -- it never touches anything it didn't create. This is
what keeps hostname removal safe (see reconcile.py).
"""

from __future__ import annotations

import json
import os

EMPTY_STATE = {"tunnel": None, "hostnames": {}}


def load(path: str) -> dict:
    if not os.path.exists(path):
        return json.loads(json.dumps(EMPTY_STATE))
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: str, state: dict) -> None:
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, path)
