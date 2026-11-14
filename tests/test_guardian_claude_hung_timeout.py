"""Guardian hung-kill threshold for Claude sessions must exceed Claude Code's
own API request timeout (API_TIMEOUT_MS default 600000 ms = 600s).

Incident 2026-11-13: the guardian killed a Dave session at ~601-614s of
silence, the same moment the CLI would have timed out the stalled model call
and retried itself (emitting system/api_retry, which _note_api_retry stamps
into last_output_time). The CLI never got to retry."""
import time
from types import SimpleNamespace

from mc.blueprints import agent_routes as ar

CLI_API_TIMEOUT_S = 600  # Claude Code API_TIMEOUT_MS default (600000 ms)


def test_claude_threshold_exceeds_cli_api_timeout():
    assert ar.GUARDIAN_HUNG_TIMEOUT_BY_PROVIDER['claude'] > CLI_API_TIMEOUT_S


def test_gemini_threshold_unchanged():
    assert ar.GUARDIAN_HUNG_TIMEOUT_BY_PROVIDER['gemini'] == 90


def _hung_session(silent_for, now):
    proc = SimpleNamespace(pid=999999, poll=lambda: None)
    return proc, {
        'status': 'running', 'project_id': 'hung_test', 'mode': 'B',
        'provider': 'claude', 'proc': proc, 'log_lines': [],
        'last_output_time': now - silent_for,
        'last_status_change_time': now - silent_for,
    }


def _run(silent_for, monkeypatch):
    killed = []
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar, '_proc_is_cpu_idle', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_kill_proc_background', lambda p: killed.append(p))
    now = time.time()
    _, session = _hung_session(silent_for, now)
    ar._guardian_check_session('hungtest0000', session, now)
    return killed


def test_claude_not_killed_at_cli_timeout_window(monkeypatch):
    # 614s was the observed kill time; the CLI's own timeout+retry owns this window.
    assert _run(614, monkeypatch) == []


def test_claude_killed_past_threshold(monkeypatch):
    assert len(_run(ar.GUARDIAN_HUNG_TIMEOUT_BY_PROVIDER['claude'] + 5, monkeypatch)) == 1
