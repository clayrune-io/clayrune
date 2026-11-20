"""The weekly memory health rules (tools/memory-eval/memory_health.py).

The weekly check measured memory getting worse for ten weeks and wrote each
week's numbers into a journal nobody is paged by. Its one alarm, "notes no
index line or link reaches", had filled with auto-written mints, so it read as
known noise, and hand-written notes went unreachable inside it.

These tests pin what makes the check tell a person: the count is split by who
wrote the note, four fixed thresholds decide what is a finding, each finding is
mailed once through the existing mailer, and nothing in the memory directory is
written. No test sends mail: the mailer is replaced at its one call boundary.
"""
import importlib
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
EVAL_DIR = PROJECT_ROOT / 'tools' / 'memory-eval'

P = {'id': 'p1'}
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f'could not load {path}'
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope='module')
def h():
    """`tools/memory-eval` is not a package; its modules import each other by
    bare name, so the directory goes on the path the way the tools put it."""
    if str(EVAL_DIR) not in sys.path:
        sys.path.insert(0, str(EVAL_DIR))
    return importlib.import_module('memory_health')


def _split(h, hand=(), position=(), mint=(), system=()):
    return {h.KIND_HAND: list(hand), h.KIND_POSITION: list(position),
            h.KIND_MINT: list(mint), h.KIND_SYSTEM: list(system)}


def _canary(recall=90.0, passed=1, total=1):
    return {'delivered': 9, 'pairs': 10, 'recall': recall,
            'contradiction_passed': passed, 'contradiction_total': total}


# ── 1. the split ────────────────────────────────────────────────────────────

def test_the_unreachable_count_is_split_by_who_wrote_the_note(h):
    names = ['feedback_a.md', 'decision_b.md', 'discovery_c.md', 'arch_d.md',
             'reference_e.md', 'project_f.md', 'something_else.md',
             'position_g.md', 'mint_docs_artifact_0a1b2c3d4e.md',
             'mint_backlog_done_5f6a7b8c9d.md']
    s = h.split_unreachable(names)
    assert s[h.KIND_HAND] == names[:7]
    assert s[h.KIND_POSITION] == ['position_g.md']
    assert s[h.KIND_MINT] == names[8:]
    assert s[h.KIND_SYSTEM] == []
    assert h.split_line(names) == \
        '  M1 by kind: hand-written 7 | positions 1 | mints 2 | system files 0'
    assert sum(len(v) for v in s.values()) == len(names), 'the kinds must add up to the total'


def test_the_memory_systems_own_files_are_not_hand_written_notes(h):
    """The invariant lists the session log and the continuity record as topic
    files. On a real vault both came out as "hand-written, unreachable", with a
    proposed link, in the first mail this would have sent."""
    from mc import memory as mem
    own = [mem.SESSION_LOG_FILE.lower(), mem.CONTINUITY_FILE]
    s = h.split_unreachable(own + ['feedback_a.md'])
    assert s[h.KIND_SYSTEM] == own and s[h.KIND_HAND] == ['feedback_a.md']
    assert h.rule_hand_unreachable(h.split_unreachable(own), {}, set())['finding'] is None


def test_an_unknown_prefix_counts_as_hand_written(h):
    """The safe direction: a wrongly raised finding can be dismissed, a note
    wrongly filed as auto-written disappears into the noise."""
    assert h.note_kind('habit_new_writer.md') == h.KIND_HAND
    assert h.note_kind('MINT_docs_x.md') == h.KIND_MINT
    assert h.split_unreachable([]) == _split(h)


# ── 2. each rule, both sides of its threshold ───────────────────────────────

