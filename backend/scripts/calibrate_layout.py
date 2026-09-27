from __future__ import annotations

import argparse
from math import gcd
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.services.common.file_utils import save_json  # noqa: E402
from backend.services.data.vod_layouts import LAYOUTS_PATH, load_layout_registry  # noqa: E402

SLOT_NAMES = [
    *(f"blue_ban{index}" for index in range(1, 6)),
    *(f"red_ban{index}" for index in range(1, 6)),
    *(f"blue_pick{index}" for index in range(1, 6)),
    *(f"red_pick{index}" for index in range(1, 6)),
]


def _normalized_bounds(
    top_left: tuple[int, int],
    bottom_right: tuple[int, int],
    width: int,
    height: int,
) -> list[float]:
    x1, y1 = top_left
    x2, y2 = bottom_right
    return [
        round(min(x1, x2) / width, 6),
        round(min(y1, y2) / height, 6),
        round(abs(x2 - x1) / width, 6),
        round(abs(y2 - y1) / height, 6),
    ]


def calibrate(
    *,
    frame_path: Path,
    layout_id: str,
    family: str,
    output_path: Path,
    anchor_names: list[str],
) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise ImportError("Install backend/requirements-vod.txt to calibrate layouts") from exc

    image_original = cv2.imread(str(frame_path))
    if image_original is None:
        raise FileNotFoundError(f"Could not load frame: {frame_path}")
    image_height, image_width = image_original.shape[:2]
    target_names = [*SLOT_NAMES, *(f"anchor:{name}" for name in anchor_names)]
    clicks: list[tuple[int, int]] = []

    def redraw() -> None:
        image = image_original.copy()
        target_index = len(clicks) // 2
        click_index = len(clicks) % 2
        for index in range(target_index):
            x1, y1 = clicks[index * 2]
            x2, y2 = clicks[index * 2 + 1]
            cv2.rectangle(image, (x1, y1), (x2, y2), (0, 200, 0), 1)
            cv2.putText(
                image,
                target_names[index],
                (x1, max(12, y1 - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.35,
                (0, 200, 0),
                1,
            )
        if target_index < len(target_names):
            corner = "TOP-LEFT" if click_index == 0 else "BOTTOM-RIGHT"
            status = f"{target_index + 1}/{len(target_names)} {target_names[target_index]}: {corner}"
        else:
            status = "Complete - press any key to save"
        cv2.rectangle(image, (0, 0), (image_width, 28), (0, 0, 0), -1)
        cv2.putText(image, status, (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.imshow("Calibrate layout", image)

    def mouse_callback(event: int, x: int, y: int, _flags: int, _param: Any) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and len(clicks) < len(target_names) * 2:
            clicks.append((x, y))
            redraw()
        elif event == cv2.EVENT_RBUTTONDOWN and clicks:
            clicks.pop()
            redraw()

    redraw()
    cv2.setMouseCallback("Calibrate layout", mouse_callback)
    cv2.waitKey(0)
    cv2.destroyAllWindows()
    if len(clicks) != len(target_names) * 2:
        raise ValueError(f"Calibration stopped after {len(clicks)} of {len(target_names) * 2} clicks")

    slots = {
        name: _normalized_bounds(
            clicks[index * 2],
            clicks[index * 2 + 1],
            image_width,
            image_height,
        )
        for index, name in enumerate(SLOT_NAMES)
    }
    anchor_root = output_path.parent / "layout_anchors" / layout_id
    anchors: list[dict[str, Any]] = []
    for anchor_offset, anchor_name in enumerate(anchor_names, start=len(SLOT_NAMES)):
        normalized = _normalized_bounds(
            clicks[anchor_offset * 2],
            clicks[anchor_offset * 2 + 1],
            image_width,
            image_height,
        )
        x = round(normalized[0] * image_width)
        y = round(normalized[1] * image_height)
        width = round(normalized[2] * image_width)
        height = round(normalized[3] * image_height)
        template_path = anchor_root / f"{anchor_name}.png"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(template_path), image_original[y : y + height, x : x + width])
        anchors.append(
            {
                "name": anchor_name,
                "bounds": normalized,
                "template": str(template_path.relative_to(output_path.parent / "layout_anchors")),
            }
        )

    common_divisor = gcd(image_width, image_height)
    registry = load_layout_registry(output_path) if output_path.exists() else {
        "version": 1,
        "legacy_aliases": {},
        "layouts": {},
    }
    registry["version"] = 1
    registry["layouts"][layout_id] = {
        "family": family,
        "version": 1,
        "status": "active",
        "coordinate_space": "normalized",
        "aspect_ratio": [image_width // common_divisor, image_height // common_divisor],
        "slots": slots,
        "anchors": anchors,
        "detection": {
            "minimum_slot_stddev": 18.0,
            "minimum_edge_density": 0.025,
            "minimum_score": 0.72,
        },
    }
    save_json(output_path, registry)
    print(f"Saved active layout {layout_id} to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Calibrate normalized VOD draft layout slots.")
    parser.add_argument("--frame", type=Path, required=True)
    parser.add_argument("--layout-id", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--output", type=Path, default=LAYOUTS_PATH)
    parser.add_argument(
        "--anchor",
        action="append",
        default=[],
        help="Static anchor name to capture after slots; repeat for multiple anchors.",
    )
    args = parser.parse_args()
    calibrate(
        frame_path=args.frame,
        layout_id=args.layout_id,
        family=args.family,
        output_path=args.output,
        anchor_names=args.anchor,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
