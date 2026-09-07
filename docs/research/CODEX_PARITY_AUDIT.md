# Codex provider parity audit

**Question:** can a Clayrune user switch a project from `claude` to `codex` and lose nothing?
**Answer:** no — but the ceiling is far higher than the code currently assumes.

**Date:** 2026-09-07
**Method:** read `CodexRuntime` (`mc/agent_runtime.py:3353`) against `ClaudeRuntime` (`:878`)
and the `AgentRuntime` ABC (`:433`); then walked Clayrune's feature surface asking "does
this reach Codex?"; then verified against the live CLI.

**Verification status:** `codex-cli 0.151.0` is installed on the audit machine and was
exercised read-only (`codex --help`, `codex exec --help`, `codex exec resume --help`, and
one trivial `codex exec --json -s read-only`). Runtime claims below are marked **[live]**
when backed by real command output and **[code]** when derived from reading source only.
Nothing was installed, modified, or restarted.

---

## 0. The headline finding: Codex leaves a rich, readable transcript — and Clayrune looks for it in the wrong place

This is the single most consequential fact in the audit, because the size of everything
else depends on it.

`CodexRuntime.transcript_path()` (`mc/agent_runtime.py:3722`) looks here:

```python
p = Path.home() / '.codex' / 'sessions' / session_id
...
transcript = p / 'transcript.jsonl'
```

That layout does not exist. **[live]** Codex actually writes:

```
~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<ISO8601>-<thread_id>.jsonl
```

A recursive search for `transcript.jsonl` anywhere under `~/.codex` returns nothing, and no
session directory is named after a thread id. `transcript_path()` therefore returns `None`
on **every** Codex session that has ever run. Codex is currently treated by all of Clayrune
as a provider with no transcript store — the same bucket as Gemini — and that classification
is simply wrong.

What the rollout file actually contains **[live]**, from a census of every rollout file on
the audit machine (83 files, 83/83 conforming):

| Record | Payload | Carries |
| --- | --- | --- |
| `session_meta` (always line 1) | — | `cwd`, `id`/`session_id`, `originator`, `source`, `cli_version`, `context_window`, `base_instructions` |
| `response_item` | `message` | `role` (`user`/`assistant`/`developer`), `content[].text` |
| `response_item` | `reasoning` | thinking text |
| `response_item` | `custom_tool_call` / `custom_tool_call_output` | tool name, arguments, **and full result body** |
| `event_msg` | `task_started` / `item_completed` / `token_count` / `task_complete` | turn boundaries, token counters |

Three consequences worth stating plainly:

1. **Project scoping is solvable.** `session_meta.cwd` is present on 83/83 files, so a
   Codex equivalent of `ClaudeRuntime._encoded_dir_candidates()` (`:1208`) is a scan-and-
   filter on `cwd` rather than a path-encoded directory lookup. Different index, same
   capability.
2. **The rollout is *richer* than what Clayrune captures today.** `custom_tool_call_output`
   holds full tool result bodies. Clayrune's only current record of a Codex session is
   `session['log_lines']`, and `_mode_a_reader` writes tool calls as the bare string
   `f"[{runtime.name} tool: {tname}]"` (`mc/agent_runtime.py:3271-3275`) — command and
   output both discarded. Clayrune is throwing away detail that is already on disk.
3. **Clayrune already holds the key and drops it.** `_mode_a_reader` captures the
   `thread.started` id into `session['provider_session_id']` (`mc/agent_runtime.py:3277`).
   That field is **written in exactly one place and read in zero** — grep across `mc/`,
   `server.py` and `static/` returns only the dataclass declaration
   (`mc/agent_runtime.py:167`) and that one write. It is never persisted to the agent log
   either: the `entry` dict in `_log_agent_completion` (`mc/blueprints/agent_routes.py:4283`)
   carries `claude_session_id` and no provider-neutral equivalent. So the one identifier
   that would locate the rollout file, and the one identifier `codex exec resume <ID>`
   needs, is captured in memory and thrown away at process exit.

**Verdict: Codex leaves a readable transcript.** The transcript-derived half of Clayrune
(conversation rail, in-chat search, Documents tab, Scribe's preferred path, subagent
visibility) is *reachable* for Codex. None of it is reached today.

---

## 1. ABC conformance — where `CodexRuntime` returns `None`, a stub, or nothing

Every abstract method is implemented; Codex is not a stub runtime. The gaps are in the
optional hooks and in the `ClaudeRuntime`-only methods that never made it onto the ABC.

