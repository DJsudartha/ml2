"""Public headless interfaces for the review-only consistency gate."""

from __future__ import annotations

import json
import subprocess
import sys

from backend.services.data.pick_order_exclusions import validate_exclusion_registry
from backend.services.modeling.pick_constants import PICK_SEQUENCE


def _run(script, *args):
    return subprocess.run(
        [sys.executable, f"backend/scripts/{script}.py", *map(str, args)],
        text=True,
        capture_output=True,
        check=False,
    )


def _game(game_no, match_id, video_id):
    return {
        "game_no": game_no,
        "liquipedia_match_id": match_id,
        "vod": f"https://www.youtube.com/watch?v={video_id}",
        "blue_team": [{"hero": f"B{i}"} for i in range(1, 6)],
        "red_team": [{"hero": f"R{i}"} for i in range(1, 6)],
    }


def test_holdout_selection_is_fixed_match_disjoint_and_media_free(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for name, prefix in (
        ("M7_World_Championship_Knockout_Stage", "m7"),
        ("MPL_Indonesia_Season_18_Regular_Season", "mpl"),
    ):
        (raw / f"{name}_games.json").write_text(
            json.dumps({
                "tournament": name,
                "pagename": name,
                "series": [
                    {"date": "2026-01-01 00:00:00", "games": [
                        _game(i, f"{prefix}-match-{(i + 1) // 2}",
                              f"{prefix}{i:0{11 - len(prefix)}d}")
                    ]}
                    for i in range(1, 7)
                ],
            }),
            encoding="utf-8",
        )
    exclusion_registry = tmp_path / "exclusions.json"
    exclusions = {"version": 1, "entries": [
        {
            "game_id": "M7_World_Championship_Knockout_Stage_games.json::series1::game1::1",
            "liquipedia_match_id": "m7-match-1",
            "layout_id": "m7_world_v1",
            "reason": "development",
            "source": "fixture",
        },
        {
            "game_id": "MPL_Indonesia_Season_18_Regular_Season_games.json::series1::game1::1",
            "liquipedia_match_id": "mpl-match-1",
            "layout_id": "mpl_id_v1",
            "reason": "development",
            "source": "fixture",
        },
    ]}
    exclusion_registry.write_text(json.dumps(exclusions), encoding="utf-8")
    output = tmp_path / "holdout.json"
    result = _run(
        "prepare_pick_order_holdout", "--raw-dir", raw,
        "--exclusion-registry", exclusion_registry, "--games-per-layout", 2,
        "--output", output,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload["games"]) == 4
    assert all(row["availability"] == "unchecked" for row in payload["games"])
    assert all(row["source_kind"] == "per_game" for row in payload["games"])
    assert all("frame" not in row and "hero" not in row for row in payload["games"])
    assert not any(row["liquipedia_match_id"].endswith("match-1") for row in payload["games"])
    assert len({row["layout_id"] for row in payload["games"]}) == 2
    assert payload["version"] == 2
    assert len(payload["exclusion_registry_id"]) == 64
    assert _run(
        "prepare_pick_order_holdout", "--raw-dir", raw,
        "--exclusion-registry", exclusion_registry, "--games-per-layout", 2,
        "--output", output,
    ).returncode == 0
    assert payload == json.loads(output.read_text(encoding="utf-8"))

    exclusions["entries"].append({
        "game_id": "M7_World_Championship_Knockout_Stage_games.json::series3::game1::3",
        "liquipedia_match_id": "m7-match-2",
        "layout_id": "m7_world_v1",
        "reason": "smoke_test",
        "source": "fixture",
    })
    exclusion_registry.write_text(json.dumps(exclusions), encoding="utf-8")
    stale = _run(
        "prepare_pick_order_holdout", "--raw-dir", raw,
        "--exclusion-registry", exclusion_registry, "--games-per-layout", 2,
        "--output", output,
    )
    assert stale.returncode != 0
    assert "Existing holdout is fixed" in stale.stderr


def test_evaluator_rejects_holdout_bound_to_stale_exclusion_registry(tmp_path):
    registry = tmp_path / "exclusions.json"
    registry.write_text(json.dumps({"version": 1, "entries": [{
        "game_id": "development-game",
        "liquipedia_match_id": "development-match",
        "layout_id": "m7_world_v1",
        "reason": "development",
        "source": "fixture",
    }]}), encoding="utf-8")
    holdout = tmp_path / "holdout.json"
    holdout.write_text(json.dumps({
        "version": 2,
        "exclusion_registry_id": "stale-registry-id",
        "games": [],
    }), encoding="utf-8")
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"version": 1, "games": []}), encoding="utf-8")
    suggestions = tmp_path / "suggestions.json"
    suggestions.write_text(json.dumps({"games": []}), encoding="utf-8")

    result = _run(
        "evaluate_pick_order_holdout",
        "--holdout", holdout,
        "--exclusion-registry", registry,
        "--gold", gold,
        "--suggestions", suggestions,
        "--output", tmp_path / "gate.json",
    )

    assert result.returncode != 0
    assert "Holdout exclusion registry is stale" in result.stderr


