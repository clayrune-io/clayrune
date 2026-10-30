"""Thin managed-landing composition; policy and execution stay in M5 leaves."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from mc import project_sync
from mc.atomic_json import write_json_atomic
from mc.dev_harness_catalogue import digest
from mc.dev_harness_diff import freeze_candidate, git_bytes, resolve_commit, FreezeError, manifest
from mc.dev_harness_integration import Evaluation, consume, evaluate_locked, snapshot_pins
from mc.dev_harness_store import HarnessStore, StoreError
from mc.landing_serialization import Ownership, assert_held, child_lease, landing


class StandardsHold(ValueError):
    """Safe code/run ID only; preserves a managed worker through teardown."""


def hold_projection(exc: Exception) -> dict[str, Any]:
    raw = str(exc) if isinstance(exc, StandardsHold) else ''
    match = re.fullmatch(r'awaiting_standards:([0-9a-f]{32})', raw)
    codes = {'landing_inputs_changed', 'landed_tree_mismatch', 'harness_state_unavailable', 'candidate_unavailable'}
    return {'code': 'awaiting_standards' if match else raw if raw in codes else 'landing_unavailable',
            'run_id': match.group(1) if match else None,
            'detail': 'native standards unavailable or held'}


def store_for_landing() -> HarnessStore:
    # Existing project-sync wiring names the install data root in both server
    # and test harnesses. Never infer trusted state from the candidate repo.
    root = project_sync._data_root
    if root is None:
        root = Path(os.environ.get('MC_DATA_DIR', str(Path(__file__).resolve().parents[1])))
    return HarnessStore(root / 'data')


def _hold_path(store: HarnessStore, project: dict[str, Any], branch: str) -> Path:
    from mc.dev_harness_diff import safe_path
    return safe_path(store.project_dir(project['id']), 'landing_holds/' + digest(branch) + '.json')


def mark_hold(project: dict[str, Any], branch: str, code: str) -> None:
    store = store_for_landing()
    path = _hold_path(store, project, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, {'branch': branch, 'code': code})


def held(project: dict[str, Any], branch: str) -> bool:
    return _hold_path(store_for_landing(), project, branch).exists()


def clear_hold(project: dict[str, Any], branch: str) -> None:
    _hold_path(store_for_landing(), project, branch).unlink(missing_ok=True)


@dataclass
class Candidate:
    store: HarnessStore
    project: dict[str, Any]
    workspace: dict[str, Any]
    owner: Ownership
    snapshot: dict[str, Any] | None
    frozen: dict[str, Any] | None
    incoming: str
    head: str
    tip: str
    evaluation: Evaluation | None
    source_manifest: dict[str, Any]

    def revalidate(self) -> dict[str, Any]:
        assert_held(self.owner)
        repo = Path(self.workspace['project_path'])
        if (resolve_commit(repo, 'HEAD') != self.head or resolve_commit(repo, self.incoming) != self.tip
                or self.store._read_snapshot(self.project['id']) != self.snapshot
                or manifest(repo) != self.source_manifest):
            raise StandardsHold('landing_inputs_changed')
        if self.evaluation is not None:
            try:
                return consume(self.store, self.evaluation)
            except (ValueError, OSError, StoreError):
                raise StandardsHold('awaiting_standards:' + self.evaluation.record['run_id']) from None
        if self.frozen is not None and freeze_candidate(repo, 'HEAD', self.incoming) != self.frozen:
            raise StandardsHold('landing_inputs_changed')
        return {**snapshot_pins(self.snapshot), 'kind': 'integration', 'B': self.head, 'H': self.tip,
                'T': self.frozen['T'] if self.frozen else None,
                'run_id': None, 'content_digest': None, 'receipt_ids': [], 'receipt_digests': []}

    @contextmanager
    def git_environment(self) -> Iterator[dict[str, str]]:
        pins = self.revalidate()
        if pins['T'] is None:
            yield dict(os.environ)  # Git will report its conflict; no reusable evaluation.
        else:
            with child_lease(self.store, self.project['id'], self.owner, pins) as env:
                yield env

    def verify_landed(self) -> None:
        if self.frozen and git_bytes(Path(self.workspace['project_path']), ['rev-parse', 'HEAD^{tree}']).decode().strip() != self.frozen['T']:
            raise StandardsHold('landed_tree_mismatch')


def prepare(project: dict[str, Any], owner: Ownership, incoming: str,
            *, repo: Path | None = None) -> Candidate:
    assert_held(owner)
    store = store_for_landing()
    workspace = {**project, 'project_path': str(repo or Path(project['project_path']))}
    root = Path(workspace['project_path'])
    try:
        snapshot = store._read_snapshot(project['id'])
    except (StoreError, OSError, ValueError):
        raise StandardsHold('harness_state_unavailable') from None
    head, tip = resolve_commit(root, 'HEAD'), resolve_commit(root, incoming)
    frozen = None
    try:
        frozen = freeze_candidate(root, 'HEAD', incoming)
    except FreezeError:
        if snapshot and snapshot['mode'] == 'enforce':
            raise StandardsHold('candidate_unavailable') from None
    evaluation = None
    if snapshot and snapshot['mode'] != 'off' and (snapshot.get('native_plan') or snapshot['mode'] == 'enforce'):
        if frozen is not None:
            from mc.blueprints.agent_routes import _register_process, _unregister_process
            evaluation = evaluate_locked(store=store, project=project, workspace=workspace, owner=owner,
                target='HEAD', incoming=incoming,
                register=lambda proc, name: _register_process(proc, 'native ' + name, 'agent_job', '', project['id']),
                unregister=_unregister_process)
            if evaluation.record['standards_hold']:
                raise StandardsHold('awaiting_standards:' + evaluation.record['run_id'])
    return Candidate(store, project, workspace, owner, snapshot, frozen, incoming, head, tip,
                     evaluation, manifest(root))


def merge_back(project, session_id, target_ref, operation):
    branch = 'clayrune/agent/' + session_id
    try:
        with landing(Path(project['project_path'])) as owner:
            result = operation(project, session_id, target_ref, owner)
            if result[0] == 'clean':
                clear_hold(project, branch)
            return result
    except (StandardsHold, StoreError, FreezeError, OSError) as exc:
        code = str(exc) if isinstance(exc, StandardsHold) else 'landing_unavailable'
        mark_hold(project, branch, code)
        return 'awaiting_standards', code
    except Exception as exc:
        from mc.landing_serialization import LandingBusy
        if not isinstance(exc, LandingBusy):
            raise
        mark_hold(project, branch, 'landing_busy')
        return 'awaiting_standards', 'landing_busy'