| ABC member | ABC line | Codex | Claude | Note |
| --- | --- | --- | --- | --- |
| `resolve_binary` (abstract) | `:533` | `:3386` | `:931` | Codex's is the more thorough of the two — probes the native OpenAI installer dir and npm prefixes, then falls back to `npx` |
| `health_check` (abstract) | `:539` | `:3768` | `:1803` | OK |
| `capabilities` (abstract) | `:544` | `:3806` | `:1828` | Four of its flags are wrong — see §2 |
| `dispatch` (abstract) | `:591` | `:3831` | `:1855` | OK (Mode A) |
| `write_followup` (abstract) | `:596` | `:3865` | `:1862` | Respawns a **new** Codex thread; does not use `exec resume` |
| `interrupt` / `stop` (abstract) | `:602` / `:607` | `:3902` / `:3905` | `:1869` / `:1875` | OK |
| `build_command` | `:548` | `:3471` | `:992` | No effort, no MCP config, no incognito, no attachments |
| `parse_event` | `:562` | `:3501` | `:1061` | Genuinely good — handles both the 0.133 and 0.151 schemas plus an unknown-type fallback |
| `transcript_path` | `:580` | `:3722` | `:1236` | **Broken** — wrong path, always `None` (§0) |
| `oneshot` | `:611` | `:3908` | `:1708` | OK |
| `explain_exit_error` | `:622` | `:3939` | (n/a) | OK |
| `auth_status` | `:630` | **not overridden** | `:1891` | Falls through to the base impl, which calls `health_check()` — a `codex --version` subprocess on every provider-list load. The ABC docstring at `:635` explicitly asks providers to override this |
| `auth_probe` | `:654` | **not overridden** | `:1910` | Same |
| `auth_logout` | `:662` | **not overridden** | — | Returns "not supported" |
| `auth_login_argv` | `:673` | **not overridden** → `None` | `:1917` | No piped login flow |
| `with_attachment_hint` | `:502` | **never called** | (n/a) | Only `GeminiRuntime` calls it (`:2664`, `:2997`, `:3006`) |
| `with_mc_tool_protocol` | `:510` | **never called** | (n/a) | Only Gemini (`:2662`, `:3003`) |
| `apply_mc_tool_blocks` | `:521` | **never called** | (n/a) | Only Gemini's reader (`:2914`) |

**Methods that exist only on `ClaudeRuntime` and are not on the ABC at all** — this is the
structural finding, because it means no provider can implement them without an interface
change first:

`list_sessions` (`:1298`), `list_written_markdown` (`:1426`), `list_running_subagents`
(`:1512`), `parse_transcript_file` (`:1621`), `memory_path` (`:1680`),
`_encoded_dir_candidates` (`:1208`), `_build_transcript_path` (`:1283`).

Their consumers do not go through the registry — they hardcode the provider:

- `mc/blueprints/agent_routes.py:8633` — `_agent_runtime.get_runtime('claude').list_written_markdown(...)` (Documents tab)
- `mc/blueprints/agent_routes.py:7292` — `_agent_runtime.get_runtime('claude').list_running_subagents(...)` (Floor subagents)
- `mc/memory.py:139`, `:216`, `:231`, `:242`, `:258` — five `get_runtime('claude')` calls inside the memory system

---

## 2. Feature-by-feature

Legend: **Yes** = works. **Partial** = runs but loses something a Claude user would keep.
**No** = does not work. **Blocked** = the Codex CLI does not expose what would be needed.

### Install, auth, model

| Feature | Claude | Codex | Evidence | To close |
| --- | --- | --- | --- | --- |
| Binary resolution incl. non-PATH installs | Yes | **Yes** | `mc/agent_runtime.py:3386-3460`; probes native installer dir + npm prefixes + `npx` fallback | — |
| Install / version detection | Yes | **Yes** | `:3768`; **[live]** `codex --version` → `codex-cli 0.151.0` | — |
| Auth state detection | Yes | **Yes** | `:3737` reads `~/.codex/auth.json` for both OAuth tokens and API key; tested `tests/test_provider_runtimes.py:547` | — |
| Cheap cached `auth_status` | Yes (`:1891`) | **Partial** | Not overridden → base impl at `:630` spawns `codex --version` per call | Override with a cached read of `auth.json` (~20 lines) |
| Login via OS terminal | Yes | **Partial** | `/api/agent/<provider>/auth-login` is provider-agnostic (`mc/blueprints/agent_routes.py:1845`) — but it 400s on `if not bin_path` (`:1859`), and `resolve_binary()` returns `None` on an npx-only install (`mc/agent_runtime.py:3389-3391`) | Make the route use `_cmd_prefix()` |
| Login in-app / remote (piped or PTY) | Yes (`:1917`) | **No** | `auth_login_argv()` not overridden → `None` → route falls to PTY-or-fail (`agent_routes.py:1698-1712`) | Test whether `codex login` prints its URL over a pipe; if yes this is a 5-line override |
| Programmatic logout | — | **No** | Base impl `:662` | Low value |
| Model picker | Yes | **Yes** | `MODEL_CHOICES` `:3360-3367`; catalog pinned by `tests/test_provider_runtimes.py:1139` | — |
| Cross-provider model guard | Yes | **Yes** | `model_supported()` `:455`; `tests/test_provider_runtimes.py:1121` asserts `codex` rejects `claude-opus-5` | — |
| Effort / reasoning level | Yes (`--effort`, `:1033`) | **No** | `build_command` `:3471` has no effort parameter | **Cheap.** **[live]** `codex` accepts `-c model_reasoning_effort=<level>`; the key is a documented `config.toml` field |

