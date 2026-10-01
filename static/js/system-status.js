// ── System status (CC /status equivalent) ───────────────────────────────────
// Surfaces the same info Claude Code's `/status` slash command shows: model,
// CLI version, auth source, rate-limit window state, connected MCP servers.
// Backend: see `_capture_system_init` + `/api/system/status[/refresh]` in
// server.py. Cache is populated for free by every agent session's init
// message; the Refresh button triggers an active one-shot claude spawn.
//
// Popover layout mirrors the CLI `/status` tabs:
//   Status  — live health: rate limit, model, CLI version, auth
//   Config  — permission mode, output style, fast mode, cwd, memory, counts
//   MCP     — per-server connection list
//   Usage   — authoritative subscription usage % (5h / weekly / per-model)
//             from the OAuth endpoint (/api/system/usage → usage_limits, the
//             same numbers the CLI `/usage` shows), plus local token
//             aggregates (today / week / top models) from
//             ~/.claude/stats-cache.json. Falls back to the header-derived
//             rate-limit window when the OAuth fetch is unavailable; still
//             links out to claude.ai as a cross-check.
let systemStatusCache = null;
let systemUsageCache = null;
let _sysStatusPopoverOpen = false;
let _sysStatusRefreshing = false;
let _sysStatusActiveTab = 'status';  // status | config | mcp | usage
let _sysUsageFetching = false;
let _sysUsageRefreshing = false;  // MC-989: the Usage tab's own "Refresh numbers" busy-state

// MC-998 — Breakdown section (docs/USAGE_BREAKDOWN_SPEC.md "Dashboard layout
// and states"). Own cache + own query-param state, separate from the
// authoritative-% bars above it; selecting a control re-fetches only this
// section, never the bars.
let systemUsageBreakdownCache = null;
let systemUsageWindowsCache = null;
let _ubBreakdownFetching = false;
let _ubWindowsFetching = false;
// Review finding #7 (docs/_journal/4668eafc-mc998-fenn-review.md): an
// in-flight fetch used to suppress the next one outright, so switching
// provider before the first request resolved left the control saying
// Codex while Claude's data stayed on screen — and a failed fetch left the
// stale cache in place with no error state. `*ReqSeq` lets the LATEST
// request always win (an older one that resolves late is discarded, never
// applied) instead of dropping the newer selection's request; `*CacheKey`
// tags which exact query produced the cache so the renderer can tell a
// stale cache from a current one.
let _ubBreakdownReqSeq = 0;
let _ubBreakdownCacheKey = '';
let _ubBreakdownError = false;
let _ubWindowsReqSeq = 0;
let _ubWindowsCacheKey = '';
let _ubWindowsError = false;
let _ubProvider = 'claude';
let _ubWindowKind = '5h';
let _ubWindowScope = 'all';
let _ubDimension = 'project';
let _ubSort = 'input';
let _ubRangeKey = '';  // '' = current window (server default); else 'range_start|range_end'

// MC-998 follow-up (Ron, 2026-09-28): the bar no longer navigates anywhere —
// it shows a small hover/click summary popup anchored to itself. Own cache,
// own fetch, deliberately independent of the full-report controls above (the
// popup always shows the bar's OWN window — provider + weekly — never
// whatever provider/window_kind the report page happens to have selected).
let _ubPopupProvider = null;      // which provider's popup is open, or null
const _ubPopupData = {};          // provider -> {ts, character, model, error}
let _ubPopupReqSeq = 0;
const _UB_POPUP_TTL_MS = 60000;
const _UB_POPUP_MIN_SAMPLES = 5;  // mirrors _MIN_ELIGIBLE_INTERVALS, mc/usage_breakdown_aggregate.py

async function fetchSystemStatus() {
  try {
    const res = await fetchFailFast(API_BASE + '/api/system/status');
    if (!res.ok) return;
    systemStatusCache = await res.json();
    renderSysStatusPill();
    _rerenderSysStatusSurfaces();
  } catch { /* network blip — keep last cache */ }
}

function _ssRateLimitHealth(rl) {
  // Map rate_limit_info into {color, label, message}. Color drives the pill
  // dot. Label is the compact pill text. Message is the popover detail line.
  if (!rl || !rl.status) return { color: 'idle', label: '—', message: 'No data yet' };
  const isUsingOverage = !!rl.isUsingOverage;
  const status = (rl.status || '').toLowerCase();
  const overage = (rl.overageStatus || '').toLowerCase();
  if (status === 'blocked' || status === 'denied') {
    return { color: 'bad', label: 'BLK', message: 'Rate limited' };
  }
  if (isUsingOverage || overage === 'using_overage') {
    return { color: 'warn', label: 'OVR', message: 'Using overage allowance' };
  }
  // Within 20% of the window reset → amber as a soft warning. Cheap proxy
  // for "approaching limit" since we don't have a usage-percentage signal.
  const resets = Number(rl.resetsAt || 0);
  if (resets > 0) {
    const remainingMs = (resets * 1000) - Date.now();
    const remainingMin = Math.max(0, Math.floor(remainingMs / 60000));
    // 5-hour window = 300 min. Inside 60 min of reset = amber.
    const win = (rl.rateLimitType || '').includes('five') ? 300 : 60;
    const compact = remainingMin >= 60
      ? `${Math.floor(remainingMin / 60)}h${remainingMin % 60 ? (remainingMin % 60) + 'm' : ''}`
      : remainingMin + 'm';
    if (remainingMin < win * 0.2) {
      return { color: 'warn', label: compact, message: 'Approaching reset' };
    }
    return { color: 'ok', label: compact, message: 'OK · ' + compact + ' to reset' };
  }
  return { color: 'ok', label: 'OK', message: 'OK' };
}

function _ssRelTime(iso) {
  if (!iso) return 'never';
  try {
    const dt = new Date(iso);
    const sec = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
    return Math.floor(sec / 86400) + 'd ago';
  } catch { return ''; }
}

function renderSysStatusPill() {
  const pill = document.getElementById('sys-status-pill');
  const label = document.getElementById('sys-status-pill-label');
  if (!pill || !label) return;
  pill.classList.remove('ok', 'warn', 'bad', 'stale');
  const s = systemStatusCache;
  if (!s || !s.captured_at) {
    label.textContent = '—';
    return;
  }
  const h = _ssRateLimitHealth(s.rate_limit_info);
  if (h.color === 'ok') pill.classList.add('ok');
  else if (h.color === 'warn') pill.classList.add('warn');
  else if (h.color === 'bad') pill.classList.add('bad');
  label.textContent = h.label;
  // Cache older than 30 min → dim the pill so users know it's not live.
  if ((s.cache_age_seconds || 0) > 1800) pill.classList.add('stale');
}

