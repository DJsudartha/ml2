from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from backend.services.common.file_utils import load_json
from backend.services.data.pick_order_annotations import (
    PICK_ORDER_ANNOTATIONS_PATH,
    iter_confirmed_pick_order_states,
)
from backend.services.data.raw_games import RAW_TOURNAMENTS_DIR, iter_raw_games
from backend.services.modeling.ban_constants import BAN_SEQUENCE
from backend.services.modeling.feature_engineering_profile import (
    FeatureEngineeringProfile,
    load_feature_engineering_profile,
)
from backend.services.modeling.features import (
    PROCESSED_STATS_PATH,
    build_ban_candidate_feature_row,
    build_hero_feature_table,
    build_pick_candidate_feature_row,
    infer_missing_roles,
)
from backend.services.modeling.pick_signal_model import ALL_SIGNAL_COLUMNS, build_pick_signal_frame


def _iter_games(raw_dir: Path = RAW_TOURNAMENTS_DIR) -> Iterator[dict[str, Any]]:
    yield from iter_raw_games(raw_dir)


def build_ban_dataset(
    processed_stats_path: Path = PROCESSED_STATS_PATH,
    raw_dir: Path = RAW_TOURNAMENTS_DIR,
    feature_profile: FeatureEngineeringProfile | None = None,
) -> dict[str, Any]:
    resolved_feature_profile = feature_profile or load_feature_engineering_profile()
    hero_table = build_hero_feature_table(processed_stats_path, feature_profile=resolved_feature_profile)
    all_heroes = sorted(hero_table["heroes"].keys())

    rows: list[dict[str, Any]] = []
    for game_row in _iter_games(raw_dir):
        blue_ban_map = {index + 1: hero for index, hero in enumerate(game_row["blue_bans"])}
        red_ban_map = {index + 1: hero for index, hero in enumerate(game_row["red_bans"])}

        prior_blue_bans: list[str] = []
        prior_red_bans: list[str] = []

        for acting_team, ban_order, turn_index in BAN_SEQUENCE:
            actual_ban = blue_ban_map.get(ban_order) if acting_team == "blue" else red_ban_map.get(ban_order)
            if actual_ban is None:
                continue

            game_id = game_row["game_id"]
            query_id = f"{game_id}::{acting_team}::ban{ban_order}"
            unavailable_heroes = set(prior_blue_bans) | set(prior_red_bans)

            for candidate_hero in all_heroes:
                if candidate_hero in unavailable_heroes:
                    continue

                feature_row = build_ban_candidate_feature_row(
                    candidate_hero=candidate_hero,
                    acting_team=acting_team,
                    ban_order=ban_order,
                    prior_blue_bans=prior_blue_bans,
                    prior_red_bans=prior_red_bans,
                    hero_table=hero_table,
                )
                rows.append(
                    {
                        "query_id": query_id,
                        "game_id": game_id,
                        "date": game_row["date"],
                        "patch": game_row["patch"],
                        "tournament": game_row["tournament"],
                        "source_file": game_row["source_file"],
                        "team": acting_team,
                        "ban_order": ban_order,
                        "turn_index": turn_index,
                        "actual_ban": actual_ban,
                        "candidate_hero": candidate_hero,
                        "label_is_ban": 1 if candidate_hero == actual_ban else 0,
                        **feature_row,
                    }
                )

            if acting_team == "blue":
                prior_blue_bans.append(actual_ban)
            else:
                prior_red_bans.append(actual_ban)

    return {
        "metadata": {
            "row_count": len(rows),
            "model_target": "label_is_ban",
            "source_processed_stats": str(processed_stats_path),
            "source_raw_dir": str(raw_dir),
            "feature_engineering_profile": {
                "adjusted_win_rate_smoothing_games": int(
                    resolved_feature_profile["adjusted_win_rate_smoothing_games"]
                ),
                "flexibility_role_threshold": float(resolved_feature_profile["flexibility_role_threshold"]),
                "pair_prior_games": int(resolved_feature_profile["pair_prior_games"]),
            },
            "note": (
                "Ban dataset is order-sensitive on real ban slots and prior bans. "
                "It does not include pick-context features because pick order is unavailable."
            ),
        },
        "rows": rows,
    }


