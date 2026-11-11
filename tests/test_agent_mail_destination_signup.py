"""Automatic destination signup, synthetic identities/providers only, no network."""
import io
import json
import subprocess
import urllib.error

import pytest

from mc.agent_mail_begin_proof import canonical_begin, DESTINATION_FAILURES, DestinationRefused
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_service_client import HttpsTransport
from test_agent_mail_u7d2e_ceremony import Kit, BEGIN, ORIGIN, ROOT_DIR, verify_sig


def pending_transport(kit, code, *, mutate=None, tweak=None):
    original = kit.world.post

    def post(origin, path, headers, raw):
        if path != BEGIN:
            return original(origin, path, headers, raw)
        value = json.loads(raw)
        proof = value['proof']
        assert verify_sig(value['value']['device_public_key'],
                          canonical_begin(value['value'], value['flow_secret'], origin,
                                          proof['timestamp'], proof['nonce']), proof['signature'])
        body = {'flow_id': value['value']['flow_id'], 'code': code}
        if mutate:
            body = mutate(body)
        kit.world.tweak = tweak or {}
        response = kit.world._sign(body, BEGIN, value['value']['flow_id'], headers)
        response['status'] = 409
        return response

    kit.world.post = post
    return original


@pytest.mark.parametrize('code', sorted(DESTINATION_FAILURES))
def test_signed_destination_refusal_preserves_pending_and_resumes_without_code_loss(tmp_path, code):
    kit = Kit(tmp_path)
    original = pending_transport(kit, code)
    with pytest.raises(DestinationRefused, match='^' + code + '$'):
        kit.start()
    row = kit.flow()
    assert row['stage'] == 'pending' and row['remote_started'] == 1 and row['assigned'] == 0
    assert not kit.world.flows and kit.world.mails == 0
    kit.world.post = original
    kit.core.resume()
    assert kit.flow()['assigned'] == 1 and kit.world.mails == 1
    kit.core.confirm(kit.core.review())
    kit.core.verify(kit.world.code)
    assert kit.flow()['ownership'] == 1


@pytest.mark.parametrize('mutate,tweak', [
    (lambda b: {**b, 'code': 'untrusted_remote_text'}, None),
    (lambda b: {**b, 'flow_id': 'other'}, None),
    (lambda b: {**b, 'extra': True}, None),
    (None, {'nonce': 'ef' * 16}),
    (None, {'path': '/v1/mail/enrollment/confirm'}),
    (None, {'install': 'other'}),
])
def test_untrusted_hint_cannot_become_a_destination_state(tmp_path, mutate, tweak):
    kit = Kit(tmp_path)
    pending_transport(kit, 'destination_unverified', mutate=mutate, tweak=tweak)
    with pytest.raises(EnrollmentRefused, match='^remote_begin_retryable$'):
        kit.start()
    assert not kit.world.flows and kit.world.mails == 0
    assert kit.flow()['assigned'] == 0


def test_python_begin_canonical_bytes_match_worker_including_fence_and_signature(tmp_path):
    kit = Kit(tmp_path)
    kit.start()
    request = kit.world.named(BEGIN)[0]['value']
    assert 'fence_digest' in request['value']
    proof = request['proof']
    canonical = canonical_begin(request['value'], request['flow_secret'], ORIGIN, proof['timestamp'], proof['nonce'])
    script = """
import { canonicalBegin } from './workers/agent-mail/enrollment-begin-proof.js';
import { verifyProof } from './workers/agent-mail/enrollment-transcript.js';
let raw = ''; for await (const chunk of process.stdin) raw += chunk;
const {request, service} = JSON.parse(raw);
const b = await canonicalBegin(request.value, request.flow_secret, service, request.proof.timestamp, request.proof.nonce);
if (!await verifyProof(request.value.device_public_key, request.proof.signature, b)) process.exit(1);
process.stdout.write(Buffer.from(b).toString('base64'));
"""
    import base64
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT_DIR,
                            input=json.dumps({'request': request, 'service': ORIGIN}),
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert base64.b64decode(result.stdout) == canonical


def test_begin_retries_use_new_possession_proof_nonce(tmp_path):
    kit = Kit(tmp_path)
    kit.start()
    row, secret = kit.ctx()
    kit.remote.ceremony.begin(row, secret)
    proofs = [call['value']['proof'] for call in kit.world.named(BEGIN)]
    assert len({p['nonce'] for p in proofs}) == 2
    assert all(p['timestamp'] == str(kit.now) for p in proofs)


def test_https_transport_keeps_bounded_signed_refusal_for_verification():
    class Opener:
        def open(self, request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 409, 'refused',
                                         {'x-mail-signature': 'synthetic'}, io.BytesIO(b'{"code":"destination_unverified"}'))
    result = HttpsTransport(opener=Opener()).post(ORIGIN, BEGIN, {}, b'{}')
    assert result['status'] == 409 and result['headers']['x-mail-signature'] == 'synthetic'
    assert result['body'] == b'{"code":"destination_unverified"}'
