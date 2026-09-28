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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from mc import state
from mc.core import _atomic_write_text, _log, now_iso
from mc import engine_fallback as _engine_fallback  # MC-961: swap record + blocked-run pointer
from mc.blueprints.push_mobile import _notify_push  # MC-961: same pointer, the notify/inbox surface

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

NODE_TYPES = ('agent', 'approval', 'action', 'wait')
# R3-1's final v2 allowlist (docs/WORKFLOW_BUILDER_SPEC.md). Growing this is a
# spec change, not a config change -- every verb here was individually argued
# through the authority guard (R3-3) before landing.
ACTION_ALLOWLIST = ('backlog_create', 'backlog_patch', 'desk_harvest',
                     'journal_append', 'notify_operator', 'restore_point_create')
TRIGGER_TYPES = ('manual', 'schedule')
RESERVED_WHEN = 'otherwise'
# The only agent-session end states that are a RESULT for the next step
# (`_read_agent_stream[_b]` and `_mode_a_reader` write them on a normal
# finish). 'interrupted' means "we don't know what happened"
# (`_reconcile_pending_agent_log_entries` at boot), 'stopped' means someone hit
# Stop, 'error' is a failure. See on_agent_step_complete.
STEP_SUCCESS_STATUSES = ('completed', 'idle')
WAIT_MODES = ('delay', 'until')

# Hyphens are allowed: step names come from agent/character type names such as
# `us-stock-investor`, and the builder tells users to reference a step as
# {{steps.NAME.output}}. Without `-` the slot never matched, so it was neither
# validated nor filled, and apex_trader's email went out as the literal
# placeholder text (run-20ca7b13, 2026-09-15).
# A step or schedule may pin the engine it runs on. Same id shapes the chat
# path accepts (agent_set_model / agent_dispatch): the value goes to the CLI's
# --model/--effort argv, so a leading '-' or anything exotic is refused here,
# at save time, instead of reaching a subprocess. Before these fields existed a
# workflow step and a scheduled run could not ask for a model at all -- they
# silently ran the project default, which the 2026-09-19 live pass recorded as
# 'claude-opus-5' where 'claude-sonnet-5' was wanted (no_silent_vendor_model_change).
_ENGINE_MODEL_RE = re.compile(r'[A-Za-z0-9._\[\]][A-Za-z0-9._\[\]-]{0,59}')
_ENGINE_EFFORT_RE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,31}')


def engine_field_errors(obj: dict, label: str) -> list:
    """Errors for an optional `model` / `effort` pin on `obj`; [] = valid.
    Absent or '' means "inherit the project default", exactly as a chat does."""
    errors: list = []
    for key, rx in (('model', _ENGINE_MODEL_RE), ('effort', _ENGINE_EFFORT_RE)):
        if key not in obj or obj.get(key) in (None, ''):
            continue
        val = obj.get(key)
        if not isinstance(val, str) or not rx.fullmatch(val.strip()):
            errors.append(f"{label}: invalid {key} {val!r}")
    return errors


_SLOT_RE = re.compile(r'\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}')
_WF_RESULT_RE = re.compile(r'```[ \t]*wf:result[ \t\r\n]*(.*?)```', re.DOTALL | re.IGNORECASE)


def wire(*, workflows_path=None, workflow_runs_dir=None,
         dispatch_agent_internal_fn=None, load_agent_log_fn=None,
         session_summary_fn=None):
    """Late-bind cross-family deps + paths. Called once by server.py."""
    global WORKFLOWS_PATH, WORKFLOW_RUNS_DIR, _dispatch_agent_internal, _load_agent_log
    global _session_summary
    if session_summary_fn is not None:
        _session_summary = session_summary_fn
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

def _parse_iso(ts: str) -> datetime:
    """Raises on anything that isn't a real timestamp -- both the save-time
    validator and the resume poll below need that failure to be loud rather
    than silently treating a garbled `at` as "already due" or "never due"."""
    dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
            errors.extend(engine_field_errors(node, f"agent step '{name}'"))
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
        elif t == 'wait':
            config = node.get('config') or {}
            mode = config.get('mode')
            if mode not in WAIT_MODES:
                errors.append(f"wait step '{name}' config.mode must be one of {WAIT_MODES}")
            elif mode == 'delay':
                minutes = config.get('minutes')
                if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
                    errors.append(f"wait step '{name}' needs a positive number of minutes")
            elif mode == 'until':
                at = config.get('at')
                if not isinstance(at, str) or not at.strip():
                    errors.append(f"wait step '{name}' needs a 'config.at' date/time")
                else:
                    try:
                        _parse_iso(at)
                    except Exception:
                        errors.append(f"wait step '{name}' has an unparseable 'config.at' value")

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
            from_type = node_from.get('type')
            if from_type in ('action', 'wait'):
                errors.append(f"{from_type} step '{frm}' cannot have conditional outgoing edges")
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


# ── Agent-assisted draft + review (MC-962) ───────────────────────────────────
#
# Human-only at the route layer (`workflow_routes.py::_refuse_if_agent_caller`
# gates `/draft`; the authority guard is enforced there, not here). This
# section only ever PRODUCES a proposal or an ANALYSIS -- neither writes to
# WORKFLOWS_PATH, and `draft_workflow` forces `enabled: False` on every
# result regardless of what the model returned, so the human is always the
# one who calls `create_workflow` (position
# `whetheranagentsessionmaycreateoreditworkflowdefi`, 2026-09-18: agents may
# draft, only a human enables/schedules/runs).
#
# The model call is a TOOLLESS oneshot -- same primitive as `mc/mail_launder.py`
# (`run_text_transform`, `--allowedTools ''`/`--strict-mcp-config` under the
# hood) -- on whichever engine `default_runtime_name()` resolves, never
# hardcoded to Claude. A provider not yet certified tool-free
# (`tool_free_transform_enforced=False`, e.g. Codex as of 2026-09) is refused
# loudly rather than silently substituted for Claude -- mail_launder's own
# precedent: "a Codex/Gemini-only install just doesn't get [this], rather
# than getting it through an unverified runtime."
#
# Everything the model returns is untrusted: parsed strictly (a JSON object/
# array or nothing -- no partial-credit salvage), size-capped before it ever
# reaches `validate_workflow`, and re-checked against the real node/action
# vocabulary by `validate_workflow` itself. `draft_workflow` retries once,
# feeding the validator's own errors back verbatim; `review_workflow`'s model
# findings are advisory only and never gate anything -- a human reads them.

