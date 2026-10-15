# Dev harness: per-project engineering standards

MC-960 (`d813ea78`) · Phase 1 specification · 2026-10-15 · Marlow

**Status: proposed, for Dave's decision and builder handoff.** This document does not enable a gate or change project settings. D1 and D2 in section 12 remain open. Requirements below describe the recommended implementation conditional on those choices; numerical limits are proposals, not adopted project rules. Phase 2 is confined to section 11.

## 1. Problem, audience and completion

Coding agents receive engineering rules but can still add a feature to an unrelated large file, grow a class indefinitely, cross a layer boundary, or omit relevant verification. Clayrune must make a project's chosen standards visible before coding and evaluate the actual proposed integration before it lands. The primary user is a developer using Clayrune to build and maintain their own software product. Clayrune itself is the first dogfood repository.

Phase 1 is complete when an enabled project can supply the same effective standards to Claude, Codex, Gemini and Qwen; check a frozen diff; preserve legacy debt without permitting its expansion; hold a nonconforming integration; return actionable findings; and record a narrowly scoped human exception. A passing report must identify exactly which tree and policy it checked. It must never claim that static checks prove good architecture.

Non-goals: rewriting existing monoliths; a universal semantic architecture judge; code generation or automatic refactors; a replacement test runner, Git host, scheduler, workflow builder or review system; a sandbox against malicious code with the user's filesystem rights; automatic policy relaxation; global changes to vendor configuration; automatic skill installation; remote publishing or deployment; and the interactive architecture-planning product in Phase 2.

## 2. Existing foundations and measured limits

Source inspected at Clayrune commit `36147c711ce2dff615722a41568b3653eceeed3c`. These are code facts at that revision, not claims about a running server's loaded version or settings.

| Existing owner | Verified behavior | Harness integration |
| --- | --- | --- |
| `AGENT_RULES.md`, “One unit, one file” | New independently changing features belong in separate modules; registries import implementations. Today this is instruction text. | Render this principle as guidance and pair it with the explicit checks and review obligation below. |
| `docs/ENTANGLEMENT_AUDIT.md` | Frozen 2026-10-02 census at `a2ce8d3`: 37 confirmed bundle-bearing files, including 29 over 1,500 lines; the large census totals 112,589 lines. Smaller bundles exist. | Use its concern inventory to prepare dogfood ownership rules. Re-measure current source during onboarding; do not seed current caps from its historical counts. |
| `tests/test_provider_architecture_guard.py` | AST scanner rejects specified vendor subprocess/import/runtime coupling in feature modules. Exceptions use `(path, enclosing function, kind)` and exact counts, with owner and removal gate; unexpected findings and stale counts fail. | Reuse the precise finding/ledger pattern. Keep this test active as an independent existing check. Do not import production logic from `tests/`. |
| `docs/PROVIDER_ARCHITECTURE_GUARD.md` | Explains a shrinking migration ledger, but still describes exact source-line keys. | The current implementation is authoritative: symbols/counts avoid unrelated line shifts. Lines remain display locations. |
| `_build_agent_context` in `mc/blueprints/agent_routes.py` | Loads project `AGENT_RULES.md` and shared rules; context is built for multiple runtimes and different context profiles. | Add one call to a new context contributor adjacent to project rules. The contributor owns rendering; no rule bodies go into the monolith. |
| `mc/agent_worktree.py`, `mc/merge_review_gate.py` | `merge_back` preserves dirty/conflicting/held branches. When review is required for the base branch, an unreviewed current tip returns `awaiting_review`; a pass merges the reviewed SHA. | Add a separate standards prerequisite through a leaf coordinator. A standards pass never substitutes for the existing independent review. |
| `mc/hivemind_integration.py` | Integrates workstreams sequentially in an integration worktree, runs named smokes after each, rolls back that disposable integration on failure. | Invoke the same standards evaluator for each integration candidate and final landing. No separate hivemind standards engine. |
| `tools/merge-review-check.py`, opt-in Git hooks | Manual merge checks read the same review store. Hooks can be skipped and do not cover fast-forward merges. | Extend the shared preflight entry point with the standards result. Clearly disclose manual Git bypasses. |
| `mc/project_safety_settings.py`, `mc/blueprints/secrets_routes.py` | Project safety writes refuse agent callers; sensitive human actions reuse `_require_human_passcode`. | Extend existing safety ownership for activation and exceptions; preserve the existing human-proof rules. |
| `mc/atomic_json.py`, project activity, agent jobs and dispatch notification | Existing persistence, job execution and result delivery mechanisms. | Use these for records, long verification and send-back. Do not build another daemon, task queue or notification channel. |

The 2026-09-25 [vendor matrix](VENDOR_HARNESS_MATRIX.md) measured real context, hook, worktree and resume differences. Some were fixed in source after those probes; the matrix explicitly separates source fixes from live verification. V1 must test current delivery for all four vendors rather than inherit the old PASS cells.

