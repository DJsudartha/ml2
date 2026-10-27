from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
import sys
from typing import Protocol, TypedDict, cast

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from backend.services.llm.pick_principles import PICK_DRAFT_PRINCIPLES

ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

DEFAULT_LOCAL_BACKEND = "tfidf"
DEFAULT_LOCAL_EMBEDDINGS_MODEL = "all-MiniLM-L6-v2"
DEFAULT_TOP_PRINCIPLES = 3


class ScoreComponents(TypedDict):
    prior_score: float
    order_adjustment: float
    context_peak: float
    context_support: float
    secure_power_signal: float
    flexibility_signal: float
    hero_power: float
    adjusted_win_rate: float
    pick_rate: float
    hero_flexibility: float
    ally_pick_synergy_signal: float
    ally_pick_synergy_max: float
    counter_vs_enemy_picks_signal: float
    counter_vs_enemy_picks_max: float
    ally_role_completion_signal: float
    ally_role_completion_max: float
    ally_role_overlap_max: float


class RecommendationItem(TypedDict):
    hero: str
    rank: int
    score: float
    score_components: ScoreComponents
    reasons: list[str]


class OrderProfile(TypedDict):
    id: str
    title: str
    summary: str
    base_score_weight: float
    secure_power_weight: float
    flexibility_weight: float
    synergy_weight: float
    counter_weight: float
    role_completion_weight: float
    redundancy_penalty_weight: float


class PickRecommendation(TypedDict, total=False):
    team: str
    pick_order: int
    phase_index: int
    global_pick_index: int
    order_profile: OrderProfile
    recommendations: list[RecommendationItem]


class RetrievedPrinciple(TypedDict):
    id: str
    title: str
    text: str
    score: float


class PickAdvice(TypedDict):
    uses_llm: bool
    provider: str
    model: str | None
    advice: str
    retrieved_principles: list[RetrievedPrinciple]
    error: str | None


class EmbeddingEncoder(Protocol):
    def encode(self, sentences: list[str], convert_to_tensor: bool = False) -> object: ...


class QueryVectorizer(Protocol):
    def transform(self, raw_documents: list[str]) -> object: ...


def _local_backend_preference() -> str:
    return os.getenv("LOCAL_DRAFT_ADVISOR_BACKEND", DEFAULT_LOCAL_BACKEND).strip().lower()


def _local_embeddings_model() -> str:
    return os.getenv(
        "LOCAL_DRAFT_ADVISOR_EMBEDDINGS_MODEL",
        DEFAULT_LOCAL_EMBEDDINGS_MODEL,
    ).strip() or DEFAULT_LOCAL_EMBEDDINGS_MODEL


def _local_top_principles() -> int:
    raw_value = os.getenv("LOCAL_DRAFT_ADVISOR_TOP_PRINCIPLES", str(DEFAULT_TOP_PRINCIPLES)).strip()
    try:
        return max(1, min(5, int(raw_value)))
    except ValueError:
        return DEFAULT_TOP_PRINCIPLES


def _principle_documents() -> list[str]:
    return [f"{item['title']}. {item['text']}" for item in PICK_DRAFT_PRINCIPLES]


@lru_cache(maxsize=1)
def _semantic_backend() -> tuple[str, EmbeddingEncoder | QueryVectorizer, object]:
    preference = _local_backend_preference()

    if preference in {"auto", "sentence-transformers"} and SentenceTransformer is not None:
        try:
            model_name = _local_embeddings_model()
            model = SentenceTransformer(model_name)
            principle_embeddings = model.encode(_principle_documents(), convert_to_tensor=False)
            return ("sentence-transformers", cast(EmbeddingEncoder, model), principle_embeddings)
        except Exception:
            if preference == "sentence-transformers":
                raise

    vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english")
    matrix = vectorizer.fit_transform(_principle_documents())
    return ("tfidf-cosine-v1", vectorizer, matrix)


