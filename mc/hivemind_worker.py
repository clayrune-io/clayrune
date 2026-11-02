"""Hivemind worker context and launch adapters (BUILTINS-4 HR-2).

The blueprint remains the data/runtime owner; this leaf owns worker policy.
Legacy workers preserve their original prompt, engine and isolation semantics.
Roster attempts pin a character snapshot and use ordinary unattended context.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
import tempfile
import time as _time
import threading
from pathlib import Path
from typing import Any
import uuid

from mc import agent_worktree as _agent_worktree
from mc import characters, engine_selection, hivemind_roster as roster
from mc import hivemind_lineage as _hm_lineage
from mc import project_sync as _sync, state
from mc.core import TimestampedLines, now_iso, _log


class RosterRecoveryHeld(RuntimeError):
    """A roster attempt cannot safely resume; callers must never fall back."""


def pin_prompt(api: Any, hm_id: str, session_id: str, context: str) -> dict[str, str]:
    """Persist one prompt per attempt before recording ownership or launching."""
    relative = f'workstreams/worker-{session_id}.prompt.txt'
    target = api._hm_dir(hm_id) / relative
    payload = context.encode('utf-8')
    fd, temporary = tempfile.mkstemp(dir=target.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'pinned_prompt_path': relative,
            'pinned_prompt_sha256': hashlib.sha256(payload).hexdigest()}


def read_pinned_prompt(session: dict) -> str:
    """Read only a confined, digest-verified prompt; inline log copies are ignored."""
    from mc.blueprints import hivemind_routes as api

    relative = session.get('pinned_prompt_path')
    digest = session.get('pinned_prompt_sha256')
    hm_id = session.get('hivemind_id')
    if not (isinstance(relative, str) and relative and isinstance(digest, str)
            and len(digest) == 64 and isinstance(hm_id, str) and hm_id):
        raise RosterRecoveryHeld('Roster attempt is missing its pinned context reference; recovery held')
    try:
        root = Path(api._hm_dir(hm_id)).resolve()
        path = (root / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(root):
            raise RosterRecoveryHeld('Roster pinned context path escapes its run directory; recovery held')
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise RosterRecoveryHeld('Roster pinned context sha256 mismatch; recovery held')
        context = payload.decode('utf-8')
    except (OSError, UnicodeError, ValueError) as exc:
        raise RosterRecoveryHeld(f'Roster pinned context file unavailable; recovery held: {exc}') from exc
    if not context:
        raise RosterRecoveryHeld('Roster pinned context file is empty; recovery held')
    return context


@dataclass(frozen=True)
class PreparedContext:
    """Server-built context and identity, consumed once by the Claude bridge.

    No public route accepts this object. The bridge must not look up a live
    character, route another model, or append another ordinary context.
    """
    character: dict[str, Any]
    assignment: dict[str, Any]
    system_prompt: str


def hold_workstream(api: Any, hm_id: str, ws: dict, reason: str, detail: str) -> None:
    ws.update(status='blocked', failure_reason=reason)
    api._hm_save_workstream(hm_id, ws['id'], ws)
    raise RuntimeError(detail)


def record_launch_failure(session: dict, error: Exception) -> None:
    """Retain diagnostics without labelling an uncertain launch completed."""
    if not session.get('character_ref'):
        return
    session['launch_error'] = str(error)
    if session.get('status') == 'running' and not session.get('proc'):
        session['status'] = 'blocked'


_launch_locks: dict[tuple[str, str], Any] = {}
_launch_locks_guard = threading.Lock()


def is_code_project(path: str) -> bool:
    # A failed git subprocess must not turn an existing code checkout into
    # "non-git analysis" and permit the shared-cwd fallback.
    root = Path(path)
    return any((parent / '.git').exists() for parent in (root, *root.parents)) or _sync.is_git_repo(path)


def pinned_context(session: dict | None) -> str | None:
    """Recover an active roster attempt without re-reading a changed roster."""
    if not (session or {}).get('character_ref'):
        return None
    context = (session or {}).get('_system_prompt')
    if isinstance(context, str) and context:
        return context
    return read_pinned_prompt(session or {})


def attempt_metadata(session: dict) -> dict[str, Any]:
    """Durable roster-only fields for pending and completion log rows."""
    if not session.get('character_ref'):
        return {}
    return {key: session[key] for key in (
        'character', 'character_ref', 'assignment_attempt', 'resolved_assignment',
        'model', 'effort', 'requested_effort', 'pinned_prompt_path', 'pinned_prompt_sha256',
        '_agent_cwd', '_worktree_isolated', 'source', 'job_shape')}


def runtime_callbacks(provider: str) -> dict:
    # Claude's readers already write completion. Runtime-owned readers need
    # the ordinary shared callback, otherwise their pending row never closes.
    if provider == 'claude':
        return {}
    from mc.blueprints import agent_routes as ar
    return dict(ar._RUNTIME_CALLBACKS)


def revive_roster_worker(entry: dict, project: dict, message: str) -> dict:
    """Surface any roster recovery failure without allowing persona fallback."""
    try:
        return _revive_roster_worker(entry, project, message)
    except RosterRecoveryHeld:
        raise
    except Exception as exc:
        raise RosterRecoveryHeld(f'Roster recovery failed; no fallback attempted: {exc}') from exc


def _revive_roster_worker(entry: dict, project: dict, message: str) -> dict:
    """Recover a reconciled attempt through its original runtime/cwd contract.

    An in-progress/unknown process remains held after restart. A terminal log
    record can resume its pinned attempt, provided no newer workstream attempt
    has replaced it. Missing pins never degrade to ordinary persona replay.
    """
    from mc.blueprints import hivemind_routes as api

    sid = entry['session_id']
    hm_id, ws_id = entry['hivemind_id'], entry['hivemind_ws_id']
    with _launch_locks_guard:
        lock = _launch_locks.setdefault((str(api._hm_dir(hm_id)), ws_id), threading.Lock())
    with lock:
        if entry.get('status') not in ('completed', 'error', 'stopped'):
            raise RosterRecoveryHeld('Roster worker process requires reconciliation before recovery')
        live = state.agent_sessions.get(sid)
        if live and (live.get('status') == 'running' or live.get('process_alive')):
            raise RosterRecoveryHeld('Roster worker already has a live process')
        manifest = api._hm_load_manifest(hm_id)
        ws = api._hm_load_workstream(hm_id, ws_id)
        if not manifest or not ws:
            raise RosterRecoveryHeld('Roster recovery is missing its manifest/workstream')
        if (manifest.get('project_id') != project['id']
                or manifest.get('project_generation') != entry.get('project_generation')
                or ws.get('assignment_attempt') != entry.get('assignment_attempt')
                or ws.get('resolved_assignment') != entry.get('resolved_assignment')
                or ws.get('current_agent_session_id') not in (None, '', sid)):
            raise RosterRecoveryHeld('Roster attempt was replaced or its generation changed; recovery held')
        context = read_pinned_prompt(entry)
        character = deepcopy(entry['character'])
        assignment = dict(entry['resolved_assignment'])
        if (assignment.get('session_id') != sid
                or assignment.get('requested_character', assignment.get('character_ref')) != entry['character_ref']):
            raise RosterRecoveryHeld('Roster recovery has inconsistent identity pins')
        cwd = entry['_agent_cwd']
        if not Path(cwd).is_dir():
            raise RosterRecoveryHeld('Roster worker cwd is missing; recovery held')
        if entry['_worktree_isolated']:
            ok, branch = _sync.git_run(cwd, ['symbolic-ref', '--short', 'HEAD'], timeout=15)
            if not ok or branch.strip() != ws.get('worktree_branch'):
                raise RosterRecoveryHeld('Roster worker branch changed; recovery held')
        elif is_code_project(project['project_path']):
            raise RosterRecoveryHeld('Roster code recovery requires its isolated worktree')
        metadata = {
            **attempt_metadata(entry), 'hivemind_id': hm_id, 'hivemind_ws_id': ws_id,
            'trigger_type': 'hivemind_worker', 'trigger_id': ws_id, 'housekeeping': True,
            'project_generation': entry['project_generation'],
            **_hm_lineage.worker_metadata(manifest),
        }
        session = {
            **metadata, '_system_prompt': context, 'session_id': sid, 'project_id': project['id'],
            'task': f'You run unattended as the same Hivemind worker.\n{message}',
            'provider': assignment['provider'], 'model': assignment['model'],
            'effort': assignment['effort'], 'mode': 'A', 'status': 'running',
            'process_alive': False, 'started_at': now_iso(), 'log_lines': TimestampedLines(),
            'last_output_time': _time.time(), '_dispatch_time': _time.time(),
        }
        max_turns = (manifest.get('config', {}).get('worker_max_turns', 0)
                     or state.CONFIG.get('agent_max_turns', 0))
        api._hm_runtime_dispatch(
            provider_name=assignment['provider'], project_id=project['id'], project_path=cwd,
            task=session['task'], system_prompt=context, model=assignment['model'],
            effort=assignment['effort'], session_id=sid, session_dict=session,
            project_generation=entry['project_generation'], metadata=metadata,
            prepared_context=PreparedContext(character, assignment, context),
            callbacks=runtime_callbacks(assignment['provider']),
            resume_id=entry.get('claude_session_id') or entry.get('provider_session_id') or '',
            max_turns=int(max_turns) if max_turns and int(max_turns) > 0 else None)
        return state.agent_sessions[sid]


def spawn_worker(api: Any, manifest: dict, ws: dict, project: dict,
                 hivemind_id: str, ws_id: str) -> str:
    if not roster.is_roster_workstream(ws):
        return spawn_legacy_worker(api, manifest, ws, project, hivemind_id, ws_id)
    with _launch_locks_guard:
        lock = _launch_locks.setdefault((str(api._hm_dir(hivemind_id)), ws_id), threading.Lock())
    with lock:
        latest = api._hm_load_workstream(hivemind_id, ws_id)
        if latest is None:
            raise ValueError('Hivemind workstream is missing; refusing launch')
        ws.update(latest)
        return spawn_roster_worker(api, manifest, ws, project, hivemind_id, ws_id)


def spawn_roster_worker(api: Any, manifest: dict, ws: dict, project: dict,
                        hivemind_id: str, ws_id: str) -> str:
    """Every fresh/manual/retry/restart attempt enters this same contract.

    Reconcile existing live ownership before making a new attempt. Pin the
    current roster record, context and launch intent before dispatch; uncertain
    launches remain owned and cannot be replaced by a blind second launch.
    Git projects require isolation; non-git analysis retains its shared cwd.
    """
    from mc.blueprints import agent_routes as ar

    owner = ws.get('current_agent_session_id')
    if owner:
        live = state.agent_sessions.get(owner)
        if live and (live.get('status') == 'running' or live.get('process_alive')):
            raise roster.RosterError('workstream already has a live worker', 409)
        if not live or live.get('status') not in ('completed', 'error', 'stopped'):
            raise roster.RosterError('workstream ownership requires reconciliation', 409)

    session_id = f'hm_{uuid.uuid4().hex[:8]}'
    # One read supplies both engine and body: a roster edit between two reads
    # must never give a worker one character's engine and another persona.
    canon = roster.parse_character_ref(ws.get('character'))
    snapshot: dict[str, Any] = {}
    cfg = manifest.get('config') or {}
    previous = ws.get('resolved_assignment') or {}
    assignment = roster.resolve_assignment(
        canon, roster.assignment_config(state.CONFIG, cfg), project, session_id=session_id,
        read_character=lambda name: characters.read_character('global', name, include_body=True),
        character_snapshot=snapshot, **roster.planner_fields(ws),
        worker_provider=cfg.get('worker_provider') or '',
        previously_defined=bool(ws.get('character_known') or
                                (previous.get('character_ref') == canon and
                                 previous.get('substituted') is not True)))
    snapshot = deepcopy(snapshot)
    # Resolve runtime/generation before context, disk ownership or worktrees.
    generation = manifest.get('project_generation')
    if type(generation) is not int or generation < 1:
        raise ValueError('Hivemind manifest has no valid project_generation')
    if api._assert_runtime_project_generation is None:
        raise RuntimeError('Hivemind project-generation validator is not wired')
    api._assert_runtime_project_generation(project['id'], generation)
    if api._agent_runtime.get_runtime(assignment['provider']) is None:
        raise ValueError(f"Unknown provider {assignment['provider']!r}; no fallback attempted")

    task = (
        'You run unattended as a Hivemind workstream worker.\n'
        f"YOUR WORKSTREAM: {ws.get('title', ws_id)}\n"
        f"Brief: {ws.get('description', '')}\n\n"
        'Follow the two-phase protocol in your system prompt: submit a '
        'handoff before marking the workstream complete.')
    # The server-owned prefix reaches ALL task-only read-floor calls. Do not
    # rely solely on trigger_type, which those existing context APIs cannot see.
    normal = ar._build_agent_context(
        dict(project, provider=assignment['provider']), task=task,
        character_body=snapshot.get('body', ''),
        character_name=snapshot.get('agent_name') or snapshot.get('display_name') or snapshot['name'],
        character_skills=snapshot.get('skills') or [], session_id=session_id,
        source='agent', job_shape='task', provider=assignment['provider'],
        model=assignment['model'])
    directive = build_worker_context(api, hivemind_id, ws_id, universal=False)
    if not directive:
        raise ValueError('Hivemind worker context is missing; refusing launch')
    context = f'{normal}\n\n{directive}'

    run_cwd, isolated = ar._maybe_isolate_worktree(
        project, session_id, always=True,
        base_ref=api._hm_worktree_base(hivemind_id) or 'HEAD')
    if not isolated and is_code_project(project['project_path']):
        hold_workstream(api, hivemind_id, ws, 'isolation_required',
                        'Roster code workstream requires an isolated worktree; launch held')
    if isolated:
        ok, base = _sync.git_run(run_cwd, ['rev-parse', 'HEAD'], timeout=15)
        if not ok or not base.strip():
            hold_workstream(api, hivemind_id, ws, 'base_unavailable',
                            'Could not pin worker base commit; launch held')
        api._hm_pin_worktree_base(hivemind_id, base)
        ws.update(worktree_session_id=session_id,
                  worktree_branch=_agent_worktree.branch_name(session_id), base_commit=base)
        task += (
            f"\n\nWork on isolated branch {ws['worktree_branch']}. Commit your "
            'changes before finishing; do not merge, push, or switch branches. '
            'Hivemind owns integration.')

    roster.stamp_attempt(ws, assignment)
    prompt_reference = pin_prompt(api, hivemind_id, session_id, context)
    ws['character_snapshot'] = deepcopy(snapshot)
    ws.pop('worker_context', None)
    ws.update(prompt_reference)
    ws['current_agent_session_id'] = session_id
    ws['status'] = 'active'
    api._hm_save_workstream(hivemind_id, ws_id, ws)
    record_substitution(api, hivemind_id, assignment, role='worker', ws_id=ws_id)
    metadata = {
        'hivemind_id': hivemind_id, 'hivemind_ws_id': ws_id,
        'trigger_type': 'hivemind_worker', 'trigger_id': ws_id,
        'housekeeping': True, 'source': 'agent', 'job_shape': 'task',
        'project_generation': generation,
        'character': deepcopy(snapshot), 'character_ref': canon,
        'assignment_attempt': ws['assignment_attempt'],
        'resolved_assignment': dict(assignment),
        **prompt_reference,
        **_hm_lineage.worker_metadata(manifest),
    }
    session = {
        **metadata, 'status': 'running', 'task': task,
        'log_lines': TimestampedLines(), 'started_at': now_iso(),
        'session_id': session_id, 'project_id': project['id'], 'mode': 'A',
        'provider': assignment['provider'], 'model': assignment['model'],
        'effort': assignment['effort'], 'requested_effort': assignment['effort'],
        'pinned_model': assignment['model'], 'model_auto_requested': False,
        'process_alive': False, 'last_output_time': _time.time(),
        'last_status_change_time': _time.time(), 'guardian_state': None,
        'recovery_attempts': 0, 'last_recovery_time': 0,
        'pending_recovery_message': None, 'circuit_breaker_tripped': False,
        '_dispatch_time': _time.time(), '_agent_cwd': run_cwd,
        '_worktree_isolated': isolated, '_system_prompt': context,
    }
    max_turns = (manifest.get('config', {}).get('worker_max_turns', 0)
                 or state.CONFIG.get('agent_max_turns', 0))
    return api._hm_runtime_dispatch(
        provider_name=assignment['provider'], project_id=project['id'],
        project_path=run_cwd, task=task, system_prompt=context,
        model=assignment['model'], effort=assignment['effort'],
        session_id=session_id, session_dict=session, project_generation=generation,
        metadata=metadata, prepared_context=PreparedContext(snapshot, assignment, context),
        callbacks=runtime_callbacks(assignment['provider']),
        max_turns=int(max_turns) if max_turns and int(max_turns) > 0 else None)


def record_substitution(api: Any, hm_id: str, assignment: dict, *, role: str,
                        ws_id: str = '') -> None:
    """Persist disclosure before launch; never dispatch with an unlogged swap."""
    if not assignment.get('substituted'):
        return
    ran_as = assignment.get('character_ref') or 'generic worker'
    content = f"requested {assignment['requested_character']} -> ran as {ran_as}"
    event = {
        'id': 'sub_' + uuid.uuid4().hex[:12], 'timestamp': now_iso(),
        'type': 'character_substitution', 'from': ws_id or role, 'to': 'all',
        'content': content, 'role': role, 'ws_id': ws_id,
        'resolved_assignment': dict(assignment),
    }
    api._hm_append_bus_message(hm_id, event)
    _log(f'[hivemind] {hm_id}: {content}', flush=True)
    api._hm_push_sse(hm_id, {**event, 'hivemind_id': hm_id})


def build_worker_context(api: Any, hivemind_id: str, ws_id: str, *, universal: bool = True) -> str:
    """Build the system prompt context for a hivemind worker agent."""
    manifest = api._hm_load_manifest(hivemind_id)
    ws = api._hm_load_workstream(hivemind_id, ws_id)
    if not manifest or not ws:
        return ''

    port = api.PORT
    parts = []

    parts.append(
        f"You are a specialist agent in a Hivemind analysis.\n"
        f"Hivemind: {manifest.get('title', '')}\n"
        f"Overall Goal: {manifest.get('goal', '')}"
    )

    parts.append(
        f"YOUR WORKSTREAM: {ws.get('title', ws_id)}\n"
        f"YOUR BRIEF: {ws.get('description', '')}"
    )

    # Handoff from previous worker (highest priority context)
    handoff = api._hm_read_handoff(hivemind_id, ws_id)
    if handoff:
        parts.append(f"HANDOFF FROM PREVIOUS WORKER:\n{handoff[:4000]}")

    # Accumulated context
    ctx = api._hm_read_context(hivemind_id, ws_id)
    if ctx:
        parts.append(f"ACCUMULATED CONTEXT:\n{ctx[:4000]}")

    # Recent findings from this workstream
    findings = api._hm_read_findings(hivemind_id, ws_id, last_n=20)
    if findings:
        findings_text = '\n'.join(
            f"- [{f.get('timestamp', '')[:16]}] {f.get('title', '')}: {f.get('content', '')[:200]}"
            for f in findings[-20:]
        )
        parts.append(f"RECENT FINDINGS FROM THIS WORKSTREAM:\n{findings_text}")

    # Relevant bus messages from other workstreams
    bus_msgs = api._hm_read_bus_messages(hivemind_id, last_n=50, ws_filter=ws_id)
    if bus_msgs:
        bus_text = '\n'.join(
            f"- [{m.get('timestamp', '')[:16]}] {m.get('from', '')} -> {m.get('to', '')}: "
            f"{m.get('content', '')[:200]}"
            for m in bus_msgs[-15:]
        )
        parts.append(f"RELEVANT MESSAGES FROM BUS:\n{bus_text}")

    # Decisions that affect this workstream
    decisions = api._hm_read_decisions(hivemind_id, last_n=20)
    relevant = [d for d in decisions if ws_id in d.get('impacts', []) or d.get('workstream') == ws_id]
    if relevant:
        dec_text = '\n'.join(
            f"- {d.get('decision', '')}: {d.get('rationale', '')[:200]}"
            for d in relevant[-10:]
        )
        parts.append(f"DECISIONS THAT AFFECT YOUR WORK:\n{dec_text}")

    # Worker capabilities (API endpoints)
    parts.append(
        f"YOUR CAPABILITIES (use curl to call these):\n"
        f'- Report a finding: curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/bus/post '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"from":"{ws_id}","type":"finding_report","title":"...","content":"...","confidence":"high|medium|low"}}'\n"""
        f'- Ask a question: curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/bus/post '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"from":"{ws_id}","type":"question","to":"ws_xxx","content":"..."}}'\n"""
        f'- Report a blocker: curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/escalate '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"from":"{ws_id}","content":"..."}}'\n"""
        f'- Submit handoff (REQUIRED before marking complete): curl -s -X POST '
        f'http://localhost:{port}/api/hivemind/{hivemind_id}/workstreams/{ws_id}/handoff '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"what_was_done":"...","key_findings_summary":"...","next_worker_should":"..."}}'\n"""
        f'- Mark complete: curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/workstreams/{ws_id}/status '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"status":"completed"}}'"""
    )

    parts.append(
        "RULES:\n"
        "1. Build on accumulated context — do NOT repeat analysis already completed\n"
        "2. Report findings as you discover them (do not batch at the end)\n"
        "3. Reference evidence and data for all findings\n"
        "4. If you need information from another workstream, ask via the bus\n"
        "5. If you encounter a decision point that affects other workstreams, escalate\n"
        "6. Do NOT write to the project MEMORY.md — your findings go to the bus only\n"
        "7. TWO-PHASE PROTOCOL:\n"
        "   PHASE 1 — Do your analysis. Post findings to the bus as you discover them.\n"
        "   PHASE 2 — When done, submit a handoff document via the handoff endpoint, "
        "then mark your workstream complete. Do NOT skip Phase 2."
    )

    # Universal Clayrune awareness — same source of truth as regular agents.
    # See api._clayrune_universal_capabilities().
    if universal:
        parts.extend(api._clayrune_universal_capabilities(port=port))

    # Pointer card, not the full 19.9 KB reference (same card regular agents
    # get — see _CLAYRUNE_API_POINTER_CARD in agent_routes.py for the
    # measured numbers behind dropping the full-file injection).
    if universal:
        parts.append(api._clayrune_api_pointer_card(port, manifest.get('project_id', '')))

    return "\n\n".join(parts)