def _picks():
    return [
        {"global_pick_index": index, "team": team,
         "team_pick_order": order, "turn_index": turn,
         "hero": f"{'B' if team == 'blue' else 'R'}{order}"}
        for index, (team, order, turn) in enumerate(PICK_SEQUENCE, 1)
    ]


def test_evaluator_requires_current_v2_holdout_and_fails_closed_on_bad_results(tmp_path):
    holdout = tmp_path / "holdout.json"
    registry = tmp_path / "exclusions.json"
    registry_payload = {"version": 1, "entries": [{
        "game_id": "development-game",
        "liquipedia_match_id": "development-match",
        "layout_id": "m7_world_v1",
        "reason": "development",
        "source": "fixture",
    }]}
    registry.write_text(json.dumps(registry_payload), encoding="utf-8")
    registry_id = validate_exclusion_registry(registry_payload)[
        "exclusion_registry_id"
    ]
    games = [
        {"game_id": f"{layout}-{i}", "layout_id": layout,
         "liquipedia_match_id": f"{layout}-match-{i // 3}",
         "blue_picks": [f"B{k}" for k in range(1, 6)],
         "red_picks": [f"R{k}" for k in range(1, 6)]}
        for layout in ("m7_world_v1", "mpl_id_v1") for i in range(30)
    ]
    holdout.write_text(json.dumps({
        "version": 1,
        "games": games,
    }), encoding="utf-8")
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({"version": 1, "games": []}), encoding="utf-8")
    suggestions = tmp_path / "suggestions.json"
    suggestions.write_text(json.dumps({"games": []}), encoding="utf-8")
    report = tmp_path / "gate.json"
    command = (
        "--holdout", holdout, "--exclusion-registry", registry, "--gold", gold,
        "--suggestions", suggestions, "--output", report,
    )
    legacy = _run("evaluate_pick_order_holdout", *command)
    assert legacy.returncode != 0
    assert "version 2" in legacy.stderr

    holdout.write_text(json.dumps({
        "version": 2,
        "exclusion_registry_id": registry_id,
        "games": games,
    }), encoding="utf-8")
    assert _run("evaluate_pick_order_holdout", *command).returncode != 0
    assert json.loads(report.read_text(encoding="utf-8"))["gate_passed"] is False

    gold.write_text(json.dumps({"version": 1, "games": [
        {"game_id": row["game_id"], "status": "confirmed", "picks": _picks()}
        for row in games
    ]}), encoding="utf-8")
    proposed = [
        {"game_id": row["game_id"], "order_complete": i < 18,
         "status": "needs_review", "picks": _picks() if i < 18 else [],
         "provenance": {"layout_id": row["layout_id"],
                        "layout_validated": True,
                        "automatic_window": True,
                        "capture_extractor_version": "complete_draft_v15",
                        "video_match_verified": True}}
        for layout in ("m7_world_v1", "mpl_id_v1")
        for i, row in enumerate(g for g in games if g["layout_id"] == layout)
    ]
    suggestions.write_text(json.dumps({"games": proposed}), encoding="utf-8")
    assert _run("evaluate_pick_order_holdout", *command).returncode == 0
    accepted = json.loads(report.read_text(encoding="utf-8"))
    assert accepted["gate_passed"] is True
    assert all(row["complete"] == 18 and row["incorrect_complete"] == 0
               for row in accepted["layouts"].values())

    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"version": 1, "games": [
        {"game_id": games[0]["game_id"], "blind": True,
         "first_pass_at": "2026-09-10T00:00:00+00:00",
         "first_order": [pick["hero"] for pick in _picks()]}
    ]}), encoding="utf-8")
    assert _run("evaluate_pick_order_holdout", *command,
                "--audit", audit).returncode == 0
    audited = json.loads(report.read_text(encoding="utf-8"))
    assert audited["gate_passed"] is True and audited["weekly_ready"] is False
    assert audited["label_audit_valid"] is False

    proposed[0]["picks"][0]["hero"] = "B2"
    suggestions.write_text(json.dumps({"games": proposed}), encoding="utf-8")
    assert _run("evaluate_pick_order_holdout", *command).returncode != 0
    rejected = json.loads(report.read_text(encoding="utf-8"))
    assert rejected["gate_passed"] is False
    assert rejected["layouts"]["m7_world_v1"]["incorrect_complete"] == 1


