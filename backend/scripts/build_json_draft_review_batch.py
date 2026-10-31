"""Build a candidate-only, hash-bound JSON review batch.

Only identity scalars are decoded until the finalized/protected match freeze
has been checked. This module never reads protected review payloads.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

from backend.scripts.build_case_scoped_batch_review import JsonSpans
from backend.services.data.pick_order_media_rights import require_media_rights
from experimental.candidate_source_recovery.verify import verify as verify_recovery


class ReviewBatchError(ValueError):
    """Input, provenance, or review response violates the frozen contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewBatchError(message)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_json(value: object) -> str:
    return sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def pinned(path: Path, expected: str) -> bytes:
    require(path.is_file() and not path.is_symlink(), f"missing or linked input: {path}")
    raw = path.read_bytes()
    require(sha_bytes(raw) == expected, f"input hash mismatch: {path}")
    return raw


BATCHES = ("B1", "R1,R2", "B2,B3", "R3", "R4", "B4,B5", "R5")
SIZES = (1, 2, 2, 1, 1, 2, 1)
TARGETS = {
    ("MPLIDS18W2_0007", 1): "MPL_Indonesia_Season_18_Regular_Season_games.json::series25::game1::1",
    ("MPLIDS18W3_0005", 2): "MPL_Indonesia_Season_18_Regular_Season_games.json::series35::game1::2",
    ("MPLIDS18W4_0005", 1): "MPL_Indonesia_Season_18_Regular_Season_games.json::series49::game1::1",
    ("MPLIDS18W5_0004", 2): "MPL_Indonesia_Season_18_Regular_Season_games.json::series59::game1::2",
    ("MPLIDS18W6_0003", 1): "MPL_Indonesia_Season_18_Regular_Season_games.json::series68::game1::1",
    ("MPLIDS18W7_0003", 1): "MPL_Indonesia_Season_18_Regular_Season_games.json::series82::game1::1",
}
RECOVERED = {"MPLIDS18W5_0004", "MPLIDS18W6_0003", "MPLIDS18W7_0003"}
PROTECTED = {"MPLIDS18W1_0007", "MPLIDS18W1_0008", "MPLIDS18W2_0002", "MPLIDS18W2_0003"}
PINS = {
    "canonical_manifest": "811ad9f508bc3e650966da6322e580154153d93bae500bee08c5096639324483",
    "canonical_dataset": "4840579db55594da6001ceac46f0b901e42ebf0af19896b23df47292535bd318",
    "candidate_manifest": "7ad8bbf12b37228bdf9b243dbcce2b5ba90b416f8802591a72ed4a0fdb2549fc",
    "empty_holdout": "514bcdd6db824c3b6f30254f20ee4a8ef6ec17694ecad21038967a74249b2e0e",
    "readiness": "62d018ca9a5f21739cfccd34299d49d18dd5d2ae1ce5438ad687f5d6ab013b08",
    "candidate_source": "34cebcab11e983f4b612aa75568c9e9c47871e2e85c855eba78c42ccb52120d3",
    "capture_manifest": "f4b25158e07d411ed67359b23ca4ef1c66dff4331fa5a632c18207c251b320cc",
    "selection": "01fe02a4d35604322a17d253723b5d1c0d14dde6f82dfd5ae2e35fb6f54e3ee0",
    "rights": "c1eb88cf562ffd6249bb56ad743ae2ce95e8c6ea20f16e1f5a587a61b3a5c2e5",
    "recovery_receipt": "bbaa2e318b32b9e25c594f0bca7c72cedb1bece824321bc3a6441dc1939309a7",
    "normalized_inventory": "1386ba9af47767fe5f8c409dd116ef986cd34737b9f698e7432e5d590a09ddc8",
}


def spans(raw: bytes) -> tuple[JsonSpans, dict]:
    scanner = JsonSpans(raw.decode("utf-8"))
    return scanner, scanner.document()


