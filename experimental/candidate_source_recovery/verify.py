"""Verify the scoped provisional supplement; never builds labels or reads holdouts."""

import argparse
import hashlib
import json
import re
from pathlib import Path

ALLOW = {("MPLIDS18W5_0004", 2), ("MPLIDS18W6_0003", 1), ("MPLIDS18W7_0003", 1)}
IDS = {
    "MPLIDS18W5_0004": "MPL_Indonesia_Season_18_Regular_Season_games.json::series59::game1::2",
    "MPLIDS18W6_0003": "MPL_Indonesia_Season_18_Regular_Season_games.json::series68::game1::1",
    "MPLIDS18W7_0003": "MPL_Indonesia_Season_18_Regular_Season_games.json::series82::game1::1",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


HOME_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/]|/(?:home|Users)/)[^\\/\s]+"
)


def require_portable_receipt(value):
    if isinstance(value, dict):
        for key, item in value.items():
            require_portable_receipt(key)
            require_portable_receipt(item)
    elif isinstance(value, list):
        for item in value:
            require_portable_receipt(item)
    elif isinstance(value, str):
        require(not HOME_PATH.search(value), "machine-specific home path in public receipt")


def normalize(source):
    m, g = source["metadata"], source["game"]
    identity = (m["match2id"], g["match2gameid"])
    require(type(identity[1]) is int and identity in ALLOW, "non-allowlisted identity")
    require(str(g["subgroup"]) == str(identity[1]), "game identity mismatch")
    require(g["date"] == m["date"] and g["patch"] == m["patch"], "date/patch mismatch")
    require(
        m["pagename"] == "MPL/Indonesia/Season_18/Regular_Season", "wrong tournament"
    )
    e, teams, rosters, bans = g["extradata"], {}, {}, {}
    require(
        {o["id"] for o in m["match2opponents"]} == {1, 2}
        and len(m["match2opponents"]) == 2,
        "opponent identity mismatch",
    )
    for op in m["match2opponents"]:
        n = op["id"]
        side = e[f"team{n}side"]
        require(side in {"blue", "red"} and side not in teams, "invalid sides")
        teams[side] = op["name"]
        rosters[side] = sorted(e[f"team{n}champion{i}"] for i in range(1, 6))
        bans[side] = [
            {
                "position": i,
                "hero": e[f"team{n}ban{i}"],
                "source_field": f"team{n}ban{i}",
            }
            for i in range(1, 6)
        ]
    picks = rosters["blue"] + rosters["red"]
    banned = [b["hero"] for side in bans.values() for b in side]
    require(all(isinstance(x, str) and x.strip() for x in picks + banned), "empty hero")
    require(
        len(set(picks)) == 10
        and len(set(banned)) == 10
        and not set(picks).intersection(banned),
        "duplicate/conflicting heroes",
    )
    return {
        "match_id": identity[0],
        "game_no": identity[1],
        "date": g["date"],
        "patch": g["patch"],
        "teams": teams,
        "rosters": rosters,
        "bans": bans,
    }


def verify(root, receipt_pin=None, original=None):
    root = Path(root)
    receipt_bytes = (root / "receipt.json").read_bytes()
    if receipt_pin is not None:
        require(sha(receipt_bytes) == receipt_pin, "receipt pin mismatch")
    receipt = json.loads(receipt_bytes)
    require_portable_receipt(receipt)
    for name, expected in receipt["files"].items():
        require(Path(name).name == name, "invalid filename")
        require(sha((root / name).read_bytes()) == expected, "file hash mismatch")
    scoped = json.loads((root / "scoped-source.json").read_bytes())
    supplement = json.loads((root / "supplement.json").read_bytes())
    require(
        supplement["production_approved"] is False
        and supplement["training_approved"] is False
        and supplement["human_review_performed"] is False
        and supplement["importer_ready"] is False,
        "approval boundary violation",
    )
    require({tuple(x) for x in scoped["allowlist"]} == ALLOW, "allowlist mismatch")
    raw = None
    if original:
        raw = Path(original).read_bytes()
        require(
            sha(raw) == receipt["original_response"]["sha256"], "original hash mismatch"
        )
    span_by_file = {r["game_file"]: r for r in receipt["source_game_spans"]}
    recovered = {}
    for row in scoped["games"]:
        require(
            (row["match_id"], row["game_no"]) in ALLOW,
            "source identity outside allowlist",
        )
        filename = row["game_file"]
        require(
            Path(filename).name == filename and filename in receipt["files"],
            "unbound source file",
        )
        game_bytes = (root / filename).read_bytes()
        span = span_by_file[filename]
        require(sha(game_bytes) == span["sha256"], "game span hash mismatch")
        game = json.loads(
            game_bytes
        )  # Only exact allowlisted game files reach this decoder.
        if raw is not None:
            require(
                raw[span["start_byte"] : span["end_byte"]] == game_bytes,
                "original span mismatch",
            )
            for key, bounds in span["metadata_spans"].items():
                value = raw[bounds["start_byte"] : bounds["end_byte"]]
                require(sha(value) == bounds["sha256"], "metadata span mismatch")
                decoded = json.loads(value)  # Exact allowed match metadata fields only.
                if key == "match2opponents":
                    decoded = [{"id": o["id"], "name": o["name"]} for o in decoded]
                require(decoded == row["metadata"][key], "source metadata mismatch")
        normalized = normalize({"metadata": row["metadata"], "game": game})
        identity = (normalized["match_id"], normalized["game_no"])
        require(
            identity == (row["match_id"], row["game_no"]) and identity not in recovered,
            "duplicate or aliased source identity",
        )
        recovered[identity] = normalized
    seen = set()
    for row in supplement["games"]:
        identity = (row["match_id"], row["game_no"])
        require(
            identity in ALLOW and identity not in seen,
            "duplicate/unallowed supplement game",
        )
        require(row["game_id"] == IDS[row["match_id"]], "legacy game identity mismatch")
        require(row["review_status"] == "unreviewed_proposal", "review status mismatch")
        seen.add(identity)
        for key, value in recovered[identity].items():
            require(row[key] == value, "normalized field mismatch: " + key)
        baseline = row["candidate_baseline"]
        require(baseline["teams"] == row["teams"], "candidate side/team mismatch")
        require(
            all(
                set(baseline["known_heroes"][side]) == set(row["rosters"][side])
                for side in ("blue", "red")
            ),
            "candidate roster mismatch",
        )
    require(seen == set(recovered) == ALLOW, "missing target game")
    return {
        "games": len(seen),
        "ordered_bans": 30,
        "production_approved": False,
        "training_approved": False,
        "original_response_replayed": raw is not None,
        "receipt_sha256": sha(receipt_bytes),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--receipt-sha256")
    parser.add_argument(
        "--original",
        type=Path,
        help="Opaque original response, hash checked before selected spans",
    )
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.receipt_sha256, args.original), indent=2))
