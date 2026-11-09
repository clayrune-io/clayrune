"""Per-child, sticky API billing for unattended Claude sessions (phase 2).

The key only enters the returned environment. Durable session fields contain
the billing choice and a bounded reason, never vault values or CLI errors.
"""
from __future__ import annotations

import math
import re
from typing import Any

from mc import api_credits_budget as budget
from mc import api_credits_offload as offload
from mc import api_credits_transport as transport
from mc.core import _log

FIELDS = ('billing', 'billing_reason', 'billing_preference', 'billing_oauth')
UNATTENDED = frozenset({'schedule', 'steward', 'night_review', 'night-review',
                       'dispatch', 'workflow', 'hivemind_orchestrator', 'hivemind_worker'})


def snapshot(session: dict) -> dict:
    return {k: session[k] for k in FIELDS if k in session}


def eligible(session: dict) -> bool:
    return ((session.get('provider') or 'claude') == 'claude'
            and session.get('trigger_type') != 'manual'
            and (session.get('trigger_type') in UNATTENDED
                 or bool(session.get('_spawned_by') or session.get('_notify_session'))))


def _subscription(session: dict, reason: str) -> None:
    if session.get('billing') == 'credits':
        session['billing_oauth'] = True
    session.update(billing='subscription', billing_reason=reason)


def _gate(session: dict, settings: dict) -> tuple[bool, str]:
    model = offload.resolve_model(session.get('model') or session.get('agent_model') or '', settings)
    return budget.can_spend(model, settings)


def _oauth_env(env: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in env.items()
            if k.upper() not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN')}


def env_for_session(session: dict, env: dict[str, str]) -> dict[str, str]:
    """A named exception AFTER provider scoping; never mutate the input env.

    Subscription remains sticky even if a later spawn could afford credits.
    Credits can only downgrade: stop/mode/key failures use OAuth thereafter.
    """
    child = dict(env)
    if session.get('billing') == 'credits' or session.get('billing_oauth'):
        child = _oauth_env(child)
    session.pop('_api_credits_respawn_required', None)
    try:
        settings = budget.load_settings()
        if session.get('billing') == 'subscription':
            return child
        if not eligible(session):
            _subscription(session, 'attended_or_non_claude')
            return child
        if session.get('billing_preference', 'auto') != 'auto':
            _subscription(session, 'schedule_subscription')
            return child
        ok, reason = _gate(session, settings)
        if not ok:
            _subscription(session, reason)
            return child
        if session.get('billing') != 'credits':
            stop = settings['budget_usd'] * settings['stop_pct'] / 100
            if stop - budget.spent_this_cycle(settings) < settings['session_reserve_usd']:
                _subscription(session, 'insufficient_session_reserve')
                return child
        from mc import secrets_store
        try:
            key = secrets_store.get_secret_value(settings['secret_name'],
                consumer='api-credits-session', unattended=True)
        except Exception as exc:
            _subscription(session, 'vault_unavailable')
            _log(f'[api-credits-session] vault read failed: {type(exc).__name__}', flush=True)
            return child
        if not isinstance(key, str) or not key.strip():
            _subscription(session, 'vault_secret_empty')
            return child
        session.update(billing='credits', billing_reason='')
        child = _oauth_env(child)
        child['ANTHROPIC_API_KEY'] = key
        return child
    except Exception as exc:
        _subscription(session, 'billing_unavailable')
        _log(f'[api-credits-session] billing gate failed: {type(exc).__name__}', flush=True)
        return child


def needs_respawn(session: dict) -> bool:
    """A live Mode B child cannot change its auth. Check at the next turn."""
    if session.get('_api_credits_respawn_required'):
        return True
    if session.get('billing') != 'credits':
        return False
    try:
        ok, reason = _gate(session, budget.load_settings())
        if not ok:
            _subscription(session, reason)
            session['_api_credits_respawn_required'] = True
            return True
    except Exception as exc:
        _subscription(session, 'billing_unavailable')
        session['_api_credits_respawn_required'] = True
        _log(f'[api-credits-session] next-turn gate failed: {type(exc).__name__}', flush=True)
        return True
    return False


def end_idle_child_if_stopped(session: dict) -> bool:
    """Scheduler/role append paths must not send into a stopped credits child.

    Only called for an idle session, under its manager lock. Reap this owned
    child before the caller takes its normal cold-revival path.
    """
    if not needs_respawn(session):
        return False
    proc = session.get('proc')
    if proc is not None and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=10)
    session['process_alive'] = False
    return True