def identity_freeze(historical: Path, freeze_path: Path) -> dict:
    manifest_raw = pinned(
        historical / "private/canonical_batch_development_20261031/promotion-manifest.json",
        PINS["canonical_manifest"],
    )
    dataset_raw = pinned(
        historical / "private/canonical_batch_development_20261031/development-examples.json",
        PINS["canonical_dataset"],
    )
    manifest_scanner, manifest_fields = spans(manifest_raw)
    protected = {
        manifest_scanner.scalar(manifest_scanner.object(span)["match_id"])
        for span in manifest_scanner.array(manifest_fields["protected_identities"])
    }
    require(protected == PROTECTED, "protected match inventory changed")
    scanner, fields = spans(dataset_raw)
    rows = scanner.array(fields["examples"])
    identities = []
    for span in rows:
        row = scanner.object(span)
        identities.append((scanner.scalar(row["game_id"]), scanner.scalar(row["match_id"])))
    finalized = {match for _, match in identities}
    require(len(rows) == 133 and len({gid for gid, _ in identities}) == 19
            and len(finalized) == 18 and not finalized & protected, "canonical identity inventory changed")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    require(freeze["kind"] == "next_review_match_exclusions_v1"
            and freeze["canonical_manifest_sha256"] == PINS["canonical_manifest"]
            and freeze["canonical_dataset_file_sha256"] == PINS["canonical_dataset"]
            and freeze["finalized_matches"] == sorted(finalized)
            and freeze["protected_matches"] == sorted(protected)
            and freeze["excluded_matches"] == sorted(finalized | protected), "identity freeze mismatch")
    return freeze


def selected_objects(raw: bytes, array_name: str, freeze: dict, targets: dict = TARGETS) -> dict:
    """Decode only explicitly selected, non-excluded case payloads."""
    scanner, fields = spans(raw)
    found, seen = {}, set()
    for span in scanner.array(fields[array_name]):
        item = scanner.object(span)
        match = scanner.scalar(item["match_id"])
        number = scanner.scalar(item["game_no"])
        gid = scanner.scalar(item["game_id"])
        identity = (match, number)
        require(identity not in seen and isinstance(match, str)
                and type(number) is int and isinstance(gid, str), "duplicate or invalid source identity")
        seen.add(identity)
        if match in freeze["excluded_matches"]:
            continue
        if identity not in targets:
            continue
        require(gid == targets[identity], "requested game identity mismatch")
        found[identity] = json.loads(scanner.text[span[0]:span[1]])
    require(set(found) == set(targets), "selected candidate records incomplete")
    return found


def selected_capture(raw: bytes, freeze: dict, targets: dict = TARGETS) -> dict:
    scanner, fields = spans(raw)
    wanted = set(targets.values())
    found, seen = {}, set()
    for span in scanner.array(fields["sample"]):
        item = scanner.object(span)
        gid = scanner.scalar(item["game_id"])
        require(gid not in seen, "duplicate capture identity")
        seen.add(gid)
        if gid not in wanted:
            continue
        record = json.loads(scanner.text[span[0]:span[1]])
        require(record["game_id"] == gid, "capture identity mismatch")
        found[gid] = record
    require(set(found) == wanted, "selected captures incomplete")
    return found


def empty_holdout(raw: bytes) -> None:
    scanner, fields = spans(raw)
    require(set(fields) == {"schema_version", "kind", "games"}
            and scanner.scalar(fields["schema_version"]) == 1
            and scanner.scalar(fields["kind"]) == "prediction_free_holdout_cases"
            and scanner.array(fields["games"]) == [], "nonempty or invalid holdout envelope")


def discover_unprocessed_matches(raw: bytes, freeze: dict, limit: int = 14) -> list[str]:
    """Identity-only discovery. No game/holdout payload or VOD is decoded."""
    scanner = JsonSpans(raw.decode("utf-8"))
    end, rows = scanner.container(0)
    require(scanner.ws(end) == len(scanner.text), "trailing normalized inventory data")
    selected = {identity[0] for identity in TARGETS}
    omitted = set(freeze["excluded_matches"]) | selected | {"MPLIDS18W3_0002"}
    found = set()
    for span in rows:
        item = scanner.object(span)
        match = scanner.scalar(item["liquipedia_match_id"])
        if match in omitted or not isinstance(match, str) or not match.startswith("MPLIDS18"):
            continue
        gid = scanner.scalar(item["game_id"])
        if isinstance(gid, str) and gid.startswith("MPL_Indonesia_Season_18_"):
            found.add(match)
    return sorted(found)[:limit]


