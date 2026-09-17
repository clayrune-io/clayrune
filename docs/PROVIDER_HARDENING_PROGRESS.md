# Provider-neutral hardening: implementation status

## Scope

Conversation identity and history, shared Scribe/memory, core-first installation
with deterministic in-app provider setup, and equivalent safety outcomes across
supported providers. The inventory is in
`research/PROVIDER_DEFAULT_CLASH_AUDIT_2026-09-16.md`.

## Architecture direction — replace coupling, not just individual failures

The completion target is a provider-neutral execution boundary, not a count of
patched call sites. Increment 1 remains a useful defect fix, not proof that this
boundary exists. The passive CLI wrapper in `MULTI_PROVIDER_DESIGN.md` is prior
art; its vendor-event passthrough, deferred helper migrations, implicit Claude
defaults and absent safety gating are not the target architecture.

### Ownership boundary

`Features -> Clayrune execution service -> provider adapters -> CLI processes`

This is a responsibility boundary inside the existing application, not a new
network service or a second runtime framework. Evolve the existing runtime and
resolver behind it, migrating consumers rather than retaining two permanent
execution paths.

| Owner | Responsibilities | Must not know/do |
|---|---|---|
| Features: chat, Claydo, Hivemind, Scribe, workflows | Domain requests, explicit user choices, required operation capabilities, domain completion criteria | CLI flags, vendor transcript formats, subprocess launching for inference, hidden provider defaults |
| Clayrune execution service | Resolve/freeze engine and policy; validate readiness/capabilities; durable lifecycle and normalized events; cancellation, quota and recovery policy; process ownership | Guess missing provider evidence, weaken requested safety, silently switch engines |
| Provider adapter | Binary/version/auth/install protocol; command construction; transport; native resume handles; event/error normalization; actual safety enforcement | Write project memory, choose domain success, own feature retry policy or mutate unrelated conversations |
| Clayrune stores | Conversations/events/checkpoints, project memory/provenance, task progress and policy records | Depend on one vendor's private transcript layout to retain Clayrune-owned state |

Raw vendor payloads stay inside adapters or explicitly scoped diagnostic
storage. Features consume versioned typed events, not `event.raw` with a
provider-name branch. Native transcript import is an adapter concern and
feeds the same normalized store; it is not the primary persistence contract.
Only available data is retained: do not invent hidden reasoning, missing
usage or model identities that the provider never exposes.

### Required contracts

1. **Execution request:** project/conversation/run IDs, unique request/attempt
   IDs, resolved provider/model/effort/account reference, context snapshot,
   declared attachments/tools, security profile, limits and deadline. Credentials
   are adapter-managed references, never copied into domain events or logs.
   Persist requested versus observed engine separately; unknown remains unknown.
   Defaults seed new work, never overwrite a continuing run's frozen choice.
2. **Separate operation profiles:** interactive toolful work, constrained
   unattended work and strictly tool-free text transformation. The last must
   not be implemented by an ordinary auto-approved agent prompt. Capabilities
   distinguish supported, unsupported and unverified for the tested CLI version,
   platform and configuration. Enforce requirements before passing sensitive
   context or starting a model; adapters cannot silently downgrade them.
3. **Normalized outcomes:** ordered events for start/output/tool activity/usage,
   checkpoint/turn completion, blocked-auth, blocked-quota, cancellation and
   terminal failure. A process exit is not domain-task success. Unsupported
   evidence is explicit. Bound streams and propagate errors/cancellation without
   confusing infrastructure failure with a successful answer.
4. **Durable lifecycle:** Clayrune owns conversation ID, attempt sequence,
   frozen engine, native resume handle, progress and recovery state. Reject
   stale-attempt events, serialize mutations and recover after restart. Keep
   explicit incognito/retention rules. Memory summaries reference their source
   conversation/checkpoint; retrieval is shared and vendor-independent.
5. **Quota/retry policy:** normalize account/provider scope and reset time when
   actually known. Pause affected work durably rather than burning generic
   retries. Unknown reset time requires an explicit retry/readiness decision,
   not a guessed countdown. Hivemind displays blocked/partial/failed accurately;
   one failed worker does not become global success. Independent work proceeds
   only under the run's documented policy. Cross-provider handover requires
   explicit consent and a labelled context transfer, never native-ID reuse.
6. **Side-effect safety:** interrupted work may already have executed tools.
   Never promise exactly-once external effects from process retries. Use
   idempotency keys where the destination supports them; otherwise retain
   uncertain outcomes and reconcile before replaying destructive/outward actions.

### Proof required before declaring decoupling complete

- **Architecture tests:** CI rejects inference subprocess launches, raw vendor
  event parsing and direct adapter imports in feature modules. Inventory existing
  exceptions with owners and removal gates; the allowlist only shrinks, cannot
  grow silently. No permanent Claude bypass behind the new facade.
- **Shared adapter conformance suite:** the same lifecycle, identity, streaming,
  error, cancellation and safety-profile contract tests run for every adapter.
  Recorded real protocol fixtures complement fakes. Provider capability claims
  include version/platform evidence and fail closed when compatibility is unknown.
- **Fault injection:** quota exhaustion at every phase, auth expiry, unknown
  model, missing binary, malformed output, huge stderr, lost connection, stale
  callbacks, concurrent chats, process/server crash and upgrade during recovery.
  Check preserved state and truthful UI, not just HTTP status or process exit.
