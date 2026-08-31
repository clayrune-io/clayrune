// ── Auth banner — multi-provider ───────────────────────────────────────────
// Always checks claude (via the legacy /api/claude/auth-status alias). When
// multi_provider_enabled is on and the default provider differs from claude,
// also checks that provider and surfaces provider-specific messaging.
let _authBannerDismissed = false;
let _authBannerLastReason = null;
// First-run auth gate state. `_claudeAuthOk`: null = unverified, true = signed
// in (verified by a probe), false = confirmed not signed in. `_authProbeKicked`
// guards the one-time boot probe so we don't spawn a `claude -p ok` subprocess
// on every 90s poll.
let _claudeAuthOk = null;
let _authProbeKicked = false;

// Track the last-known CLAUDE auth verdict so the dispatch path can refuse to
// fire a doomed run. Only claude states update it (other providers pass through
// _renderAuthBanner too). "ok" only counts when a probe actually verified it
// (last_probe_at set) — the seeded optimistic ok:True default stays "unknown".
function _updateClaudeAuthKnown(state) {
  if (state && state._provider && state._provider !== 'claude') return;
  if (state && state.ok === false) _claudeAuthOk = false;
  else if (state && state.ok === true && state.last_probe_at) _claudeAuthOk = true;
  else _claudeAuthOk = null;
}

async function refreshAuthStatus() {
  try {
    // Always check claude (the original + most common provider).
    const res = await fetchFailFast(API_BASE + '/api/claude/auth-status');
    if (!res.ok) return;
    const state = await res.json();
    // Attach provider name so _renderAuthBanner can label it correctly.
    state._provider = 'claude';
    // First-run gate: the server seeds _claude_auth_state optimistically
    // (ok:true, never probed). If we've never verified (no last_probe_at) and
    // aren't already known-bad, actively probe ONCE so a not-signed-in install
    // surfaces the sign-in CTA up front instead of after a doomed dispatch.
    if (state.ok !== false && !state.last_probe_at && !_authProbeKicked) {
      _authProbeKicked = true;
      _claudeAuthProbe();  // async; re-renders on completion
    }
    _updateClaudeAuthKnown(state);
    _renderAuthBanner(state);
  } catch (e) {
    // Network blip — leave whatever banner state we have.
  }
}

// Actively probe claude auth (spawns `claude -p ok` server-side, ~fast when
// not signed in). Best-effort; renders the banner + Settings status line on
// completion. Used by the boot gate above.
async function _claudeAuthProbe() {
  try {
    const res = await fetch(API_BASE + '/api/claude/auth-probe', { method: 'POST' });
    if (!res.ok) return;
    const state = await res.json();
    state._provider = 'claude';
    _updateClaudeAuthKnown(state);
    _renderAuthBanner(state);
    _renderClaudeAuthStatusLine(state);
  } catch (e) { /* best-effort */ }
}

