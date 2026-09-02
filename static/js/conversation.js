// ── Agent Panel ─────────────────────────────────────────────────────────────

// Tag agent-bound POST bodies with client="mobile" when the viewport is
// phone-sized. The backend then decides — based on mobile_brief_replies_enabled
// — whether to prepend a Telegram-style directive to the message that gets
// piped to claude stdin. The original message still goes into log_lines so
// the user's chat bubble shows what they typed.
function _maybeTagMobileClient(body) {
  if (window.innerWidth <= 960) body.client = 'mobile';
  return body;
}

let exitPlanModeCount = {};    // session_id → number of consecutive ExitPlanMode calls
let pendingDispatchProvider = {};  // project_id → provider chosen in the new-chat composer (per-conversation, not per-project)
let pendingDispatchCharacter = {}; // project_id → "scope:name" persona chosen in the new-chat composer (Prompt Builder Phase 2)
let characterCache = {};           // project_id → array of available characters (lazy-loaded for the composer picker)
let characterCacheLoading = {};    // project_id → true while a fetch is in flight (dedupe)

// Lazy-load a project's characters (project pool + globals) for the new-chat
// composer picker. Re-renders once when the list arrives. Best-effort: a
// failure just leaves the picker absent (no persona = today's behavior).
function _ensureCharacters(projectId) {
  if (characterCache[projectId] || characterCacheLoading[projectId]) return;
  characterCacheLoading[projectId] = true;
  fetch(API_BASE + '/api/characters?project_id=' + encodeURIComponent(projectId))
    .then(r => r.json())
    .then(list => {
      characterCache[projectId] = Array.isArray(list) ? list : [];
      characterCacheLoading[projectId] = false;
      if (characterCache[projectId].length) refreshModalById(projectId);
    })
    .catch(() => { characterCache[projectId] = []; characterCacheLoading[projectId] = false; });
}

// Short engine label for a character: the model if pinned, else the provider.
// Full ids ("claude-haiku-4-5-20251001") make the picker unreadable, so reuse
// the model-picker's friendly labels — single source of truth for the naming.
function _engShortLabel(engine) {
  if (!engine) return '';
  if (engine.model) {
    const c = (window.MC_MODEL_CHOICES || []).find(x => x[0] === engine.model);
    return c ? c[1] : engine.model;
  }
  return engine.provider || '';
}

// Character/persona dropdown for the +New composer. Returns '' (no picker)
// only on resume (persona is fixed at spawn). Always offered otherwise so the
// "Create new persona…" entry is reachable even with no characters yet.
// Default selection = none = today's plain-agent behavior.
function _composerCharacterPicker(p, resumeId) {
  if (!p || resumeId) return '';
  _ensureCharacters(p.id);
  const list = characterCache[p.id] || [];
  const cur = pendingDispatchCharacter[p.id] || '';
  const opts = list.map(c => {
    const val = esc((c.scope || 'global') + ':' + c.name);
    const eng = c.engine && (c.engine.model || c.engine.provider);
    // Name first, role in parentheses: the picker is "who do I want on this",
    // and the role is the qualifier, not the identity.
    const label = (c.agent_name ? esc(c.agent_name) + ' (' + esc(c.display_name || c.name) + ')'
                                : esc(c.display_name || c.name))
      + (c.scope === 'global' ? ' (global)' : '')
      + (eng ? ' · ' + esc(_engShortLabel(c.engine)) : '');
    return `<option value="${val}" ${val === cur ? 'selected' : ''}>${label}</option>`;
  }).join('');
  // With a project default set, an empty pick is NOT "no persona" — it inherits.
  // Labelling it "None" would state the opposite of what dispatch will do.
  const _defRec = p.default_character
    ? list.find(c => ((c.scope || 'global') + ':' + c.name) === p.default_character)
    : null;
  const noneLabel = p.default_character
    ? 'Project default' + (_defRec ? ' (' + esc(_defRec.display_name || _defRec.name) + ')' : '')
    : 'None';
  // Pencil → edit the SELECTED persona (description / instructions / delete).
  // Only shown when one is selected: there's nothing to edit otherwise, and it
  // keeps the row quiet in the common "None" case.
  const editBtn = cur
    ? `<button class="composer-persona-edit" type="button" title="Edit this persona"
        onclick="editComposerCharacter('${esc(p.id)}')">&#9998;</button>`
    : '';
  return `<div class="composer-provider-row composer-character-row">
    <span class="composer-provider-label">Persona</span>
    <select class="composer-provider-select" onchange="setComposerCharacter('${esc(p.id)}',this.value)">
      <option value="">${noneLabel}</option>${opts}
      <option value="__create__">&#43; Create new persona&hellip;</option>
    </select>
    ${editBtn}
  </div>`;
}

// Open the persona editor on whatever the picker currently has selected. The
// editor itself lives in claydo.js (the character domain) and is reached via
// window.* interop — same ES-module boundary rule as _createNewPersona above.
function editComposerCharacter(projectId) {
  const cur = pendingDispatchCharacter[projectId] || '';
  const i = cur.indexOf(':');
  if (i < 0) return;
  if (typeof window.openPersonaEditor === 'function') {
    window.openPersonaEditor(projectId, cur.slice(0, i), cur.slice(i + 1));
  }
}

// Called by the persona editor after a save/delete. Drops the cache and
// re-fetches so the picker shows the new description immediately.
// Unlike _ensureCharacters this ALWAYS re-renders — after deleting the last
// persona the list is empty, and the "only refresh if non-empty" guard there
// would leave the stale entry on screen.
function reloadCharacters(projectId) {
  delete characterCache[projectId];
  delete characterCacheLoading[projectId];
  fetch(API_BASE + '/api/characters?project_id=' + encodeURIComponent(projectId))
    .then(r => r.json())
    .then(list => {
      characterCache[projectId] = Array.isArray(list) ? list : [];
      refreshModalById(projectId);
    })
    .catch(() => { characterCache[projectId] = []; refreshModalById(projectId); });
}

// A deleted persona must not stay armed on the composer — dispatching it would
// 404 at spawn (_resolve_character reads the file that no longer exists).
function clearCharacterIfSelected(projectId, character) {
  if ((pendingDispatchCharacter[projectId] || '') === character) {
    delete pendingDispatchCharacter[projectId];
  }
}

function setComposerCharacter(projectId, character) {
  if (character === '__create__') {
    // Persona creation happens in the Claydo character flow; revert the select.
    _createNewPersona(projectId);
    refreshModalById(projectId);
    return;
  }
  pendingDispatchCharacter[projectId] = character;
  refreshModalById(projectId);
}

// Open the Claydo assistant in "Create an agent character" mode (a fresh
// persona → saved via /api/characters → appears in this picker). Both helpers
// live in claydo.js (a module) and are reached via window.* interop.
function _createNewPersona(projectId) {
  const toChar = () => { if (typeof window.setClaydoMode === 'function') window.setClaydoMode('character'); };
  try {
    if (typeof window.openClaydo === 'function') Promise.resolve(window.openClaydo()).then(toChar).catch(toChar);
    else toChar();
  } catch (e) {}
}

// Cross-module accessors for the per-chat persona state. These vars are
// module-scoped to conversation.js; resume-preview.js (the dispatch path) must
// reach them through these window-exposed helpers — NOT by touching the vars
// directly, which throws a ReferenceError across the ES-module boundary and
// aborts every dispatch (the "new/old chats immediately STOPPED" regression).
// Mirrors how provider state is shared via _composerProvider().
function getPendingCharacter(projectId) {
  return pendingDispatchCharacter[projectId] || '';
}
function clearPendingCharacter(projectId) {
  delete pendingDispatchCharacter[projectId];
}
function resolveCharacterMeta(projectId, character) {
  if (!character) return null;
  const i = character.indexOf(':');
  const scope = character.slice(0, i), name = character.slice(i + 1);
  const rec = (characterCache[projectId] || [])
    .find(c => c.name === name && (c.scope || 'global') === scope);
  const meta = { name, scope, source: 'picked',
                 display_name: (rec && (rec.display_name || rec.name)) || name };
  if (rec && rec.engine) meta.engine = rec.engine;
  if (rec && rec.agent_name) meta.agent_name = rec.agent_name;
  return meta;
}
window.setComposerCharacter = setComposerCharacter;
window.editComposerCharacter = editComposerCharacter;
window.reloadCharacters = reloadCharacters;
window.clearCharacterIfSelected = clearCharacterIfSelected;
window.getPendingCharacter = getPendingCharacter;
window.clearPendingCharacter = clearPendingCharacter;
window.resolveCharacterMeta = resolveCharacterMeta;
window._engShortLabel = _engShortLabel;
// Bridged for the Project-profile dialog (modal-manager.js), which needs the
// same character list. ES modules don't share top-level bindings, and the
// cache is a live object — hand back a getter, not a snapshot, or the dialog
// renders whatever was cached at module-eval time (i.e. nothing).
window._ensureCharacters = _ensureCharacters;
// The persona editor needs the provider list to build its Engine picker.
// A getter, not a snapshot — the list is fetched lazily after module eval.
window._agentProvidersList = () => _agentProviders || [];
// Same reason: the persona editor's model picker must show the SAME
// per-provider catalog as the composer's, not a second copy of it.
window._providerModelChoices = _providerModelChoices;
window.characterCacheFor = (pid) => characterCache[pid] || [];

// Provider is bound per-conversation. The new-chat composer picks it; the
// project's `provider` field (set via three-dot menu) is only the default seed.
function _composerProvider(p) {
  if (!p) return 'claude';
  return pendingDispatchProvider[p.id]
      || p.provider
      || (_globalConfig && _globalConfig.default_provider)
      || 'claude';
}

// Provider dropdown for the +New composer. Returns '' (no picker) when only
// claude is available — claude-only deployments stay pixel-identical.
function _composerProviderPicker(p) {
  const provs = (_agentProviders || []).filter(x => x.installed);
  if (provs.length <= 1) return '';
  const cur = _composerProvider(p);
  const opts = provs.map(x =>
    `<option value="${esc(x.name)}" ${x.name === cur ? 'selected' : ''}>${esc(x.display_name)}</option>`
  ).join('');
  return `<div class="composer-provider-row">
    <span class="composer-provider-label">Agent</span>
    <select class="composer-provider-select" onchange="setComposerProvider('${esc(p.id)}',this.value)">${opts}</select>
  </div>`;
}

function setComposerProvider(projectId, provider) {
  pendingDispatchProvider[projectId] = provider;
  // Caps changed (resume / image-input / etc.) — re-render so the composer
  // reflects the chosen provider's affordances.
  refreshModal();
}

// Per-chat model for the +New composer (was buried in ⋮ → Agent Settings).
// Sticky per project+provider for the session, like the provider picker.
// '' = default: the project/global model, or the auto-router when it's enabled.
// An explicit pick bypasses the router server-side. Fresh chats only (resume
// keeps the conversation's model).
//
// The option list is PER PROVIDER — model ids don't cross runtimes (`codex -m
// claude-opus-5` is a hard error), so it comes from the provider record served
// by /api/agent/providers (`models: [{id,label}]`), not one global list.
// Keyed "<project>::<provider>" so switching Agent can't leave a foreign model
// id armed. A provider with no catalog (kiro — no model flag) gets no picker.
let pendingDispatchModel = {};
// Declared here (not beside setComposerModel below) so it can never be read
// before its `let` initialises — the TDZ boot-throw class this file has hit.
let pendingModelCustom = {};
function _modelKey(projectId, provider) { return projectId + '::' + provider; }

// Catalog for a provider. claude falls back to MC_MODEL_CHOICES so the picker
// still works against an older server that doesn't send `models` yet.
function _providerModelChoices(provider) {
  const rec = (_agentProviders || []).find(x => x.name === provider);
  if (rec && Array.isArray(rec.models) && rec.models.length) {
    return rec.models.map(m => [m.id, m.label || m.id]);
  }
  if (provider === 'claude') {
    return (window.MC_MODEL_CHOICES || []).filter(([v]) => v !== '');
  }
  return [];
}

function _composerModelPicker(p, resumeId) {
  if (!p || resumeId) return '';
  const prov = _composerProvider(p);
  const choices = _providerModelChoices(prov);
  if (!choices.length) return '';
  const cur = pendingDispatchModel[_modelKey(p.id, prov)] || '';
  // What "Default" resolves to. The project's agent_model only seeds claude —
  // for any other runtime it's a claude id the CLI would reject, so the server
  // drops it and the provider's own default applies (mirror that here).
  const defLabel = (() => {
    if (prov !== 'claude') return 'Default';
    if (_globalConfig && _globalConfig.auto_model_enabled && !p.agent_model) return 'Auto';
    const m = choices.find(c => c[0] === (p.agent_model || ''));
    return (p.agent_model && m) ? `Default (${m[1]})` : 'Default';
  })();
  // Custom = the box is armed, or the armed id isn't in the catalog (the user
  // typed it). Keep it selected rather than silently reverting to Default.
  const isCustom = !!pendingModelCustom[_modelKey(p.id, prov)]
                   || (!!cur && !choices.some(([v]) => v === cur));
  const opts = choices
    .map(([v, l]) => `<option value="${esc(v)}" ${v === cur ? 'selected' : ''}>${esc(l)}</option>`).join('');
  const customInput = isCustom
    ? `<input class="composer-model-custom" type="text" value="${esc(cur)}"
        placeholder="model id" title="Passed to ${esc(prov)} verbatim — for a model newer than this list"
        onchange="setComposerModel('${esc(p.id)}',this.value)">`
    : '';
  return `<div class="composer-provider-row composer-model-row">
    <span class="composer-provider-label">Model</span>
    <select class="composer-provider-select" onchange="setComposerModel('${esc(p.id)}',this.value)">
      <option value="">${esc(defLabel)}</option>${opts}
      <option value="__custom__" ${isCustom ? 'selected' : ''}>Custom&hellip;</option>
    </select>
    ${customInput}
  </div>`;
}

// `__custom__` arms an empty text box (its own map, so armed-but-empty is never
// mistaken for a model id); the box's onchange lands back here with what the
// user typed. Inline, so the row still reads like the other pickers.
function setComposerModel(projectId, model) {
  const p = (typeof allProjects !== 'undefined' ? allProjects : []).find(x => x.id === projectId);
  const key = _modelKey(projectId, _composerProvider(p));
  if (model === '__custom__') {
    pendingModelCustom[key] = true;
    pendingDispatchModel[key] = '';
  } else {
    const v = (model || '').trim();
    // Typing in the box keeps it open; picking a catalog entry closes it.
    pendingModelCustom[key] = !!pendingModelCustom[key] && !!v;
    pendingDispatchModel[key] = v;
  }
  refreshModalById(projectId);
}

// ── In-chat model switcher (header pill → body popover) ─────────────────────
// The menu is rendered into document.body (NOT nested in the chat header) so
// the chat-output's overflow can't clip it, and so refreshModalById's periodic
// rebuild of the header can't blow it away. Only one is ever open.
let _chatModelMenuSid = null;
const _CMS_POP_ID = 'chat-model-menu-pop';

function _closeChatModelMenu() {
  _chatModelMenuSid = null;
  const pop = document.getElementById(_CMS_POP_ID);
  if (pop) pop.remove();
}

function toggleChatModelMenu(event, projectId, sid) {
  if (event) { event.stopPropagation(); event.preventDefault(); }
  if (_chatModelMenuSid === sid && document.getElementById(_CMS_POP_ID)) {
    _closeChatModelMenu();
    return;
  }
  _closeChatModelMenu();
  _chatModelMenuSid = sid;
  const pillEl = event && event.currentTarget;
  const p = (typeof allProjects !== 'undefined' ? allProjects : []).find(x => x.id === projectId) || {};
  const s = agentStatusCache[sid] || {};
  const pinned = s.pinnedModel || '';
  const choices = (window.MC_MODEL_CHOICES || []).filter(([v]) => v !== '');
  // What "Follow default" resolves to right now (mirrors the +New picker).
  const defLabel = (() => {
    if (typeof _globalConfig !== 'undefined' && _globalConfig.auto_model_enabled && !p.agent_model) return 'Auto-router';
    const m = choices.find(c => c[0] === (p.agent_model || ''));
    return (p.agent_model && m) ? m[1] : 'Project / global default';
  })();
  const items = [
    `<button class="cms-item${!pinned ? ' active' : ''}" onclick="switchChatModel('${esc(projectId)}','${esc(sid)}','')">Follow default<span class="cms-sub">${esc(defLabel)}</span></button>`,
    ...choices.map(([v, l]) =>
      `<button class="cms-item${pinned === v ? ' active' : ''}" onclick="switchChatModel('${esc(projectId)}','${esc(sid)}','${esc(v)}')">${esc(l)}</button>`)
  ].join('');
  const pop = document.createElement('div');
  pop.className = 'chat-model-menu open';
  pop.id = _CMS_POP_ID;
  pop.addEventListener('click', (e) => e.stopPropagation());
  pop.innerHTML = `<div class="cms-head">Model &middot; this chat</div>${items}`;
  document.body.appendChild(pop);
  _positionChatModelMenu(pillEl);
}

// Anchor the fixed popover just under the pill, clamped into the viewport.
function _positionChatModelMenu(pillEl) {
  const pop = document.getElementById(_CMS_POP_ID);
  if (!pop || !pillEl) return;
  const r = pillEl.getBoundingClientRect();
  const w = pop.offsetWidth || 200;
  let left = Math.round(r.left);
  if (left + w > window.innerWidth - 8) left = window.innerWidth - w - 8;
  pop.style.top = Math.round(r.bottom + 6) + 'px';
  pop.style.left = Math.max(8, left) + 'px';
}

// Pin (model set) or un-pin (model === '') the model for THIS conversation.
// Optimistic: stamp pinnedModel locally so the pill updates instantly; the
// server persists it and applies the switch on the next turn (a `-r` respawn
// that re-injects memory/rules). We deliberately do NOT touch the running
// `model` field — it catches up when the next turn actually respawns, so the
// pill can't flip-flop against the status poll.
async function switchChatModel(projectId, sid, model) {
  _closeChatModelMenu();
  const s = agentStatusCache[sid];
  if (s) s.pinnedModel = model;
  refreshModalById(projectId);
  try {
    await fetch(`${API_BASE}/api/project/${projectId}/agent/${sid}/model`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model }),
    });
    if (typeof showToast === 'function') {
      showToast(model ? `Model pinned — applies on your next message` : `Following the default model again`, 2600);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('Could not change model', 2500);
  }
}

// Close on any outside click, and keep it anchored on resize/scroll (bound once).
if (typeof window !== 'undefined' && !window._chatModelMenuCloserBound) {
  window._chatModelMenuCloserBound = true;
  document.addEventListener('click', (e) => {
    if (_chatModelMenuSid == null) return;
    if (e.target.closest && (e.target.closest('.provider-badge.model-switch') || e.target.closest('#' + _CMS_POP_ID))) return;
    _closeChatModelMenu();
  });
  window.addEventListener('resize', _closeChatModelMenu);
}

window.toggleChatModelMenu = toggleChatModelMenu;
window.switchChatModel = switchChatModel;

function getIncognitoFor(projectId) {
  // Global incognito project is always incognito; user can't turn it off.
  if (projectId === '_incognito') return true;
  return !!incognitoToggle[projectId];
}

function toggleIncognito(projectId) {
  if (projectId === '_incognito') return;  // forced on
  incognitoToggle[projectId] = !incognitoToggle[projectId];
  refreshModalById(projectId);
}

// Incognito is a per-session, one-shot choice: once a dispatch bakes the flag
// into that session, reset the composer toggle so the NEXT new chat defaults to
// OFF (opt-in per chat, mirroring clearPendingCharacter). The dispatched session
// keeps its own incognito flag; only the composer default resets. No refresh
// here — the dispatch flow re-renders right after.
function clearIncognito(projectId) {
  if (projectId === '_incognito') return;  // forced on — never resets
  incognitoToggle[projectId] = false;
}

// ── §8 ＋ options bottom sheet (MOBILE-only, pre-dispatch/+New screen) ───────
// Decisions 5a (pre-dispatch only) + 6b (desktop keeps the inline controls;
// the slide-up sheet is the mobile-canonical home). On mobile the composer
// collapses to input + ＋ + Send; ＋ opens a sheet holding the SAME existing
// Agent/Persona/Incognito/Resume+Search controls (relocated triggers, not new
// logic). Desktop output is byte-identical to before.
let composerSheetOpen = {};  // project_id → bool (mirrors incognitoToggle pattern)
function openComposerSheet(projectId) { composerSheetOpen[projectId] = true; refreshModalById(projectId); }
function closeComposerSheet(projectId) { composerSheetOpen[projectId] = false; refreshModalById(projectId); }

// Incognito chip — extracted from the inline dispatchRow template so the old
// call site AND the sheet share one source (preserves the forced-on branch for
// the _incognito pseudo-project).
function _incognitoChipHTML(p) {
  const incOn = getIncognitoFor(p.id);
  const incForced = isIncognitoProject(p);
  return `<div class="incognito-toggle ${incOn ? 'on' : ''} ${incForced ? 'forced' : ''}"
      onclick="${incForced ? '' : `toggleIncognito('${esc(p.id)}')`}"
      title="${incForced ? 'This is the global Incognito agent — sessions here are always incognito.' : (incOn ? 'Incognito ON: agent has full project context, but the session is not logged and nothing is appended to MEMORY.md.' : 'Incognito OFF: standard project context applies.')}">
      <span class="inc-mark">&#x1F576;&#xFE0F;</span>
      <span class="inc-label">Incognito${incForced ? '' : (incOn ? ' on' : '')}</span>
    </div>`;
}

// Compact status line under the mobile composer so current selections stay
// visible without opening the sheet. Tap → open sheet. Built from existing
// accessors only (no new state).
function _composerPlusStatusLineHTML(p, resumeId) {
  const curProv = _composerProvider(p);
  const provRec = (_agentProviders || []).find(x => x.name === curProv);
  const provName = provRec ? provRec.display_name : curProv;
  const charMeta = resolveCharacterMeta(p.id, getPendingCharacter(p.id));
  const inc = getIncognitoFor(p.id);
  const parts = [`&#9680; ${esc(provName)}`];
  if (charMeta) parts.push(esc(charMeta.display_name));
  parts.push(`&#x1F576;&#xFE0F; ${inc ? 'Incognito on' : 'Incognito off'}`);
  return `<div class="composer-plus-status" onclick="openComposerSheet('${esc(p.id)}')">${parts.join(' &middot; ')} &middot; <span class="cps-change">Change</span></div>`;
}

// The slide-up sheet itself. Always rendered (visibility is CSS-class driven off
// composerSheetOpen) so the pickers' lazy _ensureCharacters() fetch timing is
// unchanged. Calls the SAME existing picker/toggle/search functions.
function _composerSheetHTML(p, resumeId) {
  const open = !!composerSheetOpen[p.id];
  const prov = _composerProviderPicker(p);                 // '' when single provider
  const model = _composerModelPicker(p, resumeId);         // '' on resume / provider has no model flag
  const persona = _composerCharacterPicker(p, resumeId);   // '' on resume / no characters
  const inc = _incognitoChipHTML(p);
  // Spec §8 (2026-07-06): the sheet holds per-message COMPOSE options only —
  // Agent / Persona / Incognito. NO resume/search here: resuming = tap a chat in
  // the Layer-2 conversation list (a Resume action in the sheet was dead weight).
  return `<div class="composer-sheet-overlay ${open ? 'visible' : ''}" onclick="closeComposerSheet('${esc(p.id)}')">
    <div class="composer-sheet ${open ? 'visible' : ''}" onclick="event.stopPropagation()">
      <div class="composer-sheet-grip"></div>
      <div class="composer-sheet-scroll">
        <div class="composer-sheet-title">Conversation options</div>
        ${prov ? `<div class="composer-sheet-row">${prov}</div>` : ''}
        ${model ? `<div class="composer-sheet-row">${model}</div>` : ''}
        ${persona ? `<div class="composer-sheet-row">${persona}</div>` : ''}
        <div class="composer-sheet-row">${inc}</div>
      </div>
      <div class="composer-sheet-footer">
        <button type="button" class="composer-sheet-done" onclick="closeComposerSheet('${esc(p.id)}')">Done</button>
      </div>
    </div>
  </div>`;
}

function isHivemindWorker(h) {
  return !!(h.hivemindWsId || (agentStatusCache[h.sessionId] || {}).hivemindWsId);
}
function isHivemindOrchestrator(h) {
  return (h.hivemindRole === 'orchestrator') || ((agentStatusCache[h.sessionId] || {}).hivemindRole === 'orchestrator');
}

function getProjectSessions(projectId) {
  return agentHistory.filter(h => h.projectId === projectId && !isHivemindWorker(h));
}

// Tabs visible in the per-project agent strip. Filters out automated runs that
// have already finished — they're available in the Scheduler's Runs panel
// (and Agent Log tab), so showing them as tabs forever just buries the tabs
// the user actually cares about. Active automated runs DO stay visible so a
// "Run Now" or live schedule fire is watchable in the strip.
function getProjectTabSessions(projectId) {
  const TERMINAL = new Set(['completed', 'stopped', 'error']);
  const HIDDEN_TRIGGERS = new Set(['schedule', 'hivemind_worker']);
  return getProjectSessions(projectId).filter(h => {
    if (!HIDDEN_TRIGGERS.has(h.triggerType)) return true;  // manual + orchestrator always visible
    return !TERMINAL.has(h.status);  // automated: only while active
  });
}

// ── Hivemind worker popover ─────────────────────────────────────────────────
let hmPopoverTimer = null;
let hmPopoverSessionId = null;

function showHmWorkerPopover(event, sessionId) {
  cancelHideHmPopover();
  const cached = agentStatusCache[sessionId];
  if (!cached || !cached.hivemindId) return;
  const popover = document.getElementById('hm-worker-popover');
  const tab = event.currentTarget;
  const rect = tab.getBoundingClientRect();
  popover.style.left = Math.max(0, rect.left) + 'px';
  popover.style.top = (rect.bottom + 4) + 'px';
  popover.classList.remove('hidden');
  hmPopoverSessionId = sessionId;

  // Get workers from agentHistory matching this hivemind
  const hmId = cached.hivemindId;
  const workers = agentHistory.filter(h => {
    const c = agentStatusCache[h.sessionId] || h;
    return (c.hivemindId || h.hivemindId) === hmId && (c.hivemindWsId || h.hivemindWsId);
  });

  // Enrich with workstream titles from hivemindCache
  let wsTitleMap = {};
  for (const pid of Object.keys(hivemindCache)) {
    const hms = (hivemindCache[pid] || {}).hiveminds || [];
    const hm = hms.find(h => h.id === hmId);
    if (hm && hm._workstreams) {
      hm._workstreams.forEach(w => { wsTitleMap[w.id] = w.title; });
      break;
    }
  }

  if (workers.length === 0) {
    popover.innerHTML = `<div class="hm-pop-header">&#x2B21; Hivemind Workers</div><div class="hm-pop-empty">No workers spawned yet</div>`;
  } else {
    const rows = workers.map(w => {
      const wsId = w.hivemindWsId || (agentStatusCache[w.sessionId] || {}).hivemindWsId || '';
      const wsTitle = wsTitleMap[wsId] || wsId;
      const wProv = w.provider || (agentStatusCache[w.sessionId] || {}).provider || 'claude';
      const wModel = w.model || (agentStatusCache[w.sessionId] || {}).model || '';
      return `<div class="hm-pop-worker" onclick="if(typeof openHivemindDashboard==='function'){openHivemindDashboard('${esc(hmId)}','${esc(wsId)}');}">
        <span class="agent-status-dot ${w.status}"></span>
        <span class="hm-pop-ws-title">${esc(wsTitle)}</span>
        ${_providerBadge(wProv)}
        ${wModel ? `<span class="hm-pop-ws-meta">${esc(wModel)}</span>` : ''}
        <span class="hm-pop-ws-status">${esc(w.status)}</span>
      </div>`;
    }).join('');
    popover.innerHTML = `<div class="hm-pop-header">&#x2B21; Hivemind Workers (${workers.length})</div>${rows}`;
  }
}
function scheduleHideHmPopover() {
  hmPopoverTimer = setTimeout(() => {
    const el = document.getElementById('hm-worker-popover');
    if (el) el.classList.add('hidden');
    hmPopoverSessionId = null;
  }, 300);
}
function cancelHideHmPopover() {
  if (hmPopoverTimer) { clearTimeout(hmPopoverTimer); hmPopoverTimer = null; }
}


// §5: static starter prompts for the empty +New screen (project-aware is v2).
// #4: full-width rows with a leading icon + trailing chevron, per the 5a mockup.
const STARTER_CHIPS = [
  { icon: '\u{1F9EA}', label: 'Fix a failing test or bug' },       // 🧪
  { icon: '\u{2728}',  label: 'Add a feature to this project' },   // ✨
  { icon: '\u{1F4D6}', label: 'Explain how the codebase works' },  // 📖
];

// Fill the dispatch textarea with a starter prompt and focus it — does NOT send
// (the user edits/confirms). No refreshModal/setTimeout: the textarea is already
// on screen when the chip is clickable.
function fillStarterChip(projectId, text) {
  const ta = document.getElementById(`agent-task-${projectId}`);
  if (!ta) return;
  ta.value = text;
  // #6: don't pop the mobile keyboard just from tapping a chip — only focus on
  // desktop. On mobile the user taps the field or Dispatch when ready.
  if (window.innerWidth > 960) {
    ta.focus();
    ta.setSelectionRange(text.length, text.length);
  }
}

