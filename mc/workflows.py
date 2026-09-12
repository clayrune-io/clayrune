"""Workflow builder — runner + stores. Spec: `docs/WORKFLOW_BUILDER_SPEC.md`.

MC-871, Revision 2 build order steps 1-3 (store -> runner -> validation; the
canvas is step 4, a separate pass, not touched here). This module is backend
only: no route decorators live here (see `mc/blueprints/workflow_routes.py`
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

THE NODE MODEL — R2-D1: a flat `nodes` array plus an `edges` array. `_next`
(the v1 tree-of-lists pointer) is gone. An edge's optional `when` is a
property of the CONNECTION, not either endpoint: absent means unconditional,
a label means "this outcome/choice routes here", and the literal `"otherwise"`
is reserved as the fail-closed fallback target. `compile_workflow` builds
adjacency (`_parents`/`_children` per node, `_children` carrying `when`) and a
topological order; the runner never walks raw JSON, same as before.

A branching agent step (or an approval gate) declares its FULL outcome/choice
vocabulary on the node itself (`outcomes` for agent, `options` for approval)
independent of which of those are actually wired to an edge — an unconnected
outcome/option is a deliberate, visible run-end (R2-D6: "the Desk's `nothing`
outcome is exactly this — a stopped branch you can see"). Edges reference
that vocabulary; validation refuses an edge `when` that isn't in it (or the
reserved "otherwise").

JOIN + SKIP (R2-D2): a node with multiple incoming edges runs once every
parent is terminal (`completed`/`skipped`) and at least one parent's taken
edge led here. Skip is computed by a single forward pass over the topological
order after every step transition (`_propagate_skips`): a pending node whose
incoming edges are all resolved-and-none-taken becomes `skipped`. Because the
pass runs in topo order, a multi-hop skip (skip through a join, all-parents-
skipped) falls out of one pass with no separate case.

EXECUTION STAYS SERIAL (R2-D4): `frontier` (persisted, derived, replaces
`current_step`) is the list of ready-but-unstarted nodes; the runner starts
at most one per `_advance_run` iteration, in topological/stored order. This
is what keeps Q5's one-live-run reasoning, the completion-callback latch, and
single-witness restart adoption unchanged by the DAG reversal.

CYCLES (R2-D3): refused at save (`validate_workflow`, called by
create/update) and again at run start (`compile_workflow` re-validates — a
hand-edited store file is defence-in-depth, not just the CRUD path). The
third layer (refusing the connecting drag itself) is a canvas concern,
step 4, not implemented here.

THE AUTHORITY GUARD (settled, not open for re-litigation — see
`position_whetheranagentsessionmaycreateoreditworkflowdefi` in the project
memory dir): workflow DEFINITION CRUD and approval-gate DECISIONS are refused
for an agent caller, structurally, at the route layer
(`mc/blueprints/workflow_routes.py::_refuse_if_agent_caller`). This module
enforces the runtime half of the same principle: nothing here can satisfy,
skip, or auto-answer an approval gate (`resolve_decision` is the only path
that can complete one, and it is only reachable via that human-only route).
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

CURRENT_FORMAT = 2  # stamped on every record write; fixes the store version
                     # never actually reaching disk under the old STORE_VERSION name.

NODE_TYPES = ('agent', 'approval', 'action')
# R3-1's final v2 allowlist (docs/WORKFLOW_BUILDER_SPEC.md). Growing this is a
# spec change, not a config change -- every verb here was individually argued
# through the authority guard (R3-3) before landing.
ACTION_ALLOWLIST = ('backlog_create', 'backlog_patch', 'desk_harvest',
                     'journal_append', 'notify_operator', 'restore_point_create')
TRIGGER_TYPES = ('manual', 'schedule')
RESERVED_WHEN = 'otherwise'

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


# ── v1 -> v2 migration (R2-D1) ───────────────────────────────────────────────

def _migrate_v1_doc(record: dict) -> dict:
    """Upgrade a v1 nested-list record (no top-level `format` key) to
    `format: 2` nodes+edges. v1 had no rejoin (Q7's original, since-reversed
    decision), so every migrated edge has exactly one source -- lossless,
    deterministic, no separate migration tool. Applied on READ only; the
    upgraded shape is written back the next time the record is saved
    (`create_workflow`/`update_workflow` stamp `format: CURRENT_FORMAT`)."""
    nodes: list = []
    edges: list = []
    col = [0]

    def walk(steps_list, prev_name, prev_when):
        cur_prev, cur_when = prev_name, prev_when
        for node in steps_list or []:
            if not isinstance(node, dict):
                continue
            t = node.get('type')
            if t == 'paths':
                for label, sub in (node.get('branches') or {}).items():
                    walk(sub, cur_prev, label)
                if 'otherwise' in node:
                    walk(node.get('otherwise') or [], cur_prev, 'otherwise')
                return  # v1 paths is terminal in its own list -- no rejoin
            name = node.get('name')
            if not name:
                continue
            clean = {k: v for k, v in node.items() if k not in ('branches', 'otherwise', '_next')}
            clean.setdefault('x', col[0] * 260 + 60)
            clean.setdefault('y', 80)
            col[0] += 1
            nodes.append(clean)
            if cur_prev is not None:
                edge = {'from': cur_prev, 'to': name}
                if cur_when:
                    edge['when'] = cur_when
                edges.append(edge)
            if t == 'approval':
                for label in (node.get('options') or []):
                    walk((node.get('branches') or {}).get(label) or [], name, label)
                return  # v1 approval is also terminal in its own list
            cur_prev, cur_when = name, None

    walk(record.get('steps') or [], None, None)

    # v1's Paths declared its outcome vocabulary as branch-dict keys; migrate
    # that onto the preceding agent node's new `outcomes` field, since R2-D6
    # stores the vocabulary on the node, not derived from edges.
    outgoing_labels: dict = {}
    for e in edges:
        if e.get('when'):
            outgoing_labels.setdefault(e['from'], set()).add(e['when'])
    for n in nodes:
        if n.get('type') == 'agent' and n['name'] in outgoing_labels:
            n['outcomes'] = sorted(l for l in outgoing_labels[n['name']] if l != RESERVED_WHEN)

    migrated = {k: v for k, v in record.items() if k != 'steps'}
    migrated['format'] = CURRENT_FORMAT
    migrated['nodes'] = nodes
    migrated['edges'] = edges
    return migrated


# ── Definitions store ────────────────────────────────────────────────────────

def _read_definitions() -> list:
    if WORKFLOWS_PATH is None or not WORKFLOWS_PATH.exists():
        return []
    try:
        data = json.loads(WORKFLOWS_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        _log(f'[workflows] store unreadable, treating as empty: {e}')
        return []
    if not isinstance(data, list):
        return []
    return [_migrate_v1_doc(r) if isinstance(r, dict) and 'format' not in r else r
            for r in data]


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
        'format': CURRENT_FORMAT,
        'nodes': doc.get('nodes') or [],
        'edges': doc.get('edges') or [],
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
        existing['format'] = CURRENT_FORMAT
        existing['nodes'] = doc.get('nodes') or []
        existing['edges'] = doc.get('edges') or []
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

def _declared_vocab(node: dict) -> list:
    t = node.get('type')
    if t == 'agent':
        return list(node.get('outcomes') or [])
    if t == 'approval':
        return list(node.get('options') or [])
    return []


def _toposort(names: list, edges: list) -> tuple:
    """Kahn's algorithm. Ties among simultaneously-ready nodes are broken by
    `names`' original order (R2-D4: "ties broken by stored node order").
    Returns (order, cyclic_names_or_None)."""
    indeg = {n: 0 for n in names}
    children: dict = {n: [] for n in names}
    for e in edges:
        frm, to = e.get('from'), e.get('to')
        if frm in children and to in indeg:
            children[frm].append(to)
            indeg[to] += 1
    queue = [n for n in names if indeg[n] == 0]
    order: list = []
    i = 0
    while i < len(queue):
        n = queue[i]
        i += 1
        order.append(n)
        for c in children[n]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if len(order) != len(names):
        return order, set(names) - set(order)
    return order, None


def _compute_dominators(order: list, parents_map: dict) -> dict:
    """Standard iterative dominator fixpoint (R2-D5). `parents_map[n]` is the
    list of parent node names (edge direction, ignoring `when`). A root (no
    parents) dominates only itself. Workflows are "tens of lines of JSON"
    (spec Q1) -- an O(n^2) fixpoint needs no Lengauer-Tarjan."""
    all_names = set(order)
    roots = [n for n in order if not parents_map.get(n)]
    dom = {n: ({n} if n in roots else set(all_names)) for n in order}
    changed = True
    while changed:
        changed = False
        for n in order:
            if n in roots:
                continue
            preds = parents_map.get(n) or []
            if not preds:
                continue
            new_dom = set.intersection(*(dom[p] for p in preds)) | {n}
            if new_dom != dom[n]:
                dom[n] = new_dom
                changed = True
    return dom


def validate_workflow(doc: dict) -> list:
    """Return a list of error strings; empty = valid. Never raises.

    Implements two of R2-D3's three cycle-refusal layers (save-time here,
    run-time via `compile_workflow` re-calling this) plus R2-D5's dominator
    rule for `{{steps.X.*}}`/`{{prev.*}}` slots. The third layer -- refusing
    the connecting drag itself -- is a canvas concern (step 4), not
    implemented here.
    """
    errors: list = []
    trigger = doc.get('trigger') or {'type': 'manual'}
    if not isinstance(trigger, dict) or trigger.get('type') not in TRIGGER_TYPES:
        errors.append(f"trigger.type must be one of {TRIGGER_TYPES}")

    nodes = doc.get('nodes')
    if not isinstance(nodes, list) or not nodes:
        errors.append('nodes must be a non-empty list')
        return errors

    edges = doc.get('edges')
    if edges is None:
        edges = []
    if not isinstance(edges, list):
        errors.append('edges must be a list')
        return errors

    names_seen: set = set()
    nodes_by_name: dict = {}
    for node in nodes:
        if not isinstance(node, dict):
            errors.append('node is not an object')
            continue
        name = node.get('name')
        t = node.get('type')
        if not name or not isinstance(name, str):
            errors.append(f"node of type '{t}' missing a name")
            continue
        if name in names_seen:
            errors.append(f"duplicate node name '{name}'")
            continue
        names_seen.add(name)
        if t not in NODE_TYPES:
            errors.append(f"unknown node type '{t}' for '{name}'")
            continue
        nodes_by_name[name] = node
        if t == 'agent':
            if not node.get('project_id'):
                errors.append(f"agent step '{name}' missing project_id")
            if not (node.get('prompt') or '').strip():
                errors.append(f"agent step '{name}' missing prompt")
            outcomes = node.get('outcomes') or []
            if RESERVED_WHEN in outcomes:
                errors.append(f"agent step '{name}': '{RESERVED_WHEN}' is reserved, not a declared outcome")
            if len(set(outcomes)) != len(outcomes):
                errors.append(f"agent step '{name}' has duplicate outcomes")
        elif t == 'approval':
            options = node.get('options') or []
            if not options:
                errors.append(f"approval node '{name}' has no options")
            if RESERVED_WHEN in options:
                errors.append(f"approval node '{name}': '{RESERVED_WHEN}' is reserved, not a declared option")
            if len(set(options)) != len(options):
                errors.append(f"approval node '{name}' has duplicate options")
        elif t == 'action':
            if node.get('action') not in ACTION_ALLOWLIST:
                errors.append(f"action step '{name}' action must be one of {ACTION_ALLOWLIST}")

    if errors:
        return errors

    edge_list: list = []
    for e in edges:
        if not isinstance(e, dict):
            errors.append('edge is not an object')
            continue
        frm, to, when = e.get('from'), e.get('to'), e.get('when')
        if frm not in nodes_by_name:
            errors.append(f"edge references unknown node '{frm}'")
            continue
        if to not in nodes_by_name:
            errors.append(f"edge references unknown node '{to}'")
            continue
        if when is not None and not isinstance(when, str):
            errors.append(f"edge {frm}->{to} 'when' must be a string")
            continue
        node_from = nodes_by_name[frm]
        if when is not None:
            if node_from.get('type') == 'action':
                errors.append(f"action step '{frm}' cannot have conditional outgoing edges")
                continue
            vocab = _declared_vocab(node_from)
            if when != RESERVED_WHEN and when not in vocab:
                errors.append(f"edge {frm}->{to}: '{when}' is not a declared outcome/option of '{frm}'")
                continue
        edge_list.append({'from': frm, 'to': to, 'when': when})

    if errors:
        return errors

    names = list(nodes_by_name.keys())
    order, cyclic = _toposort(names, edge_list)
    if cyclic:
        errors.append(f"workflow has a cycle involving: {', '.join(sorted(cyclic))}")
        return errors

    parents_map: dict = {n: [] for n in names}
    for e in edge_list:
        parents_map[e['to']].append(e['from'])
    dom = _compute_dominators(order, parents_map)

    for name, node in nodes_by_name.items():
        texts: list = []
        if node.get('type') == 'agent':
            texts.append(node.get('prompt') or '')
        elif node.get('type') == 'action':
            texts.extend(v for v in (node.get('config') or {}).values() if isinstance(v, str))
        n_parents = len(parents_map.get(name) or [])
        for text in texts:
            for m in _SLOT_RE.finditer(text):
                path = m.group(1)
                if path.startswith('steps.'):
                    ref = path.split('.')[1]
                    if ref == name:
                        errors.append(f"'{name}' cannot reference its own output")
                    elif ref not in nodes_by_name:
                        errors.append(f"'{name}' references unknown step '{ref}'")
                    elif ref not in dom.get(name, set()):
                        errors.append(
                            f"'{name}' uses {{{{steps.{ref}.*}}}} but '{ref}' does not "
                            f"dominate '{name}' -- it can be skipped on some path into '{name}'")
                elif path.startswith('prev.'):
                    if n_parents != 1:
                        errors.append(
                            f"'{name}' uses {{{{prev.*}}}} but has {n_parents} parent(s) "
                            f"-- 'prev' is ambiguous except with exactly one")

    return errors


def compile_workflow(workflow: dict) -> tuple:
    """(nodes_by_name, order). Re-validates -- R2-D3's run-time cycle layer,
    and defence in depth against a hand-edited store file generally -- and
    raises ValueError if the stored definition somehow fails (should not
    happen; CRUD validates on write)."""
    errors = validate_workflow(workflow)
    if errors:
        raise ValueError('; '.join(errors))
    nodes = workflow.get('nodes') or []
    edges = workflow.get('edges') or []
    nodes_by_name = {n['name']: dict(n) for n in nodes}
    for n in nodes_by_name.values():
        n['_parents'] = []
        n['_children'] = []
    for e in edges:
        frm, to, when = e.get('from'), e.get('to'), e.get('when')
        nodes_by_name[to]['_parents'].append({'from': frm, 'when': when})
        nodes_by_name[frm]['_children'].append({'to': to, 'when': when})
    names = list(nodes_by_name.keys())
    order, _ = _toposort(names, edges)
    return nodes_by_name, order


# ── Slot substitution ────────────────────────────────────────────────────────

def _build_context(run: dict, node: dict) -> dict:
    steps_ctx = {}
    for name, st in (run.get('steps') or {}).items():
        steps_ctx[name] = {'output': st.get('output', ''), 'result': st.get('result') or {}}
    parents = node.get('_parents') or []
    if len(parents) == 1:
        prev_ctx = steps_ctx.get(parents[0]['from'], {'output': '', 'result': {}})
    else:
        # Validation refuses {{prev.*}} on any node without exactly one
        # parent (R2-D5) -- this fallback is only ever rendered into a
        # template that can't actually contain the slot.
        prev_ctx = {'output': '', 'result': {}}
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


# ── Frontier: join rule + skip-propagation (R2-D2, R2-D4) ────────────────────

def _edge_state(steps: dict, parent_name: str, when: Optional[str]) -> str:
    """'taken' | 'dead' | 'unknown' for the edge (parent_name --when--> ...)."""
    st = steps.get(parent_name) or {}
    status = st.get('status')
    if status == 'skipped':
        return 'dead'
    if status != 'completed':
        return 'unknown'
    if when is None:
        return 'taken'
    chosen = st.get('chosen_when')
    return 'taken' if chosen == when else 'dead'


def _node_ready(node: dict, steps: dict) -> bool:
    parents = node.get('_parents') or []
    if not parents:
        return True  # a root: ready the moment the run starts
    states = [_edge_state(steps, p['from'], p.get('when')) for p in parents]
    if any(s == 'unknown' for s in states):
        return False
    return any(s == 'taken' for s in states)


def _propagate_skips(run: dict, nodes: dict, order: list) -> None:
    """One forward pass in topological order. A pending node whose incoming
    edges are ALL resolved (no longer 'unknown') and NONE taken becomes
    `skipped`. Because the pass is topological, a node that only becomes
    skippable as a RESULT of an earlier skip in this same pass (skip through
    a join, or every parent skipped) is caught in the same call -- no fixpoint
    loop needed."""
    steps = run.setdefault('steps', {})
    for name in order:
        st = steps.setdefault(name, {'status': 'pending', 'output': '', 'result': {},
                                     'error': None, 'chosen_when': None})
        if st.get('status') != 'pending':
            continue
        parents = nodes[name].get('_parents') or []
        if not parents:
            continue
        states = [_edge_state(steps, p['from'], p.get('when')) for p in parents]
        if any(s == 'unknown' for s in states):
            continue
        if not any(s == 'taken' for s in states):
            st['status'] = 'skipped'


def _compute_frontier(run: dict, nodes: dict, order: list) -> list:
    steps = run.setdefault('steps', {})
    ready = []
    for name in order:
        st = steps.setdefault(name, {'status': 'pending', 'output': '', 'result': {},
                                     'error': None, 'chosen_when': None})
        if st.get('status') != 'pending':
            continue
        if _node_ready(nodes[name], steps):
            ready.append(name)
    return ready


def _any_in_flight(run: dict) -> bool:
    return any(st.get('status') in ('running', 'waiting') for st in (run.get('steps') or {}).values())


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
        _, order = compile_workflow(workflow)
        steps = {name: {'status': 'pending', 'output': '', 'result': {}, 'error': None,
                        'chosen_when': None} for name in order}
        run = {
            'id': f'run-{uuid.uuid4().hex[:8]}',
            'workflow_id': workflow_id,
            'status': 'running',
            'trigger': {'type': trigger_type, 'fired_at': now_iso()},
            'frontier': [],
            'steps': steps,
            'error': None,
            'created': now_iso(),
            'updated': now_iso(),
        }
        _write_run(run)
    _advance_run(run['id'])
    return _read_run(run['id']) or run


def _fail_run(run: dict, message: str) -> None:
    run['status'] = 'failed'
    run['error'] = message
    _write_run(run)
    _log(f"[workflows] run {run['id'][:12]} failed: {message}")


def _advance_run(run_id: str) -> None:
    """Execute frontier nodes, one at a time, until the run blocks (an agent
    step was dispatched, or an approval gate is waiting) or ends.

    Serialized per-process by `_runs_lock` -- workflows still run one agent
    step in flight at a time (R2-D4: the DAG expresses dependency, not
    parallelism), and this also protects the read-modify-write on the run
    file from the completion-callback thread and an HTTP request thread
    landing at once.
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
            nodes, order = compile_workflow(workflow)
        except ValueError as e:
            _fail_run(run, f'workflow no longer compiles: {e}')
            return

        while True:
            _propagate_skips(run, nodes, order)
            frontier = _compute_frontier(run, nodes, order)
            run['frontier'] = frontier
            if not frontier:
                if _any_in_flight(run):
                    _write_run(run)
                    return  # waits for the completion callback or a decision
                run['status'] = 'completed'
                _write_run(run)
                return

            name = frontier[0]
            node = nodes[name]
            t = node.get('type')

            if t == 'agent':
                if not _dispatch_step(run, node):
                    return  # _dispatch_step already marked failure
                run['frontier'] = _compute_frontier(run, nodes, order)  # 'name' just left pending
                _write_run(run)
                return  # waits for the completion callback

            if t == 'approval':
                run['status'] = 'waiting'
                run['steps'][name] = {**run['steps'].get(name, {}), 'status': 'waiting'}
                run['frontier'] = _compute_frontier(run, nodes, order)
                _write_run(run)
                _notify_approval_waiting(run, workflow, node)
                return

            if t == 'action':
                ok, output, err = _execute_action(run, node, workflow)
                run['steps'][name] = {
                    **run['steps'].get(name, {}),
                    'status': 'completed' if ok else 'failed',
                    'output': output, 'error': err,
                }
                if not ok:
                    _fail_run(run, f"action step '{name}' failed: {err}")
                    return
                continue  # recompute skips/frontier -- may unlock more work

            _fail_run(run, f"unrunnable node type '{t}' at '{name}'")
            return


