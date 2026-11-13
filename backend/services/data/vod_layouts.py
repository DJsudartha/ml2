from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from backend.services.common.file_utils import load_json

LAYOUTS_PATH = Path("backend/data/layouts.json")


@dataclass(frozen=True)
class LayoutSelection:
    layout_id: str | None
    confidence: float
    method: str


@dataclass(frozen=True)
class DraftWindow:
    layout_id: str
    start_sec: float
    end_sec: float
    confidence: float


def load_layout_registry(path: Path = LAYOUTS_PATH) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected layout registry object at {path}")
    if "layouts" not in payload:
        payload = {"version": 0, "legacy_aliases": {}, "layouts": payload}
    if not isinstance(payload.get("layouts"), dict):
        raise ValueError(f"Layout registry at {path} must include a layouts object")
    return payload


def load_layout(layout_id: str, path: Path = LAYOUTS_PATH) -> dict[str, Any]:
    registry = load_layout_registry(path)
    aliases = registry.get("legacy_aliases", {})
    resolved_id = str(aliases.get(layout_id, layout_id))
    layout = registry["layouts"].get(resolved_id)
    if not isinstance(layout, dict):
        raise KeyError(f"No layout found for {layout_id} in {path}")
    if layout.get("status", "active") != "active":
        raise ValueError(f"Layout {resolved_id} is not active: {layout.get('status')}")
    slots = layout.get("slots")
    if not isinstance(slots, dict) or not slots:
        raise ValueError(f"Layout {resolved_id} must include calibrated slots")
    return {**layout, "layout_id": resolved_id}


def detect_active_video_bounds(
    frame: np.ndarray,
    *,
    black_threshold: float = 12.0,
) -> tuple[int, int, int, int]:
    gray = (
        frame.astype("float32").mean(axis=2)
        if frame.ndim == 3
        else frame.astype("float32")
    )
    height, width = gray.shape[:2]
    active_rows = np.flatnonzero(gray.mean(axis=1) > black_threshold)
    active_cols = np.flatnonzero(gray.mean(axis=0) > black_threshold)
    if not len(active_rows) or not len(active_cols):
        return 0, 0, width, height

    x1, x2 = int(active_cols[0]), int(active_cols[-1]) + 1
    y1, y2 = int(active_rows[0]), int(active_rows[-1]) + 1
    if (x2 - x1) < width * 0.6 or (y2 - y1) < height * 0.6:
        return 0, 0, width, height
    return x1, y1, x2 - x1, y2 - y1


def slot_bounds_for_frame(
    layout: dict[str, Any],
    frame_shape: tuple[int, ...],
    *,
    active_bounds: tuple[int, int, int, int] | None = None,
) -> dict[str, tuple[int, int, int, int]]:
    frame_height, frame_width = frame_shape[:2]
    active_x, active_y, active_width, active_height = active_bounds or (
        0,
        0,
        frame_width,
        frame_height,
    )
    coordinate_space = layout.get("coordinate_space", "pixels")
    bounds: dict[str, tuple[int, int, int, int]] = {}
    for slot, raw_bounds in layout.get("slots", {}).items():
        if not isinstance(raw_bounds, list) or len(raw_bounds) != 4:
            continue
        x, y, width, height = (float(value) for value in raw_bounds)
        if coordinate_space == "normalized":
            resolved = (
                active_x + round(x * active_width),
                active_y + round(y * active_height),
                max(1, round(width * active_width)),
                max(1, round(height * active_height)),
            )
        else:
            ref_width, ref_height = layout.get("resolution", [active_width, active_height])
            resolved = (
                active_x + round(x * active_width / max(float(ref_width), 1.0)),
                active_y + round(y * active_height / max(float(ref_height), 1.0)),
                max(1, round(width * active_width / max(float(ref_width), 1.0))),
                max(1, round(height * active_height / max(float(ref_height), 1.0))),
            )
        bounds[str(slot)] = resolved
    return bounds


def crop_slots(frame: np.ndarray, layout: dict[str, Any]) -> dict[str, np.ndarray]:
    crops: dict[str, np.ndarray] = {}
    for slot, (x, y, width, height) in slot_bounds_for_frame(
        layout,
        frame.shape,
        active_bounds=detect_active_video_bounds(frame),
    ).items():
        crop = frame[y : y + height, x : x + width]
        if crop.size:
            crops[slot] = crop
    return crops


