// ── Learn practice store (browser only) ──────────────────────────────────────
// docs/TUTORIALS_SPEC.md, DECISIONS 2 and 3. Practice lives entirely in this
// tab: while a practice run is active, the page's own fetch() and EventSource
// are routed here. The real renderers (Floor, project tile, chat, Channel)
// draw a fixture project, a fixture session and a fixture Bench type exactly
// as they would draw server data, and the one write the pilot teaches (hire)
// lands in a roster kept in localStorage under `learn.*`.
//
// Nothing practice-related is ever created on the server. That is the safety
// property: there is no practice project for an agent, the scheduler or a
// publish path to run against. Anything that would leave the practice context
// for a real write (dispatch, send, publish, rename, settings) is refused here
// with a visible "Practice only" message instead of being sent.
//
// Two lessons share this store: the Floor (roster) and Workflows (practice
// workflow records). Both are scoped to the run id, so a Replay starts empty.
//
// Exposed as window.LearnPractice, consumed by learn.js. Registered once at
// module load: the wrapper stays installed and routes only while `active`,
// so there is no uninstall path that could clobber another fetch wrapper.

const PRACTICE_PID = 'learn_practice';
const PRACTICE_SID = 'learn-pip';
const PRACTICE_CSID = 'learn-pip-transcript';
const GUIDE_REF = 'project:guide';
const LS_ROSTER = 'learn.practice.roster';
const LS_WORKFLOWS = 'learn.practice.workflows';
const PRACTICE_TRANSCRIPT = 'This is a practice conversation. No agent is running.';
const PRACTICE_REFUSAL = 'Practice only. This would reach your real projects, so it was not sent.';

let _active = false;
let _gen = 0;            // bumped on enter and leave; a stale response is dropped
let _runId = '';
let _seq = 0;            // monotonic id for committed practice writes
const _refused = [];     // [{method, path}] — what was refused, for the smoke
const _unhandled = [];   // practice-project reads nobody answered
let _failHire = '';      // test seam: the next hire answers with this error
let _failSave = '';      // test seam: the next workflow save answers with this error

function _loadRoster() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_ROSTER) || 'null');
    if (s && Array.isArray(s.roster)) return s;
  } catch (e) { /* fall through to an empty roster */ }
  return { runId: '', roster: [] };
}
function _saveRoster(state) {
  try { localStorage.setItem(LS_ROSTER, JSON.stringify(state)); }
  catch (e) { console.warn('[learn] could not save the practice roster:', e); }
}

function _roster() {
  const s = _loadRoster();
  return s.runId === _runId ? s.roster : [];
}

// Practice workflows: the records the Workflows lesson saves. Same scoping as
// the roster: tied to the run id, so a Replay starts with none.
function _loadWorkflows() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_WORKFLOWS) || 'null');
    if (s && Array.isArray(s.workflows)) return s;
  } catch (e) { /* fall through to an empty list */ }
  return { runId: '', workflows: [] };
}
function _saveWorkflows(state) {
  try { localStorage.setItem(LS_WORKFLOWS, JSON.stringify(state)); }
  catch (e) { console.warn('[learn] could not save the practice workflows:', e); }
}
function _workflows() {
  const s = _loadWorkflows();
  return s.runId === _runId ? s.workflows : [];
}

function _pipFigure() {
  return {
    session_id: PRACTICE_SID, claude_session_id: PRACTICE_CSID, state: 'idle',
    reason: null, activity: '', task: 'Practice conversation. No agent is running.',
    character: null, name: 'Pip', name_from: 'user', avatar: '🐣',
    provider: '', model: '', model_from: '', started_at: '', age: 'practice',
    trigger_type: 'manual', hivemind_id: '', subagents: [],
  };
}

function _guideType() {
  const hired = _roster().some((r) => r.character === GUIDE_REF && !r.removed_at);
  return {
    scope: 'project', project_id: PRACTICE_PID, project_name: 'Learn practice',
    name: 'guide', display: 'Guide', avatar: '🧭',
    description: 'A practice type. Hiring it adds it to Learn practice and starts nothing.',
    provider: '', model: '', effort: '', rooms: hired ? ['Learn practice'] : [],
    skills: [],
  };
}

