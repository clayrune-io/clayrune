// ── "Agents may read" control for saved browser profiles (MC-1056) ───────────
// One small chip on each saved login in the pane's session menu: which sites an
// unattended agent may ask a laundered question about, through that profile. Off by
// default. Backend: mc/blueprints/browser_agent_read_routes.py. The write is human-only
// (the dashboard passcode is retyped through humanProofFetch; an agent's PUT gets a 403).
// An agent never gets page text back: it gets an answer written by a toolless model call.
//
// browser-pane.js calls bpAgentReadLoad() when it renders the menu, draws bpAgentReadChip()
// on each saved-login row and wires a click to bpAgentReadEdit(name, redraw). ES module → shared via
// window.* (discovery_es_module_cross_boundary_globals).

let _bpArPolicies = {};

const _bpArEsc = (s) => (typeof esc === 'function' ? esc(String(s)) :
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));

async function bpAgentReadLoad() {
  try {
    const r = await fetch((window.API_BASE || '') + '/api/browser/agent-read');
    _bpArPolicies = (await r.json()).profiles || {};
  } catch (e) { _bpArPolicies = {}; }
  return _bpArPolicies;
}

function bpAgentReadChip(name) {
  const p = _bpArPolicies[name];
  const on = !!(p && p.enabled && p.domains && p.domains.length);
  const title = on
    ? 'Agents may ask questions about pages on: ' + p.domains.join(', ') + '. Click to change.'
    : 'Agents may not read through this login. Click to allow chosen sites.';
  return `<span data-agentread="${_bpArEsc(name)}" title="${_bpArEsc(title)}"
    style="font-size:10px;padding:0 5px;border:1px solid ${on ? '#4a6a4a' : '#4a4a4a'};border-radius:99px;
    color:${on ? '#9ecb9e' : '#888'};cursor:pointer;flex:0 0 auto">${on ? 'agents: ' + _bpArEsc(p.domains.length === 1 ? p.domains[0] : p.domains.length + ' sites') : 'agents: off'}</span>`;
}

async function bpAgentReadEdit(name, onDone) {
  const cur = (_bpArPolicies[name] && _bpArPolicies[name].domains) || [];
  const typed = prompt(
    `Which sites may an agent ask questions about, through "${name}"?\n\n` +
    'Domains, comma separated (e.g. linkedin.com, x.com). An agent gets an answer, never the page ' +
    'itself, and cannot click or type. Leave empty to switch it off.', cur.join(', '));
  if (typed === null) return;
  const domains = typed.split(',').map(s => s.trim()).filter(Boolean);
  try {
    if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
    const res = await window.humanProofFetch(
      (window.API_BASE || '') + '/api/browser/profiles/' + encodeURIComponent(name) + '/agent-read',
      { method: 'PUT', body: JSON.stringify({ enabled: domains.length > 0, domains }) },
      { title: 'Agent reads', description: domains.length
        ? `Re-enter your dashboard passcode to let agents read ${domains.join(', ')} through "${name}".`
        : `Re-enter your dashboard passcode to stop agents reading through "${name}".` });
    if (res === null) return;                      // passcode prompt cancelled; nothing was sent
    if (!res.ok) throw new Error((res.body && (res.body.error || res.body.message)) || ('HTTP ' + res.status));
  } catch (e) { alert('Could not save it: ' + e.message); }
  await bpAgentReadLoad();
  if (typeof onDone === 'function') onDone();
}

window.bpAgentReadLoad = bpAgentReadLoad;
window.bpAgentReadChip = bpAgentReadChip;
window.bpAgentReadEdit = bpAgentReadEdit;
