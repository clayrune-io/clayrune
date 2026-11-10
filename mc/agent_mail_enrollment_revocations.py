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
        with self.store._read() as db:
            rows = [dict(r) for r in db.execute(
                'SELECT * FROM mail_enrollment_revocations WHERE done=0').fetchall()]
        for row in rows:
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
                if row['install'] is not None and assignment != CancelledAssignment(row['install'], row['generation']):
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
