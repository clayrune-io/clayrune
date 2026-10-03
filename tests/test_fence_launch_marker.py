"""Backlog dc480ad3 (MC-1040): the fence hook arms on the per-launch marker
CLAYRUNE_LAUNCHED_UNATTENDED=1 when the trigger-type lookup returns None
(server down / restarting / session unknown), and only then. When the lookup
answers, the lookup wins — including a human re-stamp to 'manual'.

Loads `fence_patched.py` beside this file when present (before the patch is
applied to steward/fence.py); once applied, copy this file into tests/ and it
imports `steward.fence` instead. Server side: tests/test_launch_marker.py.
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

_PATCHED = Path(__file__).with_name('fence_patched.py')
if _PATCHED.exists():
    _spec = importlib.util.spec_from_file_location('fence_patched', _PATCHED)
    fence = importlib.util.module_from_spec(_spec)
    sys.modules['fence_patched'] = fence
    _spec.loader.exec_module(fence)
else:
    from steward import fence

MARKER = 'CLAYRUNE_LAUNCHED_UNATTENDED'


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    monkeypatch.delenv(MARKER, raising=False)
    monkeypatch.setattr(fence, '_consume_attend_once_pass', lambda: False)


def _lookup(monkeypatch, answer):
    monkeypatch.setattr(fence, '_lookup_trigger_type', lambda sid: answer)


def test_marker_name_matches_the_server_helper():
    from mc import launch_marker
    assert fence._LAUNCH_MARKER_ENV == launch_marker.LAUNCH_MARKER_ENV == MARKER


# ── _should_arm_for_unattended_trigger ────────────────────────────────────

def test_marker_with_lookup_none_arms(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setenv(MARKER, '1')
    _lookup(monkeypatch, None)
    assert fence._should_arm_for_unattended_trigger() is True


def test_no_marker_with_lookup_none_does_not_arm(monkeypatch):
    # An ordinary session during a server restart must NOT be fenced.
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    _lookup(monkeypatch, None)
    assert fence._should_arm_for_unattended_trigger() is False


@pytest.mark.parametrize('value', ['', '0', 'true', 'yes'])
def test_only_the_literal_one_counts(monkeypatch, value):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setenv(MARKER, value)
    _lookup(monkeypatch, None)
    assert fence._should_arm_for_unattended_trigger() is False


def test_lookup_manual_wins_over_the_marker(monkeypatch):
    # The attend re-stamp: the human lifted the fence; a marker baked in at
    # launch must not override the server's current answer.
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setenv(MARKER, '1')
    _lookup(monkeypatch, {'trigger_type': 'manual', 'fence_unattended_enabled': True})
    assert fence._should_arm_for_unattended_trigger() is False


def test_lookup_flag_off_wins_over_the_marker(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setenv(MARKER, '1')
    _lookup(monkeypatch, {'trigger_type': 'dispatch', 'fence_unattended_enabled': False})
    assert fence._should_arm_for_unattended_trigger() is False


def test_lookup_unattended_arms_without_the_marker(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    _lookup(monkeypatch, {'trigger_type': 'dispatch', 'fence_unattended_enabled': True})
    assert fence._should_arm_for_unattended_trigger() is True


def test_lookup_exception_inside_helper_still_falls_back_to_marker(monkeypatch):
    # _lookup_trigger_type swallows its own network errors and returns None;
    # prove the real function does so for an unreachable server, then that the
    # marker decides.
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    monkeypatch.setattr(fence, '_MC_API_BASE', 'http://127.0.0.1:9')
    assert fence._lookup_trigger_type('sid-1') is None
    assert fence._should_arm_for_unattended_trigger() is False
    monkeypatch.setenv(MARKER, '1')
    assert fence._should_arm_for_unattended_trigger() is True


def test_marker_without_a_session_id_arms_and_does_not_look_up(monkeypatch):
    monkeypatch.setenv(MARKER, '1')

    def _boom(_sid):
        raise AssertionError('no session id, so no lookup')
    monkeypatch.setattr(fence, '_lookup_trigger_type', _boom)
    assert fence._should_arm_for_unattended_trigger() is True


def test_no_session_id_no_marker_stays_false(monkeypatch):
    assert fence._should_arm_for_unattended_trigger() is False


# ── main() end to end ─────────────────────────────────────────────────────

def _run_main(monkeypatch, tmp_path, command, *, marker, lookup):
    tp = tmp_path / 'transcript.jsonl'
    tp.write_text(json.dumps({'type': 'user', 'message': {
        'role': 'user', 'content': 'Please go implement the fix we discussed'}}) + '\n',
        encoding='utf-8')
    payload = {'tool_name': 'Bash', 'tool_input': {'command': command},
               'transcript_path': str(tp)}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'sid-1')
    if marker:
        monkeypatch.setenv(MARKER, '1')
    _lookup(monkeypatch, lookup)
    return fence.main()


def test_main_blocks_git_push_for_a_marked_child_when_the_server_is_unreachable(
        monkeypatch, tmp_path, capsys):
    rc = _run_main(monkeypatch, tmp_path, 'git push origin master', marker=True, lookup=None)
    assert rc == 2
    assert 'STEWARD FENCE blocked' in capsys.readouterr().err


def test_main_lets_an_unmarked_child_through_when_the_server_is_unreachable(
        monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, 'git push origin master',
                     marker=False, lookup=None) == 0


def test_main_marked_child_may_run_benign_commands(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, 'git status', marker=True, lookup=None) == 0


def test_main_lookup_manual_unfences_a_marked_child(monkeypatch, tmp_path):
    rc = _run_main(monkeypatch, tmp_path, 'git push origin master', marker=True,
                   lookup={'trigger_type': 'manual', 'fence_unattended_enabled': True})
    assert rc == 0
