// Desk v1 (backlog c7ac1e8c, Ron 2026-10-02: "for a non expert user (such as
// myself), we should be able to guide him how to connect his account") — the
// guided Connect flows on Connections. Window-bridged module, no `import`.
//
//   signIn(service, ui)        Higgsfield and X: start the sign-in on the server
//                              (human-only, retyped passcode), open the sign-in
//                              page in the browser pane on the service's own saved
//                              profile, then poll until the server has the result.
//   keyGuideHTML / bindKeyGuide  Gemini, OpenAI, Higgsfield's API key: numbered
//                              steps with the exact page link, a paste box that
//                              saves through the passcode-gated Secrets write
//                              path, and a Test connection button (one free read).
//   xWizardHTML / bindXWizard  X: create the developer app, paste its Client ID
//                              (Native App: no secret; a Web App's secret sits under
//                              Advanced), sign in.
//   linkedinHTML               LinkedIn: what to apply for, one line and a link.
//
// Nothing here holds a credential longer than one request: a pasted value goes
// straight from its box to POST /api/secrets and the box is emptied at once; the
// server never answers with one. The sign-in's tokens never reach this page: it
// learns only connected / needs sign-in / not connected. User-facing words are
// plain ("sign in", "saved securely"); no em-dashes.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _api(method, url, body) { return window.DeskV1Store.api(method, url, body); }
  function _toast(msg) { if (window.DeskV1Kit && window.DeskV1Kit.toast) window.DeskV1Kit.toast(msg); }

  // A human-only request: the dashboard passcode is retyped for each call. Resolves
  // to the parsed body; rejects with the SERVER's reason. `cancelled` = no passcode.
  async function humanPost(method, url, body, proof) {
    if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
    const res = await window.humanProofFetch(url, { method, body: JSON.stringify(body || {}) }, proof);
    if (res === null) { const e = new Error('the dashboard passcode was not entered, so nothing was sent'); e.cancelled = true; throw e; }
    if (!res.ok) {
      const e = new Error((res.body && (res.body.error || res.body.message)) || `HTTP ${res.status}`);
      e.status = res.status; e.body = res.body;
      throw e;
    }
    return res.body;
  }

  let _overview = null;
  function overview(opts) {
    if (_overview && !(opts && opts.force)) return Promise.resolve(_overview);
    return _api('GET', '/api/desk/connect/status').then((o) => { _overview = o || {}; return _overview; });
  }

  const LABEL = { higgsfield: 'Higgsfield', x: 'X' };

  // ── sign-in (shared) ─────────────────────────────────────────────────────
  // ui = { say(text), alive() }. Resolves { ok, message }.
  async function signIn(service, ui) {
    const label = LABEL[service] || service;
    const say = (t) => { if (ui && ui.say) ui.say(t); };
    const alive = () => !ui || !ui.alive || ui.alive();
    let start;
    try {
      start = await humanPost('POST', `/api/desk/connect/${encodeURIComponent(service)}/start`, {}, {
        title: `Sign in to ${label}`,
        description: `Re-enter your dashboard passcode to start signing in to ${label}. The sign-in page opens next to this one.`,
      });
    } catch (e) {
      return { ok: false, cancelled: !!e.cancelled, code: e && e.body ? e.body.code : undefined, message: e && e.message ? e.message : String(e) };
    }
    say(`The ${label} sign-in page is opening. Finish signing in there; this page updates by itself.`);
    try {
      if (typeof window.openBrowserPane === 'function') await window.openBrowserPane(start.auth_url, null, null, start.profile);
      else say(`Open this address to sign in: ${start.auth_url}`);
    } catch (e) { /* the pane failing to open does not end the flow: the link below still works */ }
    const deadline = Date.now() + 10 * 60 * 1000;
    while (Date.now() < deadline && alive()) {
      await new Promise((r) => setTimeout(r, window.__deskGuidePollMs || 1500));
      if (!alive()) break;
      let f;
      try { f = await _api('GET', `/api/desk/connect/flows/${encodeURIComponent(start.flow_id)}`); } catch (e) { continue; }
      if (f.status === 'done') { _overview = null; return { ok: true, message: `${label} is connected.` }; }
      if (f.status === 'error') return { ok: false, message: f.message || `${label} did not accept the sign-in.` };
      if (f.status === 'unknown') return { ok: false, message: f.message || 'The sign-in is no longer waiting. Start again.' };
    }
    return { ok: false, message: alive() ? 'The sign-in took too long. Start again.' : '' };
  }

  async function disconnect(service) {
    const label = LABEL[service] || service;
    const out = await humanPost('POST', `/api/desk/connect/${encodeURIComponent(service)}/disconnect`, {}, {
      title: `Disconnect ${label}`,
      description: `Re-enter your dashboard passcode to disconnect ${label}. Its saved sign-in is removed from this computer and you will need to sign in again to use it.`,
    });
    _overview = null;
    return out;
  }

  // ── guided key paste (Gemini, OpenAI, Higgsfield API key) ────────────────
  const KEY_GUIDES = {
    google: {
      service: 'gemini', label: 'Gemini', vault: 'gemini-api', test: true,
      link: 'https://aistudio.google.com/apikey', linkLabel: 'Google AI Studio API keys',
      steps: ['Sign in with your Google account.', 'Press “Create API key” and copy the key it shows.',
        'Video needs billing turned on for that Google project, or Veo will refuse the job.'],
      keyLabel: 'Gemini API key',
    },
    openai: {
      service: 'openai', label: 'OpenAI', vault: 'openai-api', test: true,
      link: 'https://platform.openai.com/api-keys', linkLabel: 'OpenAI API keys',
      steps: ['Sign in to your OpenAI account.', 'Press “Create new secret key”, name it, and copy the key it shows.',
        'This is separate from a ChatGPT plan: API use is billed on its own page.'],
      keyLabel: 'OpenAI API key',
    },
    higgsfield: {
      service: null, label: 'Higgsfield (API key)', vault: 'higgsfield', test: false,
      link: 'https://console.higgsfield.ai', linkLabel: 'Higgsfield console',
      steps: ['Sign in to the Higgsfield console.', 'Create an API key. It shows a key ID and a key secret; copy both.',
        'This route is billed in dollars on the API account, not from your plan credits.'],
      keyLabel: 'API key secret', userLabel: 'API key ID',
    },
  };

  function keyGuideFor(engineId) { return KEY_GUIDES[engineId] || null; }

  function keyGuideHTML(engineId, connected) {
    const g = KEY_GUIDES[engineId];
    if (!g) return '';
    const stepsHTML = g.steps.map((s) => `<li>${esc(s)}</li>`).join('');
    return `<div class="desk-v1-guide" data-guide="${esc(engineId)}">
      <ol class="desk-v1-guide-steps">
        <li>Open <a href="${esc(g.link)}" target="_blank" rel="noopener noreferrer" data-guide-link>${esc(g.linkLabel)}</a>.</li>
        ${stepsHTML}
        <li>Paste it here and press Save. It is saved securely on this computer and never shown again.
          <div class="desk-v1-guide-paste">
            ${g.userLabel ? `<label>${esc(g.userLabel)} <input type="text" class="desk-v1-rules-textinput" data-guide-user autocomplete="off" spellcheck="false"></label>` : ''}
            <label>${esc(g.keyLabel)} <input type="password" class="desk-v1-rules-textinput" data-guide-key autocomplete="off" spellcheck="false" placeholder="${connected ? 'Paste a new key to replace the saved one' : 'Paste the key'}"></label>
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-guide-save>${connected ? 'Replace key' : 'Save key'}</button>
          </div>
        </li>
        ${g.test ? `<li>Check it works. <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-guide-test${connected ? '' : ' disabled'}>Test connection</button></li>` : ''}
      </ol>
      <div class="desk-v1-guide-result" data-guide-result role="status"></div>
    </div>`;
  }

  function _result(root, text, state) {
    const out = root.querySelector('[data-guide-result]');
    if (!out) return;
    out.textContent = text || '';
    out.dataset.state = state || '';
  }

  // Save a pasted value through the Secrets write path (human-only, passcode).
  // The box is emptied BEFORE the request so a value never lingers in the DOM.
  async function saveSecret(name, value, extra) {
    const body = Object.assign({ name, value, scope: 'global', allow_unattended: true,
      description: 'Saved from Connections' }, extra || {});
    await humanPost('POST', '/api/secrets', body, {
      title: 'Save this key',
      description: 'Re-enter your dashboard passcode to save this key securely on this computer.',
    });
  }

  async function runTest(service, root) {
    _result(root, 'Checking…', 'pending');
    try {
      const out = await _api('POST', `/api/desk/connect/${encodeURIComponent(service)}/test`, {});
      _result(root, out.message || (out.ok ? 'Connected.' : 'Not accepted.'), out.ok ? 'ok' : 'bad');
      return !!out.ok;
    } catch (e) {
      _result(root, e && e.message ? e.message : String(e), 'bad');
      return false;
    }
  }

  function bindKeyGuide(root, engineId, hooks) {
    const g = KEY_GUIDES[engineId];
    const guide = root.querySelector(`[data-guide="${CSS.escape(engineId)}"]`);
    if (!g || !guide) return;
    const keyBox = guide.querySelector('[data-guide-key]');
    const userBox = guide.querySelector('[data-guide-user]');
    const save = guide.querySelector('[data-guide-save]');
    const test = guide.querySelector('[data-guide-test]');
    save.onclick = async () => {
      const value = keyBox.value.trim();
      const user = userBox ? userBox.value.trim() : '';
      if (!value) { _result(guide, `Paste the ${g.keyLabel} first.`, 'bad'); return; }
      if (userBox && !user) { _result(guide, `Paste the ${g.userLabel} too.`, 'bad'); return; }
      keyBox.value = ''; if (userBox) userBox.value = '';
      save.disabled = true;
      try {
        await saveSecret(g.vault, value, userBox ? { username: user } : {});
      } catch (e) {
        _result(guide, e && e.message ? e.message : String(e), 'bad');
        save.disabled = false;
        return;
      }
      save.disabled = false;
      _toast(`${g.label} key saved`);
      if (hooks && hooks.onSaved) hooks.onSaved();
      if (g.test && g.service) {
        if (test) test.disabled = false;
        const ok = await runTest(g.service, guide);
        if (ok && hooks && hooks.onVerified) hooks.onVerified();
      } else {
        _result(guide, 'Saved. It is used the first time you render.', 'ok');
      }
    };
    if (test) test.onclick = async () => {
      test.disabled = true;
      const ok = await runTest(g.service, guide);
      test.disabled = false;
      if (ok && hooks && hooks.onVerified) hooks.onVerified();
    };
  }

  // ── X wizard ──────────────────────────────────────────────────────────────
  // docs.x.com/resources/fundamentals/developer-portal (read 2026-10-02): the Developer Console is console.x.com.
  const X_PORTAL = 'https://console.x.com';
  const _xOpen = { open: false };

  function xWizardHTML(ov) {
    const x = (ov && ov.x) || { state: 'not_connected', app: {}, callback_url: '', scopes: [] };
    const app = x.app || {};
    const connected = x.state === 'connected';
    const locked = x.state === 'vault_locked';      // the sign-in is saved: unlock, do not sign in again
    const cb = x.callback_url || '';
    const scopes = (x.scopes || []).join(' ');
    return `<div class="desk-v1-guide desk-v1-xwizard" data-guide="x" data-x-state="${esc(x.state)}">
      <section class="desk-v1-guide-step" data-x-step="1">
        <div class="desk-v1-guide-step-head"><span class="desk-v1-guide-num">1</span> Create an app on X <span class="desk-v1-guide-done" data-x-done="1"${app.client_id ? '' : ' hidden'}>done</span></div>
        <p class="desk-v1-guide-lede">X needs a small app of yours so Clayrune can post for you. You do this once.</p>
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-x-portal>Open the X developer page</button>
        <ol class="desk-v1-guide-steps">
          <li>Sign in with the X account you want to post from.</li>
          <li>Make sure pay-per-use billing is on: buy some credits in the console. X charges per post, so without credits posting is refused.</li>
          <li>Press “New App” and give it any name.</li>
          <li>Open the app's settings and set up user authentication.</li>
          <li>App permissions: choose “Read and write”.</li>
          <li>Type of app: choose “Native App”. It has no secret to keep track of.</li>
          <li>Callback URI / Redirect URL: paste exactly <code data-x-callback>${esc(cb)}</code>
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-x-copy="${esc(cb)}">Copy</button></li>
          <li>Website URL: any web address, for example your own site.</li>
          <li>Save. X then shows a Client ID. Keep that page open for the next step.</li>
        </ol>
        <p class="desk-v1-guide-note">When you sign in, Clayrune asks X for these permissions: <code>${esc(scopes)}</code>. Posting and reading your own account, and staying signed in.</p>
      </section>
      <section class="desk-v1-guide-step" data-x-step="2">
        <div class="desk-v1-guide-step-head"><span class="desk-v1-guide-num">2</span> Paste your app's details <span class="desk-v1-guide-done" data-x-done="2"${app.client_id ? '' : ' hidden'}>saved</span></div>
        <div class="desk-v1-guide-paste">
          <label>Client ID <input type="password" class="desk-v1-rules-textinput" data-x-client-id autocomplete="off" spellcheck="false" placeholder="${app.client_id ? 'Saved. Paste a new one to replace it' : 'Paste the Client ID'}"></label>
          <details class="desk-v1-conn-advanced" data-x-advanced${app.client_secret ? ' open' : ''}>
            <summary>Advanced: my app is a Web App</summary>
            <label>Client Secret <input type="password" class="desk-v1-rules-textinput" data-x-client-secret autocomplete="off" spellcheck="false" placeholder="${app.client_secret ? 'Saved. Paste a new one to replace it' : 'Only if X showed one'}"></label>
          </details>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-x-save>Save app details</button>
        </div>
      </section>
      <section class="desk-v1-guide-step" data-x-step="3">
        <div class="desk-v1-guide-step-head"><span class="desk-v1-guide-num">3</span> Sign in with X <span class="desk-v1-guide-done" data-x-done="3"${connected ? '' : ' hidden'}>connected</span></div>
        <p class="desk-v1-guide-lede">${connected ? 'X is connected. Posts go out from your signed-in account.'
          : locked ? esc(x.reason || 'Your vault is locked. Unlock it; your sign-in is still saved.')
          : x.state === 'needs_signin' ? esc(x.reason || 'The saved sign-in ran out. Sign in again.')
          : 'Press the button, then approve Clayrune on the X page that opens.'}</p>
        <button type="button" class="desk-v1-conn-btn" data-x-signin${app.client_id && !locked ? '' : ' disabled'}>${connected ? 'Sign in again' : 'Sign in with X'}</button>
        ${locked && window.VaultUnlockUI ? window.VaultUnlockUI.buttonHTML(x) : ''}
        ${connected ? '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-x-disconnect>Disconnect</button>' : ''}
      </section>
      <div class="desk-v1-guide-result" data-guide-result role="status"></div>
    </div>`;
  }

  function bindXWizard(root, hooks) {
    const w = root.querySelector('[data-guide="x"]');
    if (!w) return;
    const say = (t, s) => _result(w, t, s);
    const done = () => { _overview = null; if (hooks && hooks.onChange) hooks.onChange(); };
    if (window.DeskV1VaultGate) window.DeskV1VaultGate.attach(w, w.dataset.xState === 'vault_locked' ? { onUnlocked: done } : undefined);    // saving the app and signing in both write to the vault; an unlock re-reads the card
    const portal = w.querySelector('[data-x-portal]');
    portal.onclick = () => {
      if (typeof window.openBrowserPane === 'function') window.openBrowserPane(X_PORTAL, null, null, 'desk-x');
      else window.open(X_PORTAL, '_blank', 'noopener');
    };
    const copy = w.querySelector('[data-x-copy]');
    if (copy) copy.onclick = () => {
      const t = copy.dataset.xCopy;
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(t).then(() => say('Copied.', 'ok'), () => say('Select the address above and copy it.', 'bad'));
      else say('Select the address above and copy it.', 'bad');
    };
    const idBox = w.querySelector('[data-x-client-id]');
    const secBox = w.querySelector('[data-x-client-secret]');
    const save = w.querySelector('[data-x-save]');
    save.onclick = async () => {
      const id = idBox.value.trim();
      const sec = secBox.value.trim();
      if (!id && !sec) { say('Paste the Client ID first.', 'bad'); return; }
      idBox.value = ''; secBox.value = '';
      save.disabled = true;
      try {
        if (id) await saveSecret('x.client-id', id, { description: 'X app Client ID' });
        if (sec) await saveSecret('x.client-secret', sec, { description: 'X app Client Secret' });
      } catch (e) {
        say(e && e.message ? e.message : String(e), 'bad');
        save.disabled = false;
        return;
      }
      _toast('X app details saved');
      done();
    };
    const signin = w.querySelector('[data-x-signin]');
    signin.onclick = async () => {
      signin.disabled = true;
      const out = await signIn('x', { say: (t) => say(t, 'pending'), alive: () => w.isConnected });
      if (!w.isConnected) return;
      if (out.ok) { _toast('X is connected'); done(); return; }
      if (out.message) say(out.message, 'bad');
      if (out.code === 'vault_locked' && window.DeskV1VaultGate) window.DeskV1VaultGate.attach(w);
      signin.disabled = false;
    };
    const off = w.querySelector('[data-x-disconnect]');
    if (off) off.onclick = async () => {
      off.disabled = true;
      try { await disconnect('x'); _toast('X disconnected'); done(); } catch (e) { say(e && e.message ? e.message : String(e), 'bad'); off.disabled = false; }
    };
  }

  // ── LinkedIn ──────────────────────────────────────────────────────────────
  const LINKEDIN_DOCS = 'https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview';
  function linkedinHTML() {
    return `<div class="desk-v1-guide-note" data-guide="linkedin">Waiting for LinkedIn approval. To post as a Company Page, apply for LinkedIn's “Community Management API” on your LinkedIn app so it can post for your page (<a href="${esc(LINKEDIN_DOCS)}" target="_blank" rel="noopener noreferrer" data-guide-link>how to apply</a>).</div>`;
  }

  window.DeskV1Guides = {
    humanPost, overview, signIn, disconnect, keyGuideFor, keyGuideHTML, bindKeyGuide,
    xWizardHTML, bindXWizard, linkedinHTML, xOpen: _xOpen,
  };
})();
