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
let _deskProposals = [];
let _deskWriting = [];
let _deskWritingTimer = null;
let _deskTriaging = false;
let _deskSeeAllOpen = false;

// ── Shell state (UI brief §1) — cadence chip, roster avatar, full voice
// records. Loaded ONCE per open, separately from _loadDesk()'s overview
// poll: none of this changes turn-to-turn the way pending drafts do, and the
// cadence lookup crosses two other stores (workflows + schedules) that the
// Desk's own overview route has no reason to join.
let _deskCadenceSchedule = null;   // the schedule whose workflow_id runs desk_harvest, or null
let _deskAllVoices = [];           // full /api/desk/voices records (platform, register, rewrites)
let _deskPosyAvatar = '';          // Posy's roster avatar value, resolved via /api/floor's bench
let _deskLedger30 = null;          // wider ledger pull for the 30-day panel (overview only ships 10)

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

// SHELL — the header cluster's own data. Two lookups the cadence chip needs
// that no existing route joins for us:
//   1. Which workflow is "the Desk's"? There is no name/tag for that — the
//      only real signal is a workflow containing a `desk_harvest` action node
//      (mc/workflows.py ACTION_ALLOWLIST), so that is what we search for.
//   2. Which schedule points at it, and is that schedule enabled or paused?
// No such workflow existing yet is a REAL, common state (spec: "still to
// build ... the scheduler cadence") — the chip renders "Set a cadence ›" for
// it, not an error.
async function _loadDeskShell() {
  try {
    const [workflows, schedules, voices] = await Promise.all([
      _deskFetch('/api/workflows'),
      _deskFetch('/api/schedules'),
      _deskFetch('/api/desk/voices'),
    ]);
    _deskAllVoices = voices || [];
    const deskWf = (workflows || []).find(w =>
      (w.nodes || []).some(n => n && n.action === 'desk_harvest'));
    _deskCadenceSchedule = deskWf
      ? (schedules || []).find(s => s.workflow_id === deskWf.id) || null
      : null;
  } catch (e) {
    _deskCadenceSchedule = null;
  }
  // Posy's face, from the SAME resolution path the Floor uses — never a
  // second copy of the avatar rule. A bench miss (no global
  // social-media-strategist type) leaves this '' and avatarHTML renders the
  // neutral dot, never a placeholder letter.
  try {
    const floor = await _deskFetch('/api/floor');
    const posy = (floor.bench || []).find(
      b => (b.scope || 'global') === 'global' && b.name === 'social-media-strategist');
    _deskPosyAvatar = posy ? (posy.avatar || '') : '';
  } catch (e) { _deskPosyAvatar = ''; }
  if (openModals.has(DESK_MODAL_ID)) renderDesk();
}

// The Board's "last 30 days" needs more than the overview's 10-row preview,
// so it gets its own pull rather than inflating what every other caller of
// /api/desk/overview has to pay for.
async function _loadDeskLedgerWindow() {
  try { _deskLedger30 = await _deskFetch('/api/desk/ledger?limit=200'); }
  catch (e) { _deskLedger30 = []; }
  if (openModals.has(DESK_MODAL_ID)) renderDesk();
}

const _DESK_DAY_NAMES = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function _deskCadenceText(sched) {
  const t = sched.schedule_type;
  const time = sched.time || '09:00';
  if (t === 'daily') {
    const days = sched.days || [];
    if (days.length === 1) return `Runs weekly · ${_DESK_DAY_NAMES[days[0]] || '?'} ${time}`;
    if (days.length > 1) return `Runs ${days.length}x/week · ${time}`;
    return `Runs daily · ${time}`;
  }
  if (t === 'interval') {
    const m = sched.interval_minutes || 60;
    return `Runs every ${m >= 60 && m % 60 === 0 ? (m / 60) + 'h' : m + 'm'}`;
  }
  if (t === 'cron') return 'Runs on cron';
  if (t === 'once') return 'Runs once';
  return 'Runs';
}

// Click opens the workflow builder ON that workflow (UI brief §1) — never a
// second cadence editor here. No workflow yet -> opens the builder fresh;
// there is no "Desk template" to seed it with, so this is honest about
// starting blank rather than pretending one exists.
function _deskCadenceChipHTML() {
  const sched = _deskCadenceSchedule;
  if (!sched) {
    return `<button class="desk-cadence-chip desk-cadence-unset"
      onclick="_deskOpenCadenceWorkflow()" title="No workflow harvests the feed yet">
      Set a cadence ›</button>`;
  }
  const paused = !sched.enabled;
  return `<button class="desk-cadence-chip${paused ? ' paused' : ''}"
    onclick="_deskOpenCadenceWorkflow()"
    title="${paused ? 'Paused — click to open the workflow' : 'Click to open the workflow that runs this'}">
    <span class="desk-cadence-dot"></span>${esc(_deskCadenceText(sched))}<span class="desk-cadence-arrow"> workflow ›</span>
  </button>`;
}

// The builder is an inline host mounted inside a project modal's Workflows
// tab now, not a floating modal (MC-871 rehost) — `openWorkflowBuilder` no-ops
// with just a console warning when no `#wfb-inline-host-<projectId>` exists,
// which is exactly what silently broke this chip when it called it with ''
// from the Desk (a project-less global modal). This gets a real project id
// and lands on that tab first.
//
// "The workflow's own project" isn't a field that exists on the record: a
// desk_harvest node's project is commonly BLANK by design ("blank = every
// project", workflow-builder.js's own field editor), so the schedule/other
// nodes are checked too, in order, before giving up and using whichever
// project modal the user already has open -- never an arbitrary pick among
// projects nobody is looking at.
function _deskWorkflowProjectId(wf, sched) {
  if (sched && sched.project_id) return sched.project_id;
  for (const n of (wf && wf.nodes) || []) {
    if (!n) continue;
    if (n.type === 'agent' && n.project_id) return n.project_id;
    if (n.type === 'action' && n.config && n.config.project_id) return n.config.project_id;
  }
  return '';
}

function _deskFallbackProjectId() {
  let best = null;
  for (const [id, entry] of openModals) {
    if (String(id).startsWith('__') || entry.minimized) continue;
    if (!best || entry.zIndex > best.zIndex) best = entry;
  }
  if (best) return best.projectId;
  return allProjects.length === 1 ? allProjects[0].id : '';
}

async function _deskOpenCadenceWorkflow() {
  const sched = _deskCadenceSchedule;
  const workflowId = sched ? sched.workflow_id : null;
  let wf = null;
  if (sched && !sched.project_id) {
    try {
      const workflows = await _deskFetch('/api/workflows');
      wf = (workflows || []).find(w => w.id === sched.workflow_id) || null;
    } catch (e) { wf = null; }
  }
  const projectId = (sched ? _deskWorkflowProjectId(wf, sched) : '') || _deskFallbackProjectId();
  if (!projectId) {
    if (typeof showToast === 'function') {
      showToast('Open a project first — the workflow builder needs one to host it in.', 4000);
    }
    console.warn('[desk] cadence chip: no project available to open the workflow builder in');
    return;
  }
  openProjectModal(projectId);
  switchModalTab(projectId, 'workflows');
  await _deskMountWorkflowBuilder(projectId, workflowId);
}

// A freshly opened project modal fires its own lazy loads (agent status,
// backlog, terminal status, social queue -- modal-manager.js's post-open
// Promise.all), each of which calls refreshModalById the moment it lands.
// That rebuild preserves an ALREADY-MOUNTED canvas (index.html's
// `_savedWfHost` guard, keyed on `#wfb-canvas-viewport` existing) but not the
// still-empty `#wfb-clayrune-section-<projectId>` loadWorkflows puts there a
// moment earlier -- whichever finishes first wins the DOM, and a lazy load
// landing between our loadWorkflows() and openWorkflowBuilder() wipes the
// host out from under us. There's no promise to await instead (openProjectModal
// doesn't hand one back), so verify and retry the mount rather than silently
// no-op like the pre-fix chip did.
async function _deskMountWorkflowBuilder(projectId, workflowId, attempt) {
  await loadWorkflows(projectId);
  if (document.getElementById('wfb-inline-host-' + projectId)) {
    await openWorkflowBuilder(workflowId, projectId);
    return;
  }
  if ((attempt || 0) >= 3) {
    console.warn('[desk] cadence chip: workflow builder host never stabilized for project', projectId);
    return;
  }
  await new Promise(r => setTimeout(r, 120));
  await _deskMountWorkflowBuilder(projectId, workflowId, (attempt || 0) + 1);
}

// ACCOUNTS — the spec's "account inventory" (which platforms are connected,
// authenticated, stale) has no store yet; only the publishing office (spec
// §1, "still to build") would populate one. Rendered honestly rather than
// guessed from vault secret names, which could easily be wrong.
function deskAccountsInfo() {
  if (typeof showToast === 'function') {
    showToast('No account inventory yet — this fills in with the publishing office.', 4000);
  }
}

