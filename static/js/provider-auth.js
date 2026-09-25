// ── Auth banner — multi-provider ───────────────────────────────────────────
// Checks the user's selected DEFAULT provider. A Codex-first installation must
// never probe or advertise Claude merely because Claude was the historical
// fallback. Project/character pins are checked when they are actually used;
// the global first-run banner is for the provider chosen during installation.
let _authBannerDismissed = false;
let _authBannerLastReason = null;
// First-run auth gate state. `_claudeAuthOk`: null = unverified, true = signed
// in (verified by a probe), false = confirmed not signed in. `_authProbeKicked`
// guards the one-time boot probe so we don't spawn a `claude -p ok` subprocess
// on every 90s poll.
let _claudeAuthOk = null;
let _authProbeKicked = false;
const _providerAuthKnown = {};

// _providerInstallMsg / _providerInstallPolicyNoteText are STORE vars
// (static/index.html) — declared there, not here, because provider-settings.js
// and first-run.js also read them back on render. Dave review, 2026-09-24:
// first-run's install-watch poll rebuilds the whole connections step from
// scratch every 4s while a terminal is live (_setupRepaint -> setupShow), and
// _renderProviderRow/​_renderProviderSettings always emitted an EMPTY message
// div — writing the text straight into that node in providerInstallSelected
// meant it survived only until the next poll tick, then silently vanished.
// Both render functions below now read these back in on every render instead.

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

function _updateProviderAuthKnown(provider, state) {
  if (!provider) return;
  if (provider === 'claude') _updateClaudeAuthKnown(state);
  const reason = state && (state.reason || state.status);
  if (state && state.ok === true) _providerAuthKnown[provider] = true;
  else if (['not_logged_in', 'invalid_api_key', 'not_installed', 'cli_not_found'].includes(reason)) {
    _providerAuthKnown[provider] = false;
  } else {
    delete _providerAuthKnown[provider];
  }
}

async function _selectedProvider() {
  let list = _agentProviders || [];
  if (!list.length && typeof _ensureAgentProviders === 'function') {
    try { list = await _ensureAgentProviders(); } catch (e) { list = []; }
  }
  const configured = (_globalConfig && _globalConfig.default_provider) || '';
  // The in-memory config changes immediately when the walkthrough or Settings
  // saves a new default; `/api/agent/providers` may still carry the previous
  // row's `default:true` until its next fetch. The explicit current choice wins.
  return list.find(p => p.name === configured)
      || list.find(p => p.default)
      || list.find(p => p.installed && p.in_use)
      || list.find(p => p.installed)
      || { name: configured || 'claude', display_name: configured || 'Claude' };
}

function _normalizeProviderAuth(provider, data) {
  const status = (data && (data.status || data.auth_status || data.reason)) || 'unknown';
  return {
    ok: !!(data && (data.ok === true || status === 'ok')),
    reason: status === 'not_installed' ? 'cli_not_found' : status,
    status,
    _provider: provider,
    last_probe_at: data && data.last_probe_at,
  };
}

