from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def artifact(root, name, payload):
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    (root / name).write_bytes(raw)
    return {"path": name, "sha256": hashlib.sha256(raw).hexdigest()}


def bundle(root):
    # All records, names, dates, judgments and numbers below are synthetic test material.
    policy = {
        "schema_version": 1, "kind": "recommendation_quality_policy", "version": "synthetic-policy-v1",
        "metric_version": "match_certified_option_precision_v1", "pair_scoring": "unordered_exact_option_v1",
        "top_k": 1, "precision_target": 0.9, "min_matches": 4, "min_matches_per_stratum": 4,
        "min_response_coverage": 1.0, "min_judgment_coverage": 1.0, "non_regression_margin": 0.05,
        "confidence": 0.8, "bootstrap_replicates": 1000, "bootstrap_seed": 7, "max_interval_width": 0.1,
        "required_strata": {"stage": ["B1"], "side": ["blue"],
                            "patch_evidence": ["verified"], "coverage": ["supported"]},
    }
    cases = [{
        "case_id": f"synthetic-{i}", "match_id": f"synthetic-match-{i}", "stage": "B1", "side": "blue",
        "patch_evidence": "verified", "coverage": "supported",
        "eligible_heroes": ["Alpha", "Beta"], "unavailable_heroes": [],
        "eligibility_evidence_sha256": "2" * 64, "review_provenance": "independent",
        "review_record_sha256": "1" * 64, "reviewed_on": "2000-01-01",
        "options": [{"heroes": ["Alpha"], "judgment": "acceptable", "composition": "pass"},
                    {"heroes": ["Beta"], "judgment": "unacceptable", "composition": "pass"}],
    } for i in range(4)]
    benchmark = {"schema_version": 1, "kind": "expert_draft_quality_benchmark",
                 "version": "synthetic-benchmark-v1", "synthetic": True, "cases": cases}
    policy_ref = artifact(root, "policy.json", policy)
    benchmark_ref = artifact(root, "benchmark.json", benchmark)
    replay = {
        "schema_version": 1, "kind": "recommendation_quality_replay", "version": "synthetic-replay-v1",
        "synthetic": True, "policy_sha256": policy_ref["sha256"],
        "benchmark_sha256": benchmark_ref["sha256"], "results_first_seen_on": "2000-01-02",
        "judgments_hidden_from_recommender": True,
        "cases": [{"case_id": case["case_id"], "candidate": [["Alpha"]], "incumbent": [["Beta"]]}
                  for case in cases],
    }
    replay_ref = artifact(root, "replay.json", replay)
    manifest = {
        "schema_version": 1, "kind": "draft_quality_manifest", "version": "synthetic-selection-v1",
        "synthetic": True, "selection": [{"case_id": c["case_id"], "match_id": c["match_id"]} for c in cases],
        "artifacts": {"policy": policy_ref, "benchmark": benchmark_ref, "replay": replay_ref},
        "freeze": {"policy_before_results": True, "judgments_before_results": True,
                   "benchmark_before_results": True, "excluded_from_development": True,
                   "earlier_result_exposure": "none"},
    }
    return manifest, policy, benchmark, replay


def run_replay(root, manifest, output="report.json"):
    manifest_ref = artifact(root, "manifest.json", manifest)
    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.replay_draft_quality_policy",
         "--manifest", str(root / "manifest.json"), "--manifest-sha256", manifest_ref["sha256"],
         "--output", output],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    report_path = root / output
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    return result, report