// REPLY TO POSY — the Board's note is not backed by a real thread yet (that
// lands with the Queue's pushback composer, UI brief build order step 4).
// Says so rather than opening a composer that goes nowhere.
function deskReplyToPosy() {
  if (typeof showToast === 'function') {
    showToast('Threaded replies to Posy land with the Queue pass — not wired yet.', 4000);
  }
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
async function deskDraft(signalId, voice, campaignId) {
  if (_deskDrafting.has(signalId)) return;
  _deskDrafting.add(signalId);
  renderDesk();
  try {
    const out = await _deskFetch('/api/desk/draft', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      // The campaign is what turns a report into an argument — `build_brief`
      // injects its thesis and tells the writer to argue it.
      body: JSON.stringify({ signal_id: signalId, voice, campaign_id: campaignId }),
    });
    if (typeof showToast === 'function') {
      const camp = _deskRunningCampaign();
      showToast(campaignId && camp
        ? `Posy is drafting a ${out.platform} post for "${camp.title}" — it lands in the Queue.`
        : `Posy is drafting a ${out.platform} post — it lands in the Queue.`);
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

// VOICES — and the cold start this exists to close.
//
// `voice_brief` is assembled out of the human's REAL edits to real drafts, and
// that is the differentiator the 2026-09-09 field scan could not find in any
// surveyed product. But a fresh voice has none: measured 2026-09-10, both
// starter voices had **0 rewrites**, so the brief handed the writer one line of
// register and nothing else. The loop only starts paying after you have already
// corrected it ten times, which is backwards.
//
// Ron's framing, and it is the design: "the way a user expresses himself in his
// requests is also part of who he is — is he paying more attention to details,
// more attention to actions, results." That evidence exists in volume before a
// single draft is written, in the messages he has already typed to his agents.
// So a voice can be SEEDED from it, then corrected by real edits, which outrank
// anything inferred.
//
// The rewrite count is shown per voice because it is the honest status: a voice
// with 0 is guessing, and the button that fixes it should sit next to the number
// that says so.
async function deskVoices() {
  let voices = [];
  try {
    voices = await _deskFetch('/api/desk/voices');
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not load the voices: ' + e.message, 4000);
    return;
  }

  const modalId = '__desk_voices';
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-fit';
  _clampModalSize(content, 560);
  content.innerHTML = `
    <div class="modal-header" style="padding:18px 24px 10px 28px">
      <div class="modal-window-controls" style="position:absolute;top:14px;right:16px;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:17px;font-weight:700;color:var(--text)">Voices</h2>
    </div>
    <div style="padding:6px 28px 22px;overflow-y:auto">
      <div class="hint" style="margin-bottom:14px">A voice learns from every edit you make to a
        draft. Until it has some, it is guessing — so you can seed it from how you already
        write to your own agents. Incognito sessions are never read.</div>
      ${voices.map(v => `
        <div class="desk-voice-row" data-voice="${esc(v.name)}">
          <div class="desk-voice-row-head">
            <span class="camp-voice-name">${esc(v.name)}</span>
            <button class="desk-platform desk-platform-btn" onclick="deskPlatformRules('${esc(v.platform || '')}')"
              title="This voice's platform rules — the brief the writer gets">${esc(v.platform || '')}</button>
            <button class="desk-platform desk-destination-btn" onclick="deskVoiceDestination('${esc(v.name)}')"
              title="Which account this voice publishes to — distinct from the platform">${esc(v.destination || 'default account')}</button>
            <span class="desk-voice-learned${(v.rewrites || []).length ? '' : ' cold'}">
              ${(v.rewrites || []).length} edit${(v.rewrites || []).length === 1 ? '' : 's'} learned
            </span>
            <span style="flex:1"></span>
            <button class="desk-seed-btn" onclick="deskSeedVoice('${esc(v.name)}')">
              Learn from how I write
            </button>
          </div>
          <div class="camp-voice-reg">${esc(v.register || 'No register yet.')}</div>
        </div>`).join('') || '<div class="desk-empty">No voices yet.</div>'}
      <div id="desk-voice-error" class="social-attr-warn" style="display:none"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}

// Seeding DISPATCHES rather than computing: what someone attends to — detail,
// action, results — is a judgement, the same reason triage is an agent and not a
// keyword score. The agent is briefed to describe a register and never to quote,
// because a transcript can contain anything that was pasted into it.
async function deskSeedVoice(name) {
  const err = document.getElementById('desk-voice-error');
  const show = (m) => { if (err) { err.textContent = m; err.style.display = 'block'; } };
  const btn = document.querySelector(`.desk-voice-row[data-voice="${name}"] .desk-seed-btn`);
  if (btn) { btn.disabled = true; btn.textContent = 'Reading…'; }
  try {
    const out = await _deskFetch(`/api/desk/voices/${encodeURIComponent(name)}/seed`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    if (!out.seeded) {
      // A real answer, not a failure: a fresh install has nothing to read yet.
      show(out.reason || 'Not enough of your own writing to characterise a voice yet.');
      if (btn) { btn.disabled = false; btn.textContent = 'Learn from how I write'; }
      return;
    }
    closeModalById('__desk_voices');
    if (typeof showToast === 'function') {
      showToast(`Reading ${out.samples} of your own messages to seed the "${name}" voice.`);
    }
  } catch (e) {
    show('Could not start it: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = 'Learn from how I write'; }
  }
}

// VOICE DESTINATION — which account a voice publishes to, distinct from the
// platform. Two voices can share a platform (two LinkedIn voices, say) and
// still need different destinations, so this cannot be folded into the
// platform badge next to it. Empty means "the platform's default account" —
// deliberately not filled in here: an account name/handle/URL is Ron's to
// type, never ours to seed (same rule as the platform-rules text below).
async function deskVoiceDestination(name) {
  let v = null;
  try {
    v = await _deskFetch(`/api/desk/voices/${encodeURIComponent(name)}`);
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not load the voice: ' + e.message, 4000);
    return;
  }

  const modalId = '__desk_destination_' + name;
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-fit';
  _clampModalSize(content, 460);
  content.innerHTML = `
    <div class="modal-header" style="padding:18px 24px 10px 28px">
      <div class="modal-window-controls" style="position:absolute;top:14px;right:16px;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:17px;font-weight:700;color:var(--text)">${esc(name)}'s destination</h2>
    </div>
    <div style="padding:6px 28px 22px;overflow-y:auto">
      <div class="hint" style="margin-bottom:14px">Which account on ${esc(v.platform || 'the platform')}
        this voice publishes to. Leave blank to mean the platform's default account.</div>
      <div class="form-group">
        <label>Destination</label>
        <input type="text" id="dest-value" value="${esc(v.destination || '')}"
          placeholder="e.g. a profile URL or account name">
      </div>
      <div id="dest-error" class="social-attr-warn" style="display:none"></div>
      <button class="btn-add" style="width:100%;margin-top:4px" onclick="deskSaveVoiceDestination('${esc(name)}')">Save</button>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}

async function deskSaveVoiceDestination(name) {
  const modalId = '__desk_destination_' + name;
  const err = document.getElementById('dest-error');
  const show = (m) => { if (err) { err.textContent = m; err.style.display = 'block'; } };
  const destination = ((document.getElementById('dest-value') || {}).value || '').trim();

  try {
    await _deskFetch(`/api/desk/voices/${encodeURIComponent(name)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destination }),
    });
  } catch (e) {
    return show('Could not save: ' + e.message);
  }
  closeModalById(modalId);
  closeModalById('__desk_voices');
  deskVoices();
  if (typeof showToast === 'function') showToast(`Saved ${name}'s destination.`);
}

// PLATFORM RULES — the brief a writer gets per platform, and what closes the
// bug where every platform but x/linkedin got an empty string.
//
// `desk_brief.build_brief` used to look these up in a hardcoded two-key dict
// (mc/desk_brief.py's old PLATFORM_NOTES). Ron's own drafts on other platforms
// — an 837-char facebook post, a 2236-char discord post — were briefed with
// nothing, because there was nowhere to set the rule. This modal is that
// somewhere: reached from the platform badge next to each voice, since a voice
// IS a platform (`platform_for`), so "which rules apply to what I'm about to
// draft" is one click from the voice that writes it.
async function deskPlatformRules(platform) {
  if (!platform) return;
  let rules = null;
  try {
    rules = await _deskFetch(`/api/desk/platforms/${encodeURIComponent(platform)}`);
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not load platform rules: ' + e.message, 4000);
    return;
  }

  const modalId = '__desk_platform_' + platform;
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-fit';
  _clampModalSize(content, 480);
  content.innerHTML = `
    <div class="modal-header" style="padding:18px 24px 10px 28px">
      <div class="modal-window-controls" style="position:absolute;top:14px;right:16px;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:17px;font-weight:700;color:var(--text)">${esc(platform)} rules</h2>
    </div>
    <div style="padding:6px 28px 22px;overflow-y:auto">
      <div class="hint" style="margin-bottom:14px">Handed to the writer verbatim before every draft
        on this platform. ${rules.configured ? ''
          : 'No rules are set yet — until you save some, the brief tells the writer there are none and to keep it short.'}</div>
      <div class="form-group">
        <label>Character limit <span style="text-transform:none;font-weight:400">(optional)</span></label>
        <input type="number" id="plat-limit" min="1" value="${rules.char_limit ? esc(String(rules.char_limit)) : ''}">
      </div>
      <div class="form-group">
        <label>Rules</label>
        <textarea class="rules-textarea" id="plat-text" rows="7"
          placeholder="Cost per post, what gets demoted, how long is too long...">${esc(rules.text || '')}</textarea>
      </div>
      <div id="plat-error" class="social-attr-warn" style="display:none"></div>
      <button class="btn-add" style="width:100%;margin-top:4px" onclick="deskSavePlatformRules('${esc(platform)}')">Save</button>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}

async function deskSavePlatformRules(platform) {
  const modalId = '__desk_platform_' + platform;
  const err = document.getElementById('plat-error');
  const show = (m) => { if (err) { err.textContent = m; err.style.display = 'block'; } };
  const text = (document.getElementById('plat-text') || {}).value || '';
  const limitRaw = ((document.getElementById('plat-limit') || {}).value || '').trim();
  const char_limit = limitRaw ? parseInt(limitRaw, 10) : null;

  try {
    await _deskFetch(`/api/desk/platforms/${encodeURIComponent(platform)}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, char_limit }),
    });
  } catch (e) {
    return show('Could not save: ' + e.message);
  }
  closeModalById(modalId);
  if (typeof showToast === 'function') showToast(`Saved rules for ${platform}.`);
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
    _loadDesk(); _loadDeskSignals(); _loadDeskProposals(); _hydrateAllSocial();
    _loadDeskShell(); _loadDeskLedgerWindow();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = DESK_MODAL_ID;
  const content = document.createElement('div');
  content.className = 'modal-content';
  // Wider than the old 940: the Board is a two-column grid (main + a fixed
  // 340px side column, UI brief §2), not a single stacked list.
  _clampModalSize(content, 1080);
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
  _loadDeskProposals();
  _hydrateAllSocial();   // the Queue surface reads the same hydrated projects
  _loadDeskShell();
  _loadDeskLedgerWindow();
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

  // Never repaint over a live cursor — see `deferRepaintWhileTyping` in
  // cross-social.js for the full reasoning and the two reports behind it.
  // Bridged through `window` because static/js/*.js are ES modules and a
  // top-level name in another file is not visible here.
  if (window.deferRepaintWhileTyping
      && window.deferRepaintWhileTyping(bodyEl, renderDesk)) return;

  const pending = _deskPendingCount();
  const labels = {
    board: 'Board', queue: 'Queue', calendar: 'Calendar', ledger: 'Ledger',
  };
  // The old "What's worth saying?" / "Read the projects" buttons are gone
  // (UI brief §1): harvest runs on the cadence now, or from the workflow's
  // own Run now — never a second trigger living here. `deskTriage`/
  // `deskHarvest` stay defined (and window-bridged) since nothing else in
  // this pass removed the routes they call; they are simply not offered as
  // header controls any more.
  tabsEl.innerHTML = DESK_TABS.map(t => `
    <button class="desk-tab${t === _deskTab ? ' active' : ''}" onclick="deskTab('${t}')">
      ${labels[t]}${t === 'queue' && pending ? ` <span class="desk-tab-badge">${pending}</span>` : ''}
    </button>`).join('') + `
    <span style="flex:1"></span>
    ${_deskCadenceChipHTML()}
    <button class="desk-voices-chip" onclick="deskVoices()"
      title="Your voices — and what each one has learned">Voices ▾</button>
    <button class="desk-accounts-chip" onclick="deskAccountsInfo()"
      title="Account inventory — not built yet">Accounts · —</button>`;

  if (_deskData._error) {
    bodyEl.innerHTML = `<div class="desk-empty">Could not reach the Desk: ${esc(_deskData._error)}</div>`;
    return;
  }
  if (_deskTab === 'board') bodyEl.innerHTML = _renderBoard();
  else if (_deskTab === 'queue') _renderQueueInto(bodyEl);
  else if (_deskTab === 'calendar') bodyEl.innerHTML = _renderCalendar();
  else bodyEl.innerHTML = _renderLedger();
}

