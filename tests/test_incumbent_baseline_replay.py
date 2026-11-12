from __future__ import annotations

import json
import shutil
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_public_replay_verifies_the_pinned_incumbent(tmp_path):
    output = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.replay_incumbent_baseline", "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    verification = report["verification"]
    assert verification["status"] == "passed"
    assert verification["reference_kind"] == "incumbent_only"
    assert verification["model_version"] == "contextual-backend-mvp-v1"
    assert verification["artifact_sha256"] == "59be2a4013f89d284daa1e637d2ce5a8874cc6e7efb320409d796421199a898e"
    assert verification["inputs_sha256"] == "81b22622e661f5ae89117b05e1134af7ecedb614e84644e3373027e8f0df2ce5"
    assert verification["source_provenance"]["source_snapshot_sha256"] == "2db7d7456e1c66794e6c9fc2ab1c27bdbdb46d1ce0df3b89409f9ec0834b9657"
    assert verification["source_bytes_checked"] is False
    assert verification["case_count"] == 7
    assert verification["results"][0]["ranked"] == ["Hirara", "Paquito", "Melissa", "Belerick", "Carmilla"]
    assert verification["results"][0]["scores"][0] == 0.11067538126361655
    assert verification["quality_evaluation"] == "not_performed"
    assert verification["activation"] == "not_attempted"
    assert report["run"]["entry_point"] == "cli_replay"
    assert report["run"]["run_id"]


