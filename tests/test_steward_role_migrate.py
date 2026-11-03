"""SR-5 pilot migration tool: dry run, revision check, snapshot, quiesce, rollback.

The server is replaced by an in-memory fake that records every call, so these
tests prove ordering and what is (not) written without any live data.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('steward_role_migrate', REPO / 'tools' / 'steward_role_migrate.py')
assert _spec is not None and _spec.loader is not None
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)

OLD_TASK = '[Backlog run] You are Dave, running the backlog on a timer.'


class FakeApi:
    def __init__(self, tmp_snapshots: Path):
        self.snapshots = tmp_snapshots
        self.row = {'id': 'sc1', 'project_id': 'p1', 'enabled': True, 'task': OLD_TASK,
                    'workflow_id': '', 'character': 'project:dave', 'model': '', 'effort': '',
                    'description': '', 'continue_session': True, 'schedule_type': 'interval',
                    'interval_minutes': 720, 'time': '09:00', 'days': [], 'run_at': '',
                    'cron_expr': '', 'delete_after_run': False, 'run_condition': None,
                    'last_run': 'x', 'next_run': 'y'}
        self.project: dict = {'id': 'p1', 'steward_mode': 'off', 'default_character': 'project:dave'}
        self.backlog = [{'id': 'it1', 'text': 'Run the backlog', 'status': 'open'}]
        self.sessions: list[dict] = []
        self.preview = {'ok': True, 'checks': [{'name': 'charter', 'ok': True, 'detail': 'open: Run the backlog'}],
                        'blockers': [{'code': 'core_adapter_unapplied', 'message': 'patch unapplied'}]}
        self.calls: list[tuple] = []
        self.bind_error: str | None = None
        self.quiet_after: int | None = None  # polls of agent/status before sessions clear
        self._polls = 0

    def passcode(self):
        return 'secret'

    def _log(self, method, path, body, human):
        self.calls.append((method, path, dict(body or {}), human))

    def writes(self):
        return [c for c in self.calls if c[0] in ('PUT', 'PATCH', 'POST') and not c[1].endswith('/preview')]

    def call(self, method, path, body=None, human=False):
        self._log(method, path, body, human)
        body = body or {}
        if method == 'GET':
            if path == '/api/schedules':
                return 200, [copy.deepcopy(self.row)]
            if path == '/api/projects':
                return 200, [{k: v for k, v in self.project.items()}]
            if path == '/api/project/p1/backlog':
                return 200, copy.deepcopy(self.backlog)
            if path == '/api/project/p1/agent/status':
                self._polls += 1
                if self.quiet_after is not None and self._polls > self.quiet_after:
                    self.sessions = []
                return 200, {'sessions': copy.deepcopy(self.sessions)}
            return 404, {'error': 'nope'}
        if path == '/api/steward-role/preview':
            return 200, copy.deepcopy(self.preview)
        if method == 'PATCH':
            item = next(i for i in self.backlog if path.endswith('/' + i['id']))
            item['text'] = body['text']
            return 200, {'ok': True}
        if method == 'PUT' and path == '/api/schedules/sc1':
            if human and not self.passcode():
                return 403, {'error': 'passcode_required'}
            if 'steward_role' in self.row and not human:
                return 403, {'error': 'Steward role writes are human-only.'}
            if 'steward_role' in body and body['steward_role'] is not None:
                if self.bind_error:
                    return 400, {'error': self.bind_error}
                self.row.update(steward_role=body['steward_role'], task=body['task'],
                                character=body['character'], enabled=body['enabled'])
                self.project['steward_assignment'] = {'schedule_id': 'sc1'}
                return 200, copy.deepcopy(self.row)
            if 'steward_role' in body:  # detach
                self.row.pop('steward_role')
                self.row.update(task=body['task'], character=body['character'], enabled=body['enabled'])
                self.project.pop('steward_assignment', None)
                return 200, copy.deepcopy(self.row)
            self.row['enabled'] = body['enabled']
            return 200, copy.deepcopy(self.row)
        return 404, {'error': 'unhandled ' + method + ' ' + path}


@pytest.fixture
def api(tmp_path):
    return FakeApi(tmp_path / 'snaps')


def argv(api, *extra, cmd='migrate'):
    base = ['migrate', '--project', 'p1', '--schedule-id', 'sc1', '--charter-item-id', 'it1',
            '--journal', 'docs/_journal/backlog-runner.md', '--wait-seconds', '0',
            '--snapshot-dir', str(api.snapshots)]
    return base + list(extra)


def run(api, *extra):
    out: list[str] = []
    code = mig.main(argv(api, *extra), api=api, out=out.append)
    return code, '\n'.join(out)


def revision_of(api):
    code, text = run(api)
    assert code == 0, text
    return next(l.split(': ')[1] for l in text.splitlines() if l.startswith('revision:'))


# ── dry run ────────────────────────────────────────────────────────────────

def test_dry_run_is_default_and_writes_nothing(api):
    code, text = run(api)
    assert code == 0
    assert 'DRY RUN' in text and 'revision:' in text
    assert 'core_adapter_unapplied' in text            # blockers are shown, not hidden
    assert api.writes() == []
    assert not api.snapshots.exists()


def test_dry_run_reports_failed_preview_with_exit_2(api):
    api.preview = {'ok': False, 'checks': [{'name': 'owner', 'ok': False, 'detail': 'character missing'}],
                   'blockers': []}
    code, text = run(api)
    assert code == mig.EXIT_PREVIEW and '[FAIL] owner' in text
    assert api.writes() == []


def test_revision_ignores_volatile_fields_and_moves_on_real_change(api):
    first = revision_of(api)
    api.row['next_run'] = 'later'
    api.row['last_run'] = 'again'
    assert revision_of(api) == first
    api.row['task'] += ' edited'
    assert revision_of(api) != first


def test_revision_moves_when_charter_or_project_changes(api):
    first = revision_of(api)
    api.backlog[0]['text'] = 'changed charter'
    second = revision_of(api)
    assert second != first
    api.project['steward_mode'] = 'on'
    assert revision_of(api) != second


# ── apply ──────────────────────────────────────────────────────────────────

def test_apply_requires_expect_revision(api):
    code, text = run(api, '--apply')
    assert code == mig.EXIT_PLAN and '--expect-revision' in text
    assert api.writes() == []


def test_stale_revision_refuses_and_writes_nothing(api):
    rev = revision_of(api)
    api.row['task'] += ' someone edited this'
    code, text = run(api, '--apply', '--expect-revision', rev)
    assert code == mig.EXIT_REVISION and 'revision changed' in text
    assert api.writes() == [] and not api.snapshots.exists()


def test_apply_happy_path_orders_writes_and_leaves_role_disabled(api):
    rev = revision_of(api)
    seen_at_first_write = {}
    real_call = api.call

    def spy(method, path, body=None, human=False):
        if method in ('PUT', 'PATCH') and 'snap' not in seen_at_first_write:
            seen_at_first_write['snap'] = sorted(p.name for p in api.snapshots.glob('*.json')) \
                if api.snapshots.exists() else []
        return real_call(method, path, body, human)

    api.call = spy
    code, text = run(api, '--apply', '--expect-revision', rev)
    assert code == 0, text
    assert len(seen_at_first_write['snap']) == 1       # snapshot existed before the first write
    puts = [c for c in api.writes() if c[0] == 'PUT']
    assert [(c[2].get('enabled'), c[3]) for c in puts] == [(False, False), (False, True)]
    assert puts[1][2]['task'] == '' and puts[1][2]['steward_role'] == {
        'version': 1, 'charter_item_id': 'it1', 'journal_path': 'docs/_journal/backlog-runner.md'}
    assert puts[1][2]['character'] == 'project:dave'
    assert api.row['enabled'] is False                 # activation stays a human click
    assert api.row['id'] == 'sc1' and api.row['interval_minutes'] == 720
    snap = json.loads(next(api.snapshots.glob('*.json')).read_text())
    assert snap['old_row']['task'] == OLD_TASK and snap['post_revision']
    assert 'secret' not in json.dumps(snap)


def test_second_apply_is_refused_not_repeated(api):
    rev = revision_of(api)
    assert run(api, '--apply', '--expect-revision', rev)[0] == 0
    before = len(api.writes())
    code, text = run(api, '--apply', '--expect-revision', rev)
    assert code == mig.EXIT_PLAN and 'already has a steward role' in text
    assert len(api.writes()) == before


def test_disabled_schedule_is_not_suspended_again(api):
    api.row['enabled'] = False
    rev = revision_of(api)
    assert run(api, '--apply', '--expect-revision', rev)[0] == 0
    assert [c[2] for c in api.writes() if c[0] == 'PUT' and 'steward_role' not in c[2]] == []


def test_running_cycle_aborts_with_old_job_paused_and_unbound(api):
    api.sessions = [{'session_id': 's9', 'trigger_type': 'schedule', 'trigger_id': 'sc1', 'status': 'running'}]
    rev = revision_of(api)
    code, text = run(api, '--apply', '--expect-revision', rev)
    assert code == mig.EXIT_QUIESCE and 's9' in text and 'paused and unchanged' in text
    assert api.row['enabled'] is False and api.row['task'] == OLD_TASK and 'steward_role' not in api.row
    assert not any('steward_role' in c[2] for c in api.writes())


def test_waits_for_cycle_to_finish_then_proceeds(api):
    api.sessions = [{'session_id': 's9', 'trigger_type': 'schedule', 'trigger_id': 'sc1', 'status': 'running'}]
    api.quiet_after = 2
    rev = revision_of(api)
    args = mig.build_parser().parse_args(argv(api, '--apply', '--expect-revision', rev))
    args.wait_seconds = 30
    sleeps: list[float] = []
    api._polls = 0
    result = mig.apply_migration(api, args, sleep=sleeps.append)
    assert 'bound the role (disabled)' in result['done'] and sleeps


@pytest.mark.parametrize('status,flags,active', [
    ('idle', {}, False), ('done', {}, False), ('completed', {}, False),
    ('idle', {'waiting_for_question': True}, True),
    ('running', {}, True), ('some-new-status', {}, True),
])
def test_active_session_classification_fails_closed(api, status, flags, active):
    api.sessions = [dict({'session_id': 's', 'trigger_type': 'schedule', 'trigger_id': 'sc1',
                          'status': status}, **flags)]
    assert bool(mig.active_sessions(api, api.row)) is active


def test_other_schedules_and_manual_sessions_do_not_block(api):
    api.sessions = [{'session_id': 'a', 'trigger_type': 'manual', 'trigger_id': '', 'status': 'running'},
                    {'session_id': 'b', 'trigger_type': 'schedule', 'trigger_id': 'other', 'status': 'running'}]
    assert mig.active_sessions(api, api.row) == []


def test_bind_failure_leaves_job_paused_and_restores_charter(api, tmp_path):
    api.bind_error = 'only one active steward assignment is allowed per project'
    charter = tmp_path / 'charter.md'
    charter.write_text('NEW charter text with the Q5 mapping', encoding='utf-8')
    code, text = run(api, '--charter-file', str(charter))
    rev = next(l.split(': ')[1] for l in text.splitlines() if l.startswith('revision:'))
    code, text = run(api, '--charter-file', str(charter), '--apply', '--expect-revision', rev)
    assert code == mig.EXIT_WRITE and 'only one active steward assignment' in text
    assert api.backlog[0]['text'] == 'Run the backlog'          # charter edit undone
    assert api.row['task'] == OLD_TASK and 'steward_role' not in api.row and api.row['enabled'] is False
    snap = json.loads(next(api.snapshots.glob('*.json')).read_text())
    assert snap['charter_changed'] is False
    assert 'Snapshot:' in text


def test_charter_file_replaces_text_before_binding(api, tmp_path):
    charter = tmp_path / 'charter.md'
    charter.write_text('NEW charter\n', encoding='utf-8')
    code, text = run(api, '--charter-file', str(charter))
    rev = next(l.split(': ')[1] for l in text.splitlines() if l.startswith('revision:'))
    assert 'WILL BE REPLACED' in text
    assert run(api, '--charter-file', str(charter), '--apply', '--expect-revision', rev)[0] == 0
    order = [c[0] + ' ' + c[1] for c in api.writes()]
    assert order.index('PATCH /api/project/p1/backlog/it1') < max(
        i for i, c in enumerate(api.writes()) if 'steward_role' in c[2])
    assert api.backlog[0]['text'] == 'NEW charter'


def test_passcode_sent_only_on_human_writes(api):
    rev = revision_of(api)
    assert run(api, '--apply', '--expect-revision', rev)[0] == 0
    # FakeApi.call receives the flag; the real Api adds `passcode` only when human=True.
    flags = {(c[0], c[3]) for c in api.writes()}
    assert ('PUT', True) in flags and ('PUT', False) in flags and ('PATCH', True) not in flags


# ── refusals ───────────────────────────────────────────────────────────────

def test_refuses_legacy_steward_workflow_and_wrong_project(api):
    api.row['steward'] = True
    assert run(api)[0] == mig.EXIT_PLAN
    api.row.pop('steward')
    api.row['workflow_id'] = 'wf'
    assert run(api)[0] == mig.EXIT_PLAN
    api.row['workflow_id'] = ''
    api.row['project_id'] = 'other'
    code, text = run(api)
    assert code == mig.EXIT_PLAN and 'belongs to project' in text


def test_refuses_missing_charter_and_missing_character(api):
    api.backlog.clear()
    code, text = run(api)
    assert code == mig.EXIT_PLAN and 'charter item' in text
    api.backlog.append({'id': 'it1', 'text': 't', 'status': 'open'})
    api.row['character'] = ''
    api.project['default_character'] = ''
    code, text = run(api)
    assert code == mig.EXIT_PLAN and '--character' in text


def test_server_unreachable_is_a_clean_error():
    class Down(mig.Api):
        def call(self, *a, **k):
            raise mig.MigrationError('server unreachable at http://x: refused', mig.EXIT_WRITE)
    out: list[str] = []
    code = mig.main(['migrate', '--project', 'p', '--schedule-id', 's', '--charter-item-id', 'i',
                     '--journal', 'docs/_journal/x.md'], api=Down('http://x'), out=out.append)
    assert code == mig.EXIT_WRITE and 'unreachable' in out[0]


# ── rollback ───────────────────────────────────────────────────────────────

def migrated(api):
    rev = revision_of(api)
    assert run(api, '--apply', '--expect-revision', rev)[0] == 0
    return str(next(api.snapshots.glob('*.json')))


def rollback(api, snap, *extra):
    out: list[str] = []
    code = mig.main(['rollback', '--snapshot', snap, '--wait-seconds', '0', *extra], api=api, out=out.append)
    return code, '\n'.join(out)


def test_rollback_dry_run_writes_nothing(api):
    snap = migrated(api)
    before = len(api.writes())
    code, text = rollback(api, snap)
    assert code == 0 and 'ROLLBACK, DRY RUN' in text and 'left DISABLED' in text
    assert len(api.writes()) == before


def test_rollback_restores_legacy_job_disabled_and_keeps_identity(api):
    snap = migrated(api)
    code, text = rollback(api, snap, '--apply')
    assert code == 0, text
    assert 'steward_role' not in api.row and api.row['task'] == OLD_TASK
    assert api.row['character'] == 'project:dave' and api.row['enabled'] is False
    assert api.row['id'] == 'sc1' and api.row['interval_minutes'] == 720
    assert 'steward_assignment' not in api.project


def test_rollback_restores_charter_text_when_migration_changed_it(api, tmp_path):
    charter = tmp_path / 'c.md'
    charter.write_text('NEW charter', encoding='utf-8')
    _, text = run(api, '--charter-file', str(charter))
    rev = next(l.split(': ')[1] for l in text.splitlines() if l.startswith('revision:'))
    assert run(api, '--charter-file', str(charter), '--apply', '--expect-revision', rev)[0] == 0
    snap = str(next(api.snapshots.glob('*.json')))
    assert rollback(api, snap, '--apply')[0] == 0
    assert api.backlog[0]['text'] == 'Run the backlog'


def test_rollback_refuses_an_edited_role_without_allow_edited(api):
    snap = migrated(api)
    api.row['interval_minutes'] = 60       # someone changed the role after migration
    code, text = rollback(api, snap, '--apply')
    assert code == mig.EXIT_REVISION and 'allow-edited' in text
    assert 'steward_role' in api.row
    assert rollback(api, snap, '--apply', '--allow-edited')[0] == 0


def test_rollback_waits_for_running_cycle(api):
    snap = migrated(api)
    api.row['enabled'] = True
    api.sessions = [{'session_id': 's7', 'trigger_type': 'schedule', 'trigger_id': 'sc1', 'status': 'running'}]
    code, text = rollback(api, snap, '--apply', '--allow-edited')
    assert code == mig.EXIT_QUIESCE and 'steward_role' in api.row and api.row['enabled'] is False


def test_rollback_warns_on_unsafe_legacy_task_and_accepts_corrected_file(api, tmp_path):
    api.row['task'] = OLD_TASK + ' Log with POST /api/project/p/backlog/abc/note after each run.'
    snap = migrated(api)
    code, text = rollback(api, snap)
    assert 'writes a backlog note over the API' in text
    fixed = tmp_path / 'fixed.txt'
    fixed.write_text('Corrected task: write to the journal file.', encoding='utf-8')
    code, text = rollback(api, snap, '--task-file', str(fixed))
    assert 'backlog note' not in text
    assert rollback(api, snap, '--task-file', str(fixed), '--apply')[0] == 0
    assert api.row['task'] == 'Corrected task: write to the journal file.'


def test_rollback_rejects_a_foreign_snapshot_file(api, tmp_path):
    bad = tmp_path / 'x.json'
    bad.write_text('{"nope": 1}', encoding='utf-8')
    code, text = rollback(api, str(bad))
    assert code == mig.EXIT_PLAN and 'not a steward role migration snapshot' in text


def test_candidates_lists_only_ordinary_schedules(api):
    out: list[str] = []
    assert mig.main(['candidates', '--project', 'p1'], api=api, out=out.append) == 0
    assert len(out) == 1 and out[0].startswith('sc1') and '[Backlog run]' in out[0]
    api.row['steward_role'] = {}
    out.clear()
    mig.main(['candidates', '--project', 'p1'], api=api, out=out.append)
    assert out == []


def test_real_api_adds_passcode_only_when_human(monkeypatch):
    sent = []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{}'

    def fake_open(req, timeout=0):
        sent.append(json.loads(req.data.decode()) if req.data else None)
        return Resp()

    monkeypatch.setattr(mig.urllib.request, 'urlopen', fake_open)
    a = mig.Api('http://h', passcode=lambda: 'pw')
    a.call('PUT', '/x', {'enabled': False})
    a.call('PUT', '/x', {'enabled': False}, human=True)
    a.call('GET', '/x')
    assert sent == [{'enabled': False}, {'enabled': False, 'passcode': 'pw'}, None]