function _ssFormatTokens(n) {
  n = Number(n) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

function _ssShortenModel(m) {
  if (!m) return '—';
  return String(m).replace(/^claude-/, '').replace(/-\d{8}$/, '');
}

// Per-provider install/auth health — makes every registered agent provider
// visible in the Status popup, not just claude. Returns '' for claude-only
// deployments (nothing extra to show).
function _renderProviderHealthRows() {
  const provs = _agentProviders || [];
  if (provs.length <= 1) return '';
  const rows = provs.map(pr => {
    const installed = !!pr.installed;
    const authOk = pr.auth_status === 'ok';
    const color = !installed ? 'var(--text-faint)'
                : authOk     ? 'var(--green)'
                :              'var(--amber)';
    const state = !installed ? 'not installed'
                : authOk     ? 'ready'
                : (pr.auth_status === 'not_logged_in' ? 'not signed in' : 'installed');
    const ver = pr.version ? ' · v' + esc(pr.version) : '';
    return `<div class="ssp-row"><span class="ssp-k">${esc(pr.display_name)}</span><span class="ssp-v" style="color:${color}">${state}${ver}</span></div>`;
  }).join('');
  return `<div class="ssp-section-head" style="margin-top:10px">Agent providers</div>${rows}`;
}

function _renderStatusTab(s, rl, h) {
  const colorClass = h.color === 'ok' ? 'green' : (h.color === 'warn' ? 'amber' : (h.color === 'bad' ? 'red' : ''));
  const resetWhen = rl.resetsAt ? new Date(rl.resetsAt * 1000).toLocaleTimeString() : '';
  const empty = !s.captured_at;
  const authDisplay = (s.apiKeySource && s.apiKeySource !== 'none')
    ? s.apiKeySource
    : 'OAuth / keychain';
  // Rate-limit / overage / model telemetry below is Claude-Code-specific —
  // the cache is fed only by claude stream readers. Other providers don't
  // expose equivalent account telemetry, so the section is headed honestly
  // and every provider's install/auth health is listed separately below.
  const multiProvider = (_agentProviders || []).length > 1;
  return `
    ${multiProvider ? '<div class="ssp-section-head">Claude Code</div>' : ''}
    <div class="ssp-row"><span class="ssp-k">Rate limit</span><span class="ssp-v ${colorClass}">${empty ? '—' : (h.message + (resetWhen ? ' · resets ' + resetWhen : ''))}</span></div>
    <div class="ssp-row"><span class="ssp-k">Window</span><span class="ssp-v">${esc((rl.rateLimitType || '').replace('_', '-') || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Overage</span><span class="ssp-v">${rl.isUsingOverage ? 'using overage' : esc(rl.overageStatus || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Model</span><span class="ssp-v">${esc(s.model || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Agent version</span><span class="ssp-v">${esc(s.claude_code_version || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Auth</span><span class="ssp-v">${esc(authDisplay)}</span></div>
    ${_renderProviderHealthRows()}
  `;
}

function _renderConfigTab(s) {
  const memPaths = Array.isArray(s.memory_paths) ? s.memory_paths : [];
  const memDisplay = memPaths.length === 0
    ? '—'
    : (memPaths.length === 1 ? memPaths[0] : memPaths.length + ' paths');
  const fastDisplay = (s.fast_mode_state && s.fast_mode_state !== 'off') ? s.fast_mode_state : 'off';
  const sspCaps = _getProviderCaps(s.provider || 'claude');
  return `
    <div class="ssp-row"><span class="ssp-k">Permission mode</span><span class="ssp-v">${esc(s.permissionMode || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Output style</span><span class="ssp-v">${esc(s.output_style || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Fast mode</span><span class="ssp-v">${esc(fastDisplay)}</span></div>
    <div class="ssp-row"><span class="ssp-k">Analytics</span><span class="ssp-v">${s.analytics_disabled ? 'disabled' : 'enabled'}</span></div>
    <div class="ssp-row"><span class="ssp-k">Working dir</span><span class="ssp-v" title="${esc(s.cwd || '')}">${esc(s.cwd || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Memory</span><span class="ssp-v" title="${esc(memPaths.join('\n'))}">${esc(memDisplay)}</span></div>
    <div class="ssp-row"><span class="ssp-k">Tools</span><span class="ssp-v">${s.tools_count || 0}</span></div>
    ${sspCaps.supports_skills ? `<div class="ssp-row"><span class="ssp-k">Skills</span><span class="ssp-v">${s.skills_count || 0}</span></div>` : ''}
    <div class="ssp-row"><span class="ssp-k">Agents</span><span class="ssp-v">${s.agents_count || 0}</span></div>
    <div class="ssp-row"><span class="ssp-k">Plugins</span><span class="ssp-v">${s.plugins_count || 0}</span></div>
    <div class="ssp-row"><span class="ssp-k">Slash commands</span><span class="ssp-v">${s.slash_commands_count || 0}</span></div>
  `;
}

function _renderMcpTab(s) {
  const mcp = Array.isArray(s.mcp_servers) ? s.mcp_servers : [];
  const mcpConnected = mcp.filter(m => (m.status || '').toLowerCase() === 'connected').length;
  const mcpTotal = mcp.length;
  if (mcpTotal === 0) {
    return '<div class="ssp-empty">No MCP servers configured for the active agent session.</div>';
  }
  const summary = `${mcpConnected}/${mcpTotal} connected`;
  return `
    <div class="ssp-section-head">MCP servers · ${esc(summary)}</div>
    <div class="ssp-mcp-list">${mcp.map(m => {
      const st = (m.status || 'unknown').toLowerCase();
      return `<div class="ssp-mcp-row"><span class="ssp-mcp-name">${esc(m.name || '—')}</span><span class="ssp-mcp-status ${esc(st)}">${esc(st)}</span></div>`;
    }).join('')}</div>
  `;
}

function _renderMcActivitySection() {
  // MC-scoped totals. Reads `mcUsageCache` from /api/usage (multi-provider).
  const u = mcUsageCache;
  const total = u ? (u.total || {}) : {};
  const tokens = u ? formatTokens(total.total_tokens || u.total_tokens || 0) : '—';
  const costNum = u ? (total.cost_usd !== undefined ? total.cost_usd : u.cost_usd) : null;
  const costStr = costNum != null ? formatCost(costNum) : '';
  const sessions = u ? (total.sessions || u.total_sessions || 0) : 0;
  const rangeBtns = TOKEN_MODES.map(m => `
    <button class="ssp-range-btn ${tokenCounterMode === m.id ? 'active' : ''}" onclick="setTokenMode('${esc(m.id)}')">${esc(m.label)}</button>
  `).join('');

  // Per-provider breakdown (shown when >1 provider has activity)
  const byProv = u ? (u.by_provider || {}) : {};
  const provEntries = Object.entries(byProv).filter(([, b]) => (b.sessions || 0) > 0);
  const multiProvider = provEntries.length > 1;
  const provBreakdown = multiProvider ? `
    <div class="ssp-section-head" style="margin-top:8px">By provider</div>
    ${provEntries.map(([pname, b]) => {
      const pTok = formatTokens(b.total_tokens || 0);
      const pCost = b.cost_usd != null ? formatCost(b.cost_usd) : '';
      const provBadge = _providerBadge(pname) || `<span class="provider-badge prov-${esc(pname)}">${esc(pname)}</span>`;
      return `<div class="ssp-row">${provBadge}<span class="ssp-v">${pTok} tok${pCost ? ' · ' + esc(pCost) : ''} · ${b.sessions || 0} sess</span></div>`;
    }).join('')}
  ` : '';

  return `
    <div class="ssp-section-head">Clayrune activity</div>
    <div class="ssp-range-row">${rangeBtns}</div>
    <div class="ssp-bignum">${tokens} tok<span class="ssp-bignum-cost">${costStr ? esc(costStr) : ''}</span></div>
    <div class="ssp-bignum-sub">${sessions.toLocaleString()} agent ${sessions === 1 ? 'session' : 'sessions'} in this range</div>
    ${provBreakdown}
  `;
}

function _ssUntil(iso) {
  // Future-relative "resets in 3h12m" for an ISO timestamp.
  if (!iso) return '';
  try {
    const ms = new Date(iso).getTime() - Date.now();
    if (ms <= 0) return 'resetting';
    const m = Math.floor(ms / 60000);
    if (m < 60) return 'resets in ' + m + 'm';
    const h = Math.floor(m / 60);
    if (h < 24) return 'resets in ' + h + 'h' + (m % 60 ? (m % 60) + 'm' : '');
    const d = Math.floor(h / 24);
    return 'resets in ' + d + 'd' + (h % 24 ? (h % 24) + 'h' : '');
  } catch { return ''; }
}

function _ssUsageLimitBar(label, win) {
  // One progress bar for an OAuth usage window {utilization, resets_at}.
  // Returns '' for null windows (e.g. per-model blocks when unused).
  if (!win || win.utilization == null) return '';
  const pct = Math.max(0, Math.min(100, Number(win.utilization) || 0));
  const color = pct >= 90 ? 'var(--red)' : pct >= 70 ? 'var(--amber)' : 'var(--green)';
  const until = _ssUntil(win.resets_at);
  return `
    <div style="margin:5px 0 9px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:3px">
        <span class="ssp-k">${esc(label)}</span>
        <span class="ssp-v">${pct.toFixed(0)}%${until ? ' · ' + esc(until) : ''}</span>
      </div>
      <div style="height:6px;border-radius:3px;background:var(--border);overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${color};border-radius:3px"></div>
      </div>
    </div>`;
}

// MC-998 — Usage Breakdown section (docs/USAGE_BREAKDOWN_SPEC.md "Dashboard
// layout and states"). Renders the payload from GET /api/system/usage/
// breakdown: compact observed totals + coverage, tokens-per-1%-point, the
// estimated/unattributed segmented bar, and a per-dimension ranking table.
// One JS render path for both desktop and phone -- emits BOTH the table
// (`.ub-table-wrap`) and the stacked-card list (`.ub-cards`) from the same
// `rows` array; app.css toggles which is visible at the existing 960px
// breakpoint (mirrors `.usage-bar-strip`'s pattern, not a second code path).
const _UB_EMPTY_STATE_LABEL = {
  no_runs: 'No runs',
  // Review finding #4: distinct from "No runs" -- work exists and overlaps
  // this window, but no session's delta could be isolated to it yet (still
  // mid-turn across the boundary). Telling the two apart is the fix; see
  // the incomplete_coverage_session_count hint line below.
  incomplete_coverage: 'Not yet measurable in this window',
  no_vendor_percentage: 'No vendor percentage',
  sampling_not_begun: 'Sampling has not begun',
  // Model scope (Opus / Sonnet) selected and every session in range has no
  // model evidence -- they are counted in the hint below, not silently dropped.
  model_unknown: 'No sessions with a recorded model in this window',
};
const _UB_BAR_STATUS_LABEL = {
  insufficient_samples: 'Insufficient samples',
  reset_crossed: 'Window reset during range',
  insufficient_calibration: 'Insufficient calibration',
  estimate_exceeds_observed: 'Estimate exceeds observed change',
  unavailable: 'Unavailable',
};

function _ubFmtTok(n) { return n == null ? 'unavailable' : _ssFormatTokens(n); }
function _ubFmtLoc(n) { return n == null ? 'unavailable' : n.toLocaleString(); }
function _ubFmtPP(n) { return n == null ? '—' : n.toFixed(2) + '%'; }
// "1.208B (96.0%)": a category's tokens with its share of the input total.
function _ubFmtShare(n, total) {
  if (n == null) return 'unavailable';
  return total > 0 ? `${_ssFormatTokens(n)} (${(100 * n / total).toFixed(1)}%)` : _ssFormatTokens(n);
}
// Input breakdown rows (Fresh / Cache reads / Cache writes, the writes split
// by TTL). `src` is a totals.tokens object or a ranking row; the TTL keys are
// optional (ranking rows carry none). `sub(label, valueHTML)` renders one row.
function _ubInputBreakdown(src, sub) {
  const total = src.input_processed_total;
  if (total == null || src.input_fresh == null) return '';
  let html = sub('Fresh', _ubFmtShare(src.input_fresh, total))
    + sub('Cache reads', _ubFmtShare(src.input_cache_read, total))
    + sub('Cache writes', _ubFmtShare(src.input_cache_write, total));
  if (src.cache_write_ttl_unknown !== undefined && (src.input_cache_write || 0) > 0) {
    // The TTL figures are a split OF the cache-write line above, not more input.
    if (src.cache_write_5m != null) html += sub('&nbsp;&nbsp;5-minute', _ubFmtShare(src.cache_write_5m, total));
    if (src.cache_write_1h != null) html += sub('&nbsp;&nbsp;1-hour', _ubFmtShare(src.cache_write_1h, total));
    if ((src.cache_write_ttl_unknown || 0) > 0) html += sub('&nbsp;&nbsp;TTL unknown', _ubFmtShare(src.cache_write_ttl_unknown, total));
  }
  return html;
}

// One muted line under a table row's Input figure: "fresh X · reads Y · writes Z".
function _ubRowSplit(r) {
  if (r.input_processed_total == null || r.input_fresh == null) return '';
  return `<div class="ub-rowsplit">fresh ${_ssFormatTokens(r.input_fresh)} · reads ${_ssFormatTokens(r.input_cache_read)} · writes ${_ssFormatTokens(r.input_cache_write)}</div>`;
}

function _renderUsageBreakdownSection() {
  const currentKey = _ubBreakdownQueryKey();
  // Review finding #7: a cache from a PRIOR selection (provider/window/
  // range/etc. changed since it was fetched) must never be shown as if it
  // answered the current one -- `stale` gates that below.
  const stale = systemUsageBreakdownCache != null && _ubBreakdownCacheKey !== currentKey;
  const b = stale ? null : systemUsageBreakdownCache;
  // Finding #7 (P2-7): the windows list is scoped to provider/window_kind/
  // window_scope, exactly like the breakdown cache above -- a cache keyed
  // to a PRIOR selection (or left behind by a failed fetch for the current
  // one) must never be shown as if it were this selection's completed-
  // window dates.
  const windowsStale = _ubWindowsCacheKey !== _ubWindowsQueryKey();
  const windowsList = (!windowsStale && systemUsageWindowsCache && systemUsageWindowsCache.windows) || [];
  const windowsUnavailable = windowsStale && (_ubWindowsError || _ubWindowsFetching);
  // MC-998: a SUCCESSFUL windows load for exactly this selection that came
  // back empty means the vendor has reported no such window in the retained
  // 90 days (e.g. Codex "prolite" sends only a 7-day limit). That is a fact
  // about the account, not missing Clayrune data -- say so instead of the
  // calibration states. A failed fetch (`_ubWindowsError`) or a cache keyed
  // to another selection never qualifies.
  const vendorReportsNoWindow = !windowsStale && !_ubWindowsError &&
    !!systemUsageWindowsCache && Array.isArray(systemUsageWindowsCache.windows) &&
    systemUsageWindowsCache.windows.length === 0;

  const provSel = `
    <select class="ub-select" onchange="_ubBreakdownControlChange('provider', event)">
      <option value="claude" ${_ubProvider === 'claude' ? 'selected' : ''}>Claude</option>
      <option value="codex" ${_ubProvider === 'codex' ? 'selected' : ''}>Codex</option>
    </select>`;
  const windowSel = `
    <select class="ub-select" onchange="_ubBreakdownControlChange('window_kind', event)">
      <option value="5h" ${_ubWindowKind === '5h' ? 'selected' : ''}>5-hour</option>
      <option value="7d" ${_ubWindowKind === '7d' ? 'selected' : ''}>7-day</option>
    </select>`;
  const scopeSel = _ubProvider === 'codex' ? '' : `
    <select class="ub-select" onchange="_ubBreakdownControlChange('window_scope', event)">
      <option value="all" ${_ubWindowScope === 'all' ? 'selected' : ''}>All models</option>
      <option value="opus" ${_ubWindowScope === 'opus' ? 'selected' : ''}>Opus</option>
      <option value="sonnet" ${_ubWindowScope === 'sonnet' ? 'selected' : ''}>Sonnet</option>
    </select>`;
  const rangeOpts = windowsList.map(w => {
    const key = `${w.range_start}|${w.range_end}`;
    const label = (w.completed ? 'Completed window · ' : 'Window · ') +
      (w.resets_at ? new Date(w.resets_at).toLocaleString() : w.range_start);
    return `<option value="${esc(key)}" ${_ubRangeKey === key ? 'selected' : ''}>${esc(label)}</option>`;
  }).join('');
  const rangeSel = `
    <select class="ub-select" onchange="_ubBreakdownControlChange('range', event)">
      <option value="" ${_ubRangeKey === '' ? 'selected' : ''}>Current window</option>
      ${rangeOpts}
    </select>${windowsUnavailable ? '<span class="ssp-hint-line"> completed windows unavailable for this selection</span>' : ''}`;
  const dimSel = `
    <select class="ub-select" onchange="_ubBreakdownControlChange('dimension', event)">
      ${['project', 'character', 'trigger', 'model', 'provider'].map(d =>
        `<option value="${d}" ${_ubDimension === d ? 'selected' : ''}>${d[0].toUpperCase() + d.slice(1)}</option>`).join('')}
    </select>`;
  const sortSel = `
    <select class="ub-select" onchange="_ubBreakdownControlChange('sort', event)">
      <option value="input" ${_ubSort === 'input' ? 'selected' : ''}>Sort: input tokens</option>
      <option value="output" ${_ubSort === 'output' ? 'selected' : ''}>Sort: output tokens</option>
      <option value="added" ${_ubSort === 'added' ? 'selected' : ''}>Sort: LOC added</option>
    </select>`;

  const controlsHTML = `
    <div class="ub-controls">${provSel}${windowSel}${scopeSel}${rangeSel}${dimSel}${sortSel}
      <button class="ssp-refresh" id="ssp-usage-refresh-btn" onclick="_ubRefreshBreakdown()" ${_ubBreakdownFetching ? 'disabled' : ''}>${_ubBreakdownFetching ? 'Refreshing…' : 'Refresh'}</button>
    </div>`;

  if (_ubBreakdownFetching && !b) {
    return `<div class="ssp-section-head">Breakdown</div>${controlsHTML}<div class="ssp-empty">Loading breakdown…</div>`;
  }
  if (!b) {
    const msg = _ubBreakdownError ? 'Breakdown failed to load for this selection — try again.' : 'Breakdown not loaded yet.';
    return `<div class="ssp-section-head">Breakdown</div>${controlsHTML}<div class="ssp-empty">${msg}</div>`;
  }
  const refreshErrorHint = _ubBreakdownError
    ? '<div class="ssp-hint-line">Last refresh failed — showing the previous result.</div>' : '';

  const esLabel = b.empty_state && !vendorReportsNoWindow ? _UB_EMPTY_STATE_LABEL[b.empty_state] || b.empty_state : '';
  const t = b.totals || {};
  const tok = t.tokens || {};
  const loc = t.loc || {};
  const telemetryUnavailable = (t.session_count || 0) > 0 && tok.input_processed_total == null;

  const totalsHTML = `
    <div class="ssp-row"><span class="ssp-k">Sessions in range</span><span class="ssp-v">${(t.session_count || 0).toLocaleString()}</span></div>
    <div class="ssp-row"><span class="ssp-k">Input tokens</span><span class="ssp-v">${_ubFmtTok(tok.input_processed_total)}</span></div>
    ${_ubInputBreakdown(tok, (k, v) => `<div class="ssp-row ub-subrow"><span class="ssp-k">${k}</span><span class="ssp-v">${v}</span></div>`)}
    <div class="ssp-row"><span class="ssp-k">Output tokens</span><span class="ssp-v">${_ubFmtTok(tok.output_tokens)}</span></div>
    <div class="ssp-row"><span class="ssp-k">LOC added / deleted</span><span class="ssp-v">${_ubFmtLoc(loc.added)} / ${_ubFmtLoc(loc.deleted)}</span></div>
    ${telemetryUnavailable ? '<div class="ssp-hint-line">Telemetry unavailable for every session in this range.</div>' : ''}
    ${(t.token_coverage_unavailable_count || 0) > 0 ? `<div class="ssp-hint-line">${t.token_coverage_unavailable_count} session(s) with unavailable token coverage.</div>` : ''}
    ${(t.loc_unavailable_count || 0) > 0 ? `<div class="ssp-hint-line">${t.loc_unavailable_count} session(s) with LOC unavailable (shared/dirty worktree).</div>` : ''}
    ${(t.incomplete_coverage_session_count || 0) > 0 ? `<div class="ssp-hint-line">${t.incomplete_coverage_session_count} session(s) overlap this window; their unmeasured portions are excluded from the totals above (segments that were measured inside it are included).</div>` : ''}
    ${(t.model_unknown_session_count || 0) > 0 ? `<div class="ssp-hint-line">${t.model_unknown_session_count} session(s) have no recorded model and are excluded from this model selection.</div>` : ''}
  `;

  const tpp = b.tokens_per_point || {};
  // `pooled_rate` is the honest name for what older payloads call `median`
  // (it is a pooled ratio-of-sums); fall back for a server that predates it.
  const tppRate = tpp.pooled_rate != null ? tpp.pooled_rate : tpp.median;
  // "Stretch" = a contiguous run of allowance samples, NOT an agent run.
  const stretchTip = 'A stretch is a contiguous run of allowance samples with Clayrune sessions measured throughout. It is not an agent run.';
  const tppSpan = tpp.total_delta_pp != null && tpp.run_count != null
    ? `${+Number(tpp.total_delta_pp).toFixed(1)}pp over <span title="${esc(stretchTip)}">${tpp.run_count} stretches</span>`
    : `n=${tpp.sample_count}`;
  // What one point is made of: per-point volume + share of workload per token
  // type. Volume only -- the vendor's per-type weighting is not identifiable.
  const comp = tpp.composition;
  const compPart = (label, c) => !c ? '' : `${label} ${c.per_point < 1000 ? '<1K' : _ssFormatTokens(c.per_point)} (${(100 * c.share).toFixed(1)}%)`;
  const compHTML = comp
    ? `<div class="ssp-hint-line ub-tpp-composition">Per point: ${[
        compPart('cache reads', comp.cache_read), compPart('cache writes', comp.cache_write),
        compPart('output', comp.output), compPart('fresh', comp.fresh)].filter(Boolean).join(' · ')}</div>
       <div class="ssp-hint-line">This is what the volume is made of, not how the vendor weights each type. The data cannot separate those.</div>`
    : '';
  // The same workload in agent turns: per-point workload / MEAN turn size
  // (consistent with the totals), median-sized turns in the hint. Absent when
  // the server could not size at least 20 turns.
  const ts = tpp.turn_size || {};
  const _turnsFmt = (n) => n < 10 ? n.toFixed(1) : String(Math.round(n));
  const turnsHTML = tpp.turns_per_point != null
    ? `<div class="ssp-hint-line ub-tpp-turns" title="Average turn size ${esc(_ubFmtTok(ts.mean))} tokens over ${ts.turn_count} turns.">~${_turnsFmt(tpp.turns_per_point)} average agent turns per 1% (~${Math.round(tpp.turns_per_point * 100).toLocaleString()} per full window)</div>
       <div class="ssp-hint-line">Turns vary ${Math.round(ts.p90 / ts.p10)}x (p10 ${esc(_ubFmtTok(ts.p10))} to p90 ${esc(_ubFmtTok(ts.p90))} tokens); a short reply costs far less, about ${_turnsFmt(tpp.turns_per_point_median_basis)} typical turns per 1%.</div>`
    : '';
  const tppHTML = tpp.status === 'ok'
    ? `<div class="ssp-row"><span class="ssp-k">Tokens per 1% (pooled rate)</span><span class="ssp-v">${_ubFmtTok(tppRate)} (p10 ${_ubFmtTok(tpp.p10)} · p90 ${_ubFmtTok(tpp.p90)}; ${tppSpan})</span></div>${compHTML}${turnsHTML}${tpp.note ? `<div class="ssp-hint-line">${esc(tpp.note)}</div>` : ''}`
    : `<div class="ssp-row"><span class="ssp-k">Tokens per 1%</span><span class="ssp-v">${esc(_UB_BAR_STATUS_LABEL[tpp.status] || 'Insufficient calibration')}</span></div>`;

  // MC-998 review finding #9: the range/caveat was dropped, the
  // pre-calibration bucket disappeared instead of staying visible (spec:
  // "unattributed bucket always visible"), and a negative `unattributed_pp`
  // (estimate > observed — never clamped server-side, see
  // `compute_segmented_bar`) rendered as if it were a real negative amount
  // of external usage instead of a separate over-estimate error.
  const seg = b.segmented_bar || {};
  // The range is the calibrated rate's own variation (p10/p90 of the pooled
  // workload-per-point), NOT bounds on what was attributed to Clayrune.
  const hasRange = Array.isArray(seg.range_pp) && seg.range_pp.length === 2;
  const rangeSuffix = hasRange
    ? ` (calibration-rate range ${_ubFmtPP(seg.range_pp[0])}–${_ubFmtPP(seg.range_pp[1])})` : '';
  const rangeNote = hasRange
    ? '<div class="ssp-hint-line">The range comes from variation in the calibrated tokens-per-1% rate (p10–p90), not from bounds on attribution.</div>' : '';
  // Absolute 0-100 scale, the SAME scale as the vendor meter: a segment's
  // width is its own pp, NOT its share of `bar_change_pp` (that read as "66%
  // of the allowance used" when the meter said 48%). The range may not start
  // at 0, so the caption states the change explicitly.
  const _segPct = (v) => Math.max(0, Math.min(100, Number(v) || 0));
  const _segs = (clayPp, unattrPp) => {
    const clay = _segPct(clayPp);
    const unattr = Math.min(_segPct(unattrPp), 100 - clay);
    const min = (w) => (w > 0 ? 'min-width:2px;' : '');
    return `<div class="ub-segbar">`
      + (clay > 0 ? `<div class="ub-segbar-estimated" style="${min(clay)}width:${clay}%"></div>` : '')
      + (unattr > 0 ? `<div class="ub-segbar-unattributed" style="${min(unattr)}width:${unattr}%"></div>` : '')
      + `</div>`;
  };
  const changeCaption = seg.bar_change_pp != null
    ? `<div class="ssp-hint-line ub-segbar-caption">Change in this range: ${_ubFmtPP(seg.bar_change_pp)} of 100%</div>` : '';
  let segbarHTML;
  if (seg.status === 'ok') {
    segbarHTML = `
      ${_segs(seg.estimated_pp, seg.unattributed_pp)}
      ${changeCaption}
      <div class="ssp-row"><span class="ssp-k">Estimated Clayrune</span><span class="ssp-v">${_ubFmtPP(seg.estimated_pp)}${rangeSuffix}</span></div>
      ${rangeNote}
      <div class="ssp-row"><span class="ssp-k">Unattributed / uncertain</span><span class="ssp-v">${_ubFmtPP(seg.unattributed_pp)}</span></div>`;
  } else if (seg.status === 'estimate_exceeds_observed') {
    segbarHTML = `
      ${_segs(seg.estimated_pp, 0)}
      ${changeCaption}
      <div class="ssp-row"><span class="ssp-k">Estimated Clayrune</span><span class="ssp-v">${_ubFmtPP(seg.estimated_pp)}${rangeSuffix}</span></div>
      ${rangeNote}
      <div class="ssp-hint-line">Estimate exceeds the observed vendor change by ${_ubFmtPP(-seg.unattributed_pp)} — shown, not clamped; not a negative unattributed amount.</div>`;
  } else if (seg.unattributed_pp != null) {
    // Pre-calibration: the whole observed change is known but not yet split
    // into estimated/unattributed — the bucket stays visible, just uncalibrated.
    segbarHTML = `
      ${_segs(0, seg.unattributed_pp)}
      ${changeCaption}
      <div class="ssp-row"><span class="ssp-k">Unattributed / uncertain</span><span class="ssp-v">${_ubFmtPP(seg.unattributed_pp)}</span></div>
      <div class="ssp-hint-line">${esc(_UB_BAR_STATUS_LABEL[seg.status] || seg.status || 'Unavailable')} — not yet split into an estimate.</div>`;
  } else {
    segbarHTML = `
      <div class="ub-segbar ub-segbar-unknown"></div>
      <div class="ssp-hint-line">${esc(_UB_BAR_STATUS_LABEL[seg.status] || seg.status || 'Unavailable')} — no observed change to attribute.</div>`;
  }

  const noWindowHTML = vendorReportsNoWindow ? (() => {
    const kind = _ubWindowKind === '5h' ? '5-hour' : 'weekly';
    const scope = _ubWindowScope === 'opus' ? ' Opus' : _ubWindowScope === 'sonnet' ? ' Sonnet' : '';
    return `<div class="ssp-empty ub-no-window">${esc(_ubProviderLabel(_ubProvider))} has not reported a ${kind}${scope} limit for this account in the last 90 days.</div>`;
  })() : '';

  const rows = (b.rankings && b.rankings.rows) || [];
  const rankingsHTML = rows.length === 0 ? '<div class="ssp-empty">No sessions to rank in this range.</div>' : `
    <div class="ub-table-wrap"><table class="ub-table">
      <thead><tr><th>${b.dimension[0].toUpperCase() + b.dimension.slice(1)}</th><th>Input</th><th>Output</th><th>LOC+</th><th>Sessions</th></tr></thead>
      <tbody>${rows.map(r => `
        <tr><td>${esc(r.label)}</td><td>${_ubFmtTok(r.input_processed_total)}${_ubRowSplit(r)}</td><td>${_ubFmtTok(r.output_tokens)}</td><td>${_ubFmtLoc(r.added)}</td><td>${r.session_count}</td></tr>
      `).join('')}</tbody>
    </table></div>
    <div class="ub-cards">${rows.map(r => `
      <div class="ub-card">
        <div class="ub-card-label">${esc(r.label)}</div>
        <div class="ub-card-row"><span>Input</span><span>${_ubFmtTok(r.input_processed_total)}</span></div>
        ${_ubInputBreakdown(r, (k, v) => `<div class="ub-card-row ub-card-subrow"><span>${k}</span><span>${v}</span></div>`)}
        <div class="ub-card-row"><span>Output</span><span>${_ubFmtTok(r.output_tokens)}</span></div>
        <div class="ub-card-row"><span>LOC+</span><span>${_ubFmtLoc(r.added)}</span></div>
        <div class="ub-card-row"><span>Sessions</span><span>${r.session_count}</span></div>
      </div>
    `).join('')}</div>
    ${(b.rankings.unknown_count || 0) > 0 ? `<div class="ssp-hint-line">${b.rankings.unknown_count} session(s) in Unknown.</div>` : ''}
    ${(b.rankings.missing_data_count || 0) > 0 ? `<div class="ssp-hint-line">${b.rankings.missing_data_count} session(s) missing token data.</div>` : ''}
    ${(b.rankings.mixed_session_count || 0) > 0 ? `<div class="ssp-hint-line">${b.rankings.mixed_session_count} session(s) ran on more than one model and are split across them.</div>` : ''}
    ${(b.rankings.whole_session_attribution_count || 0) > 0 ? `<div class="ssp-hint-line">${b.rankings.whole_session_attribution_count} session(s) have no per-model counters and are listed whole under their main model.</div>` : ''}
  `;

  return `
    <div class="ssp-section-head">Breakdown${b.coverage_begins ? '' : ' · sampling not begun'}</div>
    ${controlsHTML}
    ${refreshErrorHint}
    ${esLabel ? `<div class="ssp-empty">${esc(esLabel)}.</div>` : ''}
    ${totalsHTML}
    ${vendorReportsNoWindow ? noWindowHTML : tppHTML + segbarHTML}
    ${rankingsHTML}
    ${b.coverage_begins ? `<div class="ssp-hint-line">Retained samples since ${esc(new Date(b.coverage_begins).toLocaleDateString())}.</div>` : ''}
  `;
}

function _renderUsageTab() {
  // Two layers stacked top-to-bottom:
  //   1. MC activity — tokens/cost/sessions launched THROUGH Mission Control,
  //      filterable by today/week/month/all. Source: /api/usage.
  //   2. Claude Code activity — authoritative subscription usage % (OAuth
  //      endpoint) + tokens by model from ~/.claude/stats-cache.json
  //      (covers ALL claude usage on this machine, not just MC-launched).
  const mcSection = _renderMcActivitySection();
  const u = systemUsageCache;
  if (_sysUsageFetching && !u) return mcSection + '<div class="ssp-empty">Loading agent usage stats…</div>';
  if (!u) return mcSection + '<div class="ssp-empty">Agent usage stats not loaded yet.</div>';
  if (u.available === false) {
    return mcSection + `
      <div class="ssp-empty">Usage stats not available (${esc(u.reason || 'provider may not support stats-cache')}).</div>
      <a class="ssp-link" href="https://claude.ai/settings/usage" target="_blank" rel="noopener">Open canonical usage page →</a>
    `;
  }
  const rl = u.rate_limit_info || {};
  const resetWhen = rl.resetsAt ? new Date(rl.resetsAt * 1000).toLocaleString() : '';
  const today = u.today || {};
  const week = u.week || {};
  const month = u.month || {};
  const top = Array.isArray(u.top_models) ? u.top_models : [];

  const renderModelRows = (obj) => {
    const entries = Object.entries(obj).sort((a, b) => (b[1] || 0) - (a[1] || 0));
    if (entries.length === 0) return '<div class="ssp-empty">No activity recorded.</div>';
    return entries.map(([m, t]) => `
      <div class="ssp-token-row">
        <span class="ssp-token-name">${esc(_ssShortenModel(m))}</span>
        <span class="ssp-token-val">${_ssFormatTokens(t)} tok</span>
      </div>
    `).join('');
  };

  // Single period section driven by the top-level filter — no duplicate sections.
  const periodLabel = { all: 'All time', today: 'Today', week: 'This week', month: 'This month' }[tokenCounterMode] || 'All time';
  let periodHTML;
  if (tokenCounterMode === 'all') {
    periodHTML = top.length === 0
      ? '<div class="ssp-empty">No model usage recorded.</div>'
      : top.map(t => `
          <div class="ssp-token-row">
            <span class="ssp-token-name">${esc(_ssShortenModel(t.model))}</span>
            <span class="ssp-token-val">${_ssFormatTokens(t.tokens)} tok</span>
          </div>
        `).join('');
  } else {
    const periodData = tokenCounterMode === 'today' ? today : tokenCounterMode === 'week' ? week : month;
    periodHTML = renderModelRows(periodData);
  }

  const dataDate = u.last_data_date || u.last_computed_date || '—';

  const claudeMultiNote = (_agentProviders || []).length > 1
    ? '<div class="ssp-hint-line">Covers all Claude Code usage on this machine — not just Mission Control. Other providers don\'t publish a stats cache; see Clayrune activity above for cross-provider totals.</div>'
    : '';

  // Authoritative subscription usage %, straight from the OAuth endpoint
  // (/api/system/usage → usage_limits) — the same numbers the CLI `/usage`
  // shows. Falls back to the header-derived window (type/reset/status, no %)
  // when the OAuth fetch was unavailable (missing/expired token, offline).
  const lim = u.usage_limits || null;
  let limitsHTML;
  const _limArr = (lim && Array.isArray(lim.limits)) ? lim.limits : [];
  if (lim && (lim.five_hour || lim.seven_day || lim.seven_day_opus || lim.seven_day_sonnet || _limArr.length)) {
    const extra = lim.extra_usage || {};
    // Per-model weekly caps. The top-level seven_day_opus / seven_day_sonnet
    // keys only ever describe those two models; an account scoped to any OTHER
    // model (e.g. Fable) carries it ONLY as a `weekly_scoped` entry in limits[],
    // which previously rendered nowhere. Skip any model already drawn above so
    // the two sources can't double-render the same window.
    const _drawn = new Set();
    if (lim.seven_day_opus) _drawn.add('opus');
    if (lim.seven_day_sonnet) _drawn.add('sonnet');
    const scopedHTML = _limArr
      .filter(e => e && e.kind === 'weekly_scoped' && e.percent != null)
      .map(e => {
        const name = (e.scope && e.scope.model && e.scope.model.display_name) || '';
        if (!name || _drawn.has(name.toLowerCase())) return '';
        _drawn.add(name.toLowerCase());
        return _ssUsageLimitBar('Weekly · ' + name, { utilization: e.percent, resets_at: e.resets_at });
      }).join('');

    // Extra usage ("spend" is the same figures in structured form). Amounts are
    // MINOR units — monthly_limit 7500 with decimal_places 2 is $75.00, not
    // $7500 (what the old `'$' + monthly_limit` printed). Shown whenever there
    // is spend to report, not only while enabled: an org-disabled or capped-out
    // account still wants to see where it landed, marked (off) so the state is
    // unambiguous.
    const _dp = Number.isFinite(Number(extra.decimal_places)) ? Number(extra.decimal_places) : 2;
    const _money = (minor) => (minor == null ? null : '$' + (Number(minor) / Math.pow(10, _dp)).toFixed(_dp));
    const _used = _money(extra.used_credits);
    const _cap = _money(extra.monthly_limit);
    const _pct = Number(extra.utilization) || 0;
    const _pctCls = _pct >= 90 ? ' red' : _pct >= 70 ? ' amber' : '';
    const _showExtra = !!extra.is_enabled || Number(extra.used_credits) > 0;
    const extraHTML = _showExtra
      ? `<div class="ssp-row"><span class="ssp-k">Extra usage${extra.is_enabled ? '' : ' (off)'}</span><span class="ssp-v${_pctCls}">${_pct.toFixed(0)}%${_used ? ' · ' + _used + (_cap ? ' of ' + _cap : '') : ''}</span></div>`
      : '';

    limitsHTML = `
    <div class="ssp-section-head" style="margin-top:6px">Usage limits</div>
    ${_ssUsageLimitBar('Session · 5-hour', lim.five_hour)}
    ${_ssUsageLimitBar('Weekly · all models', lim.seven_day)}
    ${_ssUsageLimitBar('Weekly · Opus', lim.seven_day_opus)}
    ${_ssUsageLimitBar('Weekly · Sonnet', lim.seven_day_sonnet)}
    ${scopedHTML}
    ${extraHTML}`;
  } else {
    limitsHTML = `
    <div class="ssp-section-head" style="margin-top:6px">Current rate-limit window</div>
    <div class="ssp-row"><span class="ssp-k">Type</span><span class="ssp-v">${esc((rl.rateLimitType || '').replace('_', '-') || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Resets</span><span class="ssp-v">${esc(resetWhen || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Status</span><span class="ssp-v">${esc(rl.status || '—')}</span></div>`;
  }

  // ── Claude reset actions (MC-989 Part B) — neither one is auto-sent. The
  // weekly reset is Anthropic's own web toggle (only some accounts have one);
  // the 5-hour reset is the CLI's interactive `/limit-reset`, typed by hand
  // in a terminal pop-out this only OPENS. ─────────────────────────────────
  const claudeResetHTML = `
    <div class="ssp-action-row">
      <a class="ssp-refresh" href="https://claude.ai/settings/usage" target="_blank" rel="noopener">Reset weekly limit (if granted) →</a>
      <button class="ssp-refresh" onclick="_ubLaunchResetTerminal('claude', event)">Reset 5-hour session (/limit-reset)</button>
    </div>`;

  // ── Codex section — weekly + 5h % (when the record carries one), plan
  // tier, credits balance, and how stale the on-disk reading is (it only
  // updates on Codex's OWN next turn, never fetched live). Never a fake %.
  const cx = u.codex_usage_detail || null;
  let codexHTML;
  if (cx && (cx.weekly || cx.five_hour || cx.plan_type || cx.credits)) {
    const cr = cx.credits || {};
    const creditsLine = cr && (cr.has_credits || Number(cr.balance) > 0)
      ? `<div class="ssp-row"><span class="ssp-k">Credits</span><span class="ssp-v">${cr.unlimited ? 'unlimited' : (esc(String(cr.balance ?? '—')))}</span></div>`
      : '';
    codexHTML = `
    ${_ssUsageLimitBar('Weekly', cx.weekly)}
    ${_ssUsageLimitBar('Session · 5-hour', cx.five_hour)}
    ${cx.plan_type ? `<div class="ssp-row"><span class="ssp-k">Plan</span><span class="ssp-v">${esc(cx.plan_type)}</span></div>` : ''}
    ${creditsLine}
    <div class="ssp-hint-line">Sampled ${esc(_ssRelTime(cx.sampled_at))} — updates only when a Codex turn runs.</div>`;
  } else {
    codexHTML = '<div class="ssp-empty">No Codex usage sampled yet — run a Codex turn to populate this.</div>';
  }

  // ── Gemini section — Google's API-key auth exposes no quota %, so this is
  // ONLY the tokens Clayrune itself recorded through agent_log telemetry,
  // labelled honestly as such. Never draw a fake %/bar for it.
  const _geminiTokensFor = (obj) => Object.entries(obj || {})
    .filter(([m]) => /^gemini/i.test(m))
    .reduce((sum, [, t]) => sum + (Number(t) || 0), 0);
  const geminiTokens = tokenCounterMode === 'all'
    ? top.filter(t => /^gemini/i.test(t.model || '')).reduce((sum, t) => sum + (Number(t.tokens) || 0), 0)
    : _geminiTokensFor(tokenCounterMode === 'today' ? today : tokenCounterMode === 'week' ? week : month);
  const geminiHTML = `
    <div class="ssp-row"><span class="ssp-k">${esc(periodLabel)}</span><span class="ssp-v">${_ssFormatTokens(geminiTokens)} tok</span></div>
    <div class="ssp-hint-line">Tokens used through Clayrune; Google exposes no quota % for API keys.</div>`;

  return mcSection + `
    <div class="ssp-section-head">Claude Code · machine-wide</div>
    ${claudeMultiNote}
    ${limitsHTML}
    ${claudeResetHTML}

    <div class="ssp-section-head">Codex</div>
    ${codexHTML}
    <div class="ssp-action-row">
      <button class="ssp-refresh" onclick="_ubLaunchResetTerminal('codex', event)">Redeem a banked reset →</button>
    </div>

    <div class="ssp-section-head">Gemini</div>
    ${geminiHTML}

    <div class="ssp-action-row">
      <button class="ssp-refresh" onclick="openUsageReport()">Usage report — who and what used it →</button>
    </div>

    <div class="ssp-section-head">${esc(periodLabel)} · tokens by model</div>
    ${periodHTML}

    <div class="ssp-section-head">Totals</div>
    <div class="ssp-row"><span class="ssp-k">Sessions</span><span class="ssp-v">${(u.total_sessions || 0).toLocaleString()}</span></div>
    <div class="ssp-row"><span class="ssp-k">Messages</span><span class="ssp-v">${(u.total_messages || 0).toLocaleString()}</span></div>
    <div class="ssp-row"><span class="ssp-k">Data through</span><span class="ssp-v">${esc(dataDate)}</span></div>

    <div class="ssp-action-row">
      <button class="ssp-refresh" id="ssp-usage-refresh-btn" onclick="_ubRefreshUsage(event)" ${_sysUsageRefreshing ? 'disabled' : ''}>${_sysUsageRefreshing ? 'Refreshing…' : 'Refresh numbers'}</button>
    </div>
    <a class="ssp-link" href="https://claude.ai/settings/usage" target="_blank" rel="noopener">Open canonical usage page →</a>
  `;
}

function renderSysStatusPanel() {
  // Tabbed view mirroring the CLI `/status` layout. Data sources:
  //   systemStatusCache  — populated by `_capture_system_init` on every
  //                        agent run; Refresh shells out to claude.
  //   systemUsageCache   — read from ~/.claude/stats-cache.json via
  //                        /api/system/usage; lazy-fetched on Usage-tab.
  const s = systemStatusCache || {};
  const rl = s.rate_limit_info || {};
  const h = _ssRateLimitHealth(rl);
  const tab = _sysStatusActiveTab;
  const sspProvCaps = _getProviderCaps(s.provider || 'claude');
  let body = '';
  if (tab === 'config') body = _renderConfigTab(s);
  else if (tab === 'mcp') body = _renderMcpTab(s);
  else if (tab === 'usage') body = _renderUsageTab();
  else body = _renderStatusTab(s, rl, h);

  const tabBtn = (id, label, show = true) => show
    ? `<button class="ssp-tab ${tab === id ? 'active' : ''}" onclick="_sysStatusSwitchTab('${id}', event)">${label}</button>`
    : '';

  return `
    <div class="ssp-tabs">
      ${tabBtn('status', 'Status')}
      ${tabBtn('config', 'Config')}
      ${tabBtn('mcp', 'MCP', sspProvCaps.supports_mcp)}
      ${tabBtn('usage', 'Usage')}
    </div>
    <div class="ssp-tab-panel">${body}</div>
    <div class="ssp-foot">
      <span>Captured ${_ssRelTime(s.captured_at)}</span>
      <button class="ssp-refresh" id="ssp-refresh-btn" onclick="refreshSystemStatus(event)" ${_sysStatusRefreshing ? 'disabled' : ''}>${_sysStatusRefreshing ? 'Refreshing…' : 'Refresh'}</button>
    </div>
  `;
}

function _sysStatusSwitchTab(tab, ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  _sysStatusActiveTab = tab;
  if (tab === 'usage' && !systemUsageCache && !_sysUsageFetching) {
    fetchSystemUsage();
  }
  _rerenderSysStatusSurfaces();
}

async function fetchSystemUsage() {
  if (_sysUsageFetching) return;
  _sysUsageFetching = true;
  try {
    const res = await fetchFailFast(API_BASE + '/api/system/usage');
    if (res.ok) systemUsageCache = await res.json();
  } catch { /* leave cache as-is */ }
  _sysUsageFetching = false;
  _rerenderSysStatusSurfaces();
}

// MC-989 Part B — "Refresh numbers": busts the server's oauth/codex usage
// caches then re-fetches, so a sample that already landed since the last
// 60s TTL window shows immediately. Does NOT force Codex to sample fresh —
// that only happens on Codex's own next turn (see backend docstring).
async function _ubRefreshUsage(ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  if (_sysUsageRefreshing) return;
  _sysUsageRefreshing = true;
  _rerenderSysStatusSurfaces();
  try {
    const res = await fetch(API_BASE + '/api/system/usage/refresh', { method: 'POST' });
    if (res.ok) systemUsageCache = await res.json();
  } catch { /* leave cache as-is */ }
  _sysUsageRefreshing = false;
  // MC-998 follow-up: Breakdown no longer lives on this tab (moved to its own
  // Advanced-sidebar report page), so this button only ever refreshes the
  // Usage tab's own OAuth/Codex/bar data now — the report page refetches on
  // its own open/control-change, same as before.
  _rerenderSysStatusSurfaces();
}

// The exact query identity for the CURRENT control selection — shared by the
// fetcher (what it requests + tags its cache with) and the renderer (to spot
// a cache that belongs to a since-changed selection). Single source so the
// two can never drift apart.
function _ubBreakdownQueryKey() {
  const params = new URLSearchParams({
    provider: _ubProvider, window_kind: _ubWindowKind, window_scope: _ubWindowScope,
    dimension: _ubDimension, sort: _ubSort,
  });
  if (_ubRangeKey) {
    const [rs, re] = _ubRangeKey.split('|');
    params.set('range_start', rs);
    params.set('range_end', re);
  }
  return params.toString();
}
function _ubWindowsQueryKey() {
  return new URLSearchParams({ provider: _ubProvider, window_kind: _ubWindowKind, window_scope: _ubWindowScope }).toString();
}

// MC-998 — Usage Breakdown section: fetch/render pair mirroring
// fetchSystemUsage's guard/cache/rerender shape, against the two read-only
// endpoints in mc/blueprints/system_routes.py.
//
// Review finding #7: no longer drops a request just because one is already
// in flight — every call is tagged with a monotonic sequence number, and
// only the response whose sequence still matches the LATEST call is ever
// applied. A request superseded by a newer selection is discarded
// silently; it can never relabel the newer selection's data as its own.
async function fetchUsageBreakdown() {
  const key = _ubBreakdownQueryKey();
  const seq = ++_ubBreakdownReqSeq;
  _ubBreakdownFetching = true;
  _rerenderSysStatusSurfaces();
  let payload = null, succeeded = false;
  try {
    const res = await fetchFailFast(API_BASE + '/api/system/usage/breakdown?' + key);
    if (res.ok) { payload = await res.json(); succeeded = true; }
  } catch { /* succeeded stays false -> error state below */ }
  if (seq !== _ubBreakdownReqSeq) return;  // superseded by a newer selection meanwhile
  _ubBreakdownFetching = false;
  if (succeeded) {
    systemUsageBreakdownCache = payload;
    _ubBreakdownCacheKey = key;
    _ubBreakdownError = false;
  } else {
    _ubBreakdownError = true;
  }
  _rerenderSysStatusSurfaces();
}

async function fetchUsageWindows() {
  const key = _ubWindowsQueryKey();
  const seq = ++_ubWindowsReqSeq;
  _ubWindowsFetching = true;
  let payload = null, succeeded = false;
  try {
    const res = await fetchFailFast(API_BASE + '/api/system/usage/windows?' + key);
    if (res.ok) { payload = await res.json(); succeeded = true; }
  } catch { /* leave cache as-is */ }
  if (seq !== _ubWindowsReqSeq) return;  // superseded by a newer selection meanwhile
  _ubWindowsFetching = false;
  if (succeeded) {
    systemUsageWindowsCache = payload;
    _ubWindowsCacheKey = key;
    _ubWindowsError = false;
  } else {
    // Finding #7 (P2-7, "2026-09-28 re-review"): `_ubWindowsCacheKey` was
    // recorded but never checked, so a failed fetch after switching
    // provider left the PRIOR provider's completed-window dates selectable
    // under the new one -- picking one then sent that stale window's
    // range_start/range_end to the new provider's endpoint. Leave the old
    // payload out of reach of the renderer by flagging the error; the key
    // stays behind the old provider's value so the stale-check below still
    // treats it as stale regardless.
    _ubWindowsError = true;
  }
  _rerenderSysStatusSurfaces();
}

// One change handler per control — each updates the relevant state var then
// re-fetches. Provider/window_kind/window_scope changes also refresh the
// range picker's window list (it's scoped to that triple); dimension/sort
// changes only re-fetch the breakdown (rankings are recomputed server-side).
function _ubBreakdownControlChange(field, ev) {
  const val = ev.target.value;
  if (field === 'provider') { _ubProvider = val; _ubRangeKey = ''; if (val === 'codex') _ubWindowScope = 'all'; }
  else if (field === 'window_kind') { _ubWindowKind = val; _ubRangeKey = ''; }
  else if (field === 'window_scope') { _ubWindowScope = val; _ubRangeKey = ''; }
  else if (field === 'dimension') { _ubDimension = val; }
  else if (field === 'sort') { _ubSort = val; }
  else if (field === 'range') { _ubRangeKey = val; }
  _rerenderSysStatusSurfaces();
  fetchUsageBreakdown();
  if (field === 'provider' || field === 'window_kind' || field === 'window_scope') fetchUsageWindows();
}
window._ubBreakdownControlChange = _ubBreakdownControlChange;

// The report page's own manual-refresh button (unlike the controls above,
// the selection doesn't change — just re-fetch the current one).
function _ubRefreshBreakdown() {
  fetchUsageBreakdown();
  fetchUsageWindows();
}
window._ubRefreshBreakdown = _ubRefreshBreakdown;

// MC-989 Part B — opens a bare-CLI terminal pop-out for a provider's
// interactive reset flow (Claude /limit-reset, Codex "Redeem usage limit
// reset"). NEVER types or submits the command itself — see the hard rule in
// system_routes.py `_USAGE_RESET_INSTRUCTIONS`: a banked/granted reset is
// one-time and belongs to the account holder, so this only opens the
// terminal and surfaces the instruction as a toast for the human to read
// and type by hand. Both CLIs are full-screen raw-mode TUIs (not a
// line-oriented REPL), so the backend launches a REAL pty and this always
// opens the pop-out with `isPty=true` — matching `is_pty` in the response —
// so xterm wires keystrokes straight through (`disableStdin: !isPty` in
// terminal.js) instead of showing the hidden line-input "Send" box a pipe
// session would need.
async function _ubLaunchResetTerminal(provider, ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  try {
    const res = await fetch(API_BASE + '/api/system/usage/reset-terminal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider }),
    });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) {
      showToast(data.error || `Could not open ${provider} terminal`, 5000);
      return;
    }
    if (data.instruction) showToast(data.instruction, 12000);
    openTerminalPopout(window.currentProjectId, data.session_id, data.command || provider, !!data.is_pty);
  } catch {
    showToast(`Could not open ${provider} terminal`, 5000);
  }
}
window._ubRefreshUsage = _ubRefreshUsage;
window._ubLaunchResetTerminal = _ubLaunchResetTerminal;

