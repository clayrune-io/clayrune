# Agent plumbing outside the runtime core, pass 10 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `90b9786f`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function, or a read-only
count) or **read, not measured**. `server` was never imported, the live server
was sent nothing but `GET`, no model call was made, and no process that binds a
port or launches a provider CLI was started. Nothing was written under `data/`
or to a real memory directory.

STATUS: IN PROGRESS. This file is written incrementally. Sections below the
"Working notes" heading are candidate findings in the order found; the ranked
summary, feature table, backlog table and streamlining section are written
last. If you are resuming after a rollover: read "Progress log" first.

## Progress log

- [ ] process_guard, process_ledger, process_sweep, windows_process_job
- [ ] bg_job_liveness, background_tasks, background_model_policy, agent_jobs
- [ ] agent_shim, agent_shim_adopt, agent_shim_client, agent_shim_completion(+retry), agent_shim_tramp
- [ ] agent_spawn, agent_spawn_interactive, agent_uncertain_launch, agent_worktree_followup, agent_log_file, agent_log_maintenance
- [ ] conversation_contract, conversation_store, conversation_views
- [ ] execution_lifecycle, execution_policy, launch_marker, launch_transaction, isolated_launch
- [ ] midturn_rollover, mid_turn_send, turn_clock
- [ ] guardrail_hooks, fence_fail_closed, vendor_fence(+hook,+probe), vendor_context_sync
- [ ] cli_install, cli_update, cli_update_retry, mac_update, claude_*, codex_*, aider_*
- [ ] attend_once_*, caller_attribution, resume_*, state, transform_*, pty_backend
- [ ] blueprints: agent_router_stats_routes, claude_import_routes, cli_update_routes
- [ ] backlog cross-check
- [ ] prior audits still open
- [ ] final tables

## Scope note

`mc/agent_runtime.py` was pass 1, `mc/blueprints/agent_routes.py` and
`mc/agent_worktree.py` were pass 2. The `mc/agent_mail_*.py` modules and the
`mc/blueprints/agent_mail_*_routes.py` modules are taken to be pass 05b's
(its report is not in this checkout); they are not read here.

## Working notes (candidate findings, in the order found)

### N1. `mc/process_guard.py` (113 lines, read fully)

- **AP1 (L, medium) `process_guard.py:20-31`** — the guard misses ordinary
  spellings of the two things it exists to stop. Measured with
  `guard_command()` in a scratch script (listener pid 4242). Image-name kills
  ALLOWED: the `/FI "IMAGENAME eq x"` filter form of the Windows kill tool,
  `Stop-Process -ProcessName x` (an alias of `-Name`), `spps -Name x`,
  `Get-Process x | kill`, `gps x | spps`, `(Get-Process x).Kill()`, the
  CIM `Terminate` form. Listener-PID kills ALLOWED: `kill -TERM 4242`,
  `kill 111 4242` and `kill -9 111 4242` (`:30` captures only the first
  number), a second `/PID` on one command (`:28` needs the tool name before
  every match, so `finditer` sees one), `Stop-Process 4242` (positional),
  `Stop-Process -Id 111,4242`, and any indirect form (`kill $(lsof -ti :port)`).
  Fix: for PID patterns, find the command word and then scan every integer
  in that command segment; add `-ProcessName`, `/FI ... IMAGENAME`, and the
  `kill`/`spps` aliases on the right of a `Get-Process`/`gps` pipe. Tests:
  `tests/test_process_guard*.py` to be checked for these spellings (see N-tests).
- **AP2 (L, low) `process_guard.py:21-25`** — the patterns run over the whole
  command text, so a command that only MENTIONS a blocked spelling is refused:
  `grep -rn "pkill" docs/` and a `git commit -m` whose message quotes the
  rule are both blocked (measured). Live evidence: this review's own heredoc
  writes (a scratch probe, then these notes) were refused by the hook twice.
  Fix: match only at a command position (start, or after `;`, `&&`, `|`, `(`).
