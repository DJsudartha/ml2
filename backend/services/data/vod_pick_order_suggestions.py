from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from backend.services.common.file_utils import load_json
from backend.services.data.complete_draft import (
    accepted_portrait_assignment,
    best_portrait_assignment,
)
from backend.services.data.pick_order_results import (
    evidence_pick_order_result,
    hero_event_pick_order_result,
    role_remap_pick_order_result,
    slot_reveal_pick_order_result,
)
from backend.services.data.vod_layouts import crop_slots
from backend.services.modeling.pick_constants import PICK_SEQUENCE

HERO_ICON_DIR = Path("frontend/public/HeroIcon")
HERO_REFERENCE_GALLERY_DIR = Path("backend/data/raw/hero_reference_gallery")
EXTRACTOR_VERSION = "6.2.0"


def suggest_pick_order_from_game_evidence(
    raw_game: dict[str, Any],
    slot_observations: list[dict[str, Any]],
    *,
    provenance: dict[str, Any] | None = None,
    min_confidence: float = 0.8,
    complete_frame_verified: bool = False,
    lock_events: list[dict[str, Any]] | None = None,
    first_settled_timestamp_sec: float | None = None,
    require_lock_events: bool = False,
) -> dict[str, Any]:
    """Use observed hero identities, never Liquipedia's nonchronological slots.

    A settled pre-swap card supplies team-local pick positions. Liquipedia only
    constrains each team's final five identities. OCR and verified artwork may
    provide those identities, but an uncertain or contradictory game fails closed.
    """
    reasons: list[str] = []
    advisory_reasons: list[str] = []
    expected_slots = {
        f"{team}_pick{index}" for team in ("blue", "red") for index in range(1, 6)
    }
    pools = {
        team: [str(hero) for hero in raw_game.get(f"{team}_picks", [])]
        for team in ("blue", "red")
    }
    for team, pool in pools.items():
        if len(pool) != 5 or len(set(pool)) != 5:
            reasons.append(f"{team}_liquipedia_hero_set_invalid")

    events_by_slot: dict[str, dict[str, Any]] = {}
    slot_order_validated = not require_lock_events
    if require_lock_events:
        lock_events_valid = True
        if not provenance or not provenance.get("video_match_verified"):
            reasons.append("unverified_game_vod")
            lock_events_valid = False
        if not provenance or not provenance.get("layout_validated"):
            reasons.append("unknown_or_unverified_layout")
            lock_events_valid = False
        if not complete_frame_verified or first_settled_timestamp_sec is None:
            reasons.append("unverified_first_settled_pre_swap_frame")
            lock_events_valid = False
        for event in lock_events or []:
            slot = str(event.get("slot", ""))
            if slot not in expected_slots or slot in events_by_slot:
                reasons.append("duplicate_or_unknown_lock_event")
                lock_events_valid = False
                continue
            events_by_slot[slot] = event
            try:
                when = float(event["timestamp_sec"])
                stable_through = float(event["stable_through_sec"])
            except (KeyError, TypeError, ValueError):
                reasons.append(f"{slot}_lock_timestamp_missing")
                lock_events_valid = False
                continue
            if (not event.get("placeholder_observed") or
                not event.get("final_slot_verified") or
                not event.get("final_artwork_persisted") or
                stable_through - when < 0.3 or
                (first_settled_timestamp_sec is not None and
                 when >= first_settled_timestamp_sec)):
                reasons.append(f"{slot}_unverified_lock_event")
                lock_events_valid = False
        if len(events_by_slot) != 10:
            reasons.append("missing_lock_events")
            lock_events_valid = False
        chronological = []
        for team, order, _ in PICK_SEQUENCE:
            event = events_by_slot.get(f"{team}_pick{order}")
            if event and event.get("timestamp_sec") is not None:
                chronological.append(float(event["timestamp_sec"]))
        timestamp_order_conflict = len(chronological) == 10 and any(
            current - previous < 0.15
            for previous, current in zip(chronological, chronological[1:])
        )
        pre_swap_slot_order = bool(
            provenance
            and provenance.get("slot_semantics") == "pre_swap_pick_order"
        )
        if timestamp_order_conflict:
            if pre_swap_slot_order:
                advisory_reasons.append(
                    "lock_timestamps_not_used_for_pre_swap_slot_order"
                )
            else:
                reasons.append("tied_lock_events")
                lock_events_valid = False
        slot_order_validated = bool(
            lock_events_valid
            and provenance
            and provenance.get("video_match_verified")
            and provenance.get("layout_validated")
            and complete_frame_verified
            and first_settled_timestamp_sec is not None
        )

    by_slot: dict[str, list[dict[str, Any]]] = {slot: [] for slot in expected_slots}
    for observation in slot_observations:
        slot = str(observation.get("slot", ""))
        if slot in by_slot:
            by_slot[slot].append(observation)
    chosen = {}
    unresolved = {}
    identity_diagnostics: dict[str, dict[str, Any]] = {}
    for slot in sorted(expected_slots):
        team = slot.split("_")[0]
        diagnostic_candidates = [
            observation for observation in by_slot[slot]
            if observation.get("top_candidates")
        ]
        if diagnostic_candidates:
            diagnostic = max(
                diagnostic_candidates,
                key=lambda item: float(item.get("confidence", 0.0) or 0.0),
            )
            identity_diagnostics[slot] = {
                "best_candidate": diagnostic.get("best_candidate"),
                "candidate_confidence": round(
                    float(diagnostic.get("confidence", 0.0) or 0.0), 4
                ),
                "top_candidates": diagnostic.get("top_candidates", []),
                "margin": diagnostic.get("margin"),
                "assignment_margin": diagnostic.get("assignment_margin"),
                "identity_reason": diagnostic.get("reason"),
            }
        confident = [
            observation for observation in by_slot[slot]
            if observation.get("hero") in pools[team]
            and float(observation.get("confidence", 0) or 0) >= min_confidence
        ]
        identities = {str(observation["hero"]) for observation in confident}
        if len(identities) != 1:
            reason = "conflicting_hero_evidence" if identities else "unrecognized_hero"
            unresolved[slot] = reason
            continue
        hero = next(iter(identities))
        if require_lock_events:
            timestamps = sorted({float(observation.get("timestamp_sec", 0))
                                 for observation in confident})
            reported_spans = [
                float(observation.get("last_observed_sec", 0)) -
                float(observation.get("timestamp_sec", 0))
                for observation in confident
                if int(observation.get("observations", 0) or 0) >= 2
            ]
            if (len(timestamps) < 2 or timestamps[-1] - timestamps[0] < 0.4) and (
                not reported_spans or max(reported_spans) < 0.4
            ):
                unresolved[slot] = "identity_observations_not_time_separated"
                continue
        confidence = float(np.mean([
            float(observation["confidence"]) for observation in confident
        ]))
        sources = sorted({str(observation.get("source", "unknown"))
                          for observation in confident})
        evidence_frames = sorted({str(observation["frame_path"])
                                  for observation in confident
                                  if observation.get("frame_path")})
        chosen[slot] = {
            "hero": hero,
            "confidence": confidence,
            "sources": sources,
            "evidence_frames": evidence_frames,
            "identity_observed_at_sec": min(
                (float(item["timestamp_sec"]) for item in confident
                 if item.get("timestamp_sec") is not None),
                default=None,
            ),
            **identity_diagnostics.get(slot, {}),
        }

    if complete_frame_verified:
        for team, pool in pools.items():
            missing = [f"{team}_pick{index}" for index in range(1, 6)
                       if f"{team}_pick{index}" not in chosen]
            known = [chosen[f"{team}_pick{index}"]["hero"]
                     for index in range(1, 6)
                     if f"{team}_pick{index}" in chosen]
            if (
                len(pool) == 5 and len(set(pool)) == 5
                and len(missing) == 1 and len(known) == 4
                and len(set(known)) == 4
                and unresolved.get(missing[0]) == "unrecognized_hero"
                and set(known) <= set(pool)
                and (not require_lock_events or (
                    missing[0] in events_by_slot and
                    events_by_slot[missing[0]].get("final_slot_verified") and
                    events_by_slot[missing[0]].get("final_artwork_persisted")
                ))
            ):
                slot = missing[0]
                chosen[slot] = {
                    "hero": next(iter(set(pool) - set(known))),
                    "confidence": min(1.0, min_confidence + 0.02),
                    "sources": ["liquipedia_set_elimination"],
                    "evidence_frames": [],
                    "identity_observed_at_sec": None,
                }
                unresolved.pop(slot)

    reasons.extend(f"{slot}_{reason}" for slot, reason in unresolved.items())
    slot_suggestions = [
        {"slot": slot, **chosen[slot]} if slot in chosen else
        {
            "slot": slot,
            "hero": None,
            "reason": unresolved[slot],
            **identity_diagnostics.get(slot, {}),
        }
        for slot in sorted(expected_slots)
    ]

    identity_sets_valid = True
    for team, pool in pools.items():
        selected = [
            chosen[f"{team}_pick{index}"]["hero"]
            for index in range(1, 6)
            if f"{team}_pick{index}" in chosen
        ]
        if len(selected) == 5 and set(selected) != set(pool):
            reasons.append(f"{team}_observations_do_not_match_liquipedia")
            identity_sets_valid = False
        elif len(selected) != 5:
            identity_sets_valid = False
    identity_complete = len(chosen) == 10 and identity_sets_valid
    order_complete = bool(
        not reasons
        and identity_complete
        and (slot_order_validated or not require_lock_events)
    )
    proposed_picks = []
    for global_index, (team, team_pick_order, turn_index) in enumerate(
        PICK_SEQUENCE, start=1
    ):
        slot = f"{team}_pick{team_pick_order}"
        if slot not in chosen:
            continue
        evidence = chosen[slot]
        proposed_picks.append({
            "global_pick_index": global_index,
            "team": team,
            "team_pick_order": team_pick_order,
            "turn_index": turn_index,
            "slot": slot,
            "hero": evidence["hero"],
            "confidence": round(evidence["confidence"], 4),
            "identity_sources": evidence["sources"],
            "identity_observed_at_sec": evidence["identity_observed_at_sec"],
            "lock_timestamp_sec": (
                events_by_slot[slot].get("timestamp_sec")
                if slot in events_by_slot else None
            ),
            "evidence_frame": None,
            "evidence_frames": evidence["evidence_frames"],
        })
    picks = proposed_picks if order_complete else []
    all_reasons = sorted(set(reasons + advisory_reasons))
    return evidence_pick_order_result(
        game_id=raw_game["game_id"],
        identity_complete=identity_complete,
        slot_order_validated=slot_order_validated,
        order_complete=order_complete,
        confidence=round(
            float(np.mean([pick["confidence"] for pick in proposed_picks]))
            if proposed_picks
            else 0.0,
            4,
        ),
        notes="; ".join(all_reasons),
        ambiguity_reasons=sorted(set(reasons)),
        advisory_reasons=sorted(set(advisory_reasons)),
        lock_events=[events_by_slot[slot] for slot in sorted(events_by_slot)],
        extractor_version=EXTRACTOR_VERSION,
        slot_suggestions=slot_suggestions,
        proposed_picks=proposed_picks,
        picks=picks,
        provenance=provenance,
    )


