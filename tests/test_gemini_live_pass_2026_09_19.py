"""Gemini-path defects from the first gemini live pass (2026-09-19,
--model gemini-3.7-flash; evidence docs/_journal/provider-live/gemini/, which
is gitignored). Claude passed the same cells.

1. Reply text split per delta: `GeminiRuntime._read_stream` appended every
   streamed delta as its own `log_lines` element. The chat renders each
   element as a row and the driver joins them with '\\n', so "CAE658"
   (deltas "CA" + "E658") became "CA\\nE658" and new-chat reply_1 failed.
2. Usage never measured: the driver had no Gemini per-request source, so every
   token block fell back to polled snapshots (provenance=estimated). The CLI
   does record per-request tokens, in its own chat JSONL.
3. First turn 80,253 "tokens": `result.stats` sums EVERY API request of the
   process; a tool turn re-sends the prompt, so the figure is ~Nx one request.
4. "resets reset time unknown" in the allowance refusal.
Every test here fails on 3cfe4ea.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
for p in (PROJECT_ROOT, PROJECT_ROOT / 'tools' / 'provider-live'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest  # noqa: E402

import mc.agent_runtime as ar  # noqa: E402
from mc import allowance_state as al  # noqa: E402
from mc.agent_runtime import EventType, GeminiRuntime, SessionHandle  # noqa: E402
import live_gates as G  # noqa: E402

SID = '8fbfaccb-080a-495b-96ec-b56ae1e40b62'


class _FakeProc:
    def __init__(self, lines, rc=0, pid=424242):
        self.stdout = iter(lines)
        self._rc = rc
        self.pid = pid

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc


def _ev(**kw):
    return json.dumps(kw) + '\n'


def _drive(lines):
    proc = _FakeProc(lines)
    session = {'log_lines': ['> Ron: go'], 'proc': proc, 'status': 'running'}
    handle = SessionHandle(mc_session_id='mc1', provider='gemini', mode='A',
                           project_path='/p', project_id='proj', session_dict=session,
                           meta={'callbacks': {}})
    GeminiRuntime()._read_stream(proc, handle)
    return session


def _delta(text):
    return _ev(type='message', role='assistant', content=text, delta=True)


# ── 1. reply text ────────────────────────────────────────────────────────────

class TestReplyRuns:
    def test_marker_split_across_deltas_is_one_line(self):
        s = _drive([_ev(type='init', session_id=SID), _delta('CA'), _delta('E658'),
                    _ev(type='result', status='success', stats={'input_tokens': 5, 'tool_calls': 0})])
        assert s['log_lines'] == ['> Ron: go', 'CAE658']

    def test_driver_reply_text_contains_the_marker(self):
        import codex_run
        s = _drive([_delta('CA'), _delta('E658')])
        assert 'CAE658' in '\n'.join(codex_run.split_turns(s['log_lines'])[0])

    def test_tool_call_splits_runs_and_keeps_order(self):
        s = _drive([_delta('Let me '), _delta('look.'),
                    _ev(type='tool_use', tool_name='read_file', tool_id='t1', parameters={}),
                    _ev(type='tool_result', tool_id='t1', status='success'),
                    _delta('red, '), _delta('green, blue')])
        assert s['log_lines'][1:] == ['Let me look.', '[tool: read_file]',
                                      '[tool: read_file result — success]', 'red, green, blue']

    def test_run_cut_by_eof_still_lands(self):
        s = _drive([_delta('partial '), _delta('answer')])
        assert s['log_lines'][-1] == 'partial answer'

    def test_preamble_before_straddling_mc_fence_is_kept_and_block_hidden(self):
        s = _drive([_delta('Quick question.\n``'), _delta('`mc:question\n{"questions": [}'),
                    _delta('\n```')])
        assert 'Quick question.\n' in s['log_lines']
        # never streamed raw into the chat as its own line (malformed block
        # is flushed once, whole, at turn end — pre-existing behaviour)
        assert not any(l.startswith('`mc:') for l in s['log_lines'])

    def test_stream_error_event_is_surfaced_not_dropped(self):
        ev = GeminiRuntime().parse_event(json.dumps(
            {'type': 'error', 'severity': 'warning', 'message': 'Loop detected, stopping execution'}))
        assert ev is not None and ev.type == EventType.WARN
        s = _drive([_delta('a'), _ev(type='error', severity='warning',
                                    message='Agent execution blocked: process guard')])
        assert s['log_lines'][1:] == ['a', '[gemini warning] Agent execution blocked: process guard']


# ── 2 + 3. per-request usage ─────────────────────────────────────────────────

def _chat_jsonl(requests, sid=SID):
    """A gemini-cli 0.59 chat record: metadata line, then per message the
    recorder's append-then-update pattern (same id twice, tokens on the 2nd)."""
    rows = [{'sessionId': sid, 'projectHash': 'h', 'kind': 'main'}]
    for n, (inp, cached) in enumerate(requests):
        rows.append({'id': f'u{n}', 'type': 'user', 'content': []})
        rows.append({'id': f'g{n}', 'type': 'gemini', 'content': 'x'})
        rows.append({'$set': {'lastUpdated': 't'}})
        rows.append({'id': f'g{n}', 'type': 'gemini', 'content': 'x', 'model': 'gemini-3.7-flash',
                     'tokens': {'input': inp, 'output': 7, 'cached': cached, 'thoughts': 0,
                                'tool': 0, 'total': inp + 7}})
    return '\n'.join(json.dumps(r) for r in rows) + '\n'


