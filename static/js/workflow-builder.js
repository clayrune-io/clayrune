// ── Workflow Builder (MC-871, R2-D7 — "the canvas") ──────────────────────────
//
// The authoring surface on top of the already-merged runner + CRUD + DAG store
// (mc/workflows.py + mc/blueprints/workflow_routes.py, docs/WORKFLOW_BUILDER_SPEC.md,
// Revision 2).
//
// REHOSTED INLINE (Q7 re-reversed, Ron on seeing the shipped tab: "instead of
// opening yet another window, can we open the canvas on the same page we use
// for the overall workflow menu? If there's more than one, present the
// selection tabs at the top"). There is no floating modal any more — the ONE
// canvas implementation below mounts into whichever project's Workflows tab
// last claimed it (`_wfState`, a singleton exactly like the old modal was:
// only one workflow can be under edit at a time). agent-console.js's
// `loadWorkflows`/`_wfRenderClayruneSection`-successor builds a tabs row (one
// per workflow touching that project, plus "+ New Workflow") into
// `#wfb-clayrune-section-<projectId>` and an empty `#wfb-inline-host-<projectId>`
// div; `openWorkflowBuilder(workflowId, projectId)` is the one function that
// still loads a workflow and mounts the canvas into that host — called by a
// tab click, by "+ New Workflow", and (still) directly, e.g. from a test.
// `window._wfSyncTabsForProject(projectId, list)` is the entry point that
// decides whether the tabs row (and, on a real change, the mounted canvas)
// need rebuilding at all — see its own header for why that has to be
// carefully idempotent (a 3s CC-fan-out poll calls into this same path).
//
// A second load-bearing reason the modal had to go: `refreshModalById`
// (static/index.html) rebuilds the ENTIRE project modal's innerHTML on every
// SSE turn event for that project. A canvas embedded in that DOM would be
// wiped mid-edit exactly like agent-output streaming text would be, so
// `refreshModalById` now detaches and reattaches the whole
// `#workflows-body-<pid>` subtree around that rebuild — see its own comment
// there. That preservation, not anything in this file, is what makes "live"
// hosting safe.
//
// The definition is now `nodes` + `edges` (R2-D1),
// not a nested tree, and this file is the free-canvas editor for that shape:
// a palette docked left (a bottom sheet under the mobile breakpoint), drag a
// block onto the canvas to place a node, drag from an output port to an
// input port to connect two nodes. Node `x`/`y` persist per node; pan/zoom
// (`_wfCanvasWheel`, `_wfCanvasTouchStart/Move/End`) is a CSS transform on
// `#wfb-world` and is NOT persisted to the record (open item, R2-D7 — the
// store has no viewport field; adding one is a backend change out of this
// pass's scope, so a reopen starts centred rather than where you left it).
//
// NODE TYPES (R2-D6 — Paths is retired): `agent`, `approval`, `action`.
// Branching is a property of the EDGE (`when`), not a node — an agent step's
// `outcomes` array and an approval gate's `options` array are the declared
// vocabulary; each label gets its own output port, plus a mandatory
// `otherwise` port whenever that vocabulary is non-empty (R2-D6: "otherwise
// survives as a PORT"). An agent with no declared outcomes, and every action
// node, gets exactly one plain (unconditional, `when: null`) output port —
// matching what `mc/workflows.py::validate_workflow` actually accepts.
//
// CHANGE 10/11 (palette Action tile): 10 asked to remove the Action tile
// from the palette; before that landed, Ron overrode it with 11 — the tile
// STAYS, but the fixed 3-verb dropdown becomes a free-text "what should
// happen here" resolved at save/test time. 11 is a schema-touching design
// change, deliberately NOT implemented in this pass (see the resolution
// proposal in docs/WORKFLOW_BUILDER_SPEC.md's "Open" section, awaiting
// Ron's review) -- the palette tile and the action card editor below are
// therefore exactly as they were before either 10 or 11 was raised.
//
// PHASE 2'S CARD EDITORS ARE REUSED VERBATIM (brief's explicit instruction):
// `_wfRenderAgentOwn`'s project-select/persona-picker/prompt-textarea and
// `_wfRenderActionOwn`/`_wfActionFieldsHTML`/`_wfRerenderActionFields` are
// copied over unchanged from the spine build — only the surrounding layout
// (free canvas position instead of a list item) and the branching editor
// (outcomes/options as a flat array instead of nested branch lists) changed.
//
// THE SLOT-BREAK GUARD (R2-D5, carried forward from the spine's slot-ORDER
// guard, now graph-shaped): `{{steps.X.output}}` is valid in node N only if X
// DOMINATES N — X is on every path from the trigger to N, so X can never be
// skipped while N runs. `_wfFindBrokenSlotRefs` computes this with a ported
// version of the backend's toposort + dominator fixpoint
// (`mc/workflows.py::_toposort`/`_compute_dominators`) and every mutation
// that can change dominance — add/remove an edge, delete a node, remove a
// declared outcome/option (which implicitly drops the edge wired to it) —
// diffs the violation set before/after and refuses on any NEW violation,
// exactly the spine's "refuse, don't warn-and-allow, don't silently fix"
// pattern, with the reference named in the toast. NOT guarded: a rename
// (spine behaviour carried forward unchanged — this file only widens the
// guard to cover edges, not new mutation classes) and `{{prev.output}}`
// (valid only with exactly one parent — the backend's own rule, checked at
// save; by design its meaning changes on any parent change, so guarding it
// would refuse valid edits).
//
// CYCLE REFUSAL AT CONNECT (R2-D3 layer 1 — the layer that lives in the
// canvas): before an edge is added, `_wfHasPath(edges, to, from)` checks
// whether the target can already reach the source; if so the new edge would
// close a loop and the drop is refused with a toast naming both nodes. Save
// and run-start re-check server-side (`validate_workflow`/`compile_workflow`)
// — this is defence-in-depth, not the only guard.
//
// TOUCH IS FIRST-CLASS (brief, "Ron uses this from his phone"): every drag —
// place-from-palette, move-a-node, connect-a-port, pan-the-canvas — is one
// pointer-event gesture family, mirroring `floor.js`'s drag-to-hire shape
// (long-press activation on touch, 8px slop on mouse/pen). THE TRAP THAT BIT
// drag-to-hire TWICE (`floor.js` comments, `.fl-hire-dragging`): a draggable
// element's `touch-action` must default to something scrollable
// (`pan-y`/`manipulation`) and switch to `none` ONLY via a class added at
// drag ACTIVATION, never permanently — see `.wfb-node-head`/
// `.wfb-palette-block` in app.css. Ports are small dedicated controls, not
// scrollable list rows, so they carry `touch-action: none` unconditionally
// (same posture as an ordinary button) and get a real 40x40px hit box
// (`.wfb-port`) around a smaller visible dot, per the brief's "ports need
// >= 40px hit targets". Pinch-zoom is native touchstart/touchmove tracking
// (mirrors `mermaid.js`'s viewer-gesture pattern) — Pointer Events don't
// aggregate multi-touch, so a 2-finger pinch cancels any single-pointer pan
// in flight and takes over.
//
// NO BACKGROUND REFRESH while this modal is open (same as the spine build) —
// the trap of a poll rewriting the DOM mid-drag (floor.js's Floor re-render)
// does not apply here; every re-render (`_wfRender`) is the direct result of
// a user action on this same modal, never an ambient timer.
//
// FIELD SYNC: node fields (name/prompt/project/persona/outcomes/options/
// action config) are read from the DOM into the in-memory `def` only at the
// moment of a structural action (`_wfSyncDomToModel`), not on every
// keystroke — typing in one node's prompt is never clobbered by placing a
// new block or dragging an edge elsewhere on the canvas.

// The one mounted canvas -- singleton, same discipline the old modal had
// (only one workflow can be under edit at a time). `{ projectId, _wf }` where
// `_wf` is exactly the state shape `_wfFreshState` always returned; kept
// nested under `_wf` (rather than flattened) so every existing call site
// below that read `entry._wf.*` needed no further change once `_wfEntry()`
// stood in for `openModals.get(WF_MODAL_ID)`.
let _wfState = null;
function _wfEntry() { return _wfState; }
let _wfNameSeq = 0;
const _wfCharCache = new Map();

function _wfNewName(prefix) {
  _wfNameSeq += 1;
  return `${prefix}-${_wfNameSeq}`;
}