function renderSysStatusPopover() {
  const pop = document.getElementById('sys-status-popover');
  if (!pop) return;
  pop.innerHTML = renderSysStatusPanel();
}

// Pixel-snap the popover. The pill is sized by fractional-width mono text
// (label is 10.5px), so its right edge lands on a sub-pixel x; a fixed panel
// at a fractional coordinate renders blurry. Snapping must be to the *device*
// pixel grid, not CSS pixels: on Windows at 125%/150% scaling an integer CSS
// px (Math.round) still maps to a fractional device px. snap() rounds in
// device space (× dpr → round → ÷ dpr) so the composited panel rasterizes
// crisp at any display scale.
function _positionSysStatusPopover() {
  const pill = document.getElementById('sys-status-pill');
  const pop = document.getElementById('sys-status-popover');
  if (!pill || !pop) return;
  const r = pill.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const snap = (v) => Math.round(v * dpr) / dpr;
  pop.style.position = 'fixed';
  pop.style.top = snap(r.bottom + 8) + 'px';
  pop.style.right = snap(window.innerWidth - r.right) + 'px';
  pop.style.left = 'auto';
  pop.style.marginTop = '0';
}

function toggleSysStatusPopover(ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  const pop = document.getElementById('sys-status-popover');
  if (!pop) return;
  _sysStatusPopoverOpen = !pop.classList.contains('open');
  if (_sysStatusPopoverOpen) {
    renderSysStatusPopover();
    pop.classList.add('open');
    _positionSysStatusPopover();
    // Refresh cache when popover opens so the user sees current data.
    fetchSystemStatus();
    // Lazy-fetch usage if the Usage tab is the persisted active one.
    if (_sysStatusActiveTab === 'usage' && !systemUsageCache && !_sysUsageFetching) {
      fetchSystemUsage();
    }
    // Review finding #7: reopening the popover never refreshed Breakdown —
    // only the initial tab switch did (guarded on cache being null), so a
    // popover that starts already on Usage from a persisted prior session
    // showed whatever it had last fetched, however old. Unconditional, same
    // as fetchSystemStatus() above: reopening is itself the "give me current
    // data" signal.
    if (_sysStatusActiveTab === 'usage') { fetchUsageBreakdown(); fetchUsageWindows(); }
  } else {
    pop.classList.remove('open');
  }
}

