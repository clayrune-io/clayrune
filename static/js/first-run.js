// ── First-run setup ───────────────────────────────────────────────────────────
// Welcome -> Agent connections -> Essentials -> optional tour offer.
//
// Setup is its own flow, decoupled from the guided tour (walkthrough.js): it
// runs first, stands alone, and is re-openable from Settings without dragging
// the tour behind it. It reuses the walkthrough's .wt-* overlay/card CSS, the
// shared provider row (provider-auth.js _renderProviderRow), and the EXISTING
// setters for every Essentials item — no new settings, no duplicated logic.
//
// "Have I been set up?" is a server-side fact (config.setup_completed): setup
// writes server state (providers, default_provider, the LAN passcode), so it is
// a property of the install, not of one browser profile. localStorage
// `walkthrough_done` keeps gating the TOUR only.

let setupActive = false;
let setupStep = 0;
let setupForced = false;            // re-run from Settings: never skip a step
let setupSelectedProviders = new Set();
let setupExplicitDefault = '';
let setupProviderChoiceVisited = false;
let setupModelTier = 'balanced';    // default tier pre-selection; provider-neutral
let setupModelTierVisited = false;
const MODEL_TIER_LABEL = { best: 'Best', balanced: 'Balanced', fast: 'Fast' };

const SETUP_STEPS = [
  {
    id: 'welcome',
    title: 'Welcome to Clayrune',
    body: () => 'Clayrune is your operator console for long-running coding agents — a multi-project dashboard where you dispatch, monitor, and coordinate AI work across many parallel streams. Two quick things first: connect the AI vendors you use, then pick a few essentials. About a minute.',
  },
  {
    id: 'connections',
    title: 'Which AI do you work with?',
    wide: true,
    body: () => {
      // Every provider Clayrune supports (the SAME /api/agent/providers list
      // Settings and the composer read), installed ones first — not filtered
      // to installed-only. A fresh "downloaded Mac .app" install never runs
      // install.sh/install.ps1's provider prompt, so this step is the only
      // place that install-time question gets asked; filtering to installed
      // hid whichever CLI the user actually wanted here (MC fresh-install
      // report 2026-09-15: a 2-provider list with the 3rd silently missing).
      const provs = (_agentProviders || []).slice()
        .sort((a, b) => (b.installed ? 1 : 0) - (a.installed ? 1 : 0));
      const cur = setupExplicitDefault || (_globalConfig && _globalConfig.default_provider) || '';
      if (!setupProviderChoiceVisited) {
        if (cur) setupSelectedProviders.add(cur);
        // Vendors already installed AND signed in need no setup — pre-tick
        // them so the user only has to act on what's missing.
        provs.forEach(p => { if (p.installed && p.auth_status === 'ok') setupSelectedProviders.add(p.name); });
      }
      setupProviderChoiceVisited = true;
      return `Choose one or more vendors to set up. Pick one default; every selected vendor stays available per agent and per chat.<div style="margin-top:14px;display:flex;flex-direction:column;gap:8px;text-align:left">` +
        provs.map(p => _renderProviderRow(p, {
          mode: 'setup',
          selected: setupSelectedProviders.has(p.name),
          defaultName: cur,
          keyEntry: true,   // clean-VM run 3 (C4): Qwen's only sign-in is a key, so the row needs the same key field Settings shows
        })).join('') + `</div><div style="display:flex;gap:8px;margin-top:12px">
          <button type="button" class="btn-add" onclick="setupInstallSelected(this)">Install selected</button>
          <button type="button" class="btn-add" onclick="providerRefreshAll()">Check setup status</button>
        </div><div id="setup-provider-validation" role="status" style="margin-top:8px;color:var(--amber)"></div>`
        + `<div style="margin-top:16px;text-align:left">
          <div style="font-weight:600;color:var(--text)">Default model</div>
          <div style="font-size:11px;color:var(--text-faint);margin:2px 0 6px">Applies across every agent unless overridden per project or per chat.</div>
          <div class="mc-seg" id="setup-model-tier-seg">`
        + Object.keys(MODEL_TIER_LABEL).map(t => `<button type="button" class="${setupModelTier === t ? 'active' : ''}" data-tier="${t}" onclick="setupPickModelTier('${t}',this)">${MODEL_TIER_LABEL[t]}</button>`).join('')
        + `</div>
          <div style="font-size:11px;color:var(--text-faint);margin-top:6px">Balanced is recommended — you can change it later in Settings.</div>
        </div>`;
    },
    onEnter: () => {
      // Balanced is pre-selected: persist it the moment the step is first
      // shown so a user who never touches the control still gets an explicit
      // global tier saved, not silent inherit-to-native (engine_selection
      // treats an unset global as the CLI native default, not a tier).
      // Only when NOTHING is set: a re-run from Settings must never replace
      // an existing tier or exact pin just because the step was shown.
      if (!setupModelTierVisited) {
        setupModelTierVisited = true;
        const cur = String((_globalConfig && _globalConfig.agent_model) || '').trim();
        if (!cur) setupPickModelTier(setupModelTier);
      }
    },
    // Skip ONLY when the installer already wrote default_provider into
    // config.json before this first launch (install.sh / install.ps1,
    // 2026-09-14) — asking again here would be the exact double-prompt this
    // step exists to avoid. Does NOT skip on "only one CLI installed" (or
    // zero) any more: that was exactly the fresh-Mac-.app case where nothing
    // is installed yet and the user still needs to see the install offer.
    // A saved default alone is not enough: this step's own save writes it too,
    // so after a reload a user who picked but never installed was skipped past
    // the only install offer (clean-VM run, 2026-09-18). Skip only when that
    // default is actually installed and signed in. An explicit re-run from
    // Settings never skips: the user asked to see it.
    skip: () => {
      if (setupForced || setupProviderChoiceVisited) return false;
      const d = _globalConfig && _globalConfig.default_provider;
      const p = d && (_agentProviders || []).find(x => x.name === d);
      return !!(p && p.installed && p.auth_status === 'ok');
    },
  },
  {
    id: 'essentials',
    title: 'A few essentials',
    wide: true,
    body: () => _setupEssentialsHTML(),
    onEnter: () => {
      // The LAN-passcode form is the existing Settings component; it renders
      // into #local-access-section and refreshes itself there after a save.
      if (typeof window.refreshLocalAccessSection === 'function') window.refreshLocalAccessSection();
    },
  },
  {
    id: 'tour',
    title: 'Take the tour?',
    body: () => 'You’re set up. Want a quick walkthrough of the dashboard — the sidebar, project tiles, the Floor, the Desk, Hivemind and Automation? About 10 steps, 2 minutes. You can also take it any time from Settings, the Command Palette (Ctrl+K), or the <strong>?</strong> button in the header.',
    // Already toured in this browser: don't re-offer what they've done. A re-run
    // from Settings always offers it.
    skip: () => !setupForced && !!localStorage.getItem('walkthrough_done'),
  },
];

