from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from backend.services.data.vod_layouts import (
    detect_active_video_bounds,
    load_layout,
    slot_bounds_for_frame,
)
from backend.services.data.vod_pick_order_suggestions import (
    first_team_pool_events,
    stable_hero_events,
    suggest_pick_order_from_hero_events,
)
from backend.services.data.vod_pipeline import JobLedger, job_key, run_vod_pipeline
from backend.services.data.vod_sources import (
    VideoRecord,
    build_vod_manifest,
    classify_video,
    fetch_channel_uploads,
    load_vod_sources,
    parse_iso8601_duration,
)
from backend.services.vod_downloader import (
    download_vod_section,
    extract_remote_section_frames,
)
from backend.scripts.add_hero_skin_reference import add_reference


def _video(
    video_id: str,
    title: str,
    *,
    duration_sec: int = 2400,
    source_id: str = "mlbb_esports",
    channel_id: str = "UCMncR-XXNXhMyJELEgCrHlg",
) -> VideoRecord:
    return VideoRecord(
        video_id=video_id,
        channel_id=channel_id,
        title=title,
        description="M7 World Championship",
        published_at="2026-01-18T07:00:00Z",
        duration_sec=duration_sec,
        actual_start_time=None,
        source_id=source_id,
    )


def _game() -> dict:
    return {
        "game_id": "sample::series1::game4::4",
        "tournament": "M7 World Championship",
        "pagename": "M7_World_Championship",
        "source_file": "M7_World_Championship_games.json",
        "date": "2026-01-18 07:00:00",
        "blue_team_name": "Aurora Gaming PH",
        "red_team_name": "Alter Ego",
        "game_no": 4,
    }


def test_parse_duration_and_classify_source_kinds():
    assert parse_iso8601_duration("PT1H2M3S") == 3723
    assert classify_video(_video("game", "RORA vs AE | Game 4")) == "per_game"
    assert (
        classify_video(_video("series", "RORA vs AE", duration_sec=7200))
        == "per_series"
    )
    assert (
        classify_video(_video("day", "M7 Day 5", duration_sec=7 * 3600)) == "full_day"
    )


def test_discovery_reaches_page_eleven():
    pages = []

    def handler(request):
        if request.url.path.endswith("/channels"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"contentDetails": {"relatedPlaylists": {"uploads": "uploads"}}}
                    ]
                },
            )
        if request.url.path.endswith("/playlistItems"):
            page = int(request.url.params.get("pageToken", "1"))
            pages.append(page)
            payload = {"items": []}
            if page < 11:
                payload["nextPageToken"] = str(page + 1)
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"items": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch_channel_uploads(
            api_key="test",
            source={"channel_id": "UC-test", "source_id": "test"},
            published_after=datetime(2025, 1, 1, tzinfo=timezone.utc),
            client=client,
        )
    assert pages == list(range(1, 12))


@pytest.mark.parametrize(
    "title,duration",
    [("M7 RORA vs AE Game 4", 35), ("M7 RORA vs AE Game 4 Highlights", 900)],
)
def test_matching_excludes_clips(title, duration):
    entry = build_vod_manifest(
        games=[_game()],
        videos=[_video("clip", title, duration_sec=duration)],
        source_registry=load_vod_sources(),
    )["games"][0]
    assert entry["status"] == "missing_vod"


def test_wrong_game_number_cannot_match():
    entry = build_vod_manifest(
        games=[_game()],
        videos=[_video("wrong", "M7 RORA vs AE Game 3")],
        source_registry=load_vod_sources(),
    )["games"][0]
    assert entry["status"] == "missing_vod"