// Ultrawide-only right-side "Surfaces" panel (shown via container query at wide
// pane widths). Preview cards for the project's content tabs; clicking opens the
// full tab in the center area (switchModalTab). Lightweight previews for now.
function _agentSurfacesHTML(p) {
  // Items are lazy-loaded with the modal (see backlogSummary in render-core);
  // until they land, show the one line the list payload does carry rather than
  // claiming "No open items" about a project that has plenty.
  const bl = window.backlogSummary ? window.backlogSummary(p) : { open: 0, nextText: '' };
  const loaded = !!(p._backlogFull && Array.isArray(p.backlog));
  const open = loaded ? p.backlog.filter(i => !BACKLOG_CLOSED.includes(i.status)) : [];
  const topItems = open.slice(0, 3)
    .map(i => `<div class="surface-line">&#8226; ${esc((i.text || '').slice(0, 52))}</div>`).join('')
    || (bl.open
        ? `<div class="surface-line">&#8226; ${esc((bl.nextText || '').slice(0, 52))}</div>`
        : '<div class="surface-line surface-empty">No open items</div>');
  // `tab` cards swap a pane INSIDE the modal; `action` cards open their own
  // surface window. Media is the latter: the gallery is already a full surface
  // (filters, project switcher, viewers), and re-hosting it as a modal tab would
  // mean a second copy of it to keep in sync.
  const cards = [
    { tab: 'backlog',   ic: '&#9776;',   name: 'Backlog',   sub: bl.open ? `${bl.open} open` : 'empty', body: topItems },
    { tab: 'documents', ic: '&#128203;', name: 'Documents', sub: 'plans & docs', body: '<div class="surface-line surface-empty">Open to view documents</div>' },
    { tab: 'workflows', ic: '&#9939;',   name: 'Workflows', sub: 'runs',        body: '<div class="surface-line surface-empty">Open to view workflows</div>' },
    { tab: 'activity',  ic: '&#9201;',   name: 'Activity',  sub: 'timeline',    body: '<div class="surface-line surface-empty">Open to view activity</div>' },
    { tab: 'agent-log', ic: '&#128220;', name: 'Agent Log', sub: 'history',     body: '<div class="surface-line surface-empty">Open to view the log</div>' },
    { action: `openMediaSurface('${esc(p.id)}')`,
      ic: '&#128444;', name: 'Media',    sub: 'diagrams & images',
      body: '<div class="surface-line surface-empty">Open to view media</div>' },
  ];
  const cardHTML = cards.map(c => {
    const onclick = c.action || `switchModalTab('${esc(p.id)}','${c.tab}')`;
    return `
    <button class="surface-card" onclick="${onclick}" title="Open ${c.name}">
      <div class="surface-card-head"><span class="surface-ic">${c.ic}</span><span class="surface-name">${c.name}</span><span class="surface-sub">${esc(c.sub)}</span></div>
      <div class="surface-card-body">${c.body}</div>
    </button>`;
  }).join('');
  return `<div class="agent-surfaces">
    <div class="agent-surfaces-title">Surfaces</div>
    ${cardHTML}
  </div>`;
}

