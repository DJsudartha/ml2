import json
import sys

from backend.services.liquipedia.match_finder import parse_and_normalize_matches
from backend.services.data.raw_games import load_raw_games
from backend.services.data.vod_sources import build_vod_manifest, load_vod_sources


def match_payload():
    extra = {"team1side": "blue", "team2side": "red"}
    for n, hero in enumerate(["Alpha", "Balmond", "Chou", "Diggie", "Eudora"], 1):
        extra[f"team1champion{n}"] = hero
    for n, hero in enumerate(["Fanny", "Gatotkaca", "Harith", "Irithel", "Johnson"], 1):
        extra[f"team2champion{n}"] = hero
    return {
        "result": [
            {
                "match2id": "stable-match",
                "tournament": "M7",
                "pagename": "M7_World_Championship/Knockout_Stage",
                "date": "2026-01-20 00:00:00",
                "vod": "https://www.youtube.com/watch?v=abcdefghijk",
                "match2opponents": [{"name": "A"}, {"name": "B"}],
                "match2games": [
                    {
                        "match2gameid": 2,
                        "winner": "1",
                        "extradata": extra,
                        "vod": "https://youtu.be/12345678901?t=1h2m3s",
                    }
                ],
            }
        ]
    }


def test_ingestion_preserves_game_vod_series_vod_and_source_identity(tmp_path):
    normalized = parse_and_normalize_matches(match_payload())
    (tmp_path / "M7.json").write_text(json.dumps(normalized))
    game = load_raw_games(tmp_path)[0]
    assert game["vod"] == "https://youtu.be/12345678901?t=1h2m3s"
    assert game["series_vod"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert game["liquipedia_match_id"] == "stable-match"
    assert game["liquipedia_game_id"] == 2
    assert game["blue_picks"][0] == "Alpha"


def test_manifest_uses_direct_game_vod_without_youtube_search(tmp_path):
    (tmp_path / "M7.json").write_text(
        json.dumps(parse_and_normalize_matches(match_payload()))
    )
    manifest = build_vod_manifest(
        games=load_raw_games(tmp_path), videos=[], source_registry=load_vod_sources()
    )
    entry = manifest["games"][0]
    assert entry["status"] == "matched"
    assert entry["video_id"] == "12345678901"
    assert entry["provenance"] == "liquipedia_game_vod"
    assert entry["vod_timestamp_sec"] == 3723
    assert (
        entry["channel_id"] is None
    )  # LP provenance does not prove uploader identity.


def test_headless_report_does_not_count_role_slots_as_pick_order(tmp_path):
    from backend.services.data.collection import collection_report

    (tmp_path / "M7.json").write_text(
        json.dumps(parse_and_normalize_matches(match_payload()))
    )
    games = load_raw_games(tmp_path)
    manifest = build_vod_manifest(
        games=games, videos=[], source_registry=load_vod_sources()
    )
    report = collection_report(games, manifest)
    assert report["retrieved_games"] == 1
    assert report["distinct_matches"] == 1
    assert report["complete_pick_orders"] == 0
    assert report["games"][0]["pick_order_status"] == "missing"


def test_unknown_winner_is_not_assigned_to_red():
    payload = match_payload()
    payload["result"][0]["match2games"][0]["winner"] = ""
    assert (
        parse_and_normalize_matches(payload)["series"][0]["games"][0]["winner"] is None
    )


def test_shared_game_url_is_not_silently_assigned_twice(tmp_path):
    payload = match_payload()
    payload["result"][0]["match2games"].append(
        {**payload["result"][0]["match2games"][0], "match2gameid": 3}
    )
    (tmp_path / "M7.json").write_text(json.dumps(parse_and_normalize_matches(payload)))
    manifest = build_vod_manifest(
        games=load_raw_games(tmp_path), videos=[], source_registry=load_vod_sources()
    )
    assert [e["status"] for e in manifest["games"]] == ["needs_review", "needs_review"]


def test_series_url_requires_game_location_and_host_allowlist(tmp_path):
    from backend.services.data.liquipedia_vods import parse_youtube_vod

    payload = match_payload()
    payload["result"][0]["match2games"][0]["vod"] = ""
    (tmp_path / "M7.json").write_text(json.dumps(parse_and_normalize_matches(payload)))
    entry = build_vod_manifest(
        games=load_raw_games(tmp_path), videos=[], source_registry=load_vod_sources()
    )["games"][0]
    assert entry["status"] == "needs_review"
    assert entry["provenance"] == "liquipedia_series_vod"
    for url in [
        "file:///secret",
        "https://youtube.com.evil/watch?v=abcdefghijk",
        "https://youtu.be/abc",
        "https://youtu.be/abcdefghijk?t=oops",
    ]:
        assert parse_youtube_vod(url) is None


def test_media_verification_rejects_wrong_uploader_and_preserves_lp_provenance():
    from backend.services.data.liquipedia_vods import verify_manifest_media

    manifest = {
        "games": [
            {
                "game_id": "g",
                "status": "matched",
                "video_id": "abcdefghijk",
                "vod_url": "https://youtu.be/abcdefghijk",
                "source_id": "official",
                "provenance": "liquipedia_game_vod",
            }
        ]
    }
    registry = {"sources": [{"source_id": "official", "channel_id": "approved"}]}
    result = verify_manifest_media(
        manifest,
        registry,
        lambda url: {"id": "abcdefghijk", "channel_id": "other", "duration": 1800},
    )
    assert result["games"][0]["status"] == "needs_review"
    assert result["games"][0]["provenance"] == "liquipedia_game_vod"
    assert manifest["games"][0]["status"] == "matched"


def test_headless_cli_reuses_snapshots_and_fails_incomplete_order_gate(
    tmp_path, monkeypatch
):
    import requests
    from backend.scripts.collect_pick_order_data import main

    def response(url, *, params, **kwargs):
        page = params["conditions"][12:-2]
        payload = match_payload()
        payload["result"][0]["pagename"] = page
        payload["result"][0]["match2id"] = page + "-match"
        if "MPL/" in page:
            payload["result"][0]["match2games"][0]["vod"] = (
                "https://youtu.be/ABCDEFGHIJK"
            )

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return Response()

    monkeypatch.setattr(requests, "get", response)
    monkeypatch.setenv("LIQUIPEDIA_API_KEY", "test-key-not-a-secret")
    argv = [
        "collect",
        "--tournament",
        "M7_World_Championship/Knockout_Stage",
        "--tournament",
        "MPL/Indonesia/Season_17/Regular_Season",
        "--matches-per-tournament",
        "1",
        "--output-dir",
        str(tmp_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["distinct_matches"] == 2
    assert len(report["tournaments"]) == 2

    def offline(*args, **kwargs):
        raise AssertionError("Cached runs must not contact LP")

    monkeypatch.setattr(requests, "get", offline)
    monkeypatch.setattr(sys, "argv", argv + ["--require-orders"])
    assert main() == 1
    assert (
        json.loads((tmp_path / "report.json").read_text())["complete_pick_orders"] == 0
    )
