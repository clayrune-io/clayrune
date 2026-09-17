"""Synthetic evidence only: passing these tests certifies no real provider."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from mc.execution_policy import (
    Blocker, BlockerKind, BlockerScope, Capability, CapabilityClaim,
    CapabilityRejected, Certification, EvidenceRejected, ExecutionBlocked,
    ExecutionIdentity, Profile, Readiness, ReadinessRejected, RequestedEngine,
    Support, UnresolvedEngine, authorize_execution, required_capabilities,
    validate_permit,
)


NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
BEFORE = NOW - timedelta(minutes=1)
AFTER = NOW + timedelta(minutes=5)


def identity(**engine_changes):
    engine = RequestedEngine('test-provider', '', '', 'local-account-1')
    return ExecutionIdentity(replace(engine, **engine_changes), 'test-cli-1', 'test-platform', 'a' * 64)


def records(target=None, profile=Profile.INTERACTIVE):
    target = target or identity()
    ready = Readiness(target, Support.SUPPORTED, Support.SUPPORTED, BEFORE, AFTER)
    cert = Certification(target, profile, tuple(CapabilityClaim(c, Support.SUPPORTED) for c in Capability),
                         'synthetic-test-evidence', 'offline-test-runner', BEFORE, AFTER)
    return ready, cert


def authorize(target=None, profile=Profile.INTERACTIVE, **kwargs):
    target = target or identity()
    ready, cert = records(target, profile)
    return authorize_execution(target, profile, readiness=kwargs.pop('readiness', ready),
        certification=kwargs.pop('certification', cert), now=kwargs.pop('now', NOW), **kwargs)


@pytest.mark.parametrize('profile', list(Profile))
def test_exact_valid_evidence_permits_only_requested_profile(profile):
    target = identity()
    permit = authorize(target, profile)
    assert permit.identity is target
    assert permit.profile == profile
    assert permit.evidence_id == 'synthetic-test-evidence'
    validate_permit(permit, target, profile, now=NOW)


def test_engine_is_immutable_and_native_default_is_not_omission():
    engine = identity().engine
    with pytest.raises(FrozenInstanceError):
        engine.model = 'changed'
    assert engine.model == engine.effort == ''
    assert engine != replace(engine, model=None)
    assert engine != replace(engine, effort=None)


@pytest.mark.parametrize('field', ['model', 'effort'])
def test_omitted_choice_must_be_resolved_before_authorization(field):
    with pytest.raises(UnresolvedEngine):
        authorize(identity(**{field: None}))


@pytest.mark.parametrize('change', [
    {'cli_version': 'test-cli-2'}, {'platform': 'other-platform'},
    {'config_fingerprint': 'b' * 64},
    {'engine': identity(provider='other-provider').engine},
    {'engine': identity(model='other-model').engine},
    {'engine': identity(effort='high').engine},
    {'engine': identity(account_ref='other-account').engine},
])
def test_every_identity_component_invalidates_certification(change):
    old = identity()
    changed = replace(old, **change)
    _, old_cert = records(old)
    with pytest.raises(EvidenceRejected):
        authorize(changed, certification=old_cert)


def test_profile_cannot_reuse_interactive_evidence_for_unattended_or_transform():
    _, cert = records()
    for profile in (Profile.UNATTENDED, Profile.TOOL_FREE_TRANSFORM):
        with pytest.raises(EvidenceRejected):
            authorize(profile=profile, certification=cert)


@pytest.mark.parametrize('missing', ['readiness', 'certification'])
def test_no_evidence_no_execution(missing):
    error = ReadinessRejected if missing == 'readiness' else EvidenceRejected
    with pytest.raises(error):
        authorize(**{missing: None})


@pytest.mark.parametrize('support', [Support.UNSUPPORTED, Support.UNVERIFIED, None])
def test_missing_or_negative_required_capability_fails_closed(support):
    ready, cert = records()
    cap = Capability.PERMISSION_ENFORCEMENT
    claims = tuple(c for c in cert.claims if c.capability != cap)
    if support is not None:
        claims += (CapabilityClaim(cap, support),)
    cert = replace(cert, claims=claims)
    with pytest.raises(CapabilityRejected) as exc:
        authorize(readiness=ready, certification=cert)
    assert exc.value.capability == cap
    assert exc.value.support == (support or Support.UNVERIFIED)


def test_unknown_capability_or_support_cannot_be_interpreted_as_true():
    with pytest.raises(ValueError):
        CapabilityClaim('unknown', Support.SUPPORTED)
    with pytest.raises(ValueError):
        CapabilityClaim(Capability.TOOL_FREE, True)
    with pytest.raises(ValueError):
        authorize(required=frozenset({'unknown'}))
    with pytest.raises(ValueError):
        authorize(profile='unknown')


def test_additional_requirements_can_strengthen_but_never_remove_profile_requirements():
    _, cert = records()
    cert = replace(cert, claims=(CapabilityClaim(Capability.TOOL_FREE, Support.SUPPORTED),))
    with pytest.raises(CapabilityRejected):
        authorize(certification=cert, required=frozenset({Capability.TOOL_FREE}))


def test_explicit_effort_requires_certified_effort_support():
    target = identity(effort='high')
    _, cert = records(target)
    cert = replace(cert, claims=tuple(c for c in cert.claims if c.capability != Capability.EFFORT_SELECTION))
    with pytest.raises(CapabilityRejected) as exc:
        authorize(target, certification=cert)
    assert exc.value.capability == Capability.EFFORT_SELECTION
    assert target.engine.effort == 'high'


def test_native_default_effort_does_not_claim_effort_control_support():
    _, cert = records()
    cert = replace(cert, claims=tuple(c for c in cert.claims if c.capability != Capability.EFFORT_SELECTION))
    assert authorize(certification=cert).identity.engine.effort == ''


@pytest.mark.parametrize('record_name', ['readiness', 'certification'])
@pytest.mark.parametrize('when', [BEFORE - timedelta(seconds=1), AFTER])
def test_future_and_expired_evidence_fail_closed(record_name, when):
    # Both records have the same validity interval; extend the other record to
    # isolate which one rejects the request, including expiration's exact edge.
    ready, cert = records()
    if record_name == 'readiness':
        cert = replace(cert, verified_at=NOW-timedelta(days=1), expires_at=NOW+timedelta(days=1))
        error = ReadinessRejected
    else:
        ready = replace(ready, checked_at=NOW-timedelta(days=1), expires_at=NOW+timedelta(days=1))
        error = EvidenceRejected
    with pytest.raises(error):
        authorize(readiness=ready, certification=cert, now=when)


def test_readiness_is_account_and_environment_bound():
    ready, _ = records(identity(account_ref='other'))
    with pytest.raises(ReadinessRejected):
        authorize(readiness=ready)


@pytest.mark.parametrize('state', [Support.UNSUPPORTED, Support.UNVERIFIED])
def test_installed_does_not_imply_authenticated(state):
    ready, _ = records()
    with pytest.raises(ExecutionBlocked) as exc:
        authorize(readiness=replace(ready, authenticated=state))
    assert exc.value.blocker.kind == BlockerKind.AUTH
    assert exc.value.blocker.scope == BlockerScope.ACCOUNT
    assert exc.value.blocker.account_ref == 'local-account-1'
    assert exc.value.blocker.known_reset_at is None


@pytest.mark.parametrize('kind', list(BlockerKind))
def test_normalized_blockers_apply_only_to_named_provider_and_account(kind):
    blocker = Blocker(kind, 'test-provider', BlockerScope.ACCOUNT, 'test reason', 'local-account-1')
    with pytest.raises(ExecutionBlocked) as exc:
        authorize(blockers=(blocker,))
    assert exc.value.blocker is blocker
    assert authorize(identity(account_ref='local-account-2'), blockers=(blocker,))
    assert authorize(identity(provider='another'), blockers=(blocker,))


def test_provider_blocker_applies_to_all_its_accounts_not_other_providers():
    blocker = Blocker(BlockerKind.UNSUPPORTED, 'test-provider', BlockerScope.PROVIDER, 'disabled')
    with pytest.raises(ExecutionBlocked):
        authorize(identity(account_ref='local-account-2'), blockers=(blocker,))
    assert authorize(identity(provider='another'), blockers=(blocker,))


def test_known_quota_reset_is_information_not_invented_recovery():
    blocker = Blocker(BlockerKind.QUOTA, 'test-provider', BlockerScope.ACCOUNT,
                      'quota reached', 'local-account-1', known_reset_at=BEFORE)
    with pytest.raises(ExecutionBlocked) as exc:
        authorize(blockers=(blocker,))
    assert exc.value.blocker.known_reset_at == BEFORE
    assert authorize(blockers=())  # only a later trusted probe clears the blocker


def test_permit_binding_and_shortest_lifetime():
    ready, _ = records()
    ready = replace(ready, expires_at=NOW+timedelta(seconds=2))
    permit = authorize(readiness=ready)
    assert permit.expires_at == ready.expires_at
    with pytest.raises(EvidenceRejected):
        validate_permit(permit, identity(), Profile.INTERACTIVE, now=permit.expires_at)
    with pytest.raises(EvidenceRejected):
        validate_permit(permit, identity(model='changed'), Profile.INTERACTIVE, now=NOW)
    with pytest.raises(EvidenceRejected):
        validate_permit(permit, identity(), Profile.UNATTENDED, now=NOW)


def test_profiles_define_distinct_safety_requirements():
    assert required_capabilities(Profile.INTERACTIVE) < required_capabilities(Profile.UNATTENDED)
    transform = required_capabilities(Profile.TOOL_FREE_TRANSFORM)
    assert {Capability.TOOL_FREE, Capability.MCP_DISABLED, Capability.HOOKS_DISABLED,
            Capability.PLUGINS_DISABLED, Capability.INHERITED_CONFIG_ISOLATED} <= transform


def test_records_reject_mutable_or_ambiguous_evidence():
    _, cert = records()
    with pytest.raises(ValueError):
        replace(cert, claims=list(cert.claims))
    with pytest.raises(ValueError):
        replace(cert, claims=cert.claims+(cert.claims[0],))
    with pytest.raises(ValueError):
        replace(cert, expires_at=cert.verified_at)
    with pytest.raises(ValueError):
        replace(cert, verified_at=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        replace(identity(), config_fingerprint='raw configuration must not be stored')


def test_failures_are_per_call_not_shared_last_error():
    errors = []
    for kind in (BlockerKind.AUTH, BlockerKind.QUOTA):
        try:
            authorize(blockers=(Blocker(kind, 'test-provider', BlockerScope.ACCOUNT, kind.value, 'local-account-1'),))
        except ExecutionBlocked as exc:
            errors.append(exc)
    assert errors[0] is not errors[1]
    assert errors[0].blocker.kind == BlockerKind.AUTH
    assert errors[1].blocker.kind == BlockerKind.QUOTA


def test_untyped_adapter_results_do_not_bypass_record_validation():
    with pytest.raises(ReadinessRejected):
        authorize(readiness={'authenticated': True})
    with pytest.raises(EvidenceRejected):
        authorize(certification={'supported': True})
    with pytest.raises(EvidenceRejected):
        validate_permit(authorize(), identity(), 'interactive', now=NOW)


def test_module_is_pure_and_does_not_activate_an_adapter():
    import ast
    import inspect
    import mc.execution_policy as policy
    tree = ast.parse(inspect.getsource(policy))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= {'__future__', 'dataclasses', 'datetime', 'enum', 're'}
    assert not hasattr(policy, 'last_error')