def suggest_pick_order_from_role_remap(
    raw_game: dict[str, Any],
    first_complete_samples: list[dict[str, np.ndarray]],
    final_role_samples: list[dict[str, np.ndarray]],
    role_slot_map: dict[str, dict[str, str]],
    *,
    provenance: dict[str, Any] | None = None,
    min_similarity: float = 0.35,
    min_mean_similarity: float = 0.65,
    min_slot_margin: float = -0.15,
    min_team_margin: float = 0.04,
) -> dict[str, Any]:
    """Map pre-swap pick positions to Liquipedia heroes via final role positions."""
    assignments = {
        team: best_portrait_assignment(
            first_complete_samples, final_role_samples, team
        )
        for team in ("blue", "red")
    }
    reasons: list[str] = []
    heroes_by_pick: dict[tuple[str, int], str] = {}
    for team, assignment in assignments.items():
        configured = role_slot_map.get(team, {})
        hero_by_role_position = {
            configured.get(str(pick.get("slot"))): str(pick["hero"])
            for pick in raw_game.get(f"{team}_team", [])
            if pick.get("slot") is not None
            and pick.get("hero")
            and configured.get(str(pick.get("slot")))
        }
        expected_positions = {f"{team}_pick{index}" for index in range(1, 6)}
        if set(hero_by_role_position) != expected_positions:
            reasons.append(f"{team}_role_slot_map_incomplete")
            continue
        if not accepted_portrait_assignment(
            assignment,
            min_similarity=min_similarity,
            min_mean_similarity=min_mean_similarity,
            min_slot_margin=min_slot_margin,
            min_team_margin=min_team_margin,
        ):
            reasons.append(f"{team}_assignment_ambiguous")
            continue
        for team_pick_order in range(1, 6):
            pick_slot = f"{team}_pick{team_pick_order}"
            heroes_by_pick[(team, team_pick_order)] = hero_by_role_position[
                assignment["mapping"][pick_slot]
            ]

    picks = []
    if not reasons:
        for global_index, (team, team_pick_order, turn_index) in enumerate(
            PICK_SEQUENCE, start=1
        ):
            assignment = assignments[team]
            index = team_pick_order - 1
            picks.append(
                {
                    "global_pick_index": global_index,
                    "team": team,
                    "team_pick_order": team_pick_order,
                    "turn_index": turn_index,
                    "hero": heroes_by_pick[(team, team_pick_order)],
                    "slot": f"{team}_pick{team_pick_order}",
                    "confidence": round(
                        max(0.0, min(1.0, assignment["assigned_scores"][index])),
                        4,
                    ),
                    "assignment_margin": round(
                        assignment["row_margins"][index], 4
                    ),
                    "evidence_frame": None,
                    "evidence_frames": [],
                }
            )
    final_sets_match = all(
        {pick["hero"] for pick in picks if pick["team"] == team}
        == set(raw_game.get(f"{team}_picks", []))
        for team in ("blue", "red")
    )
    order_complete = len(picks) == len(PICK_SEQUENCE) and final_sets_match
    if not final_sets_match and picks:
        reasons.append("assigned_heroes_do_not_match_liquipedia")
        picks = []
        order_complete = False
    diagnostics = {
        team: (
            {
                "mapping": assignment["mapping"],
                "minimum_similarity": round(min(assignment["assigned_scores"]), 4),
                "minimum_mean_similarity": min_mean_similarity,
                "minimum_slot_margin": round(min(assignment["row_margins"]), 4),
                "team_assignment_margin": round(
                    assignment["team_assignment_margin"], 4
                ),
                "mean_similarity": round(assignment["mean_similarity"], 4),
            }
            if assignment
            else None
        )
        for team, assignment in assignments.items()
    }
    return role_remap_pick_order_result(
        game_id=raw_game["game_id"],
        order_complete=order_complete,
        confidence=round(
            float(np.mean([pick["confidence"] for pick in picks])) if picks else 0.0,
            4,
        ),
        notes="; ".join(reasons),
        extractor_version=EXTRACTOR_VERSION,
        assignment_diagnostics=diagnostics,
        picks=picks,
        provenance=provenance,
    )


