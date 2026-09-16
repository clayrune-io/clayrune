// ── Walkthrough / Tour ────────────────────────────────────────────────────────

let wtActive = false;
let wtStep = 0;
let wtDontShow = false;

const WT_STEPS = [
  {
    id: 'welcome',
    title: 'Welcome to Clayrune',
    body: 'Clayrune is your operator console for long-running coding agents — a multi-project dashboard where you dispatch, monitor, and coordinate AI work across many parallel streams. Quick tour: about 10 steps, 2 minutes.',
    target: null, pos: 'center',
  },
  {
    id: 'provider-choice',
    title: 'Which AI do you work with?',
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
      const cur = (_globalConfig && _globalConfig.default_provider) || 'claude';
      return `Clayrune drives whichever coding agent you sign in with. Pick one — you can change this any time in Settings, or per-chat in the composer.<div style="margin-top:14px;display:flex;flex-direction:column;gap:8px;text-align:left">` +
        provs.map(p => {
          const state = _wtProviderState(p);
          const installBtn = p.installed ? '' : `
            <button type="button" class="btn-add" style="padding:2px 10px;font-size:11px;flex-shrink:0"
              onclick="event.preventDefault();wtInstallProvider('${esc(p.name)}',this)">Install</button>`;
          return `
          <div>
            <label style="display:flex;align-items:center;gap:10px;cursor:pointer;padding:8px;border-radius:4px;background:var(--surface2)">
              <input type="radio" name="wt-provider" value="${esc(p.name)}" ${cur === p.name ? 'checked' : ''}
                onchange="wtSetDefaultProvider('${esc(p.name)}')"
                style="width:15px;height:15px;accent-color:var(--accent)">
              <span style="flex:1;font-weight:600;color:var(--text)">${esc(p.display_name)}</span>
              <span style="font-size:11px;font-weight:600;color:${state.color}">${esc(state.label)}</span>
              ${installBtn}
            </label>
            <div id="wt-install-msg-${esc(p.name)}" style="font-size:11px;color:var(--text-faint);padding:2px 8px 0"></div>
          </div>`;
        }).join('') + `</div>`;
    },
    target: null, pos: 'center',
    // Skip ONLY when the installer already wrote default_provider into
    // config.json before this first launch (install.sh / install.ps1,
    // 2026-09-14) — asking again here would be the exact double-prompt this
    // step exists to avoid. Does NOT skip on "only one CLI installed" (or
    // zero) any more: that was exactly the fresh-Mac-.app case where nothing
    // is installed yet and the user still needs to see the install offer.
    skip: () => !!(_globalConfig && _globalConfig.default_provider),
  },
  {
    id: 'advanced-picker',
    title: 'Choose your level',
    body: () => `Clayrune starts in a simple view. Turn on any power-user features you want to see — you can change these anytime in Settings.<div id="wt-adv-list" style="margin-top:14px;display:flex;flex-direction:column;gap:8px;text-align:left">` +
      ADV_FEATURES.map(f => `
        <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:6px;border-radius:4px;background:var(--surface2)">
          <input type="checkbox" ${advancedFlags[f.key] ? 'checked' : ''}
            onchange="setAdvancedFlag('${esc(f.key)}',this.checked)"
            style="margin-top:2px;width:15px;height:15px;accent-color:var(--accent)">
          <span style="flex:1"><span style="font-weight:600;color:var(--text)">${esc(f.label)}</span><br><span style="font-size:11px;color:var(--text-faint)">${esc(f.hint)}</span></span>
        </label>`).join('') + `</div>`,
    target: null, pos: 'center',
  },
  {
    id: 'sidebar',
    title: 'Sidebar Navigation',
    body: 'Top of the sidebar: <strong>Dashboard</strong>, <strong>Inbox</strong> (anything waiting on you), <strong>Floor</strong> (every agent, working or idle, across every project — more on that shortly) and <strong>Incognito</strong> (a scratch chat with no memory or rules). Then a workspace group — <strong>Backlog</strong>, <strong>Desk</strong> (the marketing/social surface), <strong>Automation</strong>, <strong>Calendar</strong> and <strong>History</strong>. Less-frequent surfaces (🐝 Hivemind, Skills, Secrets, Backup &amp; Restore, Personas, Media, Shared Rules, Processes) sit under <strong>Advanced</strong> — click it to expand. Hover to expand the sidebar itself.',
    target: '#sidebar', pos: 'right',
    skip: () => window.innerWidth <= 960, // hidden on mobile
  },
  {
    id: 'header',
    title: 'Header & Toolbar',
    body: 'Search projects + commands with <strong>Ctrl+K</strong> (fuzzy search, jump to any view, re-run this tour), see active agents at a glance, and check the live badge that pulses while auto-refresh is on. The <strong>?</strong> button re-runs this tour any time. Just below: switch <strong>Grid</strong>/<strong>List</strong> views, filter by status or domain, or create a new project.',
    target: '.header', pos: 'bottom',
    skip: () => window.innerWidth <= 960, // .header is display:none on mobile
  },
  {
    id: 'sample-tile',
    title: 'Project Tiles',
    body: 'Each tile shows a project’s status and last activity. Click one to open it as a modal window — multiple modals can be open at once, drag them around, resize, minimize; open conversations and their layouts even survive a page refresh. We’ve created a starter project for you — <strong>Clayrune</strong> — its agent is your in-app help desk.',
    // Spotlight the REAL tile (created on first boot / by onEnter below) so
    // the highlight sits exactly on it — the injected demo tile floated at a
    // hardcoded offset next to the real one. `demo` stays as the fallback
    // for DOMs without grid tiles (mobile list view).
    target: '.card[data-id="clayrune"]', pos: 'right', demo: 'tile',
    onEnter: async () => {
      await fetch(API_BASE + '/api/walkthrough/sample-project', { method: 'POST' });
      await refreshSilent();
    },
  },
  {
    id: 'tabs',
    title: 'Tabs & Menu',
    body: 'A project modal has seven tabs at the top of its <strong>three-dot menu</strong>: <strong>Agent</strong> (conversation + dispatch), <strong>Backlog</strong>, <strong>Social</strong> (this project’s Desk queue), <strong>Agent Log</strong> (completed sessions — click any to read its transcript), <strong>Documents</strong>, <strong>Activity</strong> and <strong>Workflows</strong>. Below them, the same menu holds Status, Appearance, Edit Profile, Agent Settings, and an <strong>Advanced</strong> group — GitHub &amp; Code Sync, Memory, Rules, Skills, Export, MCP servers, Personas, Media, and this project’s Hiveminds.',
    target: null, pos: 'left', demo: 'modal-menu', demoTarget: '.wt-menu-tabs',
  },
  {
    id: 'agent',
    title: 'Agent Dispatch',
    body: 'Type a task in the Agent tab and click Dispatch. The agent runs in the background and streams output here AND into the bottom Agent Console so you can keep watching from anywhere — including a sub-agent it dispatches on its own, which reports back into this same chat when it finishes. Plans triggered by the agent show approve/collapse buttons — nothing dangerous runs without your click.',
    target: null, pos: 'left', demo: 'modal-agent',
  },
  {
    id: 'floor',
    title: 'The Floor — every agent, at a glance',
    body: 'The Floor shows every agent session across every project as a figure on a board — working, idle, or blocked. The <strong>Bench</strong> along the side lists every agent type you can hire; drag a figure onto a project (or use its no-drag hire button) to add that type to the project’s roster. Click any figure to open its chat.',
    target: '[data-nav="floor"]', pos: 'right',
    skip: () => window.innerWidth <= 960, // mobile: reachable via the nav drawer
  },
  {
    id: 'desk',
    title: 'The Desk — marketing & social',
    body: 'The <strong>Desk</strong> is Clayrune’s in-house marketing surface: a <strong>Board</strong> for campaigns and ideas, a <strong>Queue</strong> of drafts waiting on your approval, a <strong>Calendar</strong> of what’s scheduled to go out, and a <strong>Ledger</strong> of what’s already been said. Nothing posts without a click from you.',
    target: '[data-nav="social"]', pos: 'right',
    skip: () => window.innerWidth <= 960, // mobile: reachable via the nav drawer
  },
  {
    id: 'hivemind-sidebar',
    title: '🐝 Hivemind — multi-agent runs',
    body: 'Hivemind is Clayrune’s signature feature: an orchestrator agent decomposes a goal into workstreams, then parallel worker agents tackle them in coordination. The <strong>🐝 Hivemind</strong> sidebar entry shows every hivemind across every project — each card has a planner-to-workers tree, status pill, and stats. Long-idle "active" hiveminds auto-mark themselves <strong>stale</strong> with a Restart control.',
    target: '[data-nav="hivemind"]', pos: 'right',
    // Hivemind lives in the sidebar's "Advanced" group, which is collapsed
    // (display:none) by default — without this the step spotlighted a 0x0
    // element and read as a blank screen.
    onEnter: () => { wtExpandAdvancedSidebar(); },
    onLeave: () => { wtRestoreAdvancedSidebar(); },
    skip: () => window.innerWidth <= 960, // mobile uses bottom-tab; covered separately
  },
  {
    id: 'scheduler',
    title: 'Automation — recurring agents',
    body: 'The <strong>Automation</strong> sidebar entry sets up tasks that fire on a daily / cron / interval schedule. Each schedule has a <strong>▶ Run Now</strong> button to fire immediately and a <strong>Runs</strong> button that opens an inline panel listing the most recent runs (50 per page). A schedule can also run a multi-step <strong>workflow</strong> — open one to edit it visually on the workflow canvas.',
    target: '[data-nav="scheduler"]', pos: 'right',
    skip: () => window.innerWidth <= 960, // mobile bottom-tab covers this
  },
  {
    id: 'bottom-tabs',
    title: 'Mobile navigation',
    body: 'The bottom bar is quick access: <strong>Inbox</strong>, <strong>Search</strong>, <strong>+ New project</strong>, <strong>Claydo</strong>, and <strong>You</strong> (settings). Everything else — Dashboard, Floor, Backlog, Desk, Hivemind, Automation, Calendar, History — is one tap away in the ☰ menu, top-left.',
    target: '#bottom-tab-bar', pos: 'top',
    skip: () => window.innerWidth > 960, // only on mobile
  },
  {
    id: 'ask-claydo',
    title: 'Ask Claydo any time',
    body: 'Click the floating <strong>Claydo</strong> button bottom-right (it’s pulsing for you) to ask questions about Clayrune in plain English — Claydo can highlight the relevant UI element while explaining. For hands-on project work, Claydo is also a hireable agent type from the Floor, same name and face — the FAB explains and points, hired Claydo does the work.',
    // The FAB is display:none at <=960px; on mobile Claydo is a bottom-bar slot.
    target: () => window.innerWidth <= 960 ? '#bottom-tab-bar [data-nav="claydo"]' : '#claydo-fab',
    pos: 'left',
  },
  {
    id: 'done',
    title: 'You’re all set',
    body: 'Start by exploring the <strong>Clayrune</strong> starter project or create your own with <strong>+ New Project</strong>. Full-install backups and restore points live under Advanced → Backup; your phone can reach Clayrune too, from Settings → Remote Access. Re-run this tour any time from Settings, the Command Palette (Ctrl+K), or the <strong>?</strong> button in the header.',
    target: null, pos: 'center',
  },
];