function agentPanelHTML(p) {
  const pp = p.project_path || '';
  if (!pp) {
    return `<div class="agent-panel">
      <div class="section-title">Agent</div>
      <div class="agent-disabled">Set project_path to enable agent dispatch</div>
    </div>`;
  }
  // Visible tab list: hides completed schedule/hivemind-worker runs since
  // those are surfaced in the Scheduler's Runs panel + Agent Log tab.
  const sessions = getProjectTabSessions(p.id);
  // Sort newest-first so the most recent conversation leads.
  sessions.sort((a, b) => (b.startedAt || '').localeCompare(a.startedAt || ''));

  // ── Conversation model ──────────────────────────────────────────────────
  // MOBILE (≤960px): WhatsApp-Communities drill-down — >1 convo shows a
  //   vertical list (tap to open, back bar returns), 1 → direct chat,
  //   0 → dispatch. Space-constrained, so one thing at a time.
  // DESKTOP: classic horizontal tab strip — every conversation is a tab,
  //   the whole modal is visible at once, no drill-down. There's room, so
  //   forcing a list/back dance just adds clicks (the regression reported).
  const mobileMode = isMobileChatList();
  const multi = sessions.length > 1;
  const wantNew = agentConvNew[p.id] === true;
  // Drop a stale selection (session closed/ended elsewhere) so it can't
  // blank the panel or pin us off the list.
  if (activeAgentTab[p.id] && !sessions.some(s => s.sessionId === activeAgentTab[p.id])) {
    delete activeAgentTab[p.id];
  }
  // Auto-select a tab when none is active:
  //  • desktop → classic: most-recent running/idle (else newest) so a tab is
  //    always open and the strip behaves like before the drill-down.
  //  • mobile → NEVER auto-select. Always land on the Layer-2 conversations
  //    list (even for a single chat) so resume/new stays reachable — auto-
  //    opening the lone chat trapped the user with no route to resume (and
  //    "← All conversations" just re-selected it).
  if (!wantNew && !activeAgentTab[p.id] && sessions.length && !mobileMode) {
    const running = sessions.find(s => s.status === 'running' || s.status === 'idle');
    activeAgentTab[p.id] = (running || sessions[0]).sessionId;
  }
  const activeSessionId = wantNew ? null : (activeAgentTab[p.id] || null);
  const activeSession = activeSessionId ? agentStatusCache[activeSessionId] : null;
  const st = activeSession ? (activeSession.status || 'idle') : 'idle';
  const isRunning = st === 'running';

  // Mobile Layer-2 "Conversations list" vs Layer-3 compose/thread (spec §1).
  // Land on the LIST (search + recents) whenever there are prior chats and you
  // haven't opened one (activeSession), started a new one (wantNew), or armed a
  // resume (pendingResumeId). Compose (5a) + resume + thread are Layer 3.
  const _resumeArmed = !!pendingResumeId[p.id];
  // Layer 2 lists ALL the USER's conversations (durable, transcript-derived —
  // past + present), filtered to user-initiated. Agent/scheduled runs live in
  // the ⋮ → Agent Log side flow, not here. Tapping a row opens it in the thread.
  // Rail mode (chats | topics) is a property of the whole panel, not just the
  // desktop rail — mobile's Layer-2 list is the same surface in a different
  // shape, and shipping the toggle on desktop only left phones stuck on the
  // 128-row chat list the digest exists to replace.
  const _mode = (typeof railMode === 'function') ? railMode(p.id) : 'chats';
  if (_mode === 'topics' && !_topicsData[p.id]) _loadTopics(p.id);
  const _mobileUserConvos = mobileMode ? _userInitiatedConvos(p.id) : [];
  // In topics mode the list stands on the digest, so an empty conversation list
  // must not collapse the view — otherwise switching to Topics on a project
  // with no user chats drops you into the composer with no way back to the list.
  // Channel mode (MC-937 Phase 1) is the same: its list is a roster, not
  // _mobileUserConvos, so an empty roster must still land on the list (the
  // empty state), not fall through to the composer.
  const _mobileListMode = mobileMode && !activeSession && !wantNew && !_resumeArmed
    && (_mobileUserConvos.length > 0 || _mode === 'topics' || _mode === 'channel');

  // View chrome: mobile = drill-down list + back bar; desktop = tab strip.
  let convListHTML = '', convBackBar = '', tabBar = '';
  if (mobileMode) {
    if (_mobileListMode) {
      // Layer 2 — all user conversations, with a "+ New" launcher pinned to the
      // bottom edge. Resume is reachable by tapping a conversation row (it opens
      // in the thread, ready to continue), so the button is New-only.
      convListHTML = `<div class="mobile-conv-list-view">
        <div class="rail-mode mconv-mode" role="tablist">
          <button class="rail-mode-btn${_mode === 'chats' ? ' on' : ''}" role="tab"
            onclick="setRailMode('${esc(p.id)}','chats')">Chats</button>
          <button class="rail-mode-btn${_mode === 'topics' ? ' on' : ''}" role="tab"
            onclick="setRailMode('${esc(p.id)}','topics')">Topics</button>
          <button class="rail-mode-btn${_mode === 'channel' ? ' on' : ''}" role="tab"
            onclick="setRailMode('${esc(p.id)}','channel')"
            title="Everyone who's worked in this project">Channel</button>
        </div>
        <div class="mconv-search-wrap">
          <svg class="mconv-search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2"/><line x1="16.5" y1="16.5" x2="21" y2="21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          <input type="text" class="mconv-search" id="mconv-search-${esc(p.id)}" placeholder="${_mode === 'topics' ? 'Search topics&hellip;' : _mode === 'channel' ? (_channelPersonFilter[p.id] ? 'Search conversations&hellip;' : 'Search people&hellip;') : 'Search conversations&hellip;'}"
            spellcheck="false" value="${esc(_railQuery[p.id] || '')}" oninput="railSearch('${esc(p.id)}', this.value)">
        </div>
        <div class="conv-list-scroll${_mode === 'topics' ? ' mconv-topics' : ''}${_mode === 'channel' ? ' mconv-channel' : ''}">${
          _mode === 'topics' ? _railTopicsHTML(p)
          : _mode === 'channel' ? _railChannelHTML(p)
                             : mobileUserConversationsHTML(p, _mobileUserConvos)}</div>
        <div class="conv-newbtn-bar">
          <button class="conv-newbtn" onclick="newAgentTab('${esc(p.id)}')">&#43; New conversation</button>
        </div>
      </div>`;
    } else {
      convListHTML = '';
    }
    // The on-screen "← All conversations" bar was removed: the device back button
    // already returns to the conversations list (same mcBackFromConv route, via
    // history), so the bar was redundant chrome eating vertical space on a phone.
    convBackBar = '';
  } else {
    // Desktop tab strip: oldest conversation on the left, newest on the right
    // (append-on-right). `sessions` stays newest-first for auto-select + mobile
    // list semantics; we only flip the render order here. PINNED conversations
    // (chat-level, keyed on the durable claude_session_id) sort to the FRONT and
    // carry a pin marker — each chat is independently pinnable, not the project.
    const pinnedList = p.pinned_conversations || [];
    // Prefer the server-authoritative per-session `pinned` flag (set by
    // fetchAgentStatus); fall back to claude_session_id ∈ project list when the
    // agent status hasn't been fetched yet for this session.
    const _isPinnedConv = h => {
      const cache = agentStatusCache[h.sessionId] || {};
      if (typeof cache.pinned === 'boolean') return cache.pinned;
      const c = cache.claudeSessionId || '';
      return !!c && pinnedList.includes(c);
    };
    const _ordered = sessions.slice().reverse();
    const renderTab = h => {
      const isActive = h.sessionId === activeSessionId;
      const label = (h.task || '').substring(0, 30) || 'Session';
      const isOrch = isHivemindOrchestrator(h);
      const hmBadge = isOrch ? '<span class="hm-tab-badge" title="Hivemind orchestrator">&#x2B21;</span>' : '';
      const isInc = h.incognito || (agentStatusCache[h.sessionId] || {}).incognito;
      const incBadge = isInc ? '<span class="agent-tab-incognito" title="Incognito session — not saved to project memory or agent log">&#x1F576;&#xFE0F;</span>' : '';
      const isPinnedConv = _isPinnedConv(h);
      const pinMark = `<button class="agent-tab-pin-mark${isPinnedConv ? ' pinned' : ''}" onclick="event.stopPropagation();togglePinConversationSession('${esc(p.id)}','${esc(h.sessionId)}')" title="${isPinnedConv ? 'Unpin this chat' : 'Pin this chat'}" aria-label="${isPinnedConv ? 'Unpin this chat' : 'Pin this chat'}" aria-pressed="${isPinnedConv ? 'true' : 'false'}">&#x1F4CC;</button>`;
      return `<div class="agent-tab ${isActive ? 'active' : ''} ${isOrch ? 'hivemind-orch' : ''} ${isInc ? 'incognito' : ''} ${isPinnedConv ? 'pinned-conv' : ''}" onclick="switchAgentTab('${esc(p.id)}','${esc(h.sessionId)}')" title="${esc(h.task)}${isInc ? ' (incognito)' : ''}"${isOrch ? ` onmouseenter="showHmWorkerPopover(event,'${esc(h.sessionId)}')" onmouseleave="scheduleHideHmPopover()"` : ''}>
        ${hmBadge}${incBadge}<span class="agent-status-dot ${h.status}"></span>
        <span class="agent-tab-label">${esc(label)}</span>
        ${pinMark}
        <button class="agent-tab-close" onclick="event.stopPropagation();closeAgentTab('${esc(p.id)}','${esc(h.sessionId)}')" title="Close tab">&#10005;</button>
      </div>`;
    };
    const tabsHTML = [..._ordered.filter(_isPinnedConv), ..._ordered.filter(h => !_isPinnedConv(h))]
      .map(renderTab).join('');
    tabBar = sessions.length > 0
      ? `<div class="agent-tab-bar">${tabsHTML}<button class="agent-tab-new" onclick="newAgentTab('${esc(p.id)}')">+ New</button></div>`
      : '';
  }

  // Dispatch / session-picker screen.
  //  • mobile: only with 0 conversations or an explicit "New" — NOT when the
  //    drill-down list is showing (the list has its own "New" entry point).
  //  • desktop: classic — whenever no tab is active (the tab strip stays
  //    visible above it so you can switch/start from there).
  const noActiveTab = mobileMode
    ? ((sessions.length === 0) || wantNew)
    : (!activeSession || !activeSessionId);
  if (noActiveTab) {
    // Auto-load agent log and set default resume on first render.
    if (!agentLogCache[p.id]) {
      loadAgentLog(p.id);
    } else if (!mobileMode && !(p.id in pendingResumeId)) {
      // Desktop only: pre-select the most-recent chat to resume. On mobile this
      // hid the recents LIST behind a "Resuming: X" composer — the spec wants
      // the list shown so the user taps a chat to resume (Layer 2). (Regression:
      // §8 also removed the inline recents picker on mobile — restored below.)
      pendingResumeId[p.id] = getDefaultResumeId(p.id);
    }
    // Also load .jsonl-derived conversation list (captures interrupted sessions)
    if (!conversationsCache[p.id]) {
      loadConversations(p.id);
    }
  }
  // Mobile Layer-2 list is built from the durable conversations — ensure they
  // load whenever we're not inside a specific chat (even if live sessions exist,
  // where noActiveTab is false and the block above wouldn't fire).
  if (mobileMode && !activeSession && !wantNew && !conversationsCache[p.id]) {
    loadConversations(p.id);
  }
  // Provider capability gate — all defaults match claude (safe for existing
  // callers). An active session is bound to the provider it began with; the
  // +New screen reflects the provider chosen in the composer dropdown.
  const _pcaps = activeSession
    ? _getProviderCaps(activeSession.provider || p.provider || 'claude')
    : _getProviderCaps(_composerProvider(p));
  const picker = (noActiveTab && _pcaps.supports_session_resume) ? sessionPickerHTML(p.id) : '';

  // Resume indicator (shown when a prior session is selected AND provider supports resume)
  const resumeId = (_pcaps.supports_session_resume && pendingResumeId[p.id]) || null;
  const resumeIndicator = (noActiveTab && resumeId) ? (() => {
    const convos = conversationsCache[p.id] || [];
    const convo = convos.find(c => c.claude_session_id === resumeId);
    let label;
    if (convo) {
      label = (convo.label || convo.last_user || convo.first_user || '').substring(0, 60);
    } else {
      const entries = agentLogCache[p.id] || [];
      const entry = entries.find(e => e.claude_session_id === resumeId);
      label = entry ? (entry.task || '').substring(0, 50) : resumeId.substring(0, 12);
    }
    return `<div class="resume-indicator">Resuming: ${esc(label)} <span class="ri-clear" onclick="selectResumeSession('${esc(p.id)}','')">clear</span></div>`;
  })() : '';

  // Dispatch row (only shown on the +New screen, not when viewing an active session)
  const dispatchPreviews = noActiveTab ? renderAgentImagePreviews(p.id) : '';
  // Search-past-chats box + the bottom pane that fills the dead space below the
  // composer: search results when a query is active, else the inline preview of
  // the selected resume conversation. Same gate as the picker.
  const showResume = noActiveTab && _pcaps.supports_session_resume;
  const chatSearch = showResume ? chatSearchHTML(p.id) : '';
  const searchPane = showResume ? searchPaneInner(p.id) : '';
  const incOn = getIncognitoFor(p.id);
  const incForced = isIncognitoProject(p);
  const incognitoChip = noActiveTab ? _incognitoChipHTML(p) : '';  // shared with the §8 sheet
  const _dispatchPlaceholder = incOn ? 'Incognito — not saved to memory...' : 'Describe a task for the agent...';
  const _attachInput = _pcaps.image_input ? `
    <input type="file" multiple id="agent-attach-input-${esc(p.id)}" class="agent-attach-input"
      onchange="handleAgentAttachPick(event,'${esc(p.id)}')">` : '';
  // Desktop: ＋ on the LEFT opens the picker (mirrors the in-chat composer).
  // Mobile: keeps its 📎 to the right of the pill.
  const _dispatchPlusBtn = _pcaps.image_input ? `
    <button class="btn-composer-plus" type="button" title="Attach files or take a photo"
      onclick="triggerAgentAttach('${esc(p.id)}')">&#43;</button>` : '';
  const _attachBtn = _pcaps.image_input ? `
    <button class="btn-attach" type="button" title="Attach files or take a photo"
      onclick="triggerAgentAttach('${esc(p.id)}')">&#128206;</button>` : '';
  const _dispatchMicBtn = micBtnHTML(`agent-task-${esc(p.id)}`);
  // §5 starter chips: on a truly empty +New screen (no session AND not resuming),
  // show "What should Claude work on?" + 3 tappable prompts that FILL the dispatch
  // textarea (never auto-send). Gated on !resumeId so they don't appear next to
  // the "Resuming: …" indicator. Read this.dataset.chipText to dodge quote-escaping.
  const showEmptyState = noActiveTab && !resumeId;
  const emptyStateHTML = showEmptyState ? `<div class="composer-empty-state">
    <div class="ces-icon">&#128172;</div>
    <div class="ces-heading">What should Claude work on?</div>
    <div class="ces-sub">Describe a task in plain language.<br>The agent plans, edits files, and reports back.</div>
    <div class="ces-chips">${STARTER_CHIPS.map(c =>
      `<button type="button" class="ces-chip" data-chip-text="${esc(c.label)}" onclick="fillStarterChip('${esc(p.id)}', this.dataset.chipText)">
        <span class="ces-chip-icon">${c.icon}</span>
        <span class="ces-chip-label">${esc(c.label)}</span>
        <span class="ces-chip-chev">&#8250;</span>
      </button>`
    ).join('')}</div>
  </div>` : '';
  // §8: on MOBILE, collapse the pre-dispatch control cluster into a ＋ sheet;
  // DESKTOP keeps the inline chat-search/picker + composer-controls-row exactly
  // as before (these branches are '' on desktop → byte-identical output).
  // Item 6: the +New/resume composer no longer carries a ＋ — it's redundant
  // with the "Change" status line right below it (both open the options sheet).
  // Mobile send control: a circular ↑ (per the doc) instead of the green
  // Dispatch/Send text button. Desktop keeps the labelled button.
  const _dispatchBtn = mobileMode
    ? `<button class="btn-send-arrow" onclick="dispatchAgent('${esc(p.id)}')" title="${resumeId ? 'Continue' : 'Dispatch'}" aria-label="${resumeId ? 'Continue' : 'Dispatch'}">&#8593;</button>`
    : `<button class="btn-dispatch" onclick="dispatchAgent('${esc(p.id)}')">${resumeId ? 'Continue' : 'Dispatch'}</button>`;
  // Desktop keeps the inline recents/search on the +New screen; on mobile the
  // recents ARE the Layer-2 list (rendered above), so the compose view is just
  // the composer (+ resume preview / starter chips).
  // Desktop is now the 3-pane view — conversations + search live in the RAIL, so
  // the +New compose no longer carries the inline recents/search or resume picker.
  const _leadResume = '';
  const _trailControls = mobileMode
    ? _composerPlusStatusLineHTML(p, resumeId)
    : `<div class="composer-controls-row">${_composerProviderPicker(p)}${_composerModelPicker(p, resumeId)}${_composerCharacterPicker(p, resumeId)}${incognitoChip}</div>`;
  const _trailSearchPane = `<div class="agent-search-pane" id="agent-search-pane-${esc(p.id)}">${searchPane}</div>`;
  const _mobileSheet = mobileMode ? _composerSheetHTML(p, resumeId) : '';
  // Item 1 (prev batch): on mobile the resume PREVIEW goes ABOVE the composer
  // when a resume is armed (the rare fallback path). The "resume a past
  // conversation" browse section (search + picker + preview of all chats) is
  // GONE from the mobile +New menu — the Layer-2 conversation list already lists
  // every past chat and tapping one opens it in the thread. Desktop keeps it.
  const _mobilePreviewAbove = (mobileMode && resumeId) ? _trailSearchPane : '';
  const _mobileResumeSection = '';
  const _trailSearchPaneBelow = '';  // desktop 3-pane: no inline resume/search pane
  const _composerBlock = `<div class="agent-input-row agent-drop-zone"
    ondragover="handleAgentDragOver(event,this)"
    ondragenter="handleAgentDragOver(event,this)"
    ondragleave="handleAgentDragLeave(event,this)"
    ondrop="${_pcaps.image_input ? `handleAgentDrop(event,'${esc(p.id)}')` : 'event.preventDefault()'}">
    ${_attachInput}
    ${mobileMode ? '' : _dispatchPlusBtn}
    <textarea spellcheck="true" class="agent-task-input" id="agent-task-${esc(p.id)}" rows="1"
      data-project="${esc(p.id)}"
      placeholder="${_dispatchPlaceholder}"
      onkeydown="handleInputEnter(event,()=>dispatchAgent('${esc(p.id)}'),'${esc(p.id)}')"
      onpaste="${_pcaps.image_input ? `handleAgentPaste(event,'${esc(p.id)}')` : ''}"
    ></textarea>
    ${mobileMode ? _attachBtn : ''}
    ${_dispatchMicBtn}
    ${_dispatchBtn}
  </div>`;
  // Mobile compose = flex column: the preview / starter chips / resume picker
  // SCROLL in the top area, and the composer stays pinned to the BOTTOM edge
  // (Issue A). Desktop keeps the flat, top-anchored layout.
  const dispatchRow = (noActiveTab && !_mobileListMode)
    ? (mobileMode
        ? `<div class="mobile-compose-view">
            <div class="compose-scroll">${_mobilePreviewAbove}${emptyStateHTML}${_mobileResumeSection}</div>
            <div class="compose-bottom">
              ${resumeIndicator}
              ${_composerBlock}
              ${_trailControls}
              ${dispatchPreviews}
            </div>
            ${_mobileSheet}
          </div>`
        : `${_leadResume}${resumeIndicator}${emptyStateHTML}${_composerBlock}${_trailControls}${dispatchPreviews}${_trailSearchPaneBelow}`)
    : '';

  // Active tab content
  let tabContent = '';
  if (activeSession && activeSessionId) {
    const MAX_RENDER_LINES = 500;
    const outputLines = _skipAgentOutputSids.has(activeSessionId) ? '' : (() => {
      const fullBuf = (agentOutputBuffers[activeSessionId] || []).flatMap(l => l.trimStart().startsWith('> ') ? [l] : l.split('\n'));
      const forceAll = expandedOutputSessions.has(activeSessionId);
      const truncated = !forceAll && fullBuf.length > MAX_RENDER_LINES;
      const buf = truncated ? fullBuf.slice(-MAX_RENDER_LINES) : fullBuf;
      // §7: the latest ExitPlanMode gets the actionable plan card ONLY when the
      // session is genuinely plan-pending (server-authoritative live_agent, or
      // the client cache flag). Earlier/stale ExitPlanModes keep the inert
      // Show-Plan button so a reopened old session can't re-approve a dead plan.
      let _lastExitPlanIdx = -1;
      for (let _j = buf.length - 1; _j >= 0; _j--) { if (buf[_j].trim() === '[tool: ExitPlanMode]') { _lastExitPlanIdx = _j; break; } }
      const _planPending = (p.live_agent && p.live_agent.reason === 'plan') || !!(agentStatusCache[activeSessionId] && agentStatusCache[activeSessionId].waitingForPlanApproval);
      let result = '';
      let tableLines = [];
      let planBlock = '';   // accumulates non-tool lines for plan detection
      let planRawLines = []; // raw text for plan viewer
      let mermaidLines = null;  // null when not inside a ```mermaid block; array otherwise
      function flushTable() {
        if (tableLines.length === 0) return;
        if (isPipeTable(tableLines)) {
          planBlock += `<div class="hl-table">${buildPipeTable(tableLines)}</div>`;
        } else {
          planBlock += `<div class="hl-table-pre">${tableLines.map(l => formatTableLine(esc(l))).join('\n')}</div>`;
        }
        tableLines = [];
      }
      for (let _i = 0; _i < buf.length; _i++) {
        const line = buf[_i];
        // Mermaid block detection: ```mermaid ... ``` becomes a placeholder
        // div that's later rendered by _renderAllMermaidPlaceholders.
        if (mermaidLines === null && /^\s*```\s*mermaid\b/.test(line)) {
          flushTable();
          mermaidLines = [];
          planRawLines.push(line);
          continue;
        }
        if (mermaidLines !== null) {
          if (/^\s*```\s*$/.test(line)) {
            planBlock += _mermaidPlaceholderHTML(mermaidLines.join('\n'));
            planRawLines.push(line);
            mermaidLines = null;
          } else {
            mermaidLines.push(line);
            planRawLines.push(line);
          }
          continue;
        }
        // Plan detection: when ExitPlanMode is hit, collapse prior non-tool lines
        if (line.trim() === '[tool: ExitPlanMode]') {
          flushTable();
          if (planRawLines.length >= 2) planViewerContent[activeSessionId] = planRawLines;
          if (_i === _lastExitPlanIdx && _planPending) {
            // Actionable numbered card (Review reads planViewerContent set above).
            // Wrapper id is load-bearing: approvePlan() removes #plan-approve-<sid>.
            result += `<div class="plan-card" id="plan-approve-${esc(activeSessionId)}">${_planCardHTML(p.id, activeSessionId, planRawLines)}</div>`;
          } else if (planRawLines.length >= 2) {
            result += `<button class="plan-show-btn" onclick="openPlanViewer('${esc(activeSessionId)}')">&#128196; Show Plan</button>`;
            result += `<div class="plan-hidden-block">${planBlock}</div>`;
          } else {
            result += planBlock;
          }
          planBlock = ''; planRawLines = [];
          const cls = agentLineCls(line);
          result += `<div class="${cls}">${esc(line)}</div>`;
          continue;
        }
        if (isTableLine(line)) {
          tableLines.push(line);
          planRawLines.push(line);
        } else if (tableLines.length > 0 && line.trim() === '') {
          tableLines.push(line);
          planRawLines.push(line);
        } else {
          flushTable();
          const cls = agentLineCls(line);
          // Prompts get the image-aware escaper so an attached "[Screenshot:
          // C:\path\file.jpg]" renders as a thumbnail instead of literal text.
          // Tool / error / status / queued lines stay on plain esc — we don't
          // want path-like substrings in tool traces to render images.
          const html = cls.includes('agent-line-prompt')
            ? escPromptWithImages(line)
            : (cls.includes('agent-line-tool') || cls.includes('agent-line-error') || cls.includes('agent-line-followup') || cls.includes('agent-line-queued')
                ? esc(line) : formatAgentText(line));
          const div = `<div class="${cls}">${html}</div>`;
          // Tool lines and user prompts reset plan-block accumulator
          if (cls.includes('agent-line-tool') || cls.includes('agent-line-prompt')) {
            result += planBlock + div;
            planBlock = ''; planRawLines = [];
          } else {
            planBlock += div;
            planRawLines.push(line);
          }
        }
      }
      flushTable();
      result += planBlock; // flush any remaining non-plan text
      if (truncated) {
        result = `<div class="agent-line" style="text-align:center;padding:8px;opacity:0.6;cursor:pointer" onclick="expandAgentOutput('${esc(activeSessionId)}')">&#x25B2; ${fullBuf.length - MAX_RENDER_LINES} earlier lines — click to load all &#x25B2;</div>` + result;
      }
      return result;
    })();

    const stopBtn = (isRunning || st === 'idle' || st === 'error')
      ? `<button class="btn-stop" onclick="stopAgent('${esc(p.id)}','${esc(activeSessionId)}')">Stop</button>` : '';

    // §4 cold-render: derive the typing indicator declaratively from run-state
    // so it survives a refreshModal rebuild (turn_start/turn_complete skip
    // refreshModal, but switchAgentTab / modal reopen DO rebuild). Suppressed
    // while parked on a plan/question — those states aren't "generating".
    const showTyping = isRunning && !(activeSession?.waitingForPlanApproval || activeSession?.waitingForQuestion);
    const _act = agentActivityState[activeSessionId] || '';
    const _actKind = (_act === 'thinking' || _act === 'tool') ? _act : 'writing';
    const _actInner = _actIndicatorInner(_actKind);
    const typingHTML = showTyping
      ? `<div class="agent-line typing-indicator" data-act="${_actKind}" id="typing-${esc(activeSessionId)}">${_actInner}</div>` : '';

    // Same vocabulary AND same detected-wait distinction as the project
    // badge (see consoleStatusLabel) — activeSession carries the pending
    // plan/question flags that gate "Awaiting input".
    const consoleStatusLabelText = consoleStatusLabel(st, activeSession);

    const followupPreviews = renderAgentImagePreviews('fu_' + activeSessionId);
    const guardianState = activeSession?.guardianState;
    const cbTripped = activeSession?.circuitBreakerTripped;
    const guardianBanner = cbTripped
      ? `<div class="guardian-banner">
          <span>\u26A0 Auto-recovery exhausted after repeated failures.</span>
          <button class="btn-retry" onclick="guardianReset('${esc(p.id)}','${esc(activeSessionId)}','retry')">Try Again</button>
          <button class="btn-fresh" onclick="dispatchAgent('${esc(p.id)}')">Start Fresh</button>
        </div>`
      : guardianState === 'recovering'
        ? `<div class="guardian-banner"><span>\u23F3 Guardian is recovering the session...</span></div>`
        : guardianState === 'needs_attention'
          ? `<div class="guardian-banner">
              <span>\u26A0 Session needs attention.</span>
              <button class="btn-retry" onclick="guardianReset('${esc(p.id)}','${esc(activeSessionId)}','retry')">Retry</button>
              <button class="btn-fresh" onclick="guardianReset('${esc(p.id)}','${esc(activeSessionId)}','dismiss')">Dismiss</button>
            </div>`
          : '';
    // The hidden file input is shared by both the desktop ＋ and the mobile 📎.
    const _fuAttachInput = _pcaps.image_input ? `
            <input type="file" multiple id="agent-attach-input-fu_${esc(activeSessionId)}" class="agent-attach-input"
              onchange="handleAgentAttachPick(event,'fu_${esc(activeSessionId)}')">` : '';
    // Desktop 3-pane: a ＋ on the LEFT opens the picker (matches the PDF composer:
    // ＋ left, mic right). Mobile keeps its 📎 on the right of the pill.
    const _fuPlusBtn = _pcaps.image_input ? `
            <button class="btn-composer-plus" type="button" title="Attach files or take a photo"
              onclick="triggerAgentAttach('fu_${esc(activeSessionId)}')">&#43;</button>` : '';
    const _fuAttachBtn = _pcaps.image_input ? `
            <button class="btn-attach" type="button" title="Attach files or take a photo"
              onclick="triggerAgentAttach('fu_${esc(activeSessionId)}')">&#128206;</button>` : '';
    const _fuMicBtn = micBtnHTML(`agent-followup-${esc(activeSessionId)}`);
    const chatInput = (st === 'running' || st === 'completed' || st === 'stopped' || st === 'idle' || st === 'error')
      ? `${guardianBanner}<div class="agent-chat-input">
          ${followupPreviews}
          <div class="agent-chat-input-row agent-drop-zone"
              ondragover="handleAgentDragOver(event,this)"
              ondragenter="handleAgentDragOver(event,this)"
              ondragleave="handleAgentDragLeave(event,this)"
              ondrop="${_pcaps.image_input ? `handleAgentDrop(event,'fu_${esc(activeSessionId)}')` : 'event.preventDefault()'}">
            ${_fuAttachInput}
            ${mobileMode ? '' : _fuPlusBtn}
            <textarea spellcheck="true" class="agent-task-input" id="agent-followup-${esc(activeSessionId)}" rows="1"
              data-project="${esc(p.id)}"
              placeholder="${st === 'error' ? 'Type to continue from where it stopped...' : st === 'stopped' ? 'Type to resume conversation...' : st === 'running' ? 'Interrupt and redirect agent... (Enter to send)' : 'Send follow-up...'}"
              onkeydown="handleInputEnter(event,()=>sendFollowup('${esc(p.id)}','${esc(activeSessionId)}'),'${esc(p.id)}')"
              onpaste="${_pcaps.image_input ? `handleAgentPaste(event,'fu_${esc(activeSessionId)}')` : ''}"
            ></textarea>
            ${mobileMode ? _fuAttachBtn : ''}
            ${_fuMicBtn}
            ${mobileMode
              ? `<button class="btn-send-arrow" onclick="sendFollowup('${esc(p.id)}','${esc(activeSessionId)}')" title="Send" aria-label="Send">&#8593;</button>`
              : `<button class="btn-dispatch" onclick="sendFollowup('${esc(p.id)}','${esc(activeSessionId)}')">Send</button>`}
          </div>
        </div>` : '';

    const planFileBtn = (_pcaps.supports_plan_mode && activeSession && activeSession.planFile)
      ? (() => {
          const fname = activeSession.planFile.split(/[/\\]/).pop();
          const label = planFileTitle[activeSessionId] || planFileLabel(activeSession.task || fname);
          // Lazy-fetch heading if not cached yet
          if (!planFileTitle[activeSessionId]) {
            fetch(API_BASE + `/api/project/${esc(p.id)}/agent/plan-file?session=${esc(activeSessionId)}`)
              .then(r => r.ok ? r.json() : null)
              .then(d => {
                if (d && d.content) {
                  const m = d.content.match(/^#\s+(.+)/m);
                  if (m) {
                    let t = m[1].trim();
                    if (t.length > 40) t = t.slice(0, 37) + '...';
                    planFileTitle[activeSessionId] = t;
                    const c = document.getElementById(`plan-file-btn-${activeSessionId}`);
                    if (c) c.querySelector('.btn-plan-file')?.childNodes?.forEach?.((n, i) => {
                      if (i > 0) n.textContent = ' ' + t;
                    });
                    if (c && c.querySelector('.btn-plan-file')) {
                      c.querySelector('.btn-plan-file').innerHTML = `&#128196; ${esc(t)}`;
                    }
                  }
                }
              }).catch(() => {});
          }
          return `<button class="btn-plan-file" onclick="openPlanFileViewer('${esc(p.id)}','${esc(activeSessionId)}')" title="${esc(fname)}">&#128196; ${esc(label)}</button>`;
        })()
      : '';
    const isActiveOrch = isHivemindOrchestrator({sessionId: activeSessionId, hivemindRole: (activeSession||{}).hivemindRole});
    const orchHmId = isActiveOrch ? ((activeSession||{}).hivemindId || '') : '';
    // Provider badge — ALWAYS show in the chat header (unlike _providerBadge
    // which hides claude to keep tile/list rows quiet). Inside a chat the
    // user needs to know which model they're talking to at a glance, even
    // when it's the default claude. The model name (e.g. "sonnet",
    // "gemini-2.5-pro") rides along when set — empty string means the
    // provider's CLI default; we render just the provider in that case.
    const _provName = ((activeSession && activeSession.provider) || p.provider || 'claude').toLowerCase();
    const _provLabel = (_agentProviders || []).find(x => x.name === _provName)?.display_name || _provName;
    // When the auto-router picked a model for this session, prefer the routed
    // model (activeSession.model) over the configured one (activeSession.agentModel).
    // model_source carries the attribution: 'manual' = user-configured;
    // 'auto' = classifier-picked; 'fallback' = classifier failed, user model used.
    const _modelSource = (activeSession && activeSession.modelSource) || 'manual';
    const _routedModel = (activeSession && activeSession.model) || '';
    const _provModel = _routedModel || (activeSession && activeSession.agentModel) || '';
    const _provText = _provModel ? `${_provName} · ${_provModel}` : _provName;
    const _routerSuffixHTML = (_modelSource === 'auto')
      ? ` <span style="opacity:.7;font-size:10px;font-weight:600;letter-spacing:.4px;margin-left:4px">AUTO</span>`
      : (_modelSource === 'fallback')
        ? ` <span style="opacity:.7;font-size:10px;font-weight:600;letter-spacing:.4px;margin-left:4px;color:var(--orange,#f59e0b)" title="Classifier failed; using your configured model">FB</span>`
        : '';
    const _provTitle = _provModel
      ? `Provider: ${_provLabel} • Model: ${_provModel}${_modelSource === 'auto' ? ' (auto-picked by router)' : (_modelSource === 'fallback' ? ' (classifier failed, fallback)' : '')}`
      : `Provider: ${_provLabel} (default model)`;
    // In-chat model switcher (claude only — other runtimes ignore --model).
    // The badge becomes a dropdown: pick a model to PIN this conversation (auto-
    // router off for it until changed) or "Follow default" to un-pin. The pin is
    // applied on the next turn via a `-r` respawn that re-injects memory/rules.
    const _pinnedModel = (activeSession && activeSession.pinnedModel) || '';
    const _mShort = (id) => (typeof window._modelShortLabel === 'function' ? window._modelShortLabel(id) : id);
    let _provBadge;
    if (_provName !== 'claude') {
      _provBadge = `<span class="provider-badge prov-${esc(_provName.replace(/[^a-z]/g,''))}" title="${esc(_provTitle)}">${esc(_provText)}${_routerSuffixHTML}</span>`;
    } else {
      // The current model to show on the control: pinned wins, else the routed/
      // configured model, else "Default". Friendly label (Opus 5, not the id).
      const _curLabel = _pinnedModel
        ? _mShort(_pinnedModel)
        : (_provModel ? _mShort(_provModel) : 'Default');
      const _pinMark = _pinnedModel
        ? ` <span class="cms-pin" title="Pinned to this chat">&#128204;</span>`
        : '';
      const _suffix = _pinnedModel ? '' : _routerSuffixHTML;
      const _swTitle = `Model for THIS conversation${_pinnedModel ? ' (pinned)' : ''} — click to switch. ${_provTitle}`;
      // The menu itself is built + positioned in document.body on click (escapes
      // the chat-output overflow that was clipping an in-flow dropdown).
      _provBadge = `<button type="button" class="provider-badge model-switch prov-claude${_pinnedModel ? ' pinned' : ''}" title="${esc(_swTitle)}" onclick="toggleChatModelMenu(event,'${esc(p.id)}','${esc(activeSessionId)}')"><span class="cms-key">Model</span><span class="cms-val">${esc(_curLabel)}</span>${_suffix}${_pinMark} <span class="cms-caret" aria-hidden="true">&#9662;</span></button>`;
    }
    // Persona pill (Prompt Builder Phase 2) — visible for the whole chat
    // when a character was chosen at spawn; immutable for this chat's life.
    const _char = activeSession && activeSession.character;
    // The name the type chose for itself leads; the role follows in the
    // tooltip. A pill reading "prd-writer" names a file, not whoever is
    // actually talking to you.
    const _charRole = _char && (_char.display_name || _char.name);
    const _charName = _char && (_char.agent_name || _charRole);
    // An INHERITED persona (the project default, agent types Phase 1) must not
    // look identical to one the user picked — otherwise the agent appears to
    // have silently changed personality. The pill carries the source.
    const _charInherited = _char && _char.source === 'project';
    const _charEng = (_char && _char.engine) || {};
    const _charEngBits = ['provider', 'model', 'effort']
      .filter(k => _charEng[k]).map(k => _charEng[k]);
    const _charBadge = _charName
      ? `<span class="character-badge${_charInherited ? ' inherited' : ''}" title="${
          _charInherited ? 'Project default persona' : 'Persona for this chat'}: ${esc(_charName)}${
          _char.agent_name ? ' — the ' + esc(_charRole) + ' type' : ''}${
          _char.scope === 'global' ? ' (global)' : ''}${
          _charEngBits.length ? ' — runs on ' + esc(_charEngBits.join(' · ')) : ''
        } — fixed for the conversation; start a new chat to change it">${
          // The persona's own face when it has one. A generic mask on every
          // persona answers "there is a persona" but not "which one", which is
          // the question you actually have three messages into a chat.
          _char && _char.avatar ? window.avatarHTML(_char.avatar, 26, 'av-pill')
                                : '&#x1F3AD;'} ${esc(_charName)}${
          _charInherited ? '<span class="cb-src">default</span>' : ''}</span>`
      : '';
    // APK version pill — visible only inside the Capacitor APK (the native
    // injection sets window.__clayruneAPK). Lets the user confirm which
    // version is loaded and whether the native POST bridge is active. On
    // the desktop dashboard / regular browser the pill stays hidden.
    //
    // Auto-hide the GREEN (bridge-active) pill after 20s — it's
    // confirmation, not a permanent indicator. The RED (bridge-missing)
    // pill stays forever since it's a warning the user needs to act on.
    const _apk = (typeof window !== 'undefined') ? window.__clayruneAPK : null;
    if (_apk && _apk.bridge && !window._apkPillFirstShownAt) {
      window._apkPillFirstShownAt = Date.now();
      // Schedule a one-shot re-render at 20s so the pill goes away even
      // when nothing else triggers a refresh (idle session, no SSE events).
      setTimeout(() => { try { renderAgentConsole(); refreshModal(); } catch (_) {} }, 20500);
    }
    const _apkExpired = (_apk && _apk.bridge && window._apkPillFirstShownAt
        && (Date.now() - window._apkPillFirstShownAt > 20000));
    const _apkBadge = (_apk && !_apkExpired) ? `<span class="provider-badge" style="background:${_apk.bridge ? 'rgba(16,185,129,.12)' : 'rgba(239,68,68,.15)'};color:${_apk.bridge ? '#10b981' : '#ef4444'}" title="${_apk.bridge ? 'Native POST bridge active' : 'WARNING: native bridge missing — POSTs may hang after Doze'}">APK ${esc(_apk.version)}${_apk.bridge ? '' : ' ⚠'}</span>` : '';
    tabContent = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;flex-shrink:0">
        <span class="agent-status-dot ${st}"></span>
        <span class="agent-status-label ${st}">${esc(consoleStatusLabelText)}</span>
        ${_provBadge}
        ${_charBadge}
        ${_apkBadge}
        ${isActiveOrch ? '<span class="hm-orch-label">&#x2B21; Hivemind</span>' : ''}
        ${stopBtn}
        ${_pcaps.emits_usage ? `<span class="token-badge" id="session-metrics-${esc(activeSessionId)}">${activeSession ? sessionMetricsHTML(activeSession, _pcaps) : ''}</span>` : ''}
        <span class="agent-activity" id="agent-activity-${esc(activeSessionId)}"></span>
        ${_pcaps.supports_plan_mode ? `<span id="plan-file-btn-${esc(activeSessionId)}">${planFileBtn}</span>` : ''}
        <button class="btn-popout" onclick="openPlanViewer('${esc(activeSessionId)}')" title="Open output in viewer window">Pop Out &#8599;</button>
        <button class="btn-popout btn-browser" onclick="browserButtonClick('${esc(p.id)}')" title="Open a live browser pane">&#127760; Browser<span id="bp-badge" style="display:none;margin-left:5px;background:#4caf50;color:#000;font-size:10px;font-weight:700;border-radius:8px;padding:0 5px;line-height:15px;vertical-align:middle"></span></button>
        ${orchHmId ? `<button class="btn-hm-dash" onclick="openHivemindDashboard('${esc(orchHmId)}')">Dashboard &#8599;</button>` : ''}
      </div>
      <div class="agent-chat">
        <div class="agent-output" id="agent-output-${esc(activeSessionId)}">${outputLines}${typingHTML}</div>
        ${chatInput ? `<div class="agent-chat-separator"></div>${chatInput}` : ''}
      </div>`;
  }
  // (item 5) The mobile header "+ New" button was removed — new conversation is
  // launched from the Layer-2 list's bottom button (composer ＋ behavior: TBD).

  // Desktop (>960): 5f three-pane conversation view — a per-project recents RAIL
  // on the left + the thread/compose on the right. Mobile keeps its Layer-2
  // drill-down (tabBar / convList above). Redesign step 7.
  if (!mobileMode) {
    // Rail = ALL the project's user conversations (durable, transcript-derived,
    // + agent-log entries for old chats aged out of /conversations) — same source
    // as the mobile Layer-2 list, not just the open tabs.
    if (!conversationsCache[p.id]) loadConversations(p.id);
    if (!agentLogCache[p.id]) loadAgentLog(p.id);
    // _mode is computed once at the top of this function — the mobile Layer-2
    // list needs it too, and two declarations would drift.
    const _railConvos = (_mode === 'chats' && typeof _userInitiatedConvos === 'function')
      ? _userInitiatedConvos(p.id) : [];
    const railRows = _mode === 'topics'
      ? _railTopicsHTML(p)
      : _mode === 'channel'
      ? _railChannelHTML(p)
      : (_railConvos.length
          ? mobileUserConversationsHTML(p, _railConvos)
          : '<div class="agent-rail-empty">No conversations yet.</div>');
    const _railW = parseInt(localStorage.getItem('mc_rail_w') || '', 10);
    const _railStyle = (_railW >= 200 && _railW <= 560) ? ` style="width:${_railW}px"` : '';
    // Split-view: two conversation panes side by side. Active only when a 2nd
    // session is pinned AND it's a real, distinct, known session (guards against
    // a stale split id after a conversation is closed/detached).
    const _splitSid = splitAgentTab[p.id];
    const _splitActive = !!(_splitSid && activeSessionId && _splitSid !== activeSessionId
      && agentStatusCache[_splitSid]);
    const _mainInner = _splitActive
      ? `${splitPaneHTML(p, activeSessionId, true)}<div class="agent-split-divider"></div>${splitPaneHTML(p, _splitSid, false)}`
      : `${tabContent}${dispatchRow}`;
    try { _ensureThreadsCss(); } catch (e) {}  // never let a board bug break the panel render
    return `<div class="agent-panel agent-3pane">
      <div class="agent-rail"${_railStyle}>
        <button class="conv-newbtn agent-rail-new" onclick="newAgentTab('${esc(p.id)}')">&#43; New conversation</button>
        <div class="rail-mode" role="tablist">
          <button class="rail-mode-btn${_mode === 'chats' ? ' on' : ''}" role="tab"
            onclick="setRailMode('${esc(p.id)}','chats')">Chats</button>
          <button class="rail-mode-btn${_mode === 'topics' ? ' on' : ''}" role="tab"
            onclick="setRailMode('${esc(p.id)}','topics')"
            title="Subjects across this project's chats, deduplicated">Topics</button>
          <button class="rail-mode-btn${_mode === 'channel' ? ' on' : ''}" role="tab"
            onclick="setRailMode('${esc(p.id)}','channel')"
            title="Everyone who's worked in this project">Channel</button>
          ${_mode === 'topics' ? `<button class="rail-mode-board" title="Open the full topic board"
            onclick="openThreadsBoard('${esc(p.id)}')">&#9638;</button>` : ''}
        </div>
        <div class="agent-rail-search-wrap">
          <svg class="agent-rail-search-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="7" stroke="currentColor" stroke-width="2"/><line x1="16.5" y1="16.5" x2="21" y2="21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          <input type="text" class="agent-rail-search" id="rail-search-${esc(p.id)}" placeholder="${_mode === 'topics' ? 'Search topics&hellip;' : _mode === 'channel' ? (_channelPersonFilter[p.id] ? 'Search conversations&hellip;' : 'Search people&hellip;') : 'Search conversations&hellip;'}"
            spellcheck="false" value="${esc(_railQuery[p.id] || '')}" oninput="railSearch('${esc(p.id)}', this.value)">
        </div>
        <div class="agent-rail-list">${railRows}</div>
      </div>
      <div class="agent-rail-resizer" onmousedown="startRailResize(event)" title="Drag to resize"></div>
      <div class="agent-main${_splitActive ? ' split' : ''}">
        ${_mainInner}
      </div>
      ${_agentSurfacesHTML(p)}
    </div>`;
  }

  return `<div class="agent-panel">
    <div class="agent-panel-header">
      <div class="section-title" style="margin-bottom:0">Agent</div>
    </div>
    ${tabBar}
    ${convBackBar}
    ${convListHTML}
    ${tabContent}
    ${dispatchRow}
  </div>`;
}

// Draggable splitter between the desktop 3-pane recents rail and the thread.
// Width persists in localStorage (mc_rail_w) and is re-applied on every render.
let _railResize = null;
function startRailResize(e) {
  const panel = e.target.closest('.agent-3pane');
  const rail = panel && panel.querySelector('.agent-rail');
  if (!rail) return;
  e.preventDefault();
  _railResize = { rail, startX: e.clientX, startW: rail.offsetWidth };
  document.body.style.cursor = 'col-resize';
  document.body.style.userSelect = 'none';
}
function _railResizeMove(e) {
  if (!_railResize) return;
  const w = Math.max(200, Math.min(_railResize.startW + (e.clientX - _railResize.startX), 560));
  _railResize.rail.style.width = w + 'px';
}
function _railResizeEnd() {
  if (!_railResize) return;
  try { localStorage.setItem('mc_rail_w', String(_railResize.rail.offsetWidth)); } catch (e) {}
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
  _railResize = null;
}
document.addEventListener('mousemove', _railResizeMove);
document.addEventListener('mouseup', _railResizeEnd);
window.startRailResize = startRailResize;

// Rail search — filter the recents rail's conversation rows by name, in place.
// The query is preserved per-project (the input value + focus survive refreshes
// via refreshModalById); _applyRailFilter re-hides rows after each rebuild.
let _railQuery = {};
// Conversations whose TRANSCRIPT CONTENT matches the query (not just the label).
// Keyed by projectId → { q, csids:Set }. Populated by a debounced /search-chats
// call; drives the "reveal a row the label filter would hide" branch below.
let _railContentHits = {};
const _railSearchTimers = {};
const _railSearchSeq = {};

function railSearch(projectId, q) {
  _railQuery[projectId] = q;
  // Topics mode filters a list the renderer builds, not DOM rows the rail
  // filter walks — so it needs a re-render, and none of the transcript-content
  // machinery below applies to a 13-row digest.
  if (typeof railMode === 'function' && railMode(projectId) === 'topics') {
    if (typeof refreshModalById === 'function') refreshModalById(projectId);
    return;
  }
  _applyRailFilter(projectId);        // instant label filter — no round-trip
  // ALSO search transcript content, debounced. The label is a 90-char title;
  // "mockup.html" (and most things people search for) live in the MESSAGES, so
  // a label-only filter returned nothing for content that plainly exists. This
  // reveals those conversations when the scan lands.
  const query = (q || '').trim();
  clearTimeout(_railSearchTimers[projectId]);
  if (query.length < 2) { delete _railContentHits[projectId]; return; }
  _railSearchTimers[projectId] = setTimeout(() => _railContentSearch(projectId, query), 300);
}

async function _railContentSearch(projectId, q) {
  const seq = (_railSearchSeq[projectId] = (_railSearchSeq[projectId] || 0) + 1);
  const csids = new Set();
  try {
    const res = await fetch(API_BASE + `/api/project/${encodeURIComponent(projectId)}/search-chats?q=${encodeURIComponent(q)}`);
    if (res.ok) {
      const data = await res.json();
      for (const r of (data.results || [])) if (r.csid) csids.add(r.csid);
    }
  } catch (e) { /* best-effort — a failed scan just means label-only, as before */ }
  if (seq !== _railSearchSeq[projectId]) return;               // a newer keystroke superseded this
  if ((_railQuery[projectId] || '').trim() !== q) return;      // input moved on
  _railContentHits[projectId] = { q, csids };
  _applyRailFilter(projectId);
}

// The project's OWN modal element — scoping every query here is what stops one
// project's search from filtering another's rail (two modals open → typing in
// one hid rows in the other; the old code queried `document`).
// Anchored off the search input, whose id is project-scoped: robust, and it
// avoids findModalIdForProject, which lives in another ES module and is NOT
// reachable from here (a `typeof` guard would just silently return null and
// break the filter entirely). The two inputs (desktop rail / mobile Layer-2)
// carry different ids; either one leads to the same enclosing modal.
function _railScopeEl(projectId) {
  const input = document.getElementById('rail-search-' + projectId)
    || document.getElementById('mconv-search-' + projectId);
  if (!input) return null;
  return input.closest('.modal-window') || input.closest('.agent-panel');
}

function _applyRailFilter(projectId) {
  const raw = (_railQuery[projectId] || '').trim();
  const q = raw.toLowerCase();
  // Content hits count only while they match the CURRENT query (a stale set from
  // a previous keystroke must not reveal rows for a word no longer typed).
  const hits = _railContentHits[projectId];
  const contentSet = (hits && hits.q === raw) ? hits.csids : null;
  const scope = _railScopeEl(projectId);
  if (!scope) return;
  // Same per-project query drives BOTH the desktop 3-pane rail and the mobile
  // Layer-2 list (only one is mounted at a time) — both live inside this modal.
  scope.querySelectorAll('.agent-rail-list .conv-row, .conv-list-scroll .conv-row').forEach(row => {
    // data-search (_convSearchText) — the SAME text the hidden-count uses, so
    // the "Show N hidden" toggle can't promise rows this filter then hides.
    const hay = row.dataset.search || (row.textContent || '').toLowerCase();
    const labelMatch = !q || hay.includes(q);
    const contentMatch = !!(contentSet && row.dataset.csid && contentSet.has(row.dataset.csid));
    row.style.display = (labelMatch || contentMatch) ? '' : 'none';
  });
  // The toggle is rendered once, but search runs on EVERY keystroke with no
  // re-render — so its count/visibility must be refreshed here or it goes stale
  // and advertises hidden rows this filter would immediately hide.
  _syncHiddenToggle(projectId, q, scope);
}

// How many HIDDEN chats match `q` ('' = all of them).
function _hiddenMatchCount(projectId, q) {
  const hidden = _hiddenConvSet(projectId);
  if (!hidden.size) return 0;
  return _userInitiatedConvos(projectId, true)
    .filter(c => hidden.has(_convHideKey(c)))
    .filter(c => !q || _convSearchText(c).includes(q))
    .length;
}

// Identity key for the "hide this chat" toggle. claude_session_id when there
// is one (the historical key, still what's persisted for every existing
// hidden entry); mc_session_id otherwise — a non-Claude row (MC-929) has no
// claude_session_id at all, and every such row sharing the empty string would
// mean hiding ONE Gemini chat hid ALL of them.
function _convHideKey(c) { return (c && (c.claude_session_id || c.mc_session_id)) || ''; }

function _syncHiddenToggle(projectId, q, scope) {
  // Scope to the project's modal — a global query set EVERY open project's
  // toggle to this project's count (the same cross-project leak as the filter).
  const root = scope || _railScopeEl(projectId);
  if (!root) return;
  root.querySelectorAll('.conv-hidden-toggle').forEach(btn => {
    const n = _hiddenMatchCount(projectId, q);
    btn.style.display = n ? '' : 'none';
    btn.textContent = `${_showHiddenConvos[projectId] ? 'Hide' : 'Show'} ${n} hidden`;
  });
}
window.railSearch = railSearch;
window._applyRailFilter = _applyRailFilter;

// WhatsApp-Communities-style conversation list: shown in the Agent panel when
// a project has >1 active conversation. Each row drills into that
// conversation's chat; a back bar (rendered by agentPanelHTML) returns here.
// Manually-hidden conversations (per project, by claude_session_id) — the user
// can move any chat "sideways" out of the main list. Persisted in localStorage.
let _showHiddenConvos = {};   // projectId → reveal hidden rows this session
function _hiddenConvMap() {
  try { return JSON.parse(localStorage.getItem('mc_hidden_convos') || '{}') || {}; }
  catch (e) { return {}; }
}
function _hiddenConvSet(projectId) { return new Set(_hiddenConvMap()[projectId] || []); }
function hideConversation(event, projectId, csid) {
  if (event) event.stopPropagation();
  if (!csid) return;
  const all = _hiddenConvMap();
  const s = new Set(all[projectId] || []); s.add(csid);
  all[projectId] = Array.from(s);
  try { localStorage.setItem('mc_hidden_convos', JSON.stringify(all)); } catch (e) {}
  refreshModal();
}
function unhideConversation(event, projectId, csid) {
  if (event) event.stopPropagation();
  const all = _hiddenConvMap();
  all[projectId] = (all[projectId] || []).filter(x => x !== csid);
  try { localStorage.setItem('mc_hidden_convos', JSON.stringify(all)); } catch (e) {}
  refreshModal();
}
function toggleShowHiddenConvos(projectId) {
  _showHiddenConvos[projectId] = !_showHiddenConvos[projectId];
  refreshModal();
}
window.hideConversation = hideConversation;
window.unhideConversation = unhideConversation;
window.toggleShowHiddenConvos = toggleShowHiddenConvos;

// Permanently delete a conversation (mobile long-press → confirm). Unlike hide
// (per-device, reversible), this renames the transcript server-side so it's gone
// from the list on every device. Guarded server-side against live sessions.
function promptDeleteConversation(projectId, csid, label) {
  if (!csid) return;
  const name = (label || '').trim().replace(/\s+/g, ' ').slice(0, 60) || 'this conversation';
  if (!confirm(`Delete "${name}"?\n\nThis permanently removes the conversation.`)) return;
  fetch(API_BASE + `/api/project/${projectId}/conversation/${encodeURIComponent(csid)}`, { method: 'DELETE' })
    .then(r => r.json().then(d => ({ ok: r.ok, d })).catch(() => ({ ok: r.ok, d: {} })))
    .then(({ ok, d }) => {
      if (!ok) { try { showToast(d.error || 'Delete failed', 4000); } catch (_) {} return; }
      if (conversationsCache[projectId]) {
        conversationsCache[projectId] = conversationsCache[projectId].filter(c => (c.claude_session_id || '') !== csid);
      }
      try { refreshModalById(projectId); } catch (_) {}
    })
    .catch(() => { try { showToast('Delete failed — network error', 4000); } catch (_) {} });
}
window.promptDeleteConversation = promptDeleteConversation;

// Durable, transcript-derived conversations, filtered to user-initiated (agent/
// scheduled trigger types → the ⋮ Agent Log side flow) minus manually-hidden.
// Legacy agent/system chats (dispatched before source tracking) that no filter
// can catch by trigger/source — recognised by their task text. Applied ONLY to
// source-less rows so it never hides a real UI conversation going forward.
const _AGENT_LABEL_RE = /^\s*(\[scheduled run|\[task-notification|<task-notification|you are (?:the |a hivemind worker)|\[binding for this reply|base directory for this skill|weekly learning-loop|\[system)/i;
// Empty-task resume default (nobody typed a real message — the composer was
// blank on Continue) and trivial one-turn acknowledgements ("ok", "yes", …).
// These are transcript-only sessions with no substantive user content; they're
// not conversations worth surfacing. Whole-label match, source-less rows only.
const _NOISE_RESUME_RE = /^continue (?:where we|from where you) left off\.?$/i;
const _TRIVIAL_ACK_RE = /^(ok(ay)?|kk?|yes|yep|yeah|ya|no|nope|nvm|sure|thanks|thank you|ty|thx|continue|go|go on|go ahead|proceed|done|got it)[.!]?$/i;
// A conversation's display/filter label, resilient to a mid-conversation SKILL or
// system turn polluting `last_user`. When the user runs a /skill (or the harness
// injects a turn) AFTER their real message, that injected turn becomes the most
// recent "user" text — e.g. "Base directory for this skill: …" — which both
// mislabels the card AND makes _AGENT_LABEL_RE below filter the whole genuine
// chat out of the list (the "I refreshed and my awaiting-input chat vanished"
// bug). Prefer the cleaned last user message; if it's empty or is itself an
// injected agent/system turn, fall back to the FIRST real user message
// ("Few things…"). A truly-agent chat (first AND last are markers) still resolves
// to a marker, so the filter keeps hiding it — no regression.
function _bestConvLabel(c) {
  const last = stripSysPreamble((c && (c.last_user || c.label)) || '').trim();
  if (last && !_AGENT_LABEL_RE.test(last)) return last;
  const first = stripSysPreamble((c && c.first_user) || '').trim();
  if (first && !_AGENT_LABEL_RE.test(first)) return first;
  return last || first || '';
}
// A steward is a persistent conversational agent you WANT in your chat list —
// unlike a fire-once scheduled/agent run. So it's the one exception to the
// user-initiated gate. Recognised by the backend `steward` flag OR (for
// agent-log-merged rows that lack it) the [Steward cycle] marker on the task.
function _isStewardConvo(c) {
  return !!(c && (c.steward || /^\s*\[Steward cycle\]/.test(c.first_user || c.label || '')));
}
window._isStewardConvo = _isStewardConvo;

// `includeHidden` forces hidden chats into the result regardless of the
// per-project reveal toggle — used to count how many HIDDEN chats match the
// current search (see mobileUserConversationsHTML).
function _userInitiatedConvos(projectId, includeHidden) {
  const AGENT_TRIGGERS = new Set(['schedule', 'hivemind_worker', 'hivemind_orchestrator', 'hivemind', 'auto', 'housekeeping']);
  const AGENT_SOURCES = new Set(['agent', 'api', 'cron']);  // programmatic dispatch → side flow
  const hidden = _hiddenConvSet(projectId);
  const showHidden = !!includeHidden || !!_showHiddenConvos[projectId];
  const _keep = (c) => {
    // Stewards are the one automated-trigger exception — always surfaced (unless
    // the user explicitly hid this one).
    if (_isStewardConvo(c)) return showHidden || !hidden.has(_convHideKey(c));
    if (AGENT_TRIGGERS.has(c.trigger_type || '')) return false;
    // MC-938 Phase 0: a persona dispatched without a browser Origin gets
    // auto-tagged source:agent (agent_routes.py:5567-5574) purely so it routes
    // to the side flow — that heuristic can't tell "curl/scheduler fired this"
    // from "a human picked Fenn from the composer and hit send". A `character`
    // on the row is proof a human chose an agent to talk to, so it overrides
    // the programmatic-source drop here. Genuinely automated triggers (the
    // AGENT_TRIGGERS check above) are unaffected — this only rescues rows that
    // already passed that gate.
    if (AGENT_SOURCES.has(c.source || '') && !c.character) return false;
    const src = c.source || '';
    // Test the label with agent-facing preambles (resume/continue + the per-turn
    // brevity directive) stripped, so a genuine user chat whose message merely
    // CARRIES a prepended "[BINDING…]" directive isn't misread as a system chat.
    const label = _bestConvLabel(c);
    if (!src && !label) return false;                              // nothing but a system preamble
    if (!src && _AGENT_LABEL_RE.test(label)) return false;         // legacy agent/system chat
    if (!src && _NOISE_RESUME_RE.test(label)) return false;         // empty-task "Continue where we left off."
    if (!src && (c.turns || 0) <= 1 && _TRIVIAL_ACK_RE.test(label)) return false;  // trivial 1-turn ack
    if (!showHidden && hidden.has(_convHideKey(c))) return false;
    return true;
  };
  const out = (conversationsCache[projectId] || []).filter(_keep);
  // Merge in agent-log entries whose transcript isn't in conversationsCache —
  // old chats that aged out of /conversations but are still resumable (this is
  // what the old resume picker surfaced; without it they'd be unreachable).
  const seen = new Set(out.map(c => c.claude_session_id).filter(Boolean));
  for (const e of (agentLogCache[projectId] || [])) {
    const csid = e.claude_session_id || '';
    if (!csid || seen.has(csid) || e.hivemind_ws_id) continue;
    const c = {
      claude_session_id: csid, mc_session_id: e.session_id || '',
      label: e.task || '', last_user: e.task || '', first_user: e.task || '',
      status: e.status || 'completed', turns: e.num_turns || 0,
      ts_relative: e.ts_relative || e.ts || '', trigger_type: e.trigger_type || '',
      source: e.source || '', live: false,
      // MC-938: without this, an aged-out persona chat merged in from the agent
      // log (rather than /conversations) would fail the character-carries-a-
      // human-pick override above and be dropped all over again.
      character: e.character || null,
    };
    if (!_keep(c)) continue;
    out.push(c);
    seen.add(csid);
  }
  return out;
}
// Bridged for the same reason as _isStewardConvo above: a headless regression
// harness (tools/smoke/conversation-persona-filter.mjs) needs to drive the
// filter directly against synthetic cache rows, and this module's top-level
// bindings aren't reachable from outside the ES module without one.
window._userInitiatedConvos = _userInitiatedConvos;

// Cross-reference the durable conversation rows against LIVE agent state so a
// chat that's currently working or awaiting the user shows it in the list.
// Keyed by BOTH claude_session_id (a resume reuses it under a fresh mc id) and
// mc_session_id. Value: 'waiting' (needs the user) | 'working' (running).
function _liveConvStates(p) {
  const byCsid = {}, byMcid = {};
  // MC sessions whose CURRENT transcript id we already know — their live state is
  // pinned to that one csid, so we must NOT also key it by mc_session_id. Claude
  // Code forks to a new transcript id on auto-compaction while keeping the same
  // mc_session_id, so a stale pre-compaction transcript shares the live session's
  // mc id. An mcid-keyed live state would paint "Working…" on that old fork too —
  // one running session showing as two live cards (the bug this guards).
  const mcidHasCsid = new Set();
  for (const sid in agentStatusCache) {
    const s = agentStatusCache[sid];
    if (!s || s.projectId !== p.id) continue;
    const waiting = !!(s.waitingForQuestion || s.waitingForPlanApproval);
    const working = s.status === 'running';
    if (!waiting && !working) continue;
    const st = waiting ? 'waiting' : 'working';
    if (s.claudeSessionId) {
      byCsid[s.claudeSessionId] = st;
      mcidHasCsid.add(sid);
    } else {
      // No transcript id yet (just dispatched) — mc_session_id is the only link.
      byMcid[sid] = st;
    }
  }
  // Server-authoritative fallback for a live session not yet in the cache. Skip
  // it when the cache already resolved this mc session to a specific transcript,
  // or it would re-introduce the mcid match this function deliberately avoids.
  const la = p.live_agent;
  if (la && la.session_id && !byMcid[la.session_id] && !mcidHasCsid.has(la.session_id)) {
    byMcid[la.session_id] = (la.reason === 'plan' || la.reason === 'question') ? 'waiting' : 'working';
  }
  return { byCsid, byMcid };
}
function _convLiveState(live, c) {
  const s = live.byCsid[c.claude_session_id || ''] || live.byMcid[c.mc_session_id || ''];
  if (s) return s;
  // Reload fallback: the /conversations row itself carries the awaiting-input
  // state (server-authoritative), so a chat waiting on the user keeps its badge
  // + top bubbling even before the /agent/status poll repopulates the live cache.
  if (c && (c.waiting_for_question || c.waiting_for_plan_approval)) return 'waiting';
  return null;
}

// MC-940 — repaint ONE rail row's live status in place.
//
// The Working…/Waiting-for-you pill lived only inside the full modal rebuild,
// and turn_start/turn_complete deliberately skip that rebuild (it recreates the
// chat textarea every turn — the measured cause of the mobile IME death,
// ~205ms/keystroke; see resume-preview.js). Nothing else repainted a rail row,
// so a row you were sitting on never showed that its agent had started, and —
// worse — never cleared once the turn finished. This is the rail's counterpart
// to updateAgentStatusUI(): touch only the nodes that carry status.
//
// Deliberately does NOT:
//   • re-sort the rail (see _rank) — reordering rows under the cursor mid-turn
//     is worse than a stale pill; ordering waits for the next real rebuild.
//   • write row.style.display — that belongs to _applyRailFilter, which matches
//     on data-search (the badge is not in it), so an in-place swap is filter-safe.
function updateRailRowStatus(sessionId) {
  const s = agentStatusCache[sessionId];
  if (!s) return;
  const waiting = !!(s.waitingForQuestion || s.waitingForPlanApproval);
  const state = waiting ? 'waiting' : (s.status === 'running' ? 'working' : null);
  // Same keying rule as _liveConvStates: once a session has resolved to a
  // transcript id, pin to THAT csid. A resumed/auto-compacted session shares one
  // mc_session_id with its stale forks, so an mcsid match would light them all.
  const q = v => String(v || '').replace(/["\\]/g, '');
  const sel = s.claudeSessionId
    ? `.conv-row[data-csid="${q(s.claudeSessionId)}"]`
    : `.conv-row[data-mcsid="${q(sessionId)}"]`;
  document.querySelectorAll(sel).forEach(row => {
    row.classList.toggle('conv-live-working', state === 'working');
    row.classList.toggle('conv-live-waiting', state === 'waiting');
    const rest = row.dataset.restStatus || 'completed';
    const dot = row.querySelector('.conv-name .agent-status-dot');
    if (dot) {
      const cls = state === 'waiting' ? 'needs-attention' : state === 'working' ? 'running' : rest;
      dot.className = `agent-status-dot ${cls}`;
      dot.title = state === 'waiting' ? 'Waiting for your reply'
                : state === 'working' ? 'Working…' : rest;
    }
    const time = row.querySelector('.conv-time');
    if (time) {
      if (state === 'waiting') time.innerHTML = '<span class="conv-live-badge waiting">Waiting for you</span>';
      else if (state === 'working') time.innerHTML = '<span class="conv-live-badge working">Working…</span>';
      else time.textContent = row.dataset.tsRelative || '';
    }
  });
  // Channel-mode roster row (MC-937 Phase 1): one row per PERSONA, not per
  // session, so it can't be matched by csid/mcsid like the block above —
  // match on the character it represents instead. Only the right-slot
  // content is touched, same restraint as the block above: which SECTION
  // (In the room / Bench) the row sits in is NOT updated here — reordering/
  // re-bucketing the roster mid-turn is exactly the "worse than a stale pill"
  // case the comment on this function already rules out. The next full rail
  // rebuild (mode switch, project reopen, poll-driven refreshModalById)
  // reconciles bucketing.
  const charKey = (s.character && s.character.name)
    ? (s.character.scope || 'global') + ':' + s.character.name : '';
  if (charKey) {
    document.querySelectorAll(`.channel-row[data-char-key="${q(charKey)}"]`).forEach(row => {
      const time = row.querySelector('.conv-time');
      if (!time) return;
      if (state === 'waiting') time.innerHTML = '<span class="conv-live-badge waiting">Waiting for you</span>';
      else if (state === 'working') {
        const kind = (agentActivityState[sessionId] === 'thinking') ? 'thinking' : 'tool';
        time.innerHTML = _actIndicatorInner(kind);
      } else {
        time.textContent = row.dataset.tsRelative || '';
      }
    });
  }
}
// static/js/*.js are ES modules — a top-level function is NOT global. The SSE
// handlers live in resume-preview.js and reach this through window.
window.updateRailRowStatus = updateRailRowStatus;

// Layer-2 list rows for the user's conversations. Tapping opens it in chat mode
// (openConversation): live → its thread; past → reconstructed thread ready to
// continue.
// The text a conversation row is searched against. SINGLE SOURCE OF TRUTH: it
// is stamped onto the row as data-search (what _applyRailFilter matches) AND
// used to count hidden matches for the toggle — so the count can never promise
// rows the filter would then hide.
// What a human recognises a chat BY is what they OPENED it with — "the one
// where I asked about the daily email" — not whatever was said most recently.
//
// The row title used the LAST message, so a long chat that wandered became
// unfindable. Measured on day_trading_engulfing_scanner: the chat that opens
// "wait... the daily email should also do the same" displayed as "I agree, the
// intention is to measure over time", and typing "daily email" into the rail
// filter matched nothing — while the chat sat at row 5 the whole time.
//
// Display only. `_bestConvLabel` keeps its last-message behaviour because the
// _keep() noise filters (trivial-ack, resume-nudge, agent-label) are tuned
// against it, and re-pointing those at the opening message would change which
// conversations are hidden — a much riskier edit than a title.
function _convTitle(c) {
  const first = stripSysPreamble((c && c.first_user) || '').trim();
  if (first && !_AGENT_LABEL_RE.test(first)) return first;
  return _bestConvLabel(c);
}

// The most recent thing said, for the sub-line — so the row still answers
// "where did this get to?" now that the title answers "what is it about?".
function _convRecent(c) {
  const last = stripSysPreamble((c && (c.last_user || c.label)) || '').trim();
  const first = stripSysPreamble((c && c.first_user) || '').trim();
  return (last && last !== first && !_AGENT_LABEL_RE.test(last)) ? last : '';
}

function _convSearchText(c) {
  const label = _isStewardConvo(c)
    ? '🧭 Steward' + (c.steward_objective ? ' — ' + String(c.steward_objective).split('\n')[0].slice(0, 60) : '')
    : (_convTitle(c) || '(empty conversation)').substring(0, 90);
  const meta = [c.ts_relative || '', c.turns ? `${c.turns} turn${c.turns !== 1 ? 's' : ''}` : ''].filter(Boolean).join(' · ');
  // Match on BOTH ends of the conversation. Filtering only the visible title
  // is why "daily email" returned nothing for a chat that opens with exactly
  // that phrase — the row was being tested against its last message alone.
  return `${label} ${_bestConvLabel(c)} ${_convRecent(c)} ${meta}`.toLowerCase();
}

// Resolve whether a conversation row is an incognito session. Incognito
// sessions are ephemeral server-side (no agent_log entry), so the row `c`
// carries no flag — we recover it from the LIVE session state instead:
// the cache/history entry (keyed by mc id, or matched on the transcript csid)
// still holds `incognito:true` while the session is live or recent.
function _convIsIncognito(c) {
  if (c && c.incognito) return true;
  const mcsid = (c && c.mc_session_id) || '';
  const csid = (c && c.claude_session_id) || '';
  if (mcsid && agentStatusCache[mcsid] && agentStatusCache[mcsid].incognito) return true;
  if (csid) {
    for (const sid in agentStatusCache) {
      const s = agentStatusCache[sid];
      if (s && s.claudeSessionId === csid && s.incognito) return true;
    }
  }
  const h = mcsid && agentHistory.find(x => x.sessionId === mcsid);
  return !!(h && h.incognito);
}

function mobileUserConversationsHTML(p, convos) {
  // mobileMode is a LOCAL const inside agentPanelHTML — not visible here. The
  // split-view affordance below needs it, so recompute from the shared source
  // (bare `mobileMode` threw ReferenceError, aborting the whole rail render).
  const mobileMode = (typeof isMobileChatList === 'function') ? isMobileChatList() : window.innerWidth <= 960;
  const hidden = _hiddenConvSet(p.id);
  const live = _liveConvStates(p);
  // The currently-open conversation (desktop 3-pane rail) gets a white card;
  // every other row stays transparent (shares the rail background). Match on
  // BOTH the mc session id and the (more stable across transcript/log sources)
  // claude session id, so the highlight sticks even after a session completes.
  const activeSid = activeAgentTab[p.id] || null;
  const activeCsid = (activeSid && agentStatusCache[activeSid] && agentStatusCache[activeSid].claudeSessionId) || null;
  // Bubble chats that need attention (waiting) then working ones to the top so
  // an active/awaiting conversation is never buried under older history.
  const _rank = c => { const s = _convLiveState(live, c); return s === 'waiting' ? 0 : s === 'working' ? 1 : 2; };
  const ordered = convos.map((c, i) => [c, i]).sort((a, b) => _rank(a[0]) - _rank(b[0]) || a[1] - b[1]).map(x => x[0]);
  const rows = ordered.map(c => {
    const csid = c.claude_session_id || '';
    const mcsid = c.mc_session_id || '';
    const hideKey = _convHideKey(c);
    const isHidden = hidden.has(hideKey);
    // Steward threads carry the raw "[Steward cycle] …" task as their label — ugly
    // and unrecognisable. Render a clean, identifiable name instead.
    const label = _isStewardConvo(c)
      ? esc('🧭 Steward' + (c.steward_objective ? ' — ' + String(c.steward_objective).split('\n')[0].slice(0, 60) : ''))
      : esc((_convTitle(c) || '(empty conversation)').substring(0, 90));
    const liveSt = _convLiveState(live, c);
    // The RESTING status — what the dot shows when the row is not live. Hoisted
    // out of the else branch because updateRailRowStatus() has to restore it
    // when a turn ends, and it is not recoverable from the DOM by then.
    const stt = c.live ? (c.status || 'running') : (c.status || 'completed');
    let dot, badge = '';
    if (liveSt === 'waiting') {
      dot = `<span class="agent-status-dot needs-attention" title="Waiting for your reply"></span>`;
      badge = `<span class="conv-live-badge waiting">Waiting for you</span>`;
    } else if (liveSt === 'working') {
      dot = `<span class="agent-status-dot running" title="Working…"></span>`;
      badge = `<span class="conv-live-badge working">Working…</span>`;
    } else {
      dot = `<span class="agent-status-dot ${esc(stt)}" title="${esc(stt)}"></span>`;
    }
    // Title now carries the subject, so the sub-line carries the latest turn —
    // the row answers both "what is this about?" and "where did it get to?".
    const _recent = _convRecent(c);
    const meta = [esc(c.ts_relative || ''), c.turns ? `${c.turns} turn${c.turns !== 1 ? 's' : ''}` : '',
                  _recent ? esc(_recent.slice(0, 70)) : ''].filter(Boolean).join(' · ');
    const incIcon = _convIsIncognito(c)
      ? '<span class="conv-inc-icon" title="Incognito — not saved to project memory or agent log">&#x1F576;&#xFE0F;</span>'
      : '';
    // MC-929: non-Claude providers (Gemini, etc.) keep no transcript, so past
    // turns of a dead session can only be shown read-only, and replying always
    // starts a brand-new session rather than truly resuming. Say so on the row
    // itself — a silent Resume-shaped control here would be a lie.
    const readonlyTag = (c.resume_mode === 'readonly' && !c.live)
      ? `<span class="conv-readonly-tag" title="${esc(c.provider || 'this provider')} keeps no transcript — history is read-only; replying starts a new session">read-only</span>`
      : '';
    // Who the chat was with. These rows are transcript-derived and had no
    // persona at all until /conversations started emitting one, so every chat
    // in the list looked identical — you had to open one to find out whether
    // it was the review with Fenn or the plan with Marlow.
    const _cChar = c.character;
    const _cWho = _cChar && (_cChar.agent_name || _cChar.display_name);
    const face = (_cChar && _cChar.avatar)
      ? `<span class="conv-face" title="${esc(_cWho || '')}${
          _cChar.deleted ? ' (persona since deleted)' : ''}">${
          window.avatarHTML(_cChar.avatar, 28)}</span>`
      : '';
    const hideBtn = isHidden
      ? `<button class="conv-hide" onclick="unhideConversation(event,'${esc(p.id)}','${esc(hideKey)}')" title="Move back to the list" aria-label="Unhide">&#8617;</button>`
      : `<button class="conv-hide" onclick="hideConversation(event,'${esc(p.id)}','${esc(hideKey)}')" title="Hide from this list" aria-label="Hide">&#10005;</button>`;
    // A RESUMED session reuses ONE mc_session_id across several claude
    // transcripts, so multiple rail rows can carry the same mcsid. Matching on
    // mcsid alone then lit up every sibling row white at once. When we know the
    // open conversation's claude_session_id, disambiguate on THAT (unique per
    // transcript); only fall back to the mcsid match when the csid is unknown.
    const isActive = (csid && activeCsid)
      ? (csid === activeCsid)
      : (mcsid && mcsid === activeSid);
    // Split-view affordance (desktop only): open this conversation as a 2nd pane
    // beside the current one. Only offered for a LIVE conversation that isn't the
    // one already open. Hidden on mobile (single-pane drill-down there).
    const _canSplit = !mobileMode && c.live && mcsid && !isActive && activeAgentTab[p.id];
    const splitBtn = _canSplit
      ? `<button class="conv-split" onclick="event.stopPropagation();openInSplit('${esc(p.id)}','${esc(csid)}','${esc(mcsid)}',${c.live ? 'true' : 'false'})" title="Open beside the current chat (split view)" aria-label="Open in split view">&#9707;</button>`
      : '';
    return `<div class="conv-row ${isHidden ? 'conv-hidden' : ''}${liveSt ? ' conv-live-' + liveSt : ''}${isActive ? ' active' : ''}" data-search="${esc(_convSearchText(c))}" data-csid="${esc(csid || '')}" data-mcsid="${esc(mcsid || '')}" data-ts-relative="${esc(c.ts_relative || '')}" data-rest-status="${esc(stt)}" onclick="openConversation('${esc(p.id)}','${esc(csid)}','${esc(mcsid)}',${c.live ? 'true' : 'false'})" title="${esc(c.label || '')}">
      ${face}
      <div class="conv-main">
        <div class="conv-top">
          <span class="conv-name">${dot}${incIcon}${label}</span>
          <span class="conv-time">${badge || esc(c.ts_relative || '')}</span>
        </div>
        <div class="conv-bot"><span class="conv-sub">${
          _cWho ? `<span class="conv-who">${esc(_cWho)}</span>` : ''}${readonlyTag}${esc(meta)}</span></div>
      </div>
      ${splitBtn}${hideBtn}
    </div>`;
  }).join('');
  // The toggle counts only the hidden chats MATCHING the current search. Using
  // hidden.size (the project-wide total) meant a search with no hidden matches
  // still advertised "Show N hidden" — and revealing them showed nothing,
  // because the search filter hid them again immediately.
  // The element is rendered whenever the project has ANY hidden chat (and
  // display-toggled by _syncHiddenToggle) so clearing the query can bring it
  // back — if we omitted it entirely there'd be no node left to restore.
  const _q = (_railQuery[p.id] || '').trim().toLowerCase();
  const hiddenCount = _hiddenMatchCount(p.id, _q);
  const toggle = hidden.size > 0
    ? `<button class="conv-hidden-toggle" style="${hiddenCount ? '' : 'display:none'}" onclick="toggleShowHiddenConvos('${esc(p.id)}')">${_showHiddenConvos[p.id] ? 'Hide' : 'Show'} ${hiddenCount} hidden</button>`
    : '';
  return `<div class="conv-list-header"><span class="conv-list-title">Conversations</span><span class="conv-list-count">${convos.length}</span></div>
    <div class="conv-list">${rows}</div>
    ${toggle}`;
}