def test_capture_evidence_rejects_tied_events_and_unverified_elimination(tmp_path):
    evidence = tmp_path / "evidence.json"
    output = tmp_path / "capture"
    slots = [f"{team}_pick{i}" for team in ("blue", "red") for i in range(1, 6)]
    observations = [
        {"slot": slot, "hero": f"{'B' if slot.startswith('blue') else 'R'}{slot[-1]}",
         "confidence": 0.97, "source": "broadcast_name",
         "timestamp_sec": t}
        for slot in slots for t in (100.0, 100.6)
    ]
    events = [
        {"slot": f"{team}_pick{order}", "timestamp_sec": 10.0 + index,
         "stable_through_sec": 10.5 + index, "placeholder_observed": True,
         "final_slot_verified": True, "final_artwork_persisted": True}
        for index, (team, order, _) in enumerate(PICK_SEQUENCE)
    ]
    payload = {"games": [{
        "raw_game": {"game_id": "synthetic", "blue_picks": [f"B{i}" for i in range(1, 6)],
                     "red_picks": [f"R{i}" for i in range(1, 6)]},
        "slot_observations": observations, "lock_events": events,
        "completion": {"timestamp_sec": 100.0, "swap_detected_before_selection": False},
        "provenance": {"layout_id": "mpl_id_v1", "layout_version": "synthetic_v1",
                       "layout_validated": True, "video_match_verified": True},
    }]}
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    command = ("--evidence-json", evidence, "--output-dir", output)
    assert _run("capture_complete_drafts", *command).returncode == 0
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["games"][0]["order_complete"] is True
    assert all("lock_timestamp_sec" in pick for pick in report["games"][0]["picks"])

    payload["games"][0]["lock_events"][1]["timestamp_sec"] = 10.0
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert _run("capture_complete_drafts", *command).returncode != 0
    row = json.loads((output / "report.json").read_text(encoding="utf-8"))["games"][0]
    assert not row["order_complete"] and "tied_lock_events" in row["ambiguity_reasons"]

    payload["games"][0]["lock_events"][1]["timestamp_sec"] = 11.0
    payload["games"][0]["slot_observations"] = [
        item for item in observations if item["slot"] != "blue_pick5"
    ]
    payload["games"][0]["lock_events"][-2]["final_slot_verified"] = False
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert _run("capture_complete_drafts", *command).returncode != 0
    row = json.loads((output / "report.json").read_text(encoding="utf-8"))["games"][0]
    assert not row["order_complete"]

    payload["games"][0]["slot_observations"] = observations
    payload["games"][0]["lock_events"][-2]["final_slot_verified"] = True
    payload["games"][0]["provenance"]["layout_validated"] = False
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert _run("capture_complete_drafts", *command).returncode != 0
    row = json.loads((output / "report.json").read_text(encoding="utf-8"))["games"][0]
    assert "unknown_or_unverified_layout" in row["ambiguity_reasons"]

    payload["games"][0]["provenance"]["layout_validated"] = True
    payload["games"][0]["provenance"]["video_match_verified"] = False
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert _run("capture_complete_drafts", *command).returncode != 0
    row = json.loads((output / "report.json").read_text(encoding="utf-8"))["games"][0]
    assert "unverified_game_vod" in row["ambiguity_reasons"]