_DRAFT_MAX_NODES = 25
_DRAFT_MAX_EDGES = 80
_DRAFT_MAX_FIELD_CHARS = 4000
_DRAFT_MAX_DESCRIPTION_CHARS = 4000
_DRAFT_MAX_RAW_CHARS = 60_000
_DRAFT_TIMEOUT = 90

_REVIEW_MAX_DEFINITION_BYTES = 80_000
_REVIEW_MAX_FINDINGS = 40
_REVIEW_MAX_FINDING_CHARS = 600
_REVIEW_TIMEOUT = 90
_REVIEW_SEVERITIES = ('info', 'warning', 'error')

_DRAFT_NODE_FIELDS = {
    'agent': {'project_id', 'prompt', 'character', 'outcomes', 'model', 'effort'},
    'approval': {'options'},
    'action': {'action', 'config'},
    'wait': {'config'},
}
_DRAFT_NODE_COMMON_FIELDS = {'name', 'type'}
_DRAFT_EDGE_FIELDS = {'from', 'to', 'when'}
# Mirrors static/js/workflow-builder.js's own WFB_TOOL_SPOT_CARD_W/H/GAP (a
# card is 260px wide; 140/32 are that file's own guessed-height/gap constants
# for a fresh card before its real DOM height is known) -- not measured here
# (no DOM to measure), but the same order of magnitude, so a drafted layout
# looks like the rest of the canvas rather than a different grid.
_DRAFT_LAYOUT_COL_W = 260 + 32
_DRAFT_LAYOUT_ROW_H = 140 + 32
_DRAFT_LAYOUT_ORIGIN_X = 60
_DRAFT_LAYOUT_ORIGIN_Y = 80

_NODE_VOCAB_DOC = f"""WORKFLOW DEFINITION FORMAT (format: 2) -- the only shape you may output:

{{
  "format": 2,
  "name": "short title",
  "description": "one sentence",
  "trigger": {{"type": "manual"}},
  "nodes": [ ... ],
  "edges": [ {{"from": "<node name>", "to": "<node name>", "when": "<optional outcome/option label, or omit for unconditional>"}} ]
}}

NODE TYPES (put exactly one of these shapes in each entry of "nodes"; every
node needs a unique "name" and a "type" from this list -- no others exist):

1. "agent" -- dispatches a real agent session.
   Fields: project_id (required, string), prompt (required, string -- the
   task text), character (optional, "{{scope}}:{{name}}" e.g. "global:builder",
   from the roster below -- omit to run personaless), outcomes (optional
   list of short labels this step may end in, e.g. ["worth_it", "skip"] --
   only declare these if a downstream edge needs to branch on the result),
   model/effort (optional, leave empty to inherit the project default).
   A prompt may reference an earlier step's output with
   "{{{{steps.<name>.output}}}}" or its declared result field with
   "{{{{steps.<name>.result.<key>}}}}", or its single direct parent with
   "{{{{prev.output}}}}" (only valid when the node has exactly one parent).
   A trigger-time value is "{{{{trigger.fired_at}}}}".

2. "approval" -- pauses the run for a human decision.
   Fields: options (required, non-empty list of choice labels, e.g.
   ["approve", "decline"]).

3. "action" -- a single deterministic, inward-facing side effect. No other
   verb exists; do not invent one.
   Fields: action (required, one of: {', '.join(ACTION_ALLOWLIST)}), config
   (object, string values may use the same {{{{...}}}} slots as a prompt):
     - backlog_create: config.project_id, config.text, config.priority (optional)
     - backlog_patch: config.project_id, config.item_id, config.status and/or config.text
     - desk_harvest: config.project_id (optional)
     - journal_append: config.item_id (required), config.text (required), config.title (optional)
     - notify_operator: config.message (required) -- recipient is never authorable, do not add one
     - restore_point_create: config.project_id, config.label (optional)

4. "wait" -- pauses before continuing.
   Fields: config.mode = "delay" (+ config.minutes, a positive number) or
   config.mode = "until" (+ config.at, an ISO-8601 timestamp).

EDGES connect nodes by name. "when" is optional: omit it for an
unconditional edge; set it to one of the FROM node's declared
outcomes/options to make a branch; the literal "otherwise" is reserved for a
fallback edge. "action" and "wait" steps cannot have a conditional outgoing
edge. A node with multiple incoming edges runs once every parent has
finished (completed or skipped) and at least one led here -- an outcome or
option with no edge is a deliberate, visible stop, not an error.

No other node type, action verb, trigger type, or top-level key exists.
Trigger type is "manual" or "schedule" -- always write "manual" here; a
human decides scheduling later. Never set "enabled" -- it is always false on
a draft, decided by whoever reviews and saves it."""


def _draft_roster_text(project_path: str, project_id: str) -> str:
    """Bench roster (persona ref + who it's for) for the "character" field,
    from the SAME source the picker/dispatch use (`mc.characters`) -- never
    a hardcoded list that could drift from what actually resolves."""
    from mc import characters as _characters
    try:
        chars = _characters.list_characters(
            project_path=project_path or None, project_id=project_id or None)
    except Exception:
        chars = []
    lines: list = []
    for c in chars:
        ref = f"{c.get('scope')}:{c.get('name')}"
        agent_name = (c.get('agent_name') or '').strip()
        role = (c.get('description') or '').strip()
        label = f"{ref} ({agent_name})" if agent_name else ref
        lines.append(f"- {label}: {role}" if role else f"- {label}")
    if not lines:
        return ('(no characters are defined on this box -- leave every agent '
               'step\'s "character" field empty)')
    return '\n'.join(lines)


