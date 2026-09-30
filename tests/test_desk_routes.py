"""The Desk — routes (mc/blueprints/desk_routes.py).

Guards the surface contract rather than re-testing the store (tests/test_desk.py
does that): status codes, validation, and the two properties that are design
decisions rather than implementation details —

  * a campaign without a THESIS is refused, because a campaign without one is a
    folder, and the Board's job is to answer "why is this running now";
  * there is NO publish route, and `POST /api/desk/ledger` only records that a
    human already released something.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk as _desk  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: [{'social_pending_count': 2},
                                  {'social_pending_count': 1}],
        load_project_fn=lambda _pid: None,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


# -- signals ------------------------------------------------------------------

def test_post_and_list_signals(client):
    r = client.post('/api/desk/signals', json={
        'project_id': 'mission_control', 'kind': 'release',
        'summary': 'Shipped drag-to-hire, now live'})
    assert r.status_code == 201
    assert r.get_json()['story_score'] > 0

    rows = client.get('/api/desk/signals').get_json()
    assert len(rows) == 1
    assert rows[0]['project_id'] == 'mission_control'


def test_signal_requires_project_and_summary(client):
    assert client.post('/api/desk/signals', json={'summary': 'x'}).status_code == 400
    assert client.post('/api/desk/signals', json={'project_id': 'p'}).status_code == 400


def test_bad_min_score_is_a_400_not_a_500(client):
    assert client.get('/api/desk/signals?min_score=soon').status_code == 400


def test_signal_filters(client):
    client.post('/api/desk/signals', json={
        'project_id': 'a', 'kind': 'release', 'summary': 'Shipped a real thing'})
    client.post('/api/desk/signals', json={
        'project_id': 'b', 'kind': 'commit', 'summary': 'chore: lint'})
    assert len(client.get('/api/desk/signals?project_id=a').get_json()) == 1
    hot = client.get('/api/desk/signals?min_score=0.35').get_json()
    assert len(hot) == 1 and hot[0]['project_id'] == 'a'


# -- voices -------------------------------------------------------------------

def test_voices_list_and_patch(client):
    names = [v['name'] for v in client.get('/api/desk/voices').get_json()]
    assert names == ['personal', 'product']

    r = client.patch('/api/desk/voices/personal', json={'banned': ['leverage']})
    assert r.status_code == 200 and r.get_json()['banned'] == ['leverage']


def test_unknown_voice_is_404(client):
    assert client.get('/api/desk/voices/marketing').status_code == 404
    assert client.patch('/api/desk/voices/marketing', json={}).status_code == 404
    assert client.post('/api/desk/voices/marketing/edit', json={}).status_code == 404


def test_edit_learns_and_reports_when_it_did_not(client):
    r = client.post('/api/desk/voices/personal/edit', json={
        'before': 'Excited to announce our game-changing feature!',
        'after': 'Shipped drag-to-hire. Three days, two rewrites.',
        'draft_id': 'd-1'})
    assert r.status_code == 200 and r.get_json()['learned'] is True

    # A cosmetic edit is a real answer, not an error.
    r2 = client.post('/api/desk/voices/personal/edit',
                     json={'before': 'same text', 'after': 'same text'})
    assert r2.status_code == 200 and r2.get_json()['learned'] is False


def test_brief_carries_the_rewrite(client):
    client.post('/api/desk/voices/personal/edit', json={
        'before': 'We are thrilled to leverage synergies across the stack',
        'after': 'I rewrote the scheduler and it came out slower.'})
    brief = client.get('/api/desk/voices/personal/brief').get_json()['brief']
    assert 'I rewrote the scheduler' in brief


# -- campaigns ----------------------------------------------------------------

def test_campaign_crud(client):
    r = client.post('/api/desk/campaigns', json={
        'title': 'Agent persistence', 'thesis': 'Clayrune keeps agents alive',
        'voice': 'product'})
    assert r.status_code == 201
    cid = r.get_json()['id']

    assert client.patch(f'/api/desk/campaigns/{cid}',
                        json={'state': 'running'}).get_json()['state'] == 'running'
    assert len(client.get('/api/desk/campaigns?state=running').get_json()) == 1
    assert client.delete(f'/api/desk/campaigns/{cid}').status_code == 200
    assert client.get('/api/desk/campaigns').get_json() == []


def test_earmark_over_project_budget_is_a_400_at_the_route(client):
    _desk.upsert_presence('proj-1', {'budget': {'amount': 100}})
    ok = client.post('/api/desk/campaigns', json={
        'title': 'a', 'thesis': 'th', 'project_id': 'proj-1',
        'how': {'budget': {'source': 'project', 'amount': 60}}})
    assert ok.status_code == 201

    over = client.post('/api/desk/campaigns', json={
        'title': 'b', 'thesis': 'th', 'project_id': 'proj-1',
        'how': {'budget': {'source': 'project', 'amount': 50}}})
    assert over.status_code == 400
    assert 'earmark' in over.get_json()['error']


def test_a_campaign_without_a_thesis_is_refused(client):
    """A campaign without a thesis is a folder — the Board must answer *why*."""
    assert client.post('/api/desk/campaigns',
                       json={'title': 'Stuff'}).status_code == 400


def test_bad_state_and_voice_are_400(client):
    cid = client.post('/api/desk/campaigns',
                      json={'title': 't', 'thesis': 'th'}).get_json()['id']
    assert client.patch(f'/api/desk/campaigns/{cid}',
                        json={'state': 'launched'}).status_code == 400
    assert client.post('/api/desk/campaigns',
                       json={'title': 't', 'thesis': 'th',
                             'voice': 'marketing'}).status_code == 400


def test_missing_campaign_is_404(client):
    assert client.patch('/api/desk/campaigns/nope', json={'state': 'running'}).status_code == 404
    assert client.delete('/api/desk/campaigns/nope').status_code == 404


# -- ledger -------------------------------------------------------------------

def test_ledger_records_and_consumes_its_signal(client):
    sig = client.post('/api/desk/signals', json={
        'project_id': 'mission_control', 'kind': 'release',
        'summary': 'Shipped the Desk'}).get_json()

    r = client.post('/api/desk/ledger', json={
        'platform': 'x', 'voice': 'personal', 'body': 'Shipped the Desk today',
        'signal_id': sig['id'], 'project_id': 'mission_control'})
    assert r.status_code == 201

    # The signal is now spent, so it stops being offered as raw material.
    assert client.get('/api/desk/signals?unconsumed=1').get_json() == []


def test_ledger_validation(client):
    assert client.post('/api/desk/ledger', json={'platform': 'x'}).status_code == 400
    assert client.post('/api/desk/ledger',
                       json={'platform': 'x', 'body': 'b',
                             'voice': 'marketing'}).status_code == 400


def test_outcome_roundtrip(client):
    pid = client.post('/api/desk/ledger', json={
        'platform': 'x', 'body': 'hello world'}).get_json()['id']
    r = client.post(f'/api/desk/ledger/{pid}/outcome', json={'metric': 'likes', 'value': 9})
    assert r.get_json()['outcomes'][0]['metric'] == 'likes'
    assert r.get_json()['outcomes'][0]['value'] == 9
    assert client.post('/api/desk/ledger/nope/outcome', json={}).status_code == 400
    assert client.post('/api/desk/ledger/nope/outcome',
                       json={'metric': 'likes', 'value': 1}).status_code == 404


def test_repeat_check_catches_a_re_announcement(client):
    client.post('/api/desk/ledger', json={
        'platform': 'x',
        'body': 'Shipped drag-to-hire: grab an agent off the Floor and drop '
                'it on a project to hire it.'})
    r = client.post('/api/desk/repeat-check', json={
        'body': 'Shipped drag-to-hire: grab an agent off the Floor and drop '
                'it onto a project to hire it.'}).get_json()
    assert r['repeat'] is True and r['matches']

    clean = client.post('/api/desk/repeat-check', json={
        'body': 'Restore points now keep ten snapshots plus anything pinned'}).get_json()
    assert clean['repeat'] is False


def test_repeat_check_on_empty_body(client):
    assert client.post('/api/desk/repeat-check', json={'body': '  '}).get_json() == {
        'repeat': False, 'matches': []}


# -- overview -----------------------------------------------------------------

def test_overview_sums_pending_across_projects(client):
    d = client.get('/api/desk/overview').get_json()
    assert d['pending_drafts'] == 3, 'the Desk is cross-project by construction'
    assert d['voices'] == ['personal', 'product']
    assert d['running'] == 0


def test_overview_survives_a_broken_project_loader(client):
    def boom():
        raise RuntimeError('projects unreadable')
    desk_routes.wire(load_projects_fn=boom)
    d = client.get('/api/desk/overview').get_json()
    assert d['pending_drafts'] == 0, 'a bad loader degrades the count, not the page'


# -- the structural guarantee -------------------------------------------------

def test_there_is_no_publish_route(client):
    """If this test starts failing because someone added a publish route, the
    approval gate has stopped being structural — read the module docstring."""
    src = (PROJECT_ROOT / 'mc' / 'blueprints' / 'desk_routes.py').read_text(encoding='utf-8')
    for forbidden in ('requests.', 'urllib.request', 'httpx.', 'tweepy'):
        assert forbidden not in src
    assert client.post('/api/desk/publish', json={}).status_code == 404

# -- voice seeding ------------------------------------------------------------
#
# The seed route dispatches an agent to characterise how the human writes. What
# is worth pinning here is the REFUSALS, because each one protects something the
# store cannot: an unknown voice, a corpus too thin to characterise, and the
# projects dir that computes the incognito exclusion.

def test_seeding_an_unknown_voice_is_a_404(client):
    assert client.post('/api/desk/voices/nobody/seed', json={}).status_code == 404


def test_a_thin_corpus_is_a_real_answer_not_an_error(client, tmp_path, monkeypatch):
    """A fresh install has nothing to read. Saying so beats characterising a
    voice off four messages and labelling the result 'learned'."""
    monkeypatch.setattr(desk_routes._seed, 'collect', lambda *a, **k: ['too thin'])
    r = client.post('/api/desk/voices/personal/seed', json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True and body['seeded'] is False
    assert str(desk_routes._seed.MIN_SAMPLES) in body['reason']


def test_seeding_dispatches_with_the_sample_and_never_into_a_pseudo_project(
        client, monkeypatch):
    """Pseudo-projects are skipped — dispatching INTO `_incognito` to
    characterise a voice is the one place this must never run."""
    monkeypatch.setattr(desk_routes._seed, 'collect',
                        lambda *a, **k: ['m%d and some words' % i for i in range(80)])
    monkeypatch.setattr(desk_routes, 'load_projects',
                        lambda: [{'id': '_incognito'}, {'id': 'real_project'}])
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid})
    _desk.upsert_presence('real_project', {'desk_agent': 'global:claydo'})
    seen = {}

    def _dispatch(pid, brief, _x, **kw):
        seen['pid'] = pid
        seen['brief'] = brief
        seen['character'] = kw.get('character')
        return 'sess-1'
    monkeypatch.setattr(desk_routes, 'dispatch_agent', _dispatch)

    r = client.post('/api/desk/voices/personal/seed', json={})
    assert r.status_code == 202
    assert r.get_json()['samples'] == 80
    assert seen['pid'] == 'real_project'
    assert seen['character'] == 'global:claydo'
    assert 'DESCRIBE, DO NOT QUOTE' in seen['brief']


# -- R1-A: backend agent of choice (MC-977 IA revision 2 §5.3) ----------------
#
# `presence.desk_agent` (or a campaign's own `how.agent`) replaces the
# hardcoded `global:social-media-strategist` at every dispatch site. A project
# that never picked anyone gets a 409 naming the project, never a silent
# default — see `desk_routes._desk_agent_ref` / `_pick_agent_error`.

def _dispatch_capture(monkeypatch, seen):
    def _dispatch(pid, brief, _x, **kw):
        seen['pid'] = pid
        seen['brief'] = brief
        seen['character'] = kw.get('character')
        return 'sess-1'
    monkeypatch.setattr(desk_routes, 'dispatch_agent', _dispatch)


def test_draft_dispatches_the_projects_picked_agent(client, monkeypatch):
    sig = client.post('/api/desk/signals', json={
        'project_id': 'proj-1', 'kind': 'release',
        'summary': 'Shipped the Desk'}).get_json()
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj One'})
    _desk.upsert_presence('proj-1', {'desk_agent': 'global:claydo'})
    seen = {}
    _dispatch_capture(monkeypatch, seen)

    r = client.post('/api/desk/draft', json={'signal_id': sig['id'], 'voice': 'personal'})
    assert r.status_code == 202
    assert seen['character'] == 'global:claydo'


def test_draft_without_a_picked_agent_is_409_pick_agent(client, monkeypatch):
    sig = client.post('/api/desk/signals', json={
        'project_id': 'proj-2', 'kind': 'release',
        'summary': 'Shipped something else'}).get_json()
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Two'})
    seen = {}
    _dispatch_capture(monkeypatch, seen)

    r = client.post('/api/desk/draft', json={'signal_id': sig['id'], 'voice': 'personal'})
    assert r.status_code == 409
    body = r.get_json()
    assert body['pick_agent'] is True
    assert body['project_id'] == 'proj-2'
    assert 'Proj Two' in body['error']
    assert seen == {}  # never reached dispatch


def test_draft_falls_back_to_the_campaigns_agent_with_no_project_presence(client, monkeypatch):
    cid = client.post('/api/desk/campaigns', json={
        'title': 'a', 'thesis': 'th', 'how': {'agent': 'global:claydo'}}).get_json()['id']
    sig = client.post('/api/desk/signals', json={
        'project_id': 'proj-3', 'kind': 'release',
        'summary': 'Shipped a thing'}).get_json()
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Three'})
    seen = {}
    _dispatch_capture(monkeypatch, seen)

    r = client.post('/api/desk/draft', json={
        'signal_id': sig['id'], 'voice': 'personal', 'campaign_id': cid})
    assert r.status_code == 202
    assert seen['character'] == 'global:claydo'


def test_triage_resolves_the_running_campaigns_agent(client, monkeypatch):
    client.post('/api/desk/signals', json={
        'project_id': 'proj-4', 'kind': 'release', 'summary': 'Shipped triage-worthy work'})
    cid = client.post('/api/desk/campaigns', json={
        'title': 'a', 'thesis': 'th', 'how': {'agent': 'global:claydo'}}).get_json()['id']
    client.patch(f'/api/desk/campaigns/{cid}', json={'state': 'running'})
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Four'})
    seen = {}
    _dispatch_capture(monkeypatch, seen)

    r = client.post('/api/desk/triage', json={'project_id': 'proj-4'})
    assert r.status_code == 202
    assert seen['character'] == 'global:claydo'


def test_triage_without_a_picked_agent_is_409_pick_agent(client, monkeypatch):
    client.post('/api/desk/signals', json={
        'project_id': 'proj-4b', 'kind': 'release', 'summary': 'Shipped more triage-worthy work'})
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Four B'})
    r = client.post('/api/desk/triage', json={'project_id': 'proj-4b'})
    assert r.status_code == 409
    assert r.get_json()['pick_agent'] is True


def test_accept_proposal_resolves_the_picked_agent(client, monkeypatch):
    sig = client.post('/api/desk/signals', json={
        'project_id': 'proj-5', 'kind': 'release',
        'summary': 'Shipped a proposal-worthy thing'}).get_json()
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Five'})
    _desk.upsert_presence('proj-5', {'desk_agent': 'global:claydo'})
    prop = client.post('/api/desk/proposals', json={
        'signal_id': sig['id'], 'why': 'worth saying', 'voice': 'personal'}).get_json()
    seen = {}
    _dispatch_capture(monkeypatch, seen)

    r = client.post(f'/api/desk/proposals/{prop["id"]}/accept')
    assert r.status_code == 202
    assert seen['character'] == 'global:claydo'


def test_accept_proposal_without_a_picked_agent_is_409_pick_agent(client, monkeypatch):
    sig = client.post('/api/desk/signals', json={
        'project_id': 'proj-5b', 'kind': 'release',
        'summary': 'Shipped another proposal-worthy thing'}).get_json()
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Five B'})
    prop = client.post('/api/desk/proposals', json={
        'signal_id': sig['id'], 'why': 'worth saying', 'voice': 'personal'}).get_json()

    r = client.post(f'/api/desk/proposals/{prop["id"]}/accept')
    assert r.status_code == 409
    assert r.get_json()['pick_agent'] is True


def test_dispatch_rework_refuses_without_a_picked_agent(client, monkeypatch):
    sig = client.post('/api/desk/signals', json={
        'project_id': 'proj-6', 'kind': 'release',
        'summary': 'Shipped a rework-worthy thing'}).get_json()
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Six'})
    item = {'signal_id': sig['id'], 'voice': 'personal', 'platform': 'x'}

    result = desk_routes.dispatch_rework('proj-6', item, 'make it punchier')
    assert result['dispatched'] is False
    assert 'pick' in result['reason'].lower()


def test_dispatch_rework_resolves_the_picked_agent(client, monkeypatch):
    sig = client.post('/api/desk/signals', json={
        'project_id': 'proj-7', 'kind': 'release',
        'summary': 'Shipped a rework-worthy thing'}).get_json()
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Seven'})
    _desk.upsert_presence('proj-7', {'desk_agent': 'global:claydo'})
    item = {'signal_id': sig['id'], 'voice': 'personal', 'platform': 'x'}
    seen = {}
    _dispatch_capture(monkeypatch, seen)

    result = desk_routes.dispatch_rework('proj-7', item, 'make it punchier')
    assert result['dispatched'] is True
    assert seen['character'] == 'global:claydo'


def test_no_dispatch_site_hardcodes_the_old_default_agent():
    """R1-A's acceptance line: no route string names the old default."""
    src = Path(desk_routes.__file__).read_text(encoding='utf-8')
    assert "'global:social-media-strategist'" not in src
    assert '"global:social-media-strategist"' not in src


