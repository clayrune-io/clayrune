# Agent runs survive a server restart: supervisor-shim design

Backlog MC-1150 (item `ebbb15a7`). Same mechanism as MC-1149 phase 2 (item
`663e0c81`). **Design spike only: no production code changed.** Author: Tobin,
2026-11-04, branch `clayrune/agent/fa1094516713`. Decisions that are Dave's to
make are collected in section 12.

Ron's ask (2026-11-04): *"CR should restart while an agent is running and
recapture the session."*

---

## 0. Summary

| Question | Answer |
| --- | --- |
| Can a detached child outlive the server on Windows? | **Yes, but only with the right spawn recipe.** Measured (section 2): a plainly-detached child dies to `taskkill /F /T` and to a Job-object close. A *breakaway + trampoline* spawn survived all four server-death modes tried when the launching job allows breakaway; when it does not, only WMI escapes, and the WMI child loses the caller's environment (Q4). |
| What is the design? | One tiny stdlib-only supervisor process (**the shim**) per engine process. The CLI's stdout/stderr go **straight to a log file** (the shim is not in the output path). The shim owns the CLI's stdin pipe and serves it on a loopback socket. A manifest + checkpoint on disk let a restarted server re-adopt. |
| One supervisor for MC-1149 phase 2 and MC-1150? | **Agree, with one correction** (section 10): the shim also covers restarts for `agent_jobs`, which today do *not* survive a server restart. The roll case needs one extra shim command (retire-with-spare) and one unmeasured assumption about how Claude's CLI parents its Bash children. |
| Hardest part | Not the process plumbing. It is **rebuilding the in-memory session** (reader state, `log_lines`, bg-task state) so the restarted server continues a turn without re-firing completion side effects (section 6). |
| Recommended order | Shim + tests (P1), Mode A engines (P2), Claude Mode B (P3), adoption + restart blockers + hivemind (P4), roll/job spare (P5), dogfood and flip default (P6). Everything behind `agent_survive_restart_enabled`, default OFF until P4 is live-proven. |

---

## 1. What is true today (verified against the code)

The brief's facts hold. Additions and corrections are marked **(new)**.

1. **Pipes are server-owned and anonymous.** `_mode_a_dispatch` spawns with
   `stdin=PIPE, stdout=PIPE, stderr=STDOUT` (`mc/agent_runtime.py:5501-5513`).
   The Claude Mode B spawn is the same shape (`mc/blueprints/agent_routes.py:12360-12374`).
   **(new)** There are ~20 engine spawn sites, not one:
   - `agent_runtime.py`: 4786 (Gemini dispatch), 5276 (Gemini follow-up), 5501
     (`_mode_a_dispatch`, used by Qwen 7665, Codex 9599, OpenCode 10268, Goose
     10635, Aider 10940, Kiro 11289), 7724 (Qwen follow-up), 9703 (Codex
     `_spawn_turn`), 10295, 10661, 10985, 11316 (per-turn follow-ups).
   - `agent_routes.py`: 12360 (Claude Mode B dispatch), 12521 (Claude `-p`),
     14070 (`_do_respawn_b`), 14204 (`_start_followup`), 14776 / 14853
     (`_do_respawn`, incl. the mid-turn roll), 7732 / 7855 (`_revive_from_agent_log`),
     10054 (`_auto_dispatch_followup`), 6599 / 6643 (`_auto_recover_failed_resume`).
   - Spawn flags are `CREATE_NO_WINDOW` only on Windows (`agent_runtime.py:108-112`)
     and `start_new_session=True` on POSIX (`mc/proc_kill.py:33-34`). No
     breakaway, no new process group, no job handling.
2. **The startup reaper kills the prior instance's children on purpose.**
   `_persist_pid_ledger` writes every `tracked_processes` entry to
   `data/mc_child_pids.json` (`mc/process_ledger.py:108-125`, path at
   `server.py:932`). `_reap_prior_instance_strays` (`process_ledger.py:147-197`)
   `taskkill /T`s each entry still alive and still the same image + create-time
   (`_should_reap_entry`, 128-144). It runs at `server.py:3578`, after the
   port-conflict wait (3551), so on a restart the old server is already gone.
3. **The restart path stops every session first.** `_perform_server_restart_async`
   (`system_routes.py:1863-1972`) spawns the new server *first* (1939), then
   `_stop_all_sessions_for_restart` (1738-1805) closes Mode B stdin and
   tree-kills every `running`/`idle`/`error` session, then `os._exit` (1972).
   The power-off path (`_perform_server_shutdown_async`, 1977) does the same
   without a replacement.
4. **Revival loses only the in-flight turn.** `_revive_from_agent_log`
   (`agent_routes.py:7503`) rebuilds a *new* session dict from the agent_log row
   and spawns `claude -r <claude_sid>` (resume flags at 7676, spawn at 7732/7855).
   The conversation survives; the running turn does not.
5. **Restart blockers** (`system_routes.py:1679-1717`): every session with
   `status == 'running'` plus every hivemind with `status == 'active'`. Consumed
   at 2529 (update), 2550, 2732 and 2766 (restart), each returning 409 unless
   `force`.
6. **(new, correction) `agent_jobs` do NOT survive a server restart.** The brief
   says they survive; they survive a *context roll*, which never touches the
   server. `_jobs` is an in-process dict (`mc/agent_jobs.py:39`); the job is a
   plain child of the server (`start_job`, 209-212, `creationflags=_POPEN_FLAGS`);
   it is registered into the PID ledger (`agent_routes.py:1897`), so the next boot's
   reaper kills it; and the completion watcher is a daemon thread (227-273) that
   dies with the server, so `_job_on_complete` never fires. `start_job` already
   takes an injectable `popen` (165), which is the seam this design uses.
