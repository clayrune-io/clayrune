"""Server-owned composition of measured native Preview evidence (DH-M5 A2a).

Only stored M3/M4 receipts are inputs. No HTTP upload, command execution or
client report can create activation evidence. A2b will supply preview receipts;
A2a tests use synthetic receipts to prove this boundary without native runs.
"""
from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

from mc.atomic_json import write_json_atomic
from mc.dev_harness_catalogue import digest
from mc.dev_harness_diff import preview, safe_path
from mc.dev_harness_native_plan import validate_plan
from mc.dev_harness_policy import require_digest
from mc.dev_harness_store import HarnessStore, StoreError

# Known measurement failures, never source/tool/environment integrity failures.
# A2b must retain complete per-step provenance even for these warning results.
WARNING_MEASUREMENT_CODES = {'invalid_native_report', 'empty_diff_coverage', 'unsupported_tool_version'}


def _measurements(store: HarnessStore, project: dict[str, Any], snapshot: dict[str, Any],
                  receipt_ids: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from mc.dev_harness_runner import read_receipt
    from mc.dev_harness_sources import validate_activation
    policy, envelope = snapshot['policy'], snapshot['native_plan']
    if snapshot['mode'] != 'preview' or envelope is None:
        raise ValueError('Approved native Preview required')
    prepared = validate_plan(store, project, policy, envelope)
    if envelope['payload']['git_common_dir'] is None:
        raise ValueError('Git repository required for native enforcement')
    validate_activation(store, project, policy, snapshot['catalogue'])
    source = preview(Path(project['project_path']).resolve())
    if not source['head'] or source != envelope['payload']['baseline_identity']['source']:
        raise ValueError('Measured baseline source changed')
    if (not isinstance(receipt_ids, list) or not receipt_ids or len(receipt_ids) > 64
            or any(not isinstance(v, str) for v in receipt_ids) or len(set(receipt_ids)) != len(receipt_ids)):
        raise ValueError('Invalid measurement receipt IDs')
    receipts = [read_receipt(store, project['id'], identity) for identity in receipt_ids]
    expected = {v['id']: v for v in prepared}
    outcomes: dict[str, Any] = {}
    for receipt in receipts:
        # A warning Fail is legitimate; a structural error invalidates the whole
        # evidence. M3 stops on Fail so multiple receipts may cover all steps.
        if (receipt.get('status') not in ('pass', 'fail', 'review_required', 'error') or 'code' in receipt
                or receipt.get('source') != source
                or receipt.get('native_plan_digest') != envelope['plan_digest']
                or receipt.get('checker_digest') != envelope['payload']['checker_digest']
                or not isinstance(receipt.get('results'), list) or not receipt['results']):
            raise ValueError('Invalid native measurement receipt')
        ids = [v.get('id') for v in receipt['results'] if isinstance(v, dict)]
        if any(v not in expected for v in ids) or len(ids) != len(receipt['results']):
            raise ValueError('Invalid native measurement results')
        statuses = [v.get('status') for v in receipt['results']]
        aggregate = ('error' if 'error' in statuses else 'fail' if 'fail' in statuses else
                     'review_required' if 'review_required' in statuses else 'pass')
        if receipt['status'] != aggregate:
            raise ValueError('Native measurement aggregate differs from step results')
        if ids != [identity for identity in expected if identity in ids]:
            raise ValueError('Native measurement order differs from approved plan')
        if len({v.get('environment_digest') for v in receipt['results']}) != 1:
            raise ValueError('Native measurement environment changed within run')
        # Inheritance is frozen per execution, not pinned to the environment of
        # this later activation request (e.g. a new session/job or pytest phase).
        frozen_steps = []
        for result in receipt['results']:
            require_digest(result.get('environment_digest'))
            step_identity = copy.deepcopy(expected[result['id']])
            step_identity.pop('argv')
            step_identity['native_prepared']['environment_digest'] = result['environment_digest']
            frozen_steps.append(step_identity)
        if receipt.get('steps_digest') != digest(frozen_steps):
            raise ValueError('Native measurement step identity mismatch')
        for result in receipt['results']:
            identity = result.get('id')
            if identity not in expected or identity in outcomes:
                raise ValueError('Duplicate or unexpected native measurement')
            step = expected[identity]
            native = step['native_prepared']
            warning_error = (policy['native_checks'][identity]['severity'] == 'warning'
                and result.get('status') == 'error' and result.get('code') in WARNING_MEASUREMENT_CODES)
            if ((result.get('status') not in ('pass', 'fail', 'review_required') and not warning_error)
                    or (result.get('code') is not None and not warning_error)
                    or result.get('cancelled') is not False or result.get('timed_out') is not False
                    or result.get('argv_digest') != step['argv_digest']
                    or result.get('tool_digest') != step['tool_digest']
                    or result.get('config_digest') != step['config_digest']
                    or result.get('recipe_digest') != native['recipe_digest']
                    or result.get('tool_closure_digest') != native['closure_digest']
                    or result.get('tool_version') != native['recipe_data']['tool_version']
                    or result.get('report_contract') != native['recipe_data']['report_contract']
                    or result.get('coverage') != 'native_command_only'
                    or type(result.get('exit_code')) is not int
                    or (result['status'] == 'pass' and result['exit_code'] != 0)
                    or (result['status'] != 'pass' and not warning_error
                        and result['exit_code'] not in [0, *step['fail_codes']])):
                raise ValueError('Native measurement input mismatch')
            require_digest(result.get('environment_digest'))
            outcomes[identity] = {'status': result['status'], 'severity': policy['native_checks'][identity]['severity'],
                                  'recipe_digest': native['recipe_digest'],
                                  'config_digest': step['config_digest'], 'tool_digest': step['tool_digest'],
                                  'tool_closure_digest': native['closure_digest'],
                                  'fixture_contract_digest': next(v['fixture_contract_digest'] for v in
                                      envelope['payload']['steps'] if v['id'] == identity),
                                  'environment_digest': result['environment_digest']}
            if warning_error:
                outcomes[identity]['code'] = result['code']
    if set(outcomes) != set(expected):
        raise ValueError('Every enabled native check requires measurement')
    if not any(v['severity'] == 'required' for v in outcomes.values()):
        raise ValueError('Required native check necessary for enforcement')
    if any(v['status'] != 'pass' for v in outcomes.values() if v['severity'] == 'required'):
        raise ValueError('Required native measurement did not pass')
    return receipts, outcomes


def compose_evidence(*, store: HarnessStore, project: dict[str, Any],
                     receipt_ids: list[str]) -> dict[str, Any]:
    with store._lock:
        snapshot = store.read(project['id'])
        if snapshot is None or not snapshot.get('native_plan'):
            raise ValueError('Approved native Preview required')
        receipts, outcomes = _measurements(store, project, snapshot, receipt_ids)
        policy, envelope = snapshot['policy'], snapshot['native_plan']
        identity = uuid.uuid4().hex
        value = {'evidence_id': identity, 'kind': 'measured_preview', 'status': 'pass',
                 'project_id': project['id'], 'active_revision': snapshot['revision'],
                 'snapshot_digest': snapshot['snapshot_digest'], 'policy_digest': digest(policy),
                 'catalogue_digest': snapshot['catalogue']['digest'], 'digests': copy.deepcopy(policy['digests']),
                 'coverage_scope': 'named_native_checks', 'native_plan_digest': envelope['plan_digest'],
                 'source_verification': 'pass', 'baseline_verification': 'pass', 'native_rule_verification': 'pass',
                 'source': envelope['payload']['baseline_identity']['source'],
                 'receipt_ids': list(receipt_ids),
                 'receipt_digests': [v['receipt_digest'] for v in receipts], 'native_checks': outcomes,
                 'practices': {k: {'status': 'unsupported', 'coverage': v['delivered_coverage']}
                               for k, v in snapshot['catalogue']['practices'].items()},
                 'authorizes_merge': False}
        value['evidence_digest'] = digest(value)
        if store.read(project['id']) != snapshot:
            raise StoreError('Active snapshot changed during measurement')
        validate_plan(store, project, policy, envelope)
        if preview(Path(project['project_path'])) != value['source']:
            raise ValueError('Measured baseline source changed')
        path = safe_path(store.project_dir(project['id']), 'evidence/' + identity + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, value, indent=2, allow_nan=False)
        return value


def validate_evidence(store: HarnessStore, project: dict[str, Any], snapshot: dict[str, Any],
                      policy: dict[str, Any], envelope: dict[str, Any], evidence: dict[str, Any]) -> None:
    """Re-read trusted receipts and current source at the human Enforce CAS."""
    if (snapshot.get('native_plan') != envelope or snapshot['policy'] != policy
            or evidence.get('kind') != 'measured_preview' or evidence.get('status') != 'pass'
            or evidence.get('coverage_scope') != 'named_native_checks'
            or evidence.get('project_id') != project['id']
            or evidence.get('active_revision') != snapshot['revision']
            or evidence.get('snapshot_digest') != snapshot['snapshot_digest']
            or evidence.get('native_plan_digest') != envelope['plan_digest']
            or evidence.get('evidence_digest') != digest({k: v for k, v in evidence.items() if k != 'evidence_digest'})):
        raise ValueError('Stale or invalid native activation evidence')
    receipts, outcomes = _measurements(store, project, snapshot, evidence.get('receipt_ids'))
    if (evidence.get('receipt_digests') != [v['receipt_digest'] for v in receipts]
            or evidence.get('native_checks') != outcomes
            or evidence.get('source') != envelope['payload']['baseline_identity']['source']):
        raise ValueError('Native activation measurements changed')
