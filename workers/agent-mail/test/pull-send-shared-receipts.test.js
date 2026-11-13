import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { projectSendResult, SEND_PATH } from '../pull-send-http.js';

// Unit21 owns this canonical fixture; consume the same bytes as Python. The
// projector constructs a response, rather than verifying an incoming envelope.
// Wrong route/version/correlation cases exercise Python's response verifier;
// they cannot be passed to this API without inventing a second verifier here.
const fixture = JSON.parse(readFileSync(new URL('../../../tests/fixtures/agent-mail-pull-send-receipts.json', import.meta.url), 'utf8'));
const projectionCase = ({ path, request, body }) => path === SEND_PATH
  && body.v === 1 && body.request_id === request.op_id;
const vectors = fixture.vectors.filter(projectionCase);

test('canonical receipt fixture covers 52 projections and seven incoming-envelope cases', () => {
  assert.equal(fixture.version, 1);
  assert.equal(fixture.vectors.length, 59);
  assert.equal(vectors.length, 52);
  assert.deepEqual(fixture.vectors.filter((v) => !projectionCase(v)).map((v) => v.name), [
    'missing-v', 'missing-request_id', 'bad-request_id-42', 'bad-v-43',
    'receipt-on-status', 'receipt-on-status', 'receipt-on-ticket',
  ]);
});

for (const vector of vectors) test(`canonical send receipt projection: ${vector.name}`, () => {
  const { v, request_id, ...fields } = vector.body;
  const project = () => projectSendResult({ status: vector.status, ...fields }, vector.request, request_id);
  if (vector.valid) assert.deepEqual(project(), { status: vector.status, body: vector.body });
  else assert.throws(project, { message: 'storage_unavailable' });
});
