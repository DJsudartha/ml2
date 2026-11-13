from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from backend.services.common.file_utils import load_json, save_json
from backend.services.data.raw_games import (
    RAW_TOURNAMENTS_DIR,
    load_raw_games,
    load_raw_games_by_id,
)
from backend.services.modeling.pick_constants import FIRST_PICK_PHASE_TURNS, PICK_SEQUENCE

PICK_ORDER_ANNOTATIONS_PATH = Path(
    "backend/data/raw/pick_order_annotations/pick_order_annotations.json"
)

CONFIRMED_STATUS = "confirmed"
NEEDS_REVIEW_STATUS = "needs_review"
ALLOWED_STATUSES = {CONFIRMED_STATUS, NEEDS_REVIEW_STATUS}


@dataclass(frozen=True)
class PickOrderValidationError:
    game_id: str | None
    message: str


@dataclass(frozen=True)
class PickOrderValidationReport:
    checked_games: int
    confirmed_games: int
    errors: tuple[PickOrderValidationError, ...]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def empty_annotation_payload() -> dict[str, Any]:
    return {"version": 1, "games": []}


def load_pick_order_annotations(
    path: Path = PICK_ORDER_ANNOTATIONS_PATH,
) -> dict[str, Any]:
    payload = load_json(path)
    if not payload:
        return empty_annotation_payload()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected pick-order annotation object at {path}")
    return payload


def build_annotation_template(
    raw_dir: Path = RAW_TOURNAMENTS_DIR,
) -> dict[str, Any]:
    games = []
    for game_row in load_raw_games(raw_dir):
        games.append(
            {
                "game_id": game_row["game_id"],
                "source": "manual_review",
                "status": NEEDS_REVIEW_STATUS,
                "confidence": 0.0,
                "notes": "",
                "final_picks": {
                    "blue": game_row["blue_picks"],
                    "red": game_row["red_picks"],
                },
                "picks": [],
            }
        )
    return {"version": 1, "games": games}


def save_annotation_template(output_path: Path, raw_dir: Path = RAW_TOURNAMENTS_DIR) -> None:
    save_json(output_path, build_annotation_template(raw_dir))


def _append_error(
    errors: list[PickOrderValidationError],
    game_id: str | None,
    message: str,
) -> None:
    errors.append(PickOrderValidationError(game_id=game_id, message=message))


def _extract_pick_heroes(picks: list[dict[str, Any]], team: str) -> list[str]:
    return [str(pick.get("hero")) for pick in picks if pick.get("team") == team and pick.get("hero")]


def _validate_confirmed_picks(
    annotation_game: dict[str, Any],
    raw_game: dict[str, Any],
    errors: list[PickOrderValidationError],
) -> None:
    game_id = str(annotation_game.get("game_id"))
    picks = annotation_game.get("picks")
    if not isinstance(picks, list):
        _append_error(errors, game_id, "Confirmed annotation must include a picks list.")
        return

    if len(picks) != len(PICK_SEQUENCE):
        _append_error(errors, game_id, f"Confirmed annotation must include {len(PICK_SEQUENCE)} picks.")

    seen_heroes: set[str] = set()
    sorted_picks = sorted(
        [pick for pick in picks if isinstance(pick, dict)],
        key=lambda pick: int(pick.get("global_pick_index") or 0),
    )

    for expected_global_index, pick in enumerate(sorted_picks, start=1):
        if expected_global_index > len(PICK_SEQUENCE):
            _append_error(errors, game_id, "Confirmed annotation includes extra picks.")
            continue

        expected_team, expected_team_pick_order, expected_turn_index = PICK_SEQUENCE[
            expected_global_index - 1
        ]
        hero_name = pick.get("hero")
        if not isinstance(hero_name, str) or not hero_name:
            _append_error(errors, game_id, f"Pick {expected_global_index} is missing a hero.")
            continue

        if hero_name in seen_heroes:
            _append_error(errors, game_id, f"Duplicate picked hero: {hero_name}.")
        seen_heroes.add(hero_name)

        if pick.get("global_pick_index") != expected_global_index:
            _append_error(
                errors,
                game_id,
                f"Pick {expected_global_index} has the wrong global_pick_index.",
            )
        if pick.get("team") != expected_team:
            _append_error(errors, game_id, f"Pick {expected_global_index} has the wrong team.")
        if pick.get("team_pick_order") != expected_team_pick_order:
            _append_error(
                errors,
                game_id,
                f"Pick {expected_global_index} has the wrong team_pick_order.",
            )
        if pick.get("turn_index") != expected_turn_index:
            _append_error(errors, game_id, f"Pick {expected_global_index} has the wrong turn_index.")

        allowed_heroes = raw_game["blue_picks"] if expected_team == "blue" else raw_game["red_picks"]
        if hero_name not in allowed_heroes:
            _append_error(
                errors,
                game_id,
                f"Pick {expected_global_index} hero {hero_name} is not in {expected_team}'s final picks.",
            )

    blue_annotation_picks = _extract_pick_heroes(sorted_picks, "blue")
    red_annotation_picks = _extract_pick_heroes(sorted_picks, "red")
    if set(blue_annotation_picks) != set(raw_game["blue_picks"]):
        _append_error(errors, game_id, "Confirmed annotation does not match final blue picks.")
    if set(red_annotation_picks) != set(raw_game["red_picks"]):
        _append_error(errors, game_id, "Confirmed annotation does not match final red picks.")


