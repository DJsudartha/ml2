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

export function fetchBanRecommendations(
  payload: RecommendationRequest,
  signal?: AbortSignal,
) {
  return postDraftState<RecommendationResponse>(
    "/draft/recommend-bans",
    payload,
    signal,
  );
}

export function fetchPickRecommendations(
  payload: RecommendationRequest,
  signal?: AbortSignal,
) {
  return postDraftState<RecommendationResponse>(
    "/draft/recommend-picks",
    payload,
    signal,
  );
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
