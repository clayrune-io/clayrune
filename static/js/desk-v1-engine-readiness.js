// Why a Generate / Render button is disabled, in words, and which models Clayrune
// can actually render. Pure functions: no DOM, no network, no state.
//
// A model the engine's catalogue lists but Clayrune has no render contract for
// (render_supported === false) can be priced, never rendered. The button used to
// go dead with no reason beside it (Nano Banana Pro, 2026-11-14).

// The select label's suffix: a model that is listed but cannot be rendered says so.
export function modelMark(model) {
  if (!model || model.render_supported !== false) return '';
  return model.quote_supported ? ' · price only' : ' · not renderable yet';
}

// Labels of the models of this list that Clayrune can render: the same test as
// discoveryBlocksRender, applied to each model.
export function renderableLabels(models) {
  return (models || []).filter((m) => m && m.render_supported !== false
    && !(m.price?.state === 'unknown' && !m.quote_supported))
    .map((m) => m.label || m.model_id);
}

function joinLabels(labels) {
  if (!labels.length) return '';
  if (labels.length === 1) return labels[0];
  return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

// The reason the button is off, or null when it is on / busy / held for another
// visible reason. `code` lets a caller skip a line it already draws elsewhere.
//   p         { engine, model, models } from the panel's _pick
//   s         { busy, estimating, estimate, estimateError, prompt (image only; undefined for video) }
export function disabledReason(p, s) {
  if (!p || !p.engine || !p.model) return null;
  if (s.busy) return null;
  if (!(p.engine.connected || {}).ready) {
    return { code: 'not_connected', text: `${p.engine.label} is not connected yet. Open Connections in the Desk header to set it up.` };
  }
  const model = p.model;
  if (model.render_supported === false) {
    const names = joinLabels(renderableLabels(p.models));
    return { code: 'not_renderable', text: `Clayrune can price ${model.label} but cannot generate with it yet.`
      + (names ? ` Models it can render: ${names}.` : ' No model in this list can be rendered yet.') };
  }
  if (model.price?.state === 'unknown' && !model.quote_supported) {
    return { code: 'no_price', text: `${p.engine.label} offers no verified free price for ${model.label}, so Clayrune will not render it.` };
  }
  if (s.prompt !== undefined && !String(s.prompt).trim()) {
    return { code: 'no_prompt', text: 'Type a description of the picture to turn Generate on.' };
  }
  if (s.estimating) return null; // the estimate line already says "Pricing…"
  if (s.estimateError) return { code: 'estimate_failed', text: 'The price check failed, so it is off. The reason is in the price line above.' };
  const e = s.estimate;
  if (!e || !e.estimate) return { code: 'no_estimate', text: 'Waiting for a price before it can turn on.' };
  if (e.refusal) return { code: 'refused', text: 'The price check refused this request, so it is off. The reason is in the price line above.' };
  return null;
}

// One line under the estimate. Skips not_connected: the panel already prints that.
export function reasonHTML(reason, esc) {
  if (!reason || reason.code === 'not_connected') return '';
  return `<div class="desk-v1-engine-est-detail" data-eng-why data-code="${esc(reason.code)}" role="status">${esc(reason.text)}</div>`;
}
