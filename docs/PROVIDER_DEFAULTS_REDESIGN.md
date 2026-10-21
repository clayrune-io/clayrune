# Provider defaults and project inheritance

Status: approved and implemented on `feature/provider-defaults-floor`,
2026-10-21. Activation awaits an explicitly approved server restart. See
`SETTINGS_OWNERSHIP_AUDIT.md` for the expanded settings audit and Floor changes.
No live preferences or provider accounts were changed during implementation.

## Purpose

Settings > Providers should own the default model for each provider. Projects
should inherit those preferences, with optional overrides for each provider.
Every place that starts work should display and use the same effective engine.
Provider choice, model choice, and reasoning effort must remain coherent.

This follows the existing provider-neutral execution program and completes the
unfinished settings work from the September model hierarchy plan. It does not
replace the runtime boundary or weaken its execution policies.

## Verified problem

The source audit used master at `b4d309ca`. References below are function names
and files so they survive line movement. Earlier audit findings are historical;
only the following findings were rechecked for this proposal.

| Surface | Current source behavior | Required change |
| --- | --- | --- |
| Global settings | `static/js/settings-drill.js::_renderSettings` builds a Claude-only model list and one global effort list under Agent. | Move engine defaults into Providers, using each runtime's catalog and effort metadata. |
| Provider setup | `static/js/provider-settings.js::_renderProviderSettings` manages installation, login and the default provider, but has no per-provider model defaults. It synthesizes a Claude row when inventory is empty. | Add defaults to each real provider; represent loading/error without inventing an installed provider. |
| Project settings | `static/js/modal-manager.js::_renderAgentSettingsDialog` uses `MC_MODEL_CHOICES`, and an unset provider is displayed as Claude. | Replace the engine portion with a project Providers section that can visibly inherit. |
| Fresh-install configuration | `server.py::_load_config` still seeds `agent_model` with `claude-opus-5`. | Remove the vendor-specific seed in the versioned migration; first-run remains an explicit model choice. |
| Resolution | `mc/engine_selection.py::resolve_model_full` reads one project and one global `agent_model`. Known foreign inherited pins resolve to native; they do not fall through to another preference. `resolve_effort` has no provider argument. | Resolve model and effort inside the selected provider's namespace. |
| New-chat composer | `static/js/conversation.js::_providerModelChoices` already consumes provider catalogs, but `_composerModelPicker` assumes only Claude has inherited model preferences. | Reuse catalogs; replace local inheritance guesses with the shared resolved preview. |
| Workflow preview | `static/js/workflow-builder.js::_wfEngineLine` says an unpinned non-Claude runtime uses its native default; it duplicates inheritance rules. | Display the shared resolution result and provenance. |
| Background models | `mc/memory.py::_model_for_provider` reads separate subsystem model keys and filters Claude aliases for other providers. Distiller has a resolver seam of its own. | Include background preferences and legacy subsystem pins in the inventory and cutover. |
| Dispatch | `mc/blueprints/agent_routes.py::_resolve_runtime_model` uses the shared model resolver; `_resolve_dispatch_model` is a Claude routing path, and other code still reads `agent_model` directly. | Cut over every preference consumer; changing the resolver alone is insufficient. |

The providers inventory already serializes runtime-owned model choices in
`mc/blueprints/agent_routes.py::agent_providers`. Reuse this source; do not add
another list of model IDs to Settings. Catalog contents are suggestions, not
proof of account entitlement or a ban on custom model IDs.

## Where settings belong

| Location | Responsibility |
| --- | --- |
| Settings > Providers | Select the global default provider. Each provider has connection state, default model and model-appropriate effort. Advanced background-work defaults live here too. |
| Project > Providers | Default provider: Use global, or a named provider. Provider-specific model/effort rows inherit globally until overridden. Show overrides for other providers without making them the project's default provider. |
| The Floor > Floor defaults | Identity, reply preferences, turn limits and agent tool policy. The Settings > Agent category is retired; project engine settings are Providers. |
| New chat and existing chat header | Show the effective provider/model and allow a deliberate conversation override. These controls change that conversation, not global/project preferences. |
| Agent type editor | Optional provider/model/effort pins for that type. Blank means inherit; a provider-only pin uses that provider's project/global defaults. |
| Schedule, workflow and Hivemind editors | Optional job/step or coordinator/worker overrides, clearly scoped. Inherited engines use the same preview as chats. Resuming an existing chat follows its saved engine policy. |

