"""Run-role projection for roster hiveminds (BUILTINS-4 HR-8b).

Pure read-side view of data the roster, review and integration-cycle modules
already persisted. It runs no git, reads no files, launches nothing and never
writes: callers hand in the manifest, the workstream rows and the integration
state they already loaded. Legacy engine-only runs (no `config.roster_version`)
get `None` / unchanged rows, so their API shape is untouched (spec Q4=A).

Fail-closed contract: nothing here may paint a run green on thin evidence.
`accepted` is True only when integration reports `completed` AND every code
workstream has a merged entry carrying an exact passing review AND no
workstream moved since integration pinned it. Anything missing, stale or
malformed projects as `unverified`/`held`. `project()` never raises.

JSON shape (`project()`; `schema_version` 1). The detail route adds it as
`roles` ONLY for roster runs; legacy responses have no `roles` key.

    {
      "schema_version": 1,
      "stage": "workers_running" | "workers_complete" | "workers_failed"
             | "integrating" | "review_pending" | "held" | "accepted"
             | "analysis_only" | "no_changes" | "unverified",
      "accepted": bool,            # == (stage == "accepted"); the only green
      "manifest_status": str,      # passthrough; workers_complete != accepted
      "as_of": str,                # integration.updated_at ('' before it exists)
      "reason": str,               # integration reason, '' when none
      "unverified_reasons": [str], # why a 'completed' run is not accepted
      "workstreams": [{
        "id": str, "title": str, "status": str, "dependencies": [str],
        "requested_character": str,          # "global:<type>" as requested
        "assignment_attempt": int,
        "effective_engine": null | {"provider","model","effort","session_id"},
        "integration": {
          "outcome": "not_started" | "queued" | "in_progress" | "provisional"
                   | "accepted" | "skipped" | "failed" | "held"
                   | "unverified" | "not_applicable",
          "stale": bool,                     # moved since integration pinned it
          "candidate_key": str, "reason": str
        }
      }],
      "reviewer": {
        "requested_character": str,          # config.reviewer_character
        "session_ids": [str],                # distinct reviewer sessions seen
        "count": int                         # reviewers, never workers
      },
      "reviews": [{                          # accepted first, then provisional
        "ws_id": str, "state": "accepted" | "provisional", "phase": str,
        "candidate_key": str,
        "reviewed_commit": str,              # exact merged SHA reviewed
        "pre_merge_sha": str, "incoming_branch": str, "incoming_tip": str,
        "merged_tree": str,
        "checks": null | {"named": [{"name","ok"}], "ran": bool, "note": str},
        "reviewer": null | {"session_id","character",
                            "engine": {"provider","model","effort"}},
        "review_state": "" | "pending" | "pass" | "changes_requested"
                      | "held" | "timed_out" | "cancelled",
        "verdict": "" | "pass" | "changes_requested",
        "hold_reason": str, "deadline_at": number | null,
        "report_path": str, "recorded_at": str
      }],
      "hold": null | {"kind": str, "reason": str, "ws_id": str,
                      "checks": [{"name","ok"}]},
      "counts": {"workstreams","accepted","provisional","skipped","failed"}
    }

`hold.kind`: smoke_failed | merge_conflict | worker_dirty | missing_branch |
standards | merge_error | git | changes_requested | review_timed_out |
review_cancelled | review_held | other.

`summarize()` is the list-row subset {stage, accepted, requested_characters,
reviewer_character}. `annotate_run()` / `role_counts()` / `filter_runs()` cover
the runs route: each roster run row gains `run_role`
("orchestrator"|"worker"|"reviewer"), `requested_character` and
`effective_engine`; reviewers are never counted as workers.
"""
from __future__ import annotations

from typing import Any, Mapping

from mc import hivemind_roster as roster

SCHEMA_VERSION = 1
_TRUNC = 600
_ENGINE_KEYS = ('provider', 'model', 'effort')
_FAIL_KINDS = {'smoke': 'smoke_failed', 'conflict': 'merge_conflict', 'dirty': 'worker_dirty',
               'missing_branch': 'missing_branch', 'standards': 'standards',
               'merge_error': 'merge_error', 'git': 'git'}
_REVIEW_HOLD_KINDS = {'changes_requested': 'changes_requested', 'timed_out': 'review_timed_out',
                      'cancelled': 'review_cancelled', 'held': 'review_held'}
_PROVISIONAL_PHASES = {'smoking', 'launching_review', 'reviewing'}