async function refreshSystemStatus(ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  if (_sysStatusRefreshing) return;
  _sysStatusRefreshing = true;
  _rerenderSysStatusSurfaces();
  try {
    const res = await fetch(API_BASE + '/api/system/status/refresh', { method: 'POST' });
    const data = await res.json().catch(() => null);
    if (data && data.captured_at !== undefined) {
      systemStatusCache = data;
    } else if (data && data.status) {
      systemStatusCache = data.status;
    }
  } catch { /* surface as no-update */ }
  _sysStatusRefreshing = false;
  renderSysStatusPill();
  // Drop the usage cache too so the next render re-fetches stats-cache.json.
  systemUsageCache = null;
  if (_sysStatusActiveTab === 'usage') {
    fetchSystemUsage();
    // Review finding #7: this global "Refresh" footer button updated the
    // old usage cache only, on every tab — Breakdown never moved.
    fetchUsageBreakdown();
    fetchUsageWindows();
  }
  _rerenderSysStatusSurfaces();
}

// ── Usage/status as a standalone surface (mobile entry point) ───────────────
// Mobile hides .header entirely (app.css @960: `.header { display: none }`),
// which takes the status pill — and the only route to the Usage tab — with it.
// This opens the SAME panel as a normal global-surface modal, so mobile gets
// subscription usage %, reset windows and MC token totals. Desktop keeps the
// pill; this is simply a second door onto one renderer, not a second renderer.
function openSystemUsage() {
  const modalId = '__usage';
  // Land on Usage (the reason this surface exists); the other tabs stay usable.
  _sysStatusActiveTab = 'usage';
  if (!systemUsageCache && !_sysUsageFetching) fetchSystemUsage();
  fetchSystemStatus();

  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    _rerenderSysStatusSurfaces();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 520);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Usage &amp; system status</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px;overflow-y:auto">
      <div class="sys-status-popover ssp-inline" id="sys-status-surface"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  _rerenderSysStatusSurfaces();
}