@pytest.fixture
def gemini_home(tmp_path, monkeypatch):
    monkeypatch.delenv('GEMINI_CLI_HOME', raising=False)
    chats = tmp_path / '.gemini' / 'tmp' / 'proj1' / 'chats'
    chats.mkdir(parents=True)
    return tmp_path, chats


class TestPerRequestContext:
    def test_tool_turn_context_is_last_request_not_the_process_sum(self, gemini_home, monkeypatch):
        home, chats = gemini_home
        monkeypatch.setenv('GEMINI_CLI_HOME', str(home))
        (chats / f'session-2026-09-19T18-52-{SID[:8]}.jsonl').write_text(
            _chat_jsonl([(18_664, 0), (20_140, 0), (20_700, 0), (20_749, 0)]), encoding='utf-8')
        stats = {'input_tokens': 80_253, 'total_tokens': 81_374, 'tool_calls': 3}
        s = _drive([_ev(type='init', session_id=SID), _delta('done'),
                    _ev(type='result', status='success', stats=stats)])
        assert s['usage'] == stats                  # spend stays the CLI's own total
        assert s['context_tokens'] == 20_749         # was 80,253

    def test_no_record_and_tool_calls_leaves_context_unknown(self, gemini_home, monkeypatch):
        home, _ = gemini_home
        monkeypatch.setenv('GEMINI_CLI_HOME', str(home))
        s = _drive([_ev(type='init', session_id=SID), _delta('x'),
                    _ev(type='result', status='success',
                        stats={'input_tokens': 80_253, 'tool_calls': 3})])
        assert 'context_tokens' not in s

    def test_single_request_stats_are_used_when_no_record(self, gemini_home):
        home, _ = gemini_home
        assert ar.gemini_turn_context_tokens(SID, {'input_tokens': 18_666, 'tool_calls': 0},
                                             home=str(home)) == 18_666

    def test_prefix_collision_with_another_session_is_ignored(self, gemini_home):
        home, chats = gemini_home
        other = SID[:8] + '-ffff-ffff-ffff-ffffffffffff'
        (chats / f'session-2026-09-19T18-50-{SID[:8]}.jsonl').write_text(
            _chat_jsonl([(99_999, 0)], sid=other), encoding='utf-8')
        assert ar.gemini_chat_files(SID, home=str(home)) == []


class TestDriverMeasuredUsage:
    def test_gemini_chat_record_is_measured_per_request(self):
        calls = G.ingest_native_usage('gemini', _chat_jsonl([(18_664, 0), (20_749, 4_000)]), 's')
        assert [c.provenance for c in calls] == [G.MEASURED, G.MEASURED]
        assert [c.context for c in calls] == [18_664, 20_749]   # cached counted once
        assert calls[1].cache_read == 4_000 and calls[1].input == 16_749
        rep = G.token_report(calls)
        assert rep['checks'][0]['name'] == 'first_turn_floor'
        assert rep['checks'][0]['verdict'] == G.PASS

    def test_read_native_transcript_finds_the_gemini_record(self, gemini_home):
        import codex_run
        home, chats = gemini_home
        (chats / f'session-2026-09-19T18-52-{SID[:8]}.jsonl').write_text(
            _chat_jsonl([(18_664, 0)]), encoding='utf-8')

        class _Inst:
            pass
        inst = _Inst()
        inst.home = str(home)

        class _Ctx:
            vendor = 'gemini'
        ctx = _Ctx()
        ctx.inst = inst
        text = codex_run.read_native_transcript(ctx, {'provider_session_id': SID})
        assert len(G.ingest_native_usage('gemini', text, 's')) == 1


# ── 4. wording ───────────────────────────────────────────────────────────────

class TestResetWording:
    @pytest.fixture(autouse=True)
    def _clean(self):
        al.clear_exhaustion('gemini')
        yield
        al.clear_exhaustion('gemini')

    def test_unknown_reset_is_not_doubled(self):
        al.record_exhaustion('gemini')
        msg = al.refusal_message('gemini')
        assert 'resets reset' not in msg
        # MC-964 Step D appends '(record <age>[, last probed <date>])'.
        assert msg.startswith('gemini is out of allowance (usage limit), reset time unknown'
                              ' — no fallback to another vendor (record ')

    def test_known_reset_still_reads_resets_at(self):
        al.record_exhaustion('gemini', limit_kind='daily', resets_at_display='Sep 20, 2026 9:00 AM')
        assert al.refusal_message('gemini').startswith(
            'gemini is out of allowance (daily), resets '
            'Sep 20, 2026 9:00 AM — no fallback to another vendor (record ')
