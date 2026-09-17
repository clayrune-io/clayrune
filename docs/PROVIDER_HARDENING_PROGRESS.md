# Provider-neutral hardening: implementation status

## Scope

Conversation identity and history, shared Scribe/memory, core-first installation
with deterministic in-app provider setup, and equivalent safety outcomes across
supported providers. The inventory is in
`research/PROVIDER_DEFAULT_CLASH_AUDIT_2026-09-16.md`.

## Increment 1 — engine selection and conversation persistence

- `mc/engine_selection.py` centralizes non-executing provider/model resolution.
  Unconfigured is an error by default. Existing dispatch callers explicitly
  retain their legacy Claude default until durable onboarding migration lands.
  Unknown providers never silently fall back. Known incompatible explicit model
  pairs are errors; inherited legacy foreign model defaults are omitted. Custom
  model IDs and Claude native tier aliases remain supported.
- Ordinary dispatch and project runtime lookup use the resolver. Hivemind
  workers validate before spawning; its existing Sonnet manifest defaults can
  now be rejected on Codex rather than sent to an incompatible CLI. The
  coordinator is still Claude-only: this is a fail-closed guard, not Hivemind
  feature parity.
- Context/skill delivery follows the effective conversation provider using a
  per-call project copy, without mutating saved project defaults.
- Conversation provider ownership and recorded model choices survive native
  resume/cold revival. Manual sessions receive early durable records; native
  IDs are captured when initialization supplies them. Explicit Claude model
  changes/clears persist before acknowledgment. Exact model versions matter,
  not just Opus/Sonnet/Haiku tiers.
- Touched log writers share a project-scoped read/modify/write lock. Scribe
  runs outside that lock and marks its result against a freshly read row.
  Late initialization preserves completed summaries/usage; failed launches
  become durable errors rather than orphaned pending rows. Legacy raw log
  writers outside these paths still need migration/review.

Validation: 564 offline regression tests across engine selection, runtime
adapters, conversation rail/resume, model routing, log durability, Hivemind and
workflow callbacks; new resolver passes Pyright with zero errors. No real
provider execution, installer publication, or shared-server restart was part
of this increment.

### Limits of this increment

No safe background adapter was newly enabled. Scribe, Claydo, other helper calls,
the installer and first-run UI are unchanged. No assertion of universal safety
or clean-install parity follows from the unit tests. Native-default models that
a CLI never reports are not invented from the current settings; resume preserves
the native path rather than claiming a known model. Gemini has a separate reader
and needs its own durability coverage. Explicit empty selection vs omission
still needs a unified request schema across all callers.

## Independent review findings for the next increments

### Safe background transformations

Use a separate fail-closed `safe_text_transform` interface, not the existing
`oneshot_supported` flag. That flag describes availability, not isolation.
Current non-Claude adapters include bypass/auto-approval/tool-enabled paths.
Even the existing Claude boundary needs canary tests for inherited settings,
hooks and plugins, beyond assertions that no-tools flags exist.

Required contract: resolved engine, explicit instruction/data separation,
operation/purpose, deadline and optional schema. No arbitrary cwd, caller CLI
flags, native resume IDs or permission bypass arguments. Immutable per-call
result/error/engine ownership; no singleton `last_error` for concurrent work.
Prevent tools/MCP/hooks from executing before reading untrusted data; rejecting
a tool event afterward is not containment. Bound stderr/stdout and timeouts;
nonzero exit/malformed output/unsupported isolation fail closed without logging
raw sensitive input or silently using another provider.

Migrate structured condense with the existing deterministic locked writer, not
the legacy Write-capable condense agent. Preserve mail laundering's prohibition
on raw fallback. Require canary/adversarial tests and a non-Claude-only
conversation -> Scribe -> durable memory -> other-agent retrieval test.

### Core-first setup

Removing provider prerequisites also removes incidental dependency provisioning:
Windows core cloning still needs Git, currently bootstrapped through the Claude
shell path. Provision Git independently. Defer Node only when in-app setup can
install it; current install-launch requires npm/curl already available and only
reports that a terminal opened, not successful installation.

Use durable server-side setup state independent of tour dismissal/project count;
preserve existing settings and explicit unattended arguments. Separate chosen,
installed, authenticated and ready states. Remove Claude-specific EXE recovery,
the shell installer's unbounded model-based auth probe, and process-name-wide
Claude termination from the migrated installer. Process ownership, progress,
cancellation and clean Windows/Linux/macOS validation are release gates.

## Remaining work

1. Provider-specific saved model/effort preferences and durable setup/readiness.
2. Certified tool-free adapters and selected-provider Scribe/Claydo/helper calls.
3. Per-provider unattended enforcement, isolated tool/config delivery, search and
   telemetry; no weaker fallback where enforcement is unavailable.
4. Full clean-install and end-to-end multi-provider safety matrix before claiming
   completion. Installer signing/reputation and stale-server handoff remain
   separate acceptance gates.
