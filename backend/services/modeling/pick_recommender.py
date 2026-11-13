from __future__ import annotations

import argparse
from functools import lru_cache
import json
from pathlib import Path
import sys
from typing import Any

import pandas as pd
from xgboost import XGBRanker

from backend.services.common.file_utils import load_json
from backend.services.modeling.features import (
    PROCESSED_STATS_PATH,
    build_hero_feature_table,
    build_pick_candidate_feature_row,
    infer_missing_roles,
)
from backend.services.modeling.pick_constants import (
    FIRST_PICK_PHASE_REQUIRED_BANS,
    FIRST_PICK_PHASE_TURNS,
    PICK_SEQUENCE,
    SECOND_PICK_PHASE_REQUIRED_BANS,
)
from backend.services.modeling.pick_order_profiles import (
    PickOrderProfile,
    resolve_pick_order_profile,
    score_pick_order_profile,
    weighted_signal_average_for_profile,
)
from backend.services.modeling.pick_signal_model import (
    build_pick_signal_frame,
    pick_signal_prior_score,
)

PICK_TRAINING_CONTEXT = {
    "target": "order-agnostic-pick-fit",
    "uses_confirmed_pick_order": False,
    "limitation": (
        "The current ranker was trained on final lineup fit, not confirmed sequential pick order. "
        "Treat its output as draft-fit guidance until an ordered model is trained from confirmed annotations."
    ),
}

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

PROCESSED_STATS_ABS_PATH = ROOT_DIR / PROCESSED_STATS_PATH
MODEL_DIR = ROOT_DIR / "backend/data/modeling/models"
RANKER_REPORT_PATH = MODEL_DIR / "pick_ranker_report.json"
GLOBAL_RANKER_PATH = MODEL_DIR / "pick_xgb_ranker_global.json"


def _normalize_team(team: str) -> str:
    team_name = team.strip().lower()
    if team_name not in {"blue", "red"}:
        raise ValueError("Team must be 'blue' or 'red'")
    return team_name


def _as_unique_heroes(hero_names: list[str] | None) -> list[str]:
    unique_names: list[str] = []
    seen: set[str] = set()
    for hero_name in hero_names or []:
        if not hero_name or hero_name in seen:
            continue
        seen.add(hero_name)
        unique_names.append(hero_name)
    return unique_names


@lru_cache(maxsize=1)
def _load_hero_table() -> dict[str, Any]:
    return build_hero_feature_table(PROCESSED_STATS_ABS_PATH)


@lru_cache(maxsize=1)
def _load_complete_stats() -> dict[str, Any]:
    payload = load_json(PROCESSED_STATS_ABS_PATH)
    if not isinstance(payload, dict):
        raise FileNotFoundError(f"Processed hero stats not found at {PROCESSED_STATS_ABS_PATH}")
    return payload


@lru_cache(maxsize=1)
def _load_ranker_features() -> list[str]:
    payload = load_json(RANKER_REPORT_PATH)
    if not isinstance(payload, dict):
        raise FileNotFoundError(
            f"Pick ranker report not found at {RANKER_REPORT_PATH}. Run train_pick_ranker_models.py first."
        )

    feature_names = payload.get("features", [])
    if not isinstance(feature_names, list) or not feature_names:
        raise ValueError(f"Expected feature list in {RANKER_REPORT_PATH}")

    return [str(feature_name) for feature_name in feature_names]


def _load_ranker(path: Path) -> XGBRanker:
    if not path.exists():
        raise FileNotFoundError(f"Expected trained ranker at {path}")

    model = XGBRanker()
    model.load_model(path)
    return model


@lru_cache(maxsize=1)
def _load_global_ranker() -> XGBRanker:
    return _load_ranker(GLOBAL_RANKER_PATH)


def _current_pick_index(blue_picks: list[str], red_picks: list[str]) -> int:
    return len(blue_picks) + len(red_picks)


def _current_ban_count(blue_bans: list[str], red_bans: list[str]) -> int:
    return len(blue_bans) + len(red_bans)


def _global_pick_index(blue_picks: list[str], red_picks: list[str]) -> int:
    return _current_pick_index(blue_picks, red_picks) + 1


