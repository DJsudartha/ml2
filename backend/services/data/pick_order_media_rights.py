"""Explicit local guard for VOD use; this is not a legal determination."""

from __future__ import annotations

from pathlib import Path

from backend.services.common.file_utils import load_json


def require_media_rights(path: Path | None, channel_ids: set[str]) -> dict:
    if path is None or not path.is_file():
        raise ValueError("Confirmed media rights file is required for new YouTube capture")
    payload = load_json(path)
    if not isinstance(payload, dict) or not payload.get("rights_confirmed"):
        raise ValueError("Media rights confirmation is missing")
    basis = str(payload.get("basis", "")).strip()
    if len(basis) < 20 or basis.casefold() in {
        "private use", "delete frames", "noncommercial use"
    }:
        raise ValueError("Media rights basis must describe actual authorization")
    if not channel_ids <= set(payload.get("allowed_channel_ids", [])):
        raise ValueError("Media rights do not cover all selected channels")
    if not payload.get("extract_review_crops"):
        raise ValueError("Media rights do not cover review-crop retention")
    return payload
