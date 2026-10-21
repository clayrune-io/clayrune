# Settings ownership audit and Floor additions

Date: 2026-10-21. Status: implemented on the feature branch; activation and
live-provider execution are not claimed. Source baseline: `b4d309ca`.

## Verdict

Retire the global Settings > Agent category. Its controls have several different
owners; keeping them together encourages one provider's model or integration to
look like an installation-wide agent default. Preserve stored preferences and
move each editor to the feature that owns it.

The Floor gains a **Floor defaults** action for global agent identity, working
policy and reply preferences, plus links to Providers, Scheduler and unattended
safety. The Bench already edits each hired type's identity/instructions/engine;
that editor remains authoritative. A figure's local label remains distinct from
the default identity. These are not grounds for duplicating model catalogs or
moving machine, memory and security configuration into every agent card.

## Full static row census

Every statically labeled row in the prior settings renderer is accounted for
below. Conditional image-background rows are included. Dynamic sections and
feature-visibility switches follow separately.

| Previous location | Control | Owner after this change | Disposition |
| --- | --- | --- | --- |
| Agent > Identity | User Name | Your profile | Move; user identity is separate from an agent. |
| Agent > Identity | Agent Name | Floor defaults > Identity | Move; preserve the global value and local figure/type identities. |
| Agent > Identity | Agent Face | Floor defaults > Identity | Move; preview/edit a draft and save explicitly. |
| Agent > Model | Model | Providers > Model defaults | Replace the single global model with one preference per provider. |
| Agent > Model | Effort | Providers > Model defaults | Replace the single global effort list with adapter-owned choices. |
| Agent > Behavior | Max turns | Floor defaults > Working defaults | Move; keep the current policy and human write gate. |
| Agent > Behavior | Permissions | Floor defaults > Working defaults | Move; keep the current policy and human write gate. |
| Agent > Behavior | Sandbox unattended Codex runs | System > Security | Move; retain passcode and unattended-agent refusal. |
| Agent > Behavior | Brief replies | Floor defaults > Replies | Move; save the existing two flags atomically. |
| Agent > Behavior | Agents may use emojis | Floor defaults > Replies | Move; type overrides remain effective. |
| Agent > Behavior | Sticky settings | System > Runtime and availability | Move; this controls how settings are applied at runtime. |
| Agent > Behavior | Live activity states | Appearance > Interface | Move; this changes status presentation. |
| Agent > Behavior | Keep machine awake | System > Runtime and availability | Move; this is a machine-level wake lock. |
| Agent > Behavior | Pause all scheduled runs | Scheduler | Remove the duplicate editor; the existing master switch remains there. |
| Agent > Integration | Streaming (Mode B) | Providers > Advanced > CLI integration | Move; retain global and project process-lifetime preferences. |
| Agent > Integration | Remote control EXPERIMENTAL | Providers > Advanced > CLI integration | Move; retain the existing native integration and its limitation notice. |
| Agent > Integration | Channels | Providers > Advanced > CLI integration | Move; keep native CLI channel configuration separate from ordinary agent behavior. |
| Memory > Auto-condense | Auto-condense | Memory > Auto-condense | Keep; ownership remains appropriate. |
| Memory > Auto-condense | Threshold (KB) | Memory > Auto-condense | Keep; ownership remains appropriate. |
| Memory > Tuning | Model | Providers > Background models | Remove the unscoped housekeeping model editor; retain condense role overrides per provider. |
| Memory > Tuning | Executor | Memory > Tuning | Keep; ownership remains appropriate. |
| Memory > Retrieval | Memory snippets per task | Memory > Retrieval | Keep; ownership remains appropriate. |
| Memory > Retrieval | Follow linked notes | Memory > Retrieval | Keep; ownership remains appropriate. |
| Memory > Retrieval | Mint notes at close | Memory > Retrieval | Keep; ownership remains appropriate. |
| Memory > Retrieval | Declined-ideas ledger | Memory > Retrieval | Keep; ownership remains appropriate. |
| Memory > Learning | Exploration readback | Memory > Learning | Keep; ownership remains appropriate. |
| Memory > Learning | Explorations per session | Memory > Learning | Keep; ownership remains appropriate. |
| Appearance > Theme & display | Theme | Appearance > Theme & display | Keep; ownership remains appropriate. |
| Appearance > Theme & display | Accent color | Appearance > Theme & display | Keep; ownership remains appropriate. |
| Appearance > Theme & display | Density | Appearance > Theme & display | Keep; ownership remains appropriate. |
| Appearance > Theme & display | Writing style | Appearance > Theme & display | Keep; ownership remains appropriate. |
| Appearance > Theme & display | Open surfaces as | Appearance > Theme & display | Keep; ownership remains appropriate. |
| Appearance > Theme & display | Agent replies | Appearance > Theme & display | Keep; ownership remains appropriate. |
| Appearance > Theme & display | Show usage bar | Appearance > Theme & display | Keep; ownership remains appropriate. |
| Appearance > Background | Dashboard background | Appearance > Background | Keep; ownership remains appropriate. |
| Appearance > Background | Pick a color | Appearance > Background | Keep; ownership remains appropriate. |
| Appearance > Background | Gallery | Appearance > Background | Keep; ownership remains appropriate. |
| Appearance > Background | Your image | Appearance > Background | Keep; ownership remains appropriate. |
| Appearance > Background | Dim for readability | Appearance > Background | Keep; ownership remains appropriate. |
| Appearance > Background | Crop & framing | Appearance > Background | Keep; ownership remains appropriate. |
| Appearance > Interface | Enter Key Behavior | Appearance > Interface | Keep; ownership remains appropriate. |
| Appearance > Experimental | Desk v1 | System > Runtime and availability | Move the product rollout switch; it is not a theme setting. |
| Appearance > Experimental | Your timezone | Your profile | Move this user preference out of the experimental theme section. |
| System > Paths & Server | Projects Base | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Paths & Server | Auto Workspace Base | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Paths & Server | Shared Rules Path | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Paths & Server | Port | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Paths & Server | Delegation payload warning | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Paths & Server | Backup Destination | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Paths & Server | Scheduled backup | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Paths & Server | Keep | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Paths & Server | Status | System > Paths & Server | Keep; ownership remains appropriate. |
| System > Security | Vault idle auto-lock | System > Security | Keep; ownership remains appropriate. |
| System > Security | Keep vault unlocked on restart | System > Security | Keep; ownership remains appropriate. |
| System > Server | Power | System > Server | Keep; ownership remains appropriate. |
| System > Server | Update Clayrune | System > Server | Keep; ownership remains appropriate. |
| System > Help | Interface Tour | System > Help | Keep; ownership remains appropriate. |
| System > Help | First-run setup | System > Help | Keep; ownership remains appropriate. |

