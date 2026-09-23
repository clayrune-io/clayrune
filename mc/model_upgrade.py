"""Model pin auto-upgrade gate — Piece 2 of the model-auto-upgrade spec
(Dave, 2026-09-23; Piece 1 is CodexRuntime's live catalog + tier_family +
is_stale_pin, mc/agent_runtime.py).

Ron's rule: a concrete model pin auto-upgrades to the newer model in the same
tier family exactly when the newer model costs the same or less per token.
Fails closed on every unknown — no discovery source, no price on either side,
a price that isn't lower, or a provider this gate can't resolve for a pin
that carries no explicit one (a schedule/workflow-step pin inherits its
provider from a character or project, same precedence a live dispatch
resolves through — mc.engine_selection.resolve_provider).

Applied server-side, in-process, never through the agent-refused character/
workflow HTTP routes (mc/blueprints/character_routes.py's
_refuse_if_agent_caller, mc/blueprints/workflow_routes.py's twin) — this
module writes through mc.characters.write_character / mc.workflows.
update_workflow directly, the same "internal callers bypass the route"
precedent the builtin installer already uses for characters. The write is
never attacker-controlled: the only value it can ever write is a catalog id
this gate itself computed and priced, never anything from a request body.

Prices live in ~/.clayrune/model_prices.json (mc.secrets_store.clayrune_home)
— operator/market data, not source; never committed to the repo. Same for the
upgrade audit log, model_upgrade_log.jsonl in the same directory.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from mc import agent_runtime
from mc import characters as _chars
from mc import engine_selection as _es
from mc.atomic_json import write_json_atomic
from mc.core import _log, now_iso
from mc.secrets_store import clayrune_home

PRICES_FILENAME = 'model_prices.json'
UPGRADE_LOG_FILENAME = 'model_upgrade_log.jsonl'

# Reasons evaluate_pin() can return that mean "not a real candidate" — the
# gate report never surfaces these as findings, only as silent skips.
_NON_CANDIDATE_REASONS = frozenset({
    'not_a_pin', 'unknown_provider', 'no_family', 'already_head', 'no_head',
})


def prices_path() -> Path:
    return clayrune_home() / PRICES_FILENAME


def upgrade_log_path() -> Path:
    return clayrune_home() / UPGRADE_LOG_FILENAME


def load_prices() -> dict:
    path = prices_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _log(f"[model-upgrade] prices file unreadable ({path}): {e}")
        return {}


def save_prices(data: dict) -> None:
    path = prices_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data, indent=2, ensure_ascii=False)


def get_price(provider: str, model: str) -> Optional[dict]:
    return (load_prices().get(provider) or {}).get(model)


def set_price(provider: str, model: str, input_price: float, output_price: float,
              source_url: str = '', retirement_at: str = '') -> dict:
    """Record a known per-1M-token price for provider/model.

    Not a character/workflow mutation — market data, so this is deliberately
    agent-writable (the daily scheduled run looks prices up and calls this).
    `retirement_at` is optional: when the vendor has announced end-of-life for
    THIS model, the gate flags a same-family "more expensive" verdict as
    urgent so the caller knows to email Ron rather than just log it.
    """
    provider = str(provider or '').strip().lower()
    model = str(model or '').strip()
    if not provider or not model:
        raise ValueError('provider and model are required')
    if input_price < 0 or output_price < 0:
        raise ValueError('price must be non-negative')
    data = load_prices()
    bucket = data.setdefault(provider, {})
    entry = {
        'input': float(input_price), 'output': float(output_price),
        'source_url': str(source_url or ''), 'verified_at': now_iso(),
    }
    if retirement_at:
        entry['retirement_at'] = str(retirement_at)
    bucket[model] = entry
    save_prices(data)
    return entry


def _append_audit(line: dict) -> None:
    path = upgrade_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(line, ensure_ascii=False) + '\n')
    except Exception as e:
        _log(f"[model-upgrade] audit log write failed: {e}")


def discovery_report() -> dict[str, Optional[list]]:
    """Per-runtime: model ids this install can currently discover LIVE, or
    None when the runtime has no discovery source at all. Never a guess."""
    out: dict[str, Optional[list]] = {}
    for runtime in agent_runtime.available_runtimes():
        models = runtime.discover_models()
        out[runtime.name] = ([m for m, _label in models] if models is not None else None)
    return out


# ── The gate: one pin at a time, never mutates anything ──────────────────────

@dataclass
class UpgradeDecision:
    provider: str
    old_model: str
    new_model: str = ''
    action: str = 'no_change'  # 'upgrade' | 'no_change'
    reason: str = ''
    old_price: Optional[dict] = None
    new_price: Optional[dict] = None


def evaluate_pin(provider: str, model: str) -> UpgradeDecision:
    """The upgrade gate for ONE exact model pin.

    reason values: not_a_pin, unknown_provider, no_family, no_head,
    already_head (none of these are real candidates — the caller should not
    report them), unknown_price_old, unknown_price_new, more_expensive, ok.
    """
    kind, value = _es.classify_value(model)
    if kind != 'pin':
        return UpgradeDecision(provider, model, reason='not_a_pin')
    try:
        runtime = agent_runtime.get_runtime(provider)
    except KeyError:
        return UpgradeDecision(provider, model, reason='unknown_provider')
    family: str = runtime.tier_family(value)
    if not family:
        return UpgradeDecision(provider, model, reason='no_family')
    head: str = runtime.catalog_head_for(family)
    if not head:
        return UpgradeDecision(provider, model, reason='no_head')
    if head == value:
        return UpgradeDecision(provider, model, reason='already_head')
    old_price = get_price(provider, value)
    new_price = get_price(provider, head)
    if not old_price:
        return UpgradeDecision(provider, model, new_model=head, reason='unknown_price_old')
    if not new_price:
        return UpgradeDecision(provider, model, new_model=head, old_price=old_price,
                                reason='unknown_price_new')
    if new_price['input'] <= old_price['input'] and new_price['output'] <= old_price['output']:
        return UpgradeDecision(provider, model, new_model=head, action='upgrade',
                                reason='ok', old_price=old_price, new_price=new_price)
    return UpgradeDecision(provider, model, new_model=head, old_price=old_price,
                            new_price=new_price, reason='more_expensive')


# ── Pin locations ─────────────────────────────────────────────────────────────
# Scope of pins per the spec: character engine pins (global + every project's
# local pool), project agent_model, the global default, and schedule /
# workflow-step engine pins. Each yields a PinRef whose `apply` closure is the
# ONLY thing that ever writes — and only ever with a value this module's own
# gate computed.

@dataclass
class PinRef:
    kind: str  # 'character' | 'project' | 'global' | 'schedule' | 'workflow_step'
    label: str  # human-readable locator, for the report and the audit log
    provider: str
    model: str
    apply: Callable[[str], None]


def _lookup_character_engine(name: str, project: Optional[dict]) -> Optional[dict]:
    name = (name or '').strip()
    if not name:
        return None
    project_path = (project or {}).get('project_path')
    try:
        records = _chars.list_characters(project_path=project_path, include_body=False)
    except Exception:
        return None
    for rec in records:
        if rec.get('name') == name:
            return rec.get('engine') or {}
    return None


def _resolve_provider(config: dict, project: Optional[dict],
                       character: Optional[dict]) -> str:
    """Best-effort provider for a pin that carries no explicit one of its
    own — same precedence a live dispatch resolves through. '' means this
    gate cannot tell, and the caller must skip the pin rather than guess."""
    try:
        provider, _source = _es.resolve_provider(config, project, character=character)
    except _es.EngineSelectionError:
        return ''
    return provider


def _character_apply(scope: str, name: str, project_path: Optional[str]) -> Callable[[str], None]:
    def _apply(new_model: str) -> None:
        existing = _chars.read_character(scope, name, project_path=project_path)
        if not existing:
            raise RuntimeError(f'character {name!r} ({scope}) vanished before the upgrade applied')
        engine = dict(existing.get('engine') or {})
        engine['model'] = new_model
        _chars.write_character(scope, name, existing.get('description') or '',
                                existing.get('body') or '', project_path=project_path,
                                overwrite=True, engine=engine)
    return _apply


def _character_pins(load_projects_fn: Callable[[], list]) -> Iterator[PinRef]:
    project_paths: list[str] = []
    try:
        for project in load_projects_fn():
            pp = project.get('project_path')
            if pp:
                project_paths.append(pp)
    except Exception as e:
        _log(f"[model-upgrade] project scan (characters) failed: {e}")

    for scope_path in [None, *project_paths]:
        wanted_scope = 'global' if scope_path is None else 'project'
        try:
            records = _chars.list_characters(project_path=scope_path, include_body=False)
        except Exception as e:
            _log(f"[model-upgrade] character scan failed ({scope_path}): {e}")
            continue
        for rec in records:
            if rec.get('scope') != wanted_scope:
                continue
            engine = rec.get('engine') or {}
            model = (engine.get('model') or '').strip()
            provider = (engine.get('provider') or '').strip().lower()
            if not model or not provider:
                # No explicit provider pin on the character -- nothing
                # concrete to compare against a catalog. Never guessed.
                continue
            name = rec['name']
            label = f"character:{wanted_scope}:{name}"
            if scope_path:
                label += f"@{scope_path}"
            yield PinRef('character', label, provider, model,
                         _character_apply(wanted_scope, name, scope_path))


def _project_apply(pid: str, load_project_fn: Callable[[str], Optional[dict]],
                    save_project_fn: Callable[[str, dict], Any]) -> Callable[[str], None]:
    def _apply(new_model: str) -> None:
        proj = load_project_fn(pid)
        if not proj:
            raise RuntimeError(f'project {pid!r} vanished before the upgrade applied')
        proj['agent_model'] = new_model
        save_project_fn(pid, proj)
    return _apply


def _project_pins(load_projects_fn: Callable[[], list],
                   load_project_fn: Callable[[str], Optional[dict]],
                   save_project_fn: Callable[[str, dict], Any],
                   config: dict) -> Iterator[PinRef]:
    try:
        projects = load_projects_fn()
    except Exception as e:
        _log(f"[model-upgrade] project scan failed: {e}")
        return
    for proj in projects:
        model = (proj.get('agent_model') or '').strip()
        if not model:
            continue
        kind, _value = _es.classify_value(model)
        if kind != 'pin':
            continue
        provider = _resolve_provider(config, proj, None)
        if not provider:
            continue
        pid = proj.get('id')
        yield PinRef('project', f'project:{pid}', provider, model,
                     _project_apply(pid, load_project_fn, save_project_fn))


def _global_apply(config: dict, config_path: Path) -> Callable[[str], None]:
    def _apply(new_model: str) -> None:
        config['agent_model'] = new_model
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    return _apply


def _global_pins(config: dict, config_path: Path) -> Iterator[PinRef]:
    model = (config.get('agent_model') or '').strip()
    if not model:
        return
    kind, _value = _es.classify_value(model)
    if kind != 'pin':
        return
    provider = _resolve_provider(config, None, None)
    if not provider:
        return
    yield PinRef('global', 'global:agent_model', provider, model,
                 _global_apply(config, config_path))


def _schedule_apply(schedule_id: str) -> Callable[[str], None]:
    def _apply(new_model: str) -> None:
        from mc.blueprints import scheduler_routes as _sched
        schedules = _sched._load_schedules()
        row = next((s for s in schedules if s.get('id') == schedule_id), None)
        if not row:
            raise RuntimeError(f'schedule {schedule_id!r} vanished before the upgrade applied')
        row['model'] = new_model
        _sched._save_schedules(schedules)
    return _apply


def _schedule_pins(load_project_fn: Callable[[str], Optional[dict]],
                    config: dict) -> Iterator[PinRef]:
    from mc.blueprints import scheduler_routes as _sched
    try:
        schedules = _sched._load_schedules()
    except Exception as e:
        _log(f"[model-upgrade] schedule scan failed: {e}")
        return
    for sched in schedules:
        if sched.get('workflow_id'):
            continue  # engine lives on the workflow's steps, not the schedule
        model = (sched.get('model') or '').strip()
        if not model:
            continue
        kind, _value = _es.classify_value(model)
        if kind != 'pin':
            continue
        project = load_project_fn(sched['project_id']) if sched.get('project_id') else None
        character = _lookup_character_engine(sched.get('character'), project)
        provider = _resolve_provider(config, project, character)
        if not provider:
            continue
        yield PinRef('schedule', f"schedule:{sched['id']}", provider, model,
                     _schedule_apply(sched['id']))


def _workflow_apply(wf_id: str, node_name: str) -> Callable[[str], None]:
    def _apply(new_model: str) -> None:
        import mc.workflows as _wf
        doc = _wf.get_workflow(wf_id)
        if not doc:
            raise RuntimeError(f'workflow {wf_id!r} vanished before the upgrade applied')
        found = False
        for node in doc.get('nodes') or []:
            if node.get('name') == node_name:
                node['model'] = new_model
                found = True
                break
        if not found:
            raise RuntimeError(f'workflow step {node_name!r} vanished before the upgrade applied')
        _wf.update_workflow(wf_id, doc)
    return _apply


def _workflow_pins(load_project_fn: Callable[[str], Optional[dict]],
                    config: dict) -> Iterator[PinRef]:
    import mc.workflows as _wf
    try:
        defs = _wf.list_workflows()
    except Exception as e:
        _log(f"[model-upgrade] workflow scan failed: {e}")
        return
    for wf_def in defs:
        wf_id = wf_def.get('id')
        for node in wf_def.get('nodes') or []:
            if node.get('type') != 'agent':
                continue
            model = (node.get('model') or '').strip()
            if not model:
                continue
            kind, _value = _es.classify_value(model)
            if kind != 'pin':
                continue
            project = load_project_fn(node['project_id']) if node.get('project_id') else None
            character = _lookup_character_engine(node.get('character'), project)
            provider = _resolve_provider(config, project, character)
            if not provider:
                continue
            yield PinRef('workflow_step', f"workflow:{wf_id}:{node.get('name')}",
                         provider, model, _workflow_apply(wf_id, node.get('name')))


def _all_pins(load_projects_fn: Callable[[], list],
               load_project_fn: Callable[[str], Optional[dict]],
               save_project_fn: Callable[[str, dict], Any],
               config: dict, config_path: Path) -> Iterator[PinRef]:
    yield from _character_pins(load_projects_fn)
    yield from _project_pins(load_projects_fn, load_project_fn, save_project_fn, config)
    yield from _global_pins(config, config_path)
    yield from _schedule_pins(load_project_fn, config)
    yield from _workflow_pins(load_project_fn, config)


def price_gaps(load_projects_fn: Callable[[], list],
                load_project_fn: Callable[[str], Optional[dict]],
                save_project_fn: Callable[[str, dict], Any],
                config: dict, config_path: Path) -> list:
    """(provider, model) pairs this install currently has a pin on, or that
    occupy the tier head such a pin would upgrade to, with no price on file
    yet. This is what the daily scheduled run should go look up."""
    seen: set = set()
    gaps: list = []
    prices = load_prices()

    def _maybe(provider: str, model: str) -> None:
        if not model or (provider, model) in seen:
            return
        seen.add((provider, model))
        if not ((prices.get(provider) or {}).get(model)):
            gaps.append({'provider': provider, 'model': model})

    for pin in _all_pins(load_projects_fn, load_project_fn, save_project_fn, config, config_path):
        _maybe(pin.provider, pin.model)
        kind, value = _es.classify_value(pin.model)
        if kind != 'pin':
            continue
        try:
            runtime = agent_runtime.get_runtime(pin.provider)
        except KeyError:
            continue
        family = runtime.tier_family(value)
        if not family:
            continue
        head = runtime.catalog_head_for(family)
        _maybe(pin.provider, head)
    return gaps


# ── Orchestrator ───────────────────────────────────────────────────────────────

def run_upgrade_gate(*, load_projects_fn: Callable[[], list],
                      load_project_fn: Callable[[str], Optional[dict]],
                      save_project_fn: Callable[[str, dict], Any],
                      config: dict, config_path: Path,
                      apply: bool = True) -> dict:
    """Evaluate every known model pin; apply the ones the gate approves.

    apply=False previews without writing anything (used when
    model_auto_upgrade_enabled is off, or a caller just wants a report).
    """
    upgraded: list = []
    unknown_price: list = []
    more_expensive: list = []
    skipped: list = []

    for pin in _all_pins(load_projects_fn, load_project_fn, save_project_fn, config, config_path):
        decision = evaluate_pin(pin.provider, pin.model)
        if decision.reason in _NON_CANDIDATE_REASONS:
            continue
        row: dict[str, Any] = {
            'kind': pin.kind, 'label': pin.label, 'provider': pin.provider,
            'old_model': decision.old_model, 'new_model': decision.new_model,
            'reason': decision.reason,
        }
        if decision.action == 'upgrade':
            if apply:
                try:
                    pin.apply(decision.new_model)
                except Exception as e:
                    row['error'] = str(e)
                    skipped.append(row)
                    continue
                row['old_price'] = decision.old_price
                row['new_price'] = decision.new_price
                _append_audit({
                    'at': now_iso(), 'kind': pin.kind, 'label': pin.label,
                    'provider': pin.provider, 'old_model': decision.old_model,
                    'new_model': decision.new_model,
                    'old_price': decision.old_price, 'new_price': decision.new_price,
                })
                upgraded.append(row)
            else:
                row['would_upgrade'] = True
                upgraded.append(row)
        elif decision.reason in ('unknown_price_old', 'unknown_price_new'):
            unknown_price.append(row)
        elif decision.reason == 'more_expensive':
            if (decision.old_price or {}).get('retirement_at'):
                row['retirement_urgent'] = True
            more_expensive.append(row)
        else:
            skipped.append(row)

    return {
        'applied': apply,
        'upgraded': upgraded,
        'unknown_price': unknown_price,
        'more_expensive': more_expensive,
        'skipped': skipped,
        'changed': bool(upgraded) and apply,
    }
