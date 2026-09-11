from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app


FIRST_PHASE_BLUE_BANS = ["Fanny", "Zhuxin", "Hayabusa"]
FIRST_PHASE_RED_BANS = ["Joy", "Suyou", "Lukas"]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LOCAL_DRAFT_ADVISOR_BACKEND", "tfidf")
    with TestClient(app) as test_client:
        yield test_client


def _assert_recommendation_item_shape(item: dict[str, Any]) -> None:
    assert isinstance(item["hero"], str)
    assert isinstance(item["rank"], int)
    assert isinstance(item["score"], (int, float))
    assert isinstance(item["reasons"], list)


def _assert_recommendation_shape(body: dict[str, Any], team: str) -> None:
    assert body["team"] == team
    assert isinstance(body["phase_index"], int)
    assert isinstance(body["recommendations"], list)
    if body["recommendations"]:
        _assert_recommendation_item_shape(body["recommendations"][0])


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "/draft/recommend-bans",
            {
                "team": "blue",
                "blue_picks": [],
                "red_picks": [],
                "blue_bans": [],
                "red_bans": [],
                "top_k": 3,
                "strict_turn": True,
                "rerank_pool_size": None,
            },
        ),
        (
            "/draft/recommend-picks",
            {
                "team": "blue",
                "blue_picks": [],
                "red_picks": [],
                "blue_bans": FIRST_PHASE_BLUE_BANS,
                "red_bans": FIRST_PHASE_RED_BANS,
                "top_k": 3,
                "strict_turn": True,
                "rerank_pool_size": None,
            },
        ),
    ],
)
def test_recommend_routes_return_ranked_recommendations(
    client: TestClient,
    endpoint: str,
    payload: dict[str, Any],
) -> None:
    response = client.post(endpoint, json=payload)

    assert response.status_code == 200
    _assert_recommendation_shape(response.json(), payload["team"])


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "/draft/advise-bans",
            {
                "team": "blue",
                "blue_picks": [],
                "red_picks": [],
                "blue_bans": [],
                "red_bans": [],
                "top_k": 3,
                "strict_turn": True,
                "rerank_pool_size": None,
            },
        ),
        (
            "/draft/advise-picks",
            {
                "team": "blue",
                "blue_picks": [],
                "red_picks": [],
                "blue_bans": FIRST_PHASE_BLUE_BANS,
                "red_bans": FIRST_PHASE_RED_BANS,
                "top_k": 3,
                "strict_turn": True,
                "rerank_pool_size": None,
            },
        ),
    ],
)
def test_advise_routes_return_recommendation_and_advisor(
    client: TestClient,
    endpoint: str,
    payload: dict[str, Any],
) -> None:
    response = client.post(endpoint, json=payload)
    body = response.json()

    assert response.status_code == 200
    assert set(body) >= {"recommendation", "advisor"}
    _assert_recommendation_shape(body["recommendation"], payload["team"])
    assert body["advisor"]["uses_llm"] is False
    assert body["advisor"]["provider"] == "local-semantic"
    assert isinstance(body["advisor"]["advice"], str)
    assert isinstance(body["advisor"]["retrieved_principles"], list)
    assert "error" in body["advisor"]


def test_root_healthcheck_uses_canonical_backend_app(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "MLBB Draft Backend Running"}