// HTML-safe (`esc`) is for literal markup. These two are for the OTHER two
// contexts a node name flows through: a dynamically-built CSS attribute
// selector (`_wfAttrEsc`, used when matching `[data-node="…"]` from JS) and a
// single-quoted inline-JS string literal inside an onclick/onpointerdown
// attribute (`_wfJsStrEsc`). Conflating either with `esc()` would leave an
// attacker- or just apostrophe-carrying node name able to break out of the
// selector or the attribute.
function _wfAttrEsc(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}
function _wfJsStrEsc(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function _wfBlankDef() {
  return { name: '', description: '', enabled: true, trigger: { type: 'manual' }, nodes: [], edges: [] };
}

function _wfTypeLabel(t) {
  return { agent: 'AGENT STEP', approval: 'APPROVAL GATE', action: 'CLAYRUNE ACTION' }[t] || String(t || '').toUpperCase();
}

function _wfBlankNode(type, x, y) {
  const name = _wfNewName(type);
  const base = { type, name, x: Math.round(x), y: Math.round(y) };
  if (type === 'agent') return { ...base, project_id: '', character: '', prompt: '', outcomes: [] };
  if (type === 'approval') return { ...base, options: ['approve', 'reject'] };
  if (type === 'action') return { ...base, action: 'backlog_create', config: {} };
  return base;
}

// The declared branching vocabulary a node's outgoing edges may name in
// `when` (mc/workflows.py `_declared_vocab`, mirrored here).
function _wfVocab(node) {
  if (node.type === 'agent') return node.outcomes || [];
  if (node.type === 'approval') return node.options || [];
  return [];
}

// Ports actually drawn on the node. R2-D6: a non-empty vocabulary always
// gets a trailing `otherwise` port; an empty vocabulary (a plain agent step,
// or any action node — action steps cannot have conditional edges at all,
// `validate_workflow`) gets exactly one plain unconditional port.
function _wfOutPorts(node) {
  const vocab = _wfVocab(node);
  if (node.type === 'action' || !vocab.length) return [{ when: null, label: '' }];
  return vocab.map(w => ({ when: w, label: w })).concat([{ when: 'otherwise', label: 'otherwise' }]);
}

// ── The Bench, as the palette (UI brief §2) ─────────────────────────────────
//
// "Drag people, not primitives." A palette row IS a hired agent; dropping one
// creates an agent step with that persona already chosen, so there is no
// "add an Agent step, then pick a persona" detour. Rows come from the SAME
// endpoint the Floor's bench reads (`/api/floor` → `bench`), so a type hired
// on the Floor is draggable here without a second roster concept.

// FACES: one resolution path, the Floor's. `avatarHTML` (render-core.js) is
// the only implementation — `fig:<name>` becomes the real figure image, a
// renderable emoji becomes a glyph. This is the guarded call shape
// `_floorAvatarHTML` uses: window.avatarHTML is a cross-module global and a
// module-load-order race would otherwise throw mid-render (floor.js, ws001
// Finding 1).
//
// TEST THE CLEANED VALUE, NOT THE RAW ONE. `avatarIsFigure`/`avatarIsRenderable`
// normalise (trim, then `fig:` prefix or "at least one codepoint above U+007F")
// exactly as `characters.clean_avatar` does server-side, and they are the same
// predicates `avatarHTML` itself branches on — so the usability verdict and the
// render can never disagree. That disagreement is the bug `_figure_avatar`
// documents: a `??` (an emoji flattened by a Windows console codepage) tested
// as present, rendered as nothing, and outranked the real face underneath it.
// An avatar that is neither a known figure nor a real emoji therefore falls
// THROUGH to the initial mark below — it is never echoed into the DOM as text.
function _wfAvatarHTML(person, size) {
  const v = (person && person.avatar) || '';
  const usable = (typeof window.avatarIsFigure === 'function' && window.avatarIsFigure(v))
    || (typeof window.avatarIsRenderable === 'function' && window.avatarIsRenderable(v));
  if (usable && typeof window.avatarHTML === 'function') return window.avatarHTML(v, size);
  // Genuinely faceless: the name's own initial, never a random face and never
  // the raw unusable string (Ron, 2026-09-11 — the mock's letter bubbles are
  // the FALLBACK here, not the default).
  return `<span class="wfb-face-initial" style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.44)}px"
    >${esc(_wfInitial(person))}</span>`;
}

function _wfInitial(person) {
  const s = String((person && (person.display || person.name)) || '').trim();
  return s ? s[0].toUpperCase() : '·';
}

function _wfBenchLookup(st, scope, name) {
  return (st.bench || []).find(b => (b.scope || 'global') === (scope || 'global') && b.name === name) || null;
}

// A node stores its persona as the persona picker's own value shape,
// `<scope>:<name>` (see `_wfReloadCharacters`), so the card header can find
// the bench row that owns the face.
function _wfPersonFromCharacter(st, character) {
  const s = String(character || '');
  const i = s.indexOf(':');
  if (i < 0) return null;
  return _wfBenchLookup(st, s.slice(0, i), s.slice(i + 1));
}

// ── Engine hover tooltip (Ron, 2026-09-11 — "what will this step cost to
// run") ──────────────────────────────────────────────────────────────────
// Mirrors mc/blueprints/agent_routes.py's dispatch precedence EXACTLY —
// this is a display of what mc/workflows.py's _dispatch_step actually hands
// to _dispatch_agent_internal, not an independent guess:
//   1. the resolved persona's own engine pin (`_character_engine`) — always
//      wins and, for model, BYPASSES the auto-router entirely (agent_routes.py
//      ~5794/~5925: a character's pinned model sets `model_override`, and the
//      router is never consulted once that's set).
//   2. otherwise, if the resolved provider is 'claude' and the auto-router
//      is on (`state.CONFIG.auto_model_enabled`), the model is chosen BY A
//      CLASSIFIER AT DISPATCH TIME (Haiku/Sonnet/Opus, from the prompt) —
///     this is a per-turn decision, so there is no static number to show.
//   3. otherwise: the project's own `agent_model`/`agent_effort`, then the
//      global config's, exactly like `_build_claude_flags`.
// A non-claude provider (gemini/codex/...) never reaches the router at all
// (agent_routes.py:5816 returns through `_dispatch_via_runtime` first) and
// effort is a claude-CLI-only flag (`_dispatch_via_runtime` never threads
// one through) — so both are gated on the resolved provider being 'claude'.
// A resolved-but-deleted persona, or a project this node no longer points
// at, renders an em dash and the reason — never a guessed model id.
function _wfProjectFor(node) {
  return (typeof allProjects !== 'undefined' ? allProjects : [])
    .find(p => p.id === (node && node.project_id)) || null;
}

// The persona dispatch will actually use: the node's own pick, or — exactly
// like `_resolve_character`'s `source='project'` branch — the project's
// `default_character` when the node has none. Returns the bench row (or
// null if the reference doesn't resolve) plus the raw "scope:name" it came
// from, so a stale reference can be named instead of silently dropped.
function _wfResolvedPersonaFor(st, node, proj) {
  let ref = (node && node.character) || '';
  if (!ref) ref = (proj && proj.default_character) || '';
  if (!ref) return { person: null, ref: '' };
  const i = ref.indexOf(':');
  if (i < 1) return { person: null, ref };
  return { person: _wfBenchLookup(st, ref.slice(0, i), ref.slice(i + 1)), ref };
}

// A model id → its friendly label, for whichever provider actually owns it.
// `_agentProviders` (index.html, `/api/agent/providers`) carries every
// runtime's own catalog, same as the composer's Model picker
// (conversation.js `_providerModelChoices`); `MC_MODEL_CHOICES` (modal-
// manager.js, window-exposed) is the claude-only fallback for the window
// this fetch hasn't resolved into yet. An id neither knows is still shown
// verbatim — a raw id is a fact, a blank would read as "nothing pinned".
function _wfModelLabel(provider, modelId) {
  if (!modelId) return '';
  const provRec = (typeof _agentProviders !== 'undefined' && _agentProviders || [])
    .find(x => x.name === provider);
  const hit = provRec && Array.isArray(provRec.models)
    ? provRec.models.find(x => x.id === modelId) : null;
  if (hit) return hit.label || modelId;
  if (provider === 'claude') {
    const c = (window.MC_MODEL_CHOICES || []).find(x => x[0] === modelId);
    if (c) return c[1];
  }
  return modelId;
}

// Shared core, reused by BOTH hovers (canvas node tooltip and palette popup —
// MC-871 follow-up): given an already-resolved person (bench row, possibly
// null) and its project (possibly null — the palette has no node yet, so no
// project may exist to inherit from), produce the "Model: ..." line. `proj`
// being null is NOT the same as `proj.agent_model` being empty: the former
// means we genuinely do not know what a drop will inherit and must say so,
// never guess a project or global default that might not be the one used.
function _wfEngineLine(person, proj, cfg, who) {
  const label = who || (person && (person.display || person.name)) || 'this persona';
  const pinModel = (person && person.model) || '';
  const pinProvider = (person && person.provider) || '';
  const pinEffort = (person && person.effort) || '';
  const provider = pinProvider || (proj && proj.provider) || cfg.default_provider || 'claude';

  let line;
  let resolved = true;
  if (pinModel) {
    line = `${_wfModelLabel(provider, pinModel)} — pinned on ${label}`;
  } else if (provider !== 'claude') {
    // The router only ever produces a claude id and never runs for another
    // provider's dispatch path — no static number to fall back to either,
    // since agent_model/agent_effort are claude ids the runtime would reject.
    line = `— (no model pinned on ${label}; ${provider} runtime uses its own default)`;
  } else if (cfg.auto_model_enabled) {
    line = 'chosen at dispatch — auto-router is ON (Haiku / Sonnet / Opus by task)';
  } else if (proj && proj.agent_model) {
    line = `${_wfModelLabel(provider, proj.agent_model)} — inherited, project default`;
  } else if (proj && cfg.agent_model) {
    line = `${_wfModelLabel(provider, cfg.agent_model)} — inherited, global default`;
  } else if (!proj) {
    line = `— (pins nothing; no project yet, so its default depends on where it lands)`;
    resolved = false;
  } else {
    // MC-871 follow-up: nothing pinned, provider is claude, auto-router is
    // off, and neither the project nor the global config has a model —
    // dispatch has genuinely nothing to run on. `resolved` used to stay at
    // its default `true` here (only the `!proj` branch above ever cleared
    // it), so the unresolvable-engine check callers now do against this
    // flag never fired for the one case the brief specifically named
    // ("a character that pins nothing AND a project with no model
    // default"). Fixed as part of that check, not a cosmetic change.
    line = '— (no model configured anywhere in the chain)';
    resolved = false;
  }

  if (provider === 'claude' && resolved) {
    const effort = pinEffort || (proj && proj.agent_effort) || cfg.agent_effort || '';
    if (effort) {
      // With no project yet (palette hover on a person whose home project is
      // empty and no builder hint), a pinned MODEL still resolves but the
      // effort chain does not: the project it lands in can override the
      // global default, so don't assert one we can't know.
      const src = pinEffort ? ''
        : ((proj && proj.agent_effort) ? ', project default'
        : (proj ? ', global default' : ', global default unless its project overrides'));
      line += ` · effort ${effort}${src}`;
    }
  }
  return { text: 'Model: ' + line, resolved, provider };
}

// MC-871 follow-up (Item A, case 2): a persona can pin a model id its
// provider has since stopped offering (a retired snapshot, a renamed id).
// `_wfModelLabel` deliberately shows a raw unknown id verbatim rather than
// guessing (see its own header comment) — right for the label, but it means
// nothing else in this file ever notices the id is dead. Reads the exact
// same catalog sources `_wfModelLabel` does (`_agentProviders`,
// `MC_MODEL_CHOICES`) so it can never disagree with what the label already
// shows. A provider whose catalog hasn't loaded yet, or that has no models
// list at all (a mode-A-only runtime with no --model flag), can't be judged
// either way — this only returns true on a genuine, checkable miss, never
// on missing data (a false "no longer exists" would be worse than no check).
function _wfModelUnknown(provider, modelId) {
  if (!modelId) return false;
  const list = (typeof _agentProviders !== 'undefined' && _agentProviders) || null;
  if (!list) return false;
  const provRec = list.find(x => x.name === provider);
  if (!provRec || !Array.isArray(provRec.models) || !provRec.models.length) return false;
  if (provRec.models.some(x => x.id === modelId)) return false;
  if (provider === 'claude' && (window.MC_MODEL_CHOICES || []).some(x => x[0] === modelId)) return false;
  // Retired-but-still-valid ids. `MC_LEGACY_MODEL_LABELS` (modal-manager.js)
  // exists precisely because "an existing project/conversation may be pinned
  // to one" -- the picker stopped offering claude-opus-4-8, the CLI still
  // accepts it. Treating those as dead would flag a step that runs fine.
  if (provider === 'claude' && (window.MC_LEGACY_MODEL_LABELS || {})[modelId]) return false;
  return true;
}

// Full-info resolver for an actual canvas node (person + project already
// looked up), shared by the canvas tooltip, the Save/Run-now validator
// (Item A) and the live unauthenticated-provider warning (Item B) — three
// call sites now, all going through _wfEngineLine so none of them can ever
// disagree about what a step will run on.
function _wfEngineResolution(st, node) {
  const proj = _wfProjectFor(node);
  if (!proj) {
    // Already surfaced as "Needs a project" by _wfValidateGraph's structural
    // check — not re-flagged as an engine problem too.
    return { text: 'Model: — (no project set on this step)', resolved: true, provider: '', proj: null, person: null };
  }
  const { person, ref } = _wfResolvedPersonaFor(st, node, proj);
  if (ref && !person) {
    return { text: `Model: — (persona "${ref}" not found — deleted or renamed)`, resolved: true, provider: '', proj, person: null };
  }
  const cfg = (typeof _globalConfig !== 'undefined' ? _globalConfig : {}) || {};
  const who = person ? (person.display || person.name) : '';
  const line = _wfEngineLine(person, proj, cfg, who);
  return { text: line.text, resolved: line.resolved, provider: line.provider, proj, person };
}

function _wfEngineTooltip(st, node) {
  if (!node || node.type !== 'agent') return '';
  return _wfEngineResolution(st, node).text;
}

// Palette equivalent: the bench row IS the person (no persona-reference
// lookup needed, we already have the exact row), and there is no node yet —
// so the project is whatever a drop would actually assign it, mirroring
// `_wfMakeNode`'s own fallback exactly (person's home project, else the
// builder's own project). Only when BOTH are empty is the project genuinely
// unknown.
function _wfPaletteEngineLine(st, b) {
  const projId = (b && b.project_id) || (st && st.hintProjectId) || '';
  const proj = projId ? _wfProjectFor({ project_id: projId }) : null;
  const cfg = (typeof _globalConfig !== 'undefined' ? _globalConfig : {}) || {};
  return _wfEngineLine(b, proj, cfg, b && (b.display || b.name)).text;
}

// ── Provider auth cache for the live "won't work" warning (MC-871 follow-up,
// Item B) ── keyed by provider name. A render pass can touch a dozen agent
// cards that all share one provider; this dedupes to one in-flight fetch per
// provider and caches the result briefly so dragging/typing (41 call sites
// hit _wfRender()) doesn't refire the probe on every keystroke. Reads
// whatever is cached synchronously — never blocks a render on the network —
// and kicks a background refresh on a miss/stale entry, re-rendering once it
// lands (same precedent as render-core.js's _ensureAgentProviders().then(...)
// refreshModal()).
const _wfAuthCache = Object.create(null);   // { [provider]: { status, fetchedAt } }
const _wfAuthFetching = new Set();
const _WF_AUTH_TTL_MS = 60000;

function _wfEnsureProviderAuthFresh(provider) {
  const rec = _wfAuthCache[provider];
  if ((rec && (Date.now() - rec.fetchedAt) < _WF_AUTH_TTL_MS) || _wfAuthFetching.has(provider)) return;
  _wfAuthFetching.add(provider);
  fetch(`${API_BASE}/api/agent/provider/${encodeURIComponent(provider)}/auth`)
    .then(r => (r.ok ? r.json() : null))
    .then((d) => {
      _wfAuthFetching.delete(provider);
      // Only re-render when the probe actually landed new data. Firing a
      // full-body _wfRender() on every settle -- success OR failure -- means
      // a slow/unreachable endpoint rebuilds #wfb-body while the user might
      // be mid-drag on a card the rebuild just replaced (found via the mobile
      // touch-drag smoke case: an aborted probe's unconditional re-render
      // detached the very node the next gesture was about to grab).
      if (d && d.name) {
        _wfAuthCache[d.name] = { status: d.auth_status || 'unknown', fetchedAt: Date.now() };
        if (_wfEntry()) _wfRender();
      }
    })
    .catch(() => { _wfAuthFetching.delete(provider); });
}

// Definite-negative statuses only. Confirmed against every
// AgentRuntime.health_check() implementation in mc/agent_runtime.py plus the
// claude bridge in server.py (_claude_health_check_hook): the full
// auth_status vocabulary any runtime returns is
// ok / unknown / not_installed / not_logged_in / invalid_api_key /
// quota_exceeded — nothing else. `unknown` is what a never-probed claude
// session reports even when it IS authenticated (server.py: "a fresh,
// never-checked boot reads unknown instead of falsely claiming signed-in"),
// so warning on it would cry wolf on the common case, not the broken one —
// the brief's own "unknown is not unauthenticated" line. Warn only on a
// status this list can name as a definite negative.
function _wfProviderAuthWarning(provider) {
  if (!provider) return '';
  _wfEnsureProviderAuthFresh(provider);
  const rec = _wfAuthCache[provider];
  if (!rec || rec.status === 'ok' || rec.status === 'unknown') return '';
  return `This step will fail when the workflow runs — ${provider} is not authenticated (${String(rec.status).replace(/_/g, ' ')}).`;
}

// The single warning line an agent card shows, highest-signal first. Every
// one of these is a WARNING and none of them blocks Save — measured, not
// assumed:
//   * no model anywhere in the chain is NOT a dead step. `agent_runtime.py`
//     only appends `--model` when a model is non-empty (`if model:`), and
//     server.py:86 says of an empty value verbatim: "'' would mean whatever
//     the CLI defaults to". The step runs; it just runs on a tier that
//     drifts with the CLI, which is worth saying and not worth refusing.
//   * a pinned id missing from the catalog is UNPROVEN, not dead.
//     `AgentRuntime.model_supported` documents the opposite policy for an
//     explicit pick: "the user may legitimately type a model id newer than
//     our catalog", and it is deliberately not filtered. Legacy ids are
//     exempted outright in _wfModelUnknown.
// So this stays advisory. Blocking Save on either would refuse a workflow
// that runs fine — a false "no" the user cannot override.
function _wfEngineWarning(st, node, info) {
  if (!info) return '';
  if (!info.resolved && node.project_id) {
    const projLabel = (info.proj && (info.proj.name || info.proj.id)) || 'its project';
    const who = info.person ? (info.person.display || info.person.name) : 'This step';
    return `${who} pins no model and "${projLabel}" sets no default — this step runs on whatever the CLI picks, which changes as the CLI updates.`;
  }
  if (info.person && info.person.model && _wfModelUnknown(info.provider, info.person.model)) {
    const who = info.person.display || info.person.name;
    return `"${info.person.model}" is pinned on ${who} but ${info.provider} no longer lists it — if it has been retired, this step will fail when the workflow runs.`;
  }
  if (info.resolved && info.provider) return _wfProviderAuthWarning(info.provider);
  return '';
}

function _wfBenchFiltered(st, search) {
  const q = String(search || '').trim().toLowerCase();
  const list = st.bench || [];
  if (!q) return list;
  return list.filter(b => String(b.display || '').toLowerCase().includes(q)
    || String(b.name || '').toLowerCase().includes(q));
}

function _wfSlug(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'step';
}

function _wfUniqueNodeName(st, base) {
  const taken = new Set(((st.def && st.def.nodes) || []).map(n => n.name));
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}-${i}`)) i += 1;
  return `${base}-${i}`;
}

// The palette's two tool tiles make a bare node; a PERSON makes an agent step
// with its persona and project already resolved. Brief §8c rule 1: a dropped
// person defaults its project to that persona's home room (`project_id` on the
// bench card — the "only in <project>" pin), else the project the builder was
// opened from, so nothing else needs picking.
function _wfMakeNode(st, type, scope, name, x, y) {
  if (type !== 'person') return _wfBlankNode(type, x, y);
  const person = _wfBenchLookup(st, scope, name)
    || { scope: scope || 'global', name, display: name, avatar: '', project_id: '' };
  return {
    type: 'agent',
    name: _wfUniqueNodeName(st, _wfSlug(person.name || person.display)),
    x: Math.round(x), y: Math.round(y),
    project_id: person.project_id || st.hintProjectId || '',
    character: (person.scope || 'global') + ':' + person.name,
    prompt: '', outcomes: [],
  };
}

// ── Inline host: mount / load ────────────────────────────────────────────────
//
// `#wfb-inline-host-<projectId>` is built by agent-console.js's `loadWorkflows`
// (via `_wfSyncTabsForProject` below) the moment a project's Workflows tab is
// opened. This function loads a workflow (or starts a blank one) and mounts
// the SAME canvas markup `_wfRender` has always produced into that host --
// there is no other render path.

// True if it's safe to discard whatever is currently mounted -- not dirty, or
// the user confirmed discarding it. The inline equivalent of the old modal's
// close confirm (`_wfRequestClose`), now also the gate every tab switch and
// re-mount goes through (UI brief: "switching tabs with unsaved changes must
// hit the same dirty-state warning that closing did").
// MC-871 Change 3: this now guards ONLY the cases that genuinely replace the
// mounted state (loading a different/new workflow over a dirty canvas) — a
// plain tab switch away no longer calls this at all (see agent-console.js's
// switchModalTab and _wfSyncBeforeLeave below). Worded as REPLACING, not
// discarding, per the brief: a tab switch keeps the edit; this action does
// not.
function _wfConfirmDiscardIfDirty(targetLabel) {
  if (!_wfState || !_wfState._wf || !_wfState._wf.dirty) return true;
  const current = (_wfState._wf.def && _wfState._wf.def.name) || 'this workflow';
  const opening = targetLabel || 'a different workflow';
  return confirm(`You have unsaved changes to "${current}". Open ${opening} anyway?`);
}

// Flushes any text just typed into a field (not yet synced into `def` --
// this file's FIELD SYNC discipline only syncs at the next STRUCTURAL
// action) into the in-memory model. Called from agent-console.js's
// switchModalTab right before the tab body's DOM is torn down, so a plain
// tab switch (including "← Back to conversation") never silently drops an
// in-flight edit. No render call: the DOM is about to be destroyed by the
// caller regardless, and re-rendering it here would just be discarded work.
function _wfSyncBeforeLeave(projectId) {
  if (!_wfState || _wfState.projectId !== projectId || !_wfState._wf) return;
  _wfSyncDomToModel(_wfState);
  // Every popover in this file lives on <body>, OUTSIDE the tab-content div
  // that's about to be torn down (agent-console.js's tabOn('workflows')
  // lazy-skip) — none of them get removed by that teardown on their own, so
  // without this a popover left open would keep floating on screen over
  // whatever tab/page the user switches to next.
  _wfCloseTriggerPopover();
  _wfClosePortPopover();
  _wfCloseNodeMenu();
}
window._wfSyncBeforeLeave = _wfSyncBeforeLeave;

// Another project's Workflows tab just stole the singleton canvas. Its host
// div is still live DOM in a still-open modal -- leaving it wired to
// event handlers that now resolve against a DIFFERENT project's `_wfState`
// would let a stray click there corrupt that other workflow. Reset it to an
// inert placeholder instead of leaving stale interactive markup behind.
function _wfRenderIdleHost(projectId) {
  const host = document.getElementById('wfb-inline-host-' + projectId);
  if (host) host.innerHTML = '<div class="wfb-canvas-idle">Editing moved to another project — pick a tab to resume here.</div>';
}

// `targetLabel` is the ONE place this ever confirms a discard (Ron: choosing
// "+ New Workflow" while dirty needed OK pressed TWICE, and Cancel on the
// second of those left the pane stuck). `_wfTabClick`/`_wfNewWorkflowClick`
// used to run their own `_wfConfirmDiscardIfDirty` first and then call here,
// which ran the SAME check again with no label -- two dialogs for one click,
// the second overwriting the first's wording. They now pass their label
// straight through and never check it themselves; `_wfSyncTabsForProject`'s
// own auto-select call (no caller-side check of its own) still gets the
// generic wording by passing none.
async function openWorkflowBuilder(workflowId, hintProjectId, targetLabel) {
  const projectId = hintProjectId || (_wfState && _wfState.projectId) || '';
  const host = document.getElementById('wfb-inline-host-' + projectId);
  if (!host) { console.warn('[workflow-builder] no inline host mounted for project', projectId); return; }
  if (!_wfConfirmDiscardIfDirty(targetLabel)) return;
  if (_wfState && _wfState.projectId && _wfState.projectId !== projectId) _wfRenderIdleHost(_wfState.projectId);
  // The trigger popover (Change 2) lives on <body>, outside #wfb-inline-host
  // -- a fresh load below replaces the host's contents but would otherwise
  // leave a stale popover open over whatever loads next, still wired to the
  // OLD state via its onclick handlers' closed-over `_wfPortPopover`-style
  // module state. Close it before the swap, same as _wfRender() already does
  // unconditionally for the port "+" popover.
  _wfCloseTriggerPopover();

  host.innerHTML = '<div id="wfb-body" class="wfb-modal-body"></div>';
  // Dirty-state watcher (UI brief §6/build-order step 6): delegated so it
  // survives every _wfRender() rebuild of #wfb-body's children -- attached
  // once, to the body element itself, not to anything _wfRender() replaces.
  const wfBody = host.querySelector('#wfb-body');
  wfBody.addEventListener('input', _wfMarkDirty);
  wfBody.addEventListener('change', _wfMarkDirty);

  _wfState = { projectId, _wf: null };
  await _wfLoadInto(_wfState, workflowId || null, projectId);
  _wfRender();
}

// Re-mount the ALREADY-LOADED state into a freshly (re)built host div -- no
// network refetch. Used when `_wfSyncTabsForProject` has to rebuild the tabs
// row (the workflow set changed) but the workflow already mounted for this
// project is still in that set: the in-memory edit survives, only the DOM
// wrapper around it is rebuilt.
function _wfRemountDom(projectId) {
  const host = document.getElementById('wfb-inline-host-' + projectId);
  if (!host || !_wfState || _wfState.projectId !== projectId) return;
  host.innerHTML = '<div id="wfb-body" class="wfb-modal-body"></div>';
  const wfBody = host.querySelector('#wfb-body');
  wfBody.addEventListener('input', _wfMarkDirty);
  wfBody.addEventListener('change', _wfMarkDirty);
  _wfRender();
}

function _wfFreshState(def, workflowId, error, hintProjectId) {
  // linkedSchedule: the schedule record whose workflow_id points at this
  // workflow (MC-871 Q4 -- "one store, two views"). null until loaded/created;
  // the CADENCE lives there, never duplicated onto def.trigger, which stays
  // a bare `{type: 'schedule'}` marker.
  // hintProjectId: the project the builder was opened FROM (the tab's own
  // project, when known). Fallback target for a dropped person with no home
  // room of their own (UI brief §2: "if none, the current project").
  // bench/benchLoaded/paletteSearch: the Bench roster backing the palette
  // (UI brief §2 — "the palette is the Bench, drag people not primitives"),
  // loaded once per modal open from the SAME endpoint the Floor/Bench reads.
  // dirty/savedAt (build step 6): tracked session-side only -- the store has
  // no updated_at/version field (confirmed against mc/workflows.py), so the
  // stamp is "since this modal opened", not a durable last-modified. runErrors/
  // scrollToNode carry a failed Save/Run-now's per-card messages (step 6,
  // "inline on the offending card, not only a toast") to the next render.
  // descOpen/paletteExpanded (MC-871 Change 1/9): descOpen starts OPEN when
  // the loaded def already carries a description (never hide content that's
  // there), then tracks the toolbar's disclosure toggle. paletteExpanded
  // tracks the palette's "+N more"/"Show fewer" state (Change 9) -- separate
  // from paletteSearch, which already bypasses the cap on its own.
  // promptOpen (MC-871 agent-card pass): {nodeName: bool} -- the user's own
  // disclosure toggle for each agent card's prompt panel (_wfRenderPromptField).
  // Lives here, not on the DOM, because _wfRender() rebuilds #wfb-body's
  // innerHTML from scratch on every structural change; _wfSyncDomToModel
  // migrates a renamed node's key the same way it already repoints edges.
  // _undo/_redo/_lastSnapshot/_savedSnapshot (Change 3): see _wfCheckpointForUndo
  // and _wfStampSavedSnapshot below for how these three stay in sync.
  return { def, workflowId, saving: false, error: error || null, _cardSeq: 0,
           _charLoads: [], viewport: { x: 60, y: 40, scale: 1 }, linkedSchedule: null,
           hintProjectId: hintProjectId || '', bench: [], benchLoaded: false, paletteSearch: '',
           paletteExpanded: false, promptOpen: {},
           dirty: false, savedAt: null, runErrors: null, scrollToNode: null,
           descOpen: !!(def.description && String(def.description).trim()),
           _undo: [], _redo: [], _lastSnapshot: null, _savedSnapshot: null };
}

async function _wfLoadInto(entry, workflowId, hintProjectId) {
  if (!workflowId) {
    entry._wf = _wfFreshState(_wfBlankDef(), null, null, hintProjectId);
    await _wfLoadBench(entry._wf);
    _wfStampSavedSnapshot(entry._wf);
    return;
  }
  try {
    const res = await fetch(API_BASE + '/api/workflows');
    const list = await res.json();
    const found = (list || []).find(w => w.id === workflowId);
    entry._wf = _wfFreshState(
      found ? JSON.parse(JSON.stringify(found)) : _wfBlankDef(),
      found ? found.id : null,
      found ? null : 'Workflow not found',
      hintProjectId);
    if (found) await _wfLoadLinkedSchedule(entry._wf);
  } catch (e) {
    entry._wf = _wfFreshState(_wfBlankDef(), null, 'Failed to load workflow', hintProjectId);
  }
  await _wfLoadBench(entry._wf);
  // Baseline for Reset (Change 3): "the last saved state" for a workflow that
  // loaded from the server IS this load; for a brand-new one it's the blank
  // def _wfFreshState just built. Either way, Reset must have a stable target
  // BEFORE the user's first edit, not just after their first Save.
  _wfStampSavedSnapshot(entry._wf);
}

// Same data source as the Floor/Bench (UI brief §2, "Palette data source: the
// same roster the Floor/Bench reads"). Best-effort: a failed fetch leaves the
// palette showing an empty-bench message rather than blocking the modal —
// the builder is still usable with the Approval gate / Action tools.
async function _wfLoadBench(st) {
  try {
    const res = await fetch(API_BASE + '/api/floor');
    const data = await res.json();
    st.bench = data.bench || [];
  } catch (e) {
    console.warn('[workflow-builder] bench unavailable:', e);
    st.bench = [];
  }
  st.benchLoaded = true;
}

// ── Undo / Redo / Reset (MC-871 Change 3) ────────────────────────────────────
//
// Snapshot = {def, linkedSchedule} JSON round-tripped (the same deep-copy
// idiom _wfLoadInto/_wfSave already use throughout this file). The choke
// point is `_wfRender()` itself, not the ~30 individual mutators: every
// structural mutation in this file already ends by calling `_wfRender()`
// (never on a bare keystroke -- those only touch the DOM via the delegated
// input/change listener, see `_wfMarkDirty`), so diffing "the def as of the
// last render" against "the def right now" at the top of `_wfRender()` is
// exactly one undo step per structural action and zero per keystroke, with
// no call added at any of those ~30 sites. Cap: 50 steps -- generous for one
// editing session, bounded so the stack can't grow unbounded across a long
// one.
const WFB_UNDO_CAP = 50;

function _wfSnapshot(st) {
  return JSON.stringify({ def: st.def, linkedSchedule: st.linkedSchedule });
}

function _wfStampSavedSnapshot(st) {
  st._savedSnapshot = _wfSnapshot(st);
  st._lastSnapshot = st._savedSnapshot;
}

function _wfApplySnapshot(st, raw) {
  const snap = JSON.parse(raw);
  st.def = snap.def;
  st.linkedSchedule = snap.linkedSchedule;
}

// Called at the top of every `_wfRender()`. Pushes the PREVIOUS checkpoint
// (not the current state) onto the undo stack the first time it sees the def
// has actually changed since that checkpoint, then advances the checkpoint.
// `_wfUndo`/`_wfRedo`/`_wfResetCanvas` pre-set `st._lastSnapshot` to the
// state they just applied before calling `_wfRender()`, so this sees "no
// change" on the render THEY trigger and doesn't re-push what it just popped.
function _wfCheckpointForUndo(st) {
  const snap = _wfSnapshot(st);
  if (st._lastSnapshot == null) { st._lastSnapshot = snap; return; }
  if (snap === st._lastSnapshot) return;
  st._undo = st._undo || [];
  st._undo.push(st._lastSnapshot);
  if (st._undo.length > WFB_UNDO_CAP) st._undo.shift();
  st._redo = []; // a fresh structural change invalidates any redo history
  st._lastSnapshot = snap;
}

function _wfCanUndo(st) { return !!(st._undo && st._undo.length); }
function _wfCanRedo(st) { return !!(st._redo && st._redo.length); }

function _wfUndo() {
  const entry = _wfEntry(); if (!entry) return;
  const st = entry._wf;
  if (!_wfCanUndo(st)) return;
  _wfSyncDomToModel(entry); // capture in-flight typing before it's discarded
  const cur = _wfSnapshot(st);
  st._redo = st._redo || [];
  st._redo.push(cur);
  if (st._redo.length > WFB_UNDO_CAP) st._redo.shift();
  const prev = st._undo.pop();
  _wfApplySnapshot(st, prev);
  st._lastSnapshot = prev;
  st.dirty = (prev !== st._savedSnapshot);
  st.runErrors = null;
  _wfCloseTriggerPopover();
  _wfRender();
}
window._wfUndo = _wfUndo;

function _wfRedo() {
  const entry = _wfEntry(); if (!entry) return;
  const st = entry._wf;
  if (!_wfCanRedo(st)) return;
  _wfSyncDomToModel(entry);
  const cur = _wfSnapshot(st);
  st._undo = st._undo || [];
  st._undo.push(cur);
  if (st._undo.length > WFB_UNDO_CAP) st._undo.shift();
  const next = st._redo.pop();
  _wfApplySnapshot(st, next);
  st._lastSnapshot = next;
  st.dirty = (next !== st._savedSnapshot);
  st.runErrors = null;
  _wfCloseTriggerPopover();
  _wfRender();
}
window._wfRedo = _wfRedo;

// Revert to the last SAVED state (or blank, for a workflow that was never
// saved -- brief's explicit fallback). Itself pushed onto the undo stack
// first, so Reset is undoable like everything else here -- Change 13, Ron:
// "no need to ask if I'm certain ... we have the undo button" -- a confirm
// and an undo are redundant, and undo is the better of the two. No dialog;
// it just happens, and Undo takes it back.
function _wfResetCanvas() {
  const entry = _wfEntry(); if (!entry) return;
  const st = entry._wf;
  _wfSyncDomToModel(entry);
  const cur = _wfSnapshot(st);
  const baseline = st._savedSnapshot || JSON.stringify({ def: _wfBlankDef(), linkedSchedule: null });
  if (cur === baseline) return; // already at the saved state -- nothing to do
  st._undo = st._undo || [];
  st._undo.push(cur);
  if (st._undo.length > WFB_UNDO_CAP) st._undo.shift();
  st._redo = [];
  _wfApplySnapshot(st, baseline);
  st._lastSnapshot = baseline;
  st.dirty = false;
  st.runErrors = null;
  _wfCloseTriggerPopover();
  _wfRender();
}
window._wfResetCanvas = _wfResetCanvas;

// Ctrl+Z / Ctrl+Shift+Z (Cmd on mac), while the canvas has focus. "Focus"
// here means the Workflows tab is the one currently showing -- its host div
// only exists in the DOM while that tab is active (agent-console.js's
// tabOn('workflows') lazy-skips the tab-content otherwise, see file header) --
// AND the browser's real text-field focus isn't inside an input/textarea
// (mirrors the existing edge-delete keydown guard just below: Delete/
// Backspace also excludes INPUT/TEXTAREA) so a field's own native undo is
// never hijacked. Not `host.contains(activeElement)`: clicking a node's
// (non-focusable) header div leaves `document.activeElement` on `<body>`,
// which would otherwise make the shortcut work only right after typing in a
// field -- the opposite of what "canvas has focus" means here.
document.addEventListener('keydown', (e) => {
  if (!_wfState) return;
  if (e.key.toLowerCase() !== 'z' || !(e.ctrlKey || e.metaKey)) return;
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  const host = document.getElementById('wfb-inline-host-' + _wfState.projectId);
  if (!host) return;
  e.preventDefault();
  if (e.shiftKey) _wfRedo(); else _wfUndo();
});

// ── Tabs row: "one per workflow that involves this project" ─────────────────
//
// Called by agent-console.js's `loadWorkflows` every time it re-fetches
// `/api/workflows` -- including the 3s poll that exists only to track an
// unrelated Claude Code fan-out. `_wfTabsSig` makes that safe: the tabs row
// (and, more importantly, the mounted canvas) are only touched when the SET
// of workflow ids touching this project actually changed, never on a poll
// that reconfirms the same set.
const _wfTabsSig = {}; // projectId -> last-seen "id,id,id" signature

function _wfTabsRowHTML(projectId, list, selectedId) {
  const tabs = list.map(w => `<button type="button" class="wfb-tab${w.id === selectedId ? ' active' : ''}"
      onclick="_wfTabClick('${_wfJsStrEsc(projectId)}','${_wfJsStrEsc(w.id)}','${_wfJsStrEsc(w.name || 'Untitled workflow')}')">${esc(w.name || 'Untitled workflow')}</button>`).join('');
  return `<div class="wfb-tabs-row">${tabs}
    <button type="button" class="wfb-tab wfb-tab-new" onclick="_wfNewWorkflowClick('${_wfJsStrEsc(projectId)}')">+ New Workflow</button>
  </div>`;
}

function _wfEmptyStateHTML() {
  return '<div style="color:var(--text-faint);font-style:italic;font-size:12px;padding:4px 0 2px">No workflows involve this project yet.</div>';
}

// targetName threaded through from the tabs row (Change 3's "word it as
// replacing" — the confirm names what's about to open, not just "a
// different workflow"). The discard confirm itself lives ONLY inside
// openWorkflowBuilder now (see its own comment) — this used to also run
// _wfConfirmDiscardIfDirty here first, so one click could show the dialog
// twice.
function _wfTabClick(projectId, workflowId, targetName) {
  if (_wfState && _wfState.projectId === projectId && _wfState._wf && _wfState._wf.workflowId === workflowId) return;
  openWorkflowBuilder(workflowId, projectId, targetName ? `"${targetName}"` : null);
}
window._wfTabClick = _wfTabClick;

function _wfNewWorkflowClick(projectId) {
  openWorkflowBuilder(null, projectId, 'a new workflow');
}
window._wfNewWorkflowClick = _wfNewWorkflowClick;

function _wfSyncTabsForProject(projectId, list) {
  const section = document.getElementById('wfb-clayrune-section-' + projectId);
  if (!section) return;
  const sig = list.map(w => w.id).sort().join(',');
  if (section.dataset.wfBuilt === '1' && _wfTabsSig[projectId] === sig) return; // nothing changed -- leave the canvas alone
  _wfTabsSig[projectId] = sig;
  section.dataset.wfBuilt = '1';

  const activeEntry = (_wfState && _wfState.projectId === projectId) ? _wfState._wf : null;
  const mountedId = activeEntry ? activeEntry.workflowId : undefined;
  // A brand-new, never-saved workflow (`workflowId: null`, _wfFreshState's
  // own "unsaved" marker) can NEVER appear in `list` -- it doesn't exist on
  // the server yet -- so `list.some(w => w.id === mountedId)` was always
  // false for it. That silently discarded an in-progress unsaved flow the
  // moment this section's DOM got rebuilt with a fresh (unbuilt) element --
  // e.g. closing and reopening the project modal, which is exactly what "start
  // a new flow, navigate away, come back" does (Ron: "the work is gone").
  // An unsaved draft counts as "still mounted" on its own; anything with a
  // real id still needs the list lookup (a workflow deleted elsewhere must
  // still fall through to auto-select below).
  const stillMounted = !!activeEntry && (mountedId === null || list.some(w => w.id === mountedId));
  const selected = stillMounted ? mountedId : (list[0] ? list[0].id : null);

  // "+ New Workflow" is always present, even with zero workflows (UI brief:
  // "zero = the canvas area shows the existing empty-state line plus the
  // new-workflow action") -- `_wfTabsRowHTML` renders it unconditionally and
  // simply has no per-workflow tabs alongside it when `list` is empty.
  section.innerHTML = _wfTabsRowHTML(projectId, list, selected)
    + (list.length ? '' : _wfEmptyStateHTML())
    + `<div id="wfb-inline-host-${esc(projectId)}" class="wfb-fill-col"></div>`;

  // NOT `selected == null` on its own any more: an unsaved draft's `selected`
  // IS null (it has no id yet) even though it is very much still mounted and
  // must be remounted, not treated as "nothing to show".
  if (!stillMounted && selected == null) return;
  if (stillMounted) _wfRemountDom(projectId);      // already loaded in memory -- just rebuild the DOM around it
  else openWorkflowBuilder(selected, projectId);   // a different/new workflow -- load it for real
}
window._wfSyncTabsForProject = _wfSyncTabsForProject;
window._wfConfirmDiscardIfDirty = _wfConfirmDiscardIfDirty;

// The schedule store is the single source of truth for cadence (spec Q4);
// this just finds the one row (if any) pointing back at this workflow.
async function _wfLoadLinkedSchedule(st) {
  if (!st.workflowId) return;
  try {
    const res = await fetch(API_BASE + '/api/schedules');
    const list = await res.json();
    st.linkedSchedule = (list || []).find(s => s.workflow_id === st.workflowId) || null;
  } catch (e) {
    st.linkedSchedule = null;
  }
}

function _wfDraftSchedule() {
  return { schedule_type: 'daily', time: '09:00', days: [1, 2, 3, 4, 5],
           interval_minutes: 60, run_at: '', cron_expr: '' };
}

// ── Graph helpers, ported from mc/workflows.py so the builder can enforce
//    the same rules the store re-validates on save ───────────────────────────

// Kahn's algorithm. Ties broken by `names`' own order (mirrors the backend).
// Returns a partial order if the graph is cyclic (shorter than `names`) —
// callers that care about cycles use `_wfHasPath` instead, which answers the
// question directly rather than inferring it from a short toposort.
function _wfToposort(names, edges) {
  const indeg = {}; const children = {};
  names.forEach(n => { indeg[n] = 0; children[n] = []; });
  edges.forEach(e => { if (children[e.from] && indeg[e.to] !== undefined) { children[e.from].push(e.to); indeg[e.to] += 1; } });
  const queue = names.filter(n => indeg[n] === 0);
  const order = [];
  for (let i = 0; i < queue.length; i++) {
    const n = queue[i];
    order.push(n);
    for (const c of children[n]) { indeg[c] -= 1; if (indeg[c] === 0) queue.push(c); }
  }
  return order;
}

function _wfParentsMap(names, edges) {
  const m = {};
  names.forEach(n => { m[n] = []; });
  edges.forEach(e => { if (m[e.to]) m[e.to].push(e.from); });
  return m;
}

function _wfSetEq(a, b) {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

// Standard iterative dominator fixpoint (mc/workflows.py `_compute_dominators`).
function _wfDominators(order, parentsMap) {
  const allNames = new Set(order);
  const roots = new Set(order.filter(n => !(parentsMap[n] || []).length));
  const dom = {};
  order.forEach(n => { dom[n] = roots.has(n) ? new Set([n]) : new Set(allNames); });
  let changed = true;
  while (changed) {
    changed = false;
    for (const n of order) {
      if (roots.has(n)) continue;
      const preds = parentsMap[n] || [];
      if (!preds.length) continue;
      let inter = null;
      for (const p of preds) {
        const dp = dom[p] || new Set();
        inter = inter === null ? new Set(dp) : new Set([...inter].filter(x => dp.has(x)));
      }
      inter = inter || new Set();
      inter.add(n);
      if (!_wfSetEq(inter, dom[n])) { dom[n] = inter; changed = true; }
    }
  }
  return dom;
}

// Does a path already exist from `from` to `to` over ALL edges, regardless of
// `when`? (Cycle detection ignores branch labels — same as the backend's
// toposort, which builds adjacency from every edge unconditionally.)
function _wfHasPath(edges, from, to) {
  if (from === to) return true;
  const children = {};
  edges.forEach(e => { (children[e.from] = children[e.from] || []).push(e.to); });
  const seen = new Set([from]);
  const stack = [from];
  while (stack.length) {
    const n = stack.pop();
    for (const c of (children[n] || [])) {
      if (c === to) return true;
      if (!seen.has(c)) { seen.add(c); stack.push(c); }
    }
  }
  return false;
}

const _WF_SLOT_STEP_RE = /\{\{\s*steps\.([a-zA-Z0-9_]+)\.[a-zA-Z0-9_.]+\s*\}\}/g;

function _wfCollectStepText(node) {
  const parts = [];
  if (node.type === 'agent') parts.push(node.prompt || '');
  if (node.type === 'action') Object.values(node.config || {}).forEach(v => { if (typeof v === 'string') parts.push(v); });
  return parts;
}

// R2-D5: a `{{steps.X.*}}` reference in node N is broken unless X exists AND
// X dominates N (X is on every trigger→N path, so X can never be skipped
// while N runs). Cyclic input (should not reach here — connect-time refuses
// cycles outright) falls back to treating every node as a root so this never
// throws; it is defence-in-depth, not the primary cycle guard.
function _wfFindBrokenSlotRefs(nodes, edges) {
  const names = nodes.map(n => n.name);
  const order = _wfToposort(names, edges);
  const parentsMap = _wfParentsMap(names, edges);
  const dom = _wfDominators(order.length === names.length ? order : names, parentsMap);
  const problems = [];
  for (const node of nodes) {
    for (const text of _wfCollectStepText(node)) {
      _WF_SLOT_STEP_RE.lastIndex = 0;
      let m;
      while ((m = _WF_SLOT_STEP_RE.exec(text))) {
        const ref = m[1];
        if (ref === node.name) continue; // backend flags self-reference separately at save
        if (!names.includes(ref)) { problems.push({ step: node.name, ref }); continue; }
        const domSet = dom[node.name];
        if (!domSet || !domSet.has(ref)) problems.push({ step: node.name, ref });
      }
    }
  }
  return problems;
}

function _wfNewViolations(before, after) {
  return after.filter(a => !before.some(b => b.step === a.step && b.ref === a.ref));
}

// ── Step 5b: legal-slot Insert control + a read-only "chip" legend over the
//    reused plain <textarea>/<input> fields. Ground rule 3 (reuse the Phase-2
//    editors verbatim) rules out swapping them for a contenteditable so a
//    real in-text chip could render; this is the "at minimum visually
//    distinguishable" fallback the brief allows instead. Both the Insert
//    dropdown and the chip legend's broken/valid read reuse the EXACT
//    dominator fixpoint _wfFindBrokenSlotRefs already computes for the save
//    guard (R2-D5), so what the dropdown offers and what the legend flags as
//    broken can never disagree with each other or with the validator. ───────

function _wfLegalInsertSlots(def, nodeName) {
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const names = nodes.map(n => n.name);
  const order = _wfToposort(names, edges);
  const parentsMap = _wfParentsMap(names, edges);
  const dom = _wfDominators(order.length === names.length ? order : names, parentsMap);
  const domSet = dom[nodeName] || new Set([nodeName]);
  // Definition order, not dominator-fixpoint order -- a stable, scannable
  // dropdown that doesn't reshuffle as the author wires more of the graph.
  const ancestors = names.filter(n => n !== nodeName && domSet.has(n));
  const parents = parentsMap[nodeName] || [];
  return { ancestors, prevLegal: parents.length === 1 ? parents[0] : null };
}

// MC-871 agent-card pass (Ron: "'Insert' and some other unclear options --
// not sure what is the function of that"). The mechanism is untouched -- the
// VALUE inserted at the cursor is still the literal `{{steps.NAME.output}}`
// slot string (_wfInsertSlot reads it verbatim) -- only the visible <option>
// text changes to plain English, the same value/label split the project
// picker two lines up already uses. The raw slot string is still one hover
// away via `title`, for anyone who wants it.
function _wfInsertControlHTML(def, nodeName, fieldSelector) {
  const { ancestors, prevLegal } = _wfLegalInsertSlots(def, nodeName);
  const opts = [];
  if (prevLegal) opts.push({ value: '{{prev.output}}', label: "Previous step's result" });
  ancestors.forEach(a => opts.push({ value: `{{steps.${a}.output}}`, label: `${a}'s result` }));
  opts.push({ value: '{{trigger.fired_at}}', label: 'When the trigger fired' });
  opts.push({ value: '{{run.id}}', label: "This run's ID" });
  return `<select class="wfb-insert-select" title="Pull an earlier step's result into this field, at the cursor"
      onchange="_wfInsertSlot(event,'${_wfJsStrEsc(nodeName)}','${_wfJsStrEsc(fieldSelector)}')">
    <option value="">Use an earlier step's result&hellip;</option>
    ${opts.map(o => `<option value="${esc(o.value)}" title="${esc(o.value)}">${esc(o.label)}</option>`).join('')}
  </select>`;
}

const _WF_SLOT_ANY_RE = /\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}/g;

// Single source of truth for "is this one ref legal here" -- shared by the
// read-only chip legend below and _wfTextHasBrokenSlotRef (the collapse
// force-open check), so the two can never disagree about what counts as
// broken (same guarantee the file header already documents for the dropdown
// vs. the legend).
function _wfSlotRefBroken(ref, legalSteps, prevLegal) {
  if (ref === 'prev.output') return !prevLegal;
  if (ref === 'trigger.fired_at' || ref === 'run.id') return false;
  const stepM = /^steps\.([a-zA-Z0-9_]+)\./.exec(ref);
  return !stepM || !legalSteps.has(stepM[1]);
}

// Read-only legend of the refs ALREADY TYPED into `text` (not the legal-to-
// insert list -- that's the dropdown above), each flagged valid/broken. The
// "In this text:" label exists because a bare row of `{{...}}` strings next
// to an "Insert" control read, to Ron, as more of the same unexplained UI.
function _wfSlotChipsHTML(def, nodeName, text) {
  const refs = [];
  _WF_SLOT_ANY_RE.lastIndex = 0;
  let m;
  while ((m = _WF_SLOT_ANY_RE.exec(text || ''))) refs.push(m[1]);
  if (!refs.length) return '';
  const { ancestors, prevLegal } = _wfLegalInsertSlots(def, nodeName);
  const legalSteps = new Set(ancestors);
  const chip = (ref) => {
    const broken = _wfSlotRefBroken(ref, legalSteps, prevLegal);
    return `<span class="wfb-slot-chip${broken ? ' wfb-slot-chip-broken' : ''}"
      title="${broken ? 'Not reachable on every path into this step' : 'Valid here'}">{{${esc(ref)}}}</span>`;
  };
  return `<div class="wfb-slot-chips"><span class="wfb-slot-chips-label">In this text:</span>${refs.map(chip).join('')}</div>`;
}

// Live broken-ref check, independent of any Save/Run-now attempt -- a rename
// or an edge change can make a reference illegal the instant it happens, and
// the collapsed prompt panel must not be the thing that hides that (brief:
// "the existing broken-slot detection is load-bearing; do not let the
// collapse hide it"). Reuses _wfSlotRefBroken so this can never disagree
// with what the chip legend itself would flag.
function _wfTextHasBrokenSlotRef(def, nodeName, text) {
  _WF_SLOT_ANY_RE.lastIndex = 0;
  let m;
  const { ancestors, prevLegal } = _wfLegalInsertSlots(def, nodeName);
  const legalSteps = new Set(ancestors);
  while ((m = _WF_SLOT_ANY_RE.exec(text || ''))) {
    if (_wfSlotRefBroken(m[1], legalSteps, prevLegal)) return true;
  }
  return false;
}

// Writes at the field's caret, same "sync first" discipline every other
// structural mutation follows (file header, FIELD SYNC): other cards' unsynced
// edits are captured into the model before this one mutation is applied, so a
// re-render never clobbers a prompt someone else is mid-typing.
function _wfInsertSlot(e, nodeName, fieldSelector) {
  const value = e.target.value;
  e.target.value = '';
  if (!value) return;
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  nodeName = renameMap[nodeName] || nodeName;
  const node = (entry._wf.def.nodes || []).find(n => n.name === nodeName);
  if (!node) return;
  const cardEl = document.querySelector(`.wfb-node[data-name="${_wfAttrEsc(nodeName)}"]`);
  const field = cardEl && cardEl.querySelector(fieldSelector);
  if (!field) return;
  const start = field.selectionStart != null ? field.selectionStart : field.value.length;
  const end = field.selectionEnd != null ? field.selectionEnd : field.value.length;
  const newText = field.value.slice(0, start) + value + field.value.slice(end);
  const caret = start + value.length;
  if (node.type === 'agent' && fieldSelector === '.wfb-prompt') {
    node.prompt = newText;
  } else {
    const cfgMatch = /\[data-cfg-key="([^"]+)"\]/.exec(fieldSelector);
    if (!cfgMatch) return;
    node.config = node.config || {};
    node.config[cfgMatch[1]] = newText;
  }
  _wfMarkDirty();
  _wfRender();
  const freshField = document.querySelector(`.wfb-node[data-name="${_wfAttrEsc(nodeName)}"] ${fieldSelector}`);
  if (freshField) {
    freshField.focus({ preventScroll: true });
    try { freshField.setSelectionRange(caret, caret); } catch (err) { /* not all input types support it */ }
  }
}

