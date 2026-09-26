"""Canonical constructors for review-only pick-order result dictionaries.

Callers provide domain evidence; this module owns the serialized result shape so
capture, weekly collection, and suggestion paths cannot silently drift apart.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


COMPLETE_DRAFT_EXTRACTOR_VERSION = "complete_draft_v15"


PickOrderSource = Literal[
    "vod_complete_frame_assignment",
    "vod_slot_reveal",
    "vod_hero_identity",
]
PickOrderStatus = Literal["needs_review", "failed"]
AssignmentMethod = Literal["per_game_hero_evidence", "same_vod_role_remap"]


class PickOrderResult(TypedDict, total=False):
    game_id: str
    video_id: str
    source: PickOrderSource
    status: PickOrderStatus
    identity_complete: bool
    slot_order_validated: bool
    order_complete: bool
    confidence: float
    notes: str
    extractor_version: str
    assignment_method: AssignmentMethod
    ambiguity_reasons: list[str]
    advisory_reasons: list[str]
    lock_events: list[dict[str, Any]]
    slot_suggestions: list[dict[str, Any]]
    proposed_picks: list[dict[str, Any]]
    picks: list[dict[str, Any]]
    provenance: dict[str, Any]
    assignment_diagnostics: dict[str, Any]
    detection_diagnostics: dict[str, Any] | None
    identity_warnings: list[str]
    review_crops: list[dict[str, Any]]
    review_sequences: list[dict[str, Any]]
    contact_sheet: dict[str, Any]
    frame: str
    source_fps: float
    reason: str


def _attach_provenance(
    result: PickOrderResult,
    provenance: dict[str, Any] | None,
    extractor_version: str,
) -> PickOrderResult:
    if provenance:
        result["provenance"] = {"extractor_version": extractor_version, **provenance}
    return result


def evidence_pick_order_result(
    *,
    game_id: str,
    identity_complete: bool,
    slot_order_validated: bool,
    order_complete: bool,
    confidence: float,
    notes: str,
    ambiguity_reasons: list[str],
    advisory_reasons: list[str],
    lock_events: list[dict[str, Any]],
    extractor_version: str,
    slot_suggestions: list[dict[str, Any]],
    proposed_picks: list[dict[str, Any]],
    picks: list[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
) -> PickOrderResult:
    result: PickOrderResult = {
        "game_id": game_id,
        "source": "vod_complete_frame_assignment",
        "status": "needs_review",
        "identity_complete": identity_complete,
        "slot_order_validated": slot_order_validated,
        "order_complete": order_complete,
        "confidence": confidence,
        "notes": notes,
        "ambiguity_reasons": ambiguity_reasons,
        "advisory_reasons": advisory_reasons,
        "lock_events": lock_events,
        "extractor_version": extractor_version,
        "assignment_method": "per_game_hero_evidence",
        "slot_suggestions": slot_suggestions,
        "proposed_picks": proposed_picks,
        "picks": picks,
    }
    return _attach_provenance(result, provenance, extractor_version)


def role_remap_pick_order_result(
    *,
    game_id: str,
    order_complete: bool,
    confidence: float,
    notes: str,
    extractor_version: str,
    assignment_diagnostics: dict[str, Any],
    picks: list[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
) -> PickOrderResult:
    result: PickOrderResult = {
        "game_id": game_id,
        "source": "vod_complete_frame_assignment",
        "status": "needs_review",
        "order_complete": order_complete,
        "confidence": confidence,
        "notes": notes,
        "extractor_version": extractor_version,
        "assignment_method": "same_vod_role_remap",
        "assignment_diagnostics": assignment_diagnostics,
        "picks": picks,
    }
    return _attach_provenance(result, provenance, extractor_version)


def slot_reveal_pick_order_result(
    *,
    game_id: str,
    confidence: float,
    notes: str,
    picks: list[dict[str, Any]],
) -> PickOrderResult:
    return {
        "game_id": game_id,
        "source": "vod_slot_reveal",
        "status": "needs_review",
        "confidence": confidence,
        "notes": notes,
        "picks": picks,
    }


def hero_event_pick_order_result(
    *,
    game_id: str,
    confidence: float,
    notes: str,
    extractor_version: str,
    picks: list[dict[str, Any]],
    provenance: dict[str, Any] | None = None,
) -> PickOrderResult:
    result: PickOrderResult = {
        "game_id": game_id,
        "source": "vod_hero_identity",
        "status": "needs_review",
        "confidence": confidence,
        "notes": notes,
        "extractor_version": extractor_version,
        "picks": picks,
    }
    return _attach_provenance(result, provenance, extractor_version)


def incomplete_pick_order_result(
    *,
    game_id: str,
    reason: str,
    extractor_version: str,
    completion: dict[str, Any] | None = None,
    detection_diagnostics: dict[str, Any] | None = None,
) -> PickOrderResult:
    return {
        **(completion or {}),
        "game_id": game_id,
        "source": "vod_complete_frame_assignment",
        "status": "needs_review",
        "identity_complete": False,
        "slot_order_validated": False,
        "order_complete": False,
        "confidence": 0.0,
        "notes": reason,
        "extractor_version": extractor_version,
        "assignment_method": "per_game_hero_evidence",
        "ambiguity_reasons": [reason],
        "advisory_reasons": [],
        "detection_diagnostics": detection_diagnostics,
        "slot_suggestions": [],
        "proposed_picks": [],
        "picks": [],
    }


def failed_pick_order_result(
    *, game_id: str, reason: str, video_id: str | None = None
) -> PickOrderResult:
    result: PickOrderResult = {
        "game_id": game_id,
        "status": "failed",
        "order_complete": False,
        "reason": reason,
    }
    if video_id is not None:
        result["video_id"] = video_id
    return result