async function refreshProviderAuthStatus(providerName) {
  if (!providerName || providerName === 'claude') { refreshAuthStatus(); return; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${providerName}/auth`);
    if (!res.ok) return;
    const data = await res.json();
    // Normalize to the same shape as /api/claude/auth-status
    const auth = data.auth_state || {};
    const state = {
      ok: auth.status === 'ok',
      reason: auth.status !== 'ok' ? auth.status : null,
      _provider: providerName,
    };
    _renderAuthBanner(state);
  } catch (e) { /* ignore blips */ }
}

// A live claude run is itself proof the CLI is authenticated — so a claude auth
// error CANNOT be real while a claude agent is actively running or idle-waiting.
// Suppresses the false-positive banner (Ron saw "Authenticate Claude" while
// mid-conversation with a running agent — the auth probe had tripped over
// "Reached max turns" and latched ok:false). Other providers keep their own
// signal; read-only revived tabs aren't live processes.
function _hasLiveClaudeAgent() {
  try {
    const cache = window.agentStatusCache || {};
    return Object.values(cache).some(s => s
      && (s.status === 'running' || s.status === 'idle')
      && ((s.provider || 'claude') === 'claude')
      && !s._readOnlyRevived);
  } catch (e) { return false; }
}

function _renderAuthBanner(state) {
  const banner = document.getElementById('auth-banner');
  if (!banner) return;
  const ok = !state || state.ok !== false;
  if (ok) {
    banner.classList.add('hidden');
    _authBannerLastReason = null;
    return;
  }
  const _prov = (state && state._provider) || 'claude';
  if (_prov === 'claude' && _hasLiveClaudeAgent()) {
    banner.classList.add('hidden');
    return;
  }
  // If we already dismissed this exact reason, stay hidden until reason changes.
  if (_authBannerDismissed && state.reason === _authBannerLastReason) {
    banner.classList.add('hidden');
    return;
  }
  _authBannerLastReason = state.reason;
  _authBannerDismissed = false;
  const textEl = document.getElementById('auth-banner-text');
  if (textEl) textEl.textContent = _authBannerMessage(state);
  const signin = document.getElementById('auth-banner-signin');
  if (signin) {
    const prov = (state && state._provider) || 'claude';
    const provLabel = prov === 'claude' ? 'Claude'
      : ((_agentProviders || []).find(p => p.name === prov) || {}).display_name || prov;
    signin.textContent = `Authenticate ${provLabel}`;
    if (prov === 'claude') {
      signin.onclick = () => claudeAuthenticate();
    } else {
      signin.onclick = () => settingsProviderTerminalLogin(prov, signin);
    }
  }
  banner.classList.remove('hidden');
}

function _authBannerMessage(state) {
  const prov = (state && state._provider) ? state._provider : 'claude';
  const provLabel = prov === 'claude' ? 'Claude'
    : ((_agentProviders || []).find(p => p.name === prov) || {}).display_name || prov;
  switch (state && state.reason) {
    case 'not_logged_in':
      return `Log in to ${provLabel} to get started — agents can't run until you're signed in.`;
    case 'invalid_api_key':
      return `${provLabel} credentials are invalid — sign in again to refresh them.`;
    case 'cli_not_found':
      return `The \`${prov}\` CLI isn't on this machine's PATH.`;
    default:
      return `${provLabel} authentication is failing. Sign in to retry.`;
  }
}

function dismissAuthBanner() {
  _authBannerDismissed = true;
  const banner = document.getElementById('auth-banner');
  if (banner) banner.classList.add('hidden');
}

async function claudeAuthenticate() {
  // Launches `claude` in a NEW OS-level terminal window (not MC's piped
  // pop-out — claude's OAuth flow needs a real TTY). User completes browser
  // sign-in there, then clicks "Re-check" here.
  try {
    const res = await fetch(API_BASE + '/api/claude/login-launch', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      alert('Failed to launch claude: ' + (data.error || res.status));
      return;
    }
    showToast('A terminal window opened. Type /login in it to sign in, then click Re-check here.', 12000);
  } catch (e) {
    alert('Failed to launch claude: ' + e);
  }
}

async function claudeAuthRecheck() {
  const btn = document.getElementById('auth-banner-recheck');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking...'; }
  try {
    const res = await fetch(API_BASE + '/api/claude/auth-probe', { method: 'POST' });
    const state = await res.json();
    _renderAuthBanner(state);
    _renderClaudeAuthStatusLine(state);
  } catch (e) {
    // ignore
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Re-check'; }
  }
}

// Settings panel: explicit Sign-in + Check-status buttons (banner is best-effort,
// these are the user's escape hatch when the banner doesn't surface).
async function settingsClaudeLogin() {
  try {
    const res = await fetch(API_BASE + '/api/claude/login-launch', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      alert('Failed to launch claude: ' + (data.error || res.status));
      return;
    }
    showToast('A terminal window opened. Type /login in it to sign in, then click Check status.', 12000);
  } catch (e) {
    alert('Failed to launch claude: ' + e);
  }
}

async function settingsClaudeAuthCheck() {
  const line = document.getElementById('claude-auth-status-line');
  if (line) line.innerHTML = '<span style="color:var(--text-faint)">Checking...</span>';
  try {
    const res = await fetch(API_BASE + '/api/claude/auth-probe', { method: 'POST' });
    const state = await res.json();
    _renderAuthBanner(state);
    _renderClaudeAuthStatusLine(state);
  } catch (e) {
    if (line) line.innerHTML = '<span style="color:#ef4444">Check failed</span>';
  }
}

// ── Provider Auth helpers (Gemini, Codex, Aider, ...) ─────────────────────
// One env var per provider. For OAuth providers (gemini), users can also
// click "Launch terminal login" to complete the browser flow.
const PROVIDER_AUTH_KEYS = {
  gemini:   'GEMINI_API_KEY',
  codex:    'OPENAI_API_KEY',
  aider:    'OPENAI_API_KEY',
  opencode: 'OPENCODE_API_KEY',
  goose:    'OPENAI_API_KEY',
  kiro:     'AWS_PROFILE',
};

