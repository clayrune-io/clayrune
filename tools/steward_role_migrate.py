#!/usr/bin/env python3
"""Move one legacy scheduled job onto a steward role, and back (BUILTINS-3 SR-5).

Dry-run is the default. `--apply` writes. Nothing here runs on deploy; a person
runs it, types the dashboard passcode at a hidden prompt, and activates the
role afterwards from the Automation window.

It talks to the running server over HTTP only. It never opens the schedule or
project stores itself, so the human-only gate and the refusal of agent callers
stay exactly where they are. Run from an agent session it is refused by the
server, which is the intended outcome.

    migrate   inspect (default) or apply: bind SCHEDULE to a charter item
    rollback  detach the role and restore the pre-migration job from a snapshot
    candidates  list ordinary schedules in a project, to find the id to migrate

Revision check: a dry run prints a revision (a hash of the schedule's
configuration, its charter item and the project's steward fields). `--apply`
needs `--expect-revision` with that value; if anything changed in between, it
refuses and writes nothing.

Safety properties, each pinned by tests/test_steward_role_migrate.py:
  * the recovery snapshot is written BEFORE the first write, outside the repo
    (`~/.clayrune/steward-role-migrations/`, or `--snapshot-dir`);
  * future fires are suspended and the running cycle must finish before the
    switch; a cycle that will not finish aborts with the old job paused;
  * the role is left DISABLED. Activation is a separate human click;
  * the schedule id, cadence, continuation and run condition are reused;
  * a failed bind restores a charter edit it made and leaves the job paused.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE = 'http://localhost:5199'
SNAPSHOT_VERSION = 1

# Fields that define what a schedule does. Volatile fields (last_run, next_run,
# last_error*) and server-side enrichments are excluded so the revision only
# moves when someone changes the job.
CONFIG_KEYS = (
    'id', 'project_id', 'task', 'workflow_id', 'character', 'model', 'effort',
    'description', 'continue_session', 'schedule_type', 'time', 'days',
    'interval_minutes', 'run_at', 'cron_expr', 'delete_after_run',
    'run_condition', 'steward', 'enabled', 'steward_role',
)
# Sessions in these states are finished; any other state counts as active.
QUIET_STATUSES = frozenset({'idle', 'done', 'completed', 'complete', 'error', 'failed', 'stopped'})
WAITING_FLAGS = ('waiting_for_question', 'waiting_for_plan_approval')

EXIT_OK, EXIT_PLAN, EXIT_PREVIEW, EXIT_REVISION, EXIT_WRITE, EXIT_QUIESCE = 0, 1, 2, 3, 4, 5

# Phrases in a restored legacy task that the migration spec rules out
# ("rollback must not restore known unsafe guidance"). Warned, never silently
# rewritten: the person decides, or supplies --task-file.
UNSAFE_TASK_PATTERNS = (
    (re.compile(r'/backlog/[^\s"\']*/note', re.I), 'writes a backlog note over the API'),
    (re.compile(r'\bbacklog note', re.I), 'mentions backlog notes'),
    (re.compile(r'\bself[- ]?(arm|enable|schedul)', re.I), 'tells the agent to arm its own wake'),
)


class MigrationError(Exception):
    def __init__(self, message: str, code: int = EXIT_PLAN):
        super().__init__(message)
        self.code = code


class Api:
    """The only I/O the planner and applier use; tests substitute a fake."""

    def __init__(self, base: str = DEFAULT_BASE, passcode: Callable[[], str] | None = None,
                 timeout: float = 30.0):
        self.base = base.rstrip('/')
        self._passcode = passcode
        self._cached = ''
        self.timeout = timeout

    def passcode(self) -> str:
        if not self._cached:
            if self._passcode is None:
                raise MigrationError('a dashboard passcode is required for this step')
            self._cached = self._passcode()
        return self._cached

    def call(self, method: str, path: str, body: dict | None = None,
             human: bool = False) -> tuple[int, Any]:
        payload = dict(body or {})
        if human:
            payload['passcode'] = self.passcode()
        data = json.dumps(payload).encode() if (body is not None or human) else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise MigrationError(f'server unreachable at {self.base}: {exc}', EXIT_WRITE) from exc
        try:
            return status, json.loads(raw) if raw else {}
        except ValueError:
            return status, {'error': raw.decode('utf-8', 'replace')[:300]}


# ── Reading ────────────────────────────────────────────────────────────────

def _get(api: Any, path: str, what: str) -> Any:
    status, body = api.call('GET', path)
    if status != 200:
        raise MigrationError(f'could not read {what}: HTTP {status} {_err(body)}')
    return body


def _err(body: Any) -> str:
    return str(body.get('error') or body) if isinstance(body, dict) else str(body)


def find_schedule(api: Any, schedule_id: str) -> dict:
    rows = _get(api, '/api/schedules', 'schedules')
    row = next((r for r in rows if r.get('id') == schedule_id), None)
    if row is None:
        raise MigrationError(f'schedule {schedule_id!r} not found')
    return row


def find_project(api: Any, project_id: str) -> dict:
    rows = _get(api, '/api/projects', 'projects')
    project = next((p for p in rows if p.get('id') == project_id), None)
    if project is None:
        raise MigrationError(f'project {project_id!r} not found')
    # /api/projects carries only a backlog summary; the items come from their own route.
    return dict(project, backlog=_get(api, f'/api/project/{project_id}/backlog', 'backlog'))


def find_charter(project: dict, item_id: str) -> dict:
    items = [i for i in project.get('backlog', []) if i.get('id') == item_id]
    if len(items) != 1:
        raise MigrationError(f'charter item {item_id!r} must exist exactly once in project '
                             f'{project.get("id")!r} (found {len(items)})')
    return items[0]


def config_of(row: dict) -> dict:
    return {k: row[k] for k in CONFIG_KEYS if k in row}


def revision(row: dict, project: dict, charter: dict) -> str:
    material = {
        'schedule': config_of(row),
        'charter': {k: charter.get(k) for k in ('id', 'text', 'status')},
        'project': {k: project.get(k) for k in
                    ('steward_mode', 'steward_assignment', 'default_character')},
    }
    blob = json.dumps(material, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()[:16]


def active_sessions(api: Any, row: dict) -> list[dict]:
    """Sessions this schedule started that have not finished. Unknown = active."""
    body = _get(api, f'/api/project/{row["project_id"]}/agent/status', 'agent status')
    out = []
    for s in body.get('sessions', []) if isinstance(body, dict) else []:
        if s.get('trigger_type') != 'schedule' or s.get('trigger_id') != row.get('id'):
            continue
        if s.get('status') in QUIET_STATUSES and not any(s.get(f) for f in WAITING_FLAGS):
            continue
        out.append({'session_id': s.get('session_id'), 'status': s.get('status'),
                    'waiting': [f for f in WAITING_FLAGS if s.get(f)]})
    return out


# ── Planning (no writes) ───────────────────────────────────────────────────

def plan_migration(api: Any, args: argparse.Namespace) -> dict:
    row = find_schedule(api, args.schedule_id)
    if row.get('project_id') != args.project:
        raise MigrationError(f'schedule {args.schedule_id} belongs to project '
                             f'{row.get("project_id")!r}, not {args.project!r}')
    if 'steward_role' in row:
        raise MigrationError('that schedule already has a steward role; use `rollback` to undo it')
    if row.get('steward'):
        raise MigrationError('that is a legacy steward schedule; it keeps its owner until a person '
                             'migrates the project steward from the Automation window')
    if row.get('workflow_id'):
        raise MigrationError('a workflow schedule cannot become a steward role')
    if not (row.get('task') or '').strip():
        raise MigrationError('the schedule has no task text to preserve')
    project = find_project(api, args.project)
    charter = find_charter(project, args.charter_item_id)
    character = args.character or row.get('character') or project.get('default_character') or ''
    if not character:
        raise MigrationError('no character to bind: pass --character (the role needs an explicit owner)')
    journal = args.journal
    new_text = None
    if args.charter_file:
        new_text = Path(args.charter_file).read_text(encoding='utf-8').strip()
        if not new_text:
            raise MigrationError('--charter-file is empty')
    role = {'version': 1, 'charter_item_id': args.charter_item_id, 'journal_path': journal}
    status, preview = api.call('POST', '/api/steward-role/preview', {
        'project_id': args.project, 'schedule_id': args.schedule_id,
        'steward_role': role, 'character': character, 'enabled': False})
    if status != 200 or not isinstance(preview, dict):
        raise MigrationError(f'preview failed: HTTP {status} {_err(preview)}', EXIT_PREVIEW)
    # A charter text change is applied before binding, so preview ran against
    # the old text; the bind itself re-validates against the saved text.
    plan = {
        'schedule_id': args.schedule_id, 'project_id': args.project,
        'cadence': _cadence(row), 'continue_session': row.get('continue_session', True),
        'run_condition': row.get('run_condition'), 'enabled_now': bool(row.get('enabled')),
        'character': character, 'role': role,
        'charter': {'id': charter.get('id'), 'status': charter.get('status') or '',
                    'current_text': charter.get('text') or '', 'new_text': new_text,
                    'text_changes': new_text is not None and new_text != (charter.get('text') or '').strip()},
        'revision': revision(row, project, charter),
        'preview': preview, 'active_sessions': active_sessions(api, row),
        'old_row': config_of(row), 'steward_mode': project.get('steward_mode') or 'off',
    }
    return plan


def _cadence(row: dict) -> str:
    t = row.get('schedule_type')
    if t == 'interval':
        return f'every {row.get("interval_minutes")} min'
    return t or '?'


def render_plan(plan: dict) -> str:
    p = plan['preview']
    lines = [
        f'Steward role migration, DRY RUN: schedule {plan["schedule_id"]} in {plan["project_id"]}',
        f'  keeps: id, cadence ({plan["cadence"]}), continuation={plan["continue_session"]}, '
        f'run condition={"set" if plan["run_condition"] else "none"}',
        f'  binds: character {plan["character"]}, charter item {plan["role"]["charter_item_id"]}, '
        f'journal {plan["role"]["journal_path"]}',
        f'  now enabled: {plan["enabled_now"]}; after migration the role is DISABLED until you activate it',
        f'  charter text: {"WILL BE REPLACED from --charter-file" if plan["charter"]["text_changes"] else "unchanged"}',
        f'  running cycles: {len(plan["active_sessions"])}'
        + (' (must finish first)' if plan['active_sessions'] else ''),
        'Server validation of the draft:',
    ]
    for c in p.get('checks', []):
        lines.append(f'  [{"ok" if c["ok"] else "FAIL"}] {c["name"]}: {c["detail"]}')
    blockers = p.get('blockers') or []
    if blockers:
        lines.append('Execution is blocked on this install (migration is still allowed; the role '
                     'will not fire until these clear):')
        lines += [f'  - {b["code"]}: {b["message"]}' for b in blockers]
    lines += ['', f'revision: {plan["revision"]}',
              'To apply: re-run with --apply --expect-revision ' + plan['revision']]
    return '\n'.join(lines)


# ── Applying ───────────────────────────────────────────────────────────────

def snapshot_dir(arg: str | None) -> Path:
    return Path(arg) if arg else Path.home() / '.clayrune' / 'steward-role-migrations'


def write_snapshot(directory: Path, plan: dict, now: Callable[[], datetime]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = now().strftime('%Y%m%dT%H%M%SZ')
    path = directory / f'{plan["schedule_id"]}-{stamp}.json'
    body = {'version': SNAPSHOT_VERSION, 'created_at': stamp,
            'schedule_id': plan['schedule_id'], 'project_id': plan['project_id'],
            'pre_revision': plan['revision'], 'old_row': plan['old_row'],
            'role': plan['role'], 'character': plan['character'],
            'charter_before': plan['charter']['current_text'],
            'charter_changed': False, 'post_revision': None}
    _write_json(path, body)
    return path


def _write_json(path: Path, body: dict) -> None:
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(body, indent=2, ensure_ascii=False))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def wait_quiet(api: Any, row: dict, seconds: float, sleep: Callable[[float], None]) -> list[dict]:
    deadline = time.monotonic() + seconds
    while True:
        busy = active_sessions(api, row)
        if not busy or time.monotonic() >= deadline:
            return busy
        sleep(min(5.0, max(0.1, deadline - time.monotonic())))


def _write(api: Any, method: str, path: str, body: dict, what: str, human: bool) -> Any:
    status, out = api.call(method, path, body, human=human)
    if status >= 300:
        raise MigrationError(f'{what} failed: HTTP {status} {_err(out)}', EXIT_WRITE)
    return out


def apply_migration(api: Any, args: argparse.Namespace, *, now: Callable[[], datetime] | None = None,
                    sleep: Callable[[float], None] = time.sleep) -> dict:
    now = now or (lambda: datetime.now(timezone.utc))
    if not args.expect_revision:
        raise MigrationError('--apply needs --expect-revision (take it from a dry run)')
    plan = plan_migration(api, args)
    if plan['revision'] != args.expect_revision:
        raise MigrationError(
            f'revision changed: expected {args.expect_revision}, now {plan["revision"]}. '
            'Something edited the schedule, charter or project steward fields. Nothing was written. '
            'Re-run the dry run and review it.', EXIT_REVISION)
    if not plan['preview'].get('ok'):
        raise MigrationError('the server rejected the draft; nothing was written', EXIT_PREVIEW)
    row = find_schedule(api, args.schedule_id)
    snap_path = write_snapshot(snapshot_dir(args.snapshot_dir), plan, now)
    sid, pid = plan['schedule_id'], plan['project_id']
    sched_path = f'/api/schedules/{sid}'
    done: list[str] = []
    try:
        # Quiesce: stop future fires, then let the running cycle end. The old
        # job stays recoverable (paused, unchanged) if anything below fails.
        if row.get('enabled'):
            _write(api, 'PUT', sched_path, {'enabled': False}, 'suspending the schedule', human=False)
            done.append('suspended the old schedule')
        busy = wait_quiet(api, row, args.wait_seconds, sleep)
        if busy:
            raise MigrationError(
                f'{len(busy)} cycle(s) are still running ({busy[0]["session_id"]} is '
                f'{busy[0]["status"]}). The old schedule is paused and unchanged; re-run when it '
                'has finished, or resume it from the Automation window.', EXIT_QUIESCE)
        snap = json.loads(snap_path.read_text(encoding='utf-8'))
        if plan['charter']['text_changes']:
            _write(api, 'PATCH', f'/api/project/{pid}/backlog/{plan["role"]["charter_item_id"]}',
                   {'text': plan['charter']['new_text']}, 'updating the charter text', human=False)
            snap['charter_changed'] = True
            _write_json(snap_path, snap)
            done.append('replaced the charter text')
        try:
            _write(api, 'PUT', sched_path, {
                'steward_role': plan['role'], 'task': '', 'character': plan['character'],
                'enabled': False}, 'binding the role', human=True)
        except MigrationError:
            if snap['charter_changed']:
                _write(api, 'PATCH', f'/api/project/{pid}/backlog/{plan["role"]["charter_item_id"]}',
                       {'text': snap['charter_before']}, 'restoring the charter text', human=False)
                snap['charter_changed'] = False
                _write_json(snap_path, snap)
            raise
        done.append('bound the role (disabled)')
        after = find_schedule(api, sid)
        project = find_project(api, pid)
        snap['post_revision'] = revision(after, project, find_charter(project, plan['role']['charter_item_id']))
        _write_json(snap_path, snap)
    except MigrationError as exc:
        exc.args = (f'{exc.args[0]}. Done so far: {"; ".join(done) or "nothing"}. '
                    f'Snapshot: {snap_path}',)
        raise
    return {'snapshot': str(snap_path), 'done': done, 'post_revision': snap['post_revision'],
            'next': ['Open Automation, review the steward role card, press Run Now once and read the '
                     'journal, then activate. The role is disabled until you do.']}


# ── Rollback ───────────────────────────────────────────────────────────────

def load_snapshot(path: str) -> dict:
    try:
        snap = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise MigrationError(f'cannot read snapshot {path}: {exc}') from exc
    if not isinstance(snap, dict) or snap.get('version') != SNAPSHOT_VERSION or \
            not isinstance(snap.get('old_row'), dict) or not snap.get('schedule_id'):
        raise MigrationError('not a steward role migration snapshot')
    return snap


def task_warnings(task: str) -> list[str]:
    return [f'restored task {why}' for rx, why in UNSAFE_TASK_PATTERNS if rx.search(task)]


def plan_rollback(api: Any, args: argparse.Namespace) -> dict:
    snap = load_snapshot(args.snapshot)
    row = find_schedule(api, snap['schedule_id'])
    if not isinstance(row.get('steward_role'), dict):
        raise MigrationError('the schedule has no steward role; nothing to roll back')
    task = Path(args.task_file).read_text(encoding='utf-8').strip() if args.task_file \
        else snap['old_row'].get('task', '')
    project = find_project(api, row['project_id'])
    charter = find_charter(project, row['steward_role']['charter_item_id'])
    cur = revision(row, project, charter)
    drifted = bool(snap.get('post_revision')) and cur != snap['post_revision']
    return {'snapshot': snap, 'row': row, 'task': task, 'task_source': 'file' if args.task_file else 'snapshot',
            'warnings': task_warnings(task), 'drifted': drifted, 'revision': cur,
            'enabled_now': bool(row.get('enabled')), 'active_sessions': active_sessions(api, row),
            'restore_charter': bool(snap.get('charter_changed'))
            and (charter.get('text') or '').strip() != (snap.get('charter_before') or '').strip()}


def render_rollback(plan: dict) -> str:
    snap = plan['snapshot']
    lines = [f'Steward role ROLLBACK, DRY RUN: schedule {snap["schedule_id"]}',
             f'  restores the legacy task ({len(plan["task"])} chars, from {plan["task_source"]}) '
             f'and character {snap["old_row"].get("character") or "(inherited)"}',
             '  keeps: schedule id, cadence, history, journal, pending decisions, children',
             f'  charter text: {"restored to its pre-migration text" if plan["restore_charter"] else "left as is"}',
             '  the job is left DISABLED; re-enable it yourself once you have read the restored task',
             f'  running cycles: {len(plan["active_sessions"])}']
    if plan['drifted']:
        lines.append('  WARNING: the role was edited after migration; rollback needs --allow-edited')
    lines += [f'  WARNING: {w}. Review it or pass --task-file with corrected text.' for w in plan['warnings']]
    lines += ['', 'To apply: re-run with --apply']
    return '\n'.join(lines)


def apply_rollback(api: Any, args: argparse.Namespace, *,
                   sleep: Callable[[float], None] = time.sleep) -> dict:
    plan = plan_rollback(api, args)
    if plan['drifted'] and not args.allow_edited:
        raise MigrationError('the role was edited after migration (revision differs from the snapshot); '
                             'review it and pass --allow-edited to roll back anyway. Nothing was written.',
                             EXIT_REVISION)
    snap, row = plan['snapshot'], plan['row']
    sid, pid = snap['schedule_id'], row['project_id']
    done: list[str] = []
    # Suspension of a role row is human-gated like every other role write.
    if row.get('enabled'):
        _write(api, 'PUT', f'/api/schedules/{sid}', {'enabled': False}, 'suspending the role', human=True)
        done.append('suspended the role')
    busy = wait_quiet(api, row, args.wait_seconds, sleep)
    if busy:
        raise MigrationError(f'{len(busy)} cycle(s) are still running; the role is suspended and '
                             f'unchanged. Re-run when it has finished. Done so far: '
                             f'{"; ".join(done) or "nothing"}', EXIT_QUIESCE)
    old = snap['old_row']
    _write(api, 'PUT', f'/api/schedules/{sid}', {
        'steward_role': None, 'task': plan['task'], 'character': old.get('character', ''),
        'enabled': False}, 'detaching the role', human=True)
    done.append('detached the role and restored the legacy task (disabled)')
    if plan['restore_charter']:
        _write(api, 'PATCH', f'/api/project/{pid}/backlog/{row["steward_role"]["charter_item_id"]}',
               {'text': snap['charter_before']}, 'restoring the charter text', human=False)
        done.append('restored the charter text')
    return {'done': done, 'next': ['Read the restored task, then re-enable the schedule from the '
                                   'Automation window. Journal, run history and children were not touched.']}


# ── CLI ────────────────────────────────────────────────────────────────────

def list_candidates(api: Any, project: str) -> list[dict]:
    rows = _get(api, '/api/schedules', 'schedules')
    return [{'id': r['id'], 'enabled': bool(r.get('enabled')), 'cadence': _cadence(r),
             'character': r.get('character') or '', 'task': (r.get('task') or '')[:80]}
            for r in rows if r.get('project_id') == project and 'steward_role' not in r
            and not r.get('steward') and not r.get('workflow_id')]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default=os.environ.get('CLAYRUNE_URL', DEFAULT_BASE))
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('candidates', help='list ordinary schedules in a project')
    c.add_argument('--project', required=True)
    m = sub.add_parser('migrate', help='dry run by default; --apply writes')
    m.add_argument('--project', required=True)
    m.add_argument('--schedule-id', required=True)
    m.add_argument('--charter-item-id', required=True)
    m.add_argument('--journal', required=True, help='docs/_journal/<name>.md; an existing one is kept')
    m.add_argument('--character', default='', help='scope:name; default is the schedule\'s own')
    m.add_argument('--charter-file', default='', help='replace the charter item text from this file')
    m.add_argument('--apply', action='store_true')
    m.add_argument('--expect-revision', default='')
    m.add_argument('--wait-seconds', type=float, default=120.0)
    m.add_argument('--snapshot-dir', default='')
    r = sub.add_parser('rollback', help='dry run by default; --apply writes')
    r.add_argument('--snapshot', required=True)
    r.add_argument('--task-file', default='', help='restore this task text instead of the snapshot\'s')
    r.add_argument('--allow-edited', action='store_true')
    r.add_argument('--apply', action='store_true')
    r.add_argument('--wait-seconds', type=float, default=120.0)
    return ap


def main(argv: list[str] | None = None, api: Any = None, out: Callable[[str], None] = print) -> int:
    args = build_parser().parse_args(argv)
    api = api or Api(args.base, passcode=lambda: getpass.getpass('Dashboard passcode: '))
    try:
        if args.cmd == 'candidates':
            for c in list_candidates(api, args.project):
                out(f'{c["id"]}  {"on " if c["enabled"] else "off"}  {c["cadence"]:<16} '
                    f'{c["character"] or "(inherited)":<20} {c["task"]}')
            return EXIT_OK
        if args.cmd == 'migrate':
            if not args.apply:
                plan = plan_migration(api, args)
                out(render_plan(plan))
                return EXIT_OK if plan['preview'].get('ok') else EXIT_PREVIEW
            result = apply_migration(api, args)
        else:
            if not args.apply:
                plan = plan_rollback(api, args)
                out(render_rollback(plan))
                return EXIT_OK
            result = apply_rollback(api, args)
    except MigrationError as exc:
        out(f'ERROR: {exc}')
        return exc.code
    for line in result['done']:
        out(f'done: {line}')
    if result.get('snapshot'):
        out(f'snapshot: {result["snapshot"]}')
    for line in result['next']:
        out(f'next: {line}')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
