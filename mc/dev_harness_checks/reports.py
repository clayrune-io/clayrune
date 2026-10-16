"""Validate native report envelopes; retain counts only, never tool-supplied text."""
from __future__ import annotations

import json
from typing import Any

MAX_REPORT_BYTES = 8 * 1024 * 1024


def number(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError('invalid_native_report')
    return value


def objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise ValueError('invalid_native_report')
    return value


def decode(raw: bytes) -> Any:
    if not raw or len(raw) > MAX_REPORT_BYTES:
        raise ValueError('invalid_native_report')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('invalid_native_report')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('invalid_native_report')))


def validate(contract: str, raw: bytes, code: int, fail_codes: list[int]) -> dict[str, Any]:
    """Exit/report disagreement or partial scans are Error, including exit zero."""
    if code not in [0, *fail_codes]:
        return {'status': 'error', 'code': 'unknown_native_exit'}
    if contract == 'exit-only-v1':
        return {'status': 'pass' if code == 0 else 'fail', 'coverage': 'native_command_only'}
    try:
        data = decode(raw)
        review = False
        if contract in {'ruff-json-v1', 'eslint-json-v1', 'gitleaks-json-v8'}:
            entries = objects(data)
            if contract == 'ruff-json-v1':
                for entry in entries:
                    if not isinstance(entry['code'], str) or not isinstance(entry['location'], dict):
                        raise ValueError('invalid_native_report')
                findings = len(entries)
            elif contract == 'eslint-json-v1':
                findings = sum(number(e['errorCount']) + number(e['warningCount']) for e in entries)
                if not entries or any(number(e['fatalErrorCount']) for e in entries):
                    raise ValueError('incomplete_native_scan')
            else:
                for entry in entries:
                    # Gitleaks redacts the matched secret in both fields before stdout.
                    if (entry['Secret'] != 'REDACTED' or not isinstance(entry['Match'], str)
                            or 'REDACTED' not in entry['Match']):
                        raise ValueError('unredacted_native_report')
                    if not isinstance(entry['RuleID'], str) or number(entry['StartLine']) == 0:
                        raise ValueError('invalid_native_report')
                findings = len(entries)
        elif contract == 'bandit-json-v1':
            if data['errors'] or not isinstance(data['metrics'], dict) or not data['metrics']:
                raise ValueError('incomplete_native_scan')
            if number(data['metrics']['_totals']['loc']) == 0:
                raise ValueError('incomplete_native_scan')
            entries = objects(data['results'])
            for entry in entries:
                if not isinstance(entry['test_id'], str) or number(entry['line_number']) == 0:
                    raise ValueError('invalid_native_report')
            findings = len(entries)
        elif contract == 'semgrep-json-v1':
            if (data['errors'] != [] or not isinstance(data['paths']['scanned'], list)
                    or not data['paths']['scanned'] or data['paths'].get('skipped')):
                raise ValueError('incomplete_native_scan')
            findings = len(objects(data['results']))
        elif contract == 'pip-audit-json-v1':
            entries = objects(data['dependencies'])
            if not entries or any('skip_reason' in e for e in entries):
                raise ValueError('incomplete_native_scan')
            findings = sum(len(objects(e['vulns'])) for e in entries)
            if any(not isinstance(e['name'], str) or not isinstance(e['version'], str) for e in entries):
                raise ValueError('invalid_native_report')
        elif contract == 'npm-audit-json-v2':
            if data['auditReportVersion'] != 2 or 'error' in data:
                raise ValueError('invalid_native_report')
            vulns = data['vulnerabilities']
            if not isinstance(vulns, dict) or number(data['metadata']['dependencies']['total']) == 0:
                raise ValueError('incomplete_native_scan')
            findings = len(vulns)
            if number(data['metadata']['vulnerabilities']['total']) != findings:
                raise ValueError('invalid_native_report')
        elif contract == 'osv-json-v2':
            entries = objects(data['results'])
            packages = [p for e in entries for p in objects(e['packages'])]
            if not packages or any(not isinstance(p['package'], dict) or not p['package']['name'] for p in packages):
                raise ValueError('incomplete_native_scan')
            findings = sum(len(objects(p.get('vulnerabilities', []))) for p in packages)
        elif contract == 'licence-policy-v1':
            if data['schema_version'] != 1 or not objects(data['packages']):
                raise ValueError('invalid_native_report')
            verdicts = [e['decision'] for e in data['packages']]
            if any(v not in {'allowed', 'rejected', 'unknown'} for v in verdicts):
                raise ValueError('invalid_native_report')
            findings = verdicts.count('rejected')
            review = 'unknown' in verdicts
        else:
            raise ValueError('unknown_report_contract')
        # Licence unknown is review even when the native policy exits zero.
        if (code == 0 and findings) or (code != 0 and not findings and not review):
            raise ValueError('native_exit_report_mismatch')
        return {'status': 'fail' if findings else 'review_required' if review else 'pass',
                'findings_count': findings, 'coverage': 'native_command_only'}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return {'status': 'error', 'code': 'invalid_native_report'}
