"""Roster assignment for hivemind workstreams (BUILTINS-4, ticket HR-1).

A workstream may name a roster character (`character: "global:<type>"`)
instead of a provider/model. Its engine then comes from that character through
the shared resolver (`mc.engine_selection.resolve_engine`), the same path an
ordinary dispatch uses. Spec: docs/BUILTINS_4_HIVEMIND_ROSTER_SPEC.md sections
3.1, 4 and 5 (HR-1); decisions Q1/Q2/Q4 in section 8.

This module is the ONE validation contract for every place a workstream or a
manifest's roster settings are written: inline workstreams in
`POST /api/hivemind/create`, `POST .../workstreams/create`,
`PUT .../workstreams/<id>` and `PUT /api/hivemind/<id>` (config). It is pure
apart from reading character files; it never writes, spawns or dispatches.

Decisions in force (spec section 8, Dave 2026-10-31):

* Q1 — global refs only; a roster workstream carries NO explicit
  provider/model/effort (non-empty values are refused). One assignment
  authority per row.
* Q2 — the character is resolved afresh at each new attempt (`resolve_assignment`)
  with disclosed HR-9 role/tier fallback when missing, never a default persona.
  The result is pinned on the workstream for that attempt only.
* Q4 — only an explicit `config.roster_version == 1` run is a roster run. Legacy
  manifests and their workstreams are untouched: no new keys are added to them,
  and a `character` on a legacy run is refused rather than silently ignored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from mc import characters as _characters
from mc import engine_selection
from mc import hivemind_fallback as fallback
from mc.core import _log

ROSTER_VERSION = 1
CONFIG_VERSION_KEY = 'roster_version'
CONFIG_REVIEWER_KEY = 'reviewer_character'
ENGINE_FIELDS = ('provider', 'model', 'effort')

# A character reference reaches a filesystem lookup (`<agents dir>/<name>.md`),
# so the name is pinned to a plain file stem: no separators, no dots at the
# start, bounded length. Imported packs can carry non-kebab stems, hence a
# slightly wider class than skills.validate_name.
_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
# Workstream ids become filenames under data/hiveminds/<id>/workstreams/.
_WS_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


class RosterError(ValueError):
    """A roster request that must be refused. `status` is the HTTP status the
    route adapter should answer with (400 malformed/unknown, 409 conflict)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


ReadCharacter = Callable[[str], 'dict[str, Any] | None']


def _default_read_character(name: str) -> dict[str, Any] | None:
    return _characters.read_character('global', name, include_body=False)


# ── Reference parsing and character lookup ──────────────────────────────────

def parse_character_ref(ref: Any) -> str:
    """Return the canonical `global:<name>` form, or raise RosterError.

    Never infers a type from a display or personal name: the reference is the
    file stem, spelled out.
    """
    if not isinstance(ref, str) or not ref.strip():
        raise RosterError("character must be a 'global:<type>' reference string")
    scope, sep, name = ref.strip().partition(':')
    scope = scope.strip().lower()
    name = name.strip()
    if not sep or scope not in ('global', 'project') or not name:
        raise RosterError(
            f"character {ref!r} is not a valid 'global:<type>' reference")
    if scope == 'project':
        raise RosterError(
            f"character {ref!r}: only 'global:<type>' references are accepted "
            f"on a roster workstream")
    if not _NAME_RE.match(name):
        raise RosterError(f"character {ref!r} has an invalid type name")
    return f'global:{name}'


def lookup_character(ref: Any,
                     read_character: ReadCharacter | None = None
                     ) -> tuple[str, dict[str, Any]]:
    """Canonical ref plus its character record. Raises RosterError when the
    reference is malformed or no such character exists."""
    canon = parse_character_ref(ref)
    name = canon.partition(':')[2]
    reader = read_character or _default_read_character
    try:
        rec = reader(name)
    except Exception as e:  # file I/O on the roster: observable, not swallowed
        _log(f"[hivemind-roster] could not read character {canon!r}: {e}",
             flush=True)
        raise RosterError(f"character {canon!r} could not be resolved: {e}")
    if not rec:
        raise RosterError(f"character {canon!r} not found")
    return canon, rec


