"""VOD metadata, verification, cache identity, and decoder commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

from backend.services.data.vod_pick_order_suggestions import (
    HERO_REFERENCE_GALLERY_DIR,
    hero_reference_paths,
)
from backend.services.data.vod_sources import VideoRecord, score_video_for_game


class UnverifiedGameVodError(ValueError):
    pass


def video_info(url: str) -> dict[str, Any]:
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


def verified_vod_records(
    payload: dict[str, Any],
    entries: list[dict[str, Any]],
    channels: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Validate reusable live/manual per-game VOD verification for selected games."""
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(
        payload.get("games"), list
    ):
        raise ValueError("VOD verification must contain a version 1 games list")
    rows = payload["games"]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Each VOD verification game must be an object")
    if len({row.get("game_id") for row in rows}) != len(rows):
        raise ValueError("VOD verification contains duplicate game IDs")
    by_id = {row.get("game_id"): row for row in rows}
    accepted = {}
    invalid = []
    for entry in entries:
        row = by_id.get(entry["game_id"], {})
        mode = row.get("verification_mode")
        manual_ok = (
            mode == "manual"
            and row.get("reviewer")
            and row.get("verification_evidence")
        )
        if not (
            row.get("verified")
            and (mode == "live" or manual_ok)
            and row.get("video_id") == entry.get("video_id")
            and row.get("channel_id") == channels.get(entry.get("source_id"))
            and row.get("source_kind") == "per_game"
            and not row.get("rejection_reasons")
        ):
            invalid.append(entry["game_id"])
            continue
        details = dict(row.get("candidate_details") or {})
        details.setdefault("match_confidence", 1.0)
        details["verification_mode"] = mode
        accepted[entry["game_id"]] = details
    if invalid:
        raise ValueError(
            f"VOD verification leaves {len(invalid)} selected games unverified"
        )
    return accepted


def verify_game_video(
    info: dict[str, Any],
    raw_game: dict[str, Any],
    entry: dict[str, Any],
    source_registry: dict[str, Any],
) -> dict[str, Any]:
    """Confirm that current metadata identifies the intended official per-game VOD."""
    source = next(
        (
            item
            for item in source_registry["sources"]
            if item["source_id"] == entry.get("source_id")
        ),
        None,
    )
    if source is None:
        raise UnverifiedGameVodError("unknown_official_video_source")
    raw_date = str(info.get("upload_date") or "")
    published = (
        f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}T00:00:00Z"
        if len(raw_date) == 8 and raw_date.isdigit()
        else ""
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
        candidate["source_kind"] != "per_game"
        or candidate["match_confidence"] < 0.85
        or candidate["rejection_reasons"]
        or info.get("id") != entry.get("video_id")
    ):
        raise UnverifiedGameVodError("unverified_game_video_metadata")
    return candidate


def input_identity(
    entry: dict[str, Any],
    profile: dict[str, Any],
    reference: dict[str, Any] | None,
    *,
    start: float,
    duration: float,
    probe: float | None,
    extractor_version: str,
    raw_game: dict[str, Any] | None = None,
    vod_verification: dict[str, Any] | None = None,
    evidence_mode: str = "frame",
    tail_sec: float = 20,
    gallery_dir: Path = HERO_REFERENCE_GALLERY_DIR,
) -> str:
    """Invalidate cached output when any meaningful capture input changes."""
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
                    if gallery_dir == HERO_REFERENCE_GALLERY_DIR
                    else hero_reference_paths(str(hero), gallery_dir=gallery_dir)
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
        vod_verification,
        extractor_version,
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def decoder_command(
    ffmpeg: str,
    stream_url: str,
    start: float,
    duration: float,
    *,
    one_frame: bool = False,
) -> list[str]:
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
