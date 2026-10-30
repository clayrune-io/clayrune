"""MC-1125: end-date edits cannot leave or restore a stale started term."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from flask import Flask

from mc import desk
from mc.blueprints import desk_routes
from mc.desk_tick import _cadence_refusal


@pytest.fixture
def client(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: [{'id': 'alpha'}],
        load_project_fn=lambda pid: {'id': pid},
        store_path=tmp_path / 'desk.json', signals_path=tmp_path / 'signals.jsonl')
    # Approval semantics here; test_desk_approval exercises the real proof gate.
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


def started():
    draft = desk.create_campaign(
        'Term regression', 'A bounded campaign', voiceless_ok=True,
        project_id='alpha', state='draft',
        plan={'accounts': ['x:test'], 'cadence': {'per_week': 3},
              'end': {'date': '2026-10-28', 'post_cap': 10}},
        goal={'horizon': 'short'},
        term={'index': 1, 'starts': '2026-10-15', 'ends': '2026-10-28', 'post_cap': 10})
    return desk.start_campaign(draft['id'], today='2026-10-15')


def patch_end(camp, date):
    plan = deepcopy(camp['plan'])
    plan['end']['date'] = date
    return {'plan': plan}


def stored(cid):
    return next(c for c in desk.list_campaigns() if c['id'] == cid)


@pytest.mark.parametrize('shape', ['', '?shape=v1'])
def test_end_extension_derives_term_and_still_needs_human_approval(client, shape):
    camp = started()
    old_approval = deepcopy(camp['approved'])
    r = client.patch(f"/api/desk/campaigns/{camp['id']}{shape}",
                     json=patch_end(camp, '2026-11-28'))
    assert r.status_code == 200, r.get_json()
    updated = stored(camp['id'])
    assert updated['term'] == {**camp['term'], 'ends': '2026-11-28'}
    assert updated['approved'] == old_approval
    assert desk.awaiting_approval(updated)
    assert desk.publish_blockers(camp['id'])
    r = client.post(f"/api/desk/campaigns/{camp['id']}/approve")
    assert r.status_code == 200, r.get_json()
    assert desk.publish_blockers(camp['id']) == []
    assert stored(camp['id'])['approved']['bounds']['term']['ends'] == '2026-11-28'


def test_browser_stale_term_cannot_undo_a_newer_server_end(client):
    camp = started()
    stale = deepcopy(camp['term'])
    # The server now owns a more recent end; the browser has not reloaded.
    latest = desk.update_campaign(camp['id'], patch_end(camp, '2026-11-05'))
    r = client.patch(f"/api/desk/campaigns/{camp['id']}?shape=v1",
                     json={**patch_end(latest, '2026-11-28'), 'term': stale})
    assert r.status_code == 200
    assert r.get_json()['term']['ends'] == '2026-11-28'
    # Approval's pre-save and a later unrelated save must not put October back.
    for body in ({'term': stale}, {'title': 'Renamed', 'term': stale},
                 {**patch_end(latest, '2026-11-28'), 'term': stale}):
        out = desk.update_campaign(camp['id'], body)
        assert out['term']['ends'] == '2026-11-28'
        assert out['approved'] == camp['approved']


@pytest.mark.parametrize('term', [{}, None, {'index': 9, 'starts': '1900-01-01', 'ends': '2099-01-01', 'post_cap': None}])
def test_started_term_is_not_client_writable(client, term):
    camp = started()
    out = desk.update_campaign(camp['id'], {'term': term})
    assert out['term'] == camp['term']
    assert out['approved'] == camp['approved']
    assert not desk.awaiting_approval(out)


def test_relabeling_as_draft_cannot_make_started_term_writable(client):
    camp = started()
    desk.update_campaign(camp['id'], {'state': 'draft'})
    assert desk.update_campaign(camp['id'], {'term': {}})['term'] == camp['term']


@pytest.mark.parametrize('date, expected', [('2026-12-01', '2026-12-01'),
                                           ('2027-02-28', '2027-01-13')])
def test_extension_is_capped_at_90_days_from_current_term_start(client, date, expected):
    camp = started()
    out = desk.update_campaign(camp['id'], patch_end(camp, date))
    assert out['term']['ends'] == expected
    assert desk.MAX_TERM_DAYS == 90
    assert desk.awaiting_approval(out)


def test_narrowing_and_clearing_plan_end_do_not_remove_term_ceiling(client):
    camp = started()
    narrowed = desk.update_campaign(camp['id'], patch_end(camp, '2026-10-25'))
    assert narrowed['term']['ends'] == '2026-10-25'
    assert not desk.awaiting_approval(narrowed)
    cleared = desk.update_campaign(camp['id'], patch_end(narrowed, None))
    assert cleared['term'] == narrowed['term']
    assert desk.awaiting_approval(cleared)


def test_draft_term_proposals_still_reach_the_start_gate(client):
    draft = desk.create_campaign('Draft', 'Bounded', voiceless_ok=True, state='draft')
    term = {'starts': '2026-01-01', 'ends': '2026-12-31'}
    out = desk.update_campaign(draft['id'], {'term': term})
    assert out['term'] == term
    with pytest.raises(desk.ApprovalRefused, match='max 90'):
        desk.start_campaign(draft['id'])


def test_history_current_row_changes_without_rewriting_closed_terms(client):
    camp = started()
    # Seed the same server-owned history shape Renew writes, in the temp store.
    closed = {'index': 0, 'starts': '2026-07-15', 'ends': '2026-10-13'}
    with desk._store_lock:
        store = desk._read_store()
        store['campaigns'][camp['id']]['terms'] = [closed, camp['term']]
        desk._write_store(store)
    out = desk.update_campaign(camp['id'], patch_end(camp, '2026-11-28'))
    assert out['terms'] == [closed, out['term']]
    assert out['approved'] == camp['approved']


def test_invalid_end_edit_writes_nothing(client):
    camp = started()
    before = desk.STORE_PATH.read_bytes()
    r = client.patch(f"/api/desk/campaigns/{camp['id']}", json=patch_end(camp, 'not-a-date'))
    assert r.status_code == 400
    assert desk.STORE_PATH.read_bytes() == before


def test_expired_plan_names_the_on_screen_edit_and_approval(client):
    camp = started()
    reason = _cadence_refusal(camp, [], datetime(2026, 10, 29, tzinfo=timezone.utc))
    assert reason == 'This campaign ended on 2026-10-28. Extend its end date, then approve to keep posting.'


def test_capped_term_names_the_visible_renew_action(client):
    camp = started()
    out = desk.update_campaign(camp['id'], patch_end(camp, '2027-02-28'))
    desk.approve_campaign(camp['id'])
    reason = _cadence_refusal(out, [], datetime(2027, 1, 14, tzinfo=timezone.utc))
    assert 'in Launch, choose Renew term' in reason
    renewed = desk.renew_campaign(camp['id'], today='2027-01-14')
    assert renewed['term']['index'] == 2
