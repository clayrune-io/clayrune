"""MC-1155: a dispatched child wakes its attributed caller without `notify_session`.

Before: POST .../agent/dispatch took the callback only from the body, so an agent
following the documented `{"task": ...}` shape dispatched an orphan (6 Dave
dispatches on 2026-11-04 carried dispatched_by_session_id but no callback).
Now `mc.dispatch_lineage.callback_target` defaults it to the OS-attributed caller.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mc import dispatch_lineage as dl  # noqa: E402
from mc.state import agent_sessions  # noqa: E402
from tests.test_dispatch_spawn_save_race import env  # noqa: E402,F401
from tests.test_dispatch_lineage import (  # noqa: E402,F401
    DISPATCH, _as_human, _as_session, caller, dispatch)


# ── the pure rule ───────────────────────────────────────────────────────────

def test_callback_target_rule():
    assert dl.callback_target('dave', '') == 'dave'            # default to caller
    assert dl.callback_target('dave', '  ') == 'dave'
    assert dl.callback_target('dave', 'other') == 'other'      # explicit wins
    assert dl.callback_target('dave', ' other ') == 'other'
    assert dl.callback_target('', 'other') == 'other'          # unattributed: as before
    assert dl.callback_target('', '') == ''                    # human/UI: no callback


# ── through the real handler ────────────────────────────────────────────────

def test_attributed_caller_without_notify_session_is_woken(env, dispatch, caller):
    agent_sessions['dave'] = {'session_id': 'dave', 'project_id': 'p1'}
    _as_session(caller, 'dave')
    r = dispatch(task='do a thing')
    assert r.status_code == 200, r.get_json()
    sess = env['sessions'][r.get_json()['session_id']]
    assert sess['_notify_session'] == 'dave'
    assert sess['_spawned_by'] == 'dave'


def test_explicit_notify_session_still_wins_for_the_callback(env, dispatch, caller):
    agent_sessions['dave'] = {'session_id': 'dave', 'project_id': 'p1'}
    _as_session(caller, 'dave')
    r = dispatch(task='t', notify_session='other')
    sess = env['sessions'][r.get_json()['session_id']]
    assert sess['_notify_session'] == 'other'      # callback: as named
    assert sess['_spawned_by'] == 'dave'           # lineage: unchanged (MC-1082)


def test_unattributed_dispatch_gets_no_callback(env, caller):
    _as_human(caller)
    r = env['client'].post(DISPATCH, json={'task': 'a human curl'})
    sess = env['sessions'][r.get_json()['session_id']]
    assert sess['_notify_session'] == ''
    assert sess['_spawned_by'] == ''

