"""Composite enrollment adapter and its one selector (MC-1166 unit 7d-2e-3, contract 3.1 and 3.2).

`Enrollment` takes ONE adapter (`mc/agent_mail_enrollment.py`) but needs two authorities: the human
ceremony (`begin`, `verify`: `CeremonyClient`) and the lifecycle (`cancel_flow`, `revoke`: the
unchanged 7d-2d `ServiceClient`). `CompositeRemote` routes each call to its owner and does nothing
else: no state, no retry, no fallback from one half to the other.

`select_ceremony` is the only thing that builds one. It returns None, and the factory keeps whatever
remote it already had (`UnavailableRemote`, byte for byte today's behaviour), unless BOTH
`agent_mail_ceremony_enabled` and `agent_mail_enrollment_enabled` are exactly True and the reviewed
deployment config is complete. A missing grant origin, an unusable origin or a construction failure
also yields None: the ceremony fails closed, it never half-starts.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from mc.agent_mail_ceremony_client import CeremonyClient
from mc.agent_mail_ceremony_device import DeviceIdentity
from mc.agent_mail_enrollment import OwnershipReceipt, PendingIdentity
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_enrollment_revocations import CancelledAssignment
from mc.agent_mail_lifecycle_fence import LifecycleFences
from mc.agent_mail_service_client import ServiceClient
from mc.agent_mail_store import MailStore

CEREMONY_FLAG = 'agent_mail_ceremony_enabled'
ENROLLMENT_FLAG = 'agent_mail_enrollment_enabled'
GRANT_ORIGIN_KEY = 'agent_mail_grant_origin'   # reviewed config: the control plane origin that mints grants


class CompositeRemote:
    """`begin`/`verify` -> the ceremony client; `cancel_flow`/`revoke` -> the lifecycle client."""

    def __init__(self, lifecycle: Any, ceremony: Any):
        self.lifecycle, self.ceremony = lifecycle, ceremony

    def begin(self, context: dict, flow_secret: bytes) -> PendingIdentity:
        return self.ceremony.begin(context, flow_secret)

    def verify(self, context: dict, flow_secret: bytes, code: str) -> OwnershipReceipt:
        return self.ceremony.verify(context, flow_secret, code)

    def cancel_flow(self, flow: str) -> CancelledAssignment | None:
        return self.lifecycle.cancel_flow(flow)

    def revoke(self, install: str, generation: int) -> None:
        self.lifecycle.revoke(install, generation)


def ceremony_enabled(config: Mapping[str, Any]) -> bool:
    """Both flags exactly True (an absent or truthy-but-not-True value is off)."""
    return config.get(CEREMONY_FLAG) is True and config.get(ENROLLMENT_FLAG) is True


def select_ceremony(config: Mapping[str, Any], *, store: MailStore, seal: IdentitySeal, fences: LifecycleFences,
                    transport: Any, root_public_key: str, service_origin: str,
                    device: Callable[[], DeviceIdentity], clock: Callable[[], float]) -> CompositeRemote | None:
    """A `CompositeRemote`, or None when the ceremony is off or its deployment config is unusable."""
    if not ceremony_enabled(config):
        return None
    grant_origin = config.get(GRANT_ORIGIN_KEY)
    if not isinstance(grant_origin, str) or not grant_origin:
        return None
    try:
        lifecycle = ServiceClient(store, seal, fences=fences, transport=transport, root_public_key=root_public_key,
                                  service_origin=service_origin, clock=clock)
        ceremony = CeremonyClient(store, seal, fences=fences, transport=transport, root_public_key=root_public_key,
                                  service_origin=service_origin, grant_origin=grant_origin, device=device,
                                  enabled=True, clock=clock)
    except EnrollmentRefused:
        return None
    return CompositeRemote(lifecycle, ceremony)