def is_roster(manifest: Mapping[str, Any] | None) -> bool:
    return roster.is_roster_manifest(manifest)


def _text(value: Any) -> str:
    return value[:_TRUNC] if isinstance(value, str) else ''


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _engine(assignment: Any) -> dict | None:
    a = _dict(assignment)
    if not a.get('provider'):
        return None
    out = {k: _text(a.get(k)) for k in _ENGINE_KEYS}
    out['session_id'] = _text(a.get('session_id'))
    return out


def _checks(merge: Mapping[str, Any]) -> dict | None:
    """Named checks of a merged candidate; None until its smokes have run."""
    if 'smokes' not in merge:
        return None
    named = [{'name': _text(_dict(s).get('name')), 'ok': _dict(s).get('ok') is True}
             for s in _list(merge.get('smokes'))]
    return {'named': named, 'ran': bool(merge.get('smokes_run')), 'note': _text(merge.get('note'))}


def _review_valid(review: Any) -> bool:
    r = _dict(review)
    return r.get('state') == 'pass' and _dict(r.get('result')).get('verdict') == 'pass'


def _review_item(ws_id: str, state: str, phase: str, pins: Mapping[str, Any],
                 merge: Mapping[str, Any], review: Any, key: str) -> dict:
    r = _dict(review)
    result = _dict(r.get('result'))
    engine = _engine(r.get('resolved_assignment'))
    reviewer = None
    if r.get('reviewer_session'):
        reviewer = {'session_id': _text(r.get('reviewer_session')),
                    'character': _text(r.get('reviewer_character')),
                    'engine': {k: (engine or {}).get(k, '') for k in _ENGINE_KEYS}}
    deadline = r.get('deadline_at')
    return {
        'ws_id': ws_id, 'state': state, 'phase': phase, 'candidate_key': _text(key),
        'reviewed_commit': _text(pins.get('merged_sha') or merge.get('commit')),
        'pre_merge_sha': _text(pins.get('pre_merge_sha') or merge.get('pre_merge_sha')),
        'incoming_branch': _text(pins.get('incoming_branch') or merge.get('branch')),
        'incoming_tip': _text(pins.get('incoming_tip') or merge.get('incoming_tip')),
        'merged_tree': _text(pins.get('merged_tree') or merge.get('merged_tree')),
        'checks': _checks(merge),
        'reviewer': reviewer,
        'review_state': _text(r.get('state')),
        'verdict': _text(result.get('verdict')),
        'hold_reason': _text(r.get('hold_reason')),
        'deadline_at': deadline if isinstance(deadline, (int, float)) and not isinstance(deadline, bool) else None,
        'report_path': _text(result.get('report_path')),
        'recorded_at': _text(result.get('recorded_at')),
    }


def _stale(ws: Mapping[str, Any], pinned: Mapping[str, Any]) -> bool:
    return any(ws.get(k) != pinned.get(k)
               for k in ('assignment_attempt', 'worktree_session_id', 'worktree_branch'))


def _ws_outcome(ws_id: str, pinned: dict | None, idx: int, cursor: int, st: dict,
                accepted: dict, invalid: set, skipped: set, cws: str, phase: str,
                has_candidate: bool) -> tuple[str, str]:
    if pinned is None:
        return 'not_started', ''
    if not pinned.get('worktree_session_id'):
        return 'not_applicable', 'no isolated branch (analysis-only)'
    failed = _dict(st.get('failed'))
    if failed and failed.get('ws_id') == ws_id:
        return 'failed', _text(failed.get('detail') or failed.get('kind'))
    if ws_id in accepted:
        return 'accepted', ''
    if ws_id in invalid:
        return 'unverified', 'merged without an exact passing review record'
    if ws_id in skipped:
        return 'skipped', 'no commits beyond accepted HEAD'
    if ws_id == cws:
        if st.get('status') == 'held' and not has_candidate:
            return 'held', 'merge interrupted or refused; reconciliation required'
        if has_candidate and phase in _PROVISIONAL_PHASES:
            return 'provisional', ''
        return 'in_progress', ''
    if idx < cursor:
        return 'unverified', 'passed the cursor without merge or skip evidence'
    return 'queued', ''


