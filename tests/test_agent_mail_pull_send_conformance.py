"""Worker unit 14a vectors signed by its real boundary, verified by Python.

No network, provider, database or deployment. Invalid bodies are deliberately
signed too: authentication must not bypass the section 7 receipt shape checks.
The fixtures remain independently owned; disagreement is a gate for unit 14.
"""
import base64
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from mc.agent_mail_service_client import ServiceRefused, verify_pull_response
from mc.agent_mail_service_manifest import ServiceKeyCandidate


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / 'workers' / 'agent-mail'
VECTORS = json.loads((WORKER / 'test' / 'fixtures' /
                     'pull-send-receipt-vectors.json').read_text(encoding='utf-8'))
NOW = 1_794_249_600_000
ORIGIN = 'https://mail.example.com'
INSTALL, NONCE = '1' * 32, '2' * 32
REQUEST_ID = '0000000000000000000000000000000a'

# Use the production response method rather than reproducing its transcript in
# Python. All dependencies are inert: a future accidental storage/provider call
# fails this fixture rather than silently acquiring a real capability.
SIGN_VECTORS = r"""
import { PullSendHTTP } from './pull-send-http.js';
import { base64url } from './signing.js';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const { fixture, now, origin, install, nonce } = JSON.parse(input);
const keys = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
const noIO = () => { throw new Error('conformance_must_not_perform_io'); };
const http = new PullSendHTTP({
  db: { prepare: noIO, batch: noIO }, serviceOrigin: origin,
  serviceKeyId: 'conformance-service',
  servicePrivateKey: base64url(await crypto.subtle.exportKey('pkcs8', keys.privateKey)),
  config: { from: 'agents@mail.example.net', domain: 'mail.example.net' },
  binding: { send: noIO }, clock: () => now,
});
const auth = { install_id: install, generation: '7', owner_generation: '3', nonce };
const responses = [];
for (const vector of fixture.vectors) {
  const response = await http.response(auth, fixture.path, vector.body, vector.status);
  responses.push({ name: vector.name, status: response.status,
    headers: Object.fromEntries(response.headers),
    body: Buffer.from(await response.arrayBuffer()).toString('base64') });
}
process.stdout.write(JSON.stringify({
  public_key: base64url(await crypto.subtle.exportKey('raw', keys.publicKey)), responses,
}));
"""


@pytest.fixture(scope='module')
def worker_responses():
    assert VECTORS['schema'] == 1 and VECTORS['path'] == '/v1/pull/send'
    vectors = VECTORS['vectors']
    assert len(vectors) == len({v['name'] for v in vectors}) == 35
    result = subprocess.run(
        ['node', '--input-type=module', '-e', SIGN_VECTORS], cwd=WORKER,
        input=json.dumps(dict(fixture=VECTORS, now=NOW, origin=ORIGIN,
                             install=INSTALL, nonce=NONCE)),
        text=True, capture_output=True, timeout=30, check=True,
    )
    signed = json.loads(result.stdout)
    candidate = ServiceKeyCandidate(ORIGIN, 1, 'conformance-service',
                                   signed['public_key'], 'f' * 64, (), NOW + 86_400_000)
    assert [r['name'] for r in signed['responses']] == [v['name'] for v in vectors]
    return candidate, {
        r['name']: (r['status'], r['headers'], base64.b64decode(r['body'], validate=True))
        for r in signed['responses']
    }


def verify(candidate, response, **changes):
    scope: dict[str, Any] = dict(
        candidate=candidate, install=INSTALL, generation=7, owner_generation=3,
        path=VECTORS['path'], nonce=NONCE, request_id=REQUEST_ID,
        clock_ms=lambda: NOW, request=VECTORS['request'])
    scope.update(changes)
    return verify_pull_response(response, **scope)


@pytest.mark.parametrize('vector', VECTORS['vectors'], ids=lambda v: v['name'])
def test_worker_send_vectors_verified_by_python(worker_responses, vector):
    candidate, responses = worker_responses
    response = responses[vector['name']]
    assert response[0] == vector['status'] and json.loads(response[2]) == vector['body']
    if vector['valid']:
        assert verify(candidate, response) == vector['body']
    else:
        with pytest.raises(ServiceRefused, match='^response_unverified$'):
            verify(candidate, response)


@pytest.mark.parametrize('field', [
    'path', 'status', 'nonce', 'install', 'generation', 'owner_generation',
    'origin', 'key_id', 'body',
])
def test_worker_signature_is_bound_to_response_context(worker_responses, field):
    candidate, responses = worker_responses
    response, changes = responses['reserved'], {}
    if field == 'status':
        response = (400, response[1], response[2])
    elif field == 'body':
        response = (response[0], response[1], response[2] + b' ')
    elif field in {'origin', 'key_id'}:
        candidate = replace(candidate, **{field: 'https://other.example.com'
                                         if field == 'origin' else 'another-service'})
    else:
        changes[field] = dict(path='/v1/pull/send/status', nonce='3' * 32,
                              install='4' * 32, generation=8, owner_generation=4)[field]
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        verify(candidate, response, **changes)