Do not remove intentional chat or agent-type controls while removing duplicate
*default* controls. A new-chat choice is a useful local override, not a second
place to configure the installation.

Project display example:

```text
Providers
  Default provider     Use global (Codex)
  Codex model          Use global (Balanced)
  Codex effort         Use global (provider default)
  Claude model         Best                 [project override]
  Other providers      Use global
```

Changing the project's default provider changes which saved preferences apply;
it never copies an outgoing provider's model or effort to the incoming one.
Resetting one override removes only that field, leaving the other field intact.

## Resolution contract

Resolve the provider first:

1. Explicit conversation/job/step provider.
2. Agent-type provider pin.
3. Project provider override.
4. Global default provider.

For that provider, resolve model and effort separately, with provenance:

1. Explicit conversation/job/step setting.
2. Agent-type setting, validated for the resolved provider.
3. Project preference for that provider.
4. Global preference for that provider.
5. That provider's native default.

Provider overrides do not erase valid higher-priority model pins. A conflicting
explicit provider/model tuple is a visible validation error; never silently
translate it, move it to a different vendor or spend another account's allowance.
A type without a provider pin still needs its model checked against the resolved
provider. Unknown custom IDs remain allowed unless ownership is known foreign.

Return provider, requested model policy, resolved model, effort and the source
of each field. Distinguish a resolved request from the actual model reported by
the runtime; native default may have no known exact ID before execution.
Use one server-side resolver for execution and a non-executing preview contract
for UI labels. Previews do not start model calls or sign-in flows.

## Preference representation

Implemented scoped fields in the existing config and project records (schema version 1):

```json
{
  "default_provider": "codex",
  "provider_defaults": {
    "codex": {"model": "tier:balanced", "effort": ""},
    "claude": {"model": "tier:best", "effort": ""}
  }
}
```

```json
{
  "provider": "",
  "provider_overrides": {
    "claude": {"model": "tier:best"}
  }
}
```

Keep the existing tier/pin representation rather than invent another tier system:

- Missing project entry or field: inherit.
- Explicit model `""`: native default, stopping model inheritance.
- `tier:best`, `tier:balanced`, `tier:fast`: provider-owned tier tracking.
- Any other model string: exact pin or runtime alias, subject to tuple validation.
- Effort absence: inherit; explicit `""`: native effort; a value: validate against
  the selected provider/model. Never translate similarly named effort levels
  across providers by assumption.

An unsupported tier or effort selection must show an actionable error. Preserve
an existing custom/retired pin in the editor with its actual value. Do not label
every unlisted model as retired: it may be custom, new or uncatalogued.

Use field-level patch semantics with a documented reset operation; missing input
must not erase sibling fields or other providers. Reuse existing configuration
and project persistence after validation. No new sidecar under `data/projects`.

An unconfigured global preference remains native, consistent with the September
standing decision. First-run recommends Balanced for the selected provider and
saves it on completion, preserving any pre-existing pin. A provider with no
model selector uses its native behavior explicitly.

## Background work and routing

The consumer inventory includes Scribe, condense, Distiller, classifiers, Claydo,
profile/identity generation, workflow review and mail laundering. Their provider
ownership and tool-free execution requirements stay intact. Do not make a
background transform follow whichever chat happened to run most recently.

Provide a per-provider background model preference in Providers > Advanced;
existing explicit subsystem overrides remain visible during migration. Project
context and existing task ownership determine the provider; the role-specific
preference determines its model. Keep this role path explicit so moving chat
defaults cannot silently move background work onto an expensive model.

The existing Auto picker is task routing, distinct from tracking a tier. Its
policy must be shown in Providers and resolved inside the selected provider.
Until a provider's routing behavior is implemented and verified, do not advertise
Auto for it. Preserve the existing routing configuration through migration;
never silently turn Auto into Balanced or native. Existing explicit fallback
policies likewise remain explicit and are not enabled by moving preferences.

## Existing conversations

Changing defaults applies to new unpinned work. Preserve an existing chat's saved
provider, requested policy and actual engine history. Exact pins remain exact.
The September plan allows a chat explicitly tracking a tier to re-resolve that
tier on resume; preserve that policy rather than rebasing it onto newly edited
global/project defaults. Do not treat provider-native session IDs as portable.
History and Agent Log report the saved/observed engine, not today's preferences.

## Migration and rollback