function floorPayload() {
  return {
    rooms: [{ id: PRACTICE_PID, name: 'Learn practice', emoji: '🎓', color: '',
              figures: [_pipFigure()] }],
    quiet: [], bench: [_guideType()],
    counts: { rooms: 1, figures: 1, quiet: 0, bench: 1 },
    activity_states: false, poll_seconds: 5,
  };
}

function projectRecord() {
  return {
    id: PRACTICE_PID, name: 'Learn practice', status: 'active', domain: 'general',
    emoji: '🎓', description: 'A practice project. Nothing here is real.',
    summary: 'Practice only. Nothing here touches your real projects.',
    current_task: 'Practice', next_action: '', blocked: false, blocked_reason: null,
    activity_log: [], backlog: [], project_path: '/learn-practice', last_updated: new Date().toISOString(),
    last_updated_relative: 'now', last_completed: null, live_agent: null,
    display_order: 0, provider: 'claude', use_streaming_agent: true,
    distiller_mode: 'proposed', distiller_min_recurrence: 3,
    distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
    distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
    distiller_skip_errors: true,
    roster: _roster(), _is_practice: true,
  };
}

function _sessionRecord() {
  return {
    session_id: PRACTICE_SID, status: 'idle', task: 'Practice conversation',
    started_at: new Date().toISOString(), claude_session_id: PRACTICE_CSID,
    provider_session_id: '', provider: 'claude', character: null, identity: null,
    process_alive: false, log_lines: [PRACTICE_TRANSCRIPT + '\n'], log_line_ts: [null],
    log_epoch: 0, usage: {}, cost_usd: 0, num_turns: 0,
  };
}

function _conversationRow() {
  return {
    claude_session_id: PRACTICE_CSID, mc_session_id: PRACTICE_SID, status: 'idle',
    label: 'Practice conversation', first_user: 'Practice conversation',
    last_user: '', turns: 0, ts_relative: 'now', live: false, provider: 'claude',
  };
}

function _json(obj, status) {
  return new Response(JSON.stringify(obj), {
    status: status || 200, headers: { 'Content-Type': 'application/json' },
  });
}

function _refuse(method, path) {
  _refused.push({ method, path });
  if (typeof window.showToast === 'function') window.showToast(PRACTICE_REFUSAL, 4000, 'learn-practice-only');
  return _json({ error: PRACTICE_REFUSAL, practice_only: true }, 403);
}

// The hire write. Mirrors the real route's response shape (`roster`,
// `already_hired`) so floor.js's _hireCharacter, toast and Channel hand-off run
// unchanged. Committed to the practice roster BEFORE responding, which is what
// the lesson reads as evidence — never the DOM.
function _hire(body) {
  if (_failHire) {
    const msg = _failHire; _failHire = '';
    return _json({ error: msg }, 500);
  }
  if (body.character !== GUIDE_REF) {
    return _json({ error: 'Practice only: that type is not part of this lesson.' }, 400);
  }
  const state = _loadRoster();
  const roster = state.runId === _runId ? state.roster : [];
  const already = roster.some((r) => r.character === GUIDE_REF && !r.removed_at);
  if (!already) {
    roster.push({
      character: GUIDE_REF, hired_at: new Date().toISOString(),
      hired_by: body.hired_by || 'drag', removed_at: null,
      run_id: _runId, seq: ++_seq,
    });
  }
  _saveRoster({ runId: _runId, roster });
  return _json({ roster, already_hired: already });
}

