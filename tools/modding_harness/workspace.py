"""Neutral workspace configuration; no named coding-agent directories required."""
from __future__ import annotations

import json
from pathlib import Path

from .preservation import DEFAULT_RESERVE, SaveStore


def settings(workspace):
    root = Path(workspace).resolve()
    path = root / ".modding-agent-harness/config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict) or set(data) - {"v", "save_store", "save_reserve_bytes"} or data.get("v", 1) != 1:
        raise ValueError("unsupported neutral harness configuration")
    destination = data.get("save_store")
    if destination is not None and (not isinstance(destination, str) or not destination.strip()):
        raise ValueError("save_store must be a nonempty path")
    reserve = data.get("save_reserve_bytes", DEFAULT_RESERVE)
    if type(reserve) is not int or reserve < 0:
        raise ValueError("save_reserve_bytes must be a nonnegative integer")
    destination = root / destination if destination else root / ".modding-agent-harness/backups/saves"
    return {"destination": destination.resolve(), "reserve": reserve}


def save_store(workspace, profile):
    config = settings(workspace)
    return SaveStore(workspace, config["destination"], profile.save_roots, config["reserve"])


def summaries(store):
    """List durable records without claiming their blobs have just been verified."""
    rows, unreadable = [], []
    for path in sorted((store.root / "transactions").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="ascii"))
            if (data.get("v") != 1 or data.get("id") != path.stem or data.get("complete") is not True
                    or not isinstance(data.get("groups"), list) or not data["groups"]):
                raise ValueError("incomplete transaction")
            rows.append(data)
        except (OSError, ValueError, AttributeError):
            unreadable.append(path.name)
    return sorted(rows, key=lambda row: (row.get("utc", ""), row["id"])), unreadable