async function refreshAuthStatus() {
  try {
    const selected = await _selectedProvider();
    const provider = selected.name || 'claude';
    const res = await fetchFailFast(API_BASE + `/api/agent/${provider}/auth-status`);
    if (!res.ok) return;
    const raw = await res.json();
    const state = _normalizeProviderAuth(provider, raw);
    // First-run gate: the server seeds _claude_auth_state optimistically
    // (ok:true, never probed). If we've never verified (no last_probe_at) and
    // aren't already known-bad, actively probe ONCE so a not-signed-in install
    // surfaces the sign-in CTA up front instead of after a doomed dispatch.
    if (provider === 'claude' && state.ok !== false && !state.last_probe_at && !_authProbeKicked) {
      _authProbeKicked = true;
      _claudeAuthProbe();  // async; re-renders on completion
    }
    _updateProviderAuthKnown(provider, state);
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
    _updateProviderAuthKnown('claude', state);
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
    const state = _normalizeProviderAuth(providerName, auth);
    _updateProviderAuthKnown(providerName, state);
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

// A provider nobody chose or pinned shouldn't nag about its login state — the
// Codex-only-user-sees-a-Claude-banner complaint. Backed by /api/agent/providers'
// `in_use` flag (default_provider + every project/character pin), fetched once
// at boot into _agentProviders. Errs toward SHOWING the banner (returns true)
// when the list hasn't loaded yet, since the old unconditional behavior was to
// always check claude — a cold-boot race should never silently hide a real
// "you're not signed in" problem.
function _isProviderInUse(name) {
  if ((_globalConfig && _globalConfig.default_provider) === name) return true;
  const list = _agentProviders || [];
  if (!list.length) return true;
  const entry = list.find(p => p.name === name);
  return entry ? !!entry.in_use : false;
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
  if (!_isProviderInUse(_prov)) {
    banner.classList.add('hidden');
    return;
  }
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
      return `Log in to ${provLabel} to get started: agents can't run until you're signed in.`;
    case 'invalid_api_key':
      return `${provLabel} credentials are invalid: sign in again to refresh them.`;
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

async function authBannerRecheck() {
  const selected = await _selectedProvider();
  const provider = selected.name || 'claude';
  if (provider === 'claude') return claudeAuthRecheck();
  const btn = document.getElementById('auth-banner-recheck');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking...'; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${provider}/auth`);
    const raw = await res.json();
    const state = _normalizeProviderAuth(provider, raw);
    _updateProviderAuthKnown(provider, state);
    _renderAuthBanner(state);
  } catch (e) {
    // Leave the current banner visible on a transient network failure.
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
  // Native Qwen OAuth was discontinued 2026-04-15; DASHSCOPE_API_KEY
  // (Alibaba ModelStudio) is the primary key-based path (mc/agent_runtime.py
  // QwenRuntime._qwen_auth_state also accepts OPENAI/ANTHROPIC/GEMINI keys).
  qwen:     'DASHSCOPE_API_KEY',
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
    // The row lives in both first-run setup and Settings -> Providers;
    // providerRefreshAll re-probes and repaints whichever is showing.
    await providerRefreshAll();
  } catch (e) {
    alert('Save failed: ' + e);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Save'; }
  }
}

// ONE "Sign in" button for every vendor (Ron, 2026-09-22: two buttons that
// both mean "sign me in" is exactly the redundant-UI case that gets merged).
// Tries the verified paths first — MC-927 URL-capture, then MC-928's real-PTY
// pop-out — and only drops to the unverifiable host-OS-window terminal
// (_launch_terminal_for_binary, via /api/agent/provider/<p>/login-launch)
// when the server says neither is available. That OS-window launch can't
// confirm anything opened (Popen(shell=True) returns as soon as the shell
// spawns — see docs/_journal/provider-install-terminal-popout.md for the
// same defect class in the install path), so its response's `verified:false`
// is shown honestly instead of the old unconditional "a terminal opened".
async function settingsProviderTerminalLogin(provider, btnEl) {
  const prevLabel = btnEl ? btnEl.textContent : '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Starting...'; }
  try {
    let data = await (await fetch(API_BASE + `/api/agent/${provider}/auth-login-remote`,
                                   { method: 'POST' })).json();

    // MC-928: the CLI needs a real console (its login draws an interactive
    // TUI, e.g. gemini's account picker) — the server gave us a real-PTY
    // terminal session instead of a captured URL. Open the pop-out; it's
    // the same surface openTerminalPopout always renders, with raw
    // keystrokes wired through for pty sessions (see terminal.js).
    if (data.pty && data.session_id) {
      // Pass isPty THROUGH. Without it the pop-out opens with
      // disableStdin:true and no term.onData wiring (terminal.js:134,149),
      // so the sign-in TUI renders but you cannot type into it — gemini's
      // account picker needs arrow keys. Was missing on the old
      // remote-login path; it matters now that this IS the Sign in path.
      openTerminalPopout(window.currentProjectId, data.session_id, data.command || provider, true);
      // Setup's own terminal-visibility/live-polling (first-run.js) covers
      // install already; a sign-in terminal opened from the SAME setup card
      // painted over just the same way until this hook (2026-09-24). The
      // session id lets first-run.js track this terminal as SETUP-opened, so
      // Finish/Skip closes it but never a terminal setup didn't open.
      if (typeof window._setupOnTerminalOpened === 'function') window._setupOnTerminalOpened(data.session_id);
      showToast(`Sign in to ${provider} in the terminal that just opened.`, 8000);
      return;
    }

    if (data.remote_capable !== false) {
      // claude et al: the CLI pipes its OAuth URL over plain stdout.
      let tries = 0;
      while (data.status === 'waiting_url' && tries < 20) {
        await new Promise(r => setTimeout(r, 750));
        data = await (await fetch(API_BASE + `/api/agent/${provider}/auth-login-remote/status`)).json();
        tries++;
      }
      if (data.url) {
        _renderRemoteLoginBox(provider, data.url);
        return;
      }
      showToast(`${provider} didn't print a sign-in link in time. Try Sign in again.`, 8000);
      return;
    }

    // remote_capable === false: no real-PTY backend and this CLI can't pipe
    // its login over stdout either. Last resort — the host OS-window
    // terminal — with an honest report of whether we could confirm it opened.
    const res = await fetch(API_BASE + `/api/agent/provider/${provider}/login-launch`,
                            { method: 'POST' });
    const launch = await res.json().catch(() => ({}));
    if (!res.ok || !launch.ok) {
      alert('Failed to launch terminal: ' + (launch.error || res.status));
      return;
    }
    const how = provider === 'claude' ? 'Type /login in it' : 'Complete sign-in there';
    if (launch.verified === false) {
      showToast(`Couldn't confirm a terminal opened for ${provider}. If you don't see one, run \`${launch.command || provider}\` yourself in a terminal, then click Check status.`, 14000);
    } else {
      showToast(`A terminal opened with ${provider}. ${how}, then click Check status.`, 12000);
    }
  } catch (e) {
    alert('Sign-in failed: ' + e);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = prevLabel || 'Sign in'; }
  }
}