def test_capture_does_not_infer_when_two_identities_are_unresolved(tmp_path):
    evidence = tmp_path / "evidence.json"
    output = tmp_path / "capture"
    observations = [
        {
            "slot": f"{team}_pick{index}",
            "hero": f"{'B' if team == 'blue' else 'R'}{index}",
            "confidence": 0.97,
            "source": "broadcast_name",
            "timestamp_sec": timestamp,
        }
        for team in ("blue", "red")
        for index in range(1, 5)
        for timestamp in (100.0, 100.6)
    ]
    events = [
        {
            "slot": f"{team}_pick{order}",
            "timestamp_sec": 10.0 + index,
            "stable_through_sec": 10.5 + index,
            "placeholder_observed": True,
            "final_slot_verified": True,
            "final_artwork_persisted": True,
        }
        for index, (team, order, _) in enumerate(PICK_SEQUENCE)
    ]
    evidence.write_text(json.dumps({"games": [{
        "raw_game": {
            "game_id": "two-unresolved",
            "blue_picks": [f"B{i}" for i in range(1, 6)],
            "red_picks": [f"R{i}" for i in range(1, 6)],
        },
        "slot_observations": observations,
        "lock_events": events,
        "completion": {
            "timestamp_sec": 100.0,
            "swap_detected_before_selection": False,
        },
        "provenance": {
            "layout_id": "mpl_id_v1",
            "layout_version": "synthetic_v1",
            "layout_validated": True,
            "video_match_verified": True,
        },
    }]}), encoding="utf-8")

    result = _run(
        "capture_complete_drafts",
        "--evidence-json", evidence,
        "--output-dir", output,
    )

    assert result.returncode != 0
    row = json.loads((output / "report.json").read_text(encoding="utf-8"))["games"][0]
    assert row["order_complete"] is False
    assert "blue_pick5_unrecognized_hero" in row["ambiguity_reasons"]
    assert "red_pick5_unrecognized_hero" in row["ambiguity_reasons"]
    assert not any(
        "liquipedia_set_elimination" in pick.get("identity_sources", [])
        for pick in row["proposed_picks"]
    )


