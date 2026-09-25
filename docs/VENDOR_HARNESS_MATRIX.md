# Vendor harness matrix (2026-09-25)

This is a per-vendor drill against the standing position that Clayrune is
fully vendor-agnostic, so every per-vendor capability gap counts as a
Clayrune bug to bridge. The findings come from real dispatched sessions and
their transcripts, not from reading docs. Code reading was used only to name
the mechanism behind a measured result.

**Live-server caveat, read first.** The Clayrune server that ran these
probes started at 09:02 PDT. Two relevant fixes merged to `master` after
that, so the probes ran without them:
`ca55d30` (09:16, Codex rollover reads context rather than summed usage) and
`d76a340` (09:57, auto-sync of AGENTS.md/GEMINI.md/QWEN.md from CLAUDE.md).
Where a gap below is already fixed on `master`, it says **fixed on master,
not live-verified**. Verifying those fixes needs a server restart, which
needs Ron's go-ahead.

## Method

Four identical probe sessions were dispatched at 16:53Z, one per vendor,
through `POST /agent/dispatch` (so they took the real dispatch path), with
`notify_session` set. The probe prompt is in the journal dir as `body_claude.json`:

- **A.** Five marker phrases, each checked PRESENT/ABSENT with a count:
  - A1: the CLAUDE.md `master` release-channel BINDING
  - A2: SHARED_RULES "SUBSTITUTION IS A LIE"
  - A3: AGENT_RULES email rule
  - A4: MEMORY.md "Pointer index"
  - A5: CLAUDE.md "Exception-swallowing policy"
- **B.** Native skills vs the text catalog.
- **C.** MCP servers.
- **D.** Exactly one `filesystem` MCP call.
- **E.** A per-vendor nonce, recalled on turn 2.
- **F.** An `mc:question` checkpoint.

Turn 2 was a follow-up of about 5 minutes to measure resume and cache
(G = recall the nonce, H = the question answer). Model self-reports were
cross-checked byte-for-byte against the actual transcripts wherever a
transcript exists. When a self-report and the bytes disagree, the bytes win,
and the disagreement is noted.

| Runtime | Session | Model run | Native transcript |
|---|---|---|---|
| Claude | `860c24691836` | opus (Opus 5.5) | `~/.claude/projects/…-agents-860c24691836/2b88cf25-….jsonl` |
| Codex | `527f3d4458b9` | gpt-6-astra | `~/.codex/sessions/…/rollout-…-01a0d97c-a525-….jsonl` |
| Gemini | `5827f3806376` | gemini-3.5-flash-lite | `~/.gemini/tmp/mission-control/chats/session-2026-09-25T17-00-ab2ebef5.jsonl` |
| Qwen | `e93d2c095f38` | CLI default (agent_model blank) | `~/.qwen/projects/…mission-control/chats/e6b6bdb6-….jsonl` |

The first Gemini probe (`2e13a7d9c97b`, gemini-pro-latest) failed on quota
("Gemini rate limit / quota exceeded"), and the allowance recheck returned
`unavailable`. The re-probe on Flash-Lite is the Gemini column. It is a
different model, but the harness is the same, and that harness is what this
drill measures.

Copies of every artifact are in the gitignored journal dir
`docs/_journal/vendor-harness-2026-09-25/`, with the narrative in
`docs/_journal/vendor-harness-matrix-2026-09-25.md`.

## Matrix

Each cell is PASS, GAP, or UNTESTED, with the measured number behind the verdict.

