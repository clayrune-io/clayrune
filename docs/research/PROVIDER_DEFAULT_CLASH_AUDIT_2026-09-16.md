# Provider-default clashes and install-first onboarding

Date: 2026-09-16. Baseline: `9ec4812`.

## Verdict and limits

Clayrune supports several chat runtimes, but its platform services are not yet
provider-neutral. Selecting Codex changes ordinary chat routing; it does not
change every AI call made by Clayrune. On a Codex-only machine, some services
degrade or fail. With Claude also authenticated, some work can still use Claude
despite the selected default.

This is a source audit, not a clean-machine execution matrix or certification of
every provider. No paid model calls, login changes, settings changes, or source
fixes were made for this audit. References are file/function anchors; line numbers
are approximate and can drift. Existing uncommitted continuation-model changes
in `agent_routes.py` are not counted as shipped. Findings concern Clayrune's
adapters, not claims about what vendors themselves can support.

## Clash inventory

| Area | Current behavior and consequence | Source anchor |
|---|---|---|
| Unconfigured default | Empty provider resolves to Claude, rather than an explicit not-configured state. A missing choice becomes a vendor choice. | `server.py` defaults, ~90; `mc/agent_runtime.py::default_runtime_name`, ~904 |
| Default model settings | Global `agent_model` starts at `claude-opus-5`; Agent settings offers Claude models. Changing provider does not atomically choose a model for that provider. Normal non-Claude dispatch filters incompatible inherited models and then lets the CLI choose: protection from invalid models, but not a unified preference system. | `static/js/settings-drill.js`, ~283; `mc/blueprints/settings_routes.py`; `agent_routes.py::_resolve_runtime_model` |
| Floating Claydo / workshop | Both guide stream and legacy ask directly launch Claude. Selecting an engine for the agent being created does not select the workshop's own engine. No selected-provider/model resolution at this executor. | `mc/blueprints/guide_routes.py`, ~374, ~542; `static/js/claydo.js` |
| Hireable Claydo | Important distinction: the shipped base-agent definition has **no provider or model pin**. It uses ordinary dispatch inheritance. The helper is the hardcoded path, not this definition. | `data/agents/builtin/claydo.md`; `agent_routes.py::_dispatch_agent_internal` |
| Agent voice, name and face generation | Chooses the character's model or global model, then sends it to the Claude-only summarizer. The character's provider is not selected alongside its model. A Codex model name can therefore be handed to Claude. Several endpoints have fallback/error behavior, not equivalent service. | `mc/blueprints/character_routes.py`, ~510, ~605, ~624, ~824, ~955 |
| Project profile generation | Description/emoji generation directly launches Claude using the condense model/Haiku default. | `mc/blueprints/project_routes.py`, ~619–648 |
| Scribe and continuity summaries | Transcript input is now provider-aware, including Codex rendering/log fallback. The model doing the summarization is still Claude. Reading a Codex conversation is not the same as summarizing with Codex. | `mc/memory.py::_scribe_call`, ~3715; Scribe extraction ~3810 |
| Memory condensing | Structured condense uses the Claude-only helper; legacy agent condense directly launches Claude with a Write-capable task. Both need separate migration contracts. | `mc/memory.py`, ~4227, ~4544 |
| Learned skills / Distiller | Extraction, artifact generation and reframing all call the same Claude-only helper; default model is Haiku. | `mc/distiller.py`, ~893, ~1814–1917; `server.py` defaults |
| Topic synthesis and sweeps | Uses `_scribe_call` and a Haiku default, independent of selected chat provider. | `mc/blueprints/topics_routes.py`, ~38, ~190, ~467 |
| Mail laundering | Deliberate Claude-only security boundary: untrusted mail is processed without agent tools. Must remain unavailable rather than silently use an unsafe replacement. | `mc/mail_launder.py`, ~183; `mc/agent_runtime.py::claude_oneshot_available` |
| Beacon briefing | Its briefing model call explicitly uses ClaudeRuntime.oneshot. This is a separate consumer outside ordinary agent dispatch. | `beacon/briefer.py`, ~130 |
| Automatic model routing | Classifier uses the Claude helper and routes within Claude model tiers. It is not a cross-provider role-to-model resolver. | `agent_routes.py::_AUTO_MODEL_VALID`, classifier ~5796; `server.py` auto-model defaults |
| Hivemind coordinator | Orchestrator directly launches Claude; creation defaults both orchestrator and worker model to Sonnet. | `mc/blueprints/hivemind_routes.py`, ~514–515, ~1249 |
| Hivemind workers | Provider follows project/global choice, but model comes from workstream/manifest/global without the normal chat model filter. Unknown provider explicitly falls back to Claude. Thus worker and coordinator behavior differ. | `hivemind_routes.py::_hm_spawn_worker`, ~936–1008 |
| MCP installation assistance | README configuration extraction fallback and security scan directly invoke Claude. Deterministic extraction can work without it; AI-assisted paths cannot promise selected-provider behavior. These direct calls also need a tool-isolation review. | `mc/mcp_installer.py::_extract_via_claude`, ~414; `security_scan`, ~754 |
| MCP configuration distribution | Management writes Claude-format global/project configuration. Gemini has a translation/sync bridge. Codex advertises native MCP support, but that is not a bridge from Clayrune-managed MCP configuration. Qwen currently declares MCP unsupported by its adapter. | `mc/mcp.py`, ~42, ~651 onward; `mc/agent_runtime.py` capabilities/dispatch |
| Skills and plugin loadout | Skills live in Claude directories, but Clayrune does inject a readable catalog for non-Claude project providers. Its guard checks `project.provider or 'claude'`, not the resolved conversation provider: inherited-global or per-chat/character overrides can miss the intended catalog. Commands, subagent definitions and plugin installation remain Claude-format surfaces. | `agent_routes.py::_skills_catalog_block`, ~2784 and `_dispatch_via_runtime`; `mc/skills.py`, ~43, ~92, plugin install functions |
| Transcript-derived discovery | Conversation search/index helpers still choose Claude. Agent-written Markdown discovery and native subagent discovery do too. These are separate from the recently repaired multi-provider chat rail. | `mc/memory_fts.py`, ~129, ~192; `mc/memory.py` transcript helpers; `agent_routes.py`, ~8659, ~10352 |
| Environment / usage status | Active system-status refresh explicitly starts Claude to inspect its init/tool/rate-limit data. Claude subscription windows and transcript-derived totals are not universal account health. | `mc/blueprints/system_routes.py::system_status_refresh`, ~704; preceding usage response |
| Continuation identity | Provider-aware resume/rail work has landed, but model persistence has additional local work not yet shipped. Changing a default must never silently reselect the model of an existing conversation. | `agent_routes.py` revive/resume/followup; local continuation-model diff, excluded from shipped claims |
| Capability and unattended safety parity | Claude uses persistent Mode B and PreToolUse hooks; Codex uses a different runtime and unattended workspace sandbox. Skill/plan/cost/turn telemetry capability flags differ. These cannot be made equivalent merely by routing the same prompt. | `mc/agent_runtime.py` provider capabilities; `codex_unattended_sandbox_decision`; `steward/fence.py` |
| Installer provider gate | Windows and shell installers choose/install a provider before core installation. Claude auth is a prerequisite; other selected providers defer sign-in to UI. The installer offers four providers while UI uses the runtime registry. | `installer/install.ps1`, ~557–781; `installer/install.sh`, ~88–522 |
| Installer login recovery | Windows EXE retry/login action is explicitly `claude /login`, regardless of the selected platform. Windows bounded local auth-status fix is not present in the shell installer's `claude -p "ok"` probe. | `installer/win-exe/ClayruneInstaller.cs::DoLogin`, ~263; `installer/install.sh::_check_claude_auth`, ~334 |
| In-app onboarding | Provider choice already exists, especially for packaged Mac installs. It is an optional tour step, skips when a default exists, visually defaults to Claude, and launches CLI installation into an external terminal followed by manual Refresh. It is not a durable setup/readiness workflow. | `static/js/walkthrough.js`, ~15, ~169, ~204; onboarding launch in `static/index.html` |