def test_rule_a_one_hand_written_note_is_enough(h):
    quiet = h.rule_hand_unreachable(
        _split(h, position=['position_x.md'], mint=['mint_a_1.md'] * 600), {}, set())
    assert quiet['finding'] is None, 'auto-written notes alone raised the alarm'

    loud = h.rule_hand_unreachable(
        _split(h, hand=['feedback_x.md'], mint=['mint_a_1.md'] * 600),
        {'feedback_x.md': 'reference_y.md'}, set())
    f = loud['finding']
    assert f and f['subject'] == '1 hand-written note unreachable'
    assert f['body'].splitlines()[0].startswith('1 hand-written memory note can reach no agent')
    assert '- feedback_x.md: add [[feedback_x]] to reference_y.md' in f['body']
    assert 'hand-written 1, positions 0, mints 600, system files 0' in f['body']
    assert 'Nothing links to it' in f['body'] and 'it stays invisible' in f['body']


@pytest.mark.parametrize('recall, prior, want_streak, want_finding', [
    (70.0, 1, 0, False),   # exactly 10 under: "more than 10" is not met
    (69.9, 0, 1, False),   # under, but the first such run
    (69.9, 1, 2, True),    # under, second run in a row
    (75.0, 5, 0, False),   # recovered: the streak resets
])
def test_rule_b_recall_under_baseline_two_runs_running(h, recall, prior, want_streak,
                                                       want_finding):
    r, streak = h.rule_recall(_canary(recall=recall), 80.0, prior)
    assert streak == want_streak
    assert bool(r['finding']) is want_finding
    assert f'{80.0 - recall:.1f} points under' in r['status']


def test_rule_b_recall_above_baseline_reads_as_over(h):
    r, streak = h.rule_recall(_canary(recall=85.5), 80.0, 3)
    assert r['finding'] is None and streak == 0 and '5.5 points over' in r['status']


def test_rule_b_exactly_ten_under_is_not_a_finding_by_float_error(h):
    """81.8 - 71.8 is 10.000000000000014 in binary floating point."""
    r, streak = h.rule_recall(_canary(recall=71.8), 81.8, 1)
    assert r['finding'] is None and streak == 0


def test_rule_b_says_so_when_it_cannot_measure(h):
    r, streak = h.rule_recall(_canary(recall=10.0), None, 1)
    assert r['finding'] is None and streak == 1
    assert 'NOT MEASURED' in r['status'] and '--set-recall-baseline' in r['status']
    r, streak = h.rule_recall(None, 80.0, 1)
    assert r['finding'] is None and streak == 1 and 'NOT MEASURED' in r['status']


def test_rule_c_zero_passing_contradiction_pairs(h):
    f = h.rule_contradiction(_canary(passed=0, total=3))['finding']
    assert f and f['subject'] == 'contradiction pass rate 0 of 3'
    assert '0 of 3 pinned contradiction pairs pass.' in f['body']
    one = h.rule_contradiction(_canary(passed=0, total=1))['finding']
    assert '0 of 1 pinned contradiction pair passes.' in one['body']
    assert h.rule_contradiction(_canary(passed=1, total=3))['finding'] is None
    unpinned = h.rule_contradiction(_canary(passed=None, total=0))
    assert unpinned['finding'] is None and 'NOT MEASURED' in unpinned['status']
    assert h.rule_contradiction(None)['finding'] is None


def _snap(root, when, label='x'):
    d = root / f'{when.strftime("%Y%m%dT%H%M%SZ")}-{label}'
    (d / 'memory').mkdir(parents=True)
    return d


def test_rule_d_snapshot_age_on_both_sides_of_26_hours(h, tmp_path):
    root = tmp_path / 'snaps'
    assert h.rule_snapshot(h.newest_snapshot(root, NOW))['finding']['subject'] == \
        'no memory snapshot exists'
    root.mkdir()
    assert h.rule_snapshot(h.newest_snapshot(root, NOW))['finding']['subject'] == \
        'no memory snapshot exists'

    _snap(root, NOW - timedelta(days=40), 'old')
    stale = _snap(root, NOW - timedelta(hours=27), 'late')
    name, hours = h.newest_snapshot(root, NOW)
    assert name == stale.name and round(hours) == 27
    f = h.rule_snapshot((name, hours))['finding']
    assert f and f['subject'] == 'newest memory snapshot is 27 hours old'

    _snap(root, NOW - timedelta(hours=26), 'edge')
    assert h.rule_snapshot(h.newest_snapshot(root, NOW))['finding'] is None
    _snap(root, NOW - timedelta(hours=1), 'fresh')
    assert h.rule_snapshot(h.newest_snapshot(root, NOW))['finding'] is None


