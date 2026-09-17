"""Offline execution policy contract; this module certifies NO real adapter.

API: trusted composition code supplies RequestedEngine + ExecutionIdentity,
fresh Readiness and Certification records, then calls authorize_execution BEFORE
passing sensitive input or launching. None means unresolved; '' explicitly asks
for the provider's native model/effort default. No vendor fallback is performed.

Certification records must come from a trusted, versioned canary-test runner,
not model output or an agent-authored capability declaration. The remaining
runner must actually enforce the profile, bound input/output/time, isolate
tools/MCP/hooks/plugins/config, verify identity immediately before launch and
input delivery, and handle revocation. A permit is a bound decision snapshot,
not an enforcement sandbox or a transferable permission token.

Only opaque account references and configuration fingerprints belong here;
never credentials, raw configuration, prompts, environment dumps or auth tokens.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re


class Profile(str, Enum):
    INTERACTIVE = 'interactive'
    UNATTENDED = 'unattended'
    TOOL_FREE_TRANSFORM = 'tool_free_transform'


class Support(str, Enum):
    SUPPORTED = 'supported'
    UNSUPPORTED = 'unsupported'
    UNVERIFIED = 'unverified'


class Capability(str, Enum):
    PERMISSION_ENFORCEMENT = 'permission_enforcement'
    PROJECT_BOUNDARY = 'project_boundary'
    UNTRUSTED_INPUT_BOUNDARY = 'untrusted_input_boundary'
    BOUNDED_EXECUTION = 'bounded_execution'
    UNATTENDED_RESTRICTIONS = 'unattended_restrictions'
    NO_PRIVILEGE_ESCALATION = 'no_privilege_escalation'
    TOOL_FREE = 'tool_free'
    MCP_DISABLED = 'mcp_disabled'
    HOOKS_DISABLED = 'hooks_disabled'
    PLUGINS_DISABLED = 'plugins_disabled'
    INHERITED_CONFIG_ISOLATED = 'inherited_config_isolated'
    EFFORT_SELECTION = 'effort_selection'


class BlockerKind(str, Enum):
    AUTH = 'auth'
    QUOTA = 'quota'
    UNSUPPORTED = 'unsupported'


class BlockerScope(str, Enum):
    PROVIDER = 'provider'
    ACCOUNT = 'account'


def _text(value: object, field: str, *, empty: bool = False) -> None:
    if not isinstance(value, str) or (not empty and not value) or value != value.strip():
        raise ValueError(f'{field} must be a canonical string')


def _time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timestamps must be timezone-aware')


@dataclass(frozen=True)
class RequestedEngine:
    provider: str
    model: str | None
    effort: str | None
    account_ref: str

    def __post_init__(self) -> None:
        _text(self.provider, 'provider')
        _text(self.account_ref, 'account_ref')
        if self.model is not None:
            _text(self.model, 'model', empty=True)
        if self.effort is not None:
            _text(self.effort, 'effort', empty=True)


@dataclass(frozen=True)
class ExecutionIdentity:
    engine: RequestedEngine
    cli_version: str
    platform: str
    config_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.engine, RequestedEngine):
            raise ValueError('engine must be RequestedEngine')
        _text(self.cli_version, 'cli_version')
        _text(self.platform, 'platform')
        if not isinstance(self.config_fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', self.config_fingerprint):
            raise ValueError('config_fingerprint must be a SHA-256 hex digest')


@dataclass(frozen=True)
class CapabilityClaim:
    capability: Capability
    support: Support

    def __post_init__(self) -> None:
        if not isinstance(self.capability, Capability) or not isinstance(self.support, Support):
            raise ValueError('capability claims require known capability and tri-state support enums')


@dataclass(frozen=True)
class Certification:
    identity: ExecutionIdentity
    profile: Profile
    claims: tuple[CapabilityClaim, ...]
    evidence_id: str
    verified_by: str
    verified_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionIdentity) or not isinstance(self.profile, Profile):
            raise ValueError('certification requires exact identity and operation profile')
        if not isinstance(self.claims, tuple) or not all(isinstance(c, CapabilityClaim) for c in self.claims):
            raise ValueError('claims must be an immutable tuple of CapabilityClaim')
        if len({c.capability for c in self.claims}) != len(self.claims):
            raise ValueError('duplicate capability claims')
        _text(self.evidence_id, 'evidence_id')
        _text(self.verified_by, 'verified_by')
        _time(self.verified_at)
        _time(self.expires_at)
        if self.expires_at <= self.verified_at:
            raise ValueError('certification must have a positive validity interval')

    def support_for(self, capability: Capability) -> Support:
        return next((c.support for c in self.claims if c.capability == capability), Support.UNVERIFIED)


@dataclass(frozen=True)
class Readiness:
    identity: ExecutionIdentity
    cli_installed: Support
    authenticated: Support
    checked_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ExecutionIdentity):
            raise ValueError('readiness requires exact identity')
        if not isinstance(self.cli_installed, Support) or not isinstance(self.authenticated, Support):
            raise ValueError('readiness states must be tri-state support enums')
        _time(self.checked_at)
        _time(self.expires_at)
        if self.expires_at <= self.checked_at:
            raise ValueError('readiness must have a positive validity interval')


@dataclass(frozen=True)
class Blocker:
    kind: BlockerKind
    provider: str
    scope: BlockerScope
    reason: str
    account_ref: str | None = None
    known_reset_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BlockerKind) or not isinstance(self.scope, BlockerScope):
            raise ValueError('blocker requires normalized kind and scope')
        _text(self.provider, 'provider')
        _text(self.reason, 'reason')
        if self.scope == BlockerScope.ACCOUNT:
            _text(self.account_ref, 'account_ref')
        elif self.account_ref is not None:
            raise ValueError('provider-wide blockers cannot name a single account')
        if self.known_reset_at is not None:
            _time(self.known_reset_at)

    def applies_to(self, engine: RequestedEngine) -> bool:
        return self.provider == engine.provider and (
            self.scope == BlockerScope.PROVIDER or self.account_ref == engine.account_ref)


class PolicyError(RuntimeError):
    """Per-call structured failure; there is deliberately no shared last_error."""


class UnresolvedEngine(PolicyError):
    pass


class EvidenceRejected(PolicyError):
    pass


class ReadinessRejected(PolicyError):
    pass


class CapabilityRejected(PolicyError):
    def __init__(self, capability: Capability, support: Support):
        self.capability = capability
        self.support = support
        super().__init__(f'{capability.value}: {support.value}; execution is unavailable')


class ExecutionBlocked(PolicyError):
    def __init__(self, blocker: Blocker):
        self.blocker = blocker
        super().__init__(f'{blocker.kind.value}: {blocker.reason}')


def required_capabilities(profile: Profile) -> frozenset[Capability]:
    if not isinstance(profile, Profile):
        raise ValueError('unknown execution profile')
    common = frozenset({Capability.UNTRUSTED_INPUT_BOUNDARY, Capability.BOUNDED_EXECUTION})
    if profile == Profile.TOOL_FREE_TRANSFORM:
        return common | frozenset({Capability.TOOL_FREE, Capability.MCP_DISABLED,
                                  Capability.HOOKS_DISABLED, Capability.PLUGINS_DISABLED,
                                  Capability.INHERITED_CONFIG_ISOLATED})
    interactive = common | frozenset({Capability.PERMISSION_ENFORCEMENT, Capability.PROJECT_BOUNDARY})
    if profile == Profile.UNATTENDED:
        return interactive | frozenset({Capability.UNATTENDED_RESTRICTIONS,
                                       Capability.NO_PRIVILEGE_ESCALATION})
    return interactive


@dataclass(frozen=True)
class ExecutionPermit:
    identity: ExecutionIdentity
    profile: Profile
    evidence_id: str
    issued_at: datetime
    expires_at: datetime


def authorize_execution(
    identity: ExecutionIdentity, profile: Profile, *, readiness: Readiness | None,
    certification: Certification | None, now: datetime,
    blockers: tuple[Blocker, ...] = (), required: frozenset[Capability] = frozenset(),
) -> ExecutionPermit:
    """Fail closed before input/launch; supplied active blockers need explicit clearing.

    A known quota reset is informational, not proof quota/auth recovered. This
    gate never invents a reset, changes accounts, selects models or probes CLIs.
    Fresh revalidation is required if environment, auth or blocker state changes.
    """
    _time(now)
    if not isinstance(identity, ExecutionIdentity):
        raise ValueError('identity must be ExecutionIdentity')
    caps = required_capabilities(profile)
    if not isinstance(required, frozenset) or not all(isinstance(c, Capability) for c in required):
        raise ValueError('additional requirements must be known capability enums')
    if not isinstance(blockers, tuple) or not all(isinstance(b, Blocker) for b in blockers):
        raise ValueError('blockers must be an immutable tuple of Blocker')
    if identity.engine.model is None or identity.engine.effort is None:
        raise UnresolvedEngine('model/effort omitted: resolve intent before execution; empty means native default')
    for blocker in blockers:
        if blocker.applies_to(identity.engine):
            raise ExecutionBlocked(blocker)
    if not isinstance(readiness, Readiness) or readiness.identity != identity:
        raise ReadinessRejected('missing or mismatched readiness snapshot')
    if not readiness.checked_at <= now < readiness.expires_at:
        raise ReadinessRejected('readiness is stale or future-dated')
    if readiness.cli_installed != Support.SUPPORTED:
        raise ReadinessRejected(f'CLI installation: {readiness.cli_installed.value}')
    if readiness.authenticated != Support.SUPPORTED:
        raise ExecutionBlocked(Blocker(BlockerKind.AUTH, identity.engine.provider,
            BlockerScope.ACCOUNT, f'authentication: {readiness.authenticated.value}', identity.engine.account_ref))
    if not isinstance(certification, Certification) or certification.identity != identity or certification.profile != profile:
        raise EvidenceRejected('missing or mismatched profile/environment certification')
    if not certification.verified_at <= now < certification.expires_at:
        raise EvidenceRejected('certification is stale or future-dated')
    caps |= required
    if identity.engine.effort:
        caps |= frozenset({Capability.EFFORT_SELECTION})
    for capability in sorted(caps, key=lambda c: c.value):
        support = certification.support_for(capability)
        if support != Support.SUPPORTED:
            raise CapabilityRejected(capability, support)
    return ExecutionPermit(identity, profile, certification.evidence_id, now,
                           min(readiness.expires_at, certification.expires_at))


def validate_permit(permit: ExecutionPermit, identity: ExecutionIdentity,
                    profile: Profile, *, now: datetime) -> None:
    """Check binding at use; does NOT replace fresh auth/revocation checks."""
    _time(now)
    if not isinstance(permit, ExecutionPermit):
        raise EvidenceRejected('missing execution permit')
    if (not isinstance(identity, ExecutionIdentity) or not isinstance(profile, Profile)
            or permit.identity != identity or permit.profile != profile):
        raise EvidenceRejected('execution changed after authorization')
    if not permit.issued_at <= now < permit.expires_at:
        raise EvidenceRejected('execution permit expired or future-dated')
