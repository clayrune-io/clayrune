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
let _setupPhoneRevealed = false;    // essentials-phone: "Set it up" clicked this run
let _setupAdvExpanded = false;      // essentials-detail: "Choose individually" expander

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
        </div><div class="prov-install-policy-note" style="margin-top:6px;font-size:11px;color:var(--text-faint)">${esc(_providerInstallPolicyNoteText || '')}</div>`
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
    id: 'essentials-yours',
    title: 'Make it yours',
    wide: true,
    body: () => _setupYoursHTML(),
  },
  {
    id: 'essentials-phone',
    title: 'Use Clayrune from your phone? (optional)',
    wide: true,
    body: () => _setupPhoneHTML(),
    onEnter: () => {
      // The LAN-passcode form is the existing Settings component; it renders
      // into #local-access-section and refreshes itself there after a save —
      // but only once revealed (the warning must not appear unprompted).
      if (_setupPhoneRevealed && typeof window.refreshLocalAccessSection === 'function') window.refreshLocalAccessSection();
    },
  },
  {
    id: 'essentials-detail',
    title: 'How much detail do you want to see?',
    wide: true,
    body: () => _setupDetailHTML(),
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

// ── Essentials, split into three short steps ─────────────────────────────────
// Clean-VM run (Ron, 2026-09-24): the old single "A few essentials" step
// dumped Theme, Conversation flow, Connectivity, the passcode warning and the
// full power-user checklist on one screen with no explanation of why any of
// it mattered. Each of A/B/C below opens with a one-sentence purpose and ends
// with the same footer line. Every control still calls the SAME setter
// Settings already uses (setTone/setAccent in appearance.js, setChatStyle,
// setAdvancedFlag, the LAN-passcode section in settings-sections.js) — no new
// config keys. Highlight state is patched in place where possible, not
// re-rendered: a re-render would wipe a half-typed passcode or API key.
const _SETUP_FOOTER = `<div style="margin-top:18px;font-size:11px;color:var(--text-faint)">You can change this any time in Settings.</div>`;
const _setupSec = (label, hint, inner) => `<div style="margin-top:14px;text-align:left">
    <div style="font-weight:600;color:var(--text)">${label}</div>
    <div style="font-size:11px;color:var(--text-faint);margin:2px 0 6px">${hint}</div>${inner}</div>`;

// A. "Make it yours" — theme + conversation style.
function _setupYoursHTML() {
  const tone = (typeof currentTone !== 'undefined') ? currentTone : 'warm';
  const accent = (typeof currentAccent !== 'undefined') ? currentAccent : '';
  const flow = (typeof _chatStyle !== 'undefined') && _chatStyle === 'flow';
  const act = (on) => on ? 'active' : '';
  const accents = [
    ['', 'Default', '#5b9ef5'], ['sunset', 'Sunset', '#e8824a'], ['rose', 'Rose', '#d96480'],
    ['lilac', 'Lilac', '#8a7ce0'], ['lagoon', 'Lagoon', '#4fa89a'], ['ink', 'Ink', '#6b7286'],
  ];
  return `<div style="font-size:13px;color:var(--text-dim);line-height:1.5">Pick a look and a reply style — both are one click to change later.</div>`
    + _setupSec('Theme', 'Dark is easy at night. Warm and Editorial are light, paper-like themes for daytime.',
        `<div class="mc-seg" id="setup-tone-seg">
          <button type="button" class="${act(tone === 'dark')}" onclick="setupPickTone('dark',this)">Dark</button>
          <button type="button" class="${act(tone === 'warm')}" onclick="setupPickTone('warm',this)">Warm</button>
          <button type="button" class="${act(tone === 'editorial')}" onclick="setupPickTone('editorial',this)">Editorial</button>
        </div>
        <div class="mc-accent-row" id="setup-accent-row" style="margin-top:8px;justify-content:flex-start;max-width:none">`
        + accents.map(([k, label, color]) => `<button type="button" class="mc-accent-pill ${act(accent === k)}" onclick="setupPickAccent('${k}',this)"><span class="mc-accent-swatch" style="background:${color}"></span>${label}</button>`).join('')
        + `</div>`)
    + _setupSec('Conversation flow', 'How an agent’s reply is shown — see it below before you pick.',
        `<div class="mc-seg" id="setup-flow-seg">
          <button type="button" class="${act(!flow)}" onclick="setupPickChatStyle('bubbles',this)">Bubbles</button>
          <button type="button" class="${act(flow)}" onclick="setupPickChatStyle('flow',this)">Flow</button>
        </div>
        <div style="display:flex;gap:10px;margin-top:10px;flex-wrap:wrap">
          <div class="setup-flow-preview ${act(!flow)}" id="setup-flow-preview-bubbles" style="flex:1;min-width:180px">
            <div class="setup-flow-preview-label">Bubbles</div>
            <div class="setup-mock-bubble">Found the bug — it’s a race in the SSE reconnect.</div>
            <div class="setup-mock-bubble">Fixed and pushed to your branch.</div>
          </div>
          <div class="setup-flow-preview ${act(flow)}" id="setup-flow-preview-flow" style="flex:1;min-width:180px">
            <div class="setup-flow-preview-label">Flow</div>
            <div class="setup-mock-flow">Found the bug — it’s a race in the SSE reconnect. Fixed and pushed to your branch.</div>
          </div>
        </div>`)
    + _SETUP_FOOTER;
}

// B. "Use Clayrune from your phone?" — optional. Connectivity and the LAN
// passcode form (with its locked-out warning) are revealed ONLY after an
// explicit "Set it up" click; they must never appear unprompted.
function _setupPhoneHTML() {
  let html = `<div style="font-size:13px;color:var(--text-dim);line-height:1.5">Reach your agents from your phone or another computer, and get notified when one needs you. Most people set this up later.</div>`;
  if (!_setupPhoneRevealed) {
    html += `<div style="display:flex;gap:8px;margin-top:16px">
      <button type="button" class="wt-btn setup-btn-secondary" onclick="setupSkipPhone()">Not now</button>
      <button type="button" class="wt-btn wt-btn-primary" onclick="setupRevealPhone()">Set it up</button>
    </div>`;
  } else {
    html += _setupSec('Connectivity', 'Remote access, push notifications and mobile pairing.',
        `<button type="button" class="btn-add" onclick="setupOpenConnectivity()">Open Connectivity settings…</button>`)
      + _setupSec('Passcode', 'A passcode lets your own phone or laptop on the same Wi-Fi sign in. Without one, only this computer can open Clayrune.',
        `<div id="local-access-section"></div>`);
  }
  return html + _SETUP_FOOTER;
}
function setupRevealPhone() { _setupPhoneRevealed = true; if (setupActive) setupShow(setupStep); }
// "Not now" is the default: it just advances like any other Next.
function setupSkipPhone() { setupNext(); }

// C. "How much detail do you want to see?" — two presets up front, the
// original per-feature checklist collapsed behind "Choose individually".
function _setupDetailHTML() {
  const allOn = ADV_FEATURES.every(f => !!advancedFlags[f.key]);
  let html = `<div style="font-size:13px;color:var(--text-dim);line-height:1.5">Clayrune starts simple. Power users can turn on extra panels and counters any time.</div>`;
  html += `<div style="display:flex;gap:12px;margin-top:16px;flex-wrap:wrap">
    <button type="button" class="setup-preset-card ${!allOn ? 'active' : ''}" onclick="setupPickPreset('simple',this)">
      <div class="setup-preset-title">Simple</div>
      <div class="setup-preset-hint">Just the essentials. Recommended.</div>
    </button>
    <button type="button" class="setup-preset-card ${allOn ? 'active' : ''}" onclick="setupPickPreset('power',this)">
      <div class="setup-preset-title">Power user</div>
      <div class="setup-preset-hint">Token counters, tool-call lines, GitHub badges and more, all on.</div>
    </button>
  </div>`;
  html += `<div style="margin-top:16px;text-align:left">
    <button type="button" class="setup-adv-expander" onclick="setupToggleAdvExpander()">${_setupAdvExpanded ? '▾' : '▸'} Choose individually</button>`
    + (_setupAdvExpanded ? `<div id="setup-adv-list" style="display:flex;flex-direction:column;gap:8px;margin-top:8px">`
        + ADV_FEATURES.map(f => `
        <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:6px;border-radius:4px;background:var(--surface2)">
          <input type="checkbox" ${advancedFlags[f.key] ? 'checked' : ''}
            onchange="setAdvancedFlag('${esc(f.key)}',this.checked)"
            style="margin-top:2px;width:15px;height:15px;accent-color:var(--accent)">
          <span style="flex:1"><span style="font-weight:600;color:var(--text)">${esc(f.label)}</span><br><span style="font-size:11px;color:var(--text-faint)">${esc(f.hint)}</span></span>
        </label>`).join('') + `</div>` : '')
    + `</div>`;
  return html + _SETUP_FOOTER;
}
// Presets set every ADV_FEATURES flag via the same setter Settings uses —
// no new config key, just a batch of the existing per-key one.
function setupPickPreset(which) {
  ADV_FEATURES.forEach(f => setAdvancedFlag(f.key, which === 'power'));
  if (setupActive) setupShow(setupStep);
}
function setupToggleAdvExpander() { _setupAdvExpanded = !_setupAdvExpanded; if (setupActive) setupShow(setupStep); }

// Move the .active highlight to the clicked button within its own group.
function _setupHighlight(btn) {
  if (!btn || !btn.parentElement) return;
  btn.parentElement.querySelectorAll('.active').forEach(el => el.classList.remove('active'));
  btn.classList.add('active');
}
function setupPickTone(tone, btn) { setTone(tone); _setupHighlight(btn); }
function setupPickAccent(accent, btn) { setAccent(accent); _setupHighlight(btn); }
function setupPickChatStyle(style, btn) {
  setChatStyle(style);
  _setupHighlight(btn);
  // Patch the live preview's highlight in place too — same "no re-render"
  // rule as the rest of this file, so a half-typed field elsewhere survives.
  const bubblesPrev = document.getElementById('setup-flow-preview-bubbles');
  const flowPrev = document.getElementById('setup-flow-preview-flow');
  if (bubblesPrev) bubblesPrev.classList.toggle('active', style !== 'flow');
  if (flowPrev) flowPrev.classList.toggle('active', style === 'flow');
}

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

async function setupInstallSelected(btn) {
  _setupStartInstallWatch();
  const before = _setupCurrentTerminalIds();
  await providerInstallSelected(btn, Array.from(setupSelectedProviders));
  // Diff, don't assume a fixed number of ids: a batch install can open more
  // than one terminal. Track only what THIS call actually added, so Finish/
  // Skip never closes a terminal setup did not open.
  _setupCurrentTerminalIds().forEach((id) => { if (!before.has(id)) _setupOpenedTerminalIds.add(id); });
  _setupDockLiveTerminals();
}

// Repaint hook for provider-auth.js _repaintProviderRows: only the connections
// step renders provider rows, and re-rendering any other step would wipe input.
window._setupRepaint = () => {
  if (setupActive && SETUP_STEPS[setupStep] && SETUP_STEPS[setupStep].id === 'connections') setupShow(setupStep);
};

// ── Setup-terminal visibility + live polling (clean-VM run 2026-09-24) ──────
// The setup overlay (.wt-overlay, z-2000) painted over ANY terminal pop-out
// launched from setup (a .modal-window inside #modal-layer, z-300) — install
// ("Install selected") or sign-in (Gemini's "Sign in" opens a real-PTY
// terminal for its account-picker TUI, same as install). While one is live:
// raise #modal-layer above the overlay (same mechanism app.css already uses
// for maximize, body.mc-modal-maximized), dock the setup card to the left so
// the two don't sit exactly on top of each other, and poll provider status
// the same way "Check setup status" does so a row flips without the user
// hunting for a button.
//
// Visibility (the `setup-terminal-live` body class) and polling are two
// SEPARATE lifecycles, not one — this was the regression Ron hit (2026-09-24):
// the polling loop used to remove the class the moment it stopped polling,
// including on a batch reporting "finished" with failures. MC-959 deliberately
// leaves a FAILED install's terminal open so its output can be read, so the
// terminal was still on screen with the class (and the card's left dock)
// already gone — the very next repaint (any click inside the card triggers
// one via _setupRepaint) put the centered card right back on top of it.
// Visibility now tracks DOM presence directly via a MutationObserver and is
// the only thing that ever adds/removes the class; polling only starts/stops
// its own timer.
// Modal ids (`__terminal_<sessionId>`) of terminal pop-outs SETUP itself
// opened this run — populated by setupInstallSelected's before/after diff and
// window._setupOnTerminalOpened's sessionId argument, never by scanning the
// DOM wholesale (that would also catch a terminal the user opened some other
// way). Closed on Finish/Skip via _setupCloseOpenedTerminals; reset in
// startFirstRun.
let _setupOpenedTerminalIds = new Set();
function _setupCurrentTerminalIds() {
  return new Set(Array.from(document.querySelectorAll('#modal-layer .modal-window[data-modal-id^="__terminal_"]'))
    .map((w) => w.dataset.modalId));
}
let _setupInstallWatchTimer = null;
let _setupInstallWatchRunning = false; // separate from the timer ID: setInterval's return value must never be truthiness-tested (0 is a legal id in a synthetic/non-browser host)
let _setupTerminalObserver = null;

function _setupInstallComplete() {
  const selected = (_agentProviders || []).filter(p => setupSelectedProviders.has(p.name));
  return selected.length > 0 && selected.every(p => p.installed && p.auth_status === 'ok');
}

// DOM presence, not terminal.js's own lifecycle state — decoupled on purpose
// so this holds regardless of HOW the terminal went away (user hit its own
// Close, or it auto-closed on the child process exiting). Checked ONLY as the
// stop condition, never to gate starting: right after opening one the
// terminal can take a beat to mount, and the first poll tick is 4s out — plenty.
function _setupInstallTerminalOpen() {
  return !!document.querySelector('#modal-layer .modal-window[data-modal-id^="__terminal_"]');
}

// Adds the visibility class and keeps it correct for as long as ANY setup
// terminal is actually on screen, independent of whether polling is running.
// Safe to call repeatedly (install then sign-in, or several sign-ins) — the
// observer is created once and just keeps watching #modal-layer's children.
function _setupMarkTerminalLive() {
  document.body.classList.add('setup-terminal-live');
  if (_setupTerminalObserver) return;
  const layer = document.getElementById('modal-layer');
  if (!layer) return;
  _setupTerminalObserver = new MutationObserver(() => {
    if (!_setupInstallTerminalOpen()) _setupUnmarkTerminalLive();
  });
  _setupTerminalObserver.observe(layer, { childList: true });
}

function _setupUnmarkTerminalLive() {
  document.body.classList.remove('setup-terminal-live');
  if (_setupTerminalObserver) { _setupTerminalObserver.disconnect(); _setupTerminalObserver = null; }
}

// Polls MC-959's per-batch GET .../install-status (set by providerInstallSelected
// into _providerInstallStatusUrl). Marks any vendor the batch itself reports as
// failed with a short row message and reports whether the BATCH says it is
// done — the authoritative signal, since a failed install's terminal now stays
// open (terminal.js, MC-959) so its output can be read, and the DOM-presence
// check below can no longer tell "still running" from "failed and left open".
// Returns false (never finished) on any fetch/shape problem — an older server
// without the route, a dropped connection — so the caller falls back to the
// DOM check instead of hanging on a signal that will never arrive.
async function _setupPollInstallStatus() {
  if (!_providerInstallStatusUrl) return false;
  try {
    const res = await fetch(API_BASE + _providerInstallStatusUrl);
    const data = await res.json().catch(() => null);
    if (!data || !data.ok) return false;
    // Per-vendor progress (queued/installing/installed/failed) — _renderProviderRow
    // reads this store back on the very next render, driven by the feed's own
    // result/started_at/running_now, never a client-side timer guess.
    for (const v of (data.vendors || [])) {
      _providerInstallProgress[v.name] = { result: v.result, started_at: v.started_at, running_now: !!v.running_now };
    }
    for (const name of (data.failed || [])) {
      const text = 'Install failed — see terminal.';
      _providerInstallMsg[name] = text;
      const el = document.getElementById(`prov-install-msg-${name}`);
      if (el) el.textContent = text;
    }
    return data.running === false;
  } catch (e) {
    return false;
  }
}

// Starts (or, if already running, no-ops) the status-polling loop. Callable
// for install OR sign-in — a sign-in flow never sets _providerInstallStatusUrl,
// so _setupPollInstallStatus() just always reports "not finished" from that
// signal and the loop instead stops on _setupInstallComplete() (every selected
// vendor now installed+signed in) or the terminal closing.
function _setupStartInstallWatch() {
  _setupMarkTerminalLive();
  if (_setupInstallWatchRunning) return;
  _setupInstallWatchRunning = true;
  _setupInstallWatchTimer = setInterval(async () => {
    await providerRefreshAll();
    const batchFinished = await _setupPollInstallStatus();
    // providerRefreshAll() already repainted once, BEFORE this tick's status
    // poll landed — repaint again so this tick's own progress (queued ->
    // installing -> installed/failed, elapsed seconds) shows without waiting
    // another 4s for the next tick to do it. window._repaintProviderRows:
    // both files are ES modules (static/index.html `type="module"`), so a
    // top-level `function` in provider-auth.js is NOT global here.
    if (typeof window._repaintProviderRows === 'function') window._repaintProviderRows();
    // Stop POLLING on success (nothing left to watch), OR the batch itself
    // reporting finished (MC-959 — includes a FAILED install, whose terminal
    // stays open), OR — fallback for a server too old to carry status_url —
    // the terminal itself going away. This only ever stops the poll timer;
    // it does NOT touch visibility (see _setupMarkTerminalLive above) — a
    // failed install's terminal staying open must keep the card docked.
    if (_setupInstallComplete() || batchFinished || !_setupInstallTerminalOpen()) _setupStopInstallWatch();
  }, 4000);
}

function _setupStopInstallWatch() {
  if (_setupInstallWatchRunning) { clearInterval(_setupInstallWatchTimer); _setupInstallWatchTimer = null; _setupInstallWatchRunning = false; }
  _providerInstallStatusUrl = '';
}

// Move any terminal pop-out(s) opened from setup off to the top-right corner
// so they don't land exactly under the (now left-docked) setup card. Purely a
// reposition of the existing element — terminal.js's own centering already ran.
function _setupDockLiveTerminals() {
  document.querySelectorAll('#modal-layer .modal-window[data-modal-id^="__terminal_"]').forEach((win) => {
    win.style.left = Math.max(20, window.innerWidth - win.offsetWidth - 24) + 'px';
    win.style.top = '24px';
  });
}

// Interop hook: called by provider-auth.js's settingsProviderTerminalLogin
// right after it opens a real-PTY sign-in terminal (Gemini's account-picker
// TUI, the same class of pop-out install uses) — that function is shared with
// Settings, so it only reaches here when setup is actually the caller.
// sessionId is the terminal's own session id (data.session_id at the call
// site) — recorded so this terminal is closed on Finish/Skip like an install
// terminal, never left as a permanent gap between the two tracking paths.
window._setupOnTerminalOpened = function (sessionId) {
  if (!setupActive) return;
  if (sessionId) _setupOpenedTerminalIds.add('__terminal_' + sessionId);
  _setupStartInstallWatch();
  _setupDockLiveTerminals();
};

// Close every terminal pop-out SETUP opened this run (install + sign-in) via
// terminal.js's normal close path (closeModalById -> cleanupTerminalModal),
// so the PTY session is actually torn down server-side, not just the DOM
// node removed. Only ids _setupOpenedTerminalIds actually recorded — a
// terminal the user opened some other way is never touched.
function _setupCloseOpenedTerminals() {
  if (typeof window.closeModalById !== 'function') return;
  _setupOpenedTerminalIds.forEach((id) => { try { window.closeModalById(id); } catch (_) {} });
  _setupOpenedTerminalIds = new Set();
}

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

// What the connections step still lacks, as a warning shown above the footer,
// or '' when a signed-in default is in place. NEVER a gate (Ron, clean-VM run
// 2026-09-24): with the network down nothing can install or sign in, and that
// must not trap the user in setup — they finish now and sign in later from
// Settings -> Providers. It went gate -> "every ticked vendor must be signed
// in" (Next greyed out with Claude signed in as default) -> this. The warning
// leads with the consequence so it reads as the thing to act on; the only
// friction left is setupNext()'s one confirm when NO vendor is ready at all.
function _setupConnectionsWarning() {
  const selected = (_agentProviders || []).filter(p => setupSelectedProviders.has(p.name));
  const label = p => p.display_name || p.name;
  const later = 'You can continue and sign in later from Settings → Providers.';
  if (!selected.length) return `No AI vendor selected: agents can't run until you select and sign in to at least one. ${later}`;
  const ready = selected.filter(p => p.installed && p.auth_status === 'ok');
  const pending = selected.filter(p => !ready.includes(p));
  const pendingTxt = pending.map(p => `${label(p)}: ${_setupProviderBlockReason(p)}.`).join(' ');
  if (!ready.length) return `No vendor is signed in yet: agents can't run until you sign in to at least one. ${pendingTxt} ${later}`;
  const defaultName = setupExplicitDefault || (_globalConfig && _globalConfig.default_provider);
  const def = selected.find(p => p.name === defaultName);
  const readyNames = ready.map(label).join(' or ');
  const parts = [];
  if (!def) parts.push(`Pick a default: ${readyNames} is signed in.`);
  else if (!ready.includes(def)) parts.push(`Your default, ${label(def)}, is ${_setupProviderBlockReason(def)}; make ${readyNames} the default or sign in to it.`);
  if (pending.length) parts.push(`Not ready yet: ${pendingTxt}`);
  return parts.length ? `${parts.join(' ')} ${later}` : '';
}