# -- presence: the route the 409 above needs the UI to be able to call -------
# (Dave's follow-up: R1-A made every draft/triage/accept 409 with pick_agent,
# but nothing could clear it — `upsert_presence` had no HTTP route.)

def test_get_presence_on_an_unset_project_is_not_a_404(client):
    r = client.get('/api/desk/presence/proj-9')
    assert r.status_code == 200
    body = r.get_json()
    assert body['project_id'] == 'proj-9'
    assert body['desk_agent'] is None


def test_patch_presence_sets_a_valid_agent(client, monkeypatch):
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Nine'})
    r = client.patch('/api/desk/presence/proj-9', json={'desk_agent': 'global:claydo'})
    assert r.status_code == 200
    assert r.get_json()['desk_agent'] == 'global:claydo'

    r = client.get('/api/desk/presence/proj-9')
    assert r.get_json()['desk_agent'] == 'global:claydo'


def test_patch_presence_rejects_an_unknown_agent(client, monkeypatch):
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Nine'})
    r = client.patch('/api/desk/presence/proj-9', json={'desk_agent': 'global:no-such-agent'})
    assert r.status_code == 400
    assert 'no-such-agent' in r.get_json()['error']
    assert _desk.get_presence('proj-9') is None


def test_patch_presence_rejects_a_malformed_ref(client, monkeypatch):
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Nine'})
    r = client.patch('/api/desk/presence/proj-9', json={'desk_agent': 'claydo'})
    assert r.status_code == 400