def _dispatch_step(run: dict, node: dict) -> bool:
    """Render the prompt, dispatch the agent, record pending step state.
    Returns False (and marks the run failed) on any dispatch-time error."""
    if _dispatch_agent_internal is None:
        _fail_run(run, 'dispatch not wired')
        return False
    name = node['name']
    ctx = _build_context(run, node)
    try:
        prompt = render_template(node.get('prompt') or '', ctx)
    except ValueError as e:
        _fail_run(run, f"step '{name}': {e}")
        return False
    outcomes = node.get('outcomes') or []
    if outcomes:
        prompt += _WF_RESULT_INSTRUCTION.format(outcomes=', '.join(outcomes))
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
        'chosen_when': None, 'dispatched_at': now_iso(),
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
        st = (run.get('steps') or {}).get(step_name) or {}
        # Idempotency / restart-race guard: only apply if this exact step is
        # still actually the one running. A run that already advanced (this
        # callback firing twice, or racing restart adoption) is a no-op.
        if run.get('status') != 'running' or st.get('status') != 'running':
            return
        recorded_sid = st.get('session_id')
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
            nodes, _ = compile_workflow(workflow)
        except ValueError as e:
            _fail_run(run, f'workflow no longer compiles: {e}')
            return
        node = nodes.get(step_name)
        if node is None:
            _fail_run(run, f"step '{step_name}' no longer exists in the definition")
            return

        if status == 'error':
            run['steps'][step_name] = {
                **st, 'status': 'failed', 'error': summary or 'agent ended in error',
            }
            _fail_run(run, f"step '{step_name}' agent ended in error")
            return

        outcomes = node.get('outcomes') or []
        result = _parse_wf_result(summary) if outcomes else None
        output = _strip_wf_result(summary) if outcomes else (summary or '')
        chosen_when = None
        if outcomes:
            outcome = (result or {}).get('outcome')
            # Missing or unparseable -> otherwise. Fail-closed by design
            # (spec Q3): an agent that did not declare a valid outcome routes
            # to whatever the author wired the "otherwise" port to -- or, if
            # nothing is wired there, this branch simply ends (R2-D6).
            chosen_when = outcome if outcome in outcomes else RESERVED_WHEN
        run['steps'][step_name] = {
            **st, 'status': 'completed', 'output': output, 'result': result or {},
            'error': None, 'chosen_when': chosen_when,
        }
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
        step_name = next((n for n, st in (run.get('steps') or {}).items()
                          if st.get('status') == 'waiting'), None)
        if step_name is None:
            raise ValueError('no step is currently waiting')
        workflow = get_workflow(run['workflow_id'])
        if not workflow:
            _fail_run(run, 'workflow definition no longer exists')
            raise ValueError('workflow definition no longer exists')
        try:
            nodes, _ = compile_workflow(workflow)
        except ValueError as e:
            _fail_run(run, f'workflow no longer compiles: {e}')
            raise
        node = nodes.get(step_name)
        if node is None or node.get('type') != 'approval':
            raise ValueError('current step is not an approval gate')
        options = node.get('options') or []
        if choice not in options:
            raise ValueError(f"choice must be one of {options}")
        run['steps'][step_name] = {
            **run['steps'].get(step_name, {}),
            'status': 'completed', 'output': choice, 'result': {'choice': choice},
            'error': None, 'chosen_when': choice,
        }
        run['status'] = 'running'
        _write_run(run)
    _advance_run(run_id)
    return _read_run(run['id']) or run


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
    options = node.get('options') or []
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