function _setupAnyVendorReady() {
  return (_agentProviders || []).some(p =>
    setupSelectedProviders.has(p.name) && p.installed && p.auth_status === 'ok');
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
  _setupPhoneRevealed = false;
  _setupAdvExpanded = false;
  _setupOpenedTerminalIds = new Set();
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
  const isConnStep = step.id === 'connections';
  // step.body() must run BEFORE the block-reason check: the connections
  // step's body() is what seeds setupSelectedProviders with the
  // already-installed+signed-in default on its first render (the pre-tick
  // in SETUP_STEPS[1].body). Checking the reason first read an empty set and
  // showed Next as falsely blocked on that very first render.
  const bodyHtml = step.body();
  const connWarning = isConnStep ? _setupConnectionsWarning() : '';

  let btns = '';
  if (!isFirst) btns += `<button class="wt-btn" onclick="setupBack()">Back</button>`;
  if (isLast) {
    // "Not now" (not "Skip"/wt-btn-skip): this is the tour OFFER, not a setup
    // control — wt-btn-skip is the tour's own "Skip" look, and reusing it here
    // was part of what made this final card readable as the tour itself.
    btns += `<button class="wt-btn setup-btn-secondary" onclick="setupFinish()">Not now</button>`;
    btns += `<button class="wt-btn wt-btn-primary" onclick="setupTakeTour()">Take the tour</button>`;
  } else {
    // First run cannot be skipped: setup_completed is written ONLY from the
    // final step's own button (setupFinish/setupTakeTour below). A rerun
    // from Settings ("Run setup again", setupForced) is already a set-up
    // install, so it keeps a plain close — setupSkip->setupFinish is a no-op
    // re-persist there (_setupPersistCompleted only fires when not already
    // completed). The connections step additionally blocks Next until a
    // vendor is installed+signed in and a default is chosen; its only exit
    // for a genuinely vendor-less user is the explicit-confirm link below,
    // which — like the old "Skip setup" — still ends setup and persists
    // setup_completed, so Clayrune stops re-nagging on every load.
    // Neither secondary action uses wt-btn-skip: that class IS the tour's own
    // "Skip" look (walkthrough.js), and a setup control wearing it is exactly
    // the confusion Ron hit on the clean-VM run (2026-09-23).
    if (setupForced) btns += `<button class="wt-btn setup-btn-secondary" onclick="setupSkip()">Close</button>`;
    // essentials-phone, unrevealed: the body already asks the one question
    // ("Not now" / "Set it up") — a generic Next alongside it would be a
    // third primary-looking button doing near-the-same thing as "Not now".
    // Once revealed the body is just informational again, so Next returns.
    const suppressNext = step.id === 'essentials-phone' && !_setupPhoneRevealed;
    if (!suppressNext) btns += `<button class="wt-btn wt-btn-primary" onclick="setupNext()">${isFirst ? 'Get started' : 'Next'}</button>`;
  }

  const pct = Math.round(((pos + 1) / visible.length) * 100);
  const card = document.createElement('div');
  // wt-card-setup marks this DOM as setup, not tour, for both CSS (its own
  // accent band, distinct from the tour's .wt-card look) and smoke tests
  // (tools/smoke/first-run-setup-gate.mjs asserts on this class).
  card.className = 'wt-card wt-card-setup centered' + (step.wide ? ' wt-card-wide' : '');
  // Body strings are author-controlled hardcoded text or pre-built component
  // HTML (esc()'d at the source), same contract as walkthrough.js.
  card.innerHTML = `
    <div class="setup-band">
      <span class="setup-band-label">Setup</span>
      <span class="setup-band-track"><span class="setup-band-fill" style="width:${pct}%"></span></span>
      <span class="setup-band-progress">Step ${pos + 1} of ${visible.length}</span>
    </div>
    <div class="wt-title">${esc(step.title)}</div>
    <div class="wt-body">${bodyHtml}</div>
    ${connWarning ? `<div class="wt-next-reason" id="wt-next-reason" role="status">${esc(connWarning)}</div>` : ''}
    <div class="wt-actions">
      <span style="flex:1"></span>
      ${btns}
    </div>`;
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  if (keepScroll) card.scrollTop = keepScroll;
  if (step.onEnter) step.onEnter();
}