def _load_project_path(project_id: str) -> str:
    """Lazy import: `project_routes` imports `workflow_routes`, which imports
    THIS module -- importing it at module scope would be circular. Deferred
    to call time, after both modules have finished loading, avoids it."""
    if not project_id:
        return ''
    try:
        from mc.blueprints.project_routes import load_project
        p = load_project(project_id)
    except Exception:
        return ''
    return (p or {}).get('project_path') or ''


def _parse_json_value(raw: Optional[str], open_ch: str, close_ch: str,
                      expect: type, *, max_chars: int) -> Any:
    """Tolerant-but-strict extraction, same discipline as
    `mail_launder._parse_digest_json`: strip markdown fences/leading prose by
    taking the outermost bracket pair, but the interior must parse as clean
    JSON of exactly the expected shape -- no partial-credit salvage. Returns
    None on anything else, which every caller here treats as a hard failure,
    never a fallback to raw text."""
    if not raw:
        return None
    raw = raw[:max_chars]
    i, j = raw.find(open_ch), raw.rfind(close_ch)
    if i < 0 or j < 0 or j < i:
        return None
    try:
        data = json.loads(raw[i:j + 1])
    except Exception:
        return None
    return data if isinstance(data, expect) else None


def _shape_errors(data: Any) -> list:
    """Pre-`validate_workflow` size/shape caps on the model's raw draft --
    'reject unknown node types/verbs, cap sizes' (MC-962 brief). Type/verb
    correctness is `validate_workflow`'s job (it already refuses an unknown
    node type or disallowed action); this only bounds what could otherwise
    make that call expensive or the returned proposal unreviewable."""
    if not isinstance(data, dict):
        return ['top-level response must be a JSON object']
    errors: list = []
    nodes = data.get('nodes')
    if not isinstance(nodes, list):
        errors.append("'nodes' must be a list")
    elif len(nodes) > _DRAFT_MAX_NODES:
        errors.append(f"too many nodes ({len(nodes)}); cap is {_DRAFT_MAX_NODES}")
    edges = data.get('edges')
    if edges is not None and not isinstance(edges, list):
        errors.append("'edges' must be a list")
    elif isinstance(edges, list) and len(edges) > _DRAFT_MAX_EDGES:
        errors.append(f"too many edges ({len(edges)}); cap is {_DRAFT_MAX_EDGES}")

    seen_long_field = False

    def _walk(v):
        nonlocal seen_long_field
        if isinstance(v, str):
            if not seen_long_field and len(v) > _DRAFT_MAX_FIELD_CHARS:
                seen_long_field = True
        elif isinstance(v, dict):
            for vv in v.values():
                _walk(vv)
        elif isinstance(v, list):
            for vv in v:
                _walk(vv)
    _walk(nodes if isinstance(nodes, list) else [])
    _walk(edges if isinstance(edges, list) else [])
    if seen_long_field:
        errors.append(f"a text field exceeds {_DRAFT_MAX_FIELD_CHARS} chars")
    return errors


def _draft_auto_layout(nodes: list, edges: list) -> list:
    """Assign non-overlapping x/y to every drafted node, in place, and return
    the root names (no incoming edge) for the caller to wire as
    `trigger.entry`. The model is never trusted for placement -- MC-962
    follow-up (Ron's screenshot): two drafted nodes landed at the same spot
    and rendered stacked, one hiding the other's ports.

    Layered by graph depth (longest path from a root, so a node with several
    parents sits after ALL of them, never beside one) -- column = depth,
    row = position within that depth, in the model's own node order for a
    deterministic result. A cycle should already be refused by
    `validate_workflow` after this runs, but this pass runs BEFORE that check
    (`draft_workflow`'s retry loop calls this, then validates), so it must
    terminate and produce SOME layout even if the model handed back a loop:
    any node whose depth can't be resolved in a fixed number of relaxation
    passes (bounded by node count) is dropped to the deepest resolved column
    + 1, same as a node discovered after that point."""
    names = [n.get('name') for n in nodes if isinstance(n.get('name'), str)]
    name_set = set(names)
    valid_edges = [e for e in edges if e.get('from') in name_set and e.get('to') in name_set]
    parents: dict = {n: [] for n in names}
    for e in valid_edges:
        parents[e['to']].append(e['from'])
    has_incoming = {e['to'] for e in valid_edges}
    roots = [n for n in names if n not in has_incoming]
    if not roots and names:
        roots = [names[0]]  # every node has a parent -- an all-cycle draft; anchor somewhere

    depth: dict = {n: 0 for n in roots}
    for _ in range(len(names) + 1):
        changed = False
        for n in names:
            if n in roots:
                continue
            ps = [depth[p] for p in parents.get(n, []) if p in depth]
            if not ps:
                continue
            want = max(ps) + 1
            if depth.get(n) != want:
                depth[n] = want
                changed = True
        if not changed:
            break
    unresolved = [n for n in names if n not in depth]
    if unresolved:
        floor = (max(depth.values()) + 1) if depth else 0
        for n in unresolved:
            depth[n] = floor

    rows_used: dict = {}
    by_name = {n.get('name'): n for n in nodes if isinstance(n.get('name'), str)}
    for n in names:
        d = depth[n]
        row = rows_used.get(d, 0)
        rows_used[d] = row + 1
        node = by_name[n]
        node['x'] = _DRAFT_LAYOUT_ORIGIN_X + d * _DRAFT_LAYOUT_COL_W
        node['y'] = _DRAFT_LAYOUT_ORIGIN_Y + row * _DRAFT_LAYOUT_ROW_H
    return roots


