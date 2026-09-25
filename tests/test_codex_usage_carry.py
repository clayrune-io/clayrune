"""Codex usage survives a token-triggered rollover (2026-09-25).

`_mode_a_token_rollover` pops `provider_session_id` to force the runtime's
next dispatch to start a fresh Codex thread. Codex's `turn.completed.usage`
is that THREAD's running total (see tests/test_codex_turn_context_tokens.py)
-- it restarts at zero in the fresh thread, and the shared Mode-A reader used
to store it with a straight overwrite, so a session's reported usage dropped
back to just the new thread's count on every rollover. Measured: a Kestrel
session that had shown 245,656 input read back as 39,720 after its rollover.

`_carry_codex_usage` (agent_routes.py) folds the about-to-be-abandoned
thread's usage into `session['_codex_usage_carry']` at the rollover site;
the reader-side half of the fix lives in test_codex_turn_context_tokens.py.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import agent_routes as ar  # noqa: E402


class TestCarryCodexUsage:
    def test_folds_current_usage_into_a_fresh_carry(self):
        session = {'usage': {'input_tokens': 205936, 'output_tokens': 509,
                             'cached_input_tokens': 152832}}
        ar._carry_codex_usage(session)
        assert session['_codex_usage_carry']['input_tokens'] == 205936
        assert session['_codex_usage_carry']['output_tokens'] == 509

    def test_accumulates_across_repeated_rollovers(self):
        session = {'usage': {'input_tokens': 100_000, 'output_tokens': 50}}
        ar._carry_codex_usage(session)
        session['usage'] = {'input_tokens': 60_000, 'output_tokens': 30}
        ar._carry_codex_usage(session)
        assert session['_codex_usage_carry']['input_tokens'] == 160_000
        assert session['_codex_usage_carry']['output_tokens'] == 80

    def test_no_usage_yet_is_a_no_op(self):
        session = {}
        ar._carry_codex_usage(session)
        assert '_codex_usage_carry' not in session


class TestModeATokenRolloverCarriesCodexUsage:
    def _session(self, ctx=250_000, usage=None):
        return {'context_tokens': ctx, 'provider_session_id': 'thread-1',
               'usage': usage or {'input_tokens': 205936, 'output_tokens': 509},
               'log_lines': []}

    def test_codex_rollover_carries_usage_before_dropping_the_thread(self, monkeypatch):
        monkeypatch.setitem(ar.state.CONFIG, 'context_rollover_tokens', 200_000)
        monkeypatch.setattr(ar, '_auto_fresh_handoff',
                            lambda *a, **k: ('HANDOFF', 'log line', 'activity line'))
        monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
        session = self._session()

        out = ar._mode_a_token_rollover('pp', 'p1', 's1', session, 'codex', 'go on')

        assert session.get('provider_session_id') is None
        assert session['_codex_usage_carry']['input_tokens'] == 205936
        assert out.startswith('HANDOFF')

    def test_non_codex_provider_does_not_set_a_codex_carry(self, monkeypatch):
        # Scope is Codex-only: Gemini/Qwen usage semantics are unverified
        # (docs/CONTEXT_ECONOMY_SPEC.md §4), so this fix must not touch them.
        monkeypatch.setitem(ar.state.CONFIG, 'context_rollover_tokens', 200_000)
        monkeypatch.setattr(ar, '_auto_fresh_handoff',
                            lambda *a, **k: ('HANDOFF', 'log line', 'activity line'))
        monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
        session = self._session()

        ar._mode_a_token_rollover('pp', 'p1', 's1', session, 'qwen', 'go on')

        assert '_codex_usage_carry' not in session

    def test_under_threshold_never_carries(self, monkeypatch):
        monkeypatch.setitem(ar.state.CONFIG, 'context_rollover_tokens', 200_000)
        session = self._session(ctx=1000)

        out = ar._mode_a_token_rollover('pp', 'p1', 's1', session, 'codex', 'go on')

        assert out == 'go on'
        assert '_codex_usage_carry' not in session
        assert session.get('provider_session_id') == 'thread-1'
