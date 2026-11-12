"""Replay the pinned serving incumbent; no fitting, source access, or activation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

from backend.services.modeling import contextual_mvp as mvp

INPUTS = Path(__file__).resolve().parents[2] / "tests" / "mvp_golden.json"
INPUTS_SHA256 = "81b22622e661f5ae89117b05e1134af7ecedb614e84644e3373027e8f0df2ce5"
SOURCE_FIELDS = (
    "source_snapshot_sha256", "source_payload_sha256", "source_dataset_sha256",
    "source_scoring_version", "source_code_sha256", "simulator_source_sha256",
)
FAILURES = {
    "MVP inference artifact integrity failure": (
        "artifact_integrity_mismatch", "Restore the exact pinned incumbent artifact; do not change its pin."),
    "Unapproved MVP inference artifact": (
        "artifact_provenance_mismatch", "Check the incumbent version, source, allowlist and count pins."),
    "Replay input integrity mismatch": (
        "replay_input_integrity_mismatch", "Use the unchanged pinned synthetic replay inputs."),
    "Replay source provenance mismatch": (
        "replay_source_provenance_mismatch", "Check that replay inputs and incumbent declare the same source."),
    "Pinned inference result mismatch": (
        "inference_mismatch", "Compare the serving code and simulator configuration with the pinned baseline."),
    "Replay requires the default pinned simulator configuration": (
        "configuration_override", "Run replay without simulator-pool or disabled-hero overrides."),
}


def replay(inputs: Path):
    if os.getenv("ML2_SIMULATOR_HERO_POOL") or os.getenv("ML2_DISABLED_HEROES"):
        raise RuntimeError("Replay requires the default pinned simulator configuration")
    raw = inputs.read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(raw).hexdigest() != INPUTS_SHA256:
        raise RuntimeError("Replay input integrity mismatch")
    cases = json.loads(raw)
    artifact = mvp.load_snapshot()
    if cases["source_snapshot_sha256"] != artifact["source_snapshot_sha256"]:
        raise RuntimeError("Replay source provenance mismatch")
    results = []
    for index, case in enumerate(cases["cases"], 1):
        response = mvp.recommend_picks(**case["request"])
        rows = response["recommendations"]
        if ([row["hero"] for row in rows] != case["ranked"] or any(
            not math.isclose(row["score"], case["scores"][row["hero"]]["score"],
                             rel_tol=0, abs_tol=1e-14) for row in rows
        )):
            raise RuntimeError("Pinned inference result mismatch")
        results.append({"case": index, "team": response["team"], "batch_id": response["batch_id"],
                        "ranked": [row["hero"] for row in rows],
                        "scores": [row["score"] for row in rows]})
    return {
        "status": "passed", "reference_kind": "incumbent_only",
        "model_version": artifact["version"], "artifact_sha256": mvp.ARTIFACT_SHA256,
        "inputs_sha256": INPUTS_SHA256, "inputs_hash_encoding": "utf8_lf",
        "source_provenance": {key: artifact[key] for key in SOURCE_FIELDS},
        "source_provenance_status": "declared_in_verified_artifact",
        "source_bytes_checked": False, "case_count": len(results), "results": results,
        "quality_evaluation": "not_performed", "activation": "not_attempted",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=INPUTS,
                        help="Existing pinned synthetic replay inputs (identical copies permitted)")
    parser.add_argument("--output", type=Path, required=True,
                        help="New private JSON report; existing files are never overwritten")
    args = parser.parse_args()
    run = {"run_id": str(uuid.uuid4()), "entry_point": "cli_replay",
           "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        verification = replay(args.inputs)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        code, action = FAILURES.get(str(exc), (
            "replay_unavailable", "Check readability of the pinned files and compatibility of the local runtime."))
        verification = {
            "status": "rejected", "reference_kind": "incumbent_only",
            "expected_model_version": "contextual-backend-mvp-v1",
            "failure": {"code": code, "action": action},
            "quality_evaluation": "not_performed", "activation": "not_attempted",
        }
    encoded = json.dumps(verification, sort_keys=True, separators=(",", ":")).encode("utf-8")
    report = {
        "schema_version": 1, "event": "incumbent_baseline_replay", "run": run,
        "verification": verification, "verification_sha256": hashlib.sha256(encoded).hexdigest(),
    }
    try:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    except OSError:
        print(json.dumps({"status": "rejected", "run": run, "failure": {
            "code": "report_output_unavailable",
            "action": "Choose a new report file in an existing writable private directory; no overwrite is permitted.",
        }}, sort_keys=True))
        return 1
    print(json.dumps({"status": verification["status"], "run_id": run["run_id"],
                      "verification_sha256": report["verification_sha256"],
                      "failure": verification.get("failure")}, sort_keys=True))
    return 0 if verification["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
