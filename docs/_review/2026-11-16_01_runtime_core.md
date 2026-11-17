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