def test_public_replay_reports_worked_match_metrics_without_approving_synthetic_evidence(tmp_path):
    manifest, _, _, _ = bundle(tmp_path)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0, result.stderr or result.stdout
    verification = report["verification"]
    assert verification["kind"] == "recommendation_quality_policy_report"
    assert verification["status"] == "evaluated"
    assert verification["metric_checks_passed"] is True
    assert verification["metrics"]["candidate"]["match_count"] == 4
    assert verification["metrics"]["candidate"]["judged_precision"] == 1.0
    assert verification["metrics"]["candidate"]["certified_precision"] == 1.0
    assert verification["metrics"]["candidate"]["judgment_coverage"] == 1.0
    assert verification["metrics"]["incumbent"]["judged_precision"] == 0.0
    assert verification["comparison"]["paired_match_difference"] == 1.0
    assert verification["comparison"]["lower_bound"] == 1.0
    assert verification["release_gate"]["enabled"] is False
    assert "synthetic_evidence" in verification["release_gate"]["reasons"]
    assert "policy_not_approved" in verification["release_gate"]["reasons"]
    assert verification["activation"] == "not_attempted"
    assert report["run"]["entry_point"] == "cli_replay"

def test_invalid_identity_selection_is_rejected_before_benchmark_payload_access(tmp_path):
    manifest, _, _, _ = bundle(tmp_path)
    manifest["selection"][1] = manifest["selection"][0].copy()
    manifest["artifacts"]["benchmark"]["sha256"] = "0" * 64
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 1
    assert report["verification"]["failure"]["code"] == "selection_mismatch"

def approval(root, manifest, **changes):
    record = {
        "schema_version": 1, "kind": "recommendation_quality_policy_approval", "status": "approved",
        "policy_sha256": manifest["artifacts"]["policy"]["sha256"],
        "benchmark_sha256": manifest["artifacts"]["benchmark"]["sha256"],
        "approved_on": "2000-01-01", "reference": "synthetic record only; no actual approval",
        "independent_evidence_verified": True,
    }
    record.update(changes)
    manifest["artifacts"]["approval"] = artifact(root, "approval.json", record)


def test_malformed_approval_is_rejected_without_echoing_private_fields(tmp_path):
    manifest, _, _, _ = bundle(tmp_path)
    approval(tmp_path, manifest, reference=["PRIVATE_APPROVAL_MARKER"])
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 1
    assert report["verification"]["failure"]["code"] == "approval_invalid"
    assert "PRIVATE_APPROVAL_MARKER" not in json.dumps(report) + result.stdout + result.stderr

def replace_inputs(root, manifest, policy, benchmark, replay):
    manifest["artifacts"]["policy"] = artifact(root, "policy.json", policy)
    manifest["artifacts"]["benchmark"] = artifact(root, "benchmark.json", benchmark)
    replay["policy_sha256"] = manifest["artifacts"]["policy"]["sha256"]
    replay["benchmark_sha256"] = manifest["artifacts"]["benchmark"]["sha256"]
    manifest["artifacts"]["replay"] = artifact(root, "replay.json", replay)


