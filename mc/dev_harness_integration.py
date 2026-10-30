"""Approved native evaluation and safe coordinator records (DH-M5 A2b).

Only a live result, consumed under its original landing ownership, can satisfy
that invocation. Persisted records and public projections never grant a merge.
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mc.atomic_json import write_json_atomic
from mc.dev_harness_applicability import aggregate, select
from mc.dev_harness_catalogue import digest
from mc.dev_harness_diff import FreezeError, preview, safe_path
from mc.dev_harness_integration_inputs import freeze_integration, require_tracked_clean, source_identity
from mc.dev_harness_native_plan import validate_plan
from mc.dev_harness_runner import _revalidate_source, read_receipt, run_approved
from mc.dev_harness_store import HarnessStore, StoreError
from mc.landing_serialization import Ownership, assert_held, common_dir

_SEAL = object()
_STATUSES = {'pass', 'fail', 'review_required', 'error', 'unsupported', 'pending', 'cancelled'}
_MEASUREMENT_ERRORS = {'invalid_native_report', 'empty_diff_coverage', 'unsupported_tool_version'}
_INTEGRITY_CODES = {'plan_inputs_changed', 'inputs_changed', 'active_snapshot_changed',
                    'baseline_changed', 'receipt_inputs_changed', 'evaluation_integrity_error',
                    'environment_changed', 'cancelled'}


def identities(source: dict[str, Any]) -> dict[str, Any]:
    if source['kind'] == 'preview':
        return {'kind': 'preview', 'head': source['head'], 'manifest_digest': source['manifest']['digest']}
    return {k: source[k] for k in ('kind', 'B', 'H', 'M', 'T')}


def snapshot_pins(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    return {'revision': snapshot['revision'] if snapshot else 0,
            'snapshot_digest': snapshot['snapshot_digest'] if snapshot else None,
            'native_plan_digest': snapshot.get('native_plan_digest') if snapshot else None}


@dataclass
class Evaluation:
    owner: Ownership
    record: dict[str, Any]
    snapshot: dict[str, Any] | None
    source: dict[str, Any]
    project: dict[str, Any]
    workspace: dict[str, Any]
    refs: tuple[str | None, str | None]
    registered_manifest: dict[str, Any] | None
    environment_digest: str
    seal: object


def _save(store: HarnessStore, project_id: str, value: dict[str, Any]) -> None:
    value['content_digest'] = digest({k: v for k, v in value.items() if k != 'content_digest'})
    path = safe_path(store.project_dir(project_id), 'integration_runs/' + value['run_id'] + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value, indent=2, allow_nan=False)


def evaluate_locked(*, store: HarnessStore, project: dict[str, Any], owner: Ownership,
                    target: str | None = None, incoming: str | None = None,
                    pending: dict[str, Any] | None = None, measurement: bool = False,
                    workspace: dict[str, Any] | None = None, run_id: str | None = None,
                    expected: dict[str, Any] | None = None,
                    register: Callable[[Any, str], None], unregister: Callable[[int], None],
                    cancel: threading.Event | None = None) -> Evaluation:
    assert_held(owner)
    workspace = workspace or project
    repo = Path(workspace['project_path'])
    if os.path.normcase(str(common_dir(repo))) != owner.key:
        raise ValueError('landing_ownership_required')
    if os.path.normcase(str(common_dir(Path(project['project_path'])))) != owner.key:
        raise ValueError('project_repository_mismatch')
    identity = run_id or uuid.uuid4().hex
    if not re.fullmatch(r'[0-9a-f]{32}', identity):
        raise ValueError('invalid_run_id')
    # Integrity-only read establishes the mode. Corruption cannot become Off.
    snapshot = store._read_snapshot(project['id'])
    mode = snapshot['mode'] if snapshot else 'preview'
    frozen = pending or (freeze_integration(repo, target, incoming) if target is not None and incoming is not None else preview(repo))
    envelope = snapshot.get('native_plan') if snapshot else None
    registered = None
    if mode != 'off' and envelope:
        if frozen['kind'] != 'preview' and Path(project['project_path']).resolve() != repo.resolve():
            require_tracked_clean(Path(project['project_path']))
        registered = source_identity(Path(project['project_path']), frozen)
    environment = digest(dict(os.environ))
    pins = {**snapshot_pins(snapshot), 'source': frozen}
    if expected is not None and pins != expected:
        raise FreezeError('queued_inputs_changed')
    value: dict[str, Any] = {'schema_version': 1, 'kind': 'native_integration_run', 'project_id': project['id'],
        'run_id': identity, **snapshot_pins(snapshot), 'source': identities(frozen),
        'policy_digest': digest(snapshot['policy']) if snapshot else None,
        'catalogue_digest': snapshot['catalogue']['digest'] if snapshot else None,
        'environment_digest': environment, '_refs': [target, incoming], 'receipt_ids': [], 'receipt_digests': [],
        'selection': None, 'status': 'pending', 'required': {}, 'warnings': {},
        'standards_hold': False, 'authorizes_merge': False, 'measured_evidence_id': None}
    evaluation = Evaluation(owner, value, snapshot, frozen, project, workspace, (target, incoming),
                            registered, environment, _SEAL)
    _save(store, project['id'], value)  # Persistence failure precedes native spawn.
    if mode == 'off' or not envelope:
        if measurement:
            raise ValueError('approved_preview_required')
        value.update(status='unsupported', code='mode_off' if mode == 'off' else 'native_plan_unavailable',
                     standards_hold=mode == 'enforce')
        _save(store, project['id'], value)
        return evaluation
    if measurement and mode != 'preview':
        raise ValueError('approved_preview_required')
    assert snapshot is not None
    results, integrity_code = {}, None
    try:
        try:
            prepared = {v['id']: v for v in validate_plan(store, project, snapshot['policy'], envelope)}
        except (ValueError, OSError, StoreError):
            raise FreezeError('plan_inputs_changed') from None
        if measurement and frozen != envelope['payload']['baseline_identity']['source']:
            raise FreezeError('baseline_changed')
        selection = select(envelope['payload'], frozen, measurement=measurement)
        # Public record names no source paths, even when unmapped.
        value['selection'] = {k: v for k, v in selection.items() if k != 'unmapped_paths'}
        value['selection']['unmapped_count'] = len(selection['unmapped_paths'])
        if not selection['code']:
            for check_id in selection['check_ids']:
                receipt = run_approved(store=store, project=project, snapshot=snapshot, frozen=frozen,
                    check_id=check_id, register=register, unregister=unregister, cancel=cancel,
                    _workspace_project=workspace)
                stored = read_receipt(store, project['id'], receipt['run_id'])
                if (stored != receipt or receipt.get('source') != frozen
                        or receipt.get('native_plan_digest') != envelope['plan_digest']
                        or receipt.get('checker_digest') != envelope['payload']['checker_digest']):
                    raise FreezeError('receipt_inputs_changed')
                value['receipt_ids'].append(receipt['run_id'])
                value['receipt_digests'].append(receipt['receipt_digest'])
                if receipt.get('code'):
                    raise FreezeError(receipt['code'] if receipt['code'] in _INTEGRITY_CODES else 'evaluation_integrity_error')
                if len(receipt['results']) != 1 or receipt['results'][0]['id'] != check_id:
                    raise FreezeError('receipt_inputs_changed')
                result = receipt['results'][0]
                if result.get('status') not in _STATUSES or result['status'] != receipt['status']:
                    raise FreezeError('receipt_inputs_changed')
                if result.get('code') and result['code'] not in _MEASUREMENT_ERRORS:
                    raise FreezeError('evaluation_integrity_error')
                step = prepared[check_id]
                if receipt.get('steps_digest') != digest([{k: v for k, v in step.items() if k != 'argv'}]):
                    raise FreezeError('receipt_inputs_changed')
                native = step['native_prepared']
                result_pins = {'argv_digest': step['argv_digest'], 'tool_digest': step['tool_digest'],
                    'config_digest': step['config_digest'], 'recipe_digest': native['recipe_digest'],
                    'tool_closure_digest': native['closure_digest'], 'environment_digest': environment,
                    'tool_version': native['recipe_data']['tool_version'],
                    'report_contract': native['recipe_data']['report_contract'], 'coverage': 'native_command_only'}
                incomplete = result.get('status') == 'error' and result.get('code') in _MEASUREMENT_ERRORS
                if any((key in result and result[key] != expected) or (key not in result and not incomplete)
                       for key, expected in result_pins.items()):
                    raise FreezeError('receipt_inputs_changed')
                results[check_id] = {k: result[k] for k in ('status', 'code', 'cancelled', 'timed_out') if k in result}
                # Findings and incomplete warnings never skip later required checks.
        _validate_live(store, evaluation)
    except (ValueError, OSError, StoreError, KeyError, StopIteration) as exc:
        integrity_code = str(exc) if isinstance(exc, FreezeError) and str(exc) in _INTEGRITY_CODES else 'evaluation_integrity_error'
        selection = locals().get('selection', {'check_ids': [v['id'] for v in envelope['payload']['steps']], 'code': None})
    value.update(aggregate(snapshot['policy'], selection, results, mode=mode, integrity_code=integrity_code))
    if measurement and value['status'] == 'pass':
        try:
            from mc.dev_harness_activation_evidence import compose_evidence
            evidence = compose_evidence(store=store, project=project, receipt_ids=value['receipt_ids'])
            value['measured_evidence_id'] = evidence['evidence_id']
        except (ValueError, OSError, StoreError):
            value.update(status='error', code='measurement_unavailable')
    _save(store, project['id'], value)
    return evaluation


def _validate_live(store: HarnessStore, evaluation: Evaluation) -> None:
    assert_held(evaluation.owner)
    if evaluation.seal is not _SEAL:
        raise ValueError('live_evaluation_required')
    if store._read_snapshot(evaluation.project['id']) != evaluation.snapshot:
        raise FreezeError('active_snapshot_changed')
    snapshot = evaluation.snapshot
    if snapshot and snapshot.get('native_plan') and (snapshot['mode'] == 'enforce'
            or evaluation.record.get('status') == 'pending'):
        validate_plan(store, evaluation.project, snapshot['policy'], snapshot['native_plan'])
    _revalidate_source(Path(evaluation.workspace['project_path']), evaluation.source, *evaluation.refs)
    if (evaluation.registered_manifest is not None
            and source_identity(Path(evaluation.project['project_path']), evaluation.source) != evaluation.registered_manifest):
        raise FreezeError('inputs_changed')
    if (digest(dict(os.environ)) != evaluation.environment_digest
            and ((snapshot or {}).get('mode') == 'enforce' or evaluation.record.get('status') == 'pending')):
        raise FreezeError('environment_changed')


def consume(store: HarnessStore, evaluation: Evaluation) -> dict[str, Any]:
    """Revalidate inside the still-held landing; stored JSON cannot call this."""
    _validate_live(store, evaluation)
    if evaluation.record['standards_hold']:
        raise ValueError('awaiting_standards')
    return {**snapshot_pins(evaluation.snapshot), **identities(evaluation.source),
            'run_id': evaluation.record['run_id'], 'content_digest': evaluation.record['content_digest'],
            'receipt_ids': evaluation.record['receipt_ids'], 'receipt_digests': evaluation.record['receipt_digests']}


def read_run(store: HarnessStore, project: dict[str, Any], run_id: str) -> dict[str, Any]:
    if not isinstance(run_id, str) or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise LookupError('run_not_found')
    path = safe_path(store.project_dir(project['id']), 'integration_runs/' + run_id + '.json')
    if not path.exists():
        raise LookupError('run_not_found')
    value = store._read_json(path)
    if (value.get('project_id') != project['id'] or value.get('run_id') != run_id):
        raise LookupError('run_not_found')
    required = {'schema_version', 'kind', 'project_id', 'run_id', 'revision', 'snapshot_digest',
                'native_plan_digest', 'source', 'status', 'required', 'warnings', 'standards_hold',
                'authorizes_merge', 'receipt_ids', 'receipt_digests', 'content_digest'}
    if (not required <= set(value) or value.get('schema_version') != 1
            or not isinstance(value.get('source'), dict)
            or value.get('kind') != 'native_integration_run' or value.get('authorizes_merge') is not False
            or value.get('status') not in _STATUSES
            or value.get('content_digest') != digest({k: v for k, v in value.items() if k != 'content_digest'})):
        raise StoreError('Invalid integration record')
    stale, reason = False, None
    try:
        snapshot = store.read(project['id'])
        stale = any(value[k] != v for k, v in snapshot_pins(snapshot).items())
        if not stale:
            source = value['source']
            if source['kind'] == 'pending_merge':
                from mc.dev_harness_pending_merge import freeze_pending
                current = freeze_pending(Path(project['project_path']))
                if current is None:
                    raise FreezeError('inputs_changed')
            elif source['kind'] == 'preview':
                current = preview(Path(project['project_path']))
            else:
                target, incoming = value.get('_refs', ['HEAD', source['H']])
                current = freeze_integration(Path(project['project_path']), target or 'HEAD', incoming or source['H'])
            stale = identities(current) != source
        if value.get('environment_digest') and value['environment_digest'] != digest(dict(os.environ)):
            stale = True
        reason = 'inputs_changed' if stale else None
    except (ValueError, OSError, StoreError, KeyError, TypeError):
        stale, reason = True, 'inputs_unavailable'
    # Explicit projection rather than trusting arbitrary extra stored fields.
    fields = ('project_id', 'run_id', 'revision', 'snapshot_digest', 'native_plan_digest', 'source',
              'policy_digest', 'catalogue_digest', 'environment_digest', 'receipt_ids', 'receipt_digests',
              'selection', 'status', 'required', 'warnings', 'standards_hold', 'authorizes_merge',
              'measured_evidence_id', 'code', 'content_digest')
    return {**{k: value[k] for k in fields if k in value}, 'stale': stale, 'stale_reason': reason}
