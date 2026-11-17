"""MC-1179 AP37: a spawn the OS refused must not freeze the conversation.

Only FileNotFoundError used to be classed as a proven no-child refusal; the
other refusals Windows raises for a missing cwd, a directory or locked file as
the target, and a half-replaced executable took the terminal "launch outcome
unknown" path, which no code or UI ever clears. An error outside the proven
list stays unknown (test_launch_transaction.py pins that).
"""
import errno

import pytest

from mc import agent_spawn, launch_transaction as transaction
from mc.spawn_refusal import is_prechild_refusal
from test_runtime_completion_log import env, _mode_b_session, _log_rows  # noqa: F401


def _bad_exe_format():
    exc = OSError(errno.ENOEXEC, '%1 is not a valid Win32 application')
    exc.winerror = 193  # type: ignore[attr-defined]
    return exc


PROVEN = {
    'missing-exe': FileNotFoundError(2, 'no such file'),
    'missing-cwd': NotADirectoryError(267, 'directory name is invalid'),
    'locked-or-dir': PermissionError(13, 'access denied'),
    'bad-exe-format': _bad_exe_format(),
}
UNPROVEN = {
    'generic': OSError('handle cleanup failed'),
    'disk-full': OSError(errno.ENOSPC, 'no space'),
    'not-an-oserror': RuntimeError('boom'),
}


@pytest.mark.parametrize('name', PROVEN)
def test_proven_refusals(name):
    assert is_prechild_refusal(PROVEN[name])


@pytest.mark.parametrize('name', UNPROVEN)
def test_unproven_errors_stay_unknown(name):
    assert not is_prechild_refusal(UNPROVEN[name])


def _spawn(env, monkeypatch, error):
    def refused(*args, **kwargs):
        raise error

    monkeypatch.setattr(agent_spawn.subprocess, 'Popen', refused)
    return lambda: agent_spawn.spawn_engine_proc(
        ['fake'], session_id='factory', engine='claude', root=env['tmp_path'] / 'shim',
        cfg={'agent_survive_restart_enabled': False})


@pytest.mark.parametrize('name', PROVEN)
def test_a_refused_spawn_restores_and_the_next_turn_is_allowed(env, monkeypatch, name):
    ar = env['ar']
    session = _mode_b_session(env, 'transaction')
    ar._log_agent_dispatch_pending(session, strict=True)
    ar._update_agent_log('proj1', lambda rows: rows[0].update(status='completed', summary='kept'))
    session['status'] = 'completed'
    prior = _log_rows(env)
    spawn = _spawn(env, monkeypatch, PROVEN[name])
    tx = transaction.start_turn(session, None)
    with pytest.raises(OSError):
        tx.launch(spawn)
    assert tx.restored
    assert not getattr(PROVEN[name], '_mc_launch_ambiguous', False)
    assert not session.get('_launch_uncertain')
    assert _log_rows(env) == prior
    # The conversation is not frozen: a following turn reserves normally.
    transaction.start_turn(session, None)


def test_an_unproven_oserror_still_freezes_the_turn(env, monkeypatch):
    session = _mode_b_session(env, 'transaction')
    env['ar']._log_agent_dispatch_pending(session, strict=True)
    tx = transaction.start_turn(session, None)
    with pytest.raises(OSError):
        tx.launch(_spawn(env, monkeypatch, OSError('handle cleanup failed')))
    assert not tx.restored
    assert session['_launch_uncertain']
