from __future__ import annotations

import argparse
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.common.file_utils import save_json  # noqa: E402
from backend.services.data.raw_games import RAW_TOURNAMENTS_DIR, load_raw_games  # noqa: E402
from backend.services.data.vod_sources import (  # noqa: E402
    VOD_SOURCES_PATH,
    discover_vod_manifest,
    load_vod_sources,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover official per-game VODs and build a reviewable game manifest."
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output path for the generated VOD manifest.",
    )
    parser.add_argument(
        "--source-config",
        type=Path,
        default=VOD_SOURCES_PATH,
        help="Approved official-channel source registry.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_TOURNAMENTS_DIR,
        help="Directory containing raw Liquipedia tournament JSON files.",
    )
    parser.add_argument("--since-days", type=int, default=8)
    parser.add_argument('--youtube-fallback', action='store_true', help='Search official uploads only when Liquipedia has no usable game link.')
    parser.add_argument("--min-confidence", type=float, default=0.75)
    parser.add_argument("--min-lead", type=float, default=0.10)
    args = parser.parse_args()

    load_dotenv(ROOT_DIR / "backend" / ".env")
    registry = load_vod_sources(args.source_config)
    manifest = discover_vod_manifest(
        games=load_raw_games(args.raw_dir),
        youtube_fallback=args.youtube_fallback,
        since_days=args.since_days,
        source_registry=registry,
        min_confidence=args.min_confidence,
        min_lead=args.min_lead,
    )
    save_json(args.output, manifest)

    counts: dict[str, int] = {}
    for entry in manifest["games"]:
        status = str(entry["status"])
        counts[status] = counts.get(status, 0) + 1
    summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    print(f"Wrote {len(manifest['games'])} game entries to {args.output}: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