### Dispatch and streaming

| Feature | Claude | Codex | Evidence | To close |
| --- | --- | --- | --- | --- |
| Dispatch | Yes | **Yes** | `:3831` → `_mode_a_dispatch` `:3134` | — |
| Event-stream parsing | Yes | **Yes** | `:3501`; **[live]** a real `codex exec --json` run emitted exactly `thread.started` → `turn.started` → `item.completed`(`agent_message`) → `turn.completed`, all four handled | — |
| Protocol noise kept out of chat | Yes | **Yes** | `_is_protocol_json` guard `:3255-3265`; `tests/test_provider_runtimes.py:1185` | — |
| Interrupt / stop | Yes | **Yes** | `_mode_a_interrupt` `:3335` | — |
| Token usage counters | Yes | **Yes** | `_mode_a_reader` TURN_END branch `:3280-3291`; **[live]** `turn.completed` carries `usage` | — |
| Tool-call rendering | Yes (full blocks) | **Partial** | `parse_event` builds a correct tool block with command + output (`:3595-3611`), then `_mode_a_reader:3271-3275` reduces it to `[codex tool: shell]` and drops both | Have the Mode-A reader keep the block, as the Claude readers do |
| Thinking / reasoning display | Yes | **Partial** | `parse_event` emits `THINKING` (`:3576`), but `_mode_a_reader` has no THINKING branch — it lands in the catch-all `:3296-3301` and is appended as plain chat text, visually indistinguishable from the answer | Add a THINKING branch to the Mode-A reader |
| Activity states (thinking vs writing) | Yes | **Blocked** | Claude-only: `--include-partial-messages` `:1023-1030`, consumer `agent_routes.py:3044` gated on `msg_type == 'stream_event'` at `:3185`/`:3405`. **[live]** `codex exec --json` emits only whole-item events; there is no delta/partial event type in the protocol | Not closable without upstream deltas |
| Cost reporting | Yes | **No** (mis-declared) | `capabilities()` `:3822` sets `emits_cost=True`; **[live]** `turn.completed` has keys `['type','usage']` only — no `cost_usd` | Set the flag to `False`, or derive cost from `usage` × a price table |
| Attachments / images | Yes | **No** (mis-declared) | `image_input=True` at `:3823`, but `dispatch` `:3856` never calls `with_attachment_hint()` and `build_command` never uses Codex's `-i/--image` flag (**[live]** present in `codex exec --help`) | **Cheap.** One `with_attachment_hint()` call, matching Gemini `:2664` |
| Quota-failure surfacing (MC-934) | Yes | **No — silent key-name mismatch** | The emitter logs `model={session.get('model', '')}` (`agent_routes.py:4491`), but `_dispatch_via_runtime` stamps the key `'agent_model'` (`:4908`) and `_mode_a_dispatch` never sets `'model'` at all (`mc/agent_runtime.py:3188-3204`). Only the Claude path sets it (`agent_routes.py:4533`). `_recent_quota_failures` then drops any line with an empty model: `:5209` — `if prov != provider or not model: continue`. So every Codex quota failure is logged with `model=` blank and discarded by both the composer warning (`:1268`) and the hard block `_model_quota_blocked` (`:5227`) | **One key name.** Read `agent_model` as a fallback in the emitter |
| Live rate-limit window | Yes | **No** (correctly declared) | `emits_rate_limit=False` `:3821`; the consumer at `mc/blueprints/system_routes.py:404` reads Claude's `rate_limit_event` envelope | Honest gap |
| Error explanation | Yes | **Yes** | `explain_exit_error` `:3939` | — |
| `oneshot` (Scribe / condense / summary) | Yes | **Yes** | `:3908` | — |

### Conversation, history, resume

