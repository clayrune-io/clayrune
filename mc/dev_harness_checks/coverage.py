"""Fresh native coverage/source binding and installed diff-cover invocation.

This module validates mapping and provenance. diff-cover alone computes the
changed-line denominator and percentage. No aggregate/delta or branch inference.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from mc.dev_harness_catalogue import digest
from mc.dev_harness_diff import changes, git_bytes, manifest, safe_path
from mc.dev_harness_checks.execution import version_matches
from mc.dev_harness_checks.reports import MAX_REPORT_BYTES, decode, number

SOURCE_SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.mts', '.cts'}


def _inside(name: str, roots: list[str]) -> bool:
    return any(r == '.' or name == r or name.startswith(r.rstrip('/') + '/') for r in roots)


def _source_name(name: str, root: Path, bases: list[Path]) -> str:
    paths = set()
    for base in bases:
        path = (base / name).resolve()
        try:
            relative = path.relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError('foreign_coverage_source') from exc
        if safe_path(root, relative).is_file():
            paths.add(relative)
    if len(paths) != 1:
        raise ValueError('ambiguous_or_foreign_coverage_source')
    return paths.pop()


def _bytes(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_REPORT_BYTES:
        raise ValueError('invalid_coverage_report')
    return path.read_bytes()


def source_report(raw: bytes, format_name: str, root: Path, expected: set[str],
                  source_roots: list[str]) -> dict[str, set[int]]:
    """Reject empty/foreign/new-source omissions before diff-cover can omit them."""
    if not raw or len(raw) > MAX_REPORT_BYTES or not expected:
        raise ValueError('invalid_coverage_report')
    entries: list[tuple[str, list[int]]] = []
    if format_name == 'coverage.py-xml-7.10.6':
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
            raise ValueError('invalid_coverage_report')
        tree = ET.fromstring(raw)
        if tree.tag != 'coverage' or tree.get('version') != '7.10.6':
            raise ValueError('invalid_coverage_report')
        bases = [root]
        for source in tree.findall('./sources/source'):
            if not source.text:
                raise ValueError('foreign_coverage_source')
            base = (root / source.text).resolve()
            if not base.is_relative_to(root.resolve()) or not base.is_dir():
                raise ValueError('foreign_coverage_source')
            bases.append(base)
        for item in tree.findall('./packages/package/classes/class'):
            lines = []
            for line in item.findall('./lines/line'):
                n, hits = int(line.attrib['number']), int(line.attrib['hits'])
                if n <= 0 or hits < 0:
                    raise ValueError('invalid_coverage_report')
                lines.append(n)
            entries.append((_source_name(item.attrib['filename'], root, bases), lines))
    elif format_name == 'lcov-v1':
        name: str | None = None
        lines = []
        for line in raw.decode('utf-8', errors='strict').splitlines():
            if line.startswith('SF:'):
                if name is not None:
                    raise ValueError('invalid_coverage_report')
                name, lines = _source_name(line[3:], root, [root]), []
            elif line.startswith('DA:'):
                fields = line[3:].split(',')
                n, hits = int(fields[0]), int(fields[1])
                if name is None or n <= 0 or hits < 0:
                    raise ValueError('invalid_coverage_report')
                lines.append(n)
            elif line == 'end_of_record':
                if name is None:
                    raise ValueError('invalid_coverage_report')
                entries.append((name, lines))
                name = None
        if name is not None:
            raise ValueError('invalid_coverage_report')
    else:
        raise ValueError('unsupported_coverage_format')
    measured = {}
    for name, lines in entries:
        path = safe_path(root, name)
        if (name in measured or not path.is_file() or path.is_symlink()
                or not _inside(name, source_roots)
                or not lines or len(lines) != len(set(lines))
                or max(lines) > len(path.read_bytes().splitlines())):
            raise ValueError('foreign_or_empty_coverage_source')
        measured[name] = set(lines)
    if not expected <= set(measured):
        raise ValueError('missing_changed_source_coverage')
    return measured


def validate_diff(raw: bytes, measured: dict[str, set[int]], floor: float) -> dict[str, Any]:
    data = decode(raw)
    total, violations = number(data['total_num_lines']), number(data['total_num_violations'])
    percent = data['total_percent_covered']
    if (total == 0 or violations > total or type(percent) not in (int, float)
            or not math.isfinite(percent) or not 0 <= percent <= 100
            or not isinstance(data['src_stats'], dict) or not data['src_stats']):
        raise ValueError('empty_diff_coverage')
    counted, uncovered = 0, 0
    for name, item in data['src_stats'].items():
        covered, missing = item['covered_lines'], item['violation_lines']
        for lines in (covered, missing):
            if (not isinstance(lines, list) or any(type(n) is not int or n <= 0 for n in lines)
                    or len(lines) != len(set(lines))):
                raise ValueError('invalid_diff_coverage')
        if (name not in measured or set(covered) & set(missing)
                or not set(covered + missing) <= measured[name]):
            raise ValueError('foreign_diff_coverage')
        counted += len(covered) + len(missing)
        uncovered += len(missing)
    if counted != total or uncovered != violations:
        raise ValueError('invalid_diff_coverage')
    return {'status': 'pass' if percent >= floor else 'fail',
            'changed_lines': total, 'uncovered_changed_lines': violations,
            'changed_line_percent': percent, 'floor_percent': floor,
            'branch_coverage': 'not_evaluated', 'coverage': 'native_command_only'}


def execute_coverage(step: dict[str, Any], root: Path, frozen: dict[str, Any],
                     invoke: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    cfg = step['native_prepared']['coverage']
    if (not isinstance(cfg, dict) or set(cfg) != {'report', 'format', 'source_roots', 'floor_percent', 'diff_launcher'}
            or type(cfg['floor_percent']) not in (int, float) or not math.isfinite(cfg['floor_percent'])
            or not 0 <= cfg['floor_percent'] <= 100 or not isinstance(cfg['source_roots'], list)
            or not cfg['source_roots']):
        raise ValueError('invalid_coverage_configuration')
    for name in cfg['source_roots']:
        safe_path(root, name)
    report = safe_path(root, cfg['report'])
    # Only disposable, already ignored output paths; no supplied old report can pass.
    git_bytes(root, ['check-ignore', '-q', cfg['report']])
    if report.exists() or report.is_symlink():
        raise ValueError('stale_coverage_report')
    if step['native_prepared']['recipe'] == 'coverage-python' and (
            (root / '.coverage').exists() or any(root.glob('.coverage.*'))):
        raise ValueError('stale_coverage_report')
    report.parent.mkdir(parents=True, exist_ok=True)
    diff_report = report.parent / 'diff-cover.json'
    if diff_report.exists():
        raise ValueError('stale_coverage_report')
    before = manifest(root)
    base = frozen['B'] if frozen['kind'] == 'integration' else frozen['head']
    changed = {e['new_path'] for e in changes(root, base) if e['status'][0] != 'D'}
    changed.update(v.decode('utf-8') for v in git_bytes(root, ['ls-files', '--others', '--exclude-standard', '-z']).split(b'\0') if v)
    expected = {name for name in changed if Path(name).suffix in SOURCE_SUFFIXES
                and _inside(name, cfg['source_roots'])}
    if not expected:
        raise ValueError('coverage_classification_required')
    result = invoke(step)
    result.pop('_stdout', None)
    if result['status'] != 'pass':
        return result
    if step['native_prepared']['recipe'] == 'coverage-python':
        launcher = step['native_prepared']['launcher']
        export = invoke({**step, 'argv': [step['argv'][0], *launcher[1:], 'xml', '-o', str(report)]})
        export.pop('_stdout', None)
        if export['status'] != 'pass':
            return {**export, 'status': 'error'}
    raw = _bytes(report)
    measured = source_report(raw, cfg['format'], root, expected, cfg['source_roots'])
    if any(name not in before['files'] for name in measured) or manifest(root) != before:
        raise ValueError('foreign_coverage_source')
    launcher = cfg['diff_launcher']
    if not isinstance(launcher, list) or not launcher or any(not isinstance(v, str) for v in launcher):
        raise ValueError('invalid_diff_launcher')
    executable = shutil.which(launcher[0])
    if executable is None:
        raise ValueError('diff_cover_unavailable')
    launcher = [executable, *launcher[1:]]
    diff_step = {**step, 'argv': [*launcher, '--version'], 'fail_codes': [], '_capture_stdout': True}
    version = invoke(diff_step)
    diff_version = step['native_prepared']['recipe_data']['diff_cover_version']
    if version['status'] != 'pass' or not version_matches(version.pop('_stdout', b''), diff_version):
        raise ValueError('unsupported_diff_cover_version')
    diff_step.update(argv=[*launcher, str(report), '--compare-branch=' + base,
        '--diff-range-notation=..', '--include-untracked', '--quiet',
        '--fail-under=' + str(cfg['floor_percent']), '--format=json:' + str(diff_report)], fail_codes=[1])
    diff_result = invoke(diff_step)
    diff_result.pop('_stdout', None)
    if diff_result['cancelled'] or diff_result['timed_out'] or diff_result['exit_code'] not in (0, 1):
        return {**diff_result, 'status': 'error'}
    summary = validate_diff(_bytes(diff_report), measured, cfg['floor_percent'])
    if (diff_result['exit_code'] == 0) != (summary['status'] == 'pass'):
        raise ValueError('coverage_exit_report_mismatch')
    return {**diff_result, **summary, 'source_digest': before['digest'],
            'report_digest': hashlib.sha256(raw).hexdigest(), 'coverage_config_digest': digest(cfg),
            'measured_source_digest': digest({name: before['files'][name] for name in sorted(measured)}),
            'diff_cover_version': diff_version}