def test_patch_presence_unknown_project_is_404(client, monkeypatch):
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: None)
    r = client.patch('/api/desk/presence/ghost', json={'desk_agent': 'global:claydo'})
    assert r.status_code == 404


def test_patch_presence_can_clear_the_agent(client, monkeypatch):
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'Proj Nine'})
    client.patch('/api/desk/presence/proj-9', json={'desk_agent': 'global:claydo'})
    r = client.patch('/api/desk/presence/proj-9', json={'desk_agent': None})
    assert r.status_code == 200
    assert r.get_json()['desk_agent'] is None


def test_presence_patch_refuses_keys_other_than_desk_agent(client, monkeypatch):
    """The presence route writes the agent pick only — never `budget`, whose
    earmark bounds are enforced on the campaign/presence budget paths."""
    monkeypatch.setattr(desk_routes, 'load_project', lambda pid: {'id': pid, 'name': 'P'})
    r = client.patch('/api/desk/presence/proj-x', json={'budget': {'amount': 999}})
    assert r.status_code == 400
    assert (_desk.get_presence('proj-x') or {}).get('budget', {}).get('amount') != 999


# -- playbook / outcome learning loop (§10, MC-977 R1-L) ----------------------

@pytest.fixture
def unattended(monkeypatch):
    """Simulate a live, running non-manual agent session — the same shape
    tests/test_settings_routes_unattended_gate.py uses for `is_unattended_caller`.
    The Flask test client sends no Origin header, so this is not short-circuited."""
    from mc.state import agent_sessions
    snapshot = dict(agent_sessions)
    agent_sessions.clear()
    agent_sessions['dispatch-1'] = {'status': 'running', 'trigger_type': 'dispatch'}
    yield
    agent_sessions.clear()
    agent_sessions.update(snapshot)


