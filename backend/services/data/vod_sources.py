from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
from typing import Any, Iterable

import httpx

from backend.services.common.file_utils import load_json
from backend.services.data.liquipedia_vods import liquipedia_vod_entry

VOD_SOURCES_PATH = Path("backend/data/vod_sources.json")
YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3"

SOURCE_KIND_PRIORITY = {
    "per_game": 3,
    "per_series": 2,
    "full_day": 1,
}

GAME_NUMBER_PATTERN = re.compile(r"\b(?:game|g)\s*[-#:]*\s*([1-9])\b", re.IGNORECASE)
VERSUS_PATTERN = re.compile(r"\b(?:vs\.?|versus)\b", re.IGNORECASE)
FULL_DAY_MARKERS = (
    "full day",
    "match day",
    "matchday",
    "day 1",
    "day 2",
    "day 3",
    "day 4",
    "day 5",
    "day 6",
    "day 7",
    "hari 1",
    "hari 2",
    "hari 3",
    "hari 4",
    "hari 5",
    "hari 6",
    "hari 7",
)


@dataclass(frozen=True)
class VideoRecord:
    video_id: str
    channel_id: str
    title: str
    description: str
    published_at: str
    duration_sec: int
    actual_start_time: str | None
    source_id: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _normalize_text(value: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", (value or "").casefold())
    return " ".join(text.split())


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_iso8601_duration(value: str | None) -> int:
    if not value:
        return 0
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?",
        value,
    )
    if not match:
        return 0
    parts = {key: int(raw or 0) for key, raw in match.groupdict().items()}
    return (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


def load_vod_sources(path: Path = VOD_SOURCES_PATH) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError(f"Expected VOD source registry version 1 at {path}")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"Expected at least one VOD source at {path}")

    channel_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Each VOD source must be an object")
        channel_id = str(source.get("channel_id", ""))
        if not channel_id.startswith("UC"):
            raise ValueError(f"VOD source has an invalid channel_id: {channel_id}")
        if channel_id in channel_ids:
            raise ValueError(f"Duplicate VOD source channel_id: {channel_id}")
        channel_ids.add(channel_id)
    return payload


def classify_video(video: VideoRecord) -> str:
    if video.duration_sec < 600 or re.search(
        r"\b(?:highlights?|shorts?|trailer|teaser|recap)\b", video.title, re.IGNORECASE
    ):
        return "excluded"
    text = _normalize_text(f"{video.title} {video.description[:500]}")
    has_game_number = GAME_NUMBER_PATTERN.search(text) is not None
    has_matchup = VERSUS_PATTERN.search(text) is not None

    if has_game_number and has_matchup and video.duration_sec <= 90 * 60:
        return "per_game"
    if (
        any(marker in text for marker in FULL_DAY_MARKERS)
        or video.duration_sec >= 5 * 3600
    ):
        return "full_day"
    if has_matchup or video.duration_sec <= 4 * 3600:
        return "per_series"
    return "full_day"


