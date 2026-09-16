from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.common.file_utils import save_json  # noqa: E402
from backend.services.data.raw_games import RAW_TOURNAMENTS_DIR, load_raw_games_by_id  # noqa: E402
from backend.services.data.vod_pick_order_suggestions import (  # noqa: E402
    HERO_ICON_DIR,
    HERO_REFERENCE_GALLERY_DIR,
    extract_revealed_slot_observations,
    extract_hero_identity_observations,
    find_manifest_entry,
    load_vod_manifest,
    stable_hero_events,
    suggest_pick_order_from_hero_events,
    suggest_pick_order_from_slot_reveals,
)
from backend.services.data.vod_layouts import LAYOUTS_PATH, load_layout  # noqa: E402
from backend.services.vod_downloader import process_vod  # noqa: E402


def _safe_path_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Suggest pick-order annotations from VOD slot reveal timing."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="Path to a VOD manifest JSON file.")
    parser.add_argument("--game-id", required=True, help="Raw game_id to process from the manifest.")
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_TOURNAMENTS_DIR,
        help="Directory containing raw tournament JSON files.",
    )
    parser.add_argument(
        "--layouts",
        type=Path,
        default=LAYOUTS_PATH,
        help="Path to layout definitions.",
    )
    parser.add_argument(
        "--frames-dir",
        type=Path,
        default=None,
        help="Optional pre-extracted frame directory. Skips VOD streaming when provided.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional suggestion output path. Without this, JSON is printed to stdout.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.8,
        help="Minimum reveal confidence to include in the suggestion.",
    )
    parser.add_argument(
        "--reveal-threshold",
        type=float,
        default=25.0,
        help="Mean pixel-difference threshold for detecting slot reveals.",
    )
    parser.add_argument(
        "--mode",
        choices=("hero", "slot-reveal"),
        default="hero",
        help="Hero identity is the swap-safe default; slot-reveal preserves the v1 prototype.",
    )
    parser.add_argument("--hero-icon-dir", type=Path, default=HERO_ICON_DIR)
    parser.add_argument("--hero-gallery-dir", type=Path, default=HERO_REFERENCE_GALLERY_DIR)
    args = parser.parse_args()

    manifest = load_vod_manifest(args.manifest)
    entry = find_manifest_entry(manifest, args.game_id)
    if entry.get("status") not in (None, "matched"):
        raise ValueError(
            f"Manifest entry {args.game_id} cannot be processed with status {entry.get('status')}"
        )
    raw_games = load_raw_games_by_id(args.raw_dir)
    if args.game_id not in raw_games:
        raise KeyError(f"No raw game found for {args.game_id}")

    layout_id = str(entry["layout_id"])
    layout = load_layout(layout_id, args.layouts)

    frames_dir = args.frames_dir
    if frames_dir is None:
        match_id = _safe_path_segment(args.game_id)
        frames_dir = Path(
            process_vod(
                url=str(entry["vod_url"]),
                match_id=match_id,
                start_sec=float(
                    entry.get("detected_start_sec", entry.get("draft_start_sec", 180))
                ),
                duration=float(entry.get("duration_sec", 240)),
                fps=float(entry.get("fps", 1)),
            )
        )

    frame_paths = sorted(frames_dir.glob("*.jpg"))
    provenance = {
        key: entry.get(key)
        for key in (
            "channel_id",
            "video_id",
            "source_kind",
            "match_confidence",
            "layout_id",
            "detected_start_sec",
            "detected_end_sec",
        )
        if entry.get(key) is not None
    }
    if args.mode == "slot-reveal":
        observations = extract_revealed_slot_observations(
            frame_paths=frame_paths,
            layout_slots=layout["slots"],
            fps=float(entry.get("fps", 1)),
            reveal_threshold=args.reveal_threshold,
        )
        suggestion = suggest_pick_order_from_slot_reveals(
            raw_game=raw_games[args.game_id],
            slot_reveals=observations,
            min_confidence=args.min_confidence,
        )
        suggestion["provenance"] = provenance
    else:
        observations = extract_hero_identity_observations(
            frame_paths=frame_paths,
            layout=layout,
            raw_game=raw_games[args.game_id],
            fps=float(entry.get("fps", 1)),
            start_sec=float(
                entry.get("detected_start_sec", entry.get("draft_start_sec", 0))
            ),
            hero_icon_dir=args.hero_icon_dir,
            gallery_dir=args.hero_gallery_dir,
        )
        events = stable_hero_events(observations)
        suggestion = suggest_pick_order_from_hero_events(
            raw_game=raw_games[args.game_id],
            hero_events=events,
            provenance=provenance,
        )

    payload = {"version": 1, "games": [suggestion]}
    if args.output is None:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        save_json(args.output, payload)
        print(f"Wrote VOD pick-order suggestion to {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