def load_vod_manifest(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected VOD manifest object at {path}")
    games = payload.get("games")
    if not isinstance(games, list):
        raise ValueError(f"Expected VOD manifest games list at {path}")
    return payload


def find_manifest_entry(manifest: dict[str, Any], game_id: str) -> dict[str, Any]:
    for entry in manifest.get("games", []):
        if isinstance(entry, dict) and entry.get("game_id") == game_id:
            return entry
    raise KeyError(f"No VOD manifest entry found for {game_id}")


def final_pick_slot_hero_map(raw_game: dict[str, Any]) -> dict[str, str]:
    slot_map: dict[str, str] = {}
    for team in ("blue", "red"):
        for pick in raw_game.get(f"{team}_team", []):
            slot = pick.get("slot")
            hero_name = pick.get("hero")
            if slot and hero_name:
                slot_map[f"{team}_pick{slot}"] = str(hero_name)
    return slot_map


def expected_pick_slot(global_pick_index: int) -> str:
    team, team_pick_order, _turn_index = PICK_SEQUENCE[global_pick_index - 1]
    return f"{team}_pick{team_pick_order}"


def suggest_pick_order_from_slot_reveals(
    raw_game: dict[str, Any],
    slot_reveals: list[dict[str, Any]],
    min_confidence: float = 0.8,
) -> dict[str, Any]:
    slot_hero_map = final_pick_slot_hero_map(raw_game)
    warnings: list[str] = []
    usable_reveals: list[dict[str, Any]] = []
    seen_slots: set[str] = set()

    for reveal in sorted(
        slot_reveals,
        key=lambda item: (float(item.get("observed_at_sec", 0.0)), str(item.get("slot", ""))),
    ):
        slot = str(reveal.get("slot", ""))
        confidence = float(reveal.get("confidence", 0.0) or 0.0)
        if slot not in slot_hero_map:
            warnings.append(f"Ignored unknown pick slot {slot}.")
            continue
        if slot in seen_slots:
            warnings.append(f"Ignored duplicate reveal for slot {slot}.")
            continue
        if confidence < min_confidence:
            warnings.append(f"Ignored low-confidence reveal for slot {slot}.")
            continue
        seen_slots.add(slot)
        usable_reveals.append(reveal)

    if len(usable_reveals) != len(PICK_SEQUENCE):
        warnings.append(
            f"Expected {len(PICK_SEQUENCE)} confident slot reveals, found {len(usable_reveals)}."
        )

    picks: list[dict[str, Any]] = []
    for global_pick_index, reveal in enumerate(usable_reveals[: len(PICK_SEQUENCE)], start=1):
        expected_team, expected_team_pick_order, expected_turn_index = PICK_SEQUENCE[
            global_pick_index - 1
        ]
        slot = str(reveal["slot"])
        slot_team = slot.split("_", maxsplit=1)[0]
        if slot_team != expected_team:
            warnings.append(
                f"Reveal {global_pick_index} came from {slot}, but sequence expects {expected_team}."
            )
        picks.append(
            {
                "global_pick_index": global_pick_index,
                "team": expected_team,
                "team_pick_order": expected_team_pick_order,
                "turn_index": expected_turn_index,
                "hero": slot_hero_map[slot],
                "evidence_frame": reveal.get("frame_path"),
                "observed_at_sec": reveal.get("observed_at_sec"),
            }
        )

    confidence = (
        sum(float(reveal.get("confidence", 0.0) or 0.0) for reveal in usable_reveals)
        / len(usable_reveals)
        if usable_reveals
        else 0.0
    )
    return slot_reveal_pick_order_result(
        game_id=str(raw_game["game_id"]),
        confidence=round(confidence, 4),
        notes="; ".join(warnings),
        picks=picks,
    )


def _crop_mean_abs_diff(current_crop: np.ndarray, baseline_crop: np.ndarray) -> float:
    return float(np.mean(np.abs(current_crop.astype("float32") - baseline_crop.astype("float32"))))


def extract_revealed_slot_observations(
    frame_paths: list[Path],
    layout_slots: dict[str, list[int]],
    fps: float,
    reveal_threshold: float = 25.0,
) -> list[dict[str, Any]]:
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            "opencv-python-headless is required for VOD frame analysis. "
            "Install backend/requirements-vod.txt to use this helper."
        ) from exc

    sorted_frames = sorted(frame_paths)
    if not sorted_frames:
        return []

    baseline = cv2.imread(str(sorted_frames[0]), cv2.IMREAD_GRAYSCALE)
    if baseline is None:
        raise ValueError(f"Could not read baseline frame {sorted_frames[0]}")

    pick_slots = {
        slot: bounds
        for slot, bounds in layout_slots.items()
        if "_pick" in slot and len(bounds) == 4
    }
    if pick_slots and max(max(float(value) for value in bounds) for bounds in pick_slots.values()) <= 1.0:
        frame_height, frame_width = baseline.shape[:2]
        pick_slots = {
            slot: [
                round(float(bounds[0]) * frame_width),
                round(float(bounds[1]) * frame_height),
                max(1, round(float(bounds[2]) * frame_width)),
                max(1, round(float(bounds[3]) * frame_height)),
            ]
            for slot, bounds in pick_slots.items()
        }
    baseline_crops = {
        slot: baseline[y : y + height, x : x + width]
        for slot, (x, y, width, height) in pick_slots.items()
    }

    observations: list[dict[str, Any]] = []
    observed_slots: set[str] = set()
    for frame_index, frame_path in enumerate(sorted_frames[1:], start=1):
        frame = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            continue
        for slot, (x, y, width, height) in pick_slots.items():
            if slot in observed_slots:
                continue
            current_crop = frame[y : y + height, x : x + width]
            diff = _crop_mean_abs_diff(current_crop, baseline_crops[slot])
            if diff < reveal_threshold:
                continue
            observed_slots.add(slot)
            observations.append(
                {
                    "slot": slot,
                    "frame_path": str(frame_path),
                    "observed_at_sec": round(frame_index / max(float(fps), 0.001), 3),
                    "confidence": round(min(1.0, diff / (reveal_threshold * 2.0)), 4),
                }
            )

    return observations


