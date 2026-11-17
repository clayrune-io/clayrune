"""tools/provider-live: a session's native transcript is located and parsed by
the session's OWN provider, not the vendor under test (MC-1179 pass 12).

Offline only: fixture session rows and a fake home directory. No vendor CLI, no
instance, no live provider pass. How a real vendor's transcript grades is not
shown here.
"""
import importlib.util
import json
import sys
import types
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / 'tools' / 'provider-live'


def _load(name):
    spec = importlib.util.spec_from_file_location(f'provider_live_sv_{name}', TOOLS / f'{name}.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


G = _load('live_gates')
D = _load('codex_run')
D.G = G

CLAUDE_SID = '11111111-2222-3333-4444-555555555555'
CODEX_SID = '99999999-8888-7777-6666-000000000000'

CLAUDE_ROW = {'provider': 'claude', 'claude_session_id': CLAUDE_SID}
CODEX_ROW = {'provider': 'codex', 'provider_session_id': CODEX_SID}


def _claude_line():
    return json.dumps({'type': 'assistant', 'message': {'usage': {
        'input_tokens': 10, 'output_tokens': 5, 'cache_read_input_tokens': 2,
        'cache_creation_input_tokens': 1}}})


def _codex_line():
    return json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'last_token_usage': {'input_tokens': 40, 'output_tokens': 7,
                             'cached_input_tokens': 3}}}})


def _home(tmp_path):
    claude = tmp_path / '.claude' / 'projects' / 'proj'
    claude.mkdir(parents=True)
    (claude / f'{CLAUDE_SID}.jsonl').write_text(_claude_line() + '\n', encoding='utf-8')
    codex = tmp_path / '.codex' / 'sessions' / '2026' / '11' / '17'
    codex.mkdir(parents=True)
    (codex / f'rollout-2026-11-17T00-00-00-{CODEX_SID}.jsonl').write_text(_codex_line() + '\n', encoding='utf-8')
    return tmp_path


def _ctx(vendor, home, sessions=None):
    api = types.SimpleNamespace(
        session=lambda proj, sid: (sessions or {}).get(sid),
        providers=lambda refresh=False: [{'name': vendor, 'capabilities': {'emits_usage': True}}])
    return types.SimpleNamespace(vendor=vendor, inst=types.SimpleNamespace(home=str(home)), api=api,
                                 notes={'all_sessions': [('p', s) for s in (sessions or {})]})


# ── the pure selection ──────────────────────────────────────────────────────

def test_session_vendor_prefers_the_row_provider():
    assert D.session_vendor(CLAUDE_ROW, 'codex') == 'claude'
    assert D.session_vendor(CODEX_ROW, 'claude') == 'codex'


def test_session_vendor_falls_back_when_the_row_names_none():
    assert D.session_vendor({}, 'codex') == 'codex'
    assert D.session_vendor({'provider': ''}, 'claude') == 'claude'
    assert D.session_vendor(None, 'gemini') == 'gemini'


# ── the transcript location follows the session's provider ──────────────────

def test_claude_destination_session_read_while_codex_is_under_test(tmp_path):
    ctx = _ctx('codex', _home(tmp_path))
    assert D.read_native_transcript(ctx, CLAUDE_ROW) == _claude_line() + '\n'


def test_codex_destination_session_read_while_claude_is_under_test(tmp_path):
    ctx = _ctx('claude', _home(tmp_path))
    assert D.read_native_transcript(ctx, CODEX_ROW) == _codex_line() + '\n'


def test_row_without_provider_uses_the_vendor_under_test(tmp_path):
    ctx = _ctx('claude', _home(tmp_path))
    assert D.read_native_transcript(ctx, {'claude_session_id': CLAUDE_SID}) == _claude_line() + '\n'


# ── the parse uses the same provider as the location ────────────────────────

def test_calls_for_parses_a_cross_vendor_session_as_its_own_vendor(tmp_path):
    ctx = _ctx('codex', _home(tmp_path))
    run = D.CellRun(sessions=[('p', CLAUDE_SID)], final={CLAUDE_SID: dict(CLAUDE_ROW)})
    calls, _r, _d = D.calls_for(ctx, run)
    assert [(c.provenance, c.input, c.output) for c in calls] == [(G.MEASURED, 10, 5)]


def test_calls_for_parses_each_session_by_its_own_provider_in_one_run(tmp_path):
    ctx = _ctx('claude', _home(tmp_path))
    run = D.CellRun(sessions=[('p', CLAUDE_SID), ('p', CODEX_SID)],
                    final={CLAUDE_SID: dict(CLAUDE_ROW), CODEX_SID: dict(CODEX_ROW)})
    calls, _r, _d = D.calls_for(ctx, run)
    # codex's input excludes the cached tokens (40 - 3); claude's is as reported
    assert sorted((c.source, c.input) for c in calls) == [('claude-transcript', 10), ('codex-rollout', 37)]


def test_run_usage_counts_rows_from_a_cross_vendor_session(tmp_path):
    ctx = _ctx('codex', _home(tmp_path), sessions={CLAUDE_SID: dict(CLAUDE_ROW)})
    seen = {}
    real = G.token_counts_exposed
    G.token_counts_exposed = lambda rows, emits: seen.update(rows=rows) or real(rows, emits)
    try:
        run = D.CellRun()
        D.run_usage(ctx, run)
    finally:
        G.token_counts_exposed = real
    assert seen['rows'] == 1
