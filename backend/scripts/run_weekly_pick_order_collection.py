from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.common.file_utils import save_json  # noqa: E402
from backend.services.data.raw_games import RAW_TOURNAMENTS_DIR, load_raw_games  # noqa: E402
from backend.services.data.vod_pipeline import DEFAULT_RESULTS_DIR, DEFAULT_REVIEW_PATH  # noqa: E402
from backend.services.data.pick_order_gallery_release import validate_gallery_release  # noqa: E402
from backend.services.data.pick_order_media_rights import require_media_rights  # noqa: E402
from backend.services.data.vod_pick_order_suggestions import (  # noqa: E402
    HERO_REFERENCE_GALLERY_DIR,
)
from backend.scripts.capture_complete_drafts import EXTRACTOR_VERSION  # noqa: E402
from backend.services.data.vod_sources import (  # noqa: E402
    VOD_SOURCES_PATH,
    discover_vod_manifest,
    load_vod_sources,
)

DEFAULT_MANIFEST_PATH = Path("backend/data/raw/pick_order_suggestions/latest_manifest.json")
DEFAULT_GATE_PATH = Path(
    "backend/data/raw/pick_order_suggestions/consistency_holdout/gate.json"
)


def _recent_games(raw_dir: Path, since_days: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=since_days)
    recent: list[dict] = []
    for game in load_raw_games(raw_dir):
        try:
            game_date = datetime.strptime(str(game["date"]), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except (KeyError, TypeError, ValueError):
            continue
        if cutoff <= game_date <= now and len(game.get("blue_picks", [])) == 5 and len(
            game.get("red_picks", [])
        ) == 5:
            recent.append(game)
    return recent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover and process the latest official MLBB pick-order VODs."
    )
    parser.add_argument("--since-days", type=int, default=8)
    parser.add_argument('--youtube-fallback', action='store_true')
    parser.add_argument("--source-config", type=Path, default=VOD_SOURCES_PATH)
    parser.add_argument("--raw-dir", type=Path, default=RAW_TOURNAMENTS_DIR)
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW_PATH)
    parser.add_argument("--gate-report", type=Path, default=DEFAULT_GATE_PATH)
    parser.add_argument("--media-rights-file", type=Path)
    parser.add_argument("--gallery-manifest", type=Path)
    parser.add_argument("--gallery-dir", type=Path, default=HERO_REFERENCE_GALLERY_DIR)
    parser.add_argument(
        "--profiles", type=Path, default=Path("backend/data/complete_draft_profiles.json")
    )
    parser.add_argument("--max-vod-streams", type=int, default=2)
    parser.add_argument("--max-inference-workers", type=int, default=4)
    args = parser.parse_args()

    gate = json.loads(args.gate_report.read_text(encoding="utf-8")) if (
        args.gate_report.is_file()
    ) else {}
    if not gate.get("gate_passed") or not gate.get("weekly_ready"):
        parser.error("Pick-order consistency gate is not approved for weekly rollout")
    try:
        gallery = validate_gallery_release(args.gallery_dir, args.gallery_manifest)
        if gate.get("gallery_release_id") != gallery["release_id"]:
            raise ValueError("Weekly gallery release differs from accepted holdout")
        registry = load_vod_sources(args.source_config)
        rights = require_media_rights(
            args.media_rights_file,
            {source["channel_id"] for source in registry["sources"]},
        )
        if not rights.get("freeze_private_gallery"):
            raise ValueError("Gallery retention is not authorized")
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    if args.max_vod_streams < 1 or args.max_inference_workers < 1:
        parser.error("Worker limits must be positive")
    if not args.profiles.is_file():
        parser.error("Reviewed layout profiles are missing")

    load_dotenv(ROOT_DIR / "backend" / ".env")
    games = _recent_games(args.raw_dir, args.since_days)
    manifest = discover_vod_manifest(
        games=games,
        youtube_fallback=args.youtube_fallback,
        since_days=args.since_days,
        source_registry=registry,
    )
    save_json(args.manifest_output, manifest)
    entries = sorted(
        (entry for entry in manifest["games"]
         if entry.get("status") == "matched" and
         entry.get("source_kind") == "per_game"),
        key=lambda entry: entry["game_id"],
    )

    def capture(entry):
        key = hashlib.sha256(json.dumps([
            entry["game_id"], entry["video_id"], entry.get("layout_id"),
            gallery["release_id"], EXTRACTOR_VERSION,
        ], sort_keys=True).encode()).hexdigest()[:16]
        job_dir = args.results_dir / key
        command = [
            sys.executable,
            str(ROOT_DIR / "backend/scripts/capture_complete_drafts.py"),
            "--manifest", str(args.manifest_output),
            "--profiles", str(args.profiles),
            "--raw-dir", str(args.raw_dir),
            "--output-dir", str(job_dir),
            "--game-id", entry["game_id"],
            "--media-rights-file", str(args.media_rights_file),
            "--gallery-manifest", str(args.gallery_manifest),
            "--gallery-dir", str(args.gallery_dir),
            "--evidence-mode", "crops",
        ]
        result = None
        report_path = job_dir / "report.json"
        for attempt in range(3):
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if report_path.is_file():
                try:
                    row = json.loads(report_path.read_text(encoding="utf-8"))["games"][0]
                except (KeyError, json.JSONDecodeError):
                    row = None
                if row and not (
                    row.get("status") == "failed" and row.get("reason") in {
                        "RuntimeError", "TimeoutError", "TimeoutExpired",
                        "ConnectionError",
                    } and attempt < 2
                ):
                    return row
        return {
            "game_id": entry["game_id"], "video_id": entry["video_id"],
            "status": "failed", "order_complete": False,
            "reason": f"capture_exit_{result.returncode if result else 'unknown'}",
        }

    with ThreadPoolExecutor(max_workers=min(
        args.max_vod_streams, args.max_inference_workers
    )) as workers:
        rows = list(workers.map(capture, entries))
    rows.sort(key=lambda row: row["game_id"])
    report = {
        "version": 1,
        "source": "weekly_complete_draft_capture",
        "gallery_release_id": gallery["release_id"],
        "extractor_version": EXTRACTOR_VERSION,
        "counts": {
            "selected": len(entries),
            "complete": sum(bool(row.get("order_complete")) for row in rows),
            "incomplete": sum(not row.get("order_complete") for row in rows),
            "failed": sum(row.get("status") == "failed" for row in rows),
        },
        "games": rows,
    }
    save_json(args.review_output, report)
    print(json.dumps(report["counts"], sort_keys=True))
    return 1 if report["counts"]["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