// Provider rows (_renderProviderRow above) — Settings-side handlers.
// Default: the shared save + refresh (applyDefaultProvider), then repaint
// the Settings rows so the radio and every "in use" flag agree.
async function settingsSetDefaultProvider(name) {
  await applyDefaultProvider(name);
  if (typeof window._renderSettings === 'function') window._renderSettings();
}

// Batch install of the ticked rows: ONE terminal, prerequisites handled once
// (providerInstallSelected / F7 batch route).
async function settingsInstallSelectedProviders(btnEl) {
  const names = Array.from(document.querySelectorAll('#settings-providers-section .settings-prov-install-sel:checked'))
    .map(cb => cb.value);
  if (!names.length) { showToast('Tick the vendors you want to install first.', 4000); return; }
  await providerInstallSelected(btnEl, names);
}

// ── Remote sign-in box — MC-927 URL-surfacing fallback ─────────────────────
// Rendered by settingsProviderTerminalLogin above when the server captured a
// CLI's OAuth URL from a piped subprocess (only works where the CLI
// cooperates — see AgentRuntime.auth_login_argv). Tappable link + browser-pane
// button + a box to paste the code back.
function _renderRemoteLoginBox(provider, url) {
  const hostId = `settings-remote-login-${provider}`;
  let box = document.getElementById(hostId);
  if (!box) {
    // Fall back to the row itself, then fail LOUDLY. The one thing this must
    // never do again is return silently: the URL is the only way to sign in
    // from a device that cannot see the host's browser.
    const anchor = document.querySelector(`[data-remote-login-anchor="${provider}"]`)
                || document.querySelector(`.prov-row[data-provider="${provider}"]`);
    if (!anchor) {
      window.prompt(`Open this link on any device to sign in to ${provider}:`, url);
      return;
    }
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
        || (res.ok ? 'Submitted, checking sign-in status...' : (data.error || 'Failed.'));
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

// Explicit, cost-labeled probe for a provider whose auth_probe() spends real
// quota (MC-934 — today, Gemini only, via capabilities.auth_probe_spends_quota
// in provider-settings.js). Deliberately does NOT call settingsProviderRefresh
// / force a full Settings rebuild the way that cheap function does — same
// reason settingsClaudeAuthCheck (above) patches its own status line in place
// instead: a rebuild would wipe the very message this just wrote. The pill
// itself is patched directly by id so it doesn't silently drift back to
// "signed in" for the rest of this Settings view.
async function settingsProviderAuthProbe(provider, btnEl) {
  const line = document.getElementById(`prov-auth-status-line-${provider}`);
  const pillText = document.getElementById(`prov-auth-pill-text-${provider}`);
  const pill = document.getElementById(`prov-auth-pill-${provider}`);
  if (line) line.innerHTML = '<span style="color:var(--text-faint)">Checking (spends live quota)&hellip;</span>';
  if (btnEl) btnEl.disabled = true;
  try {
    const res = await fetch(API_BASE + `/api/agent/${provider}/auth-probe`, { method: 'POST' });
    const state = await res.json().catch(() => ({}));
    const tierNote = state.tier === 'free'
      ? ` (free tier${state.quota_value ? `, ${state.quota_value}/day` : ''})` : '';
    const PILL_MAP = {
      ok: ['var(--green)', 'signed in'],
      quota_exceeded: ['var(--red)', 'quota exceeded'],
      invalid_api_key: ['var(--red)', 'credentials invalid'],
      not_logged_in: ['var(--amber)', 'not signed in'],
      not_installed: ['var(--text-faint)', 'not installed'],
    };
    const [color, text] = PILL_MAP[state.status] || ['var(--amber)', 'status unknown'];
    if (pill) pill.style.color = color;
    if (pillText) pillText.textContent = text;
    if (line) {
      if (state.status === 'quota_exceeded') {
        line.innerHTML = `<span style="color:var(--red)">Quota exceeded${esc(tierNote)}: ${esc(state.error_text || 'this model will keep failing until it resets.')}</span>`;
      } else if (state.status === 'invalid_api_key') {
        line.innerHTML = `<span style="color:var(--red)">${esc(state.error_text || 'Key rejected.')}</span>`;
      } else if (state.ok) {
        line.innerHTML = `<span style="color:var(--green)">Verified: live call succeeded${esc(tierNote)}.</span>`;
      } else {
        line.innerHTML = `<span style="color:var(--amber)">${esc(state.error_text || ('Probe returned: ' + (state.status || 'unknown')))}</span>`;
      }
    }
  } catch (e) {
    if (line) line.innerHTML = '<span style="color:#ef4444">Probe failed to reach the server.</span>';
  } finally {
    if (btnEl) btnEl.disabled = false;
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



// ── Provider row + install/refresh handlers ─────────────────────────────────
// Shared by first-run setup (first-run.js) and Settings -> Providers
// (provider-settings.js). Lived in walkthrough.js while provider choice was a
// tour step; moved here when setup was decoupled from the tour.

// Save + refresh of the default provider, shared by first-run setup's Default
// radio (first-run.js setupSetDefaultProvider) and Settings -> Providers'
// per-row "Set default" (settingsSetDefaultProvider below) so the two can't
// drift into two ways of changing the default. Reuses the generic saveSetting()
// PUT — one write path, not two.
async function applyDefaultProvider(name) {
  await saveSetting('default_provider', name);
  // First-run follows the user's choice immediately. Refresh the provider
  // inventory (its `default`/`in_use` flags predate this click), then run the
  // same selected-provider auth check used at boot so Codex never produces a
  // Claude login prompt and an unsigned-in choice gets its own CTA.
  _agentProviders = null;
  try { await _ensureAgentProviders(); } catch (e) { /* auth refresh still uses config */ }
  if (typeof refreshAuthStatus === 'function') refreshAuthStatus();
  // First-run's connections step shows a warning computed at render time
  // (first-run.js _setupConnectionsWarning); without a repaint here it goes
  // stale the moment a default is picked — the OTHER two provider-state
  // mutations (install, check-status) already repaint.
  _repaintProviderRows();
}

// F6 (clean-VM run 2026-09-18): the install route may have changed (or
// deliberately left alone) the PowerShell script policy so typed claude/gemini
// works; when it has something to say, show it verbatim next to the install
// message rather than changing the user's machine silently.
function _providerPolicyNote(data) {
  const m = data && data.execution_policy && data.execution_policy.message;
  return m ? ' ' + m : '';
}

// F7 (clean-VM run 2026-09-18): used to loop calling providerInstall() per
// vendor, and EACH call opened its OWN terminal — ticking Claude + Gemini
// launched two concurrent `winget install ... NodeJS` calls that raced each
// other. One batch request now runs every selected-but-uninstalled vendor
// in a single terminal, with the Node/npm (or pip/uv) prerequisite handled
// once — see agent_routes.py's _provider_install_command_batch.
// `only`: the names to install — Settings -> Providers passes its checked rows,
// first-run setup passes its selection.
async function providerInstallSelected(button, only) {
  const names = (_agentProviders || [])
    .filter((p) => (only || []).includes(p.name) && !p.installed)
    .map((p) => p.name);
  if (!names.length) return;
  if (button) button.disabled = true;
  // Cleared up front, not just set on success — a stale URL from a PRIOR
  // batch must never be polled as if it belonged to this one (MC-959).
  _providerInstallStatusUrl = '';
  // Seed every row 'queued' immediately, before the first status poll lands —
  // otherwise the progress bar is blank for up to 4s after the click. The
  // first poll corrects whichever one is actually running_now.
  for (const name of names) _providerInstallProgress[name] = { result: 'pending', started_at: null, running_now: false };
  // Writes into the persistent state maps (survives the next setupShow
  // rebuild the install-watch poll triggers) AND the live DOM node when one
  // exists, for immediate feedback without waiting on that rebuild.
  const setMsg = (name, text) => {
    _providerInstallMsg[name] = text;
    const el = document.getElementById(`prov-install-msg-${name}`);
    if (el) el.textContent = text;
  };
  const setPolicyNote = (text) => {
    _providerInstallPolicyNoteText = text;
    document.querySelectorAll('.prov-install-policy-note').forEach((el) => { el.textContent = text; });
  };
  try {
    const res = await fetch(API_BASE + '/api/agent/providers/install-launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }),
    });
    const data = await res.json().catch(() => ({}));
    if (data.ok) {
      // The install runs in Clayrune's own terminal pop-out (not an OS
      // window) so progress is visible in the dashboard and over the
      // tunnel/on a phone — see openTerminalPopout below and
      // agent_routes.py's _launch_install_terminal.
      if (data.session_id) {
        openTerminalPopout(window.currentProjectId, data.session_id, data.command || 'install', data.pty);
      }
      // MC-959: this batch's own per-vendor result feed (empty string = none,
      // e.g. an older server without the route) — first-run's install-watch
      // polls it to show FAILED vendors and to know when the batch itself is
      // done, rather than guessing from whether the terminal is still open.
      _providerInstallStatusUrl = data.status_url || '';
      // The execution-policy note is a property of the ONE batch install, not
      // of each vendor row — showing it per row repeated it once per selected
      // vendor (4x on a clean-VM run with everything ticked, 2026-09-24).
      // Shown once, in whichever shared note element the calling surface
      // rendered (setup and Settings each have their own instance of it).
      const policyNote = _providerPolicyNote(data);
      if (policyNote) setPolicyNote(policyNote.trim());
      for (const name of (data.installed || names)) {
        setMsg(name, 'A terminal opened to install it. Once it finishes, click "Check setup status".');
      }
      for (const name of (data.unsupported || [])) {
        setMsg(name, 'No automatic install available for this vendor: see its own Install button.');
      }
    } else if (data.command) {
      for (const name of names) {
        setMsg(name, `Couldn't start that here (${data.error || 'no runnable install'}). Run this yourself: ${data.command}`);
      }
    } else {
      for (const name of names) {
        setMsg(name, data.error || 'Could not start the install.');
      }
    }
  } catch (e) {
    for (const name of names) {
      setMsg(name, 'Install failed: ' + e);
    }
  } finally {
    if (button) button.disabled = false;
  }
}

// Per-provider state label — ONE vocabulary for first-run setup and Settings ->
// Providers (both render rows through _renderProviderRow below).
function _providerStateLabel(p) {
  if (!p.installed) return { label: 'not installed', color: 'var(--text-faint)' };
  if (p.auth_status === 'ok') return { label: 'signed in', color: 'var(--green)' };
  if (p.auth_status === 'not_logged_in') return { label: 'not signed in', color: 'var(--amber)' };
  // F9 (clean-VM run 2026-09-18): a Gemini OAuth credential file can exist
  // and still be dead — Google retired personal-account sign-in for Gemini
  // Code Assist. Local evidence alone can't tell live vs. dead, so it's
  // reported honestly instead of a false green "signed in".
  if (p.auth_status === 'unverified') return { label: 'unverified', color: 'var(--amber)' };
  if (p.auth_status === 'oauth_rejected') return { label: 'sign-in rejected', color: 'var(--amber)' };
  // MC-934: a key that exists but has no quota left, or is refused, is
  // neither "signed in" nor merely "not signed in".
  if (p.auth_status === 'quota_exceeded') return { label: 'quota exceeded', color: 'var(--red)' };
  if (p.auth_status === 'invalid_api_key') return { label: 'credentials invalid', color: 'var(--red)' };
  return { label: 'installed', color: 'var(--text-faint)' };
}

// THE provider row — one component for every vendor (Claude included), used
// by first-run setup's Agent connections step and by Settings -> Providers, so both
// surfaces and every vendor look and behave the same. Per-vendor differences
// live only in what an action DOES (the server-side login flow, the optional
// API-key field), never in the row's shape. Structure:
//   .prov-row > label.prov-row-head (select box, name, state pill, Install)
//             > .prov-row-actions   (Default radio, Sign in, Check status)
//             > .prov-row-detail    (version / error text / install hint)
//             > .prov-row-extra     (API-key entry, if the vendor takes one;
//                                    opts.keyEntry — Settings and setup)
//             > #prov-install-msg-<name>
// opts.mode 'setup'    — box = "set this vendor up"; actions only when selected
//           'settings' — box = "batch-install this one" (uninstalled rows
//                        only); actions for every installed vendor
function _renderProviderRow(p, opts) {
  const setup = opts.mode === 'setup';
  const n = esc(p.name);
  const state = _providerStateLabel(p);
  const installed = !!p.installed;
  const authOk = p.auth_status === 'ok';
  // The stale "A terminal opened to install it..." message is about the
  // INSTALL only — clear it once the row itself says installed, not once
  // sign-in is also done too. Ron, clean-VM run 2026-09-24: Claude/Gemini/Qwen
  // all showed it forever once installed, because the old condition required
  // authOk as well and an installed-but-not-signed-in row never clears that.
  if (installed) delete _providerInstallMsg[p.name];
  // The per-vendor progress bar likewise stops being useful once the row is
  // fully done (installed AND signed in) — drop it so a much later Settings
  // visit doesn't show a stale green "installed" sliver forever.
  if (installed && authOk) delete _providerInstallProgress[p.name];
  const isDefault = opts.defaultName === p.name;
  const showActions = setup ? !!opts.selected : installed;
  const box = setup
    ? `<input type="checkbox" name="setup-provider" class="prov-row-select" value="${n}" ${opts.selected ? 'checked' : ''}
         onchange="setupSelectProvider('${n}',this.checked)"
         style="width:15px;height:15px;accent-color:var(--accent)">`
    : (installed ? '' : `<input type="checkbox" class="prov-row-select settings-prov-install-sel" value="${n}"
         aria-label="Select ${esc(p.display_name)} for batch install"
         style="width:15px;height:15px;accent-color:var(--accent)">`);
  // Setup never shows the filled-accent .btn-add look on anything but the
  // step's own Next/Get started — Install/Sign in/Check status/Save read as
  // utility actions there (.setup-btn-utility), same rule as every other
  // in-step button. Settings keeps its own .btn-add look unchanged.
  const rowBtnCls = setup ? 'setup-btn-utility' : 'btn-add';
  const installBtn = installed ? '' : `
              <button type="button" class="${rowBtnCls} prov-install" style="padding:2px 10px;font-size:11px;flex-shrink:0"
                onclick="event.preventDefault();providerInstall('${n}',this)">Install</button>`;
  // Inline background/color would beat the .setup-btn-utility class (inline
  // always wins over a class), so setup mode drops them and lets the class
  // supply the look; Settings keeps the explicit surface3 pill it always had.
  const btnCss = setup ? 'padding:2px 10px;font-size:11px' : 'padding:2px 10px;font-size:11px;background:var(--surface3);color:var(--text)';
  const costs = !!(p.capabilities && p.capabilities.auth_probe_spends_quota);
  const defaultCtl = setup
    ? `<label class="prov-default"><input type="radio" name="setup-provider-default" ${isDefault ? 'checked' : ''}
         onchange="setupSetDefaultProvider('${n}')"> Default</label>`
    : `<label class="prov-default"><input type="radio" name="prov-default" ${isDefault ? 'checked' : ''}
         onchange="settingsSetDefaultProvider('${n}')"> Default</label>`;
  const needSignIn = installed && (!authOk || opts.signInWhenOk);
  // data-remote-login-anchor is LOAD-BEARING, not decoration: it is where
  // _renderRemoteLoginBox puts the captured OAuth link + paste-the-code box.
  // The attribute lived on the old provider-settings.js row markup (86f0aa7,
  // 2026-08-31) and was dropped by the F1 unified-row rewrite (4598846,
  // 2026-09-18), so the lookup silently found nothing and rendered nothing —
  // Sign in became a dead button for anyone without a host browser, i.e.
  // everyone on the tunnel. Keep it on any row that can show a Sign in button.
  const actions = !showActions ? '' : `<div class="prov-row-actions" data-remote-login-anchor="${n}" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:4px 8px">
              ${defaultCtl}
              ${needSignIn ? `<button type="button" class="${rowBtnCls} prov-sign-in" style="${btnCss}"
                onclick="settingsProviderTerminalLogin('${n}',this)">Sign in</button>` : ''}
              ${installed ? `<button type="button" class="${rowBtnCls} prov-check" style="${btnCss}"
                ${costs ? `title="Spends one live API call against ${esc(p.display_name)} to verify the key can actually serve a request: counts against today's quota."` : ''}
                onclick="providerCheckStatus('${n}',this)">Check status</button>` : ''}
            </div>`;
  // Out-of-allowance record: shown here with the way out. The record is one
  // failed run's evidence and nothing else corrects it after a top-up, so the
  // user can ask Clayrune to look again (mc/blueprints/agent_routes.py
  // agent_allowance_recheck). Wording is a re-check, not an override — a vendor
  // that is still out just refuses again on the next run.
  const allowance = (installed && p.allowance_exhausted)
    ? `<div class="prov-row-allowance" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:4px 8px 0;font-size:11px;color:var(--amber)">
              <span>${esc(p.allowance_exhausted)}</span>
              ${_allowanceRecheckBtn(p.name)}
            </div>` : '';
  const bits = [];
  // Only prefix 'v' when the runtime reports a bare number. Codex reports
  // `codex-cli 0.155.1`, which rendered as `vcodex-cli 0.155.1` on the
  // clean-VM Providers panel (2026-09-22).
  if (installed && p.version) {
    bits.push((/^\d/.test(String(p.version).trim()) ? 'v' : '') + esc(p.version));
  }
  if (installed && !authOk && p.auth_error_text) bits.push(esc(String(p.auth_error_text).slice(0, 200)));
  if (!installed && p.install_hint) bits.push(`<span style="font-family:monospace;color:var(--accent)">${esc(p.install_hint)}</span>`);
  const detail = bits.length
    ? `<div class="prov-row-detail" style="font-size:11px;color:var(--text-faint);padding:2px 8px 0;word-break:break-word">${bits.join(' · ')}</div>` : '';
  const envKey = (opts.keyEntry && installed && showActions && window.PROVIDER_AUTH_KEYS) ? window.PROVIDER_AUTH_KEYS[p.name] : '';
  const extra = envKey ? `<div class="prov-row-extra" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:6px 8px 0">
              <span style="font-size:11px;color:var(--text-faint);min-width:130px">${esc(envKey)}</span>
              <input id="settings-prov-key-${n}" type="password" class="settings-input" style="flex:1;min-width:140px"
                placeholder="${authOk ? '(saved, paste to replace)' : 'paste API key'}" autocomplete="off">
              <button type="button" class="${rowBtnCls}" onclick="settingsProviderSetEnv('${n}','${esc(envKey)}',this)">Save</button>
            </div>` : '';
  // Setup mode: the outer row itself is the selectable card (border/radius/
  // padding/selected-state match .setup-preset-card), so the head drops its
  // own background/padding — the card wrapper already supplies both.
  // Settings keeps the plain row it always had.
  const rowClass = setup ? `prov-row prov-row-card${opts.selected ? ' active' : ''}` : 'prov-row';
  const headStyle = setup
    ? 'display:flex;align-items:center;gap:10px;cursor:pointer'
    : 'display:flex;align-items:center;gap:10px;cursor:pointer;padding:8px;border-radius:4px;background:var(--surface2)';
  return `
          <div class="${rowClass}" data-provider="${n}">
            <label class="prov-row-head" style="${headStyle}">
              ${box}
              <span class="prov-row-name" style="flex:1;font-weight:600;color:var(--text)">${esc(p.display_name)}</span>
              <span class="prov-row-state" id="prov-auth-pill-${n}" style="font-size:11px;font-weight:600;color:${state.color}">${esc(state.label)}</span>
              ${installBtn}
            </label>
            ${actions}
            ${allowance}
            ${detail}
            ${extra}
            <div id="prov-install-msg-${n}" style="font-size:11px;color:var(--text-faint);padding:2px 8px 0">${esc(_providerInstallMsg[p.name] || '')}</div>
            <div id="prov-install-progress-${n}">${_providerProgressHTML(p.name)}</div>
          </div>`;
}

// Per-vendor install progress bar — queued -> installing (indeterminate sweep,
// elapsed seconds; npm gives no percentage) -> installed (full green) / failed
// (full red). Driven entirely by GET .../install-status's per-vendor
// `result`/`started_at`/`running_now` (stored into `_providerInstallProgress`
// by first-run.js's _setupPollInstallStatus), never a client-side timer guess —
// see the CSS comment in app.css for why "installing" is indeterminate.
// Renders '' once nothing is tracked for this vendor (never started this
// session, or already cleared by _renderProviderRow once fully done).
function _providerProgressHTML(name) {
  const p = _providerInstallProgress[name];
  if (!p) return '';
  const wrap = (label, color, indeterminate) => `<div style="font-size:11px;color:var(--text-faint);padding:2px 8px 0">${esc(label)}</div>
    <div class="prov-install-bar" style="margin:0 8px">
      <div class="prov-install-bar-fill${indeterminate ? ' indeterminate' : ''}" style="width:${indeterminate ? '40' : '100'}%;background:${color}"></div>
    </div>`;
  if (p.result === 'ok') return wrap('Installed', 'var(--green)', false);
  if (p.result === 'failed') return wrap('Install failed', 'var(--red)', false);
  if (p.result === 'no_result') return '';
  // 'pending': the one vendor with running_now:true is actually installing
  // (vendors install strictly sequentially — see the server route's own
  // docstring); every other pending vendor is still queued behind it.
  if (p.running_now) {
    const elapsed = p.started_at ? Math.max(0, Math.round(Date.now() / 1000 - p.started_at)) : 0;
    return wrap(`Installing… ${elapsed}s`, 'var(--accent)', true);
  }
  return wrap('Queued', 'var(--surface3)', false);
}

// "Re-check allowance" button — shared by the provider row and the composer's
// out-of-allowance warning so both say the same thing.
function _allowanceRecheckBtn(name) {
  return `<button type="button" class="btn-add prov-allowance-recheck" style="padding:2px 10px;font-size:11px;background:var(--surface3);color:var(--text)"
    title="Topped up? This clears Clayrune's out-of-allowance note for this agent so the next run can try again. If it is still out, that run will say so."
    onclick="providerAllowanceRecheck('${esc(name)}',this)">Re-check allowance</button>`;
}

async function providerAllowanceRecheck(name, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Checking…'; }
  try {
    const res = await fetch(API_BASE + `/api/agent/${encodeURIComponent(name)}/allowance/recheck`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    const label = ((_agentProviders || []).find(x => x.name === name) || {}).display_name || name;
    showToast(data.probe === 'usable'
      ? `${label} reports allowance available, ready to run.`
      : `${label} allowance re-checked. The next run will confirm; if it is still out, it will say so.`, 6000);
    _agentProviders = null;
    await _ensureAgentProviders();
    _repaintProviderRows();
    if (typeof refreshModal === 'function') refreshModal();
  } catch (e) {
    showToast('Allowance re-check failed: ' + e, 8000);
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Re-check allowance'; }
  }
}

// Per-row "Check status": re-probe ONE vendor (POST /api/agent/<p>/auth-probe —
// the same route for every vendor, Claude's `claude -p ok` and Gemini's
// quota-costing live call included; the tooltip discloses the cost), fold the
// verdict into the cached provider list, and repaint whichever surface shows
// the row.
async function providerCheckStatus(name, btnEl) {
  const msgEl = document.getElementById(`prov-install-msg-${name}`);
  if (btnEl) btnEl.disabled = true;
  if (msgEl) msgEl.textContent = 'Checking…';
  try {
    const res = await fetch(API_BASE + `/api/agent/${name}/auth-probe`, { method: 'POST' });
    const state = await res.json().catch(() => ({}));
    const p = (_agentProviders || []).find(x => x.name === name);
    if (p && res.ok) {
      p.auth_status = state.status || (state.ok ? 'ok' : 'unknown');
      p.auth_error_text = state.error_text || null;
    }
    _repaintProviderRows();
  } catch (e) {
    if (msgEl) msgEl.textContent = 'Check failed: ' + e;
  } finally {
    if (btnEl) btnEl.disabled = false;
  }
}

// Repaint every surface that renders provider rows from the cached list. The
// Settings rebuild keeps the drill-down position (module-scope view state).
function _repaintProviderRows() {
  if (typeof window._setupRepaint === 'function') window._setupRepaint();
  if (document.getElementById('settings-providers-section') && typeof window._renderSettings === 'function') {
    window._renderSettings();
  }
}

// "Install" button on an uninstalled provider row. Launches the SAME command
// the installers use (server resolves it from the runtime's own install_hint
// — see agent_provider_install_launch) in CLAYRUNE'S OWN terminal pop-out
// (openTerminalPopout, terminal.js) so the user can watch it run from
// wherever they're looking at the dashboard — including over the tunnel or
// on a phone, where a new OS window on the host would be invisible. Never
// invents its own command: {ok:false} always carries the exact one to run by
// hand when the server can't launch it itself (no npm/curl on PATH).
async function providerInstall(name, btnEl) {
  const msgEl = document.getElementById(`prov-install-msg-${name}`);
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Installing...'; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${name}/install-launch`,
                            { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (data.ok) {
      if (data.session_id) {
        openTerminalPopout(window.currentProjectId, data.session_id, data.command || name, data.pty);
      }
      if (msgEl) msgEl.textContent = 'A terminal opened to install it. Once it finishes, click Refresh.' + _providerPolicyNote(data);
      if (btnEl) {
        btnEl.textContent = 'Refresh';
        btnEl.disabled = false;
        btnEl.onclick = (e) => { e.preventDefault(); providerRefreshAll(); };
      }
    } else if (data.command) {
      if (msgEl) msgEl.textContent = `Couldn't start that here (${data.error || 'no runnable install'}). Run this yourself: ${data.command}`;
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Install'; }
    } else {
      if (msgEl) msgEl.textContent = data.error || 'Could not start the install.';
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Install'; }
    }
  } catch (e) {
    if (msgEl) msgEl.textContent = 'Install failed: ' + e;
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Install'; }
  }
}

// Re-fetch /api/agent/providers AND force every runtime to re-probe its auth
// state (`_ensureAgentProviders(true)` → `?refresh=1`) rather than just
// re-reading the server's cached health_check() result — a plain re-fetch
// still returned stale not_logged_in for a provider signed in from OUTSIDE
// Clayrune (F8, clean-VM run 2026-09-18: `claude auth status` showed
// loggedIn:true, but this button kept the step blocked until a server
// restart). Then re-render the CURRENT step so a just-installed/signed-in
// CLI's state flips without the user having to close and reopen setup.
async function providerRefreshAll() {
  try { await _ensureAgentProviders(true); } catch (e) { /* leave stale on failure */ }
    _repaintProviderRows();
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
window._renderProviderRow = _renderProviderRow;                  // Settings -> Providers (provider-settings.js) + first-run setup render rows with it
window._repaintProviderRows = _repaintProviderRows;               // first-run.js's install-watch calls this cross-module (ES modules don't share top-level scope)
window.providerCheckStatus = providerCheckStatus;               // per-row Check status button onclick
window.providerAllowanceRecheck = providerAllowanceRecheck;     // provider row + composer warning onclick
window._allowanceRecheckBtn = _allowanceRecheckBtn;             // composer warning (conversation.js)
window.applyDefaultProvider = applyDefaultProvider;             // first-run setup Default radio (first-run.js)
window.providerInstall = providerInstall;                       // provider row's generated Install button onclick
window.providerInstallSelected = providerInstallSelected;       // Install selected buttons (setup + Settings)
window.providerRefreshAll = providerRefreshAll;                 // Check setup status buttons (setup + Settings) + Install's Refresh
window.refreshAuthStatus = refreshAuthStatus;     // startRefresh 90s poll (shim) + SSE-error + fetchProjects callback
window.claudeAuthKnownBad = () => _claudeAuthOk === false; // dispatch gate: true only when a probe confirmed not-signed-in
window.providerAuthKnownBad = (provider) => _providerAuthKnown[provider || 'claude'] === false;
window.dismissAuthBanner = dismissAuthBanner;     // auth-banner static onclick
window.claudeAuthenticate = claudeAuthenticate;   // auth-banner static onclick
window.claudeAuthRecheck = claudeAuthRecheck;     // auth-banner static onclick
window.authBannerRecheck = authBannerRecheck;     // auth-banner provider-agnostic re-check
window.settingsClaudeLogin = settingsClaudeLogin; // Provider Settings section onclick
window.settingsClaudeAuthCheck = settingsClaudeAuthCheck; // Provider Settings section onclick
window.PROVIDER_AUTH_KEYS = PROVIDER_AUTH_KEYS;   // read by inline _renderProviderSettings
window.settingsProviderSetEnv = settingsProviderSetEnv;           // Provider Settings section onclick
window.settingsProviderTerminalLogin = settingsProviderTerminalLogin; // Provider Settings section onclick
window.settingsSetDefaultProvider = settingsSetDefaultProvider;       // provider row Default radio onchange (Settings)
window.settingsInstallSelectedProviders = settingsInstallSelectedProviders; // Provider Settings toolbar onclick
window.settingsProviderRefresh = settingsProviderRefresh;         // Provider Settings section onclick
window.settingsProviderAuthProbe = settingsProviderAuthProbe;     // Provider Settings section onclick (MC-934)
window.settingsRemoteLoginSubmitCode = settingsRemoteLoginSubmitCode; // remote-login box onclick
