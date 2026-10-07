// A prepared picture price survives the next free text-only quote, until an edit/reprice.
export function confirmationMatches(confirmation, request, fresh) {
  return !!confirmation && confirmation.request === JSON.stringify(request)
    && confirmation.base === JSON.stringify(fresh);
}

export function priceConfirmation(error, request, fresh) {
  const b = error && error.body;
  if (!b || error.status !== 409 || b.code !== 'render_price_changed' || !b.estimate || !b.plan) return null;
  return { request: JSON.stringify(request), base: JSON.stringify(fresh),
    quote: { ...fresh, estimate: b.estimate, plan: b.plan, refusal: null } };
}

// A worker's held quote is an offer, never permission to spend. Match the saved
// storyboard and selectors before restoring it from polling or a page reload.
export function heldPriceConfirmation(render, request, fresh) {
  const hold = render && render.hold;
  if (!hold || hold.kind !== 'render_price_changed' || !fresh
      || !fresh.board_fingerprint || fresh.board_fingerprint !== hold.board_fingerprint) return null;
  const saved = hold.request || {};
  if (!saved.owner || !request.owner || saved.owner.kind !== request.owner.kind || saved.owner.id !== request.owner.id) return null;
  const fields = ['engine_id', 'model_id', 'aspect_ratio', 'resolution', 'audio', 'project_id'];
  if (fields.some(k => JSON.stringify(saved[k]) !== JSON.stringify(request[k]))) return null;
  return priceConfirmation({ status: 409, body: hold }, request, fresh);
}

export function renderStatusWord(render) {
  if (render.submission_state === 'submitted' && render.status === 'rendering') return 'Submitted';
  return { pending: 'Waiting to start', working: 'Working: uploading pictures and submitting clips',
    queued: 'Queued', rendering: 'Rendering', ready: 'Ready', held: 'Held', failed: 'Failed' }[render.status] || render.status;
}

// Price again remains a dismissal after reload in this tab. Storage failure
// affects only quote display; every submit still gets a new server price check.
export function dismissedPriceHold(key) {
  try { return sessionStorage.getItem('desk-render-price-dismissed:' + key); }
  catch (error) { console.warn('Could not read dismissed render price', error); return null; }
}

export function dismissPriceHold(key, renderId) {
  try {
    if (renderId) sessionStorage.setItem('desk-render-price-dismissed:' + key, renderId);
    else sessionStorage.removeItem('desk-render-price-dismissed:' + key);
  } catch (error) { console.warn('Could not save dismissed render price', error); }
}