def test_explicit_playlist_scans_past_old_entries_and_rejects_unofficial_uploads():
    pages = []

    def handler(request):
        assert not request.url.path.endswith("/channels")
        if request.url.path.endswith("/playlistItems"):
            page = request.url.params.get("pageToken", "first")
            pages.append(page)
            if page == "first":
                return httpx.Response(
                    200,
                    json={
                        "items": [
                            {
                                "contentDetails": {
                                    "videoId": "old",
                                    "videoPublishedAt": "2024-01-01T00:00:00Z",
                                }
                            }
                        ],
                        "nextPageToken": "second",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "contentDetails": {
                                "videoId": vid,
                                "videoPublishedAt": "2026-01-18T00:00:00Z",
                            }
                        }
                        for vid in ["official", "unofficial"]
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": vid,
                        "snippet": {"channelId": channel},
                        "status": {"privacyStatus": "public"},
                    }
                    for vid, channel in [
                        ("official", "UC-test"),
                        ("unofficial", "UC-other"),
                    ]
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = fetch_channel_uploads(
            api_key="test",
            source={"channel_id": "UC-test", "source_id": "test"},
            published_after=datetime(2025, 1, 1, tzinfo=timezone.utc),
            client=client,
            playlist_id="example",
        )
    assert pages == ["first", "second"]
    assert [record.video_id for record in records] == ["official"]


def test_manifest_omits_unsupported_tournaments():
    game = {
        **_game(),
        "tournament": "Other",
        "pagename": "Other",
        "source_file": "Other.json",
    }
    assert (
        build_vod_manifest(games=[game], videos=[], source_registry=load_vod_sources())[
            "games"
        ]
        == []
    )


def test_aurora_rosters_are_distinct():
    game = {**_game(), "blue_team_name": "Aurora Gaming"}
    entry = build_vod_manifest(
        games=[game],
        videos=[_video("ph", "M7 RORA vs AE Game 4")],
        source_registry=load_vod_sources(),
    )["games"][0]
    assert entry["status"] == "missing_vod"


def test_repeat_matchup_on_different_date_does_not_match():
    game = {**_game(), "date": "2026-01-25 07:00:00"}
    entry = build_vod_manifest(
        games=[game],
        videos=[_video("old", "M7 RORA vs AE Game 4")],
        source_registry=load_vod_sources(),
    )["games"][0]
    assert entry["status"] == "missing_vod"


def test_wrong_stage_cannot_match():
    game = {**_game(), "source_file": "M7_World_Championship_Swiss_Stage_games.json"}
    entry = build_vod_manifest(
        games=[game],
        videos=[_video("wrong", "M7 Knockout RORA vs AE Game 4")],
        source_registry=load_vod_sources(),
    )["games"][0]
    assert entry["status"] == "missing_vod"


def test_manifest_prefers_unique_official_per_game_video():
    registry = load_vod_sources()
    videos = [
        _video("full-day", "M7 Day 5", duration_sec=7 * 3600),
        _video("game-four", "[EN] M7 Grand Finals RORA vs AE Game 4"),
    ]

    manifest = build_vod_manifest(
        games=[_game()],
        videos=videos,
        source_registry=registry,
        generated_at=datetime(2026, 1, 19, tzinfo=timezone.utc),
    )

    entry = manifest["games"][0]
    assert entry["status"] == "matched"
    assert entry["video_id"] == "game-four"
    assert entry["source_kind"] == "per_game"
    assert entry["channel_id"] == "UCMncR-XXNXhMyJELEgCrHlg"
    assert len(entry["candidate_videos"]) == 2


def test_manifest_rejects_ambiguous_same_priority_matches():
    registry = load_vod_sources()
    videos = [
        _video("language-one", "M7 RORA vs AE Game 4"),
        _video("language-two", "M7 RORA vs AE Game 4"),
    ]

    manifest = build_vod_manifest(
        games=[_game()],
        videos=videos,
        source_registry=registry,
        generated_at=datetime(2026, 1, 19, tzinfo=timezone.utc),
    )

    assert manifest["games"][0]["status"] == "ambiguous"
    assert "vod_url" not in manifest["games"][0]


