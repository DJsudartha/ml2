"""Conservative one-to-one matching for broadcast hero portraits.

The public boundary accepts a complete team score matrix. Image preparation and
temporal aggregation can evolve independently while the assignment invariants
remain testable: each final hero is used exactly once, weak portraits abstain,
and a near-tied team solution never becomes an order suggestion.
"""

from __future__ import annotations

from itertools import permutations
from typing import Any

import numpy as np


def _load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            "opencv-python-headless is required for hero portrait matching"
        ) from exc
    return cv2


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 0.0:
        return 0.0
    return float(np.dot(left, right) / denominator)


def _portrait_descriptor(image: np.ndarray) -> np.ndarray:
    cv2 = _load_cv2()
    resized = cv2.resize(image, (64, 64), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(resized, cv2.COLOR_BGR2LAB)
    luminance, channel_a, channel_b = cv2.split(lab)
    normalized_luminance = cv2.createCLAHE(
        clipLimit=2.0, tileGridSize=(4, 4)
    ).apply(luminance)
    normalized = cv2.cvtColor(
        cv2.merge((normalized_luminance, channel_a, channel_b)),
        cv2.COLOR_LAB2BGR,
    )
    hsv = cv2.cvtColor(normalized, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist(
        [hsv], [0, 1], None, [18, 8], [0, 180, 0, 256]
    ).flatten()
    histogram /= max(float(np.linalg.norm(histogram)), 1e-9)
    gray = cv2.cvtColor(normalized, cv2.COLOR_BGR2GRAY).astype("float32") / 255.0
    appearance = cv2.resize(gray, (24, 24), interpolation=cv2.INTER_AREA).flatten()
    appearance = (appearance - float(np.mean(appearance))) / max(
        float(np.std(appearance)), 1e-6
    )
    gradient_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edges = cv2.resize(
        cv2.magnitude(gradient_x, gradient_y),
        (16, 16),
        interpolation=cv2.INTER_AREA,
    ).flatten()
    edges /= max(float(np.linalg.norm(edges)), 1e-9)
    descriptor = np.concatenate((histogram * 2.0, appearance, edges * 4.0))
    norm = float(np.linalg.norm(descriptor))
    return descriptor / norm if norm > 0.0 else descriptor


def _descriptor_variants(image: np.ndarray) -> list[np.ndarray]:
    if image is None or image.size == 0:
        return []
    height, width = image.shape[:2]
    variants = []
    for inset in (0.0, 0.03, 0.06):
        x = round(width * inset)
        y = round(height * inset)
        cropped = image[y : height - y or height, x : width - x or width]
        if cropped.size:
            variants.append(_portrait_descriptor(cropped))
    return variants


def _portrait_score(samples: list[np.ndarray], references: list[np.ndarray]) -> float:
    reference_descriptors = [
        descriptor
        for reference in references
        for descriptor in _descriptor_variants(reference)
    ]
    if not reference_descriptors:
        return 0.0
    observation_scores = []
    for sample in samples:
        sample_descriptors = _descriptor_variants(sample)
        if sample_descriptors:
            observation_scores.append(
                max(
                    max(
                        0.0,
                        min(
                            1.0,
                            (
                                _cosine_similarity(
                                    sample_descriptor, reference_descriptor
                                )
                                + 1.0
                            )
                            / 2.0,
                        ),
                    )
                    for sample_descriptor in sample_descriptors
                    for reference_descriptor in reference_descriptors
                )
            )
    return float(np.median(observation_scores)) if observation_scores else 0.0


def match_team_portraits(
    samples_by_slot: dict[str, list[np.ndarray]],
    references_by_hero: dict[str, list[np.ndarray]],
    *,
    min_similarity: float = 0.72,
    min_slot_margin: float = -0.02,
    min_team_margin: float = 0.01,
) -> dict[str, Any]:
    """Score normalized temporal samples, then apply the unique-team gate."""
    scores = {
        slot: {
            hero: _portrait_score(samples, references)
            for hero, references in references_by_hero.items()
        }
        for slot, samples in samples_by_slot.items()
    }
    result = assign_unique_heroes(
        scores,
        list(references_by_hero),
        min_similarity=min_similarity,
        min_slot_margin=min_slot_margin,
        min_team_margin=min_team_margin,
    )
    for slot, row in result["slots"].items():
        row["observations"] = len(samples_by_slot.get(slot, []))
    return result


def assign_unique_heroes(
    scores_by_slot: dict[str, dict[str, float]],
    candidate_heroes: list[str],
    *,
    min_similarity: float,
    min_slot_margin: float,
    min_team_margin: float,
) -> dict[str, Any]:
    """Return the best gated bijection between team slots and final heroes."""
    slots = sorted(scores_by_slot)
    heroes = [str(hero) for hero in candidate_heroes]
    if not slots or len(slots) != len(heroes) or len(set(heroes)) != len(heroes):
        return {
            "accepted": False,
            "reason": "invalid_team_assignment_inputs",
            "team_margin": 0.0,
            "slots": {},
        }

    ranked: list[tuple[float, tuple[str, ...]]] = []
    for assigned in permutations(heroes):
        mean_score = sum(
            float(scores_by_slot[slot].get(hero, 0.0))
            for slot, hero in zip(slots, assigned)
        ) / len(slots)
        ranked.append((mean_score, assigned))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, best_assignment = ranked[0]
    runner_up_score = ranked[1][0] if len(ranked) > 1 else 0.0
    team_margin = best_score - runner_up_score

    rows: dict[str, dict[str, Any]] = {}
    assignment_accepted = team_margin >= min_team_margin
    for slot, assigned_hero in zip(slots, best_assignment):
        scored = sorted(
            (
                {
                    "hero": hero,
                    "score": round(float(scores_by_slot[slot].get(hero, 0.0)), 4),
                }
                for hero in heroes
            ),
            key=lambda item: (item["score"], item["hero"]),
            reverse=True,
        )
        assigned_score = float(scores_by_slot[slot].get(assigned_hero, 0.0))
        best_alternative = max(
            (
                float(scores_by_slot[slot].get(hero, 0.0))
                for hero in heroes
                if hero != assigned_hero
            ),
            default=0.0,
        )
        slot_margin = assigned_score - best_alternative
        slot_accepted = (
            assignment_accepted
            and assigned_score >= min_similarity
            and slot_margin >= min_slot_margin
        )
        if not assignment_accepted:
            reason = "ambiguous_team_assignment"
        elif assigned_score < min_similarity:
            reason = "low_similarity"
        elif slot_margin < min_slot_margin:
            reason = "ambiguous_slot_identity"
        else:
            reason = None
        rows[slot] = {
            "hero": assigned_hero if slot_accepted else None,
            "assigned_candidate": assigned_hero,
            "confidence": round(assigned_score, 4),
            "margin": round(slot_margin, 4),
            "team_margin": round(team_margin, 4),
            "top_candidates": scored[:2],
            "reason": reason,
        }

    accepted = assignment_accepted and all(row["hero"] for row in rows.values())
    if not assignment_accepted:
        reason = "ambiguous_team_assignment"
    elif not accepted:
        reason = "one_or_more_slots_below_identity_gate"
    else:
        reason = None
    return {
        "accepted": accepted,
        "reason": reason,
        "mean_similarity": round(best_score, 4),
        "team_margin": round(team_margin, 4),
        "slots": rows,
    }