def _cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 0.0:
        return 0.0
    return float(np.dot(left, right) / denominator)


def _hero_descriptor(image: np.ndarray) -> np.ndarray:
    cv2 = _load_cv2()
    resized = cv2.resize(image, (64, 64), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256]).flatten()
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    appearance = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA).flatten()
    descriptor = np.concatenate([histogram, appearance.astype("float32") / 255.0])
    norm = float(np.linalg.norm(descriptor))
    return descriptor / norm if norm > 0.0 else descriptor


def _load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            "opencv-python-headless is required for VOD frame analysis. "
            "Install backend/requirements-vod.txt to use this helper."
        ) from exc
    return cv2


def hero_reference_paths(
    hero_name: str,
    *,
    hero_icon_dir: Path = HERO_ICON_DIR,
    gallery_dir: Path = HERO_REFERENCE_GALLERY_DIR,
) -> list[Path]:
    references: list[Path] = []
    canonical_path = hero_icon_dir / f"{hero_name}.png"
    if canonical_path.exists():
        references.append(canonical_path)
    hero_gallery = gallery_dir / hero_name
    if hero_gallery.exists():
        references.extend(
            path
            for path in sorted(hero_gallery.iterdir())
            if path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp"}
        )
    return references


def recognize_hero_crop(
    crop: np.ndarray,
    candidate_heroes: list[str],
    *,
    hero_icon_dir: Path = HERO_ICON_DIR,
    gallery_dir: Path = HERO_REFERENCE_GALLERY_DIR,
    min_similarity: float = 0.72,
    min_margin: float = 0.03,
) -> dict[str, Any]:
    cv2 = _load_cv2()
    crop_descriptor = _hero_descriptor(crop)
    scores: list[tuple[float, str]] = []
    for hero_name in candidate_heroes:
        reference_scores: list[float] = []
        for path in hero_reference_paths(
            hero_name,
            hero_icon_dir=hero_icon_dir,
            gallery_dir=gallery_dir,
        ):
            reference = cv2.imread(str(path))
            if reference is not None:
                reference_scores.append(
                    _cosine_similarity(crop_descriptor, _hero_descriptor(reference))
                )
        if reference_scores:
            scores.append((max(reference_scores), hero_name))

    if not scores:
        return {"hero": None, "confidence": 0.0, "reason": "no_reference_images"}
    scores.sort(reverse=True)
    best_score, best_hero = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    margin = best_score - second_score
    if best_score < min_similarity or margin < min_margin:
        return {
            "hero": None,
            "confidence": round(max(0.0, best_score), 4),
            "reason": "low_similarity" if best_score < min_similarity else "ambiguous_identity",
            "best_candidate": best_hero,
            "margin": round(margin, 4),
        }
    return {
        "hero": best_hero,
        "confidence": round(min(1.0, best_score), 4),
        "margin": round(margin, 4),
    }