// ── Field sync: DOM → in-memory def, at the moment of a structural action ────

// Returns a `{oldName: newName}` map for every node renamed by this sync.
// EVERY caller that identifies a node/edge by a name baked into an onclick/
// onpointerdown attribute at the LAST render must remap through this before
// using that name — sync can rename the very node the caller is about to
// act on (the user typed a new name, then clicked "+ Add outcome" or a
// port on that same still-unrendered card), and a name baked into markup
// two renders ago is not the same string as the model's name right now.
function _wfSyncDomToModel(entry) {
  const def = entry._wf.def;
  const nameEl = document.getElementById('wfb-name');
  if (nameEl) def.name = nameEl.value;
  const descEl = document.getElementById('wfb-desc');
  if (descEl) def.description = descEl.value;
  // Enabled is a toolbar toggle pill now (Change 1), not a checkbox synced
  // from the DOM -- _wfToggleEnabled mutates def.enabled directly, the same
  // way _wfToggleSchedEnabled already does for the linked schedule's own flag.
  if (def.trigger && def.trigger.type === 'schedule') _wfSyncScheduleFormToState(entry._wf);
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const renameMap = {};
  document.querySelectorAll('.wfb-node').forEach((nodeEl) => {
    const oldName = nodeEl.dataset.name;
    const node = nodes.find(n => n.name === oldName);
    const own = nodeEl.querySelector('.wfb-node-own');
    if (!node || !own) return;
    _wfSyncNodeOwn(node, own);
    if (node.name && node.name !== oldName) {
      // Renames aren't guarded against breaking a TEXT slot reference
      // elsewhere (spine behaviour, carried forward — see file header), but
      // a rename WOULD silently orphan this node's own edges if they
      // weren't repointed, which is new breakage this file would be
      // introducing, not inheriting. Repoint them -- and Change 12a's
      // def.trigger.entry the same way, for the same reason.
      edges.forEach(e => { if (e.from === oldName) e.from = node.name; if (e.to === oldName) e.to = node.name; });
      if (def.trigger && Array.isArray(def.trigger.entry)) {
        def.trigger.entry = def.trigger.entry.map(n => n === oldName ? node.name : n);
      }
      renameMap[oldName] = node.name;
    }
  });
  // promptOpen is keyed by node name (see _wfFreshState) -- a rename that
  // isn't repointed here would silently reset that card's disclosure state
  // to collapsed the next render, since the old key would no longer match.
  if (entry._wf.promptOpen) {
    Object.keys(renameMap).forEach((oldName) => {
      if (Object.prototype.hasOwnProperty.call(entry._wf.promptOpen, oldName)) {
        entry._wf.promptOpen[renameMap[oldName]] = entry._wf.promptOpen[oldName];
        delete entry._wf.promptOpen[oldName];
      }
    });
  }
  return renameMap;
}

