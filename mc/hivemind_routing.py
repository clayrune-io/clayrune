"""Pure explicit-decomposition routing (BUILTINS-4 HR-6b; Q5=A, Q6=A).

Python API for the later mc-hivemind-routing skill (no HTTP route or CLI):

    result = plan_routing({
        'project_id': 'project', 'goal': 'Authorized deliverables',
        'max_concurrent_workers': 1,
        'reviewer_character': 'global:code-reviewer',
        'units': [{'id': 'ws_api', 'title': 'API deliverable',
                   'character': 'global:builder',
                   'owned_files': ['mc/example.py'], 'depends_on': [],
                   'checks': ['python -m pytest tests/test_example.py -q'],
                   'smokes': ['boot.mjs']}],
    }, app_config=config, read_character=character_records.get, project=project)

Supply an explicit character snapshot/lookup by file stem, as in HR-1. This
function never reads files itself, writes, executes checks, calls a model or
dispatches. HR-1 revalidates current characters at create/attempt time. A caller
can serialize the returned dictionary as JSON. Only for route='hivemind' may
the caller POST result['payload'] once to /api/hivemind/create; it must not also
dispatch those units separately. Ordinary callers use ordinary_units instead.
Invalid plans have neither dispatchable units nor a payload.

Required top-level keys: project_id, goal, units, max_concurrent_workers.
reviewer_character is required only for 3+ independent workers. Unit keys in
the example are required except smokes; description is optional. role may
only be 'worker': declare the later reviewer as reviewer_character, never as
a third unit. Checks are nonempty acceptance text, not executable commands.
Optional smokes must be bare .mjs suite names accepted by existing integration.
Both ownership and checks survive create in the workstream description.

Independence is Dave's explicit declaration, not inferred from prose: only
units with no depends_on can start at the common base. All dependents,
including transitive ones, remain in held_units with their full briefs and a
Q6 hold until Dave arranges work after reviewed integration. They are never
inserted into a common-base roster. Three roots still route to hivemind when
capacity is one; the reviewer consumes no worker slot or threshold count.

Ownership is concrete repository-relative FILE paths, normalized lexically
(backslashes, dot segments and case aliases). Globs, absolute/parent paths
and directory declarations are refused. Any overlap invalidates the whole
plan, even on a held unit. No filesystem/symlink or semantic independence
claim is made; Dave remains responsible for those declarations.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping

from mc import hivemind_roster as roster
from mc.hivemind_integration import clean_smokes

_UNIT_KEYS = {'id', 'title', 'description', 'character', 'owned_files',
              'depends_on', 'checks', 'smokes', 'role'}
_PLAN_KEYS = {'project_id', 'goal', 'units', 'reviewer_character',
              'max_concurrent_workers'}


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be a nonempty string')
    return value.strip()


def _strings(value: Any, field: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f'{field} must be a {"nonempty " if nonempty else ""}list')
    items = [_text(item, field) for item in value]
    if len(set(items)) != len(items):
        raise ValueError(f'{field} contains duplicates')
    return items


def _file(value: str) -> str:
    path = value.replace('\\', '/')
    if (path.startswith('/') or path.endswith('/')
            or '..' in path.split('/')
            or any(c in path for c in ':*?[]')
            or any(ord(c) < 32 for c in path)):
        raise ValueError(f'owned_files requires concrete repository-relative files: {value!r}')
    parts = PurePosixPath(path).parts
    # Trailing spaces/dots alias other filenames on Windows; refuse ambiguity.
    if not parts or any(part.endswith((' ', '.')) for part in parts):
        raise ValueError(f'owned_files contains an ambiguous file path: {value!r}')
    return '/'.join(parts)


def _unit(raw: Any, *, app_config: Mapping[str, Any],
          project: Mapping[str, Any] | None,
          read_character: roster.ReadCharacter) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError('unit must be an object')
    unknown = raw.keys() - _UNIT_KEYS
    if unknown:
        raise ValueError(f'unknown unit fields: {sorted(map(str, unknown))}')
    if raw.get('role', 'worker') != 'worker':
        raise ValueError('units are workers; declare the reviewer separately')
    unit_id = _text(raw.get('id'), 'id')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', unit_id):
        raise ValueError(f'invalid unit id: {unit_id!r}')
    description = raw.get('description', '')
    if not isinstance(description, str):
        raise ValueError('description must be a string')
    files = [_file(p) for p in _strings(raw.get('owned_files'), 'owned_files', nonempty=True)]
    if len({p.casefold() for p in files}) != len(files):
        raise ValueError('owned_files contains aliases of the same file')
    smokes = _strings(raw.get('smokes', []), 'smokes')
    if clean_smokes(smokes) != smokes:
        raise ValueError('smokes must contain only bare .mjs suite filenames')
    return {
        'id': unit_id,
        'title': _text(raw.get('title'), 'title'),
        'description': description.strip(),
        'character': roster.validate_character(
            raw.get('character'), app_config=app_config, project=project,
            read_character=read_character),
        'owned_files': files,
        'depends_on': _strings(raw.get('depends_on'), 'depends_on'),
        'checks': _strings(raw.get('checks'), 'checks', nonempty=True),
        'smokes': smokes,
    }


def _graph_errors(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    ids = {u['id'] for u in units}
    owners: list[tuple[str, str]] = []
    for unit in units:
        for dep in unit['depends_on']:
            if dep not in ids:
                errors.append({'code': 'missing_dependency', 'units': [unit['id']],
                               'message': f"{unit['id']} depends on missing unit {dep!r}"})
        for path in unit['owned_files']:
            key = path.casefold()
            for previous, owner in owners:
                if (key == previous or key.startswith(previous + '/')
                        or previous.startswith(key + '/')):
                    errors.append({'code': 'ownership_overlap', 'units': [owner, unit['id']],
                                   'message': f'{path!r} overlaps ownership of {previous!r}'})
            owners.append((key, unit['id']))

    # Iterative topological walk also handles deep plans without recursion.
    remaining = {u['id']: set(u['depends_on']) & ids for u in units}
    while remaining:
        ready = {uid for uid, deps in remaining.items() if not deps}
        if not ready:
            errors.append({'code': 'dependency_cycle', 'units': sorted(remaining),
                           'message': 'a dependency cycle prevents ordering these units'})
            break
        remaining = {uid: deps - ready for uid, deps in remaining.items() if uid not in ready}
    return errors


def _workstream(unit: Mapping[str, Any]) -> dict[str, Any]:
    # Create persists description, not arbitrary owned_files/checks keys.
    brief = (unit['description'] + '\n\n' if unit['description'] else '')
    brief += 'Owned files: ' + json.dumps(unit['owned_files'], ensure_ascii=False)
    brief += '\nAcceptance checks: ' + json.dumps(unit['checks'], ensure_ascii=False)
    return {'id': unit['id'], 'title': unit['title'], 'character': unit['character'],
            'description': brief, 'dependencies': [], 'smokes': list(unit['smokes'])}


def plan_routing(data: Any, *, app_config: Mapping[str, Any],
                 read_character: roster.ReadCharacter,
                 project: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return JSON-safe ordinary/hivemind/invalid routing without dispatching.

    Invalid input returns structured errors and independent_count=0, never a
    dispatchable subset. A caller must retain held_units even when a payload
    is available; a created roster does not complete the whole mixed ask.
    max_concurrent_workers is explicit authorized capacity, never inferred or
    raised by this function. Reviewer identity is validated when supplied,
    including ordinary plans, but is not counted as an independent worker.
    """
    result: dict[str, Any] = {
        'route': 'invalid', 'independent_count': 0, 'independent_units': [],
        'ordinary_units': [], 'held_units': [], 'payload': None, 'errors': [],
    }
    errors: list[dict[str, Any]] = result['errors']
    try:
        if not isinstance(data, dict):
            raise ValueError('decomposition must be an object')
        unknown = data.keys() - _PLAN_KEYS
        if unknown:
            raise ValueError(f'unknown decomposition fields: {sorted(map(str, unknown))}')
        project_id = _text(data.get('project_id'), 'project_id')
        goal = _text(data.get('goal'), 'goal')
        capacity = data.get('max_concurrent_workers')
        if type(capacity) is not int or capacity < 1:
            raise ValueError('max_concurrent_workers must be an explicit positive integer')
        raw_units = data.get('units')
        if not isinstance(raw_units, list):
            raise ValueError('units must be a list')
    except ValueError as exc:
        errors.append({'code': 'invalid_plan', 'units': [], 'message': str(exc)})
        return result

    units: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_units):
        try:
            unit = _unit(raw, app_config=app_config, project=project,
                         read_character=read_character)
            if unit['id'] in seen:
                errors.append({'code': 'duplicate_id', 'units': [unit['id']],
                               'message': f"duplicate unit id {unit['id']!r}"})
            seen.add(unit['id'])
            units.append(unit)
        except ValueError as exc:
            errors.append({'code': 'invalid_unit', 'units': [], 'index': index,
                           'message': str(exc)})
    reviewer = None
    if 'reviewer_character' in data:
        try:
            reviewer = roster.validate_character(
                data['reviewer_character'], app_config=app_config,
                project=project, read_character=read_character)
        except roster.RosterError as exc:
            errors.append({'code': 'invalid_reviewer', 'units': [], 'message': str(exc)})
    if errors:
        return result
    errors.extend(_graph_errors(units))
    if errors:
        return result

    independent = [u for u in units if not u['depends_on']]
    if len(independent) >= 3:
        payload = {
            'project_id': project_id, 'goal': goal,
            'roster_version': roster.ROSTER_VERSION,
            'max_concurrent_workers': capacity, 'reviewer_character': reviewer,
            'workstreams': [_workstream(u) for u in independent],
        }
        try:
            roster.plan_create(payload, app_config=app_config, project=project,
                               read_character=read_character)
        except roster.RosterError as exc:
            errors.append({'code': 'invalid_roster', 'units': [], 'message': str(exc)})
            return result
        result.update(route='hivemind', payload=payload)
    else:
        result.update(route='ordinary', ordinary_units=independent)
    result.update(
        independent_count=len(independent), independent_units=[u['id'] for u in independent],
        held_units=[dict(u, hold_reason='Q6: dependent follow-up for Dave after reviewed integration')
                    for u in units if u['depends_on']])
    return result
