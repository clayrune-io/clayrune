"""MC-1059 piece A (backlog b1e1b23c): ask at the moment of need.

A refused `read-digest` from a known agent session posts a card into THAT agent's chat; the
human answers it (Allow once / Always allow / Ignore) through a passcode-gated route. Reuses
the fixtures of test_browser_agent_read.py (fake browser, fake model), so nothing here touches
a network, a Chromium or a real model.
"""
from __future__ import annotations

from typing import Optional

import pytest

from tests.test_browser_agent_read import (  # noqa: F401  (fixtures are used by name)
    FEED, PASSCODE, _digest, _policy_file, env, wired)

HUMAN = {'Origin': 'http://localhost:5199'}      # what the dashboard's own fetch sends
SID = 'dispatch-1'


@pytest.fixture()
def asking(env):
    """The request route registered next to the digest route, a running dispatched agent that
    owns a chat, and a clean request store."""
    from mc import browser_agent_read_requests as store
    from mc.blueprints import browser_agent_read_request_routes as req_routes
    from mc.core import TimestampedLines
    from mc.state import agent_sessions
    store.reset_for_tests()
    env.client.application.register_blueprint(req_routes.bp)
    agent_sessions[SID] = {'status': 'running', 'trigger_type': 'dispatch', 'project_id': 'mission_control',
                           'character': {'agent_name': 'Dave'}, 'log_lines': TimestampedLines()}
    env.store, env.session = store, agent_sessions[SID]
    yield env
    store.reset_for_tests()


def _enable(env, domains, profile='li'):
    r = env.client.put(f'/api/browser/profiles/{profile}/agent-read', headers=HUMAN,
                       json={'enabled': True, 'domains': list(domains), 'passcode': PASSCODE})
    assert r.status_code == 200, r.get_json()


def _read(env, url=FEED, profile='li', sid: Optional[str] = SID):
    return _digest(env, url=url, profile=profile, session_id=sid)


def _cards(env):
    return [str(x) for x in env.session['log_lines'] if '[agent-read-request:' in str(x)]


def _rid(env):
    line = _cards(env)[-1]
    return line.split('[agent-read-request:')[1].rstrip(']')


def _answer_card(env, decision, rid=None, passcode: Optional[str] = PASSCODE, headers=HUMAN):
    body = {'decision': decision}
    if passcode is not None:
        body['passcode'] = passcode
    return env.client.post(f'/api/browser/agent-read/requests/{rid or _rid(env)}/decision',
                           json=body, headers=headers)


# ── the card is posted by the server, on the refusal ─────────────────────────

@pytest.mark.parametrize('enable_first', [False, True], ids=['profile_off', 'domain_off_list'])
def test_a_refused_read_posts_one_card_into_the_asking_agents_chat(asking, wired, enable_first):
    fb, _ = wired()
    if enable_first:
        _enable(asking, ['example.com'])
    r = _read(asking)
    body = r.get_json()
    assert r.status_code == 403 and body['error'] == ('domain_not_allowed' if enable_first else 'agent_read_off')
    assert len(_cards(asking)) == 1
    assert 'shown to the user' in body['guidance'] and 'later turn' in body['guidance']
    assert 'Do NOT' in body['guidance']                     # the no-fallback guidance is still there
    assert fb.launched == 0                                  # asking never opened a browser


def test_the_card_text_comes_from_the_server_record_not_the_transcript(asking, wired):
    wired()
    _read(asking)
    got = asking.client.get(f'/api/browser/agent-read/requests/{_rid(asking)}').get_json()
    assert got['agent_name'] == 'Dave' and got['profile'] == 'li' and got['domain'] == 'www.linkedin.com'
    assert got['state'] == 'pending'
    assert 'session_id' not in got and 'project_id' not in got      # no internal handle on the wire
    assert asking.client.get('/api/browser/agent-read/requests/nonexistent').status_code == 404


def test_at_most_one_card_per_session_profile_domain_in_ten_minutes(asking, wired, monkeypatch):
    wired()
    first = _read(asking).get_json()
    second = _read(asking).get_json()
    assert len(_cards(asking)) == 1
    assert 'already shown' in second['guidance'] and 'shown to the user' in first['guidance']
    # a different site, or the same site from another profile, is its own request
    _read(asking, url='https://x.com/home')
    assert len(_cards(asking)) == 2
    monkeypatch.setattr(asking.store, '_now', lambda: __import__('time').time() + 11 * 60)
    _read(asking)
    assert len(_cards(asking)) == 3                          # the window passed