def _hold(st: dict) -> dict | None:
    if st.get('status') != 'held':
        return None
    failed = _dict(st.get('failed'))
    current = _dict(st.get('current'))
    reviewer = _dict(current.get('reviewer'))
    if failed.get('kind') in _FAIL_KINDS:
        kind = _FAIL_KINDS[failed['kind']]
    elif reviewer.get('state') in _REVIEW_HOLD_KINDS:
        kind = _REVIEW_HOLD_KINDS[reviewer['state']]
    else:
        kind = 'other'
    ws_id = failed.get('ws_id') or _dict(current.get('candidate')).get('workstream_id') or current.get('ws_id')
    checks = [{'name': _text(_dict(s).get('name')), 'ok': _dict(s).get('ok') is True}
              for s in _list(failed.get('smokes'))]
    return {'kind': kind, 'ws_id': _text(ws_id),
            'reason': _text(st.get('reason') or reviewer.get('hold_reason')), 'checks': checks}


def _stage(workstreams: list, st: dict | None,
           problems: list[str], merged_count: int) -> str:
    if st is None:
        if workstreams and all(_dict(w).get('status') == 'completed' for w in workstreams):
            return 'workers_complete'
        if workstreams and any(_dict(w).get('status') == 'failed' for w in workstreams) \
                and not any(_dict(w).get('status') in ('active', 'pending') for w in workstreams):
            return 'workers_failed'
        return 'workers_running'
    status = st.get('status')
    if status == 'running':
        return 'integrating'
    if status == 'awaiting_review':
        return 'review_pending'
    if status == 'held':
        return 'held'
    if status == 'skipped':
        return 'analysis_only'
    if status == 'completed':
        if problems:
            return 'unverified'
        return 'accepted' if merged_count else 'no_changes'
    return 'unverified'


def _project(manifest: Mapping[str, Any], workstreams: list, integration: Any) -> dict:
    st = integration if isinstance(integration, dict) else None
    live = {w.get('id'): w for w in workstreams if isinstance(w, dict)}
    queue = [q for q in _list(st.get('queue')) if isinstance(q, dict)] if st else []
    pinned = {q.get('id'): q for q in queue}
    order = {q.get('id'): i for i, q in enumerate(queue)}
    raw_cursor = st.get('cursor') if st else None
    cursor: int = raw_cursor if type(raw_cursor) is int else 0

    accepted: dict[str, dict] = {}
    invalid: set[str] = set()
    reviews: list[dict] = []
    for m in _list(st.get('merged')) if st else []:
        m = _dict(m)
        ws_id = _text(m.get('ws_id'))
        key = _text(m.get('candidate_key'))
        if _review_valid(m.get('review')) and key:
            accepted[ws_id] = m
            reviews.append(_review_item(ws_id, 'accepted', 'accepted', m, m, m.get('review'), key))
        else:
            invalid.add(ws_id)
    skipped = {_text(_dict(s).get('ws_id')) for s in _list(st.get('skipped'))} if st else set()

    current = _dict(st.get('current')) if st else {}
    pins = _dict(current.get('candidate'))
    cws = _text(pins.get('workstream_id') or current.get('ws_id'))
    phase = _text(current.get('phase'))
    has_candidate = bool(pins)
    failed = _dict(st.get('failed')) if st else {}
    rolled_back = bool(failed.get('kind') == 'smoke' and failed.get('ws_id') == cws)
    if has_candidate and current.get('phase') in _PROVISIONAL_PHASES and not rolled_back:
        reviews.append(_review_item(cws, 'provisional', phase, pins, _dict(current.get('merge')),
                                    current.get('reviewer'),
                                    _text(_dict(current.get('reviewer')).get('candidate_key'))))

    problems: list[str] = []
    ws_rows = []
    counts = {'workstreams': 0, 'accepted': 0, 'provisional': 0, 'skipped': 0, 'failed': 0}
    for w in workstreams:
        if not isinstance(w, dict):
            continue
        ws_id = _text(w.get('id'))
        pin = pinned.get(ws_id)
        stale = bool(pin is not None and _stale(w, pin))
        outcome, why = _ws_outcome(ws_id, pin, order.get(ws_id, -1), cursor, st or {}, accepted,
                                   invalid, skipped, cws, phase, has_candidate and not rolled_back)
        if stale:
            why = 'workstream changed since integration pinned it'
            if outcome in ('accepted', 'provisional'):
                outcome = 'unverified'
        if st and st.get('status') == 'completed' and outcome not in ('accepted', 'skipped', 'not_applicable'):
            problems.append(f'{ws_id}: {outcome}')
        if stale and st and st.get('status') == 'completed':
            problems.append(f'{ws_id}: stale')
        if outcome in counts:
            counts[outcome] += 1
        counts['workstreams'] += 1
        ws_rows.append({
            'id': ws_id, 'title': _text(w.get('title')), 'status': _text(w.get('status')),
            'dependencies': [_text(d) for d in _list(w.get('dependencies'))],
            'requested_character': _text(w.get('character')),
            'assignment_attempt': w.get('assignment_attempt') if type(w.get('assignment_attempt')) is int else 0,
            'effective_engine': _engine(w.get('resolved_assignment')),
            'integration': {'outcome': outcome, 'stale': stale,
                            'candidate_key': _text(_dict(accepted.get(ws_id)).get('candidate_key')),
                            'reason': why},
        })
    if st and st.get('status') == 'completed':
        if queue and cursor != len(queue):
            problems.append('cursor did not reach the end of the queue')
        for ws_id in set(pinned) - set(live):
            problems.append(f'{ws_id}: pinned workstream missing')
        if any(r['state'] == 'provisional' for r in reviews):
            problems.append('provisional candidate still present')
        problems.extend(f'{i}: merged without exact passing review' for i in sorted(invalid))

    sessions = sorted({r['reviewer']['session_id'] for r in reviews if r['reviewer']})
    config = _dict(manifest.get('config'))
    stage = _stage(workstreams, st, problems, len(accepted))
    return {
        'schema_version': SCHEMA_VERSION, 'stage': stage, 'accepted': stage == 'accepted',
        'manifest_status': _text(manifest.get('status')),
        'as_of': _text(st.get('updated_at')) if st else '',
        'reason': _text(st.get('reason')) if st else '',
        'unverified_reasons': problems if stage == 'unverified' else [],
        'workstreams': ws_rows,
        'reviewer': {'requested_character': _text(config.get(roster.CONFIG_REVIEWER_KEY)),
                     'session_ids': sessions, 'count': len(sessions)},
        'reviews': reviews,
        'hold': _hold(st) if st else None,
        'counts': counts,
    }


