"""The Desk — drafting: the brief, the dispatch, and the learning loop.

What these guard, worst-to-lose first:

  1. THE LEARNING LOOP ACTUALLY CLOSES. Editing a draft's body teaches that
     draft's voice. This is the differentiator the 2026-09-09 field scan could
     not verify in any surveyed product, and it lives in a `try/except` inside
     an unrelated route — exactly the kind of thing a refactor drops silently.
  2. THE DESK CANNOT PUBLISH AND CANNOT ACQUIRE A PRIVATE DISPATCH. Drafts land
     `pending`; dispatch goes through the same internal call the normal endpoint
     uses, with `strict_character` set so an unresolvable Posy REFUSES rather
     than silently writing in the default agent's voice.
  3. A SIGNAL IS DRAFTED FROM ONCE. A second attempt is a 409, not a second
     draft — duplicate drafts off one event is how a queue becomes noise.
  4. THE BRIEF CARRIES ITS FACTS. The ref (so a claim is checkable), the verbatim
     voice rewrites, the prior-post warning, and the platform's real cost.
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
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'desk_signals.jsonl')
    return desk


# ── the brief ────────────────────────────────────────────────────────────────

def _signal(store, **kw):
    return store.append_signal(
        kw.pop('project_id', 'p'), kw.pop('kind', 'release'),
        kw.pop('summary', 'Shipped drag-to-hire, now live'),
        ref=kw.pop('ref', 'abc123'), **kw)


def test_voice_implies_platform(store):
    assert desk_brief.platform_for('personal') == 'x'
    assert desk_brief.platform_for('product') == 'linkedin'


def test_brief_carries_the_ref_so_a_claim_is_checkable(store):
    b = desk_brief.build_brief(_signal(store), voice='personal')
    assert 'abc123' in b
    assert 'Shipped drag-to-hire' in b


def test_brief_states_what_a_link_costs_on_x(store):
    """A writer who does not know a link costs 13x will add one every time."""
    b = desk_brief.build_brief(_signal(store), voice='personal')
    assert '$0.015' in b and '$0.200' in b


def test_brief_warns_linkedin_about_slop_suppression(store):
    b = desk_brief.build_brief(_signal(store), voice='product')
    assert 'Share on LinkedIn' in b
    assert '40%' in b, 'LinkedIn suppresses classifier-flagged AI content'


def test_brief_carries_verbatim_rewrites_not_a_summary(store):
    store.record_edit('personal',
                      before='We are thrilled to leverage synergies',
                      after='I rewrote the scheduler and it came out slower.')
    b = desk_brief.build_brief(_signal(store), voice='personal')
    assert 'I rewrote the scheduler and it came out slower.' in b


def test_brief_names_the_earlier_post_when_we_already_said_it(store):
    store.record_published(
        platform='x', voice='personal',
        body='Shipped drag-to-hire, now live and it took three days')
    b = desk_brief.build_brief(_signal(store), voice='personal')
    assert 'ALREADY SAID' in b
    assert 'not worth a second post' in b


def test_brief_argues_the_campaign_thesis_when_there_is_one(store):
    camp = store.create_campaign('Agent persistence', 'Clayrune keeps agents alive')
    b = desk_brief.build_brief(_signal(store), voice='personal', campaign=camp)
    assert 'Clayrune keeps agents alive' in b
    assert 'Do not merely report the event' in b


def test_brief_asks_for_a_real_visual_and_how_to_attach_it(store):
    """The dead `media` field (project_routes.py) is dead because no brief ever
    asked for it. The brief must state the need explicitly, forbid inventing
    one, and tell the writer the exact `media` key to POST.
    """
    b = desk_brief.build_brief(_signal(store), voice='personal')
    assert 'screenshot' in b
    assert 'Do NOT invent, describe, or ask for a generated image' in b
    assert '"media":' in b


def test_brief_carries_the_campaigns_visual_requirement(store):
    camp = store.create_campaign('t', 'thesis', visual='a screenshot of the new modal')
    b = desk_brief.build_brief(_signal(store), voice='personal', campaign=camp)
    assert 'a screenshot of the new modal' in b


def test_brief_demands_a_teaching_block_and_forbids_publishing(store):
    b = desk_brief.build_brief(_signal(store), voice='personal')
    assert 'teaching` is REQUIRED' in b
    assert 'You do not publish and you cannot' in b
    assert 'PENDING' in b


def test_brief_permits_saying_nothing(store):
    """A period with nothing worth saying produces nothing — a requirement."""
    b = desk_brief.build_brief(_signal(store), voice='personal')
    assert 'post nothing' in b


def test_brief_rejects_an_unknown_voice(store):
    with pytest.raises(ValueError):
        desk_brief.build_brief(_signal(store), voice='marketing')


# ── platform rules reach the brief ──────────────────────────────────────────
#
# THE BUG THIS SECTION GUARDS. `PLATFORM_NOTES.get(platform, '')` used to hand
# the writer an empty string for any platform that was not 'x' or 'linkedin'.
# Measured 2026-09-10 on clayrune_website's queue: an 837-char facebook draft
# and a 2236-char discord draft, both briefed with nothing. The one outcome
# this section forbids is a repeat of that silent empty string.

def test_brief_says_so_when_a_platform_has_no_rules(store):
    """The required behaviour: SAY SO in the brief text, not via a flag a
    caller has to remember to check."""
    store.create_voice('fb_test', platform='facebook')
    b = desk_brief.build_brief(_signal(store), voice='fb_test')
    assert 'NO RULES ARE SET' in b
    assert 'facebook' in b
    assert 'keep' in b.lower() and 'short' in b.lower()


def test_rules_set_through_the_store_reach_the_brief(store):
    store.create_voice('fb_test', platform='facebook')
    store.update_platform_rules('facebook', {
        'text': 'Never exceed one paragraph; links are fine.', 'char_limit': 500})
    b = desk_brief.build_brief(_signal(store), voice='fb_test')
    assert 'Never exceed one paragraph' in b
    assert '500' in b
    assert 'NO RULES ARE SET' not in b


# ── the draft route ──────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    calls = []

    def fake_dispatch(project_id, task, resume_id, **kw):
        calls.append({'project_id': project_id, 'task': task, **kw})
        return 'sess-123'

    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: [{'id': 'p', 'name': 'Proj', 'social_pending_count': 0}],
        load_project_fn=lambda pid: {'id': 'p', 'name': 'Proj'} if pid == 'p' else None,
        dispatch_fn=fake_dispatch,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    c = app.test_client()
    c.dispatch_calls = calls
    return c


def _post_signal(client):
    return client.post('/api/desk/signals', json={
        'project_id': 'p', 'kind': 'release',
        'summary': 'Shipped drag-to-hire, now live'}).get_json()


def test_draft_dispatches_posy_not_the_default_agent(client):
    sig = _post_signal(client)
    r = client.post('/api/desk/draft', json={'signal_id': sig['id'], 'voice': 'personal'})
    assert r.status_code == 202
    assert r.get_json()['session_id'] == 'sess-123'

    call = client.dispatch_calls[0]
    assert call['character'] == 'global:social-media-strategist'
    assert call['strict_character'] is True, \
        'an unresolvable Posy must refuse, not write in the default voice'
    assert call['source'] == 'agent'
    assert '$0.200' in call['task'], 'the brief itself was handed over'


def test_platform_follows_the_voice(client):
    sig = _post_signal(client)
    r = client.post('/api/desk/draft', json={'signal_id': sig['id'], 'voice': 'product'})
    assert r.get_json()['platform'] == 'linkedin'


def test_a_signal_is_drafted_from_once(client):
    sig = _post_signal(client)
    assert client.post('/api/desk/draft', json={'signal_id': sig['id']}).status_code == 202
    desk.mark_signal_consumed(sig['id'], 'draft-1')
    r = client.post('/api/desk/draft', json={'signal_id': sig['id']})
    assert r.status_code == 409
    assert r.get_json()['consumed_by'] == 'draft-1'
    assert len(client.dispatch_calls) == 1


def test_draft_validation(client):
    assert client.post('/api/desk/draft', json={}).status_code == 400
    assert client.post('/api/desk/draft', json={'signal_id': 'nope'}).status_code == 404
    sig = _post_signal(client)
    assert client.post('/api/desk/draft',
                       json={'signal_id': sig['id'], 'voice': 'marketing'}).status_code == 400
    assert client.post('/api/desk/draft',
                       json={'signal_id': sig['id'], 'campaign_id': 'nope'}).status_code == 404


# ── platform rules routes ────────────────────────────────────────────────────

def test_platform_rules_crud_reaches_the_draft_brief(client):
    """Rules set through the API must reach build_brief for a draft on that
    platform — the whole point of moving them out of a hardcoded dict."""
    r = client.get('/api/desk/platforms/facebook')
    assert r.status_code == 200
    assert r.get_json()['configured'] is False

    r = client.patch('/api/desk/platforms/facebook',
                     json={'text': 'Keep it to one paragraph.', 'char_limit': 500})
    assert r.status_code == 200
    assert r.get_json()['char_limit'] == 500

    assert desk.create_voice('fb_route_test', platform='facebook')
    sig = _post_signal(client)
    r = client.post('/api/desk/draft', json={'signal_id': sig['id'], 'voice': 'fb_route_test'})
    assert r.status_code == 202
    task = client.dispatch_calls[-1]['task']
    assert 'Keep it to one paragraph.' in task, \
        'the brief did not pick up the rules just set through the API'


def test_platform_rules_list_and_delete(client):
    assert {r['name'] for r in client.get('/api/desk/platforms').get_json()} == {'x', 'linkedin'}
    client.patch('/api/desk/platforms/discord', json={'text': 'be concise'})
    assert 'discord' in {r['name'] for r in client.get('/api/desk/platforms').get_json()}
    assert client.delete('/api/desk/platforms/discord').status_code == 200
    assert client.delete('/api/desk/platforms/discord').status_code == 404


def test_unwired_dispatch_says_so_rather_than_pretending(tmp_path):
    """Reporting success without dispatching is the substitution failure the
    agent rules forbid: a result the caller believes came from their request."""
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_project_fn=lambda pid: {'id': 'p', 'name': 'Proj'},
        store_path=tmp_path / 'd.json', signals_path=tmp_path / 'd.jsonl')
    desk_routes.dispatch_agent = None
    app.register_blueprint(desk_routes.bp)
    c = app.test_client()
    sig = c.post('/api/desk/signals', json={'project_id': 'p', 'summary': 'x'}).get_json()
    r = c.post('/api/desk/draft', json={'signal_id': sig['id']})
    assert r.status_code == 503
    assert 'brief' in r.get_json(), 'it hands back the brief rather than faking a draft'


def test_brief_preview_dispatches_nothing(client):
    sig = _post_signal(client)
    r = client.post('/api/desk/brief', json={'signal_id': sig['id']})
    assert r.status_code == 200
    assert 'PLATFORM: x' in r.get_json()['brief']
    assert client.dispatch_calls == [], 'previewing must not start an agent'


def test_there_is_no_publish_route_on_the_draft_path():
    src = (PROJECT_ROOT / 'mc' / 'desk_brief.py').read_text(encoding='utf-8')
    for forbidden in ('requests.', 'urllib.request', 'httpx.', 'tweepy'):
        assert forbidden not in src


# ── the learning loop, where it actually lives ───────────────────────────────

def test_editing_a_draft_body_teaches_its_voice(store, monkeypatch, tmp_path):
    """The loop closes in project_routes.update_social_queue_item.

    It lives in a try/except inside an unrelated route, which is precisely why
    it needs a test that fails loudly if a refactor drops it.
    """
    from mc.blueprints import project_routes

    saved = {}
    project = {'id': 'p', 'social_queue': [{
        'id': 'd1', 'body': 'Excited to announce our game-changing feature!',
        'voice': 'personal', 'platform': 'x', 'status': 'pending',
    }]}
    monkeypatch.setattr(project_routes, 'load_project', lambda pid: project)
    monkeypatch.setattr(project_routes, 'save_project',
                        lambda pid, d: saved.update({'d': d}))

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(project_routes.bp)
    r = app.test_client().patch('/api/project/p/social/queue/d1', json={
        'body': 'Shipped drag-to-hire. Three days, two rewrites.'})
    assert r.status_code == 200

    rewrites = store.get_voice('personal')['rewrites']
    assert len(rewrites) == 1
    assert rewrites[0]['before'].startswith('Excited to announce')
    assert rewrites[0]['after'].startswith('Shipped drag-to-hire')
    assert rewrites[0]['draft_id'] == 'd1'


def test_a_draft_with_no_voice_teaches_nothing(store, monkeypatch):
    """Human-authored and legacy drafts carry no voice — they must not be
    attributed to one, or the profile learns from the wrong writer."""
    from mc.blueprints import project_routes
    project = {'id': 'p', 'social_queue': [
        {'id': 'd1', 'body': 'old text here', 'status': 'pending'}]}
    monkeypatch.setattr(project_routes, 'load_project', lambda pid: project)
    monkeypatch.setattr(project_routes, 'save_project', lambda pid, d: None)

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(project_routes.bp)
    app.test_client().patch('/api/project/p/social/queue/d1',
                            json={'body': 'entirely different replacement text'})
    assert store.get_voice('personal')['rewrites'] == []
    assert store.get_voice('product')['rewrites'] == []


def test_a_voice_store_failure_never_costs_the_edit(store, monkeypatch):
    """Best-effort on purpose: losing Ron's edit to save a lesson is backwards."""
    from mc.blueprints import project_routes
    saved = {}
    project = {'id': 'p', 'social_queue': [
        {'id': 'd1', 'body': 'before text', 'voice': 'personal', 'status': 'pending'}]}
    monkeypatch.setattr(project_routes, 'load_project', lambda pid: project)
    monkeypatch.setattr(project_routes, 'save_project',
                        lambda pid, d: saved.update({'d': d}))

    def boom(*a, **kw):
        raise RuntimeError('voice store on fire')
    monkeypatch.setattr(desk, 'record_edit', boom)

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(project_routes.bp)
    r = app.test_client().patch('/api/project/p/social/queue/d1',
                                json={'body': 'the edit Ron actually made'})
    assert r.status_code == 200
    assert r.get_json()['item']['body'] == 'the edit Ron actually made'


def test_a_desk_draft_keeps_its_signal_and_teaching(monkeypatch):
    """The spec's two additions to the queue shape. Without signal_id a claim is
    untraceable, and untraceable claims lose trust in the whole system."""
    from mc.blueprints import project_routes
    project = {'id': 'p'}
    monkeypatch.setattr(project_routes, 'load_project', lambda pid: project)
    monkeypatch.setattr(project_routes, 'save_project', lambda pid, d: None)

    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(project_routes.bp)
    r = app.test_client().post('/api/project/p/social/queue', json={
        'body': 'Shipped drag-to-hire.', 'platform': 'x', 'voice': 'personal',
        'signal_id': 'sig-1', 'teaching': 'Ships-beat-promises angle; X rewards it.'})
    item = r.get_json()['item']
    assert item['signal_id'] == 'sig-1'
    assert item['teaching'].startswith('Ships-beat-promises')
    assert item['voice'] == 'personal'
    assert item['status'] == 'pending', 'a Desk draft always lands pending'