// ── Channel mode (MC-937 Phase 1 — docs/CHANNEL_MODEL_SPEC.md) ─────────────
// Channel mode lists PEOPLE, not chats: "In the room" (a live, actively
// generating run) and "Bench" (everyone who has ever participated here,
// idle). Membership is DERIVED, never persisted — a conversation with a
// `character` is channel material (spec §10); there is no new storage and no
// retroactive attribution of the un-attributed rows Chats mode still owns.
//
// Sourced from conversationsCache (the server's own union of Claude
// transcripts + non-Claude agent-log rows, `/api/project/<id>/conversations`)
// rather than the fuller _userInitiatedConvos merge — that merge's extra
// agent-log rows only cover chats that aged out of the /conversations 20-row
// display cap, which spec §11 Phase 2 addresses head-on ("lift the 20-row
// cap for the channel view"). A persona whose only history is older than the
// cached 20 will surface on the Bench again once Phase 2 lands.
function _convCharKey(c) {
  const ch = c && c.character;
  if (!ch || !ch.name) return '';
  return (ch.scope || 'global') + ':' + ch.name;
}

// projectId -> "scope:name" of the roster row currently narrowing the rail to
// one person's conversations, or absent for the full roster (spec §3/§10;
// the real filtered STREAM is Phase 2 — see openChannelPerson below).
let _channelPersonFilter = {};

// Group this project's attributed conversations into {inRoom, bench}.
//   inRoom — persona has a session actively generating right now (status
//            'running' and NOT parked on the user) — the shimmer belongs to
//            an agent working, not one waiting on a reply.
//   bench  — everyone else who's ever participated, including a persona
//            that's idle-but-waiting-for-you (badge takes the timestamp
//            slot instead of moving it off the bench — spec §3 row anatomy).
// Live/waiting state reads agentStatusCache (mirrors _liveConvStates) rather
// than the conversation rows, so a session dispatched moments ago — before
// its first /conversations poll — still shows up live.
function _channelRoster(projectId) {
  const groups = {};
  for (const c of (conversationsCache[projectId] || [])) {
    const key = _convCharKey(c);
    if (!key) continue;
    const g = groups[key] || (groups[key] = { key, char: c.character, mtime: -1, tsRelative: '' });
    const mtime = c.mtime || 0;
    if (mtime >= g.mtime) { g.mtime = mtime; g.tsRelative = c.ts_relative || ''; g.char = c.character; }
  }
  const liveState = {};  // key -> {state:'working'|'waiting', sessionId}
  for (const sid in agentStatusCache) {
    const s = agentStatusCache[sid];
    if (!s || s.projectId !== projectId) continue;
    const key = _convCharKey({ character: s.character });
    if (!key) continue;
    const waiting = !!(s.waitingForPlanApproval || s.waitingForQuestion);
    const working = s.status === 'running' && !waiting;
    if (!waiting && !working) continue;
    // A session in progress but not yet reflected in conversationsCache still
    // belongs on the roster — seed a bare group so it isn't dropped.
    if (!groups[key]) groups[key] = { key, char: s.character, mtime: 0, tsRelative: '' };
    if (working) liveState[key] = { state: 'working', sessionId: sid };
    else if (!liveState[key] || liveState[key].state !== 'working') liveState[key] = { state: 'waiting', sessionId: sid };
  }
  const inRoom = [], bench = [];
  for (const key in groups) {
    const g = groups[key];
    const ls = liveState[key] || null;
    const row = { key: g.key, char: g.char, tsRelative: g.tsRelative, mtime: g.mtime, liveState: ls };
    (ls && ls.state === 'working' ? inRoom : bench).push(row);
  }
  const byRecency = (a, b) => (b.mtime || 0) - (a.mtime || 0);
  inRoom.sort(byRecency);
  bench.sort(byRecency);
  return { inRoom, bench };
}

// One roster row. `inRoom` picks the right-slot content: the shimmer
// Thinking/Working word (reusing the typing indicator's _actIndicatorInner,
// styled for the .conv-time slot — see .conv-time .act-word in app.css) for
// a live row, else the "Waiting for you" badge or the last-spoke relative
// time — same slot, never both (spec §3).
// data-char-key + data-ts-relative let updateRailRowStatus (MC-940) patch
// this row's right slot in place from the SAME three SSE call sites it
// already touches, without a second repaint path — see the extension there.
function _channelRowHTML(p, r, inRoom) {
  const ch = r.char || {};
  const name = esc(ch.agent_name || ch.display_name || ch.name || '');
  const role = esc(ch.display_name || ch.name || '');
  const face = window.avatarHTML(ch.avatar || '', 32);
  let right;
  if (inRoom) {
    const sid = r.liveState && r.liveState.sessionId;
    const kind = (sid && agentActivityState[sid] === 'thinking') ? 'thinking' : 'tool';
    right = _actIndicatorInner(kind);
  } else if (r.liveState && r.liveState.state === 'waiting') {
    right = `<span class="conv-live-badge waiting">Waiting for you</span>`;
  } else {
    right = esc(r.tsRelative || '');
  }
  const search = `${name} ${role}`.toLowerCase();
  return `<div class="conv-row channel-row" data-search="${esc(search)}" data-char-key="${esc(r.key)}" data-ts-relative="${esc(r.tsRelative || '')}"
      onclick="openChannelPerson('${esc(p.id)}','${esc(r.key)}')" title="${name}${ch.deleted ? ' (persona since deleted)' : ''}">
    <span class="conv-face">${face}</span>
    <div class="conv-main">
      <div class="conv-top">
        <span class="conv-name">${name}</span>
        <span class="conv-time">${right}</span>
      </div>
      <div class="conv-bot"><span class="conv-sub">${role}</span></div>
    </div>
  </div>`;
}

// Roster view, or — when a person is selected — that person's conversations
// and nothing else (reuses mobileUserConversationsHTML's own row renderer, so
// a filtered row is a real chat row: live status, hide/split buttons, and
// MC-940's in-place repaint all keep working unmodified).
function _railChannelHTML(p) {
  const filterKey = _channelPersonFilter[p.id] || null;
  if (filterKey) {
    const roster = _channelRoster(p.id);
    const person = roster.inRoom.concat(roster.bench).find(r => r.key === filterKey);
    const who = person ? esc(person.char.agent_name || person.char.display_name || person.char.name) : 'this agent';
    const convos = (conversationsCache[p.id] || []).filter(c => _convCharKey(c) === filterKey);
    const list = convos.length
      ? mobileUserConversationsHTML(p, convos)
      : `<div class="agent-rail-empty">No conversations with ${who} yet.</div>`;
    return `<button class="channel-back" onclick="clearChannelPersonFilter('${esc(p.id)}')">&larr; All people</button>${list}`;
  }
  const { inRoom, bench } = _channelRoster(p.id);
  if (!inRoom.length && !bench.length) {
    // Vanilla install (spec §8): default_character is null everywhere until
    // Claydo seeding ships (Phase 5, deliberately not part of this phase) —
    // an empty roster is the honest, correct state, not a bug to paper over
    // with a fake row.
    return `<div class="channel-empty">
      <div class="channel-empty-title">No one's on this channel yet</div>
      <div class="channel-empty-sub">Pick a Persona in the &#43; New conversation composer to hire someone — every conversation with a named persona joins the roster here.</div>
    </div>`;
  }
  const roomHTML = inRoom.length
    ? `<div class="channel-section-header">In the room<span class="channel-section-count">${inRoom.length}</span></div>
       ${inRoom.map(r => _channelRowHTML(p, r, true)).join('')}`
    : '';
  const benchHTML = bench.length
    ? `<div class="channel-section-header">Bench<span class="channel-section-count">${bench.length}</span></div>
       ${bench.map(r => _channelRowHTML(p, r, false)).join('')}`
    : '';
  return roomHTML + benchHTML;
}

// Clicking a roster row (spec §3/§4). Phase 2 makes this a real filtered
// STREAM; for Phase 1 "shows that person's conversations and nothing else"
// means: narrow the rail to their chats and open the most recent one, same
// as clicking any other chat row would.
function openChannelPerson(projectId, key) {
  _channelPersonFilter[projectId] = key;
  const convos = (conversationsCache[projectId] || []).filter(c => _convCharKey(c) === key);
  convos.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  if (convos.length) {
    const c = convos[0];
    openConversation(projectId, c.claude_session_id || '', c.mc_session_id || '', !!c.live);
  } else if (typeof refreshModalById === 'function') {
    refreshModalById(projectId);
  } else {
    refreshModal();
  }
}
window.openChannelPerson = openChannelPerson;