def _normalize_draft_doc(data: dict, *, default_project_id: str) -> dict:
    """Rebuild a clean doc from only recognised keys -- the model's raw JSON
    is untrusted, so this is an allowlist copy, not a filter. `format` and
    `enabled` are never taken from the model: format is always the current
    store format, and a draft is always unsaved/disabled regardless of what
    the model wrote."""
    out: dict = {
        'format': CURRENT_FORMAT,
        'enabled': False,
        'name': str(data.get('name') or '').strip()[:_DRAFT_MAX_FIELD_CHARS] or 'Untitled workflow',
        'description': str(data.get('description') or '').strip()[:_DRAFT_MAX_FIELD_CHARS],
        'trigger': {'type': 'manual'},
    }
    nodes_out: list = []
    for node in (data.get('nodes') or []):
        if not isinstance(node, dict):
            continue
        raw_type = node.get('type')
        t: str = raw_type if isinstance(raw_type, str) else ''
        clean: dict = {k: node.get(k) for k in _DRAFT_NODE_COMMON_FIELDS if k in node}
        for k in _DRAFT_NODE_FIELDS.get(t, ()):
            if k in node:
                clean[k] = node.get(k)
        if t == 'agent':
            pid = clean.get('project_id')
            if not (isinstance(pid, str) and pid.strip()):
                clean['project_id'] = default_project_id
        nodes_out.append(clean)
    out['nodes'] = nodes_out
    edges_out = []
    for edge in (data.get('edges') or []):
        if not isinstance(edge, dict):
            continue
        edges_out.append({k: edge.get(k) for k in _DRAFT_EDGE_FIELDS if k in edge})
    out['edges'] = edges_out
    # Auto-layout (never the model's own x/y -- see _draft_auto_layout) plus
    # trigger.entry = the resulting roots, so the canvas draws a real
    # trigger -> first-step line instead of leaving it an unwired root with
    # just a warning badge (static/js/workflow-builder.js _wfRedrawEdges only
    # draws that implied line for names in trigger.entry).
    roots = _draft_auto_layout(nodes_out, edges_out)
    out['trigger']['entry'] = roots
    return out


def _run_toolless(provider: str, prompt: str, *, timeout: int) -> str:
    """The one seam that talks to a model in this module -- always
    tool-free, always through the registry (`run_text_transform`), never a
    hardcoded provider. Raises on any failure; callers turn that into an
    `ok: False` envelope, matching `mail_launder`'s fail-closed contract."""
    import mc.agent_runtime as _agent_runtime
    return _agent_runtime.run_text_transform(
        provider, prompt=prompt, model='', timeout=timeout)


def draft_workflow(description: str, project_id: str) -> dict:
    """Turn a plain-English description into a format-2 workflow proposal.
    Never writes to the store -- returns `{'definition': ..., 'errors': [...],
    'valid': bool}` (or `{'ok': False, 'error': ..., 'detail': ...}` if no
    usable draft could be produced at all) for the caller (the human-only
    `/api/workflows/draft` route) to show and let a human decide whether to
    save via the existing `create_workflow`.
    """
    description = (description or '').strip()
    if not description:
        raise ValueError('description is required')
    if len(description) > _DRAFT_MAX_DESCRIPTION_CHARS:
        raise ValueError(f'description too long (max {_DRAFT_MAX_DESCRIPTION_CHARS} chars)')
    project_id = (project_id or '').strip()

    import mc.agent_runtime as _agent_runtime
    provider = _agent_runtime.default_runtime_name()

    project_path = _load_project_path(project_id)
    roster = _draft_roster_text(project_path, project_id)
    base_prompt = (
        "You are drafting a Clayrune WORKFLOW definition from a human's plain-"
        "English description. Output ONLY the JSON object described below -- "
        "no prose, no markdown code fence, nothing before or after it.\n\n"
        f"DESCRIPTION FROM THE HUMAN:\n{description}\n\n"
        f"TARGET PROJECT (use this project_id on agent/action steps unless the "
        f"description clearly names another): {project_id or '(none given -- pick one from context or leave steps generic)'}\n\n"
        f"AVAILABLE AGENT CHARACTERS (for an agent step's optional \"character\" field):\n{roster}\n\n"
        f"{_NODE_VOCAB_DOC}"
    )

    doc: Optional[dict] = None
    errors: list = ['no attempt made']
    for attempt in range(2):
        prompt = base_prompt
        if attempt > 0:
            prompt += (
                "\n\nYour previous attempt was INVALID. Fix every one of these "
                "problems and return the corrected JSON object only, nothing "
                "else:\n- " + "\n- ".join(errors)
            )
        try:
            text = _run_toolless(provider, prompt, timeout=_DRAFT_TIMEOUT)
        except Exception as e:
            return {'ok': False, 'error': 'draft_call_failed', 'detail': str(e)}
        data = _parse_json_value(text, '{', '}', dict, max_chars=_DRAFT_MAX_RAW_CHARS)
        if data is None:
            errors = ['response was not a single valid JSON object']
            doc = None
            continue
        shape_errs = _shape_errors(data)
        if shape_errs:
            errors = shape_errs
            doc = None
            continue
        doc = _normalize_draft_doc(data, default_project_id=project_id)
        errors = validate_workflow(doc)
        if not errors:
            break

    if doc is None:
        return {'ok': False, 'error': 'draft_parse_failed', 'detail': '; '.join(errors)}
    return {'ok': True, 'definition': doc, 'errors': errors, 'valid': not errors}


def _review_roster_text(doc: dict) -> str:
    project_ids: list = []
    for node in (doc.get('nodes') or []):
        if isinstance(node, dict) and node.get('type') == 'agent':
            pid = node.get('project_id')
            if isinstance(pid, str) and pid and pid not in project_ids:
                project_ids.append(pid)
    if not project_ids:
        return _draft_roster_text('', '')
    seen_refs: set = set()
    lines: list = []
    for pid in project_ids:
        text = _draft_roster_text(_load_project_path(pid), pid)
        for line in text.splitlines():
            if line not in seen_refs:
                seen_refs.add(line)
                lines.append(line)
    return '\n'.join(lines) if lines else _draft_roster_text('', '')