- **AP3 (E, low) `process_guard.py:100`** — `clayrune_listener_pids()` (a
  `netstat -ano` subprocess) runs on EVERY shell tool call, before
  `guard_command` knows whether the command contains a PID kill at all.
  Measured: 53.5 ms per call on this box, on top of interpreter start, per
  Bash/PowerShell call, per agent, on four vendors. Fix: pass a callable and
  call it only when a `_PID_PATTERNS` match exists.
- **AP4 (L, medium) `process_guard.py:56-77`** — listener detection is
  Windows-only in effect. `:72` needs the literal `LISTENING` and `:76` takes
  the last column as the PID; Linux and macOS `netstat` print `LISTEN` and no
  PID column with these flags, so the set is always empty there and the
  "never kill the Clayrune listener" half of the guard cannot fire on two of
  three platforms (read, not measured: no such host here). Also `:57-60` knows
  port 5199 and `MC_PORT` only; a port set in `config.json` alone
  (`server.py:683`) is invisible to the hook unless the launch exports it
  (checked under agent_spawn). Fix: `psutil.net_connections` or `lsof -ti`
  off Windows; export the bound port into every agent env.

### N2. `mc/guardrail_hooks.py` (493 lines, read fully)

- **AP5 (L, medium) `guardrail_hooks.py:252-256`** — `guard_shell_command`
  quotes a token that contains a space and emits `"<py>" <script> ; exit
  $LASTEXITCODE`. In PowerShell a leading quoted string is an expression, so
  the line is a parse error. Measured (scratch, real `powershell.exe
  -NoProfile -Command`): quoted interpreter -> exit 1 with a parser error;
  the same line with a leading `. ` -> exit 2, guard ran. Codex and Gemini
  run hooks through PowerShell on Windows and map anything but 2 to "run the
  tool" (`:124-130`, `:368-370`), so on an install whose interpreter path has
  a space (a Windows user name with a space, or Python under `Program Files`)
  the process guard is a silent no-op on those vendors. The file already
  holds the fix for the fence command (`:437-440`, the `. ` prefix) and did
  not apply it here; the docstring calls the case "a disclosed, untested gap"
  (`:197-198`, `:228-229`). A space in the SCRIPT path only is fine (measured:
  exit 2). Fix: same `. ` prefix when `tokens[0]` is quoted and `os.name ==
  'nt'`. No test covers a spaced interpreter through a real shell.
- Sound: one builder for the command string (`:178`), codex inline injection
  without a file (`:271`), self-test judged on exit 2 plus a token (`:492`).
- Note (not a finding): 493 lines, of which about 330 are incident narrative
  in docstrings. The measurements belong in `docs/GUARDRAIL_PARITY_EVIDENCE.md`
  (which they already cite); the code is about 110 lines.

### N3. `mc/process_ledger.py` (205 lines, read fully)

- **AP6 (L, medium) `process_ledger.py:174-179`** — the split-brain guard
  only recognises a live prior server whose image name starts with `python`.
  The packaged macOS app runs the server as a thread of the app process
  (`app.py:541`, executable name `Clayrune`, `installer/build-macos.spec:213`),
  so there the guard never holds: a second launch beside a live one reads the
  first one's ledger and tree-kills every child whose image and creation time
  still match (`:193-197`). This is the process half of pass 1 **S2**
  (`app.py` boots with `check_port=False` and no bind first). Fix: compare
  against the ledger writer's own recorded image (store `mc_image` and
  `mc_create_time` beside `mc_pid` at `:122-123`) instead of a name prefix.
  Read, not measured. `tests/test_process_ledger*.py` to check.
- **AP7 (M, low) `process_ledger.py:124-125`, `:161-162`, `:204-205`** —
  three bare `except Exception: pass/return` on a state-file write and read.
  Project policy says these log. A ledger that silently stops being written
  is exactly the case where the next boot reaps nothing and nobody knows why.
- Sound: `_should_reap_entry` (`:128-144`) fails toward not killing on any
  missing identity; kill is by PID tree with image + creation-time match.

