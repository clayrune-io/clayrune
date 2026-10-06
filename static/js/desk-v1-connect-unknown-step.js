// MC-1062/12 entry point: unknown U2, explicit discovery and shared MCP choice.
// The independent reference unit registers through this single index script.
import { registerReferenceStep } from './desk-v1-connect-reference-step.js';
(function () {
  const W = window.DeskV1ConnectWizard;
  if (!W) return;
  registerReferenceStep(W);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const unknown = (info) => !!info && (!info.service || !info.service.id || info.service.recognised === false);
  let choice = '', mcp = '', lookupGen = 0;
  const fallback = '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-unknown-reference>Save a reference</button>';
  const options = [['lookup', 'Find connection options'], ['mcp', 'MCP server'], ['api', 'API details (reference only)'], ['reference', 'Save a reference']];
  const rows = (items, attr, selected) => `<div class="desk-v1-cfw-options" role="radiogroup" aria-label="Setup path">${items.map(([id, text]) => `<label class="desk-v1-cfw-option"><input type="radio" name="unknown-path" ${attr}="${id}" ${selected === id ? 'checked' : ''}><span class="desk-v1-cfw-option-label">${esc(text)}</span></label>`).join('')}</div>`;
  function choose(api, path) {
    const patches = { lookup: { type: 'lookup', variant: 'lookup' }, mcp: { type: 'mcp', variant: 'choose-mcp' }, api: { type: 'reference', variant: 'api-details' }, reference: { type: 'reference', variant: 'reference' } };
    api.select(patches[path]);
    api.go('setup');
  }
  const discoverInfo = (api) => ({ ...api.info, service: null, input_kind: 'url' });
  const D = () => window.DeskV1ConnectDiscover;
  const ds = () => D().state();
  function discoveredPaths() {
    const answer = ds().answer;
    const methods = new Set(((answer && answer.options) || []).map((o) => o.method));
    const paths = [];
    if (methods.has('mcp')) paths.push(['mcp', 'Review an MCP setup draft']);
    if (methods.has('api_key') || methods.has('api')) paths.push(['api', 'Review API details (reference only)']);
    return paths;
  }
  W.registerScreen({ id: 'unknown-connection', step: 'connection', match: (_s, info) => unknown(info),
    title: () => 'Connect this service', copy: () => 'This service is new to Clayrune. Choose how to add it.',
    body: () => rows(options, 'data-unknown-path', choice),
    primary: (api) => ({ label: 'Continue', disabled: !choice, run: async () => { const path = choice; choose(api, path); return false; } }),
    bind: (root) => root.querySelectorAll('[data-unknown-path]').forEach((n) => n.addEventListener('change', () => { choice = n.dataset.unknownPath; root.querySelector('[data-cfw-primary]').disabled = false; })),
    discard: () => { choice = ''; mcp = ''; lookupGen++; D()?.reset(); } });
  W.registerScreen({ id: 'unknown-lookup', step: 'setup', match: (sel) => sel.type === 'lookup',
    title: () => ds().answer ? 'Connection options' : 'Find connection options',
    copy: () => ds().answer ? 'Found information is a suggestion. Choose a setup path you can review.' : 'Look up public connection information. No sign-in or credential is used.',
    body: () => {
      const d = ds();
      if (d.busy) return '<div role="status" data-unknown-busy>Looking up public information…</div>';
      if (d.error) return `<div role="alert" data-unknown-outcome="failed">${esc(d.error)}</div>` + fallback;
      if (!d.answer) return '';
      const a = d.answer;
      const text = a.incomplete || a.outcome === 'incomplete' ? 'Discovery incomplete' : (a.options || []).length ? 'Found suggestions' : a.outcome === 'signin_wall' ? 'Nothing readable: sign-in required' : 'Nothing found';
      return `<div role="status" data-unknown-outcome="${esc(a.outcome)}">${esc(text)}</div>`
        + discoveredPaths().map(([p, label]) => `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-unknown-found="${p}">${esc(label)}</button>`).join('')
        + fallback;
    },
    details: (api) => {
      const a = ds().answer; if (!a) return '';
      const evidence = (a.options || []).map((o) => `${o.title || o.method}: ${o.evidence || ''} ${o.evidence_url || ''} ${o.guidance || ''}`)
        .concat((a.problems || []).map((p) => p.message), a.notes || [], a.warning || []);
      return api.paged('evidence', evidence, (text) => `<div class="desk-v1-cfw-fact-text">${esc(text)}</div>`);
    },
    primary: (api) => ({ label: ds().busy ? 'Cancel' : ds().answer || ds().error ? 'Look it up again' : 'Look it up', run: async () => {
      const info = discoverInfo(api), ctx = { api: api.ctx.api, repaint: api.repaint };
      if (ds().busy) { lookupGen++; await D().cancel(info, ctx); api.release('lookup'); return false; }
      api.own('lookup', () => { lookupGen++; D().reset(); }, 'type');
      const mine = ++lookupGen;
      D().run(info, ctx).then(() => { if (mine === lookupGen) api.release('lookup'); }).catch((e) => { console.warn('[connect-unknown] lookup failed: ' + e); if (mine === lookupGen) api.release('lookup'); });
      return false;
    } }),
    bind: (root, api) => {
      root.querySelectorAll('[data-unknown-found]').forEach((b) => b.addEventListener('click', () => choose(api, b.dataset.unknownFound)));
      root.querySelector('[data-unknown-reference]')?.addEventListener('click', () => choose(api, 'reference'));
      if (ds().busy) {
        const back = root.querySelector('[data-cfw-back]');
        back?.addEventListener('click', () => { lookupGen++; D().reset(); });
      }
    }, discard: () => { lookupGen++; D()?.reset(); } });
  W.registerScreen({ id: 'unknown-mcp-choice', step: 'setup', match: (sel) => sel.type === 'mcp' && !['custom-npm', 'custom-remote'].includes(sel.variant),
    title: () => 'Add an MCP server', copy: () => 'Use a package or a remote server address.',
    body: () => rows([['custom-npm', 'Package'], ['custom-remote', 'Server address']], 'data-unknown-mcp', mcp),
    primary: (api) => ({ label: 'Continue', disabled: !mcp, run: async () => { const variant = mcp; api.select({ variant }); api.go('setup'); return false; } }),
    bind: (root) => root.querySelectorAll('[data-unknown-mcp]').forEach((n) => n.addEventListener('change', () => { mcp = n.dataset.unknownMcp; root.querySelector('[data-cfw-primary]').disabled = false; })), discard: () => { mcp = ''; } });
  // Narrow dispatch for unavailable built-in APIs (including LinkedIn). This is
  // visibly a reference path and cannot match the provider API Setup/Review.
  function details(info) {
    return !(info.picker || []).some((p) => p.id === 'api') ? '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-unknown-api-details>Save API details (reference only)</button>' : '';
  }
  function bindDetails(root, api) { root.querySelector('[data-unknown-api-details]')?.addEventListener('click', () => choose(api, 'api')); }
  window.DeskV1ConnectUnknownStep = { unknown, details, bindDetails };
})();