// MC-998 follow-up (Dave, Ron 2026-09-28): the full Breakdown — who/what used
// the allowance, by agent/model/project — moved OUT of the Usage modal into
// its own Advanced-sidebar page, so the Usage modal (above) can stay a quick
// bars-and-windows glance. Same renderer (`_renderUsageBreakdownSection`),
// own modal, own mount point (`#usage-report-surface`, handled in
// `_rerenderSysStatusSurfaces`).
function openUsageReport() {
  const modalId = '__usage_report';
  // Review finding #7 (carried over): refresh unconditionally on every open/
  // reopen, not just the first — a stale Breakdown sitting behind a restored
  // minimized modal is exactly the bug that finding fixed.
  fetchUsageBreakdown();
  fetchUsageWindows();

  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    _rerenderSysStatusSurfaces();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 760);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">
        <svg class="menu-svg" style="display:inline-block;vertical-align:-3px"><use href="#ic-usage"/></svg> Usage report
      </span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px;overflow-y:auto">
      <div class="sys-status-popover ssp-inline" id="usage-report-surface"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  _rerenderSysStatusSurfaces();
}

function _rerenderSysStatusSurfaces() {
  // Three possible surfaces now: the desktop header popover, the standalone
  // Usage modal (mobile's only route in), and the standalone Usage report
  // modal (MC-998 follow-up — Breakdown moved out of the Usage popover into
  // its own Advanced-sidebar page). Each is guarded independently.
  if (_sysStatusPopoverOpen) {
    renderSysStatusPopover();
    _positionSysStatusPopover();
  }
  const surface = document.getElementById('sys-status-surface');
  if (surface) surface.innerHTML = renderSysStatusPanel();
  const reportSurface = document.getElementById('usage-report-surface');
  if (reportSurface) reportSurface.innerHTML = _renderUsageBreakdownSection();
  _renderUsageBarStrip();
}

