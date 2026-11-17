"""ML6 (docs/_review/2026-11-16_03_memory_learning.md) — the note route's
`origin` stamp must come from session state, never from a header.

`classify_caller` returned 'interactive' for any request carrying an `Origin`
header. A header is something the caller sends: an agent adding
`-H 'Origin: http://localhost:5199'` to its curl had its note stamped
`interactive`, which is the whole of the unattended-loop rail for that note.
The header may still spare a browser request the cross-project REFUSAL; it
decides nothing about the STAMP.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test_memory_note_gate import env, note  # noqa: E402,F401

ORIGIN = {'Origin': 'http://localhost:5199'}


def _post(env, pid='p1', headers=None):
    return env.client.post(f'/api/project/{pid}/memory/note',
                           json={'slug': 'alpha', 'content': note()},
                           headers=headers or {})


@pytest.mark.parametrize('trigger', ['schedule', 'dispatch', 'workflow', 'hivemind_worker', ''])
def test_an_origin_header_does_not_buy_an_interactive_stamp(env, trigger):
    env.session('s1', 'p1', trigger)
    r = _post(env, headers=ORIGIN)
    assert r.status_code == 201
    assert r.get_json()['origin'] == 'unattended'
    text = env.read('p1', 'alpha')
    assert 'origin: unattended' in text and 'origin: interactive' not in text


def test_a_header_from_an_agent_in_another_project_is_stamped_unattended_not_refused(env):
    # An unattended session elsewhere may be the writer. The header still spares
    # the request the cross-project refusal (a browser is never refused), but
    # the note it writes is stamped by session state.
    env.session('s1', 'p2', 'schedule')
    r = _post(env, pid='p1', headers=ORIGIN)
    assert r.status_code == 201
    assert r.get_json()['origin'] == 'unattended'
    assert 'origin: unattended' in env.read('p1', 'alpha')


def test_the_same_request_without_the_header_is_still_refused(env):
    env.session('s1', 'p2', 'schedule')
    r = _post(env, pid='p1')
    assert r.status_code == 403 and r.get_json()['error'] == 'cross_project_unattended'
    assert not env.exists('p1', 'alpha')


@pytest.mark.parametrize('headers', [ORIGIN, {}], ids=['origin', 'no-origin'])
def test_only_manual_sessions_running_is_interactive_with_or_without_the_header(env, headers):
    env.session('s1', 'p1', 'manual')
    r = _post(env, headers=headers)
    assert r.status_code == 201 and r.get_json()['origin'] == 'interactive'


@pytest.mark.parametrize('headers', [ORIGIN, {}], ids=['origin', 'no-origin'])
def test_nothing_running_is_interactive_with_or_without_the_header(env, headers):
    r = _post(env, headers=headers)
    assert r.status_code == 201 and r.get_json()['origin'] == 'interactive'


def test_a_finished_unattended_session_does_not_taint_the_stamp(env):
    env.session('s1', 'p1', 'schedule', status='completed')
    r = _post(env, headers=ORIGIN)
    assert r.status_code == 201 and r.get_json()['origin'] == 'interactive'


def test_patch_with_the_header_keeps_an_unattended_note_unattended(env):
    env.session('s1', 'p1', 'schedule')
    assert _post(env).get_json()['origin'] == 'unattended'
    r = env.client.patch('/api/project/p1/memory/note',
                         json={'slug': 'alpha', 'content': note(body='edited')},
                         headers=ORIGIN)
    assert r.status_code == 200
    text = env.read('p1', 'alpha')
    assert 'origin: unattended' in text and 'origin: interactive' not in text


# ── the session scan itself, and the callers that keep the header exemption ──

def test_non_manual_session_running_ignores_the_request(env):
    from mc import unattended
    env.session('s1', 'p1', 'schedule')
    with env.client.application.test_request_context('/', headers=ORIGIN):
        assert unattended.non_manual_session_running('p1') is True
        assert unattended.non_manual_session_running('p2') is False
        assert unattended.non_manual_session_running(None) is True
        # is_unattended_caller keeps its documented browser exemption (2026-09-14):
        # vault and config routes must not refuse the dashboard.
        assert unattended.is_unattended_caller('p1') is False
    assert unattended.is_unattended_caller('p1') is True
