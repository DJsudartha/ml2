"""Sequential complete-draft capture, with no extracted-frame cache on disk.

Visual completeness is a review candidate, never automatic pick-lock confirmation.
"""

from __future__ import annotations

from collections import deque
from itertools import permutations
from pathlib import Path
import queue
import re
import subprocess
import threading

import cv2
import numpy as np

from backend.services.data.vod_layouts import (
    crop_slots,
    _anchor_score,
    detect_active_video_bounds,
    slot_bounds_for_frame,
)


def artwork(crop):
    h, w = crop.shape[:2]
    return cv2.resize(
        crop[int(h * 0.12) : int(h * 0.88), int(w * 0.12) : int(w * 0.88)], (40, 64)
    )


def similarity(a, b):
    return float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0])


def portrait_similarity(a, b):
    """Compare portrait identity despite broadcast zoom/reframing animations."""
    spatial = similarity(a, b)
    histograms = []
    for image in (a, b):
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        histogram = cv2.calcHist([hsv], [0, 1], None, [18, 16], [0, 180, 0, 256])
        histograms.append(cv2.normalize(histogram, None))
    color = float(
        cv2.compareHist(histograms[0], histograms[1], cv2.HISTCMP_CORREL)
    )
    return max(spatial, color)


def analysis_image(frame):
    """Reduce spatial work while preserving every temporal observation."""
    height, width = frame.shape[:2]
    return (
        cv2.resize(frame, (640, round(height * 640 / width))) if width > 640 else frame
    )