// ── Bottom usage strip (MC-966) ──────────────────────────────────────────
// One compact colored bar per provider's WEEKLY utilization %, pinned to the
// bottom of the desktop main area. Real numbers only: `systemUsageCache
// .provider_weekly_usage` (from /api/system/usage) is built server-side to
// contain ONLY providers with a real weekly % source — Claude's OAuth usage
// endpoint, Codex's own on-disk rate_limits — so there is nothing to filter
// or guess here; a provider simply absent from that dict gets no bar.
function _ubColorClass(pct) {
  if (pct >= 90) return 'red';
  if (pct >= 70) return 'amber';
  return 'green';
}

function _ubProviderLabel(name) {
  const p = (_agentProviders || []).find(x => (x.name || '').toLowerCase() === name);
  return (p && p.display_name) || (name.charAt(0).toUpperCase() + name.slice(1));
}

// MC-998 follow-up (Ron, 2026-09-28): the bar itself no longer navigates
// anywhere — hover (or focus, or a tap) shows a small ~280px summary popup
// anchored to the bar; the full report is a `Full report ->` link inside it.
// Always the provider's WEEKLY window, all-models scope, matching what the
// bar itself displays (docs/_journal/4668eafc-mc998-fenn-review.md finding
// #6 — a click must never show a DIFFERENT provider/window than the one
// under the cursor). Two dimension fetches per open (character for the
// consumer ranking + the calibration/segmented-bar fields, model for the
// top-2-models line) — own cache, own 60s TTL, own sequence guard,
// deliberately independent of the full-report controls (`_ubProvider` /
// `_ubBreakdown*` above), which the user may have left on a different
// selection entirely.
function _ubPopupStale(provider) {
  const e = _ubPopupData[provider];
  return !e || e.fetching || (Date.now() - (e.ts || 0) > _UB_POPUP_TTL_MS);
}