| Feature | Claude | Codex | Evidence | To close |
| --- | --- | --- | --- | --- |
| **Transcript location** | Yes | **No — broken** | `transcript_path` `:3722` points at a layout that does not exist; **[live]** zero `transcript.jsonl` under `~/.codex`, real path is `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<thread_id>.jsonl` | **Cheapest high-value fix in the audit.** Glob on thread id; filter by `session_meta.cwd` |
| Conversation rail listing | Yes (transcript-derived) | **Partial** | `_non_claude_conversation_rows` (`agent_routes.py:8156`) reconstructs rows from agent-log entries. Its own docstring: "there is no fuller transcript to fall back to" — for Codex that is now false | Add a `list_sessions()` equivalent reading rollouts |
| Conversation label / turn count | Yes | **Partial** | Same function — one agent-log row per process exit, `summary` capped at 2000 chars (`:4283` entry dict) | — |
| In-chat search / full buffer | Yes | **No** | `/transcript/<csid>/full-buffer` (`agent_routes.py:7788`) reads the on-disk transcript; Codex has no `claude_session_id` and no discoverable transcript | Follows from the transcript fix |
| Delete a conversation | Yes | **No** | `delete_conversation` `agent_routes.py:7822` renames the transcript file | Follows from the transcript fix |
| **Resume** | Yes | **No** | `CodexRuntime.build_command(resume_id=…)` correctly builds `exec resume` (`:3477-3486`) but **`_dispatch_via_runtime` hardcodes `resume_id=''`** (`agent_routes.py:4948`) and has no `resume_id` parameter at all. `_non_claude_conversation_rows` returns `'resumable': False, 'resume_mode': 'readonly'` (`:8226-8228`) | **[live]** `codex exec resume [SESSION_ID] [PROMPT]` and `--last` both exist; needs a `provider_session_id` persisted to the agent log, then a resume param on the runtime dispatch path |
| Multi-turn continuity within one chat | Yes (one process) | **Partial** | `write_followup` `:3865` kills the process and spawns a fresh Codex thread, rebuilding context via `_compose_respawn_prompt` (`:3110`) — a 30-line / 4000-char tail of prior output. One MC conversation therefore produces N unrelated Codex threads | Use `exec resume <thread_id>` instead — the CLI supports it |
| Persona / character survives a turn | Yes | **No — known bug class, live on this path** | Persona reaches Codex at dispatch (`agent_routes.py:4910`, `:4920-4923`). But every follow-up **re-stashes** the context with the bare form the `_fresh_context_for` docstring exists to forbid — `agent_routes.py:6267-6270` and `:6877-6880`: `existing['_system_prompt'] = _build_agent_context(p, incognito=False, task=message)`, with no `character_body`, `character_name`, `character_skills` or `session_id`. `_compose_respawn_prompt` then faithfully re-injects the persona-less stash. This is exactly `discovery_bare_context_rebuild_drops_persona` ("a chat started with one persona resumes as another"), and because Codex respawns every turn it bites from turn 2 onward | Use `_fresh_context_for(p, session, task)` at both sites |
| Documents tab | Yes | **No** | `get_project_documents` (`agent_routes.py:8573`) hardcodes `get_runtime('claude').list_written_markdown(...)` at `:8633`; `list_written_markdown` is `ClaudeRuntime`-only (`:1426`) | Reachable — Codex rollouts carry `custom_tool_call` records with file paths |
| Subagents / Floor `active_subagents` | Yes | **No** | `_active_subagents_for_session` (`agent_routes.py:7277`) early-returns `[]` without `claude_session_id` (`:7288`) and then hardcodes `get_runtime('claude')` (`:7292`) | Codex has no Task-tool-equivalent nested transcript; low priority |

### Memory, plans, questions, safety

