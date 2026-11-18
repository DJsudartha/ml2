"""Synthetic-only selector execution-isolation proof. No real-data mode exists."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[2]
INPUT_NAMES = ("calibration-manifest.json", "evaluation-manifest.json", "exclusions.json",
               "calibration-results.json", "requirements.json")
SOURCE_NAMES = (
    "backend/__init__.py", "backend/scripts/__init__.py", "backend/services/__init__.py",
    "backend/services/data/__init__.py", "backend/scripts/replay_order_qualification.py",
    "backend/services/data/pick_order_exclusions.py", "backend/services/common/file_utils.py",
    "backend/scripts/prove_selector_isolation.py",
)
FIXTURES = "tests/fixtures/selector_isolation"
IMAGE = "python:3.12-slim"
LIMITS = {"NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
          "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"], "PidsLimit": 32,
          "Memory": 128 * 1024 * 1024, "NanoCpus": 1_000_000_000, "IpcMode": "none"}
TMPFS = {"/tmp": "rw,noexec,nosuid,size=16m,mode=1777",
         "/output": "rw,noexec,nosuid,size=4m,uid=65534,gid=65534,mode=0700"}


class ProofError(ValueError):
    pass


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def selector_command(inputs, output):
    command = [sys.executable, "-B", "-m", "backend.scripts.replay_order_qualification", "calibrate"]
    for flag, filename in zip(("calibration-manifest", "evaluation-manifest", "exclusion-registry",
                               "calibration-results", "requirements"), INPUT_NAMES):
        command.extend((f"--{flag}", str(inputs / filename)))
    return command + ["--policy-output", str(output / "policy.json"),
                      "--output", str(output / "calibration-report.json"), "--private-root", str(output)]


def read_only(path):
    try:
        with path.open("r+b"):
            return False
    except OSError:
        return True


def require_synthetic_inputs(inputs):
    for name in ("calibration-manifest.json", "evaluation-manifest.json"):
        if json.loads((inputs / name).read_bytes())["data_origin"] != "synthetic":
            raise ProofError("synthetic_inputs_required")


def observe(args):
    require_synthetic_inputs(args.inputs)
    allowed = (args.inputs / "calibration-results.json").read_bytes()
    observed = {"allowed_input_read": bool(allowed), "allowed_input_sha256": sha256(allowed),
                "canary_readable": False,
                "selector_ran": False, "failure_codes": []}
    try:
        canary = args.canary.read_bytes()
    except (FileNotFoundError, PermissionError):
        pass
    else:
        observed.update(canary_readable=True, canary_sha256=sha256(canary),
                        failure_codes=["held_out_canary_readable"])
    if not observed["canary_readable"]:
        observed["code_read_only"] = read_only(Path(__file__))
        observed["inputs_read_only"] = all(read_only(args.inputs / name) for name in INPUT_NAMES)
        observed["docker_socket_inaccessible"] = not any(
            path.exists() for path in (Path("/var/run/docker.sock"), Path("/run/docker.sock")))
        network = Path("/sys/class/net")
        observed["network_interfaces"] = sorted(path.name for path in network.iterdir()) if network.exists() else []
        command = selector_command(args.inputs, args.output_root)
        observed["selector_command"] = command
        run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30)
        observed["selector_ran"] = True
        if run.returncode:
            observed["failure_codes"] = ["synthetic_selector_failed"]
        else:
            policy_bytes = (args.output_root / "policy.json").read_bytes()
            policy = json.loads(policy_bytes)
            verification = json.loads((args.output_root / "calibration-report.json").read_bytes())["verification"]
            observed["selector"] = {
                key: policy[key] for key in ("recognition_min_score", "data_origin", "approval_status")
            }
            observed["selector"].update({key: verification[key] for key in (
                "qualification_gate_enabled", "automatic_acceptance_performed", "evaluation_payload_inspected")})
            observed["selector"]["policy_sha256"] = sha256(policy_bytes)
    print(json.dumps(observed, sort_keys=True))
    return 1 if observed["failure_codes"] else 0


def execute(command, *, timeout=30):
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=timeout)
    if run.returncode:
        raise ProofError("isolation_runtime_command_failed")
    return run.stdout.strip()


def container_observation(docker, image_id, staging, *, unsafe):
    name = f"ml2-selector-proof-{uuid.uuid4().hex}"
    mounts = {"/code": staging / "code", "/inputs": staging / "inputs"}
    if unsafe:
        mounts["/held-out/results.json"] = staging / "held-out-results.json"
    command = [docker, "create", "--name", name, "--label", f"ml2.selector-proof={name}",
               "--pull=never", "--network=none", "--ipc=none", "--read-only", "--cap-drop=ALL",
               "--security-opt=no-new-privileges", "--user=65534:65534", "--pids-limit=32",
               "--memory=128m", "--cpus=1", "--ulimit=nofile=64:64", "--ulimit=fsize=1048576:1048576",
               "--workdir=/code", "--env=PYTHONDONTWRITEBYTECODE=1", "--entrypoint=python"]
    for target, options in TMPFS.items():
        command.extend(("--tmpfs", f"{target}:{options}"))
    for target, source in mounts.items():
        command.extend(("--mount", f"type=bind,source={source},target={target},readonly"))
    command.extend((image_id, "-B", "-m", "backend.scripts.prove_selector_isolation", "observe",
                    "--inputs", "/inputs", "--output-root", "/output", "--canary", "/held-out/results.json"))
    created = False
    try:
        execute(command)
        created = True
        actual = json.loads(execute([docker, "inspect", name]))[0]
        host, config = actual["HostConfig"], actual["Config"]
        if (any(host.get(key) != value for key, value in LIMITS.items())
                or host.get("Tmpfs") != TMPFS or host.get("Binds")
                or config["User"] != "65534:65534" or config["Image"] != image_id
                or config["WorkingDir"] != "/code"
                or len(actual["Mounts"]) != len(mounts)
                or any(mount["Type"] != "bind" or mount["RW"]
                       or mounts.get(mount["Destination"]) != Path(mount["Source"])
                       for mount in actual["Mounts"])):
            raise ProofError("unexpected_isolation_configuration")
        run = subprocess.run([docker, "start", "--attach", name], capture_output=True, text=True,
                             check=False, timeout=60)
        state = json.loads(execute([docker, "inspect", "--format", "{{json .State}}", name]))
        if state["Running"] or state["OOMKilled"] or state["Error"]:
            raise ProofError("container_did_not_complete_cleanly")
        observed = json.loads(run.stdout)
        observed["exit_code"] = state["ExitCode"]
        observed["attach_exit_code"] = run.returncode
        boundary = {**{key: host[key] for key in LIMITS}, "Tmpfs": host["Tmpfs"],
                    "User": config["User"], "WorkingDir": config["WorkingDir"],
                    "mounts": {target: "read_only" for target in mounts},
                    "environment_keys": sorted(item.split("=", 1)[0] for item in config["Env"]),
                    "Ulimits": host["Ulimits"], "timeout_seconds": 60}
        return observed, boundary
    finally:
        if created:
            execute([docker, "rm", "--force", name])


def prove(report, output):
    docker = shutil.which("docker")
    if docker is None:
        raise ProofError("isolation_runtime_unavailable")
    if sys.platform != "linux":
        raise ProofError("linux_isolation_runtime_required")
    runtime = json.loads(execute([docker, "version", "--format", "{{json .}}"]))
    if runtime["Server"]["Os"] != "linux":
        raise ProofError("linux_isolation_runtime_required")
    report["runtime"] = {"client_version": runtime["Client"]["Version"],
                         "server_version": runtime["Server"]["Version"], "os": runtime["Server"]["Os"],
                         "architecture": runtime["Server"]["Arch"]}
    image = json.loads(execute([docker, "image", "inspect", IMAGE]))[0]
    if not image["Id"].startswith("sha256:") or not image.get("RepoDigests"):
        raise ProofError("resolved_image_identity_missing")
    report["image"] = {"requested": IMAGE, "id": image["Id"], "repo_digests": image["RepoDigests"]}
    revision = execute(["git", "rev-parse", "HEAD"])
    report["source_revision"] = revision
    with tempfile.TemporaryDirectory(prefix="ml2-selector-proof-", dir=output.parent) as directory:
        staging = Path(directory)
        bindings = {}
        for source in (*SOURCE_NAMES, *(f"{FIXTURES}/{name}" for name in (*INPUT_NAMES, "held-out-results.json"))):
            # Stage committed bytes, never an entire checkout or a caller-supplied data directory.
            raw = subprocess.run(["git", "show", f"{revision}:{source}"], cwd=ROOT,
                                 capture_output=True, check=True, timeout=10).stdout
            destination = staging / (f"code/{source}" if source in SOURCE_NAMES else
                                     "held-out-results.json" if source.endswith("/held-out-results.json") else
                                     f"inputs/{Path(source).name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            bindings[source] = sha256(raw)
        if sha256(Path(__file__).read_bytes()) != bindings["backend/scripts/prove_selector_isolation.py"]:
            raise ProofError("launcher_differs_from_pinned_revision")
        require_synthetic_inputs(staging / "inputs")
        report["file_sha256"] = bindings
        restricted, boundary = container_observation(docker, image["Id"], staging, unsafe=False)
        report.update(restricted=restricted, isolation_configuration=boundary)
        report["isolation_configuration_sha256"] = sha256(json.dumps(boundary, sort_keys=True).encode())
        if restricted["canary_readable"]:
            raise ProofError("held_out_canary_readable")
        if (restricted["exit_code"] != 0 or restricted["failure_codes"]
                or not all(restricted[key] for key in ("allowed_input_read", "code_read_only",
                                                       "inputs_read_only", "docker_socket_inaccessible", "selector_ran"))
                or restricted["network_interfaces"] != ["lo"]
                or restricted["allowed_input_sha256"] != bindings[f"{FIXTURES}/calibration-results.json"]
                or restricted["selector"]["data_origin"] != "synthetic"
                or restricted["selector"]["approval_status"] != "pending"
                or any(restricted["selector"][key] is not False for key in (
                    "qualification_gate_enabled", "automatic_acceptance_performed", "evaluation_payload_inspected"))):
            raise ProofError("restricted_selector_observation_failed")
        control, control_boundary = container_observation(docker, image["Id"], staging, unsafe=True)
        report.update(unsafe_control=control, unsafe_control_configuration=control_boundary)
        if (control["exit_code"] != 1 or control["canary_readable"] is not True
                or control["canary_sha256"] != bindings[f"{FIXTURES}/held-out-results.json"]
                or control["allowed_input_read"] is not True or control["selector_ran"] is not False
                or control["failure_codes"] != ["held_out_canary_readable"]):
            raise ProofError("unsafe_control_not_detected")
    report.update(status="PASS", failure_codes=[])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    proof = actions.add_parser("prove")
    proof.add_argument("--output", type=Path, required=True)
    observer = actions.add_parser("observe")
    for name in ("inputs", "output-root", "canary"):
        observer.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "observe":
        try:
            return observe(args)
        except ProofError as exc:
            print(json.dumps({"failure_codes": [str(exc)]}))
            return 1
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            print(json.dumps({"failure_codes": ["observation_unavailable"]}))
            return 1
    report = {
        "schema_version": 1, "data_origin": "synthetic", "status": "NOT_PROVEN",
        "run": {"entry_point": "cli_selector_isolation_prove", "id": str(uuid.uuid4()),
                "started_at": datetime.now(timezone.utc).isoformat()},
        "qualification_gate_enabled": False, "automatic_acceptance_performed": False,
        "failure_codes": [],
    }
    try:
        handle = args.output.open("x", encoding="utf-8")
    except OSError:
        print(json.dumps({"status": "NOT_PROVEN", "failure_codes": ["report_output_unavailable"]}))
        return 1
    with handle:
        try:
            prove(report, args.output)
        except ProofError as exc:
            report["failure_codes"] = [str(exc)]
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            report["failure_codes"] = ["isolation_execution_unavailable"]
        json.dump(report, handle, sort_keys=True, indent=2)
        handle.write("\n")
    print(json.dumps({"status": report["status"], "failure_codes": report["failure_codes"]}))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