def test_replay_verification_is_repeatable_while_run_metadata_is_distinct(tmp_path):
    reports = []
    for number in range(2):
        output = tmp_path / f"report-{number}.json"
        result = subprocess.run(
            [sys.executable, "-m", "backend.scripts.replay_incumbent_baseline", "--output", str(output)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        reports.append(json.loads(output.read_text(encoding="utf-8")))
    assert reports[0]["verification"] == reports[1]["verification"]
    assert reports[0]["verification_sha256"] == reports[1]["verification_sha256"]
    assert reports[0]["run"]["run_id"] != reports[1]["run"]["run_id"]


def test_existing_api_preserves_incumbent_identity_scores_and_canonical_turns():
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    golden = json.loads((ROOT / "tests/mvp_golden.json").read_text(encoding="utf-8"))["cases"]
    for case, batch, global_index in zip(
        golden, ("B1", "R1,R2", "B2,B3", "R3", "R4", "B4,B5", "R5"), (1, 2, 4, 6, 7, 8, 10),
    ):
        response = client.post("/draft/recommend-picks", json=case["request"])
        assert response.status_code == 200
        body = response.json()
        assert body["base_model_name"] == "contextual-backend-mvp-v1"
        assert body["source_provenance"] == {
            "artifact_sha256": "59be2a4013f89d284daa1e637d2ce5a8874cc6e7efb320409d796421199a898e",
            "source_snapshot_sha256": "2db7d7456e1c66794e6c9fc2ab1c27bdbdb46d1ce0df3b89409f9ec0834b9657",
            "counts": {"games": 17, "matches": 16, "examples": 119, "positive_memberships": 170},
        }
        for row in body["recommendations"]:
            assert row["score"] == pytest.approx(case["scores"][row["hero"]]["score"], abs=1e-14)
        assert body["batch_id"] == batch
        assert body["global_pick_index"] == global_index
        assert body["team"] == case["request"]["team"]
        assert [row["hero"] for row in body["recommendations"]] == case["ranked"]
        assert all({"hero", "rank", "score", "score_components", "reasons"} <= row.keys()
                   for row in body["recommendations"])
    wrong_turn = {**golden[0]["request"], "team": "red"}
    assert client.post("/draft/recommend-picks", json=wrong_turn).status_code == 422
    ban = client.post("/draft/recommend-bans", json={"team": "blue", "top_k": 1})
    assert ban.status_code == 200
    assert ban.json()["recommendations"][0]["hero"] == "Atlas"
    assert ban.json()["turn_index"] == 1


def isolated_runtime(tmp_path):
    root = tmp_path / "isolated"
    for relative in (
        "backend/scripts/replay_incumbent_baseline.py",
        "backend/scripts/export_backend_mvp.py",
        "backend/services/modeling/contextual_mvp.py",
        "backend/services/modeling/ban_constants.py",
        "backend/services/modeling/pick_constants.py",
        "backend/services/modeling/mvp_artifacts/contextual-v1.json",
        "tests/mvp_golden.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return root


@pytest.mark.parametrize("corruption,expected_code", [
    ("artifact", "artifact_integrity_mismatch"),
    ("provenance", "artifact_provenance_mismatch"),
    ("inputs", "replay_input_integrity_mismatch"),
    ("inference", "inference_mismatch"),
])
def test_rejected_replay_reports_safe_failure_and_retains_api_incumbent(tmp_path, corruption, expected_code):
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    request = json.loads((ROOT / "tests/mvp_golden.json").read_text(encoding="utf-8"))["cases"][0]["request"]
    before = client.post("/draft/recommend-picks", json=request).json()
    root = isolated_runtime(tmp_path)
    if corruption == "artifact":
        (root / "backend/services/modeling/mvp_artifacts/contextual-v1.json").write_bytes(b"PRIVATE_CORRUPTION_MARKER")
    elif corruption == "inputs":
        (root / "tests/mvp_golden.json").write_bytes(b"PRIVATE_CORRUPTION_MARKER")
    elif corruption == "inference":
        scorer = root / "backend/services/modeling/contextual_mvp.py"
        scorer.write_text(scorer.read_text(encoding="utf-8").replace(
            '"score": sum(', '"score": 0.001 + sum(', 1,
        ), encoding="utf-8")
    else:
        pin = root / "backend/scripts/export_backend_mvp.py"
        pin.write_text(pin.read_text(encoding="utf-8").replace(
            "2db7d7456e1c66794e6c9fc2ab1c27bdbdb46d1ce0df3b89409f9ec0834b9657", "0" * 64,
        ), encoding="utf-8")
    output = tmp_path / "rejected.json"
    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.replay_incumbent_baseline", "--output", str(output)],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verification"]["status"] == "rejected"
    assert report["verification"]["failure"]["code"] == expected_code
    assert report["verification"]["failure"]["action"]
    assert report["verification"]["activation"] == "not_attempted"
    visible = output.read_text(encoding="utf-8") + result.stdout + result.stderr
    assert "PRIVATE_CORRUPTION_MARKER" not in visible
    assert str(root) not in visible
    assert "Traceback" not in visible
    assert client.post("/draft/recommend-picks", json=request).json() == before


def test_replay_never_overwrites_an_existing_input_or_report(tmp_path):
    root = isolated_runtime(tmp_path)
    artifact = root / "backend/services/modeling/mvp_artifacts/contextual-v1.json"
    original = artifact.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.replay_incumbent_baseline", "--output", str(artifact)],
        cwd=root, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert artifact.read_bytes() == original
    assert json.loads(result.stdout)["failure"]["code"] == "report_output_unavailable"
    assert str(root) not in result.stdout + result.stderr


def test_replay_rejects_configuration_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("ML2_DISABLED_HEROES", "Hirara")
    output = tmp_path / "override.json"
    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.replay_incumbent_baseline", "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert json.loads(output.read_text(encoding="utf-8"))["verification"]["failure"]["code"] == "configuration_override"


def test_replay_accepts_only_line_ending_changes_to_the_pinned_inputs(tmp_path):
    inputs = tmp_path / "lf-inputs.json"
    inputs.write_bytes((ROOT / "tests/mvp_golden.json").read_bytes().replace(b"\r\n", b"\n"))
    output = tmp_path / "normalized.json"
    result = subprocess.run(
        [sys.executable, "-m", "backend.scripts.replay_incumbent_baseline", "--inputs", str(inputs), "--output", str(output)],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert json.loads(output.read_text(encoding="utf-8"))["verification"]["inputs_hash_encoding"] == "utf8_lf"