def _propose(project_id='mc'):
    return _desk.propose_finding(
        project_id=project_id, dimension='slot', arms={'a': 'Tue/Thu 08-10', 'b': 'other'},
        account='x:ron', metric='clicks', effect={'ratio': 2.1, 'direction': 'a>b'},
        evidence=[{'campaign_id': 'c1', 'term': 't1'}], n_total=41, confidence='medium')


def test_findings_list_and_get(client):
    fid = _propose()
    rows = client.get('/api/desk/findings?project_id=mc').get_json()
    assert len(rows) == 1 and rows[0]['id'] == fid
    assert client.get(f'/api/desk/findings/{fid}').status_code == 200
    assert client.get('/api/desk/findings/nope').status_code == 404


def test_confirm_reject_and_undo_reject_routes(client):
    fid = _propose()
    r = client.post(f'/api/desk/findings/{fid}/confirm', json={'decided_by': 'ron'})
    assert r.status_code == 200 and r.get_json()['state'] == 'confirmed'

    fid2 = _propose()
    r2 = client.post(f'/api/desk/findings/{fid2}/reject', json={'decided_by': 'ron'})
    assert r2.status_code == 200 and r2.get_json()['state'] == 'rejected'

    r3 = client.post(f'/api/desk/findings/{fid2}/undo-reject')
    assert r3.status_code == 200 and r3.get_json()['state'] == 'proposed'

    assert client.post('/api/desk/findings/nope/confirm', json={}).status_code == 404


