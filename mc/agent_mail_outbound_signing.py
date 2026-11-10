"""Fresh install-to-service request signatures using the existing sealed key."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal

SEND_PATH = '/v1/mail/send'


def headers(seal: IdentitySeal, enrollment: dict[str, Any], raw: bytes, *,
            now_ms: int, nonce: str, direction: str = 'install-to-service',
            path: str = SEND_PATH) -> dict[str, str]:
    # `direction`/`path` default to the send route; 7d-2d lifecycle calls pass their own (each a
    # distinct signature domain, contract 4.1).
    if not re.fullmatch('[a-f0-9]{32}', nonce) or not re.fullmatch('[0-9]{13}', str(now_ms)):
        raise EnrollmentRefused('invalid_signature_fields')
    digest = hashlib.sha256(raw).hexdigest()
    values = ['clayrune-agent-mail', '1', direction, enrollment['key_id'],
              enrollment['install'], str(enrollment['generation']), 'POST', path,
              str(now_ms), nonce, digest]
    signature, _ = seal.prove(enrollment['flow'], enrollment['seal_identity'],
                              enrollment['seal_generation'], enrollment['key_id'],
                              json.dumps(values, separators=(',', ':')).encode())
    return dict(zip(('x-mail-version', 'x-mail-direction', 'x-mail-key-id', 'x-mail-install-id',
                     'x-mail-generation', 'x-mail-timestamp', 'x-mail-nonce', 'x-mail-digest'),
                    values[1:6] + values[8:11]), **{'x-mail-signature': signature,
                                                                  'Content-Type': 'application/json'})
