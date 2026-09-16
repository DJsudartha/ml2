from pathlib import Path

import cv2
import numpy as np

import backend.services.data.complete_draft as capture


def fixture_tracker(monkeypatch):
    rng = np.random.default_rng(17)
    empty = np.full((64, 40, 3), 220, np.uint8)
    empty[20:35, 10:25] = 0
    heroes = [rng.integers(0, 180, (64, 40, 3), dtype=np.uint8) for _ in range(10)]
    slots = [f"{team}_pick{i}" for team in ("blue", "red") for i in range(1, 6)]
    monkeypatch.setattr(
        capture,
        "crop_slots",
        lambda frame, layout: {
            slot: frame[:, i * 40 : (i + 1) * 40] for i, slot in enumerate(slots)
        },
    )
    monkeypatch.setattr(capture, "_anchor_score", lambda *args: 1.0)
    tracker = capture.CompletionTracker({}, np.concatenate([empty] * 10, axis=1))
    return tracker, empty, heroes


def test_earliest_complete_is_saved_and_iteration_stops(monkeypatch, tmp_path):
    tracker, empty, heroes = fixture_tracker(monkeypatch)
    incomplete = np.concatenate(heroes[:9] + [empty], axis=1)
    complete = np.concatenate(heroes, axis=1)

    def frames():
        yield incomplete, 0
        for i in range(1, 4):
            yield complete, i / 60
        raise AssertionError("Decoder was consumed after completion")

    result = capture.select_complete_frame(
        frames(), tracker, tmp_path / "completed/draft.jpg"
    )
    assert result["timestamp_sec"] == 1 / 60
    assert result["frames_examined"] == 4
    assert result["needs_human_lock_confirmation"]
    assert len(list(tmp_path.rglob("*.jpg"))) == 1
    assert cv2.imread(result["frame"]).shape == complete.shape


def test_swap_before_completion_is_rejected(monkeypatch, tmp_path):
    tracker, empty, heroes = fixture_tracker(monkeypatch)
    first = np.concatenate(heroes[:9] + [empty], axis=1)
    swapped = np.concatenate([heroes[1], heroes[0]] + heroes[2:], axis=1)
    result = capture.select_complete_frame(
        [(first, 0), (swapped, 1), (swapped, 2), (swapped, 3)],
        tracker,
        tmp_path / "draft.jpg",
    )
    assert result["reason"] == "swap_detected"
    assert not list(tmp_path.iterdir())


def test_already_complete_start_cannot_prove_pre_swap(monkeypatch, tmp_path):
    tracker, _, heroes = fixture_tracker(monkeypatch)
    complete = np.concatenate(heroes, axis=1)
    result = capture.select_complete_frame(
        [(complete, i) for i in range(5)], tracker, tmp_path / "draft.jpg"
    )
    assert result["status"] == "needs_review"
    assert not (tmp_path / "draft.jpg").exists()


def test_interrupted_candidate_does_not_choose_hover(monkeypatch, tmp_path):
    tracker, empty, heroes = fixture_tracker(monkeypatch)
    incomplete = np.concatenate(heroes[:9] + [empty], axis=1)
    complete = np.concatenate(heroes, axis=1)
    frames = [incomplete, complete, incomplete, complete, complete, complete]
    result = capture.select_complete_frame(
        list(zip(frames, range(6))), tracker, tmp_path / "draft.jpg"
    )
    assert result["timestamp_sec"] == 3


def test_unknown_layout_is_rejected(monkeypatch, tmp_path):
    tracker, empty, heroes = fixture_tracker(monkeypatch)
    monkeypatch.setattr(capture, "_anchor_score", lambda *args: 0.1)
    frames = [(np.concatenate(heroes[:9] + [empty], axis=1), 0)]
    frames.extend((np.concatenate(heroes, axis=1), i) for i in range(1, 5))
    assert (
        capture.select_complete_frame(frames, tracker, tmp_path / "draft.jpg")["status"]
        == "needs_review"
    )


