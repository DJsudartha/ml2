from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from backend.services.data.pick_order_annotations import (
    build_annotation_template,
    iter_confirmed_pick_order_states,
    validate_pick_order_payload,
)
from backend.services.data.raw_games import load_raw_games, load_raw_games_by_id
from backend.services.data.vod_pick_order_suggestions import suggest_pick_order_from_slot_reveals
from backend.services.modeling.dataset_builder import build_ordered_pick_dataset
from backend.services.modeling.pick_constants import PICK_SEQUENCE

BLUE_PICKS = ["Alpha", "Balmond", "Cecilion", "Claude", "Diggie"]
RED_PICKS = ["Edith", "Fanny", "Gord", "Hanabi", "Irithel"]
BLUE_BANS = ["Joy", "Ling", "Novaria", "Suyou", "Valentina"]
RED_BANS = ["Faramis", "Fredrinn", "Hayabusa", "Luo Yi", "Yve"]


def _write_raw_tournament(raw_dir: Path) -> Path:
    tournament_path = raw_dir / "Sample_games.json"
    tournament_path.parent.mkdir(parents=True, exist_ok=True)
    tournament_path.write_text(
        json.dumps(
            {
                "tournament": "Sample Cup",
                "pagename": "Sample_Cup",
                "series": [
                    {
                        "date": "2026-01-01 00:00:00",
                        "patch": "1.0",
                        "blue_team_name": "Blue Team",
                        "red_team_name": "Red Team",
                        "games": [
                            {
                                "game_no": 1,
                                "blue_team": [
                                    {"hero": hero, "slot": index, "role": "Role"}
                                    for index, hero in enumerate(BLUE_PICKS, start=1)
                                ],
                                "red_team": [
                                    {"hero": hero, "slot": index, "role": "Role"}
                                    for index, hero in enumerate(RED_PICKS, start=1)
                                ],
                                "blue_bans": [
                                    {"hero": hero, "ban_order": index}
                                    for index, hero in enumerate(BLUE_BANS, start=1)
                                ],
                                "red_bans": [
                                    {"hero": hero, "ban_order": index}
                                    for index, hero in enumerate(RED_BANS, start=1)
                                ],
                                "winner": "blue",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    load_raw_games.cache_clear()
    return tournament_path


def _valid_picks() -> list[dict[str, Any]]:
    heroes_by_team_order = {
        ("blue", 1): "Alpha",
        ("blue", 2): "Balmond",
        ("blue", 3): "Cecilion",
        ("blue", 4): "Claude",
        ("blue", 5): "Diggie",
        ("red", 1): "Edith",
        ("red", 2): "Fanny",
        ("red", 3): "Gord",
        ("red", 4): "Hanabi",
        ("red", 5): "Irithel",
    }
    return [
        {
            "global_pick_index": global_pick_index,
            "team": team,
            "team_pick_order": team_pick_order,
            "turn_index": turn_index,
            "hero": heroes_by_team_order[(team, team_pick_order)],
            "evidence_frame": None,
            "observed_at_sec": None,
        }
        for global_pick_index, (team, team_pick_order, turn_index) in enumerate(
            PICK_SEQUENCE,
            start=1,
        )
    ]


def _valid_payload(game_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "games": [
            {
                "game_id": game_id,
                "source": "manual_review",
                "status": "confirmed",
                "confidence": 1.0,
                "notes": "",
                "picks": _valid_picks(),
            }
        ],
    }


def _write_annotations(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_raw_game_loader_builds_stable_game_id(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_raw_tournament(raw_dir)

    games = load_raw_games(raw_dir)

    assert len(games) == 1
    assert games[0]["game_id"] == "Sample_games.json::series1::game1::1"
    assert games[0]["blue_picks"] == BLUE_PICKS
    assert games[0]["red_bans"] == RED_BANS


def test_annotation_template_uses_raw_game_ids_and_final_picks(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_raw_tournament(raw_dir)

    template = build_annotation_template(raw_dir)

    assert template["version"] == 1
    assert template["games"][0]["game_id"] == "Sample_games.json::series1::game1::1"
    assert template["games"][0]["status"] == "needs_review"
    assert template["games"][0]["final_picks"]["blue"] == BLUE_PICKS


def test_valid_confirmed_annotation_passes(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_raw_tournament(raw_dir)
    raw_games = load_raw_games_by_id(raw_dir)
    payload = _valid_payload("Sample_games.json::series1::game1::1")

    report = validate_pick_order_payload(payload, raw_games)

    assert report.is_valid
    assert report.confirmed_games == 1


@pytest.mark.parametrize(
    ("mutator", "expected_message"),
    [
        (lambda payload: payload["games"][0]["picks"].pop(), "must include 10 picks"),
        (
            lambda payload: payload["games"][0]["picks"][1].update({"hero": "Alpha"}),
            "Duplicate picked hero",
        ),
        (
            lambda payload: payload["games"][0]["picks"][0].update({"team": "red"}),
            "wrong team",
        ),
        (
            lambda payload: payload["games"][0]["picks"][0].update({"hero": "Unknown Hero"}),
            "is not in blue's final picks",
        ),
        (
            lambda payload: payload["games"][0]["picks"][0].update({"global_pick_index": 2}),
            "wrong global_pick_index",
        ),
        (
            lambda payload: payload["games"][0]["picks"][7].update({"hero": "Alpha"}),
            "does not match final blue picks",
        ),
    ],
)
def test_confirmed_annotation_validation_rejects_bad_pick_orders(
    tmp_path: Path,
    mutator,
    expected_message: str,
):
    raw_dir = tmp_path / "raw"
    _write_raw_tournament(raw_dir)
    raw_games = load_raw_games_by_id(raw_dir)
    payload = _valid_payload("Sample_games.json::series1::game1::1")
    mutated_payload = copy.deepcopy(payload)
    mutator(mutated_payload)

    report = validate_pick_order_payload(mutated_payload, raw_games)

    assert not report.is_valid
    assert any(expected_message in error.message for error in report.errors)


def test_confirmed_pick_order_states_use_prior_picks_and_phase_bans(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_raw_tournament(raw_dir)
    annotations_path = tmp_path / "pick_order_annotations.json"
    _write_annotations(annotations_path, _valid_payload("Sample_games.json::series1::game1::1"))

    states = list(iter_confirmed_pick_order_states(annotations_path, raw_dir))

    assert states[0]["actual_pick"] == "Alpha"
    assert states[0]["our_picks"] == []
    assert states[0]["enemy_picks"] == []
    assert states[0]["blue_bans"] == BLUE_BANS[:3]
    assert states[0]["red_bans"] == RED_BANS[:3]

    assert states[6]["actual_pick"] == "Hanabi"
    assert states[6]["our_picks"] == ["Edith", "Fanny", "Gord"]
    assert states[6]["enemy_picks"] == ["Alpha", "Balmond", "Cecilion"]
    assert states[6]["blue_bans"] == BLUE_BANS
    assert states[6]["red_bans"] == RED_BANS


def test_ordered_pick_dataset_uses_prior_state_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    raw_dir = tmp_path / "raw"
    _write_raw_tournament(raw_dir)
    annotations_path = tmp_path / "pick_order_annotations.json"
    processed_stats_path = tmp_path / "complete_hero_stats.json"
    _write_annotations(annotations_path, _valid_payload("Sample_games.json::series1::game1::1"))
    processed_stats_path.write_text('{"heroes": {}}', encoding="utf-8")

    all_heroes = BLUE_PICKS + RED_PICKS + BLUE_BANS + RED_BANS + ["Extra Hero"]
    captured_rows = []

    def fake_build_hero_feature_table(*_args, **_kwargs):
        return {"heroes": {hero: {} for hero in all_heroes}, "global": {"global_win_rate": 0.5}}

    def fake_build_pick_candidate_feature_row(**kwargs):
        captured_rows.append(kwargs)
        return {"fake_feature": float(len(kwargs["our_picks"]))}

    monkeypatch.setattr(
        "backend.services.modeling.dataset_builder.load_feature_engineering_profile",
        lambda: {
            "adjusted_win_rate_smoothing_games": 10,
            "flexibility_role_threshold": 0.2,
            "pair_prior_games": 10,
        },
    )
    monkeypatch.setattr(
        "backend.services.modeling.dataset_builder.build_hero_feature_table",
        fake_build_hero_feature_table,
    )
    monkeypatch.setattr(
        "backend.services.modeling.dataset_builder.build_pick_candidate_feature_row",
        fake_build_pick_candidate_feature_row,
    )
    monkeypatch.setattr("backend.services.modeling.dataset_builder.infer_missing_roles", lambda *_args: [])

    dataset = build_ordered_pick_dataset(
        processed_stats_path=processed_stats_path,
        raw_dir=raw_dir,
        annotations_path=annotations_path,
    )

    assert dataset["metadata"]["model_target"] == "label_is_ordered_pick"
    first_pick_call = next(row for row in captured_rows if row["candidate_hero"] == "Alpha")
    assert first_pick_call["our_picks"] == []
    assert first_pick_call["enemy_picks"] == []
    assert first_pick_call["blue_bans"] == BLUE_BANS[:3]

    late_pick_call = next(
        row
        for row in captured_rows
        if row["candidate_hero"] == "Hanabi" and row["pick_order"] == 4
    )
    assert late_pick_call["our_picks"] == ["Edith", "Fanny", "Gord"]
    assert late_pick_call["enemy_picks"] == ["Alpha", "Balmond", "Cecilion"]
    assert late_pick_call["blue_bans"] == BLUE_BANS
    assert late_pick_call["red_bans"] == RED_BANS


def test_vod_slot_reveal_suggestions_stay_in_review_status(tmp_path: Path):
    raw_dir = tmp_path / "raw"
    _write_raw_tournament(raw_dir)
    raw_game = load_raw_games(raw_dir)[0]
    ordered_slots = [
        "blue_pick1",
        "red_pick1",
        "red_pick2",
        "blue_pick2",
        "blue_pick3",
        "red_pick3",
        "red_pick4",
        "blue_pick4",
        "blue_pick5",
        "red_pick5",
    ]
    reveals = [
        {
            "slot": slot,
            "observed_at_sec": float(index),
            "frame_path": f"frame_{index:05d}.jpg",
            "confidence": 0.9,
        }
        for index, slot in enumerate(ordered_slots, start=1)
    ]

    suggestion = suggest_pick_order_from_slot_reveals(raw_game, reveals)

    assert suggestion["status"] == "needs_review"
    assert suggestion["confidence"] == 0.9
    assert suggestion["picks"][0]["hero"] == "Alpha"
    assert suggestion["picks"][1]["hero"] == "Edith"
    assert suggestion["picks"][6]["hero"] == "Hanabi"