def test_capture_uses_validated_pre_swap_slots_when_lock_timestamps_overlap(tmp_path):
    evidence = tmp_path / "evidence.json"
    output = tmp_path / "capture"
    slots = [f"{team}_pick{i}" for team in ("blue", "red") for i in range(1, 6)]
    observations = [
        {
            "slot": slot,
            "hero": f"{'B' if slot.startswith('blue') else 'R'}{slot[-1]}",
            "confidence": 0.97,
            "source": "broadcast_name",
            "timestamp_sec": timestamp,
        }
        for slot in slots
        for timestamp in (100.0, 100.6)
    ]
    # These timestamps deliberately contain both same-phase ties and animation-
    # induced reversals. The calibrated pre-swap card positions, not their raw
    # appearance timestamps, are the chronological order source.
    timestamps = [10.0, 11.0, 10.8, 13.0, 12.7, 14.0, 15.0, 14.9, 15.0, 16.0]
    events = [
        {
            "slot": f"{team}_pick{order}",
            "timestamp_sec": timestamps[index],
            "stable_through_sec": timestamps[index] + 0.5,
            "placeholder_observed": True,
            "final_slot_verified": True,
            "final_artwork_persisted": True,
        }
        for index, (team, order, _) in enumerate(PICK_SEQUENCE)
    ]
    payload = {
        "games": [{
            "raw_game": {
                "game_id": "calibrated-pre-swap",
                "blue_picks": [f"B{i}" for i in range(1, 6)],
                "red_picks": [f"R{i}" for i in range(1, 6)],
            },
            "slot_observations": observations,
            "lock_events": events,
            "completion": {
                "timestamp_sec": 100.0,
                "swap_detected_before_selection": False,
            },
            "provenance": {
                "layout_id": "mpl_id_v1",
                "layout_version": "synthetic_v1",
                "layout_validated": True,
                "slot_semantics": "pre_swap_pick_order",
                "video_match_verified": True,
            },
        }]
    }
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    command = ("--evidence-json", evidence, "--output-dir", output)

    assert _run("capture_complete_drafts", *command).returncode == 0
    row = json.loads((output / "report.json").read_text(encoding="utf-8"))["games"][0]
    assert row["identity_complete"] is True
    assert row["slot_order_validated"] is True
    assert row["order_complete"] is True
    assert len(row["proposed_picks"]) == 10
    assert row["picks"] == row["proposed_picks"]
    assert "lock_timestamps_not_used_for_pre_swap_slot_order" in row["advisory_reasons"]

    payload["games"][0]["completion"]["swap_detected_before_selection"] = True
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert _run("capture_complete_drafts", *command).returncode != 0
    row = json.loads((output / "report.json").read_text(encoding="utf-8"))["games"][0]
    assert row["identity_complete"] is True
    assert row["slot_order_validated"] is False
    assert row["order_complete"] is False
    assert len(row["proposed_picks"]) == 10
    assert row["picks"] == []


def test_capture_exposes_partial_order_and_unresolved_identity_candidates(tmp_path):
    evidence = tmp_path / "evidence.json"
    output = tmp_path / "capture"
    observations = [
        {
            "slot": f"{team}_pick{index}",
            "hero": f"{'B' if team == 'blue' else 'R'}{index}",
            "confidence": 0.96,
            "source": "team_portrait_assignment",
            "observations": 2,
            "timestamp_sec": 100.0,
            "last_observed_sec": 100.6,
        }
        for team in ("blue", "red")
        for index in range(1, 6)
        if not (team == "blue" and index in (4, 5))
    ]
    for index in (4, 5):
        observations.append({
            "slot": f"blue_pick{index}",
            "hero": None,
            "confidence": 0.78,
            "source": "team_portrait_assignment",
            "best_candidate": f"B{index}",
            "top_candidates": [
                {"hero": f"B{index}", "score": 0.78},
                {"hero": f"B{9 - index}", "score": 0.77},
            ],
            "margin": 0.01,
            "assignment_margin": 0.008,
            "reason": "ambiguous_team_assignment",
        })
    events = [
        {
            "slot": f"{team}_pick{order}",
            "timestamp_sec": 10.0 + global_index,
            "stable_through_sec": 10.5 + global_index,
            "placeholder_observed": True,
            "final_slot_verified": True,
            "final_artwork_persisted": True,
        }
        for global_index, (team, order, _) in enumerate(PICK_SEQUENCE)
    ]
    evidence.write_text(json.dumps({"games": [{
        "raw_game": {
            "game_id": "partial-identity",
            "blue_picks": [f"B{i}" for i in range(1, 6)],
            "red_picks": [f"R{i}" for i in range(1, 6)],
        },
        "slot_observations": observations,
        "lock_events": events,
        "completion": {"timestamp_sec": 100.0, "swap_detected_before_selection": False},
        "provenance": {
            "layout_id": "m7_world_v1",
            "layout_version": "synthetic_v1",
            "layout_validated": True,
            "slot_semantics": "pre_swap_pick_order",
            "video_match_verified": True,
        },
    }]}), encoding="utf-8")

    result = _run(
        "capture_complete_drafts",
        "--evidence-json", evidence,
        "--output-dir", output,
    )
    assert result.returncode != 0
    row = json.loads((output / "report.json").read_text(encoding="utf-8"))["games"][0]
    assert row["identity_complete"] is False
    assert row["slot_order_validated"] is True
    assert row["order_complete"] is False
    assert len(row["proposed_picks"]) == 8
    assert row["picks"] == []
    unresolved = next(
        item for item in row["slot_suggestions"] if item["slot"] == "blue_pick4"
    )
    assert unresolved["top_candidates"][0] == {"hero": "B4", "score": 0.78}
    assert unresolved["margin"] == 0.01
    assert unresolved["assignment_margin"] == 0.008


