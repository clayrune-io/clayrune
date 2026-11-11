"""Flow-keyed cancellation fences and durable service-assignment reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_signing import ID
from mc.agent_mail_store import MailStore


@dataclass(frozen=True)
class CancelledAssignment:
    install: str
    generation: int


class RevocationAdapter(Protocol):
    def cancel_flow(self, flow: str) -> CancelledAssignment | None:
        """Durably fence this flow, including all in-flight/delayed begin/verify.

        Return its actual authenticated assignment, or None only if none was
        ever assigned. Idempotent retries return the SAME result, even after
        identity revocation. No key/vault access is required to cancel.
        """
        ...

    def revoke(self, install: str, generation: int) -> None: ...


class Revocations:
    def __init__(self, store: MailStore, adapter: RevocationAdapter):
        self.store, self.adapter = store, adapter

    @staticmethod
    def queue(db, row: dict) -> None:
        # An unassigned local ID is a placeholder, NEVER a remote identity.
        db.execute('INSERT OR IGNORE INTO mail_enrollment_revocations '
                   '(flow,install,generation) VALUES(?,?,?)',
                   (row['flow'], row['install'] if row['assigned'] else None,
                    row['generation'] if row['assigned'] else None))

    def retry(self) -> None:
        """Every unfinished row is attempted; the FIRST failure is raised after the rest ran, so one
        failing item never blocks the others (7d-1 supervisor contract, 7d-2d contract 5.5)."""
        with self.store._read() as db:
            rows = [dict(r) for r in db.execute(
                'SELECT * FROM mail_enrollment_revocations WHERE done=0').fetchall()]
        first: EnrollmentRefused | None = None
        for row in rows:
            try:
                self._retry_one(row)
            except EnrollmentRefused as exc:
                first = first or exc
        if first is not None:
            raise first

    def _retry_one(self, row: dict) -> None:
        if not row['resolved']:
            try:
                assignment = self.adapter.cancel_flow(row['flow'])
            except Exception:
                raise EnrollmentRefused('remote_cancel_retryable') from None
            if assignment is not None and (
                    not isinstance(assignment, CancelledAssignment)
                    or not isinstance(assignment.install, str) or not ID.fullmatch(assignment.install)
                    or type(assignment.generation) is not int or assignment.generation < 1):
                raise EnrollmentRefused('remote_cancel_scope_mismatch')
            # `assignment is None` is the Worker saying no commit exists for this flow, and the fence the
            # cancel just wrote makes a later commit impossible (7d-2e decision E6): resolve the row with
            # no remote revoke. A NON-null assignment must still equal what was queued, exactly.
            if (assignment is not None and row['install'] is not None
                    and assignment != CancelledAssignment(row['install'], row['generation'])):
                raise EnrollmentRefused('remote_cancel_scope_mismatch')
            install = assignment.install if assignment is not None else None
            generation = assignment.generation if assignment is not None else None
            with self.store._tx() as db:
                current = db.execute('SELECT * FROM mail_enrollment_revocations WHERE flow=?',
                                     (row['flow'],)).fetchone()
                if current is None:
                    raise EnrollmentRefused('lifecycle_state_corrupt')
                if current['resolved']:
                    if (current['install'], current['generation']) != (install, generation):
                        raise EnrollmentRefused('remote_cancel_scope_mismatch')
                else:
                    db.execute('UPDATE mail_enrollment_revocations SET install=?,generation=?,resolved=1 WHERE flow=?',
                               (install, generation, row['flow']))
            row['install'], row['generation'] = install, generation
        install, generation = row['install'], row['generation']
        if install is None:
            if generation is not None:
                raise EnrollmentRefused('lifecycle_state_corrupt')
        else:
            if (not isinstance(install, str) or not ID.fullmatch(install)
                    or type(generation) is not int or generation < 1):
                raise EnrollmentRefused('lifecycle_state_corrupt')
            try:
                self.adapter.revoke(install, generation)
            except Exception:
                raise EnrollmentRefused('remote_revoke_retryable') from None
        with self.store._tx() as db:
            db.execute('UPDATE mail_enrollment_revocations SET done=1 WHERE flow=? AND resolved=1',
                       (row['flow'],))
