# Runtime core review, pass 1 of 8 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `e7f2a2e4`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function, on Windows,
Python 3.14) or **read, not measured**. `server` was never imported and no
process that binds a port or launches a provider CLI was started.

Scope: `mc/agent_runtime.py`, `mc/runtime_attempt_owner.py`,
`mc/runtime_lifecycle_service.py`, `mc/provider_allowance.py`,
`mc/provider_env_scope.py`, `mc/provider_preferences.py`, `mc/core.py`,
`server.py`. `mc/blueprints/agent_routes.py` is pass 2 and was opened only where
a finding in scope depended on a call site there.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology.
Severity: **blocker** = wrong result or lost data on a normal path today;
**high** = wrong result or lost data on a reachable path, or a cost paid on
every turn/dispatch; **medium** = narrower trigger, or a latent defect behind a
disabled flag; **low** = hygiene.

Written incrementally. The summary table is rebuilt at each append; a section
that exists below is finished, a file with no section has not been reported yet
(see "Coverage" at the end).

## Ranked summary

_Rebuilt at each append. Current as of: small modules + `mc/core.py`._

| Rank | Id | Finding | Lens | Severity |
|---|---|---|---|---|
| 1 | C1 | `_atomic_write_text` reuses one temp name per process, so two threads writing the same path tear the target (measured) | L, M | high |
| 2 | L3 | Lifecycle bridge cannot close an attempt that exits before init or without an integer rc; the attempt stays active and blocks every later dispatch of that conversation (latent, service disabled) | L | medium |
| 3 | L1 | The lifecycle service is hard-wired `enabled=False` with stub callbacks; 520 lines plus per-dispatch wiring run only under tests while looking live | L, M | medium |
| 4 | L6 | `revoke_conversations` stops at the first revision conflict, leaving later ids un-fenced and returning no partial list (latent) | L | medium |
| 5 | L2 | Lifecycle errors are appended to `session['_lifecycle_errors']`, which nothing reads or logs (latent) | M | medium |
| 6 | L8 | A spawn failure inside the bridge loses its cause; `CLINotInstalledError` becomes `LaunchUncertain` (latent) | L | low |
| 7 | C2 | `_log` guards only `UnicodeEncodeError`; an `OSError` from a closed or full stdout still aborts the caller | L | low |
| 8 | C3 | `_harden_secret_perms` ignores the `icacls` exit code and swallows every failure without a log line | M | low |
| 9 | P1 | `background_model` resolves a tier differently from `resolve_tracking_request` and trusts stored value shapes | L, M | low |
| 10 | L5 | `RuntimeAttemptOwner.reconstruct` hard-codes `'codex'` in the provider-neutral owner and has no production caller | L | low |
| 11 | L7 | `ensure_project_generation_schema()` runs on every `bridge_factory` call (latent) | E | low |
| 12 | C4 | `TimestampedLines` leaves `remove`/`sort`/`reverse` un-overridden; an in-place item assignment re-stamps the line as produced now | L | low |

---

## 0. Prior audits: what is still open in this scope

_To be completed after `mc/agent_runtime.py` and `server.py` are read (the open
items live there)._

---

## 1. `mc/core.py` (332 lines, read fully)

### C1 · high · L, M — `_atomic_write_text` is not atomic against a second writer in the same process

`mc/core.py:71` builds the temp path as `.{name}.tmp{os.getpid()}`. The name is
unique per process, not per call. Two threads writing the same target share one
temp file: each opens it with `'w'` (truncate), both write at offset 0, and
whichever calls `replace` first moves a file the other is still writing or has
partly overwritten.

**Measured** (`_scratch/` script, two threads, 400 writes each to one path, one
payload 300,000 bytes and the other 120,000, a third thread reading the
target): 34 of 800 writes raised `FileNotFoundError`, and **4,471 of 45,505
reads of the target returned content that was neither payload** (a short write
laid over the head of a long one). With equal-length payloads the tear is
invisible and only the `FileNotFoundError` shows (95 of 600).

The sibling helper does not have this defect: `mc/atomic_json.py:149-151` uses
`tempfile.mkstemp` with a random part, and its docstring (`:133-145`) explains
why. Two helpers that both promise an atomic write, and only one delivers it
under concurrency.

Whether it bites depends on the caller holding a lock across the whole write.
One that does not, opened to confirm: `mc/process_ledger.py:114-123` takes
`process_tracker_lock` to build the snapshot and releases it before the write,
and `:124-125` swallows the exception. Two dispatches registering children at
the same moment (a workflow fan-out) can leave `mc_child_pids.json` torn or
holding the older snapshot; that file is what the next startup reads to reap
strays. 32 call sites use the helper (`grep`, not each one audited for locking);
the two inside this scope are judged in the `agent_runtime.py` section.

**Fix:** have `_atomic_write_text` take its temp name from `mkstemp` the way
`write_json_atomic` does (keep the `.tmp{pid}` tail so
`sweep_orphan_tmpfiles` still matches), or make it a thin wrapper over one
shared implementation.
**Test coverage:** none. `tests/test_core_paths.py` uses threads for
`path_is_within` only; no test writes one path from two threads.

### C2 · low · L — `_log` can still cancel the work it describes

