import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API_BASE,
  fetchBanAdvice,
  fetchBanRecommendations,
  fetchPickAdvice,
} from "./api";
import type {
  AdviceResponse,
  RecommendationRequest,
  RecommendationResponse,
} from "./types/draft";

const payload: RecommendationRequest = {
  team: "blue",
  blue_picks: ["Akai"],
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

  it("throws a helpful error when the local backend returns an error", async () => {
    mockFetch({ ok: false, status: 503 });

    await expect(fetchBanAdvice(payload)).rejects.toThrow(
      "Local draft API request failed: 503",
    );
  });
});
