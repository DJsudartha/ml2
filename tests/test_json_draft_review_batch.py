"""Synthetic contract tests for the candidate-only JSON review batch."""

from copy import deepcopy
import json

import pytest

from backend.scripts import build_json_draft_review_batch as subject


def synthetic_case():
    blue = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"]
    red = ["Foxtrot", "Golf", "Hotel", "India", "Juliet"]
    targets = [["Alpha"], ["Foxtrot", "Golf"], ["Bravo", "Charlie"],
               ["Hotel"], ["India"], ["Delta", "Echo"], ["Juliet"]]
    batches = []
    for name, heroes in zip(subject.BATCHES, targets):
        batches.append({"batch": name, "heroes": heroes,
                        "status": "post_roster_reorder_candidate" if name == "R5"
                        else "roster_carryover_candidate",
                        "evidence": [{"timestamp_sec": 1}], "set_elimination_inferences": [],
                        "unresolved_commitment": "Human inspection pending.",
                        "proposal_sha256": subject.sha_json({"batch": name, "heroes": sorted(heroes)})})
    bans = {
        side: [{"hero": f"Ban{start + i}", "source_position": i + 1,
                "phase": "before_B1" if i < 3 else "between_R3_R4"}
               for i in range(5)]
        for side, start in (("blue", 0), ("red", 5))
    }
    identity = next(iter(subject.TARGETS))
    case = {"match_id": identity[0], "game_no": identity[1],
            "game_id": subject.TARGETS[identity],
            "review_status": "unreviewed_proposal", "independent_verification": False,
            "known_heroes": {"blue": blue, "red": red},
            "batches": batches, "phase_aware_bans": bans,
            "complete_candidate": True, "supported_batches": 7,
            "uncertainty": ["Review pending."]}
    subject.validate_case(case)
    case["proposal_sha256"] = subject.sha_json(case)
    return case


def completed_response(case):
    response = subject.make_review_template([case])
    response.update({"reviewer_id": "human", "source_inspection_attested": True,
                     "review_started_at": "2026-10-31T01:00:00+08:00",
                     "review_completed_at": "2026-10-31T02:00:00+08:00"})
    for decision in response["cases"][0]["batches"]:
        decision.update({"decision": "Confirm", "source_inspected": True,
                         "source_note": "Inspected original source.",
                         "reviewed_at": "2026-10-31T01:30:00+08:00"})
    return response


def test_excludes_protected_before_payload_decode(monkeypatch):
    identity = next(iter(subject.TARGETS))
    source = json.dumps({"games": [
        {"match_id": "MPLIDS18W1_0007", "game_no": 1, "game_id": "protected", "secret": "DO_NOT_DECODE"},
        {"match_id": identity[0], "game_no": identity[1], "game_id": subject.TARGETS[identity]},
    ]}).encode()
    original = json.loads

    def guarded(value, *args, **kwargs):
        assert "DO_NOT_DECODE" not in value
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", guarded)
    result = subject.selected_objects(source, "games", {"excluded_matches": ["MPLIDS18W1_0007"]},
                                      {identity: subject.TARGETS[identity]})
    assert list(result) == [identity]


def test_finalized_raw_series_is_not_decoded(monkeypatch):
    raw = json.dumps({"series": [
        {"games": [{"secret": "FINALIZED_DO_NOT_DECODE"}]},
        {"games": [{"liquipedia_match_id": "selected"}]},
    ]}).encode()
    scanner, fields = subject.spans(raw)
    series = scanner.array(fields["series"])
    original = json.loads

    def guarded(value, *args, **kwargs):
        assert "FINALIZED_DO_NOT_DECODE" not in value
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", guarded)
    assert subject.source_game_at(scanner, series, 1, 0)["liquipedia_match_id"] == "selected"


def test_discovery_is_identity_only_and_excludes_known_matches(monkeypatch):
    source = json.dumps([
        {"liquipedia_match_id": "MPLIDS18W1_0007", "game_id": "hidden", "secret": "HOLDOUT"},
        {"liquipedia_match_id": "MPLIDS18W3_0002", "game_id": "known-missing"},
        {"liquipedia_match_id": "MPLIDS18W7_0099",
         "game_id": "MPL_Indonesia_Season_18_Regular_Season_games.json::series99::game1::1",
         "secret": "UNPROCESSED_PAYLOAD"},
    ]).encode()
    original = json.loads

    def guarded(value, *args, **kwargs):
        assert "HOLDOUT" not in value and "UNPROCESSED_PAYLOAD" not in value
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", guarded)
    assert subject.discover_unprocessed_matches(source, {"excluded_matches": ["MPLIDS18W1_0007"]}) == [
        "MPLIDS18W7_0099"]