def test_failed_write_cleans_partial(monkeypatch, tmp_path):
    import pytest

    tracker, empty, heroes = fixture_tracker(monkeypatch)
    frames = [(np.concatenate(heroes[:9] + [empty], axis=1), 0)]
    frames.extend((np.concatenate(heroes, axis=1), i) for i in range(1, 4))

    def failed_write(path, frame):
        Path(path).touch()
        return False

    monkeypatch.setattr(cv2, "imwrite", failed_write)
    with pytest.raises(OSError):
        capture.select_complete_frame(frames, tracker, tmp_path / "draft.jpg")
    assert not list(tmp_path.iterdir())


def test_decoder_closes_process_when_selection_stops(monkeypatch):
    import base64
    from contextlib import closing
    import subprocess
    import sys

    ok, jpeg = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    encoded = base64.b64encode(jpeg).decode()
    code = (
        "import base64,sys,time; "
        f"sys.stdout.buffer.write(base64.b64decode('{encoded}')); "
        "sys.stdout.buffer.flush(); time.sleep(60)"
    )
    processes = []
    original = subprocess.Popen

    def record_process(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(capture.subprocess, "Popen", record_process)
    with closing(
        capture.decoded_frames([sys.executable, "-c", code], fps=60, start_sec=12)
    ) as frames:
        frame, timestamp = next(frames)
        assert timestamp == 12
        assert frame.shape == (8, 8, 3)
    assert processes[0].poll() is not None
    assert processes[0].stdout.closed


def test_decoder_timeout_closes_process():
    import pytest
    import sys

    with pytest.raises(TimeoutError):
        list(
            capture.decoded_frames(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                fps=60,
                idle_timeout=0.2,
            )
        )


def test_reveal_animation_must_settle_before_frame_is_saved(monkeypatch, tmp_path):
    tracker, empty, heroes = fixture_tracker(monkeypatch)
    incomplete = np.concatenate(heroes[:9] + [empty], axis=1)
    animation = [
        np.concatenate(
            heroes[:9] + [cv2.addWeighted(empty, 1 - alpha, heroes[9], alpha, 0)],
            axis=1,
        )
        for alpha in (0.3, 0.5, 0.7, 0.9)
    ]
    complete = np.concatenate(heroes, axis=1)
    frames = [incomplete, *animation, complete, complete, complete]
    result = capture.select_complete_frame(
        list(zip(frames, range(len(frames)))), tracker, tmp_path / "draft.jpg"
    )
    assert result["timestamp_sec"] == 5


def test_reference_completion_rejects_enlarged_card_until_separators_return(monkeypatch):
    """A filled oversized card cannot be saved as the pre-swap evidence frame."""
    monkeypatch.setattr(capture, "_anchor_score", lambda *args: 1.0)
    rng = np.random.default_rng(47)
    portraits = [rng.integers(0, 180, (200, 40, 3), dtype=np.uint8) for _ in range(10)]
    blank = np.full((200, 40, 3), 220, np.uint8)

    def frame(*, missing=False, settled=False):
        image = np.full((240, 400, 3), 245, np.uint8)
        for index, portrait in enumerate(portraits):
            image[:200, index * 40 : (index + 1) * 40] = (
                blank if missing and index == 9 else portrait
            )
        if settled:
            for boundary in range(40, 400, 40):
                image[210:230, boundary] = 0
        return image

    layout = {
        "coordinate_space": "normalized",
        "slots": {
            f"{team}_pick{pick}": [index / 10, 0, 0.1, 200 / 240]
            for index, (team, pick) in enumerate(
                (team, pick)
                for team in ("blue", "red")
                for pick in range(1, 6)
            )
        },
        "completion": {
            "settled_border_band": [210 / 240, 230 / 240],
            "settled_border_gray_max": 195,
            "settled_border_dark_fraction": 0.7,
            "settled_required_separators": {"blue": 4, "red": 4},
        },
    }
    enlarged = frame()
    normal = frame(settled=True)
    tracker = capture.ReferenceCompletionTracker(
        layout, enlarged, calibration_frame=frame(missing=True)
    )
    images = [frame(missing=True), enlarged, enlarged, enlarged, normal, normal, normal]
    result = capture.find_complete_frame(zip(images, range(len(images))), tracker)
    assert result is not None
    selected, diagnostics = result
    assert diagnostics["timestamp_sec"] == 4
    assert np.array_equal(selected, normal)


def test_no_settled_pre_swap_frame_fails_closed(monkeypatch):
    monkeypatch.setattr(capture, "_anchor_score", lambda *args: 1.0)
    rng = np.random.default_rng(51)
    portraits = [rng.integers(0, 180, (200, 40, 3), dtype=np.uint8)
                 for _ in range(10)]

    def frame(order, *, settled=False):
        image = np.full((240, 400, 3), 245, np.uint8)
        for index, source in enumerate(order):
            if source is not None:
                image[:200, index * 40:(index + 1) * 40] = portraits[source]
        if settled:
            for boundary in range(40, 400, 40):
                image[210:230, boundary] = 0
        return image

    layout = {
        "coordinate_space": "normalized",
        "slots": {
            f"{team}_pick{pick}": [index / 10, 0, 0.1, 200 / 240]
            for index, (team, pick) in enumerate(
                (team, pick) for team in ("blue", "red")
                for pick in range(1, 6)
            )
        },
        "completion": {
            "settled_border_band": [210 / 240, 230 / 240],
            "settled_required_separators": {"blue": 4, "red": 4},
        },
    }
    initial = frame(list(range(10)))
    swapped = frame([1, 0, *range(2, 10)], settled=True)
    tracker = capture.ReferenceCompletionTracker(
        layout, initial, calibration_frame=frame([*range(9), None])
    )
    frames = [frame([*range(9), None]), initial, initial, initial,
              swapped, swapped, swapped]
    assert capture.find_complete_frame(zip(frames, range(7)), tracker) is None
    assert tracker.swap_seen


def test_normal_cards_match_unordered_pool_despite_expanded_reference(monkeypatch):
    monkeypatch.setattr(capture, "_anchor_score", lambda *args: 1.0)
    rng = np.random.default_rng(53)
    portraits = [rng.integers(0, 180, (200, 40, 3), dtype=np.uint8)
                 for _ in range(10)]

    def frame(*, incomplete=False, enlarged=False, settled=False):
        image = np.full((240, 400, 3), 245, np.uint8)
        for index, portrait in enumerate(portraits):
            if not (incomplete and index == 9):
                image[:200, index * 40:(index + 1) * 40] = portrait
        if enlarged:
            image[:200, 240:260] = portraits[5][:, 20:40]
        if settled:
            for boundary in range(40, 400, 40):
                image[210:230, boundary] = 0
        return image

    layout = {
        "coordinate_space": "normalized",
        "slots": {
            f"{team}_pick{pick}": [index / 10, 0, 0.1, 200 / 240]
            for index, (team, pick) in enumerate(
                (team, pick) for team in ("blue", "red")
                for pick in range(1, 6)
            )
        },
        "completion": {
            "settled_border_band": [210 / 240, 230 / 240],
            "settled_required_separators": {"blue": 4, "red": 4},
        },
    }
    oversized = frame(enlarged=True)
    normal = frame(settled=True)
    tracker = capture.ReferenceCompletionTracker(
        layout, oversized, calibration_frame=frame(incomplete=True)
    )
    frames = [frame(incomplete=True), oversized, oversized, oversized,
              normal, normal, normal]
    result = capture.find_complete_frame(zip(frames, range(7)), tracker)
    assert result is not None
    assert result[1]["timestamp_sec"] == 4


def test_game_evidence_uses_visible_heroes_not_fixed_role_slots():
    from backend.services.data.vod_pick_order_suggestions import (
        suggest_pick_order_from_game_evidence,
    )

    blue = ["Esmeralda", "Yi Sun-shin", "Pharsa", "Freya", "Kalea"]
    red = ["Uranus", "Joy", "Valentina", "Claude", "Tigreal"]
    game = {"game_id": "m7_game", "blue_picks": blue, "red_picks": red}
    visible = {
        "blue": ["Kalea", "Yi Sun-shin", "Pharsa", "Esmeralda", "Freya"],
        "red": ["Tigreal", "Uranus", "Valentina", "Joy", "Claude"],
    }
    observations = [
        {
            "slot": f"{team}_pick{index}",
            "hero": hero,
            "confidence": 0.95,
            "source": "broadcast_name",
        }
        for team, heroes in visible.items()
        for index, hero in enumerate(heroes, start=1)
    ]
    result = suggest_pick_order_from_game_evidence(game, observations)
    assert result["order_complete"]
    assert result["status"] == "needs_review"
    assert {pick["slot"]: pick["hero"] for pick in result["picks"]} == {
        observation["slot"]: observation["hero"] for observation in observations
    }


def test_game_evidence_rejects_duplicate_or_unobserved_hero():
    from backend.services.data.vod_pick_order_suggestions import (
        suggest_pick_order_from_game_evidence,
    )

    game = {
        "game_id": "duplicate",
        "blue_picks": ["A", "B", "C", "D", "E"],
        "red_picks": ["F", "G", "H", "I", "J"],
    }
    observations = [
        {
            "slot": f"{team}_pick{index}",
            "hero": hero,
            "confidence": 0.95,
            "source": "broadcast_name",
        }
        for team, heroes in (("blue", ["A", "A", "C", "D", "E"]),
                             ("red", ["F", "G", "H", "I", "J"]))
        for index, hero in enumerate(heroes, start=1)
    ]
    result = suggest_pick_order_from_game_evidence(game, observations)
    assert not result["order_complete"]
    assert not result["picks"]
    assert result["status"] == "needs_review"


def test_verified_complete_frame_can_infer_one_unread_name_from_team_set():
    from backend.services.data.vod_pick_order_suggestions import (
        suggest_pick_order_from_game_evidence,
    )

    game = {
        "game_id": "one_unread",
        "blue_picks": ["A", "B", "C", "D", "E"],
        "red_picks": ["F", "G", "H", "I", "J"],
    }
    observations = [
        {"slot": f"{team}_pick{index}", "hero": hero,
         "confidence": 0.98, "source": "broadcast_name"}
        for team, heroes in (("blue", ["A", "B", "C", "D"]),
                             ("red", ["F", "G", "H", "I", "J"]))
        for index, hero in enumerate(heroes, start=1)
    ]
    unresolved = suggest_pick_order_from_game_evidence(game, observations)
    assert not unresolved["order_complete"]
    inferred = suggest_pick_order_from_game_evidence(
        game, observations, complete_frame_verified=True
    )
    assert inferred["order_complete"]
    last_blue = next(pick for pick in inferred["picks"]
                     if pick["slot"] == "blue_pick5")
    assert last_blue["hero"] == "E"
    assert last_blue["identity_sources"] == ["liquipedia_set_elimination"]


def test_visual_identity_rejects_lower_scoring_duplicate_within_team(monkeypatch):
    from types import SimpleNamespace
    import backend.scripts.capture_complete_drafts as command

    scores = {
        1: ("A", 0.95), 2: ("A", 0.75), 3: ("C", 0.93),
        4: ("D", 0.92), 5: ("E", 0.91),
        6: ("F", 0.95), 7: ("G", 0.94), 8: ("H", 0.93),
        9: ("I", 0.92), 10: ("J", 0.91),
    }
    monkeypatch.setattr(
        command, "recognize_hero_crop",
        lambda crop, candidates: {
            "hero": scores[int(crop[0, 0, 0])][0],
            "confidence": scores[int(crop[0, 0, 0])][1],
        },
    )
    sample = {
        f"{team}_pick{index}": np.full((40, 64, 3), offset + index, np.uint8)
        for team, offset in (("blue", 0), ("red", 5))
        for index in range(1, 6)
    }
    tracker = SimpleNamespace(
        completion_observations=[(sample, index) for index in range(3)]
    )
    game = {
        "blue_picks": ["A", "B", "C", "D", "E"],
        "red_picks": ["F", "G", "H", "I", "J"],
    }
    observations, _ = command._identity_observations(
        {"identity_mode": "visual_reference"}, {}, game, tracker,
        np.zeros((1, 1, 3), np.uint8), {"timestamp_sec": 0}
    )
    assert next(item for item in observations if item["slot"] == "blue_pick1")["hero"] == "A"
    assert not any(item["slot"] == "blue_pick2" for item in observations)


def test_broadcast_hero_name_matching_is_team_pool_constrained():
    from backend.services.data.broadcast_hero_names import match_hero_name

    assert match_hero_name("UALENTINA", ["Valentina", "Lylia"])[0] == "Valentina"
    assert match_hero_name("HIRARA", ["Hirara", "Claude"])[0] == "Hirara"
    assert match_hero_name("VALENTINA", ["Claude", "Lylia"]) is None


def test_confirmed_gallery_change_invalidates_capture_job(monkeypatch, tmp_path):
    import backend.scripts.capture_complete_drafts as command

    reference = tmp_path / "confirmed.jpg"
    reference.write_bytes(b"first")
    monkeypatch.setattr(command, "hero_reference_paths", lambda hero: [reference])
    kwargs = {
        "start": 1,
        "duration": 60,
        "probe": None,
        "raw_game": {"game_id": "sample", "blue_picks": ["Grock"], "red_picks": []},
    }
    original = command.input_identity({"video_id": "abcdefghijk"}, {}, None, **kwargs)
    reference.write_bytes(b"second")
    assert original != command.input_identity(
        {"video_id": "abcdefghijk"}, {}, None, **kwargs
    )


def test_command_reuses_completed_order_without_network(monkeypatch, tmp_path):
    import json
    import sys
    import backend.scripts.capture_complete_drafts as command

    tracker, empty, heroes = fixture_tracker(monkeypatch)
    baseline = tmp_path / "empty.jpg"
    cv2.imwrite(str(baseline), np.concatenate([empty] * 10, axis=1))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "games": [
                    {
                        "game_id": "sample",
                        "video_id": "abcdefghijk",
                        "source_id": "mlbb_esports",
                        "layout_id": "test",
                        "status": "matched",
                        "vod_url": "unused",
                    }
                ]
            }
        )
    )
    profiles = tmp_path / "profiles.json"
    profiles.write_text(
        json.dumps(
            {
                "test": {
                    "layout": {},
                    "placeholder_frame": str(baseline),
                    "role_slot_map": {},
                }
            }
        )
    )
    raw_game = {"game_id": "sample"}
    monkeypatch.setattr(command, "load_raw_games_by_id", lambda _: {"sample": raw_game})
    monkeypatch.setattr(command.shutil, "which", lambda _: "ffmpeg")
    monkeypatch.setattr(
        command,
        "video_info",
        lambda _: {
            "channel_id": "UCMncR-XXNXhMyJELEgCrHlg",
            "url": "unused",
            "fps": 60,
        },
    )
    monkeypatch.setattr(command, "CompletionTracker", lambda *args, **kwargs: tracker)
    images = [np.concatenate(heroes[:9] + [empty], axis=1)] + [
        np.concatenate(heroes, axis=1)
    ] * 3
    monkeypatch.setattr(
        command,
        "decoded_frames",
        lambda *args, **kwargs: ((im, i / 60) for i, im in enumerate(images)),
    )
    monkeypatch.setattr(
        command,
        "_identity_observations",
        lambda *args, **kwargs: ([], []),
    )
    monkeypatch.setattr(
        command,
        "suggest_pick_order_from_game_evidence",
        lambda *args, **kwargs: {
            "game_id": "sample",
            "status": "needs_review",
            "order_complete": True,
            "picks": [],
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "capture",
            "--manifest",
            str(manifest),
            "--profiles",
            str(profiles),
            "--output-dir",
            str(tmp_path / "output"),
            "--evidence-mode",
            "none",
        ],
    )
    assert command.main() == 0

    def no_network(_):
        raise AssertionError("Already completed game requested media again")

    monkeypatch.setattr(command, "video_info", no_network)
    assert command.main() == 0
    assert not list((tmp_path / "output").rglob("*.jpg"))


