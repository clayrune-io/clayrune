// Backlog 8a610b84: decorative unconnected-port stubs must not intercept
// pointerdown at a port dot's exact centre. Exercise real mouse events and
// the real canvas, with all network traffic answered by the shared harness.
import assert from 'node:assert/strict';
import { startBuilder, PID } from './_wfb-harness.mjs';

const agent = (name, extra = {}) => ({
  name, type: 'agent', project_id: PID, character: '', prompt: 'p',
  outcomes: [], x: 60, y: 100, ...extra,
});
const cases = [
  { label: 'unconditional', source: agent('a'), when: '' },
  { label: 'outcome', source: agent('a', { outcomes: ['ready'] }), when: 'ready' },
  { label: 'otherwise', source: agent('a', { outcomes: ['ready'] }), when: 'otherwise' },
  { label: 'approval option', source: { name: 'a', type: 'approval', options: ['go', 'stop'], x: 60, y: 100 }, when: 'go' },
  { label: 'trigger', source: null, when: '' },
];
const fixtures = cases.map((c, i) => ({
  id: `centre-${i}`, format: 2, name: c.label, description: '', enabled: false,
  trigger: { type: 'manual' },
  nodes: [...(c.source ? [c.source] : []), agent('b', { x: 500, y: 100 })], edges: [],
}));
const h = await startBuilder({ workflows: fixtures });
const { page } = h;

for (const [i, c] of cases.entries()) {
  await h.check(`exact-centre drag from unconnected ${c.label} port creates its edge`, async () => {
    await h.open(`centre-${i}`);
    const from = c.source ? c.source.name : '__trigger__';
    const port = page.locator(`#wfb-canvas-viewport .wfb-port-out[data-node="${from}"][data-when="${c.when}"]`);
    const box = await port.locator('.wfb-port-dot').boundingBox();
    assert.ok(box, 'source dot is visible');
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    const hit = await page.evaluate(({ x, y }) => {
      const el = document.elementFromPoint(x, y);
      return el?.closest('.wfb-port-out')?.dataset.node;
    }, { x, y });
    const target = await page.locator('.wfb-node[data-name="b"] .wfb-port-in .wfb-port-dot').boundingBox();
    assert.ok(target, 'target dot is visible');
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 12 });
    await page.mouse.up();
    const def = await h.def();
    if (c.source) {
      assert.deepEqual(def.edges.map(e => [e.from, e.to, e.when || '']), [[from, 'b', c.when]]);
    } else {
      assert.deepEqual(def.trigger.entry, ['b']);
    }
    assert.equal(hit, from, 'the exact dot centre must hit the output port');
  });
}

await h.finish('workflow-port-centre');