def extract_hero_identity_observations(
    *,
    frame_paths: list[Path],
    layout: dict[str, Any],
    raw_game: dict[str, Any],
    fps: float,
    start_sec: float = 0.0,
    hero_icon_dir: Path = HERO_ICON_DIR,
    gallery_dir: Path = HERO_REFERENCE_GALLERY_DIR,
) -> list[dict[str, Any]]:
    cv2 = _load_cv2()
    observations: list[dict[str, Any]] = []
    for frame_index, frame_path in enumerate(sorted(frame_paths)):
        frame = cv2.imread(str(frame_path))
        if frame is None:
            continue
        for slot, crop in crop_slots(frame, layout).items():
            if "_pick" not in slot:
                continue
            team = slot.split("_", maxsplit=1)[0]
            candidates = list(raw_game.get(f"{team}_picks", []))
            match = recognize_hero_crop(
                crop,
                candidates,
                hero_icon_dir=hero_icon_dir,
                gallery_dir=gallery_dir,
            )
            observations.append(
                {
                    "slot": slot,
                    "team": team,
                    "hero": match.get("hero"),
                    "best_candidate": match.get("best_candidate"),
                    "margin": match.get("margin"),
                    "confidence": float(match.get("confidence", 0.0)),
                    "reason": match.get("reason"),
                    "frame_path": str(frame_path),
                    "observed_at_sec": round(
                        float(start_sec) + frame_index / max(float(fps), 0.001),
                        3,
                    ),
                }
            )
    return observations