def test_video_reference_is_a_pool_not_the_pick_order(monkeypatch, tmp_path):
    _, empty, heroes = fixture_tracker(monkeypatch)
    # Reference is post-swap: its order must never become the proposed order.
    reference = np.concatenate(
        list(reversed(heroes[:5])) + list(reversed(heroes[5:])), axis=1
    )
    tracker = capture.ReferenceCompletionTracker({}, reference)
    frames = [np.concatenate(heroes[:9] + [empty], axis=1)] + [
        np.concatenate(heroes, axis=1)
    ] * 3
    result = capture.select_complete_frame(
        list(zip(frames, range(4))), tracker, tmp_path / "draft.jpg"
    )
    assert result["timestamp_sec"] == 1
    assert result["method"] == "video_reference_matching"
    saved = cv2.imread(result["frame"])
    assert np.mean(np.abs(saved.astype(float) - frames[1].astype(float))) < 40


def test_native_timestamps_are_used_instead_of_nominal_fps():
    import base64
    import sys

    ok, jpeg = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    encoded = base64.b64encode(jpeg).decode()
    code = (
        "import base64,sys; "
        "sys.stderr.write('[Parsed_showinfo_0] n: 0 pts: 10 pts_time:0.125\\n'); "
        "sys.stderr.flush(); "
        f"sys.stdout.buffer.write(base64.b64decode('{encoded}'))"
    )
    result = list(
        capture.decoded_frames(
            [sys.executable, "-c", code], fps=60, start_sec=20, use_timestamps=True
        )
    )
    assert result[0][1] == 20.125


