# Missing hivemind character assignments (BUILTINS-4 HR-9)

Branch implementation of Ron's 2026-11-01 amendment to Q2. A well-formed
missing character no longer refuses a roster plan or a fresh worker/reviewer
attempt. Malformed references, unreadable roster files, unusable engines,
quota/auth refusals, isolation failures and review ownership holds still refuse.
No provider retry, orchestrator-model rewrite or review-gate change is included.

## Policy and configuration

`mc/hivemind_fallback.py` owns pure policy, injected lookup and reference
parsing. The order is named character, configured character for the work type,
then a generic worker without a character body, name, avatar or character skills.
An existing character with an unusable engine refuses instead of advancing
to another fallback. A mapped character that is also missing advances once to
generic; mappings never recurse.

Optional workstream/planner fields:

| Field | Values | Default |
|---|---|---|
| `work_type` | `code`, `review`, `research`, `ui`, `analysis`, `other` | `other` |
| `complexity` | `simple`, `standard`, `hard` | `standard` |

Routing retains explicit fields through ordinary units, dependent holds and
roster payloads. All three roster write paths persist the fields; active
attempts cannot change them. Reviewers use `work_type=review` and the policy's
default `standard` complexity.

The generic engine uses the run's `worker_provider` (ordinary project/global
provider inheritance when unset) and the shared engine resolver's tier tracking:
`simple: tier:fast`, `standard: tier:balanced`, `hard: tier:best`. The manifest's
`worker_model` is not inherited. Each new attempt resolves the current tier head.

The two optional config maps are allowlisted for global config writes and
accepted in roster create and manifest config updates. Run maps override global
maps by key. Both ship as `{}`, with no operator character names:

- `hivemind_role_fallbacks`: work type to `global:<file-stem>` reference.
- `hivemind_complexity_tiers`: complexity to `tier:fast`, `tier:balanced` or
  `tier:best`. Unspecified complexities keep their built-in tier.

Global writes retain the existing human/passcode gates. Neither map changes
the orchestrator's engine or the reviewer's independence requirements.

## Disclosure, pins and recovery

Create and update responses return `warnings[]` naming unknown requested
characters. The pure routing validator also returns these warnings. Validation
preflights the engine but does not pin the chosen fallback; the launch boundary
reads the current record once for both engine and persona.

`resolved_assignment` adds `requested_character`, `substituted`,
`substitution_step` (`role_fallback` / `tier_fallback`),
`substitution_reason` (`missing_at_attempt` / `never_defined`) and `tracking`.
The step and reason are empty strings when no substitution occurs.
Its `character_ref` is the actual character, or `''` for the generic worker.

The workstream's `character` and the session/log's `character_ref` retain the
requested reference for ownership and same-attempt recovery. The character
snapshot contains the actual persona, or empty name/body/skills for generic.
Recovery compares the requested pin and uses the saved prompt/engine; it does
not run fallback again or emit another substitution event. Pre-HR-9 assignment
pins retain their original recovery contract.

Server-owned `character_known` / `reviewer_character_known` evidence remembers
whether the requested reference existed at validation; successful worker
attempts also retain that observation. `missing_at_attempt` uses known
existence evidence; otherwise `never_defined` describes an unknown request.
No global character deletion history is inferred or reconstructed.

Each fresh substituted attempt appends a `character_substitution` event to the
existing run bus with role, workstream/session identity and full assignment,
then emits the event over SSE. Failure to persist disclosure refuses dispatch.
HR-8b detail, list and run-role projections expose the requested/actual identity
and all substitution fields without changing acceptance evidence. HR-8a shows
“requested X → ran as Y” for workers and reviewers, using “generic worker” when
there is no persona; the effective engine is shown alongside it.

## Verification and delivery

The tests use temporary character directories and recording runtimes. No live
model calls, logins, character changes or operator config writes are required.
The browser smoke serves this branch's static files with controlled API fixtures.
Results and exact commands are recorded in the HR-9 task journal. This is a
branch candidate; no merge, push, restart or live-run verification is claimed.

Final verification (2026-11-01): all requested hivemind and engine-selection
files passed (864 test markers, rc=0), followed by 447 focused checks after
the recovery regressions and 49 projection checks after the final identity
pin correction. Pyright on all six affected leaf modules reported 0 errors.
Both branch-served browser smokes passed, including substitution disclosure
at 1440px and 390px.

The commands below were executed through `_scratch/run_check.py`, a foreground
runner that registers itself and its child with Clayrune, expands file globs
on Windows and saves the child output. It is local scratch tooling, not a new
runtime dependency. The exact wrapper invocations and return codes were:

| Command | Result | rc |
|---|---|---|
| `python _scratch/run_check.py hr9-pytest.log python -m pytest tests/test_hivemind*.py tests/test_engine_selection*.py -q --tb=short` | 864 passed | 0 |
| `python _scratch/run_check.py hr9-final-focused.log python -m pytest tests/test_hivemind_fallback.py tests/test_hivemind_roster.py tests/test_hivemind_routing.py tests/test_hivemind_roster_worker.py tests/test_hivemind_reviewer.py tests/test_hivemind_run_roles.py -o addopts= -q --tb=short` | 447 passed | 0 |
| `python _scratch/run_check.py hr9-final-projection.log python -m pytest tests/test_hivemind_run_roles.py -o addopts= -q --tb=short` | 49 passed | 0 |
| `$env:PYRIGHT_PYTHON_FORCE_VERSION='latest'; python _scratch/run_check.py hr9-pyright-touched.log pyright mc/hivemind_fallback.py mc/hivemind_roster.py mc/hivemind_routing.py mc/hivemind_worker.py mc/hivemind_reviewer.py mc/hivemind_run_roles.py` | 0 errors, warnings or informations | 0 |
| `python _scratch/run_check.py hr9-roster-smoke.log node tools/smoke/hivemind-roster.mjs` | desktop/mobile disclosure and existing review checks pass | 0 |
| `python _scratch/run_check.py hr9-boot-smoke.log node tools/smoke/boot-smoke.mjs` | 7 boot scenarios and all additional guards pass | 0 |
| `git diff --check` | clean | 0 |

The broad test invocation predates four final worker recovery/log regressions;
the focused run covers those additions and the projection rerun covers the last
requested-identity correction. Initial red development runs were corrected;
the table reports the final applicable checks, not those preliminary results.

Delivery is blocked before commit: the automatic steward fence rejected the
required non-recursive removal of the smoke dependency junction as a destructive
delete outside scratch. No alternate deletion was attempted. The junction must
be removed from a fresh human chat before committing or removing the worktree.
No npm install was run. There are no open HR-9 design questions.

After committing, rollback by reverting the HR-9 commit to restore strict missing-character refusal
for new plans/attempts. Existing records and pinned prompts are retained.