def spawn_legacy_worker(api: Any, manifest: dict, ws: dict, p: dict, hivemind_id: str, ws_id: str) -> str:
    """Spawn a Hivemind worker through AgentRuntime for every provider."""
    project_id = p.get('id', '')
    worker_context = api._hm_build_worker_context(hivemind_id, ws_id)
    from mc import engine_selection
    _cfg = manifest.get('config', {}) or {}
    _manifest_worker_provider = (_cfg.get('worker_provider') or '').strip().lower()
    _ws_provider = (ws.get('provider') or '').strip().lower()
    # The manifest's `worker_model` default (e.g. 'sonnet') was computed ONCE
    # at hivemind-create time FOR THE MANIFEST-LEVEL worker_provider (which
    # may itself be an inherited default, e.g. the project's own provider --
    # see hivemind_create). A workstream that names its OWN, DIFFERENT
    # provider must not inherit that string: it belongs to another vendor's
    # model catalog entirely (2026-09-18, W5 live test -- found this blocking
    # EVERY mixed-vendor workstream that didn't also set its own `model`,
    # with a confusing "Model 'sonnet' belongs to provider 'aider'" refusal
    # from `engine_selection.model_provider_mismatch`'s cross-catalog search).
    # Only inherit the manifest's model default when this workstream is
    # actually using the manifest's own default provider (or has none of its
    # own) -- otherwise fall through to `model_override=None`, which resolves
    # the DESTINATION provider's own native default via the safe inherit path
    # in `engine_selection.resolve_model`.
    _model_fallback = (
        _cfg.get('worker_model') or ''
        if not _ws_provider or _ws_provider == _manifest_worker_provider
        else '')
    engine = engine_selection.resolve_engine(
        state.CONFIG, p, legacy_default='claude',
        provider_override=(ws.get('provider') or _manifest_worker_provider or ''),
        model_override=(ws.get('model') or _model_fallback or None))
    model = engine.model
    effort = (ws.get('effort') or
              manifest.get('config', {}).get('worker_effort') or '')
    task = (
        f"You are a Hivemind worker for workstream: {ws.get('title', ws_id)}.\n"
        f"Brief: {ws.get('description', '')}\n\n"
        f"Begin your analysis. Follow the two-phase protocol described in your system prompt."
    )
    session_id = f'hm_{uuid.uuid4().hex[:8]}'
    # Worktree isolation (e3c0824e): the SAME decision dispatched agents make
    # (`_maybe_isolate_worktree` — git repo, isolation setting, per-project
    # opt-out), minus the "first agent" gate: workers are parallel by
    # construction and housekeeping sessions aren't counted by it. Any failure
    # falls back to the shared tree, exactly as for a dispatched agent.
    from mc.blueprints import agent_routes as _agent_routes
    run_cwd, isolated = _agent_routes._maybe_isolate_worktree(
        p, session_id, always=True, base_ref=api._hm_worktree_base(hivemind_id) or 'HEAD')
    if isolated:
        _ok, _base = _sync.git_run(run_cwd, ['rev-parse', 'HEAD'], timeout=15)
        if _ok:
            api._hm_pin_worktree_base(hivemind_id, _base)
        ws['worktree_session_id'] = session_id
        ws['worktree_branch'] = _agent_worktree.branch_name(session_id)
        ws['base_commit'] = _base if _ok else ''
        task += (
            f"\n\nYou are working in an isolated git worktree on branch "
            f"{ws['worktree_branch']}. Commit your changes there before you "
            f"finish; do not merge, push, or switch branches. The hivemind "
            f"integrates finished workstreams onto its own integration branch.")
    provider_name = engine.provider
    max_turns = (manifest.get('config', {}).get('worker_max_turns', 0) or
                 state.CONFIG.get('agent_max_turns', 0))

    pre_session = {
        'status': 'running',
        'task': task,
        'log_lines': TimestampedLines(),
        'started_at': now_iso(),
        'session_id': session_id,
        'project_id': project_id,
        'mode': 'A',
        'housekeeping': True,
        'hivemind_id': hivemind_id,
        'hivemind_ws_id': ws_id,
        'trigger_type': 'hivemind_worker',
        'trigger_id': ws_id,
        'provider': provider_name,
        'model': model,
        'effort': effort,
        'requested_effort': effort,
        'process_alive': True,
        'last_output_time': _time.time(),
        'last_status_change_time': _time.time(),
        'guardian_state': None,
        'recovery_attempts': 0,
        'last_recovery_time': 0,
        'pending_recovery_message': None,
        'circuit_breaker_tripped': False,
        '_dispatch_time': _time.time(),
        # Same keys `_dispatch_via_runtime` stamps on a dispatched agent, so
        # respawns, LOC attribution and Floor display read a worker like any
        # other isolated agent.
        '_agent_cwd': run_cwd,
        '_worktree_isolated': isolated,
        **_hm_lineage.worker_metadata(manifest),
    }
    metadata = {
        'hivemind_id': hivemind_id,
        'hivemind_ws_id': ws_id,
        'trigger_type': 'hivemind_worker',
        'trigger_id': ws_id,
        'housekeeping': True,
        **_hm_lineage.worker_metadata(manifest),
    }
    return api._hm_runtime_dispatch(
        provider_name=provider_name, project_id=project_id, project_path=run_cwd,
        task=task, system_prompt=worker_context, model=model, effort=effort,
        session_id=session_id, session_dict=pre_session,
        project_generation=manifest.get('project_generation'),
        metadata=metadata,
        max_turns=(int(max_turns) if max_turns and int(max_turns) > 0 else None))