7. **(new) Background jobs die with the CLI at a restart too, and a CLI-native bg
   task is invisible to the server except through the stream.** `drain` (`mc/background_tasks.py:209`,
   called at the top of `_read_agent_stream_b`, `agent_routes.py:6252-6269`) says
   so out loud after a respawn. `blocks_eviction` (240) already treats an open bg
   task as "do not kill".
8. **(new) Hivemind needs no special process model.** Workers are ordinary
   Mode A sessions dispatched through `_hm_runtime_dispatch`
   (`hivemind_routes.py:1017`, `mode='A'` at 1059), recorded in `agent_sessions`
   under `ws['current_agent_session_id']`. The orchestrator is a daemon thread
   (`_hivemind_orchestrator_loop`, 1760, 10 s tick) driven from manifest files on
   disk; it silently skips workers whose session id is not in `agent_sessions`
   (1783-1787). So once sessions are re-adopted into `agent_sessions`, hivemind
   resumes with no extra work (section 8).
9. **(new) Hooks are local commands, not HTTP callbacks** (`mc/hook_entry.py`,
   `<app> --clayrune-hook fence|process-guard`), so a CLI whose server is down
   still passes its PreToolUse hooks. What *does* fail during the gap is anything
   the agent does against `localhost:<port>` (curl to the job route, the
   delegation API, an HTTP MCP pointed at the server).

---

## 2. The riskiest assumption, measured

*Does a detached child keep running on Windows when the server process tree is
killed?* Not assumed; run. Harness: `_scratch/survive_proof/proof.py` (worktree-local,
gitignored). Method: a stand-in "server" spawns the shim one of six ways; the
shim spawns a heartbeating stand-in CLI with `stdout` redirected to a file and
stdin on a pipe the shim owns; the server is then killed one of four ways; a
separate "new server" process reconnects to the shim on **both** a loopback
socket and a named pipe and checks the CLI echoed the bytes into the log. A cell
passes only if the shim *and* CLI survived *and* the log kept growing *and* both
channels round-tripped. Only PIDs the harness started were killed (create-time
checked). Windows 11 Pro 10.0.26200, Python 3.14.0 (venv), 2026-11-04.

