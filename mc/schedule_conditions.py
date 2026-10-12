"""Scheduler run conditions: a schedule may fire only when a condition holds.

One condition exists: ``spend_leftover_weekly`` ("spend leftover weekly
allowance before reset"). A schedule carrying it fires on a due tick only if

  (a) the engine's weekly usage window resets within ``lead_hours``
      (default 12) AND
  (b) the share of that window already used is below ``ceiling_percent``
      (default 80).

Anything else is a skip with a reason. Missing, malformed, already-reset or
stale usage is a skip too (fail closed): this condition exists to SPEND
allowance, so it must never spend on a number it cannot see.

No new usage fetcher. Numbers come from ``engine_pace_dispatch.provider_usage``
(Claude: ``claude_usage_source`` cache; Codex: the on-disk weekly reader) and
are validated by ``engine_pace.weekly_window`` — the same
{utilization, resets_at} reading the MC-1071 pace policy trusts.

``normalize_condition`` and ``evaluate`` are pure. ``check`` is the one place
that touches I/O (engine resolution + usage), and never raises.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, NamedTuple

from mc import engine_pace

SPEND_LEFTOVER = 'spend_leftover_weekly'
CONDITION_TYPES = (SPEND_LEFTOVER,)

DEFAULT_LEAD_HOURS = 12.0
DEFAULT_CEILING_PERCENT = 80.0
MAX_LEAD_HOURS = 168.0            # the weekly window itself
# Only these providers have a weekly reader (engine_pace_dispatch.provider_usage).
WEEKLY_PROVIDERS = ('claude', 'codex')


class Decision(NamedTuple):
    fire: bool
    reason: str


def _number(value, name: str, lo: float, hi: float, *, lo_open: bool = False):
    """(float, None) or (None, error). Bools and non-finite numbers refused."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None, f'{name} must be a number'
    try:
        num = float(value)
    except ValueError:
        return None, f'{name} must be a number'
    if not math.isfinite(num) or num > hi or num < lo or (lo_open and num == lo):
        bound = f'greater than {lo:g}' if lo_open else f'at least {lo:g}'
        return None, f'{name} must be {bound} and at most {hi:g}'
    return num, None


def normalize_condition(raw: Any, *, schedule_type: str = '',
                        workflow_target: bool = False):
    """Validate a run condition from the API. Returns (condition, error).

    Falsy ``raw`` (None, {}, False) clears the condition: (None, None).
    Stored shape: {type, lead_hours, ceiling_percent, engine}; ``engine`` ''
    means "whichever engine this schedule's agent would run on".
    """
    if raw is None or raw is False or raw == {}:
        return None, None
    if not isinstance(raw, dict):
        return None, 'run_condition must be an object'
    ctype = raw.get('type')
    if ctype not in CONDITION_TYPES:
        return None, (f'unknown run_condition type {ctype!r}; '
                      f'expected one of {list(CONDITION_TYPES)}')
    # A one-shot that is skipped is either consumed (never runs) or retried
    # every 30s tick forever; neither is what "spend leftovers" means.
    if schedule_type == 'once':
        return None, 'run_condition cannot be set on a once schedule'
    # A workflow's steps each pick their own engine, so there is no single
    # window to test.
    if workflow_target:
        return None, 'run_condition cannot be set on a workflow-targeted schedule'
    lead, err = _number(raw.get('lead_hours', DEFAULT_LEAD_HOURS),
                        'lead_hours', 0, MAX_LEAD_HOURS, lo_open=True)
    if err:
        return None, err
    ceiling, err = _number(raw.get('ceiling_percent', DEFAULT_CEILING_PERCENT),
                           'ceiling_percent', 0, 100, lo_open=True)
    if err:
        return None, err
    engine = raw.get('engine') or ''
    if not isinstance(engine, str) or engine.strip().lower() not in ('', *WEEKLY_PROVIDERS):
        return None, (f'engine must be empty (the schedule\'s own engine) or one of '
                      f'{list(WEEKLY_PROVIDERS)}, got {engine!r}')
    return {'type': ctype, 'lead_hours': lead, 'ceiling_percent': ceiling,
            'engine': engine.strip().lower()}, None


def evaluate(condition: Mapping, provider: str, usage: Mapping,
             now: datetime) -> Decision:
    """Pure verdict for one due tick. ``usage`` is {provider: {utilization,
    resets_at}} exactly as ``engine_pace`` reads it."""
    lead = float(condition.get('lead_hours', DEFAULT_LEAD_HOURS))
    ceiling = float(condition.get('ceiling_percent', DEFAULT_CEILING_PERCENT))
    window = engine_pace.weekly_window({'engine': {'provider': provider}}, usage, now)
    if window is None:
        return Decision(False, f'{provider} weekly usage is missing, invalid, stale or '
                               f'already reset; not spending blind')
    used, remaining = window
    if remaining > timedelta(hours=lead):
        hours = remaining.total_seconds() / 3600
        return Decision(False, f'{provider} weekly window resets in {hours:.1f}h, '
                               f'outside the {lead:g}h lead time')
    if used >= ceiling:
        return Decision(False, f'{provider} weekly usage {used:g}% is at or above '
                               f'the {ceiling:g}% ceiling')
    hours = remaining.total_seconds() / 3600
    return Decision(True, f'{provider} weekly window resets in {hours:.1f}h with '
                          f'{used:g}% used (ceiling {ceiling:g}%)')


def resolve_provider(sched: Mapping, condition: Mapping, *, config: Mapping,
                     load_project: Callable, read_character: Callable) -> str:
    """The engine this schedule's agent would dispatch on, mirroring
    ``_dispatch_agent_internal``: explicit condition engine, else the persona's
    own engine, else the project's provider, else the global default, else
    claude. ``read_character(scope, name, project_path=)`` is
    ``mc.characters.read_character``."""
    explicit = (condition.get('engine') or '').strip().lower()
    if explicit:
        return explicit
    from mc import engine_selection
    project = load_project(sched.get('project_id', '')) or {}
    ref = (sched.get('character') or project.get('default_character') or '').strip()
    character = None
    scope, _, name = ref.partition(':')
    if scope in ('project', 'global') and name:
        rec = read_character(scope, name.strip(),
                             project_path=project.get('project_path') if scope == 'project' else None)
        character = (rec or {}).get('engine') or None
    return engine_selection.resolve_provider(
        config, project, character=character, legacy_default='claude')[0]


def check(sched: Mapping, *, config: Mapping, load_project: Callable,
          read_character: Callable, usage_for: Callable[[str], Mapping],
          now: datetime | None = None) -> Decision | None:
    """None when the schedule has no condition; otherwise the verdict for this
    tick. Never raises: any failure is a skip carrying its reason."""
    condition = sched.get('run_condition')
    if not condition:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        provider = resolve_provider(sched, condition, config=config,
                                    load_project=load_project,
                                    read_character=read_character)
        if provider not in WEEKLY_PROVIDERS:
            return Decision(False, f'{provider} exposes no weekly usage window; '
                                   f'not spending blind')
        return evaluate(condition, provider, usage_for(provider), now)
    except Exception as e:
        return Decision(False, f'condition check failed ({e}); not spending blind')