1. Inventory all reads/writes of `agent_model`, `agent_effort`, provider defaults,
   auto-routing settings and subsystem model keys. Include installers, first-run,
   imports/exports, project copies, schedules, Hiveminds and workflow previews.
2. Compute a read-only before/after effective-engine preview for existing records.
   Expose ambiguous or behavior-changing mappings before applying migration.
3. Version the migration. Existing new-format entries win. Known concrete model
   pins belong only to their owner; do not duplicate a Claude pin into Codex.
   Legacy generic tiers may have affected several providers: preserve their
   effective policies, rather than assigning them only to the default provider.
   Unknown model ownership and ambiguous effort settings require explicit review.
4. Preserve user pins, task overrides, routing policies and saved session engines.
   Avoid broad replacement of one global string with a new map.
5. Update all writers, execute the compatibility read path, then retire legacy
   writes after the consumer audit passes. Old clients must either have a defined
   compatibility mapping or receive a clear update-required error.
6. Keep a versioned pre-migration backup outside the public repository and an
   explicit rollback procedure. Opening any editor never migrates or saves data.
   Reset/revert one field never rewrites the rest of the map.

## Delivery units and evidence

Each independently changing unit gets its own module. Wire into existing large
files with small calls; do not grow `agent_routes.py`, `modal-manager.js` or
`settings-drill.js` with another self-contained subsystem.

| Unit | Deliverable | Required evidence |
| --- | --- | --- |
| 1. Contract and migration | Provider-scoped preferences, validated writes, resolver and dry-run migration. | Precedence table, absent/native distinction, invalid tuples, custom IDs, per-provider effort, idempotence, round-trip and rollback. |
| 2. Execution cutover | Dispatch and all background/job consumers use the contract. | Captured launch requests agree with resolution; no foreign IDs, silent fallback or weakened transform isolation. |
| 3. Settings and projects | Shared provider-preference component, global and project views, inheritance/reset UI, first-run. | Desktop/phone interaction checks; save/reopen/reset; switching providers preserves each preference; loading/error is honest. |
| 4. Other surfaces | Chat/type/job editors, workflow and Hivemind previews, history labels and import/export. | Preview/execution parity, explicit override wins, imports preserve scoped settings, sessions retain engine policy across resume/restart. |
| 5. Documentation and delivery | User guide, settings architecture docs and changelog reflect implemented behavior. | Relevant backend tests, type checks, boot and affected UI smokes after each merge. Live provider tests are separately identified, not inferred from mocks. |

Minimum matrix: global-only; project provider-only override; per-provider project
model-only and effort-only overrides; explicit native; type provider-only and
model-only pins; explicit job pin; tier tracking; custom model; unsupported
effort; missing catalog; provider switches; concurrent field edits; legacy
mixed-provider installs; native-only runtime; resume/history; background roles;
and an added runtime appearing without another hardcoded menu change.

No agent fan-out, live configuration mutation, provider call, server restart or
deployment is required to review this design. Implementation completion requires
the execution and UI gates above; this proposal itself makes no shipping claim.

## Implementation notes

`mc/provider_preferences.py` owns scoped views, field patches, metadata, tracking
requests and rollback snapshots. `mc/blueprints/provider_preferences_routes.py`
owns read-only inventory/previews; existing config/project write routes retain
their human and unattended gates. Missing maps use the legacy read path. First
scoped writes materialize the legacy effective preferences, save a deduplicated
preference-only snapshot outside the repo, keep old fields and stamp version 1.
An empty scoped map is authoritative. Old unscoped writes after cutover get 409.

`static/js/engine-preferences.js` is the shared global/project editor and preview
client; `floor-preferences.js` owns the new Floor form; `settings-ownership.js`
wires moved controls into their appropriate categories. Visibility switches are
in Appearance. Workflow preview responses patch labels/warnings in place via
`workflow-engine-preview.js`, preserving unsaved fields and drag state.

The native-only runtime explicitly declares that it manages model selection;
an empty catalog for a custom-capable runtime still permits a custom ID.
Per-provider effort metadata describes levels the adapter passes through.
Existing unsupported effort pins remain visible and can be cleared; they are
not promoted into a false working capability.

Execution-profile certification, actual account entitlement and live provider
canaries remain outside this settings verification. Existing legacy executor
ownership is explicit and unchanged. The audit records a pre-existing provider
architecture guard failure reproduced on the original baseline.
