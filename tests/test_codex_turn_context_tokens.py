"""Codex context-token figure (2026-09-25).

`codex exec --json` ends a turn with `turn.completed.usage`, and the shared
Mode-A reader fed that straight into `normalize_context_tokens` for the
rollover trigger. On codex-cli 0.155.1 that usage is the THREAD's running
`total_token_usage` — every request of every turn so far, summed — so the
figure grew with tool calls and turns, never with context:

- Kestrel's 4-request first turn logged "rolling to fresh (tokens=205936)"
  after ONE turn; its last request held 52,512.
- A 97-request, 4-turn thread rolled at 11,345,241; last request 202,596.

The numbers below are the real `token_count` payloads from those rollouts
(thread/turn ids and every other field replaced). On c2f7289 every test
here fails except the one pinning the old figure.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as art  # noqa: E402
from mc.agent_runtime import CodexRuntime, SessionHandle  # noqa: E402

TID = '00000000-0000-7000-8000-000000000001'


def _u(inp, cached, out, reasoning=0):
    return {'input_tokens': inp, 'cached_input_tokens': cached,
            'cache_write_input_tokens': 0, 'output_tokens': out,
            'reasoning_output_tokens': reasoning, 'total_tokens': inp + out}


def _tc(last, total):
    return {'type': 'event_msg', 'payload': {
        'type': 'token_count',
        'info': {'last_token_usage': last, 'total_token_usage': total,
                 'model_context_window': 258400}}}


def _started():
    return {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 't'}}


def _complete():
    return {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 't'}}


_NOISE = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                                               'content': [{'text': 'x'}]}}

# One turn, four requests (the Kestrel thread that rolled after turn 1).
ONE_TURN = [
    {'type': 'session_meta', 'payload': {'id': TID}},
    _started(), _NOISE,
    _tc(_u(49127, 0, 121), _u(49127, 0, 121)),
    {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None}},
    _tc(_u(52031, 48896, 147), _u(101158, 48896, 268)),
    _tc(_u(52266, 51840, 177, 21), _u(153424, 100736, 445, 21)),
    _tc(_u(52512, 52096, 64), _u(205936, 152832, 509, 21)),
    _complete(),
]
# What `turn.completed` carried for it — the running thread total.
ONE_TURN_USAGE = {'input_tokens': 205936, 'cached_input_tokens': 152832,
                  'cache_write_input_tokens': 0, 'output_tokens': 509,
                  'reasoning_output_tokens': 21}

# Two turns; the second ran in a new `codex exec resume` process and its
# `turn.completed` still reported the thread total, 156,296, which is also
# what Clayrune's agent_log recorded as that session's usage.
TWO_TURNS = [
    {'type': 'session_meta', 'payload': {'id': TID}},
    _started(),
    _tc(_u(50082, 0, 156, 85), _u(50082, 0, 156, 85)),
    _tc(_u(51048, 49920, 60), _u(101130, 49920, 216, 85)),
    _complete(),
    _started(),
    _tc(_u(55166, 50816, 113), _u(156296, 100736, 329, 85)),
    _complete(),
]


def _write(tmp_path, records):
    f = tmp_path / f'rollout-2026-09-25T09-03-13-{TID}.jsonl'
    f.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
    return f


def _handle(session=None):
    return SessionHandle(mc_session_id='m1', provider='codex', mode='A',
                         project_path='/p', project_id='p',
                         session_dict=session if session is not None
                         else {'provider_session_id': TID})


class TestCodexTurnContext:
    def test_old_normalization_is_the_logged_rollover_figure(self):
        # Pins the bug: exactly the number clayrune.log rolled the chat on.
        assert art.normalize_context_tokens(ONE_TURN_USAGE) == 205936

    def test_runtime_reads_last_request_from_rollout(self, tmp_path, monkeypatch):
        f = _write(tmp_path, ONE_TURN)
        rt = CodexRuntime()
        monkeypatch.setattr(rt, 'transcript_path',
                            lambda pp, sid: f if sid == TID else None)
        assert rt.turn_context_tokens(_handle(), ONE_TURN_USAGE, {}) == 52512

    def test_second_turn_uses_its_own_last_request(self, tmp_path):
        f = _write(tmp_path, TWO_TURNS)
        usage = {'input_tokens': 156296, 'output_tokens': 329}
        assert art.codex_turn_context_tokens(f, usage, delay=0) == 55166

    def test_matches_on_running_total_not_position(self, tmp_path):
        # The rollout already holds a later request than the turn being
        # scored: the match on the running total still picks this turn's.
        f = _write(tmp_path, TWO_TURNS)
        usage = {'input_tokens': 101130}
        assert art.codex_turn_context_tokens(f, usage, delay=0) == 51048

    def test_lagging_rollout_is_reread(self, tmp_path, monkeypatch):
        # The CLI writes the rollout from its own task; the final token_count
        # can land after `turn.completed` reaches us on stdout.
        f = _write(tmp_path, ONE_TURN[:-3])
        sleeps = []

        def _sleep(s):
            sleeps.append(s)
            _write(tmp_path, ONE_TURN)

        monkeypatch.setattr(art._time, 'sleep', _sleep)
        assert art.codex_turn_context_tokens(f, ONE_TURN_USAGE) == 52512
        assert sleeps == [0.2]

    def test_no_match_falls_back_to_newest_request_never_the_sum(self, tmp_path):
        f = _write(tmp_path, ONE_TURN[:-3])  # rollout never caught up
        got = art.codex_turn_context_tokens(f, ONE_TURN_USAGE, delay=0)
        assert got == 52031

    def test_no_rollout_is_unknown(self):
        assert art.codex_turn_context_tokens(None, ONE_TURN_USAGE) is None

    def test_no_thread_id_is_unknown(self):
        rt = CodexRuntime()
        assert rt.turn_context_tokens(_handle({}), ONE_TURN_USAGE, {}) is None

    def test_shared_reader_stores_the_per_request_figure(self, tmp_path, monkeypatch):
        f = _write(tmp_path, ONE_TURN)
        monkeypatch.setattr(CodexRuntime, 'transcript_path',
                            lambda self, pp, sid: f if sid == TID else None)

        class _P:
            stdout = iter([
                json.dumps({'type': 'thread.started', 'thread_id': TID}) + '\n',
                json.dumps({'type': 'turn.completed', 'usage': ONE_TURN_USAGE}) + '\n'])
            pid = 4242

            def wait(self):
                return 0

            def poll(self):
                return 0

        proc = _P()
        session = {'log_lines': [], 'proc': proc, 'status': 'running'}
        handle = SessionHandle(mc_session_id='m1', provider='codex', mode='A',
                               project_path='/p', project_id='p', session_dict=session,
                               meta={'callbacks': {}})
        art._mode_a_reader(proc, handle, CodexRuntime())
        assert session['context_tokens'] == 52512
        # Usage itself is untouched: it is the thread's real billed input.
        assert session['usage']['input_tokens'] == 205936

    def test_shared_reader_adds_carried_usage_from_an_earlier_rolled_thread(
            self, tmp_path, monkeypatch):
        # Regression, 2026-09-25: a rollover pops provider_session_id and the
        # fresh thread's turn.completed.usage starts back at its own total,
        # so without the carry the session's reported usage would drop from
        # (carry + this thread) back down to just this thread's figure.
        f = _write(tmp_path, ONE_TURN)
        monkeypatch.setattr(CodexRuntime, 'transcript_path',
                            lambda self, pp, sid: f if sid == TID else None)

        class _P:
            stdout = iter([
                json.dumps({'type': 'thread.started', 'thread_id': TID}) + '\n',
                json.dumps({'type': 'turn.completed', 'usage': ONE_TURN_USAGE}) + '\n'])
            pid = 4242

            def wait(self):
                return 0

            def poll(self):
                return 0

        proc = _P()
        carry = {'input_tokens': 39720, 'output_tokens': 100,
                 'cached_input_tokens': 0, 'cache_write_input_tokens': 0,
                 'reasoning_output_tokens': 0, 'total_tokens': 39820}
        session = {'log_lines': [], 'proc': proc, 'status': 'running',
                  '_codex_usage_carry': dict(carry)}
        handle = SessionHandle(mc_session_id='m1', provider='codex', mode='A',
                               project_path='/p', project_id='p', session_dict=session,
                               meta={'callbacks': {}})
        art._mode_a_reader(proc, handle, CodexRuntime())
        # Context-size detection still matches on the RAW thread figure —
        # the carry must never leak into the rollout-matching lookup.
        assert session['context_tokens'] == 52512
        assert session['usage']['input_tokens'] == 205936 + 39720
        assert session['usage']['output_tokens'] == 509 + 100
        # The carry itself is untouched by the reader (only the rollover
        # site in agent_routes.py updates it).
        assert session['_codex_usage_carry'] == carry