def test_an_ignored_request_still_counts_against_the_rate_limit(asking, wired):
    wired()
    _read(asking)
    assert _answer_card(asking, 'ignore').status_code == 200
    _read(asking)
    assert len(_cards(asking)) == 1


def test_no_card_for_an_unknown_session_or_no_session(asking, wired):
    wired()
    for sid in ('no-such-session', None):
        r = _read(asking, sid=sid)
        assert r.status_code == 403 and 'shown to the user' not in r.get_json()['guidance']
    assert _cards(asking) == []


def test_no_card_for_a_profile_that_does_not_exist_or_a_non_https_address(asking, wired):
    wired()
    _read(asking, profile='nobody')
    _read(asking, url='http://www.linkedin.com/feed/')
    _read(asking, url='https://linkedin.com@evil.com/')
    assert _cards(asking) == []


def test_pending_cards_per_session_are_capped(asking, wired):
    wired()
    for i in range(8):
        _read(asking, url=f'https://site{i}.example.com/')
    assert len(_cards(asking)) == asking.store.MAX_PENDING_PER_SESSION


# ── answering: human only, passcode-gated ────────────────────────────────────

def test_allow_once_needs_the_passcode_and_an_unattended_caller_gets_nothing(asking, wired):
    wired()
    _read(asking)
    rid = _rid(asking)
    # an agent's own curl: no browser Origin, a live dispatched session
    r = _answer_card(asking, 'allow_once', rid, headers={})
    assert r.status_code == 403
    # the dashboard's fetch, but no passcode / a wrong one
    assert _answer_card(asking, 'allow_once', rid, passcode=None).status_code == 403
    assert _answer_card(asking, 'allow_once', rid, passcode='wrong-guess').status_code in (403, 429)
    assert asking.store.get_request(rid)['state'] == 'pending'
    assert asking.store.find_once('li', FEED, SID) is None


def test_always_allow_needs_the_passcode_and_stores_nothing_without_it(asking, wired):
    wired()
    _read(asking)
    rid = _rid(asking)
    assert _answer_card(asking, 'always', rid, passcode=None).status_code == 403
    assert _answer_card(asking, 'always', rid, headers={}).status_code == 403
    assert not _policy_file(asking).exists()


def test_a_forged_origin_with_no_passcode_grants_nothing(asking, wired):
    wired()
    _read(asking)
    for d in ('allow_once', 'always'):
        r = _answer_card(asking, d, passcode=None)            # Origin forged, no passcode
        assert r.status_code == 403 and r.get_json()['error'] in ('passcode_required', 'bad_passcode')
    assert asking.store.find_once('li', FEED, SID) is None and not _policy_file(asking).exists()


def test_ignore_stores_nothing_and_needs_no_passcode_but_not_from_an_agent(asking, wired):
    wired()
    _read(asking)
    rid = _rid(asking)
    assert _answer_card(asking, 'ignore', rid, passcode=None, headers={}).status_code == 403
    r = _answer_card(asking, 'ignore', rid, passcode=None)
    assert r.status_code == 200 and r.get_json()['state'] == 'ignored'
    assert asking.store.find_once('li', FEED, SID) is None and not _policy_file(asking).exists()
    assert _answer_card(asking, 'ignore', rid, passcode=None).status_code == 409     # already answered


def test_an_unknown_decision_or_request_is_refused(asking, wired):
    wired()
    _read(asking)
    assert _answer_card(asking, 'allow_forever').status_code == 400
    assert _answer_card(asking, 'ignore', rid='deadbeef0000').status_code == 404


def test_always_allow_adds_the_domain_to_the_profile_list_and_the_read_then_works(asking, wired):
    fb, fm = wired()
    _read(asking)
    r = _answer_card(asking, 'always')
    assert r.status_code == 200 and r.get_json()['state'] == 'always'
    got = asking.client.get('/api/browser/agent-read').get_json()['profiles']['li']
    assert got == {'enabled': True, 'domains': ['www.linkedin.com']}
    assert _read(asking).status_code == 200 and fb.launched == 1