function _wfSyncNodeOwn(node, own) {
  const nameEl = own.querySelector('.wfb-name');
  if (nameEl) node.name = nameEl.value.trim() || node.name;
  if (node.type === 'agent') {
    const projEl = own.querySelector('.wfb-project');
    const persEl = own.querySelector('.wfb-persona');
    const promptEl = own.querySelector('.wfb-prompt');
    if (projEl) node.project_id = projEl.value;
    if (persEl) node.character = persEl.value;
    if (promptEl) node.prompt = promptEl.value;
    node.outcomes = [...own.querySelectorAll('.wfb-outcome-input')].map(i => i.value.trim()).filter(Boolean);
  } else if (node.type === 'approval') {
    node.options = [...own.querySelectorAll('.wfb-option-input')].map(i => i.value.trim()).filter(Boolean);
  } else if (node.type === 'action') {
    const selEl = own.querySelector('.wfb-action-select');
    if (selEl) node.action = selEl.value;
    const config = {};
    own.querySelectorAll('[data-cfg-key]').forEach((el) => {
      const key = el.dataset.cfgKey;
      const required = el.dataset.cfgRequired === '1';
      if (el.value.trim() !== '' || required) config[key] = el.value;
    });
    node.config = config;
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function _wfRender() {
  const entry = _wfEntry();
  if (!entry || !entry._wf) return;
  const st = entry._wf;
  _wfCheckpointForUndo(st); // Change 3: one undo step per structural render, zero per keystroke
  _wfClosePortPopover(); // its anchor port is about to be replaced
  const body = document.getElementById('wfb-body');
  if (!body) return;
  body.innerHTML = _wfRenderBody(st);
  st._charLoads.forEach(({ seq, want }) => _wfReloadCharacters(seq, want));
  // Run-now/Save set scrollToNode to the first offending card (step 6); pan
  // it into view here, once, then clear the flag so it doesn't re-pan on
  // every unrelated re-render that follows.
  if (st.scrollToNode) {
    const target = st.scrollToNode;
    st.scrollToNode = null;
    _wfPanToNode(st, target);
  }
  _wfApplyViewport(st);
  _wfAttachCanvasGestures();
  // The trigger popover (Change 2) lives outside #wfb-body (same reason the
  // port popover does -- an overflow:auto ancestor would clip it), so a full
  // body re-render doesn't touch it. Every mutator it drives (_wfSetTriggerType,
  // _wfSetSchedType, _wfToggleSchedEnabled...) already ends in this same
  // _wfRender(), so refreshing its content here -- rather than teaching each
  // of those to know about the popover -- is the one place that keeps it in
  // sync without forking the cadence form.
  if (_wfTriggerPopoverOpen) {
    const pop = document.getElementById('wfb-trigger-popover');
    if (pop) pop.innerHTML = _wfTriggerPopoverHTML(st);
    else _wfTriggerPopoverOpen = false;
  }
}

// Cards live on a pan/zoom CSS-transformed canvas, not inside a scrolling
// container, so scrollIntoView() does nothing here -- centre the viewport on
// the node instead. 260/... below are the node width/an estimated header+
// first-field height (`.wfb-node` in app.css); exact centring isn't the
// point, getting the flagged card on-screen is.
function _wfPanToNode(st, nodeName) {
  const node = (st.def.nodes || []).find(n => n.name === nodeName);
  const vp = document.getElementById('wfb-canvas-viewport');
  if (!node || !vp) return;
  const rect = vp.getBoundingClientRect();
  const cx = (node.x || 0) + 130;
  const cy = (node.y || 0) + 80;
  st.viewport.x = rect.width / 2 - cx * st.viewport.scale;
  st.viewport.y = rect.height / 2 - cy * st.viewport.scale;
}

// ── The Trigger box (MC-871 Change 2, canvas) ────────────────────────────────
//
// Ron: "Needs the Trigger box to exist on the canvas as first point" -- chosen
// deliberately as its OWN shape, not a real node: it is never a member of
// `def.nodes`, never in NODE_TYPES, has no card editor of its own (Change 2
// gives it a popover instead, reusing the old `.wfb-trigger-card` form
// verbatim -- see `_wfTriggerPopoverHTML`), and `def.trigger.type` is
// unchanged by any of this. This box is a positioned, draggable marker for it
// on the free canvas -- `trigger.x`/`trigger.y` are additive keys on the same
// dict (verified round-tripping through mc/workflows.py: `doc.get('trigger')`
// is stored and returned verbatim, no key allowlist).
//
// NO input port (nothing can feed a trigger, unchanged). Change 4a REVERSES
// the "no output port either" call this comment used to make (Ron, after
// using the canvas: "There is no connection point on the Start tile so it
// cannot be tied to the first agent or action"). The port IS there now, but
// as a VIEW, not new persisted state: `mc/workflows.py:421` refuses any edge
// whose `from` isn't a real `nodes[]` member, and `:656` already defines
// "wired to the trigger" as being a ROOT (no incoming edges) -- so the port
// drives `_wfInsertRootAfterTrigger`/`_wfMakeRoot`, which only ever touch
// node membership and incoming-edge lists, and `_wfRedrawEdges` draws the
// implied trigger->root line as a read of "no incoming edges", never a
// stored edge. `_wfToposort`/`_wfDominators` still never learn the trigger is
// a node -- nothing about that changed.
// Above the root node, not to its left: a root's `x` is wherever the user
// actually dropped it (usually near the canvas's default viewport origin),
// so subtracting a further fixed offset from that can walk the trigger off
// the LEFT edge of the default {x:60,y:40,scale:1} viewport on the very
// first node placed -- exactly the case a brand-new workflow hits. Sitting
// above keeps its x anchored to a point already known to be visible.
function _wfTriggerDefaultPos(def) {
  const nodes = def.nodes || [];
  if (!nodes.length) return { x: 40, y: 40 };
  const edges = def.edges || [];
  const targets = new Set(edges.map(e => e.to));
  const root = nodes.find(n => !targets.has(n.name)) || nodes[0];
  return { x: root.x || 0, y: (root.y || 0) - 110 };
}

// MC-871 Change 4a (reverses the "no output port" call the file header used
// to document -- Ron, after using the shipped canvas: "There is no
// connection point on the Start tile so it cannot be tied to the first agent
// or action that should happen at the trigger"). The port is a VIEW, not new
// persisted state: `mc/workflows.py:421` refuses any edge whose `from` isn't
// a real `nodes[]` member, and `:656` already defines "wired to the trigger"
// as being a ROOT (no incoming edges) -- so this port doesn't add a
// `__trigger__` edge to `def.edges`, it drives `_wfMakeRoot`/
// `_wfInsertRootAfterTrigger`, which only ever touch node membership and
// incoming-edge lists. `_wfRedrawEdges` draws the implied trigger->root
// line(s) this creates as a view over "no incoming edges", never a stored
// edge. `data-when=""` matches a plain node's single unconditional port so
// `_wfPortPlusClick`/the popover pick functions need only one extra branch
// (`fromNode === '__trigger__'`), not a parallel code path.
//
// Change 12a (corrects an over-eager first cut of 4a): a trigger line used to
// be drawn to EVERY root node -- meaning any freshly dropped standalone card
// (which starts with no incoming edges, ie. a root by definition) instantly
// looked wired to the trigger, with no action from Ron. Wiring to the
// trigger is now an EXPLICIT act, same as any other connection, recorded as
// `def.trigger.entry` -- a plain array of node names -- alongside the
// trigger's already-additive `x`/`y` keys (verified: `create_workflow`/
// `update_workflow` store `doc.get('trigger')` WHOLE, and `validate_workflow`
// checks only `.type` -- mc/workflows.py:235/355-357 -- the same reason
// `trigger.x`/`trigger.y` already round-trip with no schema change). Only
// `_wfInsertRootAfterTrigger` (the trigger's own "+"/drop target) and
// `_wfMakeRoot` (dragging FROM the trigger port onto a card) add to `entry`
// -- both are gestures that touch the trigger tile directly. A plain drop on
// EMPTY canvas never does, even though the result is technically a root too.
function _wfTriggerEntry(def) {
  const trigger = def.trigger;
  return (trigger && Array.isArray(trigger.entry)) ? trigger.entry : [];
}

function _wfRenderTriggerBox(st) {
  const def = st.def;
  const trigger = def.trigger || (def.trigger = { type: 'manual' });
  const hasPos = typeof trigger.x === 'number' && typeof trigger.y === 'number';
  // Ron: "dropping a block beside Start snaps it to connect to the top of
  // the dropped tile" / "after connecting Start to a tile, it snaps to the
  // top of that tile" -- both were this same recompute, not a real snap: as
  // long as the trigger has never been dragged, EVERY render fell through to
  // _wfTriggerDefaultPos, which anchors above whichever node is currently the
  // graph's root -- so any new node, or any node picking up an incoming edge
  // that demotes it from root, silently re-homed the tile on the next
  // _wfRender(). Persisting the computed default into `trigger.x/y` the first
  // time it's needed makes `hasPos` true from then on, so this box behaves
  // exactly like a node that already has a saved position: it only ever
  // moves in response to _wfNodeDragUp's own drag-and-release.
  if (!hasPos) { const pos0 = _wfTriggerDefaultPos(def); trigger.x = pos0.x; trigger.y = pos0.y; }
  const pos = { x: trigger.x, y: trigger.y };
  // Defect 11's follow-on ("Manual -- Run now" vs "Runs weekly - Mon 07:00"):
  // the tile reads its OWN live state instead of a static "Manual"/"On a
  // schedule" label, which also gives defect 8's affordance something to
  // read -- a tile whose text changes when you configure it looks
  // interactive even before the hover/caret styling lands. Reuses
  // scheduler.js's own `scheduleDescription` (window export) rather than
  // reimplementing cadence formatting a second time; falls back to the old
  // generic wording if the linked schedule hasn't loaded yet.
  const label = trigger.type === 'schedule'
    ? (st.linkedSchedule && typeof window.scheduleDescription === 'function' ? window.scheduleDescription(st.linkedSchedule) : 'On a schedule')
    : 'Manual — Run now';
  const names = new Set((def.nodes || []).map(n => n.name));
  const hasIncoming = new Set((def.edges || []).map(e => e.to));
  // "Connected" (a solid line, no stop stub) only for entries that are BOTH
  // still real nodes AND still roots -- an entry that later gained an
  // incoming edge from elsewhere is no longer "ready the moment the run
  // starts" as a root, so drawing it as trigger-wired here would be exactly
  // the silent lie R2-D6's stop-stub convention exists to prevent.
  const wiredCount = _wfTriggerEntry(def).filter(n => names.has(n) && !hasIncoming.has(n)).length;
  // Defect 8: clicking "Trigger" opens the config popover (_wfNodeDragUp's
  // no-drag-happened branch) but nothing said so -- a caret is the same
  // affordance `.wfb-toolbar-desc-toggle` already uses for "this text opens
  // something", so the tile reads as configurable without a second visual
  // language of its own.
  return `<div class="wfb-trigger-box" data-name="__trigger__" style="left:${pos.x}px;top:${pos.y}px" title="Click to change the trigger &middot; drag to move">
    <div class="wfb-trigger-box-head" onpointerdown="_wfNodeDragDown(event)">
      <span class="wfb-trigger-box-icon">&#9654;</span>
      <span class="wfb-trigger-box-title">Trigger</span>
      <span class="wfb-trigger-box-caret">&#9662;</span>
    </div>
    <div class="wfb-trigger-box-sub">${esc(label)}</div>
    <div class="wfb-port-row wfb-trigger-port-row${wiredCount ? '' : ' wfb-port-unconnected'}">
      <span class="wfb-port wfb-port-out" data-node="__trigger__" data-when="" onpointerdown="_wfPortDown(event)"><span class="wfb-port-dot"></span></span>
      <button type="button" class="wfb-port-plus" title="First step, run&hellip;"
        onclick="_wfPortPlusClick(event,'__trigger__','')">&#43;</button>
      ${wiredCount ? '' : '<span class="wfb-port-stub"></span>'}
    </div>
  </div>`;
}

// MC-871 Change 1 (+ Ron's amendment: "the canvas is the dominant element").
// The old stacked chrome (.wfb-meta name/description/enabled, .wfb-trigger-card
// TRIGGER radios+cadence, and a bottom .wfb-actions row) spent ~500px of
// vertical space before the canvas even started and left it a fixed 520px
// stub below. All of it collapses into ONE toolbar row above the canvas:
// name (inline, borderless), a description disclosure (open only when the
// loaded def already has one -- never hide existing content), an Enabled
// pill, undo/redo/reset (Change 3), and Save/Run now/the save-stamp moved up
// from the old .wfb-actions. The TRIGGER radios + cadence form move OUT
// entirely -- Change 2 reuses them verbatim inside a popover anchored to the
// trigger tile on the canvas (_wfTriggerPopoverHTML), not rendered here.
function _wfRenderBody(st) {
  const def = st.def;
  st._cardSeq = 0;
  st._charLoads = [];
  const nodes = def.nodes || [];
  const nodesHtml = nodes.map(n => _wfRenderNode(st, n)).join('');
  const descOpen = !!st.descOpen;
  return `
    <div class="wfb-toolbar">
      <input id="wfb-name" class="wfb-toolbar-name" value="${esc(def.name || '')}" placeholder="Untitled workflow">
      <button type="button" class="wfb-toolbar-desc-toggle${descOpen ? ' active' : ''}" onclick="_wfToggleDesc()"
        title="${descOpen ? 'Hide the description' : 'Add a description'}">${descOpen ? '&#9662;' : '&#65291;'} Description</button>
      <label class="wfb-toolbar-enabled" title="Enabled">
        <span class="schedule-toggle ${def.enabled !== false ? 'on' : ''}" onclick="_wfToggleEnabled()"></span>
        <span>Enabled</span>
      </label>
      <span class="wfb-toolbar-spacer"></span>
      <button type="button" class="wfb-toolbar-btn" title="Undo (Ctrl+Z)" onclick="_wfUndo()" ${_wfCanUndo(st) ? '' : 'disabled'}>&#8630; Undo</button>
      <button type="button" class="wfb-toolbar-btn" title="Redo (Ctrl+Shift+Z)" onclick="_wfRedo()" ${_wfCanRedo(st) ? '' : 'disabled'}>&#8631; Redo</button>
      <button type="button" class="wfb-toolbar-btn" title="Revert to the last saved state" onclick="_wfResetCanvas()">&#8635; Reset</button>
      <button class="btn-sched-save" onclick="_wfSave()" ${st.saving ? 'disabled' : ''}>${st.saving ? 'Saving…' : (st.workflowId ? 'Update' : 'Create')}</button>
      <button class="btn-sched-cancel" style="color:var(--accent);border-color:var(--accent)" onclick="_wfRunNow()"
        title="${st.workflowId ? 'Validate and run this workflow now' : 'Save the workflow first'}">&#x25B6; Run now</button>
      <span id="wfb-save-stamp" class="wfb-save-stamp${st.dirty ? ' wfb-save-stamp-dirty' : ''}">${st.dirty ? 'Unsaved changes' : esc(_wfRelativeSavedLabel(st.savedAt))}</span>
    </div>
    ${descOpen ? `<div class="wfb-toolbar-desc-row">
      <textarea id="wfb-desc" rows="1" placeholder="What this pipeline is for">${esc(def.description || '')}</textarea>
    </div>` : ''}
    <div class="wfb-builder">
      <div class="wfb-palette" id="wfb-palette">${_wfRenderPalette(st)}</div>
      <div id="wfb-palette-popover" class="wfb-palette-popover hidden"></div>
      <div id="wfb-canvas-viewport" class="wfb-canvas-viewport" onpointerdown="_wfViewportDown(event)">
        <svg id="wfb-canvas-svg" class="wfb-canvas-svg"></svg>
        <div id="wfb-world" class="wfb-canvas-world">${_wfRenderTriggerBox(st)}${nodesHtml}</div>
        ${nodes.length ? '' : '<div class="wfb-canvas-empty">drop anyone anywhere &middot; drag the blue dot onto another card to connect them &middot; + on a port adds &amp; wires the next step</div>'}
      </div>
    </div>
    ${st.error ? `<div class="wfb-error">${esc(st.error)}</div>` : ''}`;
}

// The palette is the Bench plus the ONE tool that is a user intention rather
// than Clayrune housekeeping (UI brief §2, narrowed by Change 10 — see the
// palette-tools comment below). People are listed first because they are the
// common case; the tool sits under a rule so the eye lands on a face, not on
// a primitive.
//
// Cap raised 8 -> 12 (Change 9): the canvas fill (Change 1's amendment) gives
// `.wfb-palette` real height to grow into now that it's not squeezed against
// a fixed 520px viewport, so a taller visible list before anyone needs "+ N
// more" costs nothing and the column's own overflow-y:auto still catches an
// exceptionally long bench.
const WFB_PALETTE_PEOPLE_CAP = 12;

function _wfRenderPalette(st) {
  const bench = _wfBenchFiltered(st, st.paletteSearch);
  // A search already narrows the list, so it shows every match regardless of
  // expanded state (Change 9's explicit "confirm a search still shows every
  // match"); otherwise the cap applies until "+ N more" is clicked.
  const showAll = !!st.paletteSearch || !!st.paletteExpanded;
  const shown = showAll ? bench : bench.slice(0, WFB_PALETTE_PEOPLE_CAP);
  const hidden = bench.length - shown.length;
  const rows = shown.map(b => `<div class="wfb-palette-person"
      onpointerdown="_wfPaletteDown(event,'person','${_wfJsStrEsc(b.scope || 'global')}','${_wfJsStrEsc(b.name)}')"
      onmouseenter="_wfPalettePersonHover(event,'${_wfJsStrEsc(b.scope || 'global')}','${_wfJsStrEsc(b.name)}')"
      onmouseleave="_wfPalettePersonUnhover()">
      <span class="wfb-palette-avatar">${_wfAvatarHTML(b, 28)}</span>
      <span class="wfb-palette-person-info">
        <span class="wfb-palette-person-name">${esc(b.display || b.name)}</span>
        <span class="wfb-palette-person-role">${esc(b.name)}</span>
      </span>
    </div>`).join('');
  const empty = st.benchLoaded
    ? (st.paletteSearch ? 'No one matches.' : 'You have not hired anyone yet.')
    : 'Loading the bench&hellip;';
  // Change 9: "+ N more" and "Hire someone new" are two separate controls now
  // — the old single button silently only ever offered the hire flow, so the
  // capped-off people were unreachable by any path. "+ N more" only shows the
  // rest of the ALREADY-HIRED bench; it never opens the hire dialog.
  let moreBtn = '';
  if (!st.paletteSearch && hidden > 0) {
    moreBtn = `<button type="button" class="wfb-palette-more" onclick="_wfPaletteToggleExpand()">+ ${hidden} more</button>`;
  } else if (!st.paletteSearch && st.paletteExpanded && bench.length > WFB_PALETTE_PEOPLE_CAP) {
    moreBtn = `<button type="button" class="wfb-palette-more" onclick="_wfPaletteToggleExpand()">Show fewer</button>`;
  }
  return `
    <div class="wfb-palette-title">People &middot; drag onto canvas</div>
    <input class="wfb-palette-search" id="wfb-palette-search" placeholder="Search bench&hellip;"
      value="${esc(st.paletteSearch || '')}" oninput="_wfPaletteSearch(this.value)">
    <div class="wfb-palette-people">${rows || `<div class="wfb-palette-empty">${empty}</div>`}</div>
    ${moreBtn}
    <button type="button" class="wfb-palette-more" onclick="_wfHireSomeone()">Hire someone new</button>
    <div class="wfb-palette-divider"></div>
    <div class="wfb-palette-tools-title">Tools</div>
    <div class="wfb-palette-block" onpointerdown="_wfPaletteDown(event,'approval')">
      <span class="wfb-palette-icon">&#9995;</span>
      <span class="wfb-palette-block-info"><span>Approval gate</span>
        <span class="wfb-palette-block-sub">a human decides</span></span>
    </div>
    <div class="wfb-palette-block" onpointerdown="_wfPaletteDown(event,'action')">
      <span class="wfb-palette-icon">&#9881;</span>
      <span class="wfb-palette-block-info"><span>Action</span>
        <span class="wfb-palette-block-sub">${Object.keys(_WF_ACTION_META).length} verbs &middot; no agent</span></span>
    </div>
    <div class="wfb-palette-hint">Drop a person anywhere on the canvas, or onto a card to run after it &middot; drag the blue dot onto another card to connect them &middot; every port's + adds and wires the next step.</div>`;
}

// ── Palette person popup — description + engine (MC-871 follow-up) ─────────
//
// Ron: the palette popup's description was good, but a workflow spends real
// money per unattended run, so the engine has to be visible BEFORE dragging
// someone onto the canvas, not just after. A native `title` attribute can't
// be styled (no theme, no max-width, so a long description ran the full
// width of the screen) — this replaces it with a small fixed popover, same
// convention as the Hivemind worker popover (conversation.js
// showHmWorkerPopover/scheduleHideHmPopover): one shared DOM node, positioned
// off the hovered row, clamped to the viewport so it can never overflow or
// get clipped by the canvas.
let _wfPalettePopoverHideTimer = null;

function _wfPalettePersonHover(event, scope, name) {
  if (_wfPalettePopoverHideTimer) { clearTimeout(_wfPalettePopoverHideTimer); _wfPalettePopoverHideTimer = null; }
  const entry = _wfEntry();
  const st = entry && entry._wf;
  const pop = document.getElementById('wfb-palette-popover');
  const row = event.currentTarget;
  if (!st || !pop || !row) return;
  const b = _wfBenchLookup(st, scope, name);
  if (!b) return;

  pop.innerHTML = `<div class="wfb-palette-popover-desc">${esc(b.description || b.display || b.name)}</div>
    <div class="wfb-palette-popover-engine">${esc(_wfPaletteEngineLine(st, b))}</div>`;
  pop.classList.remove('hidden');

  // Measure AFTER content + max-width are applied, then clamp to the
  // viewport — the same overflow the bounded CSS width fixes for long text
  // also has to be fixed for placement, or a row near an edge would still
  // push the popover off-screen.
  const rect = row.getBoundingClientRect();
  const popRect = pop.getBoundingClientRect();
  let left = rect.right + 8;
  if (left + popRect.width > window.innerWidth - 8) left = rect.left - popRect.width - 8;
  left = Math.max(8, Math.min(left, window.innerWidth - popRect.width - 8));
  let top = rect.top;
  top = Math.max(8, Math.min(top, window.innerHeight - popRect.height - 8));
  pop.style.left = left + 'px';
  pop.style.top = top + 'px';
}

function _wfPalettePersonUnhover() {
  _wfPalettePopoverHideTimer = setTimeout(_wfHidePalettePopover, 120);
}

function _wfHidePalettePopover() {
  const pop = document.getElementById('wfb-palette-popover');
  if (pop) pop.classList.add('hidden');
}
window._wfPalettePersonHover = _wfPalettePersonHover;
window._wfPalettePersonUnhover = _wfPalettePersonUnhover;

// Re-renders ONLY the palette: a keystroke in the search box must not rebuild
// the canvas (that would blow away an unsynced prompt the user is typing in a
// card, and reset the viewport mid-search).
function _wfPaletteSearch(value) {
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  entry._wf.paletteSearch = value;
  const box = document.getElementById('wfb-palette');
  if (!box) return;
  box.innerHTML = _wfRenderPalette(entry._wf);
  const input = document.getElementById('wfb-palette-search');
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
}

// Change 9: toggles the palette between the capped and full bench list. Same
// palette-only re-render as the search box above -- must not touch the canvas.
function _wfPaletteToggleExpand() {
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  entry._wf.paletteExpanded = !entry._wf.paletteExpanded;
  const box = document.getElementById('wfb-palette');
  if (box) box.innerHTML = _wfRenderPalette(entry._wf);
}
window._wfPaletteToggleExpand = _wfPaletteToggleExpand;

// Claydo's character mode, the one creation flow (floor.js `floorHire`) — not
// a second one. Guarded the same way: it is a cross-module global.
function _wfHireSomeone() {
  if (typeof window.floorHire === 'function') window.floorHire();
}

// Icon shown in the header of a non-agent card (UI brief mockup: a small
// glyph + the type in dimmed caps). Agent cards lead with the face instead
// (below) so they carry no icon of their own.
function _wfTypeIcon(t) {
  return { approval: '&#9995;', action: '&#9881;' }[t] || '';
}

function _wfRenderNode(st, node) {
  const nameAttr = esc(node.name || '');
  let own = '';
  if (node.type === 'agent') own = _wfRenderAgentOwn(st, node);
  else if (node.type === 'approval') own = _wfRenderApprovalOwn(st, node);
  else if (node.type === 'action') own = _wfRenderActionOwn(st, node);
  else own = 'Unknown node type.';
  const edges = st.def.edges || [];
  // A declared vocabulary (agent outcomes / approval options) renders its
  // ports as pills INSIDE the card body (see _wfRenderVocabRows, called from
  // _wfRenderAgentOwn/_wfRenderApprovalOwn) -- there is nothing left for the
  // card-edge ports column to draw. Only the plain, single, unconditional
  // port (a no-outcome agent step or any action node -- action steps cannot
  // have conditional edges, validate_workflow) still uses it.
  const vocabPresent = _wfVocab(node).length > 0;
  const portsHtml = vocabPresent ? '' : (() => {
    const connected = edges.some(e => e.from === node.name && (e.when || null) === null);
    return `<div class="wfb-port-row${connected ? '' : ' wfb-port-unconnected'}">
      <span class="wfb-port wfb-port-out" data-node="${nameAttr}" data-when="" onpointerdown="_wfPortDown(event)"><span class="wfb-port-dot"></span></span>
      <button type="button" class="wfb-port-plus" title="After this step, run&hellip;"
        onclick="_wfPortPlusClick(event,'${_wfJsStrEsc(node.name)}','')">&#43;</button>
      ${connected ? '' : '<span class="wfb-port-stub"></span>'}
    </div>`;
  })();
  // An agent card leads with WHO, not with a type label — the persona was
  // chosen by the drag itself (UI brief §3), so the face is the identity and
  // the step name is the subtitle it is referenced by in slots.
  const person = node.type === 'agent' ? _wfPersonFromCharacter(st, node.character) : null;
  // Engine tooltip: what this step will actually run on and what that costs
  // (Ron, 2026-09-11) -- native `title`, the same convention the Floor's
  // provider badges and face pickers already use (floor.js), not a new
  // hover-card system.
  const engineInfo = node.type === 'agent' ? _wfEngineResolution(st, node) : null;
  const engineTitle = engineInfo ? esc(engineInfo.text) : '';
  const headHtml = node.type === 'agent'
    ? `<span class="wfb-node-avatar" title="${engineTitle}">${_wfAvatarHTML(person, 22)}</span>
       <span class="wfb-node-title" title="${engineTitle}">
         <span class="wfb-node-persona">${esc(person ? (person.display || person.name) : 'No persona yet')}</span>
         <span class="wfb-node-step-sep">&middot;</span>
         <span class="wfb-node-step-name">${esc(node.name || '')}</span>
       </span>`
    : `<span class="wfb-node-type-icon">${_wfTypeIcon(node.type)}</span><span class="wfb-node-type">${_wfTypeLabel(node.type)}</span>`;
  // Step 6: Run-now (and a failed Save) surface INLINE on the offending card,
  // not only as a toast (brief §7) -- st.runErrors is a {nodeName: message}
  // map a validate attempt populates; _wfRender's caller pans the canvas to
  // st.scrollToNode so the red-outlined card is the one already in view.
  const runError = st.runErrors && st.runErrors[node.name];
  // MC-871 follow-up: the engine warning is NOT gated behind a Save/Run-now
  // attempt -- the whole point is to warn while the user is still picking
  // the engine, not only after they try to ship it. Suppressed whenever a
  // hard error is already showing -- "one banner per card", the same rule
  // _wfApplyRunErrors documents for structural problems.
  const authWarning = (!runError && node.type === 'agent')
    ? _wfEngineWarning(st, node, engineInfo) : '';
  // Change 12a's honesty half: a node with no incoming edges is a ROOT, and
  // mc/workflows.py:656 starts every root "the moment the run starts",
  // REGARDLESS of whether it's in `def.trigger.entry` -- the frontend-only
  // fix stops auto-wiring the CANVAS, it cannot stop the RUNNER (a backend
  // change, out of scope here, reported rather than papered over). So a root
  // Ron did NOT explicitly wire still runs; the canvas must say so rather
  // than implying it's inert because no line reaches it.
  const isRoot = !edges.some(e => e.to === node.name);
  const isUnwiredRoot = isRoot && !_wfTriggerEntry(st.def).includes(node.name);
  const unwiredBadge = isUnwiredRoot
    ? `<span class="wfb-node-unwired-badge" title="No incoming edges, so this runs automatically as soon as the workflow starts — even though it isn't wired to the Trigger tile.">&#9888;</span>`
    : '';
  const nodeStateCls = runError ? ' wfb-node-error' : (authWarning ? ' wfb-node-warning' : '');
  return `<div class="wfb-node${nodeStateCls}" data-name="${nameAttr}" style="left:${node.x || 0}px;top:${node.y || 0}px">
    <div class="wfb-node-head" onpointerdown="_wfNodeDragDown(event)">
      ${unwiredBadge}
      ${headHtml}
      <button class="wfb-node-menu-btn" title="Step options" onclick="_wfNodeMenuToggle(event,'${_wfJsStrEsc(node.name)}')">&#8230;</button>
    </div>
    <div class="wfb-node-own">
      ${runError ? `<div class="wfb-node-inline-error">${esc(runError)}</div>` : ''}
      ${authWarning ? `<div class="wfb-node-inline-warning">${esc(authWarning)}</div>` : ''}
      ${own}
    </div>
    <span class="wfb-port wfb-port-in" data-node="${nameAttr}"><span class="wfb-port-dot"></span></span>
    ${vocabPresent ? '' : `<div class="wfb-ports-out">${portsHtml}</div>`}
  </div>`;
}

// Verbatim from the Phase 2 spine (static/js/workflow-builder.js pre-canvas):
// project select, persona picker with its face + caching, prompt textarea.
// Only the branching editor below the prompt changed (a flat `outcomes`
// array instead of nested branch lists, since branching is now edge `when`
// labels, not a child node — R2-D6).
// Outcome/option rows AND their ports, unified into one card-body section
// (UI brief mockup: each outcome is a pill carrying its own port dot on the
// card's right edge, a dashed "+ outcome" pill last, "otherwise" beneath in
// dimmed italic with a hollow port). Shared by the agent/approval editors
// below since both are just _wfVocab(node) with a different field name and
// mutator pair.
function _wfRenderVocabRows(st, node, kind) {
  const vocab = kind === 'outcome' ? (node.outcomes || []) : (node.options || []);
  const edges = st.def.edges || [];
  const nameAttr = esc(node.name || '');
  const removeFn = kind === 'outcome' ? '_wfRemoveOutcome' : '_wfRemoveOption';
  const addFn = kind === 'outcome' ? '_wfAddOutcome' : '_wfAddOption';
  const inputCls = kind === 'outcome' ? 'wfb-outcome-input' : 'wfb-option-input';
  const rows = vocab.map((label, oi) => {
    const connected = edges.some(e => e.from === node.name && (e.when || null) === label);
    return `<div class="wfb-vocab-row">
      <input class="${inputCls} wfb-vocab-pill" value="${esc(label)}">
      <button class="wfb-branch-del" title="Remove ${kind}" onclick="${removeFn}('${_wfJsStrEsc(node.name)}',${oi})">&#10005;</button>
      <button type="button" class="wfb-port-plus" title="After &ldquo;${esc(label)}&rdquo;, run&hellip;"
        onclick="_wfPortPlusClick(event,'${_wfJsStrEsc(node.name)}','${_wfJsStrEsc(label)}')">&#43;</button>
      <span class="wfb-port wfb-port-out" data-node="${nameAttr}" data-when="${esc(label)}" onpointerdown="_wfPortDown(event)"><span class="wfb-port-dot"></span></span>
      ${connected ? '' : '<span class="wfb-port-stub"></span>'}
    </div>`;
  }).join('');
  const otherwiseRow = vocab.length ? (() => {
    const otherConnected = edges.some(e => e.from === node.name && (e.when || null) === 'otherwise');
    return `<div class="wfb-otherwise-row">
      <em>otherwise</em>
      <span class="wfb-port wfb-port-out wfb-port-otherwise" data-node="${nameAttr}" data-when="otherwise" onpointerdown="_wfPortDown(event)"><span class="wfb-port-dot"></span></span>
      ${otherConnected ? '' : '<span class="wfb-port-stub"></span>'}
    </div>`;
  })() : '';
  return `${rows}<button class="wfb-add-btn wfb-vocab-add" onclick="${addFn}('${_wfJsStrEsc(node.name)}')">+ ${kind}</button>${otherwiseRow}`;
}

// MC-871 agent-card pass (Ron: "why show [the prompt] in the first place?").
// A card on a multi-step canvas is competing for attention against every
// other card; an always-visible, dimmed prompt textarea reads as a wall of
// grey text ahead of the thing the card is actually for -- who runs, on
// what, wired to what. Collapsed by default behind _wfTogglePromptOpen's
// disclosure, with two cases that force it open regardless of the user's own
// toggle -- both are "an error is live right now", never a soft hint:
//   - an EMPTY prompt: _wfValidateGraph flags this as an error the instant
//     Save/Run-now is tried ("Needs a prompt.") -- collapsing it behind a
//     "no prompt yet" label would still make Ron open a panel to act on it,
//     so it opens straight to the empty textarea instead (brief: "do not
//     hide that behind a collapsed panel where he cannot see it").
//   - a live broken slot reference (_wfTextHasBrokenSlotRef, independent of
//     any Save attempt -- a rename or edge change can break a reference the
//     instant it happens): forced open since the broken chip that explains
//     it lives inside the panel (_wfSlotChipsHTML).
// st.promptOpen (see _wfFreshState) is the user's own toggle and survives
// _wfRender()'s full innerHTML rebuild because it lives on session state,
// not the DOM. A force-open ALSO writes st.promptOpen (a render-time side
// effect the same shape as _wfRenderAgentOwn's own st._charLoads.push --
// this file already lets a render helper queue/record state, not just
// derive markup): otherwise, the instant the error clears -- the user types
// the missing prompt, or fixes the reference -- the very next unrelated
// re-render (placing a second block, connecting an edge) would find
// forceOpen suddenly false and userOpen still unset, and snap the panel shut
// mid-edit. Once opened, for any reason, it stays open until the user
// explicitly collapses it again.
function _wfPromptForceOpen(st, node) {
  const prompt = node.prompt || '';
  return !prompt.trim() || _wfTextHasBrokenSlotRef(st.def, node.name, prompt);
}

function _wfRenderPromptField(st, node) {
  const prompt = node.prompt || '';
  const forceOpen = _wfPromptForceOpen(st, node);
  st.promptOpen = st.promptOpen || {};
  if (forceOpen) st.promptOpen[node.name] = true;
  const open = !!st.promptOpen[node.name];
  const label = `<label>Prompt <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(<code>{{steps.NAME.output}}</code> / <code>{{prev.output}}</code> pull an earlier step's result forward)</span></label>`;
  if (open) {
    // No collapse control while an active error is forcing this open --
    // collapsing would just hide the thing the user needs to fix, and the
    // panel would silently re-force itself open again next render anyway
    // (still broken == still forced).
    const toggleBtn = forceOpen ? '' : `<button type="button" class="wfb-prompt-toggle"
        onclick="_wfTogglePromptOpen(event,'${_wfJsStrEsc(node.name)}')" title="Hide the prompt">&#9662; Hide prompt</button>`;
    return `${label}
    <div class="wfb-prompt-inset">
      <textarea class="wfb-prompt" rows="3" placeholder="What should this step do?">${esc(prompt)}</textarea>
      ${_wfInsertControlHTML(st.def, node.name, '.wfb-prompt')}
      ${_wfSlotChipsHTML(st.def, node.name, prompt)}
      ${toggleBtn}
    </div>`;
  }
  const summaryText = prompt.trim().replace(/\s+/g, ' ');
  return `${label}
    <div class="wfb-prompt-collapsed">
      <span class="wfb-prompt-summary" title="${esc(summaryText)}">${esc(summaryText)}</span>
      <button type="button" class="wfb-prompt-toggle"
        onclick="_wfTogglePromptOpen(event,'${_wfJsStrEsc(node.name)}')" title="Show the prompt">&#9656; Show prompt</button>
    </div>`;
}

// "Sync first" (same discipline as _wfToggleDesc/_wfToggleEnabled): capture
// any unsynced typing anywhere on the canvas -- including this card's own
// prompt, which only exists in the DOM while its panel is open -- before the
// re-render replaces it. The "Hide prompt" button is only ever rendered when
// !forceOpen (_wfRenderPromptField), so a plain flip is enough here -- if the
// sync above just revealed an empty/broken prompt, the render this triggers
// re-forces it open regardless of what we flip it to.
function _wfTogglePromptOpen(e, nodeName) {
  if (e) e.preventDefault();
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  nodeName = renameMap[nodeName] || nodeName;
  const st = entry._wf;
  st.promptOpen = st.promptOpen || {};
  st.promptOpen[nodeName] = !st.promptOpen[nodeName];
  _wfRender();
}
window._wfTogglePromptOpen = _wfTogglePromptOpen;

function _wfRenderAgentOwn(st, node) {
  const seq = ++st._cardSeq;
  const projects = (typeof allProjects !== 'undefined' ? allProjects : []).filter(p => p.project_path);
  const pid = node.project_id || (projects[0] && projects[0].id) || '';
  st._charLoads.push({ seq, want: node.character || '' });
  return `
    <label>Name <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(referenced as <code>{{steps.NAME.output}}</code>)</span></label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="step-name">
    <div class="wfb-project-row">
      <span class="wfb-project-icon" aria-hidden="true">&#128193;</span>
      <select id="wfb-proj-${seq}" class="wfb-project" onchange="_wfReloadCharacters(${seq})">
        ${projects.map(p => `<option value="${esc(p.id)}"${p.id === pid ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}
      </select>
    </div>
    <label>Agent</label>
    <div class="sched-agent-row">
      <span id="wfb-face-${seq}" class="sched-agent-face"></span>
      <select id="wfb-persona-${seq}" class="wfb-persona"><option value="">Loading…</option></select>
    </div>
    ${_wfRenderPromptField(st, node)}
    <div class="wfb-branch-labels">
      <label>Outcomes <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(this step must end its reply naming one &mdash; each gets its own port below to wire up)</span></label>
      ${_wfRenderVocabRows(st, node, 'outcome')}
    </div>`;
}

function _wfRenderApprovalOwn(st, node) {
  return `
    <label>Name</label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="approval-name">
    <div class="wfb-branch-labels">
      <label>Options <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(what a human can choose &mdash; delivered over the question channel; each gets its own port)</span></label>
      ${_wfRenderVocabRows(st, node, 'option')}
    </div>`;
}

// Plain-English labels over the internal ACTION_ALLOWLIST identifiers (Ron,
// looking at the raw verbs: "this is unintuitive, I don't think anyone will
// understand these actions"). Stored values / mc/workflows.py are untouched —
// this is a label/description layer only, kept in ONE lookup.
const _WF_ACTION_META = {
  backlog_create: { label: 'Add a backlog item', desc: 'Adds a new item to the project backlog. No agent involved.', group: 'Backlog' },
  backlog_patch:  { label: 'Update a backlog item', desc: "Changes an existing backlog item's status or text. No agent involved.", group: 'Backlog' },
  desk_harvest:   { label: 'Run a Desk harvest', desc: 'Scans signal sources. No agent involved.', group: 'The Desk' },
  journal_append: { label: 'Log to a backlog item\'s journal', desc: 'Appends a dated note to that item\'s journal file on disk. This is the unattended-safe log — it never writes a backlog note. No agent involved.', group: 'Backlog' },
  notify_operator: { label: 'Email me when this runs', desc: "Sends the operator an email with your message. The recipient is fixed by server config — there is no address field here, so this can't be pointed anywhere else. No agent involved.", group: 'Notify' },
  restore_point_create: { label: 'Snapshot this project', desc: 'Creates a restore point (a reversible backup) of the project before the next steps run. No agent involved.', group: 'Backup' },
};
const _WF_ACTION_GROUP_ORDER = ['Backlog', 'The Desk', 'Notify', 'Backup'];

function _wfActionSelectHTML(action) {
  const groups = {};
  Object.keys(_WF_ACTION_META).forEach(id => {
    const g = _WF_ACTION_META[id].group;
    (groups[g] = groups[g] || []).push(id);
  });
  // Never render a heading whose group is empty -- matters once R3-1's verbs
  // start landing in the lookup one at a time.
  const order = _WF_ACTION_GROUP_ORDER.filter(g => groups[g] && groups[g].length);
  return `<select class="wfb-action-select" onchange="_wfRerenderActionFields(this)">
    ${order.map(g => `<optgroup label="${esc(g)}">
      ${groups[g].map(id => `<option value="${id}"${id === action ? ' selected' : ''}>${esc(_WF_ACTION_META[id].label)}</option>`).join('')}
    </optgroup>`).join('')}
  </select>`;
}

function _wfRenderActionOwn(st, node) {
  const action = node.action || 'backlog_create';
  const meta = _WF_ACTION_META[action] || {};
  return `
    <label>Name</label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="action-name">
    <label>Action</label>
    ${_wfActionSelectHTML(action)}
    <div class="wfb-action-desc">${esc(meta.desc || '')}</div>
    <div class="wfb-action-id" title="The stored identifier -- validation errors refer to this">${esc(action)}</div>
    <div class="wfb-action-fields">${_wfActionFieldsHTML(action, node.config || {}, st.def, node.name)}</div>`;
}

// def/nodeName (step 5b): "anywhere a slot is legal, e.g. the backlog
// action's item-id field, offer an Insert control" (brief) -- every
// data-cfg-key text input/textarea below gets its own Insert dropdown +
// chip legend, keyed to that one field by its own `[data-cfg-key="…"]`
// selector so each field's insert lands in the right place.
function _wfActionFieldsHTML(action, cfg, def, nodeName) {
  const projects = (typeof allProjects !== 'undefined' ? allProjects : []).filter(p => p.project_path);
  const projOpts = (includeAny) => (includeAny ? '<option value="">(any project)</option>' : '')
    + projects.map(p => `<option value="${esc(p.id)}"${p.id === cfg.project_id ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
  const slotField = (selector, text) => def && nodeName
    ? `${_wfInsertControlHTML(def, nodeName, selector)}${_wfSlotChipsHTML(def, nodeName, text)}` : '';
  if (action === 'backlog_patch') {
    return `
      <label>Project</label>
      <select data-cfg-key="project_id" data-cfg-required="1">${projOpts(false)}</select>
      <label>Backlog item ID <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(can be a slot, e.g. <code>{{steps.triage.result.item_id}}</code>)</span></label>
      <input data-cfg-key="item_id" data-cfg-required="1" value="${esc(cfg.item_id || '')}">
      ${slotField('[data-cfg-key="item_id"]', cfg.item_id || '')}
      <label>New status <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(blank = leave unchanged)</span></label>
      <select data-cfg-key="status">
        <option value=""${!cfg.status ? ' selected' : ''}>(unchanged)</option>
        ${['open', 'in_progress', 'blocked', 'done'].map(s => `<option value="${s}"${cfg.status === s ? ' selected' : ''}>${s}</option>`).join('')}
      </select>
      <label>New text <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(blank = leave unchanged)</span></label>
      <textarea data-cfg-key="text" rows="2">${esc(cfg.text || '')}</textarea>
      ${slotField('[data-cfg-key="text"]', cfg.text || '')}`;
  }
  if (action === 'desk_harvest') {
    return `
      <label>Project <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(blank = every project)</span></label>
      <select data-cfg-key="project_id">${projOpts(true)}</select>`;
  }
  if (action === 'journal_append') {
    return `
      <label>Backlog item ID <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(can be a slot, e.g. <code>{{steps.triage.result.item_id}}</code>)</span></label>
      <input data-cfg-key="item_id" data-cfg-required="1" value="${esc(cfg.item_id || '')}">
      ${slotField('[data-cfg-key="item_id"]', cfg.item_id || '')}
      <label>Title <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(only used to name the file the first time it's created)</span></label>
      <input data-cfg-key="title" value="${esc(cfg.title || '')}">
      <label>Entry text</label>
      <textarea data-cfg-key="text" data-cfg-required="1" rows="3" placeholder="What happened this run">${esc(cfg.text || '')}</textarea>
      ${slotField('[data-cfg-key="text"]', cfg.text || '')}`;
  }
  if (action === 'notify_operator') {
    return `
      <label>Message</label>
      <textarea data-cfg-key="message" data-cfg-required="1" rows="3" placeholder="What should the email say?">${esc(cfg.message || '')}</textarea>
      ${slotField('[data-cfg-key="message"]', cfg.message || '')}
      <div class="wfb-action-desc">Goes to the operator's fixed address only — there is no recipient field to fill in here.</div>`;
  }
  if (action === 'restore_point_create') {
    return `
      <label>Project</label>
      <select data-cfg-key="project_id" data-cfg-required="1">${projOpts(false)}</select>
      <label>Label <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(optional, shown in the restore-point list)</span></label>
      <input data-cfg-key="label" value="${esc(cfg.label || '')}">
      ${slotField('[data-cfg-key="label"]', cfg.label || '')}`;
  }
  // backlog_create (default)
  return `
    <label>Project</label>
    <select data-cfg-key="project_id" data-cfg-required="1">${projOpts(false)}</select>
    <label>Text</label>
    <textarea data-cfg-key="text" data-cfg-required="1" rows="2" placeholder="What the item says">${esc(cfg.text || '')}</textarea>
    ${slotField('[data-cfg-key="text"]', cfg.text || '')}
    <label>Priority</label>
    <select data-cfg-key="priority">
      ${['low', 'normal', 'high'].map(p => `<option value="${p}"${(cfg.priority || 'normal') === p ? ' selected' : ''}>${p}</option>`).join('')}
    </select>`;
}

function _wfRerenderActionFields(selectEl) {
  const own = selectEl.closest('.wfb-node-own');
  const box = own && own.querySelector('.wfb-action-fields');
  if (!box) return;
  const meta = _WF_ACTION_META[selectEl.value] || {};
  const descEl = own.querySelector('.wfb-action-desc');
  const idEl = own.querySelector('.wfb-action-id');
  if (descEl) descEl.textContent = meta.desc || '';
  if (idEl) idEl.textContent = selectEl.value;
  const entry = _wfEntry();
  const nodeEl = selectEl.closest('.wfb-node');
  const nodeName = nodeEl ? nodeEl.dataset.name : '';
  const def = entry && entry._wf ? entry._wf.def : null;
  box.innerHTML = _wfActionFieldsHTML(selectEl.value, {}, def, nodeName);
}

// ── Persona picker (mirrors scheduler.js's reloadSchedCharacters, per-node) ──

async function _wfCharactersFor(pid) {
  if (_wfCharCache.has(pid)) return _wfCharCache.get(pid);
  const res = await fetch(API_BASE + '/api/characters?project_id=' + encodeURIComponent(pid));
  if (!res.ok) throw new Error('characters ' + res.status);
  const list = await res.json();
  _wfCharCache.set(pid, list);
  return list;
}

function _wfProjectDefault(pid, list) {
  const p = (typeof allProjects !== 'undefined' ? allProjects : []).find(x => x.id === pid);
  const d = p && p.default_character;
  if (!d) return null;
  const [scope, name] = String(d).split(':');
  return list.find(c => c.scope === scope && c.name === name) || null;
}

function _wfPaintFace(seq, list) {
  const box = document.getElementById('wfb-face-' + seq);
  const sel = document.getElementById('wfb-persona-' + seq);
  if (!box || !sel) return;
  const pid = document.getElementById('wfb-proj-' + seq)?.value || '';
  let rec = null;
  if (list) {
    const v = sel.value;
    rec = v ? list.find(c => (c.scope + ':' + c.name) === v) : _wfProjectDefault(pid, list);
  }
  box.innerHTML = window.avatarHTML(rec && rec.avatar, 26);
}

async function _wfReloadCharacters(seq, want) {
  const sel = document.getElementById('wfb-persona-' + seq);
  const pid = document.getElementById('wfb-proj-' + seq)?.value || '';
  if (!sel || !pid) return;
  const target = (want === undefined) ? sel.value : (want || '');
  sel.innerHTML = '<option value="">Loading…</option>';
  let list;
  try {
    list = await _wfCharactersFor(pid);
  } catch (e) {
    sel.innerHTML = '<option value="">Project default (persona list unavailable)</option>';
    _wfPaintFace(seq, null);
    return;
  }
  const proj = list.filter(c => c.scope === 'project');
  const glob = list.filter(c => c.scope !== 'project' && !c.shadowed_by_project);
  const label = (c) => (c.agent_name ? `${c.agent_name} — ${c.display_name || c.name}` : (c.display_name || c.name));
  const opt = (c) => { const v = c.scope + ':' + c.name; return `<option value="${esc(v)}"${v === target ? ' selected' : ''}>${esc(label(c))}</option>`; };
  const dflt = _wfProjectDefault(pid, list);
  const group = (name, rows) => rows.length ? `<optgroup label="${esc(name)}">${rows.map(opt).join('')}</optgroup>` : '';
  sel.innerHTML =
    `<option value=""${target ? '' : ' selected'}>${esc(dflt ? `Project default — ${label(dflt)}` : 'Project default (no persona)')}</option>`
    + group('This project', proj) + group('Global', glob);
  if (target && sel.value !== target) sel.value = '';
  sel.onchange = () => _wfPaintFace(seq, _wfCharCache.get(pid));
  _wfPaintFace(seq, list);
}

// ── Structural mutations ──────────────────────────────────────────────────────

function _wfSetTriggerType(t) {
  const entry = _wfEntry(); if (!entry) return;
  _wfSyncDomToModel(entry);
  const st = entry._wf;
  // Keep x/y (MC-871 Change 2): they're the canvas box's dragged position,
  // unrelated to which radio is picked -- a fresh `{type: t}` here would snap
  // the box back to its default spot every time the trigger type changes.
  // Keep `entry` too (Ron: picking a trigger option deleted the connection to
  // the agent it was wired to) -- this rebuild used to keep only x/y and drop
  // every other key on `trigger`, silently un-wiring every node in
  // `trigger.entry` (Change 4a/12a's record of an explicit "wired to the
  // trigger" act) the moment the type changed.
  const { x, y, entry: wiredEntry } = st.def.trigger || {};
  const next = { type: t };
  if (typeof x === 'number' && typeof y === 'number') { next.x = x; next.y = y; }
  if (Array.isArray(wiredEntry)) next.entry = wiredEntry;
  st.def.trigger = next;
  if (t === 'schedule' && !st.linkedSchedule) st.linkedSchedule = _wfDraftSchedule();
  _wfMarkDirty();
  _wfRender();
}

// Toolbar toggle pill (Change 1) -- same "sync first" discipline every other
// structural mutator follows, so an unsynced edit elsewhere on the canvas
// isn't clobbered by this render.
function _wfToggleEnabled() {
  const entry = _wfEntry(); if (!entry) return;
  _wfSyncDomToModel(entry);
  const st = entry._wf;
  st.def.enabled = !(st.def.enabled !== false);
  _wfMarkDirty();
  _wfRender();
}
window._wfToggleEnabled = _wfToggleEnabled;

// Toolbar description disclosure (Change 1). Purely cosmetic -- not itself an
// undo-worthy graph change, and _wfCheckpointForUndo only diffs def/
// linkedSchedule, so toggling it never pushes a spurious undo entry.
function _wfToggleDesc() {
  const entry = _wfEntry(); if (!entry) return;
  _wfSyncDomToModel(entry); // capture any typed description before hiding the field
  entry._wf.descOpen = !entry._wf.descOpen;
  _wfRender();
}
window._wfToggleDesc = _wfToggleDesc;

// ── Trigger card: schedule cadence sub-form ──────────────────────────────────
// The cadence itself lives on the linked SCHEDULE record (spec Q4 -- "one
// store, two views"), never duplicated onto def.trigger. This mirrors
// scheduler.js's own type-fields form but under a distinct `wfb-sched-*`
// namespace so the two forms can never collide if both modals are open.

function _wfRenderScheduleCadence(st) {
  const s = st.linkedSchedule || _wfDraftSchedule();
  const missing = !!(st.def.trigger && st.def.trigger.type === 'schedule' && st.linkedSchedule === null && st.workflowId);
  return `
    <div class="wfb-sched-cadence">
      ${missing ? '<div class="memory-hint" style="margin:0 0 8px">No schedule found for this trigger yet &mdash; save to create one.</div>' : ''}
      <div class="sched-type-row">
        ${['daily', 'weekly', 'interval', 'once', 'cron'].map(t => `<button type="button" class="sched-type-btn${s.schedule_type === t ? ' active' : ''}" onclick="_wfSetSchedType('${t}')">${t[0].toUpperCase()}${t.slice(1)}</button>`).join('')}
      </div>
      <div id="wfb-sched-type-fields">${_wfSchedTypeFieldsHTML(s.schedule_type, s)}</div>
      ${st.linkedSchedule && st.linkedSchedule.id ? `
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal;margin-top:8px">
        <span class="schedule-toggle ${st.linkedSchedule.enabled !== false ? 'on' : ''}" onclick="_wfToggleSchedEnabled()"></span>
        <span>${st.linkedSchedule.enabled !== false ? 'Enabled' : 'Disabled'}</span>
      </label>` : ''}
    </div>`;
}

// Deliberately separate from scheduler.js's renderSchedTypeFields (different
// element ids under wfb-sched-*) rather than shared, to avoid two forms
// fighting over one #sched-type-fields if the Scheduler modal is ALSO open.
// <input type="datetime-local"> speaks local wall time with no zone attached
// -- inlined rather than imported, since scheduler.js's own `_schedLocalInputValue`
// is module-private (ES modules don't leak top-level bindings; see this
// file's header on window accessors).
function _wfLocalInputValue(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function _wfSchedTypeFieldsHTML(type, s) {
  const time = s.time || '09:00';
  const days = s.days || [1, 2, 3, 4, 5];
  const interval = s.interval_minutes || 60;
  const runAt = _wfLocalInputValue(s.run_at);
  const cronExpr = s.cron_expr || '';
  if (type === 'daily') {
    return `
      <label>Time</label>
      <input type="time" id="wfb-sched-time" value="${esc(time)}">`;
  }
  if (type === 'weekly') {
    const dayLabels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    return `
      <label>Time</label>
      <input type="time" id="wfb-sched-time" value="${esc(time)}">
      <label>Days</label>
      <div class="sched-days">
        ${dayLabels.map((label, i) => { const d = i + 1; return `<button type="button" class="sched-day-btn${days.includes(d) ? ' active' : ''}" data-day="${d}" onclick="this.classList.toggle('active');_wfMarkDirty()">${label}</button>`; }).join('')}
      </div>`;
  }
  if (type === 'interval') {
    return `<label>Interval (minutes)</label><input type="number" id="wfb-sched-interval" value="${interval}" min="1" step="1">`;
  }
  if (type === 'once') {
    return `<label>Run At</label><input type="datetime-local" id="wfb-sched-runat" value="${esc(runAt)}">`;
  }
  if (type === 'cron') {
    return `<label>Cron Expression</label><input type="text" id="wfb-sched-cron" value="${esc(cronExpr)}" placeholder="*/15 * * * *" spellcheck="false" style="font-family:var(--mono)">`;
  }
  return '';
}

function _wfSetSchedType(type) {
  const entry = _wfEntry(); if (!entry) return;
  const st = entry._wf;
  _wfSyncScheduleFormToState(st);
  st.linkedSchedule.schedule_type = type;
  const box = document.getElementById('wfb-sched-type-fields');
  if (box) box.innerHTML = _wfSchedTypeFieldsHTML(type, st.linkedSchedule);
  document.querySelectorAll('.wfb-sched-cadence .sched-type-btn').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase() === type));
  _wfMarkDirty();
}

function _wfToggleSchedEnabled() {
  const entry = _wfEntry(); if (!entry) return;
  const st = entry._wf;
  if (!st.linkedSchedule) return;
  st.linkedSchedule.enabled = !(st.linkedSchedule.enabled !== false);
  _wfMarkDirty();
  _wfRender();
}

// Reads the cadence sub-form's live DOM values back into st.linkedSchedule.
// Called before anything that might re-render the card (switching type) or
// save, mirroring _wfSyncDomToModel's "sync at the moment of a structural
// action" rule -- typing in the cron field is never clobbered by that.
function _wfSyncScheduleFormToState(st) {
  if (!st.linkedSchedule) st.linkedSchedule = _wfDraftSchedule();
  const s = st.linkedSchedule;
  const timeEl = document.getElementById('wfb-sched-time');
  if (timeEl) s.time = timeEl.value;
  const daysEls = document.querySelectorAll('.wfb-sched-cadence .sched-day-btn.active');
  if (daysEls.length || document.getElementById('wfb-sched-time')) {
    s.days = [...daysEls].map(b => parseInt(b.dataset.day, 10));
  }
  const intervalEl = document.getElementById('wfb-sched-interval');
  if (intervalEl) s.interval_minutes = parseInt(intervalEl.value, 10) || 60;
  const runAtEl = document.getElementById('wfb-sched-runat');
  if (runAtEl && runAtEl.value) {
    // <input type="datetime-local"> is local wall time with no zone attached
    // -- convert through Date the same way scheduler.js's saveSchedule does,
    // never a raw string round-trip (that shifted a one-shot's fire time by
    // the host's UTC offset).
    const d = new Date(runAtEl.value);
    if (!isNaN(d.getTime())) s.run_at = d.toISOString();
  }
  const cronEl = document.getElementById('wfb-sched-cron');
  if (cronEl) s.cron_expr = cronEl.value;
}

// ── The Trigger popover (MC-871 Change 2) ────────────────────────────────────
//
// Everything `.wfb-trigger-card` used to render permanently above the canvas
// now lives here instead, opened by clicking the trigger TILE on the canvas.
// REUSES VERBATIM: _wfSetTriggerType, _wfRenderScheduleCadence (which itself
// calls _wfSchedTypeFieldsHTML/_wfSetSchedType/_wfToggleSchedEnabled) --
// nothing about the cadence form forked, only where it's mounted. The cadence
// still lives on `st.linkedSchedule`, never duplicated onto `def.trigger`.
//
// Same body-appended/outside-click-closes/Escape-closes shape as the port +
// popover and node menu below, for the same reason: `.wfb-modal-body` is an
// overflow:auto scroller (mobile) and the trigger tile can sit near an edge,
// so a popover positioned as a DESCENDANT of the canvas would get clipped.
let _wfTriggerPopoverOpen = false;

// Defect 11 (Ron, after using the Manual/Schedule radio pair): "if we have a
// Run now button, why do we also need that option on the start tile?" -- a
// forced binary choice duplicated what Run now already does. Reworked as a
// single unchecked-by-default checkbox: unchecked IS `manual` (the stored
// default -- mc/workflows.py's TRIGGER_TYPES/validator are untouched, a
// workflow with no schedule is still `manual` on disk), so building a flow
// never makes the author decide about scheduling first. Checking it reveals
// the cadence controls and calls the SAME `_wfSetTriggerType('schedule')`
// this used to wire to a radio; unchecking calls `_wfSetTriggerType('manual')`
// -- x/y and trigger.entry preservation (see that function) apply exactly the
// same way. Checked-with-no-cadence-yet can't happen: `_wfSetTriggerType`
// already seeds `st.linkedSchedule` from `_wfDraftSchedule()` (daily 09:00)
// the moment schedule turns on, so there is always a valid cadence to save.
function _wfTriggerPopoverHTML(st) {
  const def = st.def;
  const triggerType = (def.trigger && def.trigger.type) || 'manual';
  const isSchedule = triggerType === 'schedule';
  return `
    <div class="wfb-popover-title" style="margin-bottom:8px">Trigger</div>
    <label class="wfb-trigger-sched-toggle">
      <input type="checkbox" ${isSchedule ? 'checked' : ''} onchange="_wfSetTriggerType(this.checked ? 'schedule' : 'manual')">
      Run on a schedule
    </label>
    ${isSchedule ? _wfRenderScheduleCadence(st) : ''}`;
}

function _wfOpenTriggerPopover(anchorEl) {
  if (_wfTriggerPopoverOpen) { _wfCloseTriggerPopover(); return; } // a second click closes it
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  _wfClosePortPopover();
  _wfCloseNodeMenu();
  const rect = anchorEl.getBoundingClientRect();
  const box = document.createElement('div');
  box.className = 'wfb-port-popover wfb-trigger-popover';
  box.id = 'wfb-trigger-popover';
  box.innerHTML = _wfTriggerPopoverHTML(entry._wf);
  document.body.appendChild(box);
  _wfTriggerPopoverOpen = true;
  let left = rect.right + 10;
  let top = rect.top;
  if (left + box.offsetWidth > window.innerWidth - 8) left = Math.max(8, rect.left - box.offsetWidth - 10);
  if (top + box.offsetHeight > window.innerHeight - 8) top = Math.max(8, window.innerHeight - box.offsetHeight - 8);
  box.style.left = left + 'px';
  box.style.top = top + 'px';
  // Deferred for the same reason the port popover's own listener is: this
  // click is still propagating and would otherwise close what it just opened.
  setTimeout(() => document.addEventListener('pointerdown', _wfTriggerPopoverOutsideDown, true), 0);
}
window._wfOpenTriggerPopover = _wfOpenTriggerPopover;

// A real bug this caught in testing, not a hypothetical: the popover closes
// on ANY outside click (_wfTriggerPopoverOutsideDown, capturing phase), which
// fires BEFORE the click that triggered it (e.g. clicking Save) reaches that
// element's own handler. The cadence sub-form's fields only sync into the
// model at "the next structural action" (this file's FIELD SYNC discipline,
// header) -- fine when that form was a permanent fixture, but now the DOM
// holding it is destroyed by the SAME click that's about to trigger the sync
// (Save), so a just-toggled day-picker selection would be silently lost:
// gone from the DOM before _wfSave()'s own _wfSyncDomToModel ever runs.
// Flushing it here, before removal, closes that gap.
function _wfCloseTriggerPopover() {
  const el = document.getElementById('wfb-trigger-popover');
  if (el) {
    const entry = _wfEntry();
    if (entry && entry._wf && entry._wf.def.trigger && entry._wf.def.trigger.type === 'schedule') {
      _wfSyncScheduleFormToState(entry._wf);
      // Defect 11's follow-on: the tile's sub-label is only recomputed by a
      // full _wfRender(), which the cadence sub-form's own edits (picking
      // Weekly, toggling a day, typing a time) never trigger on their own --
      // only the schedule/manual checkbox does. Patch just the label text
      // here, now that the sync above just caught up `linkedSchedule` with
      // whatever was left in the form, so closing the popover is always the
      // point the tile catches up to what was actually configured.
      const sub = document.querySelector('.wfb-trigger-box .wfb-trigger-box-sub');
      if (sub && typeof window.scheduleDescription === 'function') sub.textContent = window.scheduleDescription(entry._wf.linkedSchedule || {});
    }
    el.remove();
  }
  document.removeEventListener('pointerdown', _wfTriggerPopoverOutsideDown, true);
  _wfTriggerPopoverOpen = false;
}

function _wfTriggerPopoverOutsideDown(e) {
  const el = document.getElementById('wfb-trigger-popover');
  if (el && !el.contains(e.target) && !e.target.closest('.wfb-trigger-box')) _wfCloseTriggerPopover();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _wfTriggerPopoverOpen) _wfCloseTriggerPopover();
});