def _sanitize_review_findings(data: Any, node_names: set) -> list:
    if not isinstance(data, list):
        return []
    out: list = []
    for item in data[:_REVIEW_MAX_FINDINGS]:
        if not isinstance(item, dict):
            continue
        message = str(item.get('message') or '').strip()[:_REVIEW_MAX_FINDING_CHARS]
        if not message:
            continue
        node = item.get('node')
        if not (isinstance(node, str) and node in node_names):
            node = None
        severity = item.get('severity')
        if severity not in _REVIEW_SEVERITIES:
            severity = 'info'
        suggestion = item.get('suggestion')
        suggestion = (str(suggestion).strip()[:_REVIEW_MAX_FINDING_CHARS]
                     if isinstance(suggestion, str) and suggestion.strip() else None)
        out.append({'node': node, 'severity': severity, 'message': message,
                   'suggestion': suggestion})
    return out


def _model_review_findings(doc: dict) -> tuple:
    """(findings, error). Best-effort: a failed/unparseable model call
    returns ([], <reason>) -- the caller still has `validate_workflow`'s
    deterministic errors, so a model outage degrades review, it doesn't
    block it (unlike `draft_workflow`, this never produces something meant
    to be saved -- there is no fail-closed obligation here)."""
    try:
        serialized = json.dumps(doc, ensure_ascii=False)
    except Exception as e:
        return [], f'definition not serializable: {e}'
    if len(serialized.encode('utf-8')) > _REVIEW_MAX_DEFINITION_BYTES:
        return [], f'definition exceeds {_REVIEW_MAX_DEFINITION_BYTES} bytes for review'

    import mc.agent_runtime as _agent_runtime
    provider = _agent_runtime.default_runtime_name()
    roster = _review_roster_text(doc)
    prompt = (
        "You are reviewing a Clayrune WORKFLOW definition for logic problems "
        "a human author would want flagged before saving/enabling it -- you "
        "are NOT validating its JSON shape (that is already checked "
        "separately). Output ONLY a JSON array, no prose, no markdown fence, "
        "each element exactly:\n"
        '{"node": "<node name from the definition, or null if it applies to '
        'the whole workflow>", "severity": "info|warning|error", "message": '
        '"<the problem, one or two sentences>", "suggestion": "<a concrete '
        'fix, or null>"}\n\n'
        "Look specifically for: unreachable or dead-end nodes (a node no "
        "edge can ever reach, or a branch that quietly goes nowhere when "
        "that looks unintentional); steps with no failure handling for "
        "something that plausibly fails (this format has no fenced "
        "on_failure yet -- flag it as a suggestion, not an error); approval "
        "gates placed somewhere that delays a decision that didn't need "
        "one, or missing before something irreversible; vague, contradictory, "
        "or underspecified agent prompts; a step's \"character\" pin that "
        "doesn't match what that persona is described as being for. Return "
        "an empty array if you find nothing worth flagging -- do not invent "
        "findings to have something to say.\n\n"
        f"AGENT CHARACTERS ON THIS BOX (for judging a persona/role mismatch):\n{roster}\n\n"
        f"THE DEFINITION:\n{serialized}"
    )
    try:
        text = _run_toolless(provider, prompt, timeout=_REVIEW_TIMEOUT)
    except Exception as e:
        return [], str(e)
    data = _parse_json_value(text, '[', ']', list, max_chars=_DRAFT_MAX_RAW_CHARS)
    if data is None:
        return [], 'review call did not return a valid JSON array'
    node_names = {n.get('name') for n in (doc.get('nodes') or [])
                 if isinstance(n, dict) and isinstance(n.get('name'), str)}
    return _sanitize_review_findings(data, node_names), None


def review_workflow(doc: Optional[dict] = None, workflow_id: Optional[str] = None) -> dict:
    """Read-only: `validate_workflow`'s deterministic errors plus advisory
    model findings on logic (never mutates anything, never gates anything --
    the caller decides what to do with the result). Raises KeyError if
    `workflow_id` doesn't resolve, ValueError if neither argument names a
    definition."""
    if workflow_id:
        record = get_workflow(workflow_id)
        if record is None:
            raise KeyError('workflow not found')
        doc = record
    if not isinstance(doc, dict):
        raise ValueError('definition or workflow_id is required')

    errors = validate_workflow(doc)
    findings = [{'node': None, 'severity': 'error', 'message': e, 'suggestion': None}
               for e in errors]
    model_findings, model_error = _model_review_findings(doc)
    findings.extend(model_findings)
    return {'ok': True, 'valid': not errors, 'errors': errors,
           'findings': findings, 'model_error': model_error}


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
            # The definition this run executes, frozen at start. See
            # `_run_definition` for why a run must never re-read the live one.
            'definition': _snapshot_definition(workflow),
            'frontier': [],
            'steps': steps,
            'error': None,
            'created': now_iso(),
            'updated': now_iso(),
        }
        _write_run(run)
    _advance_run(run['id'])
    return _read_run(run['id']) or run


_SNAPSHOT_KEYS = ('id', 'name', 'format', 'trigger', 'nodes', 'edges')


def _snapshot_definition(workflow: dict) -> dict:
    """Deep copy of the parts of a definition a run executes against."""
    return json.loads(json.dumps({k: workflow[k] for k in _SNAPSHOT_KEYS if k in workflow}))


def _run_definition(run: dict) -> Optional[dict]:
    """The definition THIS run executes: the snapshot `start_run` froze into
    it, never the live store.

    Every step after the first used to re-read the live definition via
    `get_workflow`, while `run['steps']` stayed keyed by the node names that
    existed at start. So an edit made while a run was in flight changed the
    run under its own feet: run-42a3f2aa (2026-09-12) was started with its
    email step named `action-2`, Ron renamed that node `Send email` while the
    agent step was still out, and advancing would have looked for a step the
    run had never heard of. Editing a workflow affects FUTURE runs only.

    Runs written before the snapshot existed carry no `definition` key; those
    fall back to the live definition, which is exactly the pre-snapshot
    behaviour (and fails loudly, not silently, if a step was renamed)."""
    snap = run.get('definition')
    if isinstance(snap, dict) and isinstance(snap.get('nodes'), list):
        return snap
    return get_workflow(run['workflow_id'])


