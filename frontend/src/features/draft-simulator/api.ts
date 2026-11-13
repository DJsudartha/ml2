import type {
  AdviceResponse,
  RecommendationRequest,
  RecommendationResponse,
} from "./types/draft";

const DEFAULT_API_BASE = "http://127.0.0.1:8000";

export const API_BASE = (
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE
).replace(/\/$/, "");

// At most three 20-second attempts, with 1s and 2s pauses. This gives a
// sleeping Render service time to wake without polling it between drafts.
export const RETRY_POLICY = {
  timeoutMs: 20_000,
  delaysMs: [1_000, 2_000],
} as const;

export type RetryNotice = (nextAttempt: number, totalAttempts: number) => void;

class HttpStatusError extends Error {
  readonly status: number;

  constructor(status: number) {
    super(`Draft API request failed: ${status}`);
    this.status = status;
  }
}

class RequestTimeoutError extends Error {
  constructor() {
    super("Draft API request timed out.");
  }
}

function abortError() {
  return new DOMException("Request cancelled.", "AbortError");
}

function waitForRetry(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError());
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(abortError());
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function requestOnce<TResponse>(
  endpoint: string,
  payload: RecommendationRequest | undefined,
  signal: AbortSignal | undefined,
  timeoutMs: number,
): Promise<TResponse> {
  if (signal?.aborted) throw abortError();
  const controller = new AbortController();
  let timeout: ReturnType<typeof setTimeout> | undefined;
  let onAbort: (() => void) | undefined;
  const interruption = new Promise<never>((_, reject) => {
    onAbort = () => {
      controller.abort();
      reject(abortError());
    };
    signal?.addEventListener("abort", onAbort, { once: true });
    timeout = setTimeout(() => {
      controller.abort();
      reject(new RequestTimeoutError());
    }, timeoutMs);
  });
  try {
    const request = (async () => {
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: payload ? "POST" : "GET",
        ...(payload ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        } : {}),
        signal: controller.signal,
      });
      if (!res.ok) throw new HttpStatusError(res.status);
      return res.json() as Promise<TResponse>;
    })();
    return await Promise.race([request, interruption]);
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
    if (onAbort) signal?.removeEventListener("abort", onAbort);
  }
}

export function isRetryableDraftError(error: unknown): boolean {
  return error instanceof RequestTimeoutError ||
    error instanceof TypeError ||
    (error instanceof HttpStatusError && [502, 503, 504].includes(error.status)) ||
    (error instanceof DOMException && error.name === "AbortError");
}

async function postDraftState<TResponse>(
  endpoint: string,
  payload: RecommendationRequest,
  signal?: AbortSignal,
  onRetry?: RetryNotice,
): Promise<TResponse> {
  for (let attempt = 0; attempt <= RETRY_POLICY.delaysMs.length; attempt++) {
    try {
      return await requestOnce<TResponse>(endpoint, payload, signal, RETRY_POLICY.timeoutMs);
    } catch (error) {
      if (signal?.aborted) throw abortError();
      if (!isRetryableDraftError(error) || attempt === RETRY_POLICY.delaysMs.length) throw error;
      onRetry?.(attempt + 2, RETRY_POLICY.delaysMs.length + 1);
      await waitForRetry(RETRY_POLICY.delaysMs[attempt], signal);
    }
  }
  throw new Error("Draft API retry policy exhausted.");
}

export function shouldPrewarmBackend(base = API_BASE) {
  return base.startsWith("https://");
}

export async function prewarmBackend(signal?: AbortSignal): Promise<void> {
  const health = await requestOnce<unknown>("/health", undefined, signal, 10_000);
  if (!isRecord(health) || health.status !== "ok") {
    throw new Error("Backend health check failed.");
  }
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
  onRetry?: RetryNotice,
) {
  return validateRecommendations(await postDraftState<unknown>("/draft/recommend-bans", payload, signal, onRetry), payload);
}

export async function fetchPickRecommendations(
  payload: RecommendationRequest,
  signal?: AbortSignal,
  onRetry?: RetryNotice,
) {
  return validateRecommendations(await postDraftState<unknown>("/draft/recommend-picks", payload, signal, onRetry), payload);
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
