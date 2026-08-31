"""MC-922: the Scribe was Claude-only. `_session_transcript_path` delegates to
ClaudeRuntime, so every non-Claude provider (Gemini, Codex, ...) got `None`
back from `_find_transcript_file` and fell through to the legacy stdout-tail
summary instead of a real model summary — no causal `_why:_` note, no memory
worth reading back.

MC already captures the whole conversation for every provider in
`session['log_lines']`, and `_scribe_summarize_text` takes text, not a file —
shared by `_scribe_extract` (whole transcript) and the Step-6 checkpoint
worker (delta). The fix: when no transcript file exists, render log_lines into
the same ACTION/RESULT/ASSISTANT shape and feed the identical summarizer, and
count it separately (`extracted_from_log`) so transcript-backed vs log-backed
extraction stay distinguishable in telemetry.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem

    monkeypatch.setattr(mem, '_get_memory_path',
                        lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, '_should_condense', lambda *a, **k: False)
    return mem, tmp_path


def _proj():
    return {'id': 'p1', 'name': 'P1', 'project_path': 'C:/fake/project'}


# ── _scribe_extract: the fallback itself ─────────────────────────────────────

def test_non_claude_session_with_no_transcript_summarizes_from_log_lines(env, monkeypatch):
    mem, _ = env
    monkeypatch.setattr(mem, '_find_transcript_file', lambda pp, csid: None)
    seen = {}

    def fake_summarize(text, model, want_why=False):
        seen['text'] = text
        seen['want_why'] = want_why
        return 'did the thing, worked fine', 'extracted'

    monkeypatch.setattr(mem, '_scribe_summarize_text', fake_summarize)
    session = {
        'claude_session_id': '',  # Gemini/Codex never populate this
        'log_lines': [
            '> Ron: go read the config and summarize it',
            'Sure — reading the config now.',
            '[gemini tool: Read]',
            '[interrupted]',
            '[gemini exited with code 0]',
        ],
    }
    entry, reason = mem._scribe_extract(_proj(), session)
    assert entry == 'did the thing, worked fine'
    assert reason == 'extracted_from_log'
    assert seen['want_why'] is True
    # Rendered into the same shape _scribe_render_transcript would produce.
    assert 'USER: Ron: go read the config and summarize it' in seen['text']
    assert 'ASSISTANT: Sure — reading the config now.' in seen['text']
    assert 'ACTION gemini tool: Read' in seen['text']
    # Bracket-wrapped status noise must not be treated as conversation content.
    assert '[interrupted]' not in seen['text']
    assert 'exited with code' not in seen['text']


def test_claude_session_with_transcript_is_unchanged(env, monkeypatch, tmp_path):
    """A Claude session whose transcript file exists must take EXACTLY the
    old path — no log_lines rendering, no reason-label change — even if
    log_lines also happens to be populated."""
    mem, _ = env
    tf = tmp_path / 'transcript.jsonl'
    tf.write_text('{}\n', encoding='utf-8')
    monkeypatch.setattr(mem, '_find_transcript_file',
                        lambda pp, csid: tf if csid == 'abc123' else None)
    render_calls = []
    monkeypatch.setattr(
        mem, '_scribe_render_transcript',
        lambda p: render_calls.append(p) or 'RENDERED TRANSCRIPT')
    log_render_calls = []
    monkeypatch.setattr(
        mem, '_render_log_lines_as_transcript',
        lambda ll: log_render_calls.append(ll) or 'SHOULD NEVER BE USED')

    def fake_summarize(text, model, want_why=False):
        assert text == 'RENDERED TRANSCRIPT'
        return 'claude summary', 'extracted'

    monkeypatch.setattr(mem, '_scribe_summarize_text', fake_summarize)
    session = {
        'claude_session_id': 'abc123',
        'log_lines': ['> Ron: hi there, this should be ignored'],
    }
    entry, reason = mem._scribe_extract(_proj(), session)
    assert entry == 'claude summary'
    assert reason == 'extracted'  # NOT extracted_from_log
    assert render_calls == [tf]
    assert log_render_calls == []


def test_no_transcript_and_no_log_lines_still_bails_cleanly(env, monkeypatch):
    mem, _ = env
    monkeypatch.setattr(mem, '_find_transcript_file', lambda pp, csid: None)
    assert mem._scribe_extract(
        _proj(), {'claude_session_id': '', 'log_lines': []}) == (None, 'no_csid')
    assert mem._scribe_extract(
        _proj(), {'claude_session_id': 'x', 'log_lines': None}) == (None, 'no_transcript')


def test_incognito_and_housekeeping_still_skipped(env, monkeypatch):
    mem, _ = env
    # Even with a rich log_lines buffer and no transcript, the gate must fire
    # before the fallback is ever considered.
    monkeypatch.setattr(mem, '_find_transcript_file', lambda pp, csid: None)
    session_base = {'claude_session_id': '', 'log_lines': ['> Ron: hi', 'ok working']}
    assert mem._scribe_extract(_proj(), {**session_base, 'incognito': True}) == (None, 'gated')
    assert mem._scribe_extract(_proj(), {**session_base, 'housekeeping': True}) == (None, 'gated')


def test_scribe_disabled_gate_still_wins(env, monkeypatch):
    mem, _ = env
    monkeypatch.setitem(mem.state.CONFIG, 'scribe_enabled', False)
    session = {'claude_session_id': '', 'log_lines': ['> Ron: hi', 'ok working']}
    assert mem._scribe_extract(_proj(), session) == (None, 'disabled')


def test_render_log_lines_skips_noise_keeps_content(env):
    mem, _ = env
    rendered = mem._render_log_lines_as_transcript([
        '> Ron: do X',
        'working on it',
        '[codex tool: Bash]',
        '[stream error: boom]',
        '[hint] try again',
        '',
        None,
    ])
    lines = rendered.splitlines()
    assert lines == [
        'USER: Ron: do X',
        'ASSISTANT: working on it',
        'ACTION codex tool: Bash',
    ]


def test_render_log_lines_empty_input(env):
    mem, _ = env
    assert mem._render_log_lines_as_transcript([]) == ''
    assert mem._render_log_lines_as_transcript(None) == ''


# ── _write_session_memory: the new counter actually increments ─────────────

@pytest.fixture()
def write_env(env, monkeypatch):
    """`env` plus every _write_session_memory side-fan-out neutralised, so
    the only observable effect left is the scribe_stat counter bump."""
    mem, tmp_path = env
    # _scribe_stat writes to DATA_DIR/<pid>_scribe_stats.json — redirect it so
    # the counter assertions below can never land in the real data/projects/.
    monkeypatch.setattr(mem, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(mem, '_dispatch_condense', lambda *a, **k: None)
    monkeypatch.setattr(mem._distiller, '_distill_extract_and_aggregate',
                        lambda *a, **k: None)
    monkeypatch.setattr(mem, '_topics_refresh_hook', None)
    import beacon.hooks as beacon_hooks
    monkeypatch.setattr(beacon_hooks, 'regenerate_brief_async', lambda *a, **k: None)
    return mem, tmp_path


def _stats(mem, project_id):
    import json
    fp = mem.DATA_DIR / f'{project_id}_scribe_stats.json'
    if not fp.exists():
        return {}
    return json.loads(fp.read_text(encoding='utf-8'))


def test_log_backed_extraction_bumps_distinct_counter(write_env, monkeypatch):
    mem, _ = write_env
    pid = 'p-log-counter'
    monkeypatch.setattr(mem, '_find_transcript_file', lambda pp, csid: None)
    monkeypatch.setattr(mem, '_scribe_summarize_text',
                        lambda text, model, want_why=False: ('summary from log', 'extracted'))
    session = {
        'claude_session_id': '',
        'log_lines': ['> Ron: hi', 'working on it, quite a bit of text here'],
        'session_id': 'sess1',
        'task': 'do a thing',
    }
    mem._write_session_memory(
        {'id': pid, 'project_path': ''}, session, 'completed', '', '2026-08-31')
    stats = _stats(mem, pid)
    assert stats.get('scribe_extracted_from_log') == 1
    assert 'scribe_extracted' not in stats


def test_transcript_backed_extraction_keeps_original_counter(write_env, monkeypatch, tmp_path):
    mem, _ = write_env
    pid = 'p-transcript-counter'
    tf = tmp_path / 't.jsonl'
    tf.write_text('{}\n', encoding='utf-8')
    monkeypatch.setattr(mem, '_find_transcript_file', lambda pp, csid: tf)
    monkeypatch.setattr(mem, '_scribe_render_transcript', lambda p: 'TRANSCRIPT')
    monkeypatch.setattr(mem, '_scribe_summarize_text',
                        lambda text, model, want_why=False: ('summary from transcript', 'extracted'))
    session = {
        'claude_session_id': 'abc123',
        'log_lines': [],
        'session_id': 'sess2',
        'task': 'do a thing',
    }
    mem._write_session_memory(
        {'id': pid, 'project_path': ''}, session, 'completed', '', '2026-08-31')
    stats = _stats(mem, pid)
    assert stats.get('scribe_extracted') == 1
    assert 'scribe_extracted_from_log' not in stats