Server-death modes: `exit` = `os._exit(0)` (what `system_routes.py:1972` does);
`taskkill_tree` = `taskkill /F /T /PID` (what `_kill_pid(tree=True)`,
`agent_routes.py:570-583`, and Task Manager's "end process tree" do);
`jobclose` = server inside a Job object with `KILL_ON_JOB_CLOSE`, handle closed
(a launcher/Task-Scheduler-style job that allows breakaway); `jobclose_nobreak` =
same job *without* `BREAKAWAY_OK`.

| Spawn recipe | exit | taskkill /T | job close | job close, no breakaway |
| --- | --- | --- | --- | --- |
| `plain` (today's flags) | survives | **dies** | **dies** | **dies** |
| `detached` (`DETACHED_PROCESS\|NEW_PROCESS_GROUP`) | survives | **dies** | **dies** | **dies** |
| `breakaway` (detached + `CREATE_BREAKAWAY_FROM_JOB`) | survives | **dies** | survives | **spawn fails** (access denied) |
| `double` (trampoline spawns shim, exits) | survives | survives | **dies** | **dies** |
| `wmi` (`Win32_Process.Create`, parent = WMI service) | survives | survives | survives | survives |
| **`robust`** = trampoline + breakaway, WMI if breakaway is refused (WMI child has no inherited env, section 12 Q4) | **survives** | **survives** | **survives** | **survives** |

(The first full run put `jobclose` cells under the venv `python.exe`; that
launcher stub carries its own `KILL_ON_JOB_CLOSE|SILENT_BREAKAWAY_OK` job, so the
server's children escape it and the cells read "survives" for the wrong reason.
They were re-run with the base interpreter and the table above is the re-run.)

Findings that shape the design:

- **Parent exit alone kills nothing** on Windows; children stay up. That is why
  `os._exit` cells all pass, and why today's loss is deliberate (the stop-all in
  `system_routes.py:1738` and the reaper), not an OS artifact.
- **`taskkill /T` follows ParentProcessId.** A detached flag does not help; the
  shim must not be a *live* child of anything the tree-kill starts from. The
  trampoline (spawn, then exit immediately) orphans the shim.
- **Job close is a second, independent kill.** Breakaway escapes it only if the
  job allows breakaway; if not, `CreateProcess` fails outright (the harness saw
  the spawn error, not a silent miss), and the only escape measured is
  WMI `Win32_Process.Create`. The refusal is `PermissionError: [WinError 5]
  Access is denied` raised by `Popen` (measured, `_scratch/survive_proof/bkerr.py`;
  the same call succeeds when the job has `BREAKAWAY_OK`), so `except OSError`
  catches it cleanly.
- **The WMI escape does not carry the environment.** The WMI-created child runs
  as the same user in the same session but with a fresh 47-variable environment
  (the caller's 87 are not inherited) and the Windows system directory as cwd
  (measured, `envrun.py`). Q4 in section 12 covers what that means for secrets.
- **The live server is inside a Job.** `IsProcessInJob` is true for the running
  server (pid 32284) and its venv launcher stub (33652); the job's limit flags
  could not be read from outside, so whether breakaway is permitted on this
  box's real launch path (Task Scheduler S4U per `tools/clayrune-autostart.ps1`,
  `start-hidden.vbs`, or Tauri) is **unmeasured**. The recipe therefore tries
  breakaway and falls back to WMI at runtime instead of guessing.
- **venv `python.exe` is a launcher stub** (extra process + its own job). The
  shim must be launched with `sys._base_executable`, not `sys.executable`.
  Frozen builds have no interpreter: the shim must be a mode of the app binary
  (`<app> --clayrune-shim`), exactly as `app.py:22-27` already does for hooks.
- **POSIX (WSL2 Ubuntu, not native Linux/macOS), `_scratch/survive_proof/posix_probe.py`:**
  a `start_new_session` child survives `killpg` of the server group but **dies to
  a descendants walk** (what `mc/proc_kill.kill_tree` falls back to); a
  double-fork + `setsid` child survives both. Same trampoline conclusion.
  macOS is unmeasured.
- **Cost of a shim:** 21.2 MB RSS and 6 threads (stdlib shim; stand-in CLI was
  18.9 MB). Stdin round trip over loopback socket p50 0.02 ms / p99 0.06 ms; over
  a named pipe p50 0.02 / p99 0.03 (n=200 each). Write-to-visible-in-log 7.6 ms.
  Latency is not a differentiator.

The recipe, as it should be written in `mc/agent_shim_client.py`:

```python
def spawn_detached(argv, *, log_dir):
    if sys.platform == 'win32':
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:   # trampoline execs the real shim then exits at once
            return Popen([base_exe, '-m', 'mc.agent_shim_tramp', *argv],
                         creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                         close_fds=True, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
        except OSError:    # WinError 5: job forbids breakaway. v1: return None, the
            return None    # session stays non-adoptable (WMI child gets no env, Q4)
    else:  # POSIX: double fork + setsid inside the trampoline
        return Popen([sys.executable, '-m', 'mc.agent_shim_tramp', *argv],
                     start_new_session=True, close_fds=True, ...)
```

---

## 3. Architecture

```
 server (restartable)                         shim (one per engine process, survives)
 ┌──────────────────┐   spawn_detached()     ┌───────────────────────────────┐
 │ agent_routes /   │ ─────────────────────▶ │ agent_shim.py (stdlib only)   │
 │ agent_runtime    │                        │  - Popen(CLI, stdin=PIPE,     │
 │  ShimProc adapter│   loopback socket      │          stdout=out.log FILE) │
 │  (poll/wait/     │ ◀── stdin writes ────▶ │  - serves stdin (Mode B only) │
 │   stdin/stdout)  │                        │  - writes shim.json/exit.json │
 │ tail out.log     │ ◀── file read ──────── │  - orphan timer               │
 └──────────────────┘                        └──────────────┬────────────────┘
        │ manifest/checkpoint (atomic)                      │ owns pipe, parents
        ▼                                                   ▼
   data/agent_shims/<sid>/                            CLI (+ its MCP servers,
                                                      Bash children, bg tasks)
```

### 3.1 Why the shim is *not* in the output path

The CLI is spawned by the shim with `stdout=<open file handle>, stderr=STDOUT`
(measured working: the `out.log` in the proof is written by the stand-in CLI
directly). Consequences:

- No pump thread in the shim to stall, crash or lag; the shim cannot lose bytes.
- The log is complete even if the shim dies later; the server tails the file.
- A `proc.stdout` iterator can be faked by the server with a file-tail
  (section 5) so the existing stream readers (`_read_agent_stream_b`,
  `_read_agent_stream`, `_mode_a_reader`, Gemini `_read_stream`) keep iterating
  `for line in proc.stdout` unchanged.
- Cost: no EOF signal on the file when the CLI exits; the tailer ends the
  iteration when `exit.json` exists *and* the tail has drained.

The shim exists only for what a file cannot do: **hold the CLI's stdin pipe open
across server restarts** (a CLI in Mode B exits on stdin EOF), record the CLI's
exit status, and serve as the anchor process.

### 3.2 Two shim modes

| Mode | Engines | Stdin | Channel |
| --- | --- | --- | --- |
| `oneshot` | every Mode A engine (Gemini, Qwen, Codex, OpenCode, Goose, Aider, Kiro), Claude `-p`, `agent_jobs` | the prompt, written once from `prompt.txt` then closed (`mc.resume_context.write_context_prompt` today, called from `_mode_a_dispatch` 5516-5528) | **none**; no socket is opened |
| `interactive` | Claude Mode B (persistent stream-json) | open until closed | loopback socket |

This keeps the attack surface and the moving parts proportional: only Claude
Mode B needs a live channel. Everything else is "write a file, spawn, tail a
file, read `exit.json`".

### 3.3 The stdin channel: loopback socket, token-authenticated

Both a loopback TCP socket and a Windows named pipe were built and measured
(section 2): both survive, both round-trip, same latency. **Pick: loopback TCP
on `127.0.0.1:<ephemeral>`, with a per-shim random token.** Reasons:

1. **One code path** on Windows, macOS, Linux; tested in Linux CI with no
   `win32` branch. A named pipe is Windows-only, so POSIX needs a second
   implementation (AF_UNIX) and both need tests.
2. **stdlib only.** The shim must start in a bare interpreter. Python 3.14 here
   has no `socket.AF_UNIX` on Windows (`hasattr` is False); a named pipe from
   stdlib means `multiprocessing.connection` (pickle-framed; the proof used
   `send_bytes/recv_bytes` and an HMAC authkey to stay out of `pickle`), or
   ctypes overlapped I/O.
3. **Crisp failure semantics.** `ConnectionRefused` = shim dead; handshake
   reply = alive and tells you the last acknowledged sequence number.

Cost accepted: any local process can attempt a connection. Mitigation: bind
`127.0.0.1` only; first line must be the 128-bit token from `shim.json`
(user-owned directory); the shim accepts one **owner** connection at a time and
refuses a second (section 6.4). Not measured: whether Windows Defender Firewall
prompts for a loopback-only listener (the proof ran headless, so a prompt would
not have been visible). Named pipe is the fallback if it does.

Protocol (line-framed, text): `HELLO <token> <owner_pid> <boot_id>` →
`OK <last_seq> <cli_pid> <cli_alive>`; `SEND <seq> <nbytes>\n<bytes>` → `ACK <seq>`
or `ERR <reason>`; `CLOSE_STDIN` → `ACK`; `STATUS` → json. No kill command:
the server kills by PID (identity-guarded) as it does today
(`_kill_pid`, `agent_routes.py:570`), so the shim has no remote-kill surface.
Sequence numbers make resend-after-reconnect idempotent (the shim ignores
`seq <= last_seq`).

---

## 4. Files and manifest location

Per-session directory **`data/agent_shims/<session_id>/`**, sibling of
`data/projects`, never inside it. Precedent: the PID ledger
(`data/mc_child_pids.json`, `server.py:932`) and agent job logs
(`Path(DATA_DIR).parent / 'agent_job_logs'`, `agent_routes.py:1750-1754`, whose
comment cites the same CLAUDE.md DATA_DIR rule). A path outside `DATA_DIR` needs
no `EXCLUDED_SIDECAR_SUFFIXES` entry (`server.py:1091`); nothing here is a
project record. Add `data/agent_shims/` to `.gitignore` (the ledger is at
`.gitignore:319`); the files carry absolute machine paths and a socket token, so
they are operator-specific and must never be committed (CLAUDE.md "nothing
operator-specific"). Build-time check: `mc/backup.py` walks `data/` (94-99);
confirm the new directory is excluded or deliberately included.

| File | Writer | Content |
| --- | --- | --- |
| `spec.json` | server, before spawn | `argv`, `cwd`, env **names** only (values passed via the spawn environment, never written), engine, mode, session id, schema version |
| `prompt.txt` | server | stdin payload for `oneshot`; shim deletes after delivery |
| `out.log` | **the CLI** (via inherited handle) | raw merged stdout+stderr, append-only |
| `shim.json` | shim, atomic | shim pid + create_time, CLI pid + create_time, port, token, boot id, `last_seq`, `owner_pid` |
| `exit.json` | shim, atomic | CLI return code, end time, signal if any |
| `checkpoint.json` | **server**, atomic | `consumed_offset`, schema version, JSON projection of the session dict (section 6.2) |

`env` values are deliberately absent from disk: vault placeholders resolve
server-side and credentials must never land in a file under the repo tree
(CLAUDE.md "credentials go in the vault"). The spawn passes the environment on
the `Popen` call exactly as today; the shim inherits and passes it to the CLI.
After a restart the CLI already has its environment; nothing needs re-supplying.

All JSON writes use `mc.core._atomic_write_text` (already used by the ledger,
`process_ledger.py:122`); the shim vendors a 10-line equivalent to stay
stdlib-only. Per CLAUDE.md's exception policy, any file-I/O `except` logs.

---

## 5. The ShimProc adapter (what keeps the diff small)

`subprocess.Popen` is used for engines in ~20 places and consumed by readers,
`_stop_session`, `_kill_proc_background`, `_sysprompt_cleanup`, the guardian,
etc. (`.stdin.` appears 23 times in `agent_routes.py`, 10 in `agent_runtime.py`;
`proc.stdout` iteration in 9 reader sites; counts from grep, 2026-11-04.) Rather
than touch each, introduce **one factory** `spawn_engine_proc(...)` returning a
`Popen`-compatible object:

- `.pid` = the **CLI** pid (so `_kill_pid(proc.pid, tree=True)` and
  `_register_process` keep meaning what they mean today; the shim is not in the
  CLI's tree, so the kill does not take the shim with it; the shim sees the CLI
  exit and finishes).
- `.stdout` = text-mode iterator tailing `out.log` from `offset` (poll 50 ms;
  there is no inotify on Windows), yielding complete lines, decoding UTF-8 with
  `errors='replace'` to match `text=True, encoding='utf-8', errors='replace'`
  at every current site; ends when `exit.json` exists and the tail is drained.
  Holds back a trailing partial line until its newline arrives.
- `.stdin` = writer object whose `write/flush/close/closed` go through the
  socket (Mode B) or raise `BrokenPipeError` after close (oneshot).
- `.poll()/.wait()/.returncode/.kill()/.terminate()` from `exit.json` and PID
  liveness (create-time checked, the `process_ledger._proc_identity` guard).

This is also what makes the plan reversible: with the flag off the factory
returns a real `Popen`.

`agent_jobs.start_job(popen=...)` (`agent_jobs.py:165`) accepts an injected
`popen`, so jobs adopt the same factory with **no change to `agent_jobs.py`**
beyond persisting enough to rebuild `_jobs` on boot (section 10).

New code goes in new modules, per the "one unit, one file" rule
(`AGENT_RULES.md`): `mc/agent_shim.py` (the shim), `mc/agent_shim_tramp.py`
(trampoline), `mc/agent_shim_client.py` (spawn recipe, manifest I/O,
`ShimProc`), `mc/agent_shim_adopt.py` (boot adoption + checkpointer). Nothing is
appended to `agent_routes.py` (110 merges in 60 days per
`docs/ENTANGLEMENT_AUDIT.md`); the 20 call sites each shrink to one factory call.

---

## 6. Startup adoption

### 6.1 Boot order

Today: port-conflict wait (`server.py:3551`) → vault key (3573) → **reap**
(3578) → scheduler etc. New:

1. After the port wait and vault handover, **new boot phase `adopt agent shims`**
   (`mc/agent_shim_adopt.py`), *before* the reaper.
2. The reaper receives a `protected_pids` set (CLI pid + shim pid of every
   adopted manifest) and skips them. Ledger entries gain `adoptable: true,
   session_id`; an entry whose manifest fails validation is reaped exactly as
   today. The split-brain guard (`process_ledger.py:168-176`) stays.
3. Everything else boots as before; the hivemind loop (`server.py:3581`) sees
   adopted sessions in `agent_sessions` on its first tick.

### 6.2 What adoption does per manifest

1. Validate: schema version; `shim.json` pid alive **and** create-time matches
   (PID-reuse guard); CLI pid alive or `exit.json` present; `spec.json`
   engine in the supported set; `checkpoint.json` parses. Any failure → log it,
   leave the process to the reaper, fall back to today's revive-on-next-message.
2. Fence: connect `HELLO` with this server's pid + boot id. The shim accepts one
   owner at a time; a stale owner pid that is dead is displaced, a live one is
   refused. This keeps two servers (the documented Windows port-sharing hole,
   `process_ledger.py:160-167`) from driving one CLI.
3. Rebuild `agent_sessions[sid]` from `checkpoint.json` (JSON projection of the
   session dict) and re-register it with the project manager
   (`mgr.session_ids`), the same registration `_hm_runtime_dispatch` does
   (`hivemind_routes.py:1047-1052`). Attach a `ShimProc` whose `.stdout` starts
   at `consumed_offset`.
4. Start the matching reader thread (`_read_agent_stream_b` / `_read_agent_stream`
   / `_mode_a_reader` / the Gemini reader) on the `ShimProc`, `_register_process`
   it (tracked → ledgered again), and let it run. The reader's existing
   end-of-stream paths (turn `result`, process exit, error classification)
   fire completion callbacks, `_log_agent_completion`, the delegation outbox,
   hivemind worker-done, etc. through their normal code. That is the
   "recapture the session" behaviour Ron asked for.
5. Skip `_bg_tasks.drain` (`agent_routes.py:6263`) for an adopted proc: the
   process is the same one that started those tasks, so they did survive.

### 6.3 The hard problem: rebuilding reader state (decision for Dave, section 12 Q1)

`_read_agent_stream_b` is ~750 lines of stateful parsing with side effects
(spawner notification, stop-hook boundary tracking, `_midturn`, activity state,
`_bg_tasks`, cost accumulation `proc_cost`/`proc_turns`, `log_lines`). A restarted
server cannot start mid-stream with an empty session. Two ways to get state back:

- **A. Checkpoint (recommended).** The server writes `checkpoint.json` every ~1 s
  while dirty and *synchronously before any completion side effect*:
  `consumed_offset` (updated in memory per line) + a JSON projection of the
  session dict (keys whose values are JSON-serializable, minus a denylist:
  `proc`, locks, threads, callables) + the last ~2000 `log_lines`. Adoption
  resumes at `consumed_offset`. **At-least-once window:** lines consumed after
  the last checkpoint are re-read. Mitigations: the checkpoint is forced before
  completion paths; `log_lines` entries are de-duplicated by source offset; the
  delegation outbox already allocates turns idempotently
  (`_delivery_store.allocate_turn`, used at `agent_routes.py:7557`). Worst
  case is a duplicated tool-activity line in the UI, which is cosmetic;
  a duplicated *completion* is the case the forced checkpoint exists to prevent.
- **B. Replay with side effects suppressed.** Re-parse `out.log` from byte 0 with
  a `replaying` flag the reader's ~30 side-effect call sites would each honor.
  Exact, but a refactor of the biggest function in the biggest file
  (`agent_routes.py`, 17,896 lines), with a flag that every future reader edit
  must remember. Rejected for v1; it is the right tool if A's window proves too
  leaky.

The projection test (P4) asserts a round-trip of a real captured session dict.

### 6.3.1 Completions owed across a crash, and what OFF means (P4)

`mc/agent_shim_completion.py` stamps a `_pending_completion` record into the
forced checkpoint before a completion's first side effect and clears it only
once the work is done. Boot (`replay_pending`, after the delegation store is up,
before `workflow run adoption`) runs the work of every stamped checkpoint again.
A record is cleared only when each thing it owes has *durably* happened:

- **Spawner wake.** `_notify_agent_spawner` is true for a newly committed outbox
  event *and* for an identical one already there
  (`DeliveryStore.enqueue_outcome` = `committed` / `duplicate`): either way the
  wake exists. Refused (revoked, no identity), a conflicting identity (raises)
  and a failed enqueue are not a wake; the record stays.
- **Workflow step.** Under replay the callback runs synchronously
  (`_notify_workflow_step(sync=True)` → `on_agent_step_complete(advance=False)`)
  and `_notify_workflow_sent` is set only after it returns, i.e. after the step's
  transition is on disk. It does not start the next step: `workflow run
  adoption`, which follows, finds a run with no step in flight and advances it.
  A thread would let the record clear before the step moved, and adoption would
  then read the dispatch-time `interrupted` agent-log row and refuse the late
  result.

**Flag OFF (Ron/Dave decision, 2026-11-05).** `replay_pending` is *not* gated on
`agent_survive_restart_enabled`. OFF stops new shim work (no shim-run spawns, no
new pending records) and adoption; it does not drop completions the server
already owed when the flag was turned off. Pausing replay would strand a result a
parent or workflow is waiting for, with no recovery path until the flag came
back. To get the strict alternative, gate the `replay pending completions` boot
phase in `server.py` on the flag (one condition); records then wait on disk.

### 6.4 Lifetime without a server

The shim's states:

| State | Condition | Behavior |
| --- | --- | --- |
| attached | an owner connection is open (or owner pid alive) | normal |
| orphaned | no owner for `shim_orphan_grace_min` (default 20) | start the idle test |
| draining | CLI exited | write `exit.json`, wait `shim_exit_grace_min` (default 60) for an owner to collect, then exit |
| expired | no owner for `shim_orphan_max_hours` (default 12) | tree-kill the CLI by PID (identity-checked), write `exit.json`, exit |

Idle test while orphaned: if `out.log` has not grown for the grace window **and**
(`psutil` present) the CLI's tree shows no CPU/new children, close the CLI's stdin
(Mode B CLIs exit on EOF) and finish. Log-silence alone is *not* sufficient: a
25-minute silent `pytest` in a Bash tool (the MC-1149 incident) prints nothing
to the stream. Without `psutil` the shim falls back to the hard `orphan_max`
ceiling only. A Mode A CLI needs none of this: it exits at turn end and the shim
follows. Intentional shutdown (`_perform_server_shutdown_async`) and the Stop
button keep killing, so "no server returns" after a *power-off* leaves nothing
behind.

---

## 7. Per-engine coverage (vendor-agnostic is a standing position)

The shim is engine-blind: it runs an argv and files its output. Engine-specific
behavior stays in the existing reader/parser classes, which keep receiving lines.

| Engine | Process model today | Shim mode | What survives a restart | Specifics |
| --- | --- | --- | --- | --- |
| Claude Mode B | persistent stream-json CLI (`agent_routes.py:12360`) | `interactive` | the running turn, bg tasks the CLI started, MCP servers | Needs the socket, resend-by-seq, `_sysprompt_cleanup` temp file lifetime tied to `ShimProc` exit |
| Claude `-p` (Mode A) | per-turn, stdin DEVNULL (12521, 14204) | `oneshot` (no prompt file) | the turn | |
| Codex | per-turn `codex exec --json` (`agent_runtime.py:9703`) | `oneshot` | the turn | `session_mcp_launch.prepare` / `vendor_fence` env wiring happens before the spawn and is unchanged; `_codex_last_prompt` already on the session dict |
| Gemini | per-turn (4786, 5276) | `oneshot` | the turn | |
| Qwen | per-turn (7665 via `_mode_a_dispatch`, 7724) | `oneshot` | the turn | |
| OpenCode / Goose / Aider / Kiro | per-turn (10268, 10635, 10940, 11289 and 10295/10661/10985/11316) | `oneshot` | the turn | Goose/Aider/Kiro use `DEVNULL` stdin; no prompt file |
| `agent_jobs` | plain child + daemon watcher | `oneshot` | the job *and its completion delivery* | section 10 |

Two engine-wide caveats: the per-turn engines already re-inject their system
context on every turn (`_compose_respawn_prompt`, `agent_runtime.py:5440`), so
nothing about adoption changes follow-ups; and each runtime's
`write_followup` respawn must go through the factory so *the next turn* is also
survivable (otherwise only the first turn of a session would be).

---

## 8. Hiveminds

- **Workers:** Mode A sessions through `_hm_runtime_dispatch`
  (`hivemind_routes.py:1017`); covered by P2 for free once `runtime.dispatch`
  uses the factory. Their session dict carries `hivemind_*` fields, roster
  `character_ref`, `trigger_type='hivemind_worker'`; all travel in the
  checkpoint projection. Their callbacks are the module constant
  `_RUNTIME_CALLBACKS` (`agent_routes.py:13631`, `14596`), not per-session
  closures, so adoption re-attaches them by reference.
- **Orchestrator thread:** state is on disk (`HIVEMIND_DIR/<id>/manifest.json`,
  `workstreams/*.json`). It restarts at boot (`server.py:3581`) and resumes on its
  10 s tick. Gap cost: new worker spawns are delayed by the downtime (seconds).
- **Orchestrator agent session** (the Claude session that decomposes the goal)
  is an ordinary session; adopted like any other.
- **Blocker change:** `active_hiveminds` stops blocking when every `running`
  worker session of that hivemind is adoptable. A hivemind with a worker whose
  engine/mode is not adoptable still blocks (fail closed).
- **Known unknown:** a hivemind whose worker *finishes during the downtime*:
  adoption reads `exit.json`, the reader runs to end-of-stream and sets the
  session `completed`; the orchestrator loop's finished-worker detection
  (1776-1795) then fires on the next tick. This path is the main P4 end-to-end
  test.

---

## 9. Restart blockers and the restart path

`_get_active_restart_blockers` (`system_routes.py:1679`) returns `active_sessions`
today. New shape (additive, so existing UI keeps working):

```json
{ "active_sessions": [...non-adoptable running...],
  "adoptable_sessions": [...running, will survive...],
  "active_hiveminds": [...hivemind with a non-adoptable worker...],
  "adoptable_hiveminds": [...] }
```

A session is adoptable iff `agent_survive_restart_enabled`, a manifest exists,
the shim is alive with create-time match, the engine is supported, and the last
checkpoint is no older than `shim_checkpoint_max_age_s` (default 10; stale means
the checkpointer is wedged, so block rather than risk a lossy adoption). The four
callers (2529, 2550, 2732, 2766) test only the blocking lists. `force` keeps its
meaning.

`_stop_all_sessions_for_restart` (1738) skips adoptable `running` sessions:
no stdin close, no tree-kill, no `_unregister_process` (which would drop the
ledger entry), and flushes one last checkpoint instead. **Idle Mode B sessions
keep today's behavior** (gracefully stopped, revived by `-r` on the next
message): they have no in-flight turn to lose, and adopting them would keep
~hundreds of MB of CLI+MCP resident across restarts. Exception: an idle session
with open bg tasks or an open agent job is treated as running, matching
`blocks_eviction` (`background_tasks.py:240`) and `open_job_count`
(`agent_jobs.py:139`). (Decision Q2.) `_perform_server_shutdown_async` is
unchanged: power-off kills everything.

The `os._exit(0)` at 1972 is the one server-death mode the proof shows is safe
for plain children on Windows, but only the `robust` recipe also survives the
other three; the restart path is the *least* dangerous way to exercise this.
The crash cases (taskkill /T of the server by a watchdog, an installer, a user)
are where the recipe earns its keep.

---

## 10. MC-1149 phase 2 and `agent_jobs`: one supervisor, two corrections

**Agree** one supervisor serves both. Corrections to scope:

1. **`agent_jobs` need the shim for the *restart* case, not the roll.** Per §1.6
   they already survive a roll (server-owned) but not a restart. Wiring
   `start_job(popen=shim_popen)` fixes that, and the shim's `exit.json` +
   persisted job record (`job_id, session_id, command, log_path, started_at,
   timeout`, in the same `data/agent_shims/<job id>/`) let boot rebuild `_jobs`
   and fire `_job_on_complete` (`agent_routes.py:1763`+) for jobs that finished
   during the downtime. The job timeout (`agent_jobs.py:227-244`) moves into the
   shim (it is the only process alive to enforce it).
2. **The roll is a different problem from the restart.** `_do_respawn`
   deliberately kills the old CLI (`agent_routes.py:14693`,
   `_kill_proc_background`) and then spawns a new one, so a shim around the *old*
   CLI does not keep its Bash children alive by itself. What keeps them alive is
   killing **only the CLI and its MCP servers and sparing the pids MC-1149 phase 1
   already records** (`mc/bg_job_liveness.py`, pid + create_time from the
   tool_result scan). That is a "retire-with-spare" operation, which the shim is
   the right owner of (it outlives the server, holds the handles, and can write
   the completion record). Measured support: parent exit alone leaves children
   running on Windows (section 2, every `exit` cell). **Unmeasured and
   load-bearing:** whether Claude's CLI puts its Bash children in a Job object
   that dies with the CLI process. If it does, sparing is impossible on Windows
   without the CLI's cooperation and phase-1's guidance ("route long jobs
   through the job route") is the ceiling. P5 starts with that measurement.
3. Completion for a spared job is delivered through the same outbox path as an
   `agent_job` (`_job_on_complete`), so the post-roll session gets a normal
   follow-up turn, which is MC-1149's stated outcome.

Reuse that fits: `agent_jobs.start_job`'s `popen` seam, `_job_on_complete`'s
outbox delivery, `bg_job_liveness` identity checks, `process_ledger._proc_identity`
(PID-reuse guard). Reuse that does not: `agent_jobs._jobs` as the source of truth
(in-memory; the manifest directory becomes the source, `_jobs` a cache).

---

## 11. Failure modes

| Failure | Behavior |
| --- | --- |
| Shim crashes | Mode B: CLI stdin pipe closes → CLI exits on EOF → session ends as an unexpected exit → existing revive-by-`-r` path. Mode A: CLI is unaffected (stdout is a file), turn finishes; only `exit.json` is missing → server falls back to PID liveness + log quiescence. Test required. |
| CLI dies while server is down | `exit.json` has the rc; adoption runs the reader to EOF; normal error classification (`explain_exit_error` etc.) applies. |
| `claude` CLI upgrade mid-session | Not affected (process image already loaded). |
| Stale manifest, PID reused | `create_time` guard on shim and CLI (`process_ledger.py:128-144` pattern); mismatch → ignore, never kill. |
| Two servers | Owner fence in `HELLO` (6.2). The second gets `REFUSED`, logs, does not adopt. |
| Checkpoint older than the log | At-least-once re-read (6.3). Mitigated by forced checkpoint before completion side effects. |
| Checkpoint schema newer/older than code | Fail closed: do not adopt, fall back to reap + revive; schema version in every file. |
| Disk full / log huge | Stream-json with large tool results can be big; cap by pruning `out.log` for finished sessions after N days (`shim_log_retention_days`) and surfacing a warning at a size threshold. Adopted-session logs are never pruned while live. Unmeasured: typical log size per hour; measure in P2. |
| Port exhaustion / loopback blocked by security software | `STATUS` handshake fails → treat as non-adoptable (blocks restart rather than risk killing a turn); named-pipe fallback is the escape hatch. |
| Orphaned shim after a crash with no restart | 6.4 timers; plus a boot-time and 5-minute sweep reaps shims whose `owner` is dead and whose manifest is older than `orphan_max`. This extends the 2026-09-28 orphan-sweep position (strict criteria, whole chain dead, logged) rather than contradicting it: adoptable shims are explicitly *excluded* from that sweep while their manifest is valid. |
| Agent calls the Clayrune API during the gap | Fails for seconds (as it does when a restart happens between turns today). Hooks are local (§1.9). |
| Frozen build | Shim is `<app> --clayrune-shim`; `app.py` must handle it before booting the app (mirror `app.py:22-27`). Without it, the shim cannot start in a frozen build. |
| macOS | Recipe unmeasured; POSIX double-fork + setsid proven only on WSL2 Ubuntu. P1 must run on a real Mac before enabling there. |
| `taskkill` on the CLI pid | Kills the CLI tree (as today); the shim sees exit and finishes. The shim is not in that tree. |
| Process-guard hook / PROCESS HYGIENE rule | Agents still may only kill PIDs they started; the shim is MC's, not an agent's. Nothing here lets an agent kill by image name. |

---

## 12. Decisions for Dave (options, cost, recommendation)

**Q1. Session-state rebuild: checkpoint or replay?** (6.3)
- **A. Checkpoint + offset (recommended).** Cheap, local to one new module +
  hooks at completion points. Cost: at-least-once window; needs the forced
  checkpoint discipline and a projection round-trip test.
- B. Replay with suppression. Exact but refactors the biggest reader in the
  biggest file and adds a flag every future edit must honor. Several sessions of
  work and a permanent tax.

**Q2. Adopt idle Mode B sessions in v1?** (9)
- **No (recommended).** Idle sessions revive by `-r` losing nothing; adopting
  keeps CLI+MCP trees resident across restarts. Exception: open bg task / job.
- Yes. Warm contexts survive, but memory stays pinned and the adoption set grows
  from "running" (few) to "all tabs" (many), widening every failure mode above.

**Q3. Orphan timers.** (6.4) Recommend `grace 20 min`, `exit_grace 60 min`,
`orphan_max 12 h`, configurable. Cost of too short: a long silent job is
interrupted during a long outage. Too long: an unattended box keeps a CLI and
MCP trees alive for hours. These are guesses; they should be tuned from P6 data.

**Q4. Windows spawn fallback: accept WMI?** (2) `Invoke-CimMethod
Win32_Process.Create` is the only measured escape when the launching job forbids
breakaway. Cost: a PowerShell dependency (present on Windows 10/11, absent on
nothing we support). Measured 2026-11-04 (`_scratch/survive_proof/envrun.py`):
the WMI child runs as the **same user in the same session** (session 1), but it
does **not inherit the caller's environment** (47 variables vs 87; a marker
variable set in the caller is absent) and its cwd is the Windows system
directory. So the shim cannot receive env, cwd or secrets through the process;
it would have to read them from a file, and CLAUDE.md forbids secrets on disk.
Options: (a) refuse adoption when breakaway fails: the session is spawned the
old way, stays non-adoptable and blocks restart as today **(recommended for
v1)**; (b) hand env to the WMI-spawned shim over a one-shot loopback socket the
server opens and the shim dials, so nothing touches disk; (c) per-session
Scheduled Task one-shot; (d) a Windows service. Net:
**worth knowing first which job the real launch path uses**; P1 should log which
branch was taken so this is answered with data.

**Q5. Shim language.** Python stdlib (recommended: one runtime, testable, frozen
mode via `--clayrune-shim`) vs a small Rust binary (like `mc-tunnel`: smaller
and no interpreter dependency, but a second toolchain in the build and in every
platform's CI/signing path). Cost of Python: ~21 MB resident per session
(measured).

**Q6. Default.** Flag off until P4 is live-proven on this box (a real `claude`
turn survives `POST /api/system/restart`, a hivemind completes across one), then
default ON for `oneshot` engines first, Mode B second.

---

## 13. Phased build plan

All behind `agent_survive_restart_enabled` (default false). Each phase is
independently mergeable and leaves behavior unchanged when the flag is off.

**P1: shim, client, trampoline (no wiring).** `mc/agent_shim.py`,
`mc/agent_shim_tramp.py`, `mc/agent_shim_client.py`, `.gitignore`,
`app.py --clayrune-shim`. Tests (`tests/test_agent_shim.py`):
spawn survives a stand-in server killed by `os._exit` / `taskkill /T` / job
close (Windows real; POSIX killpg + descendants-walk in CI); stdin `SEND` seq
idempotent across reconnect; token rejection; owner fence refuses a second
owner; `exit.json` rc; PID-reuse guard; partial-line tail hold-back;
orphan timers with injected clock; spawn-recipe branch logged. Exit criterion:
the section-2 matrix reproduced as an automated test on Windows + Linux.

**P2: Mode A engines through the factory.** Replace the ~14 non-Mode-B spawn sites
with `spawn_engine_proc`; `ShimProc` complete. Tests: each runtime's existing
suite passes with the flag on using a fake CLI; kill-server-mid-turn leaves the
log growing; log-size measurement logged. Exit: all Mode A runtime suites green
both flag states.

**P3: Claude Mode B.** Sites 12360, 14070, 14776, 14853, 7732: socket stdin,
`_bounded_stdin_write` (`agent_routes.py` ~14339) over the socket, interrupt
(kill by pid + new shim), `_sysprompt_cleanup`. Tests: stream-json round-trip,
stdin-hang watchdog still kills the CLI, interrupt-respawn, mid-turn roll
unchanged.

**P4: checkpoint, adoption, reaper, blockers, hivemind.**
`mc/agent_shim_adopt.py`, `process_ledger` protected set, `system_routes`
blockers + stop-all. Tests: session-dict projection round-trip on a captured
real dict; adoption of running / finished-during-downtime / dead-CLI / stale PID
/ schema-skew manifests; reaper skips protected, still reaps non-adoptable;
blocker partition; **end-to-end:** server subprocess restarted mid-turn with a
fake CLI, turn completes once, `_log_agent_completion` fires exactly once;
hivemind worker finishing during the gap. Then live: this box, a real `claude`
turn, `POST /api/system/restart` (standing approval for :5199 restarts in memory
`feedback_server_restart_approval`).

**P5: MC-1149 phase 2 + jobs.** First measure the CLI Bash-child job-object
question (§10.2). Then `agent_jobs` through the shim with boot-time
`_jobs` rebuild and `_job_on_complete` recovery; retire-with-spare at the roll
if the measurement allows. Tests: job finishes during downtime → one outbox
delivery; roll spares a recorded pid; roll does not spare unrecorded pids.

**P6: dogfood and defaults.** Floor/Settings surfacing ("N sessions will keep
running through the restart"), orphan-timer tuning from real data, log
retention, default flip per Q6, `docs/` + CHANGELOG, the position record for the
reaper (`process_ledger` header text now describes behavior that will change).

Estimate: P1 and P2 are well scoped (the factory is mechanical); P3 and P4 carry
the risk (reader state, at-least-once window). P5 is gated on one measurement.

---

## 14. What this spike did not establish

- Which Job-object flags the **real** server launch path has (Scheduler S4U,
  hidden-launch vbs, Tauri). The recipe is built to not depend on it.
- Windows Defender Firewall behavior for a loopback-only listener on an
  interactive desktop.
- macOS and native Linux behavior of the trampoline (only WSL2 Ubuntu).
- Whether Claude's CLI wraps Bash children in a Job object (decides §10.2).
- Typical `out.log` growth per hour for a Claude stream-json session.
- A live adoption with a real engine CLI: the proof used a stand-in CLI; no
  engine binary was run, and nothing was restarted on :5199.

## Appendix: reproducing the measurement

```
# Windows matrix (about 3 minutes; kills only PIDs it spawned)
.venv\Scripts\python.exe _scratch\survive_proof\proof.py orch
# subset
...\proof.py orch robust-taskkill_tree robust-jobclose_nobreak
# POSIX half (WSL)
python3 _scratch/survive_proof/posix_probe.py
# WMI child environment/user/session, and the breakaway-refused error
.venv\Scripts\python.exe _scratch\survive_proof\envrun.py
.venv\Scripts\python.exe _scratch\survive_proofkerr.py
```

Roles inside `proof.py`: `orch` (runner and "new server"), `server`, `tramp`,
`shim` (socket + named pipe), `child`. `_scratch/` is gitignored; the matrix in
section 2 is the record.