def evidence_frames(value):
    if isinstance(value, dict):
        if "frame" in value and "sha256" in value:
            yield value
        for item in value.values():
            yield from evidence_frames(item)
    elif isinstance(value, list):
        for item in value:
            yield from evidence_frames(item)


def validate_evidence(package: Path, batches: list) -> tuple[int, int]:
    count, unique = 0, set()
    base = package.resolve()
    for evidence in evidence_frames(batches):
        rel = Path(evidence["frame"])
        path = (package / rel).resolve()
        require(not rel.is_absolute() and ".." not in rel.parts
                and path.is_relative_to(base) and path.is_file() and not path.is_symlink(),
                "unsafe or missing evidence frame")
        require(sha_bytes(path.read_bytes()) == evidence["sha256"], "evidence hash mismatch")
        count += 1
        unique.add(str(rel).replace("\\", "/"))
    require(count > 0, "missing visual evidence")
    return count, len(unique)


def ordered_bans(source: dict, roster: dict, teams: dict) -> dict:
    """Retain the source's per-team slot semantics, with no slot-based pick inference."""
    require(source["teams"] == teams, "source team/side mismatch")
    for side in ("blue", "red"):
        require(set(source["rosters"][side]) == set(roster[side]), "source roster mismatch")
    bans = {}
    for side in ("blue", "red"):
        rows = source["bans"][side]
        require([row["position"] for row in rows] == [1, 2, 3, 4, 5], "ban positions incomplete")
        bans[side] = [
            {"hero": row["hero"], "source_position": row["position"],
             "phase": "before_B1" if row["position"] <= 3 else "between_R3_R4",
             "source_pointer": row.get("raw_pointer", row.get("source_field"))}
            for row in rows
        ]
    picks = [hero for side in ("blue", "red") for hero in roster[side]]
    banned = [row["hero"] for side in ("blue", "red") for row in bans[side]]
    require(len(picks) == len(set(picks)) == 10 and len(banned) == len(set(banned)) == 10
            and not set(picks) & set(banned), "duplicate or conflicting pick/ban hero")
    return bans


def validate_case(case: dict) -> None:
    """Validate source identity, seven unordered batch sets, and causal bans."""
    identity = (case["match_id"], case["game_no"])
    require(identity in TARGETS and case["game_id"] == TARGETS[identity], "unapproved case identity")
    require(case["review_status"] == "unreviewed_proposal" and case["independent_verification"] is False,
            "candidate was marked reviewed")
    roster = case["known_heroes"]
    require(set(roster) == {"blue", "red"} and all(len(roster[t]) == 5 for t in roster), "invalid roster")
    require(len(case["batches"]) == 7, "missing batch")
    picked = []
    for index, batch in enumerate(case["batches"]):
        require(batch["batch"] == BATCHES[index] and len(batch["heroes"]) == SIZES[index],
                "batch identity or size mismatch")
        side = "blue" if index in (0, 2, 5) else "red"
        require(set(batch["heroes"]) <= set(roster[side]), "batch team mismatch")
        require(batch["evidence"] and batch["status"] in
                {"roster_carryover_candidate", "post_roster_reorder_candidate"},
                "unsupported batch")
        require(not batch["set_elimination_inferences"], "set elimination is not visual proof")
        picked.extend(batch["heroes"])
    require(len(picked) == len(set(picked)) == 10
            and set(picked) == set(roster["blue"] + roster["red"]), "duplicate/missing pick")
    bans = case["phase_aware_bans"]
    require(set(bans) == {"blue", "red"}, "ban sides mismatch")
    for side in ("blue", "red"):
        require([r["source_position"] for r in bans[side]] == [1, 2, 3, 4, 5]
                and [r["phase"] for r in bans[side]] ==
                ["before_B1"] * 3 + ["between_R3_R4"] * 2, "future ban phase leakage")
    all_bans = [r["hero"] for side in ("blue", "red") for r in bans[side]]
    require(len(set(all_bans)) == 10 and not set(all_bans) & set(picked), "invalid ban identity")
    require(case["complete_candidate"] is True and case["supported_batches"] == 7,
            "incomplete candidate marked complete")
    require(case["uncertainty"] and all(batch["unresolved_commitment"] for batch in case["batches"]),
            "missing uncertainty")


