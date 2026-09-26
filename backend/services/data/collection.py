"""Headless collection selection and honest acceptance reporting."""

from backend.services.data.pick_order_annotations import validate_pick_order_payload


def select_collection_games(games, manifest, matches_per_tournament=5):
    eligible = {e["game_id"] for e in manifest["games"] if e["status"] == "matched"}
    chosen, seen = [], {}
    for game in sorted(games, key=lambda g: (g.get("date") or "", g["game_id"])):
        tournament = game.get("pagename") or game["source_file"]
        match = game.get("liquipedia_match_id")
        matches = seen.setdefault(tournament, set())
        if not match or match in matches or len(matches) >= matches_per_tournament:
            continue
        if game["game_id"] in eligible and all(
            len(set(game[t + "_picks"])) == 5 for t in ("blue", "red")
        ):
            matches.add(match)
            chosen.append(game)
    return chosen


def collection_report(games, manifest, annotations=None):
    entries = {e["game_id"]: e for e in manifest["games"]}
    annotations = {a["game_id"]: a for a in (annotations or {}).get("games", [])}
    rows = []
    for game in games:
        entry = entries.get(game["game_id"], {})
        annotation = annotations.get(game["game_id"])
        complete = False
        if annotation:
            # Validate a copy using the strict schema; do not promote stored status.
            strict = {**annotation, "status": "confirmed"}
            valid = validate_pick_order_payload(
                {"version": 1, "games": [strict]}, {game["game_id"]: game}
            ).is_valid
            complete = valid and all(
                p.get("evidence_frames") or p.get("evidence_frame")
                for p in annotation.get("picks", [])
            )
        rows.append(
            {
                "game_id": game["game_id"],
                "tournament": game.get("pagename"),
                "match_id": game.get("liquipedia_match_id"),
                "game_no": game["game_no"],
                "teams": [game.get("blue_team_name"), game.get("red_team_name")],
                "vod_status": entry.get("status", "missing_vod"),
                "vod_url": entry.get("vod_url"),
                "provenance": entry.get("provenance"),
                "pick_order_status": "complete_suggestion"
                if complete
                else "incomplete"
                if annotation
                else "missing",
                "confirmed": bool(complete and annotation.get("status") == "confirmed"),
            }
        )
    retrieved = [r for r in rows if r["vod_status"] == "matched"]
    return {
        "version": 1,
        "retrieved_games": len(retrieved),
        "distinct_matches": len(
            {(r["tournament"], r["match_id"]) for r in retrieved if r["match_id"]}
        ),
        "tournaments": sorted({r["tournament"] for r in retrieved if r["tournament"]}),
        "complete_pick_orders": sum(
            r["pick_order_status"] == "complete_suggestion" for r in rows
        ),
        "confirmed_pick_orders": sum(r["confirmed"] for r in rows),
        "games": rows,
    }