// The workflow create/update write. Mirrors the shape the real route answers
// with (`{ok, workflow}`) so the builder's own Save runs unchanged. A small
// subset of mc/workflows.py::validate_workflow: enough that a nameless or empty
// canvas fails the same way, not so much that it becomes a second validator.
// Committed to the practice list BEFORE responding; the lesson reads that list,
// never the DOM.
function _saveWorkflow(body, id) {
  if (_failSave) {
    const msg = _failSave; _failSave = '';
    return _json({ ok: false, error: msg }, 500);
  }
  const nodes = Array.isArray(body.nodes) ? body.nodes : [];
  const names = nodes.map((n) => n && n.name);
  if (!String(body.name || '').trim()) return _json({ ok: false, error: 'name is required' }, 400);
  if (!nodes.length) return _json({ ok: false, error: 'a workflow needs at least one step' }, 400);
  if (names.some((n, i) => !n || names.indexOf(n) !== i)) return _json({ ok: false, error: 'step names must be unique' }, 400);
  const edges = Array.isArray(body.edges) ? body.edges : [];
  if (edges.some((e) => !names.includes(e.from) || !names.includes(e.to))) {
    return _json({ ok: false, error: 'an edge points at a step that does not exist' }, 400);
  }
  const state = _loadWorkflows();
  const list = state.runId === _runId ? state.workflows : [];
  const now = new Date().toISOString();
  const prev = id ? list.find((w) => w.id === id) : null;
  if (id && !prev) return _json({ ok: false, error: 'workflow not found' }, 404);
  const rec = {
    id: prev ? prev.id : 'pw-' + Date.now().toString(36) + (++_seq),
    name: String(body.name).trim(), description: body.description || '',
    enabled: body.enabled !== false, trigger: body.trigger || { type: 'manual' },
    nodes, edges, format: 2, run_id: _runId, seq: ++_seq,
    created_at: prev ? prev.created_at : now, saved_at: now,
  };
  if (prev) list[list.indexOf(prev)] = rec; else list.push(rec);
  _saveWorkflows({ runId: _runId, workflows: list });
  return _json({ ok: true, workflow: rec });
}

function _readBody(init) {
  try { return JSON.parse((init && init.body) || '{}'); } catch (e) { return {}; }
}

// Returns a Response for a request the practice context answers or refuses,
// or null to let it reach the real server (a harmless read that is not about
// the practice project).
function _route(method, url, init) {
  let u;
  try { u = new URL(url, location.href); } catch (e) { return null; }
  if (u.origin !== location.origin) return null;
  const path = u.pathname;
  if (!path.startsWith('/api/')) return null;

  const proj = path.match(/^\/api\/project\/([^/]+)(\/.*)?$/);
  const sub = proj ? (proj[2] || '') : '';
  const isPracticeProject = !!proj && decodeURIComponent(proj[1]) === PRACTICE_PID;

  if (method === 'GET' || method === 'HEAD') {
    if (path === '/api/projects') return _json([projectRecord()]);
    if (path === '/api/floor') return _json(floorPayload());
    if (path === '/api/characters') return _json(_charactersFor(u));
    // The Workflows lesson: the builder lists and reloads its practice records,
    // and finds no schedule pointing at them.
    if (path === '/api/workflows') return _json(JSON.parse(JSON.stringify(_workflows())));
    if (path === '/api/schedules') return _json([]);
    if (/^\/api\/workflows\/[^/]+\/runs$/.test(path)) return _json([]);
    if (isPracticeProject) {
      if (sub === '/agent/status') return _json({ sessions: [_sessionRecord()] });
      if (sub === '/agent/log') return _json([]);
      if (sub === '/conversations') return _json([_conversationRow()]);
      if (sub === '/backlog' || sub === '/social/queue') return _json([]);
      if (sub === '/workflows') return _json({ workflows: [] });
      if (sub === '/terminal/status') return _json({ sessions: [] });
      _unhandled.push({ method, path });
      return _json({ error: 'Not available in practice.', practice_only: true }, 404);
    }
    return null;   // a read about something else: harmless, let it through
  }

  if (isPracticeProject && method === 'POST' && sub === '/roster/hire') {
    return _hire(_readBody(init));
  }
  if (method === 'POST' && path === '/api/workflows') return _saveWorkflow(_readBody(init), null);
  const putId = method === 'PUT' && path.match(/^\/api\/workflows\/([^/]+)$/);
  if (putId) return _saveWorkflow(_readBody(init), decodeURIComponent(putId[1]));
  // Claydo's own answer stream is a read of the guide, not a write to a project.
  if (path.startsWith('/api/guide/')) return null;
  // The presence heartbeat reports which chats are open. A practice chat is not
  // a real session, so it is answered here instead of being sent.
  if (path === '/api/presence') return _json({ ok: true });
  // The phone viewport watchdog (mobile.js) reports a stuck layout on its own.
  // That is telemetry, not a user write: dropped here without a "Practice only"
  // toast the user did nothing to earn.
  if (path === '/api/diag/viewport') return _json({ ok: true });
  return _refuse(method, path);
}

