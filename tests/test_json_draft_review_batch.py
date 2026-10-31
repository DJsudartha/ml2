"""Synthetic contract tests for the candidate-only JSON review batch."""

from copy import deepcopy
import json

import pytest

from backend.scripts import build_json_draft_review_batch as subject


def synthetic_case():
    blue = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    red = ["Foxtrot", "Golf", "Hotel", "India", "Juliet"]
    targets = [["Alpha"], ["Foxtrot", "Golf"], ["Bravo", "Charlie"],
               ["Hotel"], ["India"], ["Delta", "Echo"], ["Juliet"]]
    batches = []
    for name, heroes in zip(subject.BATCHES, targets):
        batches.append({"batch": name, "heroes": heroes,
                        "status": "post_roster_reorder_candidate" if name == "R5"
                        else "roster_carryover_candidate",
                        "evidence": [{"timestamp_sec": 1}], "set_elimination_inferences": [],
                        "unresolved_commitment": "Human inspection pending.",
                        "proposal_sha256": subject.sha_json({"batch": name, "heroes": sorted(heroes)})})
    bans = {
        side: [{"hero": f"Ban{start + i}", "source_position": i + 1,
                "phase": "before_B1" if i < 3 else "between_R3_R4"}
               for i in range(5)]
        for side, start in (("blue", 0), ("red", 5))
    }
    identity = next(iter(subject.TARGETS))
    case = {"match_id": identity[0], "game_no": identity[1],
            "game_id": subject.TARGETS[identity],
            "review_status": "unreviewed_proposal", "independent_verification": False,
            "known_heroes": {"blue": blue, "red": red},
            "batches": batches, "phase_aware_bans": bans,
            "complete_candidate": True, "supported_batches": 7,
            "uncertainty": ["Review pending."]}
    subject.validate_case(case)
    case["proposal_sha256"] = subject.sha_json(case)
    return case


def completed_response(case):
    response = subject.make_review_template([case])
    response.update({"reviewer_id": "human", "source_inspection_attested": True,
                     "review_started_at": "2026-10-31T01:00:00+08:00",
                     "review_completed_at": "2026-10-31T02:00:00+08:00"})
    for decision in response["cases"][0]["batches"]:
        decision.update({"decision": "Confirm", "source_inspected": True,
                         "source_note": "Inspected original source.",
                         "reviewed_at": "2026-10-31T01:30:00+08:00"})
    return response


def test_excludes_protected_before_payload_decode(monkeypatch):
    identity = next(iter(subject.TARGETS))
    source = json.dumps({"games": [
        {"match_id": "MPLIDS18W1_0007", "game_no": 1, "game_id": "protected", "secret": "DO_NOT_DECODE"},
        {"match_id": identity[0], "game_no": identity[1], "game_id": subject.TARGETS[identity]},
    ]}).encode()
    original = json.loads

    def guarded(value, *args, **kwargs):
        assert "DO_NOT_DECODE" not in value
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", guarded)
    result = subject.selected_objects(source, "games", {"excluded_matches": ["MPLIDS18W1_0007"]},
                                      {identity: subject.TARGETS[identity]})
    assert list(result) == [identity]


def test_finalized_raw_series_is_not_decoded(monkeypatch):
    raw = json.dumps({"series": [
        {"games": [{"secret": "FINALIZED_DO_NOT_DECODE"}]},
        {"games": [{"liquipedia_match_id": "selected"}]},
    ]}).encode()
    scanner, fields = subject.spans(raw)
    series = scanner.array(fields["series"])
    original = json.loads

    def guarded(value, *args, **kwargs):
        assert "FINALIZED_DO_NOT_DECODE" not in value
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", guarded)
    assert subject.source_game_at(scanner, series, 1, 0)["liquipedia_match_id"] == "selected"


def test_discovery_is_identity_only_and_excludes_known_matches(monkeypatch):
    source = json.dumps([
        {"liquipedia_match_id": "MPLIDS18W1_0007", "game_id": "hidden", "secret": "HOLDOUT"},
        {"liquipedia_match_id": "MPLIDS18W3_0002", "game_id": "known-missing"},
        {"liquipedia_match_id": "MPLIDS18W7_0099",
         "game_id": "MPL_Indonesia_Season_18_Regular_Season_games.json::series99::game1::1",
         "secret": "UNPROCESSED_PAYLOAD"},
    ]).encode()
    original = json.loads

    def guarded(value, *args, **kwargs):
        assert "HOLDOUT" not in value and "UNPROCESSED_PAYLOAD" not in value
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", guarded)
    assert subject.discover_unprocessed_matches(source, {"excluded_matches": ["MPLIDS18W1_0007"]}) == [
        "MPLIDS18W7_0099"]