## Dynamic and custom sections

| Section or item | Disposition and reason |
| --- | --- |
| Settings profile card | Opens Your profile (name/timezone); it no longer opens the retired Agent category. |
| Providers inventory and default-provider radio | Keep in Providers > Connections; show registered providers, setup state and the chosen global provider. No synthetic installed-Claude row on inventory failure. |
| Install selected; install/update/sign-in; API-key entry; account/status checks; allowance recheck | Keep with each provider connection. Existing approval, credential and installation policy is unchanged. |
| Keep agent CLIs up to date | Keep in Providers > Connections; this manages provider software. |
| Engine fallback order; add/move/remove vendor | Move from Agent > Advanced to Providers > Advanced. Preserve opt-in behavior and the blocked-run deep link. |
| Auto task routing | Separate control in Providers > Advanced, explicitly labeled Claude task routing. It is not tier tracking and is never silently enabled for another provider. |
| Condense executor | Keep in Memory. The legacy agent executor's provider ownership stays explicit; moving model preferences does not certify or replace its execution profile. |
| Token usage counter visibility | Move to Appearance > Feature visibility; preserve the existing beginner/power preset and saved flags. This changes display, not model behavior. |
| Tool call lines visibility | Move to Appearance > Feature visibility; preserve the preset and values, with no execution or model changes. |
| GitHub sync badges visibility | Move to Appearance > Feature visibility; repo/account setup stays in GitHub Sync. |
| Agent Log tab visibility | Move the visibility preference to Appearance > Feature visibility. The log remains project history; a live Floor does not replace durable records. |
| Memory & Rules menu visibility | Move visibility to Appearance > Feature visibility; preserve the current default and editor. The Floor does not replace project memory or rules. |
| LAN access and its set/change passcode form | Keep in Connectivity. Host-only bootstrap and existing human proof remain intact. |
| Remote access and setup/status/account/device controls | Keep in Connectivity. This is dashboard transport rather than an agent's native remote control. |
| Push notifications and permission/subscription/test actions | Keep in Connectivity; these configure a receiving device and browser permission. |
| Mobile pairing | Keep in Connectivity; this attaches client devices. |
| Installed-app/update controls | Keep in System > Installed App; install state, service worker/manifest, update/reinstall diagnostics belong to application maintenance. |
| Passkeys and registration/removal forms | Keep in Connectivity with existing human-only registration/removal policy. |
| Add-ons requests, approval and installation status | Keep in Add-ons. Software approval remains a human capability gate. |
| Feature tour and first-run setup | Keep under System > Help. First-run now saves a tier for its selected provider without overwriting another provider's pin. |
| Shared Rules Path | Keep in System: this selects the storage location. Editing rules remains in the existing Rules surface. |