async function _ubFetchBarPopup(provider) {
  const seq = ++_ubPopupReqSeq;
  _ubPopupData[provider] = { ..._ubPopupData[provider], fetching: true };
  const qs = `provider=${encodeURIComponent(provider)}&window_kind=7d&window_scope=all`;
  let charData = null, modelData = null, error = false;
  try {
    const [charRes, modelRes] = await Promise.all([
      fetchFailFast(API_BASE + '/api/system/usage/breakdown?' + qs + '&dimension=character'),
      fetchFailFast(API_BASE + '/api/system/usage/breakdown?' + qs + '&dimension=model'),
    ]);
    if (!charRes.ok || !modelRes.ok) throw new Error('bad status');
    charData = await charRes.json();
    modelData = await modelRes.json();
  } catch {
    error = true;
  }
  if (seq !== _ubPopupReqSeq) return; // superseded by a newer open meanwhile
  _ubPopupData[provider] = { ts: Date.now(), fetching: false, charData, modelData, error };
  if (_ubPopupProvider === provider) _ubRerenderBarPopup();
}

const _UB_POPUP_EMPTY_LABEL = new Set(['no_runs', 'sampling_not_begun', 'no_vendor_percentage']);

function _ubPopupState(b) {
  if (!b) return 'loading';
  if (b.empty_state === 'incomplete_coverage') return 'incomplete';
  if (_UB_POPUP_EMPTY_LABEL.has(b.empty_state)) return 'empty';
  return (b.tokens_per_point && b.tokens_per_point.status === 'ok') ? 'calibrated' : 'uncalibrated';
}

function _ubPopupFooter() {
  return `<div class="ssp-action-row"><a class="ssp-link" href="javascript:void(0)" onclick="_ubClosePopup(); openUsageReport();">Full report &rarr;</a></div>`;
}