// Provider-choice step handler. Reuses the generic saveSetting() PUT that
// Settings -> Default provider already calls — one write path, not two.
async function wtSetDefaultProvider(name) {
  await saveSetting('default_provider', name);
  // First-run follows the user's choice immediately. Refresh the provider
  // inventory (its `default`/`in_use` flags predate this click), then run the
  // same selected-provider auth check used at boot so Codex never produces a
  // Claude login prompt and an unsigned-in choice gets its own CTA.
  _agentProviders = null;
  try { await _ensureAgentProviders(); } catch (e) { /* auth refresh still uses config */ }
  if (typeof refreshAuthStatus === 'function') refreshAuthStatus();
}

// Per-provider state label for the provider-choice step — same three states
// the Settings provider card already shows (provider-settings.js), so a user
// who later opens Settings sees consistent language, not a second vocabulary.
function _wtProviderState(p) {
  if (!p.installed) return { label: 'not installed', color: 'var(--text-faint)' };
  if (p.auth_status === 'ok') return { label: 'signed in', color: 'var(--green)' };
  if (p.auth_status === 'not_logged_in') return { label: 'not signed in', color: 'var(--amber)' };
  return { label: 'installed', color: 'var(--text-faint)' };
}

// "Install" button on an uninstalled provider row. Launches the SAME command
// the installers use (server resolves it from the runtime's own install_hint
// — see agent_provider_install_launch) in a new OS terminal so the user can
// watch it run, mirroring the existing "Launch terminal login" pattern
// (provider-auth.js). Never invents its own command: {ok:false} always
// carries the exact one to run by hand when the server can't launch it
// itself (no npm/curl on PATH, no terminal emulator).
async function wtInstallProvider(name, btnEl) {
  const msgEl = document.getElementById(`wt-install-msg-${name}`);
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Installing...'; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${name}/install-launch`,
                            { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (data.ok) {
      if (msgEl) msgEl.textContent = 'A terminal opened to install it. Once it finishes, click Refresh.';
      if (btnEl) {
        btnEl.textContent = 'Refresh';
        btnEl.disabled = false;
        btnEl.onclick = (e) => { e.preventDefault(); wtRefreshProviders(); };
      }
    } else if (data.command) {
      if (msgEl) msgEl.textContent = `Couldn't start that here (${data.error || 'no runnable install'}) — run this yourself: ${data.command}`;
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

// Re-fetch /api/agent/providers (bypassing the boot-time cache, same pattern
// as settingsProviderRefresh in provider-auth.js) and re-render the CURRENT
// walkthrough step so a just-installed CLI's state flips from "not installed"
// without the user having to close and reopen the tour.
async function wtRefreshProviders() {
  _agentProviders = null;
  try { await _ensureAgentProviders(); } catch (e) { /* leave stale on failure */ }
  if (wtActive) wtShow(wtStep);
}

// Build virtual demo elements for the walkthrough
function wtDemoTileHTML() {
  return `<div class="card status-active" style="width:260px;aspect-ratio:1;pointer-events:none">
    <div class="card-header">
      <div class="card-title-row">
        <span class="project-name">Clayrune</span>
        <span class="domain-tag" style="background:var(--surface3);color:var(--text-dim)">General</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px">
        <span class="status-pill status-active">active</span>
      </div>
    </div>
    <div class="tile-body">
      <div class="tile-task">Learn how to use Clayrune</div>
    </div>
    <div class="tile-footer">
      <span class="backlog-badge">3 open</span>
      <span class="time-ago">just now</span>
    </div>
  </div>`;
}

function wtDemoModalHTML(activeTab) {
  let bodyContent = '';
  if (activeTab === 'backlog') {
    bodyContent = `
      <div style="padding:16px">
        <div style="display:flex;gap:8px;margin-bottom:14px">
          <input type="text" placeholder="Add backlog item..." style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:6px;font-size:13px;outline:none" disabled>
          <button style="background:var(--accent);color:var(--bg);border:none;padding:8px 16px;border-radius:6px;font-weight:600;font-size:13px" disabled>Add</button>
        </div>
        <div class="backlog-item priority-normal" style="pointer-events:none">
          <button class="backlog-check"></button>
          <span class="backlog-text">Explore the project tabs</span>
          <div class="backlog-meta"><span class="priority-badge priority-normal">normal</span></div>
        </div>
        <div class="backlog-item priority-high" style="pointer-events:none">
          <button class="backlog-check"></button>
          <span class="backlog-text">Try dispatching an AI agent</span>
          <div class="backlog-meta"><span class="priority-badge priority-high">high</span></div>
        </div>
        <div class="backlog-item priority-low" style="pointer-events:none">
          <button class="backlog-check"></button>
          <span class="backlog-text">Connect a GitHub repo for issue sync</span>
          <div class="backlog-meta"><span class="priority-badge priority-low">low</span></div>
        </div>
      </div>`;
  } else if (activeTab === 'agent') {
    bodyContent = `
      <div style="padding:16px;display:flex;flex-direction:column;gap:12px">
        <div style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--text-faint);font-size:13px;padding:40px 0">
          No agent session yet. Type a prompt below to dispatch one.
        </div>
        <div style="display:flex;gap:8px;align-items:flex-end">
          <textarea style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:10px 12px;border-radius:8px;font-size:13px;font-family:'Inter',sans-serif;resize:none;height:60px;outline:none" placeholder="Describe a task for the AI agent..." disabled></textarea>
          <button style="background:var(--accent);color:var(--bg);border:none;padding:10px 20px;border-radius:8px;font-weight:600;font-size:13px;height:42px" disabled>Dispatch</button>
        </div>
      </div>`;
  } else {
    bodyContent = `<div style="padding:40px;text-align:center;color:var(--text-faint);font-size:13px">Tab content appears here</div>`;
  }

  return `<div class="modal-window focused" style="width:700px;height:520px;pointer-events:none;position:relative;border-radius:12px;overflow:hidden">
    <div class="modal-content" style="display:flex;flex-direction:column;height:100%">
      <div class="modal-header">
        <div class="modal-window-controls">
          <button class="modal-menu-btn" style="pointer-events:none">&#x22EE;</button>
          <button class="modal-minimize" style="pointer-events:none">&#x2015;</button>
          <button class="modal-close" style="pointer-events:none">&#10005;</button>
        </div>
        <div class="card-title-row">
          <input class="name-edit" value="Clayrune" disabled style="pointer-events:none">
          <span class="domain-tag" style="background:var(--surface3);color:var(--text-dim)">General</span>
        </div>
      </div>
      <div class="modal-scroll-body" style="flex:1;overflow:hidden">${bodyContent}</div>
    </div>
  </div>`;
}

// The tab strip the demo shows lives INSIDE the three-dot menu — that's where
// the real app puts it now (`.mc-tabs-in-menu`; the old `.modal-tab-bar` is
// display:none on both desktop and mobile). `wtTabsSectionHTML` is spotlighted
// by the "Tabs & Menu" step. Order matches render-core.js's real tab row.
function wtTabsSectionHTML(activeTab) {
  const tabs = ['Agent','Backlog','Social','Agent Log','Documents','Activity','Workflows'];
  return `<div class="mc-tabs-in-menu wt-menu-tabs">` + tabs.map(t => {
    const key = t.toLowerCase().replace(' ', '-');
    return `<button class="modal-menu-item${key === activeTab ? ' active' : ''}" style="pointer-events:none">${t}</button>`;
  }).join('') + `</div>`;
}

function wtDemoMenuHTML(activeTab) {
  return `<div class="modal-window focused" style="width:700px;height:520px;pointer-events:none;position:relative;border-radius:12px;overflow:visible">
    <div class="modal-content" style="display:flex;flex-direction:column;height:100%;overflow:hidden;border-radius:12px;background:var(--surface)">
      <div class="modal-header" style="position:relative">
        <div class="modal-window-controls">
          <button class="modal-menu-btn" style="pointer-events:none;background:var(--accent-dim);color:var(--accent);border-color:var(--accent)">&#x22EE;</button>
          <button class="modal-minimize" style="pointer-events:none">&#x2015;</button>
          <button class="modal-close" style="pointer-events:none">&#10005;</button>
        </div>
          <div class="modal-menu-dropdown" style="display:block;pointer-events:none;position:absolute;top:44px;right:8px;min-width:220px;z-index:50">
            ${wtTabsSectionHTML(activeTab || 'agent')}
            <div class="modal-menu-sep"></div>
            <button class="modal-menu-item wt-menu-status" style="pointer-events:none">
              <span class="menu-icon"><svg class="menu-svg"><use href="#ic-status"/></svg></span> Change Status <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <button class="modal-menu-item" style="pointer-events:none">
              <span class="menu-icon"><svg class="menu-svg"><use href="#ic-appearance"/></svg></span> Appearance <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <button class="modal-menu-item" style="pointer-events:none">
              <span class="menu-icon"><svg class="menu-svg"><use href="#ic-edit"/></svg></span> Edit Profile&#8230;
            </button>
            <button class="modal-menu-item wt-menu-model" style="pointer-events:none">
              <span class="menu-icon"><svg class="menu-svg"><use href="#ic-settings"/></svg></span> Agent Settings <span style="margin-left:4px;color:var(--text-faint);font-size:11px">default</span>
            </button>
            <div class="modal-menu-sep"></div>
            <button class="modal-menu-item wt-menu-github" style="pointer-events:none">
              <span class="menu-icon"><svg class="menu-svg"><use href="#ic-github"/></svg></span> GitHub Sync <span style="margin-left:4px;color:var(--text-faint);font-size:11px">not connected</span> <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <div class="modal-menu-sep"></div>
            <button class="modal-menu-item" style="pointer-events:none">
              <span class="menu-icon"><svg class="menu-svg"><use href="#ic-advanced"/></svg></span> Advanced <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <div class="modal-menu-item" style="pointer-events:none;padding-left:22px;font-size:11px;color:var(--text-faint)">Code Sync &middot; Memory &middot; Rules &middot; Skills &middot; Export &middot; MCP servers &middot; Personas &middot; Media &middot; Hiveminds</div>
            <div class="modal-menu-sep"></div>
            <button class="modal-menu-item danger" style="pointer-events:none">
              <span class="menu-icon"><svg class="menu-svg"><use href="#ic-trash"/></svg></span> Delete Project
            </button>
          </div>
        <div class="card-title-row">
          <input class="name-edit" value="Clayrune" disabled style="pointer-events:none">
          <span class="domain-tag" style="background:var(--surface3);color:var(--text-dim)">General</span>
        </div>
      </div>
      <div class="modal-scroll-body" style="flex:1;overflow:hidden">
        <div style="padding:40px;text-align:center;color:var(--text-faint);font-size:13px">Tab content appears here</div>
      </div>
    </div>
  </div>`;
}

// A spotlight target is only usable if it actually occupies space on screen.
function wtIsMeasurable(el) {
  if (!el) return false;
  const r = el.getBoundingClientRect();
  return r.width > 1 && r.height > 1;
}

// Steps that spotlight something inside the collapsed-by-default "Advanced"
// sidebar group have to open it first, or they point at a display:none element.
function wtExpandAdvancedSidebar() {
  const grp = document.getElementById('sidebar-advanced-group');
  const tog = document.getElementById('sidebar-adv-toggle');
  if (!grp || grp.classList.contains('expanded')) return;
  grp.dataset.wtExpanded = '1';
  grp.classList.add('expanded');
  if (tog) tog.classList.add('expanded');
}
function wtRestoreAdvancedSidebar() {
  const grp = document.getElementById('sidebar-advanced-group');
  const tog = document.getElementById('sidebar-adv-toggle');
  if (!grp || grp.dataset.wtExpanded !== '1') return;
  delete grp.dataset.wtExpanded;
  grp.classList.remove('expanded');
  if (tog) tog.classList.remove('expanded');
}

function startWalkthrough() {
  wtActive = true;
  wtStep = 0;
  wtDontShow = false;
  showDesktop();
  wtShow(0);
}

async function wtShow(idx) {
  // Call onLeave for the previous step
  const prevStep = WT_STEPS[wtStep];
  if (prevStep && prevStep.onLeave) prevStep.onLeave();

  wtStep = idx;
  const step = WT_STEPS[idx];
  if (!step) { wtEnd(); return; }

  // Skip steps that don't apply to current viewport
  if (step.skip && step.skip()) {
    if (idx < WT_STEPS.length - 1) { wtShow(idx + 1); return; }
    else { wtEnd(); return; }
  }

  if (step.onEnter) await step.onEnter();

  // Remove old overlay
  const old = document.getElementById('wt-overlay');
  if (old) old.remove();

  const overlay = document.createElement('div');
  overlay.id = 'wt-overlay';
  overlay.className = 'wt-overlay';

  const backdrop = document.createElement('div');
  backdrop.className = 'wt-backdrop';
  backdrop.onclick = () => {}; // block clicks
  overlay.appendChild(backdrop);
  // Attach BEFORE measuring: an injected demo lives inside this overlay, and a
  // detached node measures 0x0, so wtIsMeasurable() dropped every demo target
  // (Tiles/Tabs/Agent steps drew no highlight and an unpositioned card).
  document.body.appendChild(overlay);

  // Remove previous elevation
  document.querySelectorAll('.wt-elevated').forEach(el => el.classList.remove('wt-elevated'));

  const targetSel = typeof step.target === 'function' ? step.target() : step.target;
  let targetEl = targetSel ? document.querySelector(targetSel) : null;

  // A target that exists in the DOM but has no box (display:none — e.g. the
  // collapsed "Advanced" sidebar group, or a row a media query hides) measures
  // 0x0. Highlighting it drew a small glowing rectangle in the top-left corner
  // with the card clamped beside it — the "step that points at nothing" bug.
  // Treat unmeasurable as absent so the step falls back to its demo, or to a
  // plain centered card.
  if (targetEl && !wtIsMeasurable(targetEl)) targetEl = null;

  // Inject virtual demo element if step uses one — but only when the real
  // target (if any) isn't in the DOM; a found real target always wins.
  if (step.demo && !targetEl) {
    const demo = document.createElement('div');
    demo.className = 'wt-demo';
    demo.style.cssText = 'position:fixed;z-index:2001;pointer-events:none;';

    if (step.demo === 'tile') {
      demo.innerHTML = wtDemoTileHTML();
      const isMobile = window.innerWidth <= 960;
      demo.style.left = isMobile ? `${Math.max(16, (window.innerWidth - 260) / 2)}px` : '80px';
      demo.style.top = isMobile ? '100px' : '180px';
      overlay.appendChild(demo);
      targetEl = demo.firstElementChild;
    } else if (step.demo === 'modal-menu') {
      demo.innerHTML = wtDemoMenuHTML();
      const demoW = Math.min(700, window.innerWidth - 32);
      demo.style.left = `${Math.max(16, (window.innerWidth - demoW) / 2)}px`;
      demo.style.top = `${Math.max(16, (window.innerHeight - 520) / 2)}px`;
      if (demoW < 700) demo.style.transform = `scale(${demoW / 700})`;
      demo.style.transformOrigin = 'top left';
      overlay.appendChild(demo);
      if (step.demoTarget) {
        const subEl = demo.querySelector(step.demoTarget);
        if (subEl) {
          subEl.style.outline = '2px solid var(--accent)';
          subEl.style.outlineOffset = '2px';
          subEl.style.borderRadius = '4px';
          subEl.style.background = 'var(--accent-dim)';
          targetEl = subEl;
        } else {
          targetEl = demo.firstElementChild;
        }
      } else {
        targetEl = demo.firstElementChild;
      }
    } else if (step.demo.startsWith('modal')) {
      const tab = step.demo === 'modal-backlog' ? 'backlog'
                : step.demo === 'modal-agent' ? 'agent'
                : 'agent';
      demo.innerHTML = wtDemoModalHTML(tab);
      const demoW = Math.min(700, window.innerWidth - 32);
      demo.style.left = `${Math.max(16, (window.innerWidth - demoW) / 2)}px`;
      demo.style.top = `${Math.max(16, (window.innerHeight - 520) / 2)}px`;
      if (demoW < 700) { demo.style.transform = `scale(${demoW / 700})`; demo.style.transformOrigin = 'top left'; }
      overlay.appendChild(demo);
      if (step.demoTarget) {
        const subEl = demo.querySelector(step.demoTarget);
        if (subEl) {
          subEl.style.outline = '2px solid var(--accent)';
          subEl.style.outlineOffset = '3px';
          subEl.style.borderRadius = '6px';
          targetEl = subEl;
        } else {
          targetEl = demo.firstElementChild;
        }
      } else {
        targetEl = demo.firstElementChild;
      }
    }
  }

  // Same guard for a demo sub-target: `demoTarget` is resolved against markup we
  // control, but the app's CSS still applies to it, so a selector that used to
  // resolve can silently go zero-height when a media query hides that element.
  if (targetEl && !wtIsMeasurable(targetEl)) {
    const _demoRoot = targetEl.closest('.wt-demo');
    targetEl = _demoRoot ? _demoRoot.firstElementChild : null;
    if (targetEl && !wtIsMeasurable(targetEl)) targetEl = null;
  }

  // Elevate the target (or its modal-window ancestor) above the backdrop.
  // Applies to real DOM targets only — injected demos live inside the
  // overlay (.wt-demo) and are already above the backdrop.
  if (targetEl && !targetEl.closest('.wt-demo')) {
    const modal = targetEl.closest('.modal-window');
    (modal || targetEl).classList.add('wt-elevated');
  }

  // Clip-path cutout around target
  if (targetEl) {
    const r = targetEl.getBoundingClientRect();
    const pad = 10;
    const x1 = r.left - pad, y1 = r.top - pad;
    const x2 = r.right + pad, y2 = r.bottom + pad;
    // polygon: full screen with rectangular hole
    backdrop.style.clipPath = `polygon(
      0% 0%, 100% 0%, 100% 100%, 0% 100%, 0% 0%,
      ${x1}px ${y1}px, ${x1}px ${y2}px, ${x2}px ${y2}px, ${x2}px ${y1}px, ${x1}px ${y1}px
    )`;

    const hl = document.createElement('div');
    hl.className = 'wt-highlight';
    hl.style.left = (r.left - pad) + 'px';
    hl.style.top = (r.top - pad) + 'px';
    hl.style.width = (r.width + pad * 2) + 'px';
    hl.style.height = (r.height + pad * 2) + 'px';
    overlay.appendChild(hl);
  }

  // Card
  const card = document.createElement('div');
  card.className = 'wt-card' + (step.pos === 'center' ? ' centered' : '');

  const isFirst = idx === 0;
  const isLast = idx === WT_STEPS.length - 1;

  let btns = '';
  if (isLast) {
    btns = `<button class="wt-btn wt-btn-primary" onclick="wtEnd()">Get Started</button>`;
  } else {
    if (!isFirst) btns += `<button class="wt-btn" onclick="wtBack()">Back</button>`;
    btns += `<button class="wt-btn wt-btn-skip" onclick="wtSkip()">Skip</button>`;
    btns += `<button class="wt-btn wt-btn-primary" onclick="wtNext()">${isFirst ? 'Start Tour' : 'Next'}</button>`;
  }

  let dismissHTML = '';
  if (!isLast) {
    dismissHTML = `<div class="wt-dismiss">
      <input type="checkbox" id="wt-dontshow" ${wtDontShow ? 'checked' : ''} onchange="wtDontShow=this.checked">
      <label for="wt-dontshow">Don't show this again</label>
    </div>`;
  }

  // Body strings are author-controlled hardcoded text (WT_STEPS const), so they
  // can include <strong>/<em> markup. Don't esc() — that would render the tags
  // as literal text. Functions return pre-built HTML and pass through too.
  const bodyHTML = typeof step.body === 'function' ? step.body() : step.body;

  // Skip-aware progress count: skipped steps shouldn't count toward total or
  // create gaps in the numbering. Otherwise desktop users see 13 → 15 (the
  // mobile-only bottom-tabs step at idx 13 gets eaten silently).
  const visibleSteps = WT_STEPS.filter(s => !(s.skip && s.skip()));
  const visibleIdx = visibleSteps.findIndex(s => s.id === step.id);
  const visiblePos = (visibleIdx >= 0 ? visibleIdx : 0) + 1;
  const visibleTotal = visibleSteps.length;

  card.innerHTML = `
    <div class="wt-title">${esc(step.title)}</div>
    <div class="wt-body">${bodyHTML}</div>
    <div class="wt-actions">
      <span class="wt-progress">${visiblePos} / ${visibleTotal}</span>
      ${btns}
    </div>
    ${dismissHTML}
  `;
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  // Position card near target
  if (targetEl && step.pos !== 'center') {
    wtPositionCard(targetEl, card, step.pos);
  }
}

function wtPositionCard(targetEl, cardEl, pos) {
  const tr = targetEl.getBoundingClientRect();
  const cr = cardEl.getBoundingClientRect();
  const gap = 20;
  let left, top;

  switch (pos) {
    case 'bottom':
      left = tr.left + (tr.width - cr.width) / 2;
      top = tr.bottom + gap;
      break;
    case 'top':
      left = tr.left + (tr.width - cr.width) / 2;
      top = tr.top - cr.height - gap;
      break;
    case 'left':
      left = tr.left - cr.width - gap;
      top = tr.top + (tr.height - cr.height) / 2;
      break;
    case 'right':
      left = tr.right + gap;
      top = tr.top + (tr.height - cr.height) / 2;
      break;
    default:
      return;
  }

  // Clamp to viewport
  left = Math.max(16, Math.min(left, window.innerWidth - cr.width - 16));
  top = Math.max(16, Math.min(top, window.innerHeight - cr.height - 16));
  cardEl.style.left = left + 'px';
  cardEl.style.top = top + 'px';
}

function wtNext() { if (wtStep < WT_STEPS.length - 1) wtShow(wtStep + 1); else wtEnd(); }
function wtBack() {
  let prev = wtStep - 1;
  while (prev > 0 && WT_STEPS[prev].skip && WT_STEPS[prev].skip()) prev--;
  if (prev >= 0) wtShow(prev);
}
function wtSkip() {
  if (wtDontShow) localStorage.setItem('walkthrough_done', '1');
  wtEnd();
}
function wtEnd() {
  const curStep = WT_STEPS[wtStep];
  if (curStep && curStep.onLeave) curStep.onLeave();
  wtActive = false;
  localStorage.setItem('walkthrough_done', '1');
  document.querySelectorAll('.wt-elevated').forEach(el => el.classList.remove('wt-elevated'));
  const el = document.getElementById('wt-overlay');
  if (el) el.remove();
}

// Reposition on resize
window.addEventListener('resize', () => { if (wtActive) wtShow(wtStep); });
// Escape to skip
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && wtActive) {
    e.stopPropagation();
    wtSkip();
  }
});