// ── Essentials ───────────────────────────────────────────────────────────────
// Each control calls the setter Settings already uses (setTone/setAccent in
// appearance.js, setChatStyle, setAdvancedFlag, the LAN-passcode section in
// settings-sections.js). Highlight state is patched in place, not re-rendered:
// a re-render would wipe a half-typed passcode.
function _setupEssentialsHTML() {
  const tone = (typeof currentTone !== 'undefined') ? currentTone : 'warm';
  const accent = (typeof currentAccent !== 'undefined') ? currentAccent : '';
  const flow = (typeof _chatStyle !== 'undefined') && _chatStyle === 'flow';
  const act = (on) => on ? 'active' : '';
  const accents = [
    ['', 'Default', '#5b9ef5'], ['sunset', 'Sunset', '#e8824a'], ['rose', 'Rose', '#d96480'],
    ['lilac', 'Lilac', '#8a7ce0'], ['lagoon', 'Lagoon', '#4fa89a'], ['ink', 'Ink', '#6b7286'],
  ];
  const sec = (label, hint, inner) => `<div style="margin-top:14px;text-align:left">
      <div style="font-weight:600;color:var(--text)">${label}</div>
      <div style="font-size:11px;color:var(--text-faint);margin:2px 0 6px">${hint}</div>${inner}</div>`;
  return `All of these can be changed later in Settings.`
    + sec('Theme', 'Warm and Editorial are light themes.',
        `<div class="mc-seg" id="setup-tone-seg">
          <button type="button" class="${act(tone === 'dark')}" onclick="setupPickTone('dark',this)">Dark</button>
          <button type="button" class="${act(tone === 'warm')}" onclick="setupPickTone('warm',this)">Warm</button>
          <button type="button" class="${act(tone === 'editorial')}" onclick="setupPickTone('editorial',this)">Editorial</button>
        </div>
        <div class="mc-accent-row" id="setup-accent-row" style="margin-top:8px;justify-content:flex-start;max-width:none">`
        + accents.map(([k, label, color]) => `<button type="button" class="mc-accent-pill ${act(accent === k)}" onclick="setupPickAccent('${k}',this)"><span class="mc-accent-swatch" style="background:${color}"></span>${label}</button>`).join('')
        + `</div>`)
    + sec('Conversation flow', 'Bubbles show each paragraph of an agent reply as its own card; Flow runs the reply together as one block.',
        `<div class="mc-seg" id="setup-flow-seg">
          <button type="button" class="${act(!flow)}" onclick="setupPickChatStyle('bubbles',this)">Bubbles</button>
          <button type="button" class="${act(flow)}" onclick="setupPickChatStyle('flow',this)">Flow</button>
        </div>`)
    + sec('Connectivity', 'Reach Clayrune from your phone or another machine — remote access, push notifications and mobile pairing. Optional.',
        `<button type="button" class="btn-add" onclick="setupOpenConnectivity()">Open Connectivity settings…</button>`)
    + `<div id="local-access-section" style="margin-top:14px;text-align:left"></div>`
    + sec('Choose your level', 'Clayrune starts in a simple view. Turn on any power-user features you want to see.',
        `<div id="setup-adv-list" style="display:flex;flex-direction:column;gap:8px">`
        + ADV_FEATURES.map(f => `
        <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:6px;border-radius:4px;background:var(--surface2)">
          <input type="checkbox" ${advancedFlags[f.key] ? 'checked' : ''}
            onchange="setAdvancedFlag('${esc(f.key)}',this.checked)"
            style="margin-top:2px;width:15px;height:15px;accent-color:var(--accent)">
          <span style="flex:1"><span style="font-weight:600;color:var(--text)">${esc(f.label)}</span><br><span style="font-size:11px;color:var(--text-faint)">${esc(f.hint)}</span></span>
        </label>`).join('') + `</div>`);
}