def test_cache_identity_tracks_reference_bytes_and_scan_bounds(tmp_path):
    from backend.scripts.capture_complete_drafts import input_identity

    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"first")
    args = ({"video_id": "test"}, {}, {"frame": str(reference)})
    original = input_identity(*args, start=10, duration=60, probe=None)
    assert original == input_identity(*args, start=10, duration=60, probe=None)
    assert original != input_identity(*args, start=11, duration=60, probe=None)
    reference.write_bytes(b"second")
    assert original != input_identity(*args, start=10, duration=60, probe=None)


def test_reference_uses_calibrated_bounds_not_shot_letterboxing(monkeypatch):
    _, empty, heroes = fixture_tracker(monkeypatch)
    baseline = np.concatenate([empty] * 10, axis=1)
    reference = np.concatenate(heroes, axis=1)
    observed = []

    def bounds(frame):
        observed.append(frame.copy())
        return (0, 0, frame.shape[1], frame.shape[0])

    monkeypatch.setattr(capture, "detect_active_video_bounds", bounds)
    capture.ReferenceCompletionTracker({}, reference, calibration_frame=baseline)
    assert observed
    assert all(np.array_equal(frame, baseline) for frame in observed)


def test_reference_rejects_missing_anchors(monkeypatch):
    _, _, heroes = fixture_tracker(monkeypatch)
    reference = np.concatenate(heroes, axis=1)
    tracker = capture.ReferenceCompletionTracker({}, reference)
    monkeypatch.setattr(capture, "_anchor_score", lambda *args: None)
    assert tracker.observe(reference, 0) is None
    assert tracker.anchor_frames == 0