function clearChannelPersonFilter(projectId) {
  delete _channelPersonFilter[projectId];
  if (typeof refreshModalById === 'function') refreshModalById(projectId);
  else refreshModal();
}
window.clearChannelPersonFilter = clearChannelPersonFilter;

// ── Open Threads board (project-level) ──────────────────────────────────────
// A full-width overlay that groups a project's OPEN conversations into three
// buckets — Waiting on you / Running / Stalled — so interrupted work stops being
// buried under the recency list. Reuses the rail's own data + open/hide helpers;
// "Archive" IS the existing hide mechanism (localStorage 'mc_hidden_convos').
// Frontend-only.

function _threadState(c, live) {
  const ls = _convLiveState(live, c);
  if (ls === 'waiting' || c.waiting_for_question || c.waiting_for_plan_approval) return 'waiting';
  if (ls === 'working' || (c.live && (c.status === 'running' || c.status === 'idle' || !c.status))) return 'running';
  if ((c.status || '') === 'completed') return null;               // done — not "open"
  const st = c.status || '';
  if ((c.turns || 0) >= 2 && ['interrupted', 'stopped', 'error', 'idle', ''].includes(st)) return 'stalled';
  return null;                                                       // too trivial / empty
}

// Bucket a project's open threads. {waiting, running, stalled, doneCount}.
function _openThreads(projectId) {
  const live = _liveConvStates({ id: projectId });
  const b = { waiting: [], running: [], stalled: [], doneCount: 0 };
  for (const c of _userInitiatedConvos(projectId)) {                // excludes hidden + noise
    const st = _threadState(c, live);
    if (st) b[st].push(c);
    else if ((c.status || '') === 'completed') b.doneCount++;
  }
  return b;
}

function _openThreadsBadge(projectId) {
  try {
    const b = _openThreads(projectId);
    const n = b.waiting.length + b.running.length + b.stalled.length;
    return n ? `<span class="rail-threads-badge">${n}</span>` : '';
  } catch (e) { return ''; }
}

function _threadTitle(c) {
  if (_isStewardConvo(c)) return '\u{1F9ED} Steward' + (c.steward_objective ? ' — ' + String(c.steward_objective).split('\n')[0].slice(0, 50) : '');
  return (_convTitle(c) || '(empty conversation)').slice(0, 90);
}

function _threadCardHTML(pid, c, kind) {
  const csid = c.claude_session_id || '', mcsid = c.mc_session_id || '';
  // Open the conversation but LEAVE the board open (it's a triage surface — the
  // user picks the next thread from it). openConversation raises the project
  // modal; the board stays a click away.
  const open = `openConversation('${esc(pid)}','${esc(csid)}','${esc(mcsid)}',${c.live ? 'true' : 'false'})`;
  const meta = [esc(c.ts_relative || ''), c.turns ? `${c.turns} turn${c.turns !== 1 ? 's' : ''}` : ''].filter(Boolean).join(' · ');
  let acts;
  if (kind === 'waiting') acts = `<button class="tb-act p" onclick="${open}">${c.waiting_for_plan_approval ? 'Review plan' : 'Answer'}</button>`;
  else if (kind === 'running') acts = `<button class="tb-act go" onclick="${open}">Open</button>`;
  else acts = `<button class="tb-act" onclick="${open}">Resume</button><button class="tb-act" onclick="archiveThread(event,'${esc(pid)}','${esc(csid)}')">Archive</button>`;
  return `<div class="tb-card" onclick="${open}">
      <div class="tb-title">${esc(_threadTitle(c))}</div>
      <div class="tb-meta">${meta}</div>
      <div class="tb-acts" onclick="event.stopPropagation()">${acts}</div>
    </div>`;
}

function _threadColHTML(pid, label, cls, items, kind, emptyMsg) {
  const cards = items.length ? items.map(c => _threadCardHTML(pid, c, kind)).join('')
    : `<div class="tb-empty">${emptyMsg}</div>`;
  return `<div class="tb-col">
      <div class="tb-colhead ${cls}"><span class="tb-dot"></span>${label}<span class="tb-n">${items.length}</span></div>
      ${cards}
    </div>`;
}

function _threadsBoardHTML(pid) {
  const b = _openThreads(pid);
  const open = b.waiting.length + b.running.length + b.stalled.length;
  const archived = _hiddenConvSet(pid).size;
  const name = esc((allProjects.find(x => x.id === pid) || {}).name || pid);
  const mid = '__threads_' + pid;
  const summary = `<b>${open} open</b> · ${b.stalled.length} stalled · ${b.running.length} running · ${b.waiting.length} waiting on you`
    + `<span class="tb-muted"> — ${b.doneCount} done${archived ? `, ${archived} archived` : ''}</span>`;
  // .modal-header is the manager's drag handle; .modal-content gets resize from
  // the #modal-layer observer (interactions.js). Header buttons are drag-excluded.
  return `<div class="modal-header tb-head">
      <div class="tb-htext">
        <div class="tb-hrow"><span class="tb-h1">${name}</span> <span class="tb-h2">Open Threads</span></div>
        <div class="tb-summary">${summary}</div>
      </div>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${esc(mid)}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeThreadsBoard('${esc(pid)}')" title="Close (Esc)">&#10005;</button>
      </div>
    </div>
    <div class="tb-body">
      <div class="tb-cols">
        ${_threadColHTML(pid, 'Stalled', 'slate', b.stalled, 'stalled', 'No stalled threads — you’re caught up.')}
        ${_threadColHTML(pid, 'Running', 'green', b.running, 'running', 'No agents working right now.')}
        ${_threadColHTML(pid, 'Waiting on you', 'amber', b.waiting, 'waiting', 'Nothing needs a decision right now.<br>Questions &amp; plan-approvals land here.')}
      </div>
    </div>`;
}

// ── Topics digest (the board's content) ────────────────────────────────────
// Instead of one card per chat, show agent-synthesized TOPICS (GET/POST
// /api/project/<id>/topics). A subject discussed across many chats appears once
// with a gist; the user can mark topics done/archived.
let _topicsData = {};        // pid -> {topics|null, generated_at, error, loading}
let _topicsExpanded = {};    // "pid/topicId" -> bool (chat list open)
let _topicsShowArchived = {};

// ── Rail mode: chats vs topics ───────────────────────────────────────────────
// The rail lists one row per CHAT, which is the wrong unit once a project has
// history — the same subject spans many chats, and a thread that got split
// across two transcripts reads as two unrelated entries. The topic digest is
// the same navigation in ~13 rows instead of 128.
//
// Offered as a TOGGLE rather than a replacement: the digest is Haiku-
// synthesized and best-effort, so the chat list has to stay one click away.
// The preference is global (not per project) — it's a way of working, not a
// property of a project.
function railMode(pid) {
  if (_railModeOverride[pid]) return _railModeOverride[pid];
  try {
    const v = localStorage.getItem('mc_rail_mode');
    return (v === 'topics' || v === 'channel') ? v : 'chats';
  } catch (e) { return 'chats'; }
}
const _railModeOverride = {};

function setRailMode(pid, mode) {
  _railModeOverride[pid] = (mode === 'topics' || mode === 'channel') ? mode : 'chats';
  try { localStorage.setItem('mc_rail_mode', _railModeOverride[pid]); } catch (e) {}
  // Pull the cached digest on first switch — cheap (a JSON read), and without
  // it the rail would show "no digest" for a project that has one.
  if (_railModeOverride[pid] === 'topics' && !_topicsData[pid]) _loadTopics(pid);
  if (typeof refreshModalById === 'function') refreshModalById(pid);
  else refreshModal();
}

function _refreshRailIfTopics(pid) {
  if (railMode(pid) !== 'topics') return;
  if (typeof refreshModalById === 'function') refreshModalById(pid);
}

function openTopicNewest(pid, topicId) {
  const d = _topicsData[pid] || {};
  const t = (d.topics || []).find(x => x.id === topicId);
  if (!t) return;
  const byCsid = {};
  (conversationsCache[pid] || []).forEach(c => {
    if (c.claude_session_id) byCsid[c.claude_session_id] = c;
  });
  const cands = (t.chats && t.chats.length)
    ? t.chats.map(c => c.csid).filter(Boolean)
    : (t.chat_csids || []);
  // Newest chat in the topic is the one worth continuing. Fall back to the
  // first listed when none of them are in the loaded conversation cache (an
  // old topic whose chats have aged out) — still resumable by csid.
  let best = null, bestTs = -1;
  for (const cs of cands) {
    const ts = (byCsid[cs] || {}).mtime || 0;
    if (ts >= bestTs) { bestTs = ts; best = cs; }
  }
  best = best || cands[0];
  if (!best) return;
  const c = byCsid[best] || {};
  openConversation(pid, best, c.mc_session_id || '', !!c.live);
}

function _railTopicsHTML(p) {
  const pid = p.id;
  const d = _topicsData[pid] || {};
  // Name the duration. The synthesis is one cheap-model pass over up to 50
  // chats and measured ~2.5 minutes on this project; a bare spinner at that
  // length reads as a hang, which is exactly how the Refresh button was first
  // reported ("does not seem to respond").
  if (d.loading) {
    return `<div class="agent-rail-empty">Reviewing chats&hellip;<br>
      <span style="opacity:.7">this takes a minute or two</span></div>`;
  }
  if (!d.topics) {
    return `<div class="agent-rail-empty">No topic digest yet.
      <button class="rail-topic-refresh" onclick="reviewTopics('${esc(pid)}')">Build it</button></div>`;
  }
  // Resolve each topic's chats against the loaded conversation list once —
  // needed for both the ordering and the active highlight below.
  const byCsid = {};
  (conversationsCache[pid] || []).forEach(c => {
    if (c.claude_session_id) byCsid[c.claude_session_id] = c;
  });
  const csidsOf = t => (t.chats && t.chats.length)
    ? t.chats.map(c => c.csid).filter(Boolean)
    : (t.chat_csids || []);
  // Last time anything in this topic was touched. Prefer the live conversation
  // row's mtime; fall back to the timestamp the digest cached for that chat, so
  // a topic whose chats have aged out of /conversations still sorts sensibly
  // instead of sinking to the bottom.
  const touchedAt = t => {
    let best = 0;
    const cached = {};
    (t.chats || []).forEach(c => { if (c.csid && c.ts) cached[c.csid] = c.ts; });
    for (const cs of csidsOf(t)) {
      const live = (byCsid[cs] || {}).mtime;
      if (live) { best = Math.max(best, live * 1000); continue; }
      const ts = Date.parse(cached[cs] || '');
      if (!isNaN(ts)) best = Math.max(best, ts);
    }
    return best;
  };
  // The conversation the user is actually in — matched on the claude session id
  // (stable across the mc-session churn a resume causes), same basis the chat
  // rail uses to pick its active row.
  const activeSid = activeAgentTab[pid] || null;
  const activeCsid = (activeSid && agentStatusCache[activeSid]
                      && agentStatusCache[activeSid].claudeSessionId) || null;

  const q = (_railQuery[pid] || '').trim().toLowerCase();
  const list = (d.topics || [])
    .filter(t => (t.user_state || 'open') !== 'archived')
    .filter(t => !q || `${t.title} ${t.gist || ''}`.toLowerCase().includes(q))
    // Most recently touched first. Stable for ties (topics with no resolvable
    // timestamp keep their digest order rather than shuffling per render).
    .map((t, i) => [t, i])
    .sort((a, b) => (touchedAt(b[0]) - touchedAt(a[0])) || (a[1] - b[1]))
    .map(x => x[0]);
  // Say it plainly when the map is out of date rather than quietly serving a
  // stale one — the whole reason the backend now computes real staleness.
  const staleBar = d.stale
    ? `<div class="rail-topic-stale" title="${esc(d.stale_reason || '')}">Out of date
         <button class="rail-topic-refresh" onclick="reviewTopics('${esc(pid)}')">Refresh</button></div>`
    : '';
  const rows = list.map(t => {
    // Switching from Chats to Topics should show you where you are, not make
    // you hunt for it: the topic containing the open conversation carries the
    // same treatment a row gets on hover.
    const here = !!(activeCsid && csidsOf(t).includes(activeCsid));
    return `
    <div class="rail-topic${here ? ' active' : ''}" onclick="openTopicNewest('${esc(pid)}','${esc(t.id)}')"
         title="${esc(t.gist || '')}">
      <div class="rail-topic-title">${esc(t.title)}</div>
      <div class="rail-topic-gist">${esc((t.gist || '').slice(0, 110))}</div>
      <div class="rail-topic-meta">${here ? '<span class="rail-topic-here">you are here</span> &middot; ' : ''}${t.chat_count} chat${t.chat_count !== 1 ? 's' : ''}${
        (t.user_state || 'open') === 'done' ? ' &middot; done' : ''}</div>
    </div>`;
  }).join('');
  return staleBar + (rows
    || `<div class="agent-rail-empty">${q ? 'No matching topics.' : 'No topics yet.'}</div>`);
}

function _agoShort(iso) {
  try {
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  } catch (e) { return ''; }
}

function _topicChatsHTML(pid, t) {
  const byCsid = {};
  (conversationsCache[pid] || []).forEach(c => { if (c.claude_session_id) byCsid[c.claude_session_id] = c; });
  // Prefer the label/ts the backend stored in the topic cache, so an older chat
  // that's no longer in the loaded conversation list still shows a real title
  // (no more "not in recent cache"). If it IS loaded, use the richer live label.
  const list = (t.chats && t.chats.length) ? t.chats : (t.chat_csids || []).map(cs => ({ csid: cs }));
  const rows = list.map(ch => {
    const cs = ch.csid;
    const c = byCsid[cs];
    const label = esc((((c ? _bestConvLabel(c) : '') || ch.label || '(chat)')).slice(0, 80));
    const ts = c ? (c.ts_relative || '') : (ch.ts ? _agoShort(ch.ts) : '');
    const mcid = c ? (c.mc_session_id || '') : '';
    const live = c && c.live ? 'true' : 'false';
    return `<div class="tb-chat" onclick="event.stopPropagation();openConversation('${esc(pid)}','${esc(cs)}','${esc(mcid)}',${live})">${label}<span class="tb-chat-meta">${esc(ts)}</span></div>`;
  }).join('');
  return `<div class="tb-chats">${rows || '<div class="tb-chat-empty">no chats</div>'}</div>`;
}

function _topicCardHTML(pid, t) {
  const st = ['open', 'resolved', 'automated'].includes(t.status) ? t.status : 'open';
  const badgeCls = st === 'resolved' ? 'green' : st === 'automated' ? 'slate' : 'amber';
  const us = t.user_state || 'open';
  const chip = us === 'done' ? '<span class="tb-us done">Done</span>'
    : us === 'archived' ? '<span class="tb-us arch">Archived</span>' : '';
  const acts = us === 'open'
    ? `<button class="tb-act" onclick="event.stopPropagation();setTopicState('${esc(pid)}','${esc(t.id)}','done')">Mark done</button>
       <button class="tb-act" onclick="event.stopPropagation();setTopicState('${esc(pid)}','${esc(t.id)}','archived')">Archive</button>`
    : `<button class="tb-act" onclick="event.stopPropagation();setTopicState('${esc(pid)}','${esc(t.id)}','open')">Reopen</button>`;
  const exp = _topicsExpanded[pid + '/' + t.id];
  return `<div class="tb-topic${us !== 'open' ? ' tb-topic-muted' : ''}">
      <div class="tb-topic-head" onclick="toggleTopic('${esc(pid)}','${esc(t.id)}')">
        <span class="tb-badge ${badgeCls}">${esc(st)}</span>
        <div class="tb-topic-main">
          <div class="tb-topic-title">${esc(t.title)}${chip}</div>
          <div class="tb-topic-gist">${esc(t.gist || '')}</div>
        </div>
        <span class="tb-topic-count">${t.chat_count} chat${t.chat_count !== 1 ? 's' : ''}</span>
      </div>
      <div class="tb-topic-acts">${acts}</div>
      ${exp ? _topicChatsHTML(pid, t) : ''}
    </div>`;
}

function _topicsBoardHTML(pid) {
  const mid = '__threads_' + pid;
  const name = esc((allProjects.find(x => x.id === pid) || {}).name || pid);
  const d = _topicsData[pid] || {};
  const all = d.topics || [];
  const archivedN = all.filter(t => (t.user_state || 'open') === 'archived').length;
  const shown = all.filter(t => (t.user_state || 'open') !== 'archived' || _topicsShowArchived[pid]);
  const updated = d.loading ? 'Reviewing…' : (d.generated_at ? `updated ${_agoShort(d.generated_at)}` : 'not reviewed yet');
  const summary = (d.topics ? `${shown.length} topic${shown.length !== 1 ? 's' : ''} · ${updated}` : updated)
    + (d.error ? ` · <span style="color:var(--red,#f06060)">${esc(d.error)}</span>` : '');
  const header = `<div class="modal-header tb-head">
      <div class="tb-htext">
        <div class="tb-hrow"><span class="tb-h1">${name}</span> <span class="tb-h2">Topics</span></div>
        <div class="tb-summary">${summary}</div>
      </div>
      <div class="modal-window-controls" style="position:static;display:flex;gap:6px;align-items:center">
        <button class="tb-review" onclick="reviewTopics('${esc(pid)}')" ${d.loading ? 'disabled' : ''}>${d.loading ? 'Reviewing…' : '&#8635; Review'}</button>
        <button class="tb-review tb-sweep-btn" onclick="sweepBacklog('${esc(pid)}')" title="Propose closing backlog items covered by topics you marked done">&#129529; Sweep backlog</button>
        <button class="modal-minimize" onclick="minimizeModal('${esc(mid)}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeThreadsBoard('${esc(pid)}')" title="Close (Esc)">&#10005;</button>
      </div>
    </div>`;
  let body;
  if (d.loading && !d.topics) body = '<div class="tb-empty">Reviewing your chats…</div>';
  else if (!d.topics) body = `<div class="tb-empty">No topic review yet.<br><br><button class="tb-act go" onclick="reviewTopics('${esc(pid)}')">Review chats now</button></div>`;
  else if (!shown.length) body = `<div class="tb-empty">No open topics.${archivedN ? ` <button class="tb-linkbtn" onclick="toggleTopicsArchived('${esc(pid)}')">Show ${archivedN} archived</button>` : ''}</div>`;
  else body = `<div class="tb-topics">${shown.map(t => _topicCardHTML(pid, t)).join('')}</div>`
    + (archivedN ? `<div class="tb-archline"><button class="tb-linkbtn" onclick="toggleTopicsArchived('${esc(pid)}')">${_topicsShowArchived[pid] ? 'Hide' : 'Show'} ${archivedN} archived</button></div>` : '');
  const sw = _topicsSweep[pid];
  const sweep = (sw && (sw.loading || sw.matches || sw.note || sw.error)) ? _sweepPanelHTML(pid) : '';
  return header + `<div class="tb-body">${sweep}${body}</div>`;
}

// ── Phase 3: backlog sweep (propose-then-confirm; never auto-closes) ─────────
let _topicsSweep = {};   // pid -> {matches, selected:Set, loading, note, error}
function _sweepPanelHTML(pid) {
  const s = _topicsSweep[pid] || {};
  const dismiss = `<button class="tb-linkbtn" onclick="dismissSweep('${esc(pid)}')">Dismiss</button>`;
  if (s.loading && !s.matches) return `<div class="tb-sweep"><div class="tb-sweep-head">Scanning backlog…</div></div>`;
  if (s.error) return `<div class="tb-sweep"><div class="tb-sweep-head">Sweep failed: ${esc(s.error)} ${dismiss}</div></div>`;
  if (!(s.matches || []).length) return `<div class="tb-sweep"><div class="tb-sweep-head">${esc(s.note || 'No matching backlog items.')} ${dismiss}</div></div>`;
  const rows = s.matches.map(m => {
    const on = s.selected && s.selected.has(m.id);
    return `<label class="tb-sweep-row">
        <input type="checkbox" ${on ? 'checked' : ''} onchange="toggleSweepItem('${esc(pid)}','${esc(m.id)}')">
        <span class="tb-sweep-main"><span class="tb-sweep-text">${esc(m.text)}</span>
          <span class="tb-sweep-meta"><span class="tb-conf ${esc(m.confidence)}">${esc(m.confidence)}</span> &nbsp;${esc(m.topic)} — ${esc(m.reason)}</span></span>
      </label>`;
  }).join('');
  const n = s.selected ? s.selected.size : 0;
  return `<div class="tb-sweep">
      <div class="tb-sweep-head">Backlog items covered by done topics — review &amp; confirm ${dismiss}</div>
      ${rows}
      <div class="tb-sweep-foot"><button class="tb-act go" ${n && !s.loading ? '' : 'disabled'} onclick="confirmSweep('${esc(pid)}')">${s.loading ? 'Closing…' : `Close ${n} selected`}</button></div>
    </div>`;
}
async function sweepBacklog(pid) {
  _topicsSweep[pid] = { loading: true };
  refreshThreadsBoard(pid);
  try {
    const r = await fetch((window.API_BASE || '') + `/api/project/${encodeURIComponent(pid)}/topics/backlog-sweep`, { method: 'POST' });
    const d = await r.json();
    const sel = new Set((d.matches || []).filter(m => m.confidence !== 'low').map(m => m.id));  // pre-check high+medium
    _topicsSweep[pid] = { matches: d.matches || [], note: d.note || null, error: d.error || null, selected: sel, loading: false };
  } catch (e) { _topicsSweep[pid] = { loading: false, error: String(e) }; }
  refreshThreadsBoard(pid);
}
function toggleSweepItem(pid, id) {
  const s = _topicsSweep[pid]; if (!s || !s.selected) return;
  if (s.selected.has(id)) s.selected.delete(id); else s.selected.add(id);
  refreshThreadsBoard(pid);
}
function dismissSweep(pid) { delete _topicsSweep[pid]; refreshThreadsBoard(pid); }
async function confirmSweep(pid) {
  const s = _topicsSweep[pid]; if (!s || !s.selected || !s.selected.size) return;
  const ids = Array.from(s.selected);
  const byId = {}; (s.matches || []).forEach(m => byId[m.id] = m);
  _topicsSweep[pid] = Object.assign({}, s, { loading: true });
  refreshThreadsBoard(pid);
  let ok = 0;
  for (const id of ids) {
    try {
      await fetch((window.API_BASE || '') + `/api/project/${encodeURIComponent(pid)}/backlog/${encodeURIComponent(id)}`,
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'done' }) });
      try {
        await fetch((window.API_BASE || '') + `/api/project/${encodeURIComponent(pid)}/backlog/${encodeURIComponent(id)}/note`,
          { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: 'Closed via topic sweep' + (byId[id] && byId[id].topic ? ` (topic: ${byId[id].topic})` : '') }) });
      } catch (e) {}
      ok++;
    } catch (e) {}
  }
  delete _topicsSweep[pid];
  if (typeof showToast === 'function') showToast(`Closed ${ok} backlog item${ok !== 1 ? 's' : ''} from the sweep`);
  if (typeof refreshProjectBacklog === 'function') { try { refreshProjectBacklog(pid); } catch (e) {} }
  refreshThreadsBoard(pid);
}
window.sweepBacklog = sweepBacklog;
window.confirmSweep = confirmSweep;
window.toggleSweepItem = toggleSweepItem;
window.dismissSweep = dismissSweep;

async function _loadTopics(pid) {
  try {
    const r = await fetch((window.API_BASE || '') + `/api/project/${encodeURIComponent(pid)}/topics`);
    const d = await r.json();
    // Keep the topics even when the digest is stale, and carry the flag.
    //
    // This used to be `d.stale ? null : d.topics` — which was survivable only
    // because `stale` meant "no cache exists at all". Now that staleness is
    // honest (any new chat since the digest was built), that expression would
    // blank the board almost permanently and hide a digest that is still the
    // best map available. Show it, and say it's out of date.
    _topicsData[pid] = {
      topics: d.topics || [], generated_at: d.generated_at,
      stale: !!d.stale, stale_reason: d.stale_reason || '',
      error: d.error || null, loading: false,
    };
  } catch (e) { _topicsData[pid] = { topics: null, loading: false, error: String(e) }; }
  refreshThreadsBoard(pid);
  _refreshRailIfTopics(pid);
}
async function reviewTopics(pid) {
  _topicsData[pid] = Object.assign({ topics: null }, _topicsData[pid], { loading: true });
  refreshThreadsBoard(pid);
  // Paint the loading state in the RAIL too, not just the board. Without this
  // the rail's Refresh button looked dead: the synthesis is a synchronous
  // request that takes ~2.5 minutes on a project with 50 chats, and nothing on
  // screen changed for the whole of it.
  _refreshRailIfTopics(pid);
  try {
    const r = await fetch((window.API_BASE || '') + `/api/project/${encodeURIComponent(pid)}/topics/refresh`, { method: 'POST' });
    const d = await r.json();
    _topicsData[pid] = { topics: d.topics || [], generated_at: d.generated_at,
                         stale: false, stale_reason: '',
                         error: d.error || null, loading: false };
  } catch (e) {
    _topicsData[pid] = Object.assign({}, _topicsData[pid], { loading: false, error: String(e) });
  }
  refreshThreadsBoard(pid);
  _refreshRailIfTopics(pid);
}
async function setTopicState(pid, topicId, state) {
  const d = _topicsData[pid];
  if (d && d.topics) { const t = d.topics.find(x => x.id === topicId); if (t) t.user_state = state; }
  refreshThreadsBoard(pid);
  try {
    await fetch((window.API_BASE || '') + `/api/project/${encodeURIComponent(pid)}/topics/${encodeURIComponent(topicId)}/state`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state }) });
  } catch (e) {}
}
function toggleTopic(pid, topicId) { const k = pid + '/' + topicId; _topicsExpanded[k] = !_topicsExpanded[k]; refreshThreadsBoard(pid); }
function toggleTopicsArchived(pid) { _topicsShowArchived[pid] = !_topicsShowArchived[pid]; refreshThreadsBoard(pid); }
window.reviewTopics = reviewTopics;
window.setTopicState = setTopicState;
window.toggleTopic = toggleTopic;
window.toggleTopicsArchived = toggleTopicsArchived;

function openThreadsBoard(projectId) {
  const mid = '__threads_' + projectId;
  _ensureThreadsCss();
  if (!conversationsCache[projectId]) loadConversations(projectId);
  if (!agentLogCache[projectId]) loadAgentLog(projectId);
  if (openModals.has(mid)) { focusModal(mid); _loadTopics(projectId); return; }
  // Build as a managed .modal-window so it inherits drag (.modal-header),
  // resize (#modal-layer observer → makeResizable), focus/z-order, minimize and
  // Esc-close — i.e. behaves exactly like every other MC window.
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = mid;
  const content = document.createElement('div');
  content.className = 'modal-content tb-modal';
  content.innerHTML = _topicsBoardHTML(projectId);
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(mid, { projectId: null, element: win, minimized: false, zIndex: z });
  // Restore the user's remembered size / position / text-zoom. The manager's
  // own persistence (_setModalPref/_setModalZoom) skips "__" ids, so the board
  // keeps its own (mc_threads_state). Desktop only — mobile is full-screen.
  const desktop = !(window.innerWidth <= 960);
  const st = desktop ? _threadsLoadState() : null;
  content.style.width = (st && st.w ? Math.min(st.w, window.innerWidth - 20) : Math.min(1040, window.innerWidth - 40)) + 'px';
  content.style.height = (st && st.h ? Math.min(st.h, window.innerHeight - 40) : Math.min(600, window.innerHeight - 90)) + 'px';
  if (st && typeof st.left === 'number') {
    win.style.left = Math.max(0, Math.min(window.innerWidth - 100, st.left)) + 'px';
    win.style.top = Math.max(0, Math.min(window.innerHeight - 50, st.top)) + 'px';
  } else {
    centerModalElement(win);
  }
  if (st && st.font && typeof applyModalZoom === 'function') { modalZoomLevels[mid] = st.font; applyModalZoom(win, st.font); }
  focusModal(mid);
  _loadTopics(projectId);   // pull the cached digest (or show "review now")
  // Persist size + position + zoom on any change. One MutationObserver on the
  // win/content style attributes catches drag (win left/top), resize (content
  // w/h) AND ctrl+wheel zoom (content font-size) — debounced.
  if (_threadsGeomMO) { try { _threadsGeomMO.disconnect(); } catch (e) {} }
  _threadsGeomMO = new MutationObserver(() => {
    clearTimeout(_threadsGeomTimer);
    _threadsGeomTimer = setTimeout(() => _threadsSaveState(win), 300);
  });
  _threadsGeomMO.observe(win, { attributes: true, attributeFilter: ['style'] });
  _threadsGeomMO.observe(content, { attributes: true, attributeFilter: ['style'] });
}
let _threadsGeomMO = null, _threadsGeomTimer = null;
function _threadsLoadState() {
  try { return JSON.parse(localStorage.getItem('mc_threads_state') || 'null'); } catch (e) { return null; }
}
function _threadsSaveState(win) {
  if (!win || !win.isConnected || window.innerWidth <= 960) return;
  try {
    const c = win.querySelector('.modal-content');
    if (!c) return;
    const r = win.getBoundingClientRect();
    localStorage.setItem('mc_threads_state', JSON.stringify({
      left: Math.round(r.left), top: Math.round(r.top),
      w: Math.round(c.offsetWidth), h: Math.round(c.offsetHeight),
      font: parseFloat(c.style.fontSize) || 0,
    }));
  } catch (e) {}
}
function refreshThreadsBoard(projectId) {
  const entry = (typeof openModals !== 'undefined') ? openModals.get('__threads_' + projectId) : null;
  const content = entry && entry.element.querySelector('.modal-content');
  if (content) content.innerHTML = _topicsBoardHTML(projectId);
}
// projectId omitted → close whichever threads board is open (used after opening
// a conversation from a card).
function closeThreadsBoard(projectId) {
  if (typeof closeModalById !== 'function') return;
  if (projectId) { closeModalById('__threads_' + projectId); return; }
  for (const id of Array.from(openModals.keys())) {
    if (String(id).startsWith('__threads_')) closeModalById(id);
  }
}
function archiveThread(event, projectId, csid) {
  if (event) { event.stopPropagation(); event.preventDefault(); }
  hideConversation(event, projectId, csid);   // existing: persists to mc_hidden_convos + refreshModal
  refreshThreadsBoard(projectId);
}