# Module-level so tests can monkeypatch it the same way they redirect
# WORKFLOWS_PATH/WORKFLOW_RUNS_DIR -- a workflow test run must never write
# into the real docs/_journal.
_JOURNAL_DIR = Path(__file__).resolve().parent.parent / 'docs' / '_journal'


def _journal_append(item_id: str, text: str, title: str = '') -> str:
    """Append one dated entry to docs/_journal/<item_id>-<slug>.md, creating
    it on first use. This is the sanctioned unattended log path (AGENT_RULES/
    CLAUDE.md, 2026-08-15): a workflow run is unattended machinery and must
    never write a backlog note. Filename convention matches
    `tools/backlog_journal_common.py`'s `journal_name()` so a workflow's
    entries land in the same file that tool already treats as canonical for
    this item; if that file already exists (however it was created) we append
    to it rather than starting a second one under a different slug."""
    journal_dir = _JOURNAL_DIR
    journal_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(journal_dir.glob(f'{item_id}-*.md'))
    if existing:
        path = existing[0]
    else:
        slug = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')[:48].rstrip('-') or 'item'
        path = journal_dir / f'{item_id}-{slug}.md'
    with path.open('a', encoding='utf-8') as f:
        f.write(f'\n### {now_iso()}  ·  workflow\n\n{text.strip()}\n')
    try:
        return str(path.relative_to(Path(__file__).resolve().parent.parent))
    except ValueError:
        return str(path)