### N4. `mc/process_sweep.py` (405 lines, read fully), call site `system_routes.py:347-409`

- This module kills PIDs Clayrune did not start. That is an approved exception
  (standing position cited at `:8`), all five criteria are implemented as the
  docstring says, enumeration failure fails closed at both samples
  (`:366`, `:385`), kill is by PID tree only (`:400`). Not a hygiene finding.
- **AP8 (L, low) `process_sweep.py:235-260`** — `chain_is_dead` walks THROUGH a
  live shell. A CLI whose parent is a live `cmd.exe`/`powershell.exe`/`bash`
  whose own launcher has exited reads as "chain dead" (`:249-251` after the
  wrapper step at `:255-257`). A person's interactive CLI session left open
  for 24 hours in a console that was started by a script or shortcut that
  exited, idle at its prompt for the 2 s CPU sample, meets all five criteria
  and is killed. The CPU criterion (`:327-345`, 0.05 s over 2 s) does not
  separate "abandoned" from "waiting for the user". Whether that is inside
  what the owner approved is his call; smallest narrowing: treat a live
  wrapper that owns a console window (or any live wrapper that is not
  `node`/`conhost`) as a live owner.
- **AP9 (L, low) `process_sweep.py:140-149`, `:223-232`** — criterion 1 is met
  by any process whose command line merely contains a CLI package directory
  (an editor or pager opened on a file inside it). With a dead chain and 24 h
  age it becomes a kill candidate. Narrow: require the dir to be the script
  argument of a `node` image, not any substring position.
- Non-Windows: `list_processes` returns `[]` (`:95-96`), so the sweep is a
  no-op on macOS and Linux and reports `ok`. Documented at `:79-80`.

### N5. `mc/windows_process_job.py` (223 lines, read fully)

- Sound. Handle types declared, kill-on-close set before assignment, the
  suspended-root resume fails closed on anything but one thread. No finding.
  (`set_kill_on_close` re-declares three ctypes structures per call, `:32-53`;
  cosmetic.)

### N6. `mc/bg_job_liveness.py` (202 lines, read fully)

- **AP10 (E, low) `bg_job_liveness.py:110-125`** — the 3 s budget is checked
  between descendants only; one `open_files()` call is not bounded, and the
  scan runs on the stream-reader thread for each background tool_result
  (caller in midturn_rollover, checked there). Read, not measured.
- Sound: holders carry pid + create_time so a recycled pid cannot read as
  alive (`:141-143`); every public function catches its own errors.

### N7. `mc/background_tasks.py` (254 lines, read fully)

