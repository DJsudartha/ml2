"""Infer review-only pick orders from bounded VOD streams without saving full frames."""

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
EXTRACTOR_VERSION = "complete_draft_v10"
sys.path.insert(0, str(ROOT))
from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.data.complete_draft import (  # noqa: E402
    CompletionTracker,
    ReferenceCompletionTracker,
    analysis_image,
    decoded_frames,
    find_complete_frame,
    portrait_similarity,
)
from backend.services.data.broadcast_hero_names import (  # noqa: E402
    read_broadcast_hero_names,
)
from backend.services.data.raw_games import (  # noqa: E402
    RAW_TOURNAMENTS_DIR,
    load_raw_games_by_id,
)
from backend.services.data.liquipedia_vods import parse_youtube_vod  # noqa: E402
from backend.services.data.pick_order_media_rights import require_media_rights  # noqa: E402
from backend.services.data.pick_order_gallery_release import (  # noqa: E402
    validate_gallery_release,
)
from backend.services.data.vod_sources import (  # noqa: E402
    VideoRecord,
    score_video_for_game,
)
from backend.services.data.vod_pick_order_suggestions import (  # noqa: E402
    HERO_REFERENCE_GALLERY_DIR,
    hero_reference_paths,
    recognize_hero_crop,
    suggest_pick_order_from_game_evidence,
)


def video_info(url):
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--dump-single-json",
                "--skip-download",
                "--no-warnings",
                "--socket-timeout",
                "15",
                "--retries",
                "1",
                "--no-playlist",
                "-f",
                "bestvideo[height<=720][protocol=m3u8_native][vcodec^=avc1]/"
                "bestvideo[height<=720][protocol=m3u8_native]/bestvideo[height<=720]",
                url,
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, json.JSONDecodeError):
        raise RuntimeError("Video metadata unavailable within timeout") from None


class UnverifiedGameVodError(ValueError):
    pass


def _verify_game_video(info, raw_game, entry, source_registry):
    """LP association and uploader alone cannot prove the correct game video."""
    source = next(
        (item for item in source_registry["sources"]
         if item["source_id"] == entry.get("source_id")), None
    )
    if source is None:
        raise UnverifiedGameVodError("unknown_official_video_source")
    raw_date = str(info.get("upload_date") or "")
    published = (
        f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}T00:00:00Z"
        if len(raw_date) == 8 and raw_date.isdigit() else ""
    )
    candidate = score_video_for_game(
        raw_game,
        VideoRecord(
            video_id=str(info.get("id") or ""),
            channel_id=str(info.get("channel_id") or ""),
            title=str(info.get("title") or ""),
            description=str(info.get("description") or ""),
            published_at=published,
            duration_sec=int(info.get("duration") or 0),
            actual_start_time=None,
            source_id=source["source_id"],
        ),
        source,
        source_registry.get("team_aliases", {}),
    )
    if (
        candidate["source_kind"] != "per_game" or
        candidate["match_confidence"] < 0.85 or
        candidate["rejection_reasons"] or
        info.get("id") != entry.get("video_id")
    ):
        raise UnverifiedGameVodError("unverified_game_video_metadata")
    return candidate