class CompletionTracker:
    """Keep the first complete frame while validating a short consecutive run.

    Requires a known empty overlay and an observed incomplete draft. Cross-slot
        artwork movement invalidates this attempt even if all ten slots later fill.
    """

    def __init__(self, layout, baseline, minimum_frames=3):
        self.layout = layout
        calibrated = analysis_image(baseline)
        self.shape = calibrated.shape
        self.active_bounds = detect_active_video_bounds(calibrated)
        self.bounds = slot_bounds_for_frame(
            layout,
            calibrated.shape,
            active_bounds=self.active_bounds,
        )
        self.anchor_bounds = slot_bounds_for_frame(
            {
                "coordinate_space": "normalized",
                "slots": {
                    str(i): anchor["bounds"]
                    for i, anchor in enumerate(layout.get("anchors", []))
                },
            },
            calibrated.shape,
            active_bounds=self.active_bounds,
        )
        self.references = {
            s: artwork(c) for s, c in self.crops(calibrated).items() if "_pick" in s
        }
        if len(self.references) != 10 or minimum_frames < 2:
            raise ValueError(
                "Ten calibrated pick slots and at least two observations required"
            )
        self.minimum_frames = minimum_frames
        self.freeze_first_lock = bool(
            layout.get("completion", {}).get("freeze_first_lock", False)
        )
        self.history = {}
        self.previous = {}
        self.previous_complete = None
        self.incomplete_seen = False
        self.swap_seen = False
        self.candidate = None
        self.run = 0
        self.frames_examined = 0
        self.maximum_filled = 0
        self.anchor_frames = 0
        self.completion_observations = []
        self.completion_frames = []
        self.placeholder_seen = set()
        self.lock_candidates = {}
        self.lock_event_crops = {}
        self.lock_events = []

    def observe_slot_locks(self, filled, time_sec, *, geometry_ok=True):
        """Retain only stable final-card transitions, not transient hovers."""
        if self.freeze_first_lock and not geometry_ok:
            return
        for slot in self.references:
            # The first stable placeholder-to-artwork transition is the lock
            # evidence. Later role swaps must not replace it with the artwork
            # that happens to occupy this screen position at completion.
            if self.freeze_first_lock and slot in self.lock_event_crops:
                continue
            observed = filled.get(slot)
            if observed is None:
                self.placeholder_seen.add(slot)
                self.lock_candidates.pop(slot, None)
                self.lock_event_crops.pop(slot, None)
                continue
            candidate = self.lock_candidates.get(slot)
            if candidate is None or portrait_similarity(
                observed, candidate["last_crop"]
            ) < 0.85:
                candidate = {
                    "first_seen_sec": time_sec,
                    "last_seen_sec": time_sec,
                    "first_crop": observed.copy(),
                    "last_crop": observed.copy(),
                }
                self.lock_candidates[slot] = candidate
                self.lock_event_crops.pop(slot, None)
            else:
                candidate["last_seen_sec"] = time_sec
                candidate["last_crop"] = observed.copy()
                if (time_sec - candidate["first_seen_sec"] >= 0.3 and
                    slot in self.placeholder_seen):
                    self.lock_event_crops[slot] = (
                        candidate["first_crop"].copy(),
                        candidate["last_crop"].copy(),
                    )

    def verified_lock_events(self, settled_timestamp_sec, settled_filled=None):
        events = []
        for slot in sorted(self.references):
            candidate = self.lock_candidates.get(slot)
            accepted = slot in self.lock_event_crops and candidate is not None
            events.append({
                "slot": slot,
                "timestamp_sec": candidate["first_seen_sec"] if candidate else None,
                "stable_through_sec": candidate["last_seen_sec"] if candidate else None,
                "placeholder_observed": slot in self.placeholder_seen,
                "final_slot_verified": bool(accepted),
                "final_artwork_persisted": bool(
                    accepted and candidate["first_seen_sec"] < settled_timestamp_sec
                    and (settled_filled is None or (
                        slot in settled_filled and portrait_similarity(
                            candidate["last_crop"], settled_filled[slot]
                        ) >= 0.85
                    ))
                ),
            })
        self.lock_events = events
        return events

    def crops(self, frame):
        if not self.bounds:
            return crop_slots(frame, self.layout)
        return {
            s: frame[y : y + h, x : x + w] for s, (x, y, w, h) in self.bounds.items()
        }

    def anchor_score(self, frame):
        anchors = self.layout.get("anchors", [])
        if not anchors or not all(a.get("hashes") for a in anchors):
            return _anchor_score(
                frame, self.layout, Path("backend/data/layout_anchors")
            )
        scores = []
        for i, anchor in enumerate(anchors):
            x, y, w, h = self.anchor_bounds[str(i)]
            gray = cv2.cvtColor(
                cv2.resize(frame[y : y + h, x : x + w], (9, 8)), cv2.COLOR_BGR2GRAY
            )
            value = sum(
                int(v) << j
                for j, v in enumerate((gray[:, 1:] > gray[:, :-1]).flatten())
            )
            scores.append(
                max(
                    1 - (value ^ int(ref, 16)).bit_count() / 64
                    for ref in anchor["hashes"]
                )
            )
        return float(np.mean(scores))

    def settled_border_scores(self, frame):
        """Check the calibrated player-card seams below the portrait strip.

        MPL enlarges the last locked hero over its neighbour. Ten recognizable
        portraits can therefore precede ten normal-sized, non-overlapping cards.
        A layout without a reviewed border band keeps its existing behavior.
        """
        settings = self.layout.get("completion", {})
        band = settings.get("settled_border_band")
        if band is None:
            return None
        if len(band) != 2 or not 0 <= band[0] < band[1] <= 1:
            raise ValueError("Invalid settled player-card border band")
        ax, ay, aw, ah = self.active_bounds
        y0 = ay + round(band[0] * ah)
        y1 = ay + round(band[1] * ah)
        gray = cv2.cvtColor(frame[y0:y1], cv2.COLOR_BGR2GRAY)
        if gray.shape[0] < 2:
            return {"blue": [], "red": []}
        gray_max = settings.get("settled_border_gray_max", 195)
        tolerance = max(2, round(aw * 0.004))
        scores = {}
        for team in ("blue", "red"):
            slots = sorted(
                (bounds for slot, bounds in self.bounds.items()
                 if slot.startswith(f"{team}_pick")),
                key=lambda bounds: bounds[0],
            )
            team_scores = []
            for left, right in zip(slots, slots[1:]):
                boundary = round((left[0] + left[2] + right[0]) / 2)
                columns = gray[:, max(0, boundary - tolerance):boundary + tolerance + 1]
                team_scores.append(
                    float(np.max(np.mean(columns < gray_max, axis=0)))
                    if columns.size else 0.0
                )
            scores[team] = team_scores
        return scores

    def settled_card_geometry(self, frame):
        scores = self.settled_border_scores(frame)
        if scores is None:
            return True, None
        settings = self.layout.get("completion", {})
        threshold = settings.get("settled_border_dark_fraction", 0.7)
        required = settings.get("settled_required_separators", {})
        accepted = all(
            sum(score >= threshold for score in scores[team])
            >= required.get(team, len(scores[team]))
            for team in ("blue", "red")
        )
        return accepted, scores

    def observe(self, frame, time_sec):
        self.frames_examined += 1
        analysis = analysis_image(frame)
        if analysis.shape != self.shape:
            self.candidate, self.run = None, 0
            self.completion_frames = []
            return None
        anchor = self.anchor_score(analysis)
        if anchor is None or anchor < self.layout.get("completion", {}).get(
            "anchor_threshold", 0.65
        ):
            self.candidate, self.run = None, 0
            self.completion_frames = []
            return None
        self.anchor_frames += 1
        filled = {}
        for slot, crop in self.crops(analysis).items():
            if slot not in self.references:
                continue
            observed = artwork(crop)
            neutral = np.mean(
                (crop.max(axis=2) - crop.min(axis=2) < 35) & (crop.mean(axis=2) > 185)
            )
            difference = np.mean(
                np.abs(observed.astype(float) - self.references[slot].astype(float))
            )
            if (
                similarity(observed, self.references[slot]) < 0.65
                and similarity(
                    observed[20:44, 8:32], self.references[slot][20:44, 8:32]
                )
                < 0.6
                and difference > 30
                and np.std(observed) > 20
                and neutral < (0.4 if self.references[slot].mean() > 150 else 1.0)
            ):
                filled[slot] = observed
        self.maximum_filled = max(self.maximum_filled, len(filled))
        geometry_ok, geometry_scores = self.settled_card_geometry(analysis)
        self.observe_slot_locks(filled, time_sec, geometry_ok=geometry_ok)
        for slot, observed in filled.items():
            own = self.history.get(slot)
            own_score = similarity(observed, own) if own is not None else -1
            # Compare within a team; opposing heroes cannot be exchanged.
            moved = [
                (other, similarity(observed, ref))
                for other, ref in self.history.items()
                if other != slot and other.split("_")[0] == slot.split("_")[0]
            ]
            if own is not None and any(
                score > 0.9
                and score > own_score + 0.2
                and other in filled
                and similarity(filled[other], own) > 0.9
                and similarity(filled[other], own)
                > similarity(filled[other], self.history[other]) + 0.2
                for other, score in moved
            ):
                self.swap_seen = True
        stable = len(filled) == 10 and geometry_ok and all(
            slot in self.previous
            and np.mean(
                np.abs(observed.astype(float) - self.previous[slot].astype(float))
            )
            < 5.0
            for slot, observed in filled.items()
        )
        if len(filled) < 10:
            self.incomplete_seen = True
            self.candidate, self.run = None, 0
            self.completion_frames = []
        elif not stable:
            self.candidate, self.run = None, 0
            self.completion_frames = []
        elif self.incomplete_seen and not self.swap_seen:
            if self.candidate is None:
                self.candidate = self.previous_complete or (frame.copy(), time_sec)
                self.run = 1 if self.previous_complete is not None else 0
                if self.previous_complete is not None:
                    previous_frame, previous_time = self.previous_complete
                    self.completion_observations = [
                        (self.pick_crops(previous_frame), previous_time)
                    ]
                    self.completion_frames = [(previous_frame.copy(), previous_time)]
            self.completion_observations.append((self.pick_crops(frame), time_sec))
            self.completion_observations = self.completion_observations[-self.minimum_frames :]
            self.completion_frames.append((frame.copy(), time_sec))
            self.completion_frames = self.completion_frames[-self.minimum_frames:]
            self.run += 1
            if self.run >= self.minimum_frames:
                selected, timestamp = self.candidate
                if self.freeze_first_lock and any(
                    slot in filled
                    and portrait_similarity(crops[1], filled[slot]) < 0.85
                    for slot, crops in self.lock_event_crops.items()
                ):
                    self.swap_seen = True
                    self.candidate, self.run = None, 0
                    self.completion_observations = []
                    self.completion_frames = []
                    return None
                self.verified_lock_events(timestamp, filled)
                return selected, {
                    "timestamp_sec": timestamp,
                    "validated_through_sec": time_sec,
                    "frames_examined": self.frames_examined,
                    "consecutive_complete_frames": self.run,
                    "status": "visual_completion_candidate",
                    "swap_detected_before_selection": False,
                    "needs_human_lock_confirmation": True,
                    "settled_separator_scores": geometry_scores,
                }
        for slot, observed in filled.items():
            if slot not in self.history or (
                slot in self.previous
                and similarity(observed, self.previous[slot]) > 0.99
            ):
                self.history[slot] = observed.copy()
        self.previous = filled
        self.previous_complete = (
            (frame.copy(), time_sec) if len(filled) == 10 and geometry_ok else None
        )
        return None

    def pick_crops(self, frame):
        """Return normalized pick portraits using the calibrated active area."""
        analysis = analysis_image(frame)
        return {
            slot: artwork(crop).copy()
            for slot, crop in self.crops(analysis).items()
            if "_pick" in slot
        }


