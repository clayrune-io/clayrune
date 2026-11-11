"""Python-built ceremony vectors for the mail Worker (MC-1166 7d-2e-2, contract 2.8).

`build_vectors()` produces, with fixed seeds and public material only:
  * a canonical enrollment transcript R, and Ed25519 device + mail proofs over its bytes
    (workers/agent-mail/test/enrollment-http.test.js verifies both with the Worker's `verifyProof`);
  * one service-signed 200 per ceremony path (begin / confirm / complete) in the ceremony envelope,
    install_id := flow_id and generation := 1 (the Worker verifies each with `verify`, and the test
    below verifies each with the real Python `verify_response`).

Regenerate the checked-in file with:

    python tests/test_agent_mail_u7d2e_ceremony_vectors.py
"""
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from test_agent_mail_enrollment import NOW, ORIGIN, OWNER
from test_agent_mail_u7d2_lifecycle import response
from test_agent_mail_u7d2_proof import MAIL, ROUTE_ORIGIN, SVC_PUBLIC
from mc.agent_mail_enrollment_keys import b64
from mc.agent_mail_service_client import ServiceRefused, verify_response
from mc.agent_mail_service_manifest import ServiceKeyCandidate

VECTORS = (Path(__file__).resolve().parents[1] / 'workers' / 'agent-mail' / 'test' / 'fixtures'
           / 'ceremony-vectors.json')
DEVICE = Ed25519PrivateKey.from_private_bytes(bytes(range(96, 128)))
FLOW = 'flow1'
TUNNEL = '12345678-1234-1234-1234-123456789abc'
NONCE = 'cd' * 16
PATHS = {name: f'/v1/mail/enrollment/{name}' for name in ('begin', 'confirm', 'complete')}


def canonical(r):
    """The exact bytes both sides sign: ASCII compact JSON array (enrollment-transcript.js canonicalR)."""
    return json.dumps(r, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def transcript():
    return ['clayrune-mail-enrollment', '1', ORIGIN, FLOW, 'challenge1', 'cd' * 32, str(NOW + 900_000), 'install1',
            '0', 'device1', b64(DEVICE.public_key().public_bytes_raw()), 'mail_flow1',
            b64(MAIL.public_key().public_bytes_raw()), hashlib.sha256(OWNER.encode()).hexdigest(), TUNNEL,
            ROUTE_ORIGIN, '/api/agent-mail/ingress', '1', 'service1', 'b' * 64]


def build_vectors():
    r = transcript()
    raw = canonical(r)
    bodies = {
        'begin': {'flow_id': FLOW, 'install_id': 'install1', 'expected_generation': 0, 'transcript': r,
                  'state': 'pending', 'code_sent': True},
        'confirm': {'flow_id': FLOW, 'confirmed': True, 'state': 'pending'},
        'complete': {'flow_id': FLOW, 'install_id': 'install1', 'generation': 1,
                     'state': 'ownership_recorded_delivery_pending', 'active': False, 'route_verified': False,
                     'ownership_expires_ms': NOW + 300_000},
    }
    replies = {}
    for name, body in bodies.items():
        got = response(body, path=PATHS[name], install=FLOW, generation=1, nonce=NONCE)
        h = got['headers']
        replies[name] = {'path': PATHS[name], 'body': got['body'].decode(), 'direction': h['x-mail-direction'],
                         'key_id': h['x-mail-key-id'], 'timestamp': h['x-mail-timestamp'], 'nonce': h['x-mail-nonce'],
                         'digest': h['x-mail-digest'], 'signature': h['x-mail-signature']}
    return {'_meta': 'Python-built by tests/test_agent_mail_u7d2e_ceremony_vectors.py; fixed seeds; public material only.',
            'now_ms': NOW, 'flow_id': FLOW, 'generation': 1, 'r': r, 'canonical_sha256': hashlib.sha256(raw).hexdigest(),
            'device_public_key': r[10], 'mail_public_key': r[12],
            'device_signature': b64(DEVICE.sign(raw)), 'mail_signature': b64(MAIL.sign(raw)),
            'service_public_key': SVC_PUBLIC, 'replies': replies}


def render():
    return json.dumps(build_vectors(), indent=1, sort_keys=True) + '\n'


def test_checked_in_ceremony_vectors_match_what_python_builds():
    assert VECTORS.read_text(encoding='utf-8').replace('\r\n', '\n') == render()


def _candidate():
    return ServiceKeyCandidate(ORIGIN, 1, 'service1', SVC_PUBLIC, '0' * 64, (), NOW + 1)


@pytest.mark.parametrize('name', ['begin', 'confirm', 'complete'])
def test_python_verifies_each_ceremony_reply_with_flow_id_as_install_and_generation_one(name):
    v = json.loads(VECTORS.read_text(encoding='utf-8'))['replies'][name]
    headers = {'x-mail-version': '1', 'x-mail-direction': v['direction'], 'x-mail-key-id': v['key_id'],
               'x-mail-install-id': FLOW, 'x-mail-generation': '1', 'x-mail-timestamp': v['timestamp'],
               'x-mail-nonce': v['nonce'], 'x-mail-digest': v['digest'], 'x-mail-signature': v['signature']}
    reply = {'status': 200, 'headers': headers, 'body': v['body'].encode()}
    def check(*, install=FLOW, generation=1, path=v['path'], nonce=v['nonce']):
        return verify_response(reply, candidate=_candidate(), install=install, generation=generation, path=path,
                               nonce=nonce, clock_ms=lambda: NOW)
    assert check()['flow_id'] == FLOW
    # the envelope is bound to the path, the nonce and the flow id: any other scope is refused
    other = PATHS['begin'] if name != 'begin' else PATHS['confirm']
    for bad in ({'path': other}, {'nonce': 'ef' * 16}, {'install': 'install1'}, {'generation': 2}):
        with pytest.raises(ServiceRefused):
            check(**bad)


def test_python_proofs_verify_over_the_canonical_transcript():
    v = json.loads(VECTORS.read_text(encoding='utf-8'))
    raw = canonical(v['r'])
    DEVICE.public_key().verify(base64.urlsafe_b64decode(v['device_signature'] + '=='), raw)
    MAIL.public_key().verify(base64.urlsafe_b64decode(v['mail_signature'] + '=='), raw)


if __name__ == '__main__':
    VECTORS.write_text(render(), encoding='utf-8', newline='\n')
    print(f'wrote {VECTORS}')