def input_identity(
    entry,
    profile,
    reference,
    *,
    start,
    duration,
    probe,
    raw_game=None,
    evidence_mode="frame",
    tail_sec=20,
    gallery_dir=HERO_REFERENCE_GALLERY_DIR,
):
    """Invalidate results when media, calibration, data or scan bounds change."""
    files = {}
    for path in (
        profile.get("placeholder_frame"),
        profile.get("layouts_file"),
        (reference or {}).get("frame"),
    ):
        if path:
            files[str(path)] = (
                hashlib.sha256(Path(path).read_bytes()).hexdigest()
                if Path(path).is_file()
                else None
            )
    if raw_game:
        for team in ("blue", "red"):
            for hero in raw_game.get(f"{team}_picks", []):
                references = (
                    hero_reference_paths(str(hero))
                    if gallery_dir == HERO_REFERENCE_GALLERY_DIR else
                    hero_reference_paths(str(hero), gallery_dir=gallery_dir)
                )
                for path in references:
                    files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = [
        entry,
        profile,
        reference,
        raw_game,
        files,
        start,
        duration,
        probe,
        evidence_mode,
        tail_sec,
        EXTRACTOR_VERSION,
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("_")


def _write_image(path, image):
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".partial.jpg")
    try:
        if not cv2.imwrite(str(temporary), image):
            raise OSError(f"Unable to write evidence image {path}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _persist_evidence(
    suggestion,
    first_frame,
    first_crops,
    *,
    output_dir,
    video_id,
    identity,
    evidence_mode,
    lock_event_crops=None,
    lock_events=None,
):
    if evidence_mode == "none":
        suggestion["review_crops"] = []
        return
    if evidence_mode == "frame":
        path = output_dir / "completed" / f"{video_id}_{identity}.jpg"
        _write_image(path, first_frame)
        resolved = str(path.resolve())
        suggestion["frame"] = resolved
        for pick in suggestion.get("picks", []):
            pick["evidence_frame"] = resolved
            pick["evidence_frames"] = [resolved]
        return

    evidence_dir = output_dir / "evidence" / f"{video_id}_{identity}"
    picks_by_slot = {pick["slot"]: pick for pick in suggestion.get("picks", [])}
    review_crops = []
    for slot in sorted(first_crops):
        pick = picks_by_slot.get(slot)
        path = evidence_dir / f"{slot}_settled.jpg"
        _write_image(path, first_crops[slot])
        resolved = str(path.resolve())
        review_crops.append(
            {
                "slot": slot,
                "phase": "first_settled_pre_swap",
                "frame": resolved,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        if pick:
            pick["evidence_frame"] = resolved
            pick["evidence_frames"] = [resolved]
    suggestion["review_crops"] = review_crops
    events = {item["slot"]: item for item in lock_events or []}
    sequences = []
    for slot, crops in sorted((lock_event_crops or {}).items()):
        event = events.get(slot, {})
        for phase, crop, timestamp in (
            ("first_visible", crops[0], event.get("timestamp_sec")),
            ("stable_locked", crops[1], event.get("stable_through_sec")),
        ):
            path = evidence_dir / f"{slot}_{phase}.jpg"
            _write_image(path, crop)
            sequences.append({
                "slot": slot, "phase": phase, "timestamp_sec": timestamp,
                "frame": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
    suggestion["review_sequences"] = sequences


def _cached_evidence_exists(row, evidence_mode):
    if evidence_mode == "none":
        return True
    if evidence_mode == "frame":
        return bool(row.get("frame") and Path(row["frame"]).is_file())
    crops = row.get("review_crops", [])
    return len(crops) == 10 and all(
        Path(crop.get("frame", "")).is_file() for crop in crops
    )


def _layout(profile):
    if profile.get("layout") is not None:
        return profile["layout"]
    return load_json(Path(profile["layouts_file"]))["layouts"][profile["layout_id"]]


def _tracker(layout, baseline, reference, source_fps):
    if reference:
        import cv2

        image = cv2.imread(reference["frame"])
        if image is None:
            raise ValueError("Unreadable verified hero-reference frame")
        return ReferenceCompletionTracker(
            layout, image, minimum_frames=3, calibration_frame=baseline
        )
    return CompletionTracker(
        layout,
        baseline,
        minimum_frames=max(3, round(float(source_fps) * 0.15)),
    )


def _sample_identity_frames(frames, tracker, first_frame, completion):
    """Take sparse in-memory observations; stop on role swaps or lost geometry."""
    samples = [(first_frame, completion["timestamp_sec"])]
    first_crops = tracker.pick_crops(first_frame)
    last_sample = float(completion["timestamp_sec"])
    deadline = last_sample + 2.5
    for frame, timestamp in frames:
        if timestamp > deadline:
            break
        if timestamp - last_sample < 0.5:
            continue
        analysis = analysis_image(frame)
        geometry_ok, _ = tracker.settled_card_geometry(analysis)
        current = tracker.pick_crops(frame)
        if not geometry_ok or any(
            portrait_similarity(first_crops[slot], current[slot]) < 0.65
            for slot in first_crops
        ):
            return samples, ["post_completion_swap_or_layout_change"]
        samples.append((frame, timestamp))
        last_sample = timestamp
        if len(samples) >= 3:
            break
    return samples, ([] if len(samples) >= 2 else ["identity_samples_not_time_separated"])


def _identity_observations(
    profile, layout, raw_game, tracker, first_frame, completion,
    sampled_frames=None, gallery_dir=HERO_REFERENCE_GALLERY_DIR,
):
    """Recognize the settled cards in memory; require repeated agreement."""
    mode = profile.get("identity_mode", "visual_reference")
    observations = []
    warnings = []
    if mode == "broadcast_names":
        try:
            from rapidocr import RapidOCR
        except ImportError:
            return [], ["broadcast_name_ocr_dependency_missing"]
        engine = RapidOCR()
        frames = sampled_frames or tracker.completion_frames or [
            (first_frame, completion["timestamp_sec"])
        ]
        for frame, timestamp in frames:
            observations.extend(
                read_broadcast_hero_names(
                    frame, layout, raw_game, engine, timestamp_sec=timestamp
                )
            )
    elif mode == "visual_reference":
        if sampled_frames:
            samples = [
                (tracker.pick_crops(frame), timestamp)
                for frame, timestamp in sampled_frames
            ]
        else:
            samples = getattr(tracker, "completion_observations", [])
        for sample, timestamp in samples:
            for slot, crop in sample.items():
                team = slot.split("_")[0]
                candidates = list(raw_game.get(f"{team}_picks", []))
                match = (
                    recognize_hero_crop(crop, candidates)
                    if gallery_dir == HERO_REFERENCE_GALLERY_DIR else
                    recognize_hero_crop(crop, candidates, gallery_dir=gallery_dir)
                )
                if match.get("hero"):
                    observations.append({
                        "slot": slot,
                        "hero": match["hero"],
                        "confidence": match["confidence"],
                        "source": "canonical_or_confirmed_gallery",
                        "timestamp_sec": timestamp,
                    })
    else:
        raise ValueError(f"Unsupported game identity mode {mode}")

    grouped = {}
    for observation in observations:
        key = (observation["slot"], observation["hero"])
        grouped.setdefault(key, []).append(observation)
    stable = []
    for (slot, hero), seen in grouped.items():
        times = sorted({float(observation["timestamp_sec"])
                        for observation in seen})
        if len(times) < 2 or times[-1] - times[0] < 0.4:
            continue
        stable.append({
            "slot": slot,
            "hero": hero,
            "confidence": sum(float(item["confidence"]) for item in seen) / len(seen),
            "source": seen[0]["source"],
            "observations": len(seen),
            "timestamp_sec": times[0],
            "last_observed_sec": times[-1],
        })
    if mode == "visual_reference":
        rejected_slots = set()
        for team in ("blue", "red"):
            for hero in raw_game.get(f"{team}_picks", []):
                duplicates = sorted(
                    (item for item in stable
                     if item["slot"].startswith(f"{team}_")
                     and item["hero"] == hero),
                    key=lambda item: item["confidence"],
                    reverse=True,
                )
                if len(duplicates) < 2:
                    continue
                # One hero cannot occupy two pick cards on the same team.
                # Keep a clear stronger observation; otherwise reject both.
                keep = 1 if duplicates[0]["confidence"] - duplicates[1]["confidence"] >= 0.1 else 0
                rejected_slots.update(item["slot"] for item in duplicates[keep:])
                warnings.append(f"{team}_duplicate_visual_identity_{hero}")
        stable = [item for item in stable if item["slot"] not in rejected_slots]
    if mode == "visual_reference" and len(stable) < 10:
        warnings.append("broadcast_art_gallery_incomplete_or_ambiguous")
    return stable, warnings


def _decoder_command(ffmpeg, stream_url, start, duration, *, one_frame=False):
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "info",
        "-rw_timeout",
        "15000000",
        "-threads",
        "2",
        "-ss",
        str(start),
        "-i",
        stream_url,
        "-t",
        str(duration),
        "-an",
        "-sn",
        "-vf",
        "showinfo",
        "-fps_mode",
        "passthrough",
        "-f",
        "image2pipe",
        "-c:v",
        "mjpeg",
        "-threads",
        "1",
        "-q:v",
        "2",
        "pipe:1",
    ]
    if one_frame:
        command[-1:-1] = ["-frames:v", "1"]
    return command


def _incomplete_suggestion(game_id, reason, completion=None, tracker=None):
    return {
        **(completion or {}),
        "game_id": game_id,
        "source": "vod_complete_frame_assignment",
        "status": "needs_review",
        "order_complete": False,
        "confidence": 0.0,
        "notes": reason,
        "extractor_version": EXTRACTOR_VERSION,
        "assignment_method": "per_game_hero_evidence",
        "detection_diagnostics": (
            {
                "frames_examined": tracker.frames_examined,
                "anchor_frames": tracker.anchor_frames,
                "maximum_filled": tracker.maximum_filled,
                "swap_seen": tracker.swap_seen,
            }
            if tracker is not None else None
        ),
        "picks": [],
    }


def _selected_entries(args):
    manifest = load_json(args.manifest)
    holdout_selection = manifest.get("purpose") == "blind_match_disjoint_holdout"
    entries = [
        entry
        for entry in manifest["games"]
        if (entry.get("status") == "matched" or
            (holdout_selection and entry.get("status") is None))
        and (not args.game_id or entry["game_id"] == args.game_id)
        and (not args.source_id or entry.get("source_id") == args.source_id)
    ]
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be positive")
        entries = entries[: args.limit]
    return entries


def _report_payload(rows, requested_games):
    return {
        "version": 1,
        "extractor_version": EXTRACTOR_VERSION,
        "counts": {
            "requested_games": requested_games,
            "complete_orders": sum(bool(row.get("order_complete")) for row in rows),
            "needs_review": sum(row.get("status") == "needs_review" for row in rows),
            "failed": sum(row.get("status") == "failed" for row in rows),
            "evidence_crops": sum(len(row.get("review_crops", [])) for row in rows),
            "full_frames": sum(bool(row.get("frame")) for row in rows),
        },
        "games": rows,
        "requested_games": requested_games,
    }


def _review_import(args):
    """Exercise the public review output with supplied non-media evidence."""
    payload = load_json(args.evidence_json)
    if not isinstance(payload, dict) or not isinstance(payload.get("games"), list):
        raise ValueError("Evidence input must contain games list")
    rows = []
    for item in payload["games"]:
        raw_game = item["raw_game"]
        completion = item.get("completion", {})
        provenance = item.get("provenance", {})
        suggestion = suggest_pick_order_from_game_evidence(
            raw_game,
            item.get("slot_observations", []),
            lock_events=item.get("lock_events", []),
            first_settled_timestamp_sec=completion.get("timestamp_sec"),
            require_lock_events=True,
            complete_frame_verified=(
                completion.get("swap_detected_before_selection") is False
            ),
            provenance={"capture_extractor_version": EXTRACTOR_VERSION, **provenance},
        )
        suggestion.update(completion)
        suggestion["status"] = "needs_review"
        suggestion["extractor_version"] = EXTRACTOR_VERSION
        rows.append(suggestion)
    report = _report_payload(rows, len(rows))
    save_json(args.output_dir / "report.json", report)
    print(json.dumps(report["counts"], sort_keys=True))
    return 0 if all(row.get("order_complete") for row in rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--evidence-json", type=Path,
        help="Review supplied observations/events without accessing or saving media",
    )
    parser.add_argument("--profiles", type=Path)
    parser.add_argument(
        "--media-rights-file", type=Path,
        help="Private record of actual authorization for selected YouTube channels",
    )
    parser.add_argument(
        "--gallery-manifest", type=Path,
        help="Immutable hash manifest of the authorized private reference gallery",
    )
    parser.add_argument(
        "--gallery-dir", type=Path, default=HERO_REFERENCE_GALLERY_DIR,
        help="Location of the frozen local reference gallery",
    )
    parser.add_argument("--raw-dir", type=Path, default=RAW_TOURNAMENTS_DIR)
    parser.add_argument(
        "--references", type=Path, help="Optional compatibility fallback keyed by video ID"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--probe-sec", type=float)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--game-id")
    parser.add_argument("--source-id", help="Restrict processing to one broadcaster")
    parser.add_argument("--start-sec", type=float)
    parser.add_argument("--duration-sec", type=float, default=1500)
    parser.add_argument("--tail-sec", type=float, default=20)
    parser.add_argument(
        "--evidence-mode", choices=("crops", "frame", "none"), default="crops"
    )
    args = parser.parse_args()
    if args.evidence_json:
        if not args.evidence_json.is_file():
            parser.error("--evidence-json file not found")
        try:
            return _review_import(args)
        except (KeyError, TypeError, ValueError) as exc:
            parser.error(str(exc))
    if args.manifest is None:
        parser.error("--manifest is required for VOD capture")
    gallery_release = None
    try:
        selected_for_rights = _selected_entries(args)
    except ValueError as exc:
        parser.error(str(exc))
    youtube_entries = [entry for entry in selected_for_rights
                       if parse_youtube_vod(entry.get("vod_url"))]
    if youtube_entries:
        registry = load_json(ROOT / "backend/data/vod_sources.json")
        channel_ids = {
            source["source_id"]: source["channel_id"]
            for source in registry["sources"]
        }
        try:
            require_media_rights(
                args.media_rights_file,
                {channel_ids[entry["source_id"]] for entry in youtube_entries},
            )
            if args.gallery_manifest is None:
                raise ValueError("Frozen gallery manifest is required for new YouTube capture")
            if args.probe_sec is None:
                gallery_release = validate_gallery_release(
                    args.gallery_dir, args.gallery_manifest
                )
        except (KeyError, TypeError, ValueError) as exc:
            parser.error(str(exc))
    import cv2

    cv2.setNumThreads(1)
    if not 0 < args.duration_sec <= 1500 or not 0 < args.tail_sec <= 60:
        parser.error("Use a positive duration up to 1500 seconds and tail up to 60 seconds")
    if args.probe_sec is not None and args.probe_sec < 0:
        parser.error("--probe-sec must be nonnegative")
    if args.start_sec is not None and args.start_sec < 0:
        parser.error("--start-sec must be nonnegative")
    if args.probe_sec is None and not args.profiles:
        parser.error("--profiles is required for complete-draft capture")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        parser.error("FFmpeg must be available on PATH")

    profiles = load_json(args.profiles) if args.profiles else {}
    references = load_json(args.references) if args.references else {}
    raw_games = load_raw_games_by_id(args.raw_dir) if args.probe_sec is None else {}
    try:
        entries = _selected_entries(args)
    except ValueError as exc:
        parser.error(str(exc))
    if not entries:
        parser.error("No matched games selected")
    missing_games = [entry["game_id"] for entry in entries if entry["game_id"] not in raw_games]
    if args.probe_sec is None and missing_games:
        parser.error(f"Raw Liquipedia games missing for {len(missing_games)} selected entries")

    sources = load_json(ROOT / "backend/data/vod_sources.json")
    approved = {source["source_id"]: source["channel_id"] for source in sources["sources"]}
    report_path = args.output_dir / (
        "probes.json" if args.probe_sec is not None else "report.json"
    )
    previous = (
        {row["game_id"]: row for row in load_json(report_path).get("games", [])}
        if report_path.exists()
        else {}
    )
    rows = []
    for entry in entries:
        reference = references.get(entry["video_id"])
        profile = profiles.get(entry.get("layout_id"), {})
        raw_game = raw_games.get(entry["game_id"])
        start = (
            args.probe_sec
            if args.probe_sec is not None
            else entry.get("scan_start_sec", 0)
        )
        if args.start_sec is not None and args.probe_sec is None:
            start = args.start_sec
        elif reference and args.probe_sec is None:
            start = reference.get("scan_start_sec", start)
        identity = input_identity(
            entry,
            profile,
            reference,
            start=start,
            duration=args.duration_sec,
            probe=args.probe_sec,
            raw_game=raw_game,
            evidence_mode=args.evidence_mode,
            tail_sec=args.tail_sec,
            gallery_dir=args.gallery_dir,
        )
        cached = previous.get(entry["game_id"], {})
        if (
            cached.get("job_identity") == identity
            and cached.get("order_complete")
            and _cached_evidence_exists(cached, args.evidence_mode)
        ):
            rows.append(cached)
            print(f"{entry['video_id']}: reusing completed order", flush=True)
            continue
        row = {
            "game_id": entry["game_id"],
            "video_id": entry["video_id"],
            "job_identity": identity,
        }
        try:
            info = video_info(entry["vod_url"])
            if info.get("channel_id") != approved.get(entry["source_id"]):
                raise ValueError("Unexpected video uploader")
            metadata_match = (
                _verify_game_video(info, raw_game, entry, sources)
                if parse_youtube_vod(entry.get("vod_url")) and
                args.probe_sec is None else None
            )
            command = _decoder_command(
                ffmpeg,
                info["url"],
                start,
                args.duration_sec,
                one_frame=args.probe_sec is not None,
            )
            if args.probe_sec is not None:
                with closing(
                    decoded_frames(
                        command,
                        fps=float(info["fps"]),
                        start_sec=start,
                        use_timestamps=True,
                    )
                ) as frames:
                    frame, timestamp = next(frames)
                destination = (
                    args.output_dir / "probes" / f"{entry['video_id']}_{identity}.jpg"
                )
                _write_image(destination, frame)
                row.update(
                    status="calibration_probe",
                    timestamp_sec=timestamp,
                    frame=str(destination.resolve()),
                )
            else:
                baseline = cv2.imread(profile["placeholder_frame"])
                if baseline is None:
                    raise ValueError("Unreadable placeholder reference")
                layout = _layout(profile)
                result = None
                for attempt in range(3):
                    tracker = _tracker(layout, baseline, reference, info["fps"])
                    try:
                        with closing(
                            decoded_frames(
                                command,
                                fps=float(info["fps"]),
                                start_sec=start,
                                use_timestamps=True,
                            )
                        ) as frames:
                            complete = find_complete_frame(frames, tracker)
                            if complete is None:
                                if attempt < 2 and tracker.maximum_filled >= 8:
                                    print(
                                        f"{entry['video_id']}: retry {attempt + 1}/2 "
                                        "after incomplete transition",
                                        flush=True,
                                    )
                                    continue
                                result = _incomplete_suggestion(
                                    entry["game_id"],
                                    "no_observed_complete_transition",
                                    tracker=tracker,
                                )
                                break
                            first_frame, completion = complete
                            sampled_frames, sampling_warnings = _sample_identity_frames(
                                frames, tracker, first_frame, completion
                            )
                            first_samples = [
                                crops for crops, _ in tracker.completion_observations
                            ]
                            first_crops = first_samples[0]
                            observations, warnings = _identity_observations(
                                profile, layout, raw_game, tracker, first_frame,
                                completion, sampled_frames=sampled_frames,
                                gallery_dir=args.gallery_dir,
                            )
                            warnings.extend(sampling_warnings)
                            provenance = {
                                "liquipedia_match_id": raw_game.get("liquipedia_match_id"),
                                "liquipedia_game_id": raw_game.get("liquipedia_game_id"),
                                "video_id": entry["video_id"],
                                "source_id": entry.get("source_id"),
                                "source_kind": entry.get("source_kind"),
                                "layout_id": entry.get("layout_id"),
                                "layout_version": profile.get("version"),
                                "layout_validated": (
                                    tracker.anchor_frames >= 3 and
                                    not tracker.swap_seen and
                                    bool(profile.get("version"))
                                ),
                                "first_settled_timestamp_sec": completion["timestamp_sec"],
                                "capture_extractor_version": EXTRACTOR_VERSION,
                                "gallery_release_id": (
                                    gallery_release["release_id"]
                                    if gallery_release else None
                                ),
                                "video_match_verified": metadata_match is not None,
                                "video_match_confidence": (
                                    metadata_match["match_confidence"]
                                    if metadata_match else None
                                ),
                                "automatic_window": (
                                    args.start_sec is None and reference is None and
                                    args.probe_sec is None
                                ),
                                "manual_window_supplied": args.start_sec is not None,
                                "video_reference_supplied": reference is not None,
                            }
                            result = suggest_pick_order_from_game_evidence(
                                raw_game,
                                observations,
                                provenance=provenance,
                                complete_frame_verified=(
                                    not tracker.swap_seen and tracker.anchor_frames >= 3
                                ),
                                lock_events=tracker.lock_events,
                                first_settled_timestamp_sec=completion["timestamp_sec"],
                                require_lock_events=True,
                            )
                            result.update(completion)
                            result["identity_warnings"] = warnings
                            result["status"] = "needs_review"
                            _persist_evidence(
                                result,
                                first_frame,
                                first_crops,
                                output_dir=args.output_dir,
                                video_id=entry["video_id"],
                                identity=identity,
                                evidence_mode=args.evidence_mode,
                                lock_event_crops=tracker.lock_event_crops,
                                lock_events=tracker.lock_events,
                            )
                        break
                    except (TimeoutError, RuntimeError):
                        if attempt == 2:
                            raise
                        print(f"{entry['video_id']}: retry {attempt + 1}/2", flush=True)
                result["source_fps"] = info["fps"]
                row.update(result)
        except UnverifiedGameVodError as exc:
            row.update(_incomplete_suggestion(entry["game_id"], str(exc)))
        except Exception as exc:
            row.update(status="failed", order_complete=False, reason=type(exc).__name__)
        rows.append(row)
        save_json(report_path, _report_payload(rows, len(entries)))
        detail = (
            f"order_complete={bool(row.get('order_complete'))}; "
            f"confidence={row.get('confidence', 0):.4f}"
            if args.probe_sec is None
            else f"timestamp_sec={row.get('timestamp_sec')}"
        )
        print(f"{entry['video_id']}: {row['status']}; {detail}", flush=True)
    save_json(report_path, _report_payload(rows, len(entries)))
    return (
        0
        if all(
            row.get("order_complete") or row.get("status") == "calibration_probe"
            for row in rows
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
