// MC-1179 W8: a top-level catch logs which kind of failure it swallowed, and
// never the exception text (a storage error may carry tokens or SQL inputs).
import test from 'node:test';
import assert from 'node:assert/strict';
import { failureDetail } from '../failure-log.js';

test('the exception name is kept, so a code bug and a storage fault differ in the log', () => {
  assert.equal(failureDetail(new TypeError('private owner@example.com')), 'name=TypeError');
  assert.equal(failureDetail(new RangeError('x')), 'name=RangeError');
  assert.equal(failureDetail(new Error('D1_ERROR: no such table: pull_installs')), 'name=Error');
});

test('a message is kept only when it is one of the closed codes', () => {
  assert.equal(failureDetail(new Error('storage_unavailable')), 'name=Error code=storage_unavailable');
  assert.equal(failureDetail(new Error('queue_full')), 'name=Error code=queue_full');
  class Refusal extends Error { constructor(code) { super(code); this.code = code; } }
  assert.equal(failureDetail(new Refusal('stale_generation')), 'name=Error code=stale_generation');
  // Code-shaped but not on the list: a token is lowercase hex too.
  assert.equal(failureDetail(new Error('abcdef0123456789abcdef0123456789')), 'name=Error');
  assert.equal(failureDetail(new Error('private_detail')), 'name=Error');
  assert.equal(failureDetail(new Error(' storage_unavailable')), 'name=Error');
});

test('non-Error throws and hostile names never reach the log and never throw', () => {
  for (const thrown of [undefined, null, 'private text', 42, {}, { name: 'private owner@example.com' },
    { name: 'x'.repeat(41) }, { name: 7 }, { name: 'Error', message: 'storage_unavailable' }]) {
    assert.match(failureDetail(thrown), /^name=(?:unknown|Error)$/);
  }
  const hostile = Object.defineProperty(new Error('x'), 'name', { get() { throw new Error('private'); } });
  assert.equal(failureDetail(hostile), 'name=unknown');
});