def test_duplicate_source_identity_rejected():
    identity = next(iter(subject.TARGETS))
    row = {"match_id": identity[0], "game_no": identity[1], "game_id": subject.TARGETS[identity]}
    with pytest.raises(subject.ReviewBatchError, match="duplicate"):
        subject.selected_objects(json.dumps({"games": [row, row]}).encode(), "games",
                                 {"excluded_matches": []}, {identity: subject.TARGETS[identity]})


def test_tampered_pinned_input_rejected(tmp_path):
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    original = subject.sha_bytes(source.read_bytes())
    source.write_text("{ }", encoding="utf-8")
    with pytest.raises(subject.ReviewBatchError, match="hash mismatch"):
        subject.pinned(source, original)


def test_tampered_visual_evidence_rejected(tmp_path):
    asset = tmp_path / "asset.jpg"
    asset.write_bytes(b"source crop")
    ref = {"frame": "asset.jpg", "sha256": subject.sha_bytes(asset.read_bytes())}
    assert subject.validate_evidence(tmp_path, [{"evidence": [ref]}]) == (1, 1)
    asset.write_bytes(b"changed crop")
    with pytest.raises(subject.ReviewBatchError, match="evidence hash"):
        subject.validate_evidence(tmp_path, [{"evidence": [ref]}])


def test_pinned_source_read_is_immutable(tmp_path):
    source = tmp_path / "source.json"
    source.write_bytes(b"{\"safe\":true}")
    original = source.read_bytes()
    assert subject.pinned(source, subject.sha_bytes(original)) == original
    assert source.read_bytes() == original


def test_unordered_pairs_are_valid_but_batch_order_is_not():
    case = synthetic_case()
    case["batches"][1]["heroes"].reverse()
    subject.validate_case(case)
    case["batches"][3], case["batches"][4] = case["batches"][4], case["batches"][3]
    with pytest.raises(subject.ReviewBatchError, match="batch identity"):
        subject.validate_case(case)


def test_missing_or_duplicate_hero_and_wrong_team_rejected():
    for replacement in ("Alpha", "Foxtrot"):
        case = synthetic_case()
        case["batches"][3]["heroes"] = [replacement]
        with pytest.raises(subject.ReviewBatchError):
            subject.validate_case(case)
    case = synthetic_case()
    case["batches"].pop()
    with pytest.raises(subject.ReviewBatchError, match="missing batch"):
        subject.validate_case(case)


def test_causal_ban_phases_and_duplicate_ban_rejected():
    case = synthetic_case()
    case["phase_aware_bans"]["red"][3]["phase"] = "before_B1"
    with pytest.raises(subject.ReviewBatchError, match="future ban"):
        subject.validate_case(case)
    case = synthetic_case()
    case["phase_aware_bans"]["red"][3]["hero"] = "Ban0"
    with pytest.raises(subject.ReviewBatchError, match="invalid ban"):
        subject.validate_case(case)


def test_uncertainty_cannot_be_dropped():
    case = synthetic_case()
    case["uncertainty"] = []
    with pytest.raises(subject.ReviewBatchError, match="uncertainty"):
        subject.validate_case(case)


def test_template_is_unconfirmed_and_hash_bound():
    case = synthetic_case()
    response = subject.make_review_template([case])
    subject.validate_review_response({"cases": [case]}, response, complete=False)
    with pytest.raises(subject.ReviewBatchError, match="human identity"):
        subject.validate_review_response({"cases": [case]}, response, complete=True)
    response["cases"][0]["proposal_sha256"] = "bad"
    with pytest.raises(subject.ReviewBatchError, match="proposal hash"):
        subject.validate_review_response({"cases": [case]}, response, complete=False)


def test_conflicting_correction_and_unordered_noop_rejected():
    case = synthetic_case()
    response = completed_response(case)
    decisions = response["cases"][0]["batches"]
    decisions[1]["decision"] = "Correct"
    decisions[1]["corrected_heroes"] = ["Golf", "Foxtrot"]
    with pytest.raises(subject.ReviewBatchError, match="invalid correction"):
        subject.validate_review_response({"cases": [case]}, response, complete=True)
    decisions[1]["corrected_heroes"] = ["Hotel", "Golf"]
    with pytest.raises(subject.ReviewBatchError, match="conflicting corrected"):
        subject.validate_review_response({"cases": [case]}, response, complete=True)


