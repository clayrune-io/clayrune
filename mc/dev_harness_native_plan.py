"""Nonexecuting native-plan inspection and immutable proposals (DH-M5 A2a).

Git metadata may be read. No version probe, native command, approval or job is
started here. Existing M3/M4 preparation owns file/config/tool closure checks.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from mc.atomic_json import write_json_atomic
from mc.dev_harness_catalogue import CATALOGUE_ROOT, digest
from mc.dev_harness_policy import lost_protections, require_digest, resolve_policy
from mc.dev_harness_store import HarnessStore, StoreError, _within

MAX_PLAN_BYTES = 4 * 1024 * 1024
INPUT_FIELDS = {'id', 'recipe_id', 'launcher', 'targets', 'configs', 'tool_roots',
                'command_args', 'coverage', 'timeout_seconds'}
DERIVED_FIELDS = {'recipe_digest', 'config_digests', 'tool_closure_digest',
                  'launcher_digest', 'fixture_contract_digest'}
PAYLOAD_FIELDS = {'schema_version', 'kind', 'project_id', 'project_path', 'git_common_dir',
                  'policy_inputs_digest', 'catalogue_digest', 'baseline_identity',
                  'checker_digest', 'environment_policy', 'steps', 'groups'}
TRACEABILITY = {
    'pytest': {'P02'}, 'vitest': {'P02'}, 'coverage-python': {'P02'},
    'coverage-javascript': {'P02'}, 'ruff': {'P01', 'P17'}, 'eslint': {'P01', 'P17'},
    'pyright': {'P17'}, 'typescript': {'P17'}, 'npm-build': {'P17'},
    'gitleaks': {'P06'}, 'bandit': {'P08'}, 'semgrep': {'P08'},
    'pip-audit': {'P07'}, 'npm-audit': {'P07'}, 'osv-scanner': {'P07'}, 'licence': {'P16'},
}


def strict_loads(raw: str | bytes, *, max_bytes: int | None = MAX_PLAN_BYTES) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('nonfinite_json')
    if max_bytes is not None and len(raw) > max_bytes:
        raise ValueError('native_plan_size_limit')
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except RecursionError as exc:
        raise ValueError('native_plan_nesting_limit') from exc
    if max_bytes is not None and len(json.dumps(value, allow_nan=False).encode('utf-8')) > max_bytes:
        raise ValueError('native_plan_size_limit')
    return value


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validator_digest() -> str:
    folder = Path(__file__).with_name('dev_harness_checks')
    return digest({p.name: file_digest(p) for p in sorted(folder.glob('*.py'))})


def checker_digest() -> str:
    folder = Path(__file__).parent
    names = ['dev_harness_runner.py', 'dev_harness_diff.py', 'dev_harness_native_plan.py',
             'dev_harness_activation_evidence.py', 'dev_harness_policy.py', 'dev_harness_store.py']
    # Future A2b owners become binding as soon as they ship; no placeholder code.
    names += [n for n in ('dev_harness_applicability.py', 'dev_harness_integration.py',
                          'landing_serialization.py') if (folder / n).exists()]
    return digest({'owners': {n: file_digest(folder / n) for n in names},
                   'native': validator_digest()})


def fixture_contract(identity: str) -> dict[str, Any]:
    from mc.dev_harness_checks.recipes import load_recipe
    value = strict_loads((CATALOGUE_ROOT / 'checks' / (identity + '.fixtures.json')).read_bytes())
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'recipe_id', 'recipe_digest',
            'validator_digest', 'fixture_file', 'fixture_file_digest', 'cases'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['recipe_id'] != identity or value['recipe_digest'] != digest(load_recipe(identity))
            or value['validator_digest'] != validator_digest()
            or value['fixture_file'] != f'tests/fixtures/dev_harness/{identity}.json'
            or value['cases'] != ['positive', 'finding', 'crash']):
        raise ValueError('fixture_contract_changed')
    require_digest(value['fixture_file_digest'])
    return value


def policy_inputs_digest(policy: dict[str, Any]) -> str:
    value = copy.deepcopy(policy)
    value['digests'].pop('native_rules', None)
    return digest(value)


def repository_identity(project: dict[str, Any]) -> dict[str, Any]:
    from mc import project_sync
    from mc.dev_harness_diff import git_bytes
    root = Path(project['project_path']).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('invalid_project_path')
    common = None
    if project_sync.is_git_repo(str(root)):
        common = str((root / git_bytes(root, ['rev-parse', '--git-common-dir']).decode('utf-8').strip()).resolve())
    return {'project_id': project['id'], 'project_path': str(root), 'git_common_dir': common}


def _strings(value: Any, *, nonempty: bool = False) -> list[str]:
    if (not isinstance(value, list) or len(value) > 256 or (nonempty and not value)
            or any(not isinstance(v, str) or not v or len(v) > 8192
                   or any(ord(c) < 32 for c in v) for v in value)):
        raise ValueError('invalid_native_strings')
    for v in value:
        if re.search(r'(?i)(\{\{(?:secret|user|totp):|-----BEGIN .*PRIVATE KEY|'
                     r'(?:password|passwd|secret|token|api[_-]?key|authorization)[=:]|'
                     r'^--(?:password|passwd|secret|token|api[_-]?key|authorization)$|'
                     r'https?://[^/\s]+:[^/\s]+@|'
                     r'\bbearer\s|gh[pousr]_[A-Za-z0-9]|sk-[A-Za-z0-9]{16})', v):
            raise ValueError('secret_in_native_plan')
    return value


def _relative(root: Path, value: str) -> None:
    from mc.dev_harness_diff import safe_path
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError('invalid_native_path')
    if value != '.' and (value.startswith('/') or ':' in value or '\\' in value
            or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('invalid_native_path')
    safe_path(root, value)


def _launcher(values: Any) -> list[str]:
    values = _strings(values, nonempty=True)
    executable = shutil.which(values[0])
    if executable is None:
        raise ValueError('tool_unavailable')
    path = Path(executable).resolve(strict=True)
    if not path.is_file():
        raise ValueError('tool_unavailable')
    return [str(path), *values[1:]]


def compile_step(root: Path, item: dict[str, Any], policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return approved payload data plus M3's prepared step, without execution."""
    from mc.dev_harness_checks.recipes import load_recipe, make_step
    from mc.dev_harness_runner import prepare_steps
    if not isinstance(item, dict) or set(item) != INPUT_FIELDS:
        raise ValueError('invalid_native_step_fields')
    identity = item['id']
    if not isinstance(identity, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', identity):
        raise ValueError('invalid_native_step_id')
    selection = policy['native_checks'].get(identity)
    if not selection or not selection['enabled']:
        raise ValueError('native_selection_mismatch')
    recipe = load_recipe(item['recipe_id'])
    if not set(selection['practice_ids']) <= TRACEABILITY[recipe['id']]:
        raise ValueError('invalid_native_traceability')
    value = copy.deepcopy(item)
    value['launcher'] = _launcher(value['launcher'])
    for key in ('targets', 'configs'):
        values = _strings(value[key])
        if len(values) != len(set(values)):
            raise ValueError('duplicate_native_path')
        for name in values:
            _relative(root, name)
    roots = _strings(value['tool_roots'], nonempty=True)
    if len(roots) != len(set(roots)) or any(not Path(v).is_absolute() for v in roots):
        raise ValueError('invalid_tool_closure')
    value['tool_roots'] = [str(Path(v).resolve(strict=True)) for v in roots]
    # Reject symlink/reparse roots before canonicalization can hide them.
    from mc.dev_harness_checks.recipes import closure
    closure(roots)
    if value['command_args'] is not None:
        _strings(value['command_args'], nonempty=True)
    seconds = value['timeout_seconds']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 7200:
        raise ValueError('invalid_timeout')
    if recipe['id'].startswith('coverage-'):
        if selection['severity'] == 'required':
            raise ValueError('coverage_required_unmeasurable')
        cfg = value['coverage']
        if (not isinstance(cfg, dict) or set(cfg) != {'report', 'format', 'source_roots', 'floor_percent', 'diff_launcher'}
                or cfg['format'] != ('lcov-v1' if recipe['id'] == 'coverage-javascript' else 'coverage.py-xml-7.10.6')
                or type(cfg['floor_percent']) not in (int, float) or not math.isfinite(cfg['floor_percent'])
                or not 0 <= cfg['floor_percent'] <= 100
                or cfg['floor_percent'] != policy['practices']['P02']['caps'].get('changed_line_floor_percent')):
            raise ValueError('invalid_coverage_configuration')
        _relative(root, cfg['report'])
        for name in _strings(cfg['source_roots'], nonempty=True):
            _relative(root, name)
            if not (root / name).is_dir():
                raise ValueError('invalid_coverage_source_root')
        cfg['diff_launcher'] = _launcher(cfg['diff_launcher'])
        from mc.dev_harness_diff import git_bytes
        if (root / cfg['report']).exists():
            raise ValueError('coverage_report_exists')
        git_bytes(root, ['check-ignore', '-q', cfg['report']])
    elif value['coverage'] is not None:
        raise ValueError('invalid_coverage_configuration')
    step = make_step(recipe['id'], **{k: value[k] for k in (
        'launcher', 'targets', 'configs', 'tool_roots', 'command_args', 'coverage', 'timeout_seconds')})
    step['id'] = identity
    prepared = prepare_steps(root, [step])[0]
    value.update(recipe_digest=digest(recipe), config_digests=prepared['config_digests'],
                 tool_closure_digest=prepared['native_prepared']['closure_digest'],
                 launcher_digest=prepared['tool_digest'], fixture_contract_digest=digest(fixture_contract(recipe['id'])))
    return value, prepared


def validate_groups(groups: Any, enabled: set[str], root: Path) -> None:
    if not isinstance(groups, list) or not groups or len(groups) > 64:
        raise ValueError('invalid_verification_groups')
    seen = set()
    for group in groups:
        if (not isinstance(group, dict) or set(group) != {'id', 'include', 'check_ids', 'not_applicable_check_ids', 'reason_code'}
                or not isinstance(group['id'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', group['id'])
                or group['id'] in seen or group['reason_code'] not in ('source_change', 'docs_change', 'config_change', 'other_change')):
            raise ValueError('invalid_verification_group')
        seen.add(group['id'])
        checks = _strings(group['check_ids'], nonempty=True)
        excluded = _strings(group['not_applicable_check_ids'])
        if (len(set(checks + excluded)) != len(checks + excluded) or set(checks + excluded) != enabled):
            raise ValueError('invalid_group_partition')
        includes = _strings(group['include'], nonempty=True)
        if len(set(includes)) != len(includes):
            raise ValueError('duplicate_group_prefix')
        for name in includes:
            if name != '*':
                _relative(root, name[:-1] if name.endswith('/') else name)
                if any(v in name for v in '*?![') or name in ('.', './'):
                    raise ValueError('invalid_group_prefix')


def _plan_path(store: HarnessStore, project_id: str, identity: str) -> Path:
    require_digest(identity)
    folder = store.project_dir(project_id)
    path = folder / 'native_plans' / (identity + '.json')
    if not _within(path.resolve(), folder.resolve()):
        raise StoreError('Native plan path escapes project')
    return path


def read_plan(store: HarnessStore, project_id: str, identity: str) -> dict[str, Any]:
    record = store._read_json(_plan_path(store, project_id, identity))
    strict_loads(json.dumps(record, allow_nan=False))
    if (set(record) != {'plan_digest', 'payload'} or record['plan_digest'] != identity
            or not isinstance(record['payload'], dict) or set(record['payload']) != PAYLOAD_FIELDS
            or record['payload']['project_id'] != project_id or digest(record['payload']) != identity):
        raise StoreError('Invalid native plan integrity')
    return record


def validate_binding(store: HarnessStore, project_id: str, policy: dict[str, Any],
                     envelope: dict[str, Any]) -> dict[str, Any]:
    """Stored integrity, including old approvals whose installed inputs drifted.

    Human replacement needs the intact prior bar to compute exact losses. This
    reader never authorizes execution and never tolerates a corrupt stored copy.
    """
    identity = envelope.get('plan_digest')
    if not isinstance(identity, str):
        raise ValueError('invalid_native_plan_digest')
    record = read_plan(store, project_id, identity)
    if record != envelope:
        raise StoreError('Embedded native plan differs from store')
    value = record['payload']
    if (value['schema_version'] != 1 or type(value['schema_version']) is not int
            or value['kind'] != 'native_execution_plan'
            or value['environment_policy'] != 'inherit_executor_freeze_each_run'
            or value['catalogue_digest'] != policy['catalogue_digest']
            or value['policy_inputs_digest'] != policy_inputs_digest(policy)
            or policy['digests'].get('native_rules') != identity
            or digest(value['baseline_identity']) != policy['digests'].get('baseline')):
        raise ValueError('plan_inputs_changed')
    baseline = value['baseline_identity']
    if (set(baseline) != {'kind', 'source', 'caps'} or baseline['kind'] != 'native_measurement_baseline_v1'
            or baseline['caps'] != {k: v['caps'] for k, v in policy['practices'].items() if v['enabled']}):
        raise ValueError('invalid_native_baseline')
    enabled = {k for k, v in policy.get('native_checks', {}).items() if v['enabled']}
    steps = value['steps']
    if (not isinstance(steps, list) or not 0 < len(steps) <= 64
            or any(not isinstance(v, dict) or set(v) != INPUT_FIELDS | DERIVED_FIELDS for v in steps)
            or len({v['id'] for v in steps}) != len(steps) or {v['id'] for v in steps} != enabled):
        raise ValueError('native_selection_mismatch')
    root = Path(value['project_path'])
    validate_groups(value['groups'], enabled, root)
    return value


def validate_plan(store: HarnessStore, project: dict[str, Any], policy: dict[str, Any],
                  envelope: dict[str, Any]) -> list[dict[str, Any]]:
    """Reinspect executable inputs. Ordinary application source is not a plan pin."""
    value = validate_binding(store, project['id'], policy, envelope)
    if (any(value[k] != v for k, v in repository_identity(project).items())
            or value['checker_digest'] != checker_digest()):
        raise ValueError('plan_inputs_changed')
    root = Path(value['project_path'])
    prepared = []
    for item in value['steps']:
        computed, command = compile_step(root, {k: item[k] for k in INPUT_FIELDS}, policy)
        if computed != item:
            raise ValueError('plan_inputs_changed')
        prepared.append(command)
    # Recheck source observation only when measuring/activating Enforce, not on
    # ordinary active-plan reads: new application code requires a run, no approval.
    return prepared


def native_losses(current: dict[str, Any] | None, policy: dict[str, Any], mode: str,
                  envelope: dict[str, Any] | None) -> list[str]:
    old = (current or {}).get('policy', {}).get('native_checks', {})
    old_plan = (current or {}).get('native_plan')
    def scope(plan, identity, selection):
        if not plan:
            return None
        payload = plan['payload']
        return {'step': next((v for v in payload['steps'] if v['id'] == identity), None),
                'groups': [{'id': g['id'], 'include': g['include'], 'reason_code': g['reason_code'],
                            'selected': identity in g['check_ids']} for g in payload['groups']],
                'caps': {k: payload['baseline_identity']['caps'].get(k) for k in selection['practice_ids']},
                'repository': {k: payload[k] for k in ('project_id', 'project_path', 'git_common_dir')},
                'checker_digest': payload['checker_digest']}
    return sorted(k for k, item in old.items() if item['enabled'] and item['severity'] == 'required'
        and (policy.get('native_checks', {}).get(k) != item or scope(old_plan, k, item) != scope(envelope, k, item)
             or ((current or {}).get('mode') == 'enforce' and mode != 'enforce')))


def inspect_plan(*, store: HarnessStore, project: dict[str, Any], draft: dict[str, Any],
                 catalogue: dict[str, Any], steps: list[dict[str, Any]], groups: list[dict[str, Any]]) -> dict[str, Any]:
    from mc.dev_harness_diff import preview
    strict_loads(json.dumps({'draft': draft, 'steps': steps, 'groups': groups}, allow_nan=False))
    store.project_dir(project['id'])
    if not isinstance(draft, dict):
        raise ValueError('invalid_native_policy')
    request = copy.deepcopy(draft)
    supplied = request.get('digests', {})
    if not isinstance(supplied, dict):
        raise ValueError('invalid_policy_digests')
    request['digests'] = {k: v for k, v in supplied.items() if k not in ('baseline', 'native_rules') or v is not None}
    policy = resolve_policy(request, catalogue)
    root = Path(project['project_path']).resolve()
    source = preview(root)
    baseline = {'kind': 'native_measurement_baseline_v1', 'source': source,
                'caps': {k: v['caps'] for k, v in policy['practices'].items() if v['enabled']}}
    if policy['digests'].get('baseline', digest(baseline)) != digest(baseline):
        raise ValueError('baseline_pin_mismatch')
    policy['digests']['baseline'] = digest(baseline)
    if not isinstance(steps, list) or not 0 < len(steps) <= 64:
        raise ValueError('invalid_native_steps')
    compiled = [compile_step(root, item, policy)[0] for item in steps]
    enabled = {k for k, v in policy['native_checks'].items() if v['enabled']}
    if len({v['id'] for v in compiled}) != len(compiled) or {v['id'] for v in compiled} != enabled:
        raise ValueError('native_selection_mismatch')
    validate_groups(groups, enabled, root)
    payload = {'schema_version': 1, 'kind': 'native_execution_plan', **repository_identity(project),
               'policy_inputs_digest': policy_inputs_digest(policy), 'catalogue_digest': catalogue['digest'],
               'baseline_identity': baseline, 'checker_digest': checker_digest(),
               'environment_policy': 'inherit_executor_freeze_each_run', 'steps': compiled, 'groups': copy.deepcopy(groups)}
    identity = digest(payload)
    if policy['digests'].get('native_rules', identity) != identity:
        raise ValueError('native_rules_pin_mismatch')
    policy['digests']['native_rules'] = identity
    envelope = {'plan_digest': identity, 'payload': payload}
    strict_loads(json.dumps(envelope, allow_nan=False))
    if preview(root) != source:
        raise ValueError('plan_inputs_changed')
    with store._lock:
        path = _plan_path(store, project['id'], identity)
        if path.exists():
            if read_plan(store, project['id'], identity) != envelope:
                raise StoreError('Native plan already exists with different contents')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, envelope, indent=2, allow_nan=False)
        prepared = validate_plan(store, project, policy, envelope)
        current = store._read_snapshot(project['id'])
        return {'project_id': project['id'], 'native_plan_digest': identity, 'plan': envelope,
                'policy': policy, 'commands': [v['argv'] for v in prepared],
                'coverage_scope': 'named_native_checks', 'authorizes_merge': False,
                'full_practice_coverage': {k: v['delivered_coverage'] for k, v in catalogue['practices'].items()},
                'protection_losses': lost_protections(current['policy'] if current else None, policy, 'enforce', catalogue),
                'native_losses': native_losses(current, policy, 'enforce', envelope)}
