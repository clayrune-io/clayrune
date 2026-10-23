// ── Feed ────────────────────────────────────────────────────────────────────

// Derive a small icon + kind from a free-text activity_log message so the eye
// can scan event types in the Recent bucket. Pattern matches mirror the
// _log_agent_activity call sites in server.py — keep in sync if those change.
function classifyFeedEvent(msg) {
  const m = String(msg || '').toLowerCase();
  if (!m) return { icon: '·', kind: '' };  // middle dot
  if (m.startsWith('resume failed') || m.includes(' error')) return { icon: '⚠', kind: 'error' };       // ⚠
  if (m.startsWith('agent stopped'))           return { icon: '◼', kind: 'followup' };  // ◼
  if (m.startsWith('agent interrupted'))       return { icon: '⏸', kind: 'followup' };  // ⏸
  if (m.startsWith('agent revived'))           return { icon: '↻', kind: 'dispatch' };  // ↻
  if (m.startsWith('agent resumed'))           return { icon: '↻', kind: 'dispatch' };  // ↻
  if (m.startsWith('agent dispatched'))        return { icon: '▸', kind: 'dispatch' };  // ▸
  if (m.startsWith('agent follow-up queued'))  return { icon: '⋯', kind: 'followup' };  // ⋯
  if (m.startsWith('agent follow-up'))         return { icon: '↗', kind: 'followup' };  // ↗
  if (m.startsWith('hivemind'))                return { icon: '◆', kind: 'hivemind' };  // ◆
  if (m.startsWith('github'))                  return { icon: '◒', kind: 'github' };    // ◒
  if (m.includes('completed'))                 return { icon: '✓', kind: 'completed' }; // ✓
  return { icon: '·', kind: '' };
}

// Build the "Needs you" bucket purely from live project state (don't trust
// activity_log here — it lags). Each item is something the user can act on:
// asking → plan approval / question / generic input; stuck → blocked or error.
function _buildAttentionList() {
  const items = [];
  allProjects.forEach(p => {
    const fs = friendlyStatus(p);
    if (fs !== 'asking' && fs !== 'stuck') return;
    const live = computeLiveStatus(p.id) || {};
    let icon = '✋';  // ✋ default for asking
    let msg = '';
    // `kind`/`action` drive the dashboard "Waiting on you" cards (the action
    // button label). Additive — the desktop feed ignores them.
    let kind = 'input', action = 'Open';
    if (fs === 'asking') {
      // Sub-state from the consolidated resolver (server-fed via
      // p.live_agent.reason), so a CLOSED project's item is labelled
      // correctly without this client's lazily-refreshed agentStatusCache.
      if (live.currentTaskClass === 'plan-approval') { icon = '\u{1F4CB}'; msg = 'Plan ready — needs approval'; kind = 'plan'; action = 'Review'; }       // 📋
      else if (live.currentTaskClass === 'question') { icon = '❓'; msg = 'Question pending — needs answer'; kind = 'question'; action = 'Answer'; }    // ❓
      else { msg = 'Awaiting input to proceed'; action = 'Answer'; }
    } else {
      icon = '⚠';  // ⚠
      kind = 'stuck'; action = 'Unblock';
      if (p.blocked) {
        msg = p.blocked_reason ? `Blocked: ${p.blocked_reason}` : 'Blocked';
      } else if (live.currentTaskClass === 'error') {
        msg = live.currentTask && live.currentTask !== 'Idle' ? `Error: ${live.currentTask}` : 'Error — needs intervention';
      } else {
        msg = 'Stuck — needs intervention';
      }
    }
    items.push({ projectId: p.id, project: p.name || p.id, domain: p.domain, msg, icon,
      kind, action,
      sessionId: live.sessionId || null });  // §1: deep-link target (may be null)
  });
  // Add-on approval requests (MC-1022) are blocking events that belong to no project.
  if (typeof window.addonAttentionItems === 'function') items.push(...window.addonAttentionItems());
  if (typeof window.browserSigninAttentionItems === 'function') items.push(...window.browserSigninAttentionItems());
  return items;
}

// Age bucket for the Recent feed. `old` means too stale to show at all — the
// feed isn't an archive, it's a "what's been alive lately" surface.
function _feedAgeBucket(ts) {
  if (!ts) return { key: 'old', label: '' };
  const ageMs = Date.now() - new Date(ts).getTime();
  if (isNaN(ageMs)) return { key: 'old', label: '' };
  if (ageMs < 0) return { key: 'fresh', label: 'Fresh' };  // clock skew → treat as fresh
  const H = 3600 * 1000, D = 24 * H;
  if (ageMs < H)       return { key: 'fresh', label: 'Fresh · last hour' };
  if (ageMs < D)       return { key: 'today', label: 'Today' };
  if (ageMs < 7 * D)   return { key: 'week',  label: 'This week' };
  return { key: 'old', label: '' };
}

