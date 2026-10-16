"""Trusted project snapshots, outside the project-records directory (DH-M1).

One atomic replace commits the policy, frozen catalogue, evidence and mode
together. Only the server activation path writes this record. The process-local
lock serializes compare-and-swap on the single Clayrune server; unrestricted disk
edits and other server processes are outside this feature's trust boundary.
"""
from __future__ import annotations

import copy
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.dev_harness_catalogue import digest
from mc.dev_harness_policy import (MODES, lost_protections, resolve_policy,
                                   validate_enforcement)


class RevisionConflict(ValueError):
    """The expected active revision no longer names the current snapshot."""


class StoreError(RuntimeError):
    """Unreadable/corrupt state must never be interpreted as Off or a pass."""


class HarnessStore:
    def __init__(self, data_root: Path):
        # data_root is DATA_DIR.parent, NOT data/projects/ itself.
        self.root = Path(data_root).resolve() / 'dev_harness'
        if Path(data_root).resolve().name == 'projects':
            raise ValueError('Harness state must not live under data/projects/')
        self._lock = threading.RLock()

    def project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', project_id):
            raise ValueError('Invalid project identity')
        path = self.root / project_id
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('Harness state path escapes its root')
        return path

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(read_text_with_retry(path))
            if not isinstance(data, dict):
                raise ValueError('Expected an object')
            return data
        except (OSError, ValueError, UnicodeError) as exc:
            raise StoreError(f'Unreadable harness record: {path.name}') from exc

    def _active_path(self, project_id: str) -> Path:
        folder = self.project_dir(project_id)
        path = folder / 'activated' / 'active.json'
        if not path.resolve().is_relative_to(folder.resolve()):
            raise StoreError('Active snapshot path escapes project')
        return path

    def read(self, project_id: str) -> dict[str, Any] | None:
        path = self._active_path(project_id)
        if not path.exists():
            if path.parent.exists():
                raise StoreError('Previously configured snapshot is missing')
            return None
        record = self._read_json(path)
        try:
            if (record.get('version') != 1 or record.get('project_id') != project_id
                    or type(record.get('revision')) is not int or record['revision'] < 1
                    or record.get('mode') not in MODES
                    or record.get('snapshot_digest') != digest({k: v for k, v in record.items() if k != 'snapshot_digest'})):
                raise ValueError('Snapshot identity/integrity mismatch')
            frozen = record['catalogue']
            if frozen['digest'] != digest({k: v for k, v in frozen.items() if k != 'digest'}):
                raise ValueError('Frozen catalogue digest mismatch')
            policy = record['policy']
            resolved = resolve_policy({k: policy[k] for k in ('profile', 'practices', 'digests')}, frozen)
            if resolved != policy:
                raise ValueError('Frozen policy mismatch')
            audit = record['audit']
            if (not isinstance(audit, list) or len(audit) != record['revision']
                    or [event['revision'] for event in audit] != list(range(1, record['revision'] + 1))):
                raise ValueError('Missing activation audit history')
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError('Corrupt active harness snapshot') from exc
        return record

    def read_evidence(self, project_id: str, evidence_id: str) -> dict[str, Any]:
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', evidence_id):
            raise ValueError('Invalid evidence identity')
        folder = self.project_dir(project_id)
        path = folder / 'evidence' / f'{evidence_id}.json'
        if not path.resolve().is_relative_to(folder.resolve()):
            raise ValueError('Evidence path escapes project')
        # DH-M3 owns producing trusted measured-preview records. No HTTP or
        # headless caller can submit the body of one to this store.
        if not path.exists():
            raise ValueError('Missing stored measured Preview evidence')
        return self._read_json(path)

    def activate(self, project_id: str, *, expected_revision: int,
                 draft: dict[str, Any], mode: str, catalogue: dict[str, Any],
                 evidence_id: str | None = None,
                 acknowledged_losses: list[str] | None = None) -> dict[str, Any]:
        """Called only after the route's existing caller AND passcode gates."""
        if (type(expected_revision) is not int or expected_revision < 0
                or not isinstance(mode, str) or mode not in MODES):
            raise ValueError('Expected nonnegative active revision and explicit mode')
        if (acknowledged_losses is not None and (not isinstance(acknowledged_losses, list)
                or any(not isinstance(item, str) for item in acknowledged_losses))):
            raise ValueError('Invalid lost-protection acknowledgement')
        with self._lock:
            current = self.read(project_id)
            revision = current['revision'] if current else 0
            if revision != expected_revision:
                raise RevisionConflict('Active revision changed; reload before activating')
            policy = resolve_policy(draft, catalogue)
            losses = lost_protections(current['policy'] if current else None, policy, mode, catalogue)
            if losses != sorted(acknowledged_losses or []):
                raise ValueError('Explicit acknowledgement of each lost protection is required')
            evidence = None
            if mode == 'enforce':
                if not isinstance(evidence_id, str):
                    raise ValueError('Stored measured Preview evidence is required')
                evidence = self.read_evidence(project_id, evidence_id)
                validate_enforcement(policy, evidence, catalogue, project_id, revision)
            record = {'version': 1, 'project_id': project_id, 'revision': revision + 1,
                      'mode': mode, 'policy': policy, 'catalogue': copy.deepcopy(catalogue),
                      'evidence_id': evidence_id if mode == 'enforce' else None,
                      'evidence': evidence, 'acknowledged_losses': losses,
                      'activated_at': datetime.now(timezone.utc).isoformat()}
            record['audit'] = copy.deepcopy(current['audit']) if current else []
            record['audit'].append({
                'revision': revision + 1, 'mode': mode, 'at': record['activated_at'],
                'policy_digest': digest(policy), 'catalogue_digest': catalogue['digest'],
                'digests': copy.deepcopy(policy['digests']), 'acknowledged_losses': losses,
                'evidence_id': record['evidence_id'],
            })
            record['snapshot_digest'] = digest(record)
            path = self._active_path(project_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(path, record, indent=2, allow_nan=False)
            return copy.deepcopy(record)
