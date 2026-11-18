// MC-1179 WB3: slot references have three grammars that do not agree. The server
// (mc/workflows.py _SLOT_RE) substitutes {{steps.<name>.<field>}} only when every
// character is [A-Za-z0-9_.-]; the builder's own guards matched [A-Za-z0-9_] for
// the step name, so every GENERATED name (agent-1, dave-2) was invisible to the
// refuse-at-edit guard, the chip legend and the Save check, and a step named
// "Review draft" produced a placeholder nothing ever substitutes.
import assert from 'node:assert/strict';
import { startBuilder, PID } from './_wfb-harness.mjs';

const agent = (name, extra = {}) => ({ name, type: 'agent', project_id: PID, character: '', prompt: 'p', outcomes: [], x: 100, y: 100, ...extra });
const wf = (id, nodes, edges) => ({ id, format: 2, name: id, description: '', enabled: false, trigger: { type: 'manual' }, nodes, edges });
const fixtures = [
  // hyphenated generated names, reference WIRED
  wf('wf-hyphen-ok', [agent('agent-1'), agent('agent-2', { prompt: 'Use {{steps.agent-1.output}}' })], [{ from: 'agent-1', to: 'agent-2' }]),
  // hyphenated names, reference NOT dominated (no edge)
  wf('wf-hyphen-broken', [agent('agent-1'), agent('agent-2', { prompt: 'Use {{steps.agent-1.output}}' })], []),
  // a step name with a space (legacy / API-authored), referenced from a later step
  wf('wf-space', [agent('Review draft'), agent('later', { prompt: 'Use {{steps.Review draft.output}}' })], [{ from: 'Review draft', to: 'later' }]),
];
const h = await startBuilder({ workflows: fixtures });
const { page, rec } = h;
const save = async () => {
  await page.evaluate(() => window._wfSave());
  await page.waitForFunction(() => !window._wfEntry()._wf.saving);
};
const openPrompt = async (node) => {
  await page.evaluate((n) => { window._wfOpenInspector(n); }, node);
  await page.evaluate((n) => { if (!window._wfEntry()._wf.promptOpen[n]) window._wfTogglePromptOpen(null, n); }, node);
};

await h.check('the chip legend shows a hyphenated step reference as a VALID chip', async () => {
  await h.open('wf-hyphen-ok');
  await openPrompt('agent-2');
  const chips = await page.$$eval('#wfb-inspector .wfb-slot-chip', els => els.map(e => ({ t: e.textContent, broken: e.classList.contains('wfb-slot-chip-broken') })));
  assert.deepEqual(chips, [{ t: '{{steps.agent-1.output}}', broken: false }]);
});

await h.check('the refuse-at-edit guard fires for a hyphenated step (Disconnect would strand the reference)', async () => {
  await h.open('wf-hyphen-ok');
  await page.evaluate(() => window._wfDisconnectNode('agent-1'));
  assert.equal((await h.def()).edges.length, 1, 'the edge the reference needs must survive');
});

await h.check('Save is refused client-side for an undominated hyphenated reference (no request sent)', async () => {
  h.reseed(); await h.open('wf-hyphen-broken');
  await save();
  assert.equal(rec.workflowWrites.length, 0);
  const err = await page.evaluate(() => window._wfEntry()._wf.runErrors);
  assert.ok(err && err['agent-2'], 'the offending card carries the error');
});

await h.check('Save is refused for a reference to a step whose name the server cannot substitute', async () => {
  h.reseed(); await h.open('wf-space');
  await save();
  assert.equal(rec.workflowWrites.length, 0, 'a placeholder nothing substitutes must not be saved');
  const err = await page.evaluate(() => window._wfEntry()._wf.runErrors);
  assert.ok(err && /Review draft/.test(err.later || ''), 'the message names the step: ' + JSON.stringify(err));
});

await h.check('the chip legend flags a reference to a space-named step as broken', async () => {
  await h.open('wf-space');
  await openPrompt('later');
  const chips = await page.$$eval('#wfb-inspector .wfb-slot-chip', els => els.map(e => ({ t: e.textContent, broken: e.classList.contains('wfb-slot-chip-broken') })));
  assert.deepEqual(chips, [{ t: '{{steps.Review draft.output}}', broken: true }]);
});

await h.check('typing a step name the slot grammar cannot express is refused at the rename', async () => {
  await h.open('wf-hyphen-ok');
  await page.evaluate(() => window._wfOpenInspector('agent-1'));
  await page.fill('#wfb-inspector .wfb-name', 'Review draft');
  // The reason is right under the field, inside the inspector, and not a corner toast.
  const note = page.locator('#wfb-inspector [data-note="name"]');
  await note.waitFor({ state: 'visible' });
  assert.match(await note.innerText(), /letters, digits, "-" and "_" only/);
  assert.equal(await page.locator('#toast-container .toast', { hasText: 'Step names take' }).count(), 0, 'the reason is not in a toast (earlier checks may leave unrelated toasts up)');
  await page.evaluate(() => window._wfCloseInspector());
  const d = await h.def();
  assert.deepEqual(d.nodes.map(n => n.name), ['agent-1', 'agent-2']);
  assert.equal(d.edges[0].from, 'agent-1');
});

await h.check('a rename within the grammar still goes through and repoints edges', async () => {
  await h.open('wf-hyphen-ok');
  await page.evaluate(() => window._wfOpenInspector('agent-1'));
  await page.evaluate(() => { document.querySelector('#wfb-inspector .wfb-name').value = 'Draft_2-final'; });
  await page.evaluate(() => window._wfCloseInspector());
  const d = await h.def();
  assert.deepEqual(d.nodes.map(n => n.name), ['Draft_2-final', 'agent-2']);
  assert.equal(d.edges[0].from, 'Draft_2-final');
});

await h.check('a loaded step that already has a non-grammar name is not renamed behind the user\'s back', async () => {
  await h.open('wf-space');
  await page.evaluate(() => window._wfOpenInspector('Review draft'));
  await page.evaluate(() => window._wfCloseInspector());
  assert.deepEqual((await h.def()).nodes.map(n => n.name), ['Review draft', 'later']);
});

await h.finish('workflow-slot-grammar');
