// A refused outcome / approval-option pill must say so ON THAT ROW and put focus in the
// pill, not toast in a corner (MC-1179 WB4 follow-up, Ron 2026-11-18). An emptied pill has
// no label to wire from, so its `+` and port-dot drag are refused. Asserts the reason is
// under the row inside the inspector, focus is in the pill, no toast, and a re-render keeps
// both, on a 1440-wide desktop page and on a real 390-wide phone context.
import assert from 'node:assert/strict';
import { startBuilder, agent, wf, kit, runChecks } from './_wfb-notes-kit.mjs';

const fixtures = [
  wf('wf-three', [agent('a', { outcomes: ['one', 'two', 'three'] }), agent('b', { x: 520 })], []),
  wf('wf-gate', [{ name: 'gate', type: 'approval', options: ['go', 'stop'], x: 100, y: 100 }, agent('c', { x: 520 })], []),
];
const ROW_NOTE = (i) => `#wfb-inspector [data-note="label"][data-idx="${i}"]`;

function pillChecks(h, label) {
  const k = kit(h);
  const { page } = k;
  h.check(`[${label}] an emptied outcome pill: + shows the reason on that row, focus is in the pill, no toast, no popover`, async () => {
    await k.open('wf-three', 'a');
    await k.pill(2).fill('');
    await k.row(2).locator('.wfb-port-plus').dispatchEvent('click');
    const note = page.locator(ROW_NOTE(1));
    await note.waitFor({ state: 'visible' });
    assert.match(await note.innerText(), /Name this outcome first/);
    const r = await k.row(2).boundingBox(), n = await note.boundingBox(), insp = await k.box('#wfb-inspector'), vp = page.viewportSize();
    assert.ok(n.y >= r.y + r.height - 1 && n.y - (r.y + r.height) <= 40, `note is right under row 2 (row bottom ${r.y + r.height}, note ${n.y})`);
    assert.ok(n.x >= insp.x - 1 && n.x + n.width <= insp.x + insp.width + 1, 'note inside the inspector');
    assert.ok(n.x >= 0 && n.x + n.width <= vp.width && n.y + n.height <= vp.height, 'note inside the viewport');
    assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.className), await k.pill(2).getAttribute('class'), 'focus is in the pill');
    assert.equal(await k.pill(2).getAttribute('aria-invalid'), 'true');
    assert.equal(await page.locator('#wfb-port-popover').count(), 0);
    assert.equal(await k.toasts(), 0, 'no toast');
  });
  h.check(`[${label}] the row's reason survives a re-render; typing a name clears it`, async () => {
    await k.rerender();
    const note = page.locator(ROW_NOTE(1));
    await note.waitFor({ state: 'visible' });
    assert.equal(await k.pill(2).inputValue(), '', 'the emptied pill is still empty');
    assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.classList.contains('wfb-outcome-input')), true, 'focus put back in the pill');
    await k.pill(2).fill('two');
    await note.waitFor({ state: 'hidden' });
    assert.equal(await k.pill(2).getAttribute('aria-invalid'), null);
  });
}

const desktop = await startBuilder({ workflows: fixtures, width: 1440 });
const phone = await startBuilder({ workflows: fixtures, mobile: true });
await runChecks(desktop, '1440 wide', pillChecks);
await runChecks(phone, '390 wide', pillChecks);

const d = kit(desktop);
await desktop.check('an emptied option pill on an approval gate: + shows the reason on that row, focus in the pill, no toast', async () => {
  await d.open('wf-gate', 'gate');
  await d.pill(2).fill('');
  await d.row(2).locator('.wfb-port-plus').dispatchEvent('click');
  const note = d.page.locator(ROW_NOTE(1));
  await note.waitFor({ state: 'visible' });
  assert.match(await note.innerText(), /Name this option first/);
  assert.equal(await d.page.evaluate(() => document.activeElement && document.activeElement.classList.contains('wfb-option-input')), true);
  assert.equal(await d.toasts(), 0);
});
await desktop.check('an emptied pill: dragging from its port dot shows the reason on the row, makes no edge, no toast', async () => {
  await d.open('wf-three', 'a');
  await d.pill(1).fill('');
  const dot = await d.page.locator('#wfb-inspector .wfb-vocab-row .wfb-port-out').first().boundingBox();
  const target = await d.page.locator('.wfb-node[data-name="b"]').boundingBox();
  await d.page.mouse.move(dot.x + dot.width / 2, dot.y + dot.height / 2);
  await d.page.mouse.down();
  await d.page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 12 });
  await d.page.mouse.up();
  await d.page.locator(ROW_NOTE(0)).waitFor({ state: 'visible' });
  assert.equal((await desktop.def()).edges.length, 0);
  assert.equal(await d.toasts(), 0);
  assert.equal(await d.page.evaluate(() => document.activeElement && document.activeElement.classList.contains('wfb-outcome-input')), true, 'focus in the pill');
});

await desktop.finish('workflow-label-notes', [phone]);