function _wfAddOutcome(name) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const node = (entry._wf.def.nodes || []).find(n => n.name === name); if (!node) return;
  node.outcomes = node.outcomes || [];
  node.outcomes.push('outcome-' + (node.outcomes.length + 1));
  _wfMarkDirty();
  _wfRender();
}

function _wfRemoveOutcome(name, idx) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const def = entry._wf.def;
  const node = (def.nodes || []).find(n => n.name === name); if (!node) return;
  const label = (node.outcomes || [])[idx];
  if (label === undefined) return;
  const before = _wfFindBrokenSlotRefs(def.nodes, def.edges || []);
  const savedOutcomes = node.outcomes;
  node.outcomes = node.outcomes.filter((_, i) => i !== idx);
  const newEdges = (def.edges || []).filter(e => !(e.from === name && e.when === label));
  const after = _wfFindBrokenSlotRefs(def.nodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    node.outcomes = savedOutcomes;
    showToast(`Can't remove outcome "${label}" — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs the edge on that port.`, 6000);
    return;
  }
  def.edges = newEdges;
  _wfMarkDirty();
  _wfRender();
}

function _wfAddOption(name) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const node = (entry._wf.def.nodes || []).find(n => n.name === name); if (!node) return;
  const n = (node.options || []).length + 1;
  node.options = (node.options || []).concat(['option-' + n]);
  _wfMarkDirty();
  _wfRender();
}