def test_dont_suggest_again_route(client):
    fid = _propose()
    r = client.post(f'/api/desk/findings/{fid}/dont-suggest-again', json={'decided_by': 'ron'})
    assert r.status_code == 200 and r.get_json()['state'] == 'rejected'


def test_finding_routes_refuse_a_field_naming_a_bound(client):
    """§10.5.1: the finding schema has no field that can name an approval
    bound — structural, not a wording check, so any of these keys 400s
    whatever their value."""
    fid = _propose()
    for field in ('cadence', 'budget', 'accounts', 'approval', 'end', 'post_cap'):
        r = client.post(f'/api/desk/findings/{fid}/confirm', json={field: 'anything'})
        assert r.status_code == 400, f'{field!r} must be refused'
    assert _desk.get_finding(fid)['state'] == 'proposed'


def test_finding_state_routes_refuse_an_unattended_caller(client, unattended):
    fid = _propose()
    assert client.post(f'/api/desk/findings/{fid}/confirm', json={}).status_code == 403
    assert client.post(f'/api/desk/findings/{fid}/reject', json={}).status_code == 403
    assert client.post(f'/api/desk/findings/{fid}/dont-suggest-again', json={}).status_code == 403
    assert client.post(f'/api/desk/findings/{fid}/undo-reject').status_code == 403
    # unaffected by the caller: read-only listing still works.
    assert client.get('/api/desk/findings?project_id=mc').status_code == 200
    assert _desk.get_finding(fid)['state'] == 'proposed'