function _ubRenderBarPopup(provider) {
  const usage = (systemUsageCache && systemUsageCache.provider_weekly_usage && systemUsageCache.provider_weekly_usage[provider]) || {};
  const exhausted = !!usage.exhausted;
  const pct = exhausted ? 100 : Math.max(0, Math.min(100, Number(usage.utilization) || 0));
  const until = _ssUntil(usage.resets_at);
  const header = `<div class="ssp-row"><span class="ssp-k">${esc(_ubProviderLabel(provider))} &middot; weekly</span><span class="ssp-v">${exhausted ? esc(usage.exhausted_display || 'exhausted') : pct.toFixed(0) + '%'}${until ? ' &middot; ' + esc(until) : ''}</span></div>`;

  const entry = _ubPopupData[provider];
  if (entry && entry.error) {
    return header + '<div class="ssp-hint-line">Couldn’t load usage details — try again.</div>' + _ubPopupFooter();
  }
  const b = entry && entry.charData;
  const state = _ubPopupState(b);
  if (state === 'loading') {
    return header + '<div class="ssp-empty">Loading…</div>' + _ubPopupFooter();
  }
  if (state === 'empty') {
    const since = b.coverage_begins ? new Date(b.coverage_begins).toLocaleString() : 'session start';
    return header + `<div class="ssp-hint-line">Collecting since ${esc(since)}. Numbers appear after the first chat turn completes.</div>` + _ubPopupFooter();
  }
  if (state === 'incomplete') {
    const n = (b.totals && b.totals.incomplete_coverage_session_count) || 0;
    return header + `<div class="ssp-hint-line">${n} session(s) overlap this window but aren’t measurable yet.</div>` + _ubPopupFooter();
  }

  const rows = (b.rankings && b.rankings.rows) || [];
  const tok = (r) => (r.input_processed_total || 0) + (r.output_tokens || 0);
  const named = rows.filter(r => r.label !== 'Unknown');
  const unattributedRow = rows.find(r => r.label === 'Unknown');
  const totalMeasured = rows.reduce((s, r) => s + tok(r), 0);
  const top3 = named.slice(0, 3);
  const otherTok = named.slice(3).reduce((s, r) => s + tok(r), 0);
  const unattributedTok = unattributedRow ? tok(unattributedRow) : 0;

  const tpp = b.tokens_per_point || {};
  const calibrated = state === 'calibrated';
  const valFor = (t) => calibrated
    ? ((tpp.pooled_rate || tpp.median) ? (t / (tpp.pooled_rate || tpp.median)).toFixed(1) : '0.0') + '% of allowance'
    : (totalMeasured > 0 ? ((t / totalMeasured) * 100).toFixed(0) : '0') + '% of measured';
  const row = (label, t) => `<div class="ssp-row"><span class="ssp-k">${esc(label)}</span><span class="ssp-v">${valFor(t)}</span></div>`;

  const consumersHTML = top3.map(r => row(r.label, tok(r))).join('');

  const modelRows = (entry.modelData && entry.modelData.rankings && entry.modelData.rankings.rows) || [];
  const modelsHTML = modelRows.slice(0, 2).map(r => `<div class="ssp-row"><span class="ssp-k">${esc(r.label)}</span><span class="ssp-v">${_ubFmtTok(tok(r))}</span></div>`).join('');

  const sc = (b.totals && b.totals.session_count) || 0;
  const avg = sc > 0 ? Math.round(totalMeasured / sc) : 0;
  const sessionLine = `<div class="ssp-row"><span class="ssp-k">Sessions</span><span class="ssp-v">${sc} &middot; avg ${_ubFmtTok(avg)}</span></div>`;

  const gateNote = calibrated ? '' : `<div class="ssp-hint-line">Allowance % estimates start after ${Math.max(0, _UB_POPUP_MIN_SAMPLES - (tpp.sample_count || 0))} more clean samples.</div>`;

  return header + consumersHTML + modelsHTML + row('Other', otherTok) + row('Unattributed', unattributedTok) + sessionLine + gateNote + _ubPopupFooter();
}

function _ubRerenderBarPopup() {
  const el = document.getElementById('usage-bar-popup');
  if (el && _ubPopupProvider) el.innerHTML = _ubRenderBarPopup(_ubPopupProvider);
}

function _ubPositionBarPopup(anchorEl) {
  const el = document.getElementById('usage-bar-popup');
  if (!el || !anchorEl) return;
  const r = anchorEl.getBoundingClientRect();
  el.style.position = 'fixed';
  el.style.left = Math.max(8, Math.min(window.innerWidth - 296, r.left)) + 'px';
  el.style.bottom = (window.innerHeight - r.top + 8) + 'px';
  el.style.top = 'auto';
}

let _ubPopupAnchorEl = null;
let _ubPopupPinned = false;

function _ubOpenBarPopup(provider, anchorEl) {
  let el = document.getElementById('usage-bar-popup');
  if (!el) {
    el = document.createElement('div');
    el.id = 'usage-bar-popup';
    // Shares `.sys-status-popover .ssp-*` styling (row/hint/link/empty rules)
    // for free — `.ub-bar-popup` below only overrides size/position.
    el.className = 'sys-status-popover ub-bar-popup';
    document.body.appendChild(el);
  }
  _ubPopupProvider = provider;
  _ubPopupAnchorEl = anchorEl || _ubPopupAnchorEl;
  _ubPositionBarPopup(_ubPopupAnchorEl);
  el.classList.add('open');
  el.innerHTML = _ubRenderBarPopup(provider);
  if (_ubPopupStale(provider)) _ubFetchBarPopup(provider);
}

function _ubClosePopup() {
  _ubPopupProvider = null;
  _ubPopupPinned = false;
  const el = document.getElementById('usage-bar-popup');
  if (el) el.classList.remove('open');
}
window._ubClosePopup = _ubClosePopup;

function _ubBarMouseEnter(provider, ev) { _ubOpenBarPopup(provider, ev.currentTarget); }
function _ubBarMouseLeave() { if (!_ubPopupPinned) _ubClosePopup(); }
function _ubBarFocus(provider, ev) { _ubOpenBarPopup(provider, ev.currentTarget); }
function _ubBarBlur() { if (!_ubPopupPinned) _ubClosePopup(); }
function _ubBarClick(provider, ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  if (_ubPopupProvider === provider && _ubPopupPinned) { _ubClosePopup(); return; }
  _ubPopupPinned = true;
  _ubOpenBarPopup(provider, ev.currentTarget);
}
window._ubBarMouseEnter = _ubBarMouseEnter;
window._ubBarMouseLeave = _ubBarMouseLeave;
window._ubBarFocus = _ubBarFocus;
window._ubBarBlur = _ubBarBlur;
window._ubBarClick = _ubBarClick;

window.addEventListener('resize', () => {
  if (_ubPopupProvider && _ubPopupAnchorEl) _ubPositionBarPopup(_ubPopupAnchorEl);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _ubPopupProvider) _ubClosePopup();
});
document.addEventListener('click', (e) => {
  if (!_ubPopupProvider) return;
  const pop = document.getElementById('usage-bar-popup');
  if (pop && pop.contains(e.target)) return;
  if (e.target.closest && e.target.closest('.usage-bar-item')) return; // own onclick handles this
  _ubClosePopup();
});

function _renderUsageBarStrip() {
  const el = document.getElementById('usage-bar-strip');
  if (!el) return;
  const enabled = typeof _globalConfig === 'undefined' || _globalConfig.usage_bar_enabled !== false;
  const usage = systemUsageCache && systemUsageCache.provider_weekly_usage;
  if (!enabled || !usage || Object.keys(usage).length === 0) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML = Object.entries(usage).map(([name, win]) => {
    const exhausted = !!win.exhausted;
    const pct = exhausted ? 100 : Math.max(0, Math.min(100, Number(win.utilization) || 0));
    const cls = exhausted ? 'red' : _ubColorClass(pct);
    const until = _ssUntil(win.resets_at);
    const title = exhausted
      ? `${_ubProviderLabel(name)} — ${win.exhausted_display || 'exhausted'}`
      : `${_ubProviderLabel(name)} — ${pct.toFixed(0)}% of weekly quota${until ? ' · ' + until : ''}`;
    return `
      <div class="usage-bar-item" tabindex="0" aria-label="${esc(title)}"
        onmouseenter="_ubBarMouseEnter('${name}', event)" onmouseleave="_ubBarMouseLeave()"
        onfocus="_ubBarFocus('${name}', event)" onblur="_ubBarBlur()"
        onclick="_ubBarClick('${name}', event)">
        <span class="usage-bar-label">${esc(_ubProviderLabel(name))}</span>
        <div class="usage-bar-track"><div class="usage-bar-fill ${cls}" style="width:${pct}%"></div></div>
        <span class="usage-bar-pct">${exhausted ? 'full' : pct.toFixed(0) + '%'}</span>
      </div>`;
  }).join('');
}
window._renderUsageBarStrip = _renderUsageBarStrip;

// Keep the popover pixel-snapped if the window is resized while it's open.
window.addEventListener('resize', () => {
  if (_sysStatusPopoverOpen) _positionSysStatusPopover();
});

// Close the popover on any outside click. Bound once at load.
document.addEventListener('click', (e) => {
  if (!_sysStatusPopoverOpen) return;
  const pill = document.getElementById('sys-status-pill');
  if (pill && pill.contains(e.target)) return;
  const pop = document.getElementById('sys-status-popover');
  if (pop) pop.classList.remove('open');
  _sysStatusPopoverOpen = false;
});



// ── Boot: relocated from index.html's inline boot tail. As a deferred
//    `type="module"` script this runs just after document parse instead of
//    mid-parse — the one-shot fetch + 60s poll start a few hundred ms later,
//    which is immaterial for a status pill (it renders idle until the async
//    fetch resolves anyway). Byte-verbatim from the original two lines. ──
// System status pill: initial fetch + periodic re-fetch (60s cadence matches
// the schedule banner). Cache is also auto-refreshed server-side by any
// agent activity, so the pill stays current without active polling.
//
// The bottom usage strip (MC-966) hangs off this SAME tick rather than
// starting a second polling loop: fetchSystemUsage() already backs the Usage
// tab (lazy-fetched there), and /api/system/usage caches server-side for 60s
// anyway, so polling it here at the same cadence costs nothing extra.
fetchSystemStatus();
fetchSystemUsage();
// Review finding #7: the 60s tick refreshed the OLD usage cache only, so
// Breakdown could sit stale indefinitely once loaded. Gated on
// `systemUsageBreakdownCache` (not the active tab) -- once Breakdown has
// been loaded at all this session it keeps ticking even if the user is
// currently on another tab, same as the poll never stops for the usage bar.
setInterval(() => {
  fetchSystemStatus();
  fetchSystemUsage();
  if (systemUsageBreakdownCache) { fetchUsageBreakdown(); fetchUsageWindows(); }
}, 60000);

// ── Interop: re-expose for inline / cross-module + region-generated on*=
//    handler callers. All runtime-only (resolve against window — incl. the
//    bare `typeof _rerenderSysStatusSurfaces` guard at the token-usage
//    refresher, which sees the window prop via the global scope chain).
//    All 6 state vars + the remaining helpers are module-private (zero
//    outside refs). ──
window.toggleSysStatusPopover = toggleSysStatusPopover;   // pill static onclick
window.openSystemUsage = openSystemUsage;                 // sidebarNav('usage') + mobile drawer
window.openUsageReport = openUsageReport;                 // sidebarNav('usage-report') + mobile drawer + popup footer link
window._rerenderSysStatusSurfaces = _rerenderSysStatusSurfaces; // typeof-guarded token-usage caller
// region-generated on*= handler targets:
window._sysStatusSwitchTab = _sysStatusSwitchTab;
window.refreshSystemStatus = refreshSystemStatus;