def stable_hero_events(
    observations: list[dict[str, Any]],
    *,
    minimum_observations: int = 3,
    max_gap_sec: float = 2.5,
) -> list[dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    emitted_identity: dict[str, str] = {}
    events: list[dict[str, Any]] = []
    for observation in sorted(
        observations,
        key=lambda item: (float(item.get("observed_at_sec", 0.0)), str(item.get("slot", ""))),
    ):
        slot = str(observation.get("slot", ""))
        hero = observation.get("hero")
        observed_at = float(observation.get("observed_at_sec", 0.0))
        if not slot or not hero:
            state.pop(slot, None)
            continue

        current = state.get(slot)
        if (
            current is None
            or current["hero"] != hero
            or observed_at - float(current["last_seen_sec"]) > max_gap_sec
        ):
            current = {
                "hero": str(hero),
                "count": 0,
                "first_seen_sec": observed_at,
                "last_seen_sec": observed_at,
                "confidences": [],
                "evidence_frames": [],
            }
            state[slot] = current

        current["count"] += 1
        current["last_seen_sec"] = observed_at
        current["confidences"].append(float(observation.get("confidence", 0.0)))
        frame_path = observation.get("frame_path")
        if frame_path and len(current["evidence_frames"]) < 3:
            current["evidence_frames"].append(str(frame_path))

        if current["count"] < minimum_observations or emitted_identity.get(slot) == hero:
            continue
        emitted_identity[slot] = str(hero)
        events.append(
            {
                "slot": slot,
                "team": str(observation.get("team") or slot.split("_", maxsplit=1)[0]),
                "hero": str(hero),
                "observed_at_sec": round(float(current["first_seen_sec"]), 3),
                "confidence": round(float(np.mean(current["confidences"])), 4),
                "evidence_frames": list(current["evidence_frames"]),
            }
        )
    return events


def first_team_pool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_by_team: dict[str, set[str]] = {"blue": set(), "red": set()}
    pool_events: list[dict[str, Any]] = []
    for event in sorted(
        events,
        key=lambda item: (float(item.get("observed_at_sec", 0.0)), str(item.get("slot", ""))),
    ):
        team = str(event.get("team", ""))
        hero = str(event.get("hero", ""))
        if team not in seen_by_team or not hero or hero in seen_by_team[team]:
            continue
        seen_by_team[team].add(hero)
        pool_events.append(event)
    return pool_events


def suggest_pick_order_from_hero_events(
    raw_game: dict[str, Any],
    hero_events: list[dict[str, Any]],
    *,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    pool_events = first_team_pool_events(hero_events)
    events_by_team = {
        team: [event for event in pool_events if event.get("team") == team]
        for team in ("blue", "red")
    }
    ambiguous_orders: set[tuple[str, int]] = set()
    for team, team_events in events_by_team.items():
        for index, (previous, current) in enumerate(
            zip(team_events, team_events[1:], strict=False),
            start=1,
        ):
            if abs(
                float(current.get("observed_at_sec", 0.0))
                - float(previous.get("observed_at_sec", 0.0))
            ) <= 1.0:
                ambiguous_orders.update({(team, index), (team, index + 1)})
                warnings.append(
                    f"{team.title()} picks {index} and {index + 1} appeared in the same sampling interval."
                )
    picks: list[dict[str, Any]] = []
    for global_pick_index, (team, team_pick_order, turn_index) in enumerate(
        PICK_SEQUENCE,
        start=1,
    ):
        team_events = events_by_team[team]
        if team_pick_order > len(team_events):
            warnings.append(f"Missing stable {team} pick {team_pick_order}.")
            continue
        event = team_events[team_pick_order - 1]
        hero_name = str(event["hero"])
        if hero_name not in raw_game.get(f"{team}_picks", []):
            warnings.append(f"Ignored {hero_name}; it is not in {team}'s final Liquipedia picks.")
            continue
        ambiguity_reason = event.get("ambiguity_reason")
        if (team, team_pick_order) in ambiguous_orders:
            ambiguity_reason = "same_sampling_interval_as_adjacent_team_pick"
        pick_confidence = float(event.get("confidence", 0.0))
        if ambiguity_reason:
            pick_confidence = min(pick_confidence, 0.49)
        picks.append(
            {
                "global_pick_index": global_pick_index,
                "team": team,
                "team_pick_order": team_pick_order,
                "turn_index": turn_index,
                "hero": hero_name,
                "slot": event.get("slot"),
                "evidence_frame": next(iter(event.get("evidence_frames", [])), None),
                "evidence_frames": list(event.get("evidence_frames", [])),
                "observed_at_sec": event.get("observed_at_sec"),
                "confidence": pick_confidence,
                "ambiguity_reason": ambiguity_reason,
            }
        )

    if len(picks) != len(PICK_SEQUENCE):
        warnings.append(f"Expected {len(PICK_SEQUENCE)} picks, found {len(picks)}.")
    confidence = (
        float(np.mean([float(pick["confidence"]) for pick in picks])) if picks else 0.0
    )
    return hero_event_pick_order_result(
        game_id=str(raw_game["game_id"]),
        confidence=round(confidence, 4),
        notes="; ".join(warnings),
        extractor_version=EXTRACTOR_VERSION,
        picks=picks,
        provenance=provenance,
    )