// Move the .active highlight to the clicked button within its own group.
function _setupHighlight(btn) {
  if (!btn || !btn.parentElement) return;
  btn.parentElement.querySelectorAll('.active').forEach(el => el.classList.remove('active'));
  btn.classList.add('active');
}
function setupPickTone(tone, btn) { setTone(tone); _setupHighlight(btn); }
function setupPickAccent(accent, btn) { setAccent(accent); _setupHighlight(btn); }
function setupPickChatStyle(style, btn) { setChatStyle(style); _setupHighlight(btn); }

// Connectivity lives in Settings (the `connect` category). The setup overlay
// sits above every modal, so hide it while Settings is open and bring it back
// when Settings closes — the user lands where they left off.
async function setupOpenConnectivity() {
  const overlay = document.getElementById('setup-overlay');
  if (overlay) overlay.style.display = 'none';
  try {
    await openSettings();
    drillSettings('connect');
  } catch (e) {
    if (overlay) overlay.style.display = '';
    showToast('Could not open Connectivity settings: ' + e, 6000);
    return;
  }
  const iv = setInterval(() => {
    if (typeof openModals !== 'undefined' && openModals.has('__settings')) return;
    clearInterval(iv);
    const o = document.getElementById('setup-overlay');
    if (o) o.style.display = '';
  }, 400);
}

// ── Agent connections handlers ───────────────────────────────────────────────
function setupSelectProvider(name, selected) {
  if (selected) setupSelectedProviders.add(name);
  else setupSelectedProviders.delete(name);
  if (setupActive) setupShow(setupStep);
}

async function setupSetDefaultProvider(name) {
  if (!setupSelectedProviders.has(name)) return;
  setupExplicitDefault = name;
  await applyDefaultProvider(name);
}

// Provider-neutral: the server resolves each tier per runtime via
// latest_for() (engine_selection.py), so this never names a model id.
// `btn` is omitted on the initial auto-persist (onEnter) — only a real click
// moves the highlight.
async function setupPickModelTier(tier, btn) {
  setupModelTier = tier;
  if (btn) _setupHighlight(btn);
  await saveSetting('agent_model', 'tier:' + tier);
}

