"""M3-only native recipe lifecycle: version, bounded report, safe result projection."""
from __future__ import annotations

import re
import os
from pathlib import Path
from typing import Any, Callable

from mc.dev_harness_checks.recipes import closure
from mc.dev_harness_checks.reports import validate
from mc.dev_harness_catalogue import digest


def version_matches(raw: bytes, expected: str) -> bool:
    try:
        text = raw.decode('utf-8', errors='strict').strip()
    except UnicodeError:
        return False
    # Never accept a prefix (8.28.0 != 8.28.01), multiple versions or giant banners.
    versions = re.findall(r'(?<![\d.])\d+\.\d+\.\d+(?![\d.])', text)
    return len(raw) < 4096 and versions == [expected] and not re.search(r'\d+\.\d+\.\d+[-+]', text)


def execute(step: dict[str, Any], root: Path, frozen: dict[str, Any],
            invoke: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    native = step['native_prepared']
    recipe = native['recipe_data']
    # Invoke is M3's existing managed executor, including registration/cancellation.
    probe = {**step, 'argv': [step['argv'][0], *native['launcher'][1:], *recipe['version_args']],
             'fail_codes': [], '_capture_stdout': True}
    result = invoke(probe)
    raw = result.pop('_stdout', b'')
    if result['status'] != 'pass' or not version_matches(raw, recipe['tool_version']):
        return {**result, 'status': 'error', 'code': 'unsupported_tool_version'}
    if recipe['report_contract'] == 'coverage-v1':
        from mc.dev_harness_checks.coverage import execute_coverage
        result = execute_coverage(step, root, frozen, invoke)
    else:
        result = invoke({**step, '_capture_stdout': recipe['report_contract'] != 'exit-only-v1'})
        raw = result.pop('_stdout', b'')
        if not result['cancelled'] and not result['timed_out']:
            result.update(validate(recipe['report_contract'], raw, result['exit_code'], step['fail_codes']))
    result.update(recipe_digest=native['recipe_digest'], tool_closure_digest=native['closure_digest'],
                  tool_version=recipe['tool_version'], report_contract=recipe['report_contract'],
                  environment_digest=native['environment_digest'])
    if closure(native['tool_roots']) != native['closure_digest']:
        result.update(status='error', code='tool_closure_changed')
    if digest(dict(os.environ)) != native['environment_digest']:
        result.update(status='error', code='environment_changed')
    return result