def test_retro_route_computes_too_few_posts_verdict_and_proposes_nothing(client):
    r = client.post('/api/desk/retro', json={
        'project_id': 'mc', 'metric': 'clicks',
        'dimension_arms': {
            'format': {'a': [10] * 6, 'b': [10] * 4},
        }})
    assert r.status_code == 200
    body = r.get_json()
    assert body['dimensions']['format']['verdict'] == 'too_few_posts'
    assert body['proposed'] == []


def test_retro_route_proposes_a_finding_stamped_unattended(client):
    """§10.5.2: a retro run always stamps `origin:'unattended'` on what it
    proposes — code computed it, no human judged it yet, whatever calls this
    route."""
    hi = [{'value': 30, 'campaign_id': f'c{i}'} for i in range(30)]
    lo = [{'value': 10, 'campaign_id': f'd{i}'} for i in range(30)]
    r = client.post('/api/desk/retro', json={
        'project_id': 'mc', 'account': 'x:ron', 'metric': 'clicks',
        'dimension_arms': {'slot': {
            'a': hi, 'b': lo, 'a_label': 'morning', 'b_label': 'evening',
            'evidence': [{'campaign_id': f'c{i}', 'term': 't1'} for i in range(30)],
        }}})
    assert r.status_code == 200
    body = r.get_json()
    assert body['dimensions']['slot']['verdict'] == 'finding'
    assert len(body['proposed']) == 1
    f = _desk.get_finding(body['proposed'][0])
    assert f['state'] == 'proposed' and f['origin'] == 'unattended'
    assert f['arms'] == {'a': 'morning', 'b': 'evening'}


def test_retro_interim_never_proposes_a_finding(client):
    """§10.1: 'Run retro now' shows numbers only and never proposes findings."""
    high_a = [{'value': v, 'campaign_id': f'c{i}'} for i, v in enumerate([30] * 30)]
    high_b = [{'value': v, 'campaign_id': f'd{i}'} for i, v in enumerate([10] * 30)]
    r = client.post('/api/desk/retro', json={
        'project_id': 'mc', 'interim': True,
        'dimension_arms': {'format': {'a': high_a, 'b': high_b}}})
    assert r.status_code == 200
    assert r.get_json()['proposed'] == []
    assert client.get('/api/desk/findings?project_id=mc').get_json() == []


def test_retro_route_validation(client):
    assert client.post('/api/desk/retro', json={}).status_code == 400
    assert client.post('/api/desk/retro', json={'project_id': 'mc'}).status_code == 400