class ReferenceCompletionTracker(CompletionTracker):
    """Match the ten actual broadcast portraits, independent of their final slots.

    The reference must be visually checked to contain ten heroes. Its slot order
    is not treated as pick order, so a post-swap reference is also usable.
    """

    def __init__(self, layout, reference, minimum_frames=3, calibration_frame=None):
        super().__init__(
            layout,
            reference if calibration_frame is None else calibration_frame,
            minimum_frames,
        )
        self.references = {
            s: artwork(c)
            for s, c in self.crops(analysis_image(reference)).items()
            if "_pick" in s
        }
        self.reference_sample = self.pick_crops(reference)
        self.initial_permutation = None

    def observe(self, frame, time_sec):
        self.frames_examined += 1
        analysis = analysis_image(frame)
        if analysis.shape != self.shape:
            self.run, self.candidate = 0, None
            return None
        anchor = self.anchor_score(analysis)
        if anchor is None or anchor < self.layout.get("completion", {}).get(
            "anchor_threshold", 0.65
        ):
            self.run, self.candidate = 0, None
            return None
        self.anchor_frames += 1
        assignment = {}
        confidence = {}
        for slot, crop in self.crops(analysis).items():
            if slot not in self.references:
                continue
            observed = artwork(crop)
            scores = sorted(
                (
                    (similarity(observed, ref), key)
                    for key, ref in self.references.items()
                    if key.split("_")[0] == slot.split("_")[0]
                ),
                reverse=True,
            )
            if scores[0][0] >= 0.85 and scores[0][0] - scores[1][0] >= 0.08:
                assignment[slot] = scores[0][1]
                confidence[slot] = scores[0][0]
        assignment = {
            s: hero
            for s, hero in assignment.items()
            if list(assignment.values()).count(hero) == 1
        }
        geometry_ok, geometry_scores = self.settled_card_geometry(analysis)
        self.maximum_filled = max(self.maximum_filled, len(assignment))
        pool_matched = len(assignment) == 10
        minimum_confidence = min(confidence.values()) if pool_matched else None
        pool_mean_confidence = (
            float(np.mean(list(confidence.values()))) if pool_matched else None
        )
        permutation = None
        globally_matched = None
        if pool_matched:
            permutation = tuple(
                tuple(
                    int(assignment[f"{team}_pick{index}"].rsplit("pick", 1)[1]) - 1
                    for index in range(1, 6)
                )
                for team in ("blue", "red")
            )
        elif geometry_ok and self.initial_permutation is not None:
            # An enlarged reference can contaminate adjacent portrait crops.
            # Once card geometry settles, a globally constrained team pool is
            # enough to prove all ten remain present; it does not name heroes.
            current = self.pick_crops(frame)
            globally_matched = {
                team: best_portrait_assignment([current], [self.reference_sample], team)
                for team in ("blue", "red")
            }
            coverage = self.layout.get("completion", {})
            if all(
                accepted_portrait_assignment(
                    item,
                    min_similarity=coverage.get("pool_min_similarity", 0.35),
                    min_mean_similarity=coverage.get("pool_mean_similarity", 0.7),
                    # This check proves team-pool coverage only. The enlarged
                    # reference itself can make an individual row margin
                    # negative; no hero label comes from this permutation.
                    min_slot_margin=-1.0,
                    min_team_margin=0.03,
                )
                for item in globally_matched.values()
            ):
                pool_matched = True
                permutation = tuple(
                    globally_matched[team]["permutation"]
                    for team in ("blue", "red")
                )
                minimum_confidence = min(
                    min(item["assigned_scores"])
                    for item in globally_matched.values()
                )
                pool_mean_confidence = float(np.mean([
                    item["mean_similarity"] for item in globally_matched.values()
                ]))
                self.maximum_filled = 10
        if permutation is not None:
            if self.initial_permutation is None:
                self.initial_permutation = permutation
            elif permutation != self.initial_permutation:
                self.swap_seen = True
        if not pool_matched or not geometry_ok or self.swap_seen:
            if not pool_matched:
                self.incomplete_seen = True
            self.run, self.candidate = 0, None
            self.completion_observations = []
            self.completion_frames = []
        elif self.incomplete_seen:
            if self.candidate is None:
                self.candidate = (frame.copy(), time_sec)
                self.completion_observations = []
                self.completion_frames = []
            self.completion_observations.append((self.pick_crops(frame), time_sec))
            self.completion_observations = self.completion_observations[
                -self.minimum_frames :
            ]
            self.completion_frames.append((frame.copy(), time_sec))
            self.completion_frames = self.completion_frames[-self.minimum_frames :]
            self.run += 1
            if self.run >= self.minimum_frames:
                selected, timestamp = self.candidate
                return selected, {
                    "timestamp_sec": timestamp,
                    "validated_through_sec": time_sec,
                    "frames_examined": self.frames_examined,
                    "consecutive_complete_frames": self.run,
                    "status": "visual_completion_candidate",
                    "method": "video_reference_matching",
                    "minimum_similarity": minimum_confidence,
                    "mean_pool_similarity": pool_mean_confidence,
                    "swap_detected_before_selection": False,
                    "needs_human_lock_confirmation": True,
                    "settled_separator_scores": geometry_scores,
                }
        return None