def _query_text(
    recommendation: PickRecommendation,
    blue_picks: list[str],
    red_picks: list[str],
    blue_bans: list[str],
    red_bans: list[str],
) -> str:
    recommendation_lines: list[str] = []
    for item in recommendation.get("recommendations", [])[:3]:
        components = item["score_components"]
        recommendation_lines.append(
            (
                f"hero={item['hero']} rank={item['rank']} "
                f"reasons={' | '.join(item['reasons'])} "
                f"order_adjustment={components['order_adjustment']:.4f} "
                f"secure_power_signal={components['secure_power_signal']:.4f} "
                f"flexibility_signal={components['flexibility_signal']:.4f} "
                f"hero_power={components['hero_power']:.4f} "
                f"adjusted_win_rate={components['adjusted_win_rate']:.4f} "
                f"pick_rate={components['pick_rate']:.4f} "
                f"hero_flexibility={components['hero_flexibility']:.4f} "
                f"ally_synergy_signal={components['ally_pick_synergy_signal']:.4f} "
                f"ally_synergy={components['ally_pick_synergy_max']:.4f} "
                f"enemy_counter_signal={components['counter_vs_enemy_picks_signal']:.4f} "
                f"enemy_counter_pressure={components['counter_vs_enemy_picks_max']:.4f} "
                f"ally_role_signal={components['ally_role_completion_signal']:.4f} "
                f"ally_role_completion={components['ally_role_completion_max']:.4f} "
                f"ally_role_overlap={components['ally_role_overlap_max']:.4f}"
            )
        )

    order_profile = recommendation.get("order_profile", {})
    return "\n".join(
        [
            f"team_to_act={recommendation.get('team')}",
            f"pick_order={recommendation.get('pick_order')}",
            f"phase_index={recommendation.get('phase_index')}",
            f"global_pick_index={recommendation.get('global_pick_index')}",
            f"order_profile={order_profile.get('id', 'unknown')}",
            f"order_profile_summary={order_profile.get('summary', 'none')}",
            f"blue_picks={', '.join(blue_picks) if blue_picks else 'none'}",
            f"red_picks={', '.join(red_picks) if red_picks else 'none'}",
            f"blue_bans={', '.join(blue_bans) if blue_bans else 'none'}",
            f"red_bans={', '.join(red_bans) if red_bans else 'none'}",
            *recommendation_lines,
        ]
    )


def _retrieve_principles(
    recommendation: PickRecommendation,
    blue_picks: list[str],
    red_picks: list[str],
    blue_bans: list[str],
    red_bans: list[str],
) -> tuple[str, list[RetrievedPrinciple]]:
    backend_name, model_or_vectorizer, document_matrix = _semantic_backend()
    query = _query_text(recommendation, blue_picks, red_picks, blue_bans, red_bans)

    if backend_name == "sentence-transformers":
        query_vector = cast(EmbeddingEncoder, model_or_vectorizer).encode(
            [query],
            convert_to_tensor=False,
        )
        similarity_scores = cosine_similarity(query_vector, document_matrix)[0]
    else:
        query_vector = cast(QueryVectorizer, model_or_vectorizer).transform([query])
        similarity_scores = cosine_similarity(query_vector, document_matrix)[0]

    ranked_indices = sorted(
        range(len(PICK_DRAFT_PRINCIPLES)),
        key=lambda index: float(similarity_scores[index]),
        reverse=True,
    )[: _local_top_principles()]

    principles: list[RetrievedPrinciple] = []
    for index in ranked_indices:
        principles.append(
            {
                "id": PICK_DRAFT_PRINCIPLES[index]["id"],
                "title": PICK_DRAFT_PRINCIPLES[index]["title"],
                "text": PICK_DRAFT_PRINCIPLES[index]["text"],
                "score": float(similarity_scores[index]),
            }
        )

    return backend_name, principles