def test_review_dates_after_results_are_rejected_without_inventing_timestamps(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    benchmark["cases"][0]["reviewed_on"] = "2000-01-03"
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 1
    assert report["verification"]["failure"]["code"] == "chronology_mismatch"

def test_report_versions_metric_denominators_and_bootstrap_assumptions(tmp_path):
    manifest, _, _, _ = bundle(tmp_path)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    verification = report["verification"]
    assert verification["metric_version"] == "match_certified_option_precision_v1"
    assert verification["pair_scoring"] == "unordered_exact_option_v1"
    assert verification["metric_denominators"]["certified_precision"] == (
        "equal_weight_match_rates_over_frozen_expected_option_slots")
    assert verification["metric_denominators"]["judged_precision"] == "acceptable_plus_unacceptable_emitted_options"
    assert verification["statistical_assumptions"]["resampling_unit"] == "independent_match"
    assert verification["statistical_assumptions"]["within_match_dependence"] == "preserved"
    assert verification["source_evidence_bytes_checked"] is False

def test_unjudged_options_and_abstention_keep_fixed_denominators(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    for case in benchmark["cases"]:
        case["eligible_heroes"].append("Gamma")
    replay["cases"][1]["candidate"] = [["Gamma"]]
    replay["cases"][2]["candidate"] = []
    replay["cases"][3]["candidate"] = [["Beta"]]
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    metrics = report["verification"]["metrics"]["candidate"]
    assert metrics["acceptable"] == 1
    assert metrics["unacceptable"] == 1
    assert metrics["unjudged"] == 1
    assert metrics["judged_precision"] == 0.5
    assert metrics["judgment_coverage"] == 2 / 3
    assert metrics["response_coverage"] == 0.75
    assert metrics["certified_precision"] == 0.25
    assert metrics["expected"] == 4
    assert metrics["abstained_cases"] == 1
    assert report["verification"]["metric_checks_passed"] is False


def test_pair_judgments_are_unordered_exact_options(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    for case in benchmark["cases"]:
        case.update(stage="R1,R2", side="red", eligible_heroes=["Alpha", "Beta", "Gamma"])
        case["options"] = [
            {"heroes": ["Alpha", "Beta"], "judgment": "acceptable", "composition": "pass"},
            {"heroes": ["Beta", "Gamma"], "judgment": "unacceptable", "composition": "pass"},
        ]
    policy["required_strata"]["stage"] = ["R1,R2"]
    policy["required_strata"]["side"] = ["red"]
    for row in replay["cases"]:
        row.update(candidate=[["Beta", "Alpha"]], incumbent=[["Gamma", "Beta"]])
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    assert report["verification"]["metrics"]["candidate"]["acceptable"] == 4
    assert report["verification"]["metrics"]["candidate"]["illegal"] == 0
    assert report["verification"]["strata"]["stage"]["R1,R2"]["metrics"]["candidate"]["match_count"] == 4


def test_unapproved_real_envelope_does_not_open_candidate_results(tmp_path):
    # This is a synthetic guard test shaped as a real-input envelope, not actual expert evidence.
    manifest, policy, benchmark, replay = bundle(tmp_path)
    manifest["synthetic"] = benchmark["synthetic"] = replay["synthetic"] = False
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    (tmp_path / "replay.json").write_bytes(b"PRIVATE_RESULT_MARKER")
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    assert report["verification"]["status"] == "disabled"
    assert report["verification"]["replay_bytes_checked"] is False
    assert "policy_not_approved" in report["verification"]["release_gate"]["reasons"]
    assert "metrics" not in report["verification"]
    assert "PRIVATE_RESULT_MARKER" not in json.dumps(report) + result.stdout + result.stderr


def test_proposal_assisted_evidence_is_disclosed_and_cannot_be_independent_gold(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    manifest["synthetic"] = benchmark["synthetic"] = replay["synthetic"] = False
    for case in benchmark["cases"]:
        case["review_provenance"] = "proposal_assisted"
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    approval(tmp_path, manifest)
    (tmp_path / "replay.json").write_bytes(b"PRIVATE_RESULT_MARKER")
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    verification = report["verification"]
    assert verification["replay_bytes_checked"] is False
    assert verification["declared_review_provenance"] == {
        "independent": 0, "proposal_assisted": 4, "unreviewed": 0}
    assert "missing_independent_expert_evidence" in verification["release_gate"]["reasons"]


def test_missing_numerical_policy_is_explicitly_disabled_before_payload_evaluation(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    policy.pop("min_matches")
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    manifest["artifacts"]["benchmark"]["sha256"] = "0" * 64
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    verification = report["verification"]
    assert verification["status"] == "disabled"
    assert verification["missing_policy_fields"] == ["min_matches"]
    assert verification["release_gate"]["reasons"] == ["numerical_policy_incomplete"]
    assert verification["replay_bytes_checked"] is False


def test_multiple_positions_from_one_match_do_not_inflate_independent_sample_count(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    for identity in manifest["selection"]:
        identity["match_id"] = "synthetic-shared-match"
    for case in benchmark["cases"]:
        case["match_id"] = "synthetic-shared-match"
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    verification = report["verification"]
    assert verification["metrics"]["candidate"]["match_count"] == 1
    assert "insufficient_independent_matches" in verification["metric_failures"]
    assert verification["release_gate"]["enabled"] is False


def test_interval_width_disables_an_imprecise_small_replay(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    replay["cases"][2]["candidate"] = [["Beta"]]
    replay["cases"][3]["candidate"] = [["Beta"]]
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    verification = report["verification"]
    assert verification["metrics"]["candidate"]["precision_interval"]["interval_width"] > 0.1
    assert "uncertainty_too_wide" in verification["metric_failures"]


def test_legality_and_composition_failures_are_distinct_from_expert_judgments(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    for case in benchmark["cases"]:
        case["unavailable_heroes"] = ["Beta"]
        case["options"][1]["composition"] = "fail"
    for row in replay["cases"]:
        row["candidate"] = [["Beta"]]
        row["incumbent"] = [["Alpha"]]
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    metrics = report["verification"]["metrics"]["candidate"]
    assert metrics["illegal"] == 4
    assert metrics["composition_fail"] == 4
    assert metrics["unacceptable"] == 4
    assert report["verification"]["release_gate"]["enabled"] is False


@pytest.mark.parametrize("change,code", [
    ("tamper", "input_integrity_mismatch"),
    ("path", "unsafe_path"),
    ("kind", "policy_kind_mismatch"),
    ("lineage", "replay_lineage_mismatch"),
])
def test_tampered_mismatched_or_wrong_kind_inputs_fail_closed(tmp_path, change, code):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    if change == "tamper":
        (tmp_path / "policy.json").write_bytes(b"PRIVATE_TAMPER_MARKER")
    elif change == "path":
        manifest["artifacts"]["policy"]["path"] = "../outside-private-policy.json"
    elif change == "kind":
        policy["kind"] = "reconstruction_qualification_policy"
        replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    else:
        replay["policy_sha256"] = "0" * 64
        manifest["artifacts"]["replay"] = artifact(tmp_path, "replay.json", replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 1
    assert report["verification"]["failure"]["code"] == code
    assert report["verification"]["release_gate"]["enabled"] is False
    visible = json.dumps(report) + result.stdout + result.stderr
    assert "PRIVATE_TAMPER_MARKER" not in visible
    assert str(tmp_path) not in visible
    assert "Traceback" not in visible


def test_private_path_cannot_be_echoed_as_a_policy_version(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    policy["version"] = "C:/private/PRIVATE_VERSION_MARKER"
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 1
    assert "PRIVATE_VERSION_MARKER" not in json.dumps(report) + result.stdout + result.stderr


def test_repeated_replay_is_stable_and_existing_report_is_never_overwritten(tmp_path):
    manifest, _, _, _ = bundle(tmp_path)
    first_result, first = run_replay(tmp_path, manifest, "first.json")
    second_result, second = run_replay(tmp_path, manifest, "second.json")
    assert first_result.returncode == second_result.returncode == 0
    assert first["verification"] == second["verification"]
    assert first["verification_sha256"] == second["verification_sha256"]
    assert first["run"]["run_id"] != second["run"]["run_id"]
    original = (tmp_path / "first.json").read_bytes()
    collision, _ = run_replay(tmp_path, manifest, "first.json")
    assert collision.returncode == 1
    assert (tmp_path / "first.json").read_bytes() == original
    assert json.loads(collision.stdout)["failure"]["code"] == "report_output_unavailable"

def test_missing_predeclared_stratum_disables_policy_readiness(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    policy["required_strata"]["stage"].append("R5")
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    verification = report["verification"]
    assert "missing_required_stratum" in verification["metric_failures"]
    assert verification["strata"]["stage"]["R5"]["failures"] == ["missing_required_stratum"]
    assert verification["release_gate"]["enabled"] is False

@pytest.mark.parametrize("responding_cases", [0, 1])
def test_empty_or_selectively_emitted_output_cannot_manufacture_precision(tmp_path, responding_cases):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    for row in replay["cases"][responding_cases:]:
        row["candidate"] = []
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    metrics = report["verification"]["metrics"]["candidate"]
    assert metrics["judged_precision"] == (1.0 if responding_cases else None)
    assert metrics["certified_precision"] == responding_cases / 4
    assert metrics["expected"] == 4
    assert report["verification"]["metric_checks_passed"] is False


def test_partial_top_k_is_not_treated_as_complete_output(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    policy["top_k"] = 2
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    metrics = report["verification"]["metrics"]["candidate"]
    assert metrics["expected"] == 8
    assert metrics["emitted"] == 4
    assert metrics["partial"] == 4
    assert report["verification"]["metric_checks_passed"] is False


def test_disclosed_prior_exposure_disables_before_result_payload_access(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    manifest["synthetic"] = benchmark["synthetic"] = replay["synthetic"] = False
    manifest["freeze"]["earlier_result_exposure"] = "disclosed"
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    approval(tmp_path, manifest)
    (tmp_path / "replay.json").write_bytes(b"PRIVATE_RESULT_MARKER")
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    verification = report["verification"]
    assert verification["replay_bytes_checked"] is False
    assert "result_exposure_or_unfrozen_evidence" in verification["release_gate"]["reasons"]

def test_malformed_freeze_metadata_produces_a_safe_rejection_report(tmp_path):
    manifest, _, _, _ = bundle(tmp_path)
    manifest["freeze"] = ["PRIVATE_FREEZE_MARKER"]
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 1
    assert report is not None
    assert report["verification"]["failure"]["code"] == "freeze_invalid"
    assert "PRIVATE_FREEZE_MARKER" not in json.dumps(report) + result.stdout + result.stderr
    assert "Traceback" not in result.stderr

def test_primary_certified_precision_weights_matches_equally(tmp_path):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    template = benchmark["cases"][0]
    benchmark["cases"] = [
        {**template, "case_id": f"synthetic-unequal-{i}",
         "match_id": "synthetic-ten-position-match" if i < 10 else "synthetic-one-position-match"}
        for i in range(11)
    ]
    manifest["selection"] = [{"case_id": case["case_id"], "match_id": case["match_id"]}
                             for case in benchmark["cases"]]
    replay["cases"] = [
        {"case_id": case["case_id"], "candidate": [["Alpha" if i < 10 else "Beta"]],
         "incumbent": [["Beta"]]}
        for i, case in enumerate(benchmark["cases"])
    ]
    policy.update(min_matches=2, min_matches_per_stratum=2, precision_target=0.75, max_interval_width=1.0)
    replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 0
    verification = report["verification"]
    metrics = verification["metrics"]["candidate"]
    assert metrics["case_count"] == 11
    assert metrics["match_count"] == 2
    assert metrics["acceptable"] == 10
    assert metrics["certified_precision"] == 0.5
    assert metrics["precision_interval"]["mean"] == metrics["certified_precision"]
    assert "precision_below_target" in verification["metric_failures"]
    assert verification["release_gate"]["enabled"] is False


@pytest.mark.parametrize("malformation,code", [
    ("huge_integer", "policy_invalid"),
    ("deep_json", "json_nesting_exceeded"),
])
def test_extreme_json_inputs_have_safe_structured_rejections(tmp_path, malformation, code):
    manifest, policy, benchmark, replay = bundle(tmp_path)
    if malformation == "huge_integer":
        policy["precision_target"] = 10**400
        replace_inputs(tmp_path, manifest, policy, benchmark, replay)
    else:
        raw = ("[" * 20000 + "0" + "]" * 20000).encode("utf-8")
        (tmp_path / "policy.json").write_bytes(raw)
        manifest["artifacts"]["policy"]["sha256"] = hashlib.sha256(raw).hexdigest()
    result, report = run_replay(tmp_path, manifest)
    assert result.returncode == 1
    assert report is not None
    assert report["verification"]["status"] == "rejected"
    assert report["verification"]["failure"]["code"] == code
    assert report["verification"]["release_gate"]["enabled"] is False
    assert "Traceback" not in result.stderr
    assert str(tmp_path) not in json.dumps(report) + result.stdout + result.stderr
    assert str(ROOT) not in json.dumps(report) + result.stdout + result.stderr
