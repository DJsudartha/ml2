"""Export a compact serving artifact from one exact development-only snapshot.

Never accepts a mixed review export. The complete source bytes and each record
identity are allowlisted before any payload is decoded.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

SOURCE_SHA256 = "2db7d7456e1c66794e6c9fc2ab1c27bdbdb46d1ce0df3b89409f9ec0834b9657"
PAYLOAD_SHA256 = "06d622ed38f909c5d8ee914c82f474b5bd1a968bde29cadb0159225d33f23fd3"
ALLOWED_GAMES = tuple(sorted(
    "MPL_Indonesia_Season_18_Regular_Season_games.json::" + suffix
    for suffix in (
        "series33::game1::1", "series34::game1::2", "series1::game1::1",
        "series5::game1::1", "series15::game1::1", "series21::game1::1",
        "series47::game1::1", "series54::game1::1", "series3::game1::1",
        "series20::game1::1", "series28::game1::1", "series56::game1::2",
        "series65::game1::2", "series7::game1::1", "series23::game1::2",
        "series31::game1::2", "series45::game1::1",
    )
))
BATCHES = ("B1", "R1,R2", "B2,B3", "R3", "R4", "B4,B5", "R5")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def approved_payload(raw):
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("Only the pinned development-only snapshot is permitted")
    text = raw.decode("utf-8")
    identities = re.findall(r'"game_id"\s*:\s*"([^"\\]+)"', text)
    if len(identities) < 119 or set(identities) != set(ALLOWED_GAMES):
        raise ValueError("Record identity outside permitted development allowlist")
    envelope = json.loads(text)
    payload = envelope["payload"]
    if envelope["payload_sha256"] != PAYLOAD_SHA256 or digest(payload) != PAYLOAD_SHA256:
        raise ValueError("Snapshot payload hash mismatch")
    if payload["training_game_ids"] != list(ALLOWED_GAMES):
        raise ValueError("Snapshot training allowlist mismatch")
    if payload["counts"] != {"games": 17, "matches": 16, "examples": 119,
                             "positive_memberships": 170}:
        raise ValueError("Unexpected development counts")
    return payload


def export(snapshot, hero_source):
    payload = approved_payload(snapshot.read_bytes())
    rows = payload["dataset"]["examples"]
    grouped = defaultdict(dict)
    for row in rows:
        if row["game_id"] not in ALLOWED_GAMES:
            raise ValueError("Unauthorized development record")
        grouped[row["game_id"]][row["batch_id"]] = row
    bans = {str(phase): {side: Counter() for side in ("blue", "red")}
            for phase in (1, 2)}
    for game in grouped.values():
        if set(game) != set(BATCHES):
            raise ValueError("Incomplete development game")
        for side in ("blue", "red"):
            first = game["B1"]["model_features"][f"prior_{side}_bans"]
            final = game["R4"]["model_features"][f"prior_{side}_bans"]
            if len(first) != 3 or len(final) != 5 or not set(first) <= set(final):
                raise ValueError("Incomplete phase bans")
            bans["1"][side].update(first)
            bans["2"][side].update(set(final) - set(first))
    raw_heroes = hero_source.read_bytes()
    heroes = re.findall(r'name:\s*"([^"\r\n]+)"', raw_heroes.decode())
    if len(heroes) < 100 or len(heroes) != len(set(heroes)):
        raise ValueError("Invalid simulator hero pool")
    return {
        "schema_version": 1, "version": "contextual-backend-mvp-v1",
        "source_snapshot_sha256": SOURCE_SHA256, "source_payload_sha256": PAYLOAD_SHA256,
        "source_dataset_sha256": payload["dataset_sha256"],
        "source_scoring_version": payload["scoring_version"],
        "training_game_ids": list(ALLOWED_GAMES), "training_match_ids": payload["training_match_ids"],
        "counts": payload["counts"], "statistics": payload["statistics"],
        "protocol": payload["protocol"], "ban_phase_counts": bans,
        "simulator_heroes": sorted(heroes),
        "simulator_source_sha256": hashlib.sha256(raw_heroes).hexdigest(),
        "limitations": payload["limitations"],
        "fit_status": payload["fit_status"],
        "source_code_sha256": payload["source_code_sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--hero-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export(args.snapshot, args.hero_source)
    encoded = (json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    with args.output.open("xb") as output:
        output.write(encoded)
    print(hashlib.sha256(encoded).hexdigest())


if __name__ == "__main__":
    main()
