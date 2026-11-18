// MC-1179 WB4: an outcome/option label can be renamed out from under its edge. Labels
// are read back from the pill inputs at sync time, but an edge stores the label as
// text and the row's `+` / port / ✕ bake the OLD label (or row index) at render time.
// Typing the real label and then using that same row stranded the edge, which the
// server refuses at Save ("not a declared outcome/option") with no visible edge to fix.
import assert from 'node:assert/strict';
import { startBuilder, PID } from './_wfb-harness.mjs';

const agent = (name, extra = {}) => ({ name, type: 'agent', project_id: PID, character: '', prompt: 'p', outcomes: [], x: 100, y: 100, ...extra });
const wf = (id, nodes, edges) => ({ id, format: 2, name: id, description: '', enabled: false, trigger: { type: 'manual' }, nodes, edges });
const fixtures = [
  wf('wf-empty', [agent('a', { x: 100, y: 100 })], []),
  wf('wf-wired', [agent('a', { outcomes: ['yes', 'no'] }), agent('b', { x: 520, y: 100 })], [{ from: 'a', to: 'b', when: 'yes' }]),
  wf('wf-two', [agent('a', { outcomes: ['x'], x: 60, y: 60 }), agent('b', { x: 400, y: 120 })], []),
  wf('wf-three', [agent('a', { outcomes: ['one', 'two', 'three'] })], []),
  wf('wf-gate', [{ name: 'gate', type: 'approval', options: ['go', 'stop'], x: 100, y: 100 }, agent('c', { x: 520, y: 100 })],
    [{ from: 'gate', to: 'c', when: 'go' }]),
];
const h = await startBuilder({ workflows: fixtures });
const { page } = h;
const row = (i) => page.locator('#wfb-inspector .wfb-vocab-row').nth(i - 1);
const pill = (i) => row(i).locator('.wfb-vocab-pill');
const outcomes = async (name = 'a') => (await h.def()).nodes.find(n => n.name === name).outcomes;
const edgeLabels = async () => (await h.def()).edges.map(e => `${e.from}->${e.to}:${e.when || ''}`);

await h.check('type a new outcome label, then use + on that same row: the edge carries the typed label', async () => {
  await h.open('wf-empty');
  await page.evaluate(() => window._wfOpenInspector('a'));
  await page.locator('#wfb-inspector .wfb-vocab-add').dispatchEvent('click');                  // pill pre-filled "outcome-1"
  await page.fill('#wfb-inspector .wfb-outcome-input', 'approved');   // typed, not yet synced
  await page.locator('#wfb-inspector .wfb-vocab-row .wfb-port-plus').dispatchEvent('click');
  await page.locator('.wfb-popover-row').filter({ hasText: 'Wait' }).dispatchEvent('click');
  assert.deepEqual(await outcomes(), ['approved']);
  const d = await h.def();
  assert.deepEqual(d.edges.map(e => [e.from, e.to, e.when]), [['a', d.nodes[1].name, 'approved']]);
  assert.equal(await page.locator('.wfb-node[data-name="a"] .wfb-port-stub').count(), 1,
    'only the unconnected "otherwise" row keeps its stub: the approved port is connected');
});

await h.check('renaming an outcome that is already wired repoints its edge', async () => {
  await h.open('wf-wired');
  await page.evaluate(() => window._wfOpenInspector('a'));
  await pill(1).fill('approved');
  await page.evaluate(() => window._wfCloseInspector());
  assert.deepEqual(await outcomes(), ['approved', 'no']);
  assert.deepEqual(await edgeLabels(), ['a->b:approved']);
});

await h.check('renaming an approval option repoints its edge the same way', async () => {
  await h.open('wf-gate');
  await page.evaluate(() => window._wfOpenInspector('gate'));
  await pill(1).fill('proceed');
  await page.evaluate(() => window._wfCloseInspector());
  assert.deepEqual((await h.def()).nodes[0].options, ['proceed', 'stop']);
  assert.deepEqual(await edgeLabels(), ['gate->c:proceed']);
});

