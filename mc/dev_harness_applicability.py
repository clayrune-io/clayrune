"""Approved path-group selection and independent named-check outcomes (DH-M5)."""
from __future__ import annotations

from typing import Any


def select(payload: dict[str, Any], source: dict[str, Any], *, measurement: bool = False) -> dict[str, Any]:
    enabled = [v['id'] for v in payload['steps']]
    paths: set[str] = set()
    for change in source.get('integration_changes', source.get('changes', [])):
        paths.update(change[k] for k in ('old_path', 'new_path') if change.get(k))
    if source['kind'] == 'preview':
        # git diff HEAD omits untracked paths; the frozen manifest owns them.
        paths.update(set(source['manifest']['files']) - set(source['manifest']['index']))
    groups = payload['groups']
    all_groups = measurement or (source['kind'] == 'preview' and not paths)
    matched, requested, exclusions, unmapped = [], set(), {}, []
    for path in sorted(paths):
        hits = [g for g in groups if any(p == '*' or (path.startswith(p) if p.endswith('/') else path == p)
                                        for p in g['include'])]
        if not hits:
            unmapped.append(path)
    for group in groups:
        if all_groups or any(any(p == '*' or (path.startswith(p) if p.endswith('/') else path == p)
                                for p in group['include']) for path in paths):
            matched.append(group['id'])
            requested.update(group['check_ids'])
            for identity in group['not_applicable_check_ids']:
                exclusions.setdefault(identity, set()).add(group['reason_code'])
    if measurement:
        requested.update(enabled)
    return {'check_ids': [v for v in enabled if v in requested], 'matched_group_ids': matched,
            'not_applicable': {v: sorted(exclusions.get(v, set())) for v in enabled if v not in requested},
            'unmapped_paths': sorted(unmapped),
            'code': 'unmapped_change' if unmapped else
                    'nothing_to_merge' if source['kind'] != 'preview' and not paths else None}


def aggregate(policy: dict[str, Any], selection: dict[str, Any], results: dict[str, dict[str, Any]],
              *, mode: str, integrity_code: str | None = None) -> dict[str, Any]:
    outcomes = {}
    for identity in selection['check_ids']:
        result = results.get(identity, {'status': 'pending', 'code': 'missing_result'})
        outcomes[identity] = {**result, 'severity': policy['native_checks'][identity]['severity']}
    required = {k: v for k, v in outcomes.items() if v['severity'] == 'required'}
    warnings = {k: v for k, v in outcomes.items() if v['severity'] == 'warning'}
    code = integrity_code or selection['code']
    statuses = [v['status'] for v in required.values()]
    status = ('error' if integrity_code or code == 'unmapped_change' else
              'unsupported' if code == 'nothing_to_merge' else
              'error' if any(v not in ('pass', 'fail', 'review_required') for v in statuses) else
              'fail' if 'fail' in statuses else 'review_required' if 'review_required' in statuses else 'pass')
    return {'status': status, 'code': code, 'required': required, 'warnings': warnings,
            'standards_hold': mode == 'enforce' and (bool(integrity_code) or code == 'unmapped_change'
                or any(v != 'pass' for v in statuses)), 'authorizes_merge': False}
