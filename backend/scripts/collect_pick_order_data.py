"""Fetch explicit tournament snapshots and resolve LP VODs; no review web page.

Example: python backend/scripts/collect_pick_order_data.py --tournament
M7_World_Championship/Knockout_Stage --tournament MPL/Indonesia/Season_17/Regular_Season
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.common.parser import pagename_to_filename  # noqa: E402
from backend.services.liquipedia.liquipedia_api import fetch_table  # noqa: E402
from backend.services.liquipedia.match_finder import parse_and_normalize_matches  # noqa: E402
from backend.services.data.raw_games import load_raw_games  # noqa: E402
from backend.services.data.collection import select_collection_games, collection_report  # noqa: E402
from backend.services.data.vod_sources import discover_vod_manifest, load_vod_sources  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tournament",
        action="append",
        required=True,
        help="Exact Liquipedia pagename; repeat for each tournament.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "backend/data/raw/pick_order_suggestions/lp_collection",
    )
    parser.add_argument("--matches-per-tournament", type=int, default=5)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Replace only this run directory's cached LP snapshots.",
    )
    parser.add_argument("--youtube-fallback", action="store_true")
    parser.add_argument(
        "--verify-media",
        action="store_true",
        help="Check video metadata/uploader using yt-dlp, without downloading videos or calling the YouTube Data API.",
    )
    parser.add_argument(
        "--process",
        action="store_true",
        help="Run existing video extraction after collection.",
    )
    parser.add_argument(
        "--layouts", type=Path, default=ROOT / "backend/data/layouts.json"
    )
    parser.add_argument(
        "--require-orders",
        action="store_true",
        help="Fail acceptance unless every selected game has a complete evidenced order.",
    )
    args = parser.parse_args()
    if args.matches_per_tournament < 1:
        parser.error("--matches-per-tournament must be positive")
    load_dotenv(ROOT / "backend/.env")
    raw_dir = args.output_dir / "raw"
    for page in dict.fromkeys(args.tournament):
        if not page.strip() or any(c in page for c in '[]\n\r\\:*?"<>|'):
            parser.error("Invalid tournament pagename")
        snapshot = (
            args.output_dir
            / "snapshots"
            / f"{hashlib.sha256(page.encode()).hexdigest()[:16]}.json"
        )
        if snapshot.exists() and not args.refresh:
            payload = load_json(snapshot)
        else:
            key = os.getenv("LIQUIPEDIA_API_KEY")
            if not key:
                parser.error("LIQUIPEDIA_API_KEY is required for uncached snapshots")
            payload = fetch_table(
                key, "match", "mobilelegends", f"[[pagename::{page}]]", limit=1000
            )
            if (
                not isinstance(payload.get("result"), list)
                or len(payload["result"]) >= 1000
            ):
                raise ValueError(
                    "Invalid or potentially truncated LP response; refusing incomplete snapshot"
                )
            save_json(snapshot, payload)
        normalized = parse_and_normalize_matches(payload)
        normalized["pagename"] = page
        save_json(raw_dir / f"{pagename_to_filename(page)}_games.json", normalized)
        print(
            f"{page}: {sum(len(s['games']) for s in normalized['series'])} normalized games",
            flush=True,
        )
    load_raw_games.cache_clear()
    games = [g for g in load_raw_games(raw_dir) if g["pagename"] in args.tournament]
    registry = load_vod_sources(ROOT / "backend/data/vod_sources.json")
    manifest = discover_vod_manifest(
        games=games,
        source_registry=registry,
        youtube_fallback=args.youtube_fallback,
        since_days=365,
    )
    save_json(args.output_dir / "all_manifest.json", manifest)
    selected = select_collection_games(games, manifest, args.matches_per_tournament)
    ids = {g["game_id"] for g in selected}
    selected_manifest = {
        **manifest,
        "games": [e for e in manifest["games"] if e["game_id"] in ids],
    }
    if args.verify_media:
        from backend.services.data.liquipedia_vods import verify_manifest_media

        selected_manifest = verify_manifest_media(selected_manifest, registry)
    manifest_path = args.output_dir / "manifest.json"
    save_json(manifest_path, selected_manifest)
    review_path = args.output_dir / "suggestions.json"
    if args.process:
        from backend.services.data.vod_pipeline import run_vod_pipeline

        run_vod_pipeline(
            manifest_path=manifest_path,
            raw_dir=raw_dir,
            layouts_path=args.layouts,
            results_dir=args.output_dir / "jobs",
            review_path=review_path,
            ledger_path=args.output_dir / "runtime/jobs.sqlite3",
        )
    annotations = load_json(review_path) if review_path.exists() else {}
    report = collection_report(selected, selected_manifest, annotations)
    report["verified_vods"] = sum(
        e.get("availability") == "metadata_verified" for e in selected_manifest["games"]
    )
    required = args.matches_per_tournament * len(set(args.tournament))
    report["retrieval_target_met"] = report["distinct_matches"] >= required and len(
        report["tournaments"]
    ) == len(set(args.tournament))
    report["order_target_met"] = report["retrieval_target_met"] and report[
        "complete_pick_orders"
    ] == len(selected)
    save_json(args.output_dir / "report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "games"}, indent=2))
    return (
        0
        if report["order_target_met" if args.require_orders else "retrieval_target_met"]
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
