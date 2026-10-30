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
    assert block.count('[... truncated') == 3
    assert len(block) < 4200


def test_latest_is_current_even_when_it_equals_the_first_message(monkeypatch):
    monkeypatch.setattr(mt, '_git', lambda *a: '(clean)')
    block = mt.build_state_block({'task': 'repair continuity',
                                 '_mt_latest_user_msg': 'repair continuity'}, '.')
    assert '--- Latest user message (current direction) ---\nrepair continuity' in block
    assert block.count('repair continuity') == 2


# The USER branch after the repair: a stale first message must not reach the
# extractor under the strongest label when the injected block does not LEAD
# the turn (measured 2026-10-29: 74 of 713 USER turns, see mc/scribe_user_turn.py).
STATE = ("=== Mid-task rollover state ===\n--- Unanswered user messages (answer these first) ---\n"
         "(none)\n\n--- First message of this chat (historical; may be long finished, "
         "not the current task) ---\ncreate the X.com app\n--- Original task (verbatim) ---\n"
         "create the X.com app\n=== End of mid-task rollover state ===\n\n" + mt.ROLL_MESSAGE)
REPLAY = ("=== Prior conversation, started on claude (session abc), handed off here ===\n\n"
          "User: create the X.com app\n\nAssistant: opening the portal\n\n"
          "User: --- STANDING POSITIONS (already decided) ---\n  * old\n\n"
          "--- RELEVANT MEMORY ---\n  * stale\n\n--- REPLY SHAPE ---\nshort\n")


def users(rendered):
    return [t for t in rendered.splitlines() if t.startswith('USER:')]


def test_user_text_before_rollover_block_is_kept_and_block_is_cut():
    rendered = mem._scribe_render_lines([
        line('user', 'The X app already exists; stop offering it.\n\n' + STATE),
        line('user', [{'type': 'text', 'text': '--- RELEVANT MEMORY ---\nold\n\n'
                                              'signed in\n\n' + STATE}]),
        line('user', STATE),
        line('user', "'\n\n" + STATE),
    ])
    assert users(rendered) == ['USER: The X app already exists; stop offering it.',
                               'USER: signed in', "USER: '"]
    for forbidden in ('Original task', 'create the X.com app', 'rolled over mid-task',
                      'Mid-task rollover state', 'old'):
        assert forbidden not in rendered


def test_prior_conversation_replay_without_footer_is_cut_wherever_it_starts():
    rendered = mem._scribe_render_lines([
        line('user', REPLAY),
        line('user', 'Restarted\n\n' + REPLAY),
        line('user', REPLAY + '\n' + mem._agent_runtime.HANDOFF_FOOTER + '\n\nkeep going'),
    ])
    assert users(rendered) == ['USER: Restarted', 'USER: keep going']
    for forbidden in ('Prior conversation', 'create the X.com app', 'STANDING POSITIONS',
                      'RELEVANT MEMORY', 'REPLY SHAPE', 'opening the portal'):
        assert forbidden not in rendered


def test_dispatched_callback_user_lines_are_dropped():
    rendered = mem._scribe_render_lines([
        line('user', '[dispatched agent finished] Dave (session 3ffc561a373a) ended with '
                     'status=idle.\n\nTask: MC-1089 live E2E test\n\nResult: X app created'),
        line('user', 'X app is done\n\n[dispatched agent finished] Bram ended with status=idle.'),
        line('user', [{'type': 'tool_result', 'content': 'ok'},
                      {'type': 'text', 'text': '[dispatched agent finished] Tilda ended.'}]),
    ])
    assert users(rendered) == ['USER: X app is done']
    assert 'RESULT: ok' in rendered
    assert 'dispatched agent finished' not in rendered
    assert 'MC-1089' not in rendered


