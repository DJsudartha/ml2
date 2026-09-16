"""Read vertical hero-name strips on supported draft broadcasts in memory."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

import cv2
import numpy as np

from backend.services.data.vod_layouts import (
    detect_active_video_bounds,
    slot_bounds_for_frame,
)


def normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def match_hero_name(text: str, heroes: list[str]) -> tuple[str, float] | None:
    normalized = normalized_name(text)
    if len(normalized) < 4:
        return None
    scores = sorted(
        (
            SequenceMatcher(None, normalized, normalized_name(hero)).ratio(),
            hero,
        )
        for hero in heroes
    )
    scores.reverse()
    if (
        not scores
        or scores[0][0] < 0.8
        or (len(scores) > 1 and scores[0][0] - scores[1][0] < 0.15)
    ):
        return None
    return scores[0][1], float(scores[0][0])


def read_broadcast_hero_names(
    frame: np.ndarray,
    layout: dict[str, Any],
    raw_game: dict[str, Any],
    engine: Any,
    *,
    timestamp_sec: float | None = None,
    min_ocr_score: float = 0.8,
) -> list[dict[str, Any]]:
    """Locate OCR text in the calibrated cards and constrain it to LP hero sets.

    The official MPL Indonesia Season 18 overlay prints names vertically beside
    every portrait. The caller supplies a locally initialized OCR engine; no
    frame, VOD, or OCR image is retained by this function.
    """
    active = detect_active_video_bounds(frame)
    slots = {
        slot: bounds
        for slot, bounds in slot_bounds_for_frame(
            layout, frame.shape, active_bounds=active
        ).items()
        if "_pick" in slot
    }
    observations = []
    for team in ("blue", "red"):
        team_slots = {
            slot: bounds for slot, bounds in slots.items()
            if slot.startswith(f"{team}_pick")
        }
        if len(team_slots) != 5:
            continue
        margin = round(active[2] * 0.02)
        x0 = max(active[0], min(bounds[0] for bounds in team_slots.values()) - margin)
        x1 = min(active[0] + active[2],
                 max(bounds[0] + bounds[2] for bounds in team_slots.values()) + margin)
        y0 = min(bounds[1] for bounds in team_slots.values())
        y1 = max(bounds[1] + bounds[3] for bounds in team_slots.values())
        band = frame[y0:y1, x0:x1]
        if band.size == 0:
            continue
        rotated = cv2.rotate(band, cv2.ROTATE_90_COUNTERCLOCKWISE)
        result = engine(cv2.resize(rotated, None, fx=4, fy=4))
        for box, text, score in zip(
            result.boxes if result.boxes is not None else [],
            result.txts or [],
            result.scores if result.scores is not None else [],
        ):
            score = float(score)
            if score < min_ocr_score:
                continue
            rx, ry = np.mean(box, axis=0) / 4
            px = x0 + (x1 - x0) - 1 - ry
            py = y0 + rx
            matched = match_hero_name(str(text), list(raw_game.get(f"{team}_picks", [])))
            if matched is None:
                continue
            hero, name_score = matched
            for slot, (sx, sy, sw, sh) in team_slots.items():
                if sx <= px < sx + sw and sy <= py < sy + sh:
                    observations.append({
                        "slot": slot,
                        "hero": hero,
                        "confidence": round(min(score, name_score), 4),
                        "source": "broadcast_name",
                        "timestamp_sec": timestamp_sec,
                        "observed_text": str(text),
                    })
                    break
    return observations
