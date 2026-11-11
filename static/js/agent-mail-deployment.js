// Settings > Connectivity > Agent mail deployment. Public data only; every save
// uses the shared retyped-passcode prompt. Enrollment and mail flags stay separate.
(function () {
  const PATH = '/api/agent-mail/deployment';
  const ROOT = 'agent_mail_root_public_key';
  const ORIGIN = 'agent_mail_service_origin';
  const api = () => (typeof API_BASE === 'string' ? API_BASE : '') + PATH;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let saving = false;

  function settingsHTML() {
    return `<div class="settings-section" id="agent-mail-deployment-section">
      <div class="settings-section-title">Agent mail deployment</div>
      <div class="settings-hint">Operator setup: use the public root key from your trusted offline
        signing kit and the exact service origin signed in its manifest. Changing the root changes
        which service manifests you trust. Saving does not enable mail or reset existing pins.</div>
      <div id="agent-mail-deployment-host" aria-live="polite">Loading deployment settings...</div>
    </div>`;
  }

  function paint(host, data) {
    host.innerHTML = `<form data-am-deployment-form>
      <label class="settings-hint" for="am-deployment-root">Root public key (43 characters, base64url)</label>
      <input class="am-deployment-input" id="am-deployment-root" name="${ROOT}" type="text"
        autocomplete="off" autocapitalize="none" spellcheck="false" required pattern="[A-Za-z0-9_-]{43}"
        style="box-sizing:border-box;width:100%;min-width:0" value="${esc(data[ROOT])}">
      <div class="settings-hint" data-am-deployment-root-state>${data.set[ROOT] ? 'Set' : 'Not set'}</div>
      <label class="settings-hint" for="am-deployment-origin">Service origin (https://host)</label>
      <input class="am-deployment-input" id="am-deployment-origin" name="${ORIGIN}" type="text"
        autocomplete="off" autocapitalize="none" spellcheck="false" required
        style="box-sizing:border-box;width:100%;min-width:0" value="${esc(data[ORIGIN])}">
      <div class="settings-hint" data-am-deployment-origin-state>${data.set[ORIGIN] ? 'Set' : 'Not set'}</div>
      <div class="settings-hint">HTTPS only, without a path, port, query or trailing slash.
        Use public .pub contents only; never paste a private key.</div>
      <button class="btn-add" type="submit">Save deployment settings</button>
      <div class="settings-hint" data-am-deployment-result role="status"></div>
    </form>`;
    host.querySelector('form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (saving) return;
      const form = ev.currentTarget;
      const button = form.querySelector('button');
      const output = form.querySelector('[data-am-deployment-result]');
      const body = { [ROOT]: form.elements[ROOT].value, [ORIGIN]: form.elements[ORIGIN].value };
      saving = true; button.disabled = true;
      try {
        if (typeof window.humanProofFetch !== 'function') throw new Error('Passcode prompt unavailable.');
        const result = await window.humanProofFetch(api(), { method: 'PUT', body: JSON.stringify(body) }, {
          title: 'Save mail deployment settings',
          description: 'Re-enter your dashboard passcode to change the root of trust and service origin for agent mail.',
        });
        if (result === null) { output.textContent = 'Cancelled. Nothing saved.'; return; }
        if (!result.ok) {
          const messages = {
            invalid_root_public_key: 'Use the canonical 43-character public key from root.pub.',
            invalid_service_origin: 'Use an HTTPS hostname with no path, port, query or trailing slash.',
            human_required: 'A human must save these settings.',
            storage_unavailable: 'Could not persist settings. Nothing changed.',
          };
          output.textContent = messages[result.body?.error] || 'Could not save deployment settings.';
          return;
        }
        // Read the persisted values back rather than treating HTTP 200 as proof.
        await refresh();
        const live = document.querySelector('[data-am-deployment-result]');
        if (live) live.textContent = 'Deployment settings saved.';
      } catch (_) {
        output.textContent = 'Could not complete the save. Refresh settings to check the current values.';
      } finally {
        saving = false; button.disabled = false;
      }
    });
  }

  async function refresh() {
    const host = document.getElementById('agent-mail-deployment-host');
    if (!host) return;
    try {
      const response = await fetch(api(), { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok || !data || typeof data[ROOT] !== 'string' || typeof data[ORIGIN] !== 'string'
          || !data.set || typeof data.set[ROOT] !== 'boolean' || typeof data.set[ORIGIN] !== 'boolean')
        throw new Error('Deployment settings unavailable.');
      if (document.getElementById('agent-mail-deployment-host') === host) paint(host, data);
    } catch (_) {
      host.textContent = 'Could not read deployment settings. Reopen Settings to retry.';
    }
  }

  window.agentMailDeploymentSettingsHTML = settingsHTML;
  window.refreshAgentMailDeployment = refresh;
})();
