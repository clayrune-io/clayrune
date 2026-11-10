"""Exact native-conversation proof for mail, never a character-name alias."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mc import state


class MailHeld(OSError):
    """Closed metadata reason; no mail text or provider exception in status."""
    _mc_prechild_refusal = True


def native(source: dict[str, Any]) -> str:
    return str((source.get('claude_session_id') if (source.get('provider') or 'claude') == 'claude'
                else source.get('provider_session_id')) or '')


def permissions(project: dict[str, Any]) -> dict[str, Any]:
    keys = ('agent_permission_mode', 'fence_unattended_enabled', 'codex_unattended_sandbox',
            'agent_allowed_tools', 'agent_disallowed_tools')
    # Record policy values, never system prompts, credentials or the whole config.
    return {key: [state.CONFIG.get(key), project.get(key)] for key in keys}


def snapshot(project: dict[str, Any], session: dict[str, Any],
             question: dict[str, Any] | None = None) -> dict[str, Any]:
    from mc.blueprints import agent_routes as ar
    provider = session.get('provider') or 'claude'
    if provider not in ('claude', 'codex', 'qwen', 'gemini'):
        raise MailHeld('unsupported_provider')
    identity = native(session)
    if not identity or identity.lower() in ('last', 'latest'):
        raise MailHeld('native_identity_missing')
    if session.get('incognito'):
        raise MailHeld('incognito_not_persistent')
    if session.get('character_ref'):
        raise MailHeld('roster_recovery_held')
    generation = session.get('project_generation', 1)
    ar._assert_runtime_project_generation(project['id'], generation)
    cwd = session.get('_agent_cwd') or session.get('agent_cwd') or project['project_path']
    return json.loads(json.dumps(dict(
        project_id=project['id'], project_generation=generation,
        project_path=str(Path(project['project_path']).resolve()),
        session_id=session['session_id'], provider=provider, native_id=identity,
        character=session.get('character'), cwd=str(Path(cwd).resolve()),
        isolated=bool(session.get('_worktree_isolated') or session.get('worktree_isolated')),
        source=session.get('source') or '', permissions=permissions(project),
        trigger_type=session.get('trigger_type') or 'manual', trigger_id=session.get('trigger_id') or '',
        job_shape=ar._context_profile.resolve_job_shape(session.get('source') or '', session.get('job_shape') or ''),
        question=question), sort_keys=True))


def validate(project: dict[str, Any], source: dict[str, Any], bound: dict[str, Any],
             *, busy: bool = True) -> None:
    from mc import agent_runtime
    from mc.blueprints import agent_routes as ar
    if source.get('incognito'):
        raise MailHeld('incognito_not_persistent')
    if (project.get('id') != bound['project_id']
            or source.get('project_id', project['id']) != project['id']
            or str(Path(project.get('project_path') or '').resolve()) != bound['project_path']):
        raise MailHeld('project_identity_changed')
    try:
        ar._assert_runtime_project_generation(project['id'], bound['project_generation'])
    except Exception as exc:
        raise MailHeld('project_generation_changed') from exc
    if (source.get('project_generation', 1) != bound['project_generation']
            or (source.get('provider') or 'claude') != bound['provider']
            or native(source) != bound['native_id']):
        raise MailHeld('conversation_identity_changed')
    if source.get('character') != bound['character']:
        raise MailHeld('persona_changed')
    character = bound['character']
    if character:
        ref = f"{character.get('scope', 'global')}:{character.get('name', '')}"
        resolved, _ = ar._resolve_character(project['project_path'], ref, project)
        if not resolved or resolved.get('name') != character.get('name'):
            raise MailHeld('persona_unavailable')
    if source.get('source', '') != bound['source'] or permissions(project) != bound['permissions']:
        raise MailHeld('execution_context_changed')
    if ((source.get('trigger_type') or 'manual') != bound['trigger_type']
            or (source.get('trigger_id') or '') != bound['trigger_id']
            or ar._context_profile.resolve_job_shape(source.get('source') or '', source.get('job_shape') or '')
            != bound['job_shape']):
        raise MailHeld('execution_context_changed')
    if source.get('status') in ('stopped', 'cancelled', 'closed', 'deleted'):
        raise MailHeld('session_closed')
    if source.get('character_ref') or source.get('recovery_held'):
        raise MailHeld('recovery_held')
    if any(source.get(key) for key in ('_stdin_write_uncertain', '_launch_uncertain',
                                      '_limit_resume_exhaustion', '_allowance_exhausted')):
        raise MailHeld('provider_recovery_held')
    if busy and (source.get('status') == 'running' or source.get('_respawn_in_flight')
                 or source.get('_dispatching_followup') or source.get('pending_followups')):
        raise MailHeld('session_busy')
    cwd = source.get('_agent_cwd') or source.get('agent_cwd') or bound['cwd']
    if bool(source.get('_worktree_isolated') or source.get('worktree_isolated')) != bound['isolated']:
        raise MailHeld('execution_context_changed')
    if str(Path(cwd).resolve()) != bound['cwd'] or not Path(bound['cwd']).is_dir():
        raise MailHeld('worktree_unavailable')
    runtime = agent_runtime.get_runtime(bound['provider'])
    transcript = runtime.transcript_path(project['project_path'], bound['native_id'])
    if transcript is None or not Path(transcript).is_file() or Path(transcript).stat().st_size == 0:
        raise MailHeld('native_history_missing')
    if bound['provider'] == 'claude':
        reason, _ = ar._auto_fresh_trigger(project['project_path'], bound['native_id'],
                                          source.get('context_tokens'), session_or_character=source)
        if reason or ar._resume_unresumable(source, project['id'], project['project_path'],
                                            bound['native_id']):
            raise MailHeld('rollover_required')
    refusal = ar._allowance_refusal(bound['provider'], user_initiated=True)
    if refusal:
        raise MailHeld('provider_quota_held')


def resolve(project: dict[str, Any], bound: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from mc.blueprints import agent_routes as ar
    pid, sid = project['id'], bound['session_id']
    with ar.get_manager(pid).lock:
        origin = ar.agent_sessions.get(sid)
        if origin is None:
            origin = next((r for r in ar._load_agent_log(pid, strict=True)
                           if r.get('session_id') == sid), None)
        if origin and origin.get('status') in ('stopped', 'cancelled', 'closed', 'deleted'):
            raise MailHeld('session_closed')
        # Only a matching provider/native id can establish canonical ownership.
        live = [s for s in ar.agent_sessions.values() if s.get('project_id') == pid
                and (s.get('provider') or 'claude') == bound['provider']
                and native(s) == bound['native_id']]
        active = [s for s in live if ar._session_proc_alive(s)]
        if len(active) > 1:
            raise MailHeld('duplicate_conversation_owner')
        source = active[0] if active else origin
        if source is None:
            raise MailHeld('session_unavailable')
        validate(project, source, bound)
        return source['session_id'], source