function _charactersFor(u) {
  // Pickers must not show the user's real types while practicing.
  return [{ name: 'guide', display_name: 'Guide', agent_name: 'Guide', scope: 'project',
            project_id: PRACTICE_PID, description: 'A practice type.',
            engine: { provider: 'claude', model: '' } }];
}

const _realFetch = window.fetch.bind(window);
window.fetch = async function practiceFetch(input, init) {
  if (!_active) return _realFetch(input, init);
  const isReq = typeof Request !== 'undefined' && input instanceof Request;
  const url = isReq ? input.url : String(input);
  const method = String((init && init.method) || (isReq ? input.method : 'GET')).toUpperCase();
  let body = init;
  if (isReq && !(init && init.body) && method !== 'GET' && method !== 'HEAD') {
    try { body = { ...(init || {}), body: await input.clone().text() }; } catch (e) { body = init; }
  }
  const gen = _gen;
  const res = _route(method, url, body);
  if (!res) return _realFetch(input, init);
  // A little latency so callers see an async boundary, and the stale check
  // below has something to bite on: a callback that outlives its practice run
  // must never write into the live caches.
  await new Promise((r) => setTimeout(r, 12));
  if (gen !== _gen || !_active) throw new DOMException('Practice ended', 'AbortError');
  return res;
};

// The chat's agent stream is an EventSource, which fetch routing cannot see.
// Practice sessions have no process behind them, so the stream is inert.
const _RealEventSource = window.EventSource;
if (_RealEventSource) {
  window.EventSource = function PracticeEventSource(url, cfg) {
    const s = String(url);
    if (_active && s.includes('/api/project/' + PRACTICE_PID + '/')) {
      const stub = { readyState: 1, url: s, withCredentials: false, onopen: null, onmessage: null,
        onerror: null, close() { this.readyState = 2; },
        addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; } };
      return stub;
    }
    return new _RealEventSource(url, cfg);
  };
  window.EventSource.prototype = _RealEventSource.prototype;
  for (const k of ['CONNECTING', 'OPEN', 'CLOSED']) window.EventSource[k] = _RealEventSource[k];
}

window.LearnPractice = {
  PID: PRACTICE_PID, SID: PRACTICE_SID, CSID: PRACTICE_CSID, GUIDE_REF,
  TRANSCRIPT: PRACTICE_TRANSCRIPT,
  get active() { return _active; },
  get runId() { return _runId; },
  get refused() { return _refused.slice(); },
  get unhandled() { return _unhandled.slice(); },
  // Begin routing. `fresh` starts an empty practice roster (Replay); resume
  // keeps whatever the same run already committed.
  enter(runId, fresh) {
    _gen++; _active = true; _runId = runId;
    if (fresh) _saveRoster({ runId, roster: [] });
    else if (_loadRoster().runId !== runId) _saveRoster({ runId, roster: [] });
    if (fresh || _loadWorkflows().runId !== runId) _saveWorkflows({ runId, workflows: [] });
  },
  leave() { _gen++; _active = false; },
  // The authoritative practice roster for a run: committed store state, not DOM.
  roster(runId) {
    const s = _loadRoster();
    return s.runId === (runId || _runId) ? s.roster.slice() : [];
  },
  // The practice workflow records committed by Save for a run: store state, not DOM.
  workflows(runId) {
    const s = _loadWorkflows();
    return s.runId === (runId || _runId) ? JSON.parse(JSON.stringify(s.workflows)) : [];
  },
  // Test seam: make the next practice workflow save fail with `message`.
  failNextSave(message) { _failSave = message || 'Practice save failed.'; },
  // Test seam: make the next practice hire fail with `message`.
  failNextHire(message) { _failHire = message || 'Practice hire failed.'; },
};