def note_turn(session: dict, message: str) -> None:
    session['_api_credits_turn_message'] = message


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def record_result(session: dict, msg: dict, proc_cost: dict) -> bool:
    """Accumulate normal telemetry, record the billed delta, request one replay.

    Called once per result by both readers. proc_cost is their per-process
    counter, including the CLI's saved resume baseline. Return true only for a
    halting billing error; the reader ends this child before replaying.
    """
    from mc import agent_runtime as runtime
    total = _number(msg.get('total_cost_usd'))
    previous = proc_cost.get('_api_credits_total')
    if previous is None:
        saved = _number(proc_cost.get('cli_saved_total'))
        carries = runtime._version_tuple(proc_cost.get('cli_version')) >= runtime.CLAUDE_RESUME_CARRIES_COST
        previous = saved if carries and saved is not None else 0.0
    delta = (total - previous if total >= previous else total) if total is not None else None
    runtime.accumulate_result_cost(session, msg, proc_cost)
    if session.get('billing') != 'credits':
        return False
    try:
        if delta is not None:
            usage = transport._usage(msg.get('usage'))
            models = msg.get('modelUsage')
            reported = ','.join(sorted(models)) if isinstance(models, dict) else ''
            model = str(msg.get('model') or reported or session.get('observed_model')
                        or session.get('model') or session.get('agent_model') or '')
            caller = session.get('trigger_id') if session.get('trigger_type') == 'schedule' else 'dispatch'
            from mc import api_credits_pending as pending
            pending.record(caller=f"session:{session.get('project_id', '')}:{caller or 'dispatch'}",
                          model=model, usage=usage, cost=delta, estimated=False)
            # The delta now belongs to the independent retry journal, even if
            # its ledger write failed. Never advance past an unretained delta.
            proc_cost['_api_credits_total'] = total
    except Exception as exc:
        _log(f'[api-credits-session] result accounting failed: {type(exc).__name__}', flush=True)
    try:
        if not msg.get('is_error'):
            return False
        # Only error envelopes count. An agent discussing billing in successful
        # output must never stop credits. Store canonical text, never raw errors.
        text = str(msg.get('result') or '') + ' ' + str(msg.get('errors') or '')
        error = msg.get('error')
        if isinstance(error, dict):
            text += ' ' + str(error.get('message') or '')
        status = msg.get('status_code') or (error.get('status') if isinstance(error, dict) else 0)
        if not isinstance(status, int):
            status = 0
        low = text.lower()
        if (re.search(r'\b(?:api error|http)\s*:?\s*401\b', low)
                or 'invalid api key' in low or 'authentication_error' in low):
            status = 401
        kind = transport._classify_http(status, text)
        if kind not in transport.HALTING_KINDS:
            return False
        reason = {transport.CREDIT_LOW: 'Credit balance too low',
                  transport.SPEND_LIMIT: 'API spend limit reached',
                  transport.AUTH: 'API key rejected (401)'}[kind]
        _subscription(session, reason)
        session['_api_credits_retry_pending'] = True
        try:
            budget.halt(reason, kind)
        except Exception as exc:
            # A ledger I/O failure must not strand the same failed turn on API
            # auth. Keep this session on OAuth even if the cycle stop failed.
            _log(f'[api-credits-session] stop persistence failed: {type(exc).__name__}', flush=True)
        return True
    except Exception as exc:
        _log(f'[api-credits-session] billing-error fallback failed: {type(exc).__name__}', flush=True)
        return False


def replay_after_exit(session: dict, proc: Any, routes: Any) -> bool:
    """Reuse the normal followup/respawn path after the failed child is reaped.

    Internal request metadata preserves spawner ownership; it is never accepted
    from JSON. A failed replay becomes an observable ordinary session error.
    """
    if session.get('proc') is not proc or not session.pop('_api_credits_retry_pending', False):
        return False
    if session.get('status') == 'stopped' or session.get('_interrupting'):
        return False
    message = session.get('_api_credits_turn_message')
    session['process_alive'] = False
    session['status'] = 'idle'
    try:
        if not message or routes._delegation_app is None:
            raise RuntimeError('missing replay context')
        app = routes._delegation_app
        with app.test_request_context(json={'session_id': session['session_id'], 'message': message,
                                           '_durable_delivery_ack': True},
                                      environ_overrides={'clayrune.api_credits_retry': True}):
            response = app.make_response(routes.agent_followup(session['project_id']))
            if response.status_code >= 400:
                raise RuntimeError(f'followup HTTP {response.status_code}')
        return True
    except Exception as exc:
        session['status'] = 'error'
        _log(f'[api-credits-session] subscription replay failed: {type(exc).__name__}', flush=True)
        session.setdefault('log_lines', []).append('[API credits: subscription replay failed]')
        return False
