// Read-only Studio catalogue/quote integration, independent of Connections.
export async function studioModels(api, engines, kind, projectId) {
  return Promise.all(engines.map(async (engine) => {
    if (!engine.model_discovery || !engine.connected?.ready) return engine;
    const path = `/api/desk/engines/${encodeURIComponent(engine.id)}/models?kind=${kind}`
      + (projectId ? `&project_id=${encodeURIComponent(projectId)}` : '');
    try {
      const result = await api('GET', path);
      if (!Array.isArray(result.models)) throw new Error('The service returned no model catalogue.');
      return { ...engine, models: result.models, discovery: { source: 'discovered', cached: result.cached } };
    } catch (error) {
      // Never present a failed/partial discovery as the service's catalogue.
      const models = (engine.models || []).filter(m => m.kind === kind).map(m => ({
        ...m, render_supported: true, quote_supported: engine.id === 'higgsfield_mcp',
        price: { state: 'unknown' },
      }));
      return { ...engine, models, discovery: { source: 'fallback',
        message: `Curated fallback: ${error?.message || 'Model discovery is unavailable.'}` } };
    }
  }));
}

export function discoveryNote(engine, escape) {
  if (!engine.discovery) return '';
  const text = engine.discovery.source === 'fallback' ? engine.discovery.message
    : `Models from your saved connection${engine.discovery.cached ? ' (cached for up to 24 hours)' : ''}.`;
  return `<div class="desk-v1-engine-est-detail" data-eng-discovery>${escape(text)}</div>`;
}

export function discoveryBlocksRender(picked) {
  return picked.model?.render_supported === false
    || (picked.model?.price?.state === 'unknown' && !picked.model.quote_supported);
}

export async function discoveryPrice(api, picked, body, kind) {
  if (!picked.model || picked.model.price?.state !== 'unknown') return null;
  if (!picked.model.quote_supported) return { price_unknown: true,
    note: 'Price unknown: this service offers no verified free quote.' };
  // Registered Higgsfield models keep the existing picture-aware pricing and
  // human Render flow. New catalogue models receive a text-only read quote.
  if (picked.model.render_supported) return null;
  const fields = { model_id: body.model_id, aspect_ratio: body.aspect_ratio,
    project_id: body.project_id, kind };
  if (kind === 'video') fields.owner = body.owner;
  else fields.prompt = body.prompt;
  const out = await api('POST', `/api/desk/engines/${encodeURIComponent(picked.engine.id)}/models/quote`, fields);
  if (out.price?.state !== 'quoted') return { price_unknown: true, note: out.note || 'Price unknown.' };
  return { estimate: { usd: 0, credits: out.price.credits, note: out.note },
    refusal: { message: 'This model is available to explore and quote. Rendering it is not wired yet.' } };
}