def test_diff_stat_line_cap_keeps_every_early_file_and_the_summary_line(monkeypatch):
    # Real `diff --stat` rows run ~70 chars, which is why 1,000 chars bound at ~14.
    rows = [f'mc/blueprints/desk_connect_service_{i:02d}.py | {i + 1:3d} {"+" * 30}'
            for i in range(45)]  # no leading space: _git strips the output
    stat = '\n'.join(rows + [' 45 files changed, 1035 insertions(+)'])
    monkeypatch.setattr(mt.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        stdout=stat, stderr='', returncode=0))
    out = mt._git('.', 'diff', '--stat')
    kept = out.splitlines()
    assert kept[:mt._GIT_OUT_LINES - 1] == rows[:mt._GIT_OUT_LINES - 1]
    assert kept[-1] == ' 45 files changed, 1035 insertions(+)'
    assert kept[-2] == '[... truncated: 16 line(s) omitted]'
    assert len(kept) == mt._GIT_OUT_LINES + 1
    assert len(out) > 1000  # the old character cap would have cut at ~14 rows


def test_git_output_under_the_line_cap_is_untouched(monkeypatch):
    stat = '\n'.join([f'f{i}.py | 1 +' for i in range(20)] + [' 20 files changed'])
    monkeypatch.setattr(mt.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        stdout=stat, stderr='', returncode=0))
    assert mt._git('.', 'diff', '--stat') == stat


# --- reworded threads keep their stamp (Fenn b029cbfb1ef8 / Tobin 4ff2433bec0d) ---

def test_reworded_thread_inherits_old_stamp_and_logs(env, monkeypatch):
    mem.write_continuity(P, threads=['X app setup not started'], owner='Dave')
    monkeypatch.setattr(mem, 'now_iso', lambda: LATER)
    monkeypatch.setattr(mem, '_model_call', lambda *a: json.dumps({
        'threads': ['X app setup not started (cont)']}))
    mem._extract_continuity(P, 'unrelated chatter', 'fake', owner='Dave')
    rec = mem.read_continuity(P, owner='Dave')
    assert rec['thread_confirmed'] == {'X app setup not started (cont)': FIRST}
    assert ('[continuity] reworded: X app setup not started -> '
            'X app setup not started (cont)') in env[1]


def test_genuinely_new_thread_gets_fresh_stamp(env, monkeypatch):
    mem.write_continuity(P, threads=['X app setup not started'], owner='Dave')
    monkeypatch.setattr(mem, 'now_iso', lambda: LATER)
    monkeypatch.setattr(mem, '_model_call', lambda *a: json.dumps({
        'threads': ['X app setup not started', 'Desk video editor button restyle']}))
    mem._extract_continuity(P, 'unrelated chatter', 'fake', owner='Dave')
    rec = mem.read_continuity(P, owner='Dave')
    assert rec['thread_confirmed'] == {'X app setup not started': FIRST,
                                       'Desk video editor button restyle': LATER}
    assert not [l for l in env[1] if 'reworded' in l]


def test_two_near_duplicates_do_not_both_inherit_one_stamp(env, monkeypatch):
    mem.write_continuity(P, threads=['MC-924 provider pin dropped at dispatch'], owner='Dave')
    monkeypatch.setattr(mem, 'now_iso', lambda: LATER)
    monkeypatch.setattr(mem, '_model_call', lambda *a: json.dumps({
        'threads': ['MC-924 provider pin dropped at dispatch (cont)',
                    'MC-924 provider pin dropped at dispatch, fix pending']}))
    mem._extract_continuity(P, 'unrelated chatter', 'fake', owner='Dave')
    stamps = mem.read_continuity(P, owner='Dave')['thread_confirmed']
    assert sorted(stamps.values()) == [FIRST, LATER]
    assert stamps['MC-924 provider pin dropped at dispatch (cont)'] == FIRST
    assert len([l for l in env[1] if 'reworded' in l]) == 1


def test_verbatim_kept_thread_is_not_inherited_by_its_near_duplicate(env):
    assert continuity_threads.match_reworded(
        ['task a is open', 'task a is open (cont)'], ['task a is open']) == {}


