// MC-1179 WB2: Reset / Undo / Redo measure "saved" against the state at LOAD, never
// against the last Save, and a Save leaves a linked-schedule id out of the undo
// history so a later Save POSTs a second schedule. Real builder, hermetic HTTP.
import assert from 'node:assert/strict';
import { startBuilder, PID } from './_wfb-harness.mjs';

const wf1 = {
  id: 'wf-snap', format: 2, name: 'Snapshot', description: '', enabled: false,
  trigger: { type: 'manual' }, edges: [],
  nodes: [{ name: 'a', type: 'agent', project_id: PID, character: '', prompt: 'p', outcomes: [], x: 250, y: 200 }],
};
const h = await startBuilder({ workflows: [wf1] });
const { page, rec } = h;
const outcomesOf = (d) => d.nodes.find(n => n.name === 'a').outcomes;
const save = async () => {
  await page.evaluate(() => window._wfSave());
  await page.waitForFunction(() => !window._wfEntry()._wf.saving);
};
const addOutcome = () => page.evaluate(() => window._wfAddOutcome('a'));

await h.check('Reset after a Save goes back to the SAVED state, not the state at open', async () => {
  h.reseed(); await h.open('wf-snap');
  await addOutcome();                       // outcome-1
  await save();                             // server now holds [outcome-1]
  await addOutcome();                       // outcome-2, unsaved
  await page.click('[title="Revert to the last saved state"]');
  const s = await h.wf();
  assert.deepEqual(outcomesOf(s.def), ['outcome-1'], 'Reset must land on what was saved');
  assert.equal(s.dirty, false);
  assert.deepEqual(outcomesOf(h.store[0]), ['outcome-1'], 'the server copy is the saved one');
});

await h.check('after a Save, undoing back to the saved state reads as saved, and one more Undo reads as unsaved', async () => {
  h.reseed(); await h.open('wf-snap');
  await addOutcome();                       // [outcome-1]
  await save();
  await addOutcome();                       // [outcome-1, outcome-2]
  await page.evaluate(() => window._wfUndo());
  let s = await h.wf();
  assert.deepEqual(outcomesOf(s.def), ['outcome-1']);
  assert.equal(s.dirty, false, 'equal to the saved state, so not dirty');
  await page.evaluate(() => window._wfRedo());
  s = await h.wf();
  assert.equal(s.dirty, true, 'redone past the save is unsaved');
});

await h.check('a Save of a new workflow does not push its own pre-save state onto the undo stack', async () => {
  await h.open(null);
  await page.evaluate(() => {
    window._wfPortPlusClick({ stopPropagation() {}, currentTarget: document.getElementById('wfb-canvas-viewport') }, '__trigger__', null);
    window._wfPopoverPick('wait');
    document.getElementById('wfb-name').value = 'New one';
    window._wfCloseInspector();   // sync + render: the name edit is counted BEFORE the Save
  });
  const before = (await h.wf()).undo;
  await save();
  assert.equal((await h.wf()).undo, before, 'the save-time render (server id/format added to def) is not an edit');
});

await h.check('Create + schedule, Save, Undo, Save never POSTs a second schedule', async () => {
  h.reseed();
  await h.open(null);
  await page.evaluate(() => {
    // trigger "+" -> Wait: the first step, wired to the trigger
    window._wfPortPlusClick({ stopPropagation() {}, currentTarget: document.getElementById('wfb-canvas-viewport') }, '__trigger__', null);
    window._wfPopoverPick('wait');
  });
  await page.evaluate(() => { document.getElementById('wfb-name').value = 'Scheduled'; });
  await page.evaluate(() => window._wfSetTriggerType('schedule'));
  await page.evaluate(() => window._wfSetSchedType('interval'));   // a structural edit while the schedule has no id
  await save();
  assert.equal(rec.schedulePosts.length, 1, 'first Save creates the schedule');
  const s1 = await h.wf();
  assert.ok(s1.linkedSchedule && s1.linkedSchedule.id, 'the schedule id is held');
  await page.evaluate(() => window._wfUndo());
  await save();
  assert.equal(rec.schedulePosts.length, 1, 'a second Save must update or delete, never create another schedule');
});

await h.finish('workflow-saved-snapshot');
