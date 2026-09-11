import type {
  DraftAdviceResponse,
  RecommendationRequest,
  RecommendationResponse,
} from "./types/draft";

const DEFAULT_API_BASE = "https://ml-2-8lkf.onrender.com";
const API_BASE = (import.meta.env.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE).replace(/\/$/, "");

async function postDraftState<TResponse>(
  endpoint: string,
  payload: RecommendationRequest
): Promise<TResponse> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Request failed: ${res.status}${detail ? ` ${detail}` : ""}`);
  }

  return (await res.json()) as TResponse;
}

export function fetchBanRecommendations(payload: RecommendationRequest) {
  return postDraftState<RecommendationResponse>("/draft/recommend-bans", payload);
}

export function fetchPickRecommendations(payload: RecommendationRequest) {
  return postDraftState<RecommendationResponse>("/draft/recommend-picks", payload);
}

export function fetchBanAdvice(payload: RecommendationRequest) {
  return postDraftState<DraftAdviceResponse>("/draft/advise-bans", payload);
}

export function fetchPickAdvice(payload: RecommendationRequest) {
  return postDraftState<DraftAdviceResponse>("/draft/advise-picks", payload);
}