def validate_pick_order_payload(
    payload: dict[str, Any],
    raw_games_by_id: dict[str, dict[str, Any]],
) -> PickOrderValidationReport:
    errors: list[PickOrderValidationError] = []
    if payload.get("version") != 1:
        _append_error(errors, None, "Pick-order annotation payload must use version 1.")

    games = payload.get("games")
    if not isinstance(games, list):
        _append_error(errors, None, "Pick-order annotation payload must include a games list.")
        return PickOrderValidationReport(checked_games=0, confirmed_games=0, errors=tuple(errors))

    seen_game_ids: set[str] = set()
    confirmed_games = 0
    for annotation_game in games:
        if not isinstance(annotation_game, dict):
            _append_error(errors, None, "Each annotation game must be an object.")
            continue

        game_id = annotation_game.get("game_id")
        if not isinstance(game_id, str) or not game_id:
            _append_error(errors, None, "Each annotation game must include a game_id.")
            continue

        if game_id in seen_game_ids:
            _append_error(errors, game_id, "Duplicate annotation game_id.")
        seen_game_ids.add(game_id)

        raw_game = raw_games_by_id.get(game_id)
        if raw_game is None:
            _append_error(errors, game_id, "Annotation references an unknown raw game.")
            continue

        status = annotation_game.get("status")
        if status not in ALLOWED_STATUSES:
            _append_error(errors, game_id, f"Annotation status must be one of {sorted(ALLOWED_STATUSES)}.")
            continue

        if status == CONFIRMED_STATUS:
            confirmed_games += 1
            _validate_confirmed_picks(annotation_game, raw_game, errors)

    return PickOrderValidationReport(
        checked_games=len(games),
        confirmed_games=confirmed_games,
        errors=tuple(errors),
    )


def validate_pick_order_annotations(
    annotations_path: Path = PICK_ORDER_ANNOTATIONS_PATH,
    raw_dir: Path = RAW_TOURNAMENTS_DIR,
) -> PickOrderValidationReport:
    return validate_pick_order_payload(
        payload=load_pick_order_annotations(annotations_path),
        raw_games_by_id=load_raw_games_by_id(raw_dir),
    )


def _confirmed_annotation_games(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    games = payload.get("games", [])
    if not isinstance(games, list):
        return
    for annotation_game in games:
        if isinstance(annotation_game, dict) and annotation_game.get("status") == CONFIRMED_STATUS:
            yield annotation_game


def iter_confirmed_pick_order_states(
    annotations_path: Path = PICK_ORDER_ANNOTATIONS_PATH,
    raw_dir: Path = RAW_TOURNAMENTS_DIR,
) -> Iterator[dict[str, Any]]:
    payload = load_pick_order_annotations(annotations_path)
    raw_games_by_id = load_raw_games_by_id(raw_dir)
    report = validate_pick_order_payload(payload, raw_games_by_id)
    if not report.is_valid:
        first_error = report.errors[0]
        raise ValueError(f"Invalid pick-order annotations for {first_error.game_id}: {first_error.message}")

    for annotation_game in _confirmed_annotation_games(payload):
        game_id = str(annotation_game["game_id"])
        raw_game = raw_games_by_id[game_id]
        prior_blue_picks: list[str] = []
        prior_red_picks: list[str] = []
        sorted_picks = sorted(
            annotation_game["picks"],
            key=lambda pick: int(pick["global_pick_index"]),
        )

        for pick in sorted_picks:
            team = str(pick["team"])
            phase_index = 1 if int(pick["global_pick_index"]) <= FIRST_PICK_PHASE_TURNS else 2
            blue_bans = raw_game["blue_bans"][:3] if phase_index == 1 else raw_game["blue_bans"]
            red_bans = raw_game["red_bans"][:3] if phase_index == 1 else raw_game["red_bans"]
            our_picks = prior_blue_picks if team == "blue" else prior_red_picks
            enemy_picks = prior_red_picks if team == "blue" else prior_blue_picks

            yield {
                "game_id": game_id,
                "date": raw_game["date"],
                "patch": raw_game["patch"],
                "tournament": raw_game["tournament"],
                "source_file": raw_game["source_file"],
                "team": team,
                "pick_order": int(pick["team_pick_order"]),
                "turn_index": int(pick["turn_index"]),
                "global_pick_index": int(pick["global_pick_index"]),
                "phase_index": phase_index,
                "actual_pick": str(pick["hero"]),
                "our_picks": list(our_picks),
                "enemy_picks": list(enemy_picks),
                "prior_blue_picks": list(prior_blue_picks),
                "prior_red_picks": list(prior_red_picks),
                "blue_bans": list(blue_bans),
                "red_bans": list(red_bans),
                "annotation_source": annotation_game.get("source"),
                "annotation_confidence": float(annotation_game.get("confidence", 0.0) or 0.0),
            }

            if team == "blue":
                prior_blue_picks.append(str(pick["hero"]))
            else:
                prior_red_picks.append(str(pick["hero"]))