// Inject the board's CSS once. Themed entirely via CSS vars so it tracks the
// active light/dark theme.
function _ensureThreadsCss() {
  if (document.getElementById('mc-threads-css')) return;
  const s = document.createElement('style');
  s.id = 'mc-threads-css';
  s.textContent = `
    /* --tbf drives every text size off the modal-zoom var (Ctrl/⌘+wheel or
       pinch sets --mc-zoom-font on .modal-content), so the board zooms like
       every other window; falls back to 13px when unzoomed. */
    .tb-modal{display:flex;flex-direction:column;min-width:560px;min-height:320px;padding:0;--tbf:var(--mc-zoom-font,13px)}
    .tb-head{cursor:move;display:flex;align-items:flex-start;gap:12px;padding:12px 14px;border-bottom:1px solid var(--border);flex:0 0 auto}
    .tb-htext{flex:1;min-width:0}
    .tb-hrow{display:flex;align-items:baseline;gap:8px}
    .tb-h1{font-size:calc(var(--tbf)*1.23);font-weight:800;color:var(--text)}
    .tb-h2{font-size:calc(var(--tbf)*0.92);color:var(--text-faint)}
    .tb-summary{font-size:calc(var(--tbf)*0.88);color:var(--text-dim);margin-top:3px}
    .tb-summary b{color:var(--text)} .tb-muted{color:var(--text-faint)}
    .tb-body{flex:1;min-height:0;overflow:auto;padding:14px}
    .tb-cols{display:flex;gap:12px;align-items:flex-start}
    .tb-col{flex:1;min-width:0;background:var(--bg);border:1px solid var(--border);border-radius:10px;overflow:hidden}
    .tb-colhead{padding:11px 13px;font-size:calc(var(--tbf)*0.92);font-weight:700;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--border)}
    .tb-dot{width:9px;height:9px;border-radius:50%;flex:0 0 auto}
    .tb-n{margin-left:auto;font-size:calc(var(--tbf)*0.85);font-weight:700;color:var(--text-faint);background:var(--surface);border-radius:10px;padding:1px 8px}
    .tb-colhead.amber{color:var(--amber)} .tb-colhead.amber .tb-dot{background:var(--amber)}
    .tb-colhead.green{color:var(--green)} .tb-colhead.green .tb-dot{background:var(--green)}
    .tb-colhead.slate{color:var(--text-dim)} .tb-colhead.slate .tb-dot{background:var(--text-faint)}
    .tb-card{padding:11px 13px;border-top:1px solid var(--border);cursor:pointer}
    .tb-card:first-of-type{border-top:none}
    .tb-card:hover{background:var(--surface)}
    .tb-title{font-size:var(--tbf);font-weight:600;color:var(--text);line-height:1.35;word-break:break-word}
    .tb-meta{font-size:calc(var(--tbf)*0.85);color:var(--text-faint);margin-top:4px}
    .tb-acts{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
    .tb-act{font-size:calc(var(--tbf)*0.85);font-weight:600;padding:4px 10px;border-radius:7px;border:1px solid var(--border);color:var(--text-dim);background:var(--surface);cursor:pointer}
    .tb-act:hover{border-color:var(--accent);color:var(--text)}
    .tb-act.go{border-color:var(--green);color:var(--green)}
    .tb-act.p{border-color:var(--amber);color:var(--amber)}
    .tb-empty{padding:22px 14px;text-align:center;color:var(--text-faint);font-size:calc(var(--tbf)*0.92);line-height:1.5}
    /* ── topic digest ── */
    .tb-review{font-size:calc(var(--tbf)*0.85);font-weight:600;padding:4px 10px;border-radius:7px;border:1px solid var(--accent);color:var(--accent);background:transparent;cursor:pointer}
    .tb-review:disabled{opacity:.55;cursor:default;border-color:var(--border);color:var(--text-faint)}
    .tb-topics{display:flex;flex-direction:column;gap:8px}
    .tb-topic{background:var(--bg);border:1px solid var(--border);border-radius:10px;overflow:hidden}
    .tb-topic-muted{opacity:.62}
    .tb-topic-head{display:flex;align-items:flex-start;gap:10px;padding:11px 13px;cursor:pointer}
    .tb-topic-head:hover{background:var(--surface)}
    .tb-topic-main{flex:1;min-width:0}
    .tb-topic-title{font-size:var(--tbf);font-weight:700;color:var(--text);display:flex;align-items:center;gap:7px;flex-wrap:wrap}
    .tb-topic-gist{font-size:calc(var(--tbf)*0.88);color:var(--text-dim);margin-top:3px;line-height:1.4}
    .tb-topic-count{font-size:calc(var(--tbf)*0.8);color:var(--text-faint);white-space:nowrap;flex:0 0 auto;margin-top:2px}
    .tb-badge{font-size:calc(var(--tbf)*0.72);font-weight:700;text-transform:uppercase;letter-spacing:.03em;padding:2px 7px;border-radius:20px;flex:0 0 auto;margin-top:1px}
    .tb-badge.amber{background:var(--amber-dim,rgba(240,180,41,.15));color:var(--amber)}
    .tb-badge.green{background:var(--green-dim,rgba(52,211,153,.15));color:var(--green)}
    .tb-badge.slate{background:var(--surface);color:var(--text-faint);border:1px solid var(--border)}
    .tb-us{font-size:calc(var(--tbf)*0.72);font-weight:700;padding:1px 6px;border-radius:20px}
    .tb-us.done{background:var(--green-dim,rgba(52,211,153,.15));color:var(--green)}
    .tb-us.arch{background:var(--surface);color:var(--text-faint);border:1px solid var(--border)}
    .tb-topic-acts{display:flex;gap:6px;padding:0 13px 11px;flex-wrap:wrap}
    .tb-chats{border-top:1px solid var(--border);padding:6px 13px 10px}
    .tb-chat{font-size:calc(var(--tbf)*0.85);color:var(--text-dim);padding:5px 6px;border-radius:6px;cursor:pointer;display:flex;justify-content:space-between;gap:8px}
    .tb-chat:hover{background:var(--surface);color:var(--text)}
    .tb-chat-meta{color:var(--text-faint);white-space:nowrap}
    .tb-chat-empty{color:var(--text-faint);cursor:default}
    .tb-chat-empty:hover{background:none;color:var(--text-faint)}
    .tb-linkbtn{background:none;border:none;color:var(--accent);font-size:calc(var(--tbf)*0.85);cursor:pointer;padding:2px 4px}
    .tb-archline{padding:8px 2px}
    /* ── backlog sweep ── */
    .tb-sweep-btn{border-color:var(--amber);color:var(--amber)}
    .tb-sweep{background:var(--bg);border:1px solid var(--amber);border-radius:10px;padding:10px 12px;margin-bottom:12px}
    .tb-sweep-head{font-size:calc(var(--tbf)*0.9);font-weight:700;color:var(--text);display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:6px}
    .tb-sweep-row{display:flex;gap:9px;align-items:flex-start;padding:6px 2px;cursor:pointer}
    .tb-sweep-row input{margin-top:3px;flex:0 0 auto}
    .tb-sweep-main{flex:1;min-width:0}
    .tb-sweep-text{font-size:calc(var(--tbf)*0.9);color:var(--text);display:block}
    .tb-sweep-meta{font-size:calc(var(--tbf)*0.8);color:var(--text-faint);display:block;margin-top:2px}
    .tb-conf{font-weight:700;text-transform:uppercase;font-size:calc(var(--tbf)*0.72)}
    .tb-conf.high{color:var(--green)} .tb-conf.medium{color:var(--amber)} .tb-conf.low{color:var(--text-faint)}
    .tb-sweep-foot{margin-top:8px}
    .rail-threads-badge{margin-left:6px;background:var(--accent);color:#fff;font-size:10px;font-weight:700;border-radius:10px;padding:0 6px}
    /* Shares the "+ New conversation" pill frame (.conv-newbtn.agent-rail-new)
       so it is EXACTLY the same size; we only override the colour to a
       secondary outline. Scoped to .agent-3pane so it beats .agent-3pane
       .agent-rail-new (equal specificity, later source order wins). */
    .agent-3pane .agent-rail-threads{margin-top:0;background:var(--surface);color:var(--accent);border-color:var(--accent)}
    .agent-3pane .agent-rail-threads:hover{background:var(--accent);color:#fff}`;
  document.head.appendChild(s);
}

window.openThreadsBoard = openThreadsBoard;
// Rail mode toggle — inline onclick handlers, so these MUST be bridged
// (ES-module top-level bindings are not global). Guarded by
// tools/smoke/inline-handler-scope-check.mjs.
window.railMode = railMode;
window.setRailMode = setRailMode;
window.openTopicNewest = openTopicNewest;
window.closeThreadsBoard = closeThreadsBoard;
window.archiveThread = archiveThread;

// Open a conversation in chat mode. Live/tracked → its thread; past chat with an
// MC session id → reconstruct its transcript into a read-only thread (ready to
// continue) and switch to THAT session; transcript-only / failure → arm resume.
// Deliberately does NOT reuse openProjectAtSession — its `activeAgentTab || sid`
// fallback dumped every tap back onto the currently-open chat.
async function openConversation(projectId, csid, mcSessionId, isLive) {
  // A resumed session reuses ONE mc_session_id across MULTIPLE claude
  // transcripts (e.g. a live idle tail + the completed run it continued from),
  // so several rail rows share this mcSessionId. Reusing the open tab blindly
  // meant clicking a *sibling* row just re-selected the already-open tab —
  // "clicking does nothing". Only take the fast open-tab path when the cached
  // tab actually represents THIS conversation (its claude_session_id matches
  // the row's, or the row carries no csid to distinguish by). Otherwise fall
  // through to the csid-keyed transcript reconstruct below.
  if (mcSessionId && agentStatusCache[mcSessionId]) {
    const _cachedCsid = agentStatusCache[mcSessionId].claudeSessionId || '';
    if (!csid || !_cachedCsid || _cachedCsid === csid) {
      switchAgentTab(projectId, mcSessionId);
      return;
    }
    // Shared mc id but a DIFFERENT transcript — skip the mc-based routes (both
    // the live seed and /session/<mc>/reconstruct resolve to the mc id's
    // CURRENT transcript, not this row's) and open it by csid instead.
    if (csid) { await _openConversationByCsid(projectId, csid); return; }
  }
  // SERVER-AUTHORITATIVE LIVE ROUTE. `live` comes from /conversations (the row's
  // claude_session_id matched an in-memory agent_session), so the chat IS alive
  // even when our client cache doesn't know it yet — the common case being a page
  // reload / cold modal open where fetchAgentStatus hasn't landed. Gating this on
  // the local cache alone sent a live chat down the DEAD-session path:
  // /session/<id>/reconstruct 409s ("session is live"), we fell through to the
  // transcript reconstruct, and the user got a read-only "send a message to
  // resume" thread keyed by the CSID — whose follow-up POST carried a session_id
  // the server had never issued, so /agent/send dispatched a brand-new chat with
  // no history. Seed the cache from the row and open the real session instead.
  if (isLive && mcSessionId) {
    const pName = (allProjects.find(x => x.id === projectId) || {}).name || projectId;
    // _liveSeeded: optimistic, from a possibly-stale rail row. Marked so the
    // detach pass can still retire it if the server doesn't list it (otherwise
    // a stale live:true row would leave a tab stuck on "Working…" forever).
    // Cleared implicitly — the server poll rebuilds the cache entry wholesale.
    agentStatusCache[mcSessionId] = { status: 'running', task: '', projectId,
      startedAt: '', claudeSessionId: csid || '', _liveSeeded: true };
    if (!agentHistory.find(h => h.sessionId === mcSessionId)) {
      agentHistory.unshift({ projectId, sessionId: mcSessionId, projectName: pName,
        task: '', status: 'running', startedAt: '' });
    }
    switchAgentTab(projectId, mcSessionId);  // fetchAgentStatus inside fills the real state
    return;
  }
  if (mcSessionId) {
    try {
      const rr = await fetch(API_BASE + `/api/project/${projectId}/session/${encodeURIComponent(mcSessionId)}/reconstruct`);
      if (rr.status === 409) {
        // Raced a session that just went live (rail row was stale). Same route.
        switchAgentTab(projectId, mcSessionId);
        return;
      }
      if (rr.ok) {
        const rd = await rr.json();
        agentStatusCache[mcSessionId] = {
          status: 'completed', task: rd.task || '', projectId,
          startedAt: rd.started_at || '', claudeSessionId: rd.claude_session_id || csid || '',
          _readOnlyRevived: true,
        };
        agentOutputBuffers[mcSessionId] = rd.log_lines || [];
        agentServerLines[mcSessionId] = (rd.log_lines || []).length;
        if (!agentHistory.find(h => h.sessionId === mcSessionId)) {
          const pName = (allProjects.find(x => x.id === projectId) || {}).name || projectId;
          agentHistory.unshift({ projectId, sessionId: mcSessionId, projectName: pName,
            task: rd.task || '', status: 'completed', startedAt: rd.started_at || '' });
        }
        switchAgentTab(projectId, mcSessionId);
        return;
      }
    } catch (e) { /* fall through to resume */ }
  }
  // Transcript-only conversation (no MC session id, e.g. an interrupted chat or
  // a fresh-start continuation): reconstruct straight from the claude_session_id.
  if (csid) { await _openConversationByCsid(projectId, csid); }
}
window.openConversation = openConversation;

// Open a conversation identified purely by its claude_session_id: reconstruct
// the transcript into the SAME read-only thread view the tracked chats use,
// keyed on the csid as a synthetic session id. This avoids the resume-compose
// path (which renders a blank page on Android WebView) and matches "tap opens it
// in chat mode ready to continue". Falls back to arming a resume if
// unreconstructable. Split out of openConversation so the shared-mc-id
// disambiguation path can reach it directly.
async function _openConversationByCsid(projectId, csid) {
  // A live conversation can reach here with NO mc_session_id (its
  // claude_session_id matched a running agent_session, but this row never
  // carried the MC id). If any cache entry already tracks THIS transcript as
  // live (running / awaiting the user), open that real tab. Otherwise we
  // fabricate a read-only "STOPPED" tab keyed on the csid while the sidebar
  // badge — driven by the same byCsid signal — correctly shows "Working…", a
  // desync that only a hard reload cleared. Predicate mirrors _liveConvStates().
  const liveSid = Object.keys(agentStatusCache).find(sid => {
    const s = agentStatusCache[sid];
    return s && s.projectId === projectId && s.claudeSessionId === csid &&
      (s.status === 'running' || s.waitingForQuestion || s.waitingForPlanApproval);
  });
  if (liveSid) { switchAgentTab(projectId, liveSid); return; }
  if (agentStatusCache[csid]) { switchAgentTab(projectId, csid); return; }
  try {
    const rr = await fetch(API_BASE + `/api/project/${projectId}/transcript/${encodeURIComponent(csid)}/reconstruct`);
    if (rr.status === 409) {
      // Server says this transcript belongs to a live session (race: the
      // cache scan above missed it). Open the live tab it points us at.
      const rd = await rr.json().catch(() => ({}));
      if (rd.session_id) { switchAgentTab(projectId, rd.session_id); return; }
    }
    if (rr.ok) {
      const rd = await rr.json();
      agentStatusCache[csid] = {
        status: 'completed', task: rd.task || '', projectId,
        startedAt: rd.started_at || '', claudeSessionId: csid,
        _readOnlyRevived: true,
      };
      agentOutputBuffers[csid] = rd.log_lines || [];
      agentServerLines[csid] = (rd.log_lines || []).length;
      if (!agentHistory.find(h => h.sessionId === csid)) {
        const pName = (allProjects.find(x => x.id === projectId) || {}).name || projectId;
        agentHistory.unshift({ projectId, sessionId: csid, projectName: pName,
          task: rd.task || '', status: 'completed', startedAt: rd.started_at || '' });
      }
      switchAgentTab(projectId, csid);
      return;
    }
  } catch (e) { /* fall through to resume */ }
  selectResumeSession(projectId, csid);
}
window._openConversationByCsid = _openConversationByCsid;

function conversationListHTML(p, sessions) {
  // Pinned chats (keyed on the durable claude_session_id) lead the list.
  const _pinnedList = p.pinned_conversations || [];
  const _isPin = h => {
    const cache = agentStatusCache[h.sessionId] || {};
    if (typeof cache.pinned === 'boolean') return cache.pinned;
    const c = cache.claudeSessionId || '';
    return !!c && _pinnedList.includes(c);
  };
  const _ordered = [...sessions.filter(_isPin), ...sessions.filter(h => !_isPin(h))];
  const rows = _ordered.map(h => conversationRowHTML(p, h)).join('');
  return `
    <div class="conv-list-header">
      <span class="conv-list-title">Conversations</span>
      <span class="conv-list-count">${sessions.length}</span>
    </div>
    <div class="conv-list">${rows}</div>`;
}

// One row in the conversation drill-down list. Mirrors the consolidated
// status vocabulary: ring/dot colour and sub-line come from the same
// resolver the badge/console use (running→working, detected plan/question→
// asking "Awaiting input", error→stuck, turn-done idle→done "All done").
function conversationRowHTML(p, h) {
  const cache = agentStatusCache[h.sessionId] || {};
  const sst = cache.status || h.status || 'idle';
  const waiting = cache.waitingForPlanApproval || cache.waitingForQuestion;
  const fs = sst === 'running' ? 'working'
    : waiting ? 'asking'
    : sst === 'error' ? 'stuck'
    : 'done';  // idle / completed / stopped — turn finished
  const isOrch = isHivemindOrchestrator(h);
  const isInc = h.incognito || cache.incognito;
  const name = esc((h.task || '').substring(0, 70) || 'Session');
  const when = esc(timeAgoJS(h.startedAt) || '');
  const statusText = esc(consoleStatusLabel(sst, cache));
  const convProv = h.provider || cache.provider || p.provider || 'claude';
  const _convChar = h.character || cache.character;
  // Who this chat is WITH. `agent_name` is the name the persona chose for
  // itself and the only one a human recognises — `display_name`/`name` are the
  // file stem, so a row for Dave used to read "dave".
  const _convCharName = _convChar
    && (_convChar.agent_name || _convChar.display_name || _convChar.name);
  const _convCharRole = _convChar && (_convChar.display_name || _convChar.name);
  // The row already had a 42px avatar slot with the status ring around it, and
  // it was spending it on a generic 💬 while the persona's real face sat unused
  // in a text tag. The face answers "which chat is this" from across the list;
  // the glyphs are the fallback, not the default. ⬡/🕶️ keep their text tags, so
  // nothing is lost by preferring the face.
  const glyph = (_convChar && _convChar.avatar)
    ? window.avatarHTML(_convChar.avatar, 34)
    : isOrch ? '⬡' : isInc ? '🕶️' : '💬';
  const tags = [
    isOrch ? '<span class="conv-tag orch">Hivemind</span>' : '',
    isInc ? '<span class="conv-tag inc">Incognito</span>' : '',
    // No mask glyph here any more — the face is in the avatar slot, and two
    // faces on one row is noise. The name stays: a figure is recognisable, a
    // name is searchable.
    _convCharName ? `<span class="conv-tag char" title="Persona: ${esc(_convCharName)}${
      _convCharRole && _convCharRole !== _convCharName ? ' — the ' + esc(_convCharRole) + ' type' : ''
    }">${esc(_convCharName)}</span>` : '',
    _providerBadge(convProv),
  ].join('');
  const isPinnedConv = (typeof cache.pinned === 'boolean')
    ? cache.pinned
    : (() => { const c = cache.claudeSessionId || ''; return !!c && (p.pinned_conversations || []).includes(c); })();
  const pinMark = `<button class="conv-pin-mark${isPinnedConv ? ' pinned' : ''}" onclick="event.stopPropagation();togglePinConversationSession('${esc(p.id)}','${esc(h.sessionId)}')" title="${isPinnedConv ? 'Unpin this chat' : 'Pin this chat'}" aria-label="${isPinnedConv ? 'Unpin this chat' : 'Pin this chat'}" aria-pressed="${isPinnedConv ? 'true' : 'false'}">&#x1F4CC;</button>`;
  return `
  <div class="conv-row ${isPinnedConv ? 'pinned-conv' : ''}" onclick="switchAgentTab('${esc(p.id)}','${esc(h.sessionId)}')" title="${esc(h.task || '')}">
    <div class="conv-av friendly-${fs}"><span class="conv-ring"></span>${glyph}</div>
    <div class="conv-main">
      <div class="conv-top">
        <span class="conv-name">${name}</span>
        ${pinMark}
        <span class="conv-time">${when}</span>
      </div>
      <div class="conv-bot">
        <span class="conv-sub"><span class="agent-status-dot ${sst}"></span>${statusText}</span>
        <span class="conv-badges">${tags}</span>
      </div>
    </div>
    <button class="conv-row-close" onclick="event.stopPropagation();closeAgentTab('${esc(p.id)}','${esc(h.sessionId)}')" title="Close conversation">&#10005;</button>
  </div>`;
}

// Return from a drilled-in conversation (or the New screen) to the list.
// Pure UI — also called by the popstate handler (which already consumed the
// L2 history entry). Do NOT touch history here.
function backToConvList(projectId) {
  // "Back to conversations" means the chat list, which lives in the Agent tab.
  // Reset the active modal tab so this also works from a non-Agent tab (e.g.
  // tapping Chats in the §1c bottom bar while on the Backlog tab) — otherwise
  // the user stays stranded on Backlog. No-op when already on the Agent tab.
  modalActiveTab[projectId] = 'agent';
  delete activeAgentTab[projectId];
  delete splitAgentTab[projectId];   // leaving the thread view exits split too
  delete agentConvNew[projectId];
  delete pendingResumeId[projectId];  // deselect any armed resume → back to the Layer-2 list
  refreshModal();
}

// On-screen "← All conversations" button: same UI as backToConvList, but
// also synthetically pops the L2 sentinel so the hardware-back stack stays
// in sync (next hardware back then closes the modal, not double-back).
function mcBackFromConv(projectId) {
  if (_mcConvHistoryActive) {
    _mcConvHistoryActive = false;
    _mcUnwindHistory(1);
  }
  backToConvList(projectId);
}

function switchAgentTab(projectId, sessionId) {
  // Drilling into a conversation exits "New" mode.
  delete agentConvNew[projectId];
  const wasOnList = !activeAgentTab[projectId];
  activeAgentTab[projectId] = sessionId;
  // Mobile always lands on the conversations list now, so entering ANY chat
  // (even the lone one) is a Layer-3 push → hardware-back returns to the list.
  // (Desktop: mcPushConvHistory no-ops.)
  if (wasOnList) {
    mcPushConvHistory();
  }
  refreshModal();
  // Recover anything this conversation missed while it was inactive: its SSE may
  // have closed (turn_complete, the Chromium 6-connection cap, or Doze parking),
  // so the buffer can be stale and a plain tab switch otherwise never re-fetches
  // — the agent's later output stays silently missing until a hard refresh.
  // fetchAgentStatus refills the buffer from server truth, idempotently repaints
  // the now-visible panel (the render-gap fix inside it), and reconnects the live
  // stream for a running or active-idle session. We deliberately use it rather
  // than a direct _reconcileAgentBuffer append: the append could race a
  // concurrent background poll's repaint and render the recovered lines twice,
  // whereas the clear-and-rebuild repaint is idempotent.
  //
  // But fetchAgentStatus's internal repaint is GATED on !agentEventSources[sid]
  // (it skips a session that still holds a live SSE, to avoid flickering the
  // actively-streaming foreground chat on every routine poll). An idle-but-
  // active conversation KEEPS its SSE open, so switching away and back left its
  // panel frozen on the pre-refill render until a hard page reload (2026-07-16
  // repro: switch away mid/after a turn, come back, last messages missing).
  // A deliberate switch is user-initiated and rare, so bypass that gate here:
  // once the buffer is server-authoritative, repaint the now-active panel from
  // it unconditionally. _repaintAgentOutput is a clear-and-rebuild, so it is
  // idempotent and safe even if the internal repaint already ran.
  fetchAgentStatus(projectId).then(() => {
    if (activeAgentTab[projectId] !== sessionId) return;
    _repaintAgentOutput(sessionId);
    // _repaintAgentOutput clears the whole output node and rebuilds it from the
    // buffer — but the "Thinking/Working" typing indicator lives INSIDE that
    // node (agentPanelHTML bakes it in as a child), so the repaint drops it.
    // Re-derive it from live run-state, exactly as agentPanelHTML does: show it
    // only while the session is generating (running) and not parked on a plan
    // or question. showTypingIndicator is idempotent and paints the right kind
    // (thinking/writing/tool) from agentActivityState.
    const c = agentStatusCache[sessionId] || {};
    if (c.status === 'running' && !c.waitingForPlanApproval && !c.waitingForQuestion) {
      showTypingIndicator(sessionId);
    }
  }).catch(() => {});
}

// ── Split view (desktop): two conversations of one project, side by side ─────
// The differentiator over N terminals: many agents, one console, watched at
// once. Panes REUSE the shared per-session machinery (agent-output-<sid>,
// appendAgentLine, _repaintAgentOutput, showTypingIndicator, sendFollowup,
// stopAgent) so streaming/repaint/typing "just work" for both. State:
// splitAgentTab[pid] = the 2nd pane's session id (primary stays activeAgentTab).

// A compact, self-contained conversation pane. Output node renders EMPTY and is
// filled after mount by refreshModalById's split hydrate (fresh) or preserved
// live across refreshes — same discipline as the switch-back repaint.
function splitPaneHTML(p, sid, isPrimary) {
  const s = agentStatusCache[sid] || {};
  const st = s.status || 'completed';
  const isRunning = st === 'running';
  const label = (s.task || '').substring(0, 44) || 'Conversation';
  const dot = `<span class="agent-status-dot ${esc(st)}"></span>`;
  const statusLbl = `<span class="agent-status-label ${esc(st)}">${esc(consoleStatusLabel(st, s))}</span>`;
  const stopBtn = (isRunning || st === 'idle' || st === 'error')
    ? `<button class="btn-stop" onclick="stopAgent('${esc(p.id)}','${esc(sid)}')">Stop</button>` : '';
  // ✕ closes THIS pane and keeps the other as the single view.
  const closeBtn = `<button class="agent-split-close" onclick="closeSplitPane('${esc(p.id)}','${esc(sid)}')" title="Close this pane">&#10005;</button>`;
  const composeEnabled = (st === 'running' || st === 'completed' || st === 'stopped' || st === 'idle' || st === 'error');
  const compose = composeEnabled ? `
      <div class="agent-chat-separator"></div>
      <div class="agent-chat-input"><div class="agent-chat-input-row">
        <textarea spellcheck="true" class="agent-task-input" id="agent-followup-${esc(sid)}" rows="1"
          data-project="${esc(p.id)}"
          placeholder="${isRunning ? 'Interrupt and redirect… (Enter)' : 'Send follow-up…'}"
          onkeydown="handleInputEnter(event,()=>sendFollowup('${esc(p.id)}','${esc(sid)}'),'${esc(p.id)}')"></textarea>
        <button class="btn-dispatch" onclick="sendFollowup('${esc(p.id)}','${esc(sid)}')">Send</button>
      </div></div>` : '';
  return `<div class="agent-split-pane${isPrimary ? ' primary' : ''}" data-sid="${esc(sid)}">
    <div class="agent-split-head">
      ${dot}<span class="agent-split-label" title="${esc(s.task || '')}">${esc(label)}</span>
      ${statusLbl}<span style="flex:1"></span>${stopBtn}${closeBtn}
    </div>
    <div class="agent-chat">
      <div class="agent-output" id="agent-output-${esc(sid)}"></div>
      ${compose}
    </div>
  </div>`;
}

// Open a rail conversation as the 2nd (split) pane. MVP: live sessions only.
function openInSplit(projectId, csid, mcSessionId, isLive) {
  let sid = null;
  if (mcSessionId && agentStatusCache[mcSessionId]) {
    sid = mcSessionId;
  } else if (isLive && mcSessionId) {
    // Seed the cache like openConversation's live route so SSE + status fill in.
    const pName = (allProjects.find(x => x.id === projectId) || {}).name || projectId;
    agentStatusCache[mcSessionId] = { status: 'running', task: '', projectId,
      startedAt: '', claudeSessionId: csid || '', _liveSeeded: true };
    if (!agentHistory.find(h => h.sessionId === mcSessionId)) {
      agentHistory.unshift({ projectId, sessionId: mcSessionId, projectName: pName,
        task: '', status: 'running', startedAt: '' });
    }
    sid = mcSessionId;
  }
  if (!sid) { if (typeof showToast === 'function') showToast('Split view supports live conversations', 2500); return; }
  const active = activeAgentTab[projectId];
  if (!active) { switchAgentTab(projectId, sid); return; }  // nothing to split against
  if (sid === active) return;  // can't split a conversation with itself
  splitAgentTab[projectId] = sid;
  refreshModal();
  // Hydrate both panes from server truth + connect their streams. The
  // refreshModalById split-hydrate already repainted fresh nodes; this makes
  // the buffers authoritative and (re)connects any missing SSE.
  fetchAgentStatus(projectId).then(() => {
    if (splitAgentTab[projectId] !== sid) return;
    for (const psid of [active, sid]) {
      _repaintAgentOutput(psid);
      const c = agentStatusCache[psid] || {};
      if (c.status === 'running' && !c.waitingForPlanApproval && !c.waitingForQuestion) {
        showTypingIndicator(psid);
      }
    }
  }).catch(() => {});
}

// Close one split pane; the OTHER pane becomes the single active conversation.
function closeSplitPane(projectId, closeSid) {
  const a = activeAgentTab[projectId], s = splitAgentTab[projectId];
  const keep = (closeSid === a) ? s : a;   // keep whichever pane wasn't closed
  delete splitAgentTab[projectId];
  if (keep) activeAgentTab[projectId] = keep;
  refreshModal();
  const sid = activeAgentTab[projectId];
  if (!sid) return;
  fetchAgentStatus(projectId).then(() => {
    if (activeAgentTab[projectId] !== sid) return;
    _repaintAgentOutput(sid);
    const c = agentStatusCache[sid] || {};
    if (c.status === 'running' && !c.waitingForPlanApproval && !c.waitingForQuestion) {
      showTypingIndicator(sid);
    }
  }).catch(() => {});
}