async function settingsProviderSetEnv(provider, key, btnEl) {
  const inp = document.getElementById(`settings-prov-key-${provider}`);
  if (!inp) return;
  const value = inp.value || '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Saving...'; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${provider}/env`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      alert('Failed to save: ' + (data.error || res.status));
      return;
    }
    showToast(value ? `Saved ${key}. New ${provider} sessions will use it.`
                    : `Cleared ${key}.`, 6000);
    // Mask the input now that it's saved
    if (value) inp.value = '••••••••';
    settingsProviderRefresh(provider);
  } catch (e) {
    alert('Save failed: ' + e);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Save'; }
  }
}

async function settingsProviderTerminalLogin(provider, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Launching...'; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${provider}/login-launch`,
                            { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      alert('Failed to launch terminal: ' + (data.error || res.status));
      return;
    }
    showToast(`A terminal opened with ${provider}. Complete sign-in there, then click Refresh.`, 12000);
  } catch (e) {
    alert('Launch failed: ' + e);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Launch terminal login'; }
  }
}

// ── Remote sign-in — MC-927 URL-surfacing fallback ─────────────────────────
// "Launch terminal login" opens a window on the HOST; over the tunnel that's
// invisible to whoever tapped the button from a phone. This path asks the
// server to capture the CLI's OAuth URL from a piped subprocess instead (only
// works where the CLI cooperates — see AgentRuntime.auth_login_argv; the
// server tells us plainly via remote_capable:false when it doesn't, e.g.
// gemini today) and renders it as a tappable link + browser-pane button +
// a box to paste the code back.
async function settingsRemoteLogin(provider, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Starting...'; }
  try {
    let data = await (await fetch(API_BASE + `/api/agent/${provider}/auth-login-remote`,
                                   { method: 'POST' })).json();
    if (data.remote_capable === false) {
      showToast(data.error || `Remote sign-in isn't available for ${provider} yet.`, 10000);
      return;
    }
    // MC-928: the CLI needs a real console (its login draws an interactive
    // TUI, e.g. gemini's account picker) — the server gave us a real-PTY
    // terminal session instead of a captured URL. Open the pop-out; it's
    // the same surface openTerminalPopout always renders, with raw
    // keystrokes wired through for pty sessions (see terminal.js).
    if (data.pty && data.session_id) {
      openTerminalPopout(window.currentProjectId, data.session_id, data.command || provider);
      showToast(`Sign in to ${provider} in the terminal that just opened.`, 8000);
      return;
    }
    let tries = 0;
    while (data.status === 'waiting_url' && tries < 20) {
      await new Promise(r => setTimeout(r, 750));
      data = await (await fetch(API_BASE + `/api/agent/${provider}/auth-login-remote/status`)).json();
      tries++;
    }
    if (!data.url) {
      showToast(`${provider} didn't print a sign-in link in time. Try "Launch terminal login" on the host instead.`, 10000);
      return;
    }
    _renderRemoteLoginBox(provider, data.url);
  } catch (e) {
    showToast('Remote sign-in failed: ' + e, 8000);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Sign in remotely'; }
  }
}

function _renderRemoteLoginBox(provider, url) {
  const hostId = `settings-remote-login-${provider}`;
  let box = document.getElementById(hostId);
  if (!box) {
    const anchor = document.querySelector(`[data-remote-login-anchor="${provider}"]`);
    if (!anchor) return;
    box = document.createElement('div');
    box.id = hostId;
    box.className = 'settings-hint';
    box.style.cssText = 'margin-top:8px;padding:8px;border:1px solid var(--border);border-radius:6px';
    anchor.after(box);
  }
  box.innerHTML = `
    <div>Open this on any device signed into the right account, then paste the code it gives you back here:</div>
    <div style="margin:6px 0;word-break:break-all"><a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a></div>
    <button class="btn-add" style="background:var(--surface3);color:var(--text)"
            onclick="openBrowserPane('${esc(url)}', window.currentProjectId, null, '${esc(provider)}-login')">Open in browser pane</button>
    <div style="display:flex;gap:6px;margin-top:8px">
      <input id="settings-remote-code-${esc(provider)}" type="text" class="settings-input"
             placeholder="Paste code here" style="flex:1" autocomplete="off">
      <button class="btn-add" onclick="settingsRemoteLoginSubmitCode('${esc(provider)}')">Submit</button>
    </div>
    <div id="settings-remote-code-result-${esc(provider)}" style="margin-top:4px;font-size:11px;color:var(--text-faint)"></div>`;
}