- **Adversarial isolation tests:** canary tools, MCP servers, hooks, plugins,
  inherited configuration and hostile inputs cannot breach each profile's
  promised boundary. Test absence of side effects, not just presence of flags.
- **End-to-end feature matrix:** clean single-provider installations, without
  Claude present, run setup -> chat -> parallel chats -> restart -> continuation
  -> Scribe -> another agent's retrieval and Hivemind -> quota pause -> recovery.
  Include Claude and all other advertised supported providers on their supported
  platforms. Unsupported operations are labelled, not counted as working parity.
- **Extensibility test:** a new test adapter can serve existing features without
  changing those features. Adding a real adapter requires its implementation,
  registration/catalog metadata and conformance evidence, not edits to Scribe,
  chat or Hivemind.

### Revised delivery sequence

First settle and test these contracts and the capability/compatibility matrix.
Then migrate complete vertical paths (including Claude) behind the service,
with their state store, UI errors and failure tests. Each slice removes its old
bypass before it counts as migrated. Prove a non-Claude chat-to-memory vertical
slice, then orchestration/quota recovery, then remaining consumers and unified
setup. Installer work may proceed independently only against the same readiness
contract. Incremental commits remain appropriate; partial migration is never
reported as full robustness. Final acceptance requires the matrix above, not
only a large passing unit-test count.

## Increment 2 — canonical conversation storage contracts

**Foundation implemented; not activated for live conversations.** The database
is created only when an explicit caller begins a persistent attempt. No server
startup, runtime reader, history route or Scribe call uses this store yet. This
increment therefore makes no change to current capture completeness, UI, model
execution, retention, or provider safety.

- `mc/conversation_store.py` supplies a SQLite transactional store with an
  explicit caller-owned state path, outside the project-record directory.
  Project/conversation IDs scope every operation. Ordered immutable events have
  stable IDs and sequence cursors; a repeated ID with different content fails.
- Beginning an attempt atomically records the original user message and
  requested engine. Accepted request identity is separate from process-attempt
  identity: retrying a request cannot invent another user message or change its
  input/engine. Retries require an atomic comparison against the current
  same-request token; a delayed retry cannot supersede a newer user request.
  Only the current attempt token may append: superseded processes
  cannot modify the conversation. Tokens are concurrency guards, not an
  authorization boundary; route-level project authorization remains required.
- Full text, JSON tool bodies and attachment references are retained without
  summary truncation. This is not yet attachment-blob storage, an encrypted
  secret vault, or a native transcript importer. Inputs must already satisfy
  capture/privacy policy; arbitrary environment/config dumps are not allowed.
- Incognito begin performs no filesystem access. Deletion is a recoverable
  tombstone, not physical erasure; reads hide it and both old writers and new
  attempts are refused until explicit restore. Restore does not revive old
  process tokens. Physical erasure/backup retention requires a separate policy.
- `mc/conversation_views.py` provides paginated full-event reads and a separate
  Scribe projection with explicit elision markers for oversized tool details.
  Summary formatting never alters the source events. Requested engine metadata
  is not presented as user text. Tool results retain call IDs/error attribution;
  unknown lifecycle events remain in history. `mc/conversation_contract.py`
  validates known content shapes before append and again when projecting, so
  malformed messages fail visibly instead of silently disappearing.
- Schema/application identity are checked on every connection; unsupported
  versions fail rather than rewriting state. Write transactions and rollback
  are tested against real temporary SQLite files, not only mocks.

Validation: 45 storage/projection/architecture tests plus 564 existing runtime,
engine, history, Hivemind and continuation regressions pass (609 total). All three
new modules pass Pyright with zero errors. Independent review identified and
closed request/retry duplication, malformed-content loss and delayed-retry
takeover risks. No real-provider run or production capture validation is claimed.

### Activation gate: migrate a whole conversation path

Current live buffers are insufficient as the canonical input: Claude Mode B
trims to 1,500 entries, completion rows cap summaries at 2,000 characters, and
tool-line formatting drops results. Capture must happen before display shaping.

| Migration surface | Required integration |
|---|---|
| User ingress | Capture original accepted dispatch/follow-up/queue messages, not provider echoes containing injected system context; distinguish requests from retry attempts |
| Four readers | Claude Mode A and B in `agent_routes`, generic Mode A and Gemini in `agent_runtime`: normalize and durably append before rendering; retain stale-process guards |
| Stream semantics | Specify deltas versus finalized messages and stable tool-call IDs; reconcile provider-exposed data missing from stdout without duplicating events |
| History | Canonical-first lookup before capped agent-log lookup; full history, rail listing, revival, search and export use the same project-scoped source |
| Memory | Terminal Scribe AND live checkpointing; migrate native-byte offsets to canonical sequence watermarks without weakening existing atomic memory writers |
| Privacy/lifecycle | Incognito capture/import exclusions, deletion and restoration, retention, project backup/export and attachment ownership |
| Legacy import | Adapter-only parsing; explicit completeness/provenance and import checkpoints; never let a partial new journal replace a richer existing transcript |

The next vertical slice is incomplete until these consumers are wired and tested
together. No provider capability is certified by the storage tests. Full captured
conversation history remains distinct from inaccessible private vendor reasoning.

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
