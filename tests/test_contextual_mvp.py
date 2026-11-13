from copy import deepcopy
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.main import app
from backend.scripts import export_backend_mvp as exporter
from backend.services.modeling import contextual_mvp as mvp

GOLDEN = json.loads(Path(__file__).with_name('mvp_golden.json').read_text())['cases']
CLIENT = TestClient(app)


@pytest.fixture(autouse=True)
def clear_pool_cache():
    mvp.hero_pool.cache_clear()
    yield
    mvp.hero_pool.cache_clear()


@pytest.mark.parametrize('case', GOLDEN)
def test_all_batches_preserve_original_contextual_scores(case):
    result = mvp.recommend_picks(**case['request'])
    assert [r['hero'] for r in result['recommendations']] == case['ranked']
    assert result == mvp.recommend_picks(**case['request'])
    for item in result['recommendations']:
        assert item['score'] == pytest.approx(case['scores'][item['hero']]['score'], abs=1e-14)
        for key, value in case['scores'][item['hero']]['components'].items():
            assert item['score_components'][key] == pytest.approx(value, abs=1e-14)
    assert result['historical_eligibility'] == 'unknown'
    assert result['ban_knowledge'] == 'completed_phases_known'


@pytest.mark.parametrize('index', [1, 2, 5])
def test_second_pair_member_is_filter_only(index):
    request = deepcopy(GOLDEN[index]['request'])
    first = mvp.recommend_picks(**request)
    chosen = first['recommendations'][0]['hero']
    request[f"{request['team']}_picks"].append(chosen)
    second = mvp.recommend_picks(**request)
    assert second['batch_id'] == first['batch_id']
    assert second['remaining_selections'] == 1
    assert second['trained_second_pick_model'] is False
    assert chosen not in {r['hero'] for r in second['recommendations']}
    old = {r['hero']: r['score'] for r in first['recommendations']}
    assert all(r['score'] == old[r['hero']] for r in second['recommendations'] if r['hero'] in old)


def test_permuted_completed_pair_is_invariant():
    request = deepcopy(GOLDEN[3]['request'])
    first = mvp.recommend_picks(**request)
    request['red_picks'].reverse()
    request['blue_picks'].reverse()
    assert first == mvp.recommend_picks(**request)


def test_future_bans_rejected_at_r3_and_required_at_r4():
    r3 = deepcopy(GOLDEN[3]['request'])
    r3['blue_bans'].append('Atlas')
    with pytest.raises(ValueError, match='Future-phase'):
        mvp.recommend_picks(**r3)
    r4 = deepcopy(GOLDEN[4]['request'])
    r4['blue_bans'] = r4['blue_bans'][:3]
    r4['red_bans'] = r4['red_bans'][:3]
    with pytest.raises(ValueError, match='10 completed'):
        mvp.recommend_picks(**r4)


def test_strict_turn_and_unknown_ban_preview():
    with pytest.raises(ValueError, match='6 completed'):
        mvp.recommend_picks()
    request = deepcopy(GOLDEN[0]['request'])
    request['team'] = 'red'
    with pytest.raises(ValueError, match="blue's pick turn"):
        mvp.recommend_picks(**request)
    request.update(strict_turn=False, blue_bans=[], red_bans=[])
    result = mvp.recommend_picks(**request)
    assert result['team'] == 'red' and result['turn_override']
    assert result['ban_knowledge'] == 'unknown_or_incomplete'


def test_sparse_unseen_pool_returns_deterministic_catalog_fallback(tmp_path, monkeypatch):
    pool = sorted(set(mvp.hero_pool()) - set(mvp.load_snapshot()['statistics']['global']))[:8]
    path = tmp_path/'pool.json'
    path.write_text(json.dumps(pool))
    monkeypatch.setenv('ML2_SIMULATOR_HERO_POOL', str(path))
    mvp.hero_pool.cache_clear()
    result = mvp.recommend_picks(strict_turn=False, top_k=5)
    assert [r['hero'] for r in result['recommendations']] == pool[:5]
    assert all(r['score'] == 0 for r in result['recommendations'])
    assert result['fallback_count'] == 5
    assert result == mvp.recommend_picks(strict_turn=False, top_k=5)


def test_unavailable_and_disabled_excluded(monkeypatch):
    request = deepcopy(GOLDEN[3]['request'])
    initial = mvp.recommend_picks(**request)
    disabled = initial['recommendations'][0]['hero']
    monkeypatch.setenv('ML2_DISABLED_HEROES', disabled)
    mvp.hero_pool.cache_clear()
    result = mvp.recommend_picks(**request)
    excluded = {disabled, *request['blue_picks'], *request['red_picks'],
                *request['blue_bans'], *request['red_bans']}
    assert not excluded & {r['hero'] for r in result['recommendations']}


@pytest.mark.parametrize('bad', [
    {'blue_picks':['Miya'], 'red_picks':['Miya']},
    {'blue_picks':['Unknown hero']},
    {'blue_picks':['Miya'], 'blue_bans':['Miya']},
    {'red_picks':['Miya']},
])
def test_invalid_states_return_422(bad):
    response = CLIENT.post('/draft/recommend-picks', json={'team':'blue', 'strict_turn':False, **bad})
    assert response.status_code == 422


