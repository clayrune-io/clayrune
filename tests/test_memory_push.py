"""mc/memory_push.py — mid-task memory push observer (MC-944, Ron's idea
2026-09-14, `~/.claude/plans/memory-architecture-resolve.md` "Mid-task memory
push"). REPORT MODE ONLY.

WHAT THIS CLOSES. Per-turn memory delivery (mc.memory_turn, step 5) only ever
looks at the USER's message. Nothing previously watched what a TOOL produces
mid-turn: an error string, an HTTP status, a file it opens. This module is
that second channel — see its module docstring for the full defect writeup
(the 2026-09-14 workflow-cancel-404 case).

CALIBRATION NOTE. The module's shipped default (`memory_push_min_score`,
15.0) is calibrated against the live, ~600-unit mission_control vault (see
mc/memory_push.py's docstring). A test fixture's tiny 1-3-note corpus scores
on a completely different scale (BM25 is corpus-size-sensitive by design —
see mc.memory._memory_search's own docstring on document-length
normalization), so these tests calibrate their OWN threshold per case by
first measuring the real score mc.memory._memory_search returns for the
fixture, rather than asserting against the shipped constant. That constant is
a deployment tuning input, not something this suite re-derives.
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

NOTE_TEXT = """---
name: workflow-cancel-404-stale-server
description: "Workflow cancel route 404s when the running server predates the commit that added it — restart clears it."
metadata:
  type: project
---