def test_role_remap_recovers_global_pick_order(monkeypatch):
    from backend.services.data.vod_pick_order_suggestions import (
        suggest_pick_order_from_role_remap,
    )

    _, _, heroes = fixture_tracker(monkeypatch)
    slots = [f"{team}_pick{i}" for team in ("blue", "red") for i in range(1, 6)]
    role_sample = {slot: capture.artwork(hero) for slot, hero in zip(slots, heroes)}
    blue_order = [2, 0, 4, 1, 3]
    red_order = [4, 1, 0, 3, 2]
    first_sample = {
        **{
            f"blue_pick{i + 1}": role_sample[f"blue_pick{role + 1}"]
            for i, role in enumerate(blue_order)
        },
        **{
            f"red_pick{i + 1}": role_sample[f"red_pick{role + 1}"]
            for i, role in enumerate(red_order)
        },
    }
    raw_game = {
        "game_id": "sample",
        "blue_team": [
            {"hero": f"Blue {index}", "slot": index} for index in range(1, 6)
        ],
        "red_team": [
            {"hero": f"Red {index}", "slot": index} for index in range(1, 6)
        ],
        "blue_picks": [f"Blue {index}" for index in range(1, 6)],
        "red_picks": [f"Red {index}" for index in range(1, 6)],
    }
    role_map = {
        team: {str(index): f"{team}_pick{index}" for index in range(1, 6)}
        for team in ("blue", "red")
    }
    result = suggest_pick_order_from_role_remap(
        raw_game, [first_sample] * 3, [role_sample] * 3, role_map
    )
    assert result["status"] == "needs_review"
    assert result["order_complete"]
    assert [pick["hero"] for pick in result["picks"]] == [
        "Blue 3",
        "Red 5",
        "Red 2",
        "Blue 1",
        "Blue 5",
        "Red 1",
        "Red 4",
        "Blue 2",
        "Blue 4",
        "Red 3",
    ]


