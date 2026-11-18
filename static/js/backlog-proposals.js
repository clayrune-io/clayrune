// ── Agent-proposed backlog items ────────────────────────────────────────────
// An item an agent adds is stored `status: 'proposed'` with a server-derived
// `proposed_by` ({kind:'agent', session_id, agent_name, attended}) and waits for
// a person (mc/backlog_proposals.py). It is LISTED but is not work: the list
// shows an "Agent proposed" chip with the agent's name, and Accept / Dismiss.
// Accept turns it into an ordinary open item (the proposer stays on the record);
// Dismiss closes it as `done` with `done_reason: 'dismissed'` — nothing is
// deleted. Both are human-only routes, so the buttons are only ever a person's.
//
// One module for both surfaces (the project Backlog tab in render-core.js and
// the cross-project list in cross-backlog.js) so the chip, the buttons and the
// sort rule cannot drift apart. Everything is read at render/click time off
// `window.backlogProposals`; no parse-time reference to it exists elsewhere.

const PROPOSED = 'proposed';

function isProposed(item) {
  return !!item && item.status === PROPOSED;
}

// 1 for a proposal, 0 otherwise. Used AFTER the existing keys (rank, priority)
// so a proposal sorts below accepted items of the same standing.
function proposedSortKey(item) {
  return isProposed(item) ? 1 : 0;
}

function _who(item) {
  const by = (item && item.proposed_by) || {};
  return String(by.agent_name || '').trim();
}

// '' for any item that is not a proposal, so call sites can interpolate it
// unconditionally. `esc` and `window.jsAttr` are the page's own escapers.
function proposalControlsHTML(projectId, item) {
  if (!isProposed(item)) return '';
  const name = _who(item);
  const by = (item.proposed_by || {});
  const how = by.attended === false ? ' (unattended run)' : '';
  const title = `Proposed by an agent${name ? ` (${name})` : ''}${how} — it waits for you; agents cannot accept it`;
  const pid = window.jsAttr(projectId);
  const iid = esc(item.id);
  return `<span class="proposal-chip" data-proposal-chip title="${esc(title)}">Agent proposed${name ? `<span class="proposal-chip-name"> · ${esc(name)}</span>` : ''}</span>`
    + `<button class="proposal-btn proposal-accept" data-proposal-accept type="button" onclick="decideProposal(event,'${pid}','${iid}','accept')" title="Accept: it becomes an ordinary open item">Accept</button>`
    + `<button class="proposal-btn proposal-dismiss" data-proposal-dismiss type="button" onclick="decideProposal(event,'${pid}','${iid}','dismiss')" title="Dismiss: closes it as dismissed (nothing is deleted)">Dismiss</button>`;
}

async function decideProposal(e, projectId, itemId, verb) {
  if (e) { e.stopPropagation(); if (e.preventDefault) e.preventDefault(); }
  if (verb !== 'accept' && verb !== 'dismiss') return;
  let res, err = '';
  try {
    res = await fetch(API_BASE + `/api/project/${encodeURIComponent(projectId)}/backlog/${encodeURIComponent(itemId)}/${verb}`,
      { method: 'POST' });
    if (!res.ok) {
      try { err = (await res.json()).error || ''; } catch (_) {}
      err = err || `HTTP ${res.status}`;
    }
  } catch (ex) { err = ex.message || 'network error'; }
  if (err) {
    if (typeof showToast === 'function') showToast(`Could not ${verb}: ${err}`, 5000);
  }
  // Same order as backlog-actions.js: refetch this project's list, THEN the tile
  // counts, or the poll copies the stale list back over the change.
  if (typeof refreshProjectBacklog === 'function') await refreshProjectBacklog(projectId);
  if (typeof refreshSilent === 'function') await refreshSilent();
}

window.backlogProposals = { isProposed, proposedSortKey, proposalControlsHTML };
window.decideProposal = decideProposal;