The workflow cancel route returned 404 not found because the live server
process was started before the commit that added the /api/workflow/cancel
endpoint. A stale running process is the class of bug: restart the server
after any route addition, don't assume the code on disk is the code running.
"""


def _seed_note(tmp_path):
    (tmp_path / 'discovery_workflow_cancel_404_stale_server.md').write_text(
        NOTE_TEXT, encoding='utf-8')


def _log_file(data_root, pid='p1'):
    return data_root / 'data' / 'memory_push_log' / f'{pid}.jsonl'


def _measured_score(mem, tmp_path, query):
    """The real BM25 score mc.memory._memory_search gives `query` against the
    fixture corpus — the ground truth these tests calibrate thresholds
    against, per the module docstring above."""
    hits = mem._memory_search(P, query, topk=5, expand=0, record=None)
    for h in hits:
        if h['file'].startswith('discovery_workflow_cancel_404'):
            return h['score']
    return 0.0


# ── extraction (pure, no I/O) ───────────────────────────────────────────────

def test_extract_result_query_finds_http_status_and_error_lines():
    from mc import memory_push as mp
    text = "GET /api/workflow/cancel\nHTTP/1.1 404 Not Found\nok done"
    q = mp.extract_result_query(text)
    assert '404' in q
    assert 'Not Found' in q or 'not found' in q.lower()


def test_extract_result_query_finds_exception_name():
    from mc import memory_push as mp
    text = "Traceback (most recent call last):\n  ...\nKeyError: 'project_id'"
    q = mp.extract_result_query(text)
    assert 'KeyError' in q


def test_extract_result_query_empty_on_success_only_output():
    from mc import memory_push as mp
    assert mp.extract_result_query('total 12\ndrwxr-xr-x  file1.txt\nfile2.txt') == ''
    assert mp.extract_result_query('') == ''
    assert mp.extract_result_query('   \n  ') == ''


def test_extract_result_query_scans_bounded_window_on_huge_dump():
    from mc import memory_push as mp
    # Error line sits well past the head window and before the tail window —
    # a naive full-text regex would find it; the bounded scan must not.
    filler = 'x' * 200000
    text = filler[:100000] + '\nerror: this should not be found\n' + filler[100000:]
    assert mp.extract_result_query(text) == ''
    # ...but an error line IN the tail window is found.
    text2 = filler + '\nTypeError: boom'
    assert 'TypeError' in mp.extract_result_query(text2)


def test_extract_input_query_for_read_edit_write_uses_basename():
    from mc import memory_push as mp
    assert mp.extract_input_query('Read', {'file_path': 'C:/repo/mc/agent_routes.py'}) == 'agent_routes.py'
    assert mp.extract_input_query('Edit', {'file_path': '/a/b/server.py'}) == 'server.py'
    assert mp.extract_input_query('Write', {'file_path': ''}) == ''


def test_extract_input_query_for_grep_uses_pattern_and_path():
    from mc import memory_push as mp
    q = mp.extract_input_query('Grep', {'pattern': 'tool_result', 'path': '/a/agent_routes.py'})
    assert 'tool_result' in q and 'agent_routes.py' in q


def test_extract_input_query_empty_for_unrelated_tools():
    from mc import memory_push as mp
    assert mp.extract_input_query('Bash', {'command': 'ls'}) == ''
    assert mp.extract_input_query('TodoWrite', {'todos': []}) == ''


# ── observe(): the accepted fire case ───────────────────────────────────────

def test_tool_result_with_404_surfaces_matching_note_above_threshold(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)
    from mc import state

    query = mp.extract_result_query(
        "curl -i http://localhost/api/workflow/cancel\nHTTP/1.1 404 Not Found")
    assert query

    score = _measured_score(mem, tmp_path, query)
    assert score > 0, "fixture note must actually score against this query"
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score', score - 0.01)

    session = {'session_id': 'sess-1', 'project_id': 'p1', 'provider': 'claude'}
    fired = mp.observe(P, session, 'Bash', 'tool_result', query)

    assert fired == ['discovery_workflow_cancel_404_stale_server.md']
    log_file = _log_file(data_root)
    assert log_file.exists(), "the log IS the deliverable"
    rows = [json.loads(l) for l in log_file.read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row['result'] == 'would_send'
    assert row['source'] == 'tool_result'
    assert row['tool'] == 'Bash'
    assert row['note'] == 'discovery_workflow_cancel_404_stale_server.md'
    assert row['note_class'] == 'topic'
    assert row['already_delivered_by_per_turn'] is False


def test_tool_result_with_exception_name_surfaces_matching_note(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    (tmp_path / 'discovery_keyerror_project_id_missing.md').write_text(
        "---\nname: discovery-keyerror-project-id-missing\n"
        "description: \"KeyError project_id when a session dict is missing the key — always use .get\"\n---\n"
        "A raw session['project_id'] raises KeyError when the session dict has no such "
        "key; always read it with .get('project_id', '') instead.\n",
        encoding='utf-8')
    from mc import state

    query = mp.extract_result_query(
        "Traceback (most recent call last):\nKeyError: 'project_id'")
    assert query

    hits = mem._memory_search(P, query, topk=5, expand=0, record=None)
    target = next(h for h in hits if h['file'].startswith('discovery_keyerror'))
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score', target['score'] - 0.01)

    session = {'session_id': 'sess-2', 'project_id': 'p1'}
    fired = mp.observe(P, session, 'Bash', 'tool_result', query)
    assert fired == ['discovery_keyerror_project_id_missing.md']


# ── archive-line identity (MC-964 Step B / RC4) ─────────────────────────────
# RC4: `data/memory_push_log/mission_control.jsonl` recorded archive hits as
# `note: "MEMORY_ARCHIVE.md"` with no line id — indistinguishable across
# ~2.5k lines sharing that one filename, so "was the top-up fact ever near"
# was unanswerable from telemetry alone. These tests pin the fix: an archive
# row now carries a `line` field (the line's own leading date + first 80
# chars) alongside the unchanged `note` (container filename).

ARCHIVE_LINE = (
    "- [2026-09-17] **top-up** — Codex credits added, access problem cleared."
)
ARCHIVE_DISTRACTOR = (
    "- [2026-09-18] **W3 allowance state** — Codex allowance exhausted, "
    "await reset."
)


def _seed_archive(tmp_path, lines):
    (tmp_path / 'MEMORY_ARCHIVE.md').write_text(
        '\n'.join(lines) + '\n', encoding='utf-8')


def test_archive_hit_row_carries_line_identity_not_just_filename(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_archive(tmp_path, [ARCHIVE_LINE, ARCHIVE_DISTRACTOR])
    from mc import state

    # Built directly (not via extract_result_query's error-signal filter) —
    # the words the archive line's own content uses, same as
    # codex_miss_repro.py's oracle query construction.
    query = "Codex credits added access problem cleared"

    hits = mem._memory_search(P, query, topk=5, expand=0, record=None,
                               keep_internal=True)
    target = next(h for h in hits if h['file'] == 'MEMORY_ARCHIVE.md'
                  and h['head'].startswith('- [2026-09-17]'))
    # Clear the archive multiplier bar (mp._ARCHIVE_SCORE_MULTIPLIER) with
    # the real measured score, same calibration discipline as the topic tests.
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score',
                         target['score'] / mp._ARCHIVE_SCORE_MULTIPLIER - 0.01)

    session = {'session_id': 'sess-arch-1', 'project_id': 'p1'}
    fired = mp.observe(P, session, 'Bash', 'tool_result', query)
    assert fired == ['MEMORY_ARCHIVE.md']

    rows = [json.loads(l) for l in
            _log_file(data_root).read_text(encoding='utf-8').splitlines()]
    fires = [r for r in rows if r['result'] == 'would_send']
    assert len(fires) == 1
    row = fires[0]
    assert row['note'] == 'MEMORY_ARCHIVE.md'
    assert row['note_class'] == 'archive'
    # The line id is the ARCHIVE LINE's own date + content, not the
    # container filename repeated — this is the actual RC4 fix.
    assert row['line'].startswith('- [2026-09-17]')
    assert 'Codex credits' in row['line']
    assert len(row['line']) <= 80


def test_topic_and_position_hits_carry_no_line_field(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)
    from mc import state

    query = mp.extract_result_query(
        "curl -i http://localhost/api/workflow/cancel\nHTTP/1.1 404 Not Found")
    score = _measured_score(mem, tmp_path, query)
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score', score - 0.01)

    session = {'session_id': 'sess-topic-line', 'project_id': 'p1'}
    mp.observe(P, session, 'Bash', 'tool_result', query)

    rows = [json.loads(l) for l in
            _log_file(data_root).read_text(encoding='utf-8').splitlines()]
    assert rows and all(r.get('line', '') == '' for r in rows), \
        "a topic hit is already fully identified by its filename alone"


def test_step_b_log_alone_answers_whether_a_line_appeared_in_a_window(env, monkeypatch):
    """The RC4 acceptance test, in miniature: given only the push-log rows
    from a "window" of tool-stream observations, can a reader tell whether a
    SPECIFIC archive line appeared — without re-running `_memory_search`?"""
    mem, mp, tmp_path, data_root = env
    _seed_archive(tmp_path, [ARCHIVE_LINE, ARCHIVE_DISTRACTOR])
    from mc import state

    on_topic_query = "Codex credits added access problem cleared"
    off_topic_query = mp.extract_result_query(
        "Traceback (most recent call last):\nKeyError: 'unrelated_field'")

    hits = mem._memory_search(P, on_topic_query, topk=5, expand=0,
                               record=None, keep_internal=True)
    target = next(h for h in hits if h['head'].startswith('- [2026-09-17]'))
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score',
                         target['score'] / mp._ARCHIVE_SCORE_MULTIPLIER - 0.01)

    # A "window" of tool-stream observations: one on-topic, one that
    # shares nothing with either archive line.
    session = {'session_id': 'sess-window', 'project_id': 'p1'}
    mp.observe(P, session, 'Bash', 'tool_result', on_topic_query)
    if off_topic_query:
        mp.observe(P, session, 'Bash', 'tool_result', off_topic_query)

    # "Re-run the window from logs alone": read the jsonl, no corpus access.
    rows = [json.loads(l) for l in
            _log_file(data_root).read_text(encoding='utf-8').splitlines()]

    def fired(date_prefix, needle):
        # 'would_send' == this specific line actually surfaced in a turn,
        # not merely shared a token with the query as a ranked-but-suppressed
        # candidate (a near_miss row) — the RC4 question is about DELIVERY.
        return any(r.get('note') == 'MEMORY_ARCHIVE.md'
                   and r.get('result') == 'would_send'
                   and r.get('line', '').startswith(date_prefix)
                   and needle in r.get('line', '')
                   for r in rows)

    assert fired('- [2026-09-17]', 'Codex credits'), \
        "the top-up line's own identity must be recoverable from the log alone"
    assert not fired('- [2026-09-18]', 'exhausted'), \
        "a distractor line that never cleared the bar must not read as delivered"


# ── the inverse: noise produces nothing ─────────────────────────────────────

def test_noise_output_surfaces_nothing(env):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)

    session = {'session_id': 'sess-3', 'project_id': 'p1'}
    # extract_result_query itself returns '' for plain success output — the
    # cheap pre-filter agent_routes.py relies on to skip load_project entirely.
    q = mp.extract_result_query('total 8\ndrwxr-xr-x file1.txt\nfile2.txt')
    assert q == ''
    fired = mp.observe(P, session, 'Bash', 'tool_result', q)
    assert fired == []
    assert not _log_file(data_root).exists()


def test_below_threshold_logs_near_miss_not_a_fire(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)
    from mc import state

    query = mp.extract_result_query(
        "curl -i http://localhost/api/workflow/cancel\nHTTP/1.1 404 Not Found")
    score = _measured_score(mem, tmp_path, query)
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score', score + 5.0)

    session = {'session_id': 'sess-4', 'project_id': 'p1'}
    fired = mp.observe(P, session, 'Bash', 'tool_result', query)
    assert fired == []

    log_file = _log_file(data_root)
    assert log_file.exists(), "a below-threshold candidate still logs a near-miss row"
    rows = [json.loads(l) for l in log_file.read_text(encoding='utf-8').splitlines()]
    assert rows and all(r['result'] == 'near_miss' for r in rows)


# ── dedupe + cap ─────────────────────────────────────────────────────────────

def test_dedupe_same_note_not_pushed_twice_in_one_session(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)
    from mc import state

    query = mp.extract_result_query(
        "curl -i http://localhost/api/workflow/cancel\nHTTP/1.1 404 Not Found")
    score = _measured_score(mem, tmp_path, query)
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score', score - 0.01)

    session = {'session_id': 'sess-5', 'project_id': 'p1'}
    first = mp.observe(P, session, 'Bash', 'tool_result', query)
    second = mp.observe(P, session, 'Bash', 'tool_result', query)

    assert first == ['discovery_workflow_cancel_404_stale_server.md']
    assert second == [], "the same note must not fire twice in one session"
    rows = [json.loads(l) for l in _log_file(data_root).read_text(encoding='utf-8').splitlines()]
    assert len(rows) == 1, "a duplicate must not add a second log row either"


def test_max_per_turn_caps_new_fires(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)
    (tmp_path / 'discovery_second_404_note.md').write_text(
        "---\nname: discovery-second-404-note\n"
        "description: \"A second note about a 404 not found workflow cancel error, so two units can both clear threshold.\"\n---\n"
        "A second 404 not found workflow cancel discovery, distinct file, same vocabulary as the first.\n",
        encoding='utf-8')
    from mc import state

    query = mp.extract_result_query(
        "curl -i http://localhost/api/workflow/cancel\nHTTP/1.1 404 Not Found")
    hits = mem._memory_search(P, query, topk=5, expand=0, record=None)
    worst = min(h['score'] for h in hits if h['file'].endswith('.md'))
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score', worst - 0.01)
    monkeypatch.setitem(state.CONFIG, 'memory_push_max_per_turn', 1)

    session = {'session_id': 'sess-6', 'project_id': 'p1'}
    fired = mp.observe(P, session, 'Bash', 'tool_result', query)
    assert len(fired) == 1


# ── already_delivered_by_per_turn ────────────────────────────────────────────

def test_already_delivered_by_per_turn_is_flagged_in_the_row(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)
    from mc import state

    query = mp.extract_result_query(
        "curl -i http://localhost/api/workflow/cancel\nHTTP/1.1 404 Not Found")
    score = _measured_score(mem, tmp_path, query)
    monkeypatch.setitem(state.CONFIG, 'memory_push_min_score', score - 0.01)

    session = {'session_id': 'sess-7', 'project_id': 'p1',
               '_mem_turn_delivered': {'discovery_workflow_cancel_404_stale_server.md'}}
    mp.observe(P, session, 'Bash', 'tool_result', query)

    rows = [json.loads(l) for l in _log_file(data_root).read_text(encoding='utf-8').splitlines()]
    assert rows[0]['already_delivered_by_per_turn'] is True


# ── mode gate + never-raises ─────────────────────────────────────────────────

def test_mode_off_is_a_noop(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'memory_push_mode', 'off')

    session = {'session_id': 'sess-8', 'project_id': 'p1'}
    fired = mp.observe(P, session, 'Bash', 'tool_result', 'HTTP/1.1 404 Not Found')
    assert fired == []
    assert not _log_file(data_root).exists()


def test_observe_never_raises_on_malformed_input(env):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)
    assert mp.observe(None, {'session_id': 's'}, 'Bash', 'tool_result', '404') == []
    assert mp.observe(P, None, 'Bash', 'tool_result', '404') == []
    assert mp.observe(P, {'session_id': 's'}, 'Bash', 'tool_result', None) == []


def test_observe_swallows_and_logs_internal_exceptions(env, monkeypatch):
    mem, mp, tmp_path, data_root = env
    _seed_note(tmp_path)

    def _boom(*a, **kw):
        raise RuntimeError('synthetic search failure')
    monkeypatch.setattr(mem, '_memory_search', _boom)

    session = {'session_id': 's', 'project_id': 'p1'}
    assert mp.observe(P, session, 'Bash', 'tool_result', 'HTTP/1.1 404 Not Found') == []