def test_role_remap_rejects_ambiguous_portraits(monkeypatch):
    from backend.services.data.vod_pick_order_suggestions import (
        suggest_pick_order_from_role_remap,
    )

    _, _, heroes = fixture_tracker(monkeypatch)
    same = capture.artwork(heroes[0])
    sample = {
        f"{team}_pick{index}": same
        for team in ("blue", "red")
        for index in range(1, 6)
    }
    raw_game = {
        "game_id": "sample",
        **{
            f"{team}_team": [
                {"hero": f"{team} {index}", "slot": index}
                for index in range(1, 6)
            ]
            for team in ("blue", "red")
        },
        **{
            f"{team}_picks": [f"{team} {index}" for index in range(1, 6)]
            for team in ("blue", "red")
        },
    }
    role_map = {
        team: {str(index): f"{team}_pick{index}" for index in range(1, 6)}
        for team in ("blue", "red")
    }
    result = suggest_pick_order_from_role_remap(
        raw_game, [sample] * 3, [sample] * 3, role_map
    )
    assert not result["order_complete"]
    assert result["picks"] == []
    assert "assignment_ambiguous" in result["notes"]


def test_complete_frame_can_remain_in_memory(monkeypatch):
    tracker, empty, heroes = fixture_tracker(monkeypatch)
    incomplete = np.concatenate(heroes[:9] + [empty], axis=1)
    complete = np.concatenate(heroes, axis=1)
    result = capture.find_complete_frame(
        iter([(incomplete, 0), (complete, 1), (complete, 2), (complete, 3)]),
        tracker,
    )
    assert result is not None
    frame, metadata = result
    assert np.array_equal(frame, complete)
    assert metadata["timestamp_sec"] == 1
    assert len(tracker.completion_observations) == 3


