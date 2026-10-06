"""MC-1057 part 1: a dispatch callback names its brief, it does not echo it.

Before: `Task: <the whole brief the spawner wrote>` (median ~1.8k chars of a
~5.9k-char callback, max 26k). After: a one-line title plus the exact GET that
returns the full brief. The first line is pinned verbatim because
static/js/triggered-collapse.js parses it; the child's answer is untouched.
"""
import re

from mc.callback_shape import TITLE_MAX_CHARS, brief_hint, task_line, task_title
from mc.delegation_delivery import callback_payload
from mc.question_callback import question_payload

LONG_BRIEF = ("Backlog 627a4961 (MC-975, high): Codex unattended sandbox. "
              "Read the full item text including the adopted policy.\n\n"
              + "PART A (build): a Settings UI toggle for codex_unattended_sandbox. " * 40)
ANSWER = "Shipped. Toggle is human-only; 3 tests added.\n- detail one\n- detail two"


def _child(**kw):
    child = {'session_id': '0d1915bafb3d4e5f', 'project_id': 'mission_control',
             'character': {'agent_name': 'Tobin'}, 'status': 'idle',
             'task': LONG_BRIEF, 'provider': 'claude'}
    child.update(kw)
    return child


def test_title_is_first_nonempty_line_clipped():
    assert task_title("\n\n  Fix the   cold start\nsecond line") == "Fix the cold start"
    t = task_title("x" * 500)
    assert len(t) == TITLE_MAX_CHARS and t.endswith('...')
    assert task_title('') == '' and task_title(None) == ''


def test_hint_names_the_exact_get():
    assert brief_hint('mission_control', 'abc123') == \
        'GET /api/project/mission_control/agent/log?session_id=abc123'
    assert brief_hint('', 'abc123') == ''


def test_task_line_without_a_task_still_says_so():
    assert task_line({'session_id': 's', 'project_id': 'p'}).startswith('Task: (none recorded)')


def test_finished_callback_drops_the_echo_and_keeps_the_answer_verbatim():
    msg = callback_payload(_child(), ANSWER, 'evt')['message']
    first = msg.split('\n', 1)[0]
    # Shape the collapse UI parses (static/js/triggered-collapse.js _FINISHED_RE).
    assert re.match(r'^\[dispatched agent finished\]\s+(.+?)\s+\(session\s+[\w-]+\)'
                    r'\s+ended with status=([\w-]+)', first)
    assert first == '[dispatched agent finished] Tobin (session 0d1915bafb3d) ended with status=idle.'
    assert LONG_BRIEF not in msg
    assert 'PART A (build)' not in msg.replace(
        'Backlog 627a4961 (MC-975, high): Codex unattended sandbox.', '')
    assert 'GET /api/project/mission_control/agent/log?session_id=0d1915bafb3d4e5f' in msg
    assert f"Its final message:\n{ANSWER}\n\n" in msg           # answer never keyed or clipped
    assert len(msg) < 600 + len(ANSWER)


def test_payload_still_carries_the_full_task_for_other_consumers():
    p = callback_payload(_child(), ANSWER, 'evt')
    assert p['task'] == LONG_BRIEF and p['summary'] == ANSWER


def test_question_callback_also_drops_the_echo():
    pending = {'questions': [{'header': 'Pick', 'question': 'Which?',
                              'options': [{'label': 'A'}, {'label': 'B'}]}]}
    msg = question_payload(_child(), pending, 'evt')['message']
    assert msg.startswith('[dispatched agent asked a question] Tobin (session 0d1915bafb3d)')
    assert LONG_BRIEF not in msg and 'agent/log?session_id=0d1915bafb3d4e5f' in msg
    assert '1. [Pick] Which?' in msg


def test_agent_log_session_id_filter_returns_only_that_row(tmp_data_dir):
    import importlib
    srv = importlib.import_module('server')
    importlib.reload(srv)
    from mc.blueprints import agent_routes as ar
    rows = [{'session_id': 'aaa111bbb222', 'task': 'one'},
            {'session_id': 'ccc333ddd444', 'task': LONG_BRIEF}]
    orig = ar._load_agent_log
    ar._load_agent_log = lambda pid: [dict(r) for r in rows]
    try:
        client = srv.app.test_client()
        got = client.get('/api/project/p/agent/log?session_id=ccc333ddd444').get_json()
        assert [r['session_id'] for r in got] == ['ccc333ddd444']
        assert got[0]['task'] == LONG_BRIEF
        prefix = client.get('/api/project/p/agent/log?session_id=ccc333').get_json()
        assert len(prefix) == 1
        assert len(client.get('/api/project/p/agent/log').get_json()) == 2
    finally:
        ar._load_agent_log = orig