@pytest.mark.parametrize('endpoint,draft_request', [
    ('recommend-picks', GOLDEN[0]['request']),
    ('recommend-bans', {'team':'blue', 'top_k':5}),
    ('advise-picks', GOLDEN[0]['request']),
    ('advise-bans', {'team':'blue', 'top_k':5}),
])
def test_unmocked_endpoints(endpoint, draft_request, monkeypatch):
    monkeypatch.delenv('LOCAL_DRAFT_ADVISOR_BACKEND', raising=False)
    from backend.services.llm import pick_advisor, ban_advisor
    assert pick_advisor.DEFAULT_LOCAL_BACKEND == ban_advisor.DEFAULT_LOCAL_BACKEND == 'tfidf'
    response = CLIENT.post('/draft/'+endpoint, json=draft_request)
    assert response.status_code == 200, response.text
    body = response.json()
    result = body['recommendation'] if endpoint.startswith('advise') else body
    assert len(result['recommendations']) == 5
    assert all({'hero','rank','score','reasons','score_components'} <= set(r) for r in result['recommendations'])
    if endpoint.startswith('advise'):
        assert body['advisor']['uses_llm'] is False
        assert body['advisor']['provider'] == 'local-semantic'


def test_bans_use_only_current_phase_and_team():
    artifact = mvp.load_snapshot()
    first = mvp.recommend_bans(top_k=20)
    expected = artifact['ban_phase_counts']['1']['blue']
    for row in first['recommendations']:
        assert row['score'] == expected.get(row['hero'], 0) / sum(expected.values())
    request = deepcopy(GOLDEN[4]['request'])
    request['blue_bans'] = request['blue_bans'][:3]
    request['red_bans'] = request['red_bans'][:3]
    request['team'] = 'red'
    second = mvp.recommend_bans(**request)
    expected = artifact['ban_phase_counts']['2']['red']
    for row in second['recommendations']:
        assert row['score'] == expected.get(row['hero'], 0) / sum(expected.values())
    assert second['phase_index'] == 2


def test_ban_pick_boundaries_reject_wrong_action():
    with pytest.raises(ValueError, match='matching first or second'):
        mvp.recommend_bans(blue_picks=['Miya'])
    request = deepcopy(GOLDEN[1]['request'])
    request['team'] = 'red'
    with pytest.raises(ValueError, match='matching first or second'):
        mvp.recommend_bans(**request)


def test_artifact_tampering_fails_before_payload_decode(tmp_path, monkeypatch):
    path = tmp_path/'bad.json'
    path.write_text('{"protected":"must never decode"}')
    monkeypatch.setattr(mvp, 'ARTIFACT', path)
    mvp.load_snapshot.cache_clear()
    with pytest.raises(RuntimeError, match='integrity'):
        mvp.load_snapshot()
    mvp.load_snapshot.cache_clear()


def test_source_export_guard_rejects_mixed_or_malformed_before_decode(monkeypatch):
    def forbidden_decode(*args, **kwargs):
        pytest.fail('Unapproved source was decoded')
    monkeypatch.setattr(exporter.json, 'loads', forbidden_decode)
    for data in (b'{"game_id":"protected-holdout","payload":{}}', b'{', b'{}'):
        with pytest.raises(ValueError, match='pinned development-only'):
            exporter.approved_payload(data)


def test_record_allowlist_guard_precedes_decode(monkeypatch):
    # Synthetic bytes deliberately pass the outer hash, but carry an unknown record.
    data = b'{"game_id":"unauthorized","payload":{}}'
    monkeypatch.setattr(exporter, 'SOURCE_SHA256', hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(exporter.json, 'loads', lambda *args: pytest.fail('Record decoded'))
    with pytest.raises(ValueError, match='allowlist'):
        exporter.approved_payload(data)


def test_serving_artifact_has_no_raw_records_or_targets():
    artifact = mvp.load_snapshot()
    assert artifact['training_game_ids'] == list(exporter.ALLOWED_GAMES)
    assert artifact['counts']['examples'] == 119
    assert 'dataset' not in artifact and 'examples' not in artifact and 'target_heroes' not in artifact
    assert len(artifact['simulator_heroes']) >= 100


def test_complete_twenty_action_draft_progression():
    actions = [("ban", t) for t in ("blue", "red", "blue", "red", "blue", "red")]
    actions += [("pick", t) for t in ("blue", "red", "red", "blue", "blue", "red")]
    actions += [("ban", t) for t in ("red", "blue", "red", "blue")]
    actions += [("pick", t) for t in ("red", "blue", "blue", "red")]
    state = {"blue_picks": [], "red_picks": [], "blue_bans": [], "red_bans": []}
    seen_batches = []
    for action, team in actions:
        result = (mvp.recommend_picks if action == "pick" else mvp.recommend_bans)(
            team=team, **state, top_k=5)
        assert result["recommendations"]
        used = set(sum(state.values(), []))
        assert not used & {r["hero"] for r in result["recommendations"]}
        state[f"{team}_{action}s"].append(result["recommendations"][0]["hero"])
        if action == "pick" and result["batch_id"] not in seen_batches:
            seen_batches.append(result["batch_id"])
    assert seen_batches == list(mvp.BATCHES)
    with pytest.raises(ValueError, match="No remaining"):
        mvp.recommend_picks(team="red", **state)
    with pytest.raises(ValueError, match="No remaining"):
        mvp.recommend_bans(team="red", **state)


def test_empty_observed_statistics_falls_back(monkeypatch):
    snapshot = deepcopy(mvp.load_snapshot())
    snapshot["statistics"]["global"] = {}
    monkeypatch.setattr(mvp, "load_snapshot", lambda: snapshot)
    result = mvp.recommend_picks(strict_turn=False, top_k=5)
    assert result["fallback_count"] == 5
    assert [r["hero"] for r in result["recommendations"]] == list(mvp.hero_pool())[:5]