def test_the_snapshot_root_is_the_one_the_snapshot_tool_writes(h):
    snap_tool = _load('_mc_memory_snapshot', PROJECT_ROOT / 'tools' / 'memory-snapshot.py')
    assert h.SNAP_ROOT == snap_tool.SNAP_ROOT


def test_a_mail_body_stays_under_200_words_with_the_verdict_first(h):
    hand = [f'feedback_note_{i:02d}.md' for i in range(40)]
    f = h.rule_hand_unreachable(
        _split(h, hand=hand), {n: 'reference_target.md' for n in hand}, set())['finding']
    assert len(f['body'].split()) < 200
    assert f['body'].splitlines()[0].startswith('40 hand-written memory notes can reach no agent')
    assert '- and 35 more, listed in the run output' in f['body']
    assert 'Nothing links to them' in f['body'] and 'they stay invisible' in f['body']
    for rule in (h.rule_recall(_canary(recall=50.0), 80.0, 1)[0],
                 h.rule_contradiction(_canary(passed=0, total=1)),
                 h.rule_snapshot(None), h.rule_snapshot(('20260101T000000Z-x', 900.0))):
        assert len(rule['finding']['body'].split()) < 200


# ── 3 + 4. the whole run: raised once, mailed through the existing mailer ───

@pytest.fixture()
def env(h, tmp_path, monkeypatch):
    """A real `mc.memory` over a temp vault, with every other path the run
    touches moved out of the way and the mailer replaced by a recorder."""
    import server  # noqa: F401
    from mc import memory as mem
    vault = tmp_path / 'vault'
    vault.mkdir()
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: vault / 'MEMORY.md')
    (vault / 'MEMORY.md').write_text(
        mem._mem_compose('# Index\n- [Kitchen](reference_kitchen.md)\n', [], []),
        encoding='utf-8')
    (vault / 'reference_kitchen.md').write_text(
        '---\nname: reference-kitchen\ndescription: kettle descaling with vinegar\n---\n'
        'Descale the kettle monthly. Vinegar, then two rinses.\n', encoding='utf-8')
    (vault / 'feedback_dark.md').write_text(
        '---\nname: feedback-dark\ndescription: never descale the kettle with bleach\n---\n'
        'Use vinegar for the kettle, never bleach.\n', encoding='utf-8')
    (vault / 'mint_docs_artifact_0a1b2c3d4e.md').write_text(
        'kettle descaling vinegar kettle descaling vinegar\n', encoding='utf-8')

    state_file = tmp_path / 'state' / 'health_state.json'
    monkeypatch.setattr(h, 'STATE_FILE', state_file)
    monkeypatch.setattr(h, 'SNAP_ROOT', tmp_path / 'snaps')
    _snap(tmp_path / 'snaps', datetime.now(timezone.utc) - timedelta(hours=1))
    canary = {'value': _canary()}
    monkeypatch.setattr(h, 'measure_canaries', lambda *a, **k: canary['value'])

    sent = []

    def fake_mailer(argv):
        body = Path(argv[argv.index('--body-file') + 1])
        sent.append({'argv': list(argv), 'body': body.read_text(encoding='utf-8'),
                     'body_path': body})
        return fake_mailer.rc
    fake_mailer.rc = 0
    monkeypatch.setattr(h, '_run_mailer', fake_mailer)

    def run(violations, notify=True):
        return h.report(mem, P, violations,
                        {'reference_kitchen.md', 'mint_docs_artifact_0a1b2c3d4e.md'},
                        6, 2, notify=notify)
    return {'run': run, 'sent': sent, 'vault': vault, 'state_file': state_file,
            'mailer': fake_mailer, 'canary': canary, 'tmp': tmp_path}


