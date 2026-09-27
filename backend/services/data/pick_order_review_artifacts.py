"""Persistence and summaries for compact pick-order review artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

import numpy as np


EvidenceMode = Literal["none", "frame", "crops"]


def write_image(path: Path, image: np.ndarray) -> None:
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".partial.jpg")
    try:
        if not cv2.imwrite(str(temporary), image):
            raise OSError(f"Unable to write evidence image {path}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def review_contact_sheet(
    first_crops: dict[str, np.ndarray], suggestion: dict[str, Any]
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    import cv2

    tile_width, tile_height = 200, 170
    image_height = 118
    canvas = np.full((tile_height * 2, tile_width * 5, 3), 24, np.uint8)
    rows_by_slot = {}
    for field in ("slot_suggestions", "proposed_picks", "picks"):
        for row in suggestion.get(field, []):
            if row.get("slot"):
                rows_by_slot[row["slot"]] = row
    metadata = []
    for row_index, team in enumerate(("blue", "red")):
        for column, pick_index in enumerate(range(1, 6)):
            slot = f"{team}_pick{pick_index}"
            crop = first_crops.get(slot)
            if crop is None or crop.size == 0:
                continue
            height, width = crop.shape[:2]
            scale = min((tile_width - 12) / width, image_height / height)
            resized = cv2.resize(
                crop,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            tile_x = column * tile_width
            tile_y = row_index * tile_height
            x = tile_x + (tile_width - resized.shape[1]) // 2
            y = tile_y + 4
            canvas[y : y + resized.shape[0], x : x + resized.shape[1]] = resized
            identity = rows_by_slot.get(slot, {})
            hero = identity.get("hero")
            if not hero:
                candidates = identity.get("top_candidates") or []
                candidate = identity.get("best_candidate")
                if not candidate and candidates:
                    candidate = candidates[0]["hero"]
                hero = f"? {candidate}" if candidate else "? unresolved"
            confidence = identity.get("confidence")
            if confidence is None:
                confidence = identity.get("candidate_confidence")
            cv2.putText(
                canvas,
                f"{team[0].upper()}{pick_index} {str(hero)[:20]}",
                (tile_x + 6, tile_y + 138),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.43,
                (235, 235, 235),
                1,
                cv2.LINE_AA,
            )
            if confidence is not None:
                cv2.putText(
                    canvas,
                    f"confidence {float(confidence):.3f}",
                    (tile_x + 6, tile_y + 158),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.38,
                    (170, 205, 255) if team == "blue" else (190, 180, 255),
                    1,
                    cv2.LINE_AA,
                )
            metadata.append(
                {
                    "slot": slot,
                    "hero": identity.get("hero"),
                    "best_candidate": identity.get("best_candidate"),
                    "confidence": confidence,
                    "reason": identity.get("reason"),
                    "top_candidates": identity.get("top_candidates", []),
                    "margin": identity.get("margin"),
                    "assignment_margin": identity.get("assignment_margin"),
                }
            )
    return canvas, metadata


def persist_evidence(
    suggestion: dict[str, Any],
    first_frame: np.ndarray,
    first_crops: dict[str, np.ndarray],
    *,
    output_dir: Path,
    video_id: str,
    identity: str,
    evidence_mode: EvidenceMode,
    lock_event_crops: dict[str, tuple[np.ndarray, np.ndarray]] | None = None,
    lock_events: list[dict[str, Any]] | None = None,
) -> None:
    if evidence_mode == "none":
        suggestion["review_crops"] = []
        return
    if evidence_mode == "frame":
        path = output_dir / "completed" / f"{video_id}_{identity}.jpg"
        write_image(path, first_frame)
        resolved = str(path.resolve())
        suggestion["frame"] = resolved
        for pick in suggestion.get("picks", []):
            pick["evidence_frame"] = resolved
            pick["evidence_frames"] = [resolved]
        return

    evidence_dir = output_dir / "evidence" / f"{video_id}_{identity}"
    records_by_slot = {}
    for field in ("slot_suggestions", "proposed_picks", "picks"):
        for record in suggestion.get(field, []):
            if record.get("slot"):
                records_by_slot.setdefault(record["slot"], []).append(record)
    review_crops = []
    for slot in sorted(first_crops):
        path = evidence_dir / f"{slot}_settled.jpg"
        write_image(path, first_crops[slot])
        resolved = str(path.resolve())
        review_crops.append(
            {
                "slot": slot,
                "phase": "first_settled_pre_swap",
                "frame": resolved,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        for record in records_by_slot.get(slot, []):
            record["evidence_frame"] = resolved
            record["evidence_frames"] = [resolved]
    suggestion["review_crops"] = review_crops
    contact_sheet, contact_slots = review_contact_sheet(first_crops, suggestion)
    contact_path = evidence_dir / "review_contact_sheet.jpg"
    write_image(contact_path, contact_sheet)
    suggestion["contact_sheet"] = {
        "frame": str(contact_path.resolve()),
        "sha256": hashlib.sha256(contact_path.read_bytes()).hexdigest(),
        "slots": contact_slots,
    }
    events = {item["slot"]: item for item in lock_events or []}
    sequences = []
    for slot, crops in sorted((lock_event_crops or {}).items()):
        event = events.get(slot, {})
        for phase, crop, timestamp in (
            ("first_visible", crops[0], event.get("timestamp_sec")),
            ("stable_locked", crops[1], event.get("stable_through_sec")),
        ):
            path = evidence_dir / f"{slot}_{phase}.jpg"
            write_image(path, crop)
            sequences.append(
                {
                    "slot": slot,
                    "phase": phase,
                    "timestamp_sec": timestamp,
                    "frame": str(path.resolve()),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    suggestion["review_sequences"] = sequences


def cached_evidence_exists(row: dict[str, Any], evidence_mode: EvidenceMode) -> bool:
    if evidence_mode == "none":
        return True
    if evidence_mode == "frame":
        return bool(row.get("frame") and Path(row["frame"]).is_file())
    crops = row.get("review_crops", [])
    contact_sheet = row.get("contact_sheet", {})
    return (
        len(crops) == 10
        and all(Path(crop.get("frame", "")).is_file() for crop in crops)
        and Path(contact_sheet.get("frame", "")).is_file()
    )


def capture_report_payload(
    rows: list[dict[str, Any]], requested_games: int, extractor_version: str
) -> dict[str, Any]:
    return {
        "version": 1,
        "extractor_version": extractor_version,
        "counts": {
            "requested_games": requested_games,
            "complete_orders": sum(bool(row.get("order_complete")) for row in rows),
            "needs_review": sum(row.get("status") == "needs_review" for row in rows),
            "failed": sum(row.get("status") == "failed" for row in rows),
            "evidence_crops": sum(len(row.get("review_crops", [])) for row in rows),
            "full_frames": sum(bool(row.get("frame")) for row in rows),
        },
        "games": rows,
        "requested_games": requested_games,
    }