### What is already shared and should be preserved

Ordinary dispatch resolves explicit chat choice, character pin, project default,
then global default. Its non-Claude branch raises on failure rather than secretly
retrying with Claude. Scheduler resume recovers the owning provider; workflow
agent steps call the shared dispatcher (`mc/workflows.py`, ~1004). These are not
independent Claude-only engines, although they inherit dispatcher and helper
limitations. Explicit user-created character/provider pins are intentional
overrides, not defects to erase.

Likewise, `.claude` in a storage path is not proof that a feature fails on Codex:
shared prompt injection and provider-aware transcript adapters already bridge
some paths. The audit distinguishes configuration storage from actual execution.

## Why a simple substitution is unsafe

`claude_oneshot_available()` explicitly documents that only the current Claude
oneshot has a verified no-tools contract. The current Codex `oneshot()` launches
`exec --dangerously-bypass-approvals-and-sandbox` (`agent_runtime.py`, ~5894).
Pointing mail laundering, untrusted-document summaries or background extraction
at that method would remove a security boundary. This is an adapter defect/gap to
solve, not a reason to force every user to install Claude.

Introduce explicit operation requirements: tool-free generation, toolful work,
streaming, resumability, structured output, image input and unattended safety.
Select an adapter only when it satisfies the requirement. Unsupported means a
clear unavailable reason, never a silent different provider or weaker sandbox.

## Recommended experience: install first, connect in Clayrune

1. Install Clayrune and its own runtime dependencies. No AI CLI/account/model
   request is required to open the application.