function _wfRemoveOption(name, idx) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const def = entry._wf.def;
  const node = (def.nodes || []).find(n => n.name === name); if (!node) return;
  const label = (node.options || [])[idx];
  if (label === undefined) return;
  if ((node.options || []).length <= 1) { showToast('An approval gate needs at least one option.', 4000); return; }
  const before = _wfFindBrokenSlotRefs(def.nodes, def.edges || []);
  const savedOptions = node.options;
  node.options = node.options.filter((_, i) => i !== idx);
  const newEdges = (def.edges || []).filter(e => !(e.from === name && e.when === label));
  const after = _wfFindBrokenSlotRefs(def.nodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    node.options = savedOptions;
    showToast(`Can't remove option "${label}" — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs the edge on that port.`, 6000);
    return;
  }
  def.edges = newEdges;
  _wfMarkDirty();
  _wfRender();
}

function _wfDeleteNode(name) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const def = entry._wf.def;
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const before = _wfFindBrokenSlotRefs(nodes, edges);
  const newNodes = nodes.filter(n => n.name !== name);
  const newEdges = edges.filter(e => e.from !== name && e.to !== name);
  const after = _wfFindBrokenSlotRefs(newNodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    showToast(`Can't delete "${name}" — "${newOnes[0].step}" still reads {{steps.${newOnes[0].ref}.…}}.`, 6000);
    return;
  }
  def.nodes = newNodes;
  def.edges = newEdges;
  // Change 12a: a deleted node can't stay a trigger.entry -- prune it the
  // same way its edges are pruned above.
  if (def.trigger && Array.isArray(def.trigger.entry)) {
    def.trigger.entry = def.trigger.entry.filter(n => n !== name);
  }
  if (_wfSelectedEdge && (_wfSelectedEdge.from === name || _wfSelectedEdge.to === name)) _wfSelectedEdge = null;
  _wfMarkDirty();
  _wfRender();
}

// Change 6b: copy a node's own config (prompt/persona/project/outcomes/
// options/action config) under a fresh unique name, offset so it doesn't
// land exactly on the original. Edges are per-CONNECTION state (R2-D1 — "a
// branch is a property of the connection"), so a duplicate never copies
// them; wiring the copy is the user's next decision, per the brief.
function _wfDuplicateNode(rawName) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  const name = renameMap[rawName] || rawName;
  const st = entry._wf;
  const node = (st.def.nodes || []).find(n => n.name === name);
  if (!node) return;
  const copy = JSON.parse(JSON.stringify(node));
  copy.name = _wfUniqueNodeName(st, _wfSlug(node.name));
  copy.x = (node.x || 0) + 40;
  copy.y = (node.y || 0) + 40;
  st.def.nodes = (st.def.nodes || []).concat([copy]);
  _wfMarkDirty();
  _wfRender();
  if (copy.type === 'agent') _wfFocusPrompt(copy.name);
}
window._wfDuplicateNode = _wfDuplicateNode;

// Change 6b: drop every edge into AND out of this node, leaving the card in
// place -- the undo for a mis-drop now that dropping a person on a card
// auto-wires it (Ron: "now that dropping on a card auto-wires it, I need a
// way to undo that without deleting the step"). Same slot-break guard every
// other edge-removing mutator here runs. Change 12a: also drops the node
// from `def.trigger.entry` if it was explicitly wired to the trigger --
// "every edge into and out of this node" reads as covering the implied
// trigger wire too, not just real `def.edges` members.
function _wfDisconnectNode(rawName) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  const name = renameMap[rawName] || rawName;
  const def = entry._wf.def;
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const inTriggerEntry = _wfTriggerEntry(def).includes(name);
  if (!edges.some(e => e.from === name || e.to === name) && !inTriggerEntry) return; // already isolated
  const before = _wfFindBrokenSlotRefs(nodes, edges);
  const newEdges = edges.filter(e => e.from !== name && e.to !== name);
  const after = _wfFindBrokenSlotRefs(nodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    showToast(`Can't disconnect "${name}" — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs an edge through it to stay reachable.`, 6000);
    return;
  }
  def.edges = newEdges;
  if (inTriggerEntry && def.trigger) def.trigger.entry = def.trigger.entry.filter(n => n !== name);
  if (_wfSelectedEdge && (_wfSelectedEdge.from === name || _wfSelectedEdge.to === name)) _wfSelectedEdge = null;
  _wfMarkDirty();
  _wfRender();
}
window._wfDisconnectNode = _wfDisconnectNode;

let _wfSelectedEdge = null; // { from, to, when } | null

function _wfEdgeClick(e, from, to, when) {
  e.stopPropagation();
  _wfSelectedEdge = { from, to, when: when || null };
  _wfRedrawEdges();
}

// Change 7: the visible × at an edge's midpoint. Same removal path as the
// keyboard shortcut (select then Delete/Backspace, still below) -- this is
// only a second way to REACH _wfDeleteEdge, not a second implementation, so
// the slot-break refusal + toast it already carries applies unchanged.
function _wfEdgeDelClick(e, from, to, when) {
  e.stopPropagation();
  _wfDeleteEdge(from, to, when || null);
}
window._wfEdgeDelClick = _wfEdgeDelClick;

function _wfDeleteEdge(from, to, when) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  from = renameMap[from] || from;
  to = renameMap[to] || to;
  const def = entry._wf.def;
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const before = _wfFindBrokenSlotRefs(nodes, edges);
  const newEdges = edges.filter(e => !(e.from === from && e.to === to && (e.when || null) === (when || null)));
  const after = _wfFindBrokenSlotRefs(nodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    showToast(`Can't remove that connection — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs it to stay reachable.`, 6000);
    return;
  }
  def.edges = newEdges;
  _wfSelectedEdge = null;
  _wfMarkDirty();
  _wfRender();
}

document.addEventListener('keydown', (e) => {
  if (!_wfState || !_wfSelectedEdge) return;
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  if (e.key === 'Delete' || e.key === 'Backspace') {
    _wfDeleteEdge(_wfSelectedEdge.from, _wfSelectedEdge.to, _wfSelectedEdge.when);
  } else if (e.key === 'Escape') {
    _wfSelectedEdge = null;
    _wfRedrawEdges();
  }
});

function _wfTryAddEdge(from, to, when) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  from = renameMap[from] || from;
  to = renameMap[to] || to;
  const def = entry._wf.def;
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  if (edges.some(e => e.from === from && e.to === to && (e.when || null) === (when || null))) return;
  // R2-D3 layer 1: the drag that would close a cycle is refused at drop.
  if (_wfHasPath(edges, to, from)) {
    showToast(`${from} → ${to} would create a loop (${to} already reaches ${from}).`, 5000);
    return;
  }
  const before = _wfFindBrokenSlotRefs(nodes, edges);
  const candidateEdges = edges.concat([{ from, to, when: when || undefined }]);
  const after = _wfFindBrokenSlotRefs(nodes, candidateEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    showToast(`Can't connect ${from} → ${to} — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which this would make skippable.`, 6000);
    return;
  }
  def.edges = candidateEdges;
  _wfMarkDirty();
  _wfRender();
}

// ── Canvas: pan + zoom (viewport is session-only, see file header) ──────────

function _wfApplyViewport(st) {
  const world = document.getElementById('wfb-world');
  if (world) world.style.transform = `translate(${st.viewport.x}px, ${st.viewport.y}px) scale(${st.viewport.scale})`;
  _wfRedrawEdges();
}

function _wfZoomAt(st, screenX, screenY, newScaleRaw) {
  const newScale = Math.min(2.5, Math.max(0.25, newScaleRaw));
  const v = st.viewport;
  const wx = (screenX - v.x) / v.scale;
  const wy = (screenY - v.y) / v.scale;
  v.x = screenX - wx * newScale;
  v.y = screenY - wy * newScale;
  v.scale = newScale;
  _wfApplyViewport(st);
}

function _wfCanvasWheel(e) {
  e.preventDefault();
  const entry = _wfEntry(); if (!entry) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
  const factor = Math.exp(-e.deltaY * 0.0015);
  _wfZoomAt(entry._wf, cx, cy, entry._wf.viewport.scale * factor);
}

let _wfPan = null;

function _wfViewportDown(e) {
  // The trigger box (MC-871 Change 2) is its own shape, not a `.wfb-node` --
  // this guard used to only exclude nodes/ports, so a pointerdown anywhere on
  // the trigger tile OTHER than its head (which has its own drag handler,
  // _wfNodeDragDown) fell through to here: a plain click on the "Manual"
  // sub-label or the port row PANNED THE WHOLE CANVAS (every node visibly
  // drags along with what looked like a Start-tile drag -- Ron: "if I grab
  // the start tile ... everything moves with it", the same complaint Change
  // 4b's z-index fix addressed for overlap hit-testing but not this bubble
  // path), and `setPointerCapture` below retargets the pointerup/click that
  // follows to the VIEWPORT -- exactly the mechanism _wfNodeDragDown's own
  // header comment already documents for the node "..." menu button -- which
  // silently ate clicks on the trigger's own "+" (`.wfb-port-plus` is not
  // `.wfb-port`, so it wasn't covered either).
  if (e.target.closest('.wfb-node, .wfb-port, .wfb-trigger-box')) return;
  if (typeof e.button === 'number' && e.button !== 0) return;
  if (_wfPan || _wfNodeDrag || _wfPlaceDrag || _wfConnectDrag) return;
  const entry = _wfEntry(); if (!entry) return;
  // Change 8: a pan that starts on empty canvas must never begin a native
  // text-selection drag underneath it (Ron: card text "gets selected and
  // highlighted" while panning) -- this handler never called preventDefault
  // at all before. The CSS user-select:none on .wfb-canvas-viewport (app.css)
  // is the other half; this stops the browser's own selection gesture at the
  // source rather than fighting it after the fact.
  e.preventDefault();
  const vp = e.currentTarget;
  _wfPan = { pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, vx: entry._wf.viewport.x, vy: entry._wf.viewport.y, vp };
  vp.classList.add('wfb-panning');
  try { vp.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  vp.addEventListener('pointermove', _wfViewportMove);
  vp.addEventListener('pointerup', _wfViewportUp);
  vp.addEventListener('pointercancel', _wfViewportUp);
}

function _wfViewportMove(e) {
  if (!_wfPan || e.pointerId !== _wfPan.pointerId) return;
  const entry = _wfEntry(); if (!entry) return;
  entry._wf.viewport.x = _wfPan.vx + (e.clientX - _wfPan.startX);
  entry._wf.viewport.y = _wfPan.vy + (e.clientY - _wfPan.startY);
  _wfApplyViewport(entry._wf);
}

function _wfViewportUp(e) {
  if (!_wfPan || (e.pointerId !== undefined && e.pointerId !== _wfPan.pointerId)) return;
  const st = _wfPan;
  st.vp.classList.remove('wfb-panning');
  try { st.vp.releasePointerCapture(st.pointerId); } catch (err) { /* already released */ }
  st.vp.removeEventListener('pointermove', _wfViewportMove);
  st.vp.removeEventListener('pointerup', _wfViewportUp);
  st.vp.removeEventListener('pointercancel', _wfViewportUp);
  _wfPan = null;
}

// Native touch listeners for 2-finger pinch — Pointer Events don't aggregate
// multi-touch, so this mirrors mermaid.js's viewer-gesture pinch handling
// rather than extending the pointer-based pan above.
let _wfPinch = null;

function _wfTouchDist(t) { return Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY); }
function _wfTouchMid(t) { return { x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 }; }

function _wfCanvasTouchStart(e) {
  if (e.touches.length !== 2) return;
  if (_wfPan) _wfViewportUp({ pointerId: _wfPan.pointerId });
  const entry = _wfEntry(); if (!entry) return;
  _wfPinch = { d: _wfTouchDist(e.touches) || 1, scale: entry._wf.viewport.scale, vp: e.currentTarget };
  e.preventDefault();
}

function _wfCanvasTouchMove(e) {
  if (!_wfPinch || e.touches.length !== 2) return;
  e.preventDefault();
  const entry = _wfEntry(); if (!entry) return;
  const rect = _wfPinch.vp.getBoundingClientRect();
  const mid = _wfTouchMid(e.touches);
  _wfZoomAt(entry._wf, mid.x - rect.left, mid.y - rect.top, _wfPinch.scale * (_wfTouchDist(e.touches) / _wfPinch.d));
}

function _wfCanvasTouchEnd(e) {
  if (_wfPinch && e.touches.length < 2) _wfPinch = null;
}

function _wfAttachCanvasGestures() {
  const vp = document.getElementById('wfb-canvas-viewport');
  if (!vp) return;
  vp.addEventListener('wheel', _wfCanvasWheel, { passive: false });
  vp.addEventListener('touchstart', _wfCanvasTouchStart, { passive: false });
  vp.addEventListener('touchmove', _wfCanvasTouchMove, { passive: false });
  vp.addEventListener('touchend', _wfCanvasTouchEnd);
  vp.addEventListener('touchcancel', _wfCanvasTouchEnd);
}

