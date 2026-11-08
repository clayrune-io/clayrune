"""MC-1165: run the cold review for an agent branch tip, then let landing proceed.

Reviewer = a toolless one-shot (`agent_runtime.run_text_transform('claude', ...)`,
the Scribe/mail-launder sandbox: no tools, no MCP, neutral cwd). Its whole input
is `cold_review.render_prompt(packet)`: the dispatched task text and the git diff.
This module never receives the builder's session, transcript, journal or final
message, so it cannot pass them on.

The verdict is written to the existing review store by an INTERNAL call to
`merge_review_gate.record(cold=True, reviewer_session='')`. No route is added and
none can set `cold`, so no agent-reachable write path exists.

Threading: the session-end handler runs inside the project lock on the stream
reader threads (agent_routes.py: `with get_manager(...).lock:` around
`_log_agent_completion`, lines ~6221/6535/10077). A model call there would hold
that lock for minutes, so `start()` hands the review to a worker thread, which
then runs the normal merge-back (`then`). `merge_back` holds the landing unless a
cold pass for the exact tip is on record, so an error or timeout never lands.

Retries: at most `cold_review_gate.MAX_ATTEMPTS` (2) per tip SHA, counted before
each model call and kept in the review store with the last error. The second
attempt follows the first immediately. Past the cap this makes no model call.

`review_tip` and the worker never raise.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

import mc.merge_review_gate as _gate
from mc import cold_review, cold_review_gate as _state

DEFAULT_TIMEOUT_SECONDS = 180
CONFIG_MODEL_KEY = 'cold_review_model'   # '' = the one-shot runtime's own default

Transform = Callable[..., str]
_inflight: set[str] = set()
_inflight_lock = threading.Lock()


def _default_transform(*, prompt: str, model: str, timeout: int) -> str:
    import mc.agent_runtime as ar
    if not ar.claude_oneshot_available():
        raise RuntimeError('claude_unavailable')
    return ar.run_text_transform('claude', prompt=prompt, model=model, timeout=timeout)


def _configured_model() -> str:
    from mc import state
    value = state.CONFIG.get(CONFIG_MODEL_KEY)
    return value.strip() if isinstance(value, str) else ''


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return 'model_timeout'
    if str(exc) == 'claude_unavailable':
        return 'claude_unavailable'
    return f'model_error:{type(exc).__name__}'


def needs_review(project: dict, branch: str) -> bool:
    """Is there anything to review? Setting on, branch resolvable and ahead of
    the landing base. A read-only run with no commits has nothing to land."""
    if not cold_review.enabled(project):
        return False
    base = (project or {}).get('project_path') or ''
    tip = _gate.branch_tip(project, branch)
    if not base or not tip:
        return False
    from mc import agent_worktree as aw, project_sync
    ok, out = project_sync.git_run(base, ['rev-list', '--count', f'{aw._base_ref(project)}..{tip}'],
                                   timeout=10)
    return bool(ok and out.strip().isdigit() and int(out.strip()) > 0)


def review_tip(project: dict, branch: str, ask: str, *, transform: Transform | None = None,
               model: str | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS,
               log: Callable[[str], None] = lambda m: None) -> dict[str, Any]:
    """Review the branch's current tip and record the verdict. Never raises.

    Returns `{'status': 'recorded'|'held'|'skipped', 'verdict'?, 'attempts', 'error'?}`.
    `recorded` = a cold verdict (pass or changes_requested) is on record for the
    tip; `held` = no verdict (error/timeout/bad reply, or attempts spent);
    `skipped` = nothing to review.
    """
    pid = (project or {}).get('id') or ''
    try:
        tip = _gate.branch_tip(project, branch)
        if not tip:
            return {'status': 'skipped', 'attempts': 0, 'error': 'no_tip'}
        done = _gate.find(pid, branch, tip)
        if done and done.get('cold') is True:
            return {'status': 'recorded', 'verdict': done['verdict'],
                    'attempts': _state.attempts(pid, branch, tip)['count']}
        run = transform or _default_transform
        use_model = _configured_model() if model is None else model
        error = ''
        while not _state.exhausted(pid, branch, tip):
            n = _state.begin_attempt(pid, branch, tip)
            verdict, error = _once(project, branch, tip, ask, run, use_model, timeout)
            if verdict is not None:
                log(f'[cold-review] {verdict["verdict"]} recorded for {branch} at '
                    f'{tip[:8]} (attempt {n})')
                return {'status': 'recorded', 'verdict': verdict['verdict'], 'attempts': n}
            _state.note_error(pid, branch, tip, error)
            log(f'[cold-review] attempt {n}/{_state.MAX_ATTEMPTS} for {branch} at '
                f'{tip[:8]} failed: {error}')
        a = _state.attempts(pid, branch, tip)
        return {'status': 'held', 'attempts': a['count'], 'error': a['last_error'] or error}
    except Exception as e:  # noqa: BLE001 - documented never-raises
        log(f'[cold-review] review of {branch} failed: {type(e).__name__}: {e}')
        return {'status': 'held', 'attempts': 0, 'error': f'internal:{type(e).__name__}'}


def _once(project: dict, branch: str, tip: str, ask: str, run: Transform, model: str,
          timeout: int) -> tuple[dict | None, str]:
    """One attempt. `(verdict, '')` after recording it, or `(None, error_code)`."""
    try:
        packet = cold_review.build_packet(Path(project['project_path']),
                                          _base_ref(project), tip, ask)
        prompt = cold_review.render_prompt(packet)
    except cold_review.ColdReviewError as e:
        return None, f'packet:{e.code}'
    try:
        reply = run(prompt=prompt, model=model, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - any model-side failure is a held attempt
        return None, _error_code(e)
    verdict = cold_review.parse_verdict(reply)
    if verdict['verdict'] == cold_review.HOLD:
        return None, f'reply:{verdict["error"]}'
    try:
        _gate.record(project, branch, sha=tip, verdict=verdict['verdict'],
                     reviewer_session='', reviewer_character='cold-review (one-shot)',
                     attribution='server_oneshot', cold=True,
                     summary=verdict.get('summary', ''))
    except _gate.ReviewRefused as e:
        return None, f'record_refused:{e.status}'
    return verdict, ''


def _base_ref(project: dict) -> str:
    from mc import agent_worktree as aw
    return aw._base_ref(project)


def start(project: dict, branch: str, ask: str, then: Callable[[], None], *,
          transform: Transform | None = None, model: str | None = None,
          timeout: int = DEFAULT_TIMEOUT_SECONDS,
          log: Callable[[str], None] = lambda m: None,
          announce: Callable[[str], None] = lambda m: None) -> threading.Thread | None:
    """Review in a worker thread, then call `then` (the normal merge-back) on it.

    Returns the thread, or None when a review of this branch is already running
    (that worker calls its own `then`). `then` runs whatever the outcome, since
    `merge_back` is what refuses to land without a cold pass.
    """
    key = f"{(project or {}).get('id', '')}:{branch}"
    with _inflight_lock:
        if key in _inflight:
            return None
        _inflight.add(key)

    def work():
        try:
            announce(f'Cold review started for {branch}')
            result = review_tip(project, branch, ask, transform=transform, model=model,
                                timeout=timeout, log=log)
            if result['status'] != 'recorded':
                announce(f"Cold review of {branch}: {result['status']} "
                         f"(attempts {result['attempts']}, {result.get('error', '')})")
        except Exception as e:  # noqa: BLE001
            log(f'[cold-review] worker failed for {branch}: {e}')
        finally:
            with _inflight_lock:
                _inflight.discard(key)
            try:
                then()
            except Exception as e:  # noqa: BLE001
                log(f'[cold-review] merge-back after review failed for {branch}: {e}')

    t = threading.Thread(target=work, name='cold-review', daemon=True)
    t.start()
    return t