def test_slot_is_part_of_identity_and_case_whitespace_are_not(env):
    got = continuity_threads.match_reworded(['  Repair   ROLLOVER tail-cut '],
                                            ['repair rollover tail-cut'])
    assert got == {'  Repair   ROLLOVER tail-cut ': 'repair rollover tail-cut'}
    assert (continuity_threads._identity('ship the fix', 'threads')
            != continuity_threads._identity('ship the fix', 'commitments'))


def test_reworded_thread_older_than_48h_still_flags_stale(env, monkeypatch):
    mem.write_continuity(P, threads=['Position review: 97 due, no verdicts'], owner='Vector')
    monkeypatch.setattr(mem, 'now_iso', lambda: LATER)  # 72h after FIRST
    monkeypatch.setattr(mem, '_model_call', lambda *a: json.dumps({
        'threads': ['Position review: 97 due, no verdicts recorded']}))
    mem._extract_continuity(P, 'unrelated chatter', 'fake', owner='Vector')
    rendered = mem.render_continuity(P, owner='Vector')
    assert (f'Position review: 97 due, no verdicts recorded (unconfirmed since '
            f'{FIRST} (stale; verify before acting))') in rendered
    assert 'last confirmed' not in rendered


LIVE_SHAPE = """---
name: continuity
updated: "2026-10-26T17:20:00+00:00"
---
Working state for this project — what is in flight and what was promised, PER AGENT.

## Where things stand
### Dave — 2026-10-26T17:20:00+00:00
Master is at abc with repairs live.

### Vector — 2026-10-25T15:01:10+00:00
Latest CLI check reports tools up to date.

## In flight
### Dave — 2026-10-26T17:20:00+00:00
- Builder M5 still running in session 1111 <!-- confirmed: -->
- Where Sources fix on branch x (afa1d871) is unmerged <!-- confirmed: -->
- Continuity-loss diagnosis (Fable run 2222); findings to the journal <!-- confirmed:2026-10-26T17:20:00+00:00 -->
### Vector — 2026-10-25T15:01:10+00:00
- Position review: 97 due; no verdicts recorded <!-- confirmed: -->
- MEMORY.md NUL root-cause fix not applied <!-- confirmed: -->

## Promised
### Dave — 2026-10-26T17:20:00+00:00
- Report the builder's result when it returns
### Vector — 2026-10-25T15:01:10+00:00
- Finish position review of 97 due positions
"""


def test_round_trip_on_live_file_shape_does_not_grow_the_file(env, monkeypatch):
    fp = env[0] / mem.CONTINUITY_FILE
    fp.write_text(LIVE_SHAPE, encoding='utf-8')
    before = mem.read_continuity(P)
    monkeypatch.setattr(mem, 'now_iso', lambda: LATER)
    reworded = ['Builder M5 build still running in session 1111 (cont)',
                'Where Sources fix on branch x (afa1d871) is still unmerged',
                'Continuity-loss diagnosis (Fable run 2222); findings to the journal']
    mem.write_continuity(P, threads=reworded, owner='Dave', confirmed_threads=[])
    size1, lines1 = fp.stat().st_size, fp.read_text(encoding='utf-8').count('\n')
    mem.write_continuity(P, threads=reworded, owner='Dave', confirmed_threads=[])
    assert (fp.stat().st_size, fp.read_text(encoding='utf-8').count('\n')) == (size1, lines1)
    after = mem.read_continuity(P)
    assert len(after['threads']) == len(before['threads']) == 5
    assert after['by_owner']['Dave']['thread_confirmed'] == {
        reworded[0]: '', reworded[1]: '', reworded[2]: '2026-10-26T17:20:00+00:00'}
    assert after['by_owner']['Vector'] == before['by_owner']['Vector']
    assert len([l for l in env[1] if 'reworded' in l]) == 2
