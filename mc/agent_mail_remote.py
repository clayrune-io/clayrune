"""Production remote adapters that never answer (MC-1166 unit 7d-1), and their one selector (7d-2d).

``UnavailableRemote`` stays the default: every remote operation raises a closed code; the
lifecycle's existing retry queue then retains the work
(``mail_enrollment_revocations.done`` stays 0) and blocks a new generation. Nothing here records,
returns or fabricates an acknowledgment, makes a network call, reads a key or generates one.

The real lifecycle client (``agent_mail_service_client.ServiceClient``) is chosen only by
``select_remote`` below, and only when its config flag AND evidence that the Worker lifecycle
route answers with a verified signature are both present.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from mc.agent_mail_enrollment import OwnershipReceipt, PendingIdentity
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_revocations import CancelledAssignment

REMOTE_UNAVAILABLE = 'remote_adapter_unavailable'
LIFECYCLE_FLAG = 'agent_mail_lifecycle_enabled'


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

    def post(self, origin: str, path: str, headers: Mapping[str, str], body: bytes) -> dict[str, Any]:
        """Lifecycle transport seam (7d-2d): status/headers/body of the raw reply."""
        raise RemoteUnavailable(REMOTE_UNAVAILABLE)


def select_remote(config: Mapping[str, Any], *, client: Any,
                  lifecycle_evidence: Callable[[], bool]) -> Any:
    """The real client only when the flag is exactly True, a client exists, and `lifecycle_evidence()`
    is exactly True (a verified manifest and a signature-verified Worker answer that the lifecycle
    route is enabled). Anything else, including an evidence probe that raises, is the unavailable
    adapter: it can never acknowledge."""
    if config.get(LIFECYCLE_FLAG) is not True or client is None:
        return UnavailableRemote()
    try:
        proven = lifecycle_evidence() is True
    except Exception:
        proven = False
    return client if proven else UnavailableRemote()


def unavailable_material() -> tuple[bytes, bytes]:
    """No production key generation exists; a human ceremony must supply one."""
    raise EnrollmentRefused('key_material_unavailable')
