"""Make small, unlabeled timestamped contact sheets from authorized review crops."""

from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.data.pick_order_media_rights import require_media_rights  # noqa: E402


def _contact_sheet(crops, destination):
    import cv2
    import numpy as np

    columns = 5
    cell_w, cell_h = 150, 230
    canvas = np.full(
        (math.ceil(len(crops) / columns) * cell_h, columns * cell_w, 3),
        245, dtype=np.uint8,
    )
    for index, item in enumerate(crops):
        path = Path(item["frame"])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != (
            item.get("sha256")
        ):
            raise ValueError(f"Review crop missing or changed: {path}")
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Unreadable review crop: {path}")
        resized = cv2.resize(image, (110, 175), interpolation=cv2.INTER_AREA)
        x = (index % columns) * cell_w
        y = (index // columns) * cell_h
        canvas[y + 4:y + 179, x + 20:x + 130] = resized
        label = f"{item['slot']} {item.get('phase', '')}"
        stamp = item.get("timestamp_sec")
        cv2.putText(canvas, label[:23], (x + 3, y + 196),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, (20, 20, 20), 1)
        cv2.putText(canvas, f"t={stamp}", (x + 3, y + 216),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (20, 20, 20), 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + ".partial.jpg")
    try:
        if not cv2.imwrite(str(temporary), canvas):
            raise OSError("Could not write contact sheet")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--suggestions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--media-rights-file", type=Path)
    args = parser.parse_args()
    holdout = load_json(args.holdout)
    suggestions = load_json(args.suggestions)
    if not isinstance(holdout, dict) or not isinstance(holdout.get("games"), list):
        parser.error("Holdout games list is missing")
    if not isinstance(suggestions, dict) or not isinstance(suggestions.get("games"), list):
        parser.error("Capture suggestions games list is missing")
    source_registry = load_json(ROOT / "backend/data/vod_sources.json")
    channels = {source["source_id"]: source["channel_id"]
                for source in source_registry["sources"]}
    try:
        require_media_rights(
            args.media_rights_file,
            {channels[game["source_id"]] for game in holdout["games"]},
        )
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    by_id = {item.get("game_id"): item for item in suggestions["games"]}
    review_rows = []
    for game in holdout["games"]:
        row = by_id.get(game["game_id"], {})
        evidence = [
            {key: crop.get(key) for key in
             ("slot", "phase", "timestamp_sec", "frame", "sha256")}
            for crop in row.get("review_sequences", []) + row.get("review_crops", [])
        ]
        evidence.sort(key=lambda item: (
            float(item.get("timestamp_sec") or row.get("timestamp_sec") or 0),
            str(item.get("slot") or ""), str(item.get("phase") or ""),
        ))
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", game["game_id"]).strip("_")
        sheet = args.output_dir / f"{safe_name}.jpg"
        if evidence:
            try:
                _contact_sheet(evidence, sheet)
            except (OSError, ValueError) as exc:
                parser.error(str(exc))
        stamp = int(float(
            evidence[0].get("timestamp_sec") if evidence and
            evidence[0].get("timestamp_sec") is not None else
            row.get("timestamp_sec") or 0
        ))
        url = game["vod_url"]
        separator = "&" if "?" in url else "?"
        review_rows.append({
            "game_id": game["game_id"],
            "layout_id": game["layout_id"],
            "vod_timestamp_url": f"{url}{separator}t={stamp}",
            "contact_sheet": str(sheet.resolve()) if evidence else None,
            "crop_sequence": evidence,
            "label_status": "unlabeled",
        })
    save_json(args.output_dir / "review_queue.json", {
        "version": 1,
        "selection_id": holdout.get("selection_id"),
        "audit_policy": {
            "minimum_second_pass_delay_hours": 48,
            "recheck_all_inferred_ambiguous_disagreeing": True,
            "seeded_clear_recheck_fraction": 0.2,
        },
        "games": review_rows,
    })
    print(f"Prepared {len(review_rows)} blind review rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