def build_pick_fit_dataset(
    processed_stats_path: Path = PROCESSED_STATS_PATH,
    raw_dir: Path = RAW_TOURNAMENTS_DIR,
    signals_only: bool = False,
    feature_profile: FeatureEngineeringProfile | None = None,
) -> dict[str, Any]:
    resolved_feature_profile = feature_profile or load_feature_engineering_profile()
    hero_table = build_hero_feature_table(processed_stats_path, feature_profile=resolved_feature_profile)
    processed_stats = load_json(processed_stats_path)
    if not isinstance(processed_stats, dict):
        raise ValueError(f"Expected processed hero stats dict at {processed_stats_path}")

    all_heroes = sorted(hero_table["heroes"].keys())
    rows: list[dict[str, Any]] = []

    for game_row in _iter_games(raw_dir):
        blue_bans = list(dict.fromkeys(game_row["blue_bans"]))
        red_bans = list(dict.fromkeys(game_row["red_bans"]))

        for acting_team, team_picks, enemy_picks in (
            ("blue", game_row["blue_picks"], game_row["red_picks"]),
            ("red", game_row["red_picks"], game_row["blue_picks"]),
        ):
            unique_team_picks = list(dict.fromkeys(team_picks))
            unique_enemy_picks = list(dict.fromkeys(enemy_picks))

            precomputed_enemy_missing_roles = infer_missing_roles(unique_enemy_picks, hero_table) if unique_enemy_picks else []

            for slot_index, actual_pick in enumerate(unique_team_picks, start=1):
                our_picks = [hero_name for hero_name in unique_team_picks if hero_name != actual_pick]
                precomputed_our_missing_roles = infer_missing_roles(our_picks, hero_table) if our_picks else []
                unavailable = set(our_picks) | set(unique_enemy_picks) | set(blue_bans) | set(red_bans)
                game_id = game_row["game_id"]
                query_id = f"{game_id}::{acting_team}::pick_fit::{slot_index}::{actual_pick}"

                for candidate_hero in all_heroes:
                    if candidate_hero in unavailable and candidate_hero != actual_pick:
                        continue

                    feature_row = build_pick_candidate_feature_row(
                        candidate_hero=candidate_hero,
                        acting_team=acting_team,
                        pick_order=len(our_picks) + 1,
                        phase_index=2,
                        our_picks=our_picks,
                        enemy_picks=unique_enemy_picks,
                        blue_bans=blue_bans,
                        red_bans=red_bans,
                        hero_table=hero_table,
                        complete_stats=processed_stats,
                        feature_profile=resolved_feature_profile,
                        our_missing_roles=precomputed_our_missing_roles,
                        enemy_missing_roles=precomputed_enemy_missing_roles,
                    )
                    rows.append(
                        {
                            "query_id": query_id,
                            "game_id": game_id,
                            "date": game_row["date"],
                            "patch": game_row["patch"],
                            "tournament": game_row["tournament"],
                            "source_file": game_row["source_file"],
                            "team": acting_team,
                            "slot_index": slot_index,
                            "actual_pick": actual_pick,
                            "candidate_hero": candidate_hero,
                            "label_is_pick_fit": 1 if candidate_hero == actual_pick else 0,
                            **feature_row,
                        }
                    )

    if signals_only and rows:
        raw_frame = pd.DataFrame(rows)
        signal_frame = build_pick_signal_frame(raw_frame, query_column="query_id")
        base_columns = [
            "query_id",
            "game_id",
            "date",
            "patch",
            "tournament",
            "source_file",
            "team",
            "slot_index",
            "actual_pick",
            "candidate_hero",
            "label_is_pick_fit",
        ]
        rows = signal_frame[base_columns + list(ALL_SIGNAL_COLUMNS)].to_dict(orient="records")

    return {
        "metadata": {
            "row_count": len(rows),
            "model_target": "label_is_pick_fit",
            "source_processed_stats": str(processed_stats_path),
            "source_raw_dir": str(raw_dir),
            "feature_engineering_profile": {
                "adjusted_win_rate_smoothing_games": int(
                    resolved_feature_profile["adjusted_win_rate_smoothing_games"]
                ),
                "flexibility_role_threshold": float(resolved_feature_profile["flexibility_role_threshold"]),
                "pair_prior_games": int(resolved_feature_profile["pair_prior_games"]),
            },
            "note": (
                "Pick-fit dataset is order-agnostic. Each query removes one actual picked hero from the final team "
                "and asks which legal hero best completes the remaining four-hero allied core into the full enemy draft. "
                + (
                    "Only compact pick signals are stored."
                    if signals_only
                    else "Full pick candidate features are stored."
                )
            ),
        },
        "rows": rows,
    }