def best_portrait_assignment(query_samples, reference_samples, team):
    """Find the best one-to-one slot permutation for one team's portraits."""
    query_slots = [f"{team}_pick{index}" for index in range(1, 6)]
    reference_slots = [f"{team}_pick{index}" for index in range(1, 6)]
    if not query_samples or not reference_samples or any(
        slot not in sample
        for sample in (*query_samples, *reference_samples)
        for slot in query_slots
    ):
        return None
    matrix = np.array(
        [
            [
                float(
                    np.median(
                        [
                            portrait_similarity(
                                query[query_slot], reference[reference_slot]
                            )
                            for query in query_samples
                            for reference in reference_samples
                        ]
                    )
                )
                for reference_slot in reference_slots
            ]
            for query_slot in query_slots
        ]
    )
    ranked = sorted(
        (
            (float(sum(matrix[index, choice] for index, choice in enumerate(order))), order)
            for order in permutations(range(5))
        ),
        reverse=True,
    )
    best_total, best_order = ranked[0]
    second_total = ranked[1][0]
    assigned = [float(matrix[index, choice]) for index, choice in enumerate(best_order)]
    row_margins = [
        float(matrix[index, choice] - max(np.delete(matrix[index], choice)))
        for index, choice in enumerate(best_order)
    ]
    return {
        "mapping": {
            query_slots[index]: reference_slots[choice]
            for index, choice in enumerate(best_order)
        },
        "permutation": tuple(best_order),
        "assigned_scores": assigned,
        "row_margins": row_margins,
        "team_assignment_margin": float((best_total - second_total) / 5),
        "mean_similarity": float(best_total / 5),
    }


