"""MC-968: scoped previous campaign evidence, bounded before prompt assembly."""
import json

import pytest

from mc import desk, desk_brief, state
from mc.desk_campaign_results import previous_results_digest


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(state, 'CONFIG', {})
    return desk


def seed(store):
    data = store._empty_store()
    data['campaigns'] = {
        'old': {'id': 'old', 'project_id': 'p', 'state': 'done', 'updated_at': '2026-10-01'},
        'last': {'id': 'last', 'project_id': 'p', 'title': 'Previous', 'state': 'done', 'updated_at': '2026-10-02'},
        'other': {'id': 'other', 'project_id': 'q', 'state': 'done', 'updated_at': '2026-10-03'},
        'active': {'id': 'active', 'project_id': 'p', 'state': 'running', 'updated_at': '2026-10-04'},
    }
    data['ledger'] = [
        {'id': 'a', 'campaign_id': 'last', 'published_at': '2026-01-01T00:00:00Z',
         'outcomes': [{'metric': 'impressions', 'value': 4, 'at': '2026-01-02T00:00:00Z'},
                      {'metric': 'impressions', 'value': 2, 'at': '2026-01-01T00:01:00Z'},
                      {'metric': 'replies', 'value': 0, 'at': '2026-01-02T00:00:00Z'}]},
        {'id': 'b', 'campaign_id': 'last', 'published_at': '2026-01-01T00:00:00Z', 'outcomes': []},
        {'id': 'c', 'campaign_id': 'other', 'published_at': '2026-01-01T00:00:00Z', 'outcomes': []},
    ]
    store._write_store(data)


def test_latest_done_same_project_excluding_self(store):
    seed(store)
    digest = previous_results_digest({'id': 'active', 'project_id': 'p'})
    evidence = json.loads(digest.split('\n', 1)[1])
    assert evidence['campaign'] == 'Previous'
    assert evidence['impressions'] == '4 (1/2 receipts measured)'
    assert evidence['replies'] == '0 (1/2 receipts measured)'
    assert evidence['possibly_suppressed'] == 1
    assert evidence['suppression_unknown'] == 1
    assert 'Previous' not in previous_results_digest({'id': 'last', 'project_id': 'p'})


def test_missing_campaigns_and_unknown_metrics(store):
    assert previous_results_digest({'project_id': 'p'}) == ''
    assert previous_results_digest({}) == ''
    seed(store)
    data = json.loads(previous_results_digest({'project_id': 'q'}).split('\n', 1)[1])
    assert data['impressions'] == 'unknown' and data['replies'] == 'unknown'
    assert data['suppression_assessed'] == 0


def test_bounded_digest_and_brief_wiring(store):
    seed(store)
    data = store._read_store()
    data['campaigns']['last']['title'] = '\U0001f426' * 100000
    store._write_store(data)
    campaign = {'id': 'active', 'project_id': 'p'}
    digest = previous_results_digest(campaign)
    assert len(digest) <= 1400
    assert len(json.loads(digest.split('\n', 1)[1])['campaign']) == 60
    brief = desk_brief.build_suggest_brief(campaign, accounts=[])
    assert digest in brief
    assert 'YOU ARE NOT STARTING' in brief


def test_digest_and_retro_share_live_config_threshold(store):
    seed(store)
    state.CONFIG['desk_suppression_impressions'] = 3
    data = json.loads(previous_results_digest({'project_id': 'p'}).split('\n', 1)[1])
    assert data['possibly_suppressed'] == 0