def test_without_notify_it_prints_what_it_would_send_and_records_nothing(env, capsys):
    fresh = env['run'](['feedback_dark.md'], notify=False)
    out = capsys.readouterr().out
    assert [f['rule'] for f in fresh] == ['a']
    assert env['sent'] == [], 'a run without --notify called the mailer'
    assert not env['state_file'].exists(), 'a run without --notify recorded state'
    assert 'WOULD SEND 1 mail' in out
    assert '--- subject: [Clayrune memory] 1 hand-written note unreachable' in out
    assert '| - feedback_dark.md: add [[feedback_dark]] to reference_kitchen.md' in out


def test_notify_calls_the_existing_mailer_with_subject_and_body_file(env, h):
    env['run'](['feedback_dark.md'])
    assert len(env['sent']) == 1
    argv = env['sent'][0]['argv']
    assert argv[:2] == [sys.executable,
                        str(PROJECT_ROOT / 'tools' / 'night-review' / 'send_mail.py')]
    assert argv[2:5] == ['--subject', '[Clayrune memory] 1 hand-written note unreachable',
                         '--body-file']
    assert len(argv) == 6
    body = env['sent'][0]['body']
    assert body.splitlines()[0].startswith('1 hand-written memory note can reach no agent')
    assert len(body.split()) < 200
    assert env['vault'] not in env['sent'][0]['body_path'].parents
    assert not env['sent'][0]['body_path'].exists(), 'the temp body file was left behind'


def test_the_proposed_link_comes_from_a_reachable_hand_written_note(env):
    """The mint shares more vocabulary and is in the reachable set, but a link
    from an auto-written note is not a fix a person would write."""
    env['run'](['feedback_dark.md'])
    assert '- feedback_dark.md: add [[feedback_dark]] to reference_kitchen.md' \
        in env['sent'][0]['body']


def test_a_finding_is_raised_once_across_two_runs(env):
    env['run'](['feedback_dark.md'])
    env['run'](['feedback_dark.md'])
    assert len(env['sent']) == 1, 'the same finding was mailed a second week running'


def test_a_finding_re_arms_when_it_clears_and_recurs(env):
    env['run'](['feedback_dark.md'])
    env['run']([])
    assert len(env['sent']) == 1
    env['run'](['feedback_dark.md'])
    assert len(env['sent']) == 2, 'the condition recurred and stayed silent'


def test_a_new_dark_note_is_reported_and_a_fixed_one_is_not(env):
    (env['vault'] / 'decision_other.md').write_text('pan seasoning with oil\n',
                                                    encoding='utf-8')
    env['run'](['feedback_dark.md'])
    env['run'](['decision_other.md', 'feedback_dark.md'])
    assert len(env['sent']) == 2, 'a second note went dark behind a known one, unreported'
    assert '(1 new since the last report)' in env['sent'][1]['body']
    env['run'](['decision_other.md'])
    assert len(env['sent']) == 2, 'fixing one of two notes re-sent the other'


def test_each_rule_sends_its_own_mail_once(env, h, monkeypatch):
    monkeypatch.setattr(h, 'SNAP_ROOT', env['tmp'] / 'no-snapshots-here')
    env['state_file'].parent.mkdir(parents=True)
    env['state_file'].write_text('{"recall_baseline": 80.0, "recall_under_streak": 1}',
                                 encoding='utf-8')
    env['canary']['value'] = _canary(recall=60.0, passed=0, total=2)
    env['run'](['feedback_dark.md'])
    subjects = [s['argv'][3] for s in env['sent']]
    assert subjects == [
        '[Clayrune memory] 1 hand-written note unreachable',
        '[Clayrune memory] planted-fact recall 60.0%, 20.0 points under baseline',
        '[Clayrune memory] contradiction pass rate 0 of 2',
        '[Clayrune memory] no memory snapshot exists',
    ]
    env['run'](['feedback_dark.md'])
    assert len(env['sent']) == 4, 'a standing condition was mailed twice'