`mc/core.py:41-55`: the handler catches `UnicodeEncodeError` only. The comment
at `:44-50` states the invariant ("a log line must never be able to cancel the
work it is describing") and cites the incident. `print` also raises `OSError`
when stdout is a pipe whose reader has gone or a redirected file on a full
disk, and `ValueError` on a closed stream; both propagate out of `_log` into
the same completion paths. Read, not measured.
**Fix:** catch `Exception` around the first `print` as well and drop the line.
**Test coverage:** none found for a raising stdout.

### C3 · low · M — `_harden_secret_perms` hides a failed hardening

`mc/core.py:129-140`: `subprocess.run(['icacls', ...])` has no `check=True` and
its return code is discarded, and the whole body sits in `except Exception:
pass`. A failed grant (unresolvable `USERNAME`, `icacls` missing from `PATH`)
leaves a secrets file with inherited ACLs and no trace. The project's
exception-swallowing policy names subprocess calls as the case that must log.
Read, not measured: a probe with a bogus user on a scratch file was blocked by
the steward fence and was not retried.
**Fix:** keep swallowing, log on exception and on a non-zero return code.
**Test coverage:** none.

### C4 · low · L — `TimestampedLines` edge cases

`mc/core.py:162-254`. `remove`, `sort`, `reverse` and `__imul__` are inherited
from `list` and would leave `.ts` out of step; no call site uses them on a log
buffer today (`grep` over `mc/` and `server.py`). `__setitem__` with an integer
key (`:247-251`) re-stamps the line as produced now, so an in-place edit of an
old line moves it to today for the day-divider logic the class exists to
protect.
**Checked and cleared (measured):** concurrent `append` against `[-50:]`
slicing, one writer and three readers for 8 s, produced zero length mismatches
and zero `ValueError`. `append` costs 1.05 µs against 0.02 µs for a plain list
(the `now_iso()` call); a 1,500-line slice costs 8 µs. Neither is a finding.

### Sound in `mc/core.py`

`path_is_within` (`:305-332`) resolves both sides and logs on failure;
`record_backlog_status_change` (`:260-283`) logs when its cap bites;
`sweep_orphan_tmpfiles` (`:79-119`) is age-gated and its pattern matches both
temp-name families; `time_ago` returns the input on a naive timestamp instead
of raising.

---

## 2. `mc/provider_allowance.py`, `mc/provider_env_scope.py`, `mc/provider_preferences.py` (94 + 92 + 222 lines, read fully)

### P1 · low · L, M — two tier resolvers that disagree, and unguarded stored values

`mc/provider_preferences.py:77-86` (`resolve_tracking_request`) raises
`ValueError` when a `tier:` request has no model. `:198-199`
(`background_model`) resolves the same kind of value with
`get_runtime(provider).latest_for(value[5:])` and returns whatever comes back,
including an empty value, so Scribe/condense/Distiller would be handed an empty
model name where a foreground launch would have been refused. The same lines
call `value.startswith` on a value read from `config.json` without checking it
is a string, and `get_runtime` raises `KeyError`
(`mc/agent_runtime.py:1459-1462`) for a provider that has an entry but is no
longer registered. `patch_preferences` only ever stores validated strings, so
the trigger is a hand-edited or downgraded config. Read, not measured.
**Fix:** route `background_model` through `resolve_tracking_request` and fall
through to `automatic_model` on failure.
**Test coverage:** `tests/test_provider_preferences.py` exists; not checked for
an unavailable-tier background case.

### Sound

- `provider_env_scope.py`: the scoping rule (`:55-68`) removes every spelling of
  a guarded name before restoring the Claude-saved or original value;
  `relaunch_env` (`:71-84`) strips hydrated keys so a successor cannot snapshot
  them as its own shell's. The snapshot at `:32-34` is correct only while the
  module is first imported before provider env is hydrated; today the first
  import is the module-level one at `agent_routes.py:71`, ahead of `wire`.
  Whether clearing a saved key also removes it from `os.environ` is
  `agent_routes.py` (pass 2).
- `provider_allowance.py`: `build_weekly_usage` returns fresh dicts so the
  exhaustion overlay cannot leak into the callers' caches (`:34-53`);
  `attach_to_providers` logs and degrades (`:88-94`). `allowance_state.get`
  is an in-memory read under a lock, so one call per provider is not a cost.
- `provider_preferences.py`: `patch_preferences` validates provider, field,
  effort and model before returning anything to persist (`:146-188`);
  `selected_field` honours an explicit empty scoped value (`:126-131`).
  Tests patch `BACKUP_ROOT`, which is computed from the home directory at
  import (`:26`).

---

## 3. `mc/runtime_attempt_owner.py`, `mc/runtime_lifecycle_service.py` (333 + 191 lines, read fully)

Everything in this section is **latent**: see L1. The findings describe what
happens on the day the service is enabled.

### L1 · medium · L, M — the lifecycle service is disabled by a literal, and enabling it as wired breaks every dispatch

`server.py:1070-1076` constructs the service with `enabled=False`,
`authorize=lambda facts: None` and `source_format=lambda facts: ''`. Nothing
reads a config key for it and nothing sets `enabled` afterwards (`grep`). So
`bridge_factory` returns `None` at `runtime_lifecycle_service.py:40-41` on every
dispatch, and the 524 lines of these two modules, the bridge wiring at
`agent_routes.py:11501-11568`, and the two blueprint injections
(`server.py:1091`, `:1925`) execute only under tests. The code reads as live:
it is wired, called per dispatch, and registered with `atexit`
(`server.py:2625`).

Flipping the flag is not enough either: with `source_format` returning `''`,
`runtime_lifecycle_service.py:65-67` raises `ValueError('authorized native
source format is required')` for every non-incognito dispatch, which
`agent_routes.py:11528-11535` turns into a failed session.
**Fix:** say so at the construction site (one comment naming what must be
supplied before enabling), or gate on a config key with real callbacks.
**Test coverage:** tests build their own service, so they cannot see that the
production wiring is a stub.

### L3 · medium (latent) · L — an attempt that dies before init can never be finished

`runtime_attempt_owner.py:189-202`: `_apply_exit` raises when the attempt was
never bound (`:192-193`) or when `rc` is not an `int` (`:196-197`), and sets
`_finished` only after a successful `finish`. A CLI that exits before emitting
init (not logged in, bad flag, crash on start) is the commonest failure there
is, and it lands on the first raise. The attempt stays `RUNNING` and stays the
conversation's `active_attempt`; `execution_lifecycle.py:245-246` then refuses
every later claim with "Existing execution must be resolved before claim".
Result: one failed start permanently blocks that conversation.
**Fix:** on exit without binding or without an integer rc, transition the
attempt to `FAILED`/`UNCERTAIN` instead of raising.
**Test coverage:** `tests/test_authorized_runtime_bridge.py` exists; not
checked for an exit-before-init case.

### L6 · medium (latent) · L — `revoke_conversations` can fence half a set

`runtime_lifecycle_service.py:134-147`: each id is read, then written with
`expected_revision=state.revision`. The service lock held here does not cover
the bridge, which writes through the store directly
(`runtime_attempt_owner.py:172`, `:199`). A transition landing between the read
and the write makes `execution_lifecycle.py:354` raise; the exception leaves
the loop, ids sorted before it are fenced, ids after it are not, and the caller
(`agent_routes.py:17070-17071`) gets no list of which.
**Fix:** per-id try, re-read and retry once, return revoked and failed ids.
**Test coverage:** not checked.

### L2 · medium (latent) · M — lifecycle errors go to a list nothing reads

`runtime_attempt_owner.py:145-148` and `agent_routes.py:11531`, `:11545`,
`:11555`, `:11561`, `:11567` append to `session['_lifecycle_errors']`. `grep`
over `mc/` and `server.py` finds only `setdefault(...).append` sites: no
reader, no log line. A failed native bind or a failed terminal write to the
durable store is the state-file case the exception policy says must log.
**Fix:** `_log` at the append sites.

### L8 · low (latent) · L — the cause of a failed spawn is discarded

`runtime_attempt_owner.py:115-121` runs the real spawn inside the store's
launch callback. `conversation_store.py:992-996` catches any exception from it
with `except Exception: failure = True` and raises `LaunchUncertain` at
`:1010`. A `CLINotInstalledError` (`agent_runtime.py:158-171`, whose docstring
says callers rely on its type) therefore reaches the route as "outcome is
uncertain", and the bridge keeps no copy of the original. The same callback
raises `TypeError` after a successful spawn if the handle has no
`mc_session_id` (`:118-120`), which would mark a running process uncertain and
drop its handle; no runtime returns such a handle today.
**Fix:** capture the exception in `_owned_spawn` and re-raise it from the
bridge after the store has recorded the outcome.

### L5 · low · L — `reconstruct` is Codex-only and unused

`runtime_attempt_owner.py:313` rejects any launch facts whose provider is not
the literal `'codex'`, in a module whose docstring says it is independent of
runtimes. Callers: `tests/test_authorized_runtime_bridge.py:68`,
`tests/test_runtime_attempt_owner.py:83`, and nothing else.

### L7 · low (latent) · E — schema check per dispatch

`runtime_lifecycle_service.py:50` calls
`store.ensure_project_generation_schema()` on every `bridge_factory` call, and
`:119` on every mutation. Once per store instance is enough. Read, not measured.

### Sound

`DispatchFacts` validates and freezes its inputs (`runtime_attempt_owner.py:48-62`);
the pending-callback queue (`:123-148`) correctly holds init/exit that arrive
while the launch transaction is open and replays them in order; a disabled
service opens no database on any path (`runtime_lifecycle_service.py:40-41`,
`:88-89`, `:112-113`); the store opens a connection per call, so `stop()` has
nothing to close.

One note for the day L1 is resolved, not counted as a finding because the call
site is pass 2: `prepare` compares the whole engine key, including `resume_id`
and the provenance map (`runtime_attempt_owner.py:256-257`, key built at
`runtime_lifecycle_service.py:57-64`). If a later dispatch reuses the same
`mc_session_id` with a different resume id, model or trigger id, it raises
`LifecycleConflict` before launch.

---

## 4. `mc/agent_runtime.py` (11,530 lines)

Reported in line-range parts as they were read. Ids `A1…` are stable; the
ranked table at the top is rebuilt when the file is finished.

### 4a. Lines 1-3845: shared helpers, `AgentRuntime` base, registry, `ClaudeRuntime`, process helpers

#### A1 · high · L — `_collect_trailing_reply_text` treats any line that starts with `[` as a status marker and drops reply text

`mc/agent_runtime.py:943-947`: `if stripped.startswith('['): … continue/break`.
The sibling scanner `_last_real_error_line` was fixed for exactly this in MC-935
and now uses `_MARKER_ONLY_RE` (`:849`, `:870`), which matches only a line that
is one bracket pair start to finish. This function kept the loose test, so a
reply line that merely begins with `[` (a Markdown link, a `[file:…]` /
`[browser:…]` marker, a JSON array, a `[x]` checklist) is taken for one of MC's
own markers.

**Measured** (`_scratch` script calling the function):

| `log_lines` tail | returned |
|---|---|
| `'Earlier message.'`, `'[tool: Read]'`, `'[docs](http://x) are here.'` (Claude, one element per message) | `'Earlier message.'` (the previous message) |
| `'See '`, `'[docs](x) for more'` (delta chunks) | `'See '` |
| `'See '`, `'[docs](x)'`, `' for more'` | `' for more'` |
| `'Old answer'`, `'[1, 2, 3]'` | `'Old answer'` |

Callers: the session summary that is persisted and later read by Scribe
(`agent_routes.py:10322-10323`) and a workflow step's result text
(`mc/workflows.py:2236-2238`). Both receive an older message or a fragment as
if it were the final reply, with no error.
**Fix:** use `_MARKER_ONLY_RE.match(stripped)` here too (a marker-only line is
a boundary; anything else is content).
**Test coverage:** `tests/test_codex_run_error.py:222-241` covers delta joining
and tracing lines; no case has reply text starting with `[`.

#### A2 · high · L — Claude transform timeouts do not hold on Windows when the CLI is a `.cmd` shim, and `stream_text` has no timeout at all

`oneshot` without a `deadline` runs `subprocess.run(..., timeout=leg_timeout)`
(`:3289-3304`); `describe_image` does the same (`:3352-3357`). On Windows
`resolve_binary` deliberately prefers the npm `.cmd` shim (`:2124-2129`), so the
child is `cmd.exe` and the CLI is a grandchild. On timeout `subprocess.run`
kills only `cmd.exe` and then calls `communicate()` again with no timeout, which
waits until every holder of the pipe (the still-running CLI) exits.

**Measured:** `subprocess.run([shim.cmd], timeout=2)` around an 8 s child
returned after **8.1 s**. On the review box the Claude binary resolves to a
`.CMD` file, so this is the live path. A CLI stalled on the network therefore
blocks the calling thread (Scribe, condense, Distiller, mail launder, vision
bridge) for as long as the CLI lives, and the "timed out" process is never
killed.

The `deadline` path does this correctly: `mc/transform_process_deadline.py:36-50`
tree-kills (`taskkill /T`, job object, `kill_tree`). `grep` finds no
`run_text_transform` caller in `mc/memory.py`, `mc/distiller.py` or the routes
that passes `deadline=`, so the 14 call sites use the path that does not hold.

`stream_text` (`:3386-3476`) is weaker again: the read loop at `:3427` has no
timeout; stderr is a pipe that nothing drains while stdout is read (`:3409-3410`,
a CLI that fills the stderr pipe deadlocks both sides); and the `finally` at
`:3472-3476` calls `proc.kill()`, which on a shim leaves the CLI running after a
client disconnect even though the process was started with `POPEN_NEW_SESSION`
for exactly that kind of cleanup.
**Fix:** route the no-deadline `oneshot`/`describe_image` through
`transform_process_deadline.run` with `deadline = now + timeout`; in
`stream_text` use `_kill_pid(proc.pid)` in the `finally`, send stderr to a
drained thread or `STDOUT`, and bound the loop.
**Test coverage:** none can catch it; the tests stub `subprocess.run`.

#### A3 · medium · L, M — a transcript scan that hits one exception caches an empty result, and the Documents cache writes it to disk

`list_written_markdown`, `:2861-2890`: any exception while reading or parsing a
transcript sets `hits = []` and then stores `(mtime, size, [])` in
`_DOC_WRITE_CACHE`. A finished transcript never changes `(mtime, size)`, so the
empty answer is permanent for the process, and `_doc_cache_store.mark_dirty`
(`:2896-2897`) persists it, so it also survives restarts. Every document that
session wrote disappears from the Documents tab with no log line. Triggers: a
Windows sharing violation on `open`, or one line that makes `parse_event` raise
(`:2313` does `msg.get('message', {}).get('content', [])`, which raises
`AttributeError` on `"message": null` and `TypeError` on `"content": null`).

`_session_row`, `:2725-2773`, has the same shape (`except Exception: pass`,
then cache at `:2795`): a failed open caches a row with `turns: 0` and an empty
label for a real conversation until the file next changes; one bad line stops
the scan there and caches the partial row. `list_running_subagents`
`:2966-2992` likewise.
**Fix:** on exception, log and return the result without caching it; guard the
two `.get` chains in `parse_event`.
**Test coverage:** not found for the failure path.

#### A4 · medium · L — an unreadable incognito registry is rebuilt from empty by the writer, un-marking every earlier incognito transcript

`mark_transcript_incognito`, `:1827-1838`: if the file exists but does not parse,
`data = {}` and the registry is rewritten holding only the new id (comment:
"rebuild rather than lose the new mark"). The readers treat the same condition
the opposite way: `_load_incognito_registry` returns `None` and
`is_transcript_incognito` answers True for everything (`:1855-1859`,
`:1875-1877`), because an unreadable registry must not release incognito
transcripts. So a corrupt registry is fail-closed until the next incognito
dispatch, and from that moment every previously marked transcript is listed,
backfilled, indexed and summarised as an ordinary one. No log line marks the
rebuild. Read, not measured.
**Fix:** on a parse failure, move the bad file aside (`.corrupt-<ts>`), log, and
keep failing closed for ids that cannot be confirmed; never overwrite it with a
one-entry file.
**Test coverage:** not checked for the corrupt-then-mark sequence.

#### A5 · medium · L — `last_error` is one attribute on a shared runtime object, read after the call by another thread's rules

`oneshot` clears and sets `self.last_error` (`:3282`, `:3285`, `:3306`, `:3311`,
`:3316`), and the caller reads it afterwards to classify the failure
(`run_text_transform` `:1571-1575`, `stream_text` base `:1357-1361`). The
runtime is a process-wide singleton (`_RUNTIMES`), and transforms run
concurrently (one Scribe call per finished session, plus condense, Distiller,
Claydo, the vision bridge). Thread A fails with `rc=1: usage limit…`; thread B
starts and clears the field, or times out and writes `timeout after…`; A's
caller then raises `TransformFailure` with an empty detail, or
`TransformTimeout`, which routes map to 504. The detail is what the allowance
layer reads to recognise exhaustion (`:130-132`). Read, not measured.
**Fix:** return the reason with the result (or raise it) instead of parking it
on the instance; a `threading.local` is the smallest change.
**Test coverage:** single-threaded only.

#### A6 · medium · L — `_pid_is_alive` reports an exited Windows process as alive while any handle to it is open

`:3665-3673` treats a successful `OpenProcess` as "alive". Windows keeps the
process object until its last handle closes, and a `subprocess.Popen` object is
such a handle. **Measured:** child exited with rc 0, `_pid_is_alive(pid)`
returned **True** while the `Popen` object was referenced and False once it was
dropped. Callers that check `proc.poll()` first are unaffected
(`agent_routes.py:14642`, `:18794`, `scheduler_routes.py:1213-1214`); callers
that have only a pid are not (`system_routes.py:168`, `:259`,
`process_ledger.py:174`, `:188`, `agent_routes.py:1653`): a registered process
whose parent shell or CLI still holds its handle is shown as running, and the
startup reaper can treat a dead prior server as alive.
**Fix:** after `OpenProcess`, call `GetExitCodeProcess` and compare with
`STILL_ACTIVE` (259), or `WaitForSingleObject(handle, 0)`.
**Test coverage:** tests mock the function; none exercises it on a real exited
child.

#### A7 · low · L — `apply_mc_tool_blocks` logs "ignored" after it has queued the question

`:762-780`: the question is appended to `pending_questions`,
`waiting_for_question` is set and `paused = True` before the agent-mail hook is
imported and called (`:776-777`). If that hook raises, the handler logs
`[mc:question ignored — malformed block: …]` while the turn is in fact paused
on a pending question. The `mc:todo` branch labels a failing backlog hook as a
"malformed block" the same way (`:794-796`).
**Fix:** give the hook calls their own `try` with an accurate message.

#### A8 · low · E — `ClaudeRuntime.resolve_binary` is not cached; a transform resolves it four times

`:2117` runs `shutil.which('claude')` on every call. **Measured: 1.29 ms per
call** on a 51-entry `PATH`. `claude_oneshot_available` says both of its checks
are cheap because "resolve_binary() is a cached path lookup" (`:1733-1734`); it
is not. One transform resolves four times (`transform_evidence` builds both
argvs and calls `claude_installed`, `:3188`, `:3197-3199`; `oneshot` builds the
argv again, `:3284`), one dispatch twice (`:2199`, `:2218`). About 5 ms per
transform, small next to the spawn; reported because the comment promises
otherwise and `claude_installed()` has 12 call sites.
**Fix:** cache the result for a few seconds, keyed on `PATH`.

#### A9 · low · E, M — two scan caches still evict in the middle of a pass

`_session_row` `:2788-2794` and `list_running_subagents` `:3002-3004` drop the
oldest quarter when the cache is full, during the scan. The comment at
`:1938-1948` documents this exact policy producing a 0 % hit rate for the
Documents cache, and `_trim_scan_cache` (`:1953-1969`) was written to replace
it there. The other two caches were left on the old policy; `_SESSION_ROW_CACHE`
is global across projects, so several large projects polled in turn can pass
4,096 between them. Read, not measured.
**Fix:** use `_trim_scan_cache` with a touched set in all three.

#### A10 · low · L — `maybe_set_sole_provider_default` changes memory before disk and never reconciles

`:1699` sets `config['default_provider']` on the live config dict; if the write
at `:1708` fails the function returns False (`:1709-1711`) with the in-memory
value still set, so the process runs on a default the next restart will not
have. The read-merge-write at `:1700-1708` takes no lock against the settings
route doing the same. The docstring's "idempotent" holds only when exactly one
CLI is installed: with zero or several, every call re-runs
`installed_runtimes()`, a `health_check()` per registered runtime (`:1611-1619`).
Read, not measured; the call site at `agent_routes.py:2410` is pass 2.

#### Sound in lines 1-3845

- `strip_injected_preamble` is bounded (12 passes, `:527`) and its greedy
  handoff alternative backtracks once from the end; no catastrophic pattern.
- `stop_hook_block_kind_before` is bounded at 50 parent hops (`:650`).
- `_authorize_text_transform` refuses before any input is delivered, and
  `transform_evidence` self-checks that both argv builders still carry
  `TRANSFORM_ISOLATION` (`:3197-3200`).
- `list_sessions` reads the incognito registry once per call and drops
  everything when it is unreadable (`:2669-2673`); the must-include / limit
  arithmetic at `:2676-2695` matches its docstring.
- `transcript_path` globs the worktree dirs only after the direct lookups miss
  (`:2523-2551`).
- `_trim_scan_cache` never evicts an entry the current pass touched.
- `_gemini_session_settings_path` fails closed on malformed MCP JSON
  (`:3806-3817`) and uses a content-hashed name, so distinct MCP sets do not
  collide.

---

### 4b. Lines 3846-5433: `GeminiRuntime`

#### G1 · high · L, M — Gemini `oneshot` never looks at the exit code and reports a failed transform as a successful one

`mc/agent_runtime.py:5369-5380`: `subprocess.run(...)`, then
`text = (r.stdout or '').strip(); return OneshotResult(text=text, raw=None)`.
`r.returncode` and `r.stderr` are never read. `run_text_transform` raises only
when the runtime returns `None` (`:1570-1576`), so:

- a Gemini transform that exits non-zero with nothing on stdout (quota, auth,
  network: the CLI's reason goes to stderr) returns `''` to the caller as a
  completed transform, not a `TransformFailure`;
- one that exits non-zero after printing part of an answer returns the fragment
  as the answer;
- the allowance layer never sees the stderr text it would use to recognise
  exhaustion, because `last_error` is not set on this path at all. On the two
  paths that do return `None` (`:5363-5364`, `:5377-5378`) `last_error` still
  holds whatever the previous Gemini call left there (A5), so the failure is
  classified by another call's reason.

`describe_image` twenty lines below does it correctly (`:5417-5421`: rc and
empty text both set `last_error` and return `None`), so the two transform
entry points of one class disagree. The bare `except Exception: return None`
at `:5377` swallows a subprocess failure with no log line (project policy says
log). The 180 s timeout is `subprocess.run` on what is a `.cmd` shim on
Windows, so it does not hold either (A2). Read, not measured: no Gemini CLI was
launched.
**Fix:** copy `describe_image`'s tail: `rc != 0 or not text` sets `last_error`
from stderr and returns `None`; log in the `except`.
**Test coverage:** not found for a non-zero exit; tests stub `subprocess.run`
with rc 0.

#### G2 · medium · L — a follow-up sent while a Gemini turn is still running makes the old reader report a crash and fire `on_process_exit`

`write_followup`, `:5251-5254`: kills the old process, then
`session.pop('_interrupted', None)`. `session['proc']` still points at the old
process until `:5318`, after the binary lookup, prompt assembly and spawn. The
old reader's `finally` (`:5206-5245`) runs as soon as the kill closes stdout and
tests exactly `session.get('proc') is proc`, `status == 'running'` and
`not _interrupted`: all three hold in that window. It sets `status = 'error'`,
appends `[gemini exited with code N]` plus a `[hint]`, sets
`process_alive = False` and calls the `on_process_exit` callback with the kill's
rc, for a turn the user merely superseded. `write_followup` then overwrites the
status with `'running'`, so the log lines and whatever the callback did are what
remain. `interrupt()` documents this exact race and closes it by setting
`_interrupted` first (`:5330-5336`); the follow-up path clears that flag
instead. Read, not measured. Whether the route lets a follow-up reach a running
Mode A turn is pass 2; the runtime method itself does not refuse it.
**Fix:** before the kill, detach the old reader (`session['proc'] = None`, or
set a per-proc "superseded" marker the `finally` checks).
**Test coverage:** not found.

#### G3 · medium · L (security, multi-user hosts) — the tool-free transform's isolation lives at a fixed path in the shared temp directory and is checked once per process

`_transform_home_dir` and `_transform_policy_path` (`:3948-3971`) use
`<tempdir>/clayrune-transform-isolation/…` with `mkdir(exist_ok=True)` and cache
the answer in a class attribute. On a host where the temp directory is shared
between users (any Linux box), whoever creates that directory first owns it:
they can place `gemini-home/.gemini/settings.json` (the file whose absence is
the first isolation layer, per the comment at `:3917-3919`) and replace
`gemini-tool-free.toml` after the one content check at `:3966`. The content is
verified only the first time; after that `transform_evidence` certifies the
profile by checking that the argv *contains the path* (`:4010-4012`), not that
the file still holds the deny-all policy, so a deleted or replaced policy file
is certified as `argv-self-check` passed. A temp cleaner removing the file has
the same effect without an attacker. On a single-user Windows desktop the temp
directory is per-user and this does not apply. Read, not measured.
**Fix:** keep both under the app's own private data directory (0700), and
re-verify the policy file's content in `_transform_argv` (it is 300 bytes).
**Test coverage:** none could catch it; the tests assert the argv shape.

#### G4 · low · L — one unexpected line shape ends the Gemini reader while the process is still alive

`_read_stream` wraps the whole `for` loop in one `try` (`:5003-5174`). Any
exception from the loop body leaves the loop, logs `[stream error: …]`, and the
`finally` calls `proc.wait()` with no timeout (`:5177`). Nothing reads stdout
from then on, so a CLI that keeps writing fills the pipe and blocks, and the
session stays `running` until someone interrupts it. `parse_event` can raise on
shapes it does not guard: `err = msg.get('error') or {}` followed by
`err.get('message')` (`:4250-4251`) raises `AttributeError` when `error` is a
string. That particular event is the last one of a run, so it is harmless
there; the structure is what is reported. `_probe_live_quota` has the same
unguarded shape inside its `except HTTPError` handler
(`viol = (d.get('violations') or [{}])[0]; viol.get(...)`, `:4516-4518`), which
breaks its "never raises" contract.
**Fix:** `try`/`except` per line inside the loop; `isinstance(err, dict)` guards.

#### G5 · low · L — the error classifier reads stale stderr and the agent's own reply text

`_gemini_stderr_tail` is a per-session list that is trimmed to 40 lines but
never cleared at the start of a turn (`:5019-5022`), and the exit path feeds its
last 30 lines plus the last 30 `log_lines` to `explain_exit_error`
(`:5228-5231`). That function is ordered substring matching (`:4899-4950`),
with `'oauth'`, `'quota'`, `'timed out'` and `'permission denied'` among the
needles. So a crash on turn 6 is explained by a rate-limit line turn 2 wrote to
stderr, or by the model's own answer if it happened to mention OAuth. The
OAuth-rejected branch also writes `status: 'oauth_rejected'` into the auth cache
from the same text (`:4901-4908`).
**Fix:** clear the tail in `begin_turn`; classify on stderr and `[gemini error]`
lines only, not on reply text.

#### G6 · low · L — `_slim_system_prompt` matches three anchors and two of them no longer exist

`:4715-4718`, `:4744`: the prefixes `'IMPORTANT — Plan Mode:'` and
`'Questions: When you need to ask the user'`, and the header
`--- CLAYRUNE API REFERENCE ---`. The prompt builder now writes
`"IMPORTANT — Plans: …"` and `"Questions: when you need the user to CHOOSE…"`
(`agent_routes.py:4753`, `:4777`), and `grep` finds no producer of the
`API REFERENCE` header in `mc/` or `server.py`. Steps 1 and 2 are no-ops; only
the `Current task:` removal still fires. The docstring's "degrades gracefully
(less slimming)" is accurate, which is why nothing failed: Gemini has been
receiving the plan-mode and question instruction lines the function says it
removes, on every dispatch.
**Fix:** delete steps 1-2, or anchor them on constants shared with the builder.
**Test coverage:** a test that feeds the old strings would still pass.

#### G7 · low · E — the `mc:` fence check re-joins the whole turn on every delta

`:5034`: the fence test runs `''.join(turn_text_parts)` once per text delta, so
a turn costs O(deltas × turn length). Small in absolute terms for a normal
reply (read, not measured); a rolling window of the last few characters plus
the new delta gives the same answer in O(1).

#### G8 · low · L — the class docstring describes a runtime that no longer exists

`:3849-3860` says the reader "falls back to plain-text", follow-ups use
`--checkpoint` or a one-line summary, and "Tools/MCP/Skills are NOT injected".
The code drops every non-JSON line (`:5018-5023`, which also makes
`parse_event`'s non-JSON branch at `:4147-4152` unreachable from the reader),
resumes with `--resume <id>` (`:5294`), and filters MCP servers through
`--allowed-mcp-server-names` (`:4105-4107`).

#### Sound in lines 3846-5433

- `build_command` never falls through to native MCP discovery: empty or
  malformed input yields the deny sentinel (`:4105-4107`).
- Text deltas are written to `log_lines` as one element per contiguous run and
  flushed before any non-text event and at EOF (`:4985-4993`, `:5030`, `:5185`).
- `interrupt` sets state before the kill, and the reader honours it.
- `auth_probe` is the only path that spends a live call; `health_check` only
  reads the cache it left (`:4367-4372`).
- The tool-name map is bounded at 64 entries (`:5072-5074`).

### 4c. Lines 5434-6977: shared Mode A dispatch, reader, interrupt; usage and cost helpers

#### MA1 · high · L — Stop on any Mode A engine except Gemini is recorded as a crash

`_mode_a_interrupt`, `mc/agent_runtime.py:6844-6852`, kills the process first
and sets `status = 'stopped'` afterwards. `_kill_pid` is synchronous
(`taskkill /F /T`, `:3683-3685`), so the reader's `finally` runs while the
status is still `'running'`. `_mode_a_reader` decides on that alone (`:6791`):
it sets `status = 'error'`, appends `[<engine> exited with code N]` and a
`[hint]` from `explain_exit_error` (`:6818-6826`), and fires `on_process_exit`
(`:6836`). Six runtimes use this function for both `interrupt` and `stop`
(`:7893-7897`, `:9881-9885`, `:10469-10473`, `:10831-10835`, `:11140-11144`,
`:11467-11471`: Qwen, Codex, OpenCode, Goose, Aider, Kiro).

**Measured** (scratch script: a sleeping Python child, the real
`_mode_a_reader` and `_mode_a_interrupt`, a stub runtime), 5 runs of 5:

```
final status: stopped | on_process_exit saw (rc, status): [(1, 'error')]
log_lines: ['hello', '[fake exited with code 1]', '[hint] HINT-FROM-EXPLAIN', '[interrupted]']
```

So every user Stop puts a crash line and a troubleshooting hint ("not logged
in", "rate limit", whatever the tail happens to match) in the chat, and the
exit callback runs with `status == 'error'`. That callback is
`_runtime_log_completion` (`agent_routes.py:10613`): it writes a
`[runtime-error] provider=… model=… ts=…` line, the line the quota-failure scan
parses, and calls `_log_agent_completion` on a session that reads `error` at
that moment. The in-memory status ends as `stopped` only because the
interrupt's write lands last.

`GeminiRuntime.interrupt` documents this exact race and fixes it (`:5330-5336`:
flag and status before the kill, and its reader checks `_interrupted` at
`:5209`). The shared path was not given the same fix.
**Fix:** in `_mode_a_interrupt` set `_interrupted`, `status` and
`last_status_change_time` before `_kill_pid`; in the reader's `finally` skip
the exit line, hint and error status when `_interrupted` is set; clear the flag
at the start of the next turn.
**Test coverage:** none found that runs the reader and the interrupt against a
real child; a mocked `_kill_pid` cannot show it.

#### MA2 · low · L — G4 applies to the shared reader too, and it knows the hazard

`_mode_a_reader` has the same one-`try`-around-the-loop shape (`:6551-6731`)
and the same unbounded `proc.wait()` (`:6734`). It isolates the raw-capture
callback explicitly because "a failed durable write must not leave the provider
blocked on a full stdout pipe" (`:6560-6563`), but every other statement in the
loop can end the read the same way: `Path(fp).name` in `_format_tool_activity`
when `file_path` is not a string (`:6428-6429`), `qs[0].get` when a question is
not a dict (`:6448`), `int(v or 0)` on a carried usage value (`:6543`),
`int(usage.get('input_tokens') or 0)` in `normalize_context_tokens`
(`:6005-6007`). In the Gemini reader, `gemini_chat_files` sorts by
`x.stat().st_mtime` outside its `try` (`:5730`), so a chat file removed between
the glob and the sort raises out of the `TURN_END` branch and skips
`on_turn_end` (`:5117-5124`).
**Fix:** as G4; plus `isinstance` guards in the formatter.

#### MA3 · low · L — the final status is the exit code only

`:6797` (and `:5213` for Gemini): `'completed' if rc == 0 else 'error'`. An
`ERROR`, `AUTH_ERROR` or `ALLOWANCE_EXHAUSTED` event sets `turn_error_seen`,
whose only effect is to *suppress* the empty-turn check (`:6798`). A CLI that
reports a failed turn in its stream and still exits 0 therefore finishes as
`completed`, with the one check that could have caught it turned off. The
file's own comments record two CLIs that keep rc 0 through a failure (Codex
sandbox denial, `:6628-6633`). Not verified per CLI which terminal errors exit
0; read, not measured.
**Fix:** `status = 'error'` when `turn_error_seen and not visible reply text`.

#### MA4 · low · E — the `mc:` fence check is quadratic here as well

`:6607`, same expression as G7.

#### Sound in lines 5434-6977

- `_require_prompt_delivered` disowns the process before killing it, so the
  reader does not write a status for a turn the caller reports failed
  (`:5550-5556`). (This is the pattern MA1 and G2 lack.)
- `codex_turn_context_tokens` re-reads the whole rollout each turn. **Measured**
  on synthetic rollouts: 1.3 ms at 2 MB, 6.6 ms at 10 MB, 28 ms at 41 MB (the
  substring pre-filter at `:5864` does the work); a miss costs the two 0.2 s
  sleeps. Not a finding.
- `accumulate_result_cost` keeps a per-process baseline and treats a total that
  goes backwards as a restart (`:6332-6342`); `accumulate_result_turns` sums and
  rejects bool and negative values (`:6370-6374`).
- `note_api_retry` rate-limits the status line to one per wait or per 300 s and
  still stamps `last_output_time` on every event (`:6157-6174`).
- Non-JSON stray output is kept, protocol JSON a runtime declines is dropped
  (`:6592-6603`).
- Prompt delivery is verified only for Codex; the other engines log a failed
  stdin write and return a live-looking handle. The code says so
  (`:5621-5623`); recorded here as a known gap, not a new finding.

### 4d. Lines 6977-8242: Codex rollout helpers, `QwenRuntime`, Codex module-level helpers

#### Q1 · high · L, M — Qwen `oneshot` has G1's defect and a worse fallback: the raw protocol stream is returned as the answer

`mc/agent_runtime.py:7998-8014`. Like Gemini's (G1), the exit code is never
read and `last_error` is never set; here stderr is also sent to `DEVNULL`
(`:8001`), so the CLI's reason is discarded before anyone could read it. The
return is `OneshotResult(text=last_text or (r.stdout or '').strip())`: when the
run produced no assistant text, the *whole stdout* is returned as the transform
result. With `--output-format stream-json` (`:7937`) that is the JSONL protocol
itself (`system`/`init`, a `result` with `is_error: true`), handed to Scribe,
condense, the Distiller or the mail launderer as if it were their output.
Two more ways a failure becomes content:

- `parse_event` turns any non-JSON stdout line into `ASSISTANT_TEXT`
  (`:7315-7320`), so a plain-text error printed by the CLI is the "answer";
- the profile's own enforcement is an abort (`--max-tool-calls 0` exits 55 with
  `FatalBudgetExceededError`, per the comment at `:7907-7911`), and an aborted
  run's partial text is returned as a completed transform.

`describe_image` in the same class checks `returncode` and empty text and sets
`last_error` (`:8059-8062`). The `except Exception: return None` at `:8007` logs
nothing. Read, not measured: no Qwen CLI was launched.
**Fix:** as G1; never fall back to raw stdout; keep stderr.
**Test coverage:** not found for a non-zero exit or an error `result`.

#### Q2 · low · L — text that shares an envelope with a tool call is dropped, and only the first tool call of an envelope is shown

`QwenRuntime.parse_event`, `:7355-7368`: if the assistant envelope has any
`tool_use` block it returns `TOOL_USE` with the blocks and discards
`text_parts`. The shared reader then logs `blocks[0]` only (`:6616-6617`) and
counts one tool call.

**Measured** (pure function, synthetic envelope with one text block and two
`tool_use` blocks): event type `TOOL_USE`, 2 blocks, the text appears nowhere
in the payload. The lost text never reaches `turn_text_parts`, so it is also
missing from the `mc:` block scan and the empty-turn check. Whether qwen-code
0.23.4 actually emits mixed envelopes was not verified (no CLI launched); the
parser's own docstring calls the stream Claude-Code-shaped, and that shape
allows it.
**Fix:** return the text event and the tool event separately (the reader
handles a list), and log every block.
**Test coverage:** not found for a mixed envelope.

#### Q3 · low · L — three unguarded shapes in `parse_event` (measured)

`{"type":"assistant","message":null}` raises `AttributeError`,
`{"type":"assistant","message":{"content":null}}` raises `TypeError`
(`:7342`), and `{"type":"result","is_error":true,"error":"quota"}` raises
`AttributeError` (`:7399-7400`). Each one ends the shared reader (MA2).

#### Q4 · low · M — the Qwen launch environment is assembled in four places

`dispatch` (`:7807-7816`), `write_followup` (`:7863-7876`), `oneshot`
(`:7992-7997`) and `describe_image` (`:8037-8040`) each rebuild the same
sequence (suppress-warning variable, settings-auth override, guardrail env,
defaults file, shell pin, retry budget, fence), and they already differ: the
follow-up applies `apply_env` and `prepare_env` itself because it bypasses
`_mode_a_dispatch`. A fix to one (the 2026-09-18 settings-precedence fix is
described three times in comments) has to be remembered in the others.
**Fix:** one `_qwen_env(session, *, transform: bool)`.

#### Also in this range

- `_codex_read_meta` caches `(None, None)` for a rollout whose first line could
  not be read (`:7002-7008`); a finished rollout never changes `(mtime, size)`,
  so that conversation stays out of its project's list for the process
  lifetime. Same defect as A3. Its cache also evicts mid-pass (`:7004-7006`,
  A9).
- `_CODEX_HOOK_TRUST_NOTICE_SHOWN` (`:8222`) gains one id per Codex session and
  is never pruned. Bytes per session; noted, not ranked.

#### Sound in lines 6977-8242

- Qwen `write_followup` disowns the old process before killing it
  (`:7839-7843`), the ordering G2 asks Gemini for.
- `build_command` fails closed to the deny sentinel on malformed MCP JSON
  (`:7263-7274`).
- `_qwen_auth_state` checks the vendor's own settings before generic `OPENAI_*`
  variables, matching dispatch precedence, and logs its read failures
  (`:7562-7592`).
- `codex_model_rejection` requires the exact vendor phrase and, when a status is
  present, status 400 (`:8172-8183`); `codex_error_is_notice` is an exact-phrase
  allowlist.

### 4e. Lines 8243-11530: `CodexRuntime`, `OpenCodeRuntime`, `GooseRuntime`, `AiderRuntime`, `KiroRuntime`

No provider CLI was launched for this range. Reader measurements used a Python
child that prints synthetic protocol lines; `oneshot` measurements replaced
`subprocess.run` with a stub.

#### X1 · medium · L — a follow-up sent during a running turn is recorded as a crash on OpenCode, Goose, Aider and Kiro

`mc/agent_runtime.py:10432-10434` (OpenCode), `:10795-10797` (Goose),
`:11096-11098` (Aider), `:11441-11443` (Kiro). Each kills the old process and
only later assigns `session['proc'] = proc` (`:10461`, `:10823`, `:11132`,
`:11459`). The old reader's `finally` guards on `session.get('proc') is proc`
(`:6739`, `:6790`), which is still true until the new process has been built
and spawned, so it writes the exit line, the hint, `status='error'` and fires
`on_process_exit`.

The same file explains this exact race and its fix at `:9760-9778` (Codex:
"Clearing the reference first makes the guard win instead of racing") and
applies it in Codex (`:9779`) and Qwen (`:7839-7843`). Gemini is G2. Four
runtimes still have the pre-fix order.

**Measured** (OpenCode `write_followup`, old turn = a sleeping Python child,
spawn stubbed to a second Python child): the log gained
`[opencode exited with code 1]` and `on_process_exit` fired once for the old
process with `(rc=1, status='error')`, after which the session was `running`
again on the new process.
**Fix:** set `session['proc'] = None` before `_kill_pid` in all four, or move
the kill-and-disown into one helper every `write_followup` calls.
**Test coverage:** `tests/test_codex_resume_followup_e2e.py` covers Codex only.

#### X2 · medium · E, L — Aider leaves two temp files per turn, holding the system prompt and the conversation, and never deletes them

`mc/agent_runtime.py:11070-11084` (dispatch), `:11108-11125` (follow-up),
`:11156-11169` (oneshot). Every one is `NamedTemporaryFile(delete=False)`;
`mc_aider_` appears nowhere else in the repository, so nothing removes them.
The `ctx` file is the full system prompt (rules, memory index, persona); the
`msg` file is the task or the last 20 log lines plus the new message. An
incognito session writes them too.

**Measured:** one `oneshot` call left 1 file, a second with a system prompt
left 2 more. The review box's temp directory held 2,353 `mc_aider_msg_*` files
(4.1 MB) dated across the previous month; their source was not traced (the
test suite drives the same code).
**Fix:** delete both in the reader's `finally` (the handle can carry the
paths), and in `oneshot` after `subprocess.run` returns.
**Test coverage:** none found.

#### X3 · medium (latent) · L, M — `oneshot` on OpenCode, Goose, Kiro and Aider reports a failed run as a successful answer

`:10489-10503`, `:10854-10868`, `:11488-11502`, `:11170-11179`. Same defect as
G1/Q1: the exit code is never read, stderr goes to `DEVNULL`, `last_error` is
never set, `except Exception: return None` logs nothing, and three of them fall
back to `(r.stdout or '').strip()`.

**Measured** (stubbed `subprocess.run`, rc=1, stdout = one error event):
OpenCode returned the error event's JSON as `text`; Goose the same; Kiro the
same; Aider returned `'Traceback: no api key'`. `last_error` was `None` in all
four.

Two Aider-only additions:
- `stdin_text` is written only inside `if system_prompt:` (`:11155-11162`). A
  call with a prompt and a data body but no system prompt runs without the
  data. **Measured:** the body reached no file aider was given.
- it runs `aider --yes --no-auto-commits` (`:10961`) in `cwd or home`
  (`:11173`), so a "text transform" may edit files there.

Latent, not live: none of the four sets `tool_free_transform_enforced`, so
`run_text_transform` refuses them. They are reachable through the explicit
`runtime=` argument of `mc/browser_digest.py:124` and `mc/mail_launder.py:200`,
and all four advertise `oneshot_supported=True`.
**Fix:** one shared `_oneshot_result(r, parse)` that fails on a non-zero exit
and never returns raw stdout; set `oneshot_supported=False` until a profile is
certified.
**Test coverage:** not found for a non-zero exit on any of the four.

#### X4 · medium · L — OpenCode and Goose claim session resume and never use it for a follow-up

- OpenCode: `supports_session_resume=True` (`:10367`); `dispatch` honours
  `resume_id` (`:10416`); the reader stores the native id from the synthesised
  INIT (`:6655-6657`). `write_followup` then builds the command with the model
  only (`:10442`), so every follow-up starts a new OpenCode session whose only
  history is a 20-line, 3,000-character tail (`:10435-10436`).
- Goose: `supports_session_resume=True` (`:10727`); `build_command` hard-codes
  `--no-session` (`:10581`) and has no resume parameter; `dispatch` ignores
  `resume_id` (`:10771`).

The Codex follow-up describes the consequence it fixed for itself at
`:9793-9810`: one chat stored as N separate conversations, each with no memory
of the others. Read, not measured.
**Fix:** OpenCode: pass `resume_id=session.get('provider_session_id')` and send
the message alone, as Codex does. Goose: set the capability to `False` until a
resume form is wired.
**Test coverage:** `tests/test_provider_runtimes.py` checks the command
builders, not that a follow-up resumes.

#### X5 · low · L — one Codex shell command is logged two or three times

`CodexRuntime.parse_event`, `:8726-8742` and `:8766-8783`: `item.started`,
`item.updated` and `item.completed` for a `command_execution` each return
`TOOL_USE`. The shared reader appends one `[tool: shell] <command>` line and
adds one to `tool_calls` per event (`:6614-6626`). The comment at `:8727-8729`
says other item types are suppressed early because they "would double up with
the item.completed event"; the shell call is the one type that does.

**Measured** (the real `_mode_a_reader`, one command sent as
started/updated/completed): three identical `[tool: shell] git status` lines.
Without `item.updated` it is two. Those lines are also what
`_compose_respawn_prompt` replays as the prior-turn tail.
**Fix:** key on `tool_use_id`; log on first sight, and on completion only add
the exit status.
**Test coverage:** `tests/test_provider_runtimes.py:667` pins that
`item.started` is a `TOOL_USE`; nothing runs a started/completed pair through
the reader.

#### X6 · low · L — the exit-hint classifiers match the agent's own words, and Codex's remedy names a key the file says does not work

`:10024-10043` (Codex), `:10871-10875` (Goose), `:11182-11187` (Aider),
`:11505-11509` (Kiro). Each lower-cases the last 30 log lines (`:6821`), which
include the model's reply and tool lines, and tests bare substrings before
looking at anything structured. G5 reported this for Gemini.

**Measured** (Codex, rc=1): a tail containing `Processed 4290 files` returns
"Codex is out of allowance" (`'429'`); a tail containing `I compared it with
ChatGPT.` returns "Codex isn't authenticated" (`'chatgpt'`). `oneshot` in the
same class states the hazard (`:9990-9993`) and avoids it.

The authentication hint says "Set CODEX_API_KEY or OPENAI_API_KEY"
(`:10042`). `_codex_auth_state`'s docstring records a live test that a bare
`OPENAI_API_KEY` is never sent by this CLI and says "Do not resurrect that
branch" (`:9346-9351`).
**Fix:** classify only lines the reader tagged `[<engine> error]`; drop
`OPENAI_API_KEY` from the Codex hint.
**Test coverage:** not found for a false positive.

#### X7 · low · E — Codex rollout lookups scan the whole store each time

- `transcript_path` globs `*/*/*/rollout-*-<id>.jsonl` and stats every match
  (`:8985-8988`). It runs once per `TURN_END` through `turn_context_tokens`
  (`:9019`, called at `:6529`) and again on a failed exit (`:6815-6816`).
  **Measured:** 7.2 ms per call with 217 rollouts on disk.
- `list_sessions` walks every rollout of every project and calls
  `_codex_same_path` on each (`:9041-9049`), then stats the file a second time
  (`:9046`; `_codex_read_meta` already did). **Measured:** 30 ms warm for a
  project with zero matching rollouts; `_codex_same_path` is 91 µs per call.

Both are linear in all Codex history on the machine, not in the project's.
**Fix:** let the meta cache double as a `thread_id -> path` index; remember the
resolved path on the session dict after the first turn.

#### X8 · low · L — unguarded shapes end the reader (measured)

Codex: `{"type":"item.completed","item":"oops"}` (`:8746`),
`{"type":"error","error":"boom"}` and `{"type":"turn.failed","error":"boom"}`
(`:8920`) raise `AttributeError`. Each ends the shared reader (MA2).
Kiro: `:11291` parses as `(params.session_id or result.session_id) if
isinstance(result, dict) else None`, so a notification's `params.session_id`
is always discarded. **Measured:** `session/new` with
`params.session_id="abc"` yields an INIT whose `session_id` is `None`.
Goose: an assistant message with no text block falls through to
`str(msg.get('content'))` (`:10640`). **Measured:** a content list holding one
thinking block is shown as the chat line
`[{'type': 'thinking', 'thinking': 'hm'}]`.

#### X9 · low · L — OpenCode reports the last step's tokens as the turn's

`parse_event` returns `TURN_END` for every `step_finish` (`:10290-10299`);
`_finish_turn` overwrites `session['usage']` each time (`:6544`).
**Measured** (two steps, 500 then 7 output tokens): `usage.output_tokens` is
7; `on_turn_end` fired twice; cost summed correctly (0.03). The per-step
`TURN_END` is pinned by `tests/test_provider_runtimes.py:1779-1782`; nothing
checks what the session ends up holding.
**Fix:** sum output and reasoning tokens across the steps of one process; keep
the last step's input side for the context figure.

#### X10 · low · M — three parsers for one Codex cache file, two of them silent

`model_choices` (`:8385-8400`), `discover_models` (`:8411-8428`) and
`_cache_slugs` (`:9506-9514`) each open and parse `models_cache.json`. Only the
first has an mtime cache and a log line; the other two are
`except Exception: return None` on a JSON state read (policy). `_cache_slugs`
runs on every dispatch and every follow-up (`_gate_model`, `:9528`).
**Fix:** one cached loader returning the raw entries; the three become filters.

#### Also in this range

- `CodexRuntime.dispatch` replaces a `None` `session_dict` at `:9665`, then
  tests `session_dict is not None` at `:9693`, `:9701` and `:9736` under a
  comment (`:9732-9735`) describing a case that can no longer happen.
- `read_rate_limits` refuses to probe through the npx fallback (`:8521`);
  `health_check` (`:9429`) and `_codex_auth_state` (`:9372`) run
  `npx --yes @openai/codex ...` there, and `health_check` does not use the
  `--version` memo next to it (`:9478-9499`). Read, not measured.
- `_codex_auth_state` reports failures with bare `print` (`:9387`, `:9403`);
  on a cp1252 console a non-Latin character in the exception text raises from
  inside the handler, which is what `_log` exists to prevent (C2).
- `list_sessions` swallows a read failure (`:9110`) and caches the partial row
  under the file's `(mtime, size)`; `_CODEX_ROW_CACHE` evicts mid-pass
  (`:9120-9122`, A9).
- `_transform_cwd_dir` is a fixed path in the shared temp directory (`:9923`),
  the G3 shape; latent while `tool_free_transform_enforced` is `False`.
- `env_extra` is a parameter of every `dispatch`; no caller in `mc/` or
  `server.py` passes it.
- Goose and Aider report auth `ok` from a generic `OPENAI_API_KEY` /
  `ANTHROPIC_API_KEY` in the environment (`:10709-10716`, `:11011-11018`), the
  false positive Codex's docstring documents removing.
- The Goose parser accepts five different names for the turn end (`:10653`)
  and its docstring cites no CLI version; it could not be checked without the
  CLI.

#### Sound in lines 8243-11530

- Codex `oneshot` fails on a non-zero exit, sets `last_error`, and accepts only
  structured assistant events (`:9994-10011`).
- Codex `write_followup` disowns before killing, restates model, effort,
  sandbox and fence from values stored at dispatch, and fails safe to sandboxed
  and fenced when a key is missing (`:9779`, `:9825-9836`).
- `_spawn_turn` waits for the prompt write to be acknowledged (`:9858-9879`).
- `build_command` uses the sandbox flag form each subcommand accepts and never
  emits both bypass and sandbox (`:8608-8625`).
- `_gate_model` drops an unlisted slug with a visible line instead of failing
  the turn; `retry_without_model` is bounded to one retry per turn
  (`:9549-9555`, `:9837`).
- `_installed_cli_version` is memoised on binary identity, not path
  (`:9464-9499`).
- OpenCode's `_announced` map is locked and capped (`:10308-10313`).
- `_guard_win_argv_length` is raised outside the `try` in Kiro's `oneshot`
  (`:11487`), so an over-long prompt is an error, not a `None`.
- Follow-up environments were compared with dispatch: `retry_budgets.apply_env`
  affects only Claude and Qwen and `vendor_fence.prepare_command` only Aider,
  so the follow-ups that skip them lose nothing.