def _compact_reason(reason: str) -> str:
    replacements = {
        "safe early priority for this pick window": "safe early priority",
        "keeps the draft flexible early": "early flexibility",
        "extends our first-phase combo": "extends our combo",
        "answers revealed enemy pressure": "answers enemy pressure",
        "sets up the second phase into enemy reveals": "sets up second phase well",
        "stabilizes a missing role before final picks": "stabilizes a role gap",
        "strong late role completion": "late role completion",
        "punishes the nearly finished enemy draft": "punishes the enemy endgame draft",
        "fits strongly with our revealed picks": "strong ally synergy",
        "pressures revealed enemy picks": "good into enemy reveals",
        "cleanly fills our missing role": "fills a role gap",
        "high overall hero power": "high power",
        "keeps our draft flexible": "keeps draft flexible",
        "best overall score for the current pick state": "best overall score",
    }
    return replacements.get(reason, reason)


def _supplemental_reason_labels(item: RecommendationItem) -> list[str]:
    components = item["score_components"]
    scored_labels = [
        ("fills a role gap", float(components.get("ally_role_completion_signal", 0.0))),
        ("high power", float(components.get("secure_power_signal", 0.0))),
        ("strong ally synergy", float(components.get("ally_pick_synergy_signal", 0.0))),
        ("good into enemy reveals", float(components.get("counter_vs_enemy_picks_signal", 0.0))),
        ("keeps draft flexible", float(components.get("flexibility_signal", 0.0))),
    ]
    return [
        label
        for label, score in sorted(scored_labels, key=lambda item: item[1], reverse=True)
        if score >= 0.30
    ]


def _compact_reason_summary(item: RecommendationItem) -> str:
    summary_parts = [_compact_reason(reason) for reason in item["reasons"][:2]]
    for label in _supplemental_reason_labels(item):
        if label not in summary_parts:
            summary_parts.append(label)
        if len(summary_parts) >= 2:
            break

    if not summary_parts:
        return "best overall score"
    return "; ".join(summary_parts[:2])


def _fallback_advice_text(
    recommendation: PickRecommendation,
    retrieved_principles: list[RetrievedPrinciple],
) -> str:
    picks = recommendation.get("recommendations", [])
    if not picks:
        return "No legal picks are available in the current draft state."

    best = picks[0]
    backups = picks[1:3]
    lines = [
        f"Pick {best['hero']} now.",
        f"Why: {_compact_reason_summary(best)}.",
    ]
    if retrieved_principles:
        lines.append(f"Focus: {retrieved_principles[0]['title']}.")
    if backups:
        lines.append(
            "Next: "
            + "; ".join(
                f"{item['hero']} ({_compact_reason_summary(item)})"
                for item in backups
            )
            + "."
        )

    return "\n".join(lines)


def build_pick_advice(
    recommendation: PickRecommendation,
    blue_picks: list[str] | None = None,
    red_picks: list[str] | None = None,
    blue_bans: list[str] | None = None,
    red_bans: list[str] | None = None,
) -> PickAdvice:
    blue_picks = blue_picks or []
    red_picks = red_picks or []
    blue_bans = blue_bans or []
    red_bans = red_bans or []

    if not recommendation.get("recommendations"):
        return {
            "uses_llm": False,
            "provider": "local-semantic",
            "model": None,
            "advice": "No legal picks are available in the current draft state.",
            "retrieved_principles": [],
            "error": None,
        }

    backend_name, retrieved_principles = _retrieve_principles(
        recommendation=recommendation,
        blue_picks=blue_picks,
        red_picks=red_picks,
        blue_bans=blue_bans,
        red_bans=red_bans,
    )
    advice_text = _fallback_advice_text(recommendation, retrieved_principles)

    return {
        "uses_llm": False,
        "provider": "local-semantic",
        "model": backend_name,
        "advice": advice_text,
        "retrieved_principles": retrieved_principles,
        "error": None,
    }