function _updateFeedAttentionBadge(count) {
  const tab = document.getElementById('feed-expand-tab');
  const badge = document.getElementById('feed-tab-badge');
  if (!tab || !badge) return;
  if (count > 0) {
    tab.classList.add('has-attention');
    badge.textContent = String(count);
  } else {
    tab.classList.remove('has-attention');
  }
}

// ── "Kept for review" (read-only) ───────────────────────────────────────────
// Agent worktrees the stale-worktree gc preserved because they still hold
// unmerged/uncommitted work. The count comes from GET /api/worktrees/preserved
// ?summary=1 (git-backed, so slow on a cold cache): fetched at most once per
// KEPT_REFRESH_MS, never blocking renderFeed. Clicking the row opens a list.
// Nothing here deletes or cleans up a worktree; that is the human's call.
const KEPT_REFRESH_MS = 5 * 60 * 1000;
const KEPT_MODAL_ID = '__kept_worktrees';
let _keptCount = 0;
let _keptAttemptAt = 0;     // last fetch START (success or not): failures back off too
let _keptInFlight = false;

function _refreshKeptCount() {
  if (_keptInFlight || (_keptAttemptAt && Date.now() - _keptAttemptAt < KEPT_REFRESH_MS)) return;
  _keptInFlight = true;
  _keptAttemptAt = Date.now();
  fetch(API_BASE + '/api/worktrees/preserved?summary=1')
    .then(r => (r.ok ? r.json() : null))
    .then(d => {
      const n = d && d.totals ? Number(d.totals.preserved) || 0 : null;
      if (n !== null && n !== _keptCount) { _keptCount = n; renderFeed(); }
    })
    .catch(() => {})
    .finally(() => { _keptInFlight = false; });
}

function _keptTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? '—' : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function _keptBytes(n) {
  if (n === null || n === undefined || isNaN(Number(n))) return '—';
  n = Number(n);
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? String(n) : n.toFixed(n >= 100 ? 0 : n >= 10 ? 1 : 2)) + ' ' + u[i];
}

function _keptRowHTML(it) {
  const tags = (it.held_for || []).map(t => `<span class="kept-tag ${esc(t)}">${esc(t)}</span>`).join('');
  const ahead = it.commits_ahead === null || it.commits_ahead === undefined ? '?' : it.commits_ahead;
  const dirty = it.dirty === null || it.dirty === undefined ? '?' : (it.dirty ? 'yes' : 'no');
  return `<div class="kept-row" title="${esc(it.path || '')}">
    <div class="kept-branch">${esc(it.branch || it.session_id)}</div>
    <div class="kept-meta">
      <span>last commit ${esc(_keptTime(it.last_commit_at))}</span>
      <span>ahead ${esc(String(ahead))}</span>
      <span>dirty ${esc(dirty)}</span>
      <span>size ${esc(_keptBytes(it.size_bytes))}</span>
    </div>
    <div class="kept-tags">${tags}</div>
  </div>`;
}

