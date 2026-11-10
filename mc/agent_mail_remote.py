"""Production remote adapters that never answer (MC-1166 unit 7d-1).

The remote cancel/revoke/status/renewal protocol is unit 7d-2 work. Until it
exists, every remote operation raises a closed code; the lifecycle's existing
retry queue then retains the work (``mail_enrollment_revocations.done`` stays 0)
and blocks a new generation. Nothing here records, returns or fabricates an
acknowledgment, makes a network call, reads a key or generates one.
"""
from __future__ import annotations

from typing import Any

from mc.agent_mail_enrollment import OwnershipReceipt, PendingIdentity
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_revocations import CancelledAssignment

REMOTE_UNAVAILABLE = 'remote_adapter_unavailable'


class RemoteUnavailable(EnrollmentRefused):
    """Closed code; carries no remote text."""


class UnavailableRemote:
    """Enrollment/revocation adapter for an install with no remote protocol yet."""

    def _refuse(self) -> Any:
        raise RemoteUnavailable(REMOTE_UNAVAILABLE)

    def begin(self, context: dict, flow_secret: bytes) -> PendingIdentity:
        return self._refuse()

    def verify(self, context: dict, flow_secret: bytes, code: str) -> OwnershipReceipt:
        return self._refuse()

    def cancel_flow(self, flow: str) -> CancelledAssignment | None:
        return self._refuse()

    def revoke(self, install: str, generation: int) -> None:
        self._refuse()


class UnavailableTransport:
    """Signed-HTTP transport placeholder: the service client is unit 7d-2."""

    def send(self, origin: str, path: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        raise RemoteUnavailable(REMOTE_UNAVAILABLE)


def unavailable_material() -> tuple[bytes, bytes]:
    """No production key generation exists; a human ceremony must supply one."""
    raise EnrollmentRefused('key_material_unavailable')
