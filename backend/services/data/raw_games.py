from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from backend.services.common.file_utils import load_json

RAW_TOURNAMENTS_DIR = Path("backend/data/raw/tournaments")


ROLE_MAP = {
    1: "EXP",
    2: "Jungle",
    3: "Mid",
    4: "Gold",
    5: "Roam",
}


def extract_hero_names(items: list[dict[str, Any]]) -> list[str]:
    return [item["hero"] for item in items if item.get("hero")]


def game_identifier(game_row: dict[str, Any]) -> str:
    return (
        f"{game_row['source_file']}::series{game_row['series_index']}::"
        f"game{game_row['game_index']}::{game_row['game_no']}"
    )


def _normalize_pick_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    picks: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        hero_name = item.get("hero")
        if not hero_name:
            continue
        slot = int(item.get("slot") or index)
        picks.append(
            {
                "hero": hero_name,
                "slot": slot,
                "role": item.get("role") or ROLE_MAP.get(slot),
            }
        )
    return picks


def _normalize_ban_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bans: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        hero_name = item.get("hero")
        if not hero_name:
            continue
        bans.append(
            {
                "hero": hero_name,
                "ban_order": int(item.get("ban_order") or index),
            }
        )
    return bans


def _build_game_row(
    tournament_path: Path,
    tournament_data: dict[str, Any],
    series: dict[str, Any],
    game: dict[str, Any],
    series_index: int,
    game_index: int,
) -> dict[str, Any]:
    blue_team = _normalize_pick_items(game.get("blue_team", []))
    red_team = _normalize_pick_items(game.get("red_team", []))
    blue_bans = _normalize_ban_items(game.get("blue_bans", []))
    red_bans = _normalize_ban_items(game.get("red_bans", []))

    row = {
        "source_file": tournament_path.name,
        "tournament": tournament_data.get("tournament"),
        "pagename": tournament_data.get("pagename"),
        "series_index": series_index,
        "game_index": game_index,
        "date": series.get("date"),
        "patch": series.get("patch"),
        "blue_team_name": series.get("blue_team_name"),
        "red_team_name": series.get("red_team_name"),
        "game_no": game.get("game_no"),
        "liquipedia_match_id": game.get("liquipedia_match_id"),
        "liquipedia_game_id": game.get("liquipedia_game_id"),
        "vod": game.get("vod") or "",
        "series_vod": game.get("series_vod") or series.get("vod") or "",
        "winner": game.get("winner"),
        "blue_team": blue_team,
        "red_team": red_team,
        "blue_bans_raw": blue_bans,
        "red_bans_raw": red_bans,
        "blue_picks": extract_hero_names(blue_team),
        "red_picks": extract_hero_names(red_team),
        "blue_bans": extract_hero_names(blue_bans),
        "red_bans": extract_hero_names(red_bans),
    }
    row["game_id"] = game_identifier(row)
    return row


@lru_cache(maxsize=4)
def load_raw_games(raw_dir: Path = RAW_TOURNAMENTS_DIR) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for tournament_path in sorted(raw_dir.glob("*.json")):
        tournament_data = load_json(tournament_path)
        if not isinstance(tournament_data, dict):
            continue

        for series_index, series in enumerate(tournament_data.get("series", []), start=1):
            for game_index, game in enumerate(series.get("games", []), start=1):
                rows.append(
                    _build_game_row(
                        tournament_path=tournament_path,
                        tournament_data=tournament_data,
                        series=series,
                        game=game,
                        series_index=series_index,
                        game_index=game_index,
                    )
                )

    return tuple(rows)


def iter_raw_games(raw_dir: Path = RAW_TOURNAMENTS_DIR) -> Iterator[dict[str, Any]]:
    for game_row in load_raw_games(raw_dir):
        yield dict(game_row)


def load_raw_games_by_id(raw_dir: Path = RAW_TOURNAMENTS_DIR) -> dict[str, dict[str, Any]]:
    return {game_row["game_id"]: dict(game_row) for game_row in load_raw_games(raw_dir)}