def _youtube_get(
    client: httpx.Client,
    endpoint: str,
    *,
    api_key: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = client.get(
            f"{YOUTUBE_API_BASE_URL}/{endpoint}",
            params={**params, "key": api_key},
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"YouTube Data API {endpoint} returned HTTP {exc.response.status_code}"
        ) from None
    except httpx.RequestError:
        raise RuntimeError(f"YouTube Data API {endpoint} request failed") from None
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected YouTube response for {endpoint}")
    return payload


def fetch_channel_uploads(
    *,
    api_key: str,
    source: dict[str, Any],
    published_after: datetime,
    client: httpx.Client | None = None,
    max_pages: int | None = None,
    playlist_id: str | None = None,
) -> list[VideoRecord]:
    if not api_key:
        raise ValueError("A YouTube Data API key is required")
    channel_id = str(source["channel_id"])
    owns_client = client is None
    api_client = client or httpx.Client(timeout=30.0)
    try:
        channel_payload = (
            _youtube_get(
                api_client,
                "channels",
                api_key=api_key,
                params={"part": "contentDetails", "id": channel_id},
            )
            if playlist_id is None
            else {}
        )
        channel_items = channel_payload.get("items", [])
        if playlist_id is None and not channel_items:
            raise ValueError(f"YouTube channel was not found: {channel_id}")
        uploads_id = (
            playlist_id
            or channel_items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        )

        video_ids: list[str] = []
        next_page_token: str | None = None
        cutoff_reached = False
        page_count = 0
        seen_tokens: set[str] = set()
        while True:
            page_count += 1
            params: dict[str, Any] = {
                "part": "snippet,contentDetails",
                "playlistId": uploads_id,
                "maxResults": 50,
            }
            if next_page_token:
                params["pageToken"] = next_page_token
            page = _youtube_get(
                api_client,
                "playlistItems",
                api_key=api_key,
                params=params,
            )
            for item in page.get("items", []):
                published_at = _parse_datetime(
                    item.get("contentDetails", {}).get("videoPublishedAt")
                    or item.get("snippet", {}).get("publishedAt")
                )
                if published_at and published_at < published_after.astimezone(
                    timezone.utc
                ):
                    cutoff_reached = True
                    continue
                video_id = item.get("contentDetails", {}).get("videoId")
                if video_id:
                    video_ids.append(str(video_id))
            next_page_token = page.get("nextPageToken")
            if (cutoff_reached and playlist_id is None) or not next_page_token:
                break
            if next_page_token in seen_tokens:
                raise RuntimeError("YouTube playlist pagination repeated a page token")
            seen_tokens.add(next_page_token)
            if max_pages is not None and page_count >= max_pages:
                raise RuntimeError(
                    "YouTube discovery page limit reached before the date cutoff"
                )

        records: list[VideoRecord] = []
        video_ids = list(dict.fromkeys(video_ids))
        for start in range(0, len(video_ids), 50):
            batch_ids = video_ids[start : start + 50]
            payload = _youtube_get(
                api_client,
                "videos",
                api_key=api_key,
                params={
                    "part": "snippet,contentDetails,liveStreamingDetails,status",
                    "id": ",".join(batch_ids),
                },
            )
            for item in payload.get("items", []):
                if item.get("status", {}).get("privacyStatus") != "public":
                    continue
                snippet = item.get("snippet", {})
                if snippet.get("channelId") != channel_id:
                    continue
                live_details = item.get("liveStreamingDetails", {})
                records.append(
                    VideoRecord(
                        video_id=str(item["id"]),
                        channel_id=str(snippet.get("channelId", channel_id)),
                        title=str(snippet.get("title", "")),
                        description=str(snippet.get("description", "")),
                        published_at=str(snippet.get("publishedAt", "")),
                        duration_sec=parse_iso8601_duration(
                            item.get("contentDetails", {}).get("duration")
                        ),
                        actual_start_time=live_details.get("actualStartTime"),
                        source_id=str(source["source_id"]),
                    )
                )
        return records
    finally:
        if owns_client:
            api_client.close()


def _game_number(video: VideoRecord) -> int | None:
    match = GAME_NUMBER_PATTERN.search(f"{video.title} {video.description[:500]}")
    return int(match.group(1)) if match else None


def _name_score(name: str | None, text: str, aliases: dict[str, list[str]]) -> float:
    if not name:
        return 0.0
    candidates = [name, *aliases.get(name, [])]
    normalized_text = _normalize_text(text)
    for candidate in candidates:
        normalized_candidate = _normalize_text(candidate)
        if normalized_candidate and re.search(
            rf"\b{re.escape(normalized_candidate)}\b",
            normalized_text,
        ):
            return 1.0
    return 0.0


def _tournament_score(game: dict[str, Any], source: dict[str, Any], text: str) -> float:
    normalized_text = _normalize_text(text)
    values = [
        game.get("tournament"),
        game.get("pagename"),
        game.get("source_file"),
        *source.get("tournament_patterns", []),
    ]
    for value in values:
        normalized_value = _normalize_text(str(value or ""))
        if normalized_value and normalized_value in normalized_text:
            return 1.0
    source_tokens = {
        token
        for value in source.get("tournament_patterns", [])
        for token in _normalize_text(str(value)).split()
        if len(token) >= 2
    }
    if source_tokens and source_tokens.intersection(normalized_text.split()):
        return 0.7
    return 0.0


def score_video_for_game(
    game: dict[str, Any],
    video: VideoRecord,
    source: dict[str, Any],
    aliases: dict[str, list[str]],
) -> dict[str, Any]:
    text = f"{video.title} {video.description[:1000]}"
    blue_score = _name_score(game.get("blue_team_name"), video.title, aliases)
    red_score = _name_score(game.get("red_team_name"), video.title, aliases)
    tournament_score = _tournament_score(game, source, text)
    source_kind = classify_video(video)
    expected_game_no = int(game.get("game_no") or 0)
    observed_game_no = _game_number(video)
    game_number_score = (
        1.0
        if expected_game_no and observed_game_no == expected_game_no
        else 0.0
        if observed_game_no is not None
        else 0.35
    )

    game_date = _parse_datetime(str(game.get("date") or ""))
    video_date = _parse_datetime(video.actual_start_time or video.published_at)
    if game_date and video_date:
        date_delta = abs((video_date - game_date).total_seconds())
        date_score = (
            1.0 if date_delta <= 36 * 3600 else 0.5 if date_delta <= 7 * 86400 else 0.0
        )
    else:
        date_score = 0.25

    weighted_score = (
        blue_score * 0.25
        + red_score * 0.25
        + tournament_score * 0.20
        + game_number_score * 0.20
        + date_score * 0.10
    )
    if source_kind == "per_game":
        weighted_score += 0.05
    rejection_reasons = []
    if source_kind == "excluded":
        rejection_reasons.append("short_clip_or_highlight")
    if blue_score < 0.8 or red_score < 0.8:
        rejection_reasons.append("team_names_not_confirmed")
    if tournament_score < 1.0:
        rejection_reasons.append("tournament_not_confirmed")
    if source_kind == "per_game" and observed_game_no != expected_game_no:
        rejection_reasons.append("game_number_mismatch")
    if game_date and video_date and date_score < 1.0:
        rejection_reasons.append("match_date_mismatch")
    stage_text = _normalize_text(
        str(game.get("source_file") or game.get("pagename") or "")
    )
    title_text = _normalize_text(video.title).replace("wild card", "wildcard")
    stages = ("swiss", "knockout", "wildcard")
    expected_stage = next((stage for stage in stages if stage in stage_text), None)
    observed_stage = next((stage for stage in stages if stage in title_text), None)
    if "grand finals" in title_text:
        observed_stage = "knockout"
    if expected_stage and observed_stage and expected_stage != observed_stage:
        rejection_reasons.append("stage_mismatch")
    return {
        **{key: value for key, value in asdict(video).items() if key != "description"},
        "vod_url": video.url,
        "source_kind": source_kind,
        "match_confidence": round(min(weighted_score, 1.0), 4),
        "rejection_reasons": rejection_reasons,
        "match_reasons": {
            "blue_team": round(blue_score, 4),
            "red_team": round(red_score, 4),
            "tournament": round(tournament_score, 4),
            "game_number": round(game_number_score, 4),
            "date": round(date_score, 4),
        },
    }


def _source_for_game(
    game: dict[str, Any],
    sources: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    game_text = _normalize_text(
        " ".join(
            str(value or "")
            for value in (
                game.get("tournament"),
                game.get("pagename"),
                game.get("source_file"),
            )
        )
    )
    matches: list[tuple[int, dict[str, Any]]] = []
    for source in sources:
        patterns = [
            _normalize_text(str(value))
            for value in source.get("tournament_patterns", [])
        ]
        overlap = max(
            (len(pattern) for pattern in patterns if pattern in game_text), default=0
        )
        if overlap:
            matches.append((overlap, source))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def build_vod_manifest(
    *,
    games: Iterable[dict[str, Any]],
    videos: Iterable[VideoRecord],
    source_registry: dict[str, Any],
    min_confidence: float = 0.75,
    min_lead: float = 0.10,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    sources = [
        source for source in source_registry["sources"] if isinstance(source, dict)
    ]
    aliases = {
        str(name): [str(alias) for alias in values]
        for name, values in source_registry.get("team_aliases", {}).items()
        if isinstance(values, list)
    }
    videos_by_source: dict[str, list[VideoRecord]] = {}
    for video in videos:
        videos_by_source.setdefault(video.source_id, []).append(video)

    manifest_games: list[dict[str, Any]] = []
    for game in sorted(games, key=lambda row: str(row.get("game_id", ""))):
        source = _source_for_game(game, sources)
        if source is None:
            continue

        direct = liquipedia_vod_entry(game, source)
        if direct is not None and direct['status'] == 'matched':
            manifest_games.append(direct)
            continue

        candidates = [
            score_video_for_game(game, video, source, aliases)
            for video in videos_by_source.get(str(source["source_id"]), [])
            if video.channel_id == source["channel_id"]
            and classify_video(video) != "excluded"
        ]
        candidates.sort(
            key=lambda item: (
                -SOURCE_KIND_PRIORITY.get(str(item["source_kind"]), 0),
                -float(item["match_confidence"]),
                str(item["video_id"]),
            )
        )
        eligible = [
            candidate
            for candidate in candidates
            if float(candidate["match_confidence"]) >= min_confidence
            and not candidate["rejection_reasons"]
        ]
        selected = eligible[0] if eligible else None
        ambiguous = bool(
            selected
            and len(eligible) > 1
            and eligible[0]["source_kind"] == eligible[1]["source_kind"]
            and float(eligible[0]["match_confidence"])
            - float(eligible[1]["match_confidence"])
            < min_lead
        )

        entry: dict[str, Any] = {
            "game_id": game["game_id"],
            "status": "ambiguous"
            if ambiguous
            else "matched"
            if selected
            else "missing_vod",
            "source_id": source["source_id"],
            "channel_id": source["channel_id"],
            "layout_id": source["default_layout_id"],
            "candidate_videos": candidates,
        }
        if selected and not ambiguous:
            video_start = _parse_datetime(selected.get("actual_start_time"))
            game_start = _parse_datetime(str(game.get("date") or ""))
            estimated_offset = (
                max(0.0, (game_start - video_start).total_seconds())
                if video_start and game_start
                else 0.0
            )
            if selected["source_kind"] == "per_game":
                scan_start_sec = 0.0
                scan_duration_sec = min(
                    float(selected.get("duration_sec") or 1500), 1500.0
                )
            elif selected["source_kind"] == "per_series":
                scan_start_sec = 0.0
                scan_duration_sec = float(selected.get("duration_sec") or 4 * 3600)
            else:
                scan_start_sec = max(0.0, estimated_offset - 45 * 60)
                scan_duration_sec = min(
                    105 * 60.0,
                    max(0.0, float(selected.get("duration_sec") or 0) - scan_start_sec),
                )
            entry.update(
                {
                    "vod_url": selected["vod_url"],
                    "video_id": selected["video_id"],
                    "source_kind": selected["source_kind"],
                    "match_confidence": selected["match_confidence"],
                    "scan_start_sec": round(scan_start_sec, 3),
                    "scan_duration_sec": round(scan_duration_sec, 3),
                }
            )
        elif ambiguous:
            entry["reason"] = (
                "Multiple official videos matched without a clear confidence lead."
            )
        else:
            entry["reason"] = "No official video met the matching threshold."
        manifest_games.append(direct if direct is not None and entry['status'] == 'missing_vod' else entry)

    # A shared untimestamped URL cannot identify independent game windows.
    direct_groups: dict[str, list[dict]] = {}
    for entry in manifest_games:
        if entry.get('provenance') == 'liquipedia_game_vod':
            direct_groups.setdefault(entry['video_id'], []).append(entry)
    for group in direct_groups.values():
        if len(group) > 1 and len({e['vod_timestamp_sec'] for e in group}) != len(group):
            for entry in group:
                entry['status'] = 'needs_review'
                entry['reason'] = 'The same VOD timestamp is assigned to several games.'

    created_at = generated_at or datetime.now(timezone.utc)
    return {
        "version": 1,
        "generated_at": created_at.isoformat(),
        "games": manifest_games,
    }


def discover_vod_manifest(*, games, source_registry, youtube_fallback=False, since_days=8,
                          min_confidence=0.75, min_lead=0.10):
    """LP-first discovery shared by manual and weekly CLI commands."""
    games = list(games)
    manifest = build_vod_manifest(games=games, videos=[], source_registry=source_registry)
    if youtube_fallback and any(e['status'] != 'matched' for e in manifest['games']):
        videos = discover_recent_videos(source_registry=source_registry, since_days=since_days)
        manifest = build_vod_manifest(games=games, videos=videos, source_registry=source_registry,
                                      min_confidence=min_confidence, min_lead=min_lead)
    return manifest


def discover_recent_videos(
    *,
    source_registry: dict[str, Any],
    since_days: int = 8,
    now: datetime | None = None,
    api_key: str | None = None,
) -> list[VideoRecord]:
    resolved_key = api_key or os.getenv("YOUTUBE_API_KEY", "")
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=since_days)
    records: list[VideoRecord] = []
    with httpx.Client(timeout=30.0) as client:
        for source in source_registry["sources"]:
            records.extend(
                fetch_channel_uploads(
                    api_key=resolved_key,
                    source=source,
                    published_after=cutoff,
                    client=client,
                )
            )
            for playlist_id in source.get("playlist_ids", []):
                records.extend(
                    fetch_channel_uploads(
                        api_key=resolved_key,
                        source=source,
                        published_after=cutoff,
                        client=client,
                        playlist_id=playlist_id,
                    )
                )
    return list(
        {(record.source_id, record.video_id): record for record in records}.values()
    )