def selected_readiness(raw: bytes, freeze: dict) -> dict:
    subset = {key: value for key, value in TARGETS.items() if key[0] not in RECOVERED}
    return selected_objects(raw, "games", freeze, subset)


def source_game_at(scanner: JsonSpans, series_spans: list, series_index: int,
                   game_index: int) -> dict:
    """Decode one pinned raw game; adjacent finalized games remain opaque."""
    series = scanner.object(series_spans[series_index])
    games = scanner.array(series["games"])
    raw_span = games[game_index]
    return json.loads(scanner.text[raw_span[0]:raw_span[1]])


def first_source(historical: Path, freeze: dict) -> dict:
    readiness = selected_readiness(pinned(
        historical / "private/experimental_candidate_readiness_v1_20261024/candidate-readiness.json",
        PINS["readiness"]), freeze)
    source_raw = pinned(
        historical / "private/experimental_candidate_readiness_v1_20261024/candidate-only-source.json",
        PINS["candidate_source"])
    source_scanner, source_fields = spans(source_raw)
    source_series = source_scanner.array(source_fields["series"])
    result = {}
    for identity, row in readiness.items():
        require(row["metadata_status"] == "exact_retained_raw_join"
                and row["ordered_ban_record"]["ordering_status"] == "confirmed", "unresolved old metadata")
        record = row["ordered_ban_record"]
        si = int(record["series_pointer"].split("/")[-1])
        gi = int(record["game_pointer"].split("/")[-1])
        raw = source_game_at(source_scanner, source_series, si, gi)
        require(raw["liquipedia_match_id"] == identity[0] and raw["game_no"] == identity[1],
                "retained raw identity mismatch")
        require(raw["vod"].startswith("https://www.youtube.com/watch?v="), "raw VOD missing")
        bans = {}
        for side in ("blue", "red"):
            require([x["ban_order"] for x in raw[f"{side}_bans"]] == [1, 2, 3, 4, 5],
                    "retained raw ban slots invalid")
            require([x["hero"] for x in raw[f"{side}_bans"]] ==
                    [x["hero"] for x in record[side]["positions"]], "raw ban identity mismatch")
            require(set(x["hero"] for x in raw[f"{side}_team"]) == set(row["known_heroes"][side]),
                    "retained raw roster mismatch")
            bans[side] = record[side]["positions"]
        result[identity] = {
            "teams": row["teams"], "rosters": row["known_heroes"], "bans": bans,
            "date": row["date"], "patch": row["patch"], "vod": raw["vod"],
            "source_status": "exact_retained_raw_join",
            "unknown_fields": ["source_timezone", "independently_confirmed_tournament_server_patch",
                               "historical_hero_eligibility"],
        }
    return result


def recovered_source(worktree: Path) -> dict:
    folder = worktree / "experimental/candidate_source_recovery"
    verification = verify_recovery(folder, PINS["recovery_receipt"])
    require(verification["games"] == 3 and verification["ordered_bans"] == 30
            and verification["training_approved"] is False, "recovered source verification failed")
    supplement = json.loads((folder / "supplement.json").read_bytes())
    result = {}
    for row in supplement["games"]:
        identity = (row["match_id"], row["game_no"])
        require(identity in TARGETS and identity[0] in RECOVERED and identity not in result,
                "unexpected recovered identity")
        result[identity] = {
            "teams": row["teams"], "rosters": row["rosters"], "bans": row["bans"],
            "date": row["date"], "patch": row["patch"],
            "source_status": "exact_liquipedia_api_v3_recovered_provisional",
            "unknown_fields": row["unknown_fields"],
        }
    require(len(result) == 3, "missing recovered source")
    return result