async function openKeptWorktreesModal() {
  let win = document.querySelector(`[data-modal-id="${KEPT_MODAL_ID}"]`);
  if (!win) {
    win = document.createElement('div');
    win.className = 'modal-window';
    win.dataset.modalId = KEPT_MODAL_ID;
    const content = document.createElement('div');
    content.className = 'modal-content';
    _clampModalSize(content, 640);
    win.appendChild(content);
    document.getElementById('modal-layer').appendChild(win);
    const z = nextModalZ++;
    win.style.zIndex = z;
    openModals.set(KEPT_MODAL_ID, { projectId: null, element: win, minimized: false, zIndex: z });
    centerModalElement(win);
  }
  const content = win.querySelector('.modal-content');
  const shell = body => `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Kept for review</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${KEPT_MODAL_ID}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="kept-body">${body}</div>`;
  content.innerHTML = shell('<div class="feed-empty">Loading…</div>');
  focusModal(KEPT_MODAL_ID);

  // measure=1 only STARTS the server's background disk walk (a 500-folder tree
  // takes ~30 s); the response never waits for it. While a project still
  // reports 'measuring' the list is re-fetched (without measure) and redrawn.
  const fetchList = async (measure) => {
    try {
      const r = await fetch(API_BASE + '/api/worktrees/preserved' + (measure ? '?measure=1' : ''));
      return r.ok ? await r.json() : null;
    } catch (_) { return null; }   // shown as unavailable by the caller
  };
  const draw = data => {
    const open = new Set([...content.querySelectorAll('details.kept-group[open]')].map(d => d.dataset.k));
    const first = !content.querySelector('details.kept-group');
    const projects = (data.projects || [])
      .map(p => ({ p, items: (p.items || []).filter(it => !it.live_session) }))
      .filter(g => g.items.length);
    const unreg = (data.projects || []).filter(p => (p.unregistered || []).length);
    const inUse = data.totals ? data.totals.in_use || 0 : 0;
    const disks = (data.projects || []).map(p => p.disk).filter(Boolean);
    const busy = disks.some(d => d.state === 'measuring');
    const totalBytes = data.totals ? data.totals.preserved_bytes : null;
    const lower = (data.projects || []).some(p => p.preserved_disk && p.preserved_disk.complete === false);
    const note = busy ? 'measuring…' : (disks.some(d => d.state === 'unmeasured') ? 'not measured yet' : (lower ? 'lower bound, some folders not measured' : ''));
    const diskLine = `<div class="kept-disk"><b>${esc(_keptBytes(totalBytes))}</b> on disk in the kept folders${note ? ` <span class="kept-note">(${esc(note)})</span>` : ''}</div>`;
    const intro = `<div class="kept-intro">Agent worktrees kept because they hold work that is not in the base branch yet, or uncommitted changes. This list is read-only; nothing here is deleted or merged for you.${inUse ? ` ${inUse} more ${inUse === 1 ? 'is' : 'are'} in use by a live session and not listed.` : ''}</div>${diskLine}`;
    if (!projects.length && !unreg.length) { content.innerHTML = shell(intro + '<div class="feed-empty">Nothing is being kept.</div>'); return busy; }
    const groups = projects.map((g, i) => {
      const k = 'k:' + (g.p.project_id || i);
      const pd = g.p.preserved_disk;
      const sz = pd && pd.bytes !== null && pd.bytes !== undefined ? ` <span class="kept-size">${esc(_keptBytes(pd.bytes))}</span>` : '';
      return `
    <details class="kept-group" data-k="${esc(k)}"${(first ? i === 0 : open.has(k)) ? ' open' : ''}>
      <summary>${esc(g.p.name || g.p.project_id)} <span class="fsh-count">${g.items.length}</span>${sz}</summary>
      ${g.items.map(_keptRowHTML).join('')}
    </details>`;
    }).join('');
    const unregHTML = unreg.map((p, i) => {
      const k = 'u:' + (p.project_id || i);
      const ud = p.unregistered_disk;
      const sz = ud && ud.bytes !== null && ud.bytes !== undefined ? ` <span class="kept-size">${esc(_keptBytes(ud.bytes))}</span>` : '';
      const rows = p.unregistered.map(u => `<div class="kept-row" title="${esc(u.path || '')}">
        <div class="kept-branch">${esc(u.name)}</div>
        <div class="kept-meta"><span>size ${esc(_keptBytes(u.size_bytes))}</span>${u.files === null || u.files === undefined ? '' : `<span>${esc(String(u.files))} files</span>`}</div>
      </div>`).join('');
      return `
    <details class="kept-group" data-k="${esc(k)}"${open.has(k) ? ' open' : ''}>
      <summary>Not known to git: ${esc(p.name || p.project_id)} <span class="fsh-count">${p.unregistered.length}</span>${sz}</summary>
      <div class="kept-intro">Folders under .clayrune/agents that <code>git worktree list</code> does not list. They may also appear above. Nothing here is deleted for you.</div>
      ${rows}
    </details>`;
    }).join('');
    content.innerHTML = shell(intro + groups + unregHTML);
    return busy;
  };

  const run = win._keptRun = {};     // a re-open supersedes this call's poll loop
  let data = await fetchList(true);
  if (!data) { content.innerHTML = shell('<div class="feed-empty">Could not load the list.</div>'); return; }
  let stillBusy = draw(data);
  // Poll while the walk runs; stop when it ends, the modal is closed, or after ~10 min.
  for (let n = 0; stillBusy && n < 150 && win.isConnected && win._keptRun === run; n++) {
    await new Promise(res => setTimeout(res, 4000));
    if (!win.isConnected || win._keptRun !== run) break;
    const next = await fetchList(false);
    if (next) stillBusy = draw(next);
  }
}
window.openKeptWorktreesModal = openKeptWorktreesModal;