| # | Item | Claude | Codex | Gemini | Qwen |
|---|---|---|---|---|---|
| 1 | Project BINDING rules seen exactly once | **PASS**. CLAUDE.md is loaded natively. All 5 phrases appear once each, and 0 are in the injected sysprompt file (44,321 B) | **GAP**. AGENTS.md is loaded natively (turn 1: 22,332 B). **It is re-injected on resume** (turn 2: `# AGENTS.md instructions…` 21,288 B again), so the rules appear twice from turn 2 on | **GAP**. Only GEMINI.md is loaded natively (`DEFAULT_CONTEXT_FILENAME`), and it does not exist. A1 and A5 are ABSENT; the only copies are the probe's own text. Fixed on master (`d76a340`), not live-verified | **PASS**. Qwen loads `["QWEN.md","AGENTS.md"]` by default (bundle `chunk-PYYPSK6Q.js:31-35`), so it sees the generated AGENTS.md once. Clayrune does not inject CLAUDE.md for any non-Claude vendor |
| 2 | Same-thread resume, turn 2 cache | **PASS**. Nonce recalled. 96.7 % cached (69,568 / 71,918) after a 5 m 37 s gap | **PASS**. Same thread `01a0d97c`, nonce recalled. 87.0 % cached (47,360 / 54,463) after 4 m 50 s. The uncached 7.1k is mostly the duplicate AGENTS.md from row 1 | **PASS**. Same session `ab2ebef5`, nonce recalled. 75.4 % cached (49,264 / 65,379) after 52 s | **GAP**. Same session `e6b6bdb6`, nonce recalled, but **0 % cached** (0 / 58,132) after a 5 m 58 s gap. The best hit even at a 5 s gap is 48.8 % (28,283 / 57,911), and turn 3 after 4 m 21 s is 48.7 % |
| 3 | Compaction vs `context_rollover_tokens=200000` | **PASS**. Native auto-compact fires at 967k–1,005k preTokens (8 events). All of them predate 2026-09-18, when the token rollover shipped (`2f93451`), and there are 0 since. Clayrune fires first, including mid-turn (`midturn_rollover`) | **GAP**. Native compaction fires at **204,646–239,256** input tokens (45 events in `~/.codex/sessions`, all before 2026-09-18), which leaves a 5–40k margin over Clayrune's 200k. The Clayrune roll for non-Claude runs only *between* turns (`_mode_a_token_rollover`; `_maybe_midturn_roll` is called only by the Claude readers), so one long Codex turn compacts natively and skips the Scribe flush and handoff | **PASS (by threshold)**. Native compression is at 0.5 × window (`DEFAULT_COMPRESSION_THRESHOLD`). Clayrune records no window for Gemini (`context_window: null`). Clayrune fires first for any window above 400k | **UNVERIFIED**. Native compaction is at 0.85 × window − 13k (`DEFAULT_PCT`, `AUTOCOMPACT_BUFFER`). The model and window are unreported (`agent_model ''`, `context_window null`). Clayrune fires first only if the window is ≥ 251k, and there is no measurement either way |
| 4 | Every skill visible exactly once | **PASS**. 91 through the native Skill tool, 0 in a text catalog, 0 duplicates. All four canaries are visible | **GAP**. 51 native (`<skills_instructions>`) + 70 in the Clayrune `AVAILABLE SKILLS` catalog, with **34 listed twice**. The native set includes a corrupted copy, `Codex-cli-resume-system-prompt-isolation` (see gap 4) | **GAP**. The native `activate_skill` enum has 36 entries (self-report) and the Clayrune catalog 70 (measured). Per the model, all 36 native entries also appear in the catalog, including the corrupted `Codex-cli-…` skill | **PASS**. 0 native (`~/.qwen/skills` is empty) and 70 in the catalog, measured. The model self-reported 55, which was a miscount |
| 5a | MCP matches the project's `enabled_mcp_servers` (filesystem, engram, higgsfield) | **PASS**. engram (15) + filesystem (14). higgsfield is not configured on any vendor | **GAP (partial evidence)**. The filesystem call succeeded. The rest of the set comes from user `config.toml` (node_repl, mail, sequential-thinking, tradingview), with no project filter and no engram. The rollout defers tool inventory, so only `filesystem` was proven live | **GAP**. It reported tradingview, mail, filesystem, and sequential-thinking: the unfiltered `~/.gemini/settings.json`, **no engram**, and the raw `mail` MCP, which AGENT_RULES forbids reading directly | **PASS**. engram (14) + filesystem (16), self-report |
| 5b | Filesystem MCP scope = the agent's cwd | **PASS**. Scope is the agent's own worktree | **GAP**. Scope is the main checkout, because the agent was never isolated (gap 3) | **GAP**. Scope is the main checkout + `data/uploads` | **GAP**. Scope is the main checkout |
| 5c | ask-user (`mc:question`) | **PASS**. Card rendered, and "Continue" was received on turn 2 | **PASS**. Fenced block, answer received | **PASS** | **PASS** |
| 5d | Per-turn usage correct | **PASS**. Cumulative cache_read 285,237 and the last-iteration figures are both consistent | **GAP**. `context_tokens` shows **195,651**, the sum of 4 requests, while the real context is 54,463. That is 3.6× over, and one more turn trips a false 200k rollover. Fixed on master (`ca55d30`, `abe0338`), not live-verified | **GAP**. Session usage is **last request only**: 65,379 shown against 192,489 billed over 3 requests, so the cumulative total undercounts by 66 % | **PASS**. input 231,825 = the sum of 4 requests, and cache_read 56,643 = 28,283 + 28,360 |
| 6 | Injected context bytes on turn 1 vs task bytes | 44,321 B sysprompt file + CLAUDE.md 20,108 B + MEMORY.md 19,445 B (native) against a 1,926 B task, **≈ 43 : 1**. Turn-1 prompt ≈ 65k tok | 82,322 B Clayrune prefix + AGENTS.md 22,332 B + native skills 18,112 B + multi-agent 2,700 B against 1,926 B, **≈ 65 : 1**. Turn 1 is 46,288 tok | 82,322 B Clayrune prefix, sent as the **user message**, against 1,926 B (≈ 43 : 1). Turn 1 is 63,510 tok | 82,325 B Clayrune prefix + AGENTS.md (native) against 1,926 B, **≈ 54 : 1**. Turn 1 is 57,593 tok |

**UNTESTED: OpenCode (`:8771`), Goose (`:9125`), Aider (`:9493`), and Kiro
(`:9801`).** None of their binaries (`opencode`, `goose`, `aider`,
`kiro`/`kiro-cli`) is on PATH on this box, so they are not installed. No
other vendor's result has been substituted for them.

