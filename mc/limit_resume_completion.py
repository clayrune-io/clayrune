"""Crash-safe finalization of an attempt's persisted successful result.

This never launches or redelivers provider work. Existing completion-key log
deduplication, durable spawner outbox and synchronous workflow replay own the
side effects; the reset store owns the obligation and its final receipt.
"""
from __future__ import annotations

import time

from mc import agent_shim_completion as completion
from mc import limit_resume as policy
from mc.core import _log


def finalize(ar, session: dict, attempt: int, *, restoring: bool = False) -> bool:
    """Called under the project lock; leave the obligation on any failure."""
    store = policy.store()
    row = store.get(session['session_id']) if store else None
    deferred = (row or {}).get('deferred_success') or {}
    if (not store or not row or row['phase'] != 'submitting'
            or row.get('review_required') or row['attempts'] != attempt
            or deferred.get('attempt') != attempt
            or not policy.unattended(session) or session.get('status') == 'stopped'):
        return False
    if row.get('completion_done'):
        session['status'] = 'completed' if restoring else deferred['status']
        return True
    kind = 'exit' if restoring else deferred['kind']
    session['status'] = 'completed' if restoring else deferred['status']
    session['last_status_change_time'] = time.time()
    # Stable across crashes, including after the log row was written but before
    # the receipt. The normal shim begin must not replace this key with a UUID.
    session[completion.KEY] = {'id': row['completion_key'],
                               'kind': kind, 'status': session['status']}
    old_replay = session.get('_completion_replay')
    session['_completion_replay'] = True
    old_finalizing = getattr(policy._finalizing, 'session', None)
    policy._finalizing.session = session
    try:
        if kind == 'exit':
            ar._log_agent_completion(session)
        else:
            ar._complete_agent_turn(session)
        # Ordinary completion can return with an undelivered wake. Never discard
        # the reset obligation on the strength of a queued thread or failed log.
        if ar._spawner_notify_owed(session) or ar._workflow_notify_owed(session):
            session['status'] = 'waiting_for_reset'
            session['_limit_resume_recovering'] = True
            return False
        return store.completion_done(session, attempt)
    except Exception as exc:
        session['status'] = 'waiting_for_reset'
        session['_limit_resume_recovering'] = True
        _log(f"[limit-resume] completion retained for {session['session_id']}: {exc}", flush=True)
        return False
    finally:
        policy._finalizing.session = old_finalizing
        session.pop(completion.KEY, None)
        if old_replay is None:
            session.pop('_completion_replay', None)
        else:
            session['_completion_replay'] = old_replay