window.splitPaneHTML = splitPaneHTML;
window.openInSplit = openInSplit;
window.closeSplitPane = closeSplitPane;

function newAgentTab(projectId) {
  // Clear active tab and force the dispatch screen. agentConvNew keeps it
  // there even with sessions present (otherwise multi would fall back to
  // the list and single would auto-reselect the lone conversation).
  const wasOnList = !activeAgentTab[projectId] && agentConvNew[projectId] !== true;
  delete activeAgentTab[projectId];
  delete splitAgentTab[projectId];   // starting a new chat exits split view
  agentConvNew[projectId] = true;
  // The "New / Resume" screen is a sub-level of the list — push the L2 sentinel
  // so hardware-back returns to the list, not out (mobile always has a list now).
  if (wasOnList) {
    mcPushConvHistory();
  }
  // Set null explicitly — do NOT delete. Deleting causes agentPanelHTML to
  // auto-repopulate from the log cache on the very next refreshModal(), which
  // makes the subsequent dispatch resume the prior conversation instead of
  // starting fresh. null keeps the key present (skips auto-populate) while
  // still meaning "no resume" to dispatchAgent and sessionPickerHTML.
  pendingResumeId[projectId] = null;
  refreshModal();
  // #6: desktop-only auto-focus — on mobile this popped the keyboard the moment
  // you entered the +New screen, covering the composer/sheet.
  if (window.innerWidth > 960) setTimeout(() => document.getElementById(`agent-task-${projectId}`)?.focus(), 50);
}

// Which chat does a new message continue by default?
//
// This used to be "the newest agent-log row, whatever it was" — which meant an
// UNATTENDED run silently captured the user's next message. A steward cycle
// finished at 12:54; the next thing typed in that project resumed it, so hours
// of hand-driven work were appended to the steward's transcript and the whole
// thread then displayed under its `[Steward cycle]` label (the list labels a
// conversation by its FIRST user message). Scheduled, steward and night-review
// sessions are the machine's threads: continue them only by explicit choice.
//
// Also skips `synthesized` rows — those are backfilled from a transcript MC
// never dispatched (including, before the cwd fix, every `claude -p ok` auth
// probe), so defaulting into one resumes a session with no shared context.
const _UNATTENDED_TRIGGERS = new Set(['schedule', 'steward', 'night-review']);

function getDefaultResumeId(projectId) {
  const entries = (agentLogCache[projectId] || []).filter(e => !e.hivemind_ws_id);
  const runningSessions = getProjectSessions(projectId);
  const runningResumeIds = new Set(
    runningSessions.filter(h => h.resumedFrom).map(h => h.resumedFrom)
  );
  const eligible = e => e.claude_session_id
    && !runningResumeIds.has(e.claude_session_id)
    && !e.synthesized
    && !_UNATTENDED_TRIGGERS.has(e.trigger_type);
  for (const e of entries) {
    if (eligible(e)) return e.claude_session_id;
  }
  // Nothing attended to fall back to (a project whose only history is
  // scheduled runs). Start fresh rather than silently joining a machine
  // thread — the user can still pick one from the list.
  return null;
}

function selectResumeSession(projectId, claudeSessionId) {
  const was = pendingResumeId[projectId] || null;
  pendingResumeId[projectId] = claudeSessionId || null;
  // Back-stack: arming a resume (picker → preview) pushes a sub-level so
  // hardware-back returns to the picker; the UI "clear" unwinds it to stay in
  // sync (Issue B). Mobile only — mcPushResumeHistory no-ops on desktop.
  if (claudeSessionId && !was) {
    if (typeof mcPushResumeHistory === 'function') mcPushResumeHistory();
  } else if (!claudeSessionId && was) {
    if (typeof _mcResumeHistoryActive !== 'undefined' && _mcResumeHistoryActive) {
      _mcResumeHistoryActive = false;
      if (typeof _mcUnwindHistory === 'function') _mcUnwindHistory(1);
    }
  }
  refreshModal();
  // #6: desktop-only auto-focus (mobile keyboard should wait for an explicit tap).
  if (window.innerWidth > 960) setTimeout(() => document.getElementById(`agent-task-${projectId}`)?.focus(), 50);
}

// ── Typing indicator (§4, 2026-07-05) ──────────────────────────────────────
// A left bubble with three pulsing dots shown while the agent generates. It is
// an EPHEMERAL DOM node (id=typing-<sid>); it is never written to
// agentOutputBuffers, so a rebuild (refreshModal) drops it and re-derives it
// declaratively from run-state in agentPanelHTML. Both are exported on window
// so resume-preview.js (a separate module) can call them by bare name.
function hideTypingIndicator(sessionId) {
  document.getElementById(`typing-${sessionId}`)?.remove();
}
// Live activity per session: '' | 'thinking' | 'writing' | 'tool'. Only ever
// non-empty when the server's `activity_states_enabled` flag is on and the CLI
// emits partial-message deltas; otherwise every indicator is the plain dots and
// the behavior is byte-identical to before the experiment.
const agentActivityState = {};
// Inner content of the typing indicator, shared by the cold render
// (agentPanelHTML) and the live repaint so the two can't drift.
//   writing  → three pulsing dots ("typing the reply")
//   thinking → the word "Thinking", letters shimmering in a wave
//   tool     → the word "Working" (accent-tinted), same wave
// Dots carry .act-dot: a bare `.typing-indicator span` rule would otherwise
// also match each LETTER span and render them as 6px circles.
const _ACT_WORDS = { thinking: 'Thinking', tool: 'Working' };
function _actIndicatorInner(kind) {
  const word = _ACT_WORDS[kind];
  if (!word) return '<span class="act-dot"></span>'.repeat(3);
  // Per-letter stagger drives the wave; delay scales with position.
  const letters = word.split('').map((ch, i) =>
    `<span style="animation-delay:${(i * 0.07).toFixed(2)}s">${esc(ch)}</span>`).join('');
  return `<span class="act-word">${letters}</span>`;
}
function _paintTypingIndicator(div, state) {
  const kind = (state === 'thinking' || state === 'tool') ? state : 'writing';
  div.dataset.act = kind;
  div.innerHTML = _actIndicatorInner(kind);
}
// Is this session actively generating (→ the indicator belongs on screen)?
// agentStatusCache is the primary signal, but it can be stale or absent (the
// turn_start handler only writes `status` when an entry already exists), so a
// non-empty activity state is accepted as authoritative too: the server only
// reports activity while status == 'running' (see the SSE loop's
// `act = session['activity_state'] if status == 'running' else ''`).
function _isGenerating(sessionId) {
  const rc = agentStatusCache[sessionId] || {};
  if (rc.waitingForPlanApproval || rc.waitingForQuestion) return false;  // parked on user
  return rc.status === 'running' || !!agentActivityState[sessionId];
}
function setAgentActivity(sessionId, state) {
  agentActivityState[sessionId] = state || '';
  const div = document.getElementById(`typing-${sessionId}`);
  if (div) { _paintTypingIndicator(div, agentActivityState[sessionId]); return; }
  // No node on screen, but the agent just told us it's working. Re-create it
  // rather than dropping the signal: appendAgentLine removes the node on every
  // streamed line, and the server only re-emits `activity` on CHANGE — so a
  // single missed re-add (stale status cache) used to leave the icon gone for
  // the rest of the turn (e.g. a long tool run emits no further activity).
  if (!agentActivityState[sessionId]) return;   // '' = not generating
  if (!_isGenerating(sessionId)) return;
  showTypingIndicator(sessionId);               // paints with the state just stored
}
function showTypingIndicator(sessionId) {
  const el = document.getElementById(`agent-output-${sessionId}`);
  if (!el) return;
  if (document.getElementById(`typing-${sessionId}`)) return; // already shown
  const wasPinned = _isAgentOutputPinned(el, sessionId);
  const div = document.createElement('div');
  div.className = 'agent-line typing-indicator';
  div.id = `typing-${sessionId}`;
  _paintTypingIndicator(div, agentActivityState[sessionId]);
  el.appendChild(div);
  if (wasPinned) _scheduleAgentPinScroll(sessionId, el, false);
}

function appendAgentLine(sessionId, text) {
  const el = document.getElementById(`agent-output-${sessionId}`);
  if (!el) return;
  // The instant any real line streams in, the typing indicator is stale — drop
  // it before appending (runs on every recursive multiline call; idempotent).
  hideTypingIndicator(sessionId);
  const freshMount = !el.dataset.scrollInitialized;
  const wasPinned = freshMount || _isAgentOutputPinned(el, sessionId);
  // NOTE: do NOT set `el.style.display = 'block'` here. That inline style
  // wins over CSS and clobbers the mobile `@media (max-width: 960px)` rule
  // that sets `.agent-output { display: flex }` for chat-bubble layout —
  // with display:block, `align-self: flex-end` on `.agent-line-prompt` is a
  // no-op and user prompts render left-aligned with agent narration. The
  // `.agent-output:empty { display: none }` rule (CSS) is what we'd be
  // overriding, but appendChild below makes the element non-empty in the
  // same task, so the :empty rule stops matching before paint.

  // Split multi-line text blocks into individual lines
  // But keep user prompts (> ) as a single block so styling covers all lines
  if (text.includes('\n') && !text.trimStart().startsWith('> ')) {
    for (const line of text.split('\n')) {
      appendAgentLine(sessionId, line);
    }
    return;
  }

  // Mermaid diagram interception. Must come before all other line handling
  // so ```mermaid fences are caught even if they look like other syntaxes.
  if (_handleMermaidLine(sessionId, text, el)) {
    if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
    return;
  }

  // Group consecutive table lines into a single styled block
  if (isTableLine(text)) {
    let block = el.lastElementChild;
    if (block && (block.classList.contains('hl-table') || block.classList.contains('hl-table-pre'))) {
      block._rawLines.push(text);
    } else {
      block = document.createElement('div');
      block._rawLines = [text];
      el.appendChild(block);
    }
    // Rebuild: pipe tables get <table>, box-drawing stays pre-formatted
    if (isPipeTable(block._rawLines)) {
      block.className = 'hl-table';
      block.innerHTML = buildPipeTable(block._rawLines);
    } else {
      block.className = 'hl-table-pre';
      block.innerHTML = block._rawLines.map(l => formatTableLine(esc(l))).join('\n');
    }
    if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
    return;
  }
  // Blank lines inside a table: keep them in the table block
  if (text.trim() === '') {
    const last = el.lastElementChild;
    if (last && (last.classList.contains('hl-table') || last.classList.contains('hl-table-pre'))) {
      last._rawLines.push(text);
      if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
      return;
    }
  }

  const div = document.createElement('div');
  const cls = agentLineCls(text);
  div.className = cls;
  // Reset stuck-plan counter only when a non-plan-related tool appears
  if (cls.includes('agent-line-tool') && !text.includes('ExitPlanMode') && !text.includes('EnterPlanMode')) {
    exitPlanModeCount[sessionId] = 0;
  }
  // Use rich formatting for regular text lines. Prompts get the image-aware
  // escaper so an attached image path renders as a thumbnail. Other special
  // lines (tool / error / status / queued) stay on plain textContent.
  if (cls.includes('agent-line-prompt')) {
    div.innerHTML = escPromptWithImages(text);
  } else if (cls.includes('agent-line-tool') || cls.includes('agent-line-error') || cls.includes('agent-line-followup') || cls.includes('agent-line-queued')) {
    div.textContent = text;
  } else {
    div.innerHTML = formatAgentText(text);
  }
  el.appendChild(div);
  if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);

  // Plan detection: [tool: ExitPlanMode] signals end of plan (claude + supports_plan_mode only)
  const _pdCached = agentStatusCache[sessionId];
  const _pdProj = _pdCached ? allProjects.find(p => p.id === _pdCached.projectId) : null;
  const _pdCaps = _pdProj ? _capsForProject(_pdProj) : _CLAUDE_DEFAULT_CAPS;
  if (_pdCaps.supports_plan_mode && text.trim() === '[tool: ExitPlanMode]') {
    exitPlanModeCount[sessionId] = (exitPlanModeCount[sessionId] || 0) + 1;
    // Store plan content for viewer but keep text visible for first occurrence
    planViewerContent[sessionId] = planViewerContent[sessionId] || [];
    const children = Array.from(el.children);
    for (let i = children.length - 1; i >= 0; i--) {
      const child = children[i];
      const txt = (child.textContent || '').trim();
      if (child.classList.contains('agent-line-tool') || child.classList.contains('agent-line-prompt') || child.classList.contains('plan-show-btn')) break;
      if (txt === '[tool: ExitPlanMode]') continue;
      planViewerContent[sessionId].unshift(child.textContent || child.innerText || '');
    }

    // Mark as waiting for plan approval in caches
    if (agentStatusCache[sessionId]) agentStatusCache[sessionId].waitingForPlanApproval = true;
    const histEntry = agentHistory.find(h => h.sessionId === sessionId);
    if (histEntry) histEntry.waitingForPlanApproval = true;
    refreshModal();

    // Show "Approve Plan" + "Collapse" buttons on first ExitPlanMode (plan stays visible)
    if (exitPlanModeCount[sessionId] === 1) {
      const cached = agentStatusCache[sessionId];
      const pid = cached ? cached.projectId : null;
      if (pid) {
        const approveRow = document.createElement('div');
        approveRow.className = 'plan-card';
        approveRow.id = `plan-approve-${sessionId}`;  // load-bearing: approvePlan() + stuck-loop handler look this up
        approveRow.innerHTML = _planCardHTML(pid, sessionId, planViewerContent[sessionId] || []);
        el.appendChild(approveRow);
        if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
      }
    }

    // Detect stuck ExitPlanMode loop (agent can't exit plan mode via non-interactive dispatch)
    if (exitPlanModeCount[sessionId] >= 2) {
      collapseIntoPlanButton(sessionId, el);
      const oldApprove = document.getElementById(`plan-approve-${sessionId}`);
      if (oldApprove) oldApprove.remove();
      const cached = agentStatusCache[sessionId];
      const pid = cached ? cached.projectId : null;
      const warn = document.createElement('div');
      warn.className = 'agent-plan-stuck-warning';
      warn.style.cssText = 'background:var(--amber-dim);border:1px solid var(--amber);color:var(--amber-text);padding:8px 12px;border-radius:6px;margin:6px 0;font-size:12px;line-height:1.5;display:flex;align-items:center;gap:10px;flex-wrap:wrap';
      warn.innerHTML = `&#x26A0; Agent is stuck trying to exit plan mode.${pid ? ` <button style="background:var(--green);color:#fff;border:none;padding:6px 16px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer" onclick="approvePlan('${esc(pid)}','${esc(sessionId)}')">Approve Plan</button>` : ''}`;
      el.appendChild(warn);
      if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
      exitPlanModeCount[sessionId] = 0; // Reset so it doesn't spam
    }

    // Check for plan file and inject button
    const cached = agentStatusCache[sessionId];
    if (cached && cached.projectId) {
      fetch(API_BASE + `/api/project/${cached.projectId}/agent/status`)
        .then(r => r.json())
        .then(data => {
          const s = (data.sessions || []).find(x => x.session_id === sessionId);
          if (s && s.plan_file) {
            cached.planFile = s.plan_file;
            // Fetch plan content to extract heading for button label
            fetch(API_BASE + `/api/project/${cached.projectId}/agent/plan-file?session=${sessionId}`)
              .then(r => r.ok ? r.json() : null)
              .then(planData => {
                const fname = s.plan_file.split(/[/\\]/).pop();
                let label;
                if (planData && planData.content) {
                  const headingMatch = planData.content.match(/^#\s+(.+)/m);
                  if (headingMatch) {
                    label = headingMatch[1].trim();
                    if (label.length > 40) label = label.slice(0, 37) + '...';
                    planFileTitle[sessionId] = label;
                  }
                }
                if (!label) label = planFileLabel(cached.task || fname);
                const container = document.getElementById(`plan-file-btn-${sessionId}`);
                if (container && !container.innerHTML.trim()) {
                  container.innerHTML = `<button class="btn-plan-file" onclick="openPlanFileViewer('${esc(cached.projectId)}','${esc(sessionId)}')" title="${esc(fname)}">&#128196; ${esc(label)}</button>`;
                }
              }).catch(() => {
                const fname = s.plan_file.split(/[/\\]/).pop();
                const label = planFileLabel(cached.task || fname);
                const container = document.getElementById(`plan-file-btn-${sessionId}`);
                if (container && !container.innerHTML.trim()) {
                  container.innerHTML = `<button class="btn-plan-file" onclick="openPlanFileViewer('${esc(cached.projectId)}','${esc(sessionId)}')" title="${esc(fname)}">&#128196; ${esc(label)}</button>`;
                }
              });
          }
        }).catch(() => {});
    }
  }
}

// ── Plan-approval card (§7, 2026-07-05) ────────────────────────────────────
// Parse a plan's raw lines into ordered steps. A line is a step head if it
// starts with a number (1. / 1)) or a bullet (- * •); following unmarked lines
// fold into the current step as continuation. Returns null when <2 markers are
// found (mirrors the >=2 threshold used by Path A / collapseIntoPlanButton) so
// callers fall back to showing the raw text — never drop content.
function _parsePlanSteps(lines) {
  const arr = Array.isArray(lines) ? lines : String(lines || '').split('\n');
  const markerRe = /^\s*(?:\d+[.\)]|[-*•])\s+/;
  const steps = [];
  let cur = null, markerCount = 0;
  for (const raw of arr) {
    const line = raw || '';
    if (markerRe.test(line)) {
      markerCount++;
      if (cur !== null) steps.push(cur.trim());
      cur = line.replace(markerRe, '').trim();
    } else if (cur !== null) {
      const t = line.trim();
      if (t) cur += ' ' + t;  // continuation of the current step
    }
    // lines before the first marker (plan heading/preamble) are ignored
  }
  if (cur !== null) steps.push(cur.trim());
  if (markerCount < 2) return null;
  return { steps: steps.filter(Boolean) };
}

// Build the inner HTML of an in-thread plan card. Identical markup from both the
// live-stream path (Path B) and the buffer-rebuild path (Path A) so a mid-
// approval reload shows the same card. approvePlan/openPlanViewer resolve via
// window at click time (defined in conversation.js / project-forms.js).
function _planCardHTML(projectId, sessionId, rawLines) {
  const parsed = _parsePlanSteps(rawLines);
  let body;
  if (parsed && parsed.steps.length) {
    body = `<ol class="plan-card-steps">` + parsed.steps.map(s => `<li>${esc(s)}</li>`).join('') + `</ol>`;
  } else {
    body = `<div class="plan-card-raw">${esc((rawLines || []).join('\n').trim())}</div>`;
  }
  return `<div class="plan-card-header"><span class="plan-card-title">&#128203; Plan ready to approve</span></div>`
    + body
    + `<div class="plan-card-actions">`
    +   `<button class="btn-plan-approve" onclick="approvePlan('${esc(projectId)}','${esc(sessionId)}')">Approve Plan</button>`
    +   `<button class="btn-plan-review" onclick="openPlanViewer('${esc(sessionId)}')">Review</button>`
    + `</div>`;
}

function approvePlan(projectId, sessionId) {
  // Guard against double-click
  const row = document.getElementById(`plan-approve-${sessionId}`);
  if (row) row.remove();
  // Clear plan approval state
  if (agentStatusCache[sessionId]) agentStatusCache[sessionId].waitingForPlanApproval = false;
  const hEntry = agentHistory.find(h => h.sessionId === sessionId);
  if (hEntry) hEntry.waitingForPlanApproval = false;
  // Show local echo
  const echoEl = document.getElementById(`agent-output-${sessionId}`);
  if (echoEl) {
    const div = document.createElement('div');
    div.className = 'agent-line agent-line-prompt agent-echo';
    div.textContent = '> Plan approved, please continue.';
    div.style.whiteSpace = 'pre-wrap';
    echoEl.appendChild(div);
    echoEl.scrollTop = echoEl.scrollHeight;
  }
  // Always send directly via API (more reliable than going through input element)
  fetch(API_BASE + `/api/project/${projectId}/agent/followup`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(_maybeTagMobileClient({ message: 'Plan approved, please continue.', session_id: sessionId }))
  }).catch(e => console.error('Plan approval failed:', e));
}

let _aqCounter = 0;
const _EMPTY_SET = new Set();  // shared read-only fallback for the answered-id lookup

// Cache a pending question on the session so a later panel rebuild can restore
// the form without waiting for the /agent/status poll. Deduped by question_id.
function _cachePendingQuestion(sessionId, questions, questionId) {
  const c = agentStatusCache[sessionId];
  if (!c) return;
  const list = c.pendingQuestions || (c.pendingQuestions = []);
  if (questionId && list.some(p => p.question_id === questionId)) return;
  list.push({ questions: questions || [], question_id: questionId || '' });
}

// Re-render any unanswered question forms for a session after its panel was
// rebuilt. Idempotent: renderAgentQuestion dedupes against the DOM, so this is
// a no-op when the form's still on screen and restores it when it isn't.
function _rerenderPendingQuestions(projectId, sessionId) {
  const c = agentStatusCache[sessionId];
  if (!c || !c.waitingForQuestion) return;
  for (const pq of (c.pendingQuestions || [])) {
    try { renderAgentQuestion(sessionId, projectId, pq.questions || [], pq.question_id || ''); }
    catch (_) {}
  }
}

// Render an AskUserQuestion form into the session's chat. The server re-emits
// the question on every SSE reconnect AND mirrors it on /agent/status, so this
// is called repeatedly for the same question — dedupe against the DOM, never a
// persistent set. A set that outlived the element WAS the "question silently
// never comes back" bug: once the card got wiped (modal reopen, tab switch,
// conversation-list nav — anything that rebuilds agent-output without the
// preserved node), the set still said "already rendered" and suppressed every
// re-delivery, so the agent sat waiting with no form on screen.
function renderAgentQuestion(sessionId, projectId, questions, questionId) {
  const el = document.getElementById(`agent-output-${sessionId}`);
  if (!el || !questions || !questions.length) return;
  // Agent is parked waiting for an answer → definitively not generating.
  // Covers all 3 call sites (SSE question, fetchAgentStatus, rerender) and the
  // turn_complete-suppressed window where status never flips to idle. (§4)
  hideTypingIndicator(sessionId);
  if (questionId) {
    // Don't resurrect a form the user already answered this turn…
    if ((_answeredQuestionIds[sessionId] || _EMPTY_SET).has(questionId)) return;
    // …and skip only if THIS card is actually on screen right now. (question_id
    // is uuid hex → safe to interpolate into the attribute selector.)
    if (el.querySelector(`.agent-question[data-qid="${questionId}"]`)) return;
  }
  const wasPinned = _isAgentOutputPinned(el);
  const formId = `aq-${sessionId}-${_aqCounter++}`;

  const container = document.createElement('div');
  container.className = 'agent-question';
  container.id = formId;
  if (questionId) container.dataset.qid = questionId;

  let html = '';
  // §6: a single short-option single-select question renders as one-tap chips
  // (one tap = answer). Anything else — multi-select, >4 options, long labels,
  // options carrying a .description (a pill can't show it), or multiple sub-
  // questions — keeps the full radio/checkbox form unchanged.
  const q0 = questions[0];
  const useChips = questions.length === 1 && !q0.multiSelect
    && (q0.options || []).length >= 1 && (q0.options || []).length <= 4
    && (q0.options || []).every(o => (o.label || '').length <= 24 && !o.description);
  if (useChips) {
    html += `<div class="agent-question-header">${esc(q0.header || 'Question')}</div>`;
    html += `<div class="agent-question-text">${esc(q0.question)}</div>`;
    html += `<div class="agent-question-chips">`;
    (q0.options || []).forEach(opt => {
      // Pass `this` (not the label) into the handler and read data-label — the
      // attribute is HTML-escaped safely, avoiding the nested-quote break a
      // label with an apostrophe would cause inside an onclick JS string.
      html += `<button type="button" class="agent-question-chip" data-label="${esc(opt.label)}" onclick="submitQuestionChip('${esc(projectId)}','${esc(sessionId)}','${formId}',0,this)">${esc(opt.label)}</button>`;
    });
    html += `<button type="button" class="agent-question-chip agent-question-chip-other" onclick="submitQuestionOther('${esc(projectId)}','${esc(sessionId)}','${formId}',0)">Other&#8230;</button>`;
    html += `</div>`;
    html += `<div class="agent-question-other-wrap" data-qidx="0">
      <input type="text" class="agent-question-other" placeholder="Type your answer..." onkeydown="if(event.key==='Enter'){event.preventDefault();submitQuestionOther('${esc(projectId)}','${esc(sessionId)}','${formId}',0);}">
      <button type="button" class="agent-question-other-send" onclick="submitQuestionOther('${esc(projectId)}','${esc(sessionId)}','${formId}',0)">Send</button>
    </div>`;
  } else {
    questions.forEach((q, qi) => {
      const inputType = q.multiSelect ? 'checkbox' : 'radio';
      const groupName = `${formId}-q${qi}`;
      html += `<div class="agent-question-header">${esc(q.header || 'Question')}</div>`;
      html += `<div class="agent-question-text">${esc(q.question)}</div>`;
      html += `<div class="agent-question-options" data-qidx="${qi}">`;
      (q.options || []).forEach((opt, oi) => {
        html += `<div class="agent-question-option">
          <input type="${inputType}" name="${groupName}" id="${groupName}-o${oi}" value="${esc(opt.label)}" data-desc="${esc(opt.description || '')}">
          <label for="${groupName}-o${oi}">${esc(opt.label)}${opt.description ? `<span class="aq-desc">${esc(opt.description)}</span>` : ''}</label>
        </div>`;
      });
      // "Other" option
      html += `<div class="agent-question-option">
        <input type="${inputType}" name="${groupName}" id="${groupName}-other" value="__other__" onchange="document.getElementById('${groupName}-other-text').classList.toggle('visible', this.checked || this.selected)">
        <label for="${groupName}-other">Other</label>
      </div>`;
      html += `<input type="text" class="agent-question-other" id="${groupName}-other-text" placeholder="Type your answer...">`;
      html += `</div>`;
    });
    html += `<div class="agent-question-actions">
      <button onclick="submitQuestionAnswer('${esc(projectId)}','${esc(sessionId)}','${formId}',${questions.length})">Submit Answer</button>
    </div>`;
  }

  container.innerHTML = html;
  el.appendChild(container);
  if (wasPinned) el.scrollTop = el.scrollHeight;

  // Show/hide "Other" text field on radio change
  container.querySelectorAll('input[type="radio"]').forEach(radio => {
    radio.addEventListener('change', () => {
      const group = radio.name;
      const otherText = document.getElementById(group + '-other-text');
      const otherRadio = document.getElementById(group + '-other');
      if (otherText) otherText.classList.toggle('visible', otherRadio && otherRadio.checked);
    });
  });
  container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (cb.value === '__other__') {
        const otherText = document.getElementById(cb.name + '-other-text');
        if (otherText) otherText.classList.toggle('visible', cb.checked);
      }
    });
  });
}

function submitQuestionAnswer(projectId, sessionId, formId, questionCount) {
  const container = document.getElementById(formId);
  if (!container) return;

  const answers = [];
  for (let qi = 0; qi < questionCount; qi++) {
    const groupName = `${formId}-q${qi}`;
    const checked = container.querySelectorAll(`input[name="${groupName}"]:checked`);
    const parts = [];
    checked.forEach(input => {
      if (input.value === '__other__') {
        const otherText = document.getElementById(groupName + '-other-text');
        if (otherText && otherText.value.trim()) parts.push(otherText.value.trim());
      } else {
        parts.push(input.value);
      }
    });
    if (parts.length === 0) continue;
    // Find the question text from the header
    const qTextEl = container.querySelectorAll('.agent-question-text')[qi];
    const qText = qTextEl ? qTextEl.textContent : '';
    answers.push({ question: qText, answer: parts.join(', ') });
  }

  if (answers.length === 0) return;
  _dispatchQuestionAnswer(projectId, sessionId, container, answers);
}

// §6 one-tap chip → posts a single answer immediately. `btn` is the clicked
// chip; the answer is read from its data-label (HTML-escaped safely) so labels
// with quotes/apostrophes survive.
function submitQuestionChip(projectId, sessionId, formId, qIndex, btn) {
  const container = document.getElementById(formId);
  if (!container || container.classList.contains('answered')) return;
  const label = (btn && btn.dataset && btn.dataset.label != null) ? btn.dataset.label : (btn ? btn.textContent.trim() : '');
  const qTextEl = container.querySelectorAll('.agent-question-text')[qIndex];
  const qText = qTextEl ? qTextEl.textContent : '';
  _dispatchQuestionAnswer(projectId, sessionId, container, [{ question: qText, answer: label }]);
}

// §6 "Other" chip in chip-mode: first tap reveals the text field; once visible,
// the Send button / Enter confirms and posts the typed answer.
function submitQuestionOther(projectId, sessionId, formId, qIndex) {
  const container = document.getElementById(formId);
  if (!container || container.classList.contains('answered')) return;
  const wrap = container.querySelector(`.agent-question-other-wrap[data-qidx="${qIndex}"]`);
  if (!wrap) return;
  const input = wrap.querySelector('.agent-question-other');
  if (!wrap.classList.contains('visible')) {
    wrap.classList.add('visible');
    if (input) input.focus();
    return;
  }
  const val = input ? input.value.trim() : '';
  if (!val) { if (input) input.focus(); return; }
  const qTextEl = container.querySelectorAll('.agent-question-text')[qIndex];
  const qText = qTextEl ? qTextEl.textContent : '';
  _dispatchQuestionAnswer(projectId, sessionId, container, [{ question: qText, answer: val }]);
}