def _send_operator_notification(subject: str, body: str) -> tuple:
    """Best-effort email via the existing mailer (AGENT_RULES: no new SMTP
    code, no new creds) -- same mechanism `_notify_approval_waiting` already
    uses for the approval-gate channel. The recipient is resolved entirely
    inside `send_mail.py` from server config/env; no argument here can carry
    one. That is the whole reason `notify_operator` is on the allowlist while
    `mail_send` is refused (R3-1): an authorable destination is an
    exfiltration primitive, a fixed operator inbox is not. Do not add a `to`/
    `cc`/`recipient` parameter to this function."""
    if os.environ.get('PYTEST_CURRENT_TEST') and not os.environ.get('MC_LIVE_MAIL_TESTS'):
        _log('[workflows] notify_operator suppressed under pytest')
        return True, 'suppressed under pytest'
    mailer = Path(__file__).resolve().parent.parent / 'tools' / 'night-review' / 'send_mail.py'
    if not mailer.exists():
        return False, 'mailer not found'
    try:
        import subprocess
        import sys
        r = subprocess.run(
            [sys.executable, str(mailer), '--subject', subject, '--body', body],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return False, f'send_mail exited {r.returncode}: {(r.stderr or r.stdout or "").strip()[:200]}'
        return True, 'sent'
    except Exception as e:
        return False, str(e)


def _http_json(method: str, path: str, payload: Optional[dict] = None) -> dict:
    url = f'http://127.0.0.1:{_port()}{path}'
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}