def test_uncertain_review_is_reported_not_auto_confirmed():
    case = synthetic_case()
    response = completed_response(case)
    response["cases"][0]["batches"][6]["decision"] = "Uncertain"
    subject.validate_review_response({"cases": [case]}, response, complete=True)


def test_overwrite_refusal(tmp_path):
    output = tmp_path / "review.json"
    first = {"cases": []}
    assert subject.write_output(output, first) == subject.sha_bytes(output.read_bytes())
    before = output.read_bytes()
    with pytest.raises(subject.ReviewBatchError, match="overwrite"):
        subject.write_output(output, deepcopy(first))
    assert output.read_bytes() == before


def compact_response(case):
    response = completed_response(case)
    reviewed = response['cases'][0]
    decisions = reviewed.pop('batches')
    reviewed['confirm_all_batches'] = {
        'decision': 'Confirm', 'all_seven_batches_checked': True,
        'batch_proposal_sha256': {b['batch']: b['proposal_sha256'] for b in decisions},
        'source_inspected': True, 'source_note': 'Checked all seven source phase displays.',
        'reviewed_at': '2026-10-31T01:30:00+08:00',
    }
    return response


@pytest.mark.parametrize('raw', [
    '{"decision":"Uncertain","decision":"Confirm"}',
    '{"cases":[{"batches":[{"decision":"Uncertain","decision":"Confirm"}]}]}',
    '{"cases":[{"confirm_all_batches":{"all_seven_batches_checked":false,"all_seven_batches_checked":true}}]}',
])
def test_duplicate_response_keys_rejected_recursively(raw):
    with pytest.raises(subject.ReviewBatchError, match='duplicate JSON key'):
        subject.parse_review_response(raw.encode())


def test_one_reviewed_game_of_six_and_batch_subsets_remain_unreviewed():
    cases = []
    for identity, gid in subject.TARGETS.items():
        case = synthetic_case()
        case.pop('proposal_sha256', None)
        case.update(match_id=identity[0], game_no=identity[1], game_id=gid)
        case['proposal_sha256'] = subject.sha_json(case)
        cases.append(case)
    response = completed_response(cases[3])
    result = subject.validate_review_response({'cases': cases}, response, complete=True)
    assert result == {'confirm': 7, 'correct': 0, 'uncertain': 0, 'unreviewed': 35}
    response['cases'][0]['batches'] = response['cases'][0]['batches'][3:5]
    result = subject.validate_review_response({'cases': cases}, response, complete=True)
    assert result == {'confirm': 2, 'correct': 0, 'uncertain': 0, 'unreviewed': 40}


def test_attested_compact_confirmation_expands_without_mutating_response():
    case = synthetic_case()
    response = compact_response(case)
    before = deepcopy(response)
    result = subject.validate_review_response({'cases': [case]}, response, complete=True)
    assert result == {'confirm': 7, 'correct': 0, 'uncertain': 0, 'unreviewed': 0}
    assert response == before


@pytest.mark.parametrize('field,value', [
    ('all_seven_batches_checked', False), ('all_seven_batches_checked', None),
    ('source_inspected', False), ('source_note', ' '), ('decision', 'Uncertain'),
    ('batch_proposal_sha256', {}),
])
def test_compact_confirmation_requires_complete_attestation(field, value):
    case = synthetic_case()
    response = compact_response(case)
    if value is None:
        response['cases'][0]['confirm_all_batches'].pop(field)
    else:
        response['cases'][0]['confirm_all_batches'][field] = value
    with pytest.raises(subject.ReviewBatchError):
        subject.validate_review_response({'cases': [case]}, response, complete=True)


def test_compact_cannot_mix_with_batches_or_corrections():
    case = synthetic_case()
    for field, value in [('batches', []), ('corrected_heroes', ['Alpha'])]:
        response = compact_response(case)
        if field == 'batches':
            response['cases'][0][field] = value
        else:
            response['cases'][0]['confirm_all_batches'][field] = value
        with pytest.raises(subject.ReviewBatchError):
            subject.validate_review_response({'cases': [case]}, response, complete=True)


