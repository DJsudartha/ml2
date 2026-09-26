"""Fix a match-disjoint M7/MPL S18 holdout using local Liquipedia metadata only."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.data.pick_order_exclusions import (  # noqa: E402
    load_exclusion_registry,
)
from backend.services.data.pick_order_consistency import select_holdout  # noqa: E402
from backend.services.data.raw_games import load_raw_games  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--exclusion-registry", type=Path, required=True)
    parser.add_argument("--games-per-layout", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        exclusions = load_exclusion_registry(args.exclusion_registry)
        payload = select_holdout(
            list(load_raw_games(args.raw_dir)),
            exclusions,
            args.games_per_layout,
        )
    except (KeyError, ValueError) as exc:
        parser.error(str(exc))
    if args.output.exists():
        existing = load_json(args.output)
        if existing != payload:
            parser.error("Existing holdout is fixed; use a new output path")
    save_json(args.output, payload)
    print(f"Fixed {len(payload['games'])} metadata-only games: {payload['selection_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
