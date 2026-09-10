// ── The Desk — an in-house marketing department ─────────────────────────────
// Spec: docs/THE_DESK_SPEC.md. Field scan: docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md.
//
// A workspace PEER TO THE FLOOR, not a tab inside each project. That follows
// from the content: a story about Clayrune's scheduler and a story about the
// engulfing scanner come from different projects, go out under the same voice,
// on the same calendar, to the same audience. Splitting that across project
// modals makes the calendar unviewable and the voice incoherent.
//
// FOUR SURFACES, and the split is the product:
//   BOARD     what is happening this month, and why      (campaigns + hot signals)
//   QUEUE     what needs me right now                    (drafts — the only nag)
//   CALENDAR  what is scheduled and what went out
//   LEDGER    what we have said, how it did
//
// Why four and not one list: the 2026-09-09 field scan found every competitor
// stores a QUEUE and nothing else. A queue answers "what is pending" and cannot
// answer "why are we running this" or "have we already said it". Collapsing
// these back into one pane rebuilds the thing the scan found a dozen copies of.
//
// The QUEUE surface deliberately reuses cross-social.js's row markup and its
// edit/release/push-back handlers rather than forking them — same
// .backlog-item/.social-item classes, same per-project scoping, one set of
// behaviours to keep correct. The per-project Social tab stays as a filtered
// view of the same queue.

const DESK_MODAL_ID = '__desk';
const DESK_TABS = ['board', 'queue', 'calendar', 'ledger'];
// NOTE: there is deliberately no DESK_VOICES constant here. The voices are
// defined ONCE, server-side, in `mc/desk.py` (VOICES) and read over
// GET /api/desk/voices. A second copy in this file could drift from the server's
// list silently, and nothing would have caught it.

let _deskTab = 'board';
let _deskData = { campaigns: [], hot_signals: [], recent_posts: [], voices: [], pending_drafts: 0 };
let _deskSignals = [];
let _deskLoading = false;
let _deskHarvesting = false;
let _deskDrafting = new Set();

// ── data ────────────────────────────────────────────────────────────────────