def _feature_frame(rows: list[dict[str, Any]], feature_names: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for feature_name in feature_names:
        if feature_name not in frame.columns:
            frame[feature_name] = 0.0
    return frame


def _required_bans_for_pick_index(pick_index: int) -> int:
    if pick_index < FIRST_PICK_PHASE_TURNS:
        return FIRST_PICK_PHASE_REQUIRED_BANS
    return SECOND_PICK_PHASE_REQUIRED_BANS


def resolve_next_pick_turn(
    blue_picks: list[str] | None = None,
    red_picks: list[str] | None = None,
    blue_bans: list[str] | None = None,
    red_bans: list[str] | None = None,
    team: str | None = None,
    strict_turn: bool = True,
) -> dict[str, Any]:
    blue_picks = _as_unique_heroes(blue_picks)
    red_picks = _as_unique_heroes(red_picks)
    blue_bans = _as_unique_heroes(blue_bans)
    red_bans = _as_unique_heroes(red_bans)

    pick_index = _current_pick_index(blue_picks, red_picks)
    if pick_index >= len(PICK_SEQUENCE):
        raise ValueError("No remaining pick turns in the current draft state")

    total_bans = _current_ban_count(blue_bans, red_bans)
    current_team, pick_order, draft_turn = PICK_SEQUENCE[pick_index]
    required_bans = _required_bans_for_pick_index(pick_index)

    if strict_turn and total_bans < required_bans:
        phase_name = "first" if required_bans == FIRST_PICK_PHASE_REQUIRED_BANS else "second"
        raise ValueError(
            f"The {phase_name} pick phase is not available until {required_bans} total bans are entered."
        )

    if team is not None:
        requested_team = _normalize_team(team)
        if strict_turn and requested_team != current_team:
            raise ValueError(
                f"It is currently {current_team}'s pick turn, not {requested_team}'s."
            )
        current_team = requested_team
        if current_team == "blue":
            pick_order = len(blue_picks) + 1
        else:
            pick_order = len(red_picks) + 1

    return {
        "team": current_team,
        "pick_order": int(pick_order),
        "turn_index": int(draft_turn),
        "phase_index": 1 if pick_index < FIRST_PICK_PHASE_TURNS else 2,
        "global_pick_index": int(pick_index + 1),
    }


def _legal_pick_candidates(
    hero_table: dict[str, Any],
    blue_picks: list[str],
    red_picks: list[str],
    blue_bans: list[str],
    red_bans: list[str],
) -> list[str]:
    unavailable = set(blue_picks) | set(red_picks) | set(blue_bans) | set(red_bans)
    return [hero_name for hero_name in sorted(hero_table["heroes"].keys()) if hero_name not in unavailable]


def _score_reason_flags(
    row: dict[str, Any],
    pool_frame: pd.DataFrame,
    order_profile: PickOrderProfile,
) -> list[str]:
    del pool_frame

    contribution_specs = [
        (
            "prior_score",
            float(order_profile.base_score_weight) * float(row.get("prior_score", 0.0)),
            "best overall fit in the trained ranker",
        ),
        (
            "secure_power_signal",
            float(order_profile.secure_power_weight) * float(row.get("secure_power_signal", 0.0)),
            "learned profile values secure power in this slot",
        ),
        (
            "flexibility_signal",
            float(order_profile.flexibility_weight) * float(row.get("flexibility_signal", 0.0)),
            "learned profile rewards flexibility here",
        ),
        (
            "ally_pick_synergy_signal",
            float(order_profile.synergy_weight) * float(row.get("ally_pick_synergy_signal", 0.0)),
            "learned profile values synergy with our revealed picks",
        ),
        (
            "counter_vs_enemy_picks_signal",
            float(order_profile.counter_weight) * float(row.get("counter_vs_enemy_picks_signal", 0.0)),
            "learned profile values counterplay into the revealed enemy draft",
        ),
        (
            "ally_role_completion_signal",
            float(order_profile.role_completion_weight) * float(row.get("ally_role_completion_signal", 0.0)),
            "learned profile values role completion in this slot",
        ),
    ]

    reasons = [
        reason
        for _, contribution, reason in sorted(
            contribution_specs,
            key=lambda item: item[1],
            reverse=True,
        )
        if contribution > 0.0
    ]
    if not reasons:
        reasons = ["best overall score for the current pick state"]

    deduped_reasons: list[str] = []
    for reason in reasons:
        if reason not in deduped_reasons:
            deduped_reasons.append(reason)

    return deduped_reasons[:3]


def _trained_signal_prior_score(frame: pd.DataFrame) -> pd.Series:
    return pick_signal_prior_score(frame)


def _context_sort_frame(
    frame: pd.DataFrame,
    order_profile: PickOrderProfile,
    top_pool_size: int,
) -> pd.DataFrame:
    if frame.empty:
        return frame

    scored = build_pick_signal_frame(frame.head(top_pool_size).copy())
    scored["context_peak"] = scored[
        [
            "secure_power_signal",
            "flexibility_signal",
            "ally_pick_synergy_signal",
            "counter_vs_enemy_picks_signal",
            "ally_role_completion_signal",
        ]
    ].max(axis=1)
    scored["context_support"] = weighted_signal_average_for_profile(scored, order_profile)
    scored["final_score"] = score_pick_order_profile(scored, order_profile)
    scored["order_adjustment"] = scored["final_score"] - scored["prior_score"]

    reranked_top = scored.sort_values(
        by=[
            "final_score",
            "context_peak",
            "secure_power_signal",
            "candidate_adjusted_win_rate",
        ],
        ascending=False,
    )

    remaining_pool = frame.iloc[top_pool_size:].copy()
    if not remaining_pool.empty:
        remaining_pool["context_peak"] = 0.0
        remaining_pool["context_support"] = 0.0
        remaining_pool["final_score"] = remaining_pool["prior_score"]
        remaining_pool["order_adjustment"] = 0.0

    return pd.concat([reranked_top, remaining_pool], axis=0, ignore_index=True).reset_index(drop=True)


def recommend_next_picks(
    blue_picks: list[str] | None = None,
    red_picks: list[str] | None = None,
    blue_bans: list[str] | None = None,
    red_bans: list[str] | None = None,
    team: str | None = None,
    top_k: int = 3,
    strict_turn: bool = True,
    rerank_pool_size: int | None = None,
) -> dict[str, Any]:
    blue_picks = _as_unique_heroes(blue_picks)
    red_picks = _as_unique_heroes(red_picks)
    blue_bans = _as_unique_heroes(blue_bans)
    red_bans = _as_unique_heroes(red_bans)

    turn = resolve_next_pick_turn(
        blue_picks=blue_picks,
        red_picks=red_picks,
        blue_bans=blue_bans,
        red_bans=red_bans,
        team=team,
        strict_turn=strict_turn,
    )
    hero_table = _load_hero_table()
    complete_stats = _load_complete_stats()
    candidate_heroes = _legal_pick_candidates(hero_table, blue_picks, red_picks, blue_bans, red_bans)

    if not candidate_heroes:
        return {
            **turn,
            "training_context": dict(PICK_TRAINING_CONTEXT),
            "candidate_count": 0,
            "rerank_pool_size": 0,
            "recommendations": [],
        }

    our_picks = blue_picks if turn["team"] == "blue" else red_picks
    enemy_picks = red_picks if turn["team"] == "blue" else blue_picks
    order_profile = resolve_pick_order_profile(
        global_pick_index=_global_pick_index(blue_picks, red_picks),
        phase_index=turn["phase_index"],
        pick_order=turn["pick_order"],
    )

    precomputed_our_missing_roles = infer_missing_roles(our_picks, hero_table) if our_picks else []
    precomputed_enemy_missing_roles = infer_missing_roles(enemy_picks, hero_table) if enemy_picks else []

    rows: list[dict[str, Any]] = []
    for candidate_hero in candidate_heroes:
        feature_row = build_pick_candidate_feature_row(
            candidate_hero=candidate_hero,
            acting_team=turn["team"],
            pick_order=turn["pick_order"],
            phase_index=turn["phase_index"],
            our_picks=our_picks,
            enemy_picks=enemy_picks,
            blue_bans=blue_bans,
            red_bans=red_bans,
            hero_table=hero_table,
            complete_stats=complete_stats,
            our_missing_roles=precomputed_our_missing_roles,
            enemy_missing_roles=precomputed_enemy_missing_roles,
        )
        rows.append(
            {
                "candidate_hero": candidate_hero,
                **feature_row,
            }
        )

    feature_frame = pd.DataFrame(rows)
    try:
        feature_names = _load_ranker_features()
        ranker = _load_global_ranker()
        ranker_frame = _feature_frame(rows, feature_names)
        prior_scores = ranker.predict(ranker_frame[feature_names].fillna(0.0)).tolist()
        base_model_source = "trained"
        base_model_name = "pick_xgb_ranker_global"
    except (FileNotFoundError, ValueError):
        ranker_frame = build_pick_signal_frame(feature_frame)
        prior_scores = _trained_signal_prior_score(ranker_frame).tolist()
        base_model_source = "fallback"
        base_model_name = "trained_pick_signal_prior_v1"

    feature_frame = build_pick_signal_frame(feature_frame)
    feature_frame["prior_score"] = prior_scores
    sorted_prior_frame = feature_frame.sort_values(
        by=[
            "prior_score",
            "secure_power_signal",
            "ally_role_completion_signal",
            "ally_pick_synergy_signal",
            "counter_vs_enemy_picks_signal",
        ],
        ascending=False,
    ).reset_index(drop=True)
    resolved_rerank_pool_size = min(
        len(candidate_heroes),
        max(top_k * 4, 12) if rerank_pool_size is None else max(1, rerank_pool_size),
    )
    sorted_frame = _context_sort_frame(
        frame=sorted_prior_frame,
        order_profile=order_profile,
        top_pool_size=resolved_rerank_pool_size,
    )

    recommendations: list[dict[str, Any]] = []
    for rank, row in enumerate(sorted_frame.head(max(1, top_k)).to_dict(orient="records"), start=1):
        recommendations.append(
            {
                "hero": row["candidate_hero"],
                "rank": rank,
                "score": float(row.get("final_score", row.get("prior_score", 0.0))),
                "score_components": {
                    "prior_score": float(row.get("prior_score", 0.0)),
                    "order_adjustment": float(row.get("order_adjustment", 0.0)),
                    "context_peak": float(row.get("context_peak", 0.0)),
                    "context_support": float(row.get("context_support", 0.0)),
                    "secure_power_signal": float(row.get("secure_power_signal", 0.0)),
                    "flexibility_signal": float(row.get("flexibility_signal", 0.0)),
                    "hero_power": float(row.get("candidate_hero_power", 0.0)),
                    "adjusted_win_rate": float(row.get("candidate_adjusted_win_rate", 0.0)),
                    "pick_rate": float(row.get("candidate_pick_rate", 0.0)),
                    "hero_flexibility": float(row.get("candidate_hero_flexibility", 0.0)),
                    "ally_pick_synergy_signal": float(row.get("ally_pick_synergy_signal", 0.0)),
                    "ally_pick_synergy_max": float(row.get("ally_pick_synergy_synergy_score_max", 0.0)),
                    "counter_vs_enemy_picks_signal": float(
                        row.get("counter_vs_enemy_picks_signal", 0.0)
                    ),
                    "counter_vs_enemy_picks_max": float(
                        row.get("counter_vs_enemy_picks_counter_score_max", 0.0)
                    ),
                    "ally_role_completion_signal": float(row.get("ally_role_completion_signal", 0.0)),
                    "ally_role_completion_max": float(row.get("ally_role_role_completion_max", 0.0)),
                    "ally_role_overlap_max": float(
                        row.get("candidate_role_overlap_to_our_picks_max", 0.0)
                    ),
                },
                "reasons": _score_reason_flags(row, sorted_frame, order_profile),
            }
        )

    return {
        **turn,
        "order_profile": order_profile.to_dict(),
        "base_model_source": base_model_source,
        "base_model_name": base_model_name,
        "training_context": dict(PICK_TRAINING_CONTEXT),
        "candidate_count": int(len(candidate_heroes)),
        "rerank_pool_size": int(resolved_rerank_pool_size),
        "recommendations": recommendations,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Recommend draft-fit picks with turn-aware reranking."
    )
    parser.add_argument("--team", default="blue", choices=["blue", "red"])
    parser.add_argument("--blue-picks", default="")
    parser.add_argument("--red-picks", default="")
    parser.add_argument("--blue-bans", default="")
    parser.add_argument("--red-bans", default="")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--strict-turn", action="store_true")
    parser.add_argument("--rerank-pool-size", type=int, default=None)
    args = parser.parse_args()

    def parse_csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]

    payload = recommend_next_picks(
        blue_picks=parse_csv(args.blue_picks),
        red_picks=parse_csv(args.red_picks),
        blue_bans=parse_csv(args.blue_bans),
        red_bans=parse_csv(args.red_bans),
        team=args.team,
        top_k=args.top_k,
        strict_turn=args.strict_turn,
        rerank_pool_size=args.rerank_pool_size,
    )

    print(json.dumps(payload, indent=2))