def project(manifest: Mapping[str, Any] | None, workstreams: list | None,
            integration: Any) -> dict | None:
    """Full projection for a roster run; None for legacy runs. Never raises."""
    if not is_roster(manifest):
        return None
    manifest = manifest or {}
    try:
        return _project(manifest, list(workstreams or []), integration)
    except Exception as exc:  # fail closed: an unreadable run is never green
        return {'schema_version': SCHEMA_VERSION, 'stage': 'unverified', 'accepted': False,
                'manifest_status': _text(manifest.get('status')), 'as_of': '',
                'reason': 'run-role projection failed: ' + _text(str(exc)),
                'unverified_reasons': ['projection error'], 'workstreams': [],
                'reviewer': {'requested_character': '', 'session_ids': [], 'count': 0},
                'reviews': [], 'hold': None,
                'counts': {'workstreams': 0, 'accepted': 0, 'provisional': 0, 'skipped': 0, 'failed': 0}}


def summarize(manifest: Mapping[str, Any] | None, workstreams: list | None,
              integration: Any) -> dict | None:
    """List-row subset of project(); None for legacy runs."""
    full = project(manifest, workstreams, integration)
    if full is None:
        return None
    chars = sorted({w['requested_character'] for w in full['workstreams'] if w['requested_character']})
    return {'stage': full['stage'], 'accepted': full['accepted'],
            'requested_characters': chars,
            'reviewer_character': full['reviewer']['requested_character']}


# ── Runs route (agent-log rows) ─────────────────────────────────────────────

def run_role(entry: Mapping[str, Any]) -> str:
    role = (entry or {}).get('hivemind_role')
    return role if role in ('orchestrator', 'reviewer') else 'worker'


def filter_runs(entries: list[dict], role: str) -> list[dict]:
    """Role filter for roster runs: worker excludes reviewers (distinct seat)."""
    if role in ('orchestrator', 'worker', 'reviewer'):
        return [e for e in entries if run_role(e) == role]
    return list(entries)


def role_counts(entries: list[dict]) -> dict[str, int]:
    out = {'orchestrator': 0, 'worker': 0, 'reviewer': 0}
    for e in entries:
        out[run_role(e)] += 1
    return out


def annotate_run(entry: Mapping[str, Any]) -> dict:
    """Copy of a run row plus the roster identity fields (originals untouched)."""
    row = dict(entry)
    row['run_role'] = run_role(entry)
    row['requested_character'] = _text(entry.get('character_ref'))
    row['effective_engine'] = _engine(entry.get('resolved_assignment'))
    return row