// ── Drag-to-place: palette block → new node on the canvas ────────────────────
// Same pointer-event gesture shape as floor.js's drag-to-hire: long-press
// activation on touch, 8px slop on mouse/pen, a ghost that follows the
// pointer, dropped only if released over the canvas viewport.

const WFB_LONG_PRESS_MS = 400;
const WFB_DRAG_SLOP_PX = 8;

let _wfPlaceDrag = null;

function _wfPointInRect(x, y, r) { return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom; }

// `type` is 'person' | 'approval' | 'action'. A person carries its bench
// identity (scope + name) through the drag so the drop can build an agent step
// with that persona already set — the whole point of the palette being the
// Bench (UI brief §2).
function _wfPaletteDown(e, type, scope, name) {
  if (typeof e.button === 'number' && e.button !== 0) return;
  if (_wfPlaceDrag || _wfNodeDrag || _wfPan || _wfConnectDrag) return;
  // Change 8: same text-selection guard -- a drag-out from the palette
  // starts over a row's own name/role text.
  e.preventDefault();
  if (_wfPalettePopoverHideTimer) { clearTimeout(_wfPalettePopoverHideTimer); _wfPalettePopoverHideTimer = null; }
  _wfHidePalettePopover();
  const st = {
    pointerId: e.pointerId, pointerType: e.pointerType || 'mouse',
    startX: e.clientX, startY: e.clientY, active: false, type,
    scope: scope || '', name: name || '',
    el: e.currentTarget, ghost: null, longPressTimer: null,
  };
  _wfPlaceDrag = st;
  if (st.pointerType === 'touch') {
    st.longPressTimer = setTimeout(() => { if (_wfPlaceDrag === st && !st.active) _wfPlaceActivate(st, st.startX, st.startY); }, WFB_LONG_PRESS_MS);
  }
  try { st.el.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  window.addEventListener('pointermove', _wfPlaceMove);
  window.addEventListener('pointerup', _wfPlaceUp);
  window.addEventListener('pointercancel', _wfPlaceCancel);
  window.addEventListener('blur', _wfPlaceCancel);
}

function _wfPlaceActivate(st, x, y) {
  st.active = true;
  clearTimeout(st.longPressTimer);
  st.el.classList.add('wfb-palette-dragging');
  const ghost = document.createElement('div');
  ghost.className = 'wfb-place-ghost';
  ghost.textContent = st.type === 'person'
    ? (((_wfEntry() || {})._wf
        && (_wfBenchLookup(_wfEntry()._wf, st.scope, st.name) || {}).display) || st.name)
    : _wfTypeLabel(st.type);
  ghost.style.left = x + 'px';
  ghost.style.top = y + 'px';
  document.body.appendChild(ghost);
  st.ghost = ghost;
}

function _wfPlaceMove(e) {
  const st = _wfPlaceDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  const dx = e.clientX - st.startX, dy = e.clientY - st.startY;
  if (!st.active) {
    if (st.pointerType !== 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX) _wfPlaceActivate(st, e.clientX, e.clientY);
    else if (st.pointerType === 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX * 1.5) { clearTimeout(st.longPressTimer); _wfPlaceTeardown(st); }
    return;
  }
  e.preventDefault();
  if (st.ghost) { st.ghost.style.left = e.clientX + 'px'; st.ghost.style.top = e.clientY + 'px'; }
  const vp = document.getElementById('wfb-canvas-viewport');
  if (vp) vp.classList.toggle('wfb-drop-target', _wfPointInRect(e.clientX, e.clientY, vp.getBoundingClientRect()));
}

function _wfPlaceUp(e) {
  const st = _wfPlaceDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (st.active) {
    const vp = document.getElementById('wfb-canvas-viewport');
    if (vp && _wfPointInRect(e.clientX, e.clientY, vp.getBoundingClientRect())) {
      _wfPlaceNodeAt(st.type, e.clientX, e.clientY, vp, st.scope, st.name);
    }
  }
  _wfPlaceTeardown(st);
}

function _wfPlaceCancel(e) {
  const st = _wfPlaceDrag;
  if (!st) return;
  if (e && e.pointerId !== undefined && e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  _wfPlaceTeardown(st);
}

function _wfPlaceTeardown(st) {
  st.el.classList.remove('wfb-palette-dragging');
  const vp = document.getElementById('wfb-canvas-viewport');
  if (vp) vp.classList.remove('wfb-drop-target');
  if (st.ghost) { st.ghost.remove(); st.ghost = null; }
  document.querySelectorAll('.wfb-place-ghost').forEach(g => g.remove());
  window.removeEventListener('pointermove', _wfPlaceMove);
  window.removeEventListener('pointerup', _wfPlaceUp);
  window.removeEventListener('pointercancel', _wfPlaceCancel);
  window.removeEventListener('blur', _wfPlaceCancel);
  try { st.el.releasePointerCapture(st.pointerId); } catch (e) { /* already released */ }
  _wfPlaceDrag = null;
}

function _wfPlaceNodeAt(type, clientX, clientY, vp, scope, name) {
  const entry = _wfEntry(); if (!entry) return;
  // Sync first: a drop re-renders, and the sync can RENAME the very card being
  // dropped onto, so resolve the target's name only after it has run.
  const renameMap = _wfSyncDomToModel(entry);
  const st = entry._wf;

  // Dropped ONTO the trigger tile (Change 4b): the honest equivalent of
  // dropping onto a card -- place the step and leave it a ROOT (ie. wired to
  // the trigger, R2-D4's own definition of "runs first"). Checked before the
  // node lookup below: `.wfb-trigger-box` doesn't match `.wfb-node`, so this
  // used to fall through to the free-placement branch and land the card
  // overlapping the tile instead of doing anything useful with the drop.
  const under = document.elementFromPoint(clientX, clientY);
  const triggerEl = under && under.closest ? under.closest('.wfb-trigger-box') : null;
  if (triggerEl) { _wfInsertRootAfterTrigger(entry, type, scope, name); return; }

  // Dropped ONTO an existing card = the same thing as that card's `+`: place
  // after it and wire the edge (UI brief §4). Which port: the one actually
  // under the pointer if there is one, else the card's first output port.
  const cardEl = under && under.closest ? under.closest('.wfb-node') : null;
  if (cardEl && cardEl.dataset.name) {
    const fromName = renameMap[cardEl.dataset.name] || cardEl.dataset.name;
    const portEl = under.closest('.wfb-port-out');
    const fromNode = (st.def.nodes || []).find(n => n.name === fromName);
    if (fromNode) {
      const when = portEl ? (portEl.dataset.when || null) : ((_wfOutPorts(fromNode)[0] || {}).when || null);
      _wfInsertAfter(entry, fromName, when, type, scope, name, { synced: true });
      return;
    }
  }

  const rect = vp.getBoundingClientRect();
  const v = st.viewport;
  const wx = (clientX - rect.left - v.x) / v.scale;
  const wy = (clientY - rect.top - v.y) / v.scale;
  const node = _wfMakeNode(st, type, scope, name, wx - 130, wy - 24);
  st.def.nodes = (st.def.nodes || []).concat([node]);
  _wfMarkDirty();
  _wfRender();
  if (node.type === 'agent') _wfFocusPrompt(node.name);
}

// Place a new node after `fromName`'s `when` port and wire the edge. The one
// path every auto-place uses — the port `+` popover and drop-onto-card both
// land here, so "added and wired" means one thing.
//
// No cycle check is needed: the node is brand new, so nothing can already
// reach it. No slot-break check either — this only ADDS an edge and a node,
// and `_wfFindBrokenSlotRefs` violations come from removing reachability.
function _wfInsertAfter(entry, fromName, when, type, scope, name, opts) {
  if (!(opts && opts.synced)) {
    const renameMap = _wfSyncDomToModel(entry);
    fromName = renameMap[fromName] || fromName;
  }
  const st = entry._wf;
  const def = st.def;
  const fromNode = (def.nodes || []).find(n => n.name === fromName);
  if (!fromNode) return;
  // Desktop places the new card to the right of its parent and stacks
  // branches downward, one row per port (UI brief §5).
  const idx = Math.max(0, _wfOutPorts(fromNode).findIndex(p => (p.when || null) === (when || null)));
  const node = _wfMakeNode(st, type, scope, name, (fromNode.x || 0) + 320, (fromNode.y || 0) + idx * 150);
  def.nodes = (def.nodes || []).concat([node]);
  def.edges = (def.edges || []).concat([{ from: fromName, to: node.name, when: when || undefined }]);
  _wfMarkDirty();
  _wfRender();
  if (node.type === 'agent') _wfFocusPrompt(node.name);
}

// Place a new node as a ROOT, explicitly wired to the trigger -- the
// trigger's own "+"/drop-onto-the-tile target (Change 4a/4b/12a). No EDGE is
// added: `mc/workflows.py:421` refuses any persisted edge whose `from` isn't
// a real node, and `:656` already defines a root (no incoming edges) as
// running the moment the run starts. What IS recorded is `def.trigger.entry`
// -- this gesture (the trigger's own + / a drop ON the trigger tile) is an
// explicit act, unlike an ordinary drop on empty canvas (Change 12a), so the
// new node's name is added there. `_wfRedrawEdges` draws the implied line
// for entries that are still roots; nothing here needs to know about that.
function _wfInsertRootAfterTrigger(entry, type, scope, name) {
  const st = entry._wf;
  const def = st.def;
  const trigger = def.trigger || (def.trigger = { type: 'manual' });
  const hasPos = typeof trigger.x === 'number' && typeof trigger.y === 'number';
  const pos = hasPos ? trigger : _wfTriggerDefaultPos(def);
  const node = _wfMakeNode(st, type, scope, name, (pos.x || 0), (pos.y || 0) + 110);
  def.nodes = (def.nodes || []).concat([node]);
  trigger.entry = _wfTriggerEntry(def).concat([node.name]);
  _wfMarkDirty();
  _wfRender();
  if (node.type === 'agent') _wfFocusPrompt(node.name);
}

// Drop every incoming edge into `name` AND add it to `def.trigger.entry`,
// explicitly wiring it to the trigger -- Change 4a's inverse (dragging FROM
// the trigger port ONTO an existing card), corrected by 12a to record the
// explicit act rather than relying on "has no incoming edges" alone (which
// is also true of every untouched standalone drop, and Ron does not want
// those auto-wired). Guarded by the same slot-break check every other
// edge-removing mutator here already runs (_wfDeleteEdge/_wfDeleteNode):
// stripping incoming edges can strand a `{{steps.X.*}}` reference the same
// way deleting one edge can.
function _wfMakeRoot(rawName) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  const name = renameMap[rawName] || rawName;
  const def = entry._wf.def;
  const trigger = def.trigger || (def.trigger = { type: 'manual' });
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const alreadyEntry = _wfTriggerEntry(def).includes(name);
  const hasIncoming = edges.some(e => e.to === name);
  if (alreadyEntry && !hasIncoming) return; // already explicitly wired and already a root -- nothing to do
  let newEdges = edges;
  if (hasIncoming) {
    const before = _wfFindBrokenSlotRefs(nodes, edges);
    newEdges = edges.filter(e => e.to !== name);
    const after = _wfFindBrokenSlotRefs(nodes, newEdges);
    const newOnes = _wfNewViolations(before, after);
    if (newOnes.length) {
      showToast(`Can't wire "${name}" directly to the trigger — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs an incoming edge to stay reachable.`, 6000);
      return;
    }
  }
  def.edges = newEdges;
  if (!alreadyEntry) trigger.entry = _wfTriggerEntry(def).concat([name]);
  _wfMarkDirty();
  _wfRender();
}

// The prompt is the one place the author actually types (UI brief §2 — "prompt
// focuses"), so a freshly-dropped person hands them the caret.
function _wfFocusPrompt(nodeName) {
  const el = document.querySelector(`.wfb-node[data-name="${_wfAttrEsc(nodeName)}"] .wfb-prompt`);
  // preventScroll is load-bearing, not a nicety: the modal body is an
  // overflow:auto scroller, so a plain focus() scrolls the freshly-dropped
  // card into view and drags the whole canvas out from under the pointer —
  // the card you just placed jumps, and the next drop lands somewhere else.
  if (el) el.focus({ preventScroll: true });
}

// ── Drag to move an existing node ─────────────────────────────────────────────

let _wfNodeDrag = null;

function _wfNodeDragDown(e) {
  if (typeof e.button === 'number' && e.button !== 0) return;
  // Change 6a: the "..." menu button lives INSIDE .wfb-node-head, which owns
  // this same pointerdown handler. Diagnosed live (Playwright event trace):
  // this handler's own `setPointerCapture` below retargets the button's
  // pointerup/mouseup/click to the HEAD once it captures the pointer, so the
  // button's onclick never fired -- clicking "..." looked completely dead.
  // The fix is to never start a drag from the button in the first place
  // (and never capture its pointer), not to remove the capture generally --
  // dragging the head still needs it.
  if (e.target.closest('.wfb-node-menu-btn')) return;
  if (_wfNodeDrag || _wfPan || _wfPlaceDrag || _wfConnectDrag) return;
  // The Trigger box (MC-871 Change 2) is deliberately NOT a node -- not in
  // `nodes[]`, no card markup -- but Ron wants it draggable on the same
  // canvas via the same gesture, so it shares this one drag path rather than
  // getting a second implementation. `_wfNodeDragUp` below is the only place
  // that branches on which kind of element this turned out to be.
  const nodeEl = e.currentTarget.closest('.wfb-node, .wfb-trigger-box');
  const entry = _wfEntry();
  if (!nodeEl || !entry) return;
  // Change 8: same text-selection guard as the canvas pan below -- a card or
  // trigger drag that starts before the slop threshold (or during a touch
  // long-press wait) must not let the browser start selecting the card's own
  // text underneath it.
  e.preventDefault();
  const st = {
    pointerId: e.pointerId, pointerType: e.pointerType || 'mouse',
    startX: e.clientX, startY: e.clientY, active: false, nodeEl,
    scale: entry._wf.viewport.scale,
    origX: parseFloat(nodeEl.style.left) || 0, origY: parseFloat(nodeEl.style.top) || 0,
    longPressTimer: null,
  };
  _wfNodeDrag = st;
  if (st.pointerType === 'touch') {
    st.longPressTimer = setTimeout(() => { if (_wfNodeDrag === st && !st.active) _wfNodeDragActivate(st); }, WFB_LONG_PRESS_MS);
  }
  try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  window.addEventListener('pointermove', _wfNodeDragMove);
  window.addEventListener('pointerup', _wfNodeDragUp);
  window.addEventListener('pointercancel', _wfNodeDragCancel);
  window.addEventListener('blur', _wfNodeDragCancel);
}

function _wfNodeDragActivate(st) {
  st.active = true;
  clearTimeout(st.longPressTimer);
  st.nodeEl.classList.add('wfb-node-dragging');
}

function _wfNodeDragMove(e) {
  const st = _wfNodeDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  const dx = e.clientX - st.startX, dy = e.clientY - st.startY;
  if (!st.active) {
    if (st.pointerType !== 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX) _wfNodeDragActivate(st);
    else if (st.pointerType === 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX * 1.5) { clearTimeout(st.longPressTimer); _wfNodeDragTeardown(st); }
    return;
  }
  e.preventDefault();
  st.nodeEl.style.left = (st.origX + dx / st.scale) + 'px';
  st.nodeEl.style.top = (st.origY + dy / st.scale) + 'px';
  _wfRedrawEdges();
}

function _wfNodeDragUp(e) {
  const st = _wfNodeDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (st.active) {
    const entry = _wfEntry();
    if (entry && st.nodeEl.dataset.name === '__trigger__') {
      entry._wf.def.trigger.x = parseFloat(st.nodeEl.style.left) || 0;
      entry._wf.def.trigger.y = parseFloat(st.nodeEl.style.top) || 0;
      _wfMarkDirty();
    } else {
      const node = entry && (entry._wf.def.nodes || []).find(n => n.name === st.nodeEl.dataset.name);
      if (node) {
        node.x = parseFloat(st.nodeEl.style.left) || 0;
        node.y = parseFloat(st.nodeEl.style.top) || 0;
        _wfMarkDirty();
      }
    }
  } else if (st.nodeEl.dataset.name === '__trigger__') {
    // MC-871 Change 2: a plain click on the trigger tile (no real drag ever
    // happened -- `st.active` is the exact slop/long-press gate _wfNodeDragMove
    // uses to promote this gesture to a drag in the first place) opens its
    // config popover. Reusing that flag here is what tells a click from a
    // drag apart, rather than adding a second gesture path.
    _wfOpenTriggerPopover(st.nodeEl.querySelector('.wfb-trigger-box-head') || st.nodeEl);
  }
  _wfNodeDragTeardown(st);
}

function _wfNodeDragCancel(e) {
  const st = _wfNodeDrag;
  if (!st) return;
  if (e && e.pointerId !== undefined && e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (st.active) { st.nodeEl.style.left = st.origX + 'px'; st.nodeEl.style.top = st.origY + 'px'; _wfRedrawEdges(); }
  _wfNodeDragTeardown(st);
}

function _wfNodeDragTeardown(st) {
  st.nodeEl.classList.remove('wfb-node-dragging');
  window.removeEventListener('pointermove', _wfNodeDragMove);
  window.removeEventListener('pointerup', _wfNodeDragUp);
  window.removeEventListener('pointercancel', _wfNodeDragCancel);
  window.removeEventListener('blur', _wfNodeDragCancel);
  try { st.nodeEl.releasePointerCapture(st.pointerId); } catch (e) { /* already released */ }
  _wfNodeDrag = null;
}

// ── Drag to connect: output port → input port ────────────────────────────────

let _wfConnectDrag = null;

function _wfPortDown(e) {
  e.stopPropagation();
  if (typeof e.button === 'number' && e.button !== 0) return;
  if (_wfConnectDrag || _wfNodeDrag || _wfPan || _wfPlaceDrag) return;
  e.preventDefault(); // Change 8: same text-selection guard as the other drag starts
  const portEl = e.currentTarget;
  const st = { pointerId: e.pointerId, fromNode: portEl.dataset.node, when: portEl.dataset.when || null, portEl, curX: e.clientX, curY: e.clientY };
  _wfConnectDrag = st;
  try { portEl.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  window.addEventListener('pointermove', _wfConnectMove);
  window.addEventListener('pointerup', _wfConnectUp);
  window.addEventListener('pointercancel', _wfConnectCancel);
  window.addEventListener('blur', _wfConnectCancel);
  _wfRedrawEdges();
}

// MC-871 Change 5 — "forgiving drop": a connect-drag completes on a drop
// ANYWHERE on the target card, not only the 40px in-port hit box. The in-port
// itself still exists and still highlights (visible feedback for the precise
// case), but requiring the drop to land exactly on it was the discoverability
// bug Ron hit ("no way to connect a tile to another unless triggered by the
// small plus icon") — the gesture worked, the target was just too small to
// find. `.wfb-node` never matches the trigger tile (`.wfb-trigger-box` is a
// different class, by design — nothing can feed a trigger), and excluding the
// drag's own origin card here is what "keep refusing self-connection" means
// at this layer; _wfTryAddEdge's cycle/slot-break guards are untouched.
function _wfConnectResolveTarget(clientX, clientY, fromNode) {
  const target = document.elementFromPoint(clientX, clientY);
  const cardEl = target && target.closest ? target.closest('.wfb-node') : null;
  if (!cardEl || !cardEl.dataset.name || cardEl.dataset.name === fromNode) return null;
  return cardEl;
}

function _wfConnectMove(e) {
  const st = _wfConnectDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  e.preventDefault();
  st.curX = e.clientX; st.curY = e.clientY;
  const cardEl = _wfConnectResolveTarget(e.clientX, e.clientY, st.fromNode);
  // Highlight the WHOLE candidate card (not just its in-port) so it's obvious
  // mid-gesture where the wire can land, per the brief.
  document.querySelectorAll('.wfb-node.wfb-connect-target').forEach((el) => { if (el !== cardEl) el.classList.remove('wfb-connect-target'); });
  document.querySelectorAll('.wfb-port-in.wfb-port-target').forEach((p) => { if (!cardEl || p.dataset.node !== cardEl.dataset.name) p.classList.remove('wfb-port-target'); });
  if (cardEl) {
    cardEl.classList.add('wfb-connect-target');
    const portIn = cardEl.querySelector('.wfb-port-in');
    if (portIn) portIn.classList.add('wfb-port-target');
  }
  _wfRedrawEdges();
}

function _wfConnectUp(e) {
  const st = _wfConnectDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  const cardEl = _wfConnectResolveTarget(e.clientX, e.clientY, st.fromNode);
  _wfConnectTeardown(st);
  if (!cardEl) return;
  const toName = cardEl.dataset.name;
  // Change 4a's inverse: dragging FROM the trigger port onto an existing
  // card makes that card a root, rather than trying to persist a "__trigger__"
  // edge (mc/workflows.py:421 would refuse it — see _wfRenderTriggerBox).
  if (st.fromNode === '__trigger__') _wfMakeRoot(toName);
  else _wfTryAddEdge(st.fromNode, toName, st.when);
}

function _wfConnectCancel(e) {
  const st = _wfConnectDrag;
  if (!st) return;
  if (e && e.pointerId !== undefined && e.pointerId !== st.pointerId) return;
  _wfConnectTeardown(st);
}

function _wfConnectTeardown(st) {
  document.querySelectorAll('.wfb-port-in.wfb-port-target').forEach(p => p.classList.remove('wfb-port-target'));
  document.querySelectorAll('.wfb-node.wfb-connect-target').forEach(el => el.classList.remove('wfb-connect-target'));
  window.removeEventListener('pointermove', _wfConnectMove);
  window.removeEventListener('pointerup', _wfConnectUp);
  window.removeEventListener('pointercancel', _wfConnectCancel);
  window.removeEventListener('blur', _wfConnectCancel);
  try { st.portEl.releasePointerCapture(st.pointerId); } catch (e) { /* already released */ }
  _wfConnectDrag = null;
  _wfRedrawEdges();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _wfConnectDrag) _wfConnectCancel(null);
});

// ── Edge overlay: one absolutely-positioned SVG, cubic paths between the
//    real port DOM elements' current screen positions (R2-D7: "an
//    absolutely-positioned SVG overlay drawing cubic paths between port
//    coordinates"). Recomputed on every pan/zoom tick and node-drag frame,
//    plus whenever the node/edge list changes shape via `_wfRender`. ────────

function _wfRedrawEdges() {
  const svg = document.getElementById('wfb-canvas-svg');
  const vp = document.getElementById('wfb-canvas-viewport');
  const entry = _wfEntry();
  if (!svg || !vp || !entry || !entry._wf) return;
  const rect = vp.getBoundingClientRect();
  const ptOf = (el) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2 - rect.left, y: r.top + r.height / 2 - rect.top }; };
  const edges = entry._wf.def.edges || [];
  const bezier = (p1, p2) => {
    const dx = Math.max(50, Math.abs(p2.x - p1.x) * 0.5);
    return `M ${p1.x},${p1.y} C ${p1.x + dx},${p1.y} ${p2.x - dx},${p2.y} ${p2.x},${p2.y}`;
  };
  // For THIS symmetric control-point layout, the cubic's t=0.5 point reduces
  // exactly to the segment midpoint (Change 7) -- no curve sampling needed.
  const midOf = (p1, p2) => ({ x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 });
  let html = '';
  // MC-871 Change 4a, corrected by 12a: implied trigger->root edges are a
  // VIEW over "no incoming edges" (mc/workflows.py:656) FOR NODES RON
  // EXPLICITLY WIRED (`def.trigger.entry`) -- never a stored edge, never
  // selectable/deletable like a real one (no onpointerdown, no delete ×).
  // The first cut of this drew a line to EVERY root, which made a fresh
  // standalone drop look auto-wired with no action taken; `entry` is the
  // fix. A name in `entry` that is no longer a root (gained an incoming
  // edge elsewhere) or no longer exists (deleted/renamed without repointing)
  // draws nothing -- see _wfDeleteNode/_wfSyncDomToModel for how `entry`
  // stays in sync with those.
  const triggerPortEl = vp.querySelector('.wfb-trigger-box .wfb-port-out');
  if (triggerPortEl) {
    const hasIncoming = new Set(edges.map(e => e.to));
    const wiredNames = _wfTriggerEntry(entry._wf.def);
    const nodesByName = new Map((entry._wf.def.nodes || []).map(n => [n.name, n]));
    for (const name of wiredNames) {
      const n = nodesByName.get(name);
      if (!n || hasIncoming.has(name)) continue;
      const toEl = vp.querySelector(`.wfb-port-in[data-node="${_wfAttrEsc(n.name)}"]`);
      // Deliberately NOT `.wfb-edge-path` -- that class is also how the
      // smoke suite and _wfEdgeClick's own selection counts REAL edges;
      // sharing it would silently inflate every edge-count assertion (and
      // every future one) the moment a graph has a root node, which is
      // always. `.wfb-edge-implied` is a fully standalone style, not a
      // modifier on `.wfb-edge-path`.
      if (toEl) html += `<path class="wfb-edge-implied" d="${bezier(ptOf(triggerPortEl), ptOf(toEl))}"></path>`;
    }
  }
  for (const e of edges) {
    const fromEl = vp.querySelector(`.wfb-port-out[data-node="${_wfAttrEsc(e.from)}"][data-when="${_wfAttrEsc(e.when || '')}"]`);
    const toEl = vp.querySelector(`.wfb-port-in[data-node="${_wfAttrEsc(e.to)}"]`);
    if (!fromEl || !toEl) continue;
    const p1 = ptOf(fromEl), p2 = ptOf(toEl);
    const mid = midOf(p1, p2);
    const selected = _wfSelectedEdge && _wfSelectedEdge.from === e.from && _wfSelectedEdge.to === e.to && (_wfSelectedEdge.when || null) === (e.when || null);
    // Change 7: a small × at the midpoint, quiet at rest (revealed on hover
    // of the edge or while selected -- see .wfb-edge-group in app.css) so a
    // dense graph doesn't turn into a field of ×s, but always reachable by
    // pointer alone since hovering the path itself reveals its own ×.
    html += `<g class="wfb-edge-group${selected ? ' wfb-edge-group-selected' : ''}">
      <path class="wfb-edge-path${selected ? ' wfb-edge-selected' : ''}" d="${bezier(p1, p2)}"
        onpointerdown="_wfEdgeClick(event,'${_wfJsStrEsc(e.from)}','${_wfJsStrEsc(e.to)}','${_wfJsStrEsc(e.when || '')}')"></path>
      <g class="wfb-edge-del" transform="translate(${mid.x},${mid.y})"
        onpointerdown="_wfEdgeDelClick(event,'${_wfJsStrEsc(e.from)}','${_wfJsStrEsc(e.to)}','${_wfJsStrEsc(e.when || '')}')">
        <circle class="wfb-edge-del-hit" r="10"></circle>
        <circle class="wfb-edge-del-bg" r="7"></circle>
        <text class="wfb-edge-del-glyph" x="0" y="1" text-anchor="middle" dominant-baseline="central">&#10005;</text>
      </g>
    </g>`;
  }
  if (_wfConnectDrag) {
    const fromEl = vp.querySelector(`.wfb-port-out[data-node="${_wfAttrEsc(_wfConnectDrag.fromNode)}"][data-when="${_wfAttrEsc(_wfConnectDrag.when || '')}"]`);
    if (fromEl) {
      const p2 = { x: _wfConnectDrag.curX - rect.left, y: _wfConnectDrag.curY - rect.top };
      html += `<path class="wfb-edge-temp" d="${bezier(ptOf(fromEl), p2)}"></path>`;
    }
  }
  svg.innerHTML = html;
}

