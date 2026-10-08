// Deterministic observer lifecycle checks; phone events are covered by the mobile smoke.
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../../static/js/browser-input-timing.js', import.meta.url), 'utf8')
  .replace('export const inputTiming =', 'globalThis.inputTiming =');

function collector(flag = '1') {
  let clock = 1;
  const uploads = [], intervals = [], raf = [];
  const sandbox = {
    location: { search: '?bp_input_timing=' + flag },
    localStorage: { getItem: () => null }, URLSearchParams, AbortSignal,
    performance: { now: () => clock }, navigator: { userAgent: 'phone-test' },
    document: { hidden: false }, window: { _bpStats: { transport: 'bin' } },
    console: { warn() {} }, queueMicrotask,
    setInterval(fn) { intervals.push(fn); return 1; },
    requestAnimationFrame(fn) { raf.push(fn); },
    async fetch(url, init) { uploads.push(JSON.parse(init.body)); return { ok: true }; },
  };
  vm.runInNewContext(source, sandbox);
  function element() {
    const listeners = new Map();
    return {
      isConnected: true, getClientRects: () => [1],
      addEventListener(kind, fn) { listeners.set(kind, fn); },
      removeEventListener(kind) { listeners.delete(kind); },
      fire(kind) { listeners.get(kind)?.(); },
    };
  }
  return {
    api: sandbox.inputTiming, uploads, intervals, sandbox, element,
    at(ms) { clock = ms; },
    paint() { raf.splice(0).forEach(fn => fn()); },
    async tick() { intervals[0]?.(); await new Promise(resolve => setImmediate(resolve)); },
  };
}

test('query off produces no timer or samples', async () => {
  const c = collector('0'), body = {};
  c.api.queued(body, 0); assert.equal(c.api.sent(body), null);
  await c.tick(); assert.equal(c.intervals.length, 0); assert.equal(c.uploads.length, 0);
});

test('capture time survives merging and a pre-ack loaded frame is excluded', async () => {
  const c = collector(), img = c.element(), input = c.element();
  c.api.observeFrames(img); c.api.observeInput(input);
  const a = {}, b = {}, merged = {};
  c.at(10); input.fire('input'); c.api.queued(a, 0);
  c.at(20); input.fire('input'); c.api.queued(b, 1);
  c.api.merged(merged, a, b);
  c.at(100); const samples = c.api.sent(merged);
  c.at(110); img.fire('load');
  c.at(120); c.api.ack(samples, { ok: true, status: 200 });
  c.at(130); c.paint(); await c.tick(); assert.equal(c.uploads.length, 0);
  c.at(140); img.fire('load'); c.at(150); c.paint(); await c.tick();
  const result = c.uploads[0].samples;
  assert.equal(result.length, 2);
  assert.equal(result[0].t_key, 10); assert.equal(result[1].t_key, 20);
  assert.equal(result[0].t_sent, 100); assert.equal(result[0].t_ack, 120);
  assert.equal(result[0].t_frame, 150); assert.equal(result[0].outcome, 1);
  assert.equal(result[0].request_id, result[1].request_id);
});

test('hidden frame times out explicitly and network failures have no ack', async () => {
  const c = collector(), img = c.element(), body = {};
  c.api.observeFrames(img); c.api.queued(body, 0);
  const samples = c.api.sent(body); c.at(20); c.api.ack(samples, { ok: true, status: 200 });
  c.sandbox.document.hidden = true; c.at(30); img.fire('load'); c.paint();
  c.at(10021); await c.tick();
  assert.equal(c.uploads[0].samples[0].outcome, 0);
  assert.equal(c.uploads[0].samples[0].t_frame, null);
  const failed = {}; c.api.queued(failed, 0); c.api.failed(c.api.sent(failed)); await c.tick();
  assert.equal(c.uploads[1].samples[0].outcome, 2);
  assert.equal(c.uploads[1].samples[0].t_ack, null);
});

test('reopened pane cannot finish an old queued sample and cap is 500', async () => {
  const c = collector(), oldImg = c.element(), body = {};
  const close = c.api.observeFrames(oldImg); c.api.queued(body, 0); close();
  const newImg = c.element(); c.api.observeFrames(newImg);
  const old = c.api.sent(body); c.api.ack(old, { ok: true, status: 200 });
  c.at(10); newImg.fire('load'); c.paint(); await c.tick();
  assert.equal(c.uploads[0].samples[0].outcome, 3);
  assert.equal(c.uploads[0].samples[0].t_ack, null);
  let count = 0;
  for (let n = 0; n < 600; n++) {
    const next = {}; c.api.queued(next, 0); count += c.api.sent(next).length;
  }
  assert.equal(count, 500);
});
