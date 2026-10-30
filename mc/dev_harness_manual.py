"""Manual/hook preflight, independent of the optional merge-review setting."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from mc.dev_harness_diff import FreezeError, freeze_candidate
from mc.dev_harness_integration import consume, evaluate_locked, snapshot_pins
from mc.dev_harness_pending_merge import freeze_pending, pending_pins
from mc.dev_harness_store import HarnessStore, StoreError
from mc.landing_serialization import LEASE_ENV, LandingBusy, _lease_path, common_dir, landing, validate_lease


def _leased(store: HarnessStore, project: dict[str, Any], repo: Path,
            snapshot: dict[str, Any] | None, frozen: dict[str, Any]) -> None:
    identity = os.environ[LEASE_ENV]
    value = store._read_json(_lease_path(store, project['id'], identity))
    pins = value['pins']
    if (any(pins.get(k) != v for k, v in snapshot_pins(snapshot).items())
            or any(pins.get(k) != frozen[k] for k in ('B', 'H', 'T'))):
        raise ValueError('invalid_landing_lease')
    validate_lease(store, project['id'], repo, identity, pins)
    if pins.get('run_id'):
        from mc.dev_harness_integration import read_run
        from mc.dev_harness_runner import read_receipt
        result = read_run(store, project, pins['run_id'])
        if (result['content_digest'] != pins['content_digest'] or result['standards_hold']
                or any(result['source'].get(k) != frozen[k] for k in ('B', 'H', 'T'))
                or result['receipt_ids'] != pins['receipt_ids'] or result['receipt_digests'] != pins['receipt_digests']):
            raise ValueError('invalid_landing_lease')
        receipts = [read_receipt(store, project['id'], rid) for rid in pins['receipt_ids']]
        if [r['receipt_digest'] for r in receipts] != pins['receipt_digests']:
            raise ValueError('invalid_landing_lease')


def preflight(*, store: HarnessStore, project: dict[str, Any], repo: Path,
              branch: str | None, review: Callable[[str], int], register, unregister) -> int:
    """Exit 1 = finding/review hold; 2 = unevaluable prerequisite; 0 = observation."""
    try:
        if common_dir(repo) != common_dir(Path(project['project_path'])):
            print('standards: project_repository_mismatch')
            return 2
        snapshot = store._read_snapshot(project['id'])
    except (ValueError, StoreError, OSError):
        print('standards: harness_state_unavailable')
        return 2
    enforce = bool(snapshot and snapshot['mode'] == 'enforce')
    native = bool(snapshot and snapshot['mode'] != 'off' and (snapshot.get('native_plan') or enforce))
    try:
        if os.environ.get(LEASE_ENV):
            if branch is not None:
                raise ValueError('invalid_landing_lease')
            frozen = (freeze_pending if native else pending_pins)(repo)
            if frozen is None:
                raise ValueError('invalid_landing_lease')
            _leased(store, project, repo, snapshot, frozen)
            return review(frozen['H'])  # The independent review gate still runs.
        with landing(repo) as owner:
            try:
                snapshot = store._read_snapshot(project['id'])
            except (StoreError, OSError, ValueError):
                print('standards: harness_state_unavailable')
                return 2
            enforce = bool(snapshot and snapshot['mode'] == 'enforce')
            native = bool(snapshot and snapshot['mode'] != 'off' and (snapshot.get('native_plan') or enforce))
            # Re-freeze after acquiring the OS lock, never reuse an unlocked pass.
            pending_source = freeze_pending if native else pending_pins
            frozen = pending_source(repo) if branch is None else freeze_candidate(repo, 'HEAD', branch)
            if frozen is None:
                return 0  # Ordinary commit, no pending merge.
            result = None
            if native:
                result = evaluate_locked(store=store, project=project, owner=owner,
                    workspace={**project, 'project_path': str(repo)}, pending=frozen if branch is None else None,
                    target='HEAD' if branch else None, incoming=branch,
                    register=register, unregister=unregister)
                print('standards: ' + result.record['status'] + ' run=' + result.record['run_id'])
                if result.record['standards_hold']:
                    return 1 if result.record['status'] in ('fail', 'review_required') else 2
            status = review(frozen['H'])
            if result is not None:
                consume(store, result)
            elif store._read_snapshot(project['id']) != snapshot:
                return 2
            current = pending_source(repo) if branch is None else freeze_candidate(repo, 'HEAD', branch)
            if current != frozen:
                return 2
            return status
    except LandingBusy:
        print('standards: landing_busy')
        return 2
    except (ValueError, StoreError, OSError, KeyError, TypeError):
        print('standards: candidate_or_lease_unavailable')
        try:
            if store._read_snapshot(project['id']) != snapshot:
                return 2
        except (ValueError, StoreError, OSError):
            return 2
        # A supplied invalid lease never bypasses an existing Enforce or review
        # prerequisite. Preview native gaps alone add no new standards hold.
        if enforce or os.environ.get(LEASE_ENV):
            return 2
        return review('')
