"""Shared, fail-closed steward-role fire/continue adapter (BUILTINS-3 SR-3).

The store interface deliberately contains dispatch metadata only. SR-4 owns
journals, decisions and child handoffs; no mail or backlog-note writes live here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import inspect
import json
import os
import threading
import time
from pathlib import Path
import uuid
from typing import Any, Callable, Iterator, Protocol, cast

from mc import characters, engine_selection, state
from mc import steward_role_model as model, steward_role_task as composer
from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log, now_iso


class RoleBusy(model.RoleError):
    """Active or ambiguous ownership; never queue or steal a lease by age."""


class DispatchStore(Protocol):
    def transaction(self, project_id: str) -> Any: ...
    def read(self, project_id: str) -> dict: ...
    def write(self, project_id: str, record: dict) -> None: ...


class FileDispatchStore:
    """Project-wide cross-process exclusion and crash-safe dispatch checkpoints.

    A crashed mutex is intentionally retained for operator reconciliation. A
    failed delivery retains delivery_unknown even when no session was returned.
    No timeout or PID-liveness guess grants another dispatch.
    """
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, project_id: str) -> Path:
        return self.root / (model.identifier(project_id, 'project_id') + '.json')

    @contextmanager
    def transaction(self, project_id: str) -> Iterator[None]:
        path = self._path(project_id).with_suffix('.claim')
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise RoleBusy('role claim is active or recovery is ambiguous; review required') from exc
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(json.dumps({'pid': os.getpid(), 'at': now_iso()}))
                stream.flush()
                os.fsync(stream.fileno())
            yield
        finally:
            path.unlink()  # only the claim created by this invocation

    def read(self, project_id: str) -> dict:
        path = self._path(project_id)
        try:
            record = json.loads(read_text_with_retry(path))
        except FileNotFoundError:
            return {}
        return _checkpoint(record, project_id)

    def write(self, project_id: str, record: dict) -> None:
        payload = _checkpoint(dict(record, version=1, project_id=project_id), project_id)
        write_json_atomic(self._path(project_id), payload, indent=2)


def _checkpoint(record: Any, project_id: str) -> dict:
    """A partially lost claim must never be interpreted as a first fire."""
    required = {'version', 'project_id', 'schedule_id', 'last_attempt_at', 'cycle_claim'}
    optional = {'session_id', 'predecessor_session_id', 'selection', 'last_refresh_at', 'completed_at'}
    try:
        if (not isinstance(record, dict) or not required <= set(record)
                or set(record) - required - optional or type(record['version']) is not int
                or record['version'] != 1 or record['project_id'] != project_id):
            raise ValueError('checkpoint fields/version/project mismatch')
        model.identifier(record['schedule_id'], 'schedule_id')
        for field in ('schedule_id', 'session_id', 'predecessor_session_id'):
            if record.get(field):
                model.identifier(record[field], field)
        for field in ('last_attempt_at', 'last_refresh_at', 'completed_at'):
            if field in record:
                if datetime.fromisoformat(record[field].replace('Z', '+00:00')).tzinfo is None:
                    raise ValueError(f'{field} has no timezone')
        claim = record['cycle_claim']
        model.identifier(claim['id'], 'claim_id')
        accepted = claim['status'] == 'accepted'
        keys = {'id', 'status', 'session_id', 'accepted_at'} if accepted else {'id', 'status'}
        if set(claim) != keys or claim['status'] not in ('accepted', 'delivery_unknown'):
            raise ValueError('invalid delivery claim')
        if accepted and (not {'session_id', 'selection', 'last_refresh_at'} <= set(record)
                         or claim['session_id'] != record['session_id']
                         or claim['accepted_at'] != record['last_refresh_at']):
            raise ValueError('accepted delivery evidence is incomplete')
        if accepted:
            model.identifier(record['session_id'], 'session_id')
        if 'selection' in record:
            selection = record['selection']
            if (not isinstance(selection, dict)
                    or set(selection) != {'character', 'provider', 'model', 'effort'}
                    or not all(isinstance(v, str) for v in selection.values())):
                raise ValueError('invalid execution selection')
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise model.RoleError(f'invalid role dispatch checkpoint; review required: {exc}') from exc
    return record


@dataclass(frozen=True)
class Selection:
    character: str
    provider: str
    model: str
    effort: str


@dataclass(frozen=True)
class Prepared:
    # Same private duck-typed bridge as agent_routes.prepared_context. Never
    # accepted from an HTTP payload; the existing bridge suppresses rerouting.
    character: dict
    assignment: dict
    system_prompt: str
    steward_role: bool = True


class Services(Protocol):
    def load(self, schedule_id: str) -> tuple[dict, dict, list]: ...
    def resolve(self, project: dict, row: dict) -> tuple[Selection, dict, str]: ...
    def observe(self, project_id: str, session_id: str) -> dict | None: ...
    def active(self, project_id: str) -> bool: ...
    def guard(self, project: dict, selection: Selection, session: dict | None) -> None: ...
    def refresh(self, project: dict, task: str, meta: dict, body: str,
                selection: Selection, since: str) -> str: ...
    def lifecycle(self, project: dict, row: dict, event: str, detail: dict) -> str: ...
    def deliver(self, project: dict, row: dict, task: str, prepared: Prepared,
                previous: dict | None) -> tuple[str, str]: ...


def _closed(observation: dict | None) -> bool:
    # A question or waiting child-work state may coexist with an idle pill.
    return bool(observation and observation.get('status') in ('idle', 'done', 'completed')
                and not any(observation.get(k) for k in (
                    'pending_question', 'pending_plan', 'waiting_on_user',
                    'waiting_on_children', 'waiting_for_question',
                    'waiting_for_plan_approval', '_stdin_write_uncertain')))


def prepare_and_fire(schedule_id: str, services: Services, store: DispatchStore) -> dict:
    """The only role preparation path: timer, Run Now and restart recovery.

    Return acceptance separately from completion. Advancing the watermark
    means acknowledged input delivery, never that the work has completed.
    """
    project, row, _ = services.load(schedule_id)
    pid = model.identifier(project.get('id'), 'project_id')
    with store.transaction(pid):
        project, row, rows = services.load(schedule_id)  # re-read after exclusion
        if project.get('id') != pid:
            raise model.RoleError('role project changed while claiming cycle')
        if not row.get('enabled') or project.get('steward_mode') != 'on':
            raise model.RoleError('steward role is disabled')
        if not row.get('character'):
            raise model.RoleError('role owner is not pinned')
        row = model.validate_schedule(row, project, rows, characters.read_character)
        assigned = model.assignment(project, rows)
        if not assigned or assigned.get('id') != schedule_id:
            raise model.RoleError('project assignment does not reference this role')
        record = store.read(pid)
        if services.active(pid):
            raise RoleBusy('a steward role cycle is still active in this project')
        prior_id = record.get('session_id', '')
        previous = services.observe(pid, prior_id) if prior_id else None
        claim = record.get('cycle_claim')
        if claim:
            if claim.get('status') != 'accepted' or not _closed(previous):
                raise RoleBusy('prior role cycle is active or delivery is unknown; review required')
            record['completed_at'] = now_iso()
        selection, meta, body = services.resolve(project, row)
        unchanged = record.get('selection') == asdict(selection)
        if prior_id and not _closed(previous):
            raise RoleBusy('previous role session has no confirmed completed cycle')
        same_assignment = record.get('schedule_id') == schedule_id
        same_thread = bool(prior_id and unchanged and same_assignment
                           and row.get('continue_session', True))
        if same_thread and previous and (previous.get('trigger_type') != 'schedule'
                or previous.get('trigger_id') != record.get('schedule_id')):
            raise model.RoleError('prior role session provenance does not match its checkpoint')
        if same_thread and previous:
            observed_provider = previous.get('provider', 'claude')
            if observed_provider != selection.provider:
                raise model.RoleError('prior role engine differs from its checkpoint')
            for field in ('pinned_model', 'agent_model', 'model'):
                if field in previous and previous[field] != selection.model:
                    raise model.RoleError('prior role model differs from its checkpoint')
            if ('requested_effort' in previous
                    and (previous['requested_effort'] or '') != selection.effort):
                raise model.RoleError('prior role effort differs from its checkpoint')
        services.guard(project, selection, previous if same_thread else None)
        # Protected core integration is a real dependency, not a stale fallback.
        from steward import core
        if 'cycle_snapshot' not in inspect.signature(core.build_cycle_task).parameters:
            raise model.RoleError('SR-2 core adapter patch is unapplied; role delivery blocked')
        composer.load_directive()  # fail before a claim if the resource is absent
        event = 'continue' if same_thread else ('successor' if prior_id else 'first_fire')
        handoff = services.lifecycle(project, row, event, {
            'predecessor_session_id': prior_id, 'selection': asdict(selection),
            'prior_selection': record.get('selection'),
            'requested_selection': meta.get('_role_requested_selection', asdict(selection)),
            'engine_fallback': meta.get('_role_engine_fallback'),
        })
        if meta.get('_role_engine_fallback'):
            handoff += '\nConfigured engine fallback: ' + json.dumps({
                'requested': meta['_role_requested_selection'], 'effective': asdict(selection),
                'reason': meta['_role_engine_fallback']})
        snapshot = composer.CycleSnapshot(
            meta.get('agent_name') or meta.get('display_name') or meta['name'],
            selection.character, schedule_id, row['steward_role']['charter_item_id'],
            row['steward_role']['journal_path'], character_body=body,
            delivery_context=handoff, provider=selection.provider,
            model=selection.model, effort=selection.effort)
        build: Any = core.build_cycle_task  # signature checked above; patch may be absent
        task = build(project, None, cycle_snapshot=snapshot)
        refresh = services.refresh(project, task, meta, body, selection,
                                   record.get('last_refresh_at', '') if same_assignment else '')
        task = build(project, None, refresh, cycle_snapshot=snapshot)
        from mc import distiller
        from steward import fence
        if not (task.startswith(distiller.STEWARD_TASK_MARKER)
                and distiller.STEWARD_TASK_MARKER == fence.STEWARD_MARKER):
            raise model.RoleError('steward marker/provenance contract mismatch')
        # Check the current binding again before any delivery side effect.
        current_project, current_row, _ = services.load(schedule_id)
        if current_project != project or current_row != row:
            raise model.RoleError('role configuration changed during preparation; retry next wake')
        accepted_at = now_iso()
        record.update(schedule_id=schedule_id, last_attempt_at=accepted_at,
                      cycle_claim={'id': uuid.uuid4().hex, 'status': 'delivery_unknown'})
        store.write(pid, record)  # commit the uncertain boundary BEFORE calling delivery
        prepared = Prepared(meta, asdict(selection), refresh)
        sid, outcome = services.deliver(project, row, task, prepared,
                                        previous if same_thread else None)
        if not sid or outcome not in ('dispatched', 'appended', 'revived'):
            raise model.RoleError('role delivery was not acknowledged; recovery held')
        record.update(session_id=sid, selection=asdict(selection),
                      predecessor_session_id=prior_id if not same_thread else
                      record.get('predecessor_session_id', ''),
                      last_refresh_at=accepted_at,
                      cycle_claim={'id': record['cycle_claim']['id'], 'status': 'accepted',
                                   'session_id': sid, 'accepted_at': accepted_at})
        store.write(pid, record)
        services.lifecycle(project, row, 'accepted', {
            'session_id': sid, 'predecessor_session_id': prior_id,
            'selection': asdict(selection), 'outcome': outcome})
        return {'ok': True, 'session_id': sid, 'continued': outcome,
                'predecessor_session_id': record.get('predecessor_session_id', ''),
                'origin': 'unattended', 'trigger_type': 'schedule', 'trigger_id': schedule_id}


class SchedulerServices:
    """Thin integration over existing character/context/guard/delivery services."""
    def __init__(self, scheduler: Any, lifecycle: Callable[..., str]):
        self.scheduler = scheduler
        self.lifecycle = lifecycle

    def load(self, schedule_id: str) -> tuple[dict, dict, list]:
        from mc import steward_role_writes as writes
        with writes.WRITE_LOCK:
            rows = writes._schedules()
            row = next((r for r in rows if r.get('id') == schedule_id), None)
            if not row or not model.is_role(row):
                raise model.RoleError('role schedule no longer exists')
            project = self.scheduler.load_project(row['project_id'])
            if not project:
                raise model.RoleError('role project no longer exists')
            return project, row, rows

    def resolve(self, project: dict, row: dict) -> tuple[Selection, dict, str]:
        from mc.blueprints import agent_routes as ar
        meta, body = ar._resolve_character(project['project_path'], row['character'],
                                           project=project, strict=True)
        if not meta:
            raise model.RoleError('role character could not be resolved')
        engine = engine_selection.resolve_engine(
            state.CONFIG, project, character=meta.get('engine'),
            model_override=row.get('model') or None, effort_override=row.get('effort') or None,
            legacy_default='claude')
        # Reuse the existing opt-in fallback policy before freezing the effective
        # snapshot. A change goes through the successor policy and guard check;
        # no native conversation id or old character engine pin crosses vendors.
        refusal = ar._allowance_refusal(engine.provider, user_initiated=False, project=project)
        refusal = refusal or ar._model_quota_blocked(engine.provider, engine.model)
        if refusal:
            from mc import engine_fallback
            fallback = engine_fallback.resolve_fallback(
                engine.provider, state.CONFIG, ar._provider_available_for_fallback)
            if not fallback:
                raise engine_fallback.EngineFallbackBlocked(engine_fallback.blocked_payload(refusal))
            meta = dict(meta, _role_requested_selection=asdict(Selection(
                row['character'], engine.provider, engine.model, engine.effort)),
                _role_engine_fallback=refusal)
            engine = engine_selection.resolve_engine(
                state.CONFIG, project, provider_override=fallback['provider'],
                model_override=fallback.get('model') or None,
                effort_override=row.get('effort') or None)
        if engine.effort and engine.effort not in characters.VALID_EFFORT:
            raise model.RoleError(f'unsupported role effort {engine.effort!r}')
        if engine.effort:
            from mc import agent_runtime
            if not getattr(agent_runtime.get_runtime(engine.provider), 'EFFORT_LEVELS', ()):
                raise model.RoleError(f'{engine.provider} does not support the selected role effort')
        return Selection(row['character'], engine.provider, engine.model, engine.effort), meta, body

    def observe(self, project_id: str, session_id: str) -> dict | None:
        live = state.agent_sessions.get(session_id)
        if live and live.get('project_id') == project_id:
            return dict(live, session_id=session_id)
        entries = self.scheduler._load_agent_log(project_id)
        row = next((e for e in entries if e.get('session_id') == session_id), None)
        return dict(row, session_id=session_id) if row else None

    def active(self, project_id: str) -> bool:
        from mc import distiller, steward_role_writes as writes
        steward_ids = {r['id'] for r in writes._schedules()
                       if r.get('project_id') == project_id
                       and (model.is_role(r) or r.get('steward'))}
        return any(s.get('project_id') == project_id and not _closed(s)
                   and (distiller.is_unattended_task(s.get('task', ''))
                        or (s.get('trigger_type') == 'schedule' and s.get('trigger_id') in steward_ids))
                   for s in list(state.agent_sessions.values()))

    def guard(self, project: dict, selection: Selection, session: dict | None) -> None:
        from mc import agent_runtime, guardrail_hooks, fence_fail_closed, vendor_fence
        if not state.CONFIG.get('fence_unattended_enabled', True):
            raise model.RoleError('steward roles require the unattended guard to be enabled')
        provider = selection.provider
        if provider in vendor_fence.UNVERIFIED or provider not in ('claude', 'codex', 'gemini', 'qwen'):
            raise model.RoleError(f'{provider} has no demonstrated steward-role guard')
        runtime = agent_runtime.get_runtime(provider)
        binary = runtime.resolve_binary()
        if not binary:
            raise model.RoleError(f'{provider} CLI is missing; role guard cannot be verified')
        if provider == 'claude':
            settings = guardrail_hooks.launch_file_if_exists('claude')
            if not settings or not fence_fail_closed.fail_closed_settings_path(settings):
                raise model.RoleError('Claude launch settings have no verified steward fence')
            ok, detail = fence_fail_closed.claude_fence_self_test()
        elif provider == 'codex':
            ok, detail = guardrail_hooks.codex_fence_self_test()
        else:
            env = agent_runtime._inject_guardrail_env(provider, os.environ.copy())
            vendor_fence.prepare_env(provider, {'trigger_type': 'schedule'}, env,
                                     binary=Path(binary), cwd=project['project_path'])
            ok, detail = True, ''  # native loader/runner preflight above raises on failure
        if not ok:
            raise model.RoleError(f'{provider} steward guard failed: {detail}')
        # A live Claude process must have loaded the fence-bearing launch file.
        if session and session.get('mode') == 'B' and session.get('process_alive'):
            if session.get('trigger_type') != 'schedule' or session.get('provider', 'claude') != provider:
                raise model.RoleError('live role process provenance/engine differs; recovery held')
            proc = session.get('proc')
            args = getattr(proc, 'args', None)
            if provider != 'claude' or not isinstance(args, list) or '--settings' not in args:
                raise model.RoleError('live role process guard configuration cannot be demonstrated')
            path = Path(args[args.index('--settings') + 1])
            if not fence_fail_closed.fail_closed_settings_path(path):
                raise model.RoleError('live role process settings contain no steward fence')

    def refresh(self, project: dict, task: str, meta: dict, body: str,
                selection: Selection, since: str) -> str:
        from mc.blueprints import agent_routes as ar
        from steward import core
        context = ar._build_agent_context(
            project, task=task, character_body=body,
            character_name=meta.get('agent_name') or '', character_skills=meta.get('skills') or [],
            provider=selection.provider, model=selection.model, consumer_unattended=True)
        skills = core.new_skills_since(project, since) if since else []
        if skills:
            context += '\nCurrent skill inventory changes:\n' + '\n'.join(
                f"{s['name']}: {s['description']}" for s in skills)
        return context

    def deliver(self, project: dict, row: dict, task: str, prepared: Prepared,
                previous: dict | None) -> tuple[str, str]:
        pid = project['id']
        with self.scheduler.get_manager(pid).lock:
            if previous:
                sid = previous['session_id']
                live = state.agent_sessions.get(sid)
                if live and not _closed(live):
                    raise RoleBusy('role session became busy during preparation')
                if live and live.get('mode') == 'B' and live.get('process_alive'):
                    proc = live.get('proc')
                    if proc is None or proc.poll() is not None:
                        raise model.RoleError('role process exited before append; recovery held')
                    # No fire-and-forget acknowledgment and no watchdog killing
                    # an inherited process. The caller owns the unknown checkpoint.
                    live.update(status='running', task=task, trigger_type='schedule',
                                trigger_id=row['id'], last_status_change_time=time.time())
                    _append_ack(live, task)
                    if self.scheduler._record_usage_breakdown_turn_started:
                        self.scheduler._record_usage_breakdown_turn_started(live)
                    return sid, 'appended'
        # Context/worktree preparation and provider startup must not pin the
        # project manager lock. Their existing launch paths acquire it locally.
        if previous:
            sid = previous['session_id']
            revive = (self.scheduler._revive_from_agent_log if prepared.assignment['provider'] == 'claude'
                      else self.scheduler._revive_non_claude_from_agent_log)
            if not revive(pid, sid, task, project, role_context=prepared):
                raise model.RoleError('role revival failed; no fresh fallback, recovery held')
            if prepared.assignment['provider'] != 'claude':
                wait_native_delivery(state.agent_sessions.get(sid))
            return sid, 'revived'
        sid = self.scheduler._dispatch_agent_internal(
            pid, task, trigger_type='schedule', trigger_id=row['id'],
            character=row['character'], strict_character=True,
            provider_override=prepared.assignment['provider'],
            model_override=prepared.assignment['model'],
            effort_override=prepared.assignment['effort'], prepared_context=prepared)
        if prepared.assignment['provider'] != 'claude':
            wait_native_delivery(state.agent_sessions.get(sid))
        return sid, 'dispatched'


def fire(scheduler: Any, schedule_id: str) -> dict:
    """Production entry point. Missing SR-4 integration is a visible blocker."""
    try:
        # SR-4 registers this narrow lifecycle seam after integrating its state,
        # journal and child-delivery policy. No guessed mail/channel fallback.
        lifecycle = getattr(scheduler, 'steward_role_lifecycle', None)
        if not callable(lifecycle):
            raise model.RoleError('SR-4 role lifecycle adapter unavailable; delivery blocked')
        store = FileDispatchStore(scheduler.SCHEDULES_PATH.parent / 'steward' / 'dispatch')
        return prepare_and_fire(schedule_id, SchedulerServices(
            scheduler, cast(Callable[..., str], lifecycle)), store)
    except Exception as exc:
        _log(f'[steward-role] {schedule_id}: {exc}')
        raise


def _append_ack(session: dict, task: str, timeout: float = 10) -> None:
    """Bound acknowledgment without terminating a process owned by another fire."""
    done = threading.Event()
    errors: list[Exception] = []

    def write() -> None:
        try:
            with session['stdin_lock']:
                session['proc'].stdin.write(json.dumps({'type': 'user', 'message': {
                    'role': 'user', 'content': task}}) + '\n')
                session['proc'].stdin.flush()
        except Exception as exc:
            errors.append(exc)
            _log(f'[steward-role] stdin delivery failed: {exc}')
        finally:
            done.set()

    threading.Thread(target=write, daemon=True).start()
    if not done.wait(timeout) or errors:
        session['_stdin_write_uncertain'] = True
        raise model.RoleError('role stdin delivery uncertain; recovery held')


def marker_first(task: str) -> str:
    """Keep auto-fresh disclosure after the marker on role-only revival."""
    marker = composer.STEWARD_TASK_MARKER
    if marker not in task:
        raise model.RoleError('role revival lost the steward marker')
    return marker + '\n\n' + task.replace(marker, '', 1).lstrip()


def begin_native_delivery(session: dict) -> None:
    session['_steward_role_ack'] = {'done': threading.Event(), 'ok': False}


def write_native_delivery(stream: Any, prompt: str, session: Any, context: str) -> None:
    """Final native context seam: marker first and real pipe acknowledgment."""
    from mc.resume_context import context_sha, resume_prompt
    ack = session['_steward_role_ack']
    try:
        if stream is None:
            raise BrokenPipeError('role prompt stdin unavailable')
        wire = prompt if prompt.startswith(composer.STEWARD_TASK_MARKER) else (
            composer.STEWARD_TASK_MARKER + '\n\n' + prompt)
        stream.write(wire)
        stream.flush()
        stream.close()
        if context.strip() and (prompt.startswith(context)
                                or prompt.startswith(resume_prompt(context, ''))):
            session['_ctx_delivered_sha'] = context_sha(context)
        ack['ok'] = True
    except Exception as exc:
        session['_stdin_write_uncertain'] = True
        _log(f'[steward-role] native prompt delivery failed: {exc}')
    finally:
        ack['done'].set()


def wait_native_delivery(session: dict | None, timeout: float = 10) -> None:
    ack = (session or {}).get('_steward_role_ack')
    if not ack or not ack['done'].wait(timeout) or not ack['ok']:
        if session is not None:
            session['_stdin_write_uncertain'] = True
        raise model.RoleError('native role delivery was not acknowledged; recovery held')


def scheduler_fire(scheduler: Any, schedule_id: str, *, timer: bool = False) -> dict:
    """Persist role-only run diagnostics without overwriting human bindings."""
    from mc import steward_role_writes as writes, schedule_skip_log
    try:
        result = fire(scheduler, schedule_id)
    except Exception as exc:
        result = {'ok': False, 'busy': isinstance(exc, RoleBusy), 'error': str(exc)}
    with writes.WRITE_LOCK:
        rows = writes._schedules()
        row = next((r for r in rows if r.get('id') == schedule_id), None)
        if row:
            if result['ok']:
                row['last_run'] = now_iso()
                row.pop('last_error', None)
                row.pop('last_error_at', None)
            else:
                row.update(last_error=result['error'], last_error_at=now_iso())
                schedule_skip_log.record(scheduler.SCHEDULES_PATH, schedule_id,
                                         result['error'], 'steward_role')
            if timer:
                if row.get('schedule_type') == 'once':
                    row.update(enabled=False, next_run=None)
                elif row.get('schedule_type') == 'interval':
                    row['next_run'] = (datetime.now(timezone.utc) + timedelta(
                        minutes=row.get('interval_minutes', 60))).isoformat().replace('+00:00', 'Z')
                else:
                    row['next_run'] = scheduler._compute_next_run(row)
            scheduler._save_schedules(rows)
    return result


def execution_error(scheduler: Any) -> str:
    """Read-only dependency status; guard readiness is demonstrated at fire time."""
    from steward import core
    if 'cycle_snapshot' not in inspect.signature(core.build_cycle_task).parameters:
        return 'SR-2 core adapter patch is unapplied; role delivery blocked'
    try:
        composer.load_directive()
    except model.RoleError as exc:
        return str(exc)
    if not callable(getattr(scheduler, 'steward_role_lifecycle', None)):
        return 'SR-4 role lifecycle adapter unavailable; delivery blocked'
    return ''


def timer_tick(scheduler: Any, row: dict, now: datetime, paused: bool) -> None:
    """Role timer bookkeeping reloads bindings instead of saving a stale row."""
    if not row.get('enabled'):
        return
    next_run = row.get('next_run')
    if not next_run:
        _save_next_wake(scheduler, row, scheduler._compute_next_run(row))
        return
    try:
        due = datetime.fromisoformat(next_run.replace('Z', '+00:00'))
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError) as exc:
        _log(f"[steward-role] {row.get('id')}: invalid next_run: {exc}")
        return
    if now < due:
        return
    if paused:
        if row.get('schedule_type') != 'once':
            wake = ((now + timedelta(minutes=row.get('interval_minutes', 60))).isoformat()
                    if row.get('schedule_type') == 'interval' else scheduler._compute_next_run(row))
            _save_next_wake(scheduler, row, wake)
        return
    check_row = dict(row)
    if scheduler._skipped_by_condition(check_row, now):
        _save_next_wake(scheduler, row, check_row.get('next_run'))
        return
    if not scheduler._claim_slot(row.get('id', ''), next_run):
        check_row['last_run'] = now_iso()
        _save_next_wake(scheduler, row, scheduler._compute_next_run(check_row))
        return
    # A new store instance on every timer/Run Now is intentional: a restarted
    # server sees the same write-ahead claim, not an empty in-memory lease.
    scheduler_fire(scheduler, row['id'], timer=True)


def _save_next_wake(scheduler: Any, expected: dict, wake: str | None) -> None:
    from mc import steward_role_writes as writes
    with writes.WRITE_LOCK:
        rows = writes._schedules()
        row = next((r for r in rows if r.get('id') == expected.get('id')), None)
        if row is not None and row == expected:  # a concurrent human edit takes precedence
            row['next_run'] = wake
            scheduler._save_schedules(rows)
