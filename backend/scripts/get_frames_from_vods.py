from __future__ import annotations

import argparse
import re
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.data.vod_pick_order_suggestions import (  # noqa: E402
    find_manifest_entry,
    load_vod_manifest,
)
from backend.services.vod_downloader import process_vod  # noqa: E402


def _safe_path_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract draft frames for a manifest game without downloading a full VOD."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="Path to a VOD manifest JSON file.")
    parser.add_argument("--game-id", required=True, help="Raw game_id to process from the manifest.")
    parser.add_argument("--max-height", type=int, default=1080)
    args = parser.parse_args()

    manifest = load_vod_manifest(args.manifest)
    entry = find_manifest_entry(manifest, args.game_id)
    frame_dir = process_vod(
        url=str(entry["vod_url"]),
        match_id=_safe_path_segment(args.game_id),
        start_sec=float(entry.get("detected_start_sec", entry.get("draft_start_sec", 180))),
        duration=float(entry.get("duration_sec", 240)),
        fps=float(entry.get("fps", 1)),
        max_height=args.max_height,
    )
    print(f"Extracted frames to {frame_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
