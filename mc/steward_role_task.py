"""Engine-independent cycle composer; resolution and delivery belong to SR-3."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mc.steward_role_model import CLOSED, RoleError, charter, identifier, journal_path

STEWARD_TASK_MARKER = '[Steward cycle]'
DIRECTIVE_PATH = (Path(__file__).resolve().parents[1] / 'data' / 'skills'
                  / 'builtin' / 'mc-steward' / 'CYCLE_DIRECTIVE.md')
MAX_JOB_INSTRUCTIONS = 8000


@dataclass(frozen=True)
class CycleSnapshot:
    """Resolved per-fire input, not authorization or another engine resolver.

    SR-3 owns owner/engine resolution and guard verification. Journal remains
    relative to the registered project root; charter text is read afresh here.
    delivery_context holds fallback/handoff disclosures, after the marker.
    """
    character_name: str
    character_ref: str
    schedule_id: str
    charter_item_id: str
    journal_path: str
    character_body: str = ''
    rules_refresh: str = ''
    job_instructions: str = ''
    delivery_context: str = ''
    provider: str = ''
    model: str = ''
    effort: str = ''


def load_directive() -> str:
    """Missing/empty resources visibly fail; never fall back to stale prose."""
    try:
        text = DIRECTIVE_PATH.read_text(encoding='utf-8').strip()
    except (OSError, UnicodeError) as exc:
        raise RoleError(f'steward cycle directive unavailable: {exc}') from exc
    if not text:
        raise RoleError('steward cycle directive is empty')
    return text


def build_cycle_task(project: dict, charter_item: dict | None,
                     refresh: str = '', *,
                     cycle_snapshot: CycleSnapshot | None = None,
                     now: datetime | None = None) -> str:
    """Compose marker, assignment, directive, voice/rules, permitted deltas.

    The positional signature matches steward.core.build_cycle_task. Legacy
    calls keep their objective precedence and current character, without data
    migration. Missing legacy configuration produces a hold instruction;
    invalid resolved role input raises RoleError before any delivery.
    """
    wake = (now or datetime.now().astimezone()).astimezone().isoformat(timespec='seconds')
    sections = [STEWARD_TASK_MARKER, f'Wake (local time): {wake}']
    snapshot = cycle_snapshot
    if snapshot is not None:
        identifier(snapshot.schedule_id, 'schedule_id')
        identifier(snapshot.charter_item_id, 'charter_item_id')
        scope, _, name = snapshot.character_ref.partition(':')
        if scope not in ('global', 'project'):
            raise RoleError('resolved role character must be global:<name> or project:<name>')
        identifier(name, 'character')
        if not snapshot.character_name.strip():
            raise RoleError('resolved role character name is empty')
        if len(snapshot.job_instructions) > MAX_JOB_INSTRUCTIONS:
            raise RoleError(f'job instructions exceed {MAX_JOB_INSTRUCTIONS} characters')
        # Never allow a stale supplied charter to replace the project's current ask.
        current = charter(project, {'charter_item_id': snapshot.charter_item_id})
        journal = journal_path(project, snapshot.journal_path)
        objective = current['text'].strip()
        cid = snapshot.charter_item_id
        sections.extend([
            f'You are {snapshot.character_name}, holding the steward role for this project.',
            f'Character: {snapshot.character_ref}\nSchedule: {snapshot.schedule_id}',
        ])
        if snapshot.provider or snapshot.model or snapshot.effort:
            sections.append(f'Resolved engine: {snapshot.provider}; model: {snapshot.model}; '
                            f'effort: {snapshot.effort}')
    else:
        cid = str((charter_item or {}).get('id') or '')
        objective = (project.get('steward_objective') or '').strip() or str(
            (charter_item or {}).get('text') or '').removeprefix('STEWARD CHARTER: ').strip()
        sections.append('Keep your current character name and voice; you hold the steward '
                        'role for this project. This is a legacy schedule cycle.')
        # Legacy rows have no journal binding. Derive only a location, never a
        # new mandate, identity, schedule or persistent assignment.
        journal = None
        if cid:
            try:
                identifier(cid, 'charter_item_id')
                journal = journal_path(project, f'docs/_journal/{cid}-steward.md')
            except RoleError as exc:
                sections.append(f'Configuration blocker: {exc}. Hold action until corrected.')
        if not cid or not objective or (charter_item or {}).get('status') in CLOSED:
            sections.append('Configuration blocker: missing, empty or closed charter. '
                            'Hold action; do not invent a mandate.')
        if journal is None:
            sections.append('Configuration blocker: canonical journal unavailable. Hold action; '
                            'report the blocker through the existing session channel.')
    sections.append(f'Project: {project.get("id", "")}\nCharter backlog item id: {cid}\n'
                    f'Current charter / objective:\n{objective}')
    if journal is not None:
        sections.append(f'Canonical journal (read first, append dated evidence here): {journal}')
    if snapshot is not None and snapshot.job_instructions:
        sections.append('Job-specific instructions (subordinate to project rules and the shared '
                        'role directive):\n' + snapshot.job_instructions)
    if snapshot is not None and snapshot.delivery_context:
        sections.append('Delivery / handoff context:\n' + snapshot.delivery_context)
    sections.append(load_directive())
    if snapshot is not None:
        if snapshot.character_body:
            sections.append('Current character voice (conduct and project rules take precedence):\n'
                            + snapshot.character_body)
        if snapshot.rules_refresh:
            sections.append('Current rules refresh:\n' + snapshot.rules_refresh)
    if refresh:
        sections.append(refresh)
    return '\n\n'.join(sections)
