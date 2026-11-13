"""Infer review-only pick orders from bounded VOD streams without saving full frames."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.services.common.file_utils import load_json, save_json  # noqa: E402
from backend.services.data.complete_draft import (  # noqa: E402
    decoded_frames,
    find_complete_frame,
)
from backend.services.data.complete_draft_identity import (  # noqa: E402
    identity_observations as _identity_observations,
    make_completion_tracker as _tracker,
    profile_with_asset_root as _profile_with_asset_root,
    resolve_layout as _layout,
    sample_identity_frames as _sample_identity_frames,
)
from backend.services.data.complete_draft_vod import (  # noqa: E402
    UnverifiedGameVodError,
    decoder_command as _decoder_command,
    input_identity as _capture_input_identity,
    verified_vod_records as _verified_vod_records,
    verify_game_video as _verify_game_video,
    video_info,
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
from backend.services.data.pick_order_results import (  # noqa: E402
    COMPLETE_DRAFT_EXTRACTOR_VERSION,
    failed_pick_order_result,
    incomplete_pick_order_result,
)
from backend.services.data.pick_order_review_artifacts import (  # noqa: E402
    cached_evidence_exists as _cached_evidence_exists,
    capture_report_payload,
    persist_evidence as _persist_evidence,
    write_image as _write_image,
)

from backend.services.data.vod_pick_order_suggestions import (  # noqa: E402
    HERO_REFERENCE_GALLERY_DIR,
    suggest_pick_order_from_game_evidence,
)

EXTRACTOR_VERSION = COMPLETE_DRAFT_EXTRACTOR_VERSION


def input_identity(
    entry,
    profile,
    reference,
    *,
    start,
    duration,
    probe,
    raw_game=None,
    vod_verification=None,
    evidence_mode="frame",
    tail_sec=20,
    gallery_dir=HERO_REFERENCE_GALLERY_DIR,
):
    """Compatibility facade that injects this command's extractor version."""
    return _capture_input_identity(
        entry,
        profile,
        reference,
        start=start,
        duration=duration,
        probe=probe,
        extractor_version=EXTRACTOR_VERSION,
        raw_game=raw_game,
        vod_verification=vod_verification,
        evidence_mode=evidence_mode,
        tail_sec=tail_sec,
        gallery_dir=gallery_dir,
    )


def _incomplete_suggestion(game_id, reason, completion=None, tracker=None):
    return incomplete_pick_order_result(
        game_id=game_id,
        reason=reason,
        extractor_version=EXTRACTOR_VERSION,
        completion=completion,
        detection_diagnostics=(
            {
                "frames_examined": tracker.frames_examined,
                "anchor_frames": tracker.anchor_frames,
                "maximum_filled": tracker.maximum_filled,
                "swap_seen": tracker.swap_seen,
            }
            if tracker is not None
            else None
        ),
    )


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


def _manifest_game(entry):
    """Use a fixed manifest row only when it carries both valid final hero sets."""
    if not entry.get("game_id"):
        return None
    for team in ("blue", "red"):
        picks = entry.get(f"{team}_picks")
        if (
            not isinstance(picks, list)
            or len(picks) != 5
            or not all(isinstance(hero, str) and hero for hero in picks)
            or len(set(picks)) != 5
        ):
            return None
    game = dict(entry)
    game.setdefault("source_file", str(entry["game_id"]).split("::", 1)[0])
    try:
        game.setdefault("game_no", int(str(entry["game_id"]).rsplit("::", 1)[-1]))
    except ValueError:
        pass
    return game


def _report_payload(rows, requested_games):
    """Compatibility facade for the capture command's stable report helper."""
    return capture_report_payload(rows, requested_games, EXTRACTOR_VERSION)


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
        "--profile-assets-root",
        type=Path,
        help="Resolve missing relative profile assets from another checkout/root",
    )
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
    parser.add_argument(
        "--vod-verification",
        type=Path,
        help="Previously verified official per-game VOD audit (version 1)",
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
    for entry in entries:
        if entry["game_id"] not in raw_games:
            manifest_game = _manifest_game(entry)
            if manifest_game is not None:
                raw_games[entry["game_id"]] = manifest_game
    missing_games = [
        entry["game_id"] for entry in entries if entry["game_id"] not in raw_games
    ]
    if args.probe_sec is None and missing_games:
        parser.error(f"Raw Liquipedia games missing for {len(missing_games)} selected entries")

    sources = load_json(ROOT / "backend/data/vod_sources.json")
    approved = {source["source_id"]: source["channel_id"] for source in sources["sources"]}
    verification_records = {}
    if args.vod_verification:
        if not args.vod_verification.is_file():
            parser.error("--vod-verification file not found")
        try:
            verification_records = _verified_vod_records(
                load_json(args.vod_verification), entries, approved
            )
        except (KeyError, TypeError, ValueError) as exc:
            parser.error(str(exc))
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
        profile = _profile_with_asset_root(
            profiles.get(entry.get("layout_id"), {}), args.profile_assets_root
        )
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
            vod_verification=verification_records.get(entry["game_id"]),
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
            is_youtube = parse_youtube_vod(entry.get("vod_url")) is not None
            if is_youtube and info.get("id") != entry.get("video_id"):
                raise UnverifiedGameVodError("unverified_game_video_id")
            metadata_match = (
                verification_records.get(entry["game_id"])
                or _verify_game_video(info, raw_game, entry, sources)
                if is_youtube and args.probe_sec is None else None
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
                                if (
                                    attempt < 2
                                    and tracker.maximum_filled >= 8
                                    and not tracker.swap_seen
                                ):
                                    print(
                                        f"{entry['video_id']}: retry {attempt + 1}/2 "
                                        "after incomplete transition",
                                        flush=True,
                                    )
                                    continue
                                result = _incomplete_suggestion(
                                    entry["game_id"],
                                    (
                                        "slot_movement_after_lock"
                                        if tracker.swap_seen
                                        else "no_observed_complete_transition"
                                    ),
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
                                "slot_semantics": profile.get("slot_semantics"),
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
                                "video_verification_mode": (
                                    metadata_match.get("verification_mode")
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
            row.update(
                failed_pick_order_result(
                    game_id=entry["game_id"], reason=type(exc).__name__
                )
            )
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
