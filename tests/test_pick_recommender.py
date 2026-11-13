from backend.services.modeling.pick_recommender import (
    recommend_next_picks,
    resolve_next_pick_turn,
)


FIRST_PHASE_BLUE_BANS = ["Fanny", "Zhuxin", "Hayabusa"]
FIRST_PHASE_RED_BANS = ["Joy", "Suyou", "Lukas"]
SECOND_PHASE_BLUE_BANS = ["Fanny", "Zhuxin", "Hayabusa", "Joy", "Lukas"]
SECOND_PHASE_RED_BANS = ["Suyou", "Novaria", "Ling", "Selena", "Valentina"]


def test_resolve_next_pick_turn_after_first_ban_phase():
    turn = resolve_next_pick_turn(
        blue_bans=FIRST_PHASE_BLUE_BANS,
        red_bans=FIRST_PHASE_RED_BANS,
    )

    assert turn["team"] == "blue"
    assert turn["pick_order"] == 1
    assert turn["phase_index"] == 1
    assert turn["turn_index"] == 7
    assert turn["global_pick_index"] == 1


def test_recommend_next_picks_excludes_unavailable_heroes():
    recommendation = recommend_next_picks(
        blue_picks=["Akai"],
        red_picks=["Claude"],
        blue_bans=FIRST_PHASE_BLUE_BANS,
        red_bans=FIRST_PHASE_RED_BANS,
        team="red",
        top_k=3,
    )

    unavailable = {
        "Akai",
        "Claude",
        *FIRST_PHASE_BLUE_BANS,
        *FIRST_PHASE_RED_BANS,
    }
    heroes = [item["hero"] for item in recommendation["recommendations"]]

    assert recommendation["team"] == "red"
    assert recommendation["pick_order"] == 2
    assert len(heroes) == 3
    assert not unavailable.intersection(heroes)
    assert recommendation["global_pick_index"] == 3
    assert recommendation["order_profile"]["id"] == "bridge"
    assert recommendation["base_model_source"]
    assert recommendation["base_model_name"]
    assert recommendation["training_context"]["target"] == "order-agnostic-pick-fit"
    assert recommendation["training_context"]["uses_confirmed_pick_order"] is False
    assert recommendation["training_context"]["limitation"]


def test_recommend_next_picks_uses_opener_profile_on_first_pick():
    recommendation = recommend_next_picks(
        blue_bans=FIRST_PHASE_BLUE_BANS,
        red_bans=FIRST_PHASE_RED_BANS,
        team="blue",
        top_k=2,
    )

    assert recommendation["global_pick_index"] == 1
    assert recommendation["order_profile"]["id"] == "opener"
    assert recommendation["base_model_source"]
    assert recommendation["base_model_name"]


def test_recommend_next_picks_exposes_context_components_with_revealed_draft():
    recommendation = recommend_next_picks(
        blue_picks=["Akai", "Yve"],
        red_picks=["Claude", "Lolita"],
        blue_bans=FIRST_PHASE_BLUE_BANS,
        red_bans=FIRST_PHASE_RED_BANS,
        team="blue",
        top_k=2,
        rerank_pool_size=2,
    )

    first_item = recommendation["recommendations"][0]
    score_components = first_item["score_components"]

    assert recommendation["rerank_pool_size"] == 2
    assert "prior_score" in score_components
    assert "order_adjustment" in score_components
    assert "context_peak" in score_components
    assert "secure_power_signal" in score_components
    assert "ally_pick_synergy_max" in score_components
    assert "counter_vs_enemy_picks_max" in score_components
    assert "ally_role_completion_max" in score_components
    assert recommendation["order_profile"]["id"] == "bridge"
    assert recommendation["base_model_source"]
    assert recommendation["base_model_name"]


def test_recommend_next_picks_uses_late_game_order_profile():
    recommendation = recommend_next_picks(
        blue_picks=["Akai", "Yve", "Grock", "Baxia"],
        red_picks=["Claude", "Lolita", "Pharsa", "Hylos"],
        blue_bans=SECOND_PHASE_BLUE_BANS,
        red_bans=SECOND_PHASE_RED_BANS,
        team="blue",
        top_k=2,
    )

    assert recommendation["phase_index"] == 2
    assert recommendation["global_pick_index"] == 9
    assert recommendation["order_profile"]["id"] == "closer"
    assert recommendation["base_model_source"]
    assert recommendation["base_model_name"]
    assert recommendation["recommendations"][0]["reasons"]
