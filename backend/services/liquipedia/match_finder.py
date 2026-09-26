from backend.services.liquipedia.liquipedia_api import fetch_table
from backend.services.data.raw_games import ROLE_MAP

def extract_list(prefix: str, count: int, source: dict) -> list[str]:
    values = []
    for i in range(1, count + 1):
        value = source.get(f"{prefix}{i}")
        if value:
            values.append(value)
    return values

def parse_and_normalize_matches(data: dict) -> dict:
    matches = data.get("result", [])

    if not matches:
        return {
            "tournament": None,
            "pagename": None,
            "series": [],
        }

    tournament_name = matches[0].get("tournament")
    pagename = matches[0].get("pagename")

    grouped_series = {}

    for match in matches:
        opponents = match.get("match2opponents", [])
        team1_name = opponents[0].get("name") if len(opponents) > 0 else None
        team2_name = opponents[1].get("name") if len(opponents) > 1 else None

        for game in match.get("match2games", []):
            extradata = game.get("extradata", {})

            team1_side = extradata.get("team1side")
            team2_side = extradata.get("team2side")
            raw_winner = str(game.get("winner") or "")

            team1_picks = []
            team2_picks = []
            team1_bans = []
            team2_bans = []

            for i in range(1, 6):
                curr_team1_pick = extradata.get(f"team1champion{i}")
                curr_team2_pick = extradata.get(f"team2champion{i}")
                curr_team1_ban = extradata.get(f"team1ban{i}")
                curr_team2_ban = extradata.get(f"team2ban{i}")

                if curr_team1_pick:
                    team1_picks.append({
                        "hero": curr_team1_pick,
                        "slot": i,
                        "role": ROLE_MAP[i],
                    })

                if curr_team2_pick:
                    team2_picks.append({
                        "hero": curr_team2_pick,
                        "slot": i,
                        "role": ROLE_MAP[i],
                    })

                if curr_team1_ban:
                    team1_bans.append({
                        "hero": curr_team1_ban,
                        "ban_order": i,
                    })

                if curr_team2_ban:
                    team2_bans.append({
                        "hero": curr_team2_ban,
                        "ban_order": i,
                    })

            if len(team1_picks) != 5 or len(team2_picks) != 5:
                continue

            if team1_side == "blue":
                blue_team_name = team1_name
                red_team_name = team2_name
                blue_team = team1_picks
                red_team = team2_picks
                blue_bans = team1_bans
                red_bans = team2_bans
                winner = "blue" if raw_winner == "1" else "red"

            elif team2_side == "blue":
                blue_team_name = team2_name
                red_team_name = team1_name
                blue_team = team2_picks
                red_team = team1_picks
                blue_bans = team2_bans
                red_bans = team1_bans
                winner = "blue" if raw_winner == "2" else "red"

            else:
                continue

            if raw_winner not in ('1', '2'):
                winner = None

            series_date = game.get("date") or match.get("date")
            series_patch = game.get("patch") or match.get("patch")

            series_key = (
                series_date,
                series_patch,
                blue_team_name,
                red_team_name,
            )

            if series_key not in grouped_series:
                grouped_series[series_key] = {
                    "date": series_date,
                    "patch": series_patch,
                    "blue_team_name": blue_team_name,
                    "red_team_name": red_team_name,
                    "games": [],
                }

            grouped_series[series_key]["games"].append({
                "game_no": game.get("match2gameid"),
                "liquipedia_match_id": match.get("match2id"),
                "liquipedia_game_id": game.get("match2gameid"),
                "vod": game.get("vod") or "",
                "series_vod": match.get("vod") or "",
                "blue_team": blue_team,
                "red_team": red_team,
                "blue_bans": blue_bans,
                "red_bans": red_bans,
                "winner": winner,
            })

    result = {
        "tournament": tournament_name,
        "pagename": pagename,
        "series": list(grouped_series.values()),
    }

    result["series"].sort(key=lambda s: (s["date"] or "", s["blue_team_name"] or "", s["red_team_name"] or ""))
    for series in result["series"]:
        series["games"].sort(key=lambda g: g["game_no"] or 0)

    return result

def get_matches_from_tournament(api_key: str, tournament: dict) -> dict:
    try:
        data = fetch_table(
            api_key,
            "match",
            tournament["wiki"],
            tournament["conditions"],
            limit=1000,
        )
    except Exception as e:
        print(f"Error fetching {tournament.get('name')}: {e}")
        return {
            "tournament": tournament.get("display_name"),
            "pagename": tournament.get("pagename"),
            "series": [],
        }

    if not data or "result" not in data:
        return {
            "tournament": tournament.get("display_name"),
            "pagename": tournament.get("pagename"),
            "series": [],
        }

    grouped = parse_and_normalize_matches(data)

    print(f"{tournament.get('display_name')}: {len(grouped.get('series', []))} series parsed")

    return grouped
