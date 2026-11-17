# Agent routes and session lifecycle review, pass 2 of 8 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `40fcd230`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function, or a read-only
count) or **read, not measured**. `server` was never imported, the live server
was sent nothing but `GET`, and no process that binds a port or launches a
provider CLI was started.

Scope: `mc/blueprints/agent_routes.py`, `mc/agent_worktree.py`,
`mc/delegation_delivery.py`, `mc/delegation_settlement.py`,
`mc/delegation_transcript_reconcile.py`, `mc/delegation_waiting.py`,
`mc/delegation_wake.py`, `mc/delegation_wake_absent.py`,
`mc/delegation_retry.py`, `mc/merge_review_gate.py`, `mc/cold_review.py`,
`mc/cold_review_gate.py`, `mc/cold_review_runner.py`,
`mc/engine_pace_dispatch.py`, `mc/behavior_tail.py`,
`mc/session_mcp_config.py`, `mc/session_mcp_launch.py`,
`mc/session_provenance.py`. `mc/agent_runtime.py`, `mc/core.py` and
`server.py` were pass 1 (`docs/_review/2026-11-16_01_runtime_core.md`); its
findings are not repeated, and where a finding here is the call-site half of
one of them the pass 1 id is named.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology.
Severity, same scale as pass 1: **blocker** = wrong result or lost data on a
normal path today; **high** = wrong result or lost data on a reachable path, or
a cost paid on every turn/dispatch/poll; **medium** = narrower trigger, or a
latent defect behind a disabled flag; **low** = hygiene.

Written incrementally, one commit per section. The ranked summary and the
coverage statement are written last; until they exist, this file is a partial
report and the last section present is where the pass stopped.

## Ranked summary

_Written at the end of the pass. Findings are in the sections below._

---


## 1. `mc/blueprints/agent_routes.py` lines 1-4733

Module helpers, process tracking, worktree hooks, provider env and auth
routes, stream observers. Six candidates from this stretch depend on a call
site further down the file and are reported in the section that reaches it.

#### R1 · medium · L/M — a provider-env file that fails to parse is read as empty, and the next save erases every other provider's keys

`mc/blueprints/agent_routes.py:2553-2559` returns `{}` for a missing file
**and** for any read or JSON error, with no log. `agent_provider_set_env`
then does load, `setdefault(name, {})[key] = …`, save
(`agent_routes.py:2648-2651`). So one unparseable file plus one "save key"
click rewrites the store with exactly one key in it: every other provider's
saved credential is gone, and the route answers `{'ok': True}`.

The store makes its own corruption reachable: `_save_provider_env_file`
(`agent_routes.py:2562-2566`) is a plain `write_text`, not
`write_json_atomic`, so a kill mid-write leaves a truncated file, which is the
unparseable case. It also hardens permissions only after the secret is already
on disk (`:2566`).

Same class as pass 1 A4 (state file read as empty then saved over). Read, not
run.

Smallest fix: write with `write_json_atomic`; make the loader distinguish
"missing" from "unreadable" and have `set_env` refuse with a 500 on the
latter instead of saving. Log the parse error.

Test: none. `tests/test_provider_env_scope.py` has no corrupt-file or
truncated-file case.

#### R3 · medium · L — an unreadable incognito project record is silently replaced with a blank one

`agent_routes.py:709-713`: if `_incognito.json` exists but fails to read or
parse, `except Exception: pass` falls through to `:720-738`, which builds a
fresh record (`'activity_log': []`, `'backlog': []`, default name) and writes
it over the file. Callers are the dispatch and send paths, so the overwrite
happens on the next incognito message, not on an explicit reset.
`core.load_project` has the opposite rule for every other project (a bad file
is left on disk untouched). Three swallowed exceptions on state-file I/O in
one function (`:712`, `:718`, `:739`), against the project policy.

Smallest fix: on a parse failure of an existing file, log and return an
in-memory record without writing.

Test: none found for the corrupt-record path.

#### R10 · medium · E — every Read, Edit, Write and Grep tool call re-parses the whole project record on the reader thread, and the feature flag is checked only after the parse

`_observe_memory_push_input` (`agent_routes.py:4479-4482`) calls
`load_project` whenever `extract_input_query` returns a query, and
`mc/memory_push.py:127-140` returns one for any `Read`/`Edit`/`Write` with a
`file_path` and any `Grep` with a pattern. That is most tool calls of a
coding turn, not "the overwhelming majority produce no query"
(`mc/memory_push.py:331-333`). The result side
(`agent_routes.py:4497-4500`) adds another parse for every tool result with an
error-looking line, and `_observe_negation_interrupt` (`:4457`) one more for
each `Agent` dispatch or docs write.

`memory_push.enabled()` is tested inside `observe()`
(`mc/memory_push.py:334`), after the caller has already loaded the project,
so `memory_push_mode: off` still pays the parse.

