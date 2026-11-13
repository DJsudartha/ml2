"""Layout resolution and hero identity extraction for complete draft capture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from backend.services.common.file_utils import load_json
from backend.services.data.broadcast_hero_names import read_broadcast_hero_names
from backend.services.data.complete_draft import (
    CompletionTracker,
    ReferenceCompletionTracker,
    analysis_image,
    portrait_similarity,
)
from backend.services.data.hero_portrait_matcher import match_team_portraits
from backend.services.data.vod_pick_order_suggestions import (
    HERO_REFERENCE_GALLERY_DIR,
    hero_reference_paths,
    recognize_hero_crop,
)


def resolve_layout(profile: dict[str, Any]) -> dict[str, Any]:
    if profile.get("layout") is not None:
        return profile["layout"]
    return load_json(Path(profile["layouts_file"]))["layouts"][profile["layout_id"]]


def profile_with_asset_root(
    profile: dict[str, Any], asset_root: Path | None
) -> dict[str, Any]:
    resolved = dict(profile)
    if asset_root is None:
        return resolved
    for field in ("placeholder_frame", "layouts_file"):
        configured = resolved.get(field)
        if not configured:
            continue
        configured_path = Path(configured)
        if configured_path.is_absolute() or configured_path.is_file():
            continue
        candidate = asset_root / configured_path
        if candidate.is_file():
            resolved[field] = str(candidate)
    return resolved


def make_completion_tracker(
    layout: dict[str, Any],
    baseline: np.ndarray,
    reference: dict[str, Any] | None,
    source_fps: float,
) -> CompletionTracker | ReferenceCompletionTracker:
    if reference:
        import cv2

        image = cv2.imread(reference["frame"])
        if image is None:
            raise ValueError("Unreadable verified hero-reference frame")
        return ReferenceCompletionTracker(
            layout, image, minimum_frames=3, calibration_frame=baseline
        )
    return CompletionTracker(
        layout,
        baseline,
        minimum_frames=max(3, round(float(source_fps) * 0.15)),
    )


def sample_identity_frames(
    frames,
    tracker: CompletionTracker | ReferenceCompletionTracker,
    first_frame: np.ndarray,
    completion: dict[str, Any],
) -> tuple[list[tuple[np.ndarray, float]], list[str]]:
    """Take sparse in-memory observations; stop on role swaps or lost geometry."""
    samples = [(first_frame, completion["timestamp_sec"])]
    first_crops = tracker.pick_crops(first_frame)
    last_sample = float(completion["timestamp_sec"])
    deadline = last_sample + 2.5
    for frame, timestamp in frames:
        if timestamp > deadline:
            break
        if timestamp - last_sample < 0.5:
            continue
        analysis = analysis_image(frame)
        geometry_ok, _ = tracker.settled_card_geometry(analysis)
        current = tracker.pick_crops(frame)
        if not geometry_ok or any(
            portrait_similarity(first_crops[slot], current[slot]) < 0.65
            for slot in first_crops
        ):
            return samples, ["post_completion_swap_or_layout_change"]
        samples.append((frame, timestamp))
        last_sample = timestamp
        if len(samples) >= 3:
            break
    return samples, ([] if len(samples) >= 2 else ["identity_samples_not_time_separated"])


def identity_observations(
    profile: dict[str, Any],
    layout: dict[str, Any],
    raw_game: dict[str, Any],
    tracker: CompletionTracker | ReferenceCompletionTracker,
    first_frame: np.ndarray,
    completion: dict[str, Any],
    sampled_frames: list[tuple[np.ndarray, float]] | None = None,
    gallery_dir: Path = HERO_REFERENCE_GALLERY_DIR,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Recognize settled cards in memory and require repeated agreement."""
    mode = profile.get("identity_mode", "visual_reference")
    observations = []
    warnings = []
    if mode == "broadcast_names":
        try:
            from rapidocr import RapidOCR
        except ImportError:
            return [], ["broadcast_name_ocr_dependency_missing"]
        engine = RapidOCR()
        frames = sampled_frames or tracker.completion_frames or [
            (first_frame, completion["timestamp_sec"])
        ]
        for frame, timestamp in frames:
            observations.extend(
                read_broadcast_hero_names(
                    frame, layout, raw_game, engine, timestamp_sec=timestamp
                )
            )
    elif mode == "visual_reference":
        if sampled_frames:
            samples = [
                (tracker.pick_crops(frame), timestamp)
                for frame, timestamp in sampled_frames
            ]
        else:
            samples = getattr(tracker, "completion_observations", [])
        if profile.get("identity_assignment") == "team_unique":
            cv2 = __import__("cv2")
            timestamps = sorted({float(timestamp) for _, timestamp in samples})
            for team in ("blue", "red"):
                candidates = [str(hero) for hero in raw_game.get(f"{team}_picks", [])]
                references = {}
                for hero in candidates:
                    loaded = []
                    for path in hero_reference_paths(hero, gallery_dir=gallery_dir):
                        image = cv2.imread(str(path))
                        if image is not None:
                            loaded.append(image)
                    references[hero] = loaded
                samples_by_slot = {
                    f"{team}_pick{index}": [
                        crops[f"{team}_pick{index}"]
                        for crops, _ in samples
                        if f"{team}_pick{index}" in crops
                    ]
                    for index in range(1, 6)
                }
                assignment = match_team_portraits(samples_by_slot, references)
                if not assignment.get("accepted"):
                    warnings.append(
                        f"{team}_team_portrait_assignment_"
                        f"{assignment.get('reason', 'rejected')}"
                    )
                for slot, match in assignment.get("slots", {}).items():
                    hero = match.get("hero")
                    observations.append(
                        {
                            "slot": slot,
                            "hero": hero,
                            "confidence": (
                                max(0.8, float(match.get("confidence", 0.0)))
                                if hero
                                else float(match.get("confidence", 0.0))
                            ),
                            "source": "team_portrait_assignment",
                            "observations": int(match.get("observations", 0)),
                            "timestamp_sec": timestamps[0] if timestamps else None,
                            "last_observed_sec": timestamps[-1] if timestamps else None,
                            "best_candidate": match.get("assigned_candidate"),
                            "top_candidates": match.get("top_candidates", []),
                            "margin": match.get("margin"),
                            "assignment_margin": match.get("team_margin"),
                            "reason": match.get("reason"),
                        }
                    )
            if len([item for item in observations if item.get("hero")]) < 10:
                warnings.append("broadcast_art_gallery_incomplete_or_ambiguous")
            return observations, warnings
        for sample, timestamp in samples:
            for slot, crop in sample.items():
                team = slot.split("_")[0]
                candidates = list(raw_game.get(f"{team}_picks", []))
                match = (
                    recognize_hero_crop(crop, candidates)
                    if gallery_dir == HERO_REFERENCE_GALLERY_DIR
                    else recognize_hero_crop(
                        crop, candidates, gallery_dir=gallery_dir
                    )
                )
                if match.get("hero"):
                    observations.append(
                        {
                            "slot": slot,
                            "hero": match["hero"],
                            "confidence": match["confidence"],
                            "source": "canonical_or_confirmed_gallery",
                            "timestamp_sec": timestamp,
                        }
                    )
    else:
        raise ValueError(f"Unsupported game identity mode {mode}")

    grouped = {}
    for observation in observations:
        key = (observation["slot"], observation["hero"])
        grouped.setdefault(key, []).append(observation)
    stable = []
    for (slot, hero), seen in grouped.items():
        times = sorted({float(observation["timestamp_sec"]) for observation in seen})
        if len(times) < 2 or times[-1] - times[0] < 0.4:
            continue
        stable.append(
            {
                "slot": slot,
                "hero": hero,
                "confidence": sum(float(item["confidence"]) for item in seen)
                / len(seen),
                "source": seen[0]["source"],
                "observations": len(seen),
                "timestamp_sec": times[0],
                "last_observed_sec": times[-1],
            }
        )
    if mode == "visual_reference":
        rejected_slots = set()
        for team in ("blue", "red"):
            for hero in raw_game.get(f"{team}_picks", []):
                duplicates = sorted(
                    (
                        item
                        for item in stable
                        if item["slot"].startswith(f"{team}_")
                        and item["hero"] == hero
                    ),
                    key=lambda item: item["confidence"],
                    reverse=True,
                )
                if len(duplicates) < 2:
                    continue
                keep = (
                    1
                    if duplicates[0]["confidence"] - duplicates[1]["confidence"] >= 0.1
                    else 0
                )
                rejected_slots.update(item["slot"] for item in duplicates[keep:])
                warnings.append(f"{team}_duplicate_visual_identity_{hero}")
        stable = [item for item in stable if item["slot"] not in rejected_slots]
    if mode == "visual_reference" and len(stable) < 10:
        warnings.append("broadcast_art_gallery_incomplete_or_ambiguous")
    return stable, warnings