LIVE_RUN_STATUSES = ('running', 'waiting')


def cancel_run(run_id: str) -> dict:
    """Operator cancel of a live (`running`/`waiting`) run -> `cancelled`.

    Without this a run that never gets its completion callback bricks its
    workflow: `_has_live_run` refuses every new run while one is live, and
    nothing else could ever move it out of `running`.

    Does NOT stop an in-flight agent session. The step is marked `cancelled`
    and its session id is kept (and listed in `left_running`) so the caller
    can say which chat is still going; that agent may be mid-way through a
    commit or an external write, and killing it is a separate decision the
    operator makes from its own chat's Stop control. A late completion from
    that session is a no-op: `on_agent_step_complete` only applies to a
    `running` run, and every other entry point (`_advance_run`,
    `resolve_decision`, `resume_due_waits`, `adopt_on_startup`) gates on
    `running`/`waiting` the same way."""
    with _runs_lock:
        run = _read_run(run_id)
        if run is None:
            raise KeyError('run not found')
        if run.get('status') not in LIVE_RUN_STATUSES:
            raise ValueError(f"run is not live (status={run.get('status')})")
        left_running = []
        for name, st in (run.get('steps') or {}).items():
            if st.get('status') in LIVE_RUN_STATUSES:
                run['steps'][name] = {**st, 'status': 'cancelled'}
                if st.get('session_id'):
                    left_running.append({'step': name, 'project_id': st.get('project_id', ''),
                                         'session_id': st['session_id']})
        run['status'] = 'cancelled'
        run['error'] = 'cancelled by operator'
        run['cancelled_at'] = now_iso()
        run['left_running'] = left_running
        run['frontier'] = []
        _write_run(run)
    _log(f"[workflows] run {run_id[:12]} cancelled by operator "
         f"({len(left_running)} agent session(s) left running)")
    return run


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
        workflow = _run_definition(run)
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

            if t == 'wait':
                # Parks the WHOLE run, same shape as an approval gate, rather
                # than blocking this thread with a real sleep -- the runner is
                # serial and single-process (R2-D4), so a `time.sleep` here
                # would hold `_runs_lock` (or, released, would still tie up
                # this worker) for up to the wait's own duration, and would
                # not survive a restart at all. Parking with a persisted
                # `resume_at` costs nothing while waiting and survives a
                # restart for free: `resume_due_waits` (below) is polled from
                # the scheduler's existing 30s tick and just finds the
                # deadline on disk, the same way a `waiting` approval gate is
                # already left untouched by `adopt_on_startup` and picked up
                # whenever the human decides. This is why a long wait is safe
                # here even though a blocking sleep would not be.
                resume_at = _compute_wait_resume_at(node)
                if resume_at is None:
                    _fail_run(run, f"wait step '{name}' has invalid config")
                    return
                run['status'] = 'waiting'
                run['steps'][name] = {**run['steps'].get(name, {}), 'status': 'waiting',
                                       'resume_at': resume_at}
                run['frontier'] = _compute_frontier(run, nodes, order)
                _write_run(run)
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


