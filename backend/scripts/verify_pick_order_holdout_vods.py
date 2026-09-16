"""Verify official game-level VOD metadata without downloading any media."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.data.raw_games import load_raw_games_by_id  # noqa: E402
from backend.services.data.vod_sources import (  # noqa: E402
    VideoRecord,
    load_vod_sources,
    score_video_for_game,
)


def _video_metadata(video_id):
    result = subprocess.run([
        sys.executable, "-m", "yt_dlp", "--dump-single-json", "--skip-download",
        "--no-playlist", "--no-warnings", "--socket-timeout", "15",
        "--retries", "1", f"https://www.youtube.com/watch?v={video_id}",
    ], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout)


def _published_at(metadata):
    raw = str(metadata.get("upload_date") or "")
    return (
        f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}T00:00:00Z"
        if len(raw) == 8 and raw.isdigit() else ""
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--metadata-fixture", type=Path,
        help="Offline test fixture; never counts as live VOD verification",
    )
    parser.add_argument("--max-workers", type=int, default=2)
    args = parser.parse_args()
    holdout = load_json(args.holdout)
    if not isinstance(holdout, dict) or not isinstance(holdout.get("games"), list):
        parser.error("Holdout games list is missing")
    if args.max_workers not in (1, 2):
        parser.error("Metadata verification allows one or two workers")
    registry = load_vod_sources()
    sources = {source["source_id"]: source for source in registry["sources"]}
    if args.metadata_fixture:
        if not args.metadata_fixture.is_file():
            parser.error("Metadata fixture is missing")
        fixture = load_json(args.metadata_fixture)
        if not isinstance(fixture, dict):
            parser.error("Metadata fixture must be keyed by video ID")
    else:
        fixture = None
    raw_games = load_raw_games_by_id(args.raw_dir)

    def verify(game):
        source = sources.get(game.get("source_id"))
        raw = raw_games.get(game.get("game_id"))
        reasons = []
        if source is None or raw is None:
            reasons.append("unknown_source_or_liquipedia_game")
        try:
            metadata = (
                fixture.get(game["video_id"])
                if fixture is not None else _video_metadata(game["video_id"])
            )
        except (subprocess.SubprocessError, json.JSONDecodeError):
            metadata = None
        if not isinstance(metadata, dict):
            reasons.append("video_metadata_unavailable")
            metadata = {}
        if source and metadata.get("channel_id") != source["channel_id"]:
            reasons.append("wrong_uploader")
        if metadata.get("id") != game.get("video_id"):
            reasons.append("wrong_video_id")
        candidate = None
        if source and raw and metadata:
            video = VideoRecord(
                video_id=str(metadata.get("id") or ""),
                channel_id=str(metadata.get("channel_id") or ""),
                title=str(metadata.get("title") or ""),
                description=str(metadata.get("description") or ""),
                published_at=_published_at(metadata),
                duration_sec=int(metadata.get("duration") or 0),
                actual_start_time=None,
                source_id=source["source_id"],
            )
            candidate = score_video_for_game(
                raw, video, source, registry.get("team_aliases", {})
            )
            reasons.extend(candidate["rejection_reasons"])
            if candidate["source_kind"] != "per_game":
                reasons.append("not_confidently_per_game")
            if candidate["match_confidence"] < 0.85:
                reasons.append("low_match_confidence")
        mode = "fixture" if fixture is not None else "live"
        return {
            "game_id": game["game_id"],
            "video_id": game.get("video_id"),
            "channel_id": metadata.get("channel_id"),
            "source_kind": candidate.get("source_kind") if candidate else None,
            "verification_mode": mode,
            "verified": bool(mode == "live" and not reasons),
            "rejection_reasons": sorted(set(reasons)),
            "candidate_details": candidate,
        }

    with ThreadPoolExecutor(max_workers=args.max_workers) as workers:
        rows = list(workers.map(verify, holdout["games"]))
    save_json(args.output, {"version": 1, "games": rows})
    print(f"Verified {sum(row['verified'] for row in rows)}/{len(rows)} official VODs")
    return 0 if all(row["verified"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