def make_case(candidate: dict, metadata: dict, capture: dict, package: Path) -> dict:
    identity = (candidate["match_id"], candidate["game_no"])
    require(candidate["game_id"] == capture["game_id"] == TARGETS[identity], "capture/game identity mismatch")
    require(candidate["teams"] == metadata["teams"], "candidate/source team mismatch")
    for side in ("blue", "red"):
        require(set(candidate["known_heroes"][side]) == set(metadata["rosters"][side]),
                "candidate/source picks mismatch")
    url = f"https://www.youtube.com/watch?v={candidate['video_id']}"
    require(capture["video_id"] == candidate["video_id"]
            and capture["live_identity"]["id"] == candidate["video_id"]
            and capture["live_identity"]["channel_id"] == "UC1dGHGJTXU_dkiR8tW3qQgg"
            and capture["game_metadata_verification"]["vod_url"] == url
            and capture["decode_succeeded"] is True, "VOD/source verification mismatch")
    if "vod" in metadata:
        require(metadata["vod"] == url, "raw source VOD mismatch")
    frame_refs, distinct_frames = validate_evidence(package, candidate["batches"])
    batches = deepcopy(candidate["batches"])
    for batch in batches:
        batch["heroes"] = sorted(batch["heroes"])
        batch["proposal_sha256"] = sha_json({"batch": batch["batch"], "heroes": batch["heroes"]})
    timestamps = sorted({frame["timestamp_sec"] for frame in evidence_frames(batches)})
    uncertainty = ["Proposal-assisted visual reconstruction; no human source inspection recorded.",
                   "Persistent selected-roster display is diagnostic, not an independently certified lock.",
                   "R5 post_roster_reorder_candidate evidence must be checked before player-position swaps."]
    uncertainty.extend(f"{b['batch']}: {b['unresolved_commitment']}" for b in batches)
    case = {
        "game_id": candidate["game_id"], "match_id": identity[0], "game_no": identity[1],
        "tournament": "MPL Indonesia Season 18 Regular Season",
        "teams": candidate["teams"], "known_heroes": candidate["known_heroes"],
        "source_date_claim": metadata["date"], "source_patch_claim": metadata["patch"],
        "metadata_status": metadata["source_status"], "metadata_unknown_fields": metadata["unknown_fields"],
        "original_vod_url": url, "authorized_channel_id": capture["live_identity"]["channel_id"],
        "supported_timestamps_sec": timestamps,
        "phase_aware_bans": ordered_bans(metadata, candidate["known_heroes"], candidate["teams"]),
        "ban_evidence": {
            "identity_source": metadata["source_status"],
            "phase_rule": "Liquipedia per-team ban positions 1-3 before B1; 4-5 between R3 and R4",
            "source_vod_ban_timestamps_sec": None,
            "human_verified": False,
        },
        "batches": batches,
        "recognition_ambiguous_observations": candidate["recognition_ambiguous_observations"],
        "complete_candidate": True, "supported_batches": 7,
        "independent_verification": False, "review_status": "unreviewed_proposal",
        "uncertainty": uncertainty,
        "evidence_source": {
            "package": "private/candidate_expansion_20261022/review_package",
            "asset_count_referenced": frame_refs, "distinct_asset_count_hash_verified": distinct_frames,
            "timestamp_basis": "Saved VOD crop sampling positions, not exact lock timestamps",
        },
        "provenance": {
            "candidate_manifest_sha256": PINS["candidate_manifest"],
            "capture_manifest_sha256": PINS["capture_manifest"],
            "metadata_source_sha256": PINS["candidate_source"] if identity[0] not in RECOVERED
            else PINS["recovery_receipt"],
            "metadata_merge_version": "next_review_metadata_join_v2",
            "metadata_record_sha256": sha_json(metadata),
            "original_candidate_record_sha256": sha_json(candidate),
        },
    }
    validate_case(case)
    case["proposal_sha256"] = sha_json(case)
    return case