def _load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            "opencv-python-headless is required for VOD layout analysis. "
            "Install backend/requirements-vod.txt to use this helper."
        ) from exc
    return cv2


def draft_likelihood_score(frame: np.ndarray, layout: dict[str, Any]) -> float:
    cv2 = _load_cv2()
    pick_crops = {
        slot: crop for slot, crop in crop_slots(frame, layout).items() if "_pick" in slot
    }
    if len(pick_crops) < 10:
        return 0.0

    detection = layout.get("detection", {})
    minimum_stddev = float(detection.get("minimum_slot_stddev", 18.0))
    minimum_edge_density = float(detection.get("minimum_edge_density", 0.025))
    occupied: dict[str, float] = {}
    for slot, crop in pick_crops.items():
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
        stddev_score = min(1.0, float(np.std(gray)) / max(minimum_stddev, 0.001))
        edges = cv2.Canny(gray, 60, 150)
        edge_density = float(np.count_nonzero(edges)) / max(float(edges.size), 1.0)
        edge_score = min(1.0, edge_density / max(minimum_edge_density, 0.001))
        occupied[slot] = (stddev_score + edge_score) / 2.0

    blue_score = np.mean([value for slot, value in occupied.items() if slot.startswith("blue")])
    red_score = np.mean([value for slot, value in occupied.items() if slot.startswith("red")])
    balance_score = 1.0 - min(1.0, abs(float(blue_score) - float(red_score)))
    return round(float(np.mean(list(occupied.values()))) * 0.8 + balance_score * 0.2, 4)


def _anchor_score(frame: np.ndarray, layout: dict[str, Any], assets_root: Path) -> float | None:
    anchors = layout.get("anchors", [])
    if not anchors:
        return None
    cv2 = _load_cv2()
    scores: list[float] = []
    for anchor in anchors:
        bounds = anchor.get("bounds")
        if not isinstance(bounds, list) or len(bounds) != 4:
            return 0.0
        anchor_layout = {"coordinate_space": "normalized", "slots": {"anchor": bounds}}
        x, y, width, height = slot_bounds_for_frame(
            anchor_layout,
            frame.shape,
            active_bounds=detect_active_video_bounds(frame),
        )["anchor"]
        crop = frame[y : y + height, x : x + width]
        if crop.size == 0:
            return 0.0

        reference_hashes = [str(value) for value in anchor.get("hashes", [])]
        if reference_hashes:
            gray = cv2.cvtColor(cv2.resize(crop, (9, 8)), cv2.COLOR_BGR2GRAY)
            bits = (gray[:, 1:] > gray[:, :-1]).flatten()
            observed_hash = sum(int(bit) << index for index, bit in enumerate(bits))
            similarities = [
                1.0 - (observed_hash ^ int(reference_hash, 16)).bit_count() / 64.0
                for reference_hash in reference_hashes
            ]
            scores.append(max(similarities))
            continue

        template_path = assets_root / str(anchor.get("template", ""))
        template = cv2.imread(str(template_path))
        if template is None:
            return 0.0
        template = cv2.resize(template, (crop.shape[1], crop.shape[0]))
        score = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)[0][0]
        scores.append(max(0.0, float(score)))
    return float(np.mean(scores)) if scores else 0.0


def select_layout(
    frame: np.ndarray,
    *,
    registry: dict[str, Any],
    preferred_layout_ids: Iterable[str] = (),
    assets_root: Path = Path("backend/data/layout_anchors"),
) -> LayoutSelection:
    aliases = registry.get("legacy_aliases", {})
    preferred = {str(aliases.get(item, item)) for item in preferred_layout_ids}
    scored: list[tuple[float, str, str]] = []
    for layout_id, layout in registry["layouts"].items():
        if preferred and layout_id not in preferred:
            continue
        if layout.get("status", "active") != "active" or not layout.get("slots"):
            continue
        anchor_score = _anchor_score(frame, layout, assets_root)
        heuristic_score = draft_likelihood_score(frame, layout)
        if anchor_score is None:
            score = heuristic_score * 0.8 + (0.2 if layout_id in preferred else 0.0)
            method = "source_hint_heuristic" if layout_id in preferred else "slot_heuristic"
        else:
            score = anchor_score * 0.8 + heuristic_score * 0.2
            method = "anchor"
        scored.append((float(score), str(layout_id), method))

    if not scored:
        return LayoutSelection(layout_id=None, confidence=0.0, method="unsupported")
    score, layout_id, method = max(scored)
    minimum_score = float(
        registry["layouts"][layout_id].get("detection", {}).get("minimum_score", 0.72)
    )
    if score < minimum_score:
        return LayoutSelection(layout_id=None, confidence=round(score, 4), method="unknown")
    return LayoutSelection(layout_id=layout_id, confidence=round(score, 4), method=method)


