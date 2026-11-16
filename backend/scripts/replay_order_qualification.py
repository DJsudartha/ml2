"""Calibrate and replay a private, frozen pick-order reconstruction qualification policy."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import uuid

from backend.services.data.pick_order_exclusions import validate_exclusion_registry

POLICY_KIND = "pick_order_reconstruction_qualification"
METRIC_VERSION = "exact-unordered-seven-batch-order-v1"
BATCH_SIZES = (1, 2, 2, 1, 1, 2, 1)
TARGETS = ("minimum_order_precision", "minimum_coverage", "minimum_calibration_qualified_games",
           "minimum_evaluation_games", "minimum_evaluation_qualified_games")
NEXT_ACTIONS = {
    "concrete_policy_approval_missing": "Obtain explicit approval of the exact frozen policy bytes before opening evaluation results.",
    "synthetic_evidence_only": "Supply authorized real independent evidence; synthetic success is only a software check.",
    "evaluation_reference_contaminated": "Retire the contaminated evaluation and freeze a fresh match-disjoint independent selection.",
    "reference_provenance_contradiction": "Preserve proposal exposure honestly; exposed labels cannot be renamed independent references.",
    "independent_reference_labels_missing": "Obtain blind, complete independent reference labels for every selected evaluation game.",
    "no_calibration_threshold_meets_proposed_targets": "Supply adequate reviewed calibration material or approve a new proposed policy before any new independent evaluation.",
    "input_integrity_mismatch": "Restore the exact frozen bytes; do not repin inputs after inspecting evaluation results.",
    "frozen_lineage_mismatch": "Restore the frozen manifests and exclusion registry or begin a fresh policy cycle.",
    "calibration_contract_mismatch": "Restore the precommitted calibration, numerical requirements and exclusions; any change needs a fresh untouched evaluation cycle.",
    "policy_approval_mismatch": "Supply an approval record bound to this exact policy version and byte hash.",
    "frozen_policy_values_mismatch": "Restore the values deterministically derived from the precommitted calibration and numerical requirements; fresh approval cannot retarget the frozen evaluation.",
    "approval_order_not_established": "Provide honest timestamp precision establishing approval after policy freeze and before evaluation.",
}


class QualificationError(ValueError):
    pass


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def evaluator_hash():
    return digest(Path(__file__).read_bytes().replace(b"\r\n", b"\n"))


def is_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def read_document(path, expected=None, *, unique_keys=False):
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise QualificationError("input_unavailable_or_invalid_json") from exc
    if len(raw) > 16 * 1024 * 1024:
        raise QualificationError("input_too_large")
    actual = digest(raw)
    if expected is not None and actual != expected:
        raise QualificationError("input_integrity_mismatch")
    try:
        value = json.loads(raw, object_pairs_hook=unique_metadata_object if unique_keys else None)
    except (UnicodeError, ValueError, OverflowError, RecursionError) as exc:
        raise QualificationError("input_unavailable_or_invalid_json") from exc
    if not isinstance(value, dict):
        raise QualificationError("invalid_document")
    return value, actual


def require_document(value, kind):
    if value.get("schema_version") != 1 or value.get("kind") != kind:
        raise QualificationError("wrong_document_kind_or_version")


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def finite_number(value):
    return not isinstance(value, bool) and (
        isinstance(value, int) or isinstance(value, float) and math.isfinite(value)
    )


def numerical_targets(value, failure_code):
    if not isinstance(value, dict):
        raise QualificationError(failure_code)
    for key in TARGETS:
        number = value.get(key)
        if (not finite_number(number) or
                (not 0 < number <= 1 if key in TARGETS[:2] else not isinstance(number, int) or number < 1)):
            raise QualificationError(failure_code)
    return {key: value[key] for key in TARGETS}


def date_bounds(value):
    try:
        if isinstance(value, str) and len(value) == 10:
            start = datetime.combine(date.fromisoformat(value), time.min, timezone.utc)
            return start, start + timedelta(days=1)
        point = datetime.fromisoformat(value)
        if point.tzinfo is None:
            raise ValueError
        return point, point
    except (TypeError, ValueError, OverflowError) as exc:
        raise QualificationError("invalid_or_ambiguous_freeze_time") from exc


def load_manifests(args, now):
    calibration, cal_hash = read_document(args.calibration_manifest)
    evaluation, eval_hash = read_document(args.evaluation_manifest)
    exclusion_input, exclusion_hash = read_document(args.exclusion_registry)
    try:
        exclusions = validate_exclusion_registry(exclusion_input)
    except ValueError as exc:
        raise QualificationError("invalid_exclusion_registry") from exc
    for manifest, partition in ((calibration, "calibration"), (evaluation, "qualification_evaluation")):
        require_document(manifest, "order_qualification_manifest")
        if (manifest.get("partition") != partition or not nonempty(manifest.get("version")) or
                manifest.get("data_origin") not in {"real", "synthetic"} or
                not is_hash(manifest.get("evidence_sha256")) or
                not all(nonempty(manifest.get(key)) for key in
                        ("recognition_version", "commitment_rule_version")) or
                not isinstance(manifest.get("games"), list)):
            raise QualificationError("invalid_manifest")
        if date_bounds(manifest.get("frozen_at"))[1] > now:
            raise QualificationError("manifest_not_frozen_before_run")
        games = manifest["games"]
        if any(not isinstance(row, dict) or not all(nonempty(row.get(key)) for key in
                   ("game_id", "match_id", "layout_id")) for row in games):
            raise QualificationError("invalid_manifest_membership")
        if (len({row["game_id"] for row in games}) != len(games) or
                len({(row["layout_id"], row["match_id"]) for row in games}) != len(games)):
            raise QualificationError("games_must_use_distinct_matches")
    for key in ("recognition_version", "commitment_rule_version", "data_origin"):
        if calibration[key] != evaluation[key]:
            raise QualificationError("manifest_component_mismatch")
    cal_matches = {(row["layout_id"], row["match_id"]) for row in calibration["games"]}
    eval_matches = {(row["layout_id"], row["match_id"]) for row in evaluation["games"]}
    if (cal_matches & eval_matches or
            {row["game_id"] for row in calibration["games"]} &
            {row["game_id"] for row in evaluation["games"]}):
        raise QualificationError("calibration_evaluation_overlap")
    layouts = {row["layout_id"] for row in calibration["games"] + evaluation["games"]}
    if len(layouts) > 1:
        raise QualificationError("one_layout_per_policy_required")
    excluded = {(row["layout_id"], row["liquipedia_match_id"]) for row in exclusions["entries"]}
    if eval_matches & excluded:
        raise QualificationError("evaluation_contains_excluded_match")
    if (evaluation.get("calibration_manifest_sha256") != cal_hash or
            evaluation.get("exclusion_registry_sha256") != exclusion_hash or
            not is_hash(evaluation.get("requirements_sha256"))):
        raise QualificationError("calibration_contract_mismatch")
    return calibration, evaluation, {
        "calibration_manifest_sha256": cal_hash, "evaluation_manifest_sha256": eval_hash,
        "exclusion_registry_id": exclusions["exclusion_registry_id"],
        "calibration_evidence_sha256": calibration["evidence_sha256"],
        "evaluation_evidence_sha256": evaluation["evidence_sha256"],
        "requirements_sha256": evaluation["requirements_sha256"],
        "exclusion_registry_sha256": exclusion_hash,
    }


def canonical_order(value):
    if not isinstance(value, list) or len(value) != len(BATCH_SIZES):
        return None
    if any(not isinstance(batch, list) or len(batch) != size or
           any(not nonempty(hero) for hero in batch) for batch, size in zip(value, BATCH_SIZES)):
        return None
    if len({hero for batch in value for hero in batch}) != 10:
        return None
    return tuple(tuple(sorted(batch)) for batch in value)


def load_evidence(path, manifest):
    evidence, _ = read_document(path, manifest["evidence_sha256"])
    require_document(evidence, "order_qualification_evidence")
    rows = evidence.get("games")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise QualificationError("invalid_evidence")
    if (len({row.get("game_id") for row in rows}) != len(rows) or
            {row.get("game_id") for row in rows} != {row["game_id"] for row in manifest["games"]}):
        raise QualificationError("evidence_membership_mismatch")
    for row in rows:
        score = row.get("recognition_min_score")
        if (not finite_number(score) or not 0 <= score <= 1 or
                row.get("capture_status") not in {"complete", "interrupted", "failed"} or
                not isinstance(row.get("usable_evidence"), bool) or
                not isinstance(row.get("identities_complete"), bool) or
                row.get("commitment_status") not in {"verified", "missing", "uncertain"} or
                row.get("commitment_rule_version") != manifest["commitment_rule_version"]):
            raise QualificationError("invalid_evidence_fields")
        if row["commitment_status"] == "verified" and not is_hash(row.get("commitment_evidence_sha256")):
            raise QualificationError("missing_commitment_evidence")
        reference = row.get("reference", {})
        if (not isinstance(reference, dict) or reference.get("provenance") not in
                {"independent_blind", "proposal_assisted", "missing"} or
                not isinstance(reference.get("reviewed"), bool) or
                not isinstance(reference.get("machine_answers_shown"), bool)):
            raise QualificationError("invalid_reference_provenance")
        if ((reference["provenance"] == "independent_blind" and reference["machine_answers_shown"]) or
                (reference["provenance"] == "proposal_assisted" and not reference["machine_answers_shown"]) or
                (reference["provenance"] == "missing" and reference["reviewed"])):
            raise QualificationError("reference_provenance_contradiction")
    return rows


def metrics(rows, threshold, *, independent_only=False):
    qualified = [] if threshold is None else [
        row for row in rows if canonical_order(row.get("proposal_batches")) and
        row["usable_evidence"] and row["identities_complete"] and
        row["recognition_min_score"] >= threshold and row["commitment_status"] == "verified"
    ]
    references = [row for row in qualified if row["reference"]["reviewed"] and
                  canonical_order(row["reference"].get("batches")) and
                  (not independent_only or (row["reference"]["provenance"] == "independent_blind" and
                                            row["reference"]["machine_answers_shown"] is False))]
    correct = sum(canonical_order(row["proposal_batches"]) ==
                  canonical_order(row["reference"]["batches"]) for row in references)
    total = len(rows)
    return {
        "selected_games": total, "selected_matches": total,
        "complete_games": sum(canonical_order(row.get("proposal_batches")) is not None for row in rows),
        "usable_evidence_games": sum(row["usable_evidence"] for row in rows),
        "recognition_complete_games": sum(row["identities_complete"] for row in rows),
        "recognition_threshold_games": sum(row["identities_complete"] and
                                           row["recognition_min_score"] >= threshold for row in rows) if threshold is not None else None,
        "commitment_verified_games": sum(row["commitment_status"] == "verified" for row in rows),
        "qualified_games": len(qualified) if threshold is not None else None,
        "reference_scored_games": len(references) if threshold is not None else None,
        "correct_qualified_games": correct if threshold is not None else None,
        "order_precision": correct / len(qualified) if qualified and len(references) == len(qualified) else None,
        "coverage": len(qualified) / total if total and threshold is not None else None,
        "completeness": sum(canonical_order(row.get("proposal_batches")) is not None for row in rows) / total if total else None,
        "order_precision_reference_basis": "independent_blind_only" if independent_only else "reviewed_calibration_labels",
        "capture_status_counts": dict(sorted(Counter(row["capture_status"] for row in rows).items())),
        "commitment_status_counts": dict(sorted(Counter(row["commitment_status"] for row in rows).items())),
        "reference_provenance_counts": dict(sorted(Counter(row["reference"]["provenance"] for row in rows).items())),
    }


def evaluate(args, now):
    policy, policy_hash = read_document(args.policy)
    require_document(policy, "order_qualification_policy")
    if not nonempty(policy.get("version")):
        raise QualificationError("invalid_policy_version")
    if policy.get("policy_kind") != POLICY_KIND:
        raise QualificationError("wrong_policy_kind")
    if any(not isinstance(policy.get(key), dict) for key in ("lineage", "requirements", "calibration")):
        raise QualificationError("invalid_policy_object_shape")
    targets = numerical_targets(policy["requirements"], "invalid_frozen_requirements")
    threshold = policy.get("recognition_min_score")
    if threshold is not None and (not finite_number(threshold) or not 0 <= threshold <= 1):
        raise QualificationError("invalid_frozen_threshold")
    if (policy.get("metric_definition_version") != METRIC_VERSION or
            policy["lineage"].get("evaluator_source_sha256") != evaluator_hash()):
        raise QualificationError("evaluator_definition_mismatch")
    frozen_start, frozen_end = date_bounds(policy.get("frozen_at"))
    if frozen_end > now:
        raise QualificationError("policy_not_frozen_before_run")
    calibration, evaluation, lineage = load_manifests(args, frozen_start)
    if any(policy["lineage"].get(key) != value for key, value in lineage.items()):
        raise QualificationError("frozen_lineage_mismatch")
    if any(policy.get(key) != calibration[key] for key in
           ("recognition_version", "commitment_rule_version", "data_origin")):
        raise QualificationError("frozen_component_mismatch")
    base = {
        "status": "disabled", "policy_kind": POLICY_KIND, "policy_sha256": policy_hash,
        "qualification_gate_enabled": False, "automatic_acceptance_performed": False,
        "evaluation_payload_inspected": False, "evaluation_passed": False,
        "recommendation_quality_evaluated": False,
        "underlying_evidence_bytes_checked": False,
        "provenance_basis": "hash_pinned_records_with_supplied_private_attestations",
        "frozen_policy": {**{key: policy.get(key) for key in
                             ("version", "metric_definition_version", "recognition_min_score", "recognition_version", "commitment_rule_version")},
                          "requirements": targets},
    }
    if args.approval is None:
        return {**base, "failure_codes": ["concrete_policy_approval_missing"]}
    approval, approval_hash = read_document(args.approval)
    require_document(approval, "order_qualification_approval")
    if (approval.get("policy_sha256") != policy_hash or approval.get("policy_version") != policy.get("version") or
            approval.get("approved") is not True or not nonempty(approval.get("approval_record_id")) or
            approval.get("data_origin") != policy.get("data_origin")):
        raise QualificationError("policy_approval_mismatch")
    approved_start, approved_end = date_bounds(approval.get("approved_at"))
    if approved_start < frozen_end or approved_end > now:
        raise QualificationError("approval_order_not_established")
    if evaluation.get("untouched") is not True or evaluation.get("results_exposed_before_policy") is not False:
        return {**base, "failure_codes": ["evaluation_not_untouched"]}
    if threshold is None:
        return {**base, "failure_codes": ["calibrated_threshold_missing"]}
    expected_policy, _ = calibrate(args, frozen_start)
    if any(policy.get(key) != expected_policy[key] for key in
           ("version", "recognition_min_score", "requirements", "calibration", "selection_rule", "lineage")):
        return {**base, "failure_codes": ["frozen_policy_values_mismatch"]}
    rows = load_evidence(args.evaluation_results, evaluation)
    scores = metrics(rows, threshold, independent_only=True)
    failures = []
    if any(row["reference"]["provenance"] != "independent_blind" or
           row["reference"]["machine_answers_shown"] is not False for row in rows):
        failures.append("evaluation_reference_contaminated")
    if any(not row["reference"]["reviewed"] or not canonical_order(row["reference"].get("batches")) for row in rows):
        failures.append("independent_reference_labels_missing")
    if scores["selected_games"] < targets["minimum_evaluation_games"]:
        failures.append("insufficient_evaluation_games")
    if scores["qualified_games"] < targets["minimum_evaluation_qualified_games"]:
        failures.append("insufficient_qualified_games")
    if scores["order_precision"] is None or scores["order_precision"] < targets["minimum_order_precision"]:
        failures.append("order_precision_target_not_met")
    if scores["coverage"] is None or scores["coverage"] < targets["minimum_coverage"]:
        failures.append("coverage_target_not_met")
    evaluation_passed = not failures
    if policy["data_origin"] == "synthetic":
        failures.append("synthetic_evidence_only")
    enabled = not failures
    return {
        **base, "status": "passed" if enabled else "disabled", "evaluation": scores,
        "qualification_gate_enabled": enabled, "evaluation_passed": evaluation_passed,
        "evaluation_payload_inspected": True, "failure_codes": failures,
        "approval_sha256": approval_hash,
        "lineage": {**lineage, "evaluator_source_sha256": evaluator_hash(), "evaluator_source_hash_encoding": "utf8_lf"},
        "sample_unit": "one_game_per_distinct_match",
        "statistical_assumptions": "descriptive_game_proportions_only_no_confidence_or_population_accuracy_claim",
        "denominators": {"completeness": "all_selected_games", "coverage": "all_selected_games",
                         "recognition": "all_selected_games", "commitment": "all_selected_games",
                         "order_precision": "all_qualified_games_requires_independent_reference_for_every_one"},
    }


def calibrate(args, now):
    calibration, evaluation, lineage = load_manifests(args, now)
    requirements, requirements_hash = read_document(args.requirements)
    if requirements_hash != lineage["requirements_sha256"]:
        raise QualificationError("calibration_contract_mismatch")
    require_document(requirements, "order_qualification_requirements")
    if not all(nonempty(requirements.get(key)) for key in ("version", "policy_version")):
        raise QualificationError("invalid_requirements")
    targets = numerical_targets(requirements, "invalid_numerical_requirements")
    rows = load_evidence(args.calibration_results, calibration)
    candidates = []
    for threshold in sorted({row["recognition_min_score"] for row in rows}):
        scores = metrics(rows, threshold)
        if (scores["qualified_games"] >= targets["minimum_calibration_qualified_games"] and
                scores["order_precision"] is not None and
                scores["order_precision"] >= targets["minimum_order_precision"] and
                scores["coverage"] >= targets["minimum_coverage"]):
            candidates.append((scores["qualified_games"], threshold, scores))
    selected = max(candidates, default=None, key=lambda item: (item[0], item[1]))
    policy = {
        "kind": "order_qualification_policy", "schema_version": 1, "policy_kind": POLICY_KIND,
        "version": requirements["policy_version"], "frozen_at": now.isoformat(),
        "metric_definition_version": METRIC_VERSION,
        "approval_status": "pending", "data_origin": calibration["data_origin"],
        "recognition_version": calibration["recognition_version"],
        "commitment_rule_version": calibration["commitment_rule_version"],
        "recognition_min_score": selected[1] if selected else None,
        "requirements": targets,
        "lineage": {**lineage, "requirements_sha256": requirements_hash,
                    "evaluator_source_sha256": evaluator_hash(), "evaluator_source_hash_encoding": "utf8_lf"},
        "calibration": selected[2] if selected else metrics(rows, None),
        "selection_rule": "max_qualified_games_then_highest_observed_threshold_meeting_calibration_targets",
    }
    return policy, {
        "status": "calibrated" if selected else "disabled", "policy_kind": POLICY_KIND,
        "qualification_gate_enabled": False, "automatic_acceptance_performed": False,
        "evaluation_payload_inspected": False, "calibration": policy["calibration"],
        "failure_codes": [] if selected else ["no_calibration_threshold_meets_proposed_targets"],
        "approval_status": "pending", "evaluation_selected_games": len(evaluation["games"]),
        "underlying_evidence_bytes_checked": False,
        "provenance_basis": "hash_pinned_records_with_supplied_private_attestations",
    }


METADATA_ROLES = {"custodian", "policy_selector", "independent_evaluator", "reference_reviewer"}
METADATA_EXPOSURE = {
    "capture": {"complete", "interrupted", "failed", "none", "unknown"},
    "proposal_generation": {"yes", "no", "unknown"},
    "developer_inspection": {"yes", "no", "unknown"},
    "policy_selection_access": {"clear", "exposed", "unknown"},
    "evaluator_prefreeze_result_access": {"clear", "exposed", "unknown"},
    "blind_reviewer_answer_exposure": {"clear", "exposed", "unknown"},
}


def unique_metadata_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate metadata key")
        value[key] = item
    return value


def metadata_fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise QualificationError("invalid_metadata_schema")


def metadata_identifier(value):
    return (nonempty(value) and value == value.strip() and len(value) <= 512
            and all(ord(char) >= 32 and ord(char) != 127 for char in value))


def metadata_record(value):
    metadata_fields(value, ("record_id", "sha256"))
    if not metadata_identifier(value["record_id"]) or not is_hash(value["sha256"]):
        raise QualificationError("invalid_metadata_record_binding")


def metadata_preflight(args):
    manifest, manifest_hash = read_document(args.metadata_manifest, unique_keys=True)
    if type(manifest.get("schema_version")) is not int:
        raise QualificationError("wrong_document_kind_or_version")
    require_document(manifest, "order_qualification_metadata_manifest")
    metadata_fields(manifest, ("kind", "schema_version", "version", "data_origin",
                              "exclusion_registry_sha256", "roles", "custodian_overlap_audits", "games"))
    if (not metadata_identifier(manifest["version"]) or manifest["data_origin"] not in ("real", "synthetic")
            or not is_hash(manifest["exclusion_registry_sha256"])):
        raise QualificationError("invalid_metadata_schema")
    metadata_fields(manifest["roles"], METADATA_ROLES)
    if not all(metadata_identifier(actor) for actor in manifest["roles"].values()):
        raise QualificationError("invalid_metadata_roles")
    if not isinstance(manifest["custodian_overlap_audits"], list):
        raise QualificationError("invalid_metadata_roles")
    overlaps = {role for role, actor in manifest["roles"].items()
                if role != "custodian" and actor == manifest["roles"]["custodian"]}
    audited_roles = set()
    for audit in manifest["custodian_overlap_audits"]:
        metadata_fields(audit, ("role", "record_id", "sha256"))
        role = audit["role"]
        if (not isinstance(role, str) or role not in overlaps or role in audited_roles
                or not metadata_identifier(audit["record_id"]) or not is_hash(audit["sha256"])):
            raise QualificationError("invalid_custodian_overlap_audit")
        audited_roles.add(role)
    exclusions_input, exclusions_hash = read_document(
        args.exclusion_registry, manifest["exclusion_registry_sha256"], unique_keys=True)
    registry_fields = {"version", "entries"}
    if "exclusion_registry_id" in exclusions_input:
        registry_fields.add("exclusion_registry_id")
    metadata_fields(exclusions_input, registry_fields)
    if type(exclusions_input.get("version")) is not int or exclusions_input["version"] != 1:
        raise QualificationError("invalid_exclusion_registry")
    if isinstance(exclusions_input.get("entries"), list):
        for entry in exclusions_input["entries"]:
            metadata_fields(entry, ("game_id", "liquipedia_match_id", "layout_id", "reason", "source"))
    try:
        exclusions = validate_exclusion_registry(exclusions_input)
    except ValueError as exc:
        raise QualificationError("invalid_exclusion_registry") from exc
    if ("exclusion_registry_id" in exclusions_input
            and exclusions_input["exclusion_registry_id"] != exclusions["exclusion_registry_id"]):
        raise QualificationError("invalid_exclusion_registry")
    games = manifest["games"]
    if not isinstance(games, list) or not games:
        raise QualificationError("invalid_metadata_membership")
    for row in games:
        metadata_fields(row, ("game_id", "match_id", "layout_id", "partition", "exclusion_status",
                              "historical_holdout_lineage", "custody_record", "exposure",
                              "payload_references", "reference_provenance"))
        if (not all(metadata_identifier(row[key]) for key in ("game_id", "match_id", "layout_id"))
                or row["partition"] not in ("calibration", "qualification_evaluation")
                or row["exclusion_status"] not in ("not_listed", "excluded", "unknown")
                or row["historical_holdout_lineage"] not in ("none", "retired", "protected", "unresolved")
                or row["reference_provenance"] not in ("independent_blind", "proposal_assisted", "missing")):
            raise QualificationError("invalid_metadata_membership")
        metadata_record(row["custody_record"])
        metadata_fields(row["exposure"], METADATA_EXPOSURE)
        if any(not isinstance(row["exposure"][key], str) or row["exposure"][key] not in statuses
               for key, statuses in METADATA_EXPOSURE.items()):
            raise QualificationError("invalid_metadata_exposure")
        if not isinstance(row["payload_references"], list):
            raise QualificationError("invalid_metadata_reference")
        for reference in row["payload_references"]:
            metadata_fields(reference, ("kind", "path", "sha256"))
            path = reference["path"]
            if (reference["kind"] not in ("proposal", "labels", "ocr", "media", "evaluation")
                    or not metadata_identifier(path) or not is_hash(reference["sha256"])
                    or "\\" in path or ":" in path or "\x00" in path
                    or PureWindowsPath(path).is_absolute() or path.startswith("/")
                    or ".." in path.split("/")):
                raise QualificationError("invalid_metadata_reference")
    if len({row["game_id"] for row in games}) != len(games):
        raise QualificationError("duplicate_metadata_game")
    matches = {}
    for row in games:
        previous = matches.get(row["match_id"])
        if previous is not None:
            code = ("metadata_match_split_overlap" if previous != row["partition"]
                    else "metadata_games_must_use_distinct_matches")
            raise QualificationError(code)
        matches[row["match_id"]] = row["partition"]
    counts = {"selected_games": len(games), "selected_matches": len(matches)}
    for key in METADATA_EXPOSURE:
        counts[key if key != "capture" else "capture_status"] = dict(
            sorted(Counter(row["exposure"][key] for row in games).items()))
    blockers = set()
    if overlaps - audited_roles:
        blockers.add("custodian_role_overlap_unaudited")
    excluded_matches = {row["liquipedia_match_id"] for row in exclusions["entries"]}
    excluded_games = {row["game_id"] for row in exclusions["entries"]}
    for row in games:
        exposure = row["exposure"]
        evaluation = row["partition"] == "qualification_evaluation"
        listed = row["match_id"] in excluded_matches or row["game_id"] in excluded_games
        if row["exclusion_status"] == "unknown":
            blockers.add("exclusion_status_unknown")
        elif (row["exclusion_status"] == "excluded") != listed:
            blockers.add("exclusion_status_conflict")
        if evaluation and (listed or row["exclusion_status"] == "excluded"):
            blockers.add("evaluation_contains_excluded_match")
        lineage = row["historical_holdout_lineage"]
        if lineage == "unresolved":
            blockers.add("historical_holdout_lineage_unresolved")
        elif lineage == "protected":
            blockers.add("protected_holdout")
        elif evaluation and lineage == "retired":
            blockers.add("retired_holdout_not_untouched")
        if "unknown" in exposure.values():
            blockers.add("unknown_metadata_exposure")
        provenance = row["reference_provenance"]
        if provenance == "missing":
            blockers.add("reference_not_established")
        elif ((provenance == "independent_blind" and exposure["blind_reviewer_answer_exposure"] == "exposed")
              or (provenance == "proposal_assisted" and exposure["blind_reviewer_answer_exposure"] != "exposed")):
            blockers.add("reference_provenance_contradiction")
        if evaluation:
            if provenance != "independent_blind":
                blockers.add("evaluation_reference_not_independent")
            if exposure["developer_inspection"] == "yes":
                blockers.add("evaluation_development_exposure")
            if exposure["policy_selection_access"] == "exposed":
                blockers.add("evaluation_policy_selection_access")
            if exposure["evaluator_prefreeze_result_access"] == "exposed":
                blockers.add("evaluator_prefreeze_result_access")
    counts["reference_provenance"] = dict(sorted(Counter(row["reference_provenance"] for row in games).items()))
    counts["partitions"] = dict(sorted(Counter(row["partition"] for row in games).items()))
    return {
        "status": "metadata_checked", "policy_kind": POLICY_KIND,
        "metadata_valid": True, "metadata_blockers": sorted(blockers),
        "metadata_requirements_satisfied": not blockers, "counts": counts,
        "data_origin": manifest["data_origin"],
        "custodian_role_overlaps": {"declared": len(overlaps), "audited": len(audited_roles)},
        "lineage": {"metadata_manifest_sha256": manifest_hash,
                    "exclusion_registry_sha256": exclusions_hash,
                    "exclusion_registry_id": exclusions["exclusion_registry_id"]},
        "empirical_admissibility_established": False, "referenced_payloads_opened": False,
        "human_independence_authenticated": False, "unrecorded_access_absence_established": False,
        "qualification_gate_enabled": False, "automatic_acceptance_performed": False,
        "failure_codes": sorted(blockers),
    }



def output_path(path, private_root):
    try:
        resolved, root = path.resolve(), private_root.resolve()
        if not root.is_dir() or resolved == root or root not in resolved.parents or not resolved.parent.is_dir():
            raise QualificationError("output_must_be_within_existing_private_root")
        if resolved.exists():
            raise QualificationError("output_already_exists")
        return resolved
    except (OSError, RuntimeError) as exc:
        raise QualificationError("output_path_unavailable") from exc


def main():
    parser = argparse.ArgumentParser(prog="order-qualification", description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    calibration = actions.add_parser("calibrate")
    evaluation = actions.add_parser("evaluate")
    preflight = actions.add_parser("preflight")
    for name in ("metadata-manifest", "exclusion-registry", "output", "private-root"):
        preflight.add_argument(f"--{name}", type=Path, required=True)
    for action in (calibration, evaluation):
        for name in ("calibration-manifest", "evaluation-manifest", "exclusion-registry", "calibration-results",
                     "requirements", "output", "private-root"):
            action.add_argument(f"--{name}", type=Path, required=True)
    calibration.add_argument("--policy-output", type=Path, required=True)
    for name in ("policy", "evaluation-results"):
        evaluation.add_argument(f"--{name}", type=Path, required=True)
    evaluation.add_argument("--approval", type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    run = {"run_id": str(uuid.uuid4()), "entry_point": f"cli_{args.action}", "started_at": now.isoformat()}
    try:
        output = output_path(args.output, args.private_root)
        if args.action == "calibrate":
            policy_output = output_path(args.policy_output, args.private_root)
            if policy_output == output:
                raise QualificationError("output_paths_must_differ")
    except QualificationError as exc:
        print(json.dumps({"status": "rejected", "run": run, "failure_codes": [str(exc)]}, sort_keys=True))
        return 1
    try:
        if args.action == "calibrate":
            policy, verification = calibrate(args, now)
            policy_bytes = encoded(policy)
            with policy_output.open("xb") as handle:
                handle.write(policy_bytes)
            verification["policy_sha256"] = digest(policy_bytes)
        elif args.action == "preflight":
            verification = metadata_preflight(args)
        else:
            verification = evaluate(args, now)
    except (QualificationError, OSError, KeyError, TypeError) as exc:
        verification = {"status": "disabled", "policy_kind": POLICY_KIND,
                        "qualification_gate_enabled": False, "automatic_acceptance_performed": False,
                        "failure_codes": [str(exc) if isinstance(exc, QualificationError) else "input_or_output_unavailable"]}
    if args.action == "preflight" and verification["status"] == "disabled":
        verification.update(metadata_valid=False, metadata_requirements_satisfied=False,
                            empirical_admissibility_established=False, referenced_payloads_opened=False)
    verification["next_actions"] = list(dict.fromkeys(
        NEXT_ACTIONS.get(code, "Check the frozen input contract and unmet numerical requirements; keep qualification disabled.")
        for code in verification["failure_codes"]
    ))
    if args.action == "preflight":
        verification["next_actions"] = [
            "Resolve declared metadata blockers with accountable records; preflight cannot admit or qualify games."
            if verification["failure_codes"] else
            "Obtain independent source/reference verification and approved policy; metadata checks cannot establish admissibility."
        ]
    if verification["status"] == "calibrated":
        verification["next_actions"] = [NEXT_ACTIONS["concrete_policy_approval_missing"]]
    report = {"schema_version": 1, "event": f"order_qualification_{args.action}", "run": run,
              "verification": verification, "verification_sha256": digest(encoded(verification))}
    try:
        with output.open("xb") as handle:
            handle.write(encoded(report))
    except OSError:
        print(json.dumps({"status": "rejected", "run": run, "failure_codes": ["report_output_unavailable"]}))
        return 1
    print(json.dumps({"status": verification["status"], "run_id": run["run_id"],
                      "qualification_gate_enabled": verification["qualification_gate_enabled"],
                      "failure_codes": verification["failure_codes"]}, sort_keys=True))
    return 0 if verification["status"] in {"calibrated", "passed", "metadata_checked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
