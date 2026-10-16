# Dev harness pinned guidance delivery (DH-M2)

MC-960 / d813ea78; builds on `DEV_HARNESS_POLICY.md` without changing its policy
or evidence formats. This is the M2 backend contract, not MVP acceptance.
No real upstream guidance was downloaded/installed in the implementation session.

## Owners and boundaries

`mc/dev_harness_sources.py` selects only enabled practices' unchanged Addy
guidance at `aeac41d4d005999432bb7312f7ff06383a91d59d`. It owns plans, transitive
local Markdown/backtick resource closure, provenance, install verification and
the managed-job entry. Optional providers, scanners and native tool installation
are unsupported here. The existing import guard remains community-tier,
`force=False`; no learning authority guard, passcode or caller gate is relaxed.

`mc/dev_harness_context.py` renders the frozen active snapshot into the existing
shared context builder, independently of full/lean model profile and job shape.
It appends mandatory selected practice summaries, numeric limits (or unset),
stages and check contract/coverage; never the whole catalogue or upstream bodies.
It supplies a fingerprint for cached Claude resumes, including changed/missing
installed resources. Fresh/resume persona and incognito handling remain owned
by the existing paths. Generated vendor files are not modified: the shared
injected context is the delivery mechanism on Claude, Codex, Gemini and Qwen.

`mc/skills.py` retains Git staging and the import guard. Its narrowly scoped
`import_preserved_project_tree` variant scans the selected closure, then copies
bytes without frontmatter rewriting. Git staging can fetch a 40-character SHA,
detach and verify HEAD, and records actual origin/commit/subpath before removing
`.git`. Immutable checkout file hashes also bind inspection to the recorded
checkout, and automatic CRLF conversion is disabled for that checkout. Changed
staged files require staging again. Branch/tag imports keep their existing
behavior with resolved provenance.
No plugin installation, global skill writes, builtin trust or overwrite option
is used. `agent_routes.py` changes are composition-only context calls and an
internal data parameter on the existing managed-job entry; it retains session
ownership, incognito/sandbox refusals, registration and completion delivery.

## Inspection and installation

These are new backend endpoints under `/api/project/<id>/dev-harness`:

| Method/path | Contract |
| --- | --- |
| POST `/install-plan` | `{draft, staging_id}` inspects an existing full staged pinned checkout. Returns/persists the exact selected source list, closure file hashes, MIT notice, content digest, project destination, disk estimate, existing artifact/lock identity, zero further network/cost, execution entry and platform-verification gaps. No download, install or activation. |
| POST `/install` | `{passcode, plan_digest, session_id}` requires the existing human caller test and per-call proof; resolves the stored exact plan and starts the existing registered job in that receiving project's session. No client command, force flag or source body is accepted. Returns the normal job ID/PID, not a claim of successful installation. |

The existing `/api/skills/import/git` flow stages the full repository with
`url=https://github.com/addyosmani/agent-skills`, `ref=<the immutable SHA above>`,
`auto_install=false`. That staging network operation requires the task's own
authorization; this session did not perform it. A trimmed subtree or wrong
origin/commit cannot produce an M2 plan. For the execution entry, substitute the
returned `plan_digest` for its clearly labelled self-reference placeholder;
the install endpoint constructs the exact argv itself and shell-quotes each
argument. No upstream command is executed. Existing jobs own timeout, process
registration, log and follow-up. Install jobs use PowerShell on Windows and sh
elsewhere. A frozen packaged executable's `-m` behavior remains unverified.
Failed jobs return a structured repair code for quarantine/scanner failure,
destination conflict, state/hash mismatch, invalid resources or I/O. Raw source
text, scanner findings, exception messages and passcodes are suppressed.

Local resource links retain repository-relative layout, including resources
outside an individual skill subtree. Markdown links/reference definitions and
explicit backtick paths to references/resources/scripts/assets are followed
transitively; directories include their subtree. External documentation links
are retained but never downloaded. Missing resources, path escapes, unsupported
schemes, symlinks/junctions, unselected skills and plugin component references
are refused. The closure is bounded to 2,000 files / 25 MB total / 2 MB per file;
it cannot hide a large text file beyond the existing guard's scan threshold.
An unsupported upstream reference notation must be inspected before accepting
real source delivery; fixtures do not prove all real pack references resolved.