// ── The port `+` popover: "After <label>, run…" ─────────────────────────────
//
// The 80% path (UI brief §4): every output port carries a `+` that offers what
// can come next — an Action, an Approval gate, or a PERSON — and auto-places
// and wires the pick. Drawing an arrow by hand is for REWIRING, never a
// requirement, so a whole pipeline can be built without one.
//
// Lives on <body>, not inside the modal: the modal body is an overflow:auto
// scroller and a popover anchored to a port near its edge would be clipped by
// it. Position is therefore fixed/screen-space, clamped into the viewport.

let _wfPortPopover = null; // { fromNode, when, search, anchorX, anchorY }

function _wfPortPlusClick(e, fromNode, when) {
  e.stopPropagation();
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  const already = _wfPortPopover
    && _wfPortPopover.fromNode === fromNode
    && (_wfPortPopover.when || '') === (when || '');
  const rect = e.currentTarget.getBoundingClientRect();
  _wfClosePortPopover();
  if (already) return; // a second click on the same + closes it
  _wfPortPopover = { fromNode, when: when || null, search: '', anchorX: rect.right, anchorY: rect.top };
  _wfRenderPortPopover();
}

function _wfPopoverPeopleHTML(st, search) {
  const people = _wfBenchFiltered(st, search);
  if (!people.length) return '<div class="wfb-popover-empty">No one matches.</div>';
  return people.map(b => `<div class="wfb-popover-person"
    onclick="_wfPopoverPickPerson('${_wfJsStrEsc(b.scope || 'global')}','${_wfJsStrEsc(b.name)}')">
    <span class="wfb-popover-avatar">${_wfAvatarHTML(b, 20)}</span>
    <span class="wfb-popover-person-name">${esc(b.display || b.name)}</span>
  </div>`).join('');
}

function _wfRenderPortPopover() {
  const p = _wfPortPopover; if (!p) return;
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  const box = document.createElement('div');
  box.className = 'wfb-port-popover';
  box.id = 'wfb-port-popover';
  box.innerHTML = `
    <div class="wfb-popover-title">${p.fromNode === '__trigger__' ? 'As the first step, run&hellip;' : `After <em>${esc(p.when || 'this step')}</em>, run&hellip;`}</div>
    <div class="wfb-popover-row" onclick="_wfPopoverPick('action')">
      <span class="wfb-popover-icon">&#9881;</span> Action <span class="wfb-popover-caret">&#9662;</span></div>
    <div class="wfb-popover-row" onclick="_wfPopoverPick('approval')">
      <span class="wfb-popover-icon">&#9995;</span> Approval gate</div>
    <div class="wfb-popover-people-title">People</div>
    <input class="wfb-popover-search" placeholder="Search bench&hellip;" value="${esc(p.search || '')}"
      oninput="_wfPopoverSearch(this.value)">
    <div class="wfb-popover-people">${_wfPopoverPeopleHTML(entry._wf, p.search)}</div>`;
  document.body.appendChild(box);
  // Clamp into the viewport AFTER layout, so the measured size is real.
  let left = p.anchorX + 8;
  let top = p.anchorY - 8;
  if (left + box.offsetWidth > window.innerWidth - 8) left = Math.max(8, window.innerWidth - box.offsetWidth - 8);
  if (top + box.offsetHeight > window.innerHeight - 8) top = Math.max(8, window.innerHeight - box.offsetHeight - 8);
  box.style.left = left + 'px';
  box.style.top = top + 'px';
  // Deferred: this handler is installed during a click that is still
  // propagating, and would otherwise close the popover it just opened.
  setTimeout(() => document.addEventListener('pointerdown', _wfPopoverOutsideDown, true), 0);
  const input = box.querySelector('.wfb-popover-search');
  if (input) input.focus();
}

function _wfClosePortPopover() {
  const el = document.getElementById('wfb-port-popover');
  if (el) el.remove();
  document.removeEventListener('pointerdown', _wfPopoverOutsideDown, true);
  _wfPortPopover = null;
}

function _wfPopoverOutsideDown(e) {
  const el = document.getElementById('wfb-port-popover');
  if (el && !el.contains(e.target)) _wfClosePortPopover();
}

// Only the people list is rebuilt, so the caret stays where it is as you type.
function _wfPopoverSearch(value) {
  if (!_wfPortPopover) return;
  _wfPortPopover.search = value;
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  const box = document.getElementById('wfb-port-popover');
  const list = box && box.querySelector('.wfb-popover-people');
  if (list) list.innerHTML = _wfPopoverPeopleHTML(entry._wf, value);
}

function _wfPopoverPick(type) {
  const p = _wfPortPopover; if (!p) return;
  const entry = _wfEntry(); if (!entry) return;
  _wfClosePortPopover();
  // Change 4a: the trigger's own "+" opens this SAME popover (_wfPortPlusClick
  // is called with fromNode='__trigger__' from _wfRenderTriggerBox) — a pick
  // from it places a ROOT, not a node wired via a (nonexistent) trigger edge.
  if (p.fromNode === '__trigger__') _wfInsertRootAfterTrigger(entry, type, null, null);
  else _wfInsertAfter(entry, p.fromNode, p.when, type, null, null);
}

function _wfPopoverPickPerson(scope, name) {
  const p = _wfPortPopover; if (!p) return;
  const entry = _wfEntry(); if (!entry) return;
  _wfClosePortPopover();
  if (p.fromNode === '__trigger__') _wfInsertRootAfterTrigger(entry, 'person', scope, name);
  else _wfInsertAfter(entry, p.fromNode, p.when, 'person', scope, name);
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _wfPortPopover) _wfClosePortPopover();
  if (e.key === 'Escape' && _wfNodeMenuOpenFor) _wfCloseNodeMenu();
});

// ── Node overflow menu (the header's "…", UI brief mockup) — one action for
// now (Delete step), same body-appended/outside-click-closes shape as the
// port "+" popover above. ────────────────────────────────────────────────────
let _wfNodeMenuOpenFor = null; // node name string | null

function _wfNodeMenuToggle(e, name) {
  e.stopPropagation();
  if (_wfNodeMenuOpenFor === name) { _wfCloseNodeMenu(); return; }
  _wfCloseNodeMenu();
  _wfNodeMenuOpenFor = name;
  const btn = e.currentTarget;
  const r = btn.getBoundingClientRect();
  const box = document.createElement('div');
  box.className = 'wfb-node-menu';
  box.id = 'wfb-node-menu';
  // Change 6b: Duplicate + Disconnect join the previously-only Delete step.
  // All three funnel through the same mutators every other structural
  // action uses (_wfMarkDirty + _wfRender), so all three are undoable for
  // free via the Change 3 checkpoint hook -- nothing extra needed here.
  box.innerHTML = `
    <div class="wfb-node-menu-item" onclick="_wfDuplicateNode('${_wfJsStrEsc(name)}');_wfCloseNodeMenu()">Duplicate</div>
    <div class="wfb-node-menu-item" onclick="_wfDisconnectNode('${_wfJsStrEsc(name)}');_wfCloseNodeMenu()">Disconnect</div>
    <div class="wfb-node-menu-sep"></div>
    <div class="wfb-node-menu-delete" onclick="_wfDeleteNode('${_wfJsStrEsc(name)}');_wfCloseNodeMenu()">Delete step</div>`;
  document.body.appendChild(box);
  let left = r.right - box.offsetWidth;
  if (left < 8) left = 8;
  let top = r.bottom + 4;
  box.style.left = left + 'px';
  box.style.top = top + 'px';
  // Deferred for the same reason the port popover's listener is: this click
  // is still propagating and would otherwise close what it just opened.
  setTimeout(() => document.addEventListener('pointerdown', _wfNodeMenuOutsideDown, true), 0);
}

function _wfCloseNodeMenu() {
  const el = document.getElementById('wfb-node-menu');
  if (el) el.remove();
  document.removeEventListener('pointerdown', _wfNodeMenuOutsideDown, true);
  _wfNodeMenuOpenFor = null;
}

function _wfNodeMenuOutsideDown(e) {
  const el = document.getElementById('wfb-node-menu');
  if (el && !el.contains(e.target)) _wfCloseNodeMenu();
}

// ── Save ──────────────────────────────────────────────────────────────────────

async function _wfSave() {
  const entry = _wfEntry(); if (!entry) return;
  _wfSyncDomToModel(entry);
  const st = entry._wf;
  const def = st.def;
  if (!(def.name || '').trim()) { showToast('Name the workflow before saving.', 4000); return; }
  const nodes = def.nodes || [];
  if (!nodes.length) { showToast('Drag at least one block onto the canvas before saving.', 4000); return; }
  const names = nodes.map(n => n.name);
  const dupe = names.find((n, i) => names.indexOf(n) !== i);
  if (dupe) { showToast(`Duplicate step name "${dupe}" — names must be unique.`, 5000); return; }
  if (def.trigger && def.trigger.type === 'schedule' && st.linkedSchedule &&
      st.linkedSchedule.schedule_type === 'weekly' && !(st.linkedSchedule.days || []).length) {
    showToast('Pick at least one day for a weekly schedule.', 4000); return;
  }
  // Step 6, "the same validator that guards save": mirrors the checks
  // mc/workflows.py::validate_workflow already hard-enforces (missing
  // prompt/project, no options, cycles, broken slot scope) so a violation
  // reads inline on its card before ever reaching the network, not only as a
  // round-tripped 400. The server re-validates regardless (defence in depth).
  const problems = _wfValidateGraph(def);
  if (problems.length) {
    _wfApplyRunErrors(st, problems);
    showToast(problems[0].message, 5000);
    return;
  }

  st.saving = true; st.error = null;
  _wfRender();
  const body = { name: def.name, description: def.description, enabled: def.enabled !== false, trigger: def.trigger, nodes: def.nodes, edges: def.edges || [] };
  try {
    const url = st.workflowId ? `${API_BASE}/api/workflows/${st.workflowId}` : `${API_BASE}/api/workflows`;
    const method = st.workflowId ? 'PUT' : 'POST';
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
    st.workflowId = data.workflow.id;
    st.def = JSON.parse(JSON.stringify(data.workflow));
    await _wfSaveLinkedSchedule(st);
    st.saving = false;
    st.dirty = false;
    st.savedAt = Date.now();
    st.runErrors = null;
    showToast('Workflow saved', 3000);
    _wfRender();
    if (window.refreshWorkflowsList) window.refreshWorkflowsList();
  } catch (e) {
    st.saving = false;
    st.error = 'Save failed: ' + e.message;
    // mc/workflows.py's error strings quote the offending node name in single
    // quotes ("agent step 'draft' missing prompt"). Best-effort: if a node
    // name from this graph appears quoted in the message, also surface it
    // inline on that card (defence in depth for structural checks
    // _wfValidateGraph doesn't mirror, e.g. a hand-edited store file) --
    // _wfApplyRunErrors renders on its own, so skip the redundant call below.
    const names = (def.nodes || []).map(n => n.name);
    const hit = names.find(n => e.message.includes(`'${n}'`));
    if (hit) _wfApplyRunErrors(st, [{ node: hit, message: e.message }]);
    else _wfRender();
  }
}

// The authoring face of MC-871 Q4: "choosing 'on a schedule' creates or edits
// the linked schedule record through the EXISTING CRUD. One store, two
// views." Runs AFTER the workflow itself is saved, so a brand-new workflow
// has a real id to point workflow_id at. Reverting to Manual deletes the
// linked schedule outright -- a disabled-but-lingering row pointing at a
// workflow whose author no longer wants a cadence is exactly the dangling
// state the deleted-workflow guard on the OTHER side exists to avoid.
async function _wfSaveLinkedSchedule(st) {
  const isSchedule = st.def.trigger && st.def.trigger.type === 'schedule';
  if (!isSchedule) {
    if (st.linkedSchedule && st.linkedSchedule.id) {
      try { await fetch(`${API_BASE}/api/schedules/${encodeURIComponent(st.linkedSchedule.id)}`, { method: 'DELETE' }); } catch (e) {}
      st.linkedSchedule = null;
    }
    return;
  }
  const s = st.linkedSchedule || _wfDraftSchedule();
  const body = {
    workflow_id: st.workflowId,
    task: '', project_id: '',
    enabled: s.enabled !== false,
    schedule_type: s.schedule_type || 'daily',
    time: s.time, days: s.days, interval_minutes: s.interval_minutes,
    run_at: s.run_at, cron_expr: s.cron_expr,
  };
  try {
    const url = s.id ? `${API_BASE}/api/schedules/${encodeURIComponent(s.id)}` : `${API_BASE}/api/schedules`;
    const method = s.id ? 'PUT' : 'POST';
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    st.linkedSchedule = data;
  } catch (e) {
    showToast('Workflow saved, but the schedule failed: ' + e.message, 6000);
  }
}

// ── Step 6: dirty state, Run-now inline errors, saved stamp ──────────────────
//
// No autosave timer. The store (mc/workflows.py) has no updated_at/version
// field on a workflow record -- confirmed by reading create_workflow/
// update_workflow, neither stamps one -- so there is no cheap way to detect
// "another session already changed this since I loaded it" before an
// autosave overwrites it. The brief's own escape hatch: implement dirty-
// state + stamp + on-close warning and SAY the timer was left out rather
// than ship a save that can silently clobber a concurrent edit.

// Required fields per verb (mirrors validate_workflow's per-action checks,
// mc/workflows.py lines ~408-410 dispatch into per-verb requirements the
// route layer enforces on write). desk_harvest has none -- project_id is
// optional there ("blank = every project").
const _WF_ACTION_REQUIRED = {
  backlog_create: ['project_id', 'text'],
  backlog_patch: ['project_id', 'item_id'],
  desk_harvest: [],
};

// Client mirror of mc/workflows.py::validate_workflow, scoped to what the
// canvas can point a red outline at (brief §7: Run-now "selects and scrolls
// to the offending card"). NOT a full reimplementation -- name/duplicate-name/
// empty-graph/weekly-day checks stay in _wfSave as whole-form concerns with no
// single card to blame, and the server re-validates everything regardless on
// both save and run (R2-D3/D5 defence in depth). Reuses the exact toposort/
// dominator/broken-slot helpers the save guard already computes, so this can
// never disagree with what actually gets refused.
function _wfValidateGraph(def) {
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const names = nodes.map(n => n.name);
  const problems = []; // [{node, message}]
  const add = (name, message) => problems.push({ node: name, message });

  nodes.forEach((node) => {
    if (node.type === 'agent') {
      if (!node.project_id) add(node.name, 'Needs a project.');
      if (!(node.prompt || '').trim()) add(node.name, 'Needs a prompt.');
      const outcomes = node.outcomes || [];
      if (outcomes.includes('otherwise')) add(node.name, '"otherwise" is reserved, not a usable outcome label.');
      if (new Set(outcomes).size !== outcomes.length) add(node.name, 'Outcome labels must be unique.');
    } else if (node.type === 'approval') {
      const options = node.options || [];
      if (!options.length) add(node.name, 'Needs at least one option.');
      if (options.includes('otherwise')) add(node.name, '"otherwise" is reserved, not a usable option label.');
      if (new Set(options).size !== options.length) add(node.name, 'Option labels must be unique.');
    } else if (node.type === 'action') {
      const required = _WF_ACTION_REQUIRED[node.action] || [];
      const cfg = node.config || {};
      const missing = required.filter(k => !String(cfg[k] || '').trim());
      if (missing.length) add(node.name, `Needs ${missing.join(', ')}.`);
    }
  });

  const order = _wfToposort(names, edges);
  if (order.length !== names.length) {
    names.filter(n => !order.includes(n)).forEach(n => add(n, "Part of a cycle -- this step can never run."));
  }

  _wfFindBrokenSlotRefs(nodes, edges).forEach(({ step, ref }) => {
    add(step, `Uses {{steps.${ref}.*}}, but "${ref}" isn't guaranteed to have run first.`);
  });

  return problems;
}

// One message per node (first problem wins -- the card shows one banner, not
// a list); pans the canvas to the first offender so Save/Run-now never leaves
// the fix off-screen (brief §7: "never a modal alert").
function _wfApplyRunErrors(st, problems) {
  const map = {};
  problems.forEach((p) => { if (!map[p.node]) map[p.node] = p.message; });
  st.runErrors = map;
  st.scrollToNode = problems[0].node;
  _wfRender();
}

async function _wfRunNow() {
  const entry = _wfEntry(); if (!entry) return;
  _wfSyncDomToModel(entry);
  const st = entry._wf;
  if (!st.workflowId) { showToast('Save the workflow before running it.', 4000); return; }
  const problems = _wfValidateGraph(st.def);
  if (problems.length) {
    _wfApplyRunErrors(st, problems);
    showToast(problems[0].message, 5000);
    return;
  }
  // Re-render if this clears a PREVIOUS failed attempt's red outline -- a
  // validate-clean retry must not leave a stale error card on screen while
  // the fetch is in flight.
  if (st.runErrors) { st.runErrors = null; _wfRender(); }
  try {
    const res = await fetch(`${API_BASE}/api/workflows/${encodeURIComponent(st.workflowId)}/run`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      const msg = data.error || ('HTTP ' + res.status);
      const names = (st.def.nodes || []).map(n => n.name);
      const hit = names.find(n => msg.includes(`'${n}'`));
      if (hit) _wfApplyRunErrors(st, [{ node: hit, message: msg }]);
      showToast('Run failed: ' + msg, 6000);
      return;
    }
    showToast('Run started.', 3000);
  } catch (e) {
    showToast('Run failed: ' + e.message, 6000);
  }
}

// Marks the graph dirty the instant anything changes -- either the delegated
// #wfb-body input/change listener (typing, dropdowns) or an explicit call
// from a structural mutator whose action isn't a native input/change event
// (a button click, a pointer drag). Idempotent and cheap enough to call from
// both: it only touches the DOM directly (never a full _wfRender()) so a
// keystroke marking the graph dirty can never clobber the field being typed
// into, matching this file's existing FIELD SYNC discipline.
function _wfMarkDirty() {
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  const st = entry._wf;
  if (st.dirty) return;
  st.dirty = true;
  _wfPaintSaveStamp(st);
}

function _wfPaintSaveStamp(st) {
  const el = document.getElementById('wfb-save-stamp');
  if (!el) return;
  el.textContent = st.dirty ? 'Unsaved changes' : _wfRelativeSavedLabel(st.savedAt);
  el.classList.toggle('wfb-save-stamp-dirty', !!st.dirty);
}

function _wfRelativeSavedLabel(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 5) return 'saved just now';
  if (s < 60) return `saved ${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `saved ${m}m ago`;
  return `saved ${Math.round(m / 60)}h ago`;
}

// ── Interop: window accessors for onclick/onpointerdown targets + cross-
//    module entry points (static/js/scheduler.js:626-650 is the pattern this
//    file follows — every top-level declaration here is module-scoped).
window.openWorkflowBuilder = openWorkflowBuilder;
window._wfEntry = _wfEntry; // test/debug introspection of the mounted singleton
window._wfSetTriggerType = _wfSetTriggerType;
window._wfPaletteDown = _wfPaletteDown;
window._wfPaletteSearch = _wfPaletteSearch;
window._wfHireSomeone = _wfHireSomeone;
window._wfPortPlusClick = _wfPortPlusClick;
window._wfPopoverPick = _wfPopoverPick;
window._wfPopoverPickPerson = _wfPopoverPickPerson;
window._wfPopoverSearch = _wfPopoverSearch;
window._wfViewportDown = _wfViewportDown;
window._wfNodeDragDown = _wfNodeDragDown;
window._wfPortDown = _wfPortDown;
window._wfEdgeClick = _wfEdgeClick;
window._wfDeleteNode = _wfDeleteNode;
window._wfNodeMenuToggle = _wfNodeMenuToggle;
window._wfCloseNodeMenu = _wfCloseNodeMenu;
window._wfAddOutcome = _wfAddOutcome;
window._wfRemoveOutcome = _wfRemoveOutcome;
window._wfAddOption = _wfAddOption;
window._wfRemoveOption = _wfRemoveOption;
window._wfReloadCharacters = _wfReloadCharacters;
window._wfRerenderActionFields = _wfRerenderActionFields;
window._wfInsertSlot = _wfInsertSlot;
window._wfSave = _wfSave;
window._wfRunNow = _wfRunNow;
window._wfMarkDirty = _wfMarkDirty;
window._wfSetSchedType = _wfSetSchedType;
window._wfToggleSchedEnabled = _wfToggleSchedEnabled;
