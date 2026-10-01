"""Desk v1 R1-W S3 — the Goal stop's backend (plan M11 + the goal PATCH allowlist).

Rule (MET-01): a number with no data behind it is null, never 0. Pinned:

  * `GET /api/desk/campaigns/<id>/results` answers 404 for an unknown campaign;
  * with no measurement source, or a manual source with no entries, `goal.current`
    is None (not 0), `forecast` is None, a term's `current` is None, and
    `freshness` is None;
  * with entries, `current` is the NEWEST entry's value (ties: first listed, the
    browser's own rule), `freshness` is its date, and each term reads the newest
    entry dated inside that term's window (None when none is);
  * a ledger row with no recorded outcome for the goal's metric is `metric: None`
    labelled `delayed` (or `n/a` when the goal names no metric), never 0;
  * the v1 PATCH allowlist: only metric/target/baseline/unit/horizon/deadline/
    source/entries are stored, `current` is dropped, the PATCH replaces the stored goal
    like every other v1 key, and a wrong-typed value is a 400 that writes nothing;
  * the v1 campaign shape carries the DERIVED `goal.current`, so a stale stored 0
    (every draft a client created used to carry `current: 0`) reads as None;
  * the legacy (non-v1) PATCH still replaces `goal` wholesale, untouched.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk as _desk  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-g'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


def _draft(**kw):
    body = {'id': CID, 'state': 'draft', 'projectId': 'alpha', 'goal': {'current': 0}, 'rules': {},
            'map': {'stop': 'goal', 'done': []},
            'plan': {'brief': 'Promote Alpha.', 'title': 'Alpha campaign', 'accounts': [],
                     'cadence': {'per_week': None}, 'end': {'date': None, 'post_cap': None}}}
    body.update(kw)
    return body


def _make(client, **kw):
    r = client.post('/api/desk/campaigns?shape=v1', json=_draft(**kw))
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _patch_goal(client, goal, expect=200):
    r = client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json={'goal': goal})
    assert r.status_code == expect, r.get_json()
    return r.get_json()


def _results(client):
    r = client.get(f'/api/desk/campaigns/{CID}/results')
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _stored_goal():
    return next(c for c in _desk.list_campaigns() if c['id'] == CID)['goal']


# -- M11 ----------------------------------------------------------------------

def test_unknown_campaign_is_404(client):
    assert client.get('/api/desk/campaigns/nope/results').status_code == 404


def test_no_source_means_no_data_not_zero(client):
    _make(client)
    out = _results(client)
    assert out['goal']['current'] is None
    assert out['goal']['freshness'] is None
    assert out['goal']['source'] is None
    assert out['forecast'] is None
    assert out['versions'] == []
    assert out['costs'] == {'ledger': None}


def test_manual_source_with_no_entries_is_still_no_data(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'source': 'manual'})
    out = _results(client)
    assert out['goal']['current'] is None and out['goal']['target'] == 30
    assert out['goal']['freshness'] is None and out['forecast'] is None


def test_current_is_the_newest_entry_and_freshness_is_its_date(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'source': 'manual', 'entries': [
        {'at': '2026-09-20', 'value': 11}, {'at': '2026-09-24T18:00:00Z', 'value': 14}, {'at': '2026-09-01', 'value': 3}]})
    out = _results(client)
    assert out['goal']['current'] == 14
    assert out['goal']['freshness'] == '2026-09-24T18:00:00Z'


def test_a_tie_keeps_the_first_listed_entry():
    goal = {'source': 'manual', 'entries': [{'at': '2026-09-20', 'value': 5}, {'at': '2026-09-20', 'value': 9}]}
    assert _desk.goal_current(goal) == 5


def test_entries_are_ignored_unless_the_source_is_manual(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'entries': [{'at': '2026-09-20', 'value': 11}]})
    assert _results(client)['goal']['current'] is None


def test_a_real_zero_entry_is_zero(client):
    # 0 is a measurement when somebody typed it; only "no data" is None.
    _make(client, goal={'metric': 'signups', 'target': 30, 'source': 'manual',
                        'entries': [{'at': '2026-09-20', 'value': 0}]})
    assert _results(client)['goal']['current'] == 0


def test_terms_read_the_entry_inside_their_own_window(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'horizon': 'long', 'source': 'manual', 'entries': [
        {'at': '2026-07-15', 'value': 12}, {'at': '2026-08-30', 'value': 13}, {'at': '2026-10-02', 'value': 11}]})
    stored = _desk.update_campaign(CID, {'term': {'index': 2, 'starts': '2026-09-01', 'ends': '2026-10-20'}})
    assert stored
    # `terms` is server-owned; seed it the way Renew does.
    with _desk._store_lock:
        store = _desk._read_store()
        store['campaigns'][CID]['terms'] = [
            {'index': 1, 'starts': '2026-07-01', 'ends': '2026-08-30', 'target': 15},
            {'index': 2, 'starts': '2026-09-01', 'ends': '2026-10-20'},
            {'index': 3, 'starts': '2026-11-01', 'ends': '2026-12-01'}]
        _desk._write_store(store)
    terms = _results(client)['terms']
    assert [t['index'] for t in terms] == [1, 2, 3]
    # term 1 ends 2026-08-30 (date-only: through that day), so the 08-30 entry is in it
    assert terms[0]['current'] == 13 and terms[0]['target'] == 15
    assert terms[1]['current'] == 11 and terms[1]['target'] == 30   # target falls back to the goal's
    assert terms[2]['current'] is None                              # no entry in term 3: no data, not 0


def test_forecast_needs_current_target_and_elapsed_time(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'source': 'manual',
                        'entries': [{'at': '2026-09-10', 'value': 10}]},
          term={'index': 1, 'starts': '2026-09-01', 'ends': '2026-09-30'})
    mid = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)   # 14.5 of 30 days elapsed
    out = _desk.campaign_results(CID, now=mid)
    assert out['forecast']['target'] == 30
    assert out['forecast']['projected'] == pytest.approx(10 / (14.5 / 30), abs=0.01)
    assert out['forecast']['label'] == 'below target'
    # a term that has not begun has no elapsed share, so no forecast
    before = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert _desk.campaign_results(CID, now=before)['forecast'] is None


def _post(client, **kw):
    body = {'platform': 'x', 'voice': 'v', 'body': 'hello', 'campaign_id': CID}
    body.update(kw)
    return _desk.record_published(**body)


def test_ledger_versions_are_null_with_a_label_never_zero(client):
    _make(client, goal={'metric': 'Signups', 'target': 30, 'source': 'manual'})
    measured = _post(client, piece_id='p1')
    silent = _post(client, piece_id='p2')
    other_metric = _post(client, piece_id='p3')
    _post(client, campaign_id='some-other-campaign')
    _desk.record_outcome(measured['id'], 'signups', 8, at='2026-09-20T00:00:00Z')
    _desk.record_outcome(measured['id'], 'signups', '9', at='2026-09-25T00:00:00Z')   # newer, numeric string
    _desk.record_outcome(other_metric['id'], 'likes', 40)
    _desk.record_outcome(silent['id'], 'signups', 'n/a')                               # not a number
    versions = {v['piece_id']: v for v in _results(client)['versions']}
    assert set(versions) == {'p1', 'p2', 'p3'}                  # the other campaign's post is not here
    assert versions['p1']['metric'] == 9.0 and versions['p1']['outcome'] == 'measured'
    assert versions['p1']['metric_label'] == 'Signups'
    for pid in ('p2', 'p3'):
        assert versions[pid]['metric'] is None and versions[pid]['metric_label'] == 'delayed'
        assert versions[pid]['outcome'] == 'unknown'


def test_a_goal_with_no_metric_labels_versions_na(client):
    _make(client)
    _post(client, piece_id='p1')
    v = _results(client)['versions'][0]
    assert v['metric'] is None and v['metric_label'] == 'n/a'


def test_costs_are_the_ledger_sum_or_null(client):
    _make(client)
    assert _results(client)['costs'] == {'ledger': None}      # no posts: nothing was spent that we know of
    _post(client, cost=0.015)
    _post(client, cost=0.2)
    assert _results(client)['costs']['ledger'] == pytest.approx(0.215)


# -- the PATCH allowlist ------------------------------------------------------

def test_current_is_never_stored_and_never_served_stale(client):
    out = _make(client)                                   # the draft body carried goal.current = 0
    assert 'current' not in _stored_goal()
    assert out['goal']['current'] is None
    out = _patch_goal(client, {'metric': 'signups', 'current': 99})
    assert 'current' not in _stored_goal()
    assert out['goal']['current'] is None


def test_a_stale_stored_current_reads_as_none_in_the_v1_shape(client):
    _make(client)
    with _desk._store_lock:
        store = _desk._read_store()
        store['campaigns'][CID]['goal'] = {'current': 0, 'metric': 'signups'}   # what old drafts stored
        _desk._write_store(store)
    ws = client.get('/api/desk/workspace').get_json()
    camp = next(c for c in ws['campaigns'] if c['id'] == CID)
    assert camp['goal']['current'] is None


def test_a_goal_patch_replaces_the_stored_goal_with_its_allowlisted_keys(client):
    # Same contract as every other v1 key (the client sends its whole goal), so
    # nothing the client no longer names lingers; `current` is not stored.
    _make(client, goal={'metric': 'signups', 'target': 30, 'unit': 'signups', 'horizon': 'short',
                        'deadline': '2026-10-20', 'source': 'manual', 'entries': [{'at': '2026-09-20', 'value': 4}]})
    out = _patch_goal(client, {'metric': 'signups', 'target': 40, 'source': 'manual', 'current': 4,
                               'entries': [{'at': '2026-09-20', 'value': 4}]})
    assert _stored_goal() == {'metric': 'signups', 'target': 40, 'source': 'manual',
                              'entries': [{'at': '2026-09-20', 'value': 4}]}
    assert out['goal']['current'] == 4


def test_entries_replace_the_list_and_move_current(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'source': 'manual'})
    _patch_goal(client, {'metric': 'signups', 'source': 'manual', 'entries': [{'at': '2026-09-20', 'value': 4}, {'at': '2026-09-22', 'value': 7}]})
    assert _results(client)['goal']['current'] == 7
    _patch_goal(client, {'metric': 'signups', 'source': 'manual', 'entries': [{'at': '2026-09-20', 'value': 4}]})
    assert _results(client)['goal']['current'] == 4
    _patch_goal(client, {'metric': 'signups', 'source': 'manual', 'entries': []})
    assert _results(client)['goal']['current'] is None


def test_removing_the_source_makes_current_none(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'source': 'manual',
                        'entries': [{'at': '2026-09-20', 'value': 4}]})
    _patch_goal(client, {'metric': 'signups', 'source': None, 'entries': [{'at': '2026-09-20', 'value': 4}]})
    assert _results(client)['goal']['current'] is None


def test_keys_outside_the_allowlist_are_dropped(client):
    _make(client)
    _patch_goal(client, {'metric': 'signups', 'approved': True, 'freshness': 'x', 'outcome': 'y'})
    assert set(_stored_goal()) == {'metric'}


@pytest.mark.parametrize('bad', [
    {'target': 'lots'}, {'target': True}, {'target': float('inf')}, {'baseline': [1]},
    {'horizon': 'forever'}, {'deadline': 'soon'}, {'source': 'feed'}, {'metric': 7},
    {'entries': 'x'}, {'entries': [{'at': '2026-09-20', 'value': 'ten'}]},
    {'entries': [{'at': '2026-09-20', 'value': True}]}, {'entries': [{'at': 'tomorrow', 'value': 3}]},
    {'entries': [{'value': 3}]}, {'entries': [3]},
])
def test_wrong_typed_goal_values_are_400_and_write_nothing(client, bad):
    _make(client, goal={'metric': 'signups', 'target': 30})
    before = _stored_goal()
    r = client.patch(f'/api/desk/campaigns/{CID}?shape=v1', json={'goal': bad})
    assert r.status_code == 400 and r.get_json()['error']
    assert _stored_goal() == before


def test_empty_text_and_blank_numbers_clear_the_field(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'unit': 'x'})
    _patch_goal(client, {'metric': '  ', 'target': None, 'unit': None})
    g = _stored_goal()
    assert g['metric'] is None and g['target'] is None and g['unit'] is None


def test_goal_edit_after_start_does_not_touch_the_approval(client):
    _make(client, goal={'metric': 'signups', 'target': 30, 'source': 'manual'},
          plan={'brief': 'x', 'title': 'T', 'accounts': ['x:ron'], 'cadence': {'per_week': 3},
                'end': {'date': '2026-12-01', 'post_cap': 10}})
    assert client.post(f'/api/desk/campaigns/{CID}/start').status_code == 200
    before = next(c for c in _desk.list_campaigns() if c['id'] == CID)['approved']
    out = _patch_goal(client, {'metric': 'signups', 'source': 'manual', 'target': 99, 'entries': [{'at': '2026-09-20', 'value': 1}]})
    assert next(c for c in _desk.list_campaigns() if c['id'] == CID)['approved'] == before
    assert out['awaiting_approval'] is False


def test_legacy_patch_still_replaces_goal_wholesale(client):
    _make(client, goal={'metric': 'signups', 'target': 30})
    r = client.patch(f'/api/desk/campaigns/{CID}', json={'goal': {'note': 'legacy', 'current': 5}})
    assert r.status_code == 200
    assert _stored_goal() == {'note': 'legacy', 'current': 5}
