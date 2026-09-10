"""Triage — Posy decides what is worth saying, the human approves the SELECTION.

WHY THIS EXISTS. Until 2026-09-10 a keyword regex (`desk.score_signal`) was the
only thing deciding what deserved a post, and the human read every row and
picked. With 120 signals in the feed that does not scale, and the score cannot
explain itself. Ron said twice that he expected the writer to propose; the spec
agrees (§2 Incubator: "scores signal into candidate stories and DISCARDS MOST OF
IT"). The regex was in the wrong seat.

The properties worth guarding:

  1. A DISMISSAL IS LATCHED ON THE SIGNAL, not the proposal id. A "no" that a
     fresh proposal id can walk around is not a no — that is exactly how
     `preference-1ba8d678` came back from the dead (CLAUDE.md, learning rails).
  2. ACCEPTING IS THE INSTRUCTION TO WRITE, and it still cannot publish. Two
     gates, asked in order: "worth saying?" then the Queue's "said right?".
  3. A PROPOSAL MUST CITE A REAL SIGNAL. The human checks the claim against it;
     an invented id makes that impossible.
  4. THE BRIEF MAKES DISCARDING THE SUCCESSFUL OUTCOME. An agent asked for "the
     best five" will always find five.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk, desk_brief  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402


@pytest.fixture
def client(tmp_path):
    calls = []

    def fake_dispatch(project_id, task, resume_id, **kw):
        calls.append({'project_id': project_id, 'task': task, **kw})
        return f'sess-{len(calls)}'

    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: [{'id': 'p', 'name': 'Proj'}],
        load_project_fn=lambda pid: {'id': 'p', 'name': 'Proj'},
        dispatch_fn=fake_dispatch,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    c = app.test_client()
    c.dispatch_calls = calls
    return c


def _sig(client, summary='Shipped drag-to-hire, now live'):
    return client.post('/api/desk/signals', json={
        'project_id': 'p', 'kind': 'release', 'summary': summary}).get_json()


# ── the triage pass ──────────────────────────────────────────────────────────

def test_triage_dispatches_posy_with_the_feed(client):
    for i in range(3):
        _sig(client, f'Shipped thing number {i}')
    r = client.post('/api/desk/triage', json={})
    assert r.status_code == 202
    assert r.get_json()['considering'] == 3

    task = client.dispatch_calls[0]['task']
    assert client.dispatch_calls[0]['character'] == 'global:social-media-strategist'
    assert client.dispatch_calls[0]['strict_character'] is True
    assert 'YOU ARE NOT WRITING POSTS IN THIS PASS' in task


def test_the_brief_makes_discarding_the_successful_outcome(client):
    _sig(client)
    client.post('/api/desk/triage', json={})
    task = client.dispatch_calls[0]['task']
    assert 'Picking FEWER is the better answer' in task
    assert 'Do not fill a quota' in task
    assert 'propose nothing' in task


def test_triage_offers_the_voices_and_their_platforms(client):
    _sig(client)
    client.post('/api/desk/triage', json={})
    task = client.dispatch_calls[0]['task']
    assert 'personal -> posts to x' in task
    assert 'product -> posts to linkedin' in task


def test_triage_carries_the_running_campaign(client):
    _sig(client)
    c = client.post('/api/desk/campaigns', json={
        'title': 'Agent persistence', 'thesis': 'Clayrune keeps agents alive'}).get_json()
    client.patch(f"/api/desk/campaigns/{c['id']}", json={'state': 'running'})
    client.post('/api/desk/triage', json={})
    assert 'Clayrune keeps agents alive' in client.dispatch_calls[0]['task']


def test_triage_with_an_empty_feed_says_so(client):
    r = client.post('/api/desk/triage', json={})
    assert r.status_code == 200 and r.get_json()['nothing_to_triage'] is True
    assert client.dispatch_calls == []


def test_triage_needs_a_voice(client):
    _sig(client)
    assert desk.voice_names()  # materialise the seed before clearing it
    desk.delete_voice('personal')
    desk.delete_voice('product')
    assert client.post('/api/desk/triage', json={}).status_code == 409


# ── proposals ────────────────────────────────────────────────────────────────

def test_a_proposal_must_cite_a_real_signal(client):
    r = client.post('/api/desk/proposals', json={
        'signal_id': 'invented', 'voice': 'personal', 'why': 'looks good'})
    assert r.status_code == 404, 'the human checks the claim against the signal'


def test_proposal_validation(client):
    s = _sig(client)
    assert client.post('/api/desk/proposals', json={'why': 'x'}).status_code == 400
    assert client.post('/api/desk/proposals',
                       json={'signal_id': s['id']}).status_code == 400
    assert client.post('/api/desk/proposals', json={
        'signal_id': s['id'], 'why': 'x', 'voice': 'nope'}).status_code == 400


def test_a_signal_is_proposed_once(client):
    s = _sig(client)
    first = client.post('/api/desk/proposals', json={
        'signal_id': s['id'], 'voice': 'personal', 'why': 'ships beat promises'})
    assert first.status_code == 201
    second = client.post('/api/desk/proposals', json={
        'signal_id': s['id'], 'voice': 'product', 'why': 'again'})
    assert second.get_json()['skipped'] is True
    assert len(client.get('/api/desk/proposals').get_json()) == 1


def test_the_list_joins_the_signal_so_the_claim_is_checkable(client):
    s = _sig(client)
    client.post('/api/desk/proposals', json={
        'signal_id': s['id'], 'voice': 'personal', 'why': 'ships beat promises'})
    row = client.get('/api/desk/proposals').get_json()[0]
    assert row['signal']['summary'] == 'Shipped drag-to-hire, now live'


# ── the two gates ────────────────────────────────────────────────────────────

def test_accepting_is_the_instruction_to_write(client):
    s = _sig(client)
    p = client.post('/api/desk/proposals', json={
        'signal_id': s['id'], 'voice': 'personal', 'why': 'ships beat promises'}).get_json()

    r = client.post(f"/api/desk/proposals/{p['id']}/accept")
    assert r.status_code == 202
    assert r.get_json()['platform'] == 'x'

    # The SECOND dispatch is the draft brief, not another triage.
    draft = client.dispatch_calls[-1]['task']
    assert 'You are drafting ONE social post' in draft
    assert 'You do not publish and you cannot' in draft
    assert client.get('/api/desk/proposals').get_json() == [], 'no longer pending'


def test_a_proposal_is_accepted_once(client):
    s = _sig(client)
    p = client.post('/api/desk/proposals', json={
        'signal_id': s['id'], 'voice': 'personal', 'why': 'w'}).get_json()
    assert client.post(f"/api/desk/proposals/{p['id']}/accept").status_code == 202
    assert client.post(f"/api/desk/proposals/{p['id']}/accept").status_code == 409


def test_dismissal_is_latched_on_the_signal(client):
    """A no that a fresh proposal id can walk around is not a no."""
    s = _sig(client)
    p = client.post('/api/desk/proposals', json={
        'signal_id': s['id'], 'voice': 'personal', 'why': 'w'}).get_json()
    assert client.post(f"/api/desk/proposals/{p['id']}/dismiss").status_code == 200

    again = client.post('/api/desk/proposals', json={
        'signal_id': s['id'], 'voice': 'product', 'why': 'trying again'})
    assert again.get_json()['skipped'] is True
    assert desk.signal_is_ruled_on(s['id']) is True


def test_a_dismissed_signal_is_withheld_from_the_next_triage(client):
    s1, s2 = _sig(client, 'Shipped one'), _sig(client, 'Shipped two')
    p = client.post('/api/desk/proposals', json={
        'signal_id': s1['id'], 'voice': 'personal', 'why': 'w'}).get_json()
    client.post(f"/api/desk/proposals/{p['id']}/dismiss")

    client.post('/api/desk/triage', json={})
    task = client.dispatch_calls[-1]['task']
    assert s2['id'] in task
    assert s1['id'] not in task, 'a latched no must not be re-offered'


def test_accept_and_dismiss_on_a_missing_proposal(client):
    assert client.post('/api/desk/proposals/nope/accept').status_code == 404
    assert client.post('/api/desk/proposals/nope/dismiss').status_code == 404


def test_triage_cannot_publish():
    src = (PROJECT_ROOT / 'mc' / 'desk_brief.py').read_text(encoding='utf-8')
    fn = src[src.index('def build_triage_brief'):src.index('def build_brief')]
    for forbidden in ('requests.', 'urllib.request', 'httpx.'):
        assert forbidden not in fn