The inspected local hook files were `process-guard.py`, `turn-guard.py`, `reply-budget.py`, `reply-length-guard.py` and `permission-ask-guard.py` under `~/.claude/hooks/`. They illustrate PreToolUse command refusal, prompt reminders and bounded Stop feedback. Several fail open deliberately; the process guard calls itself a tripwire. They are operator-local evidence, not distributable project defaults. The existing `mc/guardrail_hooks.py` owns vendor adaptation. A vendor hook can improve feedback latency; it cannot be the authoritative standards gate.

## 3. Research: what to adopt and skip

Evidence checked on **2026-10-15**. MC-960 links a [landscape listing](https://landscape.jimmysong.io/projects/addyosmani-agent-skills/?hl=en-US); primary evidence is the upstream repository. GitHub's commit API reported tip [`aeac41d4d005999432bb7312f7ff06383a91d59d`](https://github.com/addyosmani/agent-skills/commit/aeac41d4d005999432bb7312f7ff06383a91d59d), dated **2026-10-13T20:21:06Z**. The six upstream files linked below were fetched at that revision and matched the observed `main` byte for byte. This is an observation of that revision, not evidence of what the pack contained when the backlog item was created.

| Source | Adopt | Skip or adapt, and why |
| --- | --- | --- |
| [README](https://github.com/addyosmani/agent-skills/blob/aeac41d4d005999432bb7312f7ff06383a91d59d/README.md) | Portable Markdown guidance and small work units, with verification evidence at lifecycle boundaries. | Do not install the entire skill pack, its personas or slash-command layer. Clayrune already owns those surfaces. |
| [Constraint-driven development](https://github.com/addyosmani/agent-skills/blob/aeac41d4d005999432bb7312f7ff06383a91d59d/skills/constraint-driven-development/SKILL.md) | Explicit project limits, cost-based check placement, legacy ratchets and visibility when a change weakens standards. | Adapt its prose `CONSTRAINTS.md` contract into validated machine data with rendered explanations. Do not adopt its coverage numbers or tool menu as universal defaults; architecture limits require project calibration. |
| [Floor guard reference](https://github.com/addyosmani/agent-skills/blob/aeac41d4d005999432bb7312f7ff06383a91d59d/skills/constraint-driven-development/references/floor-guard.md) | Distinguish violation from inability to check; include untracked work in previews; detect changes to the quality bar itself. | Do not copy regex matches as proofs of broken tests or architecture. Flag suppressions/test deletions for review. Replace platform-specific diff construction with the existing argv-based Git wrapper and structured path handling. |
| [Adoption guide](https://github.com/addyosmani/agent-skills/blob/aeac41d4d005999432bb7312f7ff06383a91d59d/docs/adoption-guide.md) | Start established projects from their actual conventions and protect the area being changed; apply stronger checks to new work. | Do not force a whole-repository rewrite or dump the full skill catalog into every prompt. |
| [Code review and quality](https://github.com/addyosmani/agent-skills/blob/aeac41d4d005999432bb7312f7ff06383a91d59d/skills/code-review-and-quality/SKILL.md) | Keep correctness and design review distinct from mechanical checks; findings need a severity and action. | Do not automatically dispatch a new review persona or claim a small diff proves maintainability. Reuse Clayrune's existing review lineage and handoff. |
| [MIT license](https://github.com/addyosmani/agent-skills/blob/aeac41d4d005999432bb7312f7ff06383a91d59d/LICENSE) | Preserve the required notice if implementation copies upstream material. | This spec references ideas; it does not vendor the pack. Any later copied implementation must carry attribution and license text. |

These sources inform the proposal. They are not an instruction to run installers, change permissions, adopt upstream autonomy defaults or loosen any Clayrune human gate.

## 4. Per-project policy and ownership

### 4.1 One portable contract, one trusted active revision

Proposed new paths, relative to the user's registered repository root:

| Artifact | Purpose and authority |
| --- | --- |
| `.clayrune/dev-harness/policy.json` | Portable, versioned policy: stable rule IDs, rationale, limits, source scopes, exclusions, layer/import restrictions, unit/registry declarations, verification mappings and stage budgets. No secrets or local absolute paths. |
| `.clayrune/dev-harness/baseline.json` | Portable measured legacy ledger: approved base commit, analyzer versions, exact over-limit symbols/edges/counts, owners and removal conditions. This is debt data, never a broad file exemption. |
| Existing `AGENT_RULES.md` / vendor context | A short pointer where appropriate plus a rendered effective standards block. Do not maintain another editable prose policy in `CONSTRAINTS.md`. Existing user-authored documents remain intact. |
| Clayrune data root: `data/dev_harness/<project_id>/` | Local activation snapshot, run receipts, ratchet state and human exception records, atomically persisted through the existing writer. No files under `data/projects/`. These records are runtime/user data and must be ignored by Git and excluded from public builds. |

Paths above are proposed additions, not existing capabilities. Create no machine-global vendor files. Reuse `.clayrune` project organization without overwriting its other contents.

The user chooses a built-in **Modular development** starting profile, previews measured findings, edits the project copy and activates an exact policy+baseline digest. Built-in profiles are versioned data, not code bodies inside a registry. Existing standards can be imported as a draft, but natural-language text never becomes a blocking rule without an explicit checker definition and user review. Preset updates produce a proposed diff; they never silently replace active rules.

Activation is project-local and human-only, using the existing caller check and per-call passcode proof. It records policy digest, baseline digest, parser/checker versions and configured target branch. A missing configuration is Off. An activated configuration that becomes missing, invalid or unreadable is Error and blocks the managed integration; it must not silently become Off. Imported/cloned policies start inactive until the user reviews them. A non-Git project can preview local files, but the UI must say that merge enforcement is unavailable.

Agent branches may propose policy changes. They must continue to be checked against the **trusted active** revision, not their own new file contents. Any change to policy, baseline, exclusions, verifier commands or checker versions requires separate human activation. A side-by-side preview explains tightening and weakening. Initial onboarding and later rebaselines cannot be approved by an agent or a sibling reviewer. They do not change any existing passcode/host-only requirement.

The trusted snapshot protects the normal managed flow from self-approved policy edits. It is not a security boundary against an actor with unrestricted disk access. Direct filesystem tampering and raw Git outside Clayrune must be disclosed as coverage limits, not described as prevented.

### 4.2 Guidance before code

A new context contributor returns the active profile/revision, applicable rule IDs, limits, affected legacy entries, explicit layer/unit boundaries and reproducible check command. It includes the one-unit-one-file principle and says how to obtain complete policy detail. Keep the common block compact; load long maps only for the task's paths. A lean context must still include mandatory standards.

Wire the contributor once into `_build_agent_context`; do not manually duplicate it in four runtime classes or append whole skills. Use existing vendor context delivery and fresh-context/resume mechanisms. Persist the revision given to a session. Before the next coding turn, a changed active revision must be delivered through the existing refresh/rebuild path with persona and incognito state preserved; when native resume cannot replace context, use the existing fresh-context handoff. The merge check always uses the current active revision, even if a session had older guidance. The UI reports the mismatch rather than claiming the old turn followed new rules.

No hooks or prompt acknowledgements can grant a pass. Guidance prepares the agent; the evaluator checks the resulting tree.

## 5. Checks and language coverage

### 5.1 Recommended v1 coverage (D1)

Python plus JavaScript/TypeScript gives dogfood backend and frontend coverage. Python uses its AST; JS/TS uses one pinned parser adapter backed by the TypeScript compiler API for `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.tsx`. The adapter is a development tool, not browser runtime code. It must handle the project's actual syntax and report unavailable dependencies explicitly; do not download tools while checking a diff. This parser choice is a proposed implementation, not a claim that it is installed today.

| Input | V1 checks | Limits shown to the user |
| --- | --- | --- |
| Python | File/class/function spans; static imports including relative imports; configured unit/registry rules; test mappings. | Dynamic imports and runtime metaprogramming require review. |
| JS/TS and JSX/TSX | File/class/function/method/arrow spans; static imports/exports and literal `require`/`import`; configured unit/registry rules; test mappings. | Computed loaders, bundler aliases without an approved resolution map and implicit browser globals require review. |
| CSS, HTML, other text source | File size and configured source-to-test mapping; ownership declarations. | No claim of CSS concerns, inline-script function analysis or cross-language call-graph enforcement. |
| Other languages | Explicit Unsupported structural status; generic text size only where configured. | No class/function/import guarantee. Cannot mark a selected unsupported blocking rule as passed. |

No automatic language substitution. Syntax errors in a supported input are findings. Parser failure, unsupported syntax in a selected check or a missing parser is Error, not an empty result. An onboarding coverage table lists discovered extensions and exactly which checks are available.

### 5.2 Rule contract

Every rule must state `id`, scope, rationale, checker/version, measurement, limit or forbidden relationship, blocking/advisory severity and applicable stage. Defaults below are draft profile values for calibration under D2; they are not measured quality thresholds.

| Rule | Deterministic measurement | Draft result |
| --- | --- | --- |
| `size.file` | Physical UTF-8 source lines, including blank/comment lines; LF/CRLF equivalent; empty file = 0. | New source file above 600 lines blocks. |
| `size.class` | Inclusive AST span from declaration to end, including nested bodies. Python decorators belong to the span. | New class above 300 lines blocks. |
| `size.function` | Inclusive AST span, with async/functions/methods and JS arrows included; nested definitions also measured separately. | New function above 80 lines blocks. |
| `layers.import` | Resolved static edge `(source module, imported module)` against configured allowed/forbidden layer pairs. Check imports from changed modules and reverse references affected by moves. | New forbidden edge blocks. Unresolved internal edges require review; they are never silently omitted. |
| `units.file` | Declared independently changing units mapped to paths; each implementation file belongs to at most one unit; shared contracts may have their own unit. | Conflicting mappings or absent required ownership on a new source file block. |
| `registry.bodies` | For explicitly configured registries, callable entries reference imported implementations; inline function/class/arrow bodies in designated implementation slots fail. Literal labels and configuration data are allowed. | New embedded implementation blocks. This is a configured syntactic rule, not a detector for every registry in arbitrary code. |
| `tests.with_change` | Each changed production path matches an approved verification group; its required commands ran successfully against the checked tree. | Missing mapping, missing execution or failure blocks. Editing any test file alone is insufficient. |
| `policy.changed` | Compare candidate policy/baseline/check definitions with the activated digests. | Hold policy changes for human activation; never use the edited thresholds to judge the same candidate. |
| `verification.weakened` | Diff detects test deletions/skips, relevant suppression additions or verifier edits using language-aware patterns where available. | Requires explicit review; it is a signal, not proof that the change is wrong. |

Physical size is a tripwire. A cohesive 601-line file may deserve an exception; a 200-line file with three features still violates the engineering principle. Splitting one monolith into unowned tiny files, minifying code or moving bodies into strings is not a compliant architecture. Review must evaluate ownership and cohesion independently.

**Concerns per file cannot be counted reliably by a deterministic general-purpose checker.** V1 enforces declared units and supported registry shapes mechanically. The existing independent review must also explicitly answer whether new behavior adds a separately changing concern to an existing implementation file. Extend the existing review record/route with a structured standards assessment naming the files, responsibility, result and candidate `B/H/T` identifiers; retain its reviewer attribution and lineage checks. Do not create a parallel reviewer identity or approval store. A changed target/tree invalidates that assessment without rewriting the existing tip-review verdict. No assessment means “review required”, not an inferred pass. LLM analysis may propose findings; it may not supply a fabricated concern count or waive a deterministic failure.

### 5.3 Verification mapping without forced test churn

A verification group contains source globs, owned test paths, approved argv command(s), working directory, timeout and rationale. Commands reuse the repository's existing scripts, test runners and smoke entry points. The harness records the actual exit status and checked tree; it does not trust the builder's pasted output. Tests execute project code with the user's existing trust model; this feature adds no command sandbox.

Existing tests can satisfy a change when they exercise the relevant behavior. A bug fix normally adds a regression test; a new behavior normally changes a behavioral test. The independent review checks that claim. Documentation-only changes need no application tests. Pure cosmetic or mechanical changes can use an explicitly configured light verification group. A claim of “no behavior change” cannot itself skip a required group. Missing tests require a human-approved scoped exception, not a meaningless test that mirrors the implementation.

Clayrune dogfood mapping must retain `tests/test_provider_architecture_guard.py`; affected Python suites and scoped Pyright basic for new/moved `mc/` modules; and frontend boot plus feature smokes for relevant `static/` changes. A smoke must serve the checked branch/tree, not accidentally test the running main checkout on port 5199. Existing main-checkout smoke dependencies may be linked as already supported; never install another browser stack or recursively delete a dependency junction.

## 6. Legacy debt: baseline and ratchet

Onboarding runs a read-only full scan at a frozen commit. It emits a draft ledger for actual violations, grouped by stable `(path, qualified symbol or module, rule ID)`, with measured cap/count, owner, reason, removal condition and source commit. No automatic wildcard exemption for an entire directory. Generated/vendor exclusions require explicit paths, provenance and visible policy approval; an agent cannot label handwritten code generated to escape limits.

Once activated:

1. A new symbol/file has no legacy allowance, except a verified pure relocation under rule 4. It must meet the configured limit. An object absent from the current target does not inherit a stale allowance when a later change reintroduces the same name.
2. A legacy object above the limit may remain at its accepted measurement or shrink. The allowed cap is the tighter of the active ledger cap and the current target's measured value, with the configured ordinary limit as the floor. Thus a legacy 900-line file reduced to 850 cannot return to 900 on the next change. Growth elsewhere is never offset by deleting unrelated lines.
3. New forbidden import edges or registry bodies are not grandfathered by the file's existing size allowance. Exception keys for these findings include the specific edge/body identity and exact count. An extra call inside an allowlisted function must fail, as in the provider guard.
4. Stable keys ignore line-number shifts. A deleted object retires its entry. A detected pure move carries its old cap under a recorded identity migration; it does not create a larger allowance. Ambiguous rename/split/duplicate identities require review, never automatic debt creation. New extracted files must satisfy normal limits.
5. Improvements produce a generated ledger update for review and an atomic local ratchet after successful landing. The next gate also measures the current target, so an uncommitted portable-ledger update cannot reopen old headroom. Rebaselining upward requires human approval with the measured difference and reason.
6. A malformed ledger, duplicate keys, missing owners, fabricated entries or missing measurement provenance cannot activate. Stale entries must be pruned in the generated ledger; they do not exempt another symbol with the same line number. Expired temporary exceptions hold only affected candidates; unrelated existing debt is still grandfathered.

Dogfood must show that the current `agent_routes.py` can receive a within-cap bug fix while adding a new concern still needs extraction/review. The historical audit's 17,102 lines is not its current allowance. A planned extraction is its own ticket, not a prerequisite for enabling checks across the repository.

## 7. Execution timing, immutable inputs and integration

| Moment | Required behavior | Budget and authority |
| --- | --- | --- |
| Before first coding turn | Render active rules, coverage and relevant debt. | No scan of the whole repository per prompt. |
| Per edit | Optional changed-file preview via existing hook adapters or explicit check command. Incomplete files may yield provisional findings. | Target under 2 seconds; advisory feedback only. No cross-vendor hook requirement. |
| Per turn completion | A vendor-neutral lifecycle call snapshots the isolated worktree and runs fast structural checks; related verification can run through the existing managed job mechanism. | Target under 5 seconds for structural checks; no repeat of unchanged receipts. A timeout is Pending/Error, never green. |
| Before each managed merge | Authoritative check of the exact proposed integration tree against the current target and active policy; required verification and review complete. | Blocks landing. Default verification budget proposed at 90 seconds, editable at setup; slow configured suites stay Pending until done, not silently skipped. |
| After each integration, before the next | Validate actual integrated tree and run its affected smokes, including combinations that individual branches did not exercise. | Reuse sequential integration ownership. Failure stops the sequence; preserve source branches and diagnostics. |

No new periodic poller. Per-turn results are feedback and reusable evidence only when their exact inputs match. A worker is allowed to finish its conversational turn with a held branch; a Stop hook must not force an infinite repair loop or hide a human decision.

### 7.1 Snapshot and diff definition

Let `B` be the current resolved target commit, `H` the incoming tip, `M` their merge base, and `T` the proposed tree resulting from merging `H` into `B`. Freeze all full object IDs. Use `M..H` for branch attribution and changed-test signals; use `B..T` for actual integration changes. Existing-debt comparison uses `B`, not the branch's old fork point. If Git cannot resolve a base or construct a conflict-free candidate, report Conflict/Error and preserve work; do not check an older base instead.

Turn previews include staged, unstaged and untracked nonignored files in an immutable content manifest. A Git integration requires committed, clean input. A preview of dirty work never grants merge permission. Enumerate changed paths with NUL-safe Git output and preserve old/new names, deletes and file modes. No shell-interpolated filenames. Path containment, symlinks escaping the project, binary/undecodable source and resource limits yield explicit exclusions or errors according to the active policy, never a partial passing scan.

Scan whole changed files for size; imports need the approved graph/resolution inputs and affected reverse dependencies. Deleting or moving a module must still detect newly broken edges from unchanged importers. Do not merely count added diff lines. A new file must be scanned even if absent from ordinary unstaged `git diff` output. Cache by content and analyzer version, not file timestamp.

### 7.2 One standards prerequisite across landing paths

Add a leaf integration coordinator called by `agent_worktree.merge_back`, hivemind integration and the existing manual preflight tool. Reuse existing project Git wrappers, locks and worktree lifecycle. Prepare and verify `T` in a disposable integration worktree; do not merge into the user's dirty tree to discover whether it is compliant. Tests may write there, so verify the source-tree digest again after execution. Only coordinator-produced receipts count.

In Enforce mode the final predicate is: **standards pass or valid scoped exceptions, required structural review, existing merge-review pass when enabled, clean compatible target checkout, and unchanged inputs**. Human review cannot overwrite machine results with a new `pass` string. Disabling the existing review gate does not disable the standards gate; standards activation must explain its own independent cohesion-review requirement. Off and Preview add no standards-based merge hold and do not relax the existing merge-review gate.

Before landing, re-resolve `B`, `H`, active policy/baseline and tool versions under the existing integration serialization. Any change invalidates the receipt and recomputes the candidate. Land only the pinned commit/tree; verify the actual resulting tree equals `T`. A concurrent external Git mutation causes a hold, never approval of unexamined content. Test timestamps alone are not validity evidence.

Hivemind worker-to-integration merges also check standards. The existing review gate's explicit-target exemption must not become a standards exemption. The final integration-to-base move is checked again. Stale-worktree GC must preserve held/error branches and never remove them as merged. Share the same coordinator with `tools/merge-review-check.py`; do not create a competing `dev-harness-merge` engine.

Installed Git hooks remain convenience guards. Fast-forwards, `--no-verify`, cherry-picks, direct commits and other tools can bypass local hooks or Clayrune. The UI therefore says **“Enforced for Clayrune-managed integrations”**, not “all commits protected”. Expose one headless checker entry point for optional CI use; branch protection and required CI configuration remain the repository owner's separate action. A CLI report alone is not an authenticated managed-merge receipt.

## 8. Findings, repair and human exceptions

### 8.1 Results and receipts

The evaluator returns structured `pass`, `fail`, `error`, `pending`, `review_required`, `unsupported` or `off` status, plus per-rule outcomes. The CLI maps a completed pass to 0, blocking findings/review-required to 1, and inability to evaluate to 2. Pending is a job state, not exit 0. Unsupported selected blocking checks prevent a complete pass.

Proposed new HTTP contracts, not endpoints available today: `GET /api/project/<pid>/dev-harness` returns effective mode/revision, editable draft and language coverage; `POST .../dev-harness/preview` starts a read-only evaluation and returns 202 with run/job IDs; `GET .../dev-harness/runs/<run_id>` returns its status/evidence; `POST .../dev-harness/activate` accepts an expected active revision, validated draft and measured baseline digests plus existing human proof; `POST .../dev-harness/exceptions` creates the exact finding exception; `DELETE .../dev-harness/exceptions/<id>` withdraws an unused exception through the same human gate. Draft editing stays in the shared panel until activation; saving a repository draft cannot activate it. Revision conflicts return 409 without partial writes; refused human actions return 403; invalid policy returns 400. Runs use the existing managed-job implementation and notifications, not another queue. The existing review route carries the optional standards assessment described in section 5, checked against the current coordinator candidate.

Each receipt includes project/run IDs; stage; `B`, `H`, `M`, `T` or preview manifest; policy/baseline/checker digests; executed verifier argv IDs, durations and exit codes; findings; required-review references; exception IDs; and creation time. A finding includes rule ID, path, qualified symbol/edge, display line range, observed value, cap/baseline delta, severity and one concrete repair. Example: “`size.function`: `src/importer.py:load_batch`, 104 lines; allowed 80; extract decoding from persistence.” Reports must escape paths/messages, limit resource use and redact secret values from output.

Persist local receipts through existing atomic storage and activity logs. Crashes or unreadable records cannot become passes. Jobs survive a chat ending through Clayrune's existing job/completion mechanism. UI events are project/run scoped so a late response cannot paint another project's badge green.

### 8.2 Repair and escalation

A failed check preserves the branch and displays the report on its existing agent/session surface and the project standards panel. Return the report and check command through existing follow-up/job delivery, retaining persona and worktree identity. No unsolicited duplicate repair session. An already authorized builder can fix and rerun; a finished builder requires its lead to dispatch a fresh bounded repair task using the existing dispatch path.

No automatic retries beyond the owning task's existing authorization. Two unchanged failed attempts are summarized for the lead; a new exception is not inferred. Agent/lead choices may resolve implementation questions, but cannot approve policy weakening or human exceptions. Unattended logging uses `docs/_journal/<item-id>-<slug>.md`, never backlog-note spam. Existing notification/decision channels handle the human interrupt.

### 8.3 Narrow human override

The user can choose **Fix**, **Request repair**, or **Allow this finding once**. The last action requires the existing human-only caller check and per-call passcode proof; a body field, Origin claim or agent-written file is insufficient. New routes must reuse the current helper and preserve its host/passcode bootstrap behavior. No agent path creates, extends or approves an exception.

A one-time exception binds exact project, rule/finding keys, `B/H/T`, policy digest, human reason, owner, expiry and approver evidence. Proposed default lifetime: 24 hours, consumed by one successful landing. It cannot authorize a different tip, target or policy, and it never deletes the underlying finding. Unrelated blocking rules and the existing review gate still apply. Error states with no complete scan cannot be waved through using a finding exception; repair the tooling or deliberately change the project's active mode through the human safety surface. A human can withdraw an unused exception; after landing, reversal is a normal reviewed revert, not automatic history rewriting.

Permanent threshold/exclusion/baseline changes are policy activations with visible diffs, not “Allow once”. Off/Preview/Enforce transitions are human-only, audited and invalidate outstanding receipts. This gate grants no deployment, credential or publishing authority.

## 9. Project and Desk surface

Provide one **Engineering standards** panel scoped to the registered software project. Entry points are the project's existing menu/settings and a small link/status card on the Desk project page. Both open the same panel and store; do not build independent settings editors. The Desk is an entry point, not a requirement for running checks. Do not add a top-level global dashboard for Phase 1.

The panel must support this sequence:

1. **Inspect:** show repository/target identity, active mode, detected languages, supported checks and last checked tree. A project without Git gets an explicit preview-only explanation.
2. **Choose and edit:** select the modular profile, change limits/rationale, scopes, layer edges, unit/registry declarations and existing verification commands. Show a readable summary with an advanced validated-data editor. Never claim a natural-language rule has a checker when it does not.
3. **Preview and baseline:** run a scan with progress and cancel; show current debt separately from new violations, actual command prerequisites and estimated verification cost. A new legacy ledger is a proposal requiring approval.
4. **Activate:** show exact policy and baseline changes and human-only action; imported policies remain inactive. Show missing tools or coverage before activation. Do not silently downgrade unavailable blocking checks to warnings.
5. **Resolve:** show failed rule, path/symbol, measured delta, repair action, run evidence, pending review and scoped exception action. Recheck uses the same run service. A receipt for an older tree is marked Stale.

Modes are **Off**, **Preview** and **Enforce**. Preview reports findings but never claims to block landing. Enforce becomes available only after activation prerequisites pass. While work runs, display Pending and elapsed stage; cancel stays Cancelled/held. Zero findings plus a parser failure is Error. A summary badge links to the evidence and explains the managed-integration boundary.

Keep user copy about standards and results; raw SHA/argv detail belongs in an expandable evidence view. At phone width the panel must scroll, preserve edits and pin final actions to the bottom. Keyboard labels, focus restoration, loading/errors, stale revisions and cross-project navigation are acceptance requirements.

## 10. Build tickets and acceptance gates

Names below are proposed new modules, not existing files. Each row is one builder-owned unit with its own focused tests. New units must remain separate; the registry imports implementations. Estimates are planning ranges in builder sessions, including focused tests, not measured throughput or authorization to launch parallel agents. Shared-file wiring is serialized by the integration owner.

| Ticket | Ownership / deliverable | Depends on | Acceptance / estimate |
| --- | --- | --- | --- |
| DH-1 Policy contract | `mc/dev_harness_policy.py`, data-only `data/dev_harness_profiles/modular.json`, `tests/test_dev_harness_policy.py` | D1/D2 | Validate fields, digests, globs, unsupported selections and command declarations; deterministic profile rendering. 1 session. |
| DH-2 Trusted state and baseline | `mc/dev_harness_store.py`, `mc/dev_harness_baseline.py`, separate tests for each | DH-1 | Atomic snapshot/receipts, precise ledger validation, shrink/delete/move cases, corrupt data fails closed; no project-record sidecars. 1–2 sessions. |
| DH-3 Python adapter | `mc/dev_harness_python.py`, its parser fixtures/test file | DH-1 | Decorated/async/nested symbols, imports/relative imports, AST size locations, registry-body shapes and parse errors. 1 session. |
| DH-4 JS/TS adapter | `tools/dev-harness/parse-js.mjs`, its pinned development manifest/lock, `mc/dev_harness_javascript.py`, focused parser tests | DH-1, D1 | JS/TS/JSX/TSX, class methods/arrows, imports/aliases, missing parser and unsupported syntax. Adapter outputs common facts; no independent policy evaluator. 1–2 sessions. |
| DH-5 Frozen diff and graph | `mc/dev_harness_diff.py`, `mc/dev_harness_graph.py`, separate tests | DH-1, adapters | Clean/dirty snapshots, untracked/staged input, moves/deletes, NUL-safe filenames, reverse-edge effects, conflicts and target advancement. 1–2 sessions. |
| DH-6 Mechanical evaluation | `mc/dev_harness_checks.py` as imported rule registry; rule bodies in `mc/dev_harness_rules/{size,boundaries,units,verification_changes}.py` with individual tests | DH-2–5 | New violations block despite old debt, configured registry rule catches inline bodies, unit mappings cannot masquerade as semantic proof, policy edits cannot self-approve. 1–2 sessions. |
| DH-7 Verification execution | `mc/dev_harness_verification.py`, focused tests | DH-5/6 | Reuse managed jobs/commands; mappings select real suites; timeouts, cancellation, source mutation and stale output never pass. 1 session. |
| DH-8 Context delivery | `mc/dev_harness_context.py`, focused context tests; one minimal `_build_agent_context` call | DH-1/2 | Full/lean/task/fresh/resume paths receive same revision, keep persona, no whole-pack injection or changed vendor-global files. 1 session. |
| DH-9 Integration prerequisite | `mc/dev_harness_integration.py`, focused integration tests; minimal calls from worktree, hivemind and existing manual preflight owners | DH-5–7 | All managed landings use same receipt predicate, review remains independent, exact-tree/base recheck, held GC preservation, sequential smoke checks. No broad merge rewrite. 1–2 sessions. |
| DH-10 Human policy/exception API | `mc/blueprints/dev_harness_routes.py`, `mc/dev_harness_exceptions.py`, separate route/exception tests; minimal registration, project-safety key and existing review-record/route wiring | DH-2/7/9 | Draft read/preview allowed; activation/weakening/override refuse agents and require existing human proof; expiry/tip binding, structural-assessment lineage and no partial writes. 1 session. |
| DH-11 Project panel | `static/js/dev-harness-panel.js`, `static/css/dev-harness-panel.css`, `tools/smoke/dev-harness.mjs` | DH-10 | One panel behind both project and Desk links; edit/preview/progress/activate/repair flows; mobile, keyboard and stale-result coverage. Entry points only in existing large files. 1–2 sessions. |
| DH-12 Headless delivery and dogfood | `tools/dev-harness-check.py`, `tests/test_dev_harness_cli.py`, `tests/test_dev_harness_vendor_delivery.py`; separate user/developer guide documentation | DH-8–11 | CLI 0/1/2; all four real vendor deliveries verified; repeatable dogfood dry run; optional CI instructions; packaging includes profiles/parser assets. 1–2 sessions. |

The range is **12–19 builder sessions**, plus lead decisions and independent reviews. Split a row further if its owner cannot finish the acceptance scope in two sessions; do not combine tickets to save dispatch overhead. DH-2 and DH-5 each contain two related leaf modules because persistence versus ledger logic and diff versus graph logic must remain independently editable. Their registry/wiring carries no rule bodies. Shared entry points receive only composition calls; no new subsystem bodies in audited monoliths.

Minimum release evidence:

| Scenario | Required result |
| --- | --- |
| Each size limit exceeded in a new file/class/function | Fail with exact measurement and location; all three limits tested independently. |
| Legacy symbol edited within cap, then reduced, then regrown | First passes; reduction tightens the next accepted cap; regrowth fails. |
| Smaller new concern in a legacy file | Ownership/registry checks where configured, plus mandatory structural review; size alone cannot approve it. |
| Added forbidden import / module rename leaving a broken importer | Fail or review-required for unsupported resolution; never silently drop the edge. |
| Production change with no verification mapping; test file touched but commands not run | Both fail; a mapped successful existing suite can pass without test-file churn. |
| Candidate edits baseline, limits, exclusions or checker command to make itself green | Remains checked against active policy; human activation required. |
| Parser missing/crashed, malformed report, shallow clone without usable base, cancelled job | Explicit Error/Cancelled; no valid merge receipt. |
| Target/tip/policy changes after a green run; tests mutate source | Receipt invalidated; recompute and verify. |
| Ordinary agent landing, hivemind intermediate merge, final integration landing, stale GC | Same standards requirement; holds preserve work. |
| Human exception reused on another tree, expired exception, forged agent/UI fields | Refused; unaffected human gates remain in place. |
| Claude/Codex/Gemini/Qwen dispatch and follow-up | Same rule revision and limits visible; run the same bad/good diff fixture through the shared evaluator, independent of hook support. Record actual runtime versions and failures. |
| One green branch combined with another | Recheck combined tree and affected smokes after each integration, before proceeding. |

Clayrune dogfood activation is a later human action. This specification authorizes no live setting change, main-branch merge, server restart or push. Implementation tests use temporary repositories and disposable processes, never the production project as a mutation fixture. New/moved Python modules pass scoped Pyright basic; new UI passes boot and focused interaction smokes. Update the user guide only when these surfaces exist, not as if this proposal had shipped.

## 11. Phase 2: interactive architecture planning (outline only)

Phase 2 adds a project-scoped visual tour of the product's components, responsibilities and dependencies. It gathers expected usage, scalability needs, cost envelope, hosting constraints and resiliency targets, then presents architecture options with assumptions and unresolved decisions. An accepted plan can propose unit/layer declarations, limits and verification groups for the Phase 1 policy. It depends on Phase 1's versioned contract, measured baseline, results panel and human activation/exception mechanisms; a diagram alone does not establish an enforced boundary. Planning output remains a draft until accepted and converted into supported checks. It cannot provision hosting, spend money, change credentials, deploy or bypass human gates. No Phase 2 screen flow, provider recommendation engine, infrastructure generator or ticket breakdown is specified here.

## 12. Open decisions for Dave

These are real product choices. Marlow has not selected them. Dave records his selections in the item's journal before affected builder dispatch; an irreversible or human-gate change still follows Ron's existing approval rules. The options' session costs are estimates, not quotations.

| ID | Options and costs | Recommendation and consequence |
| --- | --- | --- |
| D1: v1 structural language coverage | **A. Python only:** omit DH-4, save roughly 1–2 builder sessions and a parser packaging dependency; Clayrune frontend receives only generic size checks and review. **B. Python + JS/TS:** include DH-4 and parser distribution, roughly 1–2 extra sessions; covers dogfood frontend and common web projects. Other languages remain explicitly unsupported. | **B recommended.** Frontend monoliths are among the audit's largest hotspots; Python-only must be labelled a narrower first release. |
| D2: enforcement profile and adoption | **A. Activate strict limits after a baseline preview:** draft 600/300/80 line limits, zero growth above legacy caps, required verification and structural review; immediate merge holds and occasional one-time exceptions. **B. Begin in Preview:** identical checks and proposed limits, collect findings before a later human Enforce activation; fewer interruptions initially, but no standards-based merge prevention until activation. Both retain human-only policy changes and explicit exceptions; UI supports both modes regardless. | **A recommended for Clayrune dogfood, with user activation.** It delivers the requested enforcement without forcing a rewrite. Measure the first preview before adopting numerical caps; no arbitrary probation period or agent-controlled mode switch. |

Implementation mechanics proposed here can be revised during contract review. Broader language coverage, automated semantic judges and the architecture-planning UI require new scope decisions; they must not enter Phase 1 through an opportunistic builder addition.