| Feature | Claude | Codex | Evidence | To close |
| --- | --- | --- | --- | --- |
| Read floor / memory at dispatch | Yes | **Yes** | `_dispatch_via_runtime` calls `_build_agent_context(p, …)` at `agent_routes.py:4917` and passes the result as `system_prompt` | — |
| **Per-turn memory refresh (MC-944)** | Yes | **No** | All four sites are Claude Mode-B stdin writes: `agent_routes.py:4053` and `:5656` (`seed_delivered`), `:6501` and `:6609` (`refresh_for_turn`). Each writes a `json.dumps({"type":"user",…})` envelope to a live `proc.stdin` and starts `_read_agent_stream_b`. Codex is Mode A (`supports_mode_b=False`, `:3812`) and never touches any of them | Call `refresh_for_turn` in `write_followup` before `_compose_respawn_prompt` — the module is a leaf and provider-agnostic |
| Scribe extraction | Yes (transcript) | **Partial** | `_scribe_extract` (`mc/memory.py:2865`) prefers a transcript, falls back to `session['log_lines']` for any non-Claude provider (tagged `extracted_from_log`). It works — but because `_mode_a_reader` reduced every tool call to `[codex tool: shell]`, the Scribe is summarising a log with no tool results in it, while the full record sits unread in the rollout file | Point `_session_transcript_path` at the runtime rather than hardcoding `get_runtime('claude')` (`mc/memory.py:139`) |
| **Negation interrupt (shipped today)** | Yes | **No** | `_observe_negation_interrupt` (`agent_routes.py:2013`) is called at `:3233` (inside `_read_agent_stream`) and `:3453` (inside `_read_agent_stream_b`) — both Claude-only readers. It needs `tool_name` **and** `tool_input`; `_mode_a_reader` extracts only `blocks[0]['name']` and discards the input (`:3271-3275`) | Depends on the tool-block fix above; then the hook is portable |
| Plan detection / approval | Yes | **No** (mis-declared) | Detection lives at `agent_routes.py:3235-3259` and `:3455-3478`, both Claude readers (`Write`/`Edit` of `.md`, Bash heredoc, `ExitPlanMode` → `waiting_for_plan_approval`). `_mode_a_reader` has none of it. Yet `supports_plan_mode=True` (`:3816`) is surfaced at `agent_routes.py:1244` and gates real UI at `static/js/conversation.js:1217`, `:1354` — a plan button whose backing data can never be written | Either implement plan-file registration in the Mode-A reader, or set the flag `False` |
| Questions (`mc:question`) | Yes | **No — actively broken** | The universal context block tells *every* provider to "emit a fenced ```mc:question``` block and STOP" (`agent_routes.py:2083-2095`), and `_dispatch_via_runtime` splices it into the Codex system prompt via `_build_agent_context` (`:4917`). But `CodexRuntime.dispatch:3856` never calls `with_mc_tool_protocol()` and `_mode_a_reader` never calls `apply_mc_tool_blocks()`. Codex is instructed to use a protocol nothing on its path parses: the fence lands in `log_lines` as literal text and the run dead-ends | **Cheap.** Gemini shows the working pattern: `with_mc_tool_protocol` at `:2662`/`:3003`, `apply_mc_tool_blocks` at `:2914` |
| Incognito | Yes | **Partial / divergent** | For Claude, `_build_agent_context(incognito=True)` deliberately **keeps** full project context and adds an INCOGNITO notice (`agent_routes.py:2405-2410`, `:2505-2515`). On the runtime path, `agent_routes.py:4915-4917` is `if not incognito:` — an incognito Codex session gets **no system prompt at all**: no rules, no memory pointer, no persona, no incognito notice. Incognito means two different things per provider | Call `_build_agent_context(incognito=True)` on the runtime path too |
| Worktree isolation | Yes | **No — and this one is a safety issue, not just a missing feature** | `_maybe_isolate_worktree` (`agent_routes.py:845`) has exactly one caller, `:5536`, which sits *after* the non-Claude early return at `:5382`. `_dispatch_via_runtime` passes `project_path=pp` raw (`:4945`), and merge-back (`:4295`) never fires because nothing sets `_worktree_isolated`. So a Codex agent is never the "2nd concurrent agent" that gets isolated — **and it runs with `--dangerously-bypass-approvals-and-sandbox` (`mc/agent_runtime.py:3496`) in the shared tree, alongside a concurrent Claude agent that believes it is isolated from exactly that** | Move the isolation call above the provider branch |
| Skills | Yes (native discovery) | **Partial** | Clayrune *does* deliver skills to non-Claude providers as a text catalog — `_skills_catalog_block` (`agent_routes.py:2226`) renders each skill's name, description and `SKILL.md:` path, injected at `:2605`. **But the gate is project-level, not dispatch-level**: `:2236` — `if (project.get('provider') or 'claude').lower() == 'claude': return ''`. A Codex chat picked per-conversation inside a Claude-default project gets **no** catalog. Separately, **[live]** Codex has a native skills loader (a real rollout's `session_meta.base_instructions` carries a `<skills_instructions>` block with roots `~/.agents/skills`, `~/.codex/skills/.system`, `<project>/.agents/skills`) that Clayrune never writes to — it installs only to `~/.claude/skills` and `<project>/.claude/skills` (`mc/skills.py:43-45`, `:98`). So `supports_skills=False` (`:3814`) understates the CLI | Gate on the **session's** provider, not the project's; optionally mirror into `<project>/.agents/skills` for native discovery |
| Delegated-agent marker (`source='agent'`, MC-925) | Yes | **No** | `_dispatch_via_runtime` calls `_build_agent_context(...)` at `agent_routes.py:4919` **without** `source=`, unlike the three Claude call sites (`:5485`, `:5503`, `:5513`), and the session dict (`:4887-4911`) has no `'source'` key. The strict-character guard at `:2435-2440` therefore never fires — a Codex subagent can impersonate the project's default agent, the exact failure MC-925 exists to prevent | Pass `source=` through `_dispatch_via_runtime` |
| Provider detected at the right level | — | **No — root cause of two rows above** | `_build_agent_context` decides provider shape from the **project** record: `:2430` — `_is_claude = (project.get('provider') or 'claude').lower() == 'claude'`, same test as the skills gate at `:2236`. A per-conversation Codex pick (composer override, or a character's `engine.provider`, precedence at `:5351-5354`) inside a Claude-default project is built as though it were Claude — no skills catalog, and it receives the Claude-only "Recent activity" block (`:2749`) that the comment at `:2746` warns a non-Claude agent misreads as a task list | Thread the resolved session provider into the context builder |
| MCP | Yes | **Unverified** | `supports_mcp=True` (`:3813`). True of the CLI (`~/.codex/config.toml`), but grep finds no Codex MCP wiring in `mc/agent_runtime.py` — Clayrune's per-project MCP trim and idle-eviction do not reach it | Out of scope here; needs its own check |

### Score

Counting only rows marked **Yes**: **13 of 48** features work fully on Codex today.
10 are **Partial**, 23 are **No**, 1 is **Blocked** upstream, 1 unverified.

The 13 cluster entirely in *spawning and parsing*: binary resolution, install detection,
auth detection, model picker, cross-provider model guard, dispatch, event parsing,
protocol-noise suppression, interrupt/stop, token counters, error explanation, `oneshot`,
and the read floor at dispatch.

Everything that happens **after** the first process exits — history, resume, search,
Documents, plans, questions, per-turn memory, persona continuity, quota — is where Codex
falls off. Two things dominate that list: one wrong path constant (§0) and the fact that
`_dispatch_via_runtime` is a second, thinner dispatch path that has quietly drifted from
the Claude one.

---

## (a) Cheap, and they unblock real use

Ranked by (value ÷ cost).

1. **Fix `transcript_path()`.** `mc/agent_runtime.py:3722`. Glob
   `~/.codex/sessions/**/rollout-*-<thread_id>.jsonl`; the thread id is already in the
   filename. This one constant is the gate on conversation history, in-chat search,
   Documents, conversation delete, and the Scribe's high-fidelity path. Roughly a dozen
   lines.
2. **Persist `provider_session_id`.** It is captured at `mc/agent_runtime.py:3277` and read
   nowhere. Add it to the `entry` dict in `_log_agent_completion`
   (`mc/blueprints/agent_routes.py:4283`) beside `claude_session_id`. Without it, both the
   transcript link and native resume die at process exit. Prerequisite for (3) and (4).
3. **Wire resume.** `build_command` already emits `exec resume` correctly (`:3477`) and
   is unit-tested (`tests/test_provider_runtimes.py:346-361`). What is missing is a
   `resume_id` parameter on `_dispatch_via_runtime` — it hardcodes `''` at
   `agent_routes.py:4948` — and flipping `'resumable'`/`'resume_mode'` at `:8226-8228`
   once a `provider_session_id` exists. **[live]** the CLI's `--all` flag is documented as
   "disables cwd filtering", i.e. Codex already scopes its own session list to the working
   directory — the semantics Clayrune wants.
4. **Use resume for follow-up turns.** `write_followup:3865` currently kills the thread and
   replays a 4000-char tail. Passing `resume_id=session['provider_session_id']` gives real
   conversational continuity and stops one chat fragmenting into N rollout files.
5. **Wire `mc:question`.** Copy Gemini exactly: `with_mc_tool_protocol()` in `dispatch` and
   `write_followup` (`:2662`, `:3003`), `apply_mc_tool_blocks()` at the turn boundary in the
   Mode-A reader (`:2914`). Today Codex is *told* to use a protocol nothing parses —
   strictly worse than not asking.
6. **Fix the persona regression.** `agent_routes.py:6267-6270` and `:6877-6880` re-stash the
   context with a bare `_build_agent_context(p, incognito=False, task=message)`. Swap both
   for `_fresh_context_for(p, session, task)` — the helper that exists for exactly this.
   One-line change each; without it a Codex chat silently changes persona at turn 2.
7. **Fix the quota key-name mismatch.** `agent_routes.py:4491` reads `session['model']`; the
   runtime path only ever sets `agent_model` (`:4908`). Falling back to `agent_model` in that
   one f-string turns MC-934 on for every non-Claude provider at once.
8. **Keep the tool block in `_mode_a_reader`.** `:3271-3275` throws away a correctly-parsed
   command and output. Fixing it improves tool rendering, restores Scribe fidelity, and is
   the precondition for the negation interrupt.
9. **Gate provider-shaped context on the session, not the project.** `agent_routes.py:2236`
   and `:2430` both test `project.get('provider')`. A per-conversation Codex pick in a
   Claude-default project is therefore built as a Claude session — no skills catalog, plus
   a "Recent activity" block the code's own comment (`:2746`) says a non-Claude agent
   misreads as a task list. Also pass `source=` at `:4919` so MC-925's strict-character
   guard applies to delegated Codex agents.
10. **Per-turn memory in `write_followup`.** `mc/memory_turn.refresh_for_turn` is a leaf
   module with no Claude assumptions; call it before `_compose_respawn_prompt`.
11. **Attachments.** One `with_attachment_hint()` call in `dispatch`. Optionally pass
   Codex's `-i/--image`. `image_input=True` is currently a false promise.
12. **Effort knob.** `-c model_reasoning_effort=<level>` in `build_command`.
13. **Mirror skills to `<project>/.agents/skills`.** Codex has a native skills loader;
    Clayrune just writes to a directory Codex does not read.
14. **Correct the false capability flags** — `emits_cost` (`:3822`, live-disproved),
    `supports_plan_mode` (`:3816`, drives dead UI), `supports_skills` (`:3814`,
    understates). A wrong flag is worse than a missing feature because the UI acts on it.
15. **Cache `auth_status`.** Stop spawning `codex --version` on every provider-list load.
16. **Incognito parity.** `agent_routes.py:4915` should call
    `_build_agent_context(incognito=True)` rather than skipping context entirely.

## (b) Genuinely hard, or upstream-blocked

1. **Activity states — blocked by the Codex wire protocol.** This is the one place Codex
   simply cannot do what Claude does. Claude's thinking-vs-writing signal needs
   `--include-partial-messages` and per-token `stream_event` deltas
   (`mc/agent_runtime.py:1023`, consumer `agent_routes.py:3044`/`:3185`). **[live]**
   `codex exec --json` emits only completed items — `thread.started`, `turn.started`,
   `item.started`, `item.completed`, `turn.completed`. There is no delta event to subscribe
   to. The most Codex can honestly offer is a coarse state derived from `item.started`
   (`reasoning` → thinking, `command_execution` → working). Do not promise more.
2. **Live rate-limit window.** Claude emits a `rate_limit_event` with window state
   (`system_routes.py:404`). Codex emits token counts only. `emits_rate_limit=False` is
   already honest; leave it.
3. **Subagents.** Claude's Floor visibility is built on nested per-subagent transcript
   files (`iter_transcript_files_in_dir:779`, `list_running_subagents:1512`). Codex's
   rollout is a single flat stream with no subagent fan-out to observe. Structurally absent,
   not merely unimplemented.
4. **Plan mode.** Claude's detection is anchored on the `ExitPlanMode` tool. Codex has no
   equivalent, so any Codex plan flow would be a prompt convention plus a filesystem watch
   on the plans dir — a different mechanism, not a port. Meanwhile `supports_plan_mode=True`
   should be corrected today regardless.
5. **The `ClaudeRuntime`-only method surface.** `list_sessions`, `list_written_markdown`,
   `list_running_subagents`, `parse_transcript_file`, `memory_path` are not on the ABC, and
   their callers hardcode `get_runtime('claude')` in seven places (`agent_routes.py:7292`,
   `:8633`; `mc/memory.py:139`, `:216`, `:231`, `:242`, `:258`). Promoting them to the ABC
   with `None`/`[]` defaults is the refactor that makes *any* second provider first-class.
   This is the real cost of "first-class Codex" — the per-feature fixes in (a) are cheap
   precisely because this one is not.
6. **Worktree isolation.** Not conceptually hard, but the call sits below the non-Claude
   early return inside `_dispatch_agent_internal`, so moving it means restructuring the
   shared pre-dispatch path — a refactor with blast radius on the Claude path, which is the
   one that must not regress. Worth flagging up front anyway (see the table row): until it
   moves, concurrent-agent isolation is a guarantee Clayrune only actually keeps when every
   agent in the project is Claude.

7. **`_dispatch_via_runtime` as a parallel dispatch path.** The deepest structural problem
   is not any single feature. `_dispatch_agent_internal` returns early for non-Claude
   providers (`agent_routes.py:5382`), and everything below that line — worktrees, effort,
   the auto-router, resume, transcript preload, `source=`, the model stamp MC-934 needs —
   is simply unreachable. Each gap in (a) is cheap on its own precisely because this one is
   not; but fixing them one at a time re-creates the same drift the next time a feature
   lands on the Claude path only. The durable fix is to make the two paths converge before
   the provider branch, not to keep porting features across it.

## (c) Look like gaps, but are fine as-is

1. **`supports_mode_b=False`.** Correct. Codex has no persistent-stdin mode; Mode A is the
   honest answer, and `_compose_respawn_prompt` is a reasonable mitigation.
2. **`emits_num_turns=False`, `context_window=None`.** Accurate. (`context_window` is in
   the rollout's `session_meta` if ever wanted, but nothing needs it.)
3. **`supports_ask_user_question=False`.** Correct about the *native* tool. The gap is the
   missing `mc:question` fallback, listed in (a), not this flag.
4. **The MC-934 quota *design*.** The mechanism is genuinely provider-agnostic — one
   `[runtime-error]` line on disk, parsed by `_recent_quota_failures` (`agent_routes.py:5178`)
   and `_model_quota_blocked` (`:5227`), the latter called before the provider branch. No
   redesign is needed; only the `model` vs `agent_model` key name is wrong, which is why the
   fix sits in (a) rather than here.
5. **`context_injection='file'` / `AGENTS.md`.** Correct — **[live]** confirmed, a real
   rollout shows `AGENTS.md` content injected as a developer message. Clayrune's
   prepend-to-prompt approach also works and is a fine belt-and-braces.
6. **Incognito still leaving a rollout on disk.** Not a Codex-specific defect — Claude has
   the identical property, and the incognito notice already says so verbatim
   (`agent_routes.py:2513-2515`: "hides this session from Clayrune surfaces, not from
   disk"). Worth noting: **[live]** `codex exec` has an `--ephemeral` flag ("run without
   persisting session files to disk") which would let Codex be *stronger* than Claude here.
   Optional upgrade, not a gap.
7. **`npx` fallback.** Well-designed, and correctly stores an absolute `npx` path to dodge
   the Windows `CreateProcess`-can't-launch-a-`.cmd` trap (`:3450-3460`). The one bug it
   causes is in the auth routes, noted in (a).
8. **`parse_event` dual-schema handling.** The 0.133/0.151 divergence and the
   unknown-item-type fallback (`:3684-3697`) are handled better here than in most of the
   runtimes. Leave it alone.

---

## 3. Test coverage — green, and not parity evidence

`tests/test_provider_runtimes.py` is 1446 lines; `TestCodexRuntime` runs `:324-635`.

**What it tests well:** argv construction (`:330-369`), `parse_event` across both schemas
including the unknown-type degradation (`:371-490`), binary resolution including the
native-installer path (`:527-543`), auth-state detection from `auth.json` (`:547-569`), and
model-catalog hygiene (`:1121-1155`).

**What it does not test — and the specific holes that let real defects through:**

1. **`test_transcript_path_missing_session` (`:1185-1186`) asserts only the empty-id case
   returns `None`.** There is no positive test, so the fact that `transcript_path()` is
   pointed at a directory layout that has never existed is invisible to a green suite. This
   single missing assertion hides the audit's biggest finding.
2. **`test_live_probe_events` (`:1188-1231`) reads exactly one line and kills the process.**
   It proves `thread.started` is emitted; it proves nothing about `item.completed` shapes,
   turn completion, session-file creation, or resume.
3. **No test exercises `_mode_a_reader` end-to-end for Codex.** The only Mode-A reader test
   is `TestModeAReaderProtocolNoise` (`:1185`), which tests the `_is_protocol_json` helper in
   isolation. The reader's discarding of tool blocks — the root of three separate gaps
   above — has no coverage.
4. **`test_capabilities` (`:494-500`) asserts the flags match themselves.** It asserts
   `emits_cost is True` and `supports_plan_mode is True`. Both are false in reality. The
   test pins the mis-declaration in place.
5. **Nothing tests the dispatch *path*** — that `_dispatch_via_runtime` hardcodes
   `resume_id=''`, that incognito yields an empty system prompt, that no memory refresh
   occurs on follow-up. Those live in `agent_routes.py` and no Codex test reaches them.

**Assessment:** the suite tests that Clayrune builds the right command line and parses the
right JSON. It does not test that a Codex session is usable. Both are worth having; only
the second is parity evidence.

---

## 4. What to do first

Three of the findings are **correctness bugs on shipped behaviour**, not missing features,
and they should be separated from the parity work because they are cheap and they are
wrong today:

- the persona regression at `agent_routes.py:6267-6270` / `:6877-6880` (a Codex chat
  silently changes persona from turn 2),
- the MC-934 `model` / `agent_model` key mismatch at `:4491` (quota protection is off for
  every runtime provider, not just Codex),
- worktree isolation never applying to a Codex agent that runs sandbox-bypassed in a tree
  a concurrent Claude agent thinks is protected.

Then, if only one parity thing gets done: **fix `transcript_path()` and persist
`provider_session_id`.** Together they are perhaps forty lines, and they convert Codex from
"a provider with no history" into "a provider whose history Clayrune can read" — the
precondition for resume, search, Documents, and high-fidelity memory. Everything else in
section (a) is independently cheap but individually smaller.

The honest ceiling: with (a) done, Codex reaches parity on everything except activity-state
granularity, live rate-limit windows, subagent visibility, and plan mode — four features
that are absent from the Codex CLI itself, not from Clayrune's use of it. That is a good
outcome and it should be stated as the target rather than discovered as a disappointment.
