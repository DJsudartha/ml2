"""Experimental headless OCR diagnostic for a calibrated, cropped draft strip.

Reads existing frames only. Does not infer missing heroes, confirm annotations,
or claim that a visible name is a locked pick. Install requirements-ocr.txt first.
"""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.data.raw_games import load_raw_games_by_id  # noqa: E402
from backend.services.data.broadcast_hero_names import match_hero_name  # noqa: E402
from backend.services.data.vod_layouts import (  # noqa: E402
    detect_active_video_bounds,
    slot_bounds_for_frame,
)


def match_name(text, heroes):
    matched = match_hero_name(text, heroes)
    return matched[0] if matched else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session",
        type=Path,
        required=True,
        help="Existing frame-sampling session JSON; does not use the web page.",
    )
    parser.add_argument("--start-index", type=int, required=True)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument(
        "--band",
        type=float,
        nargs=4,
        required=True,
        metavar=("X", "Y", "W", "H"),
        help="Normalized draft text-strip bounds in active video area.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--rotation",
        type=int,
        choices=(0, 90, 270),
        default=90,
        help="Counterclockwise degrees.",
    )
    args = parser.parse_args()
    x, y, w, h = args.band
    if (
        args.start_index < 0
        or not 1 <= args.count <= 60
        or min(x, y) < 0
        or min(w, h) <= 0
        or x + w > 1
        or y + h > 1
    ):
        parser.error("Use an in-bounds band, nonnegative start, and 1–60 frames.")
    import cv2
    import numpy as np
    from rapidocr import RapidOCR

    engine = RapidOCR()
    results = []
    for source in load_json(args.session)["sources"]:
        games = load_raw_games_by_id(Path(source["raw_dir"]))
        registry = load_json(Path(source["layouts"]))
        entries = {
            e["game_id"]: e for e in load_json(Path(source["manifest"]))["games"]
        }
        for game_id, sample in source["samples"].items():
            if not sample.get("frames_dir"):
                continue
            game = games[game_id]
            layout_id = entries[game_id]["layout_id"]
            layout_id = registry.get("legacy_aliases", {}).get(layout_id, layout_id)
            layout = registry["layouts"][layout_id]
            paths = sorted(Path(sample["frames_dir"]).glob("*.jpg"))
            for i in range(
                args.start_index, min(len(paths), args.start_index + args.count)
            ):
                frame = cv2.imread(str(paths[i]))
                if frame is None:
                    raise ValueError(f"Unreadable frame {paths[i]}")
                ax, ay, aw, ah = detect_active_video_bounds(frame)
                sx, sy, sw, sh = (
                    int(ax + x * aw),
                    int(ay + y * ah),
                    int(w * aw),
                    int(h * ah),
                )
                band = frame[sy : sy + sh, sx : sx + sw]
                if args.rotation:
                    band = cv2.rotate(
                        band,
                        cv2.ROTATE_90_COUNTERCLOCKWISE
                        if args.rotation == 90
                        else cv2.ROTATE_90_CLOCKWISE,
                    )
                result = engine(cv2.resize(band, None, fx=4, fy=4))
                slots = slot_bounds_for_frame(
                    layout, frame.shape, active_bounds=(ax, ay, aw, ah)
                )
                labels, readings = {}, []
                for box, text, score in zip(
                    result.boxes if result.boxes is not None else [],
                    result.txts or [],
                    result.scores or [],
                ):
                    rx, ry = np.mean(box, axis=0) / 4
                    if args.rotation == 90:
                        px, py = sx + sw - 1 - ry, sy + rx
                    elif args.rotation == 270:
                        px, py = sx + ry, sy + sh - 1 - rx
                    else:
                        px, py = sx + rx, sy + ry
                    readings.append(
                        {
                            "text": text,
                            "score": float(score),
                            "position": [float(px), float(py)],
                        }
                    )
                    if score < 0.85:
                        continue
                    for slot, (bx, by, bw, bh) in slots.items():
                        if "_pick" not in slot or not (
                            bx <= px < bx + bw and by <= py < by + bh
                        ):
                            continue
                        hero = match_name(text, game[slot.split("_")[0] + "_picks"])
                        if hero:
                            labels.setdefault(slot, []).append(hero)
                unique = {s: hs[0] for s, hs in labels.items() if len(set(hs)) == 1}
                unique = {
                    s: hero
                    for s, hero in unique.items()
                    if list(unique.values()).count(hero) == 1
                }
                results.append(
                    {
                        "game_id": game_id,
                        "frame": str(paths[i].resolve()),
                        "time": sample.get("start_sec", 0) + i / sample["fps"],
                        "slot_heroes": unique,
                        "readings": readings,
                        "status": "needs_review",
                        "all_ten_read": len(unique) == 10,
                    }
                )
                print(f"{game_id} frame {i}: {len(unique)}/10 names", flush=True)
    save_json(
        args.output,
        {"version": 1, "method": "experimental_name_ocr", "frames": results},
    )
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