def test_last_stable_role_arrangement_replaces_pre_swap_arrangement(monkeypatch):
    tracker, _, heroes = fixture_tracker(monkeypatch)
    first = np.concatenate(heroes, axis=1)
    tracker.completion_observations = [(tracker.pick_crops(first), 1)] * 3
    swapped = np.concatenate(
        [heroes[1], heroes[0], *heroes[2:]], axis=1
    )
    frames = [
        (first, 1.11),
        (first, 1.22),
        (first, 1.33),
        (swapped, 2.11),
        (swapped, 2.22),
        (swapped, 2.33),
    ]
    result = capture.collect_last_stable_role_observations(
        iter(frames), tracker, first_timestamp=1
    )
    assert result is not None
    assert result["permutation"]["blue"][:2] == [1, 0]
    assert result["timestamp_sec"] == 2.33


def test_role_arrangement_accepts_animated_but_unambiguous_portraits(monkeypatch):
    tracker, _, heroes = fixture_tracker(monkeypatch)
    first = np.concatenate(heroes, axis=1)
    tracker.completion_observations = [(tracker.pick_crops(first), 1)] * 3
    role_crops = tracker.pick_crops(first)
    # Broadcast transitions can materially alter/scale the portrait while its
    # globally unique team assignment remains clear.
    for slot, crop in role_crops.items():
        crop[:] = cv2.addWeighted(crop, 0.7, np.full_like(crop, 60), 0.3, 0)
    monkeypatch.setattr(tracker, "pick_crops", lambda _frame: role_crops)

    result = capture.collect_last_stable_role_observations(
        iter((np.empty((1, 1, 3), np.uint8), time) for time in (1.11, 1.22, 1.33)),
        tracker,
        first_timestamp=1,
    )

    assert result is not None
    assert result["timestamp_sec"] == 1.33