- Sound. Pure functions over session keys; the CLI's own list is authoritative
  (`:118-137`), timeout-moved shell calls are told apart by the tool_use input
  (`:89-94`). No finding. (`drain` `:209-218` leaves `INTERIM_KEY`,
  `LAST_END_AUTO_KEY` and `TOOL_USES_KEY` set; whether a stale `INTERIM_KEY`
  matters is decided in `agent_routes.py`, pass 2's file. Not followed.)

### N8. `mc/agent_jobs.py` (321 lines, read fully), route `agent_routes.py:1956-2060`

- **AP11 (E/L, low) `agent_jobs.py:41`, `:251-252`, `:283-289`** — `_jobs`
  is never pruned. Every job ever started keeps its record AND its live
  `Popen` object (`'proc'`, `:245`) for the life of the server; on Windows
  that is one open process handle per finished job. `open_job_count`,
  `describe_open_jobs` and `list_open_jobs` (`:146-179`) scan all of them
  under the lock. Fix: drop `proc` when the watcher finishes and prune
  finished jobs older than a day.
- **AP12 (E, low) `agent_jobs.py:49-54`** — `_tail` reads the whole log file
  to return the last 4 KB. A full test-suite log is megabytes. Fix: seek.
- **AP13 (L, low) `agent_routes.py:2032-2040` + `agent_jobs.py:259-262`** —
  `"timeout_minutes": "nan"` passes the route's `<= 0` check, survives
  `min(nan, cap)` (measured in Python: `min(float('nan'), 120.0)` is `nan`),
  and in the watcher `max(0.0, nan*60)` is `0.0`, which `:262` turns into "no
  timeout". An agent-supplied NaN removes the cap. Fix: `math.isfinite`.
  (The route line is pass 2's file; the consequence is here.)
- **AP14 (L, low) `agent_jobs.py:99`** — a non-string `shell` (a number, a
  list) raises `AttributeError` on `.strip()`, which the route does not catch
  (`agent_routes.py:2050` catches `ShellResolutionError` only): 500.
- Known and documented, not a new finding: jobs live only in memory
  (`:170-172`); the restart endpoints block on open jobs
  (`system_routes.py:1728-1745`), a crash or forced restart loses the wake.
- Sound: argv invocation with `pipefail` (`:136`), WSL launcher rejected
  (`:106-109`), stdin is DEVNULL, tree kill is supplied by the caller, the
  capture hooks cannot change a job (`test_run_capture.py:197-216`).

### N9. `mc/agent_shim.py` (936 lines, read fully)

The whole shim family (N9 to N12, about 3,100 lines) is behind
`agent_survive_restart_enabled`, default False (`agent_shim_client.py:45`) and
absent from this install's config (checked, read-only). Every finding in
N9 to N12 is therefore LATENT here.

- **AP15 (L, low, latent) `agent_shim.py:602-611`, `:777-797`, `:914-917`** —
  the lifetime loop can block for ever. `_do_send` holds `_send_lock` across
  `cli.stdin.write` (`:796`); if the CLI is hung and the payload exceeds the
  pipe buffer the write never returns. Once the owner is gone the loop reaches
  `A_CLOSE_STDIN` (a hung CLI is idle and silent, so the idle test passes),
  calls `_close_stdin()` on the main thread (`:917`), and that waits on
  `_send_lock` (`:603`). The loop never ticks again, so `orphan_max` (`:458`)
  never kills the hung CLI: shim and CLI live until the machine reboots. This
  is the 2026-09-23 stdin deadlock (`agent_routes.py:15302-15310`) moved into
  the shim, without the watchdog that fixed it there. Fix: `acquire(timeout=)`
  in `_close_stdin`, or run the close off the main loop. Read, not measured
  (the interactive path needs a listening socket, which this pass may not open).
- **AP16 (L, medium, latent) `agent_shim.py:920`** — the final
  `_write_manifest()` is unconditional. `write_spec` (`agent_shim_client.py:
  214-218`) deletes `shim.json` for a relaunch in the SAME directory, and the
  new shim writes its own; an older shim for that directory that is still
  draining (waiting for its owner connection to close, up to `exit_grace_s`,
  60 minutes by default) then writes its OLD manifest over the new one when it
  finishes. In-memory the new run is safe (`ShimProc` keeps the manifest it
  was given, `agent_shim_client.py:608-611`), but everything that re-reads
  the directory is now looking at dead pids: `ShimClient.connect` on a
  reconnect (`:317-319` -> "shim not alive") and adoption after a restart
  (`inspect`, `:253-265`). The live CLI is then invisible to the next server.
  Fix: before the final write, re-read `spec.json` and skip if its
  `launch_id` is no longer this shim's. Read, not measured.
- **AP17 (L, low, latent) `agent_shim.py:455-459`, `:874-876`, `:894`** — a
  oneshot shim has no socket, so after a server restart nothing can ever
  re-attach: its only owner is `spec.owner_pid`, the dead server. It stays
  "orphaned" from the restart on and is tree-killed at `orphan_max` (12 h)
  whether or not the new server adopted it and is reading its log. Only a
  turn that runs 12 hours past a restart is hit.
- **AP18 (M, low, latent) `agent_shim.py:389-407`** — one tmp name per process
  per target (`.shim.json.tmp<pid>`), written from the serve threads
  (`_claim_owner` -> `:662`) and the main thread with no lock around the
  write. Same pattern as pass 1 **C1**; two owner claims at once can tear or
  lose the manifest. The manifest dict is built under `_lock`, the file write
  is not.
- **AP19 (M, low) `agent_shim.py:877-884`** — `run()` catches `OSError` from
  `_spawn_cli`, but `spec['argv']` missing or not a list raises
  `KeyError`/`TypeError` there (`:562`) and the shim dies with a traceback to
  DEVNULL and no `exit.json`; the server waits out the 20 s ready timeout and
  then cannot tell "slow" from "dead" (`agent_shim_client.py:797-800`).
- Sound: identity is (pid, create time) from one reader with per-platform
  tolerance (`:159-188`); kill-if-ours holds the handle / pidfd across the
  kill (`:273-368`); no remote kill command; token compared in constant time
  (`:708`); sequence numbers re-checked under the write lock (`:777-814`).

### N10. `mc/agent_shim_client.py` (816 lines, read fully)

- **AP20 (E, medium, latent) `agent_shim_client.py:639-656` with
  `agent_shim.py:130-141`** — on macOS `proc_create_time` is a `ps`
  subprocess. `ShimProc.poll()` calls `process_state(self.pid, ...)` whenever
  there is no `exit.json` (`:652`), and `LogTail.__next__` calls
  `self._finished()` -> `poll()` on every idle poll, every 50 ms (`:525`,
  `:537`). That is about 20 `ps` launches per second per running agent for
  the whole of every quiet stretch of a turn. Read, not measured (no macOS
  host; the design doc itself says this reader is unmeasured on a Mac).
  Fix: sample liveness at most once a second in `poll()`; `exit.json` is the
  fast path and needs no process probe.
- **AP21 (L, low, latent) `agent_shim_client.py:381-405`** — `send` is not
  idempotent after a TIMEOUT, only after a dropped connection. If the second
  attempt times out (`:386-388`) the method raises but leaves `self._sock`
  connected and `self._seq` un-advanced. If the shim's blocked write later
  completes it sends `ACK seq` into that socket. The next `send` reuses the
  same `seq` for a NEW payload, reads the stale `ACK`, and reports success;
  the shim sees `seq <= last_seq` and drops the new payload as a duplicate
  (`agent_shim.py:786-788`). Not reachable from the server today: a failed
  write marks the session errored and the proc is replaced
  (`agent_routes.py:15354-15363`). Fix: `self._drop()` before raising at `:388`.
- **AP22 (E, low, latent) `agent_shim_client.py:473-492`, `:539-543`** —
  `LogTail._cut` walks the buffer byte by byte in Python and re-slices the
  remainder per line; a partial line is re-scanned from byte 0 after every
  read. Measured: 2.5 MB/s on 100-byte lines, 0.78 s for one 8 MB line
  (0.07 s for 2 MB, so quadratic). `_read_more` re-opens the file every poll.
  Fix: `buf.find(b'\n', scan_from)` and keep the handle open.
- **AP23 (L, low, latent) `agent_shim_client.py:668-671`** — `ShimProc.kill`
  checks identity, then calls the unchecked `kill_tree`. The module's own
  `kill_tree_if_ours` (`agent_shim.py:371`) exists to close that window. And
  when the manifest has no `cli_ct` (the shim could not read it,
  `agent_shim.py:576`), `_cli_alive()` is always False and `kill()` silently
  does nothing: Stop has no effect on that run.
- Sound: spawn refuses rather than work around a job that forbids breakaway
  (`:144-149`); `launch` never reports "nothing running" unless it verified it
  (`:784-800`, `cli_running`); spec carries env NAMES only (`:201`).

### N11. `mc/agent_shim_adopt.py` (937 lines, read fully)

- **AP24 (L, medium, latent) `agent_shim_adopt.py:69`, `:506-509`, `:848-849`** —
  `RESTORABLE_READER_PATHS` is an empty frozenset and nothing in `mc/` adds to
  it (grep: only tests monkeypatch it, `tests/test_agent_shim_adopt.py:47`,
  and `tests/test_agent_shim_adopt_blockers.py:340` pins it empty). `assess`
  therefore refuses every directory before it looks at a process, and
  `is_adoptable_session` is always False. Consequences, flag ON: no session
  is ever adopted, spared or moved out of the restart blockers; `_attach`,
  `start_all`, `start_one`, `_start_reader`, `_register_adopted_cli`
  (`:640-803`, about 165 lines) and the adoptable half of `partition_blockers`
  run only under test. The docstring says so (`:57-68`, a recorded decision),
  so this is not a bug in the module; it is the answer to "does survive-restart
  work": turning the flag on buys the shim, a trampoline, a checkpoint write
  per live session every 1 to 3 s (`:371-387`, a JSON round trip of the whole
  session dict plus 2,000 log lines), and no survivable session. Fix: none
  here; either finish one reader path or state in the settings text that the
  flag does not yet keep anything alive.
- **AP25 (E, low, latent) `agent_shim_adopt.py:622-623`,
  `agent_shim_completion.py:124-125`, `agent_spawn.py:417`** — nothing ever
  deletes a session directory under `agent_shims` (grep over `mc/`: the only
  removals are `shim.json`/`exit.json` on relaunch, `agent_shim_client.py:216`,
  and `prompt.txt`, `agent_shim.py:593`). `out.log` is append-only across
  turns, so with the flag on each session keeps its whole raw output for
  ever, and every boot reads `spec.json` + `shim.json` + `checkpoint.json`
  (`claim_all`) and `checkpoint.json` again (`replay_pending`) for every
  session that ever ran. Fix: prune directories whose shim and CLI are gone
  and whose checkpoint has no pending stamp, older than N days.
- **AP26 (E, low, latent) `agent_shim_adopt.py:268-269`, `:339-344`** —
  `_WRITE_LOCKS` and `_LAST_OFFSET` gain one entry per (session, launch) and
  are kept "for the life of the process" by design; a per-turn engine makes a
  new launch id every turn. Small entries; unbounded count.
- **AP27 (M, low) `agent_shim_adopt.py:558-559`** — `_holds_work` swallows a
  failed job-table read as "holds nothing" while its sibling `_owns_open_job`
  (`:812-820`) fails closed and logs. Two answers to one question.
- Sound: `write_checkpoint` takes the reader boundary, then one lock over
  snapshot and replace, and drops a snapshot behind what is on disk
  (`:289-321`); `protected_pids` re-verifies identity when the reaper asks
  (`:586-591`); the ledger entry keeps the manifest's create time, never a
  fresh sample (`:746-770`).

### N12. `mc/agent_shim_completion.py` (196), `agent_shim_completion_retry.py` (107), `agent_shim_tramp.py` (106): read fully

- **AP28 (L, low, latent) `agent_shim_completion_retry.py:77-79` with
  `agent_shim_completion.py:78-81`** — the retry tick skips a session that is
  live in this process and carries a stamp, on the grounds that "its own
  thread acks it". When the ack was called with `owed=True`
  (`agent_routes.py:10171`, `:10206`) that thread has finished and did NOT
  ack, and the stamp stays on the live dict. The retry then skips it on every
  tick until the session is evicted from `agent_sessions`; only then does the
  disk check run. The module's stated purpose (do not wait for the next boot)
  is not met for that case. Fix: skip only while the session's status is
  still `running`. Read, not measured.
- **AP29 (M, low) `agent_shim_completion_retry.py:97-104`** — the idle-run
  scan is a general workflow recovery (advance a `running` run that nothing
  is driving) and is not gated on the survive-restart flag, yet it lives in a
  module named for the shim and is reached only through
  `scheduler_routes.py:906`. Anyone who removes or disables the shim family
  removes workflow recovery with it. Fix: move the scan beside
  `workflows.advance_idle_run`.
- **AP30 (L, low, latent) `agent_shim_tramp.py:62-76`** — on POSIX the parent
  ignores the first child's exit status (`:65`) and a failed `execv` in the
  grandchild exits 127 with nothing written; `_record_error` (`:48-53`) only
  covers the Windows spawn and the first `fork`. The server then waits out
  the whole ready timeout to learn what the trampoline knew at once.
- Sound: replay is serialized by one lock and clears a stamp only when the
  runner returned True (`agent_shim_completion.py:141-146`, `:183-190`);
  `begin` is a two-attribute check and returns for a plain Popen session, so
  the default install pays nothing per turn (`:62-63`).

### N13. `mc/agent_spawn.py` (473), `agent_spawn_interactive.py` (172), `agent_uncertain_launch.py` (119): read fully

- **AP31 (L, low, latent) `agent_uncertain_launch.py:64-78`, `:99-110`,
  `agent_spawn.py:384-387`** — when a shim launch misses its deadline and no
  manifest for that launch ever lands (the shim died before its first write),
  `_attempt` can never confirm anything, the marker stays, and every later
  spawn for that session raises, with the flag on OR off. The only way out is
  deleting `uncertain.json` by hand; the error text says so, and no route or
  UI surface names the file (grep over `mc/` and `static/`: the marker is
  read only here). One missed deadline ends a chat until someone opens a
  file manager. Fix: when the launch's `spec.json` is older than the shim's
  own `orphan_max`, treat the launch as gone and clear the marker.
- **AP32 (L, low, latent) `agent_spawn.py:343-347`** — if the delivery code
  has not closed the capture stream after 10 s, the factory logs "sending
  what was written", settles the capture as FAILED, and still launches the
  shim with the partial payload (`:414-426`). The CLI runs a turn on a
  truncated prompt while the caller is told delivery failed. Fix: raise
  instead of launching.
- **AP33 (L, low) `process_guard.py:57-60` with `agent_spawn.py:109-111`** —
  second half of AP4, confirmed: the spawn factory exports the bound port to
  every agent as `CLAYRUNE_PORT`, the guard reads only `MC_PORT`, and
  `server.py` never sets `MC_PORT` (only `app.py:475` does). A dev-mode
  server on a port set in `config.json` has no protected listener at all.
  Fix: one line, read `CLAYRUNE_PORT` in the guard.
- Sound: with the flag off the factory is the old `Popen` with the old
  arguments plus one failed `open()` for the marker (`:379-389`); the
  fallback never runs the delivery code twice (`:443-473`); a prior shim that
  cannot be proven gone is never overwritten (`:398-412`,
  `agent_spawn_interactive.py:88-113`).

### N14. `mc/agent_worktree_followup.py` (56), `agent_log_file.py` (110), `agent_log_maintenance.py` (18): read fully

- **AP34 (E, low) `agent_log_file.py:96-101`, `agent_log_maintenance.py:7-11`
  with `agent_routes.py:7444`** — `snapshot()` is used as a READ at three call
  sites (`agent_routes.py:7540`, `:13724`, `:16126`) but always rewrites the
  whole log: `_update_agent_log` saves unconditionally after the mutator, and
  `claim_rows` changes nothing once every row carries an attempt id. Measured
  (read-only `ls`): the three largest agent logs on this install are 1.6, 2.0
  and 2.1 MB. So one message into a non-live or human-joinable session is a
  full parse plus a full atomic rewrite of up to 2 MB before any work, the
  model-pin route does it twice (`:13724` then `:13727`), and deleting a plan
  rewrites EVERY project's log whether or not it referenced the plan
  (`:16123-16126`). Fix: have `_update_agent_log` skip the save when the
  mutator reports no change.
- Sound: `transaction` is re-entrant per thread and releases the OS lock on
  close, including after a crash (`agent_log_file.py:20-58`); `merge_rows`
  applies a change only if the row is still what the snapshot saw (`:87-90`);
  `resolve_followup_cwd` no longer drops an isolated session into the shared
  tree (`agent_worktree_followup.py:48-49`).

<!-- APPEND-HERE -->