async function settingsRemoteLoginSubmitCode(provider) {
  const input = document.getElementById(`settings-remote-code-${provider}`);
  const resultEl = document.getElementById(`settings-remote-code-result-${provider}`);
  if (!input || !input.value.trim()) return;
  const code = input.value.trim();
  input.disabled = true;
  try {
    const res = await fetch(API_BASE + `/api/agent/${provider}/auth-login-remote/code`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    const data = await res.json().catch(() => ({}));
    if (resultEl) {
      resultEl.textContent = (data.output_tail || '').trim().slice(-300)
        || (res.ok ? 'Submitted — checking sign-in status...' : (data.error || 'Failed.'));
    }
    // A wrong code leaves the CLI re-prompting; a right one flips auth_status —
    // reuse the existing check/refresh paths rather than inventing a new one.
    if (provider === 'claude') { settingsClaudeAuthCheck(); } else { settingsProviderRefresh(provider); }
  } catch (e) {
    if (resultEl) resultEl.textContent = 'Submit failed: ' + e;
  } finally {
    input.disabled = false;
    input.value = '';
  }
}

async function settingsProviderRefresh(provider) {
  // Force a fresh /api/agent/providers fetch (reset the cached list) and
  // re-render the Settings panel so the new state shows up.
  _agentProviders = null;
  try {
    await fetch(API_BASE + `/api/agent/provider/${provider}/auth`);
  } catch (e) { /* fire-and-forget; the providers re-fetch is what counts */ }
  if (typeof refreshSettings === 'function') {
    refreshSettings();
  } else if (openModals && openModals.has('__settings')) {
    // Fallback: re-open the Settings modal so the new auth-state pills render.
    closeModalById('__settings');
    setTimeout(() => openSettings(), 50);
  }
}

function _renderClaudeAuthStatusLine(state) {
  const line = document.getElementById('claude-auth-status-line');
  if (!line) return;
  if (!state || state.ok === undefined) { line.innerHTML = ''; return; }
  if (state.ok) {
    line.innerHTML = '<span style="color:#22c55e">&#x2713; Signed in</span>';
  } else {
    const reason = state.reason === 'not_logged_in' ? 'Not signed in'
                 : state.reason === 'invalid_api_key' ? 'Invalid credentials'
                 : state.reason === 'cli_not_found' ? 'claude CLI not found'
                 : 'Auth issue';
    line.innerHTML = `<span style="color:#ef4444">&#x2717; ${esc(reason)}</span>`;
  }
}



// ── Interop: re-expose for inline / static-HTML / cross-region callers.
//    All runtime-EXCEPT `refreshAuthStatus`, which the inline `startRefresh`
//    references at parse time via `setInterval(()=>window.refreshAuthStatus(),
//    90000)` — that inline shim (a 1-line deferral edit, NOT part of this
//    moved region) resolves the window prop at each 90s tick, after this
//    deferred module has evaluated. `PROVIDER_AUTH_KEYS` is a read-only const
//    read by the inline Provider Settings section (`_renderProviderSettings`)
//    at render time — window-exposed so the bare read resolves. State
//    (_authBannerDismissed / _authBannerLastReason) + _renderAuthBanner /
//    _authBannerMessage / _renderClaudeAuthStatusLine / refreshProviderAuthStatus
//    are module-private. ──
window.refreshAuthStatus = refreshAuthStatus;     // startRefresh 90s poll (shim) + SSE-error + fetchProjects callback
window.claudeAuthKnownBad = () => _claudeAuthOk === false; // dispatch gate: true only when a probe confirmed not-signed-in
window.dismissAuthBanner = dismissAuthBanner;     // auth-banner static onclick
window.claudeAuthenticate = claudeAuthenticate;   // auth-banner static onclick
window.claudeAuthRecheck = claudeAuthRecheck;     // auth-banner static onclick
window.settingsClaudeLogin = settingsClaudeLogin; // Provider Settings section onclick
window.settingsClaudeAuthCheck = settingsClaudeAuthCheck; // Provider Settings section onclick
window.PROVIDER_AUTH_KEYS = PROVIDER_AUTH_KEYS;   // read by inline _renderProviderSettings
window.settingsProviderSetEnv = settingsProviderSetEnv;           // Provider Settings section onclick
window.settingsProviderTerminalLogin = settingsProviderTerminalLogin; // Provider Settings section onclick
window.settingsProviderRefresh = settingsProviderRefresh;         // Provider Settings section onclick
window.settingsRemoteLogin = settingsRemoteLogin;                 // Provider Settings section onclick
window.settingsRemoteLoginSubmitCode = settingsRemoteLoginSubmitCode; // remote-login box onclick
