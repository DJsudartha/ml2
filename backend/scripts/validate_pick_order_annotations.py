from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.data.pick_order_annotations import (  # noqa: E402
    PICK_ORDER_ANNOTATIONS_PATH,
    validate_pick_order_annotations,
)
from backend.services.data.raw_games import RAW_TOURNAMENTS_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate curated pick-order annotations against raw Liquipedia games."
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        default=PICK_ORDER_ANNOTATIONS_PATH,
        help="Path to the pick-order annotation JSON file.",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_TOURNAMENTS_DIR,
        help="Directory containing raw tournament JSON files.",
    )
    args = parser.parse_args()

    report = validate_pick_order_annotations(
        annotations_path=args.annotations,
        raw_dir=args.raw_dir,
    )
    print(
        "Checked "
        f"{report.checked_games} annotation games; "
        f"{report.confirmed_games} confirmed; "
        f"{len(report.errors)} errors."
    )

    for error in report.errors:
        prefix = f"{error.game_id}: " if error.game_id else ""
        print(f"ERROR: {prefix}{error.message}")

    return 0 if report.is_valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
