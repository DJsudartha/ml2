"""Resolve curated game links without title search or a YouTube API key.

Liquipedia supplies the game association, not proof of uploader identity or media
availability. Series-only links remain review items until their game is located.
"""

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit


def read_video_metadata(url):
    import yt_dlp

    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": 20,
            "retries": 2,
        }
    ) as client:
        return client.extract_info(url, download=False)


def verify_manifest_media(
    manifest, source_registry, metadata_reader=read_video_metadata
):
    channels = {s["source_id"]: s["channel_id"] for s in source_registry["sources"]}

    def verify(entry):
        entry = dict(entry)
        if entry.get("status") != "matched":
            return entry
        try:
            video = metadata_reader(entry["vod_url"])
            entry["channel_id"] = video.get("channel_id")
            entry["video_title"] = video.get("title")
            entry["video_duration_sec"] = video.get("duration")
            if video.get("id") != entry["video_id"] or video.get(
                "channel_id"
            ) != channels.get(entry["source_id"]):
                entry.update(
                    status="needs_review",
                    availability="metadata_mismatch",
                    reason="Video ID or uploader does not match approved source.",
                )
            elif video.get("is_live") or not video.get("duration"):
                entry.update(
                    status="needs_review",
                    availability="not_finished",
                    reason="Media is live or has no known duration.",
                )
            elif float(entry.get("vod_timestamp_sec") or 0) >= float(video["duration"]):
                entry.update(
                    status="needs_review",
                    availability="invalid_timestamp",
                    reason="VOD timestamp exceeds media duration.",
                )
            else:
                entry["availability"] = "metadata_verified"
        except Exception as exc:
            # Do not persist signed stream URLs or request headers from exceptions.
            entry.update(
                status="needs_review",
                availability="unavailable",
                reason=f"Metadata check failed ({type(exc).__name__}).",
            )
        return entry

    with ThreadPoolExecutor(max_workers=2) as workers:
        return {**manifest, "games": list(workers.map(verify, manifest["games"]))}


def parse_youtube_vod(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        url = urlsplit(value.strip())
        if url.scheme not in ("https", "http") or url.username or url.password:
            return None
        host = (url.hostname or "").lower()
        query = parse_qs(url.query)
        if host == "youtu.be":
            video_id = url.path.strip("/")
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            parts = url.path.strip("/").split("/")
            video_id = (
                query.get("v", [""])[0]
                if url.path == "/watch"
                else (
                    parts[1]
                    if len(parts) == 2 and parts[0] in ("live", "embed")
                    else ""
                )
            )
        else:
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return None
        stamp = query.get(
            "t", query.get("start", parse_qs(url.fragment).get("t", ["0"]))
        )[0]
        if stamp.isdigit():
            seconds = int(stamp)
        else:
            m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", stamp)
            if not m or not stamp:
                return None
            seconds = sum(
                int(v or 0) * scale for v, scale in zip(m.groups(), (3600, 60, 1))
            )
        return {
            "video_id": video_id,
            "vod_url": f"https://www.youtube.com/watch?v={video_id}",
            "vod_timestamp_sec": seconds,
            "original_url": value,
        }
    except (ValueError, TypeError):
        return None


def liquipedia_vod_entry(game, source):
    value = game.get("vod") or game.get("series_vod")
    parsed = parse_youtube_vod(value)
    if not parsed:
        return None
    per_game = bool(game.get("vod"))
    timestamp = parsed["vod_timestamp_sec"]
    return {
        "game_id": game["game_id"],
        **parsed,
        "status": "matched" if per_game else "needs_review",
        "source_id": source["source_id"],
        "channel_id": None,
        "layout_id": source["default_layout_id"],
        "source_kind": ("timestamped_game" if timestamp else "per_game")
        if per_game
        else "per_series",
        "provenance": "liquipedia_game_vod" if per_game else "liquipedia_series_vod",
        "liquipedia_match_id": game.get("liquipedia_match_id"),
        "liquipedia_game_id": game.get("liquipedia_game_id"),
        "match_confidence": 1.0 if per_game else 0.0,
        "availability": "unchecked",
        "scan_start_sec": max(0, timestamp - 720) if timestamp else 0,
        "scan_duration_sec": 1500,
        "reason": "Liquipedia game association; verify media and draft."
        if per_game
        else "Series VOD alone does not identify a game window.",
        "candidate_videos": [{"source": "liquipedia", **parsed}],
    }