def validate_character(ref: Any, *, app_config: Mapping[str, Any],
                       project: Mapping[str, Any] | None = None,
                       read_character: ReadCharacter | None = None,
                       warnings: list[str] | None = None,
                       **criteria: Any) -> str:
    """Canonical ref, with a warning for a missing HR-9 character.

    The engine check is a preflight only (pure, no probes): the authoritative
    resolution happens per attempt in `resolve_assignment`.
    """
    canon = parse_character_ref(ref)
    try:
        assignment = resolve_assignment(canon, app_config, project,
                                        read_character=read_character, **criteria)
    except (engine_selection.EngineSelectionError, ValueError) as exc:
        raise RosterError(str(exc)) from exc
    if assignment['substituted'] and warnings is not None:
        warnings.append(f"character {canon!r} not found; HR-9 fallback will be resolved per attempt")
    return canon


# ── Per-attempt assignment (Q2) ─────────────────────────────────────────────

def resolve_assignment(ref: Any, app_config: Mapping[str, Any],
                       project: Mapping[str, Any] | None = None, *,
                       session_id: str = '',
                       read_character: ReadCharacter | None = None,
                       work_type: str = 'other', complexity: str = 'standard',
                       worker_provider: str = '', previously_defined: bool = False,
                       character_snapshot: dict[str, Any] | None = None
                       ) -> dict[str, Any]:
    """Resolve a roster character to the engine for ONE new attempt.

    Reads the character as it is now (Q2: roster edits reach new attempts),
    uses disclosed HR-9 fallback if it is gone, and lets `EngineSelectionError` from the
    shared resolver propagate unchanged so an unusable engine is a visible
    refusal rather than a substitution. A resolved character owns its engine;
    a generic fallback tracks its complexity tier on the run's worker provider.
    The manifest's baked-in worker model never leaks into either path.

    Returns the `resolved_assignment` projection (spec section 4). Model and
    effort are '' when the provider's native default applies.
    """
    canon = parse_character_ref(ref)
    reader = read_character or _default_read_character

    def read(name: str) -> dict[str, Any] | None:
        try:
            return reader(name)
        except Exception as exc:
            _log(f'[hivemind-roster] could not read character {name!r}: {exc}', flush=True)
            raise RosterError(f'character {name!r} could not be resolved: {exc}') from exc

    choice = fallback.choose(canon, app_config, work_type=work_type, complexity=complexity,
                             previously_defined=previously_defined, lookup=read,
                             parse_ref=parse_character_ref)
    rec = choice.record or {'name': '', 'body': '', 'skills': []}
    engine = engine_selection.resolve_engine(
        app_config, project, character=rec.get('engine') or None,
        provider_override=worker_provider if choice.tier else '',
        model_override=choice.tier or None,
        legacy_default='claude')
    if character_snapshot is not None:
        character_snapshot.update(rec)
    return {
        'character_ref': choice.character_ref,
        'session_id': session_id,
        'provider': engine.provider,
        'model': engine.model,
        'effort': engine.effort,
        'tracking': engine.tracking,
        **choice.disclosure,
    }


def next_attempt(ws: Mapping[str, Any]) -> int:
    n = ws.get('assignment_attempt')
    return (n if type(n) is int and n >= 0 else 0) + 1


def stamp_attempt(ws: dict[str, Any], assignment: Mapping[str, Any],
                  attempt: int | None = None) -> None:
    """Pin an attempt's assignment on the workstream record (caller saves it)."""
    ws['assignment_attempt'] = attempt if attempt is not None else next_attempt(ws)
    ws['resolved_assignment'] = dict(assignment)
    if assignment.get('substituted') is False:
        ws['character_known'] = True


# ── Run / row predicates ────────────────────────────────────────────────────

def is_roster_config(config: Mapping[str, Any] | None) -> bool:
    v = (config or {}).get(CONFIG_VERSION_KEY)
    return type(v) is int and v == ROSTER_VERSION