def test_the_recall_streak_is_carried_between_notify_runs(env, h):
    env['state_file'].parent.mkdir(parents=True)
    env['state_file'].write_text('{"recall_baseline": 80.0}', encoding='utf-8')
    env['canary']['value'] = _canary(recall=60.0)
    env['run']([])
    assert env['sent'] == [], 'one run under baseline was mailed'
    env['run']([], notify=False)
    assert env['sent'] == []
    assert h.read_state()['recall_under_streak'] == 1, 'a dry run advanced the streak'
    env['run']([])
    assert [s['argv'][3] for s in env['sent']] == [
        '[Clayrune memory] planted-fact recall 60.0%, 20.0 points under baseline']
    assert h.read_state()['recall_baseline'] == 80.0


def test_a_failed_send_is_tried_again_next_run(env, capsys):
    env['mailer'].rc = 1
    env['run'](['feedback_dark.md'])
    assert 'SEND FAILED' in capsys.readouterr().out
    env['mailer'].rc = 0
    env['run'](['feedback_dark.md'])
    assert len(env['sent']) == 2, 'a mail that never went out was recorded as raised'
    env['run'](['feedback_dark.md'])
    assert len(env['sent']) == 2


# ── 5. it reports, it never edits ───────────────────────────────────────────

def _tree(root):
    return {str(f.relative_to(root)): (f.read_bytes(), f.stat().st_mtime_ns)
            for f in sorted(root.rglob('*')) if f.is_file()}


def test_nothing_under_the_memory_directory_is_written(env, h, monkeypatch):
    """Every rule firing, mail sent, state recorded: the vault is byte-for-byte
    and mtime-for-mtime what it was. The link is a draft in the mail."""
    monkeypatch.setattr(h, 'SNAP_ROOT', env['tmp'] / 'no-snapshots-here')
    env['state_file'].parent.mkdir(parents=True)
    env['state_file'].write_text('{"recall_baseline": 80.0, "recall_under_streak": 1}',
                                 encoding='utf-8')
    env['canary']['value'] = _canary(recall=60.0, passed=0, total=1)
    before = _tree(env['vault'])

    env['run'](['feedback_dark.md'], notify=False)
    env['run'](['feedback_dark.md'])
    env['run'](['feedback_dark.md'])

    assert len(env['sent']) == 4
    assert _tree(env['vault']) == before, 'the health run wrote into the memory directory'
    assert env['state_file'].is_file()
    assert env['vault'] not in env['state_file'].parents


def test_the_real_state_file_is_outside_any_memory_directory():
    """Checked on a fresh import, not the fixture's override."""
    fresh = _load('_mc_memory_health_fresh', EVAL_DIR / 'memory_health.py')
    assert fresh.STATE_FILE == PROJECT_ROOT / 'data' / 'memory-eval' / 'health_state.json'


def test_the_recall_baseline_is_set_once_by_hand_and_kept(env, h, monkeypatch, capsys):
    monkeypatch.setattr(h._harness, 'assert_no_server', lambda: None)
    monkeypatch.setattr(sys, 'argv', ['memory_health.py'])
    h.main()
    assert 'recall baseline : not set' in capsys.readouterr().out
    assert not env['state_file'].exists(), 'looking at the state created it'

    monkeypatch.setattr(sys, 'argv', ['memory_health.py', '--set-recall-baseline', '81.84'])
    h.main()
    assert 'recall baseline : 81.8%' in capsys.readouterr().out
    env['run'](['feedback_dark.md'])
    state = h.read_state()
    assert state['recall_baseline'] == 81.8, 'a run dropped the stored baseline'
    assert state['flagged'], 'setting the baseline lost the raised-once record'

    monkeypatch.setattr(sys, 'argv', ['memory_health.py', '--set-recall-baseline', '0'])
    with pytest.raises(SystemExit):
        h.main()