@pytest.mark.parametrize('field,value', [
    ('review_started_at', '2026-10-31T03:00:00+08:00'),
    ('review_started_at', '2026-10-31T01:00:00'),
    ('review_completed_at', '2026-10-31T00:59:00+08:00'),
    ('reviewed_at', '2026-10-31T00:59:00+08:00'),
    ('reviewed_at', '2026-10-31T02:01:00+08:00'),
    ('reviewed_at', '2026-10-31T01:30:00'),
])
def test_review_timestamps_must_be_aware_and_within_ordered_interval(field, value):
    case = synthetic_case()
    response = completed_response(case)
    if field == 'reviewed_at':
        response['cases'][0]['batches'][0][field] = value
    else:
        response[field] = value
    with pytest.raises(subject.ReviewBatchError):
        subject.validate_review_response({'cases': [case]}, response, complete=True)


def test_review_timestamp_offsets_compare_instants_inclusively():
    case = synthetic_case()
    response = completed_response(case)
    response['review_completed_at'] = '2026-10-30T17:00:00Z'
    for decision in response['cases'][0]['batches']:
        decision['reviewed_at'] = '2026-10-31T02:00:00+09:00'
    subject.validate_review_response({'cases': [case]}, response, complete=True)


@pytest.mark.parametrize('change', ['duplicate_case', 'unknown_case', 'duplicate_batch', 'unknown_batch',
                                    'case_hash', 'batch_hash', 'identity', 'empty_cases', 'empty_batches'])
def test_subset_identity_and_hash_guards(change):
    case = synthetic_case()
    response = completed_response(case)
    reviewed = response['cases'][0]
    if change == 'duplicate_case':
        response['cases'].append(deepcopy(reviewed))
    elif change == 'unknown_case':
        reviewed['game_id'] = 'unknown'
    elif change == 'duplicate_batch':
        reviewed['batches'].append(deepcopy(reviewed['batches'][0]))
    elif change == 'unknown_batch':
        reviewed['batches'][0]['batch'] = 'unknown'
    elif change == 'case_hash':
        reviewed['proposal_sha256'] = 'bad'
    elif change == 'batch_hash':
        reviewed['batches'][0]['proposal_sha256'] = 'bad'
    elif change == 'identity':
        reviewed['game_no'] = True
    elif change == 'empty_cases':
        response['cases'] = []
    else:
        reviewed['batches'] = []
    with pytest.raises(subject.ReviewBatchError):
        subject.validate_review_response({'cases': [case]}, response, complete=True)

@pytest.mark.parametrize('duplicate_in', ['response', 'batch'])
def test_cli_rejects_raw_duplicate_keys_before_schema_validation(tmp_path, monkeypatch, duplicate_in):
    case = synthetic_case()
    batch = tmp_path / 'batch.json'
    response = tmp_path / 'response.json'
    batch.write_text(json.dumps({'cases': [case]}), encoding='utf-8')
    response.write_text(json.dumps(completed_response(case)), encoding='utf-8')
    target = response if duplicate_in == 'response' else batch
    target.write_text('{"nested":{"decision":"Uncertain","decision":"Confirm"}}', encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['review', '--validate-response', str(response),
                                   '--review-batch', str(batch), '--review-batch-sha256',
                                   subject.sha_bytes(batch.read_bytes())])
    with pytest.raises(subject.ReviewBatchError, match='duplicate JSON key'):
        subject.main()


def test_cli_reports_expanded_compact_decisions_and_omissions(tmp_path, monkeypatch, capsys):
    case = synthetic_case()
    other = deepcopy(case)
    other['game_id'] = 'other-synthetic-game'
    other['match_id'] = 'other-synthetic-match'
    batch = tmp_path / 'batch.json'
    response = tmp_path / 'response.json'
    batch.write_text(json.dumps({'cases': [case, other]}), encoding='utf-8')
    response.write_text(json.dumps(compact_response(case)), encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['review', '--validate-response', str(response),
                                   '--review-batch', str(batch), '--review-batch-sha256',
                                   subject.sha_bytes(batch.read_bytes())])
    subject.main()
    assert json.loads(capsys.readouterr().out) == {
        'valid': True, 'finalized': False, 'confirm': 7, 'correct': 0, 'uncertain': 0, 'unreviewed': 7}


@pytest.mark.parametrize('change', ['case_hash', 'batch_hash', 'unknown_batch', 'reviewed_at',
                                    'reviewer', 'source_attestation'])