def _execute_action(run: dict, node: dict, workflow: Optional[dict] = None) -> tuple:
    """(ok, output_text, error). Never raises -- dispatch-time errors become
    a terminal run failure, exactly like an agent step's dispatch failure."""
    ctx = _build_context(run, node)
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
        if action == 'journal_append':
            item_id = rendered.get('item_id') or ''
            text = rendered.get('text') or ''
            if not item_id or not text.strip():
                return False, '', 'journal_append requires item_id and text'
            rel = _journal_append(item_id, text, rendered.get('title') or '')
            return True, f'appended to {rel}', None
        if action == 'notify_operator':
            # No `to`/`cc`/`recipient` is ever read from `rendered` here --
            # see _send_operator_notification's docstring. The message is the
            # only authorable part of this action.
            message = rendered.get('message') or ''
            wf_name = (workflow or {}).get('name', '')
            subject = f"[Clayrune workflow] {wf_name}: {node.get('name', '')}".strip(': ')
            ok, info = _send_operator_notification(subject, message or '(no message)')
            if not ok:
                return False, '', f'notify_operator failed: {info}'
            return True, info, None
        if action == 'restore_point_create':
            pid = rendered.get('project_id')
            payload = {'label': rendered.get('label')} if rendered.get('label') else {}
            resp = _http_json('POST', f'/api/backup/restore-point/{pid}', payload)
            return True, f"restore point {resp.get('snap_id', '')}", None
        return False, '', f"'{action}' is not in the allowlist"
    except urllib.error.HTTPError as e:
        return False, '', f'HTTP {e.code}: {e.read().decode(errors="replace")[:300]}'
    except Exception as e:
        return False, '', str(e)


# ── Restart adoption (fail-closed, spec Q5 / R2-D4) ──────────────────────────

def adopt_on_startup() -> None:
    """Scan `data/workflow_runs/` for runs that were mid-step when the server
    went down. A `waiting` run is simply still waiting -- untouched. A run
    with status `running` has at most one node in `running` state (R2-D4:
    execution is serial), checked against the agent_log the spawner callback
    already writes: if the child completed while we were down, replay its
    result through the normal completion path (which recomputes skips and
    the frontier exactly as a live completion would); if it cannot be
    confirmed complete, the run is marked `interrupted` rather than silently
    re-dispatched (an agent step may have had side effects)."""
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
        step_name = next((n for n, st in (run.get('steps') or {}).items()
                          if st.get('status') == 'running'), None)
        if step_name is None:
            # Nothing in flight -- re-derive the frontier and continue
            # (covers e.g. an action step that hadn't been picked up yet).
            _advance_run(run['id'])
            continue
        step_state = run['steps'][step_name]
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