def build_ordered_pick_dataset(
    processed_stats_path: Path = PROCESSED_STATS_PATH,
    raw_dir: Path = RAW_TOURNAMENTS_DIR,
    annotations_path: Path = PICK_ORDER_ANNOTATIONS_PATH,
    signals_only: bool = False,
    feature_profile: FeatureEngineeringProfile | None = None,
) -> dict[str, Any]:
    resolved_feature_profile = feature_profile or load_feature_engineering_profile()
    hero_table = build_hero_feature_table(processed_stats_path, feature_profile=resolved_feature_profile)
    processed_stats = load_json(processed_stats_path)
    if not isinstance(processed_stats, dict):
        raise ValueError(f"Expected processed hero stats dict at {processed_stats_path}")

    all_heroes = sorted(hero_table["heroes"].keys())
    rows: list[dict[str, Any]] = []

    for state in iter_confirmed_pick_order_states(
        annotations_path=annotations_path,
        raw_dir=raw_dir,
    ):
        unavailable = (
            set(state["prior_blue_picks"])
            | set(state["prior_red_picks"])
            | set(state["blue_bans"])
            | set(state["red_bans"])
        )
        query_id = (
            f"{state['game_id']}::{state['team']}::ordered_pick::"
            f"{state['global_pick_index']}::{state['actual_pick']}"
        )
        precomputed_our_missing_roles = (
            infer_missing_roles(state["our_picks"], hero_table) if state["our_picks"] else []
        )
        precomputed_enemy_missing_roles = (
            infer_missing_roles(state["enemy_picks"], hero_table) if state["enemy_picks"] else []
        )

        for candidate_hero in all_heroes:
            if candidate_hero in unavailable:
                continue

            feature_row = build_pick_candidate_feature_row(
                candidate_hero=candidate_hero,
                acting_team=state["team"],
                pick_order=state["pick_order"],
                phase_index=state["phase_index"],
                our_picks=state["our_picks"],
                enemy_picks=state["enemy_picks"],
                blue_bans=state["blue_bans"],
                red_bans=state["red_bans"],
                hero_table=hero_table,
                complete_stats=processed_stats,
                feature_profile=resolved_feature_profile,
                our_missing_roles=precomputed_our_missing_roles,
                enemy_missing_roles=precomputed_enemy_missing_roles,
            )
            rows.append(
                {
                    "query_id": query_id,
                    "game_id": state["game_id"],
                    "date": state["date"],
                    "patch": state["patch"],
                    "tournament": state["tournament"],
                    "source_file": state["source_file"],
                    "team": state["team"],
                    "pick_order": state["pick_order"],
                    "turn_index": state["turn_index"],
                    "global_pick_index": state["global_pick_index"],
                    "phase_index": state["phase_index"],
                    "actual_pick": state["actual_pick"],
                    "candidate_hero": candidate_hero,
                    "label_is_ordered_pick": 1 if candidate_hero == state["actual_pick"] else 0,
                    "annotation_source": state["annotation_source"],
                    "annotation_confidence": state["annotation_confidence"],
                    **feature_row,
                }
            )

    if signals_only and rows:
        raw_frame = pd.DataFrame(rows)
        signal_frame = build_pick_signal_frame(raw_frame, query_column="query_id")
        base_columns = [
            "query_id",
            "game_id",
            "date",
            "patch",
            "tournament",
            "source_file",
            "team",
            "pick_order",
            "turn_index",
            "global_pick_index",
            "phase_index",
            "actual_pick",
            "candidate_hero",
            "label_is_ordered_pick",
            "annotation_source",
            "annotation_confidence",
        ]
        rows = signal_frame[base_columns + list(ALL_SIGNAL_COLUMNS)].to_dict(orient="records")

    return {
        "metadata": {
            "row_count": len(rows),
            "model_target": "label_is_ordered_pick",
            "source_processed_stats": str(processed_stats_path),
            "source_raw_dir": str(raw_dir),
            "source_annotations": str(annotations_path),
            "feature_engineering_profile": {
                "adjusted_win_rate_smoothing_games": int(
                    resolved_feature_profile["adjusted_win_rate_smoothing_games"]
                ),
                "flexibility_role_threshold": float(resolved_feature_profile["flexibility_role_threshold"]),
                "pair_prior_games": int(resolved_feature_profile["pair_prior_games"]),
            },
            "note": (
                "Ordered pick dataset uses only confirmed pick-order annotations. "
                "Each query is built from prior picks and the bans available at that draft phase."
            ),
        },
        "rows": rows,
    }