async function _deskFetch(path, opts) {
  const res = await fetch(API_BASE + path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function _loadDesk() {
  if (_deskLoading) return;
  _deskLoading = true;
  try {
    _deskData = await _deskFetch('/api/desk/overview');
  } catch (e) {
    // A dead endpoint must not leave the pane looking like a quiet week —
    // renderDesk() shows the error rather than an empty board.
    _deskData = { ..._deskData, _error: String(e.message || e) };
  } finally {
    _deskLoading = false;
    if (openModals.has(DESK_MODAL_ID)) renderDesk();
  }
}

async function _loadDeskSignals() {
  try {
    _deskSignals = await _deskFetch('/api/desk/signals?limit=120&sort=score');
  } catch (e) { _deskSignals = []; }
  if (openModals.has(DESK_MODAL_ID)) renderDesk();
}

// Harvest is idempotent by `ref` (mc/desk_harvest.py), so a double-click costs
// a round trip and nothing else — no confirm, no disabled-state gymnastics.
async function deskHarvest() {
  if (_deskHarvesting) return;
  _deskHarvesting = true;
  renderDesk();
  try {
    const out = await _deskFetch('/api/desk/signals/harvest', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (typeof showToast === 'function') {
      showToast(`Harvested ${out.commits || 0} commits, ${out.backlog || 0} shipped items`);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Harvest failed: ' + e.message);
  } finally {
    _deskHarvesting = false;
    await Promise.all([_loadDesk(), _loadDeskSignals()]);
  }
}

// Ask the roster's writer (Posy) for a draft off one signal. The Desk does not
// generate — it briefs. The draft lands PENDING on the Queue; nothing here can
// publish it, and nothing here should ever grow the ability to.
async function deskDraft(signalId, voice) {
  if (_deskDrafting.has(signalId)) return;
  _deskDrafting.add(signalId);
  renderDesk();
  try {
    const out = await _deskFetch('/api/desk/draft', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ signal_id: signalId, voice }),
    });
    if (typeof showToast === 'function') {
      showToast(`Posy is drafting a ${out.platform} post — it lands in the Queue.`);
    }
  } catch (e) {
    // A 409 means the signal was already drafted from, which is a real answer
    // and not a failure — say which, rather than a generic error.
    const msg = /409/.test(e.message) ? 'Already drafted from that one.'
                                      : 'Could not brief the writer: ' + e.message;
    if (typeof showToast === 'function') showToast(msg);
  } finally {
    _deskDrafting.delete(signalId);
    _loadDeskSignals();
  }
}

// Create a campaign.
//
// THIS WAS A CHAIN OF prompt() CALLS AND THAT WAS WRONG. Ron typed a voice the
// API did not accept and lost the thesis, the title and the agenda he had
// already written — a validation error that destroys prior input is never
// acceptable, and `prompt()` cannot offer a picker, so it made an invalid value
// possible in the first place. A real form fixes both at once: nothing is lost
// on a bad value, and VOICE IS A SELECT, so a bad value cannot be entered.
//
// The voices come from GET /api/desk/voices — the server's own list — rather
// than a constant duplicated in this file. There were two copies of that tuple
// and the frontend one could silently drift from `mc/desk.py`.
//
// The thesis sits FIRST, before the title, because the API refuses without it
// and the order should make that obvious rather than surfacing it as an error.
async function deskNewCampaign() {
  let voices = [];
  try {
    voices = await _deskFetch('/api/desk/voices');
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not load the voices: ' + e.message, 4000);
    return;
  }

  const modalId = '__desk_campaign';
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  // `modal-fit` drops the inherited 80vh height — this form is ~500px tall and
  // the rest was empty space under the button.
  content.className = 'modal-content modal-fit';
  _clampModalSize(content, 520);
  content.innerHTML = `
    <div class="modal-header" style="padding:18px 24px 10px 28px">
      <div class="modal-window-controls" style="position:absolute;top:14px;right:16px;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:17px;font-weight:700;color:var(--text)">New campaign</h2>
    </div>
    <div style="padding:6px 28px 22px;overflow-y:auto">
      <div class="form-group">
        <label>1. The argument</label>
        <input type="text" id="camp-thesis" placeholder="Clayrune keeps agents alive between sessions">
        <div class="hint">Not the topic — the claim you want a reader to end up believing.
          Every draft in this campaign has to earn it.</div>
      </div>
      <div class="form-group">
        <label>2. Short name</label>
        <input type="text" id="camp-title" placeholder="Agent persistence">
        <div class="hint">For your eyes only, on the Board.</div>
      </div>
      <div class="form-group">
        <label>3. Which voices carry it</label>
        <div class="camp-voice-picks">
          ${voices.map((v, i) => `
            <label class="camp-voice-pick">
              <input type="checkbox" class="camp-voice-cb" value="${esc(v.name)}" ${i === 0 ? 'checked' : ''}>
              <span class="camp-voice-name">${esc(v.name)}</span>
              <span class="desk-platform">${esc(v.platform || '')}</span>
              ${v.register ? `<span class="camp-voice-reg">${esc(v.register)}</span>` : ''}
            </label>`).join('')}
        </div>
        <!-- Pick MORE THAN ONE and the same argument gets written once per
             voice, never cross-posted. The platform is not a separate choice
             here: it belongs to the voice, and letting a campaign set both
             would let them disagree. -->
        <div class="hint">Pick more than one and the argument gets written once per voice,
          in each voice's own register. The platform comes with the voice — these are not
          variants of each other, which is why nothing is ever cross-posted.</div>
      </div>
      <div class="form-group">
        <label>4. Why now <span style="text-transform:none;font-weight:400">(optional)</span></label>
        <input type="text" id="camp-agenda" placeholder="launch window opens in three weeks">
      </div>
      <div id="camp-error" class="social-attr-warn" style="display:none"></div>
      <button class="btn-add" style="width:100%;margin-top:4px" onclick="deskSubmitCampaign()">Create campaign</button>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  const first = document.getElementById('camp-thesis');
  if (first) first.focus();
}

// Submit WITHOUT tearing the form down on failure — the whole point of replacing
// the prompt chain. The modal only closes on success.
async function deskSubmitCampaign() {
  const err = document.getElementById('camp-error');
  const show = (m) => { if (err) { err.textContent = m; err.style.display = 'block'; } };

  const thesis = (document.getElementById('camp-thesis') || {}).value?.trim() || '';
  const title = (document.getElementById('camp-title') || {}).value?.trim() || '';
  const agenda = (document.getElementById('camp-agenda') || {}).value?.trim() || '';
  const picked = Array.from(document.querySelectorAll('.camp-voice-cb'))
    .filter(cb => cb.checked).map(cb => cb.value);

  if (!thesis) return show('The argument is required — without one this is a folder, not a campaign.');
  if (!title) return show('Give it a short name so you can find it on the Board.');
  if (!picked.length) return show('Pick at least one voice — a campaign has to be spoken by someone.');

  try {
    await _deskFetch('/api/desk/campaigns', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, thesis, agenda, voices: picked }),
    });
  } catch (e) {
    // Stays open with everything still typed in it.
    return show('Could not create it: ' + e.message);
  }
  closeModalById('__desk_campaign');
  if (typeof showToast === 'function') showToast(`Campaign "${title}" created.`);
  await _loadDesk();
}

// Move a campaign through its states from the Board. Proposed -> running is the
// one that matters; the rest exist so a campaign can be stopped without being
// deleted, because a dropped campaign is still evidence of a decision.
async function deskCampaignState(id, state) {
  try {
    await _deskFetch(`/api/desk/campaigns/${encodeURIComponent(id)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state }),
    });
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not update it: ' + e.message, 4000);
  }
  await _loadDesk();
}