function setupInstallSelected(btn) {
  return providerInstallSelected(btn, Array.from(setupSelectedProviders));
}

// Repaint hook for provider-auth.js _repaintProviderRows: only the connections
// step renders provider rows, and re-rendering any other step would wipe input.
window._setupRepaint = () => {
  if (setupActive && SETUP_STEPS[setupStep] && SETUP_STEPS[setupStep].id === 'connections') setupShow(setupStep);
};

// Human-readable reason a selected provider is blocking Next — F2 (clean-VM
// run 2026-09-18): the old message was the step's own static hint repeated
// verbatim, so clicking a gated Next looked like it did nothing. Naming the
// exact vendor + exact problem gives the user something actionable instead.
function _setupProviderBlockReason(p) {
  if (!p.installed) return 'not installed';
  switch (p.auth_status) {
    case 'not_logged_in': return 'not signed in';
    case 'invalid_api_key': return 'invalid API key';
    case 'quota_exceeded': return 'quota exceeded';
    case 'unverified': return 'sign-in unverified — see note below';
    case 'oauth_rejected': return 'sign-in rejected by Google';
    case 'unknown': return 'status unknown — click Check setup status';
    default: return 'not signed in';
  }
}

// ── Flow ─────────────────────────────────────────────────────────────────────
function _setupVisible() { return SETUP_STEPS.filter(s => !(s.skip && s.skip())); }

// opts.rerun: opened from Settings ("Run setup again") — never skip a step.
function startFirstRun(opts) {
  setupActive = true;
  setupForced = !!(opts && opts.rerun);
  setupStep = 0;
  setupSelectedProviders = new Set();
  setupExplicitDefault = '';
  setupProviderChoiceVisited = false;
  // Reflect what is already saved; a pin (non-tier value) highlights nothing
  // and is left alone unless the user clicks a tier.
  const curModel = String((_globalConfig && _globalConfig.agent_model) || '').trim();
  setupModelTier = curModel.startsWith('tier:') ? curModel.slice(5)
    : (curModel ? '' : 'balanced');
  setupModelTierVisited = false;
  showDesktop();
  setupShow(0);
}

// The boot gate (index.html). True only when the server says this install was
// never set up. `'setup_completed' in _globalConfig` guards a failed /api/config
// hydration: {} must not read as "never set up" and re-run setup on an install
// that already finished it. An install whose browser already finished the OLD
// tour-owned first run (walkthrough_done — that flow included the provider
// choice) is treated as set up, and the flag is recorded on the server once.
function firstRunNeeded() {
  if (!_globalConfig || !('setup_completed' in _globalConfig)) return false;
  if (_globalConfig.setup_completed) return false;
  if (localStorage.getItem('walkthrough_done')) { _setupPersistCompleted(); return false; }
  return true;
}

async function _setupPersistCompleted() {
  try {
    const res = await fetch(API_BASE + '/api/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ setup_completed: true }),
    });
    if (res.ok) { try { _globalConfig.setup_completed = true; } catch (_) {} }
    else console.warn('setup_completed not saved: HTTP ' + res.status);
  } catch (e) { console.warn('setup_completed not saved', e); }
}

