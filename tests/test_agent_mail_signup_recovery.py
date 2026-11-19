"""Signup replacements parked until the separately reviewed v2 migration."""
import pytest

from mc.agent_mail_activation import MAX_ATTEMPTS
from mc.agent_mail_signup import TTL
from mc.blueprints.agent_mail_enrollment_routes import BASE
from test_agent_mail_enrollment_routes import rig  # noqa: F401


class SignupReplacementUnavailable(AssertionError):
    """Expected only at the final replacement assertion, after setup succeeds."""


needs_selection_migration = pytest.mark.xfail(
    strict=True, raises=SignupReplacementUnavailable,
    reason='Requires the v2 identity selection and retention migration in AGENT_MAIL_PULL_CONTRACT section 13.2',
)


def assert_replaced(original, retry, blocked):
    result = retry['state'], retry['reason']
    if result == blocked:
        raise SignupReplacementUnavailable(result)
    assert retry['state'] == 'pending' and retry['flow_id'] != original['flow_id']


@needs_selection_migration
def test_expired_signup_can_start_again(rig):
    original = rig.start().json
    assert original['state'] == 'pending'
    rig.h.now += TTL
    assert rig.client.get(BASE).json['signup']['state'] == 'expired'
    assert_replaced(original, rig.start().json, ('expired', 'flow_expired'))


@needs_selection_migration
def test_pending_signup_can_correct_owner_typo(rig):
    original = rig.post('start', owner='typo@example.com', consent_version='v1').json
    assert original['state'] == 'pending'
    assert_replaced(original, rig.start().json, ('held', 'operation_conflict'))


@needs_selection_migration
def test_exhausted_enable_allows_new_signup_without_extending_old_attempts(rig):
    original = rig.start().json
    assert original['state'] == 'pending'
    rig.verify()

    def lose_response():
        raise OSError('synthetic connection loss')

    rig.h.activation_after = lose_response
    for _ in range(MAX_ATTEMPTS):
        assert rig.post('enable').json['reason'] == 'connection_lost'
    assert rig.post('enable').json['reason'] == 'request_limit'
    assert len(rig.h.activation_calls) == MAX_ATTEMPTS
    assert_replaced(original, rig.start().json, ('held', 'request_limit'))
