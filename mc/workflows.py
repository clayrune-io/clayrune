"""Workflow builder — runner + stores. Spec: `docs/WORKFLOW_BUILDER_SPEC.md`.

MC-871 Phase 1 of the spec's own build order (runner + stores -> scheduler
`workflow_id` + calendar badge -> tab list -> builder modal). This module is
backend only: no route decorators live here (see `mc/blueprints/workflow_routes.py`
for those) and no UI.

WHERE STATE LIVES, and why: `data/workflows.json` (definitions) and
`data/workflow_runs/<run_id>.json` (one file per run) are SIBLINGS of
`DATA_DIR` (`data/projects/`), never members of it — `load_projects()` parses
every `*.json` under `data/projects/` as a project record, and a stray file
there 500s both restart endpoints (the LOAD-BEARING DATA_DIR rule in
CLAUDE.md). `data/schedules.json` and `data/desk.json` are the precedent.

THE HANDOFF, and how it avoids a polling loop or a second process: an agent
step's completion is delivered by the SAME latch that already wakes a
dispatching agent's own chat (`agent_routes._maybe_notify_spawner`, MC-946),
generalised to also recognise a workflow run as a waiter
(`_notify_workflow`/`on_agent_step_complete` below). Nothing in this module
polls; `on_agent_step_complete` is the entry point the completion callback
calls, on a thread, best-effort.

THE NODE MODEL — five types (spec Q2), represented as a nested tree (spec Q7:
"a spine, not a graph" — a Paths node's branches are each their own vertical
list running to its own end; no rejoin). `_compile()` flattens that tree into
a name -> node map with resolved `_next` pointers, so the runner never walks
the raw nested JSON at execution time.

THE AUTHORITY GUARD (settled, not open for re-litigation — see
`position_whetheranagentsessionmaycreateoreditworkflowdefi` in the project
memory dir): workflow DEFINITION CRUD and approval-gate DECISIONS are refused
for an agent caller, structurally, at the route layer
(`mc/blueprints/workflow_routes.py::_refuse_if_agent_caller`). This module
enforces the runtime half of the same principle: nothing here can satisfy,
skip, or auto-answer an approval gate (`_advance_run` halts at one and returns
without touching `steps.<name>.output` for the branch that wasn't chosen).
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from mc import state
from mc.core import _atomic_write_text, _log, now_iso

# -- wired by server.py (see wire()) ------------------------------------------
WORKFLOWS_PATH: Optional[Path] = None
WORKFLOW_RUNS_DIR: Optional[Path] = None
# _dispatch_agent_internal / _load_agent_log (agent_routes). Late-bound the
# same way scheduler_routes.py takes them, so this module never imports
# agent_routes.py -- agent_routes imports THIS module directly (leaf, no
# Flask, no cycle) to fire the completion callback.
_dispatch_agent_internal: Optional[Callable[..., str]] = None
_load_agent_log: Optional[Callable[[str], list]] = None

_store_lock = threading.Lock()
_runs_lock = threading.Lock()

STORE_VERSION = 1

NODE_TYPES = ('agent', 'paths', 'approval', 'action')
ACTION_ALLOWLIST = ('backlog_create', 'backlog_patch', 'desk_harvest')
TRIGGER_TYPES = ('manual', 'schedule')
TERMINAL_STATUSES = ('completed', 'failed', 'interrupted')

_SLOT_RE = re.compile(r'\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}')
_WF_RESULT_RE = re.compile(r'```[ \t]*wf:result[ \t\r\n]*(.*?)```', re.DOTALL | re.IGNORECASE)


def wire(*, workflows_path=None, workflow_runs_dir=None,
         dispatch_agent_internal_fn=None, load_agent_log_fn=None):
    """Late-bind cross-family deps + paths. Called once by server.py."""
    global WORKFLOWS_PATH, WORKFLOW_RUNS_DIR, _dispatch_agent_internal, _load_agent_log
    if workflows_path is not None:
        WORKFLOWS_PATH = Path(workflows_path)
    if workflow_runs_dir is not None:
        WORKFLOW_RUNS_DIR = Path(workflow_runs_dir)
    if dispatch_agent_internal_fn is not None:
        _dispatch_agent_internal = dispatch_agent_internal_fn
    if load_agent_log_fn is not None:
        _load_agent_log = load_agent_log_fn


def _port() -> int:
    # Same resolution order as question_channel._answer: env wins over config.
    return int(os.environ.get('MC_PORT') or state.CONFIG.get('port') or 5199)


# ── Definitions store ────────────────────────────────────────────────────────

def _read_definitions() -> list:
    if WORKFLOWS_PATH is None or not WORKFLOWS_PATH.exists():
        return []
    try:
        data = json.loads(WORKFLOWS_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        _log(f'[workflows] store unreadable, treating as empty: {e}')
        return []
    return data if isinstance(data, list) else []


def _write_definitions(defs: list) -> None:
    if WORKFLOWS_PATH is None:
        return
    WORKFLOWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(WORKFLOWS_PATH, json.dumps(defs, indent=2, ensure_ascii=False))


def list_workflows() -> list:
    with _store_lock:
        return _read_definitions()


def get_workflow(workflow_id: str) -> Optional[dict]:
    with _store_lock:
        return next((w for w in _read_definitions() if w.get('id') == workflow_id), None)


def create_workflow(doc: dict) -> dict:
    errors = validate_workflow(doc)
    if errors:
        raise ValueError('; '.join(errors))
    now = now_iso()
    record = {
        'id': f'wf-{uuid.uuid4().hex[:8]}',
        'name': (doc.get('name') or '').strip() or 'Untitled workflow',
        'description': (doc.get('description') or '').strip(),
        'enabled': bool(doc.get('enabled', True)),
        'trigger': doc.get('trigger') or {'type': 'manual'},
        'steps': doc.get('steps') or [],
        'created': now,
        'updated': now,
    }
    with _store_lock:
        defs = _read_definitions()
        defs.append(record)
        _write_definitions(defs)
    return record


def update_workflow(workflow_id: str, doc: dict) -> dict:
    errors = validate_workflow(doc)
    if errors:
        raise ValueError('; '.join(errors))
    with _store_lock:
        defs = _read_definitions()
        idx = next((i for i, w in enumerate(defs) if w.get('id') == workflow_id), None)
        if idx is None:
            raise KeyError('workflow not found')
        existing = defs[idx]
        existing['name'] = (doc.get('name') or existing.get('name') or '').strip()
        existing['description'] = (doc.get('description') or '').strip()
        if 'enabled' in doc:
            existing['enabled'] = bool(doc.get('enabled'))
        existing['trigger'] = doc.get('trigger') or existing.get('trigger') or {'type': 'manual'}
        existing['steps'] = doc.get('steps') or []
        existing['updated'] = now_iso()
        _write_definitions(defs)
        return existing


def delete_workflow(workflow_id: str) -> bool:
    with _store_lock:
        defs = _read_definitions()
        n = len(defs)
        defs = [w for w in defs if w.get('id') != workflow_id]
        if len(defs) == n:
            return False
        _write_definitions(defs)
        return True


# ── Validation + compilation ─────────────────────────────────────────────────

def validate_workflow(doc: dict) -> list:
    """Return a list of error strings; empty = valid. Never raises."""
    errors: list = []
    trigger = doc.get('trigger') or {'type': 'manual'}
    if not isinstance(trigger, dict) or trigger.get('type') not in TRIGGER_TYPES:
        errors.append(f"trigger.type must be one of {TRIGGER_TYPES}")
    steps = doc.get('steps')
    if not isinstance(steps, list) or not steps:
        errors.append('steps must be a non-empty list')
        return errors
    nodes_by_name: dict = {}
    _compile_list(steps, nodes_by_name, errors)
    return errors


def _compile_list(steps_list: list, nodes_by_name: dict, errors: list) -> Optional[str]:
    """Flatten one vertical list of nodes (top-level `steps`, or one Paths/
    approval branch) into `nodes_by_name`, wiring `_next` pointers as it goes.
    Returns the name of the first executable node in this list, or None for an
    empty list (an empty branch/list means "run ends here" -- not a hack, a
    real DAG terminal, e.g. the Desk's `nothing` branch, spec §Acceptance)."""
    entry = None
    pending_plain_name = None  # last agent/action node still awaiting its 'next'
    for node in steps_list or []:
        if not isinstance(node, dict):
            errors.append('step is not an object')
            continue
        t = node.get('type')
        name = node.get('name')

        if t == 'paths':
            if pending_plain_name is None or nodes_by_name.get(pending_plain_name, {}).get('type') != 'agent':
                errors.append(f"paths node '{name}' must directly follow an agent step")
                continue
            branches = node.get('branches') or {}
            if not isinstance(branches, dict) or not branches:
                errors.append(f"paths node '{name}' has no branches")
                branches = {}
            branch_entries = {label: _compile_list(sub, nodes_by_name, errors)
                              for label, sub in branches.items()}
            # MANDATORY otherwise -- the fail-closed default an agent that did
            # not declare a matching outcome is routed to (spec Q3). Absence is
            # a hard validation error, not a silent None.
            if 'otherwise' not in node:
                errors.append(f"paths node '{name}' missing mandatory otherwise branch")
                otherwise_entry = None
            else:
                otherwise_entry = _compile_list(node.get('otherwise') or [], nodes_by_name, errors)
            nodes_by_name[pending_plain_name]['_next'] = {
                'kind': 'branches',
                'branches': branch_entries,
                'otherwise': otherwise_entry,
                'outcomes': list(branch_entries.keys()),
            }
            pending_plain_name = None
            continue

        if not name or not isinstance(name, str):
            errors.append(f"step of type '{t}' missing a name")
            continue
        if name in nodes_by_name:
            errors.append(f"duplicate step name '{name}'")
            continue
        if t not in ('agent', 'approval', 'action'):
            errors.append(f"unknown step type '{t}' for '{name}'")
            continue

        compiled = dict(node)
        nodes_by_name[name] = compiled
        if entry is None:
            entry = name
        if pending_plain_name is not None:
            nodes_by_name[pending_plain_name]['_next'] = {'kind': 'plain', 'name': name}
            pending_plain_name = None

        if t == 'approval':
            options = node.get('options') or []
            branches = node.get('branches') or {}
            if not options:
                errors.append(f"approval node '{name}' has no options")
            branch_entries = {label: _compile_list(branches.get(label) or [], nodes_by_name, errors)
                              for label in options}
            compiled['_next'] = {'kind': 'branches_by_choice', 'branches': branch_entries,
                                 'options': list(options)}
        elif t == 'agent':
            if not node.get('project_id'):
                errors.append(f"agent step '{name}' missing project_id")
            if not (node.get('prompt') or '').strip():
                errors.append(f"agent step '{name}' missing prompt")
            compiled['_next'] = {'kind': 'plain', 'name': None}
            pending_plain_name = name
        elif t == 'action':
            if node.get('action') not in ACTION_ALLOWLIST:
                errors.append(f"action step '{name}' action must be one of {ACTION_ALLOWLIST}")
            compiled['_next'] = {'kind': 'plain', 'name': None}
            pending_plain_name = name

    return entry


def compile_workflow(workflow: dict) -> tuple:
    """(entry_name, nodes_by_name). Raises ValueError if the stored definition
    somehow fails validation (should not happen -- CRUD validates on write)."""
    errors: list = []
    nodes_by_name: dict = {}
    entry = _compile_list(workflow.get('steps') or [], nodes_by_name, errors)
    if errors:
        raise ValueError('; '.join(errors))
    return entry, nodes_by_name


# ── Slot substitution ────────────────────────────────────────────────────────

def _build_context(run: dict) -> dict:
    steps_ctx = {}
    for name, st in (run.get('steps') or {}).items():
        steps_ctx[name] = {'output': st.get('output', ''), 'result': st.get('result') or {}}
    prev_name = run.get('last_step')
    prev_ctx = steps_ctx.get(prev_name, {'output': '', 'result': {}}) if prev_name else {'output': '', 'result': {}}
    return {
        'steps': steps_ctx,
        'prev': prev_ctx,
        'trigger': {'fired_at': (run.get('trigger') or {}).get('fired_at', '')},
        'run': {'id': run.get('id', '')},
    }


def render_template(template: str, ctx: dict) -> str:
    """Plain string substitution -- no expressions, no filters (spec Q3). An
    unresolvable slot fails the run at that step, loudly: a silently-empty
    substitution would be a fabricated prompt."""
    missing: list = []

    def _repl(m):
        path = m.group(1).split('.')
        cur: Any = ctx
        for part in path:
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                missing.append(m.group(0))
                return m.group(0)
        return str(cur)

    out = _SLOT_RE.sub(_repl, template or '')
    if missing:
        raise ValueError(f"unresolved slot(s): {', '.join(sorted(set(missing)))}")
    return out


def _strip_wf_result(text: str) -> str:
    """Drop the raw ```wf:result``` fence from what flows forward as a step's
    `output` -- the handoff moves conclusions, not protocol JSON (spec Q3:
    'What is explicitly not passed')."""
    if not text or 'wf:result' not in text:
        return text
    return _WF_RESULT_RE.sub('', text).strip()


def _parse_wf_result(text: str) -> Optional[dict]:
    """The LAST well-formed ```wf:result``` block in text, or None."""
    matches = list(_WF_RESULT_RE.finditer(text or ''))
    if not matches:
        return None
    try:
        data = json.loads(matches[-1].group(1).strip())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


_WF_RESULT_INSTRUCTION = """

---
This step feeds a branching decision. End your reply with a fenced block:
```wf:result
{{"outcome": "<one of: {outcomes}>", "summary": "<one-line summary>"}}
```
Pick exactly one outcome from that list. If none genuinely fit, omit the block
entirely rather than guess -- the run falls back to its "otherwise" branch,
which is the fail-closed default for exactly that case."""


# ── Run store ─────────────────────────────────────────────────────────────────

def _run_path(run_id: str) -> Path:
    return WORKFLOW_RUNS_DIR / f'{run_id}.json'  # type: ignore[operator]


def _read_run(run_id: str) -> Optional[dict]:
    p = _run_path(run_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        _log(f'[workflows] run {run_id[:12]} unreadable: {e}')
        return None


def _write_run(run: dict) -> None:
    run['updated'] = now_iso()
    WORKFLOW_RUNS_DIR.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
    _atomic_write_text(_run_path(run['id']), json.dumps(run, indent=2, ensure_ascii=False))


def list_runs(workflow_id: str, limit: int = 50) -> list:
    if WORKFLOW_RUNS_DIR is None or not WORKFLOW_RUNS_DIR.exists():
        return []
    runs = []
    for f in WORKFLOW_RUNS_DIR.glob('*.json'):
        try:
            r = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        if r.get('workflow_id') == workflow_id:
            runs.append(r)
    runs.sort(key=lambda r: r.get('created', ''), reverse=True)
    return runs[:limit]


def get_run(run_id: str) -> Optional[dict]:
    return _read_run(run_id)


def _has_live_run(workflow_id: str) -> bool:
    """One live run per workflow (spec Q5) -- same guard shape as the steward
    cycle's `_steward_cycle_running`: a trigger fire while a run is live is
    skipped, not queued."""
    for r in list_runs(workflow_id, limit=1000):
        if r.get('status') in ('running', 'waiting'):
            return True
    return False


# ── Starting + advancing a run ───────────────────────────────────────────────

def start_run(workflow_id: str, trigger_type: str = 'manual') -> dict:
    workflow = get_workflow(workflow_id)
    if not workflow:
        raise KeyError('workflow not found')
    if not workflow.get('enabled', True):
        raise ValueError('workflow is disabled')
    with _runs_lock:
        if _has_live_run(workflow_id):
            raise RuntimeError('a run of this workflow is already live')
        entry, _nodes = compile_workflow(workflow)
        run = {
            'id': f'run-{uuid.uuid4().hex[:8]}',
            'workflow_id': workflow_id,
            'status': 'running',
            'trigger': {'type': trigger_type, 'fired_at': now_iso()},
            'current_step': entry,
            'last_step': None,
            'steps': {},
            'error': None,
            'created': now_iso(),
            'updated': now_iso(),
        }
        _write_run(run)
        if entry is None:
            run['status'] = 'completed'
            _write_run(run)
            return run
    _advance_run(run['id'])
    return _read_run(run['id']) or run


def _fail_run(run: dict, message: str) -> None:
    run['status'] = 'failed'
    run['error'] = message
    _write_run(run)
    _log(f"[workflows] run {run['id'][:12]} failed: {message}")


def _advance_run(run_id: str) -> None:
    """Execute nodes starting at `run['current_step']` until the run blocks
    (an agent step was dispatched, or an approval gate is waiting) or ends.

    Serialized per-process by `_runs_lock` -- workflows explicitly forbid
    parallel fan-out (spec "Scope"), and this also protects the read-modify-
    write on the run file from the completion-callback thread and an HTTP
    request thread landing at once.
    """
    with _runs_lock:
        run = _read_run(run_id)
        if run is None or run.get('status') != 'running':
            return
        workflow = get_workflow(run['workflow_id'])
        if not workflow:
            _fail_run(run, 'workflow definition no longer exists')
            return
        try:
            _entry, nodes = compile_workflow(workflow)
        except ValueError as e:
            _fail_run(run, f'workflow no longer compiles: {e}')
            return

        while True:
            step_name = run.get('current_step')
            if step_name is None:
                run['status'] = 'completed'
                _write_run(run)
                return
            node = nodes.get(step_name)
            if node is None:
                _fail_run(run, f"step '{step_name}' no longer exists in the definition")
                return
            t = node.get('type')

            if t == 'agent':
                if not _dispatch_step(run, node):
                    return  # _dispatch_step already marked failure or is waiting
                _write_run(run)
                return  # waits for the completion callback

            if t == 'approval':
                run['status'] = 'waiting'
                run.setdefault('steps', {})[step_name] = {
                    'status': 'waiting', 'output': '', 'result': {}, 'error': None,
                }
                _write_run(run)
                _notify_approval_waiting(run, workflow, node)
                return

            if t == 'action':
                ok, output, err = _execute_action(run, node)
                run.setdefault('steps', {})[step_name] = {
                    'status': 'completed' if ok else 'failed',
                    'output': output, 'result': {}, 'error': err,
                }
                if not ok:
                    _fail_run(run, f"action step '{step_name}' failed: {err}")
                    return
                run['last_step'] = step_name
                run['current_step'] = (node.get('_next') or {}).get('name')
                continue

            _fail_run(run, f"unrunnable node type '{t}' at '{step_name}'")
            return


def _dispatch_step(run: dict, node: dict) -> bool:
    """Render the prompt, dispatch the agent, record pending step state.
    Returns False (and marks the run failed) on any dispatch-time error."""
    if _dispatch_agent_internal is None:
        _fail_run(run, 'dispatch not wired')
        return False
    name = node['name']
    ctx = _build_context(run)
    try:
        prompt = render_template(node.get('prompt') or '', ctx)
    except ValueError as e:
        _fail_run(run, f"step '{name}': {e}")
        return False
    next_info = node.get('_next') or {}
    if next_info.get('kind') == 'branches':
        prompt += _WF_RESULT_INSTRUCTION.format(outcomes=', '.join(next_info.get('outcomes') or []))
    try:
        session_id = _dispatch_agent_internal(
            node['project_id'], prompt,
            trigger_type='workflow', trigger_id=f"{run['id']}:{name}",
            character=node.get('character') or '',
            notify_workflow={'run_id': run['id'], 'step': name},
        )
    except Exception as e:
        _fail_run(run, f"step '{name}' dispatch failed: {e}")
        return False
    run.setdefault('steps', {})[name] = {
        'status': 'running', 'project_id': node['project_id'],
        'session_id': session_id, 'output': '', 'result': {}, 'error': None,
        'dispatched_at': now_iso(),
    }
    return True


# ── Agent step completion (the handoff) ──────────────────────────────────────

def on_agent_step_complete(run_id: str, step_name: str, project_id: str,
                           session_id: str, status: str, summary: str) -> None:
    """Called from `agent_routes._notify_workflow_step` when a workflow's
    agent step finishes (Mode A exit or Mode B turn boundary -- the same
    latch MC-946 already fires from, generalised; see that module). Also the
    entry point restart adoption replays through, with `status`/`summary`
    taken from the persisted agent_log instead of a live session.
    """
    with _runs_lock:
        run = _read_run(run_id)
        if run is None:
            return
        # Idempotency / restart-race guard: only apply if the run is still
        # actually parked on this exact step. A run that already advanced
        # (this callback firing twice, or racing restart adoption) is a no-op.
        if run.get('status') != 'running' or run.get('current_step') != step_name:
            return
        recorded_sid = (run.get('steps', {}).get(step_name) or {}).get('session_id')
        if recorded_sid and session_id and recorded_sid != session_id:
            _log(f"[workflows] run {run_id[:12]} step '{step_name}': ignoring "
                 f"completion from stale session {session_id[:12]} (project "
                 f"{project_id}), expected {recorded_sid[:12]}")
            return
        workflow = get_workflow(run['workflow_id'])
        if not workflow:
            _fail_run(run, 'workflow definition no longer exists')
            return
        try:
            _entry, nodes = compile_workflow(workflow)
        except ValueError as e:
            _fail_run(run, f'workflow no longer compiles: {e}')
            return
        node = nodes.get(step_name)
        if node is None:
            _fail_run(run, f"step '{step_name}' no longer exists in the definition")
            return

        if status == 'error':
            run['steps'][step_name] = {
                **(run['steps'].get(step_name) or {}),
                'status': 'failed', 'error': summary or 'agent ended in error',
            }
            _fail_run(run, f"step '{step_name}' agent ended in error")
            return

        next_info = node.get('_next') or {}
        result = _parse_wf_result(summary) if next_info.get('kind') == 'branches' else None
        output = _strip_wf_result(summary) if next_info.get('kind') == 'branches' else (summary or '')
        run['steps'][step_name] = {
            **(run['steps'].get(step_name) or {}),
            'status': 'completed', 'output': output, 'result': result or {}, 'error': None,
        }

        if next_info.get('kind') == 'branches':
            outcome = (result or {}).get('outcome')
            if outcome in (next_info.get('branches') or {}):
                next_name = next_info['branches'][outcome]
            else:
                # Missing or unparseable -> otherwise. Fail-closed by design
                # (spec Q3): an agent that did not declare a valid outcome is
                # routed to the branch the author designated for exactly that.
                next_name = next_info.get('otherwise')
        elif next_info.get('kind') == 'plain':
            next_name = next_info.get('name')
        else:
            _fail_run(run, f"step '{step_name}' has an unresolvable next pointer")
            return

        run['last_step'] = step_name
        run['current_step'] = next_name
        _write_run(run)

    _advance_run(run_id)


# ── Approval gate decision ───────────────────────────────────────────────────

def resolve_decision(run_id: str, choice: str) -> dict:
    """Resolve a parked approval gate. The runner cannot call this itself --
    only a human, via the route layer's agent-caller refusal (spec Scope: 'an
    approval gate cannot be removed, satisfied, or auto-answered by the
    runner or by any agent step')."""
    with _runs_lock:
        run = _read_run(run_id)
        if run is None:
            raise KeyError('run not found')
        if run.get('status') != 'waiting':
            raise ValueError(f"run is not waiting (status={run.get('status')})")
        step_name = run.get('current_step')
        workflow = get_workflow(run['workflow_id'])
        if not workflow:
            _fail_run(run, 'workflow definition no longer exists')
            raise ValueError('workflow definition no longer exists')
        _entry, nodes = compile_workflow(workflow)
        node = nodes.get(step_name)
        if node is None or node.get('type') != 'approval':
            raise ValueError('current step is not an approval gate')
        next_info = node.get('_next') or {}
        options = next_info.get('options') or []
        if choice not in options:
            raise ValueError(f"choice must be one of {options}")
        run['steps'][step_name] = {
            **(run['steps'].get(step_name) or {}),
            'status': 'completed', 'output': choice, 'result': {'choice': choice}, 'error': None,
        }
        run['last_step'] = step_name
        run['current_step'] = (next_info.get('branches') or {}).get(choice)
        run['status'] = 'running'
        _write_run(run)
    _advance_run(run_id)
    return _read_run(run_id) or run


def _notify_approval_waiting(run: dict, workflow: dict, node: dict) -> None:
    """Best-effort email so a parked approval gate doesn't sit silent. Reuses
    the existing mailer (AGENT_RULES: no new SMTP code); does not attempt the
    question_channel.py reply-parsing loop -- that module is bound to a live
    agent session's SSE-poll heartbeat, which a workflow run has none of.
    Resolution is `POST /api/workflow-runs/<run_id>/decision`, called by a
    human (run-view UI is a later phase)."""
    # Under pytest this function is exercised by every approval-gate test, and
    # it sends REAL mail: Ron's inbox took a run of "[Clayrune workflow]
    # DECISION NEEDED: gated / gated2 / adopt-wait" — those are fixture names,
    # not workflows he owns. Same failure class as the test suite spawning a
    # real `claude auth login` (af7e0a3): a test must never take an action the
    # outside world can see. Opt in explicitly to exercise the send itself.
    import os
    if os.environ.get('PYTEST_CURRENT_TEST') and not os.environ.get('MC_LIVE_MAIL_TESTS'):
        _log('[workflows] approval-gate email suppressed under pytest')
        return
    mailer = Path(__file__).resolve().parent.parent / 'tools' / 'night-review' / 'send_mail.py'
    if not mailer.exists():
        return
    options = (node.get('_next') or {}).get('options') or []
    body = (
        f"Workflow '{workflow.get('name','')}' is parked on an approval gate.\n\n"
        f"Run    : {run['id']}\n"
        f"Step   : {node.get('name','')}\n"
        f"Options: {', '.join(options)}\n\n"
        f"Resolve: POST /api/workflow-runs/{run['id']}/decision "
        f"{{\"choice\": \"<one of the options above>\"}}\n"
    )
    try:
        import subprocess
        import sys
        subprocess.run(
            [sys.executable, str(mailer), '--subject',
             f"[Clayrune workflow] DECISION NEEDED: {workflow.get('name','')}",
             '--body', body],
            capture_output=True, text=True, timeout=60)
    except Exception as e:
        _log(f"[workflows] approval-gate email failed: {e}")


# ── Clayrune actions (allowlisted, deterministic, no agent in the loop) ──────

def _http_json(method: str, path: str, payload: Optional[dict] = None) -> dict:
    url = f'http://127.0.0.1:{_port()}{path}'
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _execute_action(run: dict, node: dict) -> tuple:
    """(ok, output_text, error). Never raises -- dispatch-time errors become
    a terminal run failure, exactly like an agent step's dispatch failure."""
    ctx = _build_context(run)
    action = node.get('action')
    config = node.get('config') or {}
    try:
        rendered = {k: (render_template(v, ctx) if isinstance(v, str) else v)
                   for k, v in config.items()}
    except ValueError as e:
        return False, '', str(e)
    try:
        if action == 'backlog_create':
            pid = rendered.get('project_id')
            resp = _http_json('POST', f'/api/project/{pid}/backlog',
                              {'text': rendered.get('text', ''),
                               'priority': rendered.get('priority', 'normal')})
            item = resp.get('item') or {}
            return True, f"created {item.get('key', item.get('id', ''))}", None
        if action == 'backlog_patch':
            pid = rendered.get('project_id')
            item_id = rendered.get('item_id')
            patch = {k: v for k, v in rendered.items()
                     if k in ('status', 'text') and v is not None}
            resp = _http_json('PATCH', f'/api/project/{pid}/backlog/{item_id}', patch)
            item = resp.get('item') or {}
            return True, f"patched {item.get('key', item_id)}", None
        if action == 'desk_harvest':
            payload = {'project_id': rendered['project_id']} if rendered.get('project_id') else {}
            resp = _http_json('POST', '/api/desk/signals/harvest', payload)
            return True, json.dumps(resp), None
        return False, '', f"'{action}' is not in the allowlist"
    except urllib.error.HTTPError as e:
        return False, '', f'HTTP {e.code}: {e.read().decode(errors="replace")[:300]}'
    except Exception as e:
        return False, '', str(e)


# ── Restart adoption (fail-closed, spec Q5) ──────────────────────────────────

def adopt_on_startup() -> None:
    """Scan `data/workflow_runs/` for runs that were mid-step when the server
    went down. A `waiting` run is simply still waiting -- untouched. A run
    whose current step was `running` is checked against the agent_log the
    spawner callback already writes: if the child completed while we were
    down, replay its result through the normal completion path; if it cannot
    be confirmed complete, the run is marked `interrupted` rather than
    silently re-dispatched (an agent step may have had side effects)."""
    if WORKFLOW_RUNS_DIR is None or not WORKFLOW_RUNS_DIR.exists():
        return
    if _load_agent_log is None:
        _log('[workflows] adopt_on_startup: agent log not wired, skipping')
        return
    for f in WORKFLOW_RUNS_DIR.glob('*.json'):
        try:
            run = json.loads(f.read_text(encoding='utf-8'))
        except Exception as e:
            _log(f'[workflows] adopt: unreadable run file {f.name}: {e}')
            continue
        if run.get('status') != 'running':
            continue
        step_name = run.get('current_step')
        step_state = (run.get('steps') or {}).get(step_name) or {}
        sid = step_state.get('session_id')
        pid = step_state.get('project_id')
        if not sid or not pid:
            run['status'] = 'interrupted'
            run['error'] = f"step '{step_name}' has no recorded session to confirm"
            _write_run(run)
            continue
        try:
            log = _load_agent_log(pid) or []
        except Exception as e:
            log = []
            _log(f'[workflows] adopt: agent_log read failed for {pid}: {e}')
        entry = next((e for e in log if e.get('session_id') == sid), None)
        # "Confirmed complete" means the child actually finished its turn --
        # NOT merely that its agent_log row is no longer 'in_progress'.
        # `_reconcile_pending_agent_log_entries` (which runs immediately
        # before this, at boot) flips an orphaned in-flight row's status to
        # 'interrupted' -- that string means exactly "we don't know what
        # happened", the opposite of confirmed, and treating it as success
        # here would silently advance a run on a fabricated result. 'error'/
        # 'stopped' are equally not a confirmation. Only the two statuses
        # `_read_agent_stream[_b]` actually writes on a normal finish count.
        if entry and entry.get('status') in ('completed', 'idle'):
            _log(f"[workflows] adopting run {run['id'][:12]} step '{step_name}' "
                 f"-- child {sid[:12]} completed while down, advancing")
            on_agent_step_complete(
                run_id=run['id'], step_name=step_name, project_id=pid,
                session_id=sid, status=entry.get('status', 'unknown'),
                summary=entry.get('summary', ''))
        else:
            run['status'] = 'interrupted'
            run['error'] = (f"step '{step_name}' session {sid[:12]} not confirmed "
                            f"complete after restart")
            _write_run(run)
            _log(f"[workflows] run {run['id'][:12]} marked interrupted "
                 f"(step '{step_name}' unconfirmed)")
