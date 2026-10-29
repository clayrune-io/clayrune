"""Trusted project snapshots, outside the project-records directory (DH-M1).

One atomic replace commits the policy, frozen catalogue, evidence and mode
together. Only the server activation path writes this record. The process-local
lock serializes compare-and-swap on the single Clayrune server; unrestricted disk
edits and other server processes are outside this feature's trust boundary.
"""
from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mc.atomic_json import (read_text_with_retry, replace_with_retry,
                            write_json_atomic)
from mc.dev_harness_catalogue import digest
from mc.dev_harness_policy import (MODES, lost_protections, resolve_policy,
                                   resolve_frozen_policy, validate_enforcement)


def _within(path: Path, base: Path) -> bool:
    """Containment test that survives the Windows extended-length-prefix resolve() quirk.

    Path.resolve() on a path being created by another thread can return the
    extended-length form (or a stale one) for one side only, so a plain
    is_relative_to() spuriously reports an escape. Compare with the prefix
    stripped and case folded; symlink resolution is untouched.
    """
    def norm(p: Path) -> Path:
        text = os.path.normcase(os.fspath(p))
        return Path(text[4:] if text.startswith('\\\\?\\') else text)
    return norm(path).is_relative_to(norm(base))


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
        if not _within(path.resolve(), self.root):
            raise ValueError('Harness state path escapes its root')
        return path

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            from mc.dev_harness_native_plan import strict_loads
            # Trusted snapshots/receipts can embed both a plan and measured
            # source, plus audit history. The 4 MiB *plan input* limit must not
            # make a successfully written snapshot unreadable on its next read.
            data = strict_loads(read_text_with_retry(path), max_bytes=None)
            if not isinstance(data, dict):
                raise ValueError('Expected an object')
            return data
        except (OSError, ValueError, UnicodeError) as exc:
            raise StoreError(f'Unreadable harness record: {path.name}') from exc

    def _active_path(self, project_id: str) -> Path:
        folder = self.project_dir(project_id)
        path = folder / 'activated' / 'active.json'
        if not _within(path.resolve(), folder.resolve()):
            raise StoreError('Active snapshot path escapes project')
        return path

    def read(self, project_id: str) -> dict[str, Any] | None:
        record = self._read_snapshot(project_id)
        if record and record.get('native_plan'):
            from mc.dev_harness_native_plan import validate_plan
            try:
                validate_plan(self, {'id': project_id,
                    'project_path': record['native_plan']['payload']['project_path']}, record['policy'], record['native_plan'])
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise StoreError('Active native plan inputs changed') from exc
        return record

    def _read_snapshot(self, project_id: str) -> dict[str, Any] | None:
        """Integrity-only prior bar for human replacement; never run authority."""
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
            resolved = resolve_frozen_policy(policy, frozen)
            if resolved != policy:
                raise ValueError('Frozen policy mismatch')
            audit = record['audit']
            if (not isinstance(audit, list) or len(audit) != record['revision']
                    or [event['revision'] for event in audit] != list(range(1, record['revision'] + 1))):
                raise ValueError('Missing activation audit history')
            envelope = record.get('native_plan')
            if envelope is not None:
                from mc.dev_harness_native_plan import validate_binding
                validate_binding(self, project_id, policy, envelope)
                if (record.get('native_plan_digest') != envelope['plan_digest']
                        or audit[-1].get('native_plan_digest') != envelope['plan_digest']):
                    raise ValueError('Snapshot native plan binding differs')
            elif record.get('native_plan_digest') is not None or any(
                    v['enabled'] for v in policy.get('native_checks', {}).values()):
                raise ValueError('Missing approved native plan')
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError('Corrupt active harness snapshot') from exc
        return record

    def read_evidence(self, project_id: str, evidence_id: str) -> dict[str, Any]:
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', evidence_id):
            raise ValueError('Invalid evidence identity')
        folder = self.project_dir(project_id)
        path = folder / 'evidence' / f'{evidence_id}.json'
        if not _within(path.resolve(), folder.resolve()):
            raise ValueError('Evidence path escapes project')
        # The activation-evidence owner composes trusted measurements. No HTTP or
        # headless caller can submit the body of one to this store.
        if not path.exists():
            raise ValueError('Missing stored measured Preview evidence')
        record = self._read_json(path)
        if record.get('coverage_scope') == 'named_native_checks' and record.get('evidence_id') != evidence_id:
            raise StoreError('Measured Preview evidence identity mismatch')
        return record

    def activate(self, project_id: str, *, expected_revision: int,
                 draft: dict[str, Any], mode: str, catalogue: dict[str, Any],
                 evidence_id: str | None = None,
                 acknowledged_losses: list[str] | None = None,
                 native_plan_digest: str | None = None,
                 acknowledged_native_losses: list[str] | None = None,
                 project: dict[str, Any] | None = None) -> dict[str, Any]:
        """Called only after the route's existing caller AND passcode gates."""
        if (type(expected_revision) is not int or expected_revision < 0
                or not isinstance(mode, str) or mode not in MODES):
            raise ValueError('Expected nonnegative active revision and explicit mode')
        if (acknowledged_losses is not None and (not isinstance(acknowledged_losses, list)
                or any(not isinstance(item, str) for item in acknowledged_losses))):
            raise ValueError('Invalid lost-protection acknowledgement')
        if (acknowledged_native_losses is not None and (not isinstance(acknowledged_native_losses, list)
                or any(not isinstance(item, str) for item in acknowledged_native_losses)
                or acknowledged_native_losses != sorted(set(acknowledged_native_losses)))):
            raise ValueError('Invalid native loss acknowledgement')
        with self._lock:
            current = self._read_snapshot(project_id)
            revision = current['revision'] if current else 0
            if revision != expected_revision:
                raise RevisionConflict('Active revision changed; reload before activating')
            policy = resolve_policy(draft, catalogue)
            losses = lost_protections(current['policy'] if current else None, policy, mode, catalogue)
            if losses != sorted(acknowledged_losses or []):
                raise ValueError('Explicit acknowledgement of each lost protection is required')
            from mc.dev_harness_native_plan import native_losses, read_plan, validate_plan
            envelope = None
            if native_plan_digest is not None:
                if project is None or project['id'] != project_id:
                    raise ValueError('Registered project required for native plan approval')
                envelope = read_plan(self, project_id, native_plan_digest)
                validate_plan(self, project, policy, envelope)
            elif any(v['enabled'] for v in policy['native_checks'].values()):
                raise ValueError('Exact native plan approval required')
            native_lost = native_losses(current, policy, mode, envelope)
            if native_lost != (acknowledged_native_losses or []):
                raise ValueError('Explicit acknowledgement of each native lost protection is required')
            evidence = None
            if mode == 'enforce':
                if not isinstance(evidence_id, str):
                    raise ValueError('Stored measured Preview evidence is required')
                evidence = self.read_evidence(project_id, evidence_id)
                if envelope is not None:
                    from mc.dev_harness_activation_evidence import validate_evidence
                    if current is None or project is None:
                        raise ValueError('Approved Preview required before native Enforce')
                    validate_evidence(self, project, current, policy, envelope, evidence)
                validate_enforcement(policy, evidence, catalogue, project_id, revision)
            record = {'version': 1, 'project_id': project_id, 'revision': revision + 1,
                      'mode': mode, 'policy': policy, 'catalogue': copy.deepcopy(catalogue),
                      'evidence_id': evidence_id if mode == 'enforce' else None,
                      'evidence': evidence, 'acknowledged_losses': losses,
                      'native_plan_digest': native_plan_digest, 'native_plan': envelope,
                      'acknowledged_native_losses': native_lost,
                      'activated_at': datetime.now(timezone.utc).isoformat()}
            record['audit'] = copy.deepcopy(current['audit']) if current else []
            record['audit'].append({
                'revision': revision + 1, 'mode': mode, 'at': record['activated_at'],
                'policy_digest': digest(policy), 'catalogue_digest': catalogue['digest'],
                'digests': copy.deepcopy(policy['digests']), 'acknowledged_losses': losses,
                'evidence_id': record['evidence_id'],
                'native_plan_digest': native_plan_digest, 'acknowledged_native_losses': native_lost,
            })
            record['snapshot_digest'] = digest(record)
            path = self._active_path(project_id)
            if current is None:
                self._write_first(path, record)
            else:
                write_json_atomic(path, record, indent=2, allow_nan=False)
            return copy.deepcopy(record)

    @staticmethod
    def _write_first(path: Path, record: dict[str, Any]) -> None:
        """First activation: `activated/` must appear already holding active.json.

        read() treats 'activated/ exists, active.json missing' as a deleted
        snapshot and fails closed. Creating the directory and then writing
        into it opened a window where any unlocked reader (a per-instance lock
        does not cover render()'s fresh HarnessStore) saw exactly that state.
        Stage the whole directory under a sibling name read() never looks at,
        then rename it into place in one step.
        """
        path.parent.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.activated-', suffix='.stage',
                                      dir=str(path.parent.parent)))
        try:
            write_json_atomic(stage / path.name, record, indent=2, allow_nan=False)
            replace_with_retry(str(stage), path.parent)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
