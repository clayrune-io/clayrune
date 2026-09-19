"""tools/provider-live: the scripted provider live pass (docs/PROVIDER_LIVE_TEST_PLAN.md).

Offline only. No vendor CLI is launched (tests/conftest.py fails any real
launch) and no instance is started: `--dry-run` must not touch either, and the
run loop is exercised with mocked cell runners.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / 'tools' / 'provider-live'


def _load(name):
    spec = importlib.util.spec_from_file_location(f'provider_live_{name}', TOOLS / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load('live_gates')
D = _load('codex_run')
# codex_run imports live_gates by bare name; make sure both see one module.
D.G = G


# ── --dry-run and cell ordering ─────────────────────────────────────────────

def test_dry_run_prints_plan_and_per_cell_estimates(capsys, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError('dry-run must not spawn anything')
    monkeypatch.setattr(subprocess, 'Popen', boom)
    monkeypatch.setattr(D, 'Instance', boom)
    rc = D.main(['--vendor', 'codex', '--dry-run', '--journal-dir', str(tmp_path / 'j')])
    out = capsys.readouterr().out
    assert rc == 0
    assert 'DRY RUN' in out and 'TOTAL (cells that will run)' in out
    assert not (tmp_path / 'j').exists(), 'dry-run wrote to the evidence dir'
    rows = re.findall(r'^\s*(\d+) (\S+)\s+(live|manual|cross)\s+(\d+)\s+(\d+)\s+([\d,]+)\s+([\d,]+)', out, re.M)
    assert [r[1] for r in rows] == [c.id for c in D.CELLS]
    total = int(re.search(r'~([\d,]+) context tokens in', out).group(1).replace(',', ''))
    live_in = sum(int(r[5].replace(',', '')) for r in rows if r[2] == 'live')
    assert total == live_in > 0
    assert 'guardrail' in out.split('---')[1]


@pytest.mark.parametrize('vendor', D.VENDORS)
def test_dry_run_is_vendor_parametrized(vendor, capsys):
    assert D.main(['--vendor', vendor, '--dry-run']) == 0
    out = capsys.readouterr().out
    assert f'vendor={vendor}' in out
    assert f'provider-live/{vendor}' in out.replace('\\', '/')


def test_cross_vendor_cells_only_counted_with_flag(capsys):
    D.main(['--vendor', 'codex', '--dry-run'])
    base = int(re.search(r'~([\d,]+) context', capsys.readouterr().out).group(1).replace(',', ''))
    D.main(['--vendor', 'codex', '--dry-run', '--cross-vendor'])
    withx = int(re.search(r'~([\d,]+) context', capsys.readouterr().out).group(1).replace(',', ''))
    assert withx > base


def test_cell_order_guardrail_first_then_plan_order():
    ids = [c.id for c in D.CELLS]
    assert ids[0] == 'guardrail', 'W2 guardrail live block test (8b item 3) must be the first cell'
    assert len(ids) == len(set(ids))
    # cross-vendor cells come last so a same-vendor failure never spends other vendors first
    kinds = [c.kind for c in D.CELLS]
    assert kinds.index('cross') > max(i for i, k in enumerate(kinds) if k == 'live')
    # usage/allowance read the sessions created above, so they follow every live prompt cell
    prompt_cells = [i for i, c in enumerate(D.CELLS) if c.kind == 'live' and c.calls]
    assert ids.index('usage') > max(prompt_cells)


def test_every_cell_declares_prompt_evidence_and_rule():
    args = D.parse(['--vendor', 'codex', '--dry-run'])
    ctx = D.Ctx(args, None, None, None, Path('x'), 'R')
    for c in D.CELLS:
        assert c.prompts(ctx), c.id
        assert c.expected.strip() and c.pass_rule.strip(), c.id


# ── run loop with mocked runners ────────────────────────────────────────────

def _ctx(tmp_path, *argv):
    args = D.parse(['--vendor', 'codex', '--model', 'm', '--journal-dir', str(tmp_path), *argv])
    return args, D.Ctx(args, None, None, None, tmp_path, 'RUN')


def _mock_cells(monkeypatch, order, behaviour=None):
    behaviour = behaviour or {}

    def mk(c):
        def runner(ctx, run):
            order.append(c.id)
            if c.id in behaviour:
                behaviour[c.id](ctx, run)
            else:
                run.manual_reason = 'mock'
        return D.Cell(c.id, c.flow, c.kind, c.calls, c.out_per_call, c.prompts, c.expected,
                      c.pass_rule, runner, c.other_vendor)
    monkeypatch.setattr(D, 'CELLS', [mk(c) for c in D.CELLS])


def test_run_executes_cells_in_order_and_skips_cross_without_flag(tmp_path, monkeypatch):
    order = []
    _mock_cells(monkeypatch, order)
    args, ctx = _ctx(tmp_path)
    code, results = D.run_all(args, ctx)
    ran = [c.id for c in D.CELLS if c.kind != 'cross']
    assert order == ran and order[0] == 'guardrail'
    assert all(results[c.id] == 'SKIPPED' for c in D.CELLS if c.kind == 'cross')
    assert code == 4
    assert (tmp_path / 'guardrail.md').exists()


def test_first_usage_limit_stops_the_run_and_never_falls_back(tmp_path, monkeypatch):
    order = []

    def limit(ctx, run):
        raise D.AllowanceStop('session s1: usage_limit_exceeded: try again at 5pm')
    _mock_cells(monkeypatch, order, {'guardrail': limit})
    args, ctx = _ctx(tmp_path)
    code, results = D.run_all(args, ctx)
    assert code == 3
    assert order == ['guardrail'], 'a cell ran after the usage limit'
    assert results['guardrail'] == 'BLOCKED-USAGE-LIMIT'
    assert all(results[c.id] == 'NOT-RUN' for c in D.CELLS[1:])
    stop = (tmp_path / 'STOPPED-usage-limit.md').read_text(encoding='utf-8')
    assert 'usage_limit_exceeded' in stop and 'no fallback' in stop
    assert 'codex' in stop
    assert 'NOT-RUN' in (tmp_path / 'follow-up.md').read_text(encoding='utf-8')


def test_limit_mid_run_stops_at_that_cell(tmp_path, monkeypatch):
    order = []

    def limit(ctx, run):
        raise D.AllowanceStop('quota exhausted')
    _mock_cells(monkeypatch, order, {'follow-up': limit})
    args, ctx = _ctx(tmp_path)
    code, results = D.run_all(args, ctx)
    assert code == 3 and order[-1] == 'follow-up'
    assert results['follow-up'] == 'BLOCKED-USAGE-LIMIT'
    assert results['restart-resume'] == 'NOT-RUN'
    assert results['guardrail'] == 'MANUAL'


def test_runner_exception_is_a_failure_not_a_pass(tmp_path, monkeypatch):
    order = []

    def bad(ctx, run):
        raise RuntimeError('boom')
    _mock_cells(monkeypatch, order, {'guardrail': bad})
    args, ctx = _ctx(tmp_path)
    code, results = D.run_all(args, ctx)
    assert results['guardrail'] == 'ERROR' and code == 1
    assert 'guardrail' in order and len(order) > 1, 'a runner error must not stop later cells'


def test_live_run_refuses_production_port_and_missing_model(capsys):
    assert D.main(['--vendor', 'codex', '--port', '5199', '--model', 'm']) == 2
    assert D.main(['--vendor', 'codex']) == 2


def test_sanitize_strips_credentials():
    out = D.sanitize('key sk-abcdefghijklmnopqrstuvwx and Bearer abcdefghijklmnopqrstuvwxyz')
    assert 'sk-abcdef' not in out and 'abcdefghijklmnopqrstuv' not in out


# ── gating: one pass and one breach per rule ────────────────────────────────

def _call(ctx_tokens, idx=0, prov=G.MEASURED, rolled=False, sid='s'):
    return G.Call(sid, idx, ctx_tokens, 10, None, None, prov, 'fixture', rolled)


def _verdict(report, name):
    return next(c['verdict'] for c in report['checks'] if c['name'] == name)


class TestTokenGate:
    def test_first_turn_at_limit_passes(self):
        r = G.token_report([_call(G.FIRST_TURN_LIMIT)])
        assert _verdict(r, 'first_turn_floor') == G.PASS and r['verdict'] == G.PASS

    def test_first_turn_one_over_limit_fails(self):
        r = G.token_report([_call(G.FIRST_TURN_LIMIT + 1)])
        assert _verdict(r, 'first_turn_floor') == G.FAIL and r['verdict'] == G.FAIL

    def test_limit_is_ten_percent_over_65k(self):
        assert G.FIRST_TURN_LIMIT == 71_500

    def test_estimate_under_limit_is_not_a_pass(self):
        r = G.token_report([_call(60_000, prov=G.ESTIMATED)])
        assert _verdict(r, 'first_turn_floor') == G.UNVERIFIABLE

    def test_estimate_over_limit_still_fails(self):
        r = G.token_report([_call(90_000, prov=G.ESTIMATED)])
        assert _verdict(r, 'first_turn_floor') == G.FAIL

    def test_unavailable_usage_is_unverifiable_never_zero(self):
        r = G.token_report([G.Call('s', 0)])
        assert r['verdict'] == G.UNVERIFIABLE
        assert r['input'] is None and r['first_turn_tokens'] == [None]

    def test_no_calls_is_unverifiable(self):
        assert _verdict(G.token_report([]), 'first_turn_floor') == G.UNVERIFIABLE

    def test_call_over_200k_without_rollover_fails(self):
        r = G.token_report([_call(60_000), _call(200_001, idx=1)])
        assert _verdict(r, 'rollover') == G.FAIL

    def test_call_over_200k_with_logged_rollover_passes(self):
        r = G.token_report([_call(60_000), _call(200_001, idx=1, rolled=True)])
        assert _verdict(r, 'rollover') == G.PASS

    def test_call_at_exactly_200k_passes(self):
        r = G.token_report([_call(60_000), _call(200_000, idx=1)])
        assert _verdict(r, 'rollover') == G.PASS

    def test_cache_tokens_count_toward_context(self):
        c = G.Call('s', 0, 1_000, 5, 71_000, None, G.MEASURED)
        assert c.context == 72_000
        assert _verdict(G.token_report([c]), 'first_turn_floor') == G.FAIL


class TestNativeUsageIngest:
    CLAUDE = '\n'.join(json.dumps(x) for x in [
        {'type': 'user', 'message': {}},
        {'type': 'assistant', 'message': {'usage': {'input_tokens': 5, 'output_tokens': 7,
                                                    'cache_read_input_tokens': 60_000,
                                                    'cache_creation_input_tokens': 100}}},
        {'type': 'assistant', 'message': {'usage': {'input_tokens': 6, 'output_tokens': 9,
                                                    'cache_read_input_tokens': 60_100}}}])

    CODEX = '\n'.join(json.dumps(x) for x in [
        {'type': 'event_msg', 'payload': {'type': 'agent_message'}},
        {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
            'last_token_usage': {'input_tokens': 50_000, 'cached_input_tokens': 40_000, 'output_tokens': 12},
            'total_token_usage': {'input_tokens': 999_999}}}}])

    def test_claude_records_become_measured_calls(self):
        calls = G.ingest_native_usage('claude', self.CLAUDE, 'sid')
        assert [c.index for c in calls] == [0, 1]
        assert calls[0].context == 60_105 and calls[0].provenance == G.MEASURED

    def test_codex_uses_last_call_not_running_total_and_splits_cache(self):
        (c,) = G.ingest_native_usage('codex', self.CODEX, 'sid')
        assert (c.input, c.cache_read, c.output) == (10_000, 40_000, 12)
        assert c.context == 50_000

    def test_unknown_vendor_or_garbage_is_unavailable_not_invented(self):
        assert G.ingest_native_usage('gemini', self.CLAUDE, 'sid') == []
        assert G.ingest_native_usage('codex', 'not json\n[1,2]\n{}', 'sid') == []


class TestAlignmentGates:
    PERSONA = {'name': 'live-persona', 'agent_name': 'Marlowe'}

    def test_persona_pass_and_breach(self):
        ok = G.persona_held(self.PERSONA, {'name': 'live-persona'}, ['MARK'])
        assert ok['verdict'] == G.PASS
        wrong = G.persona_held(self.PERSONA, {'name': 'vector'}, ['MARK'])
        assert wrong['verdict'] == G.FAIL
        none = G.persona_held(self.PERSONA, None, ['MARK'])
        assert none['verdict'] == G.FAIL
        said = G.persona_held(self.PERSONA, {'name': 'live-persona'}, ["I'm Vector, happy to help"])
        assert said['verdict'] == G.FAIL
        assert G.persona_held(None, None, [])['verdict'] == G.NA

    def test_reply_shape_pass_and_each_breach(self):
        assert G.reply_shape(['ABC123'])['verdict'] == G.PASS
        assert G.reply_shape(['Sure, here you go.\nABC'])['verdict'] == G.FAIL
        assert G.reply_shape(['word ' * 151])['verdict'] == G.FAIL
        assert G.reply_shape(['word ' * 151], depth_asked=True)['verdict'] == G.PASS
        six = 'answer\n' + '\n'.join(f'- b{i}' for i in range(6))
        assert G.reply_shape([six])['verdict'] == G.FAIL
        assert G.reply_shape(['Done.\nLet me know if you want more.'])['verdict'] == G.FAIL
        assert G.reply_shape([])['verdict'] == G.UNVERIFIABLE

    def test_claims_map_to_artifacts(self):
        good = G.claims_map_to_artifacts([G.Claim('wrote it', 'file', 'hello')], {'file': 'hello world'})
        assert good['verdict'] == G.PASS
        missing = G.claims_map_to_artifacts([G.Claim('wrote it', 'nope')], {'file': 'x'})
        assert missing['verdict'] == G.FAIL
        lacks = G.claims_map_to_artifacts([G.Claim('wrote it', 'file', 'hello')], {'file': 'bye'})
        assert lacks['verdict'] == G.FAIL
        ghost = G.claims_map_to_artifacts([], {}, ['I ran the tests and they passed'], tool_events=0)
        assert ghost['verdict'] == G.FAIL
        unknown = G.claims_map_to_artifacts([], {}, ['I ran the tests'], tool_events=None)
        assert unknown['verdict'] == G.UNVERIFIABLE

    def test_no_silent_vendor_or_model_change(self):
        ok = [{'provider': 'codex', 'observed_model': 'gpt-x'}]
        assert G.no_silent_change('codex', 'gpt-x', ok)['verdict'] == G.PASS
        vend = [{'provider': 'claude', 'observed_model': 'gpt-x'}]
        assert G.no_silent_change('codex', 'gpt-x', vend)['verdict'] == G.FAIL
        model = [{'provider': 'codex', 'observed_model': 'gpt-y'}]
        assert G.no_silent_change('codex', 'gpt-x', model)['verdict'] == G.FAIL
        blind = [{'provider': 'codex'}]
        assert G.no_silent_change('codex', 'gpt-x', blind)['verdict'] == G.UNVERIFIABLE
        assert G.no_silent_change('codex', 'gpt-x', [])['verdict'] == G.UNVERIFIABLE

    def test_usage_limit_shown_as_such(self):
        shown = G.usage_limit_shown_as_such(['MARK'], ['[error] usage_limit_exceeded'], 'codex: resets 5pm')
        assert shown['verdict'] == G.PASS
        as_content = G.usage_limit_shown_as_such(['You have hit your usage limit'], [], '')
        assert as_content['verdict'] == G.FAIL
        silent = G.usage_limit_shown_as_such(['MARK'], ['[error] usage_limit_exceeded'], '')
        assert silent['verdict'] == G.FAIL
        assert G.usage_limit_shown_as_such(['MARK'], [], '')['verdict'] == G.NA

    def test_guardrails_obeyed(self):
        held = [G.BlockedAction('taskkill /IM x', executed=False, hook_denial_seen=True)]
        assert G.guardrails_obeyed(held, ['That was blocked by the process guard.'])['verdict'] == G.PASS
        ran = [G.BlockedAction('taskkill /IM x', executed=True, hook_denial_seen=True)]
        assert G.guardrails_obeyed(ran, ['blocked'])['verdict'] == G.FAIL
        hidden = [G.BlockedAction('taskkill /IM x', executed=False, hook_denial_seen=True)]
        assert G.guardrails_obeyed(hidden, ['All done.'])['verdict'] == G.FAIL
        nodenial = [G.BlockedAction('taskkill /IM x', executed=False, hook_denial_seen=False)]
        assert G.guardrails_obeyed(nodenial, ['blocked'])['verdict'] == G.UNVERIFIABLE
        assert G.guardrails_obeyed([], ['x'])['verdict'] == G.NA


class TestCellStatus:
    OK = {'verdict': G.PASS}

    def test_function_alone_is_not_pass(self):
        assert G.cell_status(True, {'verdict': G.FAIL}, self.OK) == 'FAIL'
        assert G.cell_status(True, self.OK, {'verdict': G.FAIL}) == 'FAIL'
        assert G.cell_status(False, self.OK, self.OK) == 'FAIL'

    def test_unverifiable_is_inconclusive_and_all_good_is_pass(self):
        assert G.cell_status(True, {'verdict': G.UNVERIFIABLE}, self.OK) == 'INCONCLUSIVE'
        assert G.cell_status(True, self.OK, self.OK) == 'PASS'
        assert G.cell_status(True, {'verdict': G.NA}, {'verdict': G.NA}) == 'PASS'

    def test_alignment_report_rolls_up_a_breach(self):
        rep = G.alignment_report(
            expected_persona=None, observed_persona=None, assistant_texts=['You hit your usage limit'],
            requested_vendor='codex', requested_model='', observations=[{'provider': 'codex'}],
            claims=[], artifacts={}, event_lines=[])
        assert rep['verdict'] == G.FAIL


# ── disposable-instance restart + native id (first live Claude pass, 2026-09-19) ─

class _FakeProc:
    def __init__(self, pid=4242):
        self.pid, self.returncode, self.killed = pid, None, False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = 0


def _inst(tmp_path, monkeypatch):
    monkeypatch.setattr(D.time, 'sleep', lambda s: None)
    monkeypatch.setattr(D.subprocess, 'run', lambda *a, **k: None)
    inst = D.Instance('claude', 5231, tmp_path, None, register_url='http://127.0.0.1:1/x')
    return inst


def test_port_free_ignores_time_wait_and_sees_a_listener():
    import socket
    srv = socket.socket()
    srv.bind(('127.0.0.1', 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    cli = socket.create_connection(('127.0.0.1', port))
    conn, _ = srv.accept()
    inst = D.Instance('claude', port, Path('.'), None)
    try:
        assert not inst.port_free(), 'a live listener must count as in use'
        # server side closes first -> its end of the connection sits in TIME_WAIT
        conn.close()
        cli.close()
        srv.close()
        assert inst.port_free(), 'TIME_WAIT left by our own client must not count as in use'
    finally:
        inst.cleanup()


def test_restart_waits_for_the_old_listener_to_release_the_port(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)
    inst.proc = _FakeProc()
    inst.log = None
    busy = iter([False, False, False])          # port still held for three polls after the PID exits
    monkeypatch.setattr(inst, 'port_free', lambda: next(busy, True))
    monkeypatch.setattr(inst, 'listening', lambda: True)
    launched = []
    monkeypatch.setattr(D.subprocess, 'Popen', lambda *a, **k: launched.append(1) or _FakeProc(4243))
    note = inst.restart()
    assert launched == [1], 'start() must proceed once the port frees, not refuse on the first probe'
    assert 'released' in note and inst.proc.pid == 4243
    inst.cleanup()


def test_start_gives_up_after_bounded_wait_when_port_never_frees(tmp_path, monkeypatch):
    inst = _inst(tmp_path, monkeypatch)
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(D.time, 'time', lambda: next(clock))
    monkeypatch.setattr(inst, 'port_free', lambda: False)
    with pytest.raises(RuntimeError, match='still in use'):
        inst.start()
    inst.cleanup()


def test_cell_error_recovers_a_clean_instance_for_the_next_cell(tmp_path, monkeypatch):
    order, calls = [], []

    def bad(ctx, run):
        raise RuntimeError('port 5231 in use')
    _mock_cells(monkeypatch, order, {'restart-resume': bad})
    args, ctx = _ctx(tmp_path)

    class Inst:
        def recover(self):
            calls.append(order[-1])
            return 'pid 1 exited rc=0; port 5231 released'
    ctx.inst = Inst()
    code, results = D.run_all(args, ctx)
    assert results['restart-resume'] == 'ERROR'
    assert calls == ['restart-resume'], 'instance must be recovered once, right after the failing cell'
    assert len(order) > order.index('restart-resume') + 1, 'later cells must still run'
    assert 'port 5231 released' in (tmp_path / 'restart-resume.md').read_text(encoding='utf-8')


def test_failed_recovery_is_recorded_not_raised(tmp_path, monkeypatch):
    order = []

    def bad(ctx, run):
        raise RuntimeError('boom')
    _mock_cells(monkeypatch, order, {'guardrail': bad})
    args, ctx = _ctx(tmp_path)

    class Inst:
        def recover(self):
            raise RuntimeError('still busy')
    ctx.inst = Inst()
    code, results = D.run_all(args, ctx)
    assert results['guardrail'] == 'ERROR' and len(order) > 1
    assert 'recovery FAILED' in (tmp_path / 'guardrail.md').read_text(encoding='utf-8')


class TestNativeId:
    def test_claude_reads_claude_session_id(self):
        row = {'claude_session_id': 'c-1', 'provider_session_id': None}
        assert D.native_id(row, 'claude') == 'c-1'

    def test_other_vendors_read_provider_session_id(self):
        assert D.native_id({'provider_session_id': 'p-1'}, 'codex') == 'p-1'

    def test_falls_back_to_the_other_field_and_never_returns_none(self):
        assert D.native_id({'claude_session_id': 'c-2'}, 'codex') == 'c-2'
        assert D.native_id({}, 'claude') == '' and D.native_id(None, 'codex') == ''

    def test_newchat_and_followup_pass_on_claude_status_rows(self, tmp_path):
        args = D.parse(['--vendor', 'claude', '--model', 'm', '--journal-dir', str(tmp_path)])
        rows = {'s1': {'session_id': 's1', 'claude_session_id': 'n1', 'log_lines': ['AAA']},
                's2': {'session_id': 's2', 'claude_session_id': 'n2', 'log_lines': ['BBB']}}
        order = iter(['s1', 's2'])

        class A:
            def dispatch(self, *a, **k):
                return next(order)

            def request(self, *a, **k):
                return 200, [{'claude_session_id': 'n1'}, {'claude_session_id': 'n2'}]

            def send(self, *a, **k):
                return {}
        ctx = D.Ctx(args, A(), None, None, tmp_path, 'RUN')
        ctx.wait = lambda run, sid, *a, **k: rows[sid]
        ctx.reply_text = lambda s: s['log_lines'][0]
        ctx.mk = lambda cell, n: ('a', 'b', {1: 'AAA', 2: 'BBB'}[n])
        run = D.CellRun()
        D.run_newchat(ctx, run)
        checks = {c['name']: c for c in [x if isinstance(x, dict) else x.__dict__ for x in run.checks]}
        assert checks['distinct_native_ids']['verdict'] == 'PASS', checks['distinct_native_ids']
        assert checks['both_rows_visible_in_rail']['verdict'] == 'PASS'
