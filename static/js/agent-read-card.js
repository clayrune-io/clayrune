// ── Agent-read request card (`[agent-read-request:<id>]`, MC-1059 piece A) ───
// An agent asked to read a page through a saved browser profile and was refused
// (profile off, or the site is not on its list). The SERVER then posts one marker
// line into that agent's chat; this turns it into a card:
//
//   <agent> wants to read <site> through <profile>      [Allow once] [Always allow] [Ignore]
//
// The marker carries only an id. Every word on the card (agent, site, profile, state)
// comes from GET /api/browser/agent-read/requests/<id>, never from the transcript, so a
// marker an agent types into its own reply can only show what the server recorded or
// "expired". Allow once / Always allow go through humanProofFetch (the dashboard passcode,
// retyped); the server refuses them from an agent and from a request with no passcode.
// Ignore stores nothing. Backend: mc/blueprints/browser_agent_read_request_routes.py.
//
// Same shape as exploration-card.js: a live per-line hook (`_handleAgentReadLine`, called from
// appendAgentLine) and a placeholder for the cold-render string builder, both mounted by
// `mountAgentReadCards`. ES module -> shared via window.* (window-bridged, like its siblings).

const _arcState = new Map();      // request id -> last record from the server

const _arcEsc = (s) => (typeof esc === 'function' ? esc(String(s)) :
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));

const _ARC_MARKER_RE = /^\s*\[agent-read-request:([a-f0-9]{6,32})\]\s*$/;

function agentReadCardPlaceholderHTML(rid) {
  return `<div class="agent-read-card-mount" data-rid="${_arcEsc(rid)}"></div>`;
}

function _arcBody(rec) {
  if (rec.state === 'pending') {
    return `<div class="agent-read-card-hint">Allow once lets it read one page of this site in the next 15 minutes.
      Always allow adds the site to this login's list. Either asks for your passcode.</div>
      <div class="agent-read-card-foot">
        <button type="button" class="agent-read-card-btn primary" data-arc="allow_once">Allow once</button>
        <button type="button" class="agent-read-card-btn" data-arc="always">Always allow</button>
        <button type="button" class="agent-read-card-btn quiet" data-arc="ignore">Ignore</button>
        <span class="agent-read-card-msg" data-arc-msg></span>
      </div>`;
  }
  const words = {
    allowed_once: 'Allowed once: it can read one page of this site now.',
    always: 'Always allowed: the site is on this login\'s list.',
    ignored: 'Ignored. Nothing was allowed.',
    expired: 'This request has expired.',
  };
  return `<div class="agent-read-card-foot"><span class="agent-read-card-msg" data-arc-msg>${_arcEsc(words[rec.state] || rec.state)}</span></div>`;
}

function _arcRender(mount, rec) {
  const known = rec.state !== 'expired';
  mount.innerHTML = `<div class="agent-read-card" data-state="${_arcEsc(rec.state)}">
      <div class="agent-read-card-kicker">Browser read request</div>
      ${known ? `<div class="agent-read-card-title"><b>${_arcEsc(rec.agent_name)}</b> wants to read
        <b>${_arcEsc(rec.domain)}</b> through <b>${_arcEsc(rec.profile)}</b></div>`
        : '<div class="agent-read-card-title">An agent asked to read a page.</div>'}
      ${_arcBody(rec)}
    </div>`;
  mount.querySelectorAll('[data-arc]').forEach((b) => b.addEventListener('click', () => _arcDecide(mount, rec, b.dataset.arc)));
}

async function _arcDecide(mount, rec, decision) {
  const msg = mount.querySelector('[data-arc-msg]');
  const say = (t) => { if (msg) msg.textContent = t; };
  const url = (window.API_BASE || '') + '/api/browser/agent-read/requests/' + encodeURIComponent(rec.id) + '/decision';
  const init = { method: 'POST', body: JSON.stringify({ decision }) };
  mount.querySelectorAll('[data-arc]').forEach((b) => { b.disabled = true; });
  try {
    let res;
    if (decision === 'ignore') {
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: init.body });
      res = { ok: r.ok, status: r.status, body: await r.json().catch(() => ({})) };
    } else {
      if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
      const what = decision === 'always' ? `always let ${rec.agent_name} read ${rec.domain} through "${rec.profile}"`
                                          : `let ${rec.agent_name} read one page of ${rec.domain} through "${rec.profile}"`;
      res = await window.humanProofFetch(url, init, {
        title: 'Agent read', description: `Re-enter your dashboard passcode to ${what}.` });
      if (res === null) { mount.querySelectorAll('[data-arc]').forEach((b) => { b.disabled = false; }); return; }   // cancelled; nothing sent
    }
    if (res.status === 409 || res.status === 404) { await _arcLoad(mount, rec.id, true); return; }
    if (!res.ok) throw new Error((res.body && (res.body.error || res.body.message)) || ('HTTP ' + res.status));
    const next = Object.assign({}, rec, { state: res.body.state || rec.state });
    _arcState.set(rec.id, next);
    _arcRender(mount, next);
  } catch (e) {
    mount.querySelectorAll('[data-arc]').forEach((b) => { b.disabled = false; });
    say('Could not save it: ' + e.message);
  }
}

async function _arcLoad(mount, rid, force) {
  // A settled request never changes again; a pending one may have been answered elsewhere.
  const seen = _arcState.get(rid);
  if (!force && seen && seen.state !== 'pending') { _arcRender(mount, seen); return; }
  let rec;
  try {
    const r = await fetch((window.API_BASE || '') + '/api/browser/agent-read/requests/' + encodeURIComponent(rid));
    rec = r.ok ? await r.json() : { id: rid, state: 'expired' };
  } catch (e) { rec = { id: rid, state: 'expired' }; }
  rec = Object.assign({ id: rid }, rec);
  _arcState.set(rid, rec);
  _arcRender(mount, rec);
}

function mountAgentReadCards(root) {
  const scope = root || document;
  scope.querySelectorAll('.agent-read-card-mount:not([data-arc-mounted])').forEach((mount) => {
    mount.dataset.arcMounted = '1';
    _arcLoad(mount, mount.dataset.rid || '', false);
  });
}

// Live per-line path (conversation.js appendAgentLine). True when the line was the marker.
function _handleAgentReadLine(sessionId, text, el) {
  const m = _ARC_MARKER_RE.exec(text || '');
  if (!m) return false;
  const ph = document.createElement('div');
  ph.className = 'agent-read-card-mount';
  ph.dataset.rid = m[1];
  el.appendChild(ph);
  mountAgentReadCards(el);
  return true;
}

// The cold-render builder asks one question: is this line the marker, and what is its id.
function agentReadMarkerId(line) {
  const m = _ARC_MARKER_RE.exec(line || '');
  return m ? m[1] : null;
}

window.agentReadCardPlaceholderHTML = agentReadCardPlaceholderHTML;
window.agentReadMarkerId = agentReadMarkerId;
window.mountAgentReadCards = mountAgentReadCards;
window._handleAgentReadLine = _handleAgentReadLine;
