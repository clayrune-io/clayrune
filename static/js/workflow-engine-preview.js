// A passive resolution response must never rebuild editable workflow fields.
export function refreshWorkflowEnginePreview(st, {resolveEngine, warningFor, paletteLine, benchLookup}) {
  if (!st?.def) return;
  const warnings = new Map();
  for (const node of st.def.nodes || []) {
    if (node.type !== 'agent') continue;
    const info = resolveEngine(st, node);
    const warning = st.runErrors?.[node.name] ? '' : warningFor(st, node, info);
    warnings.set(node.name, warning);
    const el = document.querySelector(`.wfb-node[data-name="${CSS.escape(node.name)}"]`);
    if (!el) continue;
    const avatar = el.querySelector('.wfb-node-avatar');
    if (avatar) avatar.title = info.text;
    el.classList.toggle('wfb-node-warning', !!warning);
    let badge = el.querySelector('.wfb-node-validation-warning');
    if (warning && !badge) {
      badge = document.createElement('span');
      badge.className = 'wfb-node-validation-badge wfb-node-validation-warning';
      badge.textContent = '⚠';
      el.appendChild(badge);
    }
    if (badge) { if (warning) badge.title = warning; else badge.remove(); }
  }
  const body = document.querySelector('#wfb-inspector .wfb-inspector-body');
  if (body) {
    const warning = warnings.get(st.inspectorOpen) || '';
    let inspector = body.querySelector('.wfb-node-inline-warning');
    if (warning && !inspector) {
      inspector = document.createElement('div');
      inspector.className = 'wfb-node-inline-warning';
      body.prepend(inspector);
    }
    if (inspector) { if (warning) inspector.textContent = warning; else inspector.remove(); }
  }
  const pop = document.getElementById('wfb-palette-popover');
  if (pop && !pop.classList.contains('hidden') && pop.dataset.engineName) {
    const person = benchLookup(st, pop.dataset.engineScope, pop.dataset.engineName);
    const label = pop.querySelector('.wfb-palette-popover-engine');
    if (person && label) {
      label.textContent = paletteLine(st, person);
      const rect = pop.getBoundingClientRect();
      if (rect.bottom > innerHeight - 8) pop.style.top = Math.max(8, innerHeight - rect.height - 8) + 'px';
    }
  }
}