def test_weekly_command_refuses_processing_before_rights_and_accuracy_gate(tmp_path):
    manifest = tmp_path / "manifest.json"
    result = _run(
        "run_weekly_pick_order_collection", "--raw-dir", tmp_path,
        "--manifest-output", manifest, "--since-days", 1,
    )
    assert result.returncode != 0
    assert not manifest.exists()
    assert "gate" in result.stderr.casefold() or "rights" in result.stderr.casefold()


def test_weekly_ready_path_is_new_capture_route_and_noop_on_empty_raw(tmp_path):
    gallery = tmp_path / "gallery"
    (gallery / "Kalea").mkdir(parents=True)
    (gallery / "Kalea" / "synthetic.jpg").write_bytes(b"sample")
    from backend.services.data.pick_order_gallery_release import gallery_release_manifest

    gallery_manifest = tmp_path / "gallery.json"
    release = gallery_release_manifest(gallery)
    gallery_manifest.write_text(json.dumps(release), encoding="utf-8")
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"gate_passed": True, "weekly_ready": True,
                                "gallery_release_id": release["release_id"]}),
                    encoding="utf-8")
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps({
        "rights_confirmed": True,
        "basis": "Synthetic authorization for self-created sample data in test",
        "allowed_channel_ids": ["UCMncR-XXNXhMyJELEgCrHlg",
                                "UC1dGHGJTXU_dkiR8tW3qQgg"],
        "extract_review_crops": True, "freeze_private_gallery": True,
    }), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    result = _run(
        "run_weekly_pick_order_collection", "--raw-dir", tmp_path / "raw",
        "--manifest-output", manifest, "--since-days", 1,
        "--gate-report", gate, "--media-rights-file", rights,
        "--gallery-manifest", gallery_manifest, "--gallery-dir", gallery,
        "--results-dir", tmp_path / "jobs", "--review-output", tmp_path / "review.json",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(manifest.read_text(encoding="utf-8"))["games"] == []


def test_capture_refuses_new_youtube_media_without_rights_file(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"games": [{
        "game_id": "test", "video_id": "abcdefghijk",
        "vod_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "status": "matched", "source_id": "mlbb_esports",
    }]}), encoding="utf-8")
    profiles = tmp_path / "profiles.json"
    profiles.write_text("{}", encoding="utf-8")
    result = _run(
        "capture_complete_drafts", "--manifest", manifest,
        "--profiles", profiles, "--output-dir", tmp_path / "output",
    )
    assert result.returncode != 0
    assert "rights" in result.stderr.casefold()
    assert not (tmp_path / "output").exists()


def test_gallery_release_is_hash_frozen_and_requires_explicit_rights(tmp_path):
    gallery = tmp_path / "gallery"
    (gallery / "Kalea").mkdir(parents=True)
    (gallery / "Kalea" / "alternate.jpg").write_bytes(b"synthetic crop")
    archive = tmp_path / "gallery.zip"
    manifest = tmp_path / "gallery.json"
    command = (
        "--gallery-dir", gallery, "--archive", archive,
        "--manifest-output", manifest,
    )
    assert _run("release_pick_order_gallery", *command).returncode != 0
    assert not archive.exists()
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps({
        "rights_confirmed": True,
        "basis": "Synthetic test authorization for self-created sample bytes only",
        "allowed_channel_ids": ["UCMncR-XXNXhMyJELEgCrHlg",
                                "UC1dGHGJTXU_dkiR8tW3qQgg"],
        "extract_review_crops": True, "freeze_private_gallery": True,
    }), encoding="utf-8")
    assert _run("release_pick_order_gallery", *command,
                "--media-rights-file", rights).returncode == 0
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(payload["files"]) == 1 and len(payload["release_id"]) == 64
    (gallery / "Kalea" / "alternate.jpg").write_bytes(b"changed")
    assert _run("release_pick_order_gallery", *command,
                "--media-rights-file", rights).returncode != 0


