"""Steward binding contract (BUILTINS-3 SR-1), without persistence or dispatch.

The schedule owns the assignment. The backlog owns its mandate. A character
reference is pinned on the schedule, never copied into the role block.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterable

VERSION = 1
ROLE_KEYS = frozenset({'version', 'charter_item_id', 'journal_path'})
CLOSED = frozenset({'done', 'wontdo', 'cancelled', 'archived'})
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


class RoleError(ValueError):
    """Invalid or unavailable assignment; callers must fail closed."""


def identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise RoleError(f'{label} must be a safe nonempty identifier')
    return value


def is_role(schedule: dict) -> bool:
    # Presence, including malformed/null data, must never enter raw-task dispatch.
    return 'steward_role' in schedule


def charter(project: dict, role: dict) -> dict:
    item_id = identifier(role.get('charter_item_id'), 'charter_item_id')
    matches = [i for i in project.get('backlog', []) if i.get('id') == item_id]
    if len(matches) != 1:
        raise RoleError('charter must exist exactly once in the assigned project')
    item = matches[0]
    if not isinstance(item.get('text'), str) or not item['text'].strip():
        raise RoleError('charter text must not be empty')
    if item.get('status') in CLOSED:
        raise RoleError('charter is closed')
    return item


def journal_path(project: dict, value: Any) -> Path:
    """Resolve against registered root, rejecting lexical and realpath escapes."""
    if not isinstance(value, str) or '\\' in value or '\x00' in value:
        raise RoleError('journal_path must be a relative docs/_journal/*.md path')
    parts = PurePosixPath(value).parts
    if (PureWindowsPath(value).drive or PurePosixPath(value).is_absolute()
            or len(parts) < 3 or parts[:2] != ('docs', '_journal')
            or any(p in ('.', '..') or ':' in p for p in value.split('/'))
            or not value.endswith('.md')):
        raise RoleError('journal_path must stay within docs/_journal/ and end in .md')
    root_value = project.get('project_path')
    if not isinstance(root_value, str) or not Path(root_value).is_absolute():
        raise RoleError('project must have a registered absolute project_path')
    try:
        root = Path(root_value).resolve()
        journal_root = (root / 'docs' / '_journal').resolve()
        target = (root / value).resolve()
        if (not journal_root.is_relative_to(root)
                or not target.is_relative_to(journal_root) or target == journal_root
                or target.is_dir()):
            raise RoleError('journal_path escapes the canonical project journal directory')
    except (OSError, RuntimeError) as exc:
        raise RoleError(f'journal_path cannot be resolved: {exc}') from exc
    return target


def owner(project: dict, ref: Any,
          read_character: Callable[..., dict | None]) -> str:
    if not isinstance(ref, str):
        raise RoleError('role character must be a roster reference')
    scope, _, name = ref.partition(':')
    if scope not in ('global', 'project') or not _ID.fullmatch(name):
        raise RoleError('role character must be global:<name> or project:<name>')
    if not any(r.get('character') == ref and not r.get('removed_at')
               for r in project.get('roster', [])):
        raise RoleError(f'role owner {ref!r} is not hired on this project')
    try:
        record = read_character(scope, name,
                                project_path=project.get('project_path') if scope == 'project' else None,
                                include_body=False)
    except Exception as exc:
        raise RoleError(f'role owner lookup failed: {exc}') from exc
    if not record:
        raise RoleError(f'role owner {ref!r} no longer exists')
    return ref


def validate_schedule(schedule: dict, project: dict, schedules: Iterable[dict],
                      read_character: Callable[..., dict | None]) -> dict:
    """Validate the merged row; return a new row with its inherited owner pinned."""
    result = dict(schedule)
    pid = identifier(result.get('project_id'), 'project_id')
    identifier(result.get('id'), 'schedule_id')
    if project.get('id') != pid:
        raise RoleError('assignment project does not match the registered project')
    role = result.get('steward_role')
    if not isinstance(role, dict) or set(role) != ROLE_KEYS:
        raise RoleError('steward_role requires exactly version, charter_item_id, journal_path')
    if type(role.get('version')) is not int or role['version'] != VERSION:
        raise RoleError('unsupported steward_role version')
    if result.get('task') or result.get('workflow_id') or result.get('steward'):
        raise RoleError('exactly one of task, workflow_id or steward_role is allowed')
    if type(result.get('enabled')) is not bool:
        raise RoleError('role enabled must be boolean')
    if result.get('delete_after_run'):
        raise RoleError('role assignments cannot delete_after_run')
    charter(project, role)
    journal_path(project, role['journal_path'])
    result['character'] = owner(project, result.get('character') or project.get('default_character'),
                                read_character)
    if result['enabled']:
        for row in schedules:
            if (row.get('id') != result['id'] and row.get('project_id') == pid
                    and row.get('enabled', True) and (is_role(row) or row.get('steward'))):
                raise RoleError('only one active steward assignment is allowed per project')
    result['steward_role'] = dict(role)
    return result


def assignment(project: dict, schedules: Iterable[dict]) -> dict | None:
    schedules = list(schedules)
    pointer = project.get('steward_assignment')
    if pointer is None:
        return None
    if not isinstance(pointer, dict) or set(pointer) != {'schedule_id'}:
        raise RoleError('steward_assignment requires exactly schedule_id')
    sid = identifier(pointer.get('schedule_id'), 'schedule_id')
    row = next((s for s in schedules if s.get('id') == sid), None)
    if not row or row.get('project_id') != project.get('id') or not is_role(row):
        raise RoleError('steward_assignment must reference a same-project role schedule')
    active = [s for s in schedules if s.get('project_id') == project.get('id')
              and s.get('enabled', True) and is_role(s)]
    if active and (len(active) > 1 or active[0]['id'] != sid):
        raise RoleError('project pointer must reference its one active role assignment')
    return row


def objective(project: dict, schedules: Iterable[dict]) -> str:
    """Read-only compatibility view; no migration or project write on reads."""
    row = assignment(project, schedules)
    if row is None:
        return (project.get('steward_objective') or '').strip()
    return charter(project, row['steward_role'])['text'].strip()


def protected_charters(project_id: str, schedules: Iterable[dict]) -> set[str]:
    # Disabled assignments still protect their mandate.
    return {s['steward_role']['charter_item_id'] for s in schedules
            if s.get('project_id') == project_id and isinstance(s.get('steward_role'), dict)
            and isinstance(s['steward_role'].get('charter_item_id'), str)}
