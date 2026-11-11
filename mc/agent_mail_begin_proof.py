"""Device possession for enrollment begin; canonical bytes shared with the Worker."""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

from mc.agent_mail_enrollment_keys import EnrollmentRefused

BEGIN_FIELDS = ('flow_id', 'owner_address', 'device_id', 'device_public_key', 'mail_key_id',
                'mail_public_key', 'tunnel_uuid', 'route_origin', 'manifest_sequence',
                'service_key_id', 'service_public_key_sha256')
DESTINATION_FAILURES = frozenset({'destination_unverified', 'destination_unavailable',
                                'destination_capacity', 'destination_rate_limited',
                                'destination_pending', 'destination_expired'})


class DestinationRefused(EnrollmentRefused):
    """An authenticated closed retry hint, never an ownership acknowledgment."""

    def __init__(self, code: str):
        super().__init__(code if code in DESTINATION_FAILURES else 'destination_unavailable')


def canonical_begin(value: Mapping[str, str], secret: str, service: str,
                    timestamp: str, nonce: str) -> bytes:
    return json.dumps(['clayrune-mail-enrollment-begin', '1', service, timestamp, nonce,
                       hashlib.sha256(secret.encode('ascii')).hexdigest(),
                       *(value[k] for k in BEGIN_FIELDS), value.get('fence_digest', '')],
                      separators=(',', ':'), ensure_ascii=True).encode('ascii')