// ── interop: page-called surface ─────────────────────────────────────────────
// Everything below is invoked from OUTSIDE this module — static/generated
// inline event attributes and the inline boot script — all of which resolve
// against the global object, never module scope.
window.startWalkthrough = startWalkthrough; // interop: header ? btn (onclick), Settings "Take Tour" (generated onclick), palette action, first-run auto-start (setTimeout in fetchProjects().then)
window.wtNext = wtNext; // interop: wt-card generated onclick (Start Tour / Next)
window.wtBack = wtBack; // interop: wt-card generated onclick (Back)
window.wtSkip = wtSkip; // interop: wt-card generated onclick (Skip)
window.wtEnd = wtEnd;   // interop: wt-card generated onclick (Get Started)
window.wtSetDefaultProvider = wtSetDefaultProvider; // interop: provider-choice step's generated onchange
window.wtInstallProvider = wtInstallProvider; // interop: provider-choice step's generated Install button onclick
window.wtRefreshProviders = wtRefreshProviders; // interop: wtInstallProvider's generated Refresh button onclick
// interop: the "Don't show this again" checkbox writes `wtDontShow=this.checked`
// from a generated onchange attribute. Inline handlers resolve against the
// global object and can't see module-scoped `let` bindings — without this
// bridge the assignment would create a NEW, diverging window.wtDontShow
// property and the checkbox would silently stop registering. The accessor
// routes window-property reads/writes to the module binding (one source of
// truth; the moved code itself stays byte-verbatim).
Object.defineProperty(window, 'wtDontShow', {
  get() { return wtDontShow; },
  set(v) { wtDontShow = v; },
});
