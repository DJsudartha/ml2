"""Replay a frozen draft-quality policy; never trains, promotes or activates a model."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
import uuid

STAGES = {"B1": ("blue", 1), "R1,R2": ("red", 2), "B2,B3": ("blue", 2),
          "R3": ("red", 1), "R4": ("red", 1), "B4,B5": ("blue", 2), "R5": ("red", 1)}
STRATA = ("stage", "side", "patch_evidence", "coverage")
NUMBERS = ("top_k", "precision_target", "min_matches", "min_matches_per_stratum",
           "min_response_coverage", "min_judgment_coverage", "non_regression_margin",
           "confidence", "bootstrap_replicates", "bootstrap_seed", "max_interval_width")
FAILURE_ACTION = "Check the pinned input versions, hashes, selection and policy; keep the gate disabled."


class InvalidInput(ValueError):
    pass


def require(condition, code="schema_invalid"):
    if not condition:
        raise InvalidInput(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def is_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def text(value):
    require(isinstance(value, str) and 0 < len(value) <= 200 and not any(ord(c) < 32 for c in value))
    return value


def day(value):
    require(isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value), "date_precision_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInput("date_precision_invalid") from exc


def header(payload, kind):
    require(isinstance(payload, dict) and type(payload.get("schema_version")) is int
            and payload["schema_version"] == 1)
    require(payload.get("kind") == kind, "policy_kind_mismatch")
    require(isinstance(payload.get("version"), str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", payload["version"]), "version_invalid")


def local_path(root, name):
    require(isinstance(name, str) and name and not Path(name).is_absolute(), "unsafe_path")
    target = (root / name).resolve()
    require(target != root and target.is_relative_to(root), "unsafe_path")
    return target


def read_json(path, expected):
    require(is_hash(expected), "input_integrity_mismatch")
    with path.open("rb") as handle:
        raw = handle.read(10 * 1024 * 1024 + 1)
    require(len(raw) <= 10 * 1024 * 1024, "input_too_large")
    require(digest(raw) == expected, "input_integrity_mismatch")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result

    def reject_constant(_):
        raise InvalidInput("schema_invalid")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject_constant)


def read_artifact(root, references, name, checked):
    ref = references[name]
    require(isinstance(ref, dict) and set(ref) == {"path", "sha256"})
    payload = read_json(local_path(root, ref["path"]), ref["sha256"])
    checked[name] = ref["sha256"]
    return payload


def option(heroes, size):
    require(isinstance(heroes, list) and len(heroes) == size)
    normalized = tuple(sorted(text(hero) for hero in heroes))
    require(len(set(normalized)) == size)
    return normalized


def validate_policy(policy):
    header(policy, "recommendation_quality_policy")
    require(policy.get("metric_version") == "match_certified_option_precision_v1", "metric_version_mismatch")
    require(policy.get("pair_scoring") == "unordered_exact_option_v1", "metric_version_mismatch")
    missing = [key for key in (*NUMBERS, "required_strata") if key not in policy]
    if missing:
        return missing
    allowed = {"stage": set(STAGES), "side": {"blue", "red"},
               "patch_evidence": {"verified", "patch_neutral", "unknown"},
               "coverage": {"supported", "limited", "fallback"}}
    require(isinstance(policy["required_strata"], dict) and set(policy["required_strata"]) == set(STRATA),
            "policy_invalid")
    for key, values in policy["required_strata"].items():
        require(isinstance(values, list) and values and all(isinstance(value, str) for value in values)
                and len(values) == len(set(values)) and set(values) <= allowed[key], "policy_invalid")
    for key in ("top_k", "min_matches", "min_matches_per_stratum", "bootstrap_replicates", "bootstrap_seed"):
        require(type(policy[key]) is int, "policy_invalid")
    require(1 <= policy["top_k"] <= 20 and policy["min_matches"] >= 2
            and policy["min_matches_per_stratum"] >= 2 and 1000 <= policy["bootstrap_replicates"] <= 100000
            and 0 <= policy["bootstrap_seed"] <= 2**32 - 1, "policy_invalid")
    for key in ("precision_target", "min_response_coverage", "min_judgment_coverage",
                "non_regression_margin", "confidence", "max_interval_width"):
        require(type(policy[key]) in (int, float) and math.isfinite(policy[key]), "policy_invalid")
    require(0 < policy["precision_target"] <= 1 and 0 < policy["min_response_coverage"] <= 1
            and 0 < policy["min_judgment_coverage"] <= 1 and 0 <= policy["non_regression_margin"] < 1
            and 0.5 < policy["confidence"] < 1 and 0 < policy["max_interval_width"] <= 1, "policy_invalid")
    return []


def identity_selection(selection):
    require(isinstance(selection, list), "selection_mismatch")
    selected = {}
    for identity in selection:
        require(isinstance(identity, dict) and set(identity) == {"case_id", "match_id"}, "selection_mismatch")
        case_id, match_id = text(identity["case_id"]), text(identity["match_id"])
        require(case_id not in selected, "selection_mismatch")
        selected[case_id] = match_id
    return selected


def validate_cases(benchmark, selected, synthetic):
    header(benchmark, "expert_draft_quality_benchmark")
    require(type(benchmark.get("synthetic")) is bool and benchmark["synthetic"] == synthetic, "provenance_mismatch")
    require(isinstance(benchmark.get("cases"), list))
    cases = {}
    for case in benchmark["cases"]:
        require(isinstance(case, dict))
        case_id, match_id = text(case.get("case_id")), text(case.get("match_id"))
        require(case_id not in cases and selected.get(case_id) == match_id, "selection_mismatch")
        require(case.get("stage") in STAGES and case.get("side") == STAGES[case["stage"]][0])
        require(case.get("patch_evidence") in ("verified", "patch_neutral", "unknown"))
        require(case.get("coverage") in ("supported", "limited", "fallback"))
        for key in ("eligible_heroes", "unavailable_heroes"):
            require(isinstance(case.get(key), list))
            names = [text(hero) for hero in case[key]]
            require(len(names) == len(set(names)))
        available = set(case["eligible_heroes"]) - set(case["unavailable_heroes"])
        require(len(available) >= STAGES[case["stage"]][1], "legality_universe_invalid")
        require(case.get("review_provenance") in ("independent", "proposal_assisted", "unreviewed"))
        require(case.get("review_record_sha256") is None or is_hash(case["review_record_sha256"]))
        require(case.get("eligibility_evidence_sha256") is None or is_hash(case["eligibility_evidence_sha256"]))
        if case.get("reviewed_on") is not None:
            day(case["reviewed_on"])
        require(isinstance(case.get("options"), list))
        seen = set()
        for judged in case["options"]:
            require(isinstance(judged, dict) and set(judged) == {"heroes", "judgment", "composition"})
            heroes = option(judged["heroes"], STAGES[case["stage"]][1])
            require(heroes not in seen)
            seen.add(heroes)
            require(judged["judgment"] in ("acceptable", "unacceptable", "unjudged"))
            require(judged["composition"] in ("pass", "fail", "unjudged"))
            require(judged["judgment"] != "acceptable" or
                    (set(heroes) <= available and judged["composition"] != "fail"), "benchmark_inconsistent")
        cases[case_id] = case
    require(set(cases) == set(selected), "selection_mismatch")
    return cases


def interval(values, policy):
    mean = statistics.fmean(values)
    if min(values) == max(values):
        low = high = lower = mean
    else:
        rng = random.Random(policy["bootstrap_seed"])
        samples = sorted(statistics.fmean(rng.choices(values, k=len(values)))
                         for _ in range(policy["bootstrap_replicates"]))

        def quantile(probability):
            position = probability * (len(samples) - 1)
            index = int(position)
            return samples[index] + (samples[min(index + 1, len(samples) - 1)] - samples[index]) * (position - index)

        lower = quantile(1 - policy["confidence"])
        low = quantile((1 - policy["confidence"]) / 2)
        high = quantile((1 + policy["confidence"]) / 2)
    return {"mean": mean, "lower_bound": lower, "central_interval": [low, high], "interval_width": high - low}


def score(case, emitted, policy):
    require(isinstance(emitted, list))
    size = STAGES[case["stage"]][1]
    available = set(case["eligible_heroes"]) - set(case["unavailable_heroes"])
    expected = min(policy["top_k"], math.comb(len(available), size))
    options = [option(heroes, size) for heroes in emitted]
    require(len(options) == len(set(options)) and len(options) <= expected, "replay_invalid")
    judgments = {option(row["heroes"], size): row for row in case["options"]}
    row = {"match_id": case["match_id"], "expected": expected, "emitted": len(options),
           "acceptable": 0, "unacceptable": 0, "unjudged": 0, "illegal": 0,
           "composition_fail": 0, "composition_unjudged": 0, "responded": int(bool(options)),
           "partial": int(0 < len(options) < expected)}
    for heroes in options:
        judged = judgments.get(heroes, {"judgment": "unjudged", "composition": "unjudged"})
        row[judged["judgment"]] += 1
        row["illegal"] += int(not set(heroes) <= available)
        row["composition_fail"] += int(judged["composition"] == "fail")
        row["composition_unjudged"] += int(judged["composition"] == "unjudged")
    return row


def summarize(rows, policy):
    counts = {key: sum(row[key] for row in rows) for key in
              ("expected", "emitted", "acceptable", "unacceptable", "unjudged", "illegal",
               "composition_fail", "composition_unjudged", "responded", "partial")}
    matches = defaultdict(lambda: [0, 0])
    for row in rows:
        matches[row["match_id"]][0] += row["acceptable"]
        matches[row["match_id"]][1] += row["expected"]
    scores = {key: value[0] / value[1] for key, value in matches.items()}
    judged = counts["acceptable"] + counts["unacceptable"]
    return {
        "case_count": len(rows), "match_count": len(matches), **counts,
        "abstained_cases": len(rows) - counts["responded"],
        "response_coverage": counts["responded"] / len(rows),
        "judgment_coverage": judged / counts["emitted"] if counts["emitted"] else 0.0,
        "judged_precision": counts["acceptable"] / judged if judged else None,
        "certified_precision": counts["acceptable"] / counts["expected"],
        "precision_interval": interval(list(scores.values()), policy),
    }, scores


def evaluate_group(cases, outputs, policy, minimum):
    metrics, scores = {}, {}
    for arm in ("candidate", "incumbent"):
        rows = [score(case, outputs.get(case_id, {}).get(arm, []), policy) for case_id, case in cases.items()]
        metrics[arm], scores[arm] = summarize(rows, policy)
    paired = interval([scores["candidate"][key] - scores["incumbent"][key] for key in scores["candidate"]], policy)
    reasons = []
    if metrics["candidate"]["match_count"] < minimum:
        reasons.append("insufficient_independent_matches")
    for arm in ("candidate", "incumbent"):
        m = metrics[arm]
        if m["response_coverage"] < policy["min_response_coverage"]:
            reasons.append(arm + "_response_coverage")
        if m["judgment_coverage"] < policy["min_judgment_coverage"]:
            reasons.append(arm + "_judgment_coverage")
        if m["illegal"] or m["composition_fail"] or m["composition_unjudged"] or m["partial"]:
            reasons.append(arm + "_legality_or_composition")
    if metrics["candidate"]["precision_interval"]["lower_bound"] < policy["precision_target"]:
        reasons.append("precision_below_target")
    if paired["lower_bound"] < -policy["non_regression_margin"]:
        reasons.append("non_regression_not_established")
    if (metrics["candidate"]["precision_interval"]["interval_width"] > policy["max_interval_width"]
            or paired["interval_width"] > policy["max_interval_width"]):
        reasons.append("uncertainty_too_wide")
    return metrics, {"paired_match_difference": paired["mean"], **paired}, sorted(set(reasons))


def replay(manifest_path, expected):
    root = manifest_path.resolve().parent
    manifest = read_json(manifest_path, expected)
    header(manifest, "draft_quality_manifest")
    require(type(manifest.get("synthetic")) is bool)
    synthetic = manifest["synthetic"]
    require(isinstance(manifest.get("artifacts"), dict) and "policy" in manifest["artifacts"]
            and "benchmark" in manifest["artifacts"])
    refs, checked = manifest["artifacts"], {"manifest": expected}
    # Validate identity-only selection before opening any corresponding benchmark payload.
    selection = identity_selection(manifest.get("selection"))
    policy = read_artifact(root, refs, "policy", checked)
    missing = validate_policy(policy)
    base = {"kind": "recommendation_quality_policy_report", "lineage": checked, "synthetic": synthetic,
            "policy_version": policy["version"], "metric_version": policy["metric_version"],
            "pair_scoring": policy["pair_scoring"], "activation": "not_attempted",
            "source_evidence_bytes_checked": False, "scope": "recommendation_quality_only",
            "metric_denominators": {
                "judged_precision": "acceptable_plus_unacceptable_emitted_options",
                "certified_precision": "frozen_expected_option_slots",
                "response_coverage": "frozen_benchmark_cases", "judgment_coverage": "all_emitted_options"},
            "statistical_assumptions": {
                "resampling_unit": "independent_match", "within_match_dependence": "preserved",
                "interval_scope": "approximate_observed_between_match_variation",
                "unseen_rare_failures": "not_ruled_out_by_degenerate_bootstrap",
                "strata": "all_observed_one_dimensional_categories_from_frozen_selection"}}
    reasons = ["synthetic_evidence"] if synthetic else []
    if missing:
        return {**base, "status": "disabled", "missing_policy_fields": missing, "replay_bytes_checked": False,
                "release_gate": {"enabled": False, "reasons": [
                    "policy_definition_incomplete" if "required_strata" in missing else "numerical_policy_incomplete"]}}
    benchmark = read_artifact(root, refs, "benchmark", checked)
    cases = validate_cases(benchmark, selection, synthetic)
    base["benchmark_version"] = benchmark["version"]
    base["benchmark_counts"] = {"cases": len(cases), "matches": len({c["match_id"] for c in cases.values()})}
    base["declared_review_provenance"] = {
        value: sum(c["review_provenance"] == value for c in cases.values())
        for value in ("independent", "proposal_assisted", "unreviewed")}
    approval = None
    if "approval" in refs:
        approval = read_artifact(root, refs, "approval", checked)
        require(isinstance(approval, dict) and set(approval) == {
            "schema_version", "kind", "status", "policy_sha256", "benchmark_sha256",
            "approved_on", "reference", "independent_evidence_verified"
        } and approval.get("schema_version") == 1
            and approval.get("kind") == "recommendation_quality_policy_approval", "approval_invalid")
        require(approval["status"] in ("proposed", "approved", "rejected")
                and type(approval["independent_evidence_verified"]) is bool, "approval_invalid")
        reference = approval["reference"]
        require(reference is None or (isinstance(reference, str) and 0 < len(reference) <= 200
                                      and not any(ord(c) < 32 for c in reference)), "approval_invalid")
        require(approval.get("policy_sha256") == checked["policy"] and
                approval.get("benchmark_sha256") == checked["benchmark"], "approval_lineage_mismatch")
        if approval["status"] == "approved":
            require(isinstance(approval["approved_on"], str) and isinstance(reference, str), "approval_invalid")
    approved = bool(approval and approval.get("status") == "approved"
                    and approval.get("reference") and approval.get("independent_evidence_verified") is True)
    if approved:
        day(approval["approved_on"])
    else:
        reasons.append("policy_not_approved")
    freeze = manifest.get("freeze", {})
    require(isinstance(freeze, dict), "freeze_invalid")
    if (not all(freeze.get(key) is True for key in ("policy_before_results", "judgments_before_results",
                                                   "benchmark_before_results", "excluded_from_development"))
            or freeze.get("earlier_result_exposure") != "none"):
        reasons.append("result_exposure_or_unfrozen_evidence")
    if not cases or any(case["review_provenance"] != "independent" or not case.get("review_record_sha256")
                        or not case.get("reviewed_on") for case in cases.values()):
        reasons.append("missing_independent_expert_evidence")
    if any(not case.get("eligibility_evidence_sha256") or case["patch_evidence"] == "unknown"
           for case in cases.values()):
        reasons.append("unverified_case_evidence")
    if "replay" not in refs:
        reasons.append("missing_replay")
    if not cases or "replay" not in refs or (reasons and not synthetic):
        return {**base, "status": "disabled", "replay_bytes_checked": False,
                "release_gate": {"enabled": False, "reasons": sorted(set(reasons))}}
    results = read_artifact(root, refs, "replay", checked)
    header(results, "recommendation_quality_replay")
    require(type(results.get("synthetic")) is bool and results["synthetic"] == synthetic
            and results.get("policy_sha256") == checked["policy"]
            and results.get("benchmark_sha256") == checked["benchmark"], "replay_lineage_mismatch")
    seen_on = day(results["results_first_seen_on"])
    if approved:
        require(day(approval["approved_on"]) <= seen_on, "chronology_mismatch")
    require(all(not case.get("reviewed_on") or day(case["reviewed_on"]) <= seen_on
                for case in cases.values()), "chronology_mismatch")
    if results.get("judgments_hidden_from_recommender") is not True:
        reasons.append("recommender_exposed_to_judgments")
    outputs = {}
    require(isinstance(results.get("cases"), list))
    for row in results["cases"]:
        require(isinstance(row, dict) and set(row) == {"case_id", "candidate", "incumbent"})
        require(row["case_id"] in cases and row["case_id"] not in outputs, "selection_mismatch")
        outputs[row["case_id"]] = row
    metrics, comparison, failures = evaluate_group(cases, outputs, policy, policy["min_matches"])
    strata = {}
    for dimension in STRATA:
        strata[dimension] = {}
        values = set(policy["required_strata"][dimension]) | {case[dimension] for case in cases.values()}
        for value in sorted(values):
            subset = {key: case for key, case in cases.items() if case[dimension] == value}
            if not subset:
                strata[dimension][value] = {"failures": ["missing_required_stratum"]}
                failures.append("missing_required_stratum")
                continue
            group_metrics, group_comparison, group_failures = evaluate_group(
                subset, outputs, policy, policy["min_matches_per_stratum"])
            strata[dimension][value] = {"metrics": group_metrics, "comparison": group_comparison,
                                       "failures": group_failures}
            failures.extend(group_failures)
    failures = sorted(set(failures))
    return {**base, "status": "evaluated", "replay_bytes_checked": True,
            "metric_checks_passed": not failures, "metric_failures": failures,
            "metrics": metrics, "comparison": comparison, "strata": strata,
            "statistical_unit": "independent_match", "interval_method": "paired_match_cluster_percentile_bootstrap_v1",
            "confidence": policy["confidence"], "bootstrap_replicates": policy["bootstrap_replicates"],
            "bootstrap_seed": policy["bootstrap_seed"],
            "release_gate": {"enabled": not reasons and not failures, "reasons": sorted(set(reasons + failures))}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--output", required=True, help="New report path relative to the private manifest directory")
    args = parser.parse_args()
    run = {"run_id": str(uuid.uuid4()), "entry_point": "cli_replay",
           "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        verification = replay(args.manifest, args.manifest_sha256)
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        code = str(exc) if isinstance(exc, InvalidInput) else "input_unavailable"
        verification = {"kind": "recommendation_quality_policy_report", "status": "rejected",
                        "release_gate": {"enabled": False, "reasons": [code]},
                        "failure": {"code": code, "action": FAILURE_ACTION}, "activation": "not_attempted"}
    encoded = json.dumps(verification, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    report = {"schema_version": 1, "event": "draft_quality_policy_replay", "run": run,
              "verification": verification, "verification_sha256": digest(encoded)}
    try:
        output = local_path(args.manifest.resolve().parent, args.output)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    except (OSError, InvalidInput):
        print(json.dumps({"status": "rejected", "run_id": run["run_id"],
                          "failure": {"code": "report_output_unavailable", "action": "Choose a new private report filename."}}))
        return 1
    print(json.dumps({"status": verification["status"], "run_id": run["run_id"],
                      "verification_sha256": report["verification_sha256"],
                      "release_gate_enabled": verification["release_gate"]["enabled"]}, sort_keys=True))
    return 1 if verification["status"] == "rejected" else 0


if __name__ == "__main__":
    raise SystemExit(main())