def is_roster_manifest(manifest: Mapping[str, Any] | None) -> bool:
    return is_roster_config((manifest or {}).get('config'))


def is_roster_workstream(ws: Mapping[str, Any] | None) -> bool:
    return bool((ws or {}).get('character'))


def assignment_locked(ws: Mapping[str, Any]) -> bool:
    """True while an attempt may still be running; the assignment is then fixed.

    A paused workstream keeps its `current_agent_session_id` (pause does not
    stop the process), so ownership, not just status, counts.
    """
    return ws.get('status') == 'active' or bool(ws.get('current_agent_session_id'))


# ── Shared field checks ─────────────────────────────────────────────────────

def _check_no_engine_fields(entry: Mapping[str, Any], where: str) -> None:
    for key in ENGINE_FIELDS:
        v = entry.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        raise RosterError(
            f"{where}: '{key}' cannot be set on a roster workstream; its "
            f"engine comes from the character")


def _check_ws_id(ws_id: Any, where: str) -> str:
    if not isinstance(ws_id, str) or not _WS_ID_RE.match(ws_id):
        raise RosterError(f"{where}: invalid workstream id {ws_id!r}")
    return ws_id


def _roster_row_fields(character: str, unit: Mapping[str, Any] | None = None,
                       *, known: bool = False) -> dict[str, Any]:
    """The persisted roster keys for a new row. Engine fields are pinned to ''
    so every existing reader of `ws['provider']` etc. keeps working."""
    return {
        'character': character,
        'character_known': known,
        **planner_fields(unit or {}),
        'assignment_attempt': 0,
        'resolved_assignment': None,
        'provider': '',
        'model': '',
        'effort': '',
    }


# ── Create (inline workstreams) ─────────────────────────────────────────────

@dataclass(frozen=True)
class RosterPlan:
    """A fully validated roster create request. Nothing has been written."""
    config: dict[str, Any]
    characters: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def workstream_fields(self, index: int) -> dict[str, Any]:
        return self.rows[index]


def planner_fields(unit: Mapping[str, Any]) -> dict[str, str]:
    try:
        return fallback.planner_fields(unit)
    except ValueError as exc:
        raise RosterError(str(exc)) from exc


def assignment_config(app_config: Mapping[str, Any], run_config: Mapping[str, Any]) -> dict[str, Any]:
    """Run overrides apply only to the two HR-9 policy maps."""
    return {**app_config, **{k: run_config[k] for k in (fallback.ROLE_KEY, fallback.TIERS_KEY)
                            if k in run_config}}