2. A deterministic setup screen asks which provider to use. Detect existing
   installations and accounts, but do not equate detection with user consent.
   Allow explicit "set up later" with non-AI UI still usable.
3. Install missing provider dependencies with owned, registered processes,
   visible progress, bounded failure handling, retry and cancellation. Run the
   provider's supported interactive/browser sign-in flow; show device codes or
   terminal interaction when required. Do not promise every vendor can log in
   through an embedded form.
4. Verify authentication separately from CLI presence and model availability.
   Prefer non-billable checks; identify any explicit connectivity test that may
   incur usage. Reuse already-authenticated installations without another login.
5. Save provider and provider-specific model/effort preferences together. Show
   what Claydo and background work will use, plus unavailable capabilities.
   Start AI background work only when a valid configured executor exists.
6. Launch the optional product tour after setup. Store setup state server-side,
   separately from browser-local tour dismissal. Interrupted setup resumes.

This should unify Windows, shell and packaged Mac behavior. CLI installers can
retain optional unattended provider arguments that seed the same setup state.
Existing users keep their choices; no forced new popup on every update. Earlier
code intentionally avoided surprising existing users with provider questions;
this proposal preserves that while moving **new-install** setup into the app.

The bootstrap is already deterministic, not AI-driven. The UI also already has
provider inventory, installation and login endpoints. This is consolidation and
hardening of existing pieces, not a need to build an AI-powered installer.

## Resolution contract to implement

- A provider/model/effort is a coherent engine tuple, never independently mixed
  strings. Keep provider-specific preferences, not one Claude-shaped global model.
- New unpinned work inherits the relevant project/global default. Built-in Claydo
  is unpinned. User-explicit overrides remain visible and win for new work.
- Every feature calls one resolver/executor with operation requirements and an
  optional explicit override. No feature embeds Claude/Haiku/Sonnet as fallback.
- Existing chats retain their actual engine snapshot, including across restart.
  A global default change affects future unpinned work, not old conversations.
  Explicit model changes are deliberate; cross-vendor continuation must not
  pretend native session IDs are interchangeable.
- Background jobs use the selected provider too, with suitable provider-specific
  lightweight model preferences. Cross-provider fallback requires explicit opt-in
  and disclosure; quota failure alone must not spend another account silently.
- UI shows the **effective engine and why**, not just a provider selector whose
  value some downstream feature ignores.

## Delivery order and acceptance gates

### Confirmed scope

The scope includes overall safety hardening across all supported
providers, not just Claude sessions. Equivalent safety outcomes are required
even where enforcement mechanisms differ. Cover interactive and unattended
execution, tool-free background processing, untrusted input, project/session
boundaries and explicit provider/model ownership. Each provider/operation pair
must pass its safety tests before being enabled; unsupported enforcement must
fail closed with an actionable explanation, not silently relax permissions or
switch providers. A provider-neutral selector alone does not satisfy this gate.

The provider-agnostic correction includes both conversation continuity and the
remaining Scribe/memory dependencies, not only onboarding and new-chat routing.
Conversation coverage must include parallel sessions, rail visibility, history,
resume/restart and stable provider/model identity. Memory coverage must include
provider-owned transcript ingestion, safe selected-provider summarization,
durable shared storage, retrieval and condensing. A non-Claude-only installation
must be tested end-to-end: conversation -> stored memory -> retrieval by another
agent. Unsupported safe execution must be visible, never silently substituted
with Claude. This confirms scope; it does not mark implementation complete.

1. Engine-resolution/readiness contract, durable setup and installer decoupling;
   preserve explicit overrides and conversation model continuity.
2. Claydo/workshop and generated identity/profile features through that contract.
3. Verified tool-free executors, then Scribe/condense/Distiller/topics/mail/Beacon
   and MCP assistance. Keep security-sensitive features fail-closed meanwhile.
4. Hivemind, MCP distribution, skill/plugin delivery, search/discovery and accurate
   per-provider telemetry/capability UI.

Required matrix: zero providers; Claude-only; Codex-only; Gemini-only; Qwen-only;
multiple installed with non-Claude default; CLI installed but logged out; missing
prerequisite; quota exhausted; interrupted setup; existing-install upgrade.
Run supported Windows/macOS/Linux installation variants rather than assuming one
script fix covers all. Other registered runtimes need explicit capability tests
before claiming the same support tier.

Key assertions: no Claude process or Claude login prompt on a Codex-only chosen
flow; two parallel chats remain separately visible/resumable; model survives
restart; changing defaults does not mutate old chats; every helper reports its
actual engine; no foreign model ID reaches a runtime; selected MCP/skills arrive
at the intended conversation; unsafe oneshot adapters are refused; no silent
paid cross-provider fallback; setup works without Claydo or any model call.

This proposal does not address installer signing/antivirus reputation by itself.
Nor does moving setup into the UI remove the need for reliable asset loading and
server-version handoff: the earlier MIME and stale-server incidents are separate
installation acceptance gates.
