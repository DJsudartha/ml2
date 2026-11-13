"""Cached contextual MVP serving; no fitting or historical source access at runtime.

The five-component probability mixture is the frozen Draft Lab formula applied
once to exported sufficient statistics. Catalog fallback has score zero and is
always ranked after observed candidates. Scores are ranking weights, not accuracy.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path

from backend.scripts.export_backend_mvp import ALLOWED_GAMES, BATCHES, SOURCE_SHA256
from backend.services.modeling.ban_constants import BAN_SEQUENCE
from backend.services.modeling.pick_constants import PICK_SEQUENCE

ARTIFACT = Path(__file__).with_name("mvp_artifacts") / "contextual-v1.json"
ARTIFACT_SHA256 = "59be2a4013f89d284daa1e637d2ce5a8874cc6e7efb320409d796421199a898e"
BATCH_INDEX = (0, 1, 1, 2, 2, 3, 4, 5, 5, 6)
BATCH_START = (0, 1, 3, 5, 6, 7, 9)
BATCH_SIZE = (1, 2, 2, 1, 1, 2, 1)
LEGACY_COMPONENTS = (
    "prior_score", "order_adjustment", "context_peak", "context_support",
    "secure_power_signal", "flexibility_signal", "hero_power", "adjusted_win_rate",
    "pick_rate", "hero_flexibility", "ally_pick_synergy_signal", "ally_pick_synergy_max",
    "counter_vs_enemy_picks_signal", "counter_vs_enemy_picks_max",
    "ally_role_completion_signal", "ally_role_completion_max", "ally_role_overlap_max",
    "phase_fit_share", "current_slot_share", "ban_rate", "enemy_pick_synergy_max",
    "counter_vs_our_picks_max", "enemy_role_completion_max",
)


@lru_cache(maxsize=1)
def load_snapshot():
    raw = ARTIFACT.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ARTIFACT_SHA256:
        raise RuntimeError("MVP inference artifact integrity failure")
    artifact = json.loads(raw)
    if (artifact["schema_version"] != 1 or artifact["version"] != "contextual-backend-mvp-v1"
            or artifact["source_snapshot_sha256"] != SOURCE_SHA256
            or artifact["training_game_ids"] != list(ALLOWED_GAMES)
            or artifact["counts"] != {"games": 17, "matches": 16, "examples": 119,
                                       "positive_memberships": 170}):
        raise RuntimeError("Unapproved MVP inference artifact")
    return artifact


@lru_cache(maxsize=1)
def hero_pool():
    """Configure the simulator pool with a JSON array; never infer historical legality."""
    path = os.getenv("ML2_SIMULATOR_HERO_POOL")
    values = json.loads(Path(path).read_text(encoding="utf-8")) if path else load_snapshot()["simulator_heroes"]
    if (not isinstance(values, list) or not values or
            any(not isinstance(h, str) or not h.strip() or h != h.strip() for h in values)
            or len(set(values)) != len(values)):
        raise RuntimeError("Simulator hero pool must contain unique nonempty names")
    disabled = {h.strip() for h in os.getenv("ML2_DISABLED_HEROES", "").split(",") if h.strip()}
    if disabled - set(values):
        raise RuntimeError("Configured disabled hero is outside simulator pool")
    return tuple(sorted(set(values) - disabled))


def _lists(blue_picks, red_picks, blue_bans, red_bans):
    values = [list(v or []) for v in (blue_picks, red_picks, blue_bans, red_bans)]
    if any(len(v) > 5 for v in values):
        raise ValueError("Each team has at most five picks and five bans")
    flat = sum(values, [])
    if any(not isinstance(h, str) or h not in load_snapshot()["simulator_heroes"]
           and h not in hero_pool() for h in flat):
        raise ValueError("Unknown simulator hero")
    if len(flat) != len(set(flat)):
        raise ValueError("A hero cannot occur twice or be both selected and banned")
    return values


def _team(team):
    if team not in ("blue", "red"):
        raise ValueError("Team must be 'blue' or 'red'")
    return team


def contextual_scores(statistics, protocol, batch, side, allies, enemies):
    """Frozen HistoricalContextFrequency mixture, using its sufficient counts."""
    counts = statistics["global"]
    if not counts:
        return {}
    alpha = protocol["global_uniform_pseudocount"]
    total = sum(counts.values()) + alpha * len(counts)
    base = {h: (n + alpha) / total for h, n in counts.items()}
    strength = protocol["conditional_prior_memberships"]

    def smooth(values):
        denominator = sum(values.values()) + strength
        return {h: (values.get(h, 0) + strength * base[h]) / denominator for h in base}

    parts = {"global": base, "batch": smooth(statistics["batch"].get(batch, {})),
             "side": smooth(statistics["side"].get(side, {}))}
    for kind, context in (("ally", allies), ("enemy", enemies)):
        distributions = [smooth(statistics[kind].get(h, {})) for h in sorted(set(context))]
        parts[kind] = ({h: sum(d[h] for d in distributions) / len(distributions) for h in base}
                       if distributions else base)
    return {h: {"score": sum(protocol["weights"][k] * parts[k][h] for k in parts),
                "components": {k: parts[k][h] for k in parts}} for h in base}


def _response(turn, values, scored, top_k, rerank_pool_size, *, action, batch=None,
              partial=0, complete_bans=False):
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    if rerank_pool_size is not None and (type(rerank_pool_size) is not int or rerank_pool_size < 1):
        raise ValueError("rerank_pool_size must be positive")
    excluded = set(sum(values, []))
    eligible = [h for h in hero_pool() if h not in excluded]
    ranking = sorted(eligible, key=lambda h: (-scored.get(h, {}).get("score", 0.0), h))
    recommendations = []
    for rank, hero in enumerate(ranking[:top_k], 1):
        evidence = scored.get(hero)
        score = evidence["score"] if evidence else 0.0
        if not math.isfinite(score):
            raise RuntimeError("Non-finite inference score")
        components = dict.fromkeys(LEGACY_COMPONENTS, 0.0)
        components.update(evidence.get("components", {}) if evidence else {})
        components["prior_score"] = score
        recommendations.append({"hero": hero, "rank": rank, "score": score,
                                "score_components": components,
                                "reasons": ["Contextual finalized development frequency" if action == "pick" and evidence
                                            else "Source-backed completed-phase ban frequency" if evidence
                                            else "Deterministic simulator-pool fallback; no observed support"],
                                "evidence_source": "finalized_development_statistics" if evidence else "catalog_fallback"})
    profile = "opener" if batch == 0 else "closer" if batch is not None and batch >= 4 else "bridge"
    result = {**turn, "candidate_count": len(eligible),
            "rerank_pool_size": min(len(eligible), rerank_pool_size or len(eligible)),
            "recommendations": recommendations,
            "reasoning": "Experimental full-development frequencies with current-state exclusions; no validated accuracy claim.",
            "base_model_source": "finalized_development_contextual_statistics" if action == "pick" else "source_backed_completed_phase_ban_counts",
            "base_model_name": load_snapshot()["version"],
            "order_profile": {"id": profile, "title": profile.title(), "summary": "Canonical pre-batch context"},
            "training_context": {"target": "confirmed-ordered-pick", "uses_confirmed_pick_order": True,
                                 "limitation": "17 development games; independent hero scores; historical competitive eligibility unknown; catalog fallback has no learned evidence."},
            "batch_id": BATCHES[batch] if batch is not None else None,
            "batch_size": BATCH_SIZE[batch] if batch is not None else None,
            "remaining_selections": BATCH_SIZE[batch] - partial if batch is not None else 1,
            "trained_second_pick_model": False,
            "inference_mode": "remaining_member_filter_only" if partial else "unordered_prebatch_marginal_hero_scores",
            "ban_knowledge": "completed_phases_known" if complete_bans else "unknown_or_incomplete",
            "historical_eligibility": "unknown", "simulator_pool_policy": "configured_current_pool_minus_known_exclusions",
            "fallback_count": sum(r["evidence_source"] == "catalog_fallback" for r in recommendations),
            "fit_status": load_snapshot()["fit_status"],
            "limitations": dict(load_snapshot()["limitations"]),
            "rerank_policy": "fixed contextual scores; no second-stage reranker",
            "source_provenance": {"artifact_sha256": ARTIFACT_SHA256,
                                  "source_snapshot_sha256": SOURCE_SHA256,
                                  "counts": dict(load_snapshot()["counts"])}}
    if action == "ban":
        result.pop("training_context")
        result["ban_evidence"] = "Liquipedia completed-phase sets from 17 finalized development games; user-confirmed per-team phase chronology"
    return result


def recommend_picks(*, team="blue", blue_picks=None, red_picks=None, blue_bans=None,
                    red_bans=None, top_k=3, strict_turn=True, rerank_pool_size=None):
    side = _team(team)
    values = _lists(blue_picks, red_picks, blue_bans, red_bans)
    blue, red, bb, rb = values
    count = len(blue) + len(red)
    if count >= 10:
        raise ValueError("No remaining pick turns in the current draft state")
    expected_side, _, draft_turn = PICK_SEQUENCE[count]
    expected_blue = sum(actor == "blue" for actor, _, _ in PICK_SEQUENCE[:count])
    if (len(blue), len(red)) != (expected_blue, count - expected_blue):
        raise ValueError("Picks do not match a current canonical draft prefix")
    index = BATCH_INDEX[count]
    phase_bans = 3 if index <= 3 else 5
    if len(bb) > phase_bans or len(rb) > phase_bans:
        raise ValueError("Future-phase bans cannot enter the current pick state")
    if strict_turn and (len(bb), len(rb)) != (phase_bans, phase_bans):
        raise ValueError(f"Pick phase requires {phase_bans * 2} completed bans")
    if strict_turn and side != expected_side:
        raise ValueError(f"It is currently {expected_side}'s pick turn, not {side}'s.")
    partial = count - BATCH_START[index]
    prior = {"blue": blue[:], "red": red[:]}
    if partial:
        prior[expected_side] = prior[expected_side][:-partial]
    artifact = load_snapshot()
    scored = contextual_scores(artifact["statistics"], artifact["protocol"], BATCHES[index], side,
                               prior[side], prior["red" if side == "blue" else "blue"])
    turn = {"team": side, "pick_order": len(blue if side == "blue" else red) + 1,
            "phase_index": 1 if index <= 3 else 2, "turn_index": draft_turn,
            "global_pick_index": count + 1, "turn_override": side != expected_side}
    return _response(turn, values, scored, top_k, rerank_pool_size, action="pick", batch=index,
                     partial=partial, complete_bans=len(bb) == len(rb) == phase_bans)


def recommend_bans(*, team="blue", blue_picks=None, red_picks=None, blue_bans=None,
                   red_bans=None, top_k=3, strict_turn=True, rerank_pool_size=None):
    side = _team(team)
    values = _lists(blue_picks, red_picks, blue_bans, red_bans)
    blue, red, bb, rb = values
    count = len(bb) + len(rb)
    if count >= 10:
        raise ValueError("No remaining ban turns in the current draft state")
    actor, _, draft_turn = BAN_SEQUENCE[count]
    expected_blue = sum(t == "blue" for t, _, _ in BAN_SEQUENCE[:count])
    if strict_turn:
        if (len(bb), len(rb)) != (expected_blue, count - expected_blue):
            raise ValueError("Bans do not match a canonical current draft prefix")
        if (len(blue), len(red)) != ((0, 0) if count < 6 else (3, 3)):
            raise ValueError("Bans require the matching first or second draft phase")
        if actor != side:
            raise ValueError(f"It is currently {actor}'s ban turn, not {side}'s.")
    phase = 1 if count < 6 else 2
    counts = load_snapshot()["ban_phase_counts"][str(phase)][side]
    denominator = sum(counts.values())
    scores = {h: {"score": n / denominator, "components": {"phase_fit_share": n / denominator,
               "ban_rate": n / 17}} for h, n in counts.items()} if denominator else {}
    turn = {"team": side, "ban_order": len(bb if side == "blue" else rb) + 1,
            "turn_index": draft_turn, "phase_index": phase, "turn_override": side != actor}
    return _response(turn, values, scores, top_k, rerank_pool_size, action="ban")
