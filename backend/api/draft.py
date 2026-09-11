from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from backend.services.modeling.advisor_pipeline import (
    advise_bans,
    advise_picks,
    recommend_bans,
    recommend_picks,
)

router = APIRouter()


class DraftStateRequest(BaseModel):
    team: Literal["blue", "red"]
    blue_picks: list[str] = Field(default_factory=list)
    red_picks: list[str] = Field(default_factory=list)
    blue_bans: list[str] = Field(default_factory=list)
    red_bans: list[str] = Field(default_factory=list)
    top_k: int = 3
    strict_turn: bool = True
    rerank_pool_size: int | None = None


@router.post("/recommend-bans")
def recommend_bans_route(request: DraftStateRequest) -> dict[str, Any]:
    return recommend_bans(
        team=request.team,
        blue_picks=request.blue_picks,
        red_picks=request.red_picks,
        blue_bans=request.blue_bans,
        red_bans=request.red_bans,
        top_k=request.top_k,
        strict_turn=request.strict_turn,
        rerank_pool_size=request.rerank_pool_size,
    )


@router.post("/advise-bans")
def advise_bans_route(request: DraftStateRequest) -> dict[str, Any]:
    return advise_bans(
        team=request.team,
        blue_picks=request.blue_picks,
        red_picks=request.red_picks,
        blue_bans=request.blue_bans,
        red_bans=request.red_bans,
        top_k=request.top_k,
        strict_turn=request.strict_turn,
        rerank_pool_size=request.rerank_pool_size,
    )


@router.post("/recommend-picks")
def recommend_picks_route(request: DraftStateRequest) -> dict[str, Any]:
    return recommend_picks(
        team=request.team,
        blue_picks=request.blue_picks,
        red_picks=request.red_picks,
        blue_bans=request.blue_bans,
        red_bans=request.red_bans,
        top_k=request.top_k,
        strict_turn=request.strict_turn,
        rerank_pool_size=request.rerank_pool_size,
    )


@router.post("/advise-picks")
def advise_picks_route(request: DraftStateRequest) -> dict[str, Any]:
    return advise_picks(
        team=request.team,
        blue_picks=request.blue_picks,
        red_picks=request.red_picks,
        blue_bans=request.blue_bans,
        red_bans=request.red_bans,
        top_k=request.top_k,
        strict_turn=request.strict_turn,
        rerank_pool_size=request.rerank_pool_size,
    )