// ── open ────────────────────────────────────────────────────────────────────

async function openDesk() {
  if (openModals.has(DESK_MODAL_ID)) {
    const entry = openModals.get(DESK_MODAL_ID);
    if (entry.minimized) restoreModal(DESK_MODAL_ID);
    focusModal(DESK_MODAL_ID);
    renderDesk();
    _loadDesk(); _loadDeskSignals(); _hydrateAllSocial();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = DESK_MODAL_ID;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 940);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">The Desk</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${DESK_MODAL_ID}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${DESK_MODAL_ID}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:0 24px 20px 28px">
      <div class="desk-tabs" id="desk-tabs"></div>
      <div id="desk-body" style="max-height:66vh;overflow-y:auto"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(DESK_MODAL_ID, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(DESK_MODAL_ID);

  renderDesk();
  _loadDesk();
  _loadDeskSignals();
  _hydrateAllSocial();   // the Queue surface reads the same hydrated projects
}

function deskTab(tab) {
  if (!DESK_TABS.includes(tab)) return;
  _deskTab = tab;
  renderDesk();
}

// ── render ──────────────────────────────────────────────────────────────────

function _deskPendingCount() {
  // Count locally rather than trusting the overview's snapshot: the user can
  // release a draft from the Queue tab without the overview being refetched,
  // and a tab badge that lies about what is waiting is worse than no badge.
  //
  // `approved` counts too. A released draft still owes Ron two actions — post
  // it, then record that it went out — and until the second one happens the
  // story ledger has no row, which silently disables the repetition guard and
  // leaves the Calendar and Ledger surfaces empty. Counting only `pending`
  // would hide exactly the work that closes the loop.
  let n = 0;
  for (const p of (typeof allProjects !== 'undefined' ? allProjects : [])) {
    if (p._socialQueueFull && Array.isArray(p.social_queue)) {
      n += p.social_queue.filter(i => i.status === 'pending' || i.status === 'approved').length;
    } else {
      // The projects payload only summarises PENDING (`social_pending_count`),
      // so an unhydrated project undercounts until its queue is fetched. Better
      // than blocking the badge on a full hydration of every project.
      n += (p.social_pending_count || 0);
    }
  }
  return n;
}

function renderDesk() {
  const tabsEl = document.getElementById('desk-tabs');
  const bodyEl = document.getElementById('desk-body');
  if (!tabsEl || !bodyEl) return;

  const pending = _deskPendingCount();
  const labels = {
    board: 'Board', queue: 'Queue', calendar: 'Calendar', ledger: 'Ledger',
  };
  tabsEl.innerHTML = DESK_TABS.map(t => `
    <button class="desk-tab${t === _deskTab ? ' active' : ''}" onclick="deskTab('${t}')">
      ${labels[t]}${t === 'queue' && pending ? ` <span class="desk-tab-badge">${pending}</span>` : ''}
    </button>`).join('') + `
    <span style="flex:1"></span>
    <button class="desk-harvest-btn" onclick="deskHarvest()" ${_deskHarvesting ? 'disabled' : ''}
      title="Read every project's new commits and shipped backlog items into the feed">
      ${_deskHarvesting ? 'Reading the projects…' : 'Read the projects'}
    </button>`;

  if (_deskData._error) {
    bodyEl.innerHTML = `<div class="desk-empty">Could not reach the Desk: ${esc(_deskData._error)}</div>`;
    return;
  }
  if (_deskTab === 'board') bodyEl.innerHTML = _renderBoard();
  else if (_deskTab === 'queue') _renderQueueInto(bodyEl);
  else if (_deskTab === 'calendar') bodyEl.innerHTML = _renderCalendar();
  else bodyEl.innerHTML = _renderLedger();
}

function _deskSectionHTML(label, hint) {
  return `<div class="desk-section">
    <span class="desk-section-label">${label}</span>
    ${hint ? `<span class="desk-section-hint">${hint}</span>` : ''}
    <span class="desk-section-rule"></span>
  </div>`;
}

// Posts published in the last seven days. The Calendar answers "when", this
// answers "are we actually shipping anything" — the number a marketing surface
// should lead with alongside what it owes you.
function _deskThisWeek() {
  const cut = new Date(Date.now() - 7 * 864e5).toISOString();
  return (_deskData.recent_posts || []).filter(p => (p.published_at || '') >= cut).length;
}

// BOARD — what is happening this month, and why.
function _renderBoard() {
  const camps = _deskData.campaigns || [];
  const all = _deskSignals.length ? _deskSignals : (_deskData.hot_signals || []);
  const hot = all.slice(0, 25);
  const pending = _deskPendingCount();
  const running = camps.filter(c => c.state === 'running').length;
  const week = _deskThisWeek();
  const ideas = all.filter(s => !s.consumed_by && (s.story_score || 0) >= 0.35).length;

  // Exactly ONE hero figure, and it is what the Desk owes you — the only number
  // here that should ever pull Ron out of what he was doing. Everything else on
  // this surface is for reading, so it stays secondary by size on purpose.
  const kpiHTML = `
    <div class="desk-kpis">
      <div class="desk-kpi hero${pending ? '' : ' clear'}">
        <span class="desk-kpi-label">Waiting on you</span>
        <span class="desk-kpi-value">${pending || 'None'}</span>
        ${pending
          ? `<button class="desk-kpi-cta" onclick="deskTab('queue')">Review ${pending === 1 ? 'it' : 'them'} →</button>`
          : `<span class="desk-kpi-note">Nothing needs a decision.</span>`}
      </div>
      <div class="desk-kpi">
        <span class="desk-kpi-label">Campaigns</span>
        <span class="desk-kpi-value">${running}</span>
        <span class="desk-kpi-note">${running === 1 ? 'running now' : 'running now'}${camps.length > running ? ` · ${camps.length - running} idle` : ''}</span>
      </div>
      <div class="desk-kpi">
        <span class="desk-kpi-label">Went out</span>
        <span class="desk-kpi-value">${week}</span>
        <span class="desk-kpi-note">in the last 7 days</span>
      </div>
      <div class="desk-kpi">
        <span class="desk-kpi-label">Story ideas</span>
        <span class="desk-kpi-value">${ideas}</span>
        <span class="desk-kpi-note">unused, worth a look</span>
      </div>
    </div>`;

  const campHTML = camps.length ? `<div class="desk-campaigns">${camps.map(c => `
    <div class="desk-campaign state-${esc(c.state)}">
      <div class="desk-campaign-head">
        <span class="desk-campaign-title">${esc(c.title)}</span>
        <span class="desk-state-chip">${esc(c.state)}</span>
        ${(c.voices || (c.voice ? [c.voice] : [])).map(v => `<span class="desk-voice-chip">${esc(v)}</span>`).join('')}
      </div>
      <div class="desk-campaign-thesis">${esc(c.thesis)}</div>
      ${c.agenda ? `<div class="desk-campaign-agenda"><b>Why now:</b> ${esc(c.agenda)}</div>` : ''}
      <div class="desk-campaign-actions">
        ${c.state === 'proposed' ? `<button onclick="deskCampaignState('${esc(c.id)}','running')">Start it</button>` : ''}
        ${c.state === 'running' ? `<button onclick="deskCampaignState('${esc(c.id)}','paused')">Pause</button>` : ''}
        ${c.state === 'paused' ? `<button onclick="deskCampaignState('${esc(c.id)}','running')">Resume</button>` : ''}
        ${c.state !== 'done' && c.state !== 'dropped' ? `<button onclick="deskCampaignState('${esc(c.id)}','done')">Finish</button>` : ''}
      </div>
    </div>`).join('')}</div>` : `
    <div class="desk-empty" style="margin-bottom:22px">
      No campaigns yet. A campaign carries a <b>thesis</b> and a reason it is
      running now — that is what makes this a plan rather than a list of drafts.
    </div>`;

  // The feed, sorted by story score. Most of these will never become posts, and
  // that is the design: the feed is the evidence, a campaign is the argument.
  //
  // The score is a METER rather than a printed number: magnitude is what it
  // encodes, and a bar is read at a glance where "0.05" has to be parsed. Kind
  // stays a LABEL — six cycled category colours would be an unvalidated
  // categorical palette for no gain. Low scorers dim rather than disappear;
  // nothing is hidden from the feed, it just stops competing.
  const sigHTML = hot.length ? `<div class="desk-feed">${hot.map(s => {
    const score = Math.max(0, Math.min(1, s.story_score || 0));
    const cold = score < 0.35;
    return `
    <div class="desk-signal ${cold ? 'cold' : 'hot'}">
      <span class="desk-meter" title="Story value ${score.toFixed(2)} — a suggestion, not a verdict">
        <span class="desk-meter-fill" style="width:${Math.round(score * 100)}%"></span>
      </span>
      <span class="desk-signal-kind">${esc(s.kind)}</span>
      <span class="desk-signal-text">${esc(s.summary || '')}</span>
      <span class="desk-signal-proj">${esc(s.project_id || '')}</span>
      <span class="desk-signal-when">${esc((s.occurred_at || '').slice(0, 10))}</span>
      ${s.consumed_by
        ? `<span class="desk-signal-used" title="Already drafted from">used</span>`
        : `<span class="desk-draft-actions">
             <button class="desk-draft-btn" onclick="deskDraft('${esc(s.id)}','ron')"
               title="Ask Posy for an X post in Ron's voice">X</button>
             <button class="desk-draft-btn" onclick="deskDraft('${esc(s.id)}','clayrune')"
               title="Ask Posy for a LinkedIn post in Clayrune's voice">in</button>
           </span>`}
    </div>`;
  }).join('')}</div>` : `
    <div class="desk-empty">
      Nothing in the feed yet. Hit <b>Read the projects</b> — it pulls each
      project's new commits and shipped backlog items in.
    </div>`;

  return kpiHTML
    + `<div class="desk-section">
         <span class="desk-section-label">Campaigns</span>
         ${camps.length ? '<span class="desk-section-hint">what we are arguing, and why now</span>' : ''}
         <span class="desk-section-rule"></span>
         <button class="desk-new-campaign" onclick="deskNewCampaign()"
           title="A campaign carries a thesis and a reason to run now">+ New campaign</button>
       </div>`
    + campHTML
    + _deskSectionHTML('What happened',
        'highest story value first — most of it will never become a post')
    + sigHTML;
}

// QUEUE — the only surface that ever demands anything of you.
function _renderQueueInto(bodyEl) {
  // Reuse cross-social.js wholesale: it already renders rows with the correct
  // per-project scoping and the attribution guard, and two copies of that logic
  // would drift. We just host its container.
  bodyEl.innerHTML = `
    <div style="display:flex;gap:8px;align-items:center;margin:10px 0 12px;flex-wrap:wrap">
      <input type="text" id="asl-search" placeholder="Search draft text..."
        value="${esc((window._allSocialFilter && window._allSocialFilter.search) || '')}"
        style="flex:1;min-width:180px;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
        oninput="_allSocialFilter.search=this.value;renderAllSocial()">
      <span id="asl-count" style="font-size:11px;color:var(--text-faint)"></span>
    </div>
    <div id="asl-list"></div>`;
  if (typeof renderAllSocial === 'function') renderAllSocial();
}

// CALENDAR — what is scheduled and what went out, grouped by day.
function _renderCalendar() {
  const posts = _deskData.recent_posts || [];
  if (!posts.length) {
    return `<div class="desk-empty">Nothing has gone out yet. Released drafts land here and in the Ledger.</div>`;
  }
  const byDay = {};
  for (const p of posts) {
    const day = (p.published_at || '').slice(0, 10) || 'undated';
    (byDay[day] = byDay[day] || []).push(p);
  }
  return _deskSectionHTML('What went out', 'newest first') +
    Object.keys(byDay).sort().reverse().map(day => `
    <div class="desk-day">
      <div class="desk-day-label">${esc(day)}</div>
      ${byDay[day].map(p => `
        <div class="desk-post">
          <span class="desk-platform">${esc(p.platform || '')}</span>
          <span class="desk-voice-chip">${esc(p.voice || '')}</span>
          <span class="desk-post-body">${esc((p.body || '').slice(0, 140))}</span>
        </div>`).join('')}
    </div>`).join('');
}

// LEDGER — what we have said. Its job is not analytics; it is stopping the
// Desk repeating itself and letting it reference its own earlier posts.
function _renderLedger() {
  const posts = _deskData.recent_posts || [];
  if (!posts.length) {
    return `<div class="desk-empty">
      Nothing published yet. Once there is, this is what stops the Desk
      re-announcing the same feature next month.
    </div>`;
  }
  return _deskSectionHTML('Everything we have said',
      'so the Desk does not re-announce it next month') +
    posts.map(p => `
    <div class="desk-ledger-row">
      <div class="desk-ledger-head">
        <span class="desk-platform">${esc(p.platform || '')}</span>
        <span class="desk-voice-chip">${esc(p.voice || '')}</span>
        ${p.project_id ? `<span class="desk-signal-proj">${esc(p.project_id)}</span>` : ''}
        <span class="desk-signal-when">${esc((p.published_at || '').slice(0, 10))}</span>
      </div>
      <div class="desk-ledger-body">${esc(p.body || '')}</div>
      ${p.outcome ? `<div class="desk-ledger-outcome">${esc(JSON.stringify(p.outcome))}</div>` : ''}
    </div>`).join('');
}

// ── Interop: these are ES modules, so top-level names are NOT global.
//    Same rationale as cross-social.js's block. ──
window.openDesk = openDesk;
window.deskTab = deskTab;
window.deskHarvest = deskHarvest;
window.deskDraft = deskDraft;
window.deskNewCampaign = deskNewCampaign;
window.deskSubmitCampaign = deskSubmitCampaign;
window.deskCampaignState = deskCampaignState;
window.renderDesk = renderDesk;
