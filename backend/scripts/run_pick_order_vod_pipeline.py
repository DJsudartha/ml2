from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.data.pick_order_annotations import PICK_ORDER_ANNOTATIONS_PATH  # noqa: E402
from backend.services.data.raw_games import RAW_TOURNAMENTS_DIR  # noqa: E402
from backend.services.data.vod_layouts import LAYOUTS_PATH  # noqa: E402
from backend.services.data.vod_pipeline import (  # noqa: E402
    DEFAULT_LEDGER_PATH,
    DEFAULT_RESULTS_DIR,
    DEFAULT_REVIEW_PATH,
    run_vod_pipeline,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process matched official VODs into human-review pick-order suggestions."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=RAW_TOURNAMENTS_DIR)
    parser.add_argument("--annotations", type=Path, default=PICK_ORDER_ANNOTATIONS_PATH)
    parser.add_argument("--layouts", type=Path, default=LAYOUTS_PATH)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW_PATH)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--max-vod-streams", type=int, default=2)
    parser.add_argument("--max-inference-workers", type=int, default=4)
    args = parser.parse_args()

    report = run_vod_pipeline(
        manifest_path=args.manifest,
        raw_dir=args.raw_dir,
        annotations_path=args.annotations,
        layouts_path=args.layouts,
        results_dir=args.results_dir,
        review_path=args.review_output,
        ledger_path=args.ledger,
        max_vod_streams=args.max_vod_streams,
        max_inference_workers=args.max_inference_workers,
    )
    print(json.dumps(report["counts"], sort_keys=True))
    return 0 if not report["counts"].get("processing_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