def test_blind_review_artifact_hides_machine_hero_labels(tmp_path):
    import cv2
    import hashlib
    import numpy as np

    crop = tmp_path / "crop.jpg"
    assert cv2.imwrite(str(crop), np.full((64, 40, 3), 100, np.uint8))
    digest = hashlib.sha256(crop.read_bytes()).hexdigest()
    holdout = tmp_path / "selection.json"
    holdout.write_text(json.dumps({"version": 1, "games": [{
        "game_id": "sample", "layout_id": "m7_world_v1",
        "vod_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "video_id": "abcdefghijk", "source_id": "mlbb_esports",
    }]}), encoding="utf-8")
    suggestions = tmp_path / "suggestions.json"
    suggestions.write_text(json.dumps({"games": [{
        "game_id": "sample", "timestamp_sec": 100,
        "picks": [{"hero": "Kalea"}],
        "review_sequences": [{"slot": "blue_pick1", "phase": "first_visible",
                              "timestamp_sec": 99.5, "frame": str(crop), "sha256": digest}],
        "review_crops": [{"slot": "blue_pick1", "phase": "first_settled_pre_swap",
                          "frame": str(crop), "sha256": digest}],
    }]}), encoding="utf-8")
    rights = tmp_path / "rights.json"
    rights.write_text(json.dumps({
        "rights_confirmed": True,
        "basis": "Synthetic authorization for self-created sample crop bytes",
        "allowed_channel_ids": ["UCMncR-XXNXhMyJELEgCrHlg"],
        "extract_review_crops": True,
    }), encoding="utf-8")
    output = tmp_path / "blind"
    command = ("--holdout", holdout, "--suggestions", suggestions,
               "--output-dir", output)
    assert _run("prepare_blind_pick_order_review", *command).returncode != 0
    assert _run("prepare_blind_pick_order_review", *command,
                "--media-rights-file", rights).returncode == 0
    queue = json.loads((output / "review_queue.json").read_text(encoding="utf-8"))
    assert queue["games"][0]["vod_timestamp_url"].endswith("&t=99")
    assert "Kalea" not in json.dumps(queue)
    assert (output / "sample.jpg").is_file()


def test_holdout_vod_verifier_rejects_wrong_uploader_from_metadata_fixture(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "M7_World_Championship_Knockout_Stage_games.json").write_text(
        json.dumps({"tournament": "M7", "pagename": "M7_World_Championship/Knockout_Stage",
                    "series": [{"date": "2026-01-18 07:00:00",
                                "blue_team_name": "Aurora Gaming PH",
                                "red_team_name": "Alter Ego",
                                "games": [_game(1, "match-1", "abcdefghijk")]}]}),
        encoding="utf-8",
    )
    game_id = "M7_World_Championship_Knockout_Stage_games.json::series1::game1::1"
    holdout = tmp_path / "holdout.json"
    holdout.write_text(json.dumps({"version": 1, "games": [{
        "game_id": game_id, "video_id": "abcdefghijk",
        "source_id": "mlbb_esports", "source_kind": "per_game",
    }]}), encoding="utf-8")
    fixture = tmp_path / "metadata.json"
    fixture.write_text(json.dumps({"abcdefghijk": {
        "id": "abcdefghijk", "channel_id": "UC-unofficial",
        "title": "M7 RORA vs AE Game 1", "description": "M7 World Championship",
        "duration": 2400, "upload_date": "20260118",
    }}), encoding="utf-8")
    output = tmp_path / "verification.json"
    result = _run(
        "verify_pick_order_holdout_vods", "--holdout", holdout,
        "--raw-dir", raw, "--metadata-fixture", fixture, "--output", output,
    )
    assert result.returncode != 0
    row = json.loads(output.read_text(encoding="utf-8"))["games"][0]
    assert row["verified"] is False
    assert "wrong_uploader" in row["rejection_reasons"]