def make_review_template(cases: list) -> dict:
    return {
        "format": "proposal_assisted_review_response_v1",
        "reviewer_id": None,
        "source_inspection_attested": False,
        "review_started_at": None,
        "review_completed_at": None,
        "proposal_assisted": True,
        "independent_blind_evaluation": False,
        "cases": [
            {"game_id": case["game_id"], "match_id": case["match_id"],
             "game_no": case["game_no"], "proposal_sha256": case["proposal_sha256"],
             "batches": [
                 {"batch": batch["batch"], "proposal_sha256": batch["proposal_sha256"],
                  "decision": None, "corrected_heroes": [], "source_inspected": False,
                  "source_note": None, "reviewed_at": None}
                 for batch in case["batches"]]}
            for case in cases],
    }


def parse_review_response(raw: bytes) -> dict:
    """Reject duplicate keys recursively before any response schema validation."""
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ReviewBatchError(f"invalid JSON constant: {value}")

    try:
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError) as exc:
        raise ReviewBatchError(f"invalid review JSON: {exc}") from exc
    require(isinstance(value, dict), "review JSON must be an object")
    return value


def _review_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _review_decisions(reviewed: dict, proposals: dict) -> list:
    """Expand only an explicit human attestation; never fill omitted batches."""
    identity_fields = {"game_id", "match_id", "game_no", "proposal_sha256"}
    compact = "confirm_all_batches" in reviewed
    require(set(reviewed) == identity_fields | ({"confirm_all_batches"} if compact else {"batches"}),
            "mixed or unknown review case declarations")
    if not compact:
        require(isinstance(reviewed["batches"], list) and reviewed["batches"], "empty review batches")
        return reviewed["batches"]
    attestation = reviewed["confirm_all_batches"]
    require(isinstance(attestation, dict) and set(attestation) == {
        "decision", "all_seven_batches_checked", "batch_proposal_sha256",
        "source_inspected", "source_note", "reviewed_at"}, "invalid compact attestation fields")
    require(attestation["decision"] == "Confirm"
            and attestation["all_seven_batches_checked"] is True and len(proposals) == 7,
            "compact confirmation requires all-seven human attestation")
    require(attestation["batch_proposal_sha256"] == {
        name: proposal["proposal_sha256"] for name, proposal in proposals.items()},
        "compact batch proposal hash mismatch")
    return [{"batch": name, "proposal_sha256": proposal["proposal_sha256"],
             "decision": "Confirm", "corrected_heroes": [],
             "source_inspected": attestation["source_inspected"],
             "source_note": attestation["source_note"], "reviewed_at": attestation["reviewed_at"]}
            for name, proposal in proposals.items()]


