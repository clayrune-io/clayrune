// A refused step name in the workflow builder must say so NEXT TO THE FIELD, not in a
// corner toast (MC-1179 WB3 follow-up, Ron 2026-11-18: "the comment on the wrong name
// format appears in the top right corner and is easy to miss ... it should be presented
// on same screen area as the edited field"). Asserts position (inside the inspector's
// box, right under the field), absence of a toast, and survival of a re-render, on a
// 1440-wide desktop page and on a real 390-wide phone context (bottom-sheet inspector).
import assert from 'node:assert/strict';
import { startBuilder, agent, wf, NOTE, FIELD, kit, assertNextTo, runChecks } from './_wfb-notes-kit.mjs';

const fixtures = [wf('wf-name', [agent('agent-1'), agent('agent-2')], [{ from: 'agent-1', to: 'agent-2' }])];

// Checks that run on BOTH the desktop page and the phone context.
function nameChecks(h, label) {
  const k = kit(h);
  const { page } = k;
  h.check(`[${label}] a bad step name: reason under the field, typed text kept, marked invalid, model keeps the old name, no toast`, async () => {
    await k.open('wf-name', 'agent-1');
    await page.fill(FIELD, 'Review draft');
    await page.locator(NOTE).waitFor({ state: 'visible' });
    assert.match(await page.locator(NOTE).innerText(), /letters, digits, "-" and "_" only/);
    assert.match(await page.locator(NOTE).innerText(), /"agent-1"/, 'says which name is kept');
    await assertNextTo(k, FIELD, NOTE);
    assert.equal(await page.inputValue(FIELD), 'Review draft', 'what was typed stays visible');
    assert.equal(await page.getAttribute(FIELD, 'aria-invalid'), 'true');
    assert.deepEqual(await k.names(), ['agent-1', 'agent-2']);
    assert.equal(await k.toasts(), 0, 'no toast');
  });
  h.check(`[${label}] a re-render while the reason is showing keeps it, the typed text and the invalid mark`, async () => {
    await k.rerender();
    await page.locator(NOTE).waitFor({ state: 'visible' });
    await assertNextTo(k, FIELD, NOTE);
    assert.equal(await page.inputValue(FIELD), 'Review draft');
    assert.equal(await page.getAttribute(FIELD, 'aria-invalid'), 'true');
    assert.equal(await k.toasts(), 0);
  });
  h.check(`[${label}] a structural action (+ outcome) while it is showing neither renames the step nor toasts`, async () => {
    await page.locator('#wfb-inspector .wfb-vocab-add').dispatchEvent('click');
    await page.locator(NOTE).waitFor({ state: 'visible' });
    assert.deepEqual(await k.names(), ['agent-1', 'agent-2']);
    assert.equal(await page.inputValue(FIELD), 'Review draft');
    assert.equal(await k.toasts(), 0);
  });
  h.check(`[${label}] leaving the field reverts it to the old name, keeps the reason up, then lets it go`, async () => {
    await page.locator(FIELD).focus();
    await page.locator(FIELD).blur();
    await page.waitForFunction(() => document.querySelector('#wfb-inspector .wfb-name').value === 'agent-1'); // reverted on blur
    await page.locator(NOTE).waitFor({ state: 'visible' });
    assert.match(await page.locator(NOTE).innerText(), /Kept "agent-1"/);
    assert.equal(await page.getAttribute(FIELD, 'aria-invalid'), null, 'no longer marked invalid');
    await assertNextTo(k, FIELD, NOTE);
    await k.rerender();
    await page.locator(NOTE).waitFor({ state: 'visible' });
    assert.equal(await k.toasts(), 0);
    await page.locator(NOTE).waitFor({ state: 'hidden', timeout: 6000 });
  });
}

const desktop = await startBuilder({ workflows: fixtures, width: 1440 });
const phone = await startBuilder({ workflows: fixtures, mobile: true });
await runChecks(desktop, '1440 wide', nameChecks);
await runChecks(phone, '390 wide', nameChecks);

const d = kit(desktop);
await desktop.check('typing a legal name clears the reason; the rename then goes through', async () => {
  await d.open('wf-name', 'agent-1');
  await d.page.fill(FIELD, 'Review draft');
  await d.page.locator(NOTE).waitFor({ state: 'visible' });
  await d.page.fill(FIELD, 'Review_draft');
  await d.page.locator(NOTE).waitFor({ state: 'hidden' });
  assert.equal(await d.page.getAttribute(FIELD, 'aria-invalid'), null);
  await d.page.evaluate(() => window._wfCloseInspector());
  assert.deepEqual(await d.names(), ['Review_draft', 'agent-2']);
  assert.equal(await d.toasts(), 0);
});
await desktop.check('a refused name that reaches the sync with no keystroke (programmatic set) is shown inline, not toasted', async () => {
  await d.open('wf-name', 'agent-1');
  await d.page.evaluate(() => { document.querySelector('#wfb-inspector .wfb-name').value = 'bad name!'; window._wfOpenInspector('agent-1'); });
  await d.page.locator(NOTE).waitFor({ state: 'visible' });
  assert.deepEqual(await d.names(), ['agent-1', 'agent-2']);
  assert.equal(await d.toasts(), 0);
});

await desktop.finish('workflow-field-notes', [phone]);