def accepted_portrait_assignment(
    assignment,
    *,
    min_similarity=0.35,
    min_mean_similarity=0.65,
    min_slot_margin=-0.15,
    min_team_margin=0.04,
):
    return bool(
        assignment
        and min(assignment["assigned_scores"]) >= min_similarity
        and assignment["mean_similarity"] >= min_mean_similarity
        and min(assignment["row_margins"]) >= min_slot_margin
        and assignment["team_assignment_margin"] >= min_team_margin
    )


def collect_last_stable_role_observations(
    frames,
    tracker,
    *,
    first_timestamp,
    tail_sec=20,
    sampling_interval_sec=0.1,
    minimum_observations=3,
):
    """Keep the last stable complete portrait arrangement entirely in memory."""
    first_samples = [crops for crops, _ in tracker.completion_observations]
    if not first_samples:
        raise ValueError("Complete-draft observations are unavailable")
    last_sampled = first_timestamp
    active_permutation = None
    active_samples = deque(maxlen=minimum_observations)
    last_stable = None
    for frame, timestamp in frames:
        if timestamp > first_timestamp + tail_sec:
            break
        if timestamp - last_sampled < sampling_interval_sec:
            continue
        last_sampled = timestamp
        # The overlay may deliberately animate or replace its central anchors when
        # portraits move into role order. Matching the same ten portraits is the
        # stronger invariant here, so do not require the calibration anchors to
        # remain unchanged after the first complete frame has been established.
        crops = tracker.pick_crops(frame)
        assignments = {
            team: best_portrait_assignment(first_samples, [crops], team)
            for team in ("blue", "red")
        }
        if not all(accepted_portrait_assignment(value) for value in assignments.values()):
            active_permutation = None
            active_samples.clear()
            continue
        permutation = tuple(
            assignments[team]["permutation"] for team in ("blue", "red")
        )
        if permutation != active_permutation:
            active_permutation = permutation
            active_samples.clear()
        active_samples.append((crops, timestamp))
        if len(active_samples) == minimum_observations:
            last_stable = {
                "samples": [sample for sample, _ in active_samples],
                "timestamp_sec": active_samples[-1][1],
                "permutation": {
                    team: list(assignments[team]["permutation"])
                    for team in ("blue", "red")
                },
            }
    return last_stable