// Shared dispatch for BOTH the radio/checkbox form and the tap chips (§6).
// answers = [{question, answer}]. Builds the directive message, marks the card
// answered, tears down inputs+chips, clears waiting state, and sends via
// sendFollowup (or a raw POST fallback). One network path, no duplication.
function _dispatchQuestionAnswer(projectId, sessionId, container, answers) {
  if (!container || !answers || !answers.length) return;
  if (container.classList.contains('answered')) return;

  // Directive phrasing: the prior turn was killed mid-tool_use, so claude
  // resumes with an unresolved AskUserQuestion in its transcript and, without
  // explicit framing, can treat the reply as a fresh turn and re-ask. Tell it
  // up front: this IS the answer, do not re-ask.
  const message = (
    'I answered your AskUserQuestion through the Clayrune UI. The values '
    + 'below are my chosen responses — proceed with the task using them. '
    + 'Do NOT re-ask these questions.\n\n'
  ) + answers.map(a =>
    a.question ? `Q: ${a.question}\nA: ${a.answer}` : a.answer
  ).join('\n\n');

  // Mark answered (DOM + per-turn answered-set so a rebuild can't re-render it
  // from cache before turn_start).
  container.classList.add('answered');
  if (container.dataset.qid) {
    (_answeredQuestionIds[sessionId] || (_answeredQuestionIds[sessionId] = new Set())).add(container.dataset.qid);
  }
  const summary = `<div class="agent-question-answer">Answered: ${esc(answers.map(a => a.answer).join(', '))}</div>`;
  const actionsEl = container.querySelector('.agent-question-actions');
  if (actionsEl) actionsEl.innerHTML = summary;         // form mode
  else container.insertAdjacentHTML('beforeend', summary); // chip mode (no actions row)
  // Disable both form inputs and chip buttons (each a no-op in the other mode).
  container.querySelectorAll('input').forEach(i => i.disabled = true);
  container.querySelectorAll('button.agent-question-chip, button.agent-question-other-send').forEach(b => b.disabled = true);
  // Drop out of "asking" immediately (server confirms on turn_start).
  if (agentStatusCache[sessionId]) agentStatusCache[sessionId].waitingForQuestion = false;
  const _hAns = agentHistory.find(h => h.sessionId === sessionId);
  if (_hAns) _hAns.waitingForQuestion = false;
  if (agentStatusCache[sessionId]) agentStatusCache[sessionId].pendingQuestions = [];

  const input = document.getElementById(`agent-followup-${sessionId}`);
  if (input) {
    input.value = message;
    sendFollowup(projectId, sessionId);
  } else {
    fetch(API_BASE + `/api/project/${projectId}/agent/followup`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_maybeTagMobileClient({ message, session_id: sessionId }))
    }).catch(() => {});
  }
}

async function sendFollowup(projectId, sessionId) {
  const input = document.getElementById(`agent-followup-${sessionId}`);
  const message = input.value.trim();
  if (!message) { input.focus(); return; }

  // Guardian guards — prevent piling on during recovery
  const guardCached = agentStatusCache[sessionId];
  if (guardCached?.guardianState === 'recovering') {
    showToast('Session is being recovered \u2014 please wait', 3000);
    return;
  }
  if (guardCached?.circuitBreakerTripped) {
    showToast('Session recovery exhausted \u2014 use "Try Again" or "Start Fresh"', 5000);
    return;
  }

  input.value = '';
  if (input.id) delete textareaValues[input.id];

  // Immediate local echo — show the user's message in DOM only (not buffer)
  // The server will send the real version via SSE which gets added to the buffer
  const echoEl = document.getElementById(`agent-output-${sessionId}`);
  if (echoEl) {
    const div = document.createElement('div');
    // Badge slash commands in the optimistic echo too, so the styling doesn't
    // pop in only once the server round-trips the line back over SSE.
    const _isCmd = window.isSlashCommandLine && window.isSlashCommandLine(`> ${message}`);
    div.className = 'agent-line agent-line-prompt agent-echo'
      + (_isCmd ? ' agent-line-cmd' : '');
    div.textContent = `> ${message}`;
    div.style.whiteSpace = 'pre-wrap';
    echoEl.appendChild(div);
    echoEl.scrollTop = echoEl.scrollHeight;
  }

  // Zero-gap picker update: reflect the newest user message in conversationsCache
  // so when this session later becomes non-running (stops / ends), the picker
  // already shows the real last line without waiting for a reload.
  {
    const cachedForConvo = agentStatusCache[sessionId] || {};
    const csid = cachedForConvo.claudeSessionId || '';
    if (csid) upsertConversationCache(projectId, csid, message, 'running');
  }

  // Upload any pasted images and build final message
  const imageKey = 'fu_' + sessionId;
  const imagePaths = await uploadAgentImages(imageKey);
  const fullMessage = buildTaskWithImages(message, imagePaths);
  clearAgentImages(imageKey);
  // Remove preview thumbnails from DOM
  const previewContainer = input.closest('.agent-chat-input')?.querySelector('.agent-image-previews');
  if (previewContainer) previewContainer.remove();

  // Phase 2 of 2026-04-27 race consolidation: frontend never reads cached
  // status to pick the route. We always POST /agent/send and let the server
  // read live session state under the lock to decide between interrupt /
  // followup / revive / dispatch. UI status flips only when SSE delivers a
  // status event — no optimistic cache writes here.

  // Mark this send as in-flight before opening SSE. Until `turn_start` arrives
  // (or 8s passes) we ignore turn_complete/status events from the eager-opened
  // SSE — those reflect the PRIOR idle state, not the new turn we're about to
  // dispatch. See _sendInFlight comment for the full race.
  _sendInFlight[sessionId] = Date.now();
  _turnStartAcked[sessionId] = false;
  setTimeout(() => {
    // Safety net: if turn_start never arrives, lift the gate so terminal
    // status events can flow again.
    if (_sendInFlight[sessionId] && (Date.now() - _sendInFlight[sessionId]) >= 8000) {
      delete _sendInFlight[sessionId];
    }
  }, 8500);

  // Eagerly (re)open SSE BEFORE the POST so we don't miss `turn_start` on a
  // slow mobile request. If we waited until the fetch resolved, the server
  // could already have transitioned running → idle (fast turn) and our new
  // SSE would never see status='running' — leaving the UI stuck on IDLE.
  // The server's stream reader is session-scoped (not process-scoped), so a
  // pre-opened SSE survives the respawn that interrupt-resume performs.
  if (agentEventSources[sessionId]) {
    agentEventSources[sessionId].close();
    delete agentEventSources[sessionId];
    if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
  }
  connectAgentStream(projectId, sessionId);

  // Fire the API call. Server decides the route. SSE delivers the result.
  // For follow-ups: the existing session already carries its incognito flag,
  // so we only forward the request-level flag if the project is currently set
  // to incognito (matters when the server falls back to dispatching fresh —
  // e.g. revive of a purged session that has no log entry).
  const sendBody = { message: fullMessage, session_id: sessionId };
  // Forward incognito from the SESSION's own flag, not just the project toggle:
  // the toggle is now a one-shot that resets after dispatch, but this session
  // may itself be incognito. Matters when the server falls back to a fresh
  // dispatch (revive of a purged session with no log entry) — it must stay
  // incognito even though the composer default has since reset.
  const _sessInc = (agentStatusCache[sessionId] && agentStatusCache[sessionId].incognito)
    || (agentHistory.find(h => h.sessionId === sessionId) || {}).incognito;
  if (getIncognitoFor(projectId) || _sessInc) sendBody.incognito = true;
  _maybeTagMobileClient(sendBody);
  // Fail-fast timeout: on Android after a Doze cycle the WebView fetch can
  // hang forever on dead sockets — the user sees their prompt cleared, the
  // echo briefly appear and then vanish (reconcile sees no recovered line),
  // and no error toast. Surface the failure within 12s so the user knows to
  // retry instead of staring at a frozen "Completed" pill. The local echo
  // line carries `agent-echo-failed` so it stays visible (as a retry cue)
  // instead of being silently wiped by the next reconcile pass.
  const _sendAC = new AbortController();
  const _sendTID = setTimeout(() => _sendAC.abort(), 12000);
  fetch(API_BASE + `/api/project/${projectId}/agent/send`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(sendBody),
    signal: _sendAC.signal
  }).then(r => { clearTimeout(_sendTID); return r.json(); }).then(data => {
    if (!data.ok && !data.queued) {
      console.error('Send failed:', data.error);
    }
    // A successful send makes this session server-live again (followup /
    // revive / dispatch alike) — drop the read-only marker so the freshness
    // reconciler resumes healing this sid (it skips _readOnlyRevived, which
    // is set by the push-tap reconstruct path and was never cleared).
    if (data.ok && agentStatusCache[sessionId]) {
      delete agentStatusCache[sessionId]._readOnlyRevived;
    }
    // If queued (Mode A follow-up while previous turn still running),
    // mark the echo so the user knows it isn't going out yet.
    if (data.queued) {
      const el = document.getElementById(`agent-output-${sessionId}`);
      if (el) {
        const echo = el.querySelector('.agent-echo');
        if (echo) {
          echo.classList.add('agent-echo-queued');
          echo.textContent = `> [queued] ${message}`;
        }
      }
    }
    // If the server dispatched a fresh session under a new id (no live
    // session_id was provided, or the prior one was purged), point the UI
    // at the new session so SSE connects there.
    const targetSessionId = data.session_id || sessionId;
    if (targetSessionId !== sessionId) {
      // A fresh session was spawned — redirect the active tab so the user
      // sees the new session running rather than the old one stuck at its
      // previous terminal status.
      const oldHist = agentHistory.find(h => h.sessionId === sessionId && h.projectId === projectId);
      if (oldHist && !agentHistory.find(h => h.sessionId === targetSessionId)) {
        agentHistory.unshift({ ...oldHist, sessionId: targetSessionId, status: 'running', startedAt: new Date().toISOString() });
      }
      agentStatusCache[targetSessionId] = { ...(agentStatusCache[sessionId] || {}), status: 'running', startedAt: new Date().toISOString(), claudeSessionId: '' };
      activeAgentTab[projectId] = targetSessionId;
      _sendInFlight[targetSessionId] = _sendInFlight[sessionId] || Date.now();
      delete _sendInFlight[sessionId];
      refreshModal();
      renderAgentConsole();
      if (agentEventSources[sessionId]) {
        agentEventSources[sessionId].close();
        delete agentEventSources[sessionId];
      }
      if (!agentEventSources[targetSessionId]) {
        connectAgentStream(projectId, targetSessionId);
      }
    } else if (!agentEventSources[targetSessionId]) {
      // Eager connect above may have failed (e.g. session momentarily
      // missing); retry now that we know the server route resolved.
      connectAgentStream(projectId, targetSessionId);
    }
    // Reconcile after a short window: gives SSE a chance to deliver the new
    // `> Ron:` line normally, and if it didn't (mobile drop, stale cursor),
    // we silently fill in the gap so the user always sees their prompt.
    setTimeout(() => _reconcileAgentBuffer(projectId, targetSessionId), 1500);
    // Status-recovery watchdog. On mobile WebView + CF Tunnel the eager-
    // opened SSE often silently buffers — turn_start never reaches the
    // client and the pill stays frozen on "idle" / "Completed" while the
    // agent works or waits on an AskUserQuestion. Symptom: user has to
    // background+foreground the app for any update. If turn_start hasn't
    // fired by ~4s after POST resolved, tear down the (possibly dead) SSE,
    // reconnect, and reconcile status + pending question from server.
    setTimeout(() => {
      if (!(_sendInFlight[sessionId] || _sendInFlight[targetSessionId])) return;
      if (agentEventSources[targetSessionId]) {
        agentEventSources[targetSessionId].close();
        delete agentEventSources[targetSessionId];
      }
      if (agentSSEWatchdogs[targetSessionId]) {
        clearInterval(agentSSEWatchdogs[targetSessionId]);
        delete agentSSEWatchdogs[targetSessionId];
      }
      connectAgentStream(projectId, targetSessionId);
      _reconcileAgentBuffer(projectId, targetSessionId);
    }, 4000);
  }).catch(e => {
    clearTimeout(_sendTID);
    console.error('Send error:', e);
    delete _sendInFlight[sessionId];
    const aborted = (e && (e.name === 'AbortError' || /aborted/i.test(String(e))));
    // Mark the local echo as failed so it stays visible (otherwise the next
    // reconcile pass wipes any `.agent-echo` the moment it sees an unrelated
    // "> " line from prior turns, and the user is left with no trace of what
    // they typed). Also restore the input so they can retry without retyping.
    const el = document.getElementById(`agent-output-${sessionId}`);
    if (el) {
      const echo = el.querySelector('.agent-echo');
      if (echo) {
        echo.classList.remove('agent-echo');
        echo.classList.add('agent-echo-failed');
        echo.style.opacity = '0.6';
        echo.textContent = `> [failed to send] ${message}`;
      }
    }
    const inp = document.getElementById(`agent-followup-${sessionId}`);
    // Only restore the textarea if the server never acknowledged the message
    // (turn_start never arrived). If turn_start DID arrive, the agent received
    // the message and is/was running — restoring would show stale text that the
    // user already sent, which is the "text jumps back" bug.
    if (inp && !inp.value && !_turnStartAcked[sessionId]) inp.value = message;
    showToast(aborted
      ? 'Send timed out — network may be paused. Try again.'
      : `Send failed: ${e && e.message ? e.message : 'network error'}`, 4500);
  });

  // (Previously: a 20s "agent appears unresponsive" toast fired here. Removed
  // 2026-05-28 — the new fail-fast send timeout above + visible echo retention
  // already cover the actual-failure case; this toast just nagged on every
  // turn that legitimately took >20s, which is most non-trivial work.)
}

const _recentlyStoppedSessions = {};  // sessionId → timestamp when stopped

async function stopAgent(projectId, sessionId) {
  // Phase 2: no optimistic cache writes. Server is idempotent — pressing
  // Stop on an already-stopped session returns 200 with already_stopped:true.
  // Status flip happens when SSE delivers the terminal status event.
  if (followupTimeouts[sessionId]) { clearTimeout(followupTimeouts[sessionId].timerId); delete followupTimeouts[sessionId]; }
  _recentlyStoppedSessions[sessionId] = Date.now();
  // Close SSE so the next reconnect picks up the post-stop state cleanly.
  if (agentEventSources[sessionId]) {
    agentEventSources[sessionId].close();
    delete agentEventSources[sessionId];
  }
  if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
  // REQUIRED: stopAgent closes its own EventSource synchronously above, so no
  // terminal 'status' SSE arrives to remove the dots — without this they'd
  // animate forever after a manual Stop. (§4)
  hideTypingIndicator(sessionId);

  // Fire the stop request with a timeout — server kill can take seconds
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 8000);
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/agent/stop`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_id: sessionId }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      console.warn('Stop response:', data.error || res.status);
    }
  } catch(e) {
    clearTimeout(timeoutId);
    if (e.name === 'AbortError') {
      console.warn('Stop request timed out — process may still be terminating');
      showToast('Stop sent — process is terminating...', 3000);
    } else {
      console.error('Stop request error:', e);
    }
  }
  refreshSilent();
}

// Refresh a single project's backlog in-place (used when agent TodoWrite syncs mid-stream)
async function refreshProjectBacklog(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/backlog`);
    if (!res.ok) return;
    const backlog = await res.json();
    const p = allProjects.find(x => x.id === projectId);
    if (p) {
      p.backlog = backlog;
      p._backlogFull = true;  // full bodies loaded — preserve across list re-fetches
      refreshModalById(projectId);
    }
  } catch(e) {}
}

// Fetch all agent sessions for a project on modal open / page boot
async function fetchAgentStatus(projectId) {
  try {
    const res = await fetchFailFast(API_BASE + `/api/project/${projectId}/agent/status`);
    const data = await res.json();
    const sessions = data.sessions || [];
    const pName = (allProjects.find(x => x.id === projectId) || {}).name || projectId;

    for (const s of sessions) {
      const sid = s.session_id;
      // Don't let server overwrite a recently-stopped session with 'error'
      // (protects against race where reader thread sets error before stop lands)
      const stoppedAt = _recentlyStoppedSessions[sid];
      if (stoppedAt && (Date.now() - stoppedAt < 15000) && s.status === 'error') {
        s.status = 'stopped';
      }
      if (stoppedAt && (Date.now() - stoppedAt >= 15000)) {
        delete _recentlyStoppedSessions[sid];
      }
      // Long Mode-B session advisory toast — REMOVED 2026-05-22. It was
      // well-intentioned (nudging a restart so a long session reloads its
      // Step-6-captured memory fresh) but in practice just an annoying 14s
      // nag. The server still computes `s.long_session_advisory`; nothing
      // consumes it now. To bring the nudge back, render it somewhere
      // non-intrusive (e.g. an inline session-panel hint) rather than a toast.
      agentStatusCache[sid] = { status: s.status, task: s.task, projectId, startedAt: s.started_at, planFile: s.plan_file || '', usage: s.usage || {}, cost_usd: s.cost_usd || 0, num_turns: s.num_turns || 0, hivemindId: s.hivemind_id || '', hivemindWsId: s.hivemind_ws_id || '', hivemindRole: s.hivemind_role || '', triggerType: s.trigger_type || 'manual', triggerId: s.trigger_id || '', waitingForPlanApproval: s.waiting_for_plan_approval || false, waitingForQuestion: s.waiting_for_question || false, guardianState: s.guardian_state || null, circuitBreakerTripped: s.circuit_breaker_tripped || false, claudeSessionId: s.claude_session_id || '', incognito: !!s.incognito, provider: s.provider || 'claude', agentModel: s.agent_model || '', model: s.model || '', modelSource: s.model_source || 'manual', pinnedModel: s.pinned_model || '', character: s.character || null, pinned: !!s.pinned };
      // A FRESH conversation has no claude_session_id at dispatch time, so the
      // zero-gap upsert in startAgent() (resume-preview.js) is skipped for it —
      // `if (resumeId && task)` is false. Nothing else inserted it, so the new
      // chat was missing from the rail/Layer-2 list until a page refresh made
      // loadConversations() re-fetch the (now transcript-backed) server list.
      // This tick is where the CLI's session id first reaches us, so upsert here.
      //
      // Guard on the cache already being LOADED: upsertConversationCache creates
      // the array if absent, and the `if (!conversationsCache[p.id]) loadConversations(...)`
      // guards elsewhere would then see a non-empty array and never fetch the
      // real list — leaving the rail showing ONLY this one conversation.
      const _csid = s.claude_session_id || '';
      if (_csid && !s.hivemind_ws_id && Array.isArray(conversationsCache[projectId])
          && !conversationsCache[projectId].some(c => c.claude_session_id === _csid)) {
        upsertConversationCache(projectId, _csid, s.task || '', s.status);
      }
      // Question-form reconciliation (parity with _reconcileAgentBuffer). The
      // wholesale cache rebuild above drops pendingQuestions on every poll. If we
      // don't re-derive it here, a form that only ever lived in the cache — the
      // question arrived while this session's panel wasn't in the DOM (a
      // background agent tab, an unfocused or closed modal) — is lost on the next
      // poll and never comes back: the SSE is already connected so it won't
      // re-emit, and _rerenderPendingQuestions finds an empty cache when the tab
      // is finally focused. Re-derive from server truth so the form restores on
      // focus / tab-switch. renderAgentQuestion is DOM-dedup-safe and no-ops when
      // the panel is absent or the qid was already answered this turn.
      if (s.waiting_for_question) {
        agentStatusCache[sid].pendingQuestions = (s.pending_questions || []).map(pq => ({
          questions: pq.questions || [], question_id: pq.question_id || ''
        }));
        for (const pq of (s.pending_questions || [])) {
          try { renderAgentQuestion(sid, projectId, pq.questions || [], pq.question_id || ''); }
          catch (_) {}
        }
      }
      if (s.log_lines && s.log_lines.length > 0) {
        const _prevBufLen = (agentOutputBuffers[sid] || []).length;
        // Defensive: older / runtime-dispatched sessions may not have seeded
        // the user prompt into server-side log_lines, so a wholesale replace
        // would wipe the locally-echoed `> {task}` prefix and the user's
        // first message would disappear from the chat. Prepend it back when
        // missing so the chat survives a refresh.
        const _hasPrompt = s.log_lines.some(l => (l || '').trimStart().startsWith('> '));
        if (!_hasPrompt && s.task) {
          agentOutputBuffers[sid] = [`> ${s.task}`, ...s.log_lines];
        } else {
          agentOutputBuffers[sid] = s.log_lines;
        }
        // Anchor the SSE since= cursor to the server's authoritative count so
        // a subsequent connectAgentStream() resumes at the right index instead
        // of replaying all log_lines from 0 (which would double the buffer
        // and double-render every line in the DOM).
        agentServerLines[sid] = s.log_lines.length;
        // Render-gap recovery: if the buffer just GREW here (lines arrived while
        // this panel was inactive/backgrounded and its SSE was parked), the
        // refreshModal() at the end of this function PRESERVES the existing
        // agent-output node (scroll/perf) and won't repaint it from the grown
        // buffer — so the recovered lines stay invisible. Repaint from the buffer
        // now. Gated on (a) growth and (b) no live SSE, so the actively-streaming
        // chat (kept in sync incrementally by appendAgentLine) never flickers.
        if ((agentOutputBuffers[sid] || []).length > _prevBufLen
            && !agentEventSources[sid]
            && document.getElementById(`agent-output-${sid}`)) {
          _repaintAgentOutput(sid);
        }
      }
      const existingHist = agentHistory.find(h => h.sessionId === sid);
      if (!existingHist) {
        agentHistory.unshift({ projectId, sessionId: sid, projectName: pName, task: s.task || '', status: s.status, startedAt: s.started_at || '', hivemindId: s.hivemind_id || '', hivemindWsId: s.hivemind_ws_id || '', hivemindRole: s.hivemind_role || '', triggerType: s.trigger_type || 'manual', triggerId: s.trigger_id || '', incognito: !!s.incognito, provider: s.provider || 'claude', agentModel: s.agent_model || '', model: s.model || '', modelSource: s.model_source || 'manual', character: s.character || null });
      } else {
        if (s.incognito && !existingHist.incognito) existingHist.incognito = true;
        if (s.trigger_type && !existingHist.triggerType) existingHist.triggerType = s.trigger_type;
        if (s.trigger_id && !existingHist.triggerId) existingHist.triggerId = s.trigger_id;
        if (s.provider && !existingHist.provider) existingHist.provider = s.provider;
        if (s.agent_model && !existingHist.agentModel) existingHist.agentModel = s.agent_model;
      }
      // Auto-connect SSE for:
      //  - actively-running sessions (need live output)
      //  - sessions waiting on AskUserQuestion (need re-delivery of the form
      //    after mobile cold-reopen / modal-rebuild)
      //  - the active tab of an open modal that's idle (so the user sees
      //    status flip when they send a follow-up; otherwise sendFollowup
      //    has to open the stream, which can miss a fast turn_start → idle)
      // We still skip idle background sessions to preserve Chromium's 6-slot
      // per-origin cap; only the currently-visible idle one is subscribed.
      // Split-view: the 2nd pane's session is "active" too — keep its idle SSE
      // connected and its repaint ungated so both panes stream live.
      const isActiveTab = (activeAgentTab[projectId] === sid
        || splitAgentTab[projectId] === sid) && openModals.has(projectId);
      const wantsLiveStream = (
        s.status === 'running' ||
        (s.status === 'idle' && (s.waiting_for_question || isActiveTab))
      );
      if (wantsLiveStream && !agentEventSources[sid]) {
        connectAgentStream(projectId, sid);
      }
    }
    // Sessions the server no longer lists — after a restart that cleared the
    // in-memory agent_sessions, or the 30-min stale-purge in server.py. The
    // conversation itself is NOT gone: its transcript is on disk + in the
    // agent_log, and a follow-up transparently resumes it (`claude -r`). So we
    // DETACH the tab instead of deleting it — keep the entry, its rendered
    // buffer, and its status cache so the window stays put ("don't make the
    // user feel the chat is gone"); only tear down the live stream and mark it
    // 'stopped'. 'stopped' shows the "Type to resume conversation..." input,
    // draws no Stop button, and isn't in the wantsLiveStream set, so it won't
    // trigger a doomed SSE reconnect to a session the server forgot. The user
    // can still ✕-close it deliberately (closeAgentTab). [keep-window-open]
    const serverSids = new Set(sessions.map(s => s.session_id));
    // Read-only reconstructed sessions (deep-link tap on a dead session) are
    // intentionally client-injected and will never be in serverSids — don't
    // touch them or a background poll would disturb the tab mid-read.
    // Temp sessions (_pending_*) are optimistic pre-POST entries — the server
    // doesn't know about them yet; exclude them so a concurrent fetchAgentStatus
    // doesn't race-detach the temp session and cause a spurious BLOCKED flash.
    // Locally-running sessions are also protected: a fetchAgentStatus response
    // initiated BEFORE dispatch may arrive after the temp→real promotion and
    // carry an empty session list, which would race-detach the newly promoted
    // real session ID (not _pending_ any more) and cause a BLOCKED flash.
    const _isInjectedRO = sid => !!(agentStatusCache[sid] || {})._readOnlyRevived;
    const _isPending = sid => (sid || '').startsWith('_pending_');
    const _isLocallyRunning = sid => {
      const c = agentStatusCache[sid] || {};
      return c.status === 'running' && !c._liveSeeded;  // seeded-optimistic ≠ confirmed
    };
    const _isDetached = h => h.projectId === projectId && !serverSids.has(h.sessionId) && !_isInjectedRO(h.sessionId) && !_isPending(h.sessionId) && !_isLocallyRunning(h.sessionId);
    const detachedEntries = agentHistory.filter(_isDetached);
    for (const det of detachedEntries) {
      // Tear down only the live resources; KEEP the entry, its output buffer,
      // and its status cache (the latter holds claudeSessionId, needed to
      // resume, and is what agentPanelHTML reads to render the active session).
      if (agentEventSources[det.sessionId]) {
        agentEventSources[det.sessionId].close();
        delete agentEventSources[det.sessionId];
      }
      if (agentSSEWatchdogs[det.sessionId]) {
        clearInterval(agentSSEWatchdogs[det.sessionId]);
        delete agentSSEWatchdogs[det.sessionId];
      }
      det.status = 'stopped';
      if (agentStatusCache[det.sessionId]) agentStatusCache[det.sessionId].status = 'stopped';
    }
    // activeAgentTab is intentionally NOT cleared for a detached session — the
    // user stays on the conversation they were reading; it just becomes
    // resumable in place. (agentPanelHTML still drops a selection that points to
    // a session no longer in the tab list, e.g. one the user ✕-closed.)

    // Auto-select a tab when none is active. DESKTOP: always (classic tab
    // strip — a tab should be open). MOBILE: only a single conversation
    // (→ direct chat); with >1 the drill-down list is the home view and
    // auto-selecting on every background sync would override
    // openProjectModal's list-first reset and yank the user into a chat the
    // instant the list appears (the "flashes then jumps" bug).
    if (sessions.length > 0 && !activeAgentTab[projectId]) {
      const visible = sessions.filter(s => !s.hivemind_ws_id);
      // DESKTOP only. MOBILE never auto-selects — even a lone conversation lands
      // on the Layer-2 list (the home view), so hardware-back returns there
      // instead of exiting to the dashboard. The old `|| visible.length <= 1`
      // exception drilled straight into a single active chat on project open
      // (Engulfing Dashboard), stranding the back button.
      if (!isMobileChatList()) {
        const running = visible.find(s => s.status === 'running' || s.status === 'idle');
        activeAgentTab[projectId] = running ? running.session_id : (visible[0] || sessions[0]).session_id;
      }
    }
    if (sessions.length > 0 || detachedEntries.length > 0) {
      refreshModal();
      renderAgentConsole();
    }
  } catch(e) {}
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window._maybeTagMobileClient = _maybeTagMobileClient;
window._composerProvider = _composerProvider;
window.getIncognitoFor = getIncognitoFor;
window.isHivemindWorker = isHivemindWorker;
window.getProjectSessions = getProjectSessions;
window.getProjectTabSessions = getProjectTabSessions;
window.scheduleHideHmPopover = scheduleHideHmPopover;
window.cancelHideHmPopover = cancelHideHmPopover;
window.agentPanelHTML = agentPanelHTML;
window.backToConvList = backToConvList;
window.switchAgentTab = switchAgentTab;
window.getDefaultResumeId = getDefaultResumeId;
window.selectResumeSession = selectResumeSession;
window.appendAgentLine = appendAgentLine;
window.showTypingIndicator = showTypingIndicator;
window.hideTypingIndicator = hideTypingIndicator;
window.setAgentActivity = setAgentActivity;   // interop: resume-preview.js SSE handler
window._isGenerating = _isGenerating;         // interop: resume-preview.js SSE handler
window._cachePendingQuestion = _cachePendingQuestion;
window._rerenderPendingQuestions = _rerenderPendingQuestions;
window.renderAgentQuestion = renderAgentQuestion;
window.sendFollowup = sendFollowup;
window.stopAgent = stopAgent;
window.refreshProjectBacklog = refreshProjectBacklog;
window.fetchAgentStatus = fetchAgentStatus;
window.approvePlan = approvePlan;
window._parsePlanSteps = _parsePlanSteps;
window._planCardHTML = _planCardHTML;
window.mcBackFromConv = mcBackFromConv;
window.newAgentTab = newAgentTab;
window.fillStarterChip = fillStarterChip;
window.setComposerProvider = setComposerProvider;
window.setComposerModel = setComposerModel;
// Keyed by project+provider (see _modelKey) — a model armed for one runtime must
// never ride along on a dispatch to another. Resolves against the provider the
// composer is CURRENTLY showing, which is the one being dispatched.
window.getPendingDispatchModel = (projectId) => {
  const p = (typeof allProjects !== 'undefined' ? allProjects : []).find(x => x.id === projectId);
  return pendingDispatchModel[_modelKey(projectId, _composerProvider(p))] || '';
};
// The provider the user actually PICKED this turn in the composer, distinct
// from _composerProvider's resolved value (which falls back to project/global
// default and is therefore never empty). Dispatch needs the picked value so an
// unset picker doesn't override a character's pinned provider — see
// getPendingDispatchModel above, which already has this shape for model.
window.getPendingDispatchProvider = (projectId) => pendingDispatchProvider[projectId] || '';
window.showHmWorkerPopover = showHmWorkerPopover;
window.submitQuestionAnswer = submitQuestionAnswer;
window.submitQuestionChip = submitQuestionChip;
window.submitQuestionOther = submitQuestionOther;
window.toggleIncognito = toggleIncognito;
window.clearIncognito = clearIncognito;
window.openComposerSheet = openComposerSheet;
window.closeComposerSheet = closeComposerSheet;