# ── Allow once: a single-use pass for profile + domain + session ─────────────

def test_allow_once_lets_exactly_one_read_through_and_then_the_next_is_refused(asking, wired):
    fb, fm = wired()
    _read(asking)
    assert _answer_card(asking, 'allow_once').status_code == 200
    ok = _read(asking)
    assert ok.status_code == 200 and ok.get_json()['ok'] is True and len(fm.calls) == 1
    again = _read(asking)
    assert again.status_code == 403 and again.get_json()['error'] == 'agent_read_off'
    assert not _policy_file(asking).exists()                  # a pass never touches the list


def test_a_pass_is_spent_only_by_a_read_that_succeeded(asking, wired):
    fb, fm = wired(reply='not json at all')                   # the laundering step fails closed
    _read(asking)
    _answer_card(asking, 'allow_once')
    assert _read(asking).status_code == 502
    assert asking.store.find_once('li', FEED, SID) is not None     # still there for a retry


def test_a_pass_covers_only_its_profile_domain_and_session(asking, wired):
    from mc.core import TimestampedLines
    from mc.state import agent_sessions
    wired()
    (asking.tmp / 'profiles' / 'other').mkdir()
    agent_sessions['dispatch-2'] = {'status': 'running', 'trigger_type': 'dispatch',
                                    'project_id': 'mission_control', 'log_lines': TimestampedLines()}
    _read(asking)
    _answer_card(asking, 'allow_once')
    assert _read(asking, url='https://evil.example/feed').status_code == 403           # another site
    assert _read(asking, profile='other').status_code == 403                           # another profile
    assert _read(asking, sid='dispatch-2').status_code == 403                          # another session
    assert _read(asking, sid=None).status_code == 403                                  # no session at all
    assert asking.store.find_once('li', FEED, SID) is not None                         # untouched by all that
    assert _read(asking, url='https://evil.com/?u=www.linkedin.com').status_code == 403


def test_a_pass_expires_after_fifteen_minutes(asking, wired, monkeypatch):
    import time
    wired()
    _read(asking)
    _answer_card(asking, 'allow_once')
    assert asking.store.ONCE_TTL_S == 15 * 60
    monkeypatch.setattr(asking.store, '_now', lambda: time.time() + 15 * 60 + 5)
    assert _read(asking).status_code == 403


def test_a_pass_reads_only_the_pass_domain_a_redirect_off_it_is_refused(asking, wired):
    fb, fm = wired(hrefs=('about:blank', 'https://evil.example/landing'))
    _read(asking)
    _answer_card(asking, 'allow_once')
    r = _read(asking)
    assert r.status_code == 403 and r.get_json()['error'] == 'redirect_off_list'
    assert fm.calls == []


def test_passes_do_not_survive_a_restart(asking, wired):
    wired()
    _read(asking)
    _answer_card(asking, 'allow_once')
    asking.store.reset_for_tests()                            # what a fresh process looks like
    assert _read(asking).status_code == 403


# ── an agent can never create or widen a grant ───────────────────────────────

def test_an_agents_own_calls_never_grant_anything(asking, wired):
    wired()
    for _ in range(3):
        _read(asking)
    rid = _rid(asking)
    agent = {}                                                 # no browser Origin: the agent's curl
    for d in ('allow_once', 'always', 'ignore'):
        r = asking.client.post(f'/api/browser/agent-read/requests/{rid}/decision',
                               json={'decision': d, 'passcode': PASSCODE}, headers=agent)
        assert r.status_code == 403, d
    r = asking.client.put('/api/browser/profiles/li/agent-read',
                          json={'enabled': True, 'domains': ['linkedin.com'], 'passcode': PASSCODE}, headers=agent)
    assert r.status_code == 403
    assert asking.store.get_request(rid)['state'] == 'pending'
    assert asking.store.find_once('li', FEED, SID) is None and not _policy_file(asking).exists()


def test_the_only_way_to_open_a_request_is_a_refused_read(asking):
    rules = {r.rule for r in asking.client.application.url_map.iter_rules()
             if 'agent-read/requests' in r.rule}
    assert rules == {'/api/browser/agent-read/requests/<rid>',
                     '/api/browser/agent-read/requests/<rid>/decision'}
    assert asking.client.post('/api/browser/agent-read/requests', json={'profile': 'li'}).status_code in (404, 405)