**Measured:** `json.loads` of the largest project record on this install
(2.69 MB) is 8.8 ms (5-run mean). A 60-tool-call turn pays roughly half a
second of parsing for a report-mode log. This is a call site of the still-open
2026-09-10 E2 (uncached `load_project`), added after that review.

Smallest fix: test `_memory_push.enabled()` before `load_project`, and pass
the fields `observe` needs from the session instead of the full record.

Test: none measures or bounds it.

#### R13 · medium · L — a Claude auth probe that times out leaves a stale "signed in" in place

`agent_routes.py:3538-3540` says an explicit check must fail closed and never
leave a stale `ok: true`. The `TimeoutExpired` branch (`:3543-3545`) and the
generic `except` (`:3548-3550`) do exactly that: they stamp `last_probe_at`
and nothing else, so `ok` keeps its previous value and the freshly stamped
time makes the stale reading look current. The generic branch also drops the
exception without a log. The probe is `subprocess.run(timeout=…)` on the CLI
shim, so on Windows a hung probe is also a call site of pass 1 A2 (the
timeout kills the shim, not the node child).

Smallest fix: call `_mark_claude_auth_error('unknown', …)` in both branches.

Test: none found for the timeout branch.

#### R2 · low · L — clearing a saved key does not "let the shell value take over"

`agent_routes.py:2653-2654` assigns `os.environ[key]` unconditionally, so a
value the server was started with is overwritten on save. `:2655-2657` then
pops the key on an empty value with the comment "let shell env take over";
the shell value was already overwritten and is gone until a restart. The
startup hydrate has the opposite, documented rule (shell-set variables win).

Smallest fix: remember the pre-save `os.environ` value per key and restore it
on clear, or correct the comment.

#### R4 · low · M — `_kill_pid` reports success whatever `taskkill` returned

`agent_routes.py:645-647` returns `True` without reading the return code, so
"access denied" and "no such process" both read as killed. `:648-649`
swallows the subprocess exception without a log.

#### R5 · low · L — second copy of the Windows liveness probe pass 1 found wrong (call-site half of A6)

`agent_routes.py:618-628` is the same `OpenProcess`-succeeds-means-alive test
as pass 1 A6: an exited process that anyone still holds a handle to reads as
alive. This copy feeds `_live_registered_processes`, which defers worktree
removal while it reports a live process. Fix with A6, in one shared helper.

#### R7 · low · E — `_compute_code_delta` dumps the full history twice to subtract one from the other

`agent_routes.py:1576` (`rev-list --branches HEAD`) and `:1581`
(`rev-list <base>`) each return every reachable commit, built into two Python
sets per isolated completion. **Measured** on this repository:
`rev-list --branches HEAD` is 4,941 commits, 203 KB, 26 ms. Small today,
linear in history. Smallest fix: one `rev-list --branches HEAD --not <base>`.

#### R9 · low · E — `_register_process` parses the whole project record to read its name

`agent_routes.py:1868-1873`, once per spawned process; exception swallowed
without a log. Call site of 2026-09-10 E2.

#### R14 · low · L — an expired captured login is killed without its process tree, and finished ones are never removed

`agent_routes.py:3719` is `proc.kill()` on a CLI shim (the node child survives
on Windows; same shape as pass 1 A2). `:3716-3717` returns early for a session
that already exited, so its entry stays in `_captured_login_sessions` until
the next login for that provider replaces it.

#### R15 · low · M — request bodies used without a type guard

`agent_routes.py:2011-2012` (`request.get_json() or {}` then `.get`: a JSON
list body is a 500), `:3871-3872` (`.strip()` on a non-string `code`),
`:2630` (`.strip()` on a non-string `key`). Each is a 500 instead of a 400,
nothing worse. Further instances are added to this finding by later sections
rather than raised separately.

#### R16 · low · E — the provider list reads the same 512 KB log tail once per runtime

`agent_routes.py:2497` calls `_recent_quota_failures(rt.name)` inside the
per-runtime loop; each call seeks and reads the last 512,000 bytes of
`clayrune.log` and regex-scans it (`:12465-12485`). Eight runtimes are
registered (`mc/agent_runtime.py:11523-11530`), so one list load is eight
reads of the same bytes. **Measured:** the scan alone is 0.6 ms per call on a
synthetic 512 KB chunk; the reads are not measured. Smallest fix: read and
group by provider once, before the loop.

**Sound in this stretch:** the env-var name allowlist
(`agent_routes.py:2634-2645`) does stop the route being used to overwrite
`PATH`/`HOME`; load and save run under `_provider_env_lock`
(`:2648-2651`). The three stream observers keep their never-raise contract
and log what they catch (`:4464-4465`, `:4487-4488`, `:4505-4506`), which is
the project exception policy applied correctly.