def test_duplicate_source_identity_rejected():
    identity = next(iter(subject.TARGETS))
    row = {"match_id": identity[0], "game_no": identity[1], "game_id": subject.TARGETS[identity]}
    with pytest.raises(subject.ReviewBatchError, match="duplicate"):
        subject.selected_objects(json.dumps({"games": [row, row]}).encode(), "games",
                                 {"excluded_matches": []}, {identity: subject.TARGETS[identity]})


def test_tampered_pinned_input_rejected(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    original = subject.sha_bytes(source.read_bytes())
    source.write_text("{ }", encoding="utf-8")
    with pytest.raises(subject.ReviewBatchError, match="hash mismatch"):
        subject.pinned(source, original)


def test_tampered_visual_evidence_rejected(tmp_path):
    asset = tmp_path / "asset.jpg"
    asset.write_bytes(b"source crop")
    ref = {"frame": "asset.jpg", "sha256": subject.sha_bytes(asset.read_bytes())}
    assert subject.validate_evidence(tmp_path, [{"evidence": [ref]}]) == (1, 1)
    asset.write_bytes(b"changed crop")
    with pytest.raises(subject.ReviewBatchError, match="evidence hash"):
        subject.validate_evidence(tmp_path, [{"evidence": [ref]}])


def test_pinned_source_read_is_immutable(tmp_path):
    source = tmp_path / "source.json"
    source.write_bytes(b"{\"safe\":true}")
    original = source.read_bytes()
    assert subject.pinned(source, subject.sha_bytes(original)) == original
    assert source.read_bytes() == original


def test_unordered_pairs_are_valid_but_batch_order_is_not():
    case = synthetic_case()
    case["batches"][1]["heroes"].reverse()
    subject.validate_case(case)
    case["batches"][3], case["batches"][4] = case["batches"][4], case["batches"][3]
    with pytest.raises(subject.ReviewBatchError, match="batch identity"):
        subject.validate_case(case)


def test_missing_or_duplicate_hero_and_wrong_team_rejected():
    for replacement in ("Alpha", "Foxtrot"):
        case = synthetic_case()
        case["batches"][3]["heroes"] = [replacement]
        with pytest.raises(subject.ReviewBatchError):
            subject.validate_case(case)
    case = synthetic_case()
    case["batches"].pop()
    with pytest.raises(subject.ReviewBatchError, match="missing batch"):
        subject.validate_case(case)


def test_causal_ban_phases_and_duplicate_ban_rejected():
    case = synthetic_case()
    case["phase_aware_bans"]["red"][3]["phase"] = "before_B1"
    with pytest.raises(subject.ReviewBatchError, match="future ban"):
        subject.validate_case(case)
    case = synthetic_case()
    case["phase_aware_bans"]["red"][3]["hero"] = "Ban0"
    with pytest.raises(subject.ReviewBatchError, match="invalid ban"):
        subject.validate_case(case)


def test_uncertainty_cannot_be_dropped():
    case = synthetic_case()
    case["uncertainty"] = []
    with pytest.raises(subject.ReviewBatchError, match="uncertainty"):
        subject.validate_case(case)


def test_template_is_unconfirmed_and_hash_bound():
    case = synthetic_case()
    response = subject.make_review_template([case])
    subject.validate_review_response({"cases": [case]}, response, complete=False)
    with pytest.raises(subject.ReviewBatchError, match="human identity"):
        subject.validate_review_response({"cases": [case]}, response, complete=True)
    response["cases"][0]["proposal_sha256"] = "bad"
    with pytest.raises(subject.ReviewBatchError, match="proposal hash"):
        subject.validate_review_response({"cases": [case]}, response, complete=False)


def test_conflicting_correction_and_unordered_noop_rejected():
    case = synthetic_case()
    response = completed_response(case)
    decisions = response["cases"][0]["batches"]
    decisions[1]["decision"] = "Correct"
    decisions[1]["corrected_heroes"] = ["Golf", "Foxtrot"]
    with pytest.raises(subject.ReviewBatchError, match="invalid correction"):
        subject.validate_review_response({"cases": [case]}, response, complete=True)
    decisions[1]["corrected_heroes"] = ["Hotel", "Golf"]
    with pytest.raises(subject.ReviewBatchError, match="conflicting corrected"):
        subject.validate_review_response({"cases": [case]}, response, complete=True)


def test_uncertain_review_is_reported_not_auto_confirmed():
    case = synthetic_case()
    response = completed_response(case)
    response["cases"][0]["batches"][6]["decision"] = "Uncertain"
    subject.validate_review_response({"cases": [case]}, response, complete=True)


def test_overwrite_refusal(tmp_path):
    output = tmp_path / "review.json"
    first = {"cases": []}
    assert subject.write_output(output, first) == subject.sha_bytes(output.read_bytes())
    before = output.read_bytes()
    with pytest.raises(subject.ReviewBatchError, match="overwrite"):
        subject.write_output(output, deepcopy(first))
    assert output.read_bytes() == before