def _compute_wait_resume_at(node: dict) -> Optional[str]:
    """The absolute UTC instant a wait step should resolve at, computed ONCE
    when the step is first dispatched (not re-derived on every poll -- a
    'delay' wait counts from when the run actually reached it, not from
    workflow-definition time). Returns None on a config validation would
    have already refused; callers treat that as a run-failure, not a silent
    skip."""
    config = node.get('config') or {}
    mode = config.get('mode')
    if mode == 'delay':
        minutes = config.get('minutes')
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
            return None
        return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat().replace('+00:00', 'Z')
    if mode == 'until':
        at = config.get('at')
        if not isinstance(at, str) or not at.strip():
            return None
        try:
            _parse_iso(at)  # stored/compared verbatim; this just confirms it parses
        except Exception:
            return None
        return at
    return None


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
            # An explicit per-step pin wins over the character's and the
            # project's default -- the same precedence a chat dispatch gets.
            # A pin naming a model that belongs to a DIFFERENT provider still
            # hard-refuses (ValueError, step fails visibly, below) -- that
            # never changes. MC-961: a pin whose own vendor is out of
            # allowance is a different case -- if the user has opted into
            # `engine_fallback_order`, dispatch may swap this step to a
            # fallback vendor (dropping the pin for that vendor's own
            # default model) instead of failing outright. Off by default;
            # see mc/engine_fallback.py.
            model_override=(node.get('model') or '').strip(),
            effort_override=(node.get('effort') or '').strip() or None,
            notify_workflow={'run_id': run['id'], 'step': name},
        )
    except _engine_fallback.EngineFallbackBlocked as e:
        # MC-961 item 4: no fallback resolved (unset, or every configured
        # entry itself unusable) -- str(e) is already the vendor+reset+pointer
        # message (EngineFallbackBlocked.__init__ passes payload['error']
        # straight through), so `_fail_run` below records the same pointer a
        # blocked chat/schedule refusal gets. This is the notify/inbox half.
        _fail_run(run, f"step '{name}' dispatch failed: {e}")
        try:
            _notify_push(
                title='Workflow run blocked',
                body=f"{run['id'][:12]} step '{name}' — {e.payload.get('error', str(e))}",
                project_id=node['project_id'], kind='agent')
        except Exception as ne:
            _log(f"[workflows] blocked-run notification failed: {ne}")
        return False
    except Exception as e:
        _fail_run(run, f"step '{name}' dispatch failed: {e}")
        return False
    # MC-961 item 3: "record on the run" -- the swap already happened inside
    # _dispatch_agent_internal (loud there: agent_log + chat line +
    # notification, see _apply_engine_fallback); this step also carries the
    # same {from,to,reason,vendor_reset} record so the workflow's own Runs
    # panel (which renders `steps`, not the joined agent_log row) shows it
    # without a second lookup.
    _live = state.agent_sessions.get(session_id) or {}
    run.setdefault('steps', {})[name] = {
        'status': 'running', 'project_id': node['project_id'],
        'session_id': session_id, 'output': '', 'result': {}, 'error': None,
        'chosen_when': None, 'dispatched_at': now_iso(),
        'engine_fallback': _live.get('engine_fallback'),
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
        workflow = _run_definition(run)
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
        if status not in STEP_SUCCESS_STATUSES:
            # 'stopped' (someone hit Stop), 'interrupted' (boot reconcile's
            # "we don't know what happened") or anything unrecognised is NOT a
            # result. This used to fall through to 'completed', so stopping a
            # workflow's agent mid-task handed its half-finished text to the
            # next step as if it were the answer. Same fail-closed rule
            # adopt_on_startup always applied, now applied to every caller.
            run['steps'][step_name] = {
                **st, 'status': 'failed',
                'error': f"agent session ended '{status or 'unknown'}', not completed",
            }
            run['status'] = 'interrupted'
            run['error'] = (f"step '{step_name}' session {(session_id or '')[:12]} "
                            f"ended '{status or 'unknown'}' without completing")
            _write_run(run)
            _log(f"[workflows] run {run_id[:12]} marked interrupted: {run['error']}")
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
        workflow = _run_definition(run)
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


# ── Wait resume (polled, not blocked) ────────────────────────────────────────

def resume_due_waits() -> int:
    """Resolve every parked `wait` step whose `resume_at` has passed. This is
    the ONLY thing that ever advances a wait -- there is no blocking sleep
    anywhere in the runner (see the `t == 'wait'` branch in `_advance_run`
    for why). The caller is `mc/blueprints/scheduler_routes.py`'s existing
    30s scheduler tick, already running in the server process; this function
    adds no thread, no timer, and no new concurrency to the runner, which
    stays exactly as serial as R2-D4 requires.

    Also the reason a long wait needs no separate restart-adoption path
    (unlike an in-flight agent step): `resume_at` is a plain field on the
    persisted run, so the very next tick after a restart just finds it due
    (or not) -- `adopt_on_startup` already leaves `waiting` runs untouched
    for exactly this reason (a parked approval gate is the same shape and
    already relies on it). No side effect can have been missed by going
    down, because nothing runs *during* a wait.

    Returns the number of steps resolved, for tests and the scheduler's own
    logging -- never raises; a single bad run file is logged and skipped so
    it can't starve every other run's waits behind it.
    """
    if WORKFLOW_RUNS_DIR is None or not WORKFLOW_RUNS_DIR.exists():
        return 0
    now = datetime.now(timezone.utc)
    resumed = 0
    for f in WORKFLOW_RUNS_DIR.glob('*.json'):
        run_id = f.stem
        run_to_advance = None
        with _runs_lock:
            try:
                run = _read_run(run_id)
            except Exception as e:
                _log(f'[workflows] resume_due_waits: unreadable run {run_id[:12]}: {e}')
                continue
            if run is None or run.get('status') != 'waiting':
                continue
            step_name = next((n for n, st in (run.get('steps') or {}).items()
                              if st.get('status') == 'waiting' and 'resume_at' in st), None)
            if step_name is None:
                continue  # e.g. an approval gate -- a human resolves that, not this
            resume_at = run['steps'][step_name].get('resume_at') or ''
            try:
                due = _parse_iso(resume_at) <= now
            except Exception as e:
                _log(f"[workflows] run {run_id[:12]} step '{step_name}': "
                     f"unparseable resume_at {resume_at!r}, resuming now rather "
                     f"than wedging the run forever: {e}")
                due = True
            if not due:
                continue
            run['steps'][step_name] = {
                **run['steps'][step_name], 'status': 'completed',
                'output': f'waited until {resume_at}', 'error': None, 'chosen_when': None,
            }
            run['status'] = 'running'
            _write_run(run)
            run_to_advance = run_id
            resumed += 1
        if run_to_advance:  # outside the lock -- _advance_run takes it itself
            _advance_run(run_to_advance)
    return resumed


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
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
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
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
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
        step_name = _running_step(run)
        if step_name is None:
            # Nothing in flight -- re-derive the frontier and continue
            # (covers e.g. an action step that hadn't been picked up yet).
            _advance_run(run['id'])
            continue
        # Boot: no session can be live yet, so only the durable agent_log
        # can confirm. Anything short of a confirmed end is interrupted.
        verdict, status, summary = _step_session_verdict(run['steps'][step_name],
                                                         use_live=False)
        if verdict == 'ended':
            _replay_step_end(run, step_name, status, summary, 'completed while down')
        else:
            _interrupt_unconfirmed(run['id'], step_name,
                                   'not confirmed complete after restart')


# ── Running-step reconciler (the backstop for a missed completion hook) ─────

# Every status a session can END in. A live session in any other status
# (normally 'running') is still working and is left alone, however long.
_STEP_END_STATUSES = STEP_SUCCESS_STATUSES + ('error', 'stopped', 'interrupted')
# How long a running agent step may have NO live session and NO completion
# record before the reconciler stops waiting and marks the run interrupted.
# Only a race window needs covering here (dispatch -> session registered,
# process exit -> agent-log row written), so minutes, not hours.
STALLED_STEP_SECONDS_DEFAULT = 600
# agent_routes._last_reply_text, wired by server.py: the summary a Mode B
# turn-boundary hook would have delivered, for a live session whose agent_log
# row is still the dispatch-time 'in_progress' placeholder.
_session_summary: Optional[Callable[[dict], str]] = None


def _running_step(run: dict) -> Optional[str]:
    return next((n for n, st in (run.get('steps') or {}).items()
                 if st.get('status') == 'running'), None)


def _step_session_verdict(step_state: dict, *, use_live: bool) -> tuple:
    """('in_flight' | 'ended' | 'unconfirmed', status, summary) for one
    running agent step -- the ONE place both restart adoption and the periodic
    reconciler decide whether a step's agent actually finished.

    Order: a live session that is still working wins (never touch it). Then
    the durable agent_log row, if it is past its dispatch-time 'in_progress'
    placeholder -- the same row and summary `_log_agent_completion` handed the
    hook. Then a live session that has ended but whose row is not written yet
    (a Mode B turn boundary never writes one), using the hook's own summary
    function. Nothing else confirms anything."""
    sid = step_state.get('session_id') or ''
    pid = step_state.get('project_id') or ''
    if not sid or not pid:
        return 'unconfirmed', None, ''
    live = state.agent_sessions.get(sid) if use_live else None
    if live is not None and live.get('status') not in _STEP_END_STATUSES:
        return 'in_flight', live.get('status'), ''
    entry = None
    if _load_agent_log is not None:
        try:
            entry = next((e for e in (_load_agent_log(pid) or [])
                          if e.get('session_id') == sid), None)
        except Exception as e:
            _log(f'[workflows] agent_log read failed for {pid}: {e}')
    if entry and entry.get('status') and entry.get('status') != 'in_progress':
        return 'ended', entry.get('status'), entry.get('summary', '') or ''
    if live is not None:
        if _session_summary is None:
            return 'in_flight', live.get('status'), ''  # wait for the row
        try:
            summary = _session_summary(live) or ''
        except Exception as e:
            _log(f'[workflows] live summary failed for {sid[:12]}: {e}')
            summary = ''
        return 'ended', live.get('status'), summary
    return 'unconfirmed', None, ''


def _replay_step_end(run: dict, step_name: str, status, summary: str, why: str) -> None:
    st = run['steps'][step_name]
    _log(f"[workflows] run {run['id'][:12]} step '{step_name}': child "
         f"{(st.get('session_id') or '')[:12]} ended '{status}' ({why}), replaying "
         f"through on_agent_step_complete")
    on_agent_step_complete(
        run_id=run['id'], step_name=step_name, project_id=st.get('project_id', ''),
        session_id=st.get('session_id', ''), status=status or 'unknown',
        summary=summary or '')


def _interrupt_unconfirmed(run_id: str, step_name: str, reason: str) -> bool:
    """Mark a run interrupted because its running step's end can't be
    confirmed. Re-checked under the lock: only if that exact step is still
    running, so a completion that landed meanwhile always wins."""
    with _runs_lock:
        run = _read_run(run_id)
        if run is None or run.get('status') != 'running':
            return False
        st = (run.get('steps') or {}).get(step_name) or {}
        if st.get('status') != 'running':
            return False
        sid = (st.get('session_id') or '')[:12]
        run['steps'][step_name] = {**st, 'stalled_at': now_iso()}
        run['status'] = 'interrupted'
        run['error'] = (f"step '{step_name}' session {sid or '(none recorded)'} {reason}"
                        if sid else f"step '{step_name}' has no recorded session to confirm")
        _write_run(run)
    _log(f"[workflows] run {run_id[:12]} marked interrupted: {run['error']}")
    return True


def reconcile_running_steps(now: Optional[datetime] = None) -> dict:
    """Periodic backstop for the completion hook (scheduler tick, every 30s).

    The live wake path (`agent_routes._maybe_notify_spawner` ->
    `on_agent_step_complete`) is a single best-effort chain; when any link
    drops, the run sits `running` forever and `_has_live_run` refuses every
    future run. Measured 2026-09-14: run-5de9dfa5's codex step completed
    (agent_log row `completed`) and nothing advanced it -- the server process
    predated the fix that carried `_notify_workflow` onto non-claude sessions.

    For each `running` run's running agent step: a live, still-working
    session is left alone; a confirmed end (see `_step_session_verdict`) is
    replayed through `on_agent_step_complete`, which applies the same
    success/error/stopped rules as the live hook and is idempotent against a
    hook that fires late; no live session and no completion record past
    `workflow_stalled_step_seconds` marks the run interrupted, with the reason
    on `run.error` and logged -- never silent, and it frees the one-live-run
    guard. Returns counts; never raises per run."""
    counts = {'advanced': 0, 'stalled': 0}
    if WORKFLOW_RUNS_DIR is None or not WORKFLOW_RUNS_DIR.exists():
        return counts
    now = now or datetime.now(timezone.utc)
    try:
        grace = float(state.CONFIG.get('workflow_stalled_step_seconds',
                                       STALLED_STEP_SECONDS_DEFAULT))
    except (TypeError, ValueError):
        grace = STALLED_STEP_SECONDS_DEFAULT
    for f in WORKFLOW_RUNS_DIR.glob('*.json'):
        run_id = f.stem
        try:
            run = _read_run(run_id)
            if run is None or run.get('status') != 'running':
                continue
            step_name = _running_step(run)
            if step_name is None:
                continue
            st = run['steps'][step_name]
            verdict, status, summary = _step_session_verdict(st, use_live=True)
            if verdict == 'in_flight':
                continue
            if verdict == 'ended':
                _replay_step_end(run, step_name, status, summary,
                                 'completion hook never arrived')
                after = _read_run(run_id) or {}
                if ((after.get('steps') or {}).get(step_name) or {}).get('status') != 'running':
                    counts['advanced'] += 1
                continue
            try:
                age = (now - _parse_iso(st.get('dispatched_at') or '')).total_seconds()
            except Exception:
                age = float('inf')  # no usable timestamp: surface, don't wait forever
            if age < grace:
                continue
            if _interrupt_unconfirmed(
                    run_id, step_name,
                    f"has no live session and no completion record "
                    f"{int(age // 60)} min after dispatch -- its completion never arrived"):
                counts['stalled'] += 1
        except Exception as e:
            _log(f'[workflows] reconcile: run {run_id[:12]} skipped: {e}')
    return counts
