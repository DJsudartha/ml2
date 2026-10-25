import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DraftInterface } from "./DraftInterface";

const recommendationResponse = {
  team: "blue",
  recommendations: [{ hero: "Akai", rank: 1, score: 9.25, reasons: ["strong fit for this exact slot"] }],
};

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
});

function mockRecommendationFetch() {
  const fetchMock = vi.fn().mockImplementation((_url: string, init: RequestInit) => Promise.resolve({
    ok: true,
    json: async () => ({ ...recommendationResponse, team: JSON.parse(String(init.body)).team }),
  } as Response));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function requestBodyAt(fetchMock: ReturnType<typeof vi.fn>, index: number) {
  const [, init] = fetchMock.mock.calls[index] as [string, RequestInit];
  return JSON.parse(String(init.body));
}

describe("DraftInterface", () => {
  it("renders the initial draft page without recommendations", () => {
    render(<DraftInterface />);

    expect(screen.getByRole("button", { name: /^start$/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /reset/i })).toBeEnabled();
    expect(screen.getByText("Ban")).toBeInTheDocument();
    expect(screen.getByText("30")).toBeInTheDocument();
    expect(screen.queryByText("Blue Suggestions")).not.toBeInTheDocument();
  });

  it("filters heroes by selected role", async () => {
    const user = userEvent.setup();
    render(<DraftInterface />);

    await user.click(screen.getByRole("button", { name: "Mage" }));
    expect(screen.queryByRole("button", { name: /miya/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /eudora/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByRole("button", { name: /miya/i })).toBeInTheDocument();
  });

  it("uses the local API and shows recommendations for the active team", async () => {
    const fetchMock = mockRecommendationFetch();
    const user = userEvent.setup();
    render(<DraftInterface />);

    await user.click(screen.getByRole("button", { name: /^start$/i }));

    expect(await screen.findByText("Blue Suggestions")).toBeInTheDocument();
    expect(await screen.findByText("#1 Akai")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/draft/recommend-bans",
      expect.objectContaining({ method: "POST" }),
    );
    expect(requestBodyAt(fetchMock, 0)).toEqual({
      team: "blue",
      blue_picks: [],
      red_picks: [],
      blue_bans: [],
      red_bans: [],
      top_k: 3,
      strict_turn: true,
      rerank_pool_size: null,
    });
  });

  it("selects a hero and sends the updated draft state", async () => {
    const fetchMock = mockRecommendationFetch();
    const user = userEvent.setup();
    render(<DraftInterface />);

    await user.click(screen.getByRole("button", { name: /^start$/i }));
    await screen.findByText("#1 Akai");
    await user.click(screen.getByRole("button", { name: /miya/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getAllByAltText("Miya")).toHaveLength(2);
    expect(requestBodyAt(fetchMock, 1).blue_bans).toEqual(["Miya"]);
  });

  it("finds Hirara by role and displays and serializes her canonical pick", async () => {
    const fetchMock = mockRecommendationFetch();
    const user = userEvent.setup();
    render(<DraftInterface />);

    await user.click(screen.getByRole("button", { name: "Mage" }));
    expect(screen.queryByRole("button", { name: /hirara/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "All" }));
    await user.click(screen.getByRole("button", { name: /^start$/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    for (const [index, name] of ["Miya", "Balmond", "Saber", "Alice", "Nana", "Tigreal"].entries()) {
      fireEvent.click(screen.getByText(name));
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(index + 2));
    }

    await user.click(screen.getByRole("button", { name: "Assassin" }));
    await user.click(screen.getByRole("button", { name: /hirara/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(8));

    expect(requestBodyAt(fetchMock, 7).blue_picks).toEqual(["Hirara"]);
    expect(screen.getAllByAltText("Hirara")).toHaveLength(2);
    expect(screen.getByRole("button", { name: /hirara/i })).toBeDisabled();
    for (const icon of screen.getAllByAltText("Hirara")) {
      expect(icon).toHaveAttribute("src", `${import.meta.env.BASE_URL}HeroIcon/Hirara.png`);
    }
  }, 15000);

  it("shows a useful message when the local backend is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 503 } as Response),
    );
    const user = userEvent.setup();
    render(<DraftInterface />);

    await user.click(screen.getByRole("button", { name: /^start$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Recommendations unavailable from http://127.0.0.1:8000",
    );
  });

  it("counts down after the draft starts", async () => {
    vi.useFakeTimers();
    mockRecommendationFetch();
    render(<DraftInterface />);

    fireEvent.click(screen.getByRole("button", { name: /^start$/i }));
    await vi.advanceTimersByTimeAsync(1000);

    expect(screen.getByText("29")).toBeInTheDocument();
  });
});


describe("live recommendation lifecycle", () => {
  it("sends only completed actions throughout all seven batches and both ban phases", async () => {
    const fetchMock = vi.fn().mockImplementation((_url: string, init: RequestInit) => Promise.resolve({
      ok: true, json: async () => ({ team: JSON.parse(String(init.body)).team, recommendations: [] }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<DraftInterface />);
    fireEvent.click(screen.getByRole("button", { name: /^start$/i }));
    // Independent action-level expectation, including partial double-pick turns.
    const actions = [
      ["blue", "ban", "Miya"], ["red", "ban", "Balmond"],
      ["blue", "ban", "Saber"], ["red", "ban", "Alice"],
      ["blue", "ban", "Nana"], ["red", "ban", "Tigreal"],
      ["blue", "pick", "Alucard"], ["red", "pick", "Karina"],
      ["red", "pick", "Akai"], ["blue", "pick", "Franco"],
      ["blue", "pick", "Bane"], ["red", "pick", "Bruno"],
      ["red", "ban", "Clint"], ["blue", "ban", "Rafaela"],
      ["red", "ban", "Eudora"], ["blue", "ban", "Zilong"],
      ["red", "pick", "Layla"], ["blue", "pick", "Fanny"],
      ["blue", "pick", "Lolita"], ["red", "pick", "Minotaur"],
    ] as const;
    const expected = { blue_picks: [] as string[], red_picks: [] as string[], blue_bans: [] as string[], red_bans: [] as string[] };
    const batches: Record<number, string> = { 6: "B1", 7: "R1/R2", 8: "R1/R2", 9: "B2/B3", 10: "B2/B3", 11: "R3", 16: "R4", 17: "B4/B5", 18: "B4/B5", 19: "R5" };
    for (const [index, [team, action, hero]] of actions.entries()) {
      await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(index + 1));
      expect(requestBodyAt(fetchMock, index)).toEqual({ ...expected, team, top_k: 3, strict_turn: true, rerank_pool_size: null });
      expect(fetchMock.mock.calls[index][0]).toBe(`http://127.0.0.1:8000/draft/recommend-${action === "pick" ? "picks" : "bans"}`);
      expect(screen.getByLabelText("Draft phase")).toHaveTextContent(batches[index] ?? `Ban phase ${index < 6 ? 1 : 2}`);
      fireEvent.click(screen.getByText(hero));
      expected[`${team}_${action === "pick" ? "picks" : "bans"}`].push(hero);
    }
    expect(screen.queryByText(/Suggestions$/)).not.toBeInTheDocument();
    expect(screen.getAllByText("Complete")).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(20);
    expect(Object.values(expected).map((rows) => rows.length)).toEqual([5, 5, 5, 5]);
  }, 15000);

  it("ignores late success and errors even when fetch ignores abort, including reset and restart", async () => {
    const pending: { resolve: (value: Response) => void; reject: (error: Error) => void }[] = [];
    const fetchMock = vi.fn().mockImplementation(() => new Promise<Response>((resolve, reject) => pending.push({ resolve, reject })));
    vi.stubGlobal("fetch", fetchMock);
    render(<DraftInterface />);
    fireEvent.click(screen.getByRole("button", { name: /^start$/i }));
    fireEvent.click(screen.getByRole("button", { name: /miya/i }));
    await act(async () => pending[1].resolve({ ok: true, json: async () => ({ ...recommendationResponse, team: "red" }) } as Response));
    expect(await screen.findByText("#1 Akai")).toBeInTheDocument();
    await act(async () => pending[0].resolve({ ok: true, json: async () => ({ ...recommendationResponse, recommendations: [{ ...recommendationResponse.recommendations[0], hero: "Nana" }] }) } as Response));
    expect(screen.queryByText("#1 Nana")).not.toBeInTheDocument();
    expect(screen.getByText("#1 Akai")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /balmond/i }));
    expect(screen.queryByText("#1 Akai")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    fireEvent.click(screen.getByRole("button", { name: /reset/i }));
    fireEvent.click(screen.getByRole("button", { name: /^start$/i }));
    await act(async () => pending[3].resolve({ ok: true, json: async () => recommendationResponse } as Response));
    await act(async () => pending[2].reject(new Error("late error")));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("#1 Akai")).toBeInTheDocument();
    expect(requestBodyAt(fetchMock, 3).blue_bans).toEqual([]);
  }, 15000);

  it("resets the timer after the first automatic member of a double-pick batch", async () => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0);
    const fetchMock = vi.fn().mockImplementation((_url: string, init: RequestInit) => Promise.resolve({
      ok: true, json: async () => ({ team: JSON.parse(String(init.body)).team, recommendations: [] }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<DraftInterface />);
    fireEvent.click(screen.getByRole("button", { name: /^start$/i }));
    for (const hero of ["Miya", "Balmond", "Saber", "Alice", "Nana", "Tigreal", "Alucard"]) {
      fireEvent.click(screen.getByText(hero));
    }
    expect(screen.getByLabelText("Draft phase")).toHaveTextContent("R1/R2 / 2 remaining");
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByLabelText("Draft phase")).toHaveTextContent("R1/R2 / 1 remaining");
    expect(screen.getByText("30")).toBeInTheDocument();
    const countAfterFirst = fetchMock.mock.calls.length;
    const payload = requestBodyAt(fetchMock, countAfterFirst - 1);
    expect(payload.team).toBe("red");
    expect(payload.blue_picks).toEqual(["Alucard"]);
    expect(payload.red_picks).toHaveLength(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(fetchMock).toHaveBeenCalledTimes(countAfterFirst);
    expect(screen.getByLabelText("Draft phase")).toHaveTextContent("R1/R2 / 1 remaining");
  });

  it("distinguishes successful empty results from malformed responses", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ team: "blue", recommendations: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ team: "red" }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<DraftInterface />);
    fireEvent.click(screen.getByRole("button", { name: /^start$/i }));
    expect(await screen.findByText("No recommendations returned for this draft state.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /miya/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid recommendation response");
    expect(screen.queryByText("No recommendations returned for this draft state.")).not.toBeInTheDocument();
  });
});