def validate_review_response(batch_file: dict, response: dict, *, complete: bool) -> dict:
    """Validate explicit reviewed subsets and return counts, never finalize or mutate."""
    require(isinstance(response, dict) and set(response) == {
        "format", "proposal_assisted", "independent_blind_evaluation", "reviewer_id",
        "source_inspection_attested", "review_started_at", "review_completed_at", "cases"},
        "invalid review response fields")
    require(response["format"] == "proposal_assisted_review_response_v1"
            and response["proposal_assisted"] is True
            and response["independent_blind_evaluation"] is False, "review exposure mismatch")
    cases, source_identities = {}, set()
    for case in batch_file["cases"]:
        gid = case["game_id"]
        identity = (case["match_id"], case["game_no"])
        require(isinstance(gid, str) and isinstance(identity[0], str)
                and type(identity[1]) is int and gid not in cases
                and identity not in source_identities, "duplicate or invalid source case identity")
        names = [batch["batch"] for batch in case["batches"]]
        require(all(isinstance(name, str) for name in names)
                and len(names) == len(set(names)) and set(names) == set(BATCHES),
                "duplicate or invalid source batch identity")
        cases[gid] = case
        source_identities.add(identity)
    require(isinstance(response["cases"], list) and response["cases"], "empty review cases")
    started = _review_time(response["review_started_at"])
    completed = _review_time(response["review_completed_at"])

    def require_interval():
        require(isinstance(response["reviewer_id"], str) and response["reviewer_id"].strip()
                and response["source_inspection_attested"] is True
                and started is not None and completed is not None,
                "missing human identity/attestation/time")
        require(started <= completed, "reversed review interval")

    if complete:
        require_interval()
    counts = {"confirm": 0, "correct": 0, "uncertain": 0,
              "unreviewed": sum(len(case["batches"]) for case in cases.values())}
    seen_cases = set()
    for reviewed in response["cases"]:
        require(isinstance(reviewed, dict), "invalid review case")
        gid = reviewed.get("game_id")
        require(isinstance(gid, str) and gid in cases and gid not in seen_cases,
                "unknown or duplicate review case identity")
        seen_cases.add(gid)
        case = cases[gid]
        require(type(reviewed.get("game_no")) is int
                and (reviewed.get("match_id"), reviewed.get("game_no"), reviewed.get("proposal_sha256"))
                == (case["match_id"], case["game_no"], case["proposal_sha256"]),
                "review proposal hash or identity mismatch")
        proposals = {batch["batch"]: batch for batch in case["batches"]}
        decisions = _review_decisions(reviewed, proposals)
        chosen, seen_batches = [], set()
        for decision in decisions:
            require(isinstance(decision, dict) and set(decision) == {
                "batch", "proposal_sha256", "decision", "corrected_heroes",
                "source_inspected", "source_note", "reviewed_at"}, "invalid review decision fields")
            name = decision["batch"]
            require(isinstance(name, str) and name in proposals and name not in seen_batches,
                    "unknown or duplicate review batch identity")
            seen_batches.add(name)
            proposal = proposals[name]
            require(decision["proposal_sha256"] == proposal["proposal_sha256"], "review batch hash mismatch")
            action = decision["decision"]
            require(action is None or isinstance(action, str)
                    and action in {"Confirm", "Correct", "Uncertain"}, "invalid review decision")
            if action is None:
                require(decision["source_inspected"] is False and decision["corrected_heroes"] == []
                        and decision["source_note"] is None and decision["reviewed_at"] is None,
                        "unconfirmed field was prefilled")
                continue
            require_interval()
            reviewed_at = _review_time(decision["reviewed_at"])
            require(decision["source_inspected"] is True and reviewed_at is not None
                    and isinstance(decision["source_note"], str) and decision["source_note"].strip(),
                    "review lacks source inspection note/time")
            require(started <= reviewed_at <= completed, "reviewed_at outside review interval")
            if action == "Correct":
                heroes = decision["corrected_heroes"]
                require(isinstance(heroes, list) and all(isinstance(hero, str) for hero in heroes)
                        and len(heroes) == len(proposal["heroes"])
                        and len(set(heroes)) == len(heroes) and set(heroes) != set(proposal["heroes"]),
                        "invalid correction")
                side = "blue" if name in {"B1", "B2,B3", "B4,B5"} else "red"
                require(set(heroes) <= set(case["known_heroes"][side]), "correction team mismatch")
                chosen.extend(heroes)
            else:
                require(decision["corrected_heroes"] == [], "conflicting correction")
                if action == "Confirm":
                    chosen.extend(proposal["heroes"])
            counts[action.lower()] += 1
            counts["unreviewed"] -= 1
        require(len(chosen) == len(set(chosen)), "conflicting corrected hero assignments")
        if complete and len(decisions) == 7 and all(
                b["decision"] in {"Confirm", "Correct"} for b in decisions):
            require(len(chosen) == 10, "corrected game incomplete")
    return counts