// TRIAGE — Posy decides what is worth saying, you approve the SELECTION.
//
// The feed can hold hundreds of signals. Reading them all to find the two worth
// posting is exactly the work this surface exists to remove, and a keyword score
// cannot do it — it cannot tell a shipped feature from a chore containing the
// word "shipped", and it cannot explain itself. So Posy reads the feed and
// proposes; each proposal cites its signal and says WHY in her words.
async function deskTriage() {
  if (_deskTriaging) return;
  _deskTriaging = true;
  renderDesk();
  try {
    const out = await _deskFetch('/api/desk/triage', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    if (typeof showToast === 'function') {
      showToast(out.nothing_to_triage
        ? 'Nothing left to weigh — every signal is used or already ruled on.'
        : `Posy is weighing ${out.considering} signals. Her picks land here.`);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not start triage: ' + e.message, 4000);
  } finally {
    _deskTriaging = false;
    await _loadDeskProposals();
  }
}

// ACCEPTING USED TO LOOK LIKE NOTHING HAPPENED. Ron, 2026-09-10: "I clicked
// the what's worth saying button, selected one item for draft but I don't know
// if anything is happening, there is no indication." The draft WAS being
// written — the row simply left the list (it is no longer `proposed`), the only
// feedback was a toast that had already faded, and the Queue badge did not
// refresh because this function does not reload the Desk payload.
//
// Writing takes an agent a minute or two, so the gap between "I clicked" and
// "something appeared" is exactly where a user concludes the button is broken.
// Accepted proposals are now fetched too and shown as an in-flight strip until
// their draft lands in the Queue.
async function _loadDeskProposals() {
  try {
    const [proposed, accepted] = await Promise.all([
      _deskFetch('/api/desk/proposals'),
      _deskFetch('/api/desk/proposals?state=accepted').catch(() => []),
    ]);
    _deskProposals = proposed;
    // Only those whose post has not arrived yet — once the draft is on the
    // Queue the Queue badge is the honest indicator and this strip is noise.
    const drafted = new Set(_deskDraftedSignalIds());
    _deskWriting = (accepted || []).filter(
      p => !drafted.has((p.signal && p.signal.id) || p.signal_id));
  } catch (e) { _deskProposals = []; _deskWriting = []; }
  _deskSyncWritingPoll();
  if (openModals.has(DESK_MODAL_ID)) renderDesk();
}

// THE DESK HAS NO POLLING AT ALL — verified 2026-09-10, there is not one
// setInterval in this file. That is why Ron's draft "wasn't showing without the
// refresh", and it also means the in-flight strip above would have sat there
// forever claiming Posy was still writing, which is exactly the stuck state the
// commit that added it said it could not reach. It could.
//
// So the poll exists ONLY while something is genuinely in flight, and stops the
// moment nothing is: a Desk sitting idle costs nothing, and there is no timer to
// leak when the modal closes. 6s is the writing timescale (an agent takes a
// minute or two), not a UI-liveness timescale.
function _deskSyncWritingPoll() {
  // Also polls while a push-back's rework is in flight (Thread with Posy,
  // §3) — the successor draft that closes that loop is a normal queue item on
  // an ALREADY-HYDRATED project, so `_hydrateAllSocial` alone won't see it
  // land (it only fetches projects that aren't hydrated yet); each reworking
  // project needs its own `refreshProjectSocialQueue` to notice the new row.
  const reworking = _deskReworkingProjectIds();
  const shouldPoll = (_deskWriting.length > 0 || reworking.size > 0) && openModals.has(DESK_MODAL_ID);
  if (shouldPoll && !_deskWritingTimer) {
    _deskWritingTimer = setInterval(() => {
      if (!openModals.has(DESK_MODAL_ID)) { _deskStopWritingPoll(); return; }
      _loadDesk(); _hydrateAllSocial(); _loadDeskProposals();
      for (const pid of _deskReworkingProjectIds()) refreshProjectSocialQueue(pid);
    }, 6000);
  } else if (!shouldPoll && _deskWritingTimer) {
    _deskStopWritingPoll();
  }
}

function _deskStopWritingPoll() {
  if (_deskWritingTimer) { clearInterval(_deskWritingTimer); _deskWritingTimer = null; }
}

// Signal ids that already have a draft on the queue, hydrated or not.
function _deskDraftedSignalIds() {
  const out = [];
  for (const p of (projects || [])) {
    for (const i of (p.social_queue || [])) if (i.signal_id) out.push(i.signal_id);
  }
  return out;
}

// Accepting IS the instruction to write — one gate here ("worth saying?"), then
// the Queue's separate gate ("said right?"). Never merged.
async function deskDecideProposal(id, decision) {
  try {
    const out = await _deskFetch(`/api/desk/proposals/${encodeURIComponent(id)}/${decision}`,
                                 { method: 'POST' });
    if (typeof showToast === 'function') {
      showToast(decision === 'accept'
        ? `Posy is drafting the ${out.platform} post — it lands in the Queue.`
        : 'Dismissed. That signal will not be suggested again.');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not record that: ' + e.message, 4000);
  }
  // _loadDesk() too: without it the Queue tab badge does not move, so the
  // one durable signal that the click did something never appears.
  await Promise.all([_loadDesk(), _loadDeskProposals(), _loadDeskSignals()]);
}

// THIS IS WHAT A RUNNING CAMPAIGN ACTUALLY DOES. Until now it did nothing: the
// draft buttons were hardcoded to two voice names, and no draft ever carried a
// campaign_id, so the thesis never reached the writer even though `build_brief`
// has always accepted one. A campaign was decoration.
//
// Now: exactly one running campaign becomes the DEFAULT context for drafting.
// Its voices are the buttons, and each draft is briefed to argue its thesis
// rather than merely report the event. With none running (or several, which is
// ambiguous), the buttons fall back to every available voice and the draft is
// a standalone post.
function _deskRunningCampaign() {
  const running = (_deskData.campaigns || []).filter(c => c.state === 'running');
  return running.length === 1 ? running[0] : null;
}

function _deskDraftButtons(s) {
  const camp = _deskRunningCampaign();
  const names = camp
    ? (camp.voices || (camp.voice ? [camp.voice] : []))
    : (_deskData.voices || []);
  if (!names.length) {
    return `<span class="desk-signal-used" title="Create a voice first">no voice</span>`;
  }
  return names.map(v => {
    const label = esc(v).slice(0, 4);
    const why = camp
      ? `Draft for "${esc(camp.title)}" in the ${esc(v)} voice`
      : `Draft a standalone post in the ${esc(v)} voice`;
    return `<button class="desk-draft-btn" onclick="deskDraft('${esc(s.id)}','${esc(v)}'${
      camp ? `,'${esc(camp.id)}'` : ''})" title="${why}">${label}</button>`;
  }).join('');
}

function _deskSectionHTML(label, hint) {
  return `<div class="desk-section">
    <span class="desk-section-label">${label}</span>
    ${hint ? `<span class="desk-section-hint">${hint}</span>` : ''}
    <span class="desk-section-rule"></span>
  </div>`;
}

// A signal's raw `summary` is a commit subject or a backlog item's own text
// (mc/desk_harvest.py) — sometimes genuinely shouted (backlog items get
// written in caps). There is no per-signal "Posy rewrite" store yet (UI brief
// §2 wants one); this is a cheap, honest mitigation for the specific failure
// the brief calls out — sentence-case a mostly-uppercase string — not a
// substitute for one. Ordinary mixed-case text passes through untouched.
function _deskHumanizeTitle(text) {
  const t = (text || '').trim();
  const letters = t.replace(/[^a-zA-Z]/g, '');
  const upper = t.replace(/[^A-Z]/g, '');
  if (letters.length > 6 && upper.length / letters.length > 0.7) {
    const lower = t.toLowerCase();
    return lower.charAt(0).toUpperCase() + lower.slice(1);
  }
  return t;
}

// WAITING ON YOU — real numbers only. `n` matches the Queue tab's own badge
// count (_deskPendingCount) so the two never disagree. The age/voice
// breakdown needs the actual rows, which only exist once _hydrateAllSocial
// has fetched every project that HAS pending work — exactly the condition
// _deskPendingCount's own fallback branch already documents. `hydrated: false`
// means "still loading", not "empty": the caller renders a dash rather than a
// wrong zero.
function _deskWaitingStats() {
  const rows = [];
  for (const p of (typeof allProjects !== 'undefined' ? allProjects : [])) {
    if (p._socialQueueFull && Array.isArray(p.social_queue)) {
      for (const item of p.social_queue) {
        if (item.status === 'pending' || item.status === 'approved') rows.push(item);
      }
    }
  }
  const n = _deskPendingCount();
  const hydrated = rows.length >= n;
  let oldestDays = null;
  if (rows.length) {
    const oldest = rows.reduce((a, b) => ((a.created_at || '') < (b.created_at || '') ? a : b));
    oldestDays = Math.max(0, Math.floor((Date.now() - new Date(oldest.created_at).getTime()) / 864e5));
  }
  const byVoice = {};
  for (const r of rows) {
    const v = r.voice || 'unassigned';
    byVoice[v] = (byVoice[v] || 0) + 1;
  }
  return { n, hydrated, oldestDays, byVoice };
}

// LAST 30 DAYS — "went out" is real (the ledger). The other three the brief
// asks for have no store behind them yet, and are reported as such at the end
// of the build rather than guessed:
//   killed by you    — reject_social_queue_item only ever pushes BACK
//                       (needs_changes); a hard kill IS reachable via
//                       PATCH {status:"rejected"} but there is no cross-project
//                       aggregate of it, and _hydrateAllSocial only fetches
//                       projects that currently HAVE a pending item, so a
//                       project with zero pending but some killed drafts would
//                       silently undercount rather than error.
//   released edited   — the ledger record (mc/desk.py record_published) has
//                       no edited/released_unedited field; that is Queue-pass
//                       wiring (UI brief §3's soft lock), not built yet.
//   X API spend       — no per-post cost field on the ledger either.
function _deskLast30() {
  const cut = Date.now() - 30 * 864e5;
  const rows = _deskLedger30 || _deskData.recent_posts || [];
  const went = rows.filter(p => new Date(p.published_at || 0).getTime() >= cut).length;
  return { went };
}

// A campaign's progress bar (UI brief §2's 5-segment bar). "out" is real
// (ledger rows tagged with this campaign_id, mc/desk.py record_published).
// "planned" is real (camp.planned, the intended-posts list). "in queue" is
// NOT trackable: a queue item never records which campaign briefed it
// (mc/blueprints/desk_routes.py draft() only inlines the campaign into the
// BRIEF TEXT sent to Posy, nothing structured survives on the queue item) —
// rendered as a tooltipped dash rather than invented. Campaigns also have no
// end-date field, so "ends <date>" from the mockup is left off entirely
// rather than fabricated.
function _deskCampaignProgress(c) {
  const rows = _deskLedger30 || _deskData.recent_posts || [];
  const out = rows.filter(p => p.campaign_id === c.id).length;
  const planned = (c.planned || []).length;
  const total = Math.max(1, out + planned);
  return { out, planned, outPct: Math.round(out / total * 100) };
}

function _deskCampaignCardHTML(c) {
  const prog = _deskCampaignProgress(c);
  return `
    <div class="desk-campaign state-${esc(c.state)}">
      <div class="desk-campaign-head">
        <span class="desk-campaign-title">${esc(c.title)}</span>
        <span class="desk-state-chip">${esc(c.state)}</span>
        ${(c.voices || (c.voice ? [c.voice] : [])).map(v => `<span class="desk-voice-chip">${esc(v)}</span>`).join('')}
      </div>
      <div class="desk-campaign-thesis"><b>Thesis:</b> ${esc(c.thesis)}</div>
      ${c.agenda ? `<div class="desk-campaign-agenda"><b>Why now:</b> ${esc(c.agenda)}</div>` : ''}
      <div class="desk-campaign-progress">
        <span class="desk-progress-bar">
          <span class="desk-progress-seg desk-progress-out" style="width:${prog.outPct}%"></span>
          <span class="desk-progress-seg desk-progress-planned" style="width:${100 - prog.outPct}%"></span>
        </span>
        <span class="desk-progress-label">${prog.out} out · <span
          title="A draft doesn't record which campaign briefed it yet">— in queue</span> · ${prog.planned} planned</span>
      </div>
      <div class="desk-campaign-actions">
        ${c.state === 'proposed' ? `<button onclick="deskCampaignState('${esc(c.id)}','running')">Start it</button>` : ''}
        ${c.state === 'running' ? `<button onclick="deskCampaignState('${esc(c.id)}','paused')">Pause</button>` : ''}
        ${c.state === 'paused' ? `<button onclick="deskCampaignState('${esc(c.id)}','running')">Resume</button>` : ''}
        ${c.state !== 'done' && c.state !== 'dropped' ? `<button onclick="deskCampaignState('${esc(c.id)}','done')">Finish</button>` : ''}
      </div>
      <button class="desk-campaign-open" disabled title="No campaign detail screen yet">Open ›</button>
    </div>`;
}

// WORTH A STORY — Posy's top picks, by score. Reuses the exact meter/kind/
// draft-button machinery the raw feed row already has (_deskDraftButtons),
// restyled into a card per the mockup. The italic "angle" line is real only
// when a triage proposal already exists for this signal (proposal.why is
// authored by Posy); most signals have none yet, so the line is simply
// omitted rather than invented — there is no per-signal angle store.
function _deskStoryCardHTML(s) {
  const score = Math.max(0, Math.min(1, s.story_score || 0));
  const cold = score < 0.35;
  const title = _deskHumanizeTitle(s.summary || '');
  const prop = (_deskProposals || []).find(p => (p.signal_id || (p.signal && p.signal.id)) === s.id);
  return `
    <div class="desk-story${cold ? ' cold' : ''}">
      <span class="desk-meter" title="Story value ${score.toFixed(2)} — a suggestion, not a verdict">
        <span class="desk-meter-fill" style="width:${Math.round(score * 100)}%"></span>
      </span>
      <div class="desk-story-main">
        <div class="desk-story-title">${esc(title)}</div>
        <div class="desk-story-meta">${esc(s.kind)} · ${esc(s.project_id || '')} · ${esc((s.occurred_at || '').slice(0, 10))}</div>
        ${prop ? `<div class="desk-story-angle">"${esc(prop.why)}"</div>` : ''}
      </div>
      ${cold ? `
        <span class="desk-story-skip" title="No skip reason recorded yet">below the story bar</span>
        <button class="desk-draft-anyway" onclick="this.closest('.desk-story').classList.add('expanded')">Draft anyway ▾</button>` : ''}
      <span class="desk-draft-actions">${_deskDraftButtons(s)}</span>
    </div>`;
}

// The raw feed row — UNCHANGED from the old Board's "What happened" section.
// Kept verbatim (not restyled) because it now backs "See all N ›" (UI brief
// §2), a secondary view of the exact same 120-row list the story cards above
// are picked from, not a new surface.
function _deskFeedRowHTML(s) {
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
        : `<span class="desk-draft-actions">${_deskDraftButtons(s)}</span>`}
    </div>`;
}

function deskToggleSeeAll() {
  _deskSeeAllOpen = !_deskSeeAllOpen;
  renderDesk();
}

// POSY'S NOTE — a chat bubble (UI brief §2), not a generated mentor note: the
// spec files the mentor's actual period-focus generation under "still to
// build" (docs/THE_DESK_SPEC.md). This composes an honest status line from
// REAL numbers (pending count, feed size) rather than inventing the
// curriculum observation / "why now" opinion Posy has not actually made yet —
// including the quiet-week case the brief names explicitly ("never empty
// tiles").
function _deskPosyNoteHTML(pending, feedCount) {
  const avatar = (typeof window.avatarHTML === 'function') ? window.avatarHTML(_deskPosyAvatar, 36) : '';
  const now = new Date();
  const stamp = `this week's note · ${now.toLocaleDateString(undefined, { weekday: 'short' })} `
    + `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
  let body;
  if (pending > 0) {
    body = `${pending} draft${pending === 1 ? '' : 's'} waiting for you`
      + (feedCount ? `, out of ${feedCount} signal${feedCount === 1 ? '' : 's'} in the feed.` : '.');
  } else if (feedCount > 0) {
    body = `Nothing waiting on you right now. ${feedCount} signal${feedCount === 1 ? '' : 's'} `
      + `in the feed — worth a look under "Worth a story" below.`;
  } else {
    body = `Nothing worth saying this week — the feed is empty. Run the harvest `
      + `from the cadence workflow to pull in what shipped.`;
  }
  return `<div class="desk-note-row">
    <span class="desk-note-avatar">${avatar}</span>
    <div class="desk-note-body">
      <div class="desk-note-head">
        <span class="desk-note-name">Posy</span><span class="desk-note-stamp">${esc(stamp)}</span>
      </div>
      <div class="agent-output desk-note-output"><div class="agent-line desk-note-bubble">${esc(body)}</div></div>
      <div class="desk-note-actions">
        ${pending ? `<button class="btn-header-action" onclick="deskTab('queue')">Review the ${pending} draft${pending === 1 ? '' : 's'}</button>` : ''}
        <button class="btn-header-action" onclick="deskReplyToPosy()">Reply to Posy</button>
      </div>
    </div>
  </div>`;
}

function _deskSideColumnHTML() {
  const w = _deskWaitingStats();
  const last30 = _deskLast30();
  const voiceBits = Object.entries(w.byVoice).map(([v, n]) => `${n} for ${esc(v)}`).join(' · ');

  const waitingCard = `
    <div class="desk-side-card desk-waiting-card">
      <span class="desk-side-label">Waiting on you</span>
      <span class="desk-side-hero">${w.n || 'None'}</span>
      ${w.n ? `
        <span class="desk-side-sub">${w.hydrated && w.oldestDays !== null ? `oldest ${w.oldestDays}d` : 'loading…'}${voiceBits ? ' · ' + voiceBits : ''}</span>
        <button class="btn-header-action" onclick="deskTab('queue')">Review them →</button>`
        : `<span class="desk-side-sub">Nothing needs a decision.</span>`}
    </div>`;

  const last30Card = `
    <div class="desk-side-card">
      <span class="desk-side-label">Last 30 days</span>
      <div class="desk-stat-grid">
        <div class="desk-stat"><span class="desk-stat-val">${last30.went}</span><span class="desk-stat-lbl">went out</span></div>
        <div class="desk-stat"><span class="desk-stat-val" title="No cross-project aggregate for kills yet">—</span><span class="desk-stat-lbl">killed by you</span></div>
        <div class="desk-stat"><span class="desk-stat-val" title="Release does not record edited-vs-unedited yet">—</span><span class="desk-stat-lbl">released edited</span></div>
        <div class="desk-stat"><span class="desk-stat-val" title="The ledger has no per-post cost field yet">—</span><span class="desk-stat-lbl">X API spend</span></div>
      </div>
      <div class="desk-side-note">Edit rate is the detection defense. If it drops under 50% Posy will say so — once it is tracked.</div>
    </div>`;

  const curriculumCard = `
    <div class="desk-side-card desk-curriculum-card">
      <span class="desk-side-label">What you keep changing</span>
      <div class="desk-empty">Posy hasn't drawn any lessons from your edits yet — this fills in as she reads more of them.</div>
    </div>`;

  return waitingCard + last30Card + curriculumCard;
}

// BOARD — what is happening this month, and why (UI brief §2).
function _renderBoard() {
  const camps = _deskData.campaigns || [];
  const rawFeed = _deskSignals.length ? _deskSignals : (_deskData.hot_signals || []);
  const top = rawFeed.filter(s => !s.consumed_by).slice(0, 5);
  const pending = _deskPendingCount();

  const noteHTML = _deskPosyNoteHTML(pending, rawFeed.length);

  // Posy's in-flight writing status still needs to be visible even with the
  // manual triage trigger gone from the header (UI brief §1) — a campaign
  // fires it via the workflow now, but "nothing says a draft is coming" is
  // the same silent-feeling gap either way.
  const writingHTML = _deskWriting.length ? `
    <div class="desk-writing">
      <span class="desk-writing-dot"></span>
      Posy is writing ${_deskWriting.length} post${_deskWriting.length === 1 ? '' : 's'} —
      ${_deskWriting.map(p => `<span class="desk-voice-chip">${esc(p.voice)}</span>`).join(' ')}
      <span class="desk-writing-hint">it lands in the Queue when she is done.</span>
    </div>` : '';

  const campHTML = camps.length
    ? `<div class="desk-campaigns">${camps.map(_deskCampaignCardHTML).join('')}</div>`
    : `<div class="desk-empty" style="margin-bottom:22px">
         No campaigns yet. A campaign carries a <b>thesis</b> and a reason it is
         running now — that is what makes this a plan rather than a list of drafts.
       </div>`;

  const storyHTML = top.length
    ? `<div class="desk-stories">${top.map(_deskStoryCardHTML).join('')}</div>`
    : `<div class="desk-empty">Nothing in the feed yet — it fills in on the next cadence run.</div>`;

  const seeAllHTML = _deskSeeAllOpen
    ? `<div class="desk-feed">${rawFeed.map(_deskFeedRowHTML).join('')}</div>`
    : '';

  const mainCol = noteHTML
    + writingHTML
    + `<div class="desk-section">
         <span class="desk-section-label">Campaigns</span>
         ${camps.length ? '<span class="desk-section-hint">what we are arguing, and why now</span>' : ''}
         <span class="desk-section-rule"></span>
         <button class="desk-new-campaign" onclick="deskNewCampaign()"
           title="A campaign carries a thesis and a reason to run now">+ New campaign</button>
       </div>`
    + campHTML
    + `<div class="desk-section">
         <span class="desk-section-label">Worth a story</span>
         <span class="desk-section-hint">Posy's top picks from ${rawFeed.length} signals — most will never become a post</span>
         <span class="desk-section-rule"></span>
         <button class="desk-see-all" onclick="deskToggleSeeAll()">${_deskSeeAllOpen ? 'Hide ↑' : `See all ${rawFeed.length} ›`}</button>
       </div>`
    + storyHTML
    + seeAllHTML;

  return `<div class="desk-board-grid">
    <div class="desk-board-main">${mainCol}</div>
    <div class="desk-board-side">${_deskSideColumnHTML()}</div>
  </div>`;
}

// QUEUE (UI brief §3) — the only surface that ever demands anything of you.
// `300px list | review pane`, review pane itself `minmax(0,1fr) 300px`.
//
// Reads the SAME data cross-social.js hydrates (`allProjects[].social_queue`,
// filled by `_hydrateAllSocial`) rather than a second fetch — two copies of
// "what is pending across every project" would drift. What changes here is
// the PRESENTATION: a compact, selectable row on the left instead of a full
// accordion card, and a real review pane on the right instead of the row
// carrying every action inline. Mutations still go through the existing
// routes/functions (saveSocialBody, patchSocialItem, markSocialItemPosted,
// restoreSocialItem — all in social-actions.js) — this never forks the write
// path, only the read/selection layer on top of it.
const _DESK_QUEUE_STATUSES = new Set(['pending', 'approved', 'needs_changes']);
let _deskQueueSelectedId = null;   // `${projectId}:${itemId}`, or null
let _deskQueueFilters = { voice: 'all', project: 'all' };
let _deskQueueChecks = {};         // itemId -> {loading, body, repeat, error}
let _deskPlatformRulesCache = {};  // platform -> rules | null (in flight)

function _deskQueueAllItems() {
  const rows = [];
  for (const p of (typeof allProjects !== 'undefined' ? allProjects : [])) {
    if (p._socialQueueFull && Array.isArray(p.social_queue)) {
      for (const item of p.social_queue) rows.push({ p, item });
    }
  }
  return rows;
}

function _deskQueueRows() {
  let rows = _deskQueueAllItems().filter(r => _DESK_QUEUE_STATUSES.has(r.item.status));
  if (_deskQueueFilters.voice !== 'all') rows = rows.filter(r => (r.item.voice || '') === _deskQueueFilters.voice);
  if (_deskQueueFilters.project !== 'all') rows = rows.filter(r => r.item.project_id === _deskQueueFilters.project);
  rows.sort((a, b) => (a.item.created_at || '').localeCompare(b.item.created_at || ''));
  return rows;
}

function _deskVoicePlatform(name) {
  const v = (_deskAllVoices || []).find(x => x.name === name);
  return v ? (v.platform || '') : '';
}

function _deskVoicePillClass(platform) {
  if (platform === 'x' || platform === 'twitter') return 'desk-vpill-x';
  if (platform === 'linkedin') return 'desk-vpill-li';
  return 'desk-vpill-neutral';
}

function _deskFindSignal(id) {
  return (_deskSignals || []).find(s => s.id === id) || null;
}

function deskQueueSelect(projectId, itemId) {
  _deskQueueSelectedId = projectId + ':' + itemId;
  renderDesk();
}

function deskQueueSetFilter(kind, value) {
  _deskQueueFilters[kind] = value;
  renderDesk();
}

function _deskQueueRowHTML(row, selected) {
  const { p, item } = row;
  const voice = item.voice || '';
  const platform = _deskVoicePlatform(voice) || item.platform || '';
  const proj = (typeof allProjects !== 'undefined' ? allProjects : []).find(x => x.id === item.project_id);
  const age = (typeof window.timeAgoShort === 'function') ? window.timeAgoShort(item.created_at) : '';
  return `<div class="desk-qrow${selected ? ' selected' : ''}" data-item-id="${esc(item.id)}"
      onclick="deskQueueSelect('${esc(p.id)}','${esc(item.id)}')">
    <div class="desk-qrow-top">
      <span class="desk-vpill ${_deskVoicePillClass(platform)}">${esc(voice || platform || 'unspecified')}</span>
      <span class="desk-qrow-proj">${esc((proj && proj.name) || item.project_id || '')}</span>
      <span class="desk-qrow-age">${esc(age)}</span>
      ${!item.edited ? '<span class="desk-qrow-dot" title="Not yet edited"></span>' : ''}
    </div>
    <div class="desk-qrow-body">${esc(item.body || '')}</div>
  </div>`;
}

// Real pricing (docs/THE_DESK_SPEC.md, verified 2026-09-09 at
// docs.x.com/x-api/getting-started/pricing): $0.015/post, $0.20 if the body
// contains a URL. LinkedIn's Share-on-LinkedIn is free. V1 has exactly these
// two platforms — this mirrors the same numbers already written into
// mc.desk's platform-rules free text rather than a second, drifting copy.
// Anything else has no known cost and renders honestly as a dash.
const _DESK_URL_RE = /https?:\/\//i;
function _deskReleaseCost(platform, body) {
  const p = (platform || '').toLowerCase();
  if (p === 'x' || p === 'twitter') return _DESK_URL_RE.test(body || '') ? '$0.20' : '$0.015';
  if (p === 'linkedin') return 'free';
  return null;
}

async function _deskLoadPlatformRules(platform) {
  if (!platform || _deskPlatformRulesCache[platform] !== undefined) return;
  _deskPlatformRulesCache[platform] = null;
  try {
    _deskPlatformRulesCache[platform] = await _deskFetch(`/api/desk/platforms/${encodeURIComponent(platform)}`);
  } catch (e) {
    _deskPlatformRulesCache[platform] = {};
  }
  if (openModals.has(DESK_MODAL_ID)) renderDesk();
}

// The repeat-check hits the ledger's real similarity route (mc.desk.similar_published)
// rather than reimplementing it client-side — same reasoning as reusing the
// write path: one place that decides "have we said this before".
function _deskQueueRunChecks(item) {
  const id = item.id;
  const body = item.body || '';
  _deskQueueChecks[id] = { loading: true, body };
  _deskFetch('/api/desk/repeat-check', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  }).then(out => {
    _deskQueueChecks[id] = { loading: false, body, repeat: out.matches || [] };
    if (openModals.has(DESK_MODAL_ID)) renderDesk();
  }).catch(e => {
    _deskQueueChecks[id] = { loading: false, body, repeat: null, error: String(e.message || e) };
    if (openModals.has(DESK_MODAL_ID)) renderDesk();
  });
}

// CHECKS (UI brief §3) — computed before release. Every row is informational
// except the ledger one, and every row that has no store behind it renders
// an honest dash with a tooltip rather than a guess (ground rule: never
// invent a value, never hide the row).
function _deskChecksHTML(item) {
  const body = item.body || '';
  const cache = _deskQueueChecks[item.id];
  if (!cache || cache.body !== body) _deskQueueRunChecks(item);
  const fresh = cache && cache.body === body && !cache.loading;

  const repeatRow = !fresh
    ? `<div class="desk-check-row unknown"><span class="dcr-mark">…</span> Checking the ledger…</div>`
    : cache.repeat === null
      ? `<div class="desk-check-row unknown" title="${esc(cache.error || '')}"><span class="dcr-mark">—</span> Could not check the ledger</div>`
      : cache.repeat.length
        ? `<div class="desk-check-row warn"><span class="dcr-mark">!</span> Said before — ${Math.round(cache.repeat[0].overlap * 100)}% overlap with a ${esc(cache.repeat[0].platform || '')} post
             ${cache.repeat[0].url ? `(<a href="${esc(cache.repeat[0].url)}" target="_blank" rel="noopener">open it</a>)` : ''}</div>`
        : `<div class="desk-check-row ok"><span class="dcr-mark">✓</span> Not said before (ledger)</div>`;

  const signal = item.signal_id ? _deskFindSignal(item.signal_id) : null;
  const signalRow = !item.signal_id
    ? `<div class="desk-check-row unknown" title="This draft has no originating signal recorded"><span class="dcr-mark">—</span> Claim traces to a signal</div>`
    : signal
      ? `<div class="desk-check-row ok" title="${esc(signal.summary || '')}"><span class="dcr-mark">✓</span> Claim traces to <a onclick="deskTab('board')">${esc(_deskHumanizeTitle(signal.summary || '').slice(0, 44))}</a></div>`
      : `<div class="desk-check-row unknown" title="Not in the currently loaded feed"><span class="dcr-mark">—</span> Claim traces to signal ${esc(item.signal_id)}</div>`;

  const voice = item.voice ? (_deskAllVoices || []).find(v => v.name === item.voice) : null;
  let bannedRow;
  if (!item.voice || !voice) {
    bannedRow = `<div class="desk-check-row unknown" title="No voice recorded on this draft"><span class="dcr-mark">—</span> No banned words for this voice</div>`;
  } else {
    const hits = (voice.banned || []).filter(w => w && body.toLowerCase().includes(String(w).toLowerCase()));
    bannedRow = hits.length
      ? `<div class="desk-check-row warn"><span class="dcr-mark">!</span> Banned for ${esc(voice.name)}: ${hits.map(esc).join(', ')}</div>`
      : `<div class="desk-check-row ok"><span class="dcr-mark">✓</span> No banned words for ${esc(voice.name)}</div>`;
  }

  const assetRow = (item.media || []).length
    ? `<div class="desk-check-row ok"><span class="dcr-mark">✓</span> ${(item.media || []).length} asset(s) attached</div>`
    : `<div class="desk-check-row warn"><span class="dcr-mark">!</span> Asset missing — releases without media</div>`;

  return `<div class="desk-checks-card">
    <div class="desk-side-label" style="margin-bottom:8px">Checks</div>
    ${repeatRow}${signalRow}${bannedRow}${assetRow}
  </div>`;
}

// SAME STORY, OTHER VOICE — never cross-posts (spec: written twice, never
// copied). Reuses deskDraft() for "Ask Posy" rather than a second dispatch
// path.
function _deskOtherVoiceHTML(item) {
  if (!item.signal_id) {
    return `<div class="desk-other-voice-card" title="No originating signal — nothing to compare">
      Same story, other voice — no signal recorded</div>`;
  }
  const others = _deskQueueAllItems().filter(r =>
    r.item.signal_id === item.signal_id && r.item.voice && r.item.voice !== item.voice);
  if (others.length) {
    const o = others[0];
    return `<div class="desk-other-voice-card"><b>${esc(o.item.voice)}</b> already has a draft
      from this signal — <a onclick="deskQueueSelect('${esc(o.p.id)}','${esc(o.item.id)}')">open it ›</a></div>`;
  }
  const otherVoiceName = (_deskAllVoices || []).map(v => v.name).find(n => n !== item.voice);
  if (!otherVoiceName) {
    return `<div class="desk-other-voice-card">Same story, other voice — no other voice exists yet</div>`;
  }
  return `<div class="desk-other-voice-card"><b>${esc(otherVoiceName)}</b> has no draft from this
    signal yet — <button onclick="deskDraft('${esc(item.signal_id)}','${esc(otherVoiceName)}')">Ask Posy ›</button></div>`;
}

function _deskUpdateCharCounter(el) {
  const wrap = el.closest('.desk-post-preview');
  const counter = wrap && wrap.querySelector('.desk-char-counter');
  if (!counter) return;
  const len = el.innerText.length;
  const m = counter.textContent.match(/\/\s*(\d+)/);
  if (m) {
    const limit = parseInt(m[1], 10);
    counter.textContent = `${len} / ${limit}`;
    counter.classList.toggle('over', len > limit);
  } else {
    counter.textContent = `${len} chars`;
  }
}

// Autosave on blur -> update_social_queue_item -> desk.record_edit. THIS IS
// THE LEARNING LOOP (UI brief §3) — saveSocialBody (social-actions.js) is the
// exact function the old flat Queue used for the same contenteditable body,
// so this is not a second write path, just a desk-aware wrapper that also
// refreshes the soft-lock/char-counter/checks state the review pane shows.
async function deskQueueSaveBody(e, projectId, itemId) {
  await saveSocialBody(e, projectId, itemId);
  renderDesk();
}

async function deskQueueRelease(projectId, itemId) {
  const res = await fetch(API_BASE + `/api/project/${projectId}/social/queue/${itemId}/approve`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decided_by: 'user' }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (typeof showToast === 'function') showToast(data.error || 'Could not release this draft.', 4000);
    return;
  }
  // NOT A PUBLISH CALL. approve() only ever flips a status flag — Phase 1's
  // shape since it shipped, and unchanged here. Wiring the publishing office
  // is UI brief build order step 5, and needs Ron's go-ahead: it is
  // outward-facing. Said plainly so nobody reads "Release" as "posted".
  if (typeof showToast === 'function') {
    showToast('Recorded. Publishing is not wired yet — post it yourself, then use "Mark posted" to record the link.', 6000);
  }
  if (typeof refreshProjectSocialQueue === 'function') await refreshProjectSocialQueue(projectId);
  renderDesk();
}

// The override link records `released_unedited: true` on the SERVER, derived
// from the item's own `edited` flag rather than trusted client input — see
// approve_social_queue_item (project_routes.py), which sets it to
// `not item.get('edited')` regardless of what the request claims.
function deskQueueReleaseUnedited(projectId, itemId) {
  if (!confirm('Release this draft without ever editing it?\n\nThis is the single '
    + 'highest-risk artifact the system can produce — it will be flagged on the receipt.')) return;
  deskQueueRelease(projectId, itemId);
}

async function deskQueueKill(projectId, itemId) {
  if (!confirm('Kill this draft? This cannot be undone.')) return;
  await patchSocialItem(projectId, itemId, { status: 'rejected' });
  if (_deskQueueSelectedId === projectId + ':' + itemId) _deskQueueSelectedId = null;
  if (typeof showToast === 'function') showToast('Draft killed.');
  renderDesk();
}

// No per-draft schedule store exists yet (UI brief §3 lists it alongside
// Release, but nothing wires a draft to the scheduler) — honest placeholder,
// same pattern as deskAccountsInfo/deskReplyToPosy above.
function deskQueueSchedule() {
  if (typeof showToast === 'function') {
    showToast('Scheduling a release is not wired yet — there is no per-draft schedule store.', 4000);
  }
}

async function deskQueueAttachAsset(projectId, itemId) {
  const path = (prompt('Path to the media file (under data/media/):') || '').trim();
  if (!path) return;
  await patchSocialItem(projectId, itemId, { media: [path] });
  renderDesk();
}

function deskQueueToggleInsert(e) {
  e.stopPropagation();
  const wrap = e.target.closest('.desk-insert-wrap');
  const menu = wrap && wrap.querySelector('.desk-insert-menu');
  if (!menu) return;
  const opening = menu.style.display !== 'block';
  document.querySelectorAll('.desk-insert-menu').forEach(m => { m.style.display = 'none'; });
  menu.style.display = opening ? 'block' : 'none';
}

function _deskInsertIntoBody(text) {
  const bodyEl = document.querySelector('.desk-post-body');
  if (!bodyEl) return null;
  const current = bodyEl.innerText || '';
  bodyEl.innerText = current + (current && !current.endsWith(' ') ? ' ' : '') + text;
  return bodyEl;
}

// A tracked link always inserts the `?ref=desk-<item_id>` form the Ledger's
// "to repo" count reads (UI brief §4) — appended automatically so it is never
// left off by hand.
async function deskQueueInsertLink(projectId, itemId) {
  const url = (prompt('Destination URL (a tracking tag is appended automatically):') || '').trim();
  if (!url) return;
  const sep = url.includes('?') ? '&' : '?';
  const bodyEl = _deskInsertIntoBody(`${url}${sep}ref=desk-${itemId}`);
  document.querySelectorAll('.desk-insert-menu').forEach(m => { m.style.display = 'none'; });
  if (bodyEl) await deskQueueSaveBody({ target: bodyEl }, projectId, itemId);
}

async function deskQueueInsertMention(projectId, itemId) {
  const handle = (prompt('@handle to mention:') || '').trim();
  if (!handle) return;
  const bodyEl = _deskInsertIntoBody(handle.startsWith('@') ? handle : '@' + handle);
  document.querySelectorAll('.desk-insert-menu').forEach(m => { m.style.display = 'none'; });
  if (bodyEl) await deskQueueSaveBody({ target: bodyEl }, projectId, itemId);
}

function _deskReviewPaneHTML(row) {
  const { p, item } = row;
  const voice = item.voice || '';
  const platform = _deskVoicePlatform(voice) || item.platform || '';
  const pillClass = _deskVoicePillClass(platform);

  const signal = item.signal_id ? _deskFindSignal(item.signal_id) : null;
  const signalHTML = !item.signal_id
    ? `<span class="desk-review-nosignal" title="No originating signal recorded on this draft">from signal — none recorded</span>`
    : signal
      ? `<a class="desk-review-signal" onclick="deskTab('board')" title="${esc(signal.summary || '')}">from signal ${esc(_deskHumanizeTitle(signal.summary || '').slice(0, 44))} ›</a>`
      : `<span class="desk-review-nosignal" title="Not in the currently loaded feed">from signal ${esc(item.signal_id)}</span>`;

  const editedHTML = item.edited
    ? `<span class="desk-review-edited green">✓ edited · ${item.edited_lines || 1} line${(item.edited_lines || 1) === 1 ? '' : 's'}</span>`
    : `<span class="desk-review-edited red">● not yet edited</span>`;

  if (_deskPlatformRulesCache[platform] === undefined) _deskLoadPlatformRules(platform);
  const rules = _deskPlatformRulesCache[platform];
  const len = (item.body || '').length;
  const charLimit = rules && rules.char_limit;
  const counterHTML = charLimit
    ? `<span class="desk-char-counter${len > charLimit ? ' over' : ''}">${len} / ${charLimit}</span>`
    : `<span class="desk-char-counter" title="No character limit on file for this platform">${len} chars</span>`;

  const assetHTML = (item.media || []).length ? '' : `
    <div class="desk-needs-asset">
      <span>Needs asset — no media attached yet.</span>
      <button onclick="deskQueueAttachAsset('${esc(p.id)}','${esc(item.id)}')">Attach ›</button>
    </div>`;

  const cost = _deskReleaseCost(platform, item.body);
  const platLabel = platform === 'x' ? 'X' : platform === 'linkedin' ? 'LinkedIn' : (platform || 'the platform');
  const priced = `Release to ${platLabel}${cost ? ' · ' + cost : ' · —'}`;
  const locked = !item.edited;

  let railHTML;
  if (item.status === 'needs_changes') {
    railHTML = `
      <div class="desk-checks-card" style="color:var(--amber-text)">
        <b>Pushed back</b> — ${esc(item.note || 'no note recorded')}
      </div>
      <button class="desk-schedule-btn" onclick="restoreSocialItem(event,'${esc(p.id)}','${esc(item.id)}')">Restore to pending</button>`;
  } else if (item.status === 'approved') {
    railHTML = `
      <div class="desk-checks-card" style="color:var(--green-text)">
        <b>Released</b> — recorded${item.released_unedited ? ' (unedited)' : ''}. Publishing is not wired
        yet: post it yourself, then record the link below.
      </div>
      <button class="desk-schedule-btn" onclick="markSocialItemPosted(event,'${esc(p.id)}','${esc(item.id)}')">Mark posted ›</button>
      <button class="desk-kill-btn" onclick="deskQueueKill('${esc(p.id)}','${esc(item.id)}')">Kill this draft</button>`;
  } else {
    railHTML = `
      <button class="desk-release-btn${locked ? ' locked' : ''}" ${locked ? 'disabled' : ''}
        onclick="deskQueueRelease('${esc(p.id)}','${esc(item.id)}')"
        title="${locked ? 'Edit the draft at least once to unlock' : ''}">${esc(priced)}</button>
      <div class="desk-release-unlock">Release unlocks after your first edit — ${
        locked ? `or <a onclick="deskQueueReleaseUnedited('${esc(p.id)}','${esc(item.id)}')">release unedited anyway</a>`
               : 'already unlocked.'}</div>
      <button class="desk-schedule-btn" onclick="deskQueueSchedule()">Schedule ▾</button>
      <button class="desk-kill-btn" onclick="deskQueueKill('${esc(p.id)}','${esc(item.id)}')">Kill this draft</button>
      ${_deskChecksHTML(item)}
      ${_deskOtherVoiceHTML(item)}`;
  }

  return `
    <div class="desk-review-left">
      <div class="desk-review-meta">
        <span class="desk-vpill ${pillClass}">${esc(voice || platform || 'unspecified')}</span>
        ${signalHTML}
        ${editedHTML}
      </div>
      <div class="desk-post-preview">
        <div class="desk-post-preview-head">
          ${(typeof window.avatarHTML === 'function') ? window.avatarHTML('', 28) : ''}
          <span class="desk-post-voice">${esc(voice || 'unspecified')}</span>
          <span class="desk-post-asis">as it will appear</span>
          ${counterHTML}
        </div>
        <div class="desk-post-body" contenteditable="true" spellcheck="true"
          oninput="_deskUpdateCharCounter(this)"
          onblur="deskQueueSaveBody(event,'${esc(p.id)}','${esc(item.id)}')">${esc(item.body || '')}</div>
        ${assetHTML}
      </div>
      <div class="desk-post-footer">
        <span>Click to edit in place. Every edit teaches the ${esc(voice || 'unspecified')} voice.</span>
        <div class="desk-insert-wrap">
          <button class="desk-insert-btn" onclick="deskQueueToggleInsert(event)">Insert ▾</button>
          <div class="desk-insert-menu" style="display:none">
            <button onclick="deskQueueInsertLink('${esc(p.id)}','${esc(item.id)}')">Tracked link</button>
            <button onclick="deskQueueInsertMention('${esc(p.id)}','${esc(item.id)}')">Mention</button>
            <button disabled title="This campaign has no canonical URL stored yet">Campaign canonical URL</button>
          </div>
        </div>
      </div>
      ${_deskThreadHTML(row)}
    </div>
    <div class="desk-review-rail">${railHTML}</div>`;
}

// ── Thread with Posy (UI brief §3, build order step 4) ──────────────────────
//
// REUSE, NOT A FORK: bubbles are `.agent-output`/`.agent-line`/`.agent-line-
// prompt` verbatim (same reuse the Board's note bubble already established,
// app.css:8198+); the typing indicator is `.typing-indicator`/`.act-dot`
// verbatim (conversation.js's own §4 component — only a label span is added
// next to the dots, because the shared component has no slot for custom
// text); quick-reply chips are `.agent-question-chip` verbatim (the
// conversation view's own chip, used elsewhere for AskUserQuestion); the
// composer is `.agent-input-row`/`.agent-task-input`/`.btn-dispatch` verbatim
// plus `micBtnHTML()` (composer-extras.js) for the mic button. Sending goes
// through the EXISTING `/reject` route (mc/blueprints/project_routes.py
// reject_social_queue_item -> desk_routes.dispatch_rework) — the same
// pushback+rework mechanism cross-social.js's old push-back modal already
// used, not a second write path or a new dispatch.
//
// THE DATA MODEL IS A CHAIN, NOT A SINGLE THREAD, AND THAT IS LOAD-BEARING TO
// UNDERSTAND BEFORE TOUCHING THIS: a push-back does not revise this queue item
// in place. It stores the note on THIS item (status -> needs_changes) and
// dispatches a rework that lands as a BRAND NEW item carrying `reworked_from`
// = this item's id (mc/desk_brief.py build_rework_brief, mc/blueprints/
// desk_routes.py dispatch_rework). So "the thread" is reconstructed by
// walking `reworked_from` backward across however many rounds have happened,
// not read off one item's history. This means the UI brief's "her revision
// streams into the POST PREVIEW above" is not literally what happens — the
// revision is a NEW queue row, not an in-place body update — and reworking
// that backend behavior to match is out of scope for a surfaces-only pass
// (it is a shipped, tested mechanism: tests/test_social_queue.py, the
// 2026-09-11 "close the push-back loop" comment in project_routes.py). The
// UI below makes the chain legible instead: a "revision is ready -> open it"
// link when a successor has landed, and a "superseded" notice (composer
// hidden) when the item being viewed is no longer the live one.
function _deskThreadChain(item) {
  const all = _deskQueueAllItems().map(r => r.item);
  const chain = [item];
  let cur = item;
  while (cur.reworked_from) {
    const prev = all.find(i => i.id === cur.reworked_from);
    if (!prev || chain.includes(prev)) break;
    chain.unshift(prev);
    cur = prev;
  }
  return chain;
}

function _deskThreadSuccessor(item) {
  return _deskQueueAllItems().find(r => r.item.reworked_from === item.id) || null;
}

// A rework is "in flight" once dispatched and until its successor lands. No
// timeout — same convention as _deskWriting's own poll (proposals), which
// also never gives up waiting rather than guess at a failure.
function _deskReworkPending(item) {
  return !!item.rework_session_id && !_deskThreadSuccessor(item);
}

function _deskReworkingProjectIds() {
  const ids = new Set();
  for (const r of _deskQueueAllItems()) {
    if (_deskReworkPending(r.item)) ids.add(r.p.id);
  }
  return ids;
}

function _deskThreadHeaderHTML() {
  const avatar = (typeof window.avatarHTML === 'function') ? window.avatarHTML(_deskPosyAvatar, 24) : '';
  return `<div class="desk-thread-head">${avatar}<span class="desk-thread-name">Posy</span></div>`;
}

// Interleaves each round's teaching (why this draft) with the note that
// pushed it into the NEXT round, in chain order — which is what reconstructs
// a readable conversation out of what is actually several linked queue items.
// Ground rule: a round with nothing to say renders no bubble, never faked copy.
function _deskThreadBubblesHTML(chain) {
  const parts = [];
  for (const round of chain) {
    if (round.teaching) {
      parts.push(`<div class="agent-line">${esc(round.teaching)}</div>`);
    }
    if (round.note) {
      parts.push(`<div class="agent-line agent-line-prompt">${esc(round.note)}</div>`);
    }
  }
  return parts.join('');
}

const DESK_QUICK_REPLY_CHIPS = ['Shorter', 'Less technical', 'Add the cost', 'Try LinkedIn voice'];

function deskThreadFillChip(itemId, text) {
  const ta = document.getElementById('desk-thread-input-' + itemId);
  if (!ta) return;
  ta.value = text;
  ta.focus();
}

// FIXED SET, NOT LEARNED (UI brief §3's fallback: "ship a sensible fixed set
// and say so — do not invent a learning mechanism this pass"). There is no
// per-operator pushback-history store yet to learn from.
function _deskThreadChipsHTML(item) {
  return `<div class="desk-thread-chips agent-question-chips">${
    DESK_QUICK_REPLY_CHIPS.map(c =>
      `<button type="button" class="agent-question-chip" onclick="deskThreadFillChip('${esc(item.id)}','${esc(c)}')">${esc(c)}</button>`
    ).join('')}</div>`;
}

function _deskThreadComposerHTML(item, disabled) {
  const taId = 'desk-thread-input-' + item.id;
  return `
    ${_deskThreadChipsHTML(item)}
    <div class="agent-input-row">
      <textarea class="agent-task-input" id="${taId}" rows="1" ${disabled ? 'disabled' : ''}
        placeholder="${disabled ? "Posy is already revising this draft…" : 'Push back to Posy…'}"
        onkeydown="handleInputEnter(event,()=>deskThreadSendPushback('${esc(item.project_id)}','${esc(item.id)}'),null)"
      ></textarea>
      ${(!disabled && typeof window.micBtnHTML === 'function') ? window.micBtnHTML(taId) : ''}
      <button class="btn-dispatch" id="desk-thread-send-${esc(item.id)}" ${disabled ? 'disabled' : ''}
        onclick="deskThreadSendPushback('${esc(item.project_id)}','${esc(item.id)}')">Send</button>
    </div>`;
}

// Posts to the SAME /reject route the old push-back modal used
// (submitPushBack, social-actions.js) — this is not a second write path, only
// a second trigger for it. dispatch_rework (server) is what turns "posts a
// pushback note" into "and dispatches Posy to revise" — both already true of
// that route before this pass touched anything.
async function deskThreadSendPushback(projectId, itemId) {
  const ta = document.getElementById('desk-thread-input-' + itemId);
  const note = ta ? ta.value.trim() : '';
  if (!note) return;
  const btn = document.getElementById('desk-thread-send-' + itemId);
  if (btn) btn.disabled = true;
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/social/queue/${itemId}/reject`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ note, decided_by: 'user' }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (typeof showToast === 'function') showToast(data.error || 'Could not push back.', 4000);
      if (btn) btn.disabled = false;
      return;
    }
    await refreshProjectSocialQueue(projectId);
    _deskSyncWritingPoll();
    renderDesk();
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not push back: ' + e.message, 4000);
    if (btn) btn.disabled = false;
  }
}

function _deskThreadHTML(row) {
  const item = row.item;
  const chain = _deskThreadChain(item);
  const bubbles = _deskThreadBubblesHTML(chain);
  const successor = _deskThreadSuccessor(item);
  const reworkPending = _deskReworkPending(item);

  let tailHTML = '';
  if (reworkPending) {
    tailHTML = `<div class="agent-line typing-indicator desk-thread-typing" data-act="writing">
      <span class="act-dot"></span><span class="act-dot"></span><span class="act-dot"></span>
      <span class="desk-thread-typing-label">Posy is revising the draft&hellip;</span>
    </div>`;
  } else if (successor) {
    tailHTML = `<div class="agent-line desk-thread-ready">Her revision is ready &mdash; <a
      onclick="deskQueueSelect('${esc(successor.p.id)}','${esc(successor.item.id)}')">open it &rsaquo;</a></div>`;
  } else if (item.status === 'needs_changes') {
    tailHTML = `<div class="agent-line desk-thread-note">No rework is in flight for this push-back &mdash;
      edit the draft yourself, or push back again.</div>`;
  }

  const bodyHTML = (bubbles || tailHTML)
    ? `<div class="agent-output desk-thread-output">${bubbles}${tailHTML}</div>`
    : `<div class="desk-thread-empty">Nothing from Posy on this draft yet.</div>`;

  const canPushBack = !successor && (item.status === 'pending' || item.status === 'needs_changes');
  const footerHTML = successor
    ? `<div class="desk-thread-superseded">This draft was superseded by a revision &mdash; <a
        onclick="deskQueueSelect('${esc(successor.p.id)}','${esc(successor.item.id)}')">open it &rsaquo;</a> to keep pushing back.</div>`
    : canPushBack ? _deskThreadComposerHTML(item, reworkPending) : '';

  return `<div class="desk-thread">${_deskThreadHeaderHTML()}${bodyHTML}${footerHTML}</div>`;
}

function _renderQueueInto(bodyEl) {
  const rows = _deskQueueRows();
  const key = (r) => r.p.id + ':' + r.item.id;
  if (!_deskQueueSelectedId || !rows.some(r => key(r) === _deskQueueSelectedId)) {
    _deskQueueSelectedId = rows.length ? key(rows[0]) : null;
  }

  const voiceOpts = ['<option value="all">All voices</option>'].concat(
    (_deskAllVoices || []).map(v =>
      `<option value="${esc(v.name)}"${_deskQueueFilters.voice === v.name ? ' selected' : ''}>${esc(v.name)}</option>`));
  const projIds = Array.from(new Set(_deskQueueAllItems()
    .filter(r => _DESK_QUEUE_STATUSES.has(r.item.status)).map(r => r.item.project_id)));
  const projOpts = ['<option value="all">All projects</option>'].concat(
    projIds.map(pid => {
      const proj = (typeof allProjects !== 'undefined' ? allProjects : []).find(x => x.id === pid);
      return `<option value="${esc(pid)}"${_deskQueueFilters.project === pid ? ' selected' : ''}>${esc((proj && proj.name) || pid)}</option>`;
    }));

  const listHTML = rows.length
    ? rows.map(r => _deskQueueRowHTML(r, key(r) === _deskQueueSelectedId)).join('')
    : `<div class="desk-queue-empty">Nothing waiting on you.</div>`;

  const selectedRow = rows.find(r => key(r) === _deskQueueSelectedId);
  const reviewHTML = selectedRow
    ? _deskReviewPaneHTML(selectedRow)
    : `<div class="desk-review-empty">Select a draft to review it.</div>`;

  bodyEl.innerHTML = `<div class="desk-queue-grid">
    <div class="desk-queue-list">
      <div class="desk-queue-list-head">
        <span class="desk-queue-count">${rows.length} DRAFT${rows.length === 1 ? '' : 'S'} · OLDEST FIRST</span>
        <div class="desk-queue-filters">
          <select class="desk-queue-filter" onchange="deskQueueSetFilter('voice', this.value)">${voiceOpts.join('')}</select>
          <select class="desk-queue-filter" onchange="deskQueueSetFilter('project', this.value)">${projOpts.join('')}</select>
        </div>
      </div>
      <div class="desk-queue-rows">${listHTML}</div>
    </div>
    <div class="desk-queue-review">${reviewHTML}</div>
  </div>`;
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
window.deskTriage = deskTriage;
window.deskDecideProposal = deskDecideProposal;
window.deskNewCampaign = deskNewCampaign;
window.deskVoices = deskVoices;
window.deskSeedVoice = deskSeedVoice;
window.deskPlatformRules = deskPlatformRules;
window.deskSavePlatformRules = deskSavePlatformRules;
window.deskVoiceDestination = deskVoiceDestination;
window.deskSaveVoiceDestination = deskSaveVoiceDestination;
window.deskSubmitCampaign = deskSubmitCampaign;
window.deskCampaignState = deskCampaignState;
window.renderDesk = renderDesk;
window.deskAccountsInfo = deskAccountsInfo;
window.deskReplyToPosy = deskReplyToPosy;
window.deskToggleSeeAll = deskToggleSeeAll;
window._deskOpenCadenceWorkflow = _deskOpenCadenceWorkflow;
window.deskQueueSelect = deskQueueSelect;
window.deskQueueSetFilter = deskQueueSetFilter;
window.deskQueueSaveBody = deskQueueSaveBody;
window.deskQueueRelease = deskQueueRelease;
window.deskQueueReleaseUnedited = deskQueueReleaseUnedited;
window.deskQueueKill = deskQueueKill;
window.deskQueueSchedule = deskQueueSchedule;
window.deskQueueAttachAsset = deskQueueAttachAsset;
window.deskQueueToggleInsert = deskQueueToggleInsert;
window.deskQueueInsertLink = deskQueueInsertLink;
window.deskQueueInsertMention = deskQueueInsertMention;
window._deskUpdateCharCounter = _deskUpdateCharCounter;
window.deskThreadFillChip = deskThreadFillChip;
window.deskThreadSendPushback = deskThreadSendPushback;