## Adjacent project and execution surfaces

- Project menu **Agent Settings** becomes **Providers**: choose Use global or a
  provider, then set optional per-provider model/effort overrides. Inherit,
  provider-managed native default, tier tracking, exact pin and a custom ID stay
  distinct. Advanced process lifetime remains available.
- Chat composer, workflow previews and agent-type editors consume provider
  catalogs or the shared server preview. They retain deliberate local choices.
  New provider effort options list levels the adapter actually passes through;
  saved pins are not erased just because a current catalog omits them.
- Changing preferences does not relabel or rebind a captured conversation.
  Captured native-default requests remain empty rather than acquiring today's
  project model. Explicit tracking policies resolve at the launch boundary.
- Scribe/condense/Distiller/classifier model preferences have per-provider role
  overrides and a separate background default. Existing execution ownership,
  isolation, authentication and allowance behavior is unchanged.

## Design and accessibility review

Review basis: the local apple-design references for Settings (put task options
in context and minimize duplication), Layout (adapt smallest/largest layouts),
Accessibility (perceivable and operable controls), Color (semantic colors) and
Typography (legibility and hierarchy). Existing Clayrune theme tokens, settings
navigation and modal patterns are retained.

The new model/effort fields have native labeled selectors; Floor defaults use
labeled inputs and explicit Save/Cancel, a bounded scrolling body and a pinned
footer. Phone selectors have 44px minimum height. Search links to moved controls
and expands matching background/integration details. Opening defaults dismisses
the first-open tutorial offer so it cannot obscure phone actions. Existing
human-only gates remain enforced by the server and passcode flow.

## Migration and rollback

Reads return a migration preview and never write. The first scoped human save
materializes the legacy effective values for registered providers, applies only
the requested field patch, records schema version 1 and keeps legacy fields.
Before cutover it also writes a deduplicated preference-only snapshot under
`~/.clayrune/provider-preferences-backups/`, outside the public repository. A
backup failure aborts the change. No project task text or full config is copied.

An omitted field inherits; explicit empty selects the provider-managed native
default. Null in a patch resets that field; null provider resets that provider's
entry. A reset cannot resurrect a hidden legacy pin. New writes reject foreign
model IDs, unsupported effort, unknown fields/providers and ambiguous mixed old
and new schemas. Old clients get an update-required 409 rather than an ignored
write. Configuration persistence is atomic and precedes live-state mutation.

Rollback is a source revert plus restoration of the preference snapshot, removing
the scoped map and its version marker so retained legacy values become active.
Do not restore unrelated project/config fields from an old whole-file image.

## Verification and limits

Source audit plus hermetic browser interaction checks at 1440px and 390px;
screenshots and the rendered settings census are under
`_scratch/provider-preferences/`. They use labeled synthetic projects/models and
do not establish real account/model entitlement. No provider calls, sign-ins,
live preference writes, schedule changes or server restart were performed.

The provider architecture guard already fails on the unmodified baseline:
`mc/delegation_transcript_reconcile.py` imports `ClaudeRuntime` at module scope.
The same finding was reproduced in a detached baseline worktree. This change
does not modify that module or weaken the guard allowlist.

Final branch checks:

| Check | Result |
| --- | --- |
| 19 selected backend suites, including scoped preference/HTTP/migration, project/Floor, continuation, effort, transforms, routing, ownership and human gates | 525 passed, 0 failed |
| `pyright mc/provider_preferences.py mc/blueprints/provider_preferences_routes.py` | 0 errors |
| `npm --prefix tools/smoke test` | Passed all 10 default frontend scripts, including boot, provider auth/cache, stream dedupe, delivery, paste and viewers |
| `node tools/smoke/provider-preferences.mjs` | Passed at 1440px and 390px; field-only writes, native/inherit/reset, invalid tuple, canceled proof, Floor drafts, search, security and overflow |
| `node tools/smoke/workflow-builder.mjs` | Passed full canvas suite, including save, schedule, mobile gestures and draft preservation |
| `node tools/smoke/first-run-provider-chooser.mjs` and `first-run-setup-gate.mjs` | Passed |
| `node tools/smoke/codex-sandbox-setting.mjs` and `engine-fallback-settings.mjs` | Passed, including passcode cancellation and the moved deep link |
| Unmodified baseline provider architecture guard | Existing concrete-adapter import finding reproduced; not suppressed |

A read-only migration rehearsal compared exact baseline engine/model functions
against scoped copies of the live API preferences without a provider call or
mutation. Results are recorded in the ignored session journal rather than
publishing installation-specific inventory in this repository.

Activation needs an explicitly
approved server restart because this change adds a blueprint and editable config
key. A hard reload then loads the new client modules.
