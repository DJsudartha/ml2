"""Public synthetic proof commands; host-only observations never prove isolation."""

import json
import os
from pathlib import Path
import subprocess
import sys
import shutil


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/selector_isolation"


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "backend.scripts.prove_selector_isolation", *map(str, args)],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False, timeout=20,
    )


def test_missing_runtime_fails_closed_with_a_report_and_never_claims_pass(tmp_path):
    output = tmp_path / "proof.json"
    run = run_cli("prove", "--output", output, env={**os.environ, "PATH": ""})
    assert run.returncode == 1, run.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "NOT_PROVEN"
    assert report["failure_codes"] == ["isolation_runtime_unavailable"]
    assert report["data_origin"] == "synthetic"
    assert report["qualification_gate_enabled"] is False
    assert report["automatic_acceptance_performed"] is False
    assert "Traceback" not in run.stdout + run.stderr


def test_public_observer_detects_readable_synthetic_held_out_answers(tmp_path):
    run = run_cli("observe", "--inputs", FIXTURES, "--output-root", tmp_path,
                  "--canary", FIXTURES / "held-out-results.json")
    assert run.returncode == 1
    observed = json.loads(run.stdout)
    assert observed["canary_readable"] is True
    assert observed["allowed_input_read"] is True
    assert observed["selector_ran"] is False
    assert observed["failure_codes"] == ["held_out_canary_readable"]
    assert "proposal_batches" not in run.stdout + run.stderr
    assert "reference" not in run.stdout + run.stderr


def test_public_observer_runs_existing_selector_with_only_synthetic_calibration_inputs(tmp_path):
    inputs, output = tmp_path / "inputs", tmp_path / "output"
    inputs.mkdir()
    output.mkdir()
    for name in ("calibration-manifest.json", "evaluation-manifest.json", "exclusions.json",
                 "calibration-results.json", "requirements.json"):
        shutil.copyfile(FIXTURES / name, inputs / name)
    run = run_cli("observe", "--inputs", inputs, "--output-root", output,
                  "--canary", tmp_path / "held-out/results.json")
    assert run.returncode == 0, run.stderr or run.stdout
    observed = json.loads(run.stdout)
    assert observed["allowed_input_read"] is True
    assert observed["canary_readable"] is False
    assert observed["selector_ran"] is True
    assert observed["selector"]["recognition_min_score"] == .8
    assert observed["selector"]["data_origin"] == "synthetic"
    assert observed["selector"]["approval_status"] == "pending"
    assert observed["selector"]["evaluation_payload_inspected"] is False
    assert observed["selector"]["qualification_gate_enabled"] is False
    assert observed["selector"]["automatic_acceptance_performed"] is False
    assert len(observed["selector"]["policy_sha256"]) == 64
    assert "status" not in observed  # An observation on the host is never an isolation PASS.


def test_public_proof_refuses_to_replace_a_previous_report(tmp_path):
    output = tmp_path / "proof.json"
    output.write_text("previous proof", encoding="utf-8")
    run = run_cli("prove", "--output", output)
    assert run.returncode == 1
    assert output.read_text(encoding="utf-8") == "previous proof"
    assert json.loads(run.stdout)["failure_codes"] == ["report_output_unavailable"]
    assert "Traceback" not in run.stdout + run.stderr


def test_public_observer_rejects_real_manifest_before_reading_payloads(tmp_path):
    for name in ("calibration-manifest.json", "evaluation-manifest.json"):
        manifest = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        manifest["data_origin"] = "real"
        (tmp_path / name).write_text(json.dumps(manifest), encoding="utf-8")
    run = run_cli("observe", "--inputs", tmp_path, "--output-root", tmp_path,
                  "--canary", tmp_path / "absent.json")
    assert run.returncode == 1
    assert json.loads(run.stdout)["failure_codes"] == ["synthetic_inputs_required"]
    assert not (tmp_path / "policy.json").exists()