await h.check('typing a label then dragging from the row\'s port dot wires the typed label', async () => {
  await h.open('wf-two');
  await page.evaluate(() => window._wfOpenInspector('a'));
  await pill(1).fill('approved');
  const dot = await page.locator('#wfb-inspector .wfb-vocab-row .wfb-port-out').first().boundingBox();
  const target = await page.locator('.wfb-node[data-name="b"]').boundingBox();
  // Grab 6px above centre: the unconnected-port stub is a 2px line across the dot's middle.
  await page.mouse.move(dot.x + dot.width / 2, dot.y + dot.height / 2 - 6);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 12 });
  await page.mouse.up();
  assert.deepEqual(await outcomes(), ['approved']);
  assert.deepEqual(await edgeLabels(), ['a->b:approved']);
});

await h.check('clearing outcome 2 of 3 and clicking its X removes only that outcome', async () => {
  await h.open('wf-three');
  await page.evaluate(() => window._wfOpenInspector('a'));
  await pill(2).fill('');
  await row(2).locator('.wfb-branch-del').dispatchEvent('click');
  assert.deepEqual(await outcomes(), ['one', 'three']);
});

await h.check('an emptied pill that is not wired is still dropped at the next sync', async () => {
  await h.open('wf-three');
  await page.evaluate(() => window._wfOpenInspector('a'));
  await pill(2).fill('');
  await page.evaluate(() => window._wfCloseInspector());
  assert.deepEqual(await outcomes(), ['one', 'three']);
});

await h.check('an emptied pill that has an edge keeps its label: no edge is silently stranded or dropped', async () => {
  await h.open('wf-wired');
  await page.evaluate(() => window._wfOpenInspector('a'));
  await pill(1).fill('');
  await page.evaluate(() => window._wfCloseInspector());
  assert.deepEqual(await outcomes(), ['yes', 'no']);
  assert.deepEqual(await edgeLabels(), ['a->b:yes']);
});

await h.check('an emptied unwired pill: its + is refused with a toast, no popover, no new step', async () => {
  await h.open('wf-three');
  await page.evaluate(() => window._wfOpenInspector('a'));
  await pill(2).fill('');
  await row(2).locator('.wfb-port-plus').dispatchEvent('click');
  assert.equal(await page.locator('#wfb-port-popover').count(), 0, 'no popover opens');
  assert.match(await page.locator('.toast, #toast, [class*="toast"]').allInnerTexts().then(a => a.join(' ')), /Name this outcome first/);
  assert.equal((await h.def()).nodes.length, 1);
  assert.deepEqual(await edgeLabels(), []);
});

await h.check('an emptied option pill on an approval gate: its + is refused the same way', async () => {
  await h.open('wf-gate');
  await page.evaluate(() => window._wfOpenInspector('gate'));
  await pill(2).fill('');
  await row(2).locator('.wfb-port-plus').dispatchEvent('click');
  assert.equal(await page.locator('#wfb-port-popover').count(), 0);
  assert.match(await page.locator('[class*="toast"]').allInnerTexts().then(a => a.join(' ')), /Name this option first/);
});

await h.check('an emptied unwired pill: a drag from its port dot makes no edge and drops no label', async () => {
  await h.open('wf-two');
  await page.evaluate(() => window._wfOpenInspector('a'));
  await pill(1).fill('');
  const dot = await page.locator('#wfb-inspector .wfb-vocab-row .wfb-port-out').first().boundingBox();
  const target = await page.locator('.wfb-node[data-name="b"]').boundingBox();
  await page.mouse.move(dot.x + dot.width / 2, dot.y + dot.height / 2 - 6);
  await page.mouse.down();
  await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 12 });
  await page.mouse.up();
  assert.deepEqual(await edgeLabels(), []);
  assert.match(await page.locator('[class*="toast"]').allInnerTexts().then(a => a.join(' ')), /Name this outcome first/);
});

await h.finish('workflow-label-rename');
