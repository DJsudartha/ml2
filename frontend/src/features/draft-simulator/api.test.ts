import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API_BASE,
  fetchBanAdvice,
  fetchBanRecommendations,
  fetchPickRecommendations,
  fetchPickAdvice,
  prewarmBackend,
  RETRY_POLICY,
  shouldPrewarmBackend,
} from "./api";
import type {
  AdviceResponse,
  RecommendationRequest,
  RecommendationResponse,
} from "./types/draft";

const payload: RecommendationRequest = {
  team: "blue",
  blue_picks: ["Miya"],
  red_picks: ["Claude"],
  blue_bans: ["Fanny"],
  red_bans: ["Joy"],
  top_k: 2,
  strict_turn: true,
  rerank_pool_size: null,
};

const recommendation: RecommendationResponse = {
  team: "blue",
  recommendations: [
    {
      hero: "Akai",
      rank: 1,
      score: 9.1,
      reasons: ["mock reason"],
    },
  ],
};

const advice: AdviceResponse = {
  recommendation,
  advisor: {
    uses_llm: false,
    provider: "local-semantic",
    model: "mock-advisor",
    advice: "Pick Akai.",
    retrieved_principles: [],
  },
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function mockFetch(response: Partial<Response>) {
  const fetchMock = vi.fn().mockResolvedValue(response as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("draft simulator API client", () => {
  it("defaults to the local backend", () => {
    expect(API_BASE).toBe("http://127.0.0.1:8000");
  });

  it("posts recommendation requests through the shared client", async () => {
    const fetchMock = mockFetch({
      ok: true,
      json: vi.fn().mockResolvedValue(recommendation),
    });

    await expect(fetchBanRecommendations(payload)).resolves.toEqual(
      recommendation,
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/draft/recommend-bans",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
      }),
    );
  });

  it.each([
    ["ban", fetchBanAdvice, "/draft/advise-bans"],
    ["pick", fetchPickAdvice, "/draft/advise-picks"],
  ])("posts %s advice requests to the local backend", async (_action, request, endpoint) => {
    const fetchMock = mockFetch({
      ok: true,
      json: vi.fn().mockResolvedValue(advice),
    });

    await expect(request(payload)).resolves.toEqual(advice);
    expect(fetchMock).toHaveBeenCalledWith(
      `http://127.0.0.1:8000${endpoint}`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("prewarms through health without claiming recommendation readiness", async () => {
    const fetchMock = mockFetch({ ok: true, json: async () => ({ status: "ok" }) });
    expect(shouldPrewarmBackend()).toBe(false);
    expect(shouldPrewarmBackend("https://ml2-backend-sfy3.onrender.com")).toBe(true);
    await expect(prewarmBackend()).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(`${API_BASE}/health`, expect.objectContaining({ method: "GET" }));
  });

  it("does not retry invalid draft states", async () => {
    const fetchMock = mockFetch({ ok: false, status: 422 });

    await expect(fetchBanAdvice(payload)).rejects.toThrow(
      "Draft API request failed: 422",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("retries a transient 503 and recovers with the same draft payload", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 503 })
      .mockResolvedValueOnce({ ok: true, json: async () => recommendation });
    vi.stubGlobal("fetch", fetchMock);
    const onRetry = vi.fn();
    const pending = fetchBanRecommendations(payload, undefined, onRetry);
    await vi.advanceTimersByTimeAsync(RETRY_POLICY.delaysMs[0]);
    await expect(pending).resolves.toEqual(recommendation);
    expect(onRetry).toHaveBeenCalledWith(2, 3);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].body).toBe(JSON.stringify(payload));
  });

  it.each([502, 504])("retries HTTP %i once before recovery", async (status) => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status })
      .mockResolvedValueOnce({ ok: true, json: async () => recommendation });
    vi.stubGlobal("fetch", fetchMock);
    const pending = fetchBanRecommendations(payload);
    await vi.advanceTimersByTimeAsync(1_000);
    await expect(pending).resolves.toEqual(recommendation);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("retries a network failure once before recovery", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ ok: true, json: async () => recommendation });
    vi.stubGlobal("fetch", fetchMock);
    const pending = fetchBanRecommendations(payload);
    await vi.advanceTimersByTimeAsync(1_000);
    await expect(pending).resolves.toEqual(recommendation);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("stops after three transient failures", async () => {
    vi.useFakeTimers();
    const fetchMock = mockFetch({ ok: false, status: 503 });
    const pending = fetchPickRecommendations(payload);
    const rejection = expect(pending).rejects.toThrow("Draft API request failed: 503");
    await vi.advanceTimersByTimeAsync(3_000);
    await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("times out each attempt even if fetch ignores abort", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockImplementation(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);
    const pending = fetchPickRecommendations(payload);
    const rejection = expect(pending).rejects.toThrow("Draft API request timed out.");
    await vi.advanceTimersByTimeAsync(RETRY_POLICY.timeoutMs * 3 + 3_000);
    await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("cancels a pending retry when the draft state changes", async () => {
    vi.useFakeTimers();
    const fetchMock = mockFetch({ ok: false, status: 503 });
    const controller = new AbortController();
    const pending = fetchPickRecommendations(payload, controller.signal);
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(3_000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});


describe("recommendation response validation", () => {
  it("uses the pick endpoint, links abort signals and orders by backend rank", async () => {
    const second = { ...recommendation.recommendations[0], hero: "Yve", rank: 2 };
    const fetchMock = mockFetch({ ok: true, json: async () => ({ ...recommendation, recommendations: [second, recommendation.recommendations[0]] }) });
    const controller = new AbortController();
    const result = await fetchPickRecommendations(payload, controller.signal);
    expect(result.recommendations.map((row) => row.hero)).toEqual(["Akai", "Yve"]);
    expect(fetchMock).toHaveBeenCalledWith(`${API_BASE}/draft/recommend-picks`, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it.each([
    {}, { team: "red", recommendations: [] },
    { ...recommendation, recommendations: [{ hero: "Akai", rank: 1, score: "1", reasons: [] }] },
    { ...recommendation, recommendations: [{ ...recommendation.recommendations[0], hero: "Miya" }] },
    { ...recommendation, recommendations: [{ ...recommendation.recommendations[0], hero: "Fanny" }] },
    { ...recommendation, recommendations: [recommendation.recommendations[0], recommendation.recommendations[0]] },
    { ...recommendation, recommendations: [{ ...recommendation.recommendations[0], reasons: null }] },
  ])("rejects malformed, wrong-team, duplicate or unavailable suggestions: %j", async (value) => {
    mockFetch({ ok: true, json: async () => value });
    await expect(fetchPickRecommendations(payload)).rejects.toThrow(/Invalid/);
  });
});