def write_output(output: Path, value: dict) -> str:
    require(not output.exists(), "refusing to overwrite review batch")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write((json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return sha_bytes(output.read_bytes())


def build(historical: Path, worktree: Path, output: Path) -> dict:
    freeze_path = worktree / "private/json_next_review_batch_20261031/identity-freeze.json"
    freeze = identity_freeze(historical, freeze_path)  # Before candidate payload decoding.
    root = historical / "private/candidate_expansion_20261022/review_package"
    empty_holdout(pinned(root / "holdout/blank_cases.json", PINS["empty_holdout"]))
    candidate_raw = pinned(root / "candidates/original_candidates.json", PINS["candidate_manifest"])
    candidates = selected_objects(candidate_raw, "games", freeze)
    captures = selected_capture(pinned(historical / "private/candidate_expansion_20261022/capture_manifest.json",
                                       PINS["capture_manifest"]), freeze)
    pinned(historical / "private/candidate_expansion_20261022/selection.json", PINS["selection"])
    rights = historical / "private/media_rights.json"
    pinned(rights, PINS["rights"])
    require_media_rights(rights, {"UC1dGHGJTXU_dkiR8tW3qQgg"})
    metadata = first_source(historical, freeze) | recovered_source(worktree)
    require(set(metadata) == set(TARGETS), "incomplete metadata merge")
    cases = [make_case(candidates[key], metadata[key], captures[TARGETS[key]], root)
             for key in TARGETS]
    inventory = pinned(
        historical / "backend/data/raw/pick_order_suggestions/tournament_pipeline/20261009_005112/normalized_games.json",
        PINS["normalized_inventory"])
    discovery = discover_unprocessed_matches(inventory, freeze)
    result = {
        "schema_version": 1, "kind": "candidate_only_json_draft_review_batch",
        "status": "unreviewed_proposals", "production_approved": False,
        "training_approved": False, "finalization_performed": False,
        "proposal_assisted": True, "independent_blind_evaluation": False,
        "scope": {"tournament": "MPL Indonesia Season 18 Regular Season", "selected_games": len(cases),
                  "selected_matches": len({c["match_id"] for c in cases}), "supported_batches": 42,
                  "complete_proposals": len(cases), "partial_proposals": 0,
                  "omitted_from_old_candidate_package": 2,
                  "identity_only_further_matches_to_investigate": discovery,
                  "identity_only_discovery_is_not_a_candidate": True,
                  "economical_evidence_limit": "Six remaining complete cached proposals; further matches have no cached complete phase evidence and need new bounded media processing."},
        "input_hashes": {**PINS, "identity_freeze": sha_bytes(freeze_path.read_bytes())},
        "exclusion_match_ids": freeze["excluded_matches"],
        "metadata_merge": {"version": "next_review_metadata_join_v2",
                           "source_games": {c["match_id"]: c["metadata_status"] for c in cases},
                           "sha256": sha_json({key[0]: metadata[key] for key in TARGETS})},
        "cases": cases,
    }
    result["review_response_template"] = make_review_template(cases)
    validate_review_response(result, result["review_response_template"], complete=False)
    # Recheck source bytes before the exclusive output write.
    pinned(root / "candidates/original_candidates.json", PINS["candidate_manifest"])
    pinned(historical / "private/canonical_batch_development_20261031/development-examples.json",
           PINS["canonical_dataset"])
    digest = write_output(output, result)
    return {"output": str(output), "sha256": digest,
            "complete": len(cases), "partial": 0, "supported_batches": 42}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path)
    parser.add_argument("--worktree", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-response", type=Path)
    parser.add_argument("--review-batch", type=Path)
    parser.add_argument("--review-batch-sha256")
    args = parser.parse_args()
    if args.validate_response:
        require(args.review_batch is not None and args.review_batch_sha256 is not None,
                "review batch and exact SHA-256 required for response validation")
        batch = parse_review_response(pinned(args.review_batch, args.review_batch_sha256))
        response = parse_review_response(args.validate_response.read_bytes())
        counts = validate_review_response(batch, response, complete=True)
        print(json.dumps({"valid": True, "finalized": False,
                          **counts}, indent=2))
        return
    require(args.historical is not None and args.output is not None,
            "historical source and output path required for export")
    print(json.dumps(build(args.historical, args.worktree, args.output), indent=2))


if __name__ == "__main__":
    main()