Project artifacts:

```text
.clayrune/dev-harness/guidance/<sources-digest>/LICENSE
.clayrune/dev-harness/guidance/<sources-digest>/skills/<original-name>/SKILL.md
.clayrune/dev-harness/guidance/<sources-digest>/<referenced resource paths>
.clayrune/dev-harness/sources.lock.json          portable proposal, no activation
```

The source lock includes exact provenance/licence, original relative file paths
and hashes. A plan binds the prior lock's bytes so a subsequent edit requires
inspection again. Existing artifacts are reused only if byte-identical; failed
partial copies and user edits are preserved, never overwritten/deleted. Plans,
human approval markers and verified installation records are under the existing
trusted `data/dev_harness/<id>/` runtime root, outside `data/projects/` and public
builds. Passcodes do not persist or enter the job command. Direct unrestricted
filesystem edits are outside the existing trust boundary; these are not signed
records or an execution sandbox.

The verified receipt is written after closure scan/copy/hash verification and
portable lock persistence. Every delivery and Enforce activation revalidates
installed resources against the selected active digest. A missing/partial or
quarantined import has no receipt; a changed/deleted file or mismatched source
selection is Error, never a green guidance result. Preview can still contain
uninstalled guidance, explicitly Pending/Error. M1's check coverage remains
Unsupported; installation cannot manufacture native-check or reviewer evidence.
The M3 runner must bind its measured evidence to this verified source digest.

Context pointers use the project's registered canonical path; dispatched
worktrees can read the same immutable resources without another installation.
No worktree link or global vendor setting is created. The original skill body
is loaded on demand once by the consuming agent. Prompt delivery itself cannot
enforce reading or prove a successful check. Active revisions explicitly
supersede earlier summaries while preserving the session's persona.

## Verification and remaining acceptance

```powershell
python -m pytest tests/test_dev_harness_vendor_delivery.py
pyright mc/dev_harness_sources.py mc/dev_harness_context.py mc/blueprints/dev_harness_routes.py
```

Tests use temporary source trees, human-approval/receipt fixtures and mocked
job launch; actual import guard scanning is exercised, including transitive
malicious content and scanner crash. Full/lean, incognito and fresh/resume
contexts cover all four vendors and revision/resource invalidation. No installed
upstream pack, live vendor CLI invocation, real managed install job, network
installation, packaged app or multi-platform delivery probe ran. All four real
vendor probes and actual pinned-pack resource closure/guard compatibility are
required before M6 MVP acceptance; no claim of real delivery is made here.

M1's existing packaged catalogue-data gap remains open. This branch adds no
runner, checker, UI, merge hold, activation, CI protection or production change.
No merge, push or server restart is authorized for this builder session.

Final branch verification:

- `python -m pytest tests/test_dev_harness_vendor_delivery.py`: **65 passed**,
  rc=0, 30.03s. This final run covers the completed source/context modules and
  structured job-error redaction; no tests skipped.
- `pyright mc/dev_harness_sources.py mc/dev_harness_context.py mc/blueprints/dev_harness_routes.py mc/skills.py`:
  **0 errors, 0 warnings**, rc=0, after the final code change.
- `python -m pytest tests/test_dev_harness_vendor_delivery.py tests/test_dev_harness_catalogue.py tests/test_dev_harness_store.py tests/test_dev_harness_routes.py tests/test_resume_persona.py tests/test_context_profile_by_model_tier.py tests/test_context_profile_by_job_shape.py tests/test_vendor_context_sync.py tests/test_skill_import_guard.py tests/test_skills_staging_path.py tests/test_agent_jobs.py`:
  **451 passed**, rc=0, 115.75s. This broader run preceded the reserved
  incognito-pseudo-project fixture and final structured CLI error reporting;
  both changes are covered by the final 65-test run.
- `git diff --check`: rc=0. No static asset changes, so boot/feature smokes do
  not apply. The old M1 absence assertion now excludes the two delivered M2
  endpoints; exceptions and preview remain absent.

The global plan file write was refused by the unattended fence; the plan and
implementation/verification log are in the worktree's ignored
`docs/_journal/d813ea78-dev-harness.md`. Engram tools were unavailable; no global
configuration/setup or memory-index edit was attempted. Dave must copy the
worktree journal when integrating. There are no open scope/design decisions.
