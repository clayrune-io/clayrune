"""Released recipe data, approved argv compilation and installed closure identity."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from mc.dev_harness_catalogue import CATALOGUE_ROOT, digest
from mc.dev_harness_diff import safe_path

RECIPE_IDS = ('pytest', 'vitest', 'ruff', 'eslint', 'pyright', 'typescript', 'npm-build',
              'gitleaks', 'bandit', 'semgrep', 'pip-audit', 'npm-audit', 'osv-scanner',
              'licence', 'coverage-python', 'coverage-javascript')


def load_recipe(identity: str) -> dict[str, Any]:
    if identity not in RECIPE_IDS:
        raise ValueError('unknown_native_recipe')
    entry = json.loads((CATALOGUE_ROOT / 'checks' / (identity + '.json')).read_text(encoding='utf-8'))
    if (entry['id'] != identity or entry['schema_version'] != 1
            or not re.fullmatch(r'\d+\.\d+\.\d+', entry['tool_version'])
            or entry['report_contract'] not in {'exit-only-v1', 'ruff-json-v1', 'eslint-json-v1',
                'gitleaks-json-v8', 'bandit-json-v1', 'semgrep-json-v1', 'pip-audit-json-v1',
                'npm-audit-json-v2', 'osv-json-v2', 'licence-policy-v1', 'coverage-v1'}):
        raise ValueError('invalid_native_recipe')
    return entry


def closure(paths: list[str]) -> str:
    """Hash approved installed tool roots, including additions/deletions, not just launchers.

    Caller supplies the reviewed distribution/runtime/plugin roots. This is not
    automatic dependency discovery or a claim of isolation from arbitrary host code.
    """
    if not isinstance(paths, list) or not paths or len(paths) > 128:
        raise ValueError('invalid_tool_closure')
    files = {}
    total = 0
    for name in paths:
        path = Path(name)
        if (not path.is_absolute() or path.is_symlink() or not path.exists()
                or getattr(os.lstat(path), 'st_file_attributes', 0) & 0x400):
            raise ValueError('invalid_tool_closure')
        entries = sorted(path.rglob('*')) if path.is_dir() else [path]
        for item in entries:
            if item.is_symlink() or getattr(os.lstat(item), 'st_file_attributes', 0) & 0x400:
                raise ValueError('invalid_tool_closure')
            if item.is_dir():
                continue
            total += item.stat().st_size
            if not item.is_file() or len(files) >= 100000 or total > 512 * 1024 * 1024:
                raise ValueError('invalid_tool_closure')
            with item.open('rb') as stream:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: stream.read(65536), b''):
                    hasher.update(chunk)
                files[str(item)] = hasher.hexdigest()
    if not files:
        raise ValueError('invalid_tool_closure')
    return digest(files)


def make_step(identity: str, *, launcher: list[str], targets: list[str], configs: list[str],
              tool_roots: list[str], timeout_seconds: float = 120,
              command_args: list[str] | None = None,
              coverage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Internal approved-plan seam; no installer, shell parsing or HTTP input path.

    launcher can be an installed binary or [python, -m, module]. Existing
    project unit/type/build/licence/coverage commands use explicit approved args.
    Security/lint/audit output flags come exclusively from the data recipe.
    """
    recipe = load_recipe(identity)
    args = recipe['args']
    if (not isinstance(targets, list) or any(not isinstance(v, str) or not v for v in targets)
            or (recipe['configured_command'] and targets)
            or (not recipe['configured_command'] and '{targets}' not in args and targets)
            or (not recipe['configured_command'] and '{targets}' in args and not targets)
            or (identity in {'gitleaks', 'pip-audit'} and len(targets) != 1)):
        raise ValueError('invalid_native_targets')
    if (coverage is not None) != (recipe['report_contract'] == 'coverage-v1'):
        raise ValueError('invalid_coverage_configuration')
    if recipe['configured_command']:
        if not isinstance(command_args, list) or not command_args:
            raise ValueError('native_command_required')
        args = command_args
    elif command_args is not None:
        raise ValueError('native_arguments_not_supported')
    expanded = []
    for value in args:
        if value == '{rules}':
            rules = [p for p in configs if p.endswith(('.yaml', '.yml'))]
            if len(rules) != 1:
                raise ValueError('native_rules_required')
            expanded.append(rules[0])
        else:
            expanded.extend(targets if value == '{targets}' else [value])
    native = {'recipe': identity, 'launcher': launcher, 'tool_roots': tool_roots}
    if coverage is not None:
        native['coverage'] = coverage
    return {'id': identity, 'argv': [*launcher, *expanded], 'timeout_seconds': timeout_seconds,
            'fail_codes': recipe['fail_codes'], 'configs': configs, 'native': native}


def prepare(root: Path, step: dict[str, Any]) -> dict[str, Any]:
    native = step['native']
    if not isinstance(native, dict) or set(native) - {'recipe', 'launcher', 'tool_roots', 'coverage'}:
        raise ValueError('invalid_native_recipe')
    recipe = load_recipe(native['recipe'])
    if any(os.environ.get(key) for key in ('GITLEAKS_CONFIG', 'GITLEAKS_CONFIG_TOML',
                                          'SEMGREP_RULES', 'COVERAGE_RCFILE', 'COVERAGE_FILE')):
        raise ValueError('unbound_environment_config')
    launcher = native['launcher']
    if (not isinstance(launcher, list) or not launcher or any(not isinstance(v, str) or not v or '\0' in v for v in launcher)
            or step['argv'][:len(launcher)] != launcher or step['fail_codes'] != recipe['fail_codes']):
        raise ValueError('invalid_native_recipe')
    args = step['argv'][len(launcher):]
    if not recipe['configured_command']:
        rules = [p for p in step['configs'] if p.endswith(('.yaml', '.yml'))]
        fixed = [rules[0] if value == '{rules}' and rules else value for value in recipe['args']]
        marker = fixed.index('{targets}') if '{targets}' in fixed else len(fixed)
        if args[:marker] != fixed[:marker] or ('{targets}' not in fixed and args != fixed):
            raise ValueError('invalid_native_recipe')
        for name in args[marker:]:
            if name.startswith('-') or not safe_path(root, name).exists():
                raise ValueError('invalid_native_targets')
        if ('{targets}' in fixed and not args[marker:]
                or recipe['id'] in {'gitleaks', 'pip-audit'} and len(args[marker:]) != 1):
            raise ValueError('invalid_native_targets')
    if recipe['id'] == 'semgrep' and not any(p.endswith(('.yaml', '.yml')) for p in step['configs']):
        raise ValueError('native_rules_required')
    tool_roots = list(native['tool_roots'])
    if recipe['report_contract'] == 'coverage-v1':
        cfg = native.get('coverage', {})
        launcher = cfg.get('diff_launcher', [])
        if not launcher or not isinstance(launcher, list) or any(not isinstance(v, str) for v in launcher):
            raise ValueError('invalid_diff_launcher')
        executable = shutil.which(launcher[0])
        if not executable:
            raise ValueError('diff_cover_unavailable')
        tool_roots.append(str(Path(executable).resolve()))
    # Config/lock/rules closure is explicit and hashed by M3. Semgrep never uses a registry.
    return {**native, 'tool_roots': tool_roots, 'recipe_data': recipe, 'recipe_digest': digest(recipe),
            'closure_digest': closure(tool_roots), 'environment_digest': digest(dict(os.environ))}


def config_closure(root: Path, step: dict[str, Any]) -> list[str]:
    recipe = load_recipe(step['native']['recipe'])
    names = set(step['configs'])
    for pattern in recipe['config_globs']:
        names.update(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
    return sorted(names)
