"""ML24 (MC-1179): the push observer's work and its log are bounded, and the
two columns it got wrong are right.

`mc/memory_push.py` ships in report mode: it delivers nothing to the agent and
its log is the whole deliverable. On the reviewed install that log was
129.6 MB (273,371 rows, 219,060 of them `near_miss`) with no cap, and every
Read/Edit/Write/Grep ran a full `_memory_search` on the thread that reads the
agent's stream. Nothing tested a bound. These do:

  - one query-bearing call in N per session reaches the search;
  - a call logs at most two `near_miss` rows (the two best-ranked);
  - the log rotates at a fixed size, and a log from before the bound is set
    aside under a name rotation never overwrites;
  - a bare number from 400 to 599 (a line number, a byte count) is not an
    HTTP status;
  - `already_delivered_by_per_turn` compares unit ids with unit ids.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — triggers memory.wire()
    from mc import memory as mem
    from mc import memory_push as mp

    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    (tmp_path / 'MEMORY.md').write_text('# index\n', encoding='utf-8')
    data_root = tmp_path / 'data_root'
    monkeypatch.setattr(mem, 'DATA_DIR', data_root / 'data' / 'projects')
    return mem, mp, tmp_path, data_root


P = {'id': 'p1'}


def _log_file(data_root, pid='p1'):
    return data_root / 'data' / 'memory_push_log' / f'{pid}.jsonl'


def _rows(data_root):
    f = _log_file(data_root)
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding='utf-8').splitlines()]


def _seed_notes(tmp_path, n):
    for i in range(n):
        (tmp_path / f'discovery_cancel_route_note_{i}.md').write_text(
            f"---\nname: discovery-cancel-route-note-{i}\n"
            f"description: \"Note {i} about the workflow cancel route 404 not found.\"\n---\n"
            f"Workflow cancel route returned 404 not found, variant {i}.\n",
            encoding='utf-8')


QUERY = "workflow cancel route 404 not found"


# ── sampling: the search does not run on every call ─────────────────────────

def test_only_one_call_in_n_reaches_the_search(env, monkeypatch):
    mem, mp, tmp_path, _data_root = env
    _seed_notes(tmp_path, 1)
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'memory_push_sample_every', 5)

    calls = []
    real = mem._memory_search

    def _counting(*a, **kw):
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(mem, '_memory_search', _counting)

    session = {'session_id': 'sess-sample', 'project_id': 'p1'}
    for _ in range(20):
        mp.observe(P, session, 'Read', 'tool_input', QUERY)

    assert len(calls) == 4, "20 calls at one in 5 is 4 searches"


def test_the_first_call_of_a_session_is_observed(env, monkeypatch):
    """A short session still contributes: sampling starts with a hit."""
    mem, mp, tmp_path, _data_root = env
    _seed_notes(tmp_path, 1)
    calls = []
    real = mem._memory_search
    monkeypatch.setattr(mem, '_memory_search',
                        lambda *a, **kw: calls.append(1) or real(*a, **kw))

    mp.observe(P, {'session_id': 'short', 'project_id': 'p1'},
               'Read', 'tool_input', QUERY)
    assert len(calls) == 1


def test_sampling_is_per_session_not_global(env, monkeypatch):
    mem, mp, tmp_path, _data_root = env
    _seed_notes(tmp_path, 1)
    calls = []
    real = mem._memory_search
    monkeypatch.setattr(mem, '_memory_search',
                        lambda *a, **kw: calls.append(1) or real(*a, **kw))

    for i in range(3):
        mp.observe(P, {'session_id': f's{i}', 'project_id': 'p1'},
                   'Read', 'tool_input', QUERY)
    assert len(calls) == 3


# ── near-miss rows: the two best-ranked, no more ────────────────────────────

def test_one_call_logs_at_most_two_near_miss_rows(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_notes(tmp_path, 5)
    from mc import state
    hits = mem._memory_search(P, QUERY, topk=6, expand=0, record=None)
    assert len(hits) >= 4, "fixture must give the search more than two candidates"
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score',
                         max(h['score'] for h in hits) + 50.0)

    mp.observe(P, {'session_id': 'sess-nm', 'project_id': 'p1'},
               'Bash', 'tool_result', QUERY)

    rows = _rows(data_root)
    assert rows and all(r['result'] == 'near_miss' for r in rows)
    assert len(rows) == 2
    assert sorted(r['rank'] for r in rows) == [1, 2]


# ── the log is bounded ──────────────────────────────────────────────────────

def test_log_rotates_at_the_size_bound(tmp_path):
    from mc import memory_push_log as pl
    f = tmp_path / 'p1.jsonl'
    row = {'k': 'x' * 100}
    for _ in range(200):
        pl.append_row(f, row, max_bytes=2000)

    assert f.stat().st_size <= 2000 + 200
    prev = tmp_path / 'p1.jsonl.1'
    assert prev.exists()
    assert prev.stat().st_size <= 2000 + 200
    total = sum(p.stat().st_size for p in tmp_path.iterdir())
    assert total <= 2 * (2000 + 200), "two generations, never more"
    # Every line in both generations is still a whole JSON row.
    for p in (f, prev):
        for ln in p.read_text(encoding='utf-8').splitlines():
            assert json.loads(ln) == row


def test_a_log_from_before_the_bound_is_set_aside_not_deleted(tmp_path):
    from mc import memory_push_log as pl
    f = tmp_path / 'p1.jsonl'
    old = ''.join(json.dumps({'old': i}) + '\n' for i in range(2000))
    f.write_text(old, encoding='utf-8')
    assert f.stat().st_size > 2 * 2000
    logged = []

    row = {'k': 'x' * 100}
    for _ in range(200):          # enough to rotate several times afterwards
        pl.append_row(f, row, log=logged.append, max_bytes=2000)

    kept = tmp_path / 'p1.pre-cap.jsonl'
    assert kept.exists(), "the pre-bound log must survive later rotations"
    assert kept.read_text(encoding='utf-8') == old
    assert any('pre-cap' in m for m in logged)
    assert f.stat().st_size <= 2000 + 200


def test_append_never_raises_when_rotation_is_refused(tmp_path, monkeypatch):
    from mc import memory_push_log as pl
    f = tmp_path / 'p1.jsonl'
    f.write_text('x' * 3000 + '\n', encoding='utf-8')
    logged = []

    def _refuse(*a, **k):
        raise PermissionError('in use')
    monkeypatch.setattr(pl._os, 'replace', _refuse)
    monkeypatch.setattr(pl._os, 'rename', _refuse)
    monkeypatch.setattr(pl, '_rotate_warned', set())

    pl.append_row(f, {'a': 1}, log=logged.append, max_bytes=2000)
    pl.append_row(f, {'a': 2}, log=logged.append, max_bytes=2000)

    lines = f.read_text(encoding='utf-8').splitlines()
    assert json.loads(lines[-1]) == {'a': 2}, "the row is appended anyway"
    assert len(logged) == 1, "one line per file, not one per row"


def test_observer_writes_through_the_bounded_log(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_notes(tmp_path, 1)
    from mc import memory_push_log as pl
    from mc import state
    monkeypatch.setattr(pl, 'MAX_BYTES', 600)
    monkeypatch.setitem(state.CONFIG, 'memory_push_sample_every', 1)
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score', 10 ** 6)

    for i in range(12):
        mp.observe(P, {'session_id': f's{i}', 'project_id': 'p1'},
                   'Bash', 'tool_result', QUERY)

    f = _log_file(data_root)
    assert f.with_name(f.name + '.1').exists(), "the observer's log rotated"
    assert f.stat().st_size <= 600 + 600


# ── a bare number from 400 to 599 is not an HTTP status ─────────────────────

@pytest.mark.parametrize('text', [
    'wrote 512 bytes to out.bin',
    '   404\tdef handler():',
    'listening on 127.0.0.1 with 450 workers',
    'total 588',
])
def test_a_bare_number_is_not_a_failure_signal(text):
    from mc import memory_push as mp
    assert mp.extract_result_query(text) == ''


@pytest.mark.parametrize('text', [
    'HTTP/1.1 404 Not-Here',
    'response status 503',
    'status_code=500',
    'statusCode: 502',
    'exit code 404',
    '< http/2 429',
])
def test_a_status_code_in_http_context_still_is(text):
    from mc import memory_push as mp
    assert mp.extract_result_query(text) == text


# ── already_delivered_by_per_turn: unit ids against unit ids ────────────────

ARCHIVE_LINE = "- [2026-09-17] **top-up** — Codex credits added, access problem cleared."
ARCHIVE_OTHER = "- [2026-09-18] **W3 allowance state** — Codex allowance exhausted, await reset."


def _archive_fire(env, monkeypatch, delivered):
    mem, mp, tmp_path, data_root = env
    (tmp_path / 'MEMORY_ARCHIVE.md').write_text(
        ARCHIVE_LINE + '\n' + ARCHIVE_OTHER + '\n', encoding='utf-8')
    from mc import state
    query = "Codex credits added access problem cleared"
    hits = mem._memory_search(P, query, topk=5, expand=0, record=None,
                               keep_internal=True)
    target = next(h for h in hits if h['head'].startswith('- [2026-09-17]'))
    other = next(h for h in hits if h['head'].startswith('- [2026-09-18]'))
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score',
                         target['score'] / mp._ARCHIVE_SCORE_MULTIPLIER - 0.01)
    session = {'session_id': 'sess-uid', 'project_id': 'p1',
               '_mem_turn_delivered': delivered(target, other)}
    mp.observe(P, session, 'Bash', 'tool_result', query)
    fires = [r for r in _rows(data_root) if r['result'] == 'would_send']
    assert len(fires) == 1 and fires[0]['line'].startswith('- [2026-09-17]')
    return fires[0]


def test_already_delivered_is_true_for_the_archive_line_per_turn_sent(env, monkeypatch):
    """`mc.memory_turn` records an archive hit by its unit id
    (`MEMORY_ARCHIVE.md#<hash>`), never by the bare file name."""
    row = _archive_fire(env, monkeypatch, lambda target, other: {target['uid']})
    assert row['already_delivered_by_per_turn'] is True


def test_already_delivered_is_false_when_a_different_archive_line_was_sent(env, monkeypatch):
    row = _archive_fire(env, monkeypatch, lambda target, other: {other['uid']})
    assert row['already_delivered_by_per_turn'] is False


# ── the hand-run reader sees every generation ───────────────────────────────

_READER_SCRIPT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
import push_review
print(json.dumps([r['n'] for r in push_review.read_rows(__import__('pathlib').Path(sys.argv[2]))]))
'''


def test_push_review_reads_every_generation_oldest_first(tmp_path):
    """`tools/memory-eval/push_review.py` is the log's one reader. Rotation
    must not hide the previous generation, or a pre-bound log, from it."""
    import os
    import subprocess
    cur = tmp_path / 'p1.jsonl'
    (tmp_path / 'p1.pre-cap.jsonl').write_text('{"n": 1}\n{"n": 2}\n', encoding='utf-8')
    (tmp_path / 'p1.jsonl.1').write_text('{"n": 3}\n', encoding='utf-8')
    cur.write_text('{"n": 4}\nnot json\n{"n": 5}\n', encoding='utf-8')
    (tmp_path / 'p10.jsonl').write_text('{"n": 99}\n', encoding='utf-8')  # another project

    r = subprocess.run(
        [sys.executable, '-c', _READER_SCRIPT,
         str(PROJECT_ROOT / 'tools' / 'memory-eval'), str(cur)],
        capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT),
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1]) == [1, 2, 3, 4, 5]