def find_complete_frame(frames, tracker):
    """Return the first validated complete frame without writing it to disk."""
    for frame, timestamp in frames:
        result = tracker.observe(frame, timestamp)
        if result is not None:
            return result
        if tracker.swap_seen:
            break
    return None


def decoded_frames(command, *, fps, start_sec=0, idle_timeout=30, use_timestamps=False):
    """Decode every source frame from FFmpeg's MJPEG pipe; close it on early stop.

    Only a small in-memory queue is retained. No video or surplus JPEG is written.
    The caller must close this generator (use contextlib.closing).
    """
    if fps <= 0:
        raise ValueError("Source FPS must be positive")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunks = queue.Queue(maxsize=4)
    stopped = threading.Event()
    errors = deque(maxlen=8)
    timestamps = queue.Queue()

    def read_stdout():
        while not stopped.is_set():
            chunk = process.stdout.read1(65536)
            while not stopped.is_set():
                try:
                    chunks.put(chunk, timeout=0.1)
                    break
                except queue.Full:
                    pass
            if not chunk:
                break

    def read_stderr():
        for line in process.stderr:
            errors.append(line)
            match = re.search(rb"\bn:\s*\d+.*?\bpts_time:([-\d.]+)", line)
            if match:
                timestamps.put(float(match.group(1)))

    reader = threading.Thread(target=read_stdout, daemon=True)
    error_reader = threading.Thread(target=read_stderr, daemon=True)
    reader.start()
    error_reader.start()
    pending = bytearray()
    index = 0
    try:
        while True:
            try:
                chunk = chunks.get(timeout=idle_timeout)
            except queue.Empty:
                raise TimeoutError(
                    "No video frames received within the media timeout"
                ) from None
            if not chunk:
                if process.wait(timeout=5):
                    raise RuntimeError(
                        "FFmpeg could not decode the bounded VOD section"
                    )
                break
            pending.extend(chunk)
            if len(pending) > 16 * 1024 * 1024:
                raise ValueError("Invalid oversized JPEG from decoder")
            while True:
                end = pending.find(b"\xff\xd9")
                if end < 0:
                    break
                encoded = bytes(pending[: end + 2])
                del pending[: end + 2]
                frame = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    raise ValueError("Invalid decoded frame")
                timestamp = index / fps
                if use_timestamps:
                    try:
                        timestamp = timestamps.get(timeout=idle_timeout)
                    except queue.Empty:
                        raise ValueError(
                            "Decoder did not supply a source timestamp"
                        ) from None
                yield frame, start_sec + timestamp
                index += 1
    finally:
        stopped.set()
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=5)
        error_reader.join(timeout=5)
        process.stdout.close()
        process.stderr.close()


def select_complete_frame(frames, tracker, destination):
    """Save only the selected image. The input iterator is consumed until success."""
    result = find_complete_frame(frames, tracker)
    if result is not None:
        selected, metadata = result
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.stem + ".partial.jpg")
        try:
            if not cv2.imwrite(str(temporary), selected):
                raise OSError("Unable to save complete draft frame")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {**metadata, "frame": str(destination.resolve())}
    return {
        "status": "needs_review",
        "reason": "swap_detected"
        if tracker.swap_seen
        else "no_observed_complete_transition",
        "frames_examined": tracker.frames_examined,
        "maximum_filled_slots": tracker.maximum_filled,
        "anchor_frames": tracker.anchor_frames,
    }