function setupNext() {
  // Never blocks (see _setupConnectionsWarning). Leaving with NO vendor ready
  // gets one confirm naming the consequence; clicking OK finishes setup
  // normally, so the user is not re-asked on every load.
  if (SETUP_STEPS[setupStep].id === 'connections' && !_setupAnyVendorReady()) {
    if (!confirm('No AI vendor is signed in yet, so Clayrune cannot run agents until you sign in to one. You can do that any time from Settings → Providers. Continue anyway?')) return;
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
  _setupStopInstallWatch();
  _setupUnmarkTerminalLive();
  _setupCloseOpenedTerminals();
  const el = document.getElementById('setup-overlay');
  if (el) el.remove();
  if (!(_globalConfig && _globalConfig.setup_completed)) _setupPersistCompleted();
}

function setupTakeTour() {
  setupFinish();
  startWalkthrough();
}

// The header '?' button (index.html) and the command-palette "Take Tour"
// entry (modal-manager.js) both used to call startWalkthrough() directly.
// The tour's last step is now the only place it's offered (point 4 of the
// first-run fix) — redirect into setup instead of hiding either control,
// since a static header button can't cheaply track async config-load state
// but a click-time check can.
function startTourOrSetup() {
  if (setupActive || (typeof firstRunNeeded === 'function' && firstRunNeeded())) { startFirstRun(); return; }
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
window.setupRevealPhone = setupRevealPhone;     // interop: essentials-phone "Set it up" generated onclick
window.setupSkipPhone = setupSkipPhone;         // interop: essentials-phone "Not now" generated onclick
window.setupPickPreset = setupPickPreset;       // interop: essentials-detail preset card generated onclick
window.setupToggleAdvExpander = setupToggleAdvExpander; // interop: essentials-detail "Choose individually" generated onclick
window.startTourOrSetup = startTourOrSetup;     // interop: header '?' button + command-palette "Take Tour" entry
