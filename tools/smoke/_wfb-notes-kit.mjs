// Shared helpers for the inline-refusal smokes (workflow-field-notes.mjs, workflow-label-notes.mjs).
import assert from 'node:assert/strict';
import { startBuilder, PID } from './_wfb-harness.mjs';

export { startBuilder, PID };
export const agent = (name, extra = {}) => ({ name, type: 'agent', project_id: PID, character: '', prompt: 'p', outcomes: [], x: 100, y: 100, ...extra });
export const wf = (id, nodes, edges) => ({ id, format: 2, name: id, description: '', enabled: false, trigger: { type: 'manual' }, nodes, edges });
export const NOTE = '#wfb-inspector [data-note="name"]';
export const FIELD = '#wfb-inspector .wfb-name';

export function kit(h) {
  const { page } = h;
  const row = (i) => page.locator('#wfb-inspector .wfb-vocab-row').nth(i - 1);
  const pill = (i) => row(i).locator('.wfb-vocab-pill');
  return {
    page, row, pill,
    toasts: () => page.locator('#toast-container .toast').count(),
    box: (sel) => page.locator(sel).first().boundingBox(),
    open: async (id, node) => { await h.open(id); await page.evaluate((n) => window._wfOpenInspector(n), node); },
    names: async () => (await h.def()).nodes.map(n => n.name),
    rerender: () => page.evaluate(() => window._wfRender()),
  };
}

// "Next to the field": inside the inspector's own box, directly under the anchor (within
// 40px of its bottom edge) and inside the viewport.
export async function assertNextTo(k, anchorSel, noteSel) {
  const [f, n, insp, vp] = [await k.box(anchorSel), await k.box(noteSel), await k.box('#wfb-inspector'), k.page.viewportSize()];
  assert.ok(f && n && insp, 'field, note and inspector are all rendered');
  assert.ok(n.y >= f.y + f.height - 1, `note sits below the field (note.y ${n.y}, field bottom ${f.y + f.height})`);
  assert.ok(n.y - (f.y + f.height) <= 40, `note is right under the field (gap ${n.y - (f.y + f.height)})`);
  assert.ok(n.x >= insp.x - 1 && n.x + n.width <= insp.x + insp.width + 1, `note is inside the inspector horizontally (${JSON.stringify(n)} in ${JSON.stringify(insp)})`);
  assert.ok(n.y >= insp.y && n.y + n.height <= insp.y + insp.height + 1, 'note is inside the inspector vertically');
  assert.ok(n.x >= 0 && n.x + n.width <= vp.width && n.y + n.height <= vp.height, `note is inside the viewport (${JSON.stringify(n)}, ${vp.width}x${vp.height})`);
}

// Run `register(h, label)`'s checks in order on harness `h`. `register` calls h.check
// synchronously; they are queued here and awaited one by one.
export async function runChecks(h, label, register) {
  const queued = [];
  const orig = h.check;
  h.check = (name, fn) => { queued.push([name, fn]); };
  register(h, label);
  h.check = orig;
  for (const [name, fn] of queued) await orig(name, fn);
}
