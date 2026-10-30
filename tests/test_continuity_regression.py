"""The stale first-ask / assistant-only Scribe feedback loop (2026-10-29)."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from mc import continuity_threads, memory as mem, midturn_rollover as mt

P = {'id': 'continuity-regression'}
FIRST = '2026-10-26T17:20:00+00:00'
LATER = '2026-10-29T17:20:00+00:00'


@pytest.fixture
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, 'now_iso', lambda: FIRST)
    logs = []
    monkeypatch.setattr(mem, '_log', logs.append)
    return tmp_path, logs


def line(role, content, **extra):
    return json.dumps({'type': role, 'message': {'content': content}, **extra})


def test_latest_human_correction_reaches_extractor_after_long_tool_output(env, monkeypatch):
    mem.write_continuity(P, threads=['X app setup not started'], owner='Dave')
    delta = mem._scribe_render_lines([
        line('assistant', [{'type': 'text', 'text': 'X app setup not started'}]),
        line('assistant', [{'type': 'text', 'text': 'unrelated work ' * 1500}]),
        line('user', 'The X app is already created; I have signed in on X.'),
    ])
    seen = []

    def extract(model, system, payload):
        seen.append(payload)
        assert 'USER: The X app is already created; I have signed in on X.' in payload
        assert 'unrelated work' in payload
        return '{"threads":[],"commitments":[],"understanding":"X app complete"}'

    monkeypatch.setattr(mem, '_model_call', extract)
    mem._extract_continuity(P, delta, 'fake', owner='Dave')
    assert len(seen[0].split('NEW CONVERSATION SLICE (newest tail):\n')[1]) == 12000
    assert mem.read_continuity(P, owner='Dave')['threads'] == []
    assert 'X app setup not started' not in mem.render_continuity(P, owner='Dave')
    assert any('wrote owner=Dave threads=0' in log for log in env[1])


def test_user_strings_blocks_and_injection_filters_share_one_bounded_turn():
    rendered = mem._scribe_render_lines([
        'null', '[]', '{not json',
        line('user', '<system-reminder>injected fact</system-reminder>Restarted'),
        line('user', [
            {'type': 'tool_result', 'content': 'tool payload'},
            {'type': 'text', 'text': '--- RELEVANT MEMORY ---\nold claim\n\nI signed in.'},
            {'type': 'image', 'source': {'data': 'BASE64'}},
        ]),
        line('user', [{'type': 'text', 'text': 'x' * 700},
                      {'type': 'text', 'text': 'y' * 700}]),
        line('user', 'Stop hook feedback: secret', isMeta=True),
        line('user', [{'type': 'text', 'text': 'synthetic hook'}], isSynthetic=True),
        line('user', 'prior compaction replay', isCompactSummary=True),
        line('user', '<system-reminder>injected only</system-reminder>'),
    ])
    users = [t for t in rendered.splitlines() if t.startswith('USER:')]
    assert users == ['USER: Restarted', 'USER: I signed in.', 'USER: ' + 'x' * 600]
    assert 'RESULT: tool payload' in rendered
    for forbidden in ('injected fact', 'old claim', 'BASE64', 'synthetic hook',
                      'prior compaction replay', 'injected only', 'secret'):
        assert forbidden not in rendered


def test_unrelated_writes_and_extractions_do_not_refresh_old_threads(env, monkeypatch):
    mem.write_continuity(P, threads=['X setup pending'], owner='Dave')
    monkeypatch.setattr(mem, 'now_iso', lambda: LATER)
    mem.write_continuity(P, commitments=['report results'], owner='Dave')
    monkeypatch.setattr(mem, '_model_call', lambda *a: json.dumps({
        'threads': ['X setup pending', 'repair rollover'],
        'confirmed_threads': ['repair rollover'],
    }))
    mem._extract_continuity(P, 'USER: repair rollover', 'fake', owner='Dave')
    rec = mem.read_continuity(P, owner='Dave')
    assert rec['thread_confirmed'] == {'X setup pending': FIRST, 'repair rollover': LATER}
    rendered = mem.render_continuity(P, owner='Dave')
    assert f'X setup pending (unconfirmed since {FIRST} (stale;' in rendered
    assert f'repair rollover (last confirmed {LATER})' in rendered
    assert 'last confirmed' in mem.render_continuity(P, owner='Fenn')
    assert 'stale; verify before acting' in mem.render_continuity(P, owner='Fenn')


def test_explicit_reconfirmation_updates_only_named_thread(env, monkeypatch):
    mem.write_continuity(P, threads=['a', 'b'], owner='Dave')
    monkeypatch.setattr(mem, 'now_iso', lambda: LATER)
    monkeypatch.setattr(mem, '_model_call', lambda *a: json.dumps({
        'threads': ['a', 'b'], 'confirmed_threads': ['b', 'not retained'],
    }))
    mem._extract_continuity(P, 'USER: keep working on b', 'fake', owner='Dave')
    assert mem.read_continuity(P, owner='Dave')['thread_confirmed'] == {'a': FIRST, 'b': LATER}


def test_legacy_thread_is_not_renewed_by_claiming_or_owner_stamp(env, monkeypatch):
    (env[0] / mem.CONTINUITY_FILE).write_text(
        '---\nname: continuity\n---\n\n## In flight\n- legacy task\n', encoding='utf-8')
    monkeypatch.setattr(mem, '_model_call', lambda *a: '{"threads":["legacy task"]}')
    mem._extract_continuity(P, 'unrelated work', 'fake', owner='Dave')
    rec = mem.read_continuity(P, owner='Dave')
    assert rec['thread_confirmed']['legacy task'] == ''
    assert 'unconfirmed since unknown' in mem.render_continuity(P, owner='Dave')


@pytest.mark.parametrize('hours,stale', [(47, False), (48, True), (49, True)])
def test_age_boundary(hours, stale):
    now = datetime.fromisoformat(FIRST) + timedelta(hours=hours)
    assert ('stale;' in continuity_threads.age_label(FIRST, now.isoformat())) is stale


@pytest.mark.parametrize('stamp', ['', 'bad-date', '2099-01-01T00:00:00Z'])
def test_unknown_invalid_and_future_stamps_are_flagged(stamp):
    assert 'verify before acting' in continuity_threads.age_label(stamp, LATER)


def test_atomic_write_failure_never_logs_success(env, monkeypatch):
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(mem, '_atomic_write_text', fail)
    monkeypatch.setattr(mem, '_model_call', lambda *a: '{"threads":["a"]}')
    assert mem._extract_continuity(P, 'delta', 'fake') is None
    assert not any('wrote owner=' in log for log in env[1])
    assert any('extraction failed: disk full' in log for log in env[1])


@pytest.mark.parametrize('output', ['\n'.join(f'?? image-{i}.png' for i in range(1000)),
                                   'one-long-path' * 2000])
def test_noisy_git_cannot_consume_recent_turn_budget(monkeypatch, output):
    monkeypatch.setattr(mt.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        stdout=output, stderr='', returncode=0))
    block = mt.build_state_block({'task': 'Create the X app',
        '_mt_latest_user_msg': 'X app already exists; fix continuity',
        '_mt_unanswered_msgs': [{'ts': '17:00Z', 'text': 'Do not recreate it'}]}, '.')
    assert '--- Original task' not in block
    assert 'historical; may be long finished, not the current task' in block
    assert block.index('Do not recreate it') < block.index('Create the X app')
    assert block.index('X app already exists') < block.index('Create the X app')
    assert block.count('[... truncated]') == 3
    assert len(block) < 4200


def test_latest_is_current_even_when_it_equals_the_first_message(monkeypatch):
    monkeypatch.setattr(mt, '_git', lambda *a: '(clean)')
    block = mt.build_state_block({'task': 'repair continuity',
                                 '_mt_latest_user_msg': 'repair continuity'}, '.')
    assert '--- Latest user message (current direction) ---\nrepair continuity' in block
    assert block.count('repair continuity') == 2
