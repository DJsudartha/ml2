"""Metadata-only holdout selection and review-only pick-order acceptance scoring."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from backend.services.data.liquipedia_vods import liquipedia_vod_entry
from backend.services.data.pick_order_annotations import validate_pick_order_payload
from backend.services.modeling.pick_constants import PICK_SEQUENCE

LAYOUT_SOURCES = {
    "m7_world_v1": {
        "source_id": "mlbb_esports",
        "default_layout_id": "m7_world_v1",
    },
    "mpl_id_v1": {
        "source_id": "mpl_indonesia",
        "default_layout_id": "mpl_id_v1",
    },
}
REQUIRED_GAMES_PER_LAYOUT = 30
MIN_COMPLETE_PER_LAYOUT = 18


def _layout_for_game(game: dict[str, Any]) -> str | None:
    name = "_".join(str(game.get(field) or "") for field in
                    ("pagename", "source_file")).replace("/", "_").casefold()
    if "m7_world_championship" in name and "knockout" in name:
        return "m7_world_v1"
    if "mpl_indonesia_season_18" in name:
        return "mpl_id_v1"
    return None


def _match_key(game: dict[str, Any]) -> tuple[str, str]:
    return (_layout_for_game(game) or "", str(game.get("liquipedia_match_id") or ""))


def select_holdout(
    raw_games: list[dict[str, Any]],
    development_ids: set[str],
    games_per_layout: int = REQUIRED_GAMES_PER_LAYOUT,
) -> dict[str, Any]:
    """Fix a selection without touching VOD media or any gallery images."""
    if games_per_layout < 1:
        raise ValueError("games_per_layout must be positive")
    by_id = {game["game_id"]: game for game in raw_games}
    missing_development = development_ids - by_id.keys()
    if missing_development:
        raise ValueError(f"Unknown development game IDs: {len(missing_development)}")
    blocked_matches = {_match_key(by_id[game_id]) for game_id in development_ids}
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        layout: defaultdict(list) for layout in LAYOUT_SOURCES
    }
    for game in raw_games:
        layout = _layout_for_game(game)
        if layout is None or _match_key(game) in blocked_matches:
            continue
        if not game.get("liquipedia_match_id"):
            continue
        if any(len(game.get(f"{team}_picks", [])) != 5 or
               len(set(game.get(f"{team}_picks", []))) != 5
               for team in ("blue", "red")):
            continue
        vod = liquipedia_vod_entry(game, LAYOUT_SOURCES[layout])
        if not vod or vod["status"] != "matched" or vod["source_kind"] != "per_game":
            continue
        grouped[layout][str(game["liquipedia_match_id"])].append({
            **vod,
            "layout_id": layout,
            "blue_picks": game["blue_picks"],
            "red_picks": game["red_picks"],
        })
    selected = []
    for layout, matches in grouped.items():
        ordered_matches = sorted(matches)
        for rows in matches.values():
            rows.sort(key=lambda row: row["game_id"])
        # Round robin avoids letting one series dominate the blind sample.
        depth = 0
        chosen = []
        while len(chosen) < games_per_layout:
            next_rows = [matches[match][depth] for match in ordered_matches
                         if depth < len(matches[match])]
            if not next_rows:
                break
            chosen.extend(next_rows[: games_per_layout - len(chosen)])
            depth += 1
        if len(chosen) < games_per_layout:
            raise ValueError(
                f"Only {len(chosen)} eligible match-disjoint {layout} games; "
                f"need {games_per_layout}"
            )
        selected.extend(chosen)
    # No review labels, crops, or manually supplied draft windows are seeded.
    games = [
        {key: row[key] for key in (
            "game_id", "layout_id", "liquipedia_match_id", "video_id", "vod_url",
            "source_id", "source_kind", "provenance", "availability",
            "blue_picks", "red_picks",
        )}
        for row in selected
    ]
    selection_id = hashlib.sha256(
        json.dumps([(row["game_id"], row["video_id"]) for row in games],
                   sort_keys=True).encode()
    ).hexdigest()
    return {
        "version": 1,
        "selection_id": selection_id,
        "purpose": "blind_match_disjoint_holdout",
        "rights_status": "unconfirmed",
        "games_per_layout": games_per_layout,
        "games": games,
    }


def _ordered_heroes(picks: list[dict[str, Any]]) -> list[str] | None:
    if not isinstance(picks, list) or len(picks) != len(PICK_SEQUENCE):
        return None
    by_index = {pick.get("global_pick_index"): pick for pick in picks
                if isinstance(pick, dict)}
    if len(by_index) != len(PICK_SEQUENCE):
        return None
    heroes = []
    for index, (team, order, turn) in enumerate(PICK_SEQUENCE, 1):
        pick = by_index.get(index)
        if (not pick or pick.get("team") != team or
            pick.get("team_pick_order") != order or pick.get("turn_index") != turn or
                not isinstance(pick.get("hero"), str)):
            return None
        heroes.append(pick["hero"])
    return heroes


def score_holdout(
    holdout: dict[str, Any], gold: dict[str, Any], suggestions: dict[str, Any]
) -> dict[str, Any]:
    """Score the full-route output, counting any malformed complete as incorrect."""
    games = holdout.get("games", [])
    ids = [game.get("game_id") for game in games]
    if holdout.get("version") != 1 or len(set(ids)) != len(ids):
        raise ValueError("Invalid or duplicate holdout game IDs")
    if gold.get("version") != 1 or not isinstance(gold.get("games"), list):
        raise ValueError("Gold must be confirmed annotation v1")
    if not isinstance(suggestions.get("games"), list):
        raise ValueError("Suggestions must contain games list")
    by_id = {game["game_id"]: game for game in games}
    validation = validate_pick_order_payload(gold, by_id)
    gold_by_id = {row.get("game_id"): row for row in gold["games"]
                  if isinstance(row, dict)}
    duplicate_suggestions = len({row.get("game_id") for row in suggestions["games"]
                                 if isinstance(row, dict)}) != len(suggestions["games"])
    suggestion_by_id = {row.get("game_id"): row for row in suggestions["games"]
                        if isinstance(row, dict)}
    layouts = {}
    all_gold = validation.is_valid and set(gold_by_id) == set(ids) and all(
        row.get("status") == "confirmed" for row in gold_by_id.values()
    )
    for layout in LAYOUT_SOURCES:
        layout_games = [game for game in games if game.get("layout_id") == layout]
        stats = {
            "selected": len(layout_games), "gold_confirmed": 0,
            "complete": 0, "correct_complete": 0, "incorrect_complete": 0,
            "abstained": 0, "layout_misclassified": 0,
            "layout_rejected": 0, "manual_or_legacy_route_rejected": 0,
            "draft_window_found": 0, "draft_window_correct": 0,
            "draft_window_gold_ranges": 0, "draft_window_recall": None,
            "direct_identity_correct": 0, "direct_identity_total": 0,
            "inferred_identity_correct": 0, "inferred_identity_total": 0,
            "exact_order_accuracy": None, "complete_coverage": None,
        }
        for game in layout_games:
            game_id = game["game_id"]
            reference = gold_by_id.get(game_id)
            row = suggestion_by_id.get(game_id, {})
            if reference and reference.get("status") == "confirmed":
                stats["gold_confirmed"] += 1
            provenance = row.get("provenance") or {}
            observed_layout = provenance.get("layout_id")
            if observed_layout is not None and observed_layout != layout:
                stats["layout_misclassified"] += 1
            try:
                start = float(reference["draft_start_sec"])
                end = float(reference["draft_end_sec"])
                if start > end:
                    raise ValueError("invalid range")
                stats["draft_window_gold_ranges"] += 1
                when = row.get("timestamp_sec")
                if when is not None:
                    stats["draft_window_found"] += 1
                    if start <= float(when) <= end:
                        stats["draft_window_correct"] += 1
            except (KeyError, TypeError, ValueError):
                pass
            actual = _ordered_heroes(reference.get("picks", [])) if reference else None
            if actual:
                observations = (
                    row.get("picks", []) if row.get("order_complete") else
                    [
                        {"slot": item.get("slot"), "hero": item.get("hero"),
                         "identity_sources": item.get("sources") or []}
                        for item in row.get("slot_suggestions", [])
                        if item.get("hero")
                    ]
                )
                gold_by_slot = {
                    f"{team}_pick{order}": actual[index - 1]
                    for index, (team, order, _) in enumerate(PICK_SEQUENCE, 1)
                }
                for pick in observations:
                    slot = pick.get("slot")
                    if slot not in gold_by_slot:
                        continue
                    source = "inferred" if "liquipedia_set_elimination" in (
                        pick.get("identity_sources") or []
                    ) else "direct"
                    stats[f"{source}_identity_total"] += 1
                    if pick.get("hero") == gold_by_slot[slot]:
                        stats[f"{source}_identity_correct"] += 1
            if not row.get("order_complete"):
                stats["abstained"] += 1
                if "unknown_or_unverified_layout" in (
                    row.get("ambiguity_reasons") or []
                ):
                    stats["layout_rejected"] += 1
                continue
            stats["complete"] += 1
            if observed_layout is None or not provenance.get("layout_validated"):
                stats["layout_misclassified"] += 1
            route_valid = (
                provenance.get("automatic_window") is True and
                provenance.get("video_match_verified") is True and
                not provenance.get("manual_window_supplied") and
                not provenance.get("video_reference_supplied") and
                provenance.get("capture_extractor_version") == "complete_draft_v10"
            )
            if not route_valid:
                stats["manual_or_legacy_route_rejected"] += 1
            proposed = _ordered_heroes(row.get("picks", []))
            if (actual == proposed and observed_layout == layout and
                    provenance.get("layout_validated") and route_valid):
                stats["correct_complete"] += 1
            else:
                stats["incorrect_complete"] += 1
        if layout_games:
            stats["complete_coverage"] = round(stats["complete"] / len(layout_games), 4)
            stats["draft_window_recall"] = round(
                stats["draft_window_correct"] / len(layout_games), 4
            ) if all_gold and stats["draft_window_gold_ranges"] == len(layout_games) else None
        if stats["complete"]:
            stats["exact_order_accuracy"] = round(
                stats["correct_complete"] / stats["complete"], 4
            ) if all_gold else None
        layouts[layout] = stats
    accuracy_passed = (
        all_gold and not duplicate_suggestions and
        len(games) == REQUIRED_GAMES_PER_LAYOUT * len(LAYOUT_SOURCES) and
        all(stats["selected"] == REQUIRED_GAMES_PER_LAYOUT and
            stats["complete"] >= MIN_COMPLETE_PER_LAYOUT and
            stats["incorrect_complete"] == 0 and
            stats["layout_misclassified"] == 0 and
            stats["manual_or_legacy_route_rejected"] == 0
            for stats in layouts.values())
    )
    return {
        "version": 1,
        "selection_id": holdout.get("selection_id"),
        "gate_passed": accuracy_passed,
        "weekly_ready": False,  # Separate rights and blind-label audit are required.
        "gold_valid": all_gold,
        "duplicate_suggestions": duplicate_suggestions,
        "validation_errors": [error.message for error in validation.errors],
        "layouts": layouts,
    }


def check_blind_label_audit(
    holdout: dict[str, Any], gold: dict[str, Any],
    suggestions: dict[str, Any], audit: dict[str, Any]
) -> list[str]:
    """Check blind first-pass labels and the delayed, seeded second pass."""
    errors = []
    games = holdout.get("games", [])
    if audit.get("version") != 1 or not isinstance(audit.get("games"), list):
        return ["audit_v1_games_required"]
    records = audit["games"]
    if len(records) != len(games) or len({row.get("game_id") for row in records}) != (
        len(games)
    ):
        errors.append("audit_must_cover_every_holdout_game_once")
    by_id = {row.get("game_id"): row for row in records}
    gold_by_id = {row.get("game_id"): row for row in gold.get("games", [])}
    suggested = {row.get("game_id"): row for row in suggestions.get("games", [])}
    clear = []
    risky = set()
    for game in games:
        game_id = game["game_id"]
        row = suggested.get(game_id, {})
        inferred = any(
            "liquipedia_set_elimination" in (pick.get("identity_sources") or [])
            for pick in row.get("picks", [])
        )
        if inferred or row.get("ambiguity_reasons") or not row.get("order_complete"):
            risky.add(game_id)
        else:
            clear.append(game_id)
    seeded = sorted(
        clear, key=lambda game_id: hashlib.sha256(
            f"{holdout.get('selection_id')}::{game_id}".encode()
        ).hexdigest()
    )[: math.ceil(len(clear) * 0.2)]
    for game in games:
        game_id = game["game_id"]
        record = by_id.get(game_id)
        gold_order = _ordered_heroes(gold_by_id.get(game_id, {}).get("picks", []))
        if not record or not record.get("blind") or gold_order is None:
            errors.append(f"{game_id}_blind_first_pass_or_gold_missing")
            continue
        first_order = record.get("first_order")
        if not isinstance(first_order, list) or len(first_order) != 10:
            errors.append(f"{game_id}_first_order_invalid")
            continue
        must_recheck = game_id in risky or game_id in seeded or first_order != gold_order
        try:
            first_time = datetime.fromisoformat(record["first_pass_at"])
            if first_time.tzinfo is None:
                raise ValueError("naive timestamp")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{game_id}_first_pass_timestamp_invalid")
            continue
        if must_recheck:
            try:
                second_time = datetime.fromisoformat(record["second_pass_at"])
                if second_time.tzinfo is None or (
                    second_time - first_time < timedelta(hours=48)
                ):
                    raise ValueError("second pass too early")
            except (KeyError, TypeError, ValueError):
                errors.append(f"{game_id}_second_pass_missing_or_too_early")
                continue
            second_order = record.get("second_order")
            if not isinstance(second_order, list) or len(second_order) != 10:
                errors.append(f"{game_id}_second_order_invalid")
                continue
            if first_order != second_order:
                if record.get("resolved_order") != gold_order:
                    errors.append(f"{game_id}_disagreement_not_adjudicated")
            elif second_order != gold_order:
                errors.append(f"{game_id}_gold_disagrees_with_review")
        elif first_order != gold_order:
            errors.append(f"{game_id}_gold_disagrees_with_first_pass")
    return errors


def check_vod_verification(holdout: dict[str, Any], verification: dict[str, Any],
                           channels: dict[str, str]) -> list[str]:
    if verification.get("version") != 1 or not isinstance(
        verification.get("games"), list
    ):
        return ["vod_verification_v1_games_required"]
    rows = verification["games"]
    if len(rows) != len(holdout.get("games", [])) or len({
        row.get("game_id") for row in rows
    }) != len(rows):
        return ["vod_verification_must_cover_every_holdout_game_once"]
    by_id = {row.get("game_id"): row for row in rows}
    errors = []
    for game in holdout.get("games", []):
        row = by_id.get(game["game_id"], {})
        mode = row.get("verification_mode")
        manual_ok = mode == "manual" and row.get("reviewer") and row.get(
            "verification_evidence"
        )
        if not (row.get("verified") and (mode == "live" or manual_ok)
                and row.get("video_id") == game.get("video_id")
                and row.get("channel_id") == channels.get(game.get("source_id"))
                and row.get("source_kind") == "per_game"):
            errors.append(f"{game['game_id']}_official_game_vod_unverified")
    return errors
