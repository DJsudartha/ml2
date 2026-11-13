from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.modeling import advisor_pipeline


client = TestClient(app)

VALID_DRAFT_STATE = {
    "team": "blue",
    "blue_picks": ["Akai"],
    "red_picks": ["Claude"],
    "blue_bans": ["Fanny"],
    "red_bans": ["Joy"],
    "top_k": 2,
    "strict_turn": False,
    "rerank_pool_size": 8,
}


def _recommendation_payload(action: str) -> dict[str, Any]:
    return {
        "team": "blue",
        "turn_index": 1,
        "candidate_count": 2,
        "rerank_pool_size": 2,
        "recommendations": [
            {
                "hero": "Akai" if action == "ban" else "Claude",
                "rank": 1,
                "score": 9.5,
                "reasons": ["mocked contract response"],
            }
        ],
    }


def _advisor_payload(action: str) -> dict[str, Any]:
    return {
        "recommendation": _recommendation_payload(action),
        "advisor": {
            "uses_llm": False,
            "provider": "local-semantic",
            "model": "mock-advisor",
            "advice": f"{action.title()} the mocked hero.",
            "retrieved_principles": [],
        },
    }


def test_health_routes():
    assert client.get("/").json() == {"message": "MLBB Draft Backend Running"}
    assert client.get("/health").json() == {"status": "ok"}
    assert len(client.get("/draft").json()["endpoints"]) == 4


@pytest.mark.parametrize(
    ("endpoint", "pipeline_function", "expected_payload"),
    [
        ("/draft/recommend-bans", "recommend_bans", _recommendation_payload("ban")),
        ("/draft/recommend-picks", "recommend_picks", _recommendation_payload("pick")),
        ("/draft/advise-bans", "advise_bans", _advisor_payload("ban")),
        ("/draft/advise-picks", "advise_picks", _advisor_payload("pick")),
    ],
)
def test_draft_routes_forward_request_state_to_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    pipeline_function: str,
    expected_payload: dict[str, Any],
):
    calls: list[dict[str, Any]] = []

    def fake_pipeline(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return expected_payload

    monkeypatch.setattr(advisor_pipeline, pipeline_function, fake_pipeline)

    response = client.post(endpoint, json=VALID_DRAFT_STATE)

    assert response.status_code == 200
    assert response.json() == expected_payload
    assert calls == [VALID_DRAFT_STATE]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("team", "green"),
        ("blue_picks", "Akai"),
        ("top_k", 0),
        ("top_k", 21),
        ("rerank_pool_size", 0),
    ],
)
def test_draft_routes_reject_invalid_request_fields(field: str, value: Any):
    response = client.post(
        "/draft/recommend-picks",
        json={**VALID_DRAFT_STATE, field: value},
    )

    assert response.status_code == 422
