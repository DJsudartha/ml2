from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def order():
    return [["A"], ["B", "C"], ["D", "E"], ["F"], ["G"], ["H", "I"], ["J"]]


def result(game_id, score, *, wrong=False):
    proposed = order()
    if wrong:
        proposed[0], proposed[3] = proposed[3], proposed[0]
    return {
        "game_id": game_id, "capture_status": "complete", "usable_evidence": True,
        "identities_complete": True, "recognition_min_score": score,
        "commitment_status": "verified", "commitment_rule_version": "synthetic-lock-v1",
        "commitment_evidence_sha256": "a" * 64, "proposal_batches": proposed,
        "reference": {"provenance": "independent_blind", "reviewed": True,
                      "machine_answers_shown": False, "batches": order()},
    }


def setup_inputs(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    exclusions = tmp_path / "exclusions.json"
    write_json(exclusions, {"version": 1, "entries": [{
        "game_id": "excluded", "liquipedia_match_id": "excluded-match",
        "layout_id": "synthetic-layout", "reason": "development", "source": "synthetic",
    }]})
    calibration = tmp_path / "calibration-results.json"
    write_json(calibration, {"kind": "order_qualification_evidence", "schema_version": 1,
                            "games": [result(f"c{i}", score, wrong=i > 2)
                                      for i, score in enumerate((.9, .8, .7, .6), 1)]})
    evaluation = tmp_path / "evaluation-results.json"
    rows = [result(f"e{i}", score) for i, score in enumerate((.95, .85, .9, .7), 1)]
    rows[0]["proposal_batches"][1].reverse()
    rows[1]["commitment_status"] = "missing"
    rows[2]["proposal_batches"] = []
    rows[2]["identities_complete"] = False
    rows[3]["proposal_batches"][0], rows[3]["proposal_batches"][3] = (
        rows[3]["proposal_batches"][3], rows[3]["proposal_batches"][0])
    write_json(evaluation, {"kind": "order_qualification_evidence", "schema_version": 1, "games": rows})
    manifests = []
    for partition, prefix, evidence in (("calibration", "c", calibration),
                                         ("qualification_evaluation", "e", evaluation)):
        path = tmp_path / f"{prefix}-manifest.json"
        write_json(path, {
            "kind": "order_qualification_manifest", "schema_version": 1,
            "version": f"synthetic-{prefix}-v1", "partition": partition,
            "data_origin": "synthetic", "frozen_at": "2020-01-01",
            "recognition_version": "synthetic-recognition-v1",
            "commitment_rule_version": "synthetic-lock-v1",
            "untouched": partition == "qualification_evaluation",
            "results_exposed_before_policy": False,
            "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
            "games": [{"game_id": f"{prefix}{i}", "match_id": f"{prefix}-match-{i}",
                       "layout_id": "synthetic-layout"} for i in range(1, 5)],
        })
        manifests.append(path)
    requirements = tmp_path / "requirements.json"
    write_json(requirements, {
        "kind": "order_qualification_requirements", "schema_version": 1,
        "version": "synthetic-requirements-v1", "policy_version": "synthetic-policy-v1",
        "minimum_order_precision": 1.0, "minimum_coverage": .25,
        "minimum_calibration_qualified_games": 2, "minimum_evaluation_games": 4,
        "minimum_evaluation_qualified_games": 1,
    })
    evaluation_manifest = json.loads(manifests[1].read_text(encoding="utf-8"))
    evaluation_manifest.update(
        calibration_manifest_sha256=hashlib.sha256(manifests[0].read_bytes()).hexdigest(),
        requirements_sha256=hashlib.sha256(requirements.read_bytes()).hexdigest(),
        exclusion_registry_sha256=hashlib.sha256(exclusions.read_bytes()).hexdigest(),
    )
    write_json(manifests[1], evaluation_manifest)
    return private, exclusions, calibration, evaluation, manifests, requirements


def run_cli(*args):
    return subprocess.run([sys.executable, "-m", "backend.scripts.replay_order_qualification", *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, check=False)


def calibrate_inputs(inputs):
    private, exclusions, calibration, evaluation, manifests, requirements = inputs
    policy, report = private / "policy.json", private / "calibration-report.json"
    run = run_cli("calibrate", "--calibration-manifest", manifests[0], "--evaluation-manifest", manifests[1],
                  "--exclusion-registry", exclusions, "--calibration-results", calibration,
                  "--requirements", requirements, "--policy-output", policy,
                  "--output", report, "--private-root", private)
    return run, (private, exclusions, calibration, evaluation, manifests, requirements, policy, report)


def calibrate(tmp_path):
    return calibrate_inputs(setup_inputs(tmp_path))


def test_calibration_freezes_data_driven_threshold_without_opening_evaluation_results(tmp_path):
    run, (_, _, _, evaluation, manifests, _, policy, report) = calibrate(tmp_path)
    assert run.returncode == 0, run.stderr or run.stdout
    proposal = json.loads(policy.read_text(encoding="utf-8"))
    assert proposal["policy_kind"] == "pick_order_reconstruction_qualification"
    # Two correct games survive at .8; adding .7 admits a wrong complete order.
    assert proposal["recognition_min_score"] == .8
    assert proposal["approval_status"] == "pending"
    assert proposal["lineage"]["evaluation_manifest_sha256"] == hashlib.sha256(manifests[1].read_bytes()).hexdigest()
    data = json.loads(report.read_text(encoding="utf-8"))["verification"]
    assert data["evaluation_payload_inspected"] is False
    assert data["calibration"]["qualified_games"] == 2
    assert data["calibration"]["order_precision"] == 1.0
    assert data["qualification_gate_enabled"] is False
    assert evaluation.exists()


def approval_for(private, policy):
    approval = private / "approval.json"
    write_json(approval, {
        "kind": "order_qualification_approval", "schema_version": 1,
        "policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
        "policy_version": "synthetic-policy-v1", "approved": True,
        "approval_record_id": "synthetic-approval-only", "data_origin": "synthetic",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    })
    return approval


def evaluate(inputs, *, approval=None, output_name="evaluation-report.json"):
    private, exclusions, calibration, evaluation, manifests, requirements, policy, _ = inputs
    output = private / output_name
    args = ["evaluate", "--calibration-manifest", manifests[0], "--evaluation-manifest", manifests[1],
            "--exclusion-registry", exclusions, "--calibration-results", calibration, "--requirements", requirements,
            "--policy", policy, "--evaluation-results", evaluation,
            "--output", output, "--private-root", private]
    if approval:
        args.extend(("--approval", approval))
    return run_cli(*args), output


def test_public_evaluation_separates_game_metrics_and_never_turns_synthetic_success_into_readiness(tmp_path):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    private, _, _, _, _, _, policy, _ = inputs
    run, output = evaluate(inputs, approval=approval_for(private, policy))
    assert run.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    result = report["verification"]
    assert result["evaluation_passed"] is True
    assert result["qualification_gate_enabled"] is False
    assert result["automatic_acceptance_performed"] is False
    assert result["failure_codes"] == ["synthetic_evidence_only"]
    assert result["next_actions"] == ["Supply authorized real independent evidence; synthetic success is only a software check."]
    assert result["frozen_policy"]["recognition_min_score"] == .8
    assert result["frozen_policy"]["requirements"]["minimum_evaluation_games"] == 4
    metrics = result["evaluation"]
    assert metrics["selected_games"] == metrics["selected_matches"] == 4
    assert metrics["complete_games"] == 3
    assert metrics["recognition_complete_games"] == 3
    assert metrics["commitment_verified_games"] == 3
    assert metrics["commitment_status_counts"] == {"missing": 1, "verified": 3}
    assert metrics["qualified_games"] == 1
    assert metrics["correct_qualified_games"] == 1
    assert metrics["order_precision"] == 1.0
    assert metrics["coverage"] == .25
    assert result["sample_unit"] == "one_game_per_distinct_match"
    assert result["recommendation_quality_evaluated"] is False
    assert result["underlying_evidence_bytes_checked"] is False
    assert report["run"]["entry_point"] == "cli_evaluate"


def test_missing_approval_keeps_evaluation_payload_unopened(tmp_path):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    inputs[3].write_bytes(b"PRIVATE_UNTOUCHED_RESULTS")
    run, output = evaluate(inputs)
    assert run.returncode == 1
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["failure_codes"] == ["concrete_policy_approval_missing"]
    assert decision["evaluation_payload_inspected"] is False
    assert decision["qualification_gate_enabled"] is False


@pytest.mark.parametrize("tamper", ["evaluation", "policy", "manifest"])
def test_frozen_hash_mismatches_disable_without_disclosing_payloads_or_paths(tmp_path, tamper):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    private, _, _, evaluation, manifests, _, policy, _ = inputs
    approval = approval_for(private, policy)
    if tamper == "evaluation":
        evaluation.write_bytes(b"PRIVATE_TAMPER_MARKER")
        expected = "input_integrity_mismatch"
    elif tamper == "policy":
        value = json.loads(policy.read_text(encoding="utf-8"))
        value["recognition_min_score"] = .7
        write_json(policy, value)
        expected = "policy_approval_mismatch"
    else:
        value = json.loads(manifests[1].read_text(encoding="utf-8"))
        value["version"] = "tampered"
        write_json(manifests[1], value)
        expected = "frozen_lineage_mismatch"
    run, output = evaluate(inputs, approval=approval)
    assert run.returncode == 1
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["qualification_gate_enabled"] is False
    assert decision["failure_codes"] == [expected]
    visible = run.stdout + run.stderr + output.read_text(encoding="utf-8")
    assert "PRIVATE_TAMPER_MARKER" not in visible
    assert str(tmp_path) not in visible
    assert "Traceback" not in visible


def test_proposal_assisted_evaluation_is_disclosed_and_cannot_score_as_independent_reference(tmp_path):
    inputs = setup_inputs(tmp_path)
    evaluation, manifest = inputs[3], inputs[4][1]
    evidence = json.loads(evaluation.read_text(encoding="utf-8"))
    evidence["games"][0]["reference"].update(provenance="proposal_assisted", machine_answers_shown=True)
    evidence_hash = write_json(evaluation, evidence)
    membership = json.loads(manifest.read_text(encoding="utf-8"))
    membership["evidence_sha256"] = evidence_hash
    write_json(manifest, membership)
    run, inputs = calibrate_inputs(inputs)
    assert run.returncode == 0
    run, output = evaluate(inputs, approval=approval_for(inputs[0], inputs[6]))
    assert run.returncode == 1
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert "evaluation_reference_contaminated" in decision["failure_codes"]
    assert decision["evaluation"]["reference_provenance_counts"] == {"independent_blind": 3, "proposal_assisted": 1}
    assert decision["evaluation"]["order_precision"] is None
    assert decision["evaluation"]["reference_scored_games"] == 0
    assert decision["qualification_gate_enabled"] is False


def test_overlap_is_rejected_before_calibration_payload_access(tmp_path):
    inputs = setup_inputs(tmp_path)
    manifest = inputs[4][1]
    membership = json.loads(manifest.read_text(encoding="utf-8"))
    membership["games"][0]["match_id"] = "c-match-1"
    write_json(manifest, membership)
    inputs[2].write_bytes(b"UNREAD_CALIBRATION")
    run, all_inputs = calibrate_inputs(inputs)
    assert run.returncode == 1
    report = json.loads(all_inputs[7].read_text(encoding="utf-8"))["verification"]
    assert report["failure_codes"] == ["calibration_evaluation_overlap"]
    assert not all_inputs[6].exists()


def test_inadequate_calibration_never_invents_a_numerical_threshold(tmp_path):
    inputs = setup_inputs(tmp_path)
    requirements = json.loads(inputs[5].read_text(encoding="utf-8"))
    requirements["minimum_calibration_qualified_games"] = 5
    requirements_hash = write_json(inputs[5], requirements)
    manifest = json.loads(inputs[4][1].read_text(encoding="utf-8"))
    manifest["requirements_sha256"] = requirements_hash
    write_json(inputs[4][1], manifest)
    run, all_inputs = calibrate_inputs(inputs)
    assert run.returncode == 1
    policy = json.loads(all_inputs[6].read_text(encoding="utf-8"))
    assert policy["recognition_min_score"] is None
    assert policy["calibration"]["qualified_games"] is None
    assert policy["calibration"]["recognition_threshold_games"] is None
    decision = json.loads(all_inputs[7].read_text(encoding="utf-8"))["verification"]
    assert decision["failure_codes"] == ["no_calibration_threshold_meets_proposed_targets"]
    assert decision["qualification_gate_enabled"] is False


def test_replay_verification_repeats_without_reusing_run_identity(tmp_path):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    approval = approval_for(inputs[0], inputs[6])
    reports = []
    for number in range(2):
        run, output = evaluate(inputs, approval=approval, output_name=f"repeat-{number}.json")
        assert run.returncode == 1
        reports.append(json.loads(output.read_text(encoding="utf-8")))
    assert reports[0]["verification"] == reports[1]["verification"]
    assert reports[0]["verification_sha256"] == reports[1]["verification_sha256"]
    assert reports[0]["run"]["run_id"] != reports[1]["run"]["run_id"]


def test_day_precision_approval_does_not_invent_order_with_same_day_policy_freeze(tmp_path):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    approval = approval_for(inputs[0], inputs[6])
    value = json.loads(approval.read_text(encoding="utf-8"))
    value["approved_at"] = value["approved_at"][:10]
    write_json(approval, value)
    run, output = evaluate(inputs, approval=approval)
    assert run.returncode == 1
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["failure_codes"] == ["approval_order_not_established"]


def test_reports_are_new_files_inside_the_explicit_private_root(tmp_path):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    private, _, _, _, _, _, policy, _ = inputs
    original = policy.read_bytes()
    run, _ = evaluate(inputs, output_name="policy.json")
    assert run.returncode == 1
    assert policy.read_bytes() == original
    assert json.loads(run.stdout)["failure_codes"] == ["output_already_exists"]
    run, output = evaluate(inputs, output_name="../outside.json")
    assert run.returncode == 1
    assert not output.exists()
    assert json.loads(run.stdout)["failure_codes"] == ["output_must_be_within_existing_private_root"]
    assert private.is_dir()


def test_frozen_evaluation_contract_rejects_retargeting_before_calibration_payload_access(tmp_path):
    inputs = setup_inputs(tmp_path)
    requirements = json.loads(inputs[5].read_text(encoding="utf-8"))
    requirements["minimum_order_precision"] = .5
    write_json(inputs[5], requirements)
    inputs[2].write_bytes(b"UNREAD_PRIVATE_CALIBRATION")
    run, all_inputs = calibrate_inputs(inputs)
    assert run.returncode == 1
    report = json.loads(all_inputs[7].read_text(encoding="utf-8"))["verification"]
    assert report["failure_codes"] == ["calibration_contract_mismatch"]
    assert not all_inputs[6].exists()


def test_exposed_reference_cannot_be_renamed_independent(tmp_path):
    inputs = setup_inputs(tmp_path)
    evidence = json.loads(inputs[3].read_text(encoding="utf-8"))
    evidence["games"][0]["reference"]["machine_answers_shown"] = True
    evidence_hash = write_json(inputs[3], evidence)
    manifest = json.loads(inputs[4][1].read_text(encoding="utf-8"))
    manifest["evidence_sha256"] = evidence_hash
    write_json(inputs[4][1], manifest)
    run, inputs = calibrate_inputs(inputs)
    assert run.returncode == 0
    run, output = evaluate(inputs, approval=approval_for(inputs[0], inputs[6]))
    assert run.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert report["failure_codes"] == ["reference_provenance_contradiction"]
    assert report["qualification_gate_enabled"] is False


def test_high_recognition_and_complete_proposals_without_commitment_never_qualify(tmp_path):
    inputs = setup_inputs(tmp_path)
    evidence = json.loads(inputs[3].read_text(encoding="utf-8"))
    for row in evidence["games"]:
        row["recognition_min_score"] = .99
        row["commitment_status"] = "missing"
    evidence_hash = write_json(inputs[3], evidence)
    manifest = json.loads(inputs[4][1].read_text(encoding="utf-8"))
    manifest["evidence_sha256"] = evidence_hash
    write_json(inputs[4][1], manifest)
    run, inputs = calibrate_inputs(inputs)
    assert run.returncode == 0
    run, output = evaluate(inputs, approval=approval_for(inputs[0], inputs[6]))
    assert run.returncode == 1
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["evaluation"]["complete_games"] == 3
    assert decision["evaluation"]["recognition_threshold_games"] == 3
    assert decision["evaluation"]["commitment_verified_games"] == 0
    assert decision["evaluation"]["qualified_games"] == 0
    assert decision["evaluation"]["order_precision"] is None
    assert decision["evaluation"]["coverage"] == 0
    assert decision["evaluation_passed"] is False
    assert "insufficient_qualified_games" in decision["failure_codes"]


@pytest.mark.parametrize("mode,code", [("repeated", "games_must_use_distinct_matches"),
                                       ("excluded", "evaluation_contains_excluded_match")])
def test_correlated_or_development_matches_cannot_enter_independent_evaluation(tmp_path, mode, code):
    inputs = setup_inputs(tmp_path)
    manifest = json.loads(inputs[4][1].read_text(encoding="utf-8"))
    manifest["games"][0]["match_id"] = "e-match-2" if mode == "repeated" else "excluded-match"
    write_json(inputs[4][1], manifest)
    run, inputs = calibrate_inputs(inputs)
    assert run.returncode == 1
    decision = json.loads(inputs[7].read_text(encoding="utf-8"))["verification"]
    assert decision["failure_codes"] == [code]
    assert not inputs[6].exists()


def test_recommendation_quality_policy_cannot_be_used_as_reconstruction_qualification(tmp_path):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    policy = json.loads(inputs[6].read_text(encoding="utf-8"))
    policy["policy_kind"] = "recommendation_quality"
    write_json(inputs[6], policy)
    run, output = evaluate(inputs, approval=approval_for(inputs[0], inputs[6]))
    assert run.returncode == 1
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["failure_codes"] == ["wrong_policy_kind"]


@pytest.mark.parametrize("field", ["metric", "source"])
def test_frozen_policy_rejects_different_evaluator_definitions(tmp_path, field):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    policy = json.loads(inputs[6].read_text(encoding="utf-8"))
    assert policy["metric_definition_version"] == "exact-unordered-seven-batch-order-v1"
    if field == "metric":
        policy["metric_definition_version"] = "different-definition"
    else:
        policy["lineage"]["evaluator_source_sha256"] = "0" * 64
    write_json(inputs[6], policy)
    run, output = evaluate(inputs, approval=approval_for(inputs[0], inputs[6]))
    assert run.returncode == 1
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["failure_codes"] == ["evaluator_definition_mismatch"]


@pytest.mark.parametrize("change", ["threshold", "minimum"])
def test_new_approval_cannot_authorize_values_outside_the_precommitted_calibration(tmp_path, change):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    policy = json.loads(inputs[6].read_text(encoding="utf-8"))
    if change == "threshold":
        policy["recognition_min_score"] = .7
    else:
        policy["requirements"]["minimum_evaluation_games"] = 1
    write_json(inputs[6], policy)
    approval = approval_for(inputs[0], inputs[6])
    inputs[3].write_bytes(b"DO_NOT_OPEN_PRIVATE_EVALUATION")
    run, output = evaluate(inputs, approval=approval)
    assert run.returncode == 1
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["failure_codes"] == ["frozen_policy_values_mismatch"]
    assert decision["evaluation_payload_inspected"] is False
    assert decision["qualification_gate_enabled"] is False


@pytest.mark.parametrize("field", ["lineage", "requirements", "calibration"])
@pytest.mark.parametrize("bad_value", [[], None, "PRIVATE_BAD_SHAPE"])
def test_malformed_nested_policy_objects_have_safe_structured_rejections(tmp_path, field, bad_value):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    policy = json.loads(inputs[6].read_text(encoding="utf-8"))
    policy[field] = bad_value
    write_json(inputs[6], policy)
    run, output = evaluate(inputs, approval=approval_for(inputs[0], inputs[6]))
    assert run.returncode == 1
    assert output.exists()
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["failure_codes"] == ["invalid_policy_object_shape"]
    assert decision["qualification_gate_enabled"] is False
    visible = run.stdout + run.stderr + output.read_text(encoding="utf-8")
    assert "PRIVATE_BAD_SHAPE" not in visible
    assert str(tmp_path) not in visible
    assert "Traceback" not in visible


def safe_disabled_decision(run, output, private_fixture):
    assert run.returncode == 1
    assert output.exists()
    visible = run.stdout + run.stderr + output.read_text(encoding="utf-8")
    assert "Traceback" not in visible
    assert str(private_fixture) not in visible
    decision = json.loads(output.read_text(encoding="utf-8"))["verification"]
    assert decision["qualification_gate_enabled"] is False
    return decision


@pytest.mark.parametrize("field,code", [
    ("minimum_order_precision", "invalid_numerical_requirements"),
    ("minimum_calibration_qualified_games", "no_calibration_threshold_meets_proposed_targets"),
])
def test_huge_requirement_integers_do_not_overflow_the_public_calibration(tmp_path, field, code):
    inputs = setup_inputs(tmp_path)
    requirements = json.loads(inputs[5].read_text(encoding="utf-8"))
    requirements[field] = 10 ** 400
    requirements_hash = write_json(inputs[5], requirements)
    manifest = json.loads(inputs[4][1].read_text(encoding="utf-8"))
    manifest["requirements_sha256"] = requirements_hash
    write_json(inputs[4][1], manifest)
    run, inputs = calibrate_inputs(inputs)
    decision = safe_disabled_decision(run, inputs[7], tmp_path)
    assert decision["failure_codes"] == [code]


@pytest.mark.parametrize("partition", ["calibration", "evaluation"])
def test_huge_recognition_integers_have_safe_public_rejections(tmp_path, partition):
    inputs = setup_inputs(tmp_path)
    index = 0 if partition == "calibration" else 1
    evidence_path, manifest_path = inputs[2 + index], inputs[4][index]
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["games"][0]["recognition_min_score"] = 10 ** 400
    evidence_hash = write_json(evidence_path, evidence)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence_sha256"] = evidence_hash
    manifest_hash = write_json(manifest_path, manifest)
    if partition == "calibration":
        evaluation_manifest = json.loads(inputs[4][1].read_text(encoding="utf-8"))
        evaluation_manifest["calibration_manifest_sha256"] = manifest_hash
        write_json(inputs[4][1], evaluation_manifest)
    run, inputs = calibrate_inputs(inputs)
    output = inputs[7]
    if partition == "evaluation":
        assert run.returncode == 0
        run, output = evaluate(inputs, approval=approval_for(inputs[0], inputs[6]))
    decision = safe_disabled_decision(run, output, tmp_path)
    assert decision["failure_codes"] == ["invalid_evidence_fields"]


@pytest.mark.parametrize("field,code", [("threshold", "invalid_frozen_threshold"),
                                       ("target", "invalid_frozen_requirements")])
def test_huge_frozen_policy_numbers_have_safe_public_rejections(tmp_path, field, code):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    policy = json.loads(inputs[6].read_text(encoding="utf-8"))
    if field == "threshold":
        policy["recognition_min_score"] = 10 ** 400
    else:
        policy["requirements"]["minimum_order_precision"] = 10 ** 400
    write_json(inputs[6], policy)
    run, output = evaluate(inputs, approval=approval_for(inputs[0], inputs[6]))
    decision = safe_disabled_decision(run, output, tmp_path)
    assert decision["failure_codes"] == [code]


def test_nonfinite_json_exponent_is_rejected_before_missing_approval_reporting(tmp_path):
    run, inputs = calibrate(tmp_path)
    assert run.returncode == 0
    original = inputs[6].read_text(encoding="utf-8")
    altered = original.replace('"recognition_min_score": 0.8', '"recognition_min_score": 1e400')
    assert altered != original
    inputs[6].write_text(altered, encoding="utf-8")
    run, output = evaluate(inputs)
    decision = safe_disabled_decision(run, output, tmp_path)
    assert decision["failure_codes"] == ["invalid_frozen_threshold"]


def test_json_integer_beyond_runtime_digit_limit_is_a_safe_input_rejection(tmp_path):
    inputs = setup_inputs(tmp_path)
    raw = inputs[5].read_text(encoding="utf-8")
    altered = raw.replace('"minimum_order_precision": 1.0', '"minimum_order_precision": ' + "9" * 5000)
    assert altered != raw
    inputs[5].write_text(altered, encoding="utf-8")
    manifest = json.loads(inputs[4][1].read_text(encoding="utf-8"))
    manifest["requirements_sha256"] = hashlib.sha256(inputs[5].read_bytes()).hexdigest()
    write_json(inputs[4][1], manifest)
    run, inputs = calibrate_inputs(inputs)
    decision = safe_disabled_decision(run, inputs[7], tmp_path)
    assert decision["failure_codes"] == ["input_unavailable_or_invalid_json"]
