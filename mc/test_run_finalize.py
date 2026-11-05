"""MC-1148 4b: launcher authority and exit evidence, separate from reporters.

No execution or server-side child observation. An authorized launcher attests
to its child's wait result; the server observes only the launcher's identity.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import os
import secrets
from typing import Any, Callable

from mc import caller_attribution as ca, test_run_store as runs
from mc.core import _log
from mc.test_run_ingest import IngestRefused, OwnerBinding, ProducerReceipt, TestRunIngest


class FinalizeConflict(ValueError):
    """A terminal run already has different process evidence."""


@dataclass(frozen=True)
class FinalizeCapability:
    run_id: str
    token: str


def peer_process_identity(caller: ca.Attribution, peer_addr: str, peer_port: Any,
                          server_port: Any, roots: dict[int, str],
                          proc_identity: Callable[[int], Any]) -> str | None:
    """Resolve the socket owner, not merely the matching managed ancestor.

    Reuse attribution's system-table and unprivileged-tree fallback. Never
    parse its diagnostic prose or accept PID/creation-time claims in JSON.
    """
    if (caller.status != ca.ATTRIBUTED or caller.pid is None
            or roots.get(caller.pid) != caller.session_id or not ca._is_loopback(peer_addr)):
        return None
    try:
        peer, server = int(peer_port), int(server_port) if server_port else None
        psutil = ca._load_psutil()
        if psutil is None:
            return None
        try:
            pid = ca._owner_pid_system_wide(psutil, peer, server)
        except Exception as exc:
            _log(f'[test-runs] peer socket lookup failed: {type(exc).__name__}', flush=True)
            pid = None
        if pid is None:
            hit, _ = ca._search_managed_trees(psutil, roots, peer, server)
            pid = hit[1] if hit is not None else None
        if pid is None:
            return None
        _, created = proc_identity(pid)
        if (type(created) not in (int, float) or not math.isfinite(created) or created < 0):
            return None
        chain = [pid] + [int(p.pid) for p in psutil.Process(pid).parents()][:ca._MAX_CHAIN]
        for ancestor in chain:
            if ancestor in roots:
                if ancestor != caller.pid or roots[ancestor] != caller.session_id:
                    return None
                # Creation time brackets the socket/ancestry check (PID reuse).
                if proc_identity(pid)[1] != created:
                    return None
                return f'pid:{pid}@{created}'
            if ancestor == os.getpid():
                return None
    except Exception as exc:
        _log(f'[test-runs] peer identity lookup failed: {type(exc).__name__}', flush=True)
    return None


class TestRunFinalize:
    """One ingest lifetime; capabilities die on close/disable/server restart.

    HMAC capabilities bind project/run/session/enrolling process. No token or
    signing key is persisted, no unbounded grant table, and no timer can expire
    a still-running launcher. Every use also checks current policy and identity.
    Only enrollment issues a capability; reporter enrollment cannot recover it.
    """

    __test__ = False

    def __init__(self, ingest: TestRunIngest):
        self.ingest = ingest
        self._key: bytes | None = secrets.token_bytes(32)

    def close(self) -> None:
        with self.ingest._lock:
            self._key = None

    def _token(self, run: dict[str, Any]) -> str:
        if self._key is None:
            raise IngestRefused('Launcher capability unavailable')
        scope = json.dumps(['launcher-finalize-v1', run['project_id'], run['run_id'],
                            run['owner_session_id'], run['process_identity']],
                           separators=(',', ':')).encode()
        return hmac.new(self._key, scope, hashlib.sha256).hexdigest()

    def _owner(self, project_id: str, caller: Any) -> OwnerBinding:
        owner = self.ingest._owner(project_id, caller)
        if (owner.session_id is None or not isinstance(owner.process_identity, str)
                or not 0 < len(owner.process_identity) <= 512):
            raise IngestRefused('Launcher identity unavailable')
        return owner

    def enroll(self, project_id: str, caller: Any, metadata: dict[str, Any], *,
               parent_run_id: str | None = None) -> tuple[
                   dict[str, Any], ProducerReceipt, FinalizeCapability]:
        with self.ingest._lock:
            owner = self._owner(project_id, caller)
            if self._key is None:
                raise IngestRefused('Launcher capability unavailable')
            run, reporter = self.ingest.enroll(project_id, caller, metadata,
                                               parent_run_id=parent_run_id)
            # The trusted resolver is called again by ingest. Fail closed if the
            # peer process changed during enrollment; never issue its capability.
            if (run['process_identity'] != owner.process_identity
                    or run['owner_session_id'] != owner.session_id):
                raise IngestRefused('Launcher identity changed')
            return run, reporter, FinalizeCapability(run['run_id'], self._token(run))

    def finalize(self, project_id: str, caller: Any, run_id: str, token: str,
                 result: dict[str, Any]) -> dict[str, Any]:
        with self.ingest._lock:
            owner = self._owner(project_id, caller)
            with runs._LOCK:
                record = self.ingest.store._read(project_id, run_id)
                run = record['enrollment']
                if (run['owner_session_id'] != owner.session_id
                        or run['process_identity'] != owner.process_identity
                        or not isinstance(token, str) or len(token) != 64 or not token.isascii()
                        or not hmac.compare_digest(self._token(run), token)):
                    raise IngestRefused('Launcher capability unavailable')
                payload = self._payload(run['process_identity'], result)
                if record['snapshot']['state'] in runs.TERMINAL_STATES:
                    prior = next((e for e in record['events']
                                  if e['producer_id'] == runs._PROCESS_PRODUCER), None)
                    if prior is None or prior['payload'] != payload:
                        raise FinalizeConflict('Conflicting process exit evidence')
                    return copy.deepcopy(record['snapshot'])
                return self.ingest.store.finish(
                    project_id, run_id, state=payload['state'], exit_code=payload['exit_code'],
                    observed_at=self.ingest._clock(), evidence=payload['evidence'])

    @staticmethod
    def _payload(identity: str, result: dict[str, Any]) -> dict[str, Any]:
        if (not isinstance(result, dict) or set(result) != {'exit_code', 'signal', 'termination'}):
            raise ValueError('Invalid launcher result')
        code, signal, termination = result['exit_code'], result['signal'], result['termination']
        if (type(code) is not int or not -(2**31) <= code <= 2**32 - 1
                or termination not in ('exited', 'signalled', 'cancelled', 'timeout')
                or (signal is not None and (type(signal) is not int or not 1 <= signal <= 255))
                or (termination == 'exited' and (signal is not None or code < 0))
                or (termination == 'signalled' and signal is None)
                or (termination != 'exited' and code == 0)):
            raise ValueError('Invalid launcher result')
        verdict = (termination if termination in ('cancelled', 'timeout')
                   else 'passed' if code == 0 else 'failed')
        return {'state': verdict, 'exit_code': code,
                'evidence': {'source': 'launcher_observed', 'process_identity': identity,
                             'signal': signal, 'termination': termination}}