async function setupShow(idx) {
  const prevStep = SETUP_STEPS[setupStep];
  const prevCard = document.querySelector('#setup-overlay .wt-card');
  const keepScroll = prevCard && prevStep && SETUP_STEPS[idx] === prevStep ? prevCard.scrollTop : 0;

  setupStep = idx;
  const step = SETUP_STEPS[idx];
  if (!step) { setupFinish(); return; }
  if (step.skip && step.skip()) {
    if (idx < SETUP_STEPS.length - 1) { setupShow(idx + 1); return; }
    setupFinish(); return;
  }

  const old = document.getElementById('setup-overlay');
  const wasHidden = !!(old && old.style.display === 'none');
  if (old) old.remove();

  const overlay = document.createElement('div');
  overlay.id = 'setup-overlay';
  overlay.className = 'wt-overlay';
  if (wasHidden) overlay.style.display = 'none';
  const backdrop = document.createElement('div');
  backdrop.className = 'wt-backdrop';
  overlay.appendChild(backdrop);

  const visible = _setupVisible();
  const pos = visible.findIndex(s => s.id === step.id);
  const isFirst = pos === 0;
  const isLast = pos === visible.length - 1;

  let btns = '';
  if (!isFirst) btns += `<button class="wt-btn" onclick="setupBack()">Back</button>`;
  if (isLast) {
    btns += `<button class="wt-btn wt-btn-skip" onclick="setupFinish()">Not now</button>`;
    btns += `<button class="wt-btn wt-btn-primary" onclick="setupTakeTour()">Take the tour</button>`;
  } else {
    btns += `<button class="wt-btn wt-btn-skip" onclick="setupSkip()">Skip setup</button>`;
    btns += `<button class="wt-btn wt-btn-primary" onclick="setupNext()">${isFirst ? 'Get started' : 'Next'}</button>`;
  }

  const card = document.createElement('div');
  card.className = 'wt-card centered' + (step.wide ? ' wt-card-wide' : '');
  // Body strings are author-controlled hardcoded text or pre-built component
  // HTML (esc()'d at the source), same contract as walkthrough.js.
  card.innerHTML = `
    <div class="wt-title">${esc(step.title)}</div>
    <div class="wt-body">${step.body()}</div>
    <div class="wt-actions">
      <span class="wt-progress">${pos + 1} / ${visible.length}</span>
      ${btns}
    </div>`;
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  if (keepScroll) card.scrollTop = keepScroll;
  if (step.onEnter) step.onEnter();
}

function setupNext() {
  if (SETUP_STEPS[setupStep].id === 'connections') {
    const selected = (_agentProviders || []).filter(p => setupSelectedProviders.has(p.name));
    const defaultProvider = setupExplicitDefault || (_globalConfig && _globalConfig.default_provider);
    const problems = [];
    if (!selected.length) {
      problems.push('Select at least one vendor.');
    } else {
      if (!setupSelectedProviders.has(defaultProvider)) {
        problems.push('Choose a default from your selected vendors.');
      }
      for (const p of selected) {
        if (!p.installed || p.auth_status !== 'ok') {
          problems.push(`${p.display_name || p.name}: ${_setupProviderBlockReason(p)}`);
        }
      }
    }
    if (problems.length) {
      const el = document.getElementById('setup-provider-validation');
      if (el) el.textContent = problems.join(' · ') + ' — or skip setup and finish it later in Settings → Providers.';
      return;
    }
  }
  if (setupStep < SETUP_STEPS.length - 1) setupShow(setupStep + 1); else setupFinish();
}

function setupBack() {
  let prev = setupStep - 1;
  while (prev > 0 && SETUP_STEPS[prev].skip && SETUP_STEPS[prev].skip()) prev--;
  if (prev >= 0) setupShow(prev);
}

// Skipping is a decision, not an omission: it is recorded as completed so the
// flow does not nag on every load. "Run setup again" in Settings reopens it.
function setupSkip() { setupFinish(); }

function setupFinish() {
  setupActive = false;
  const el = document.getElementById('setup-overlay');
  if (el) el.remove();
  if (!(_globalConfig && _globalConfig.setup_completed)) _setupPersistCompleted();
}

function setupTakeTour() {
  setupFinish();
  startWalkthrough();
}

// ── interop: page-called surface ─────────────────────────────────────────────
// Invoked from OUTSIDE this module — the inline boot script (first-run gate)
// and generated onclick/onchange attributes — all of which resolve against the
// global object, never module scope.
window.startFirstRun = startFirstRun;   // interop: boot gate (index.html) + Settings "Run setup again" (generated onclick)
window.firstRunNeeded = firstRunNeeded; // interop: boot gate (index.html)
window.setupNext = setupNext;           // interop: setup card generated onclick
window.setupBack = setupBack;
window.setupSkip = setupSkip;
window.setupFinish = setupFinish;
window.setupTakeTour = setupTakeTour;
window.setupSelectProvider = setupSelectProvider;         // interop: provider row checkbox onchange (provider-auth.js _renderProviderRow)
window.setupSetDefaultProvider = setupSetDefaultProvider; // interop: provider row Default radio onchange
window.setupPickModelTier = setupPickModelTier;           // interop: model tier segmented control onclick
window.setupInstallSelected = setupInstallSelected;
window.setupPickTone = setupPickTone;
window.setupPickAccent = setupPickAccent;
window.setupPickChatStyle = setupPickChatStyle;
window.setupOpenConnectivity = setupOpenConnectivity;