def plan_create(data: Mapping[str, Any], *, app_config: Mapping[str, Any],
                project: Mapping[str, Any] | None = None,
                read_character: ReadCharacter | None = None
                ) -> RosterPlan | None:
    """Validate a `POST /api/hivemind/create` body's roster content.

    Returns None for a legacy request (no `roster_version`) after confirming it
    does not smuggle roster fields in; raises RosterError otherwise. Runs
    BEFORE the manifest is saved or any decomposer dispatched, and checks every
    inline workstream, so a bad second item leaves nothing behind.
    """
    has_version = CONFIG_VERSION_KEY in data
    inline = data.get('workstreams') or []
    if not has_version:
        if data.get(CONFIG_REVIEWER_KEY):
            raise RosterError(
                f"'{CONFIG_REVIEWER_KEY}' requires '{CONFIG_VERSION_KEY}': "
                f"{ROSTER_VERSION}")
        if isinstance(inline, list):
            for i, entry in enumerate(inline):
                if isinstance(entry, dict) and entry.get('character'):
                    raise RosterError(
                        f"workstreams[{i}]: 'character' requires "
                        f"'{CONFIG_VERSION_KEY}': {ROSTER_VERSION} on the hivemind")
        return None

    version = data.get(CONFIG_VERSION_KEY)
    if type(version) is not int or version != ROSTER_VERSION:
        raise RosterError(
            f"unsupported '{CONFIG_VERSION_KEY}' {version!r}; "
            f"only {ROSTER_VERSION} is supported")
    if not data.get(CONFIG_REVIEWER_KEY):
        raise RosterError(
            f"'{CONFIG_REVIEWER_KEY}' is required for a roster hivemind")
    warnings: list[str] = []
    app_config = assignment_config(app_config, data)
    try:
        fallback.validate_config(app_config, parse_character_ref)
    except ValueError as exc:
        raise RosterError(str(exc)) from exc
    reviewer = validate_character(
        data[CONFIG_REVIEWER_KEY], app_config=app_config, project=project,
        read_character=read_character, warnings=warnings, work_type='review',
        worker_provider=data.get('worker_provider') or '')
    reviewer_known = not warnings
    if not isinstance(inline, list) or not inline:
        raise RosterError(
            'a roster hivemind needs inline workstreams; it has no decomposer')

    characters: list[str] = []
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for i, entry in enumerate(inline):
        where = f'workstreams[{i}]'
        if not isinstance(entry, dict):
            raise RosterError(f'{where}: must be an object')
        title = entry.get('title')
        if not isinstance(title, str) or not title.strip():
            raise RosterError(f'{where}: title required')
        if entry.get('id'):
            ws_id = _check_ws_id(entry['id'], where)
            if ws_id in seen_ids:
                raise RosterError(f'{where}: duplicate workstream id {ws_id!r}')
            seen_ids.add(ws_id)
        if not entry.get('character'):
            raise RosterError(f"{where}: 'character' is required")
        _check_no_engine_fields(entry, where)
        try:
            before = len(warnings)
            characters.append(validate_character(
                entry['character'], app_config=app_config, project=project,
                read_character=read_character, warnings=warnings,
                worker_provider=data.get('worker_provider') or '', **planner_fields(entry)))
            rows.append(_roster_row_fields(characters[-1], entry, known=len(warnings) == before))
        except RosterError as exc:
            raise RosterError(f'{where}: {exc}', exc.status)
    return RosterPlan(
        config={CONFIG_VERSION_KEY: ROSTER_VERSION,
                CONFIG_REVIEWER_KEY: reviewer, 'reviewer_character_known': reviewer_known,
                **{k: data[k] for k in (fallback.ROLE_KEY, fallback.TIERS_KEY) if k in data}},
        characters=characters, rows=rows, warnings=warnings)


# ── Create / update a single workstream ─────────────────────────────────────

def check_workstream_create(
        data: Mapping[str, Any], ws_id: str,
        manifest: Mapping[str, Any] | None, *,
        app_config: Mapping[str, Any],
        project: Mapping[str, Any] | None = None,
        ws_exists: Callable[[str], bool] | None = None,
        read_character: ReadCharacter | None = None,
        warnings: list[str] | None = None) -> dict[str, Any]:
    """Roster fields to merge into a new workstream, or {} for a legacy run.

    Raises RosterError for a roster/legacy mismatch, an engine field on a
    roster row, an unusable id, or a malformed character.
    """
    if not is_roster_manifest(manifest):
        if data.get('character'):
            raise RosterError(
                f"'character' requires a roster hivemind "
                f"('{CONFIG_VERSION_KEY}': {ROSTER_VERSION})")
        return {}
    _check_ws_id(ws_id, 'workstream')
    if ws_exists is not None and ws_exists(ws_id):
        raise RosterError(f'workstream {ws_id!r} already exists', 409)
    if not data.get('character'):
        raise RosterError("'character' is required on a roster hivemind")
    _check_no_engine_fields(data, 'workstream')
    notices: list[str] = []
    cfg = (manifest or {}).get('config') or {}
    canon = validate_character(
        data['character'], app_config=assignment_config(app_config, cfg), project=project,
        read_character=read_character, warnings=notices,
        worker_provider=cfg.get('worker_provider') or '', **planner_fields(data))
    if warnings is not None:
        warnings.extend(notices)
    return _roster_row_fields(canon, data, known=not notices)


