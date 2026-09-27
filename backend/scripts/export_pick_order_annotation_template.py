from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.common.file_utils import save_json  # noqa: E402
from backend.services.data.pick_order_annotations import build_annotation_template  # noqa: E402
from backend.services.data.raw_games import RAW_TOURNAMENTS_DIR  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a pick-order annotation review template from raw tournament games."
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_TOURNAMENTS_DIR,
        help="Directory containing raw tournament JSON files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional output path. Without this, the template is printed to stdout.",
    )
    args = parser.parse_args()

    payload = build_annotation_template(raw_dir=args.raw_dir)
    if args.output is None:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        save_json(args.output, payload)
        print(f"Wrote {len(payload['games'])} template games to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