def locate_draft_window_from_frames(
    frame_paths: list[Path],
    *,
    registry: dict[str, Any],
    preferred_layout_ids: Iterable[str],
    scan_start_sec: float,
    sample_fps: float,
    minimum_run_sec: float = 30.0,
    padding_sec: float = 30.0,
    maximum_window_sec: float = 720.0,
) -> DraftWindow | None:
    windows = locate_draft_windows_from_frames(
        frame_paths,
        registry=registry,
        preferred_layout_ids=preferred_layout_ids,
        scan_start_sec=scan_start_sec,
        sample_fps=sample_fps,
        minimum_run_sec=minimum_run_sec,
        padding_sec=padding_sec,
        maximum_window_sec=maximum_window_sec,
    )
    return windows[0] if windows else None


def locate_draft_windows_from_frames(
    frame_paths: list[Path],
    *,
    registry: dict[str, Any],
    preferred_layout_ids: Iterable[str],
    scan_start_sec: float,
    sample_fps: float,
    minimum_run_sec: float = 30.0,
    padding_sec: float = 30.0,
    maximum_window_sec: float = 720.0,
) -> list[DraftWindow]:
    timed_frames = [
        (frame_path, scan_start_sec + index / max(sample_fps, 0.001))
        for index, frame_path in enumerate(sorted(frame_paths))
    ]
    return locate_draft_windows_from_timed_frames(
        timed_frames,
        registry=registry,
        preferred_layout_ids=preferred_layout_ids,
        sample_fps=sample_fps,
        minimum_run_sec=minimum_run_sec,
        padding_sec=padding_sec,
        maximum_window_sec=maximum_window_sec,
    )


def locate_draft_windows_from_timed_frames(
    timed_frames: list[tuple[Path, float]],
    *,
    registry: dict[str, Any],
    preferred_layout_ids: Iterable[str],
    sample_fps: float,
    minimum_run_sec: float = 30.0,
    padding_sec: float = 30.0,
    maximum_window_sec: float = 720.0,
) -> list[DraftWindow]:
    cv2 = _load_cv2()
    observations: list[tuple[float, LayoutSelection]] = []
    for frame_path, observed_at_sec in sorted(timed_frames, key=lambda item: item[1]):
        frame = cv2.imread(str(frame_path))
        if frame is None:
            continue
        selection = select_layout(
            frame,
            registry=registry,
            preferred_layout_ids=preferred_layout_ids,
        )
        observations.append((float(observed_at_sec), selection))

    minimum_frames = max(2, round(minimum_run_sec * max(sample_fps, 0.001)))
    runs: list[list[tuple[float, LayoutSelection]]] = []
    current_run: list[tuple[float, LayoutSelection]] = []
    maximum_gap_sec = max(2.0 / max(sample_fps, 0.001), 2.0)
    for observation in observations:
        observed_at_sec, selection = observation
        if selection.layout_id and (
            not current_run
            or (
                current_run[-1][1].layout_id == selection.layout_id
                and observed_at_sec - current_run[-1][0] <= maximum_gap_sec
            )
        ):
            current_run.append(observation)
        else:
            if len(current_run) >= minimum_frames:
                runs.append(current_run)
            current_run = [observation] if selection.layout_id else []
    if len(current_run) >= minimum_frames:
        runs.append(current_run)

    windows: list[DraftWindow] = []
    for run in runs:
        detected_start = run[0][0]
        detected_end = run[-1][0]
        start_sec = max(0.0, detected_start - padding_sec)
        end_sec = min(detected_end + padding_sec, start_sec + maximum_window_sec)
        windows.append(
            DraftWindow(
                layout_id=str(run[0][1].layout_id),
                start_sec=round(start_sec, 3),
                end_sec=round(end_sec, 3),
                confidence=round(float(np.mean([item[1].confidence for item in run])), 4),
            )
        )
    return windows