def test_compact_keeps_hash_identity_time_and_reviewer_guards(change):
    case = synthetic_case()
    response = compact_response(case)
    reviewed = response['cases'][0]
    compact = reviewed['confirm_all_batches']
    if change == 'case_hash':
        reviewed['proposal_sha256'] = 'bad'
    elif change == 'batch_hash':
        compact['batch_proposal_sha256']['B1'] = 'bad'
    elif change == 'unknown_batch':
        compact['batch_proposal_sha256']['unknown'] = 'bad'
    elif change == 'reviewed_at':
        compact['reviewed_at'] = '2026-10-31T02:01:00+08:00'
    elif change == 'reviewer':
        response['reviewer_id'] = ' '
    else:
        response['source_inspection_attested'] = False
    with pytest.raises(subject.ReviewBatchError):
        subject.validate_review_response({'cases': [case]}, response, complete=True)


def test_partial_reviews_keep_explicit_uncertainty_and_correction_guards():
    case = synthetic_case()
    response = completed_response(case)
    decisions = response['cases'][0]['batches']
    response['cases'][0]['batches'] = [decisions[1], decisions[3]]
    decisions[1]['decision'] = 'Uncertain'
    result = subject.validate_review_response({'cases': [case]}, response, complete=True)
    assert result == {'confirm': 1, 'correct': 0, 'uncertain': 1, 'unreviewed': 5}
    decisions[1].update(decision='Correct', corrected_heroes=['Hotel', 'Golf'])
    with pytest.raises(subject.ReviewBatchError, match='conflicting corrected'):
        subject.validate_review_response({'cases': [case]}, response, complete=True)
    decisions[1]['corrected_heroes'] = ['Alpha', 'Golf']
    with pytest.raises(subject.ReviewBatchError, match='correction team'):
        subject.validate_review_response({'cases': [case]}, response, complete=True)


def test_submission_does_not_mutate_proposal_or_normal_response():
    case = synthetic_case()
    response = completed_response(case)
    before = deepcopy((case, response))
    subject.validate_review_response({'cases': [case]}, response, complete=True)
    assert (case, response) == before


def test_full_six_case_template_with_only_first_game_confirmed():
    cases = []
    for identity, gid in subject.TARGETS.items():
        case = synthetic_case()
        case.pop('proposal_sha256')
        case.pop('proposal_sha256', None)
        case.update(match_id=identity[0], game_no=identity[1], game_id=gid)
        case['proposal_sha256'] = subject.sha_json(case)
        cases.append(case)
    response = subject.make_review_template(cases)
    completed = completed_response(cases[0])
    for key in ('reviewer_id', 'source_inspection_attested', 'review_started_at', 'review_completed_at'):
        response[key] = completed[key]
    response['cases'][0] = completed['cases'][0]
    result = subject.validate_review_response({'cases': cases}, response, complete=True)
    assert result == {'confirm': 7, 'correct': 0, 'uncertain': 0, 'unreviewed': 35}
    assert all(b['decision'] is None for c in response['cases'][1:] for b in c['batches'])


def test_wrong_case_identity_alias_is_rejected():
    cases = [synthetic_case(), synthetic_case()]
    identity = list(subject.TARGETS)[1]
    cases[1].update(match_id=identity[0], game_no=identity[1], game_id=subject.TARGETS[identity])
    response = completed_response(cases[0])
    alias = deepcopy(response['cases'][0])
    alias['game_id'] = cases[1]['game_id']
    response['cases'].append(alias)
    with pytest.raises(subject.ReviewBatchError, match='identity'):
        subject.validate_review_response({'cases': cases}, response, complete=True)


@pytest.mark.parametrize('change', ['game_id', 'match_game_alias', 'batch'])
def test_duplicate_source_indexes_are_rejected(change):
    case = synthetic_case()
    cases = [case]
    if change == 'batch':
        case['batches'].append(deepcopy(case['batches'][0]))
    else:
        other = deepcopy(case)
        if change == 'match_game_alias':
            other['game_id'] = 'alias'
        cases.append(other)
    with pytest.raises(subject.ReviewBatchError, match='duplicate'):
        subject.validate_review_response({'cases': cases}, completed_response(case), complete=True)


@pytest.mark.parametrize('field,value', [('source_inspected', True), ('corrected_heroes', ['Alpha']),
                                       ('source_note', 'Prefilled note'),
                                       ('reviewed_at', '2026-10-31T01:30:00+08:00')])
def test_null_decisions_cannot_have_prefilled_inspection_fields(field, value):
    case = synthetic_case()
    response = completed_response(case)
    decision = subject.make_review_template([case])['cases'][0]['batches'][0]
    decision[field] = value
    response['cases'][0]['batches'][0] = decision
    with pytest.raises(subject.ReviewBatchError, match='prefilled'):
        subject.validate_review_response({'cases': [case]}, response, complete=True)
