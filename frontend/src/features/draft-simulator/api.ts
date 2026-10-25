import type {
  AdviceResponse,
  RecommendationRequest,
  RecommendationResponse,
} from "./types/draft";

const DEFAULT_API_BASE = "http://127.0.0.1:8000";

export const API_BASE = (
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE
).replace(/\/$/, "");

async function postDraftState<TResponse>(
  endpoint: string,
  payload: RecommendationRequest,
  signal?: AbortSignal,
): Promise<TResponse> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });

  if (!res.ok) {
    throw new Error(`Local draft API request failed: ${res.status}`);
  }

  return res.json() as Promise<TResponse>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function validateRecommendations(value: unknown, payload: RecommendationRequest): RecommendationResponse {
  const unavailable = new Set([...payload.blue_picks, ...payload.red_picks, ...payload.blue_bans, ...payload.red_bans]);
  const seenHeroes = new Set<string>();
  const seenRanks = new Set<number>();
  if (!isRecord(value) || value.team !== payload.team || !Array.isArray(value.recommendations)) {
    throw new Error("Invalid recommendation response.");
  }
  for (const row of value.recommendations) {
    if (!isRecord(row) || typeof row.hero !== "string" || !row.hero.trim() ||
        typeof row.rank !== "number" || !Number.isInteger(row.rank) || row.rank < 1 ||
        typeof row.score !== "number" || !Number.isFinite(row.score) ||
        !Array.isArray(row.reasons) || !row.reasons.every((reason: unknown) => typeof reason === "string") ||
        seenHeroes.has(row.hero) || seenRanks.has(row.rank) || unavailable.has(row.hero)) {
      throw new Error("Invalid or unavailable hero in recommendation response.");
    }
    seenHeroes.add(row.hero);
    seenRanks.add(row.rank);
  }
  if (value.training_context !== undefined &&
      (!isRecord(value.training_context) ||
       (value.training_context.limitation !== null && typeof value.training_context.limitation !== "string"))) {
    throw new Error("Invalid recommendation training context.");
  }
  const response = value as unknown as RecommendationResponse;
  return { ...response, recommendations: [...response.recommendations].sort((a, b) => a.rank - b.rank) };
}

export async function fetchBanRecommendations(
  payload: RecommendationRequest,
  signal?: AbortSignal,
) {
  return validateRecommendations(await postDraftState<unknown>("/draft/recommend-bans", payload, signal), payload);
}

export async function fetchPickRecommendations(
  payload: RecommendationRequest,
  signal?: AbortSignal,
) {
  return validateRecommendations(await postDraftState<unknown>("/draft/recommend-picks", payload, signal), payload);
}

export function fetchBanAdvice(
  payload: RecommendationRequest,
  signal?: AbortSignal,
) {
  return postDraftState<AdviceResponse>("/draft/advise-bans", payload, signal);
}

export function fetchPickAdvice(
  payload: RecommendationRequest,
  signal?: AbortSignal,
) {
  return postDraftState<AdviceResponse>("/draft/advise-picks", payload, signal);
}