## Ranked gaps and the fix each needs

1. **Non-Claude agents are never worktree-isolated.** Codex, Gemini, and Qwen
   all ran in the shared main checkout while other agents were live. Claude,
   dispatched the same way at the same moment, got its own worktree.
   `_maybe_isolate_worktree` has one call site (`agent_routes.py:9762`),
   which sits after `if provider_name != 'claude': return
   _dispatch_via_runtime(…)` (`:9550`), and `_dispatch_via_runtime` (`:8295`)
   always uses `project_path`. Concurrent non-Claude agents therefore write
   into Ron's working tree and each other's, and commit on whatever branch is
   checked out.
   *Fix:* call `_maybe_isolate_worktree` in `_dispatch_via_runtime` and pass
   the returned cwd to the runtime, the MCP filesystem root, and
   `vendor_context_sync`. Wire `_worktree_merge_back_on_end` for Mode-A exit.
2. **Codex native compaction can beat Clayrune's rollover inside a turn.**
   Codex compacts at 204–239k, against Clayrune's 200k, and only Claude has a
   mid-turn roll. A native compaction skips the forced Scribe checkpoint and
   the handoff.
   *Fix:* either set Codex's `model_auto_compact_token_limit` (via `-c`) above
   Clayrune's threshold plus a margin, or lower the non-Claude rollover to
   about 0.8 × the vendor's native threshold. Also teach the Codex reader to
   detect a `compacted` event and run the Scribe flush when it does.
3. **Gemini loads the unfiltered user MCP set.** That set includes the raw
   `mail` MCP and tradingview, and has no engram.
   *Fix:* build a per-dispatch Gemini settings file
   (`GEMINI_CLI_SYSTEM_SETTINGS_PATH`, the hook-path precedent) with exactly
   `enabled_mcp_servers`, the way Qwen uses `--allowed-mcp-server-names`. Do
   the same for Codex through `-c mcp_servers.<x>.enabled=false`, plus an
   engram entry.
4. **The skill catalog is duplicated on Codex (34 twice) and Gemini (about
   36 twice), and there is a corrupted skill copy.** `~/.agents/skills/`
   holds a 28-skill snapshot dated 2026-07-13, and inside it
   `claude-cli-resume-system-prompt-isolation/SKILL.md` has been rewritten to
   `name: Codex-cli-…` with "`Codex -r`" in its body. That is a Claude-CLI
   skill with its subject substituted. Codex and Gemini both read that
   directory natively. No Clayrune code writes it (`grep` over `mc/` finds no
   match). The 19 skills added to `~/.claude/skills` since then are missing
   from it.
   *Fix:* drop from the Clayrune `AVAILABLE SKILLS` catalog any skill the
   vendor already lists natively, the way Claude gets 0. Treat
   `~/.agents/skills` as either Clayrune-owned (regenerated from source with
   no name rewriting) or ignored. Delete the corrupted copy only after Ron
   confirms, because it is outside the repo.
5. **Codex re-injects AGENTS.md on every resume.** That is 21,288 B, about
   5.3k tokens, uncached per follow-up, and the BINDING rules appear twice
   from turn 2 on. This is native Codex behaviour on `exec resume`.
   *Fix:* measure whether moving the project rules into Clayrune's (cached)
   turn-1 prefix and suppressing AGENTS.md for dispatched Codex sessions
   (`project_doc_max_bytes=0`) beats the present duplicate. Leave AGENTS.md
   in place for Ron's direct CLI use.
6. **Qwen's prompt cache tops out at about 49 % and drops to 0 % after
   about 6 minutes.** Every Qwen follow-up after a pause re-pays the whole
   58k-token prompt.
   *Fix:* investigate the cacheable-prefix layout. Clayrune's 82 KB prefix
   sits in the first user message after the CLI's own system prompt, and the
   cache boundary sits at about half of the prompt. Record the provider's
   cache TTL in the vendor capability table so the UI can warn.
7. **Gemini usage is last-request only (66 % undercount).**
   *Fix:* accumulate per-request `usageMetadata` the same way the Qwen reader does.
8. **Gemini has no project rules today.** Fixed on master (`d76a340`, which
   writes GEMINI.md). Needs a restart and one Gemini dispatch to verify.
9. **The Codex context meter shows the summed figure (195,651 vs 54,463).**
   Fixed on master (`ca55d30`/`abe0338`). Needs a restart to verify.
10. **The Qwen and Gemini context windows are unknown to Clayrune**
    (`context_window: null`, and the Qwen model is blank). This blocks the
    compaction check in row 3 and the UI meter.
    *Fix:* resolve the model and window per vendor at dispatch.
11. **Turn-1 injection is 43–65× the task.** It is flat per dispatch, and
    the Codex token journal (`docs/_journal/codex-token-efficiency-2026-09-25.md`)
    already covers the AVAILABLE SKILLS (41.7 KB) share. Gap 4's de-dup is the
    largest single cut available to Codex and Gemini.