function renderFeed() {
  const feedEl = document.getElementById('feed-entries');
  if (!feedEl) return;
  _refreshKeptCount();

  // ── Bucket 1: Needs you (derived from live state, not the log) ────────
  const attention = _buildAttentionList();

  // ── Bucket 2: Recent — one rolling line per project ───────────────────
  // activity_log is insert(0,…) so index 0 is the newest entry per project.
  const groups = [];
  allProjects.forEach(p => {
    const log = p.activity_log || [];
    if (!log.length) return;
    const newest = log[0];
    groups.push({
      projectId: p.id,
      project: p.name || p.id,
      domain: p.domain,
      ts: newest.ts || '',
      ts_relative: newest.ts_relative || '',
      msg: newest.msg || '',
      count: log.length,
    });
  });
  groups.sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));
  const recent = groups.slice(0, 24);

  const parts = [];

  if (attention.length) {
    parts.push(`<div class="feed-section-head needs">Needs you <span class="fsh-count">${attention.length}</span></div>`);
    parts.push('<div class="feed-bucket">');
    attention.forEach(it => {
      const color = getDomainConfig(it.domain).color;
      parts.push(`
        <div class="feed-entry attention" data-project-id="${esc(it.projectId)}" data-session-id="${esc(it.sessionId || '')}" title="Click to open the waiting chat">
          <div class="feed-entry-row">
            <span class="fe-icon attention">${it.icon}</span>
            <div>
              <div class="feed-entry-project" style="color:${color}">${esc(it.project)}</div>
              <div class="feed-entry-msg">${esc(it.msg)}</div>
            </div>
            <span></span>
          </div>
        </div>`);
    });
    parts.push('</div>');
  }

  if (_keptCount > 0) {
    parts.push(`<div class="feed-bucket"><div class="feed-entry kept-review" title="Agent worktrees kept because they hold unmerged or uncommitted work (read-only list)">
      <div class="feed-entry-row">
        <span class="fe-icon">&#128230;</span>
        <div><div class="feed-entry-project">Kept for review <span class="fsh-count">${_keptCount}</span></div></div>
        <span></span>
      </div></div></div>`);
  }

  // Time-bucket the per-project rows. Anything older than a week drops off —
  // the Recent surface is for what's alive, not an archive (Agent Log covers
  // history). Fresh stays full-color; older buckets fade via CSS.
  const buckets = { fresh: [], today: [], week: [] };
  let droppedOld = 0;
  recent.forEach(g => {
    const b = _feedAgeBucket(g.ts);
    if (b.key === 'old') { droppedOld++; return; }
    buckets[b.key].push(g);
  });
  const visibleCount = buckets.fresh.length + buckets.today.length + buckets.week.length;

  parts.push(`<div class="feed-section-head">Recent${visibleCount ? ` <span class="fsh-count">${visibleCount}</span>` : ''}</div>`);
  if (!visibleCount) {
    parts.push(`<div class="feed-empty">${droppedOld ? 'No activity in the last 7 days' : 'No activity yet'}</div>`);
  } else {
    const order = [
      { key: 'fresh', label: 'Fresh · last hour' },
      { key: 'today', label: 'Today' },
      { key: 'week',  label: 'This week' },
    ];
    order.forEach(({ key, label }) => {
      const rows = buckets[key];
      if (!rows.length) return;
      parts.push(`<div class="feed-subhead">${label}</div>`);
      parts.push('<div class="feed-bucket">');
      rows.forEach(g => {
        const color = getDomainConfig(g.domain).color;
        const cls = classifyFeedEvent(g.msg);
        const more = g.count > 1 ? `<div class="fe-more">+${g.count - 1} earlier</div>` : '';
        parts.push(`
          <div class="feed-entry bucket-${key}" data-project-id="${esc(g.projectId)}">
            <div class="feed-entry-row">
              <span class="fe-icon ${esc(cls.kind)}">${cls.icon}</span>
              <div>
                <div class="feed-entry-project" style="color:${color}">${esc(g.project)}</div>
                <div class="feed-entry-msg">${esc(g.msg)}</div>
                ${more}
              </div>
              <span class="feed-entry-ts">${esc(g.ts_relative || g.ts || '')}</span>
            </div>
          </div>`);
      });
      parts.push('</div>');
    });
  }

  feedEl.innerHTML = parts.join('');
  feedEl.querySelectorAll('.feed-entry[data-project-id]').forEach(el => {
    el.addEventListener('click', () => {
      // §1: "Needs you" rows carry a session id → deep-link straight to the
      // waiting chat (plan/question state), skipping the project list. Recent
      // rows (and any attention row with no live session) fall back to the
      // project modal as before.
      if (el.dataset.projectId === '__addons__') { window.openAddonsSettings(); return; }
      if (el.dataset.projectId === '__browser_signin__') { window.openSecretsVault(); return; }
      const sid = el.dataset.sessionId;
      if (sid) openProjectAtSession(el.dataset.projectId, sid);
      else openProjectModal(el.dataset.projectId);
    });
  });

  const keptEl = feedEl.querySelector('.feed-entry.kept-review');
  if (keptEl) keptEl.addEventListener('click', openKeptWorktreesModal);

  _updateFeedAttentionBadge(attention.length);
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.renderFeed = renderFeed;
window._buildAttentionList = _buildAttentionList;  // §1b: mobile inbox reuses it
