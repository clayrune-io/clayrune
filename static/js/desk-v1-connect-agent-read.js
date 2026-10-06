// Desk v1 — Connections: the sites an AGENT may read through a browser-pane connection
// (MC-1059 piece B; MC-1056 built the policy, the Browser pane's login menu was its only editor).
//
// A connection the Desk reads through the browser pane (`read_via: 'pane'`) signs in through a
// saved browser profile. This adds one block to that connection's Read-via row: which sites an
// unattended agent may ask a laundered question about through that SAME profile, and a way to
// change the list. It is the same list the pane's menu edits (`GET /api/browser/agent-read`,
// `PUT /api/browser/profiles/<name>/agent-read`), so the two screens always agree.
//
// The write is human-only: every change goes through humanProofFetch (the dashboard passcode,
// retyped) and the server refuses an agent's PUT. An agent never gets page text back from a read.
//
// The connection names its profile (`browser_profile`) once the person has set one; before that
// the block offers the saved profiles to choose from rather than guessing which one the pane
// would pick. Window-bridged module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const API = () => window.API_BASE || '';

  function html(ch) {
    if (!ch || (ch.read_via === 'api')) return '';
    return `<div class="desk-v1-agentread" data-agentread-for="${esc(ch.id)}">
        <span class="desk-v1-how-field-label">Agents may read</span>
        <div data-agentread-body><div class="desk-v1-rules-hint">Loading…</div></div>
      </div>`;
  }

  async function _json(url) {
    const r = await fetch(API() + url);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  function _chips(domains) {
    if (!domains.length) return '<div class="desk-v1-rules-hint" data-agentread-empty>No site. Agents cannot read through this sign-in.</div>';
    return `<ul class="desk-v1-agentread-list">${domains.map((d) => `
        <li data-agentread-site="${esc(d)}"><span>${esc(d)}</span>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-agentread-remove="${esc(d)}" aria-label="${esc('Stop agents reading ' + d)}">Remove</button></li>`).join('')}</ul>`;
  }

  function _body(st) {
    const picker = st.fixedProfile ? '' : `
        <label class="desk-v1-conn-readvia-profile">Through the saved sign-in
          <select data-agentread-profile>${st.profiles.map((p) => `<option value="${esc(p)}"${p === st.profile ? ' selected' : ''}>${esc(p)}</option>`).join('')}</select></label>`;
    if (!st.profile) {
      return '<div class="desk-v1-rules-hint" data-agentread-noprofile>Set a signed-in browser profile above (or sign in once in the Browser pane) to choose what agents may read through it.</div>';
    }
    return `${picker}
        ${_chips(st.domains)}
        <div class="desk-v1-agentread-add">
          <input type="text" class="desk-v1-rules-textinput" data-agentread-input maxlength="253" placeholder="linkedin.com" aria-label="Site agents may read">
          <button type="button" class="desk-v1-conn-btn" data-agentread-add>Allow site</button>
        </div>
        <div class="desk-v1-rules-hint">Agents get an answer about a page on these sites, never the page, and cannot click or type. Changing the list asks for your passcode.</div>
        <div class="desk-v1-rules-hint" data-agentread-status></div>`;
  }

  // The one write: the profile's whole policy, behind the passcode prompt. `description` lets a
  // caller that knows more (the Connect wizard's browser Read step) say it in the prompt.
  async function put(profile, domains, description) {
    if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
    const res = await window.humanProofFetch(
      API() + '/api/browser/profiles/' + encodeURIComponent(profile) + '/agent-read',
      { method: 'PUT', body: JSON.stringify({ enabled: domains.length > 0, domains }) },
      { title: 'Agent reads', description: description || (domains.length
        ? `Re-enter your dashboard passcode to let agents read ${domains.join(', ')} through "${profile}".`
        : `Re-enter your dashboard passcode to stop agents reading through "${profile}".`) });
    if (res === null) return null;                              // cancelled: nothing was sent
    if (!res.ok) throw new Error((res.body && (res.body.error || res.body.message)) || ('HTTP ' + res.status));
    return (res.body && res.body.domains) || domains;
  }
  const _save = (st, domains) => put(st.profile, domains);

  // Every profile's policy and the saved profile names, as the Browser pane's own menu reads them.
  async function loadPolicies() {
    const [pol, prof] = await Promise.all([_json('/api/browser/agent-read'), _json('/api/browser/profiles')]);
    return { policies: (pol && pol.profiles) || {}, saved: ((prof && prof.profiles) || []).map((p) => p.name) };
  }

  function _paint(mount, st) {
    mount.innerHTML = _body(st);
    const status = mount.querySelector('[data-agentread-status]');
    const say = (t) => { if (status) status.textContent = t || ''; };
    const change = async (next, doneWords) => {
      try {
        const saved = await _save(st, next);
        if (saved === null) return;
        st.domains = saved;
        _paint(mount, st);
        const s2 = mount.querySelector('[data-agentread-status]');
        if (s2) s2.textContent = doneWords;
      } catch (e) { say('Could not save it: ' + (e && e.message ? e.message : e)); }
    };
    mount.querySelectorAll('[data-agentread-remove]').forEach((b) => b.addEventListener('click', () => {
      change(st.domains.filter((d) => d !== b.dataset.agentreadRemove), 'Removed.');
    }));
    const input = mount.querySelector('[data-agentread-input]');
    const add = () => {
      const v = (input.value || '').trim();
      if (!v) return;
      change(st.domains.concat([v]), 'Saved.');
    };
    const addBtn = mount.querySelector('[data-agentread-add]');
    if (addBtn) addBtn.addEventListener('click', add);
    if (input) input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); add(); } });
    const pick = mount.querySelector('[data-agentread-profile]');
    if (pick) pick.addEventListener('change', () => { st.profile = pick.value; st.domains = (st.policies[st.profile] || {}).domains || []; _paint(mount, st); });
  }

  // Fill the block `html(ch)` drew inside `row`. Never throws: a failed load says so in place.
  async function bind(row, ch) {
    const host = row && row.querySelector(`[data-agentread-for="${CSS.escape(ch.id)}"]`);
    const mount = host && host.querySelector('[data-agentread-body]');
    if (!mount) return;
    try {
      const { policies, saved } = await loadPolicies();
      const fixed = (ch.browser_profile || '').trim().toLowerCase();
      const profile = fixed || (saved.includes('main') ? 'main' : (saved[0] || ''));
      if (!host.isConnected) return;                     // the screen repainted while this loaded
      _paint(mount, { fixedProfile: !!fixed, profile, profiles: saved, policies,
                      domains: ((policies[profile] || {}).domains) || [] });
    } catch (e) {
      mount.innerHTML = '<div class="desk-v1-rules-hint" data-agentread-error>Could not load which sites agents may read.</div>';
    }
  }

  window.DeskV1ConnectAgentRead = { html, bind, put, loadPolicies };
})();