def test_youtube_discovery_uses_uploads_playlist_and_public_videos():
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/channels"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"contentDetails": {"relatedPlaylists": {"uploads": "UPLOADS"}}}
                    ]
                },
            )
        if request.url.path.endswith("/playlistItems"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "snippet": {"publishedAt": "2026-01-18T07:00:00Z"},
                            "contentDetails": {
                                "videoId": "video-1",
                                "videoPublishedAt": "2026-01-18T07:00:00Z",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "video-1",
                        "snippet": {
                            "channelId": "UC-official",
                            "title": "Blue vs Red Game 1",
                            "description": "Cup",
                            "publishedAt": "2026-01-18T07:00:00Z",
                        },
                        "contentDetails": {"duration": "PT31M"},
                        "liveStreamingDetails": {
                            "actualStartTime": "2026-01-18T06:55:00Z"
                        },
                        "status": {"privacyStatus": "public"},
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        records = fetch_channel_uploads(
            api_key="test-key",
            source={"channel_id": "UC-official", "source_id": "official"},
            published_after=datetime(2026, 1, 1, tzinfo=timezone.utc),
            client=client,
        )

    assert [record.video_id for record in records] == ["video-1"]
    assert records[0].duration_sec == 1860
    assert requested_paths == [
        "/youtube/v3/channels",
        "/youtube/v3/playlistItems",
        "/youtube/v3/videos",
    ]


def test_normalized_slots_scale_inside_letterboxed_video():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[60:660, :] = 255
    assert detect_active_video_bounds(frame) == (0, 60, 1280, 600)

    layout = {
        "coordinate_space": "normalized",
        "slots": {"blue_pick1": [0.1, 0.2, 0.3, 0.4]},
    }
    assert slot_bounds_for_frame(
        layout,
        frame.shape,
        active_bounds=(0, 60, 1280, 600),
    )["blue_pick1"] == (128, 180, 384, 240)


def test_uncalibrated_layout_is_rejected(tmp_path: Path):
    layouts_path = tmp_path / "layouts.json"
    layouts_path.write_text(
        json.dumps(
            {
                "version": 1,
                "layouts": {
                    "mpl_id_v1": {
                        "status": "needs_calibration",
                        "coordinate_space": "normalized",
                        "slots": {},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="needs_calibration"):
        load_layout("mpl_id_v1", layouts_path)


def test_temporal_events_ignore_later_slot_swap():
    observations = []
    for second, slot in enumerate(
        ["blue_pick1"] * 3 + ["blue_pick2"] * 3,
        start=1,
    ):
        observations.append(
            {
                "slot": slot,
                "team": "blue",
                "hero": "Alpha",
                "confidence": 0.95,
                "observed_at_sec": float(second),
                "frame_path": f"frame_{second}.jpg",
            }
        )

    stable = stable_hero_events(observations)
    pool = first_team_pool_events(stable)

    assert len(stable) == 2
    assert len(pool) == 1
    assert pool[0]["hero"] == "Alpha"


def test_hero_event_suggestions_remain_review_only():
    raw_game = {
        "game_id": "sample",
        "blue_picks": ["Alpha"],
        "red_picks": ["Edith"],
    }
    suggestion = suggest_pick_order_from_hero_events(
        raw_game,
        [
            {
                "slot": "blue_pick1",
                "team": "blue",
                "hero": "Alpha",
                "confidence": 0.9,
                "observed_at_sec": 10.0,
                "evidence_frames": ["frame.jpg"],
            },
            {
                "slot": "red_pick1",
                "team": "red",
                "hero": "Edith",
                "confidence": 0.8,
                "observed_at_sec": 20.0,
                "evidence_frames": ["frame2.jpg"],
            },
        ],
        provenance={"video_id": "official-video"},
    )

    assert suggestion["status"] == "needs_review"
    assert suggestion["provenance"]["extractor_version"]
    assert suggestion["picks"][0]["slot"] == "blue_pick1"


def test_same_interval_team_picks_are_marked_ambiguous():
    raw_game = {
        "game_id": "sample",
        "blue_picks": ["Alpha"],
        "red_picks": ["Edith", "Fanny"],
    }
    suggestion = suggest_pick_order_from_hero_events(
        raw_game,
        [
            {
                "slot": "blue_pick1",
                "team": "blue",
                "hero": "Alpha",
                "confidence": 0.9,
                "observed_at_sec": 10.0,
            },
            {
                "slot": "red_pick1",
                "team": "red",
                "hero": "Edith",
                "confidence": 0.9,
                "observed_at_sec": 20.0,
            },
            {
                "slot": "red_pick2",
                "team": "red",
                "hero": "Fanny",
                "confidence": 0.9,
                "observed_at_sec": 20.0,
            },
        ],
    )

    red_picks = [pick for pick in suggestion["picks"] if pick["team"] == "red"]
    assert len(red_picks) == 2
    assert all(
        pick["ambiguity_reason"] == "same_sampling_interval_as_adjacent_team_pick"
        for pick in red_picks
    )
    assert all(pick["confidence"] == 0.49 for pick in red_picks)


def test_job_ledger_and_key_are_idempotent(tmp_path: Path):
    layout = {"layout_id": "m7_world_v1", "version": 1}
    entry = {
        "game_id": "game",
        "video_id": "video",
        "vod_url": "https://example.test/video",
        "scan_start_sec": 0,
        "scan_duration_sec": 720,
    }
    first_key = job_key(entry, layout)
    assert first_key == job_key(dict(entry), dict(layout))

    ledger = JobLedger(tmp_path / "jobs.sqlite3")
    output = tmp_path / "result.json"
    output.write_text("{}", encoding="utf-8")
    ledger.start(first_key, "game")
    ledger.complete(first_key, output)
    assert ledger.completed_output(first_key) == output


def test_pipeline_reports_missing_vod_without_media_access(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "games": [
                    {
                        "game_id": "missing-game",
                        "status": "missing_vod",
                        "reason": "No official upload",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    annotations = tmp_path / "annotations.json"
    annotations.write_text('{"version": 1, "games": []}', encoding="utf-8")
    review_path = tmp_path / "review.json"

    report = run_vod_pipeline(
        manifest_path=manifest,
        raw_dir=tmp_path / "raw",
        annotations_path=annotations,
        results_dir=tmp_path / "results",
        review_path=review_path,
        ledger_path=tmp_path / "jobs.sqlite3",
    )

    assert report["counts"] == {"missing_vod": 1}
    assert json.loads(review_path.read_text(encoding="utf-8"))["games"] == []


def test_section_download_rejects_more_than_twelve_minutes(tmp_path: Path):
    with pytest.raises(ValueError, match="12 minutes"):
        download_vod_section(
            "https://example.test/video",
            tmp_path,
            start_sec=0,
            duration_sec=721,
            max_height=360,
        )


def test_remote_section_removes_temporary_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import backend.services.vod_downloader as downloader

    segment_parents: list[Path] = []

    def fake_download(url, output_dir, **_kwargs):
        del url
        segment = output_dir / "segment.mp4"
        segment.write_bytes(b"temporary media")
        segment_parents.append(segment.parent)
        return segment

    def fake_extract(video_path, output_dir, **_kwargs):
        del video_path, output_dir
        return []

    monkeypatch.setattr(downloader, "RUNTIME_SEGMENT_DIR", tmp_path / "runtime")
    monkeypatch.setattr(downloader, "download_vod_section", fake_download)
    monkeypatch.setattr(downloader, "extract_frames_from_file", fake_extract)

    assert (
        extract_remote_section_frames(
            url="https://example.test/video",
            output_dir=tmp_path / "frames",
            start_sec=10,
            duration_sec=60,
            fps=1,
            max_height=360,
            prefer_direct_stream=False,
        )
        == []
    )
    assert segment_parents and not segment_parents[0].exists()


def test_confirmed_skin_reference_is_content_addressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import backend.scripts.add_hero_skin_reference as reference_script

    icon_dir = tmp_path / "icons"
    icon_dir.mkdir()
    (icon_dir / "Alpha.png").write_bytes(b"canonical")
    crop = tmp_path / "review.jpg"
    crop.write_bytes(b"reviewed crop")
    monkeypatch.setattr(reference_script, "HERO_ICON_DIR", icon_dir)

    first = add_reference("Alpha", crop, tmp_path / "gallery")
    second = add_reference("Alpha", crop, tmp_path / "gallery")

    assert first == second
    assert first.read_bytes() == b"reviewed crop"


def test_confirmed_skin_reference_accepts_hero_present_in_liquipedia_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import backend.scripts.add_hero_skin_reference as reference_script

    icon_dir = tmp_path / "icons"
    icon_dir.mkdir()
    crop = tmp_path / "review.jpg"
    crop.write_bytes(b"reviewed new-hero crop")
    monkeypatch.setattr(reference_script, "HERO_ICON_DIR", icon_dir)

    destination = add_reference(
        "Hirara",
        crop,
        tmp_path / "gallery",
        known_heroes={"Hirara"},
    )

    assert destination.parent.name == "Hirara"
    assert destination.read_bytes() == b"reviewed new-hero crop"
