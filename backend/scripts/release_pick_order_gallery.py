"""Freeze an authorized gallery into a portable, local-only private ZIP archive."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.data.pick_order_gallery_release import (  # noqa: E402
    gallery_release_manifest,
)
from backend.services.data.pick_order_media_rights import require_media_rights  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gallery-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--media-rights-file", type=Path)
    args = parser.parse_args()
    sources = load_json(ROOT / "backend/data/vod_sources.json")["sources"]
    try:
        rights = require_media_rights(
            args.media_rights_file, {source["channel_id"] for source in sources}
        )
        if not rights.get("freeze_private_gallery"):
            raise ValueError("Media rights do not cover a private gallery release")
        manifest = gallery_release_manifest(args.gallery_dir)
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    if args.manifest_output.exists() and load_json(args.manifest_output) != manifest:
        parser.error("Existing gallery release is immutable")
    if args.archive.exists():
        if args.manifest_output.is_file() and load_json(args.manifest_output) == manifest:
            print(manifest["release_id"])
            return 0
        parser.error("Existing archive cannot be overwritten")
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.archive.with_name(args.archive.name + ".partial")
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(
                "gallery_manifest.json", json.dumps(manifest, sort_keys=True, indent=2)
            )
            for item in manifest["files"]:
                archive.write(args.gallery_dir / item["path"],
                              f"gallery/{item['path']}")
        temporary.replace(args.archive)
    finally:
        temporary.unlink(missing_ok=True)
    save_json(args.manifest_output, manifest)
    print(manifest["release_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