def test_portrait_similarity_uses_color_when_framing_changes():
    portrait = np.zeros((64, 40, 3), np.uint8)
    portrait[:, :20] = (20, 20, 220)
    portrait[:, 20:] = (20, 220, 20)
    reframed = np.roll(portrait, 12, axis=1)

    assert capture.portrait_similarity(portrait, reframed) > capture.similarity(
        portrait, reframed
    )


def test_broadcast_profiles_preserve_reviewed_role_to_screen_order():
    import json

    profiles = json.loads(
        Path("backend/data/complete_draft_profiles.json").read_text(encoding="utf-8")
    )

    assert profiles["m7_world_v1"]["role_slot_map"]["blue"] == {
        "1": "blue_pick1",
        "2": "blue_pick4",
        "3": "blue_pick5",
        "4": "blue_pick3",
        "5": "blue_pick2",
    }
    assert profiles["m7_world_v1"]["role_slot_map"]["red"] == {
        "1": "red_pick3",
        "2": "red_pick2",
        "3": "red_pick1",
        "4": "red_pick5",
        "5": "red_pick4",
    }
    assert profiles["mpl_id_v1"]["role_slot_map"]["blue"] == {
        "1": "blue_pick1",
        "2": "blue_pick2",
        "3": "blue_pick3",
        "4": "blue_pick5",
        "5": "blue_pick4",
    }
    assert profiles["mpl_id_v1"]["role_slot_map"]["red"] == {
        "1": "red_pick1",
        "2": "red_pick3",
        "3": "red_pick4",
        "4": "red_pick5",
        "5": "red_pick2",
    }


def test_crop_evidence_saves_ten_images_without_complete_frame(tmp_path):
    from backend.scripts.capture_complete_drafts import _persist_evidence

    sample = {
        f"{team}_pick{index}": np.full((64, 40, 3), index * 20, np.uint8)
        for team in ("blue", "red")
        for index in range(1, 6)
    }
    suggestion = {
        "picks": [
            {"slot": slot, "hero": f"Hero {index}"}
            for index, slot in enumerate(sorted(sample), start=1)
        ]
    }
    _persist_evidence(
        suggestion,
        np.zeros((360, 640, 3), np.uint8),
        sample,
        output_dir=tmp_path,
        video_id="video",
        identity="identity",
        evidence_mode="crops",
    )
    assert len(suggestion["review_crops"]) == 10
    assert len(list(tmp_path.rglob("*.jpg"))) == 10
    assert not (tmp_path / "completed").exists()


def test_capture_report_counts_review_artifacts():
    from backend.scripts.capture_complete_drafts import _report_payload

    report = _report_payload(
        [
            {
                "status": "needs_review",
                "order_complete": True,
                "review_crops": [{}] * 10,
            },
            {"status": "failed", "order_complete": False, "frame": "debug.jpg"},
        ],
        2,
    )

    assert report["counts"] == {
        "requested_games": 2,
        "complete_orders": 1,
        "needs_review": 1,
        "failed": 1,
        "evidence_crops": 10,
        "full_frames": 1,
    }
