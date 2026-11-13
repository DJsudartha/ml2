"""Dependency-free HTTP smoke test for the containerized draft API."""

from __future__ import annotations

import argparse
import json
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url", help="Local container URL, for example http://127.0.0.1:10000")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    opener = build_opener(ProxyHandler({}))

    def request(path: str, payload: dict | None = None, *, headers: dict | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(
            base + path,
            data=data,
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST" if payload is not None else "GET",
        )
        with opener.open(req, timeout=10) as response:
            return response.status, dict(response.headers), json.load(response)

    def check_recommendations(result: dict, state: dict) -> None:
        rows = result["recommendations"]
        assert result["team"] == state["team"] and rows, result
        heroes = [row["hero"] for row in rows]
        unavailable = set().union(
            state.get("blue_picks", []), state.get("red_picks", []),
            state.get("blue_bans", []), state.get("red_bans", []),
        )
        assert len(heroes) == len(set(heroes)) and not unavailable.intersection(heroes), rows
        assert all(isinstance(row["score"], (int, float)) for row in rows), rows

    assert request("/health")[2] == {"status": "ok"}
    first_ban = {"team": "blue", "top_k": 5, "strict_turn": True}
    check_recommendations(request("/draft/recommend-bans", first_ban)[2], first_ban)
    assert "advisor" in request("/draft/advise-bans", first_ban)[2]

    first_pick = {
        "team": "blue", "top_k": 5, "strict_turn": True,
        "blue_bans": ["Atlas", "Selena", "Fanny"],
        "red_bans": ["Freya", "Marcel", "Melissa"],
    }
    check_recommendations(request("/draft/recommend-picks", first_pick)[2], first_pick)
    assert "advisor" in request("/draft/advise-picks", first_pick)[2]

    after_r3 = {
        **first_pick, "team": "red",
        "blue_picks": ["Hirara", "Claude", "Gloo"],
        "red_picks": ["Paquito", "Belerick", "Minotaur"],
    }
    try:
        request("/draft/recommend-picks", after_r3)
    except HTTPError as error:
        assert error.code == 422, error.code
    else:
        raise AssertionError("R4 accepted only six completed bans")
    after_r3["blue_bans"] += ["Mathilda", "Zhuxin"]
    after_r3["red_bans"] += ["Alice", "Uranus"]
    check_recommendations(request("/draft/recommend-picks", after_r3)[2], after_r3)

    preflight = Request(
        base + "/draft/recommend-picks",
        headers={
            "Origin": "https://djsudartha.github.io",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
        method="OPTIONS",
    )
    with opener.open(preflight, timeout=10) as response:
        assert response.status == 200
        assert response.headers["Access-Control-Allow-Origin"] == "https://djsudartha.github.io"
    print("PASS: health, picks, bans, advice, exclusions, R4 boundary, and production CORS")


if __name__ == "__main__":
    main()
