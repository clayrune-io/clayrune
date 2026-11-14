import test from 'node:test';
import assert from 'node:assert/strict';
import { serviceFrame, clientFrame } from '../pull-frame.js';
import { base64url, bytes, unbase64url, sha256 } from '../signing.js';

test('wake codec signs the empty hint and binds type, scope, epoch and payload', async () => {
  const key = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const config = { serviceOrigin: 'https://mail.example.com', serviceKeyId: 'service',
    servicePrivateKey: base64url(await crypto.subtle.exportKey('pkcs8', key.privateKey)), clock: () => 1000 };
  const scope = { install_id: '1'.repeat(32), generation: 1, owner_generation: 1, epoch: 2 };
  const frame = JSON.parse(await serviceFrame(config, scope, 'wake', {}));
  assert.deepEqual(JSON.parse(new TextDecoder().decode(unbase64url(frame.payload))), {});
  assert.equal(frame.payload_digest, await sha256(bytes('{}')));
  const transcript = (type) => bytes(JSON.stringify(['clayrune-agent-mail-pull-frame', '1', config.serviceOrigin,
    type, frame.key_id, frame.install_id, '1', '1', '2', frame.frame_id, '1000', frame.payload_digest]));
  assert.equal(await crypto.subtle.verify('Ed25519', key.publicKey, unbase64url(frame.signature), transcript('wake')), true);
  assert.equal(await crypto.subtle.verify('Ed25519', key.publicKey, unbase64url(frame.signature), transcript('status')), false);
  await assert.rejects(serviceFrame(config, scope, 'wake', { event: 'forbidden' }), { message: 'invalid_request' });
  await assert.rejects(serviceFrame(config, scope, 'wake', []), { message: 'invalid_request' });
  await assert.rejects(serviceFrame(config, scope, 'wake_unknown', {}), { message: 'unsupported_frame' });
  assert.throws(() => clientFrame(JSON.stringify({ v: 1, type: 'wake', ...scope,
    request_id: '2'.repeat(32), payload: {} })), { message: 'unsupported_frame' });
});