# ── the canary numbers come from the probe's own functions ──────────────────

def test_recall_and_contradictions_are_measured_with_the_canary_probe(env, h, monkeypatch):
    import json
    monkeypatch.undo()   # drop the fixture's measure_canaries stub (and the rest)
    from mc import memory as mem
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: env['vault'] / 'MEMORY.md')
    fc = importlib.import_module('fact_canary_probe')
    canaries = env['tmp'] / 'canaries.jsonl'
    pairs = env['tmp'] / 'pairs.jsonl'
    monkeypatch.setattr(fc, 'CANARIES_FILE', canaries)
    monkeypatch.setattr(fc, 'PAIRS_FILE', pairs)
    assert h.measure_canaries(mem, P, 6, 2) is None, 'no canaries pinned is not a zero'

    canaries.write_text(json.dumps({
        'id': 'c1', 'fact': 'vinegar for the kettle', 'date_recorded': '2026-01-01',
        'queries': ['how do I descale the kettle', 'zzqx nothing matches this'],
        'match_type': 'file', 'match_value': 'reference_kitchen.md'}) + '\n',
        encoding='utf-8')
    got = h.measure_canaries(mem, P, 6, 2)
    assert (got['delivered'], got['pairs'], got['recall']) == (1, 2, 50.0)
    assert got['contradiction_total'] == 0

    pairs.write_text(json.dumps({
        'id': 'p1', 'subject': 'kettle', 'query': 'descale the kettle',
        'stale': {'match_type': 'file', 'match_value': 'no_such_note.md'},
        'corrected': {'match_type': 'file', 'match_value': 'reference_kitchen.md'}}) + '\n',
        encoding='utf-8')
    got = h.measure_canaries(mem, P, 6, 2)
    assert (got['contradiction_passed'], got['contradiction_total']) == (1, 1)


# ── eval.py only calls in ───────────────────────────────────────────────────

def test_eval_prints_the_split_and_hands_the_run_to_the_rules(env, h, monkeypatch, capsys):
    from mc import memory as mem
    ev = _load('_mc_memory_eval', EVAL_DIR / 'eval.py')
    assert ev.memory_health is h
    monkeypatch.setattr(ev._harness, 'wire', lambda **k: (mem, P))
    monkeypatch.setattr(ev._harness, 'assert_no_server', lambda: None)
    monkeypatch.setattr(ev, 'load_suite', lambda: ['zzqx nothing matches this'])
    monkeypatch.setattr(ev, 'deadband', lambda m, p: {
        'mode': 'x', 'lines': 1, 'bytes': 1, 'line_trigger': 1, 'line_floor': 2,
        'byte_trigger': 1, 'byte_floor': 2, 'byte_half_suppressed': False,
        'line_half_live': True})
    calls = []
    monkeypatch.setattr(h, 'report', lambda *a, **k: calls.append((a, k)))

    violations = ev.run(notify=True)
    out = capsys.readouterr().out
    assert violations == ['feedback_dark.md', 'mint_docs_artifact_0a1b2c3d4e.md']
    assert '  M1 VIOLATIONS (uncited AND unreachable): 2' in out, 'the old total is the trend line'
    assert '  M1 by kind: hand-written 1 | positions 0 | mints 1 | system files 0' in out
    (args, kwargs), = calls
    assert args[2] == violations and args[3] == {'reference_kitchen.md'}
    assert kwargs == {'notify': True}

    calls.clear()
    ev.run(canary=True, notify=True)
    assert calls == [], 'the rules ran against the injected-canary copy'