def check_workstream_update(
        ws: Mapping[str, Any], data: Mapping[str, Any],
        manifest: Mapping[str, Any] | None, *,
        app_config: Mapping[str, Any],
        project: Mapping[str, Any] | None = None,
        read_character: ReadCharacter | None = None,
        warnings: list[str] | None = None) -> dict[str, Any]:
    """Roster changes for a `PUT .../workstreams/<id>` body ({} = none).

    Validated before the route applies anything. A same-value `character` is a
    no-op; a different one is refused while an attempt may be running (409) —
    it takes effect only at a new attempt boundary.
    """
    if not is_roster_manifest(manifest):
        if data.get('character'):
            raise RosterError(
                f"'character' requires a roster hivemind "
                f"('{CONFIG_VERSION_KEY}': {ROSTER_VERSION})")
        return {}
    _check_no_engine_fields(data, 'workstream')
    criteria = planner_fields({**ws, **data})
    criteria_changes = {k: v for k, v in criteria.items() if k in data and v != ws.get(k)}
    if criteria_changes and assignment_locked(ws):
        raise RosterError('the assignment cannot change while an attempt may be running', 409)
    if 'character' not in data:
        return criteria_changes
    if not data.get('character'):
        raise RosterError("'character' cannot be cleared on a roster workstream")
    cfg = (manifest or {}).get('config') or {}
    notices: list[str] = []
    canon = validate_character(
        data['character'], app_config=assignment_config(app_config, cfg), project=project,
        read_character=read_character, warnings=notices, **criteria,
        worker_provider=cfg.get('worker_provider') or '')
    if warnings is not None:
        warnings.extend(notices)
    if canon == ws.get('character'):
        return criteria_changes
    if assignment_locked(ws):
        raise RosterError(
            'the assignment cannot change while an attempt may be running; '
            'stop the workstream and retry', 409)
    return {'character': canon, 'character_known': not notices, **criteria_changes}


# ── Manifest config (PUT /api/hivemind/<id>) ────────────────────────────────

def clean_config_update(
        manifest: Mapping[str, Any], incoming: Mapping[str, Any], *,
        app_config: Mapping[str, Any],
        project: Mapping[str, Any] | None = None,
        read_character: ReadCharacter | None = None,
        warnings: list[str] | None = None) -> dict[str, Any]:
    """The `config` patch to merge, with roster keys validated.

    `roster_version` is fixed at creation: setting it on a legacy run would
    leave that run's existing workstreams without characters, and clearing it
    would silently drop review. A same-value write is a no-op.
    `reviewer_character` is validated and canonicalised on a roster run and
    refused on a legacy one. Everything else passes through untouched.
    """
    cleaned = dict(incoming)
    current = (manifest.get('config') or {}) if manifest else {}
    cfg = assignment_config(app_config, {**current, **incoming})
    try:
        fallback.validate_config(cfg, parse_character_ref)
    except ValueError as exc:
        raise RosterError(str(exc)) from exc
    cleaned.pop('reviewer_character_known', None)  # server-owned evidence
    if CONFIG_VERSION_KEY in cleaned:
        same = (type(cleaned[CONFIG_VERSION_KEY]) is type(current.get(CONFIG_VERSION_KEY))
                and cleaned[CONFIG_VERSION_KEY] == current.get(CONFIG_VERSION_KEY))
        if not same:
            raise RosterError(
                f"'{CONFIG_VERSION_KEY}' cannot be changed after creation")
    if CONFIG_REVIEWER_KEY in cleaned:
        if not is_roster_config(current):
            raise RosterError(
                f"'{CONFIG_REVIEWER_KEY}' applies only to a roster hivemind")
        notices: list[str] = []
        cleaned[CONFIG_REVIEWER_KEY] = validate_character(
            cleaned[CONFIG_REVIEWER_KEY], app_config=cfg, work_type='review',
            worker_provider=incoming.get('worker_provider', current.get('worker_provider')) or '',
            project=project, read_character=read_character, warnings=notices)
        cleaned['reviewer_character_known'] = not notices
        if warnings is not None:
            warnings.extend(notices)
    return cleaned

