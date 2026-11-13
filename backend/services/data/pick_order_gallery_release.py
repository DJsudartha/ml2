"""Content-address a local private hero gallery without accepting new crops."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.services.common.file_utils import load_json

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def gallery_release_manifest(gallery_dir: Path) -> dict:
    if not gallery_dir.is_dir():
        raise ValueError(f"Gallery directory not found: {gallery_dir}")
    files = []
    for path in sorted(gallery_dir.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_SUFFIXES:
            continue
        if path.is_symlink():
            raise ValueError("Gallery symlinks are not allowed in frozen releases")
        files.append({
            "path": path.relative_to(gallery_dir).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        })
    if not files:
        raise ValueError("Gallery release needs at least one image")
    release_id = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"version": 1, "release_id": release_id, "files": files}


def validate_gallery_release(gallery_dir: Path, manifest_path: Path | None) -> dict:
    if manifest_path is None or not manifest_path.is_file():
        raise ValueError("Frozen gallery manifest is missing")
    expected = load_json(manifest_path)
    actual = gallery_release_manifest(gallery_dir)
    if expected != actual:
        raise ValueError("Gallery changed since frozen release; holdout scoring is blocked")
    return actual
