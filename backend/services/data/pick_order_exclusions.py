"""Versioned development-match exclusions for blind pick-order holdouts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from backend.services.common.file_utils import load_json


EXCLUSION_REASONS = {
    "calibration",
    "development",
    "gallery_source",
    "retired_holdout",
    "smoke_test",
}


def validate_exclusion_registry(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("Exclusion registry must be a version 1 object")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Exclusion registry must contain entries")

    normalized = []
    seen_games = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Exclusion registry entries must be objects")
        required = (
            "game_id",
            "liquipedia_match_id",
            "layout_id",
            "reason",
            "source",
        )
        if any(not isinstance(entry.get(field), str) or not entry[field] for field in required):
            raise ValueError("Exclusion registry entry fields must be non-empty strings")
        if entry["reason"] not in EXCLUSION_REASONS:
            raise ValueError(f"Unsupported exclusion reason: {entry['reason']}")
        if entry["game_id"] in seen_games:
            raise ValueError(f"Duplicate exclusion game ID: {entry['game_id']}")
        seen_games.add(entry["game_id"])
        normalized.append({field: entry[field] for field in required})

    normalized.sort(key=lambda row: row["game_id"])
    canonical = {"version": 1, "entries": normalized}
    canonical["exclusion_registry_id"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return canonical


def load_exclusion_registry(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Missing exclusion registry: {path}")
    return validate_exclusion_registry(load_json(path))
