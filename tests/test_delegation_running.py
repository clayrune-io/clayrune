"""mc/delegation_running.py: running children on the dispatching chat."""
from mc import background_tasks as bg
from mc import delegation_running as dr

PROJECTS = {'web': {'name': 'Clayrune website'}, 'mc': {'name': 'Mission Control'}}


def sess(pid, status='running', **extra):
    return {'project_id': pid, 'status': status, 'started_at': '2026-11-24T19:00:00Z',
            'log_lines': [], **extra}


def run(sessions, parents, **kw):
    return dr.running_by_parent(
        sessions, set(parents), PROJECTS.get,
        name_of=lambda s, _labels: s.get('who', 'Tobin'), labels_fn=dict, **kw)


def test_child_in_another_project_is_listed_on_its_parent():
    sessions = {
        'dave': sess('web', 'idle'),
        'tobin': sess('mc', _spawned_by='dave', _notify_session='dave',
                      claude_session_id='csid-t',
                      log_lines=['> User: go', '[tool: Bash] ls', 'reading',
                                 '[tool: Read] a.py', 'I used [tool: x] in prose']),
    }
    assert run(sessions, ['dave']) == {'dave': [{
        'session_id': 'tobin', 'claude_session_id': 'csid-t', 'project_id': 'mc',
        'project_name': 'Mission Control', 'name': 'Tobin', 'state': 'working',
        'started_at': '2026-11-24T19:00:00Z', 'steps': 2}]}


def test_only_the_requested_parents_are_answered():
    sessions = {'a': sess('web'), 'b': sess('mc', _spawned_by='a')}
    assert run(sessions, ['b']) == {}
    assert run(sessions, []) == {}


def test_finished_child_drops_out():
    for status in ('idle', 'completed', 'error', 'stopped', 'waiting_for_reset'):
        sessions = {'c': sess('mc', status, _spawned_by='p')}
        assert run(sessions, ['p']) == {}, status


def test_asking_child_is_labelled():
    for flag in ('waiting_for_question', 'waiting_for_plan_approval'):
        sessions = {'c': {**sess('mc', _spawned_by='p'), flag: True}}
        assert run(sessions, ['p'])['p'][0]['state'] == 'asking'


def test_idle_child_with_a_held_background_job_has_not_ended(monkeypatch):
    monkeypatch.setattr(bg, 'waiting_label',
                        lambda s: 'waiting on background task: pytest' if s.get('held') else '')
    sessions = {'c': sess('mc', 'idle', _spawned_by='p', held=True)}
    assert run(sessions, ['p'])['p'][0]['state'] == 'background'


def test_report_target_sees_the_child_as_well_as_the_dispatcher():
    sessions = {'c': sess('mc', _spawned_by='dave1', _notify_session='dave2')}
    assert sorted(run(sessions, ['dave1', 'dave2'])) == ['dave1', 'dave2']


def test_notify_session_alone_is_enough():
    sessions = {'c': sess('mc', _notify_session='p')}
    assert list(run(sessions, ['p'])) == ['p']


def test_child_messaging_its_spawner_does_not_make_the_spawner_its_child():
    # 2026-09-24: the child names itself notify_session, re-arming the parent.
    sessions = {'p': sess('web', _notify_session='c'),
                'c': sess('mc', 'idle', _spawned_by='p')}
    assert run(sessions, ['c']) == {}


def test_a_grandchild_does_not_adopt_its_grandparent():
    sessions = {'g': sess('web', _notify_session='c'),
                'p': sess('web', 'idle', _spawned_by='g'),
                'c': sess('mc', 'idle', _spawned_by='p')}
    assert run(sessions, ['c']) == {}


def test_only_direct_children_and_never_itself():
    sessions = {'g': sess('web', 'idle'),
                'p': sess('web', _spawned_by='g'),
                'c': sess('mc', _spawned_by='p', _notify_session='c')}
    out = run(sessions, ['g', 'p', 'c'])
    assert {k: [r['session_id'] for r in v] for k, v in out.items()} == {'g': ['p'], 'p': ['c']}


def test_a_lineage_cycle_terminates():
    # a and b name each other as spawner; walking up from a never reaches c.
    sessions = {'a': sess('web', 'idle', _spawned_by='b'),
                'b': sess('web', 'idle', _spawned_by='a'),
                'c': sess('mc', _notify_session='a')}
    assert [r['session_id'] for r in run(sessions, ['a'])['a']] == ['c']


def test_incognito_and_housekeeping_are_hidden_but_hivemind_workers_show():
    sessions = {
        'i': sess('mc', _spawned_by='p', incognito=True),
        'h': sess('mc', _spawned_by='p', housekeeping=True),
        'w': sess('mc', _spawned_by='p', housekeeping=True, hivemind_id='hm_1'),
    }
    assert [r['session_id'] for r in run(sessions, ['p'])['p']] == ['w']


def test_several_children_oldest_first_and_project_loaded_once():
    calls = []
    sessions = {
        'new': sess('mc', _spawned_by='p', started_at='2026-11-24T19:05:00Z'),
        'old': sess('mc', _spawned_by='p', started_at='2026-11-24T19:01:00Z'),
    }
    out = dr.running_by_parent(sessions, {'p'}, lambda pid: calls.append(pid),
                               name_of=lambda s, _l: 'x', labels_fn=dict)
    assert [r['session_id'] for r in out['p']] == ['old', 'new']
    assert calls == ['mc']
    assert out['p'][0]['project_name'] == 'mc'  # unreadable project: its id


def test_nothing_is_read_when_there_are_no_children():
    def boom(*_a):
        raise AssertionError('read with no children')
    assert dr.running_by_parent({'p': sess('web')}, {'p'}, boom, labels_fn=boom) == {}


def test_a_failure_never_breaks_the_status_poll():
    def boom(_pid):
        raise OSError('disk')
    sessions = {'c': sess('mc', _spawned_by='p')}
    assert dr.running_by_parent(sessions, {'p'}, boom,
                                name_of=lambda s, _l: 'x', labels_fn=dict) == {}


def test_default_name_is_the_floor_name():
    sessions = {'c': sess('mc', _spawned_by='p', character={'agent_name': 'Fenn'})}
    out = dr.running_by_parent(sessions, {'p'}, PROJECTS.get)
    assert out['p'][0]['name'] == 'Fenn'


def test_step_count_does_not_fall_when_the_chat_is_trimmed():
    from mc import log_lines_trim
    from mc.core import TimestampedLines
    lines = TimestampedLines(['> User: go'] + ['[tool: Read] x'] * 2500)
    assert dr.step_count({'log_lines': lines}) == 2500
    once = log_lines_trim.trim(lines)
    assert len(once) < len(lines)
    assert dr.step_count({'log_lines': once}) == 2500
    once.extend(['[tool: Bash] y'] * 600)
    twice = log_lines_trim.trim(once)
    assert dr.step_count({'log_lines': twice}) == 3100
