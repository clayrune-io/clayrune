# Codex live-run readiness (docs/VENDOR_AGNOSTIC_PROGRAM.md section 8b)

Written 2026-09-19 by Tobin, offline, on branch `clayrune/agent/2ca149dc0bd2` (base `live/vendor-agnostic` @ 6604b08, driver commit `8fa73f2`). No vendor was prompted.

**Verdict: NOT READY to ask for allowance.** Item 5 needs Ron's clean-VM run; item 7 is far from done; items 1 to 4 are merged but I did not re-run the full suite or the smokes.

| # | Gate | Status | Evidence | What is not shown |
|---|---|---|---|---|
| 1 | W0 merged: green, full suite + smokes, Codex 0.154 rollout importer proven on a fixture | MERGED, importer proven; suite/smokes NOT RE-RUN by me | W0 merge on branch: `3e6c9ec` (accept codex-cli 0.154 rollout), `0566d8e` (W0 changelog). Fixtures: `tests/test_codex_conversation_rail.py`, `tests/test_allowance_state.py` | Full pytest + all smokes from a clean worktree: not run this session. Today: 273 passed, 1 skipped across allowance / guardrail-injection / runtimes / architecture-guard / codex-rail |
| 2 | W1 merged: Codex `transform` tool-free (`-s read-only` + empty cwd), architecture guard green | MERGED; guard green | `62cb9dd` (W1 merge), `e04f963`, comment at `mc/agent_runtime.py:7252`; `tests/test_provider_architecture_guard.py` passes (50 with the new driver tests) | Proven by command construction only; never executed against Codex |
| 3 | W2 merged: Codex hook config installed, exact diff reviewed; live block test is first Codex run item | MERGED; live test SCRIPTED, NOT RUN | `3dc5dbd` (W2 merge), `a203690` (per-launch injection, not global config), `b8c0b9f`, `docs/GUARDRAIL_PARITY_EVIDENCE.md`. Live test = cell 1 of `tools/provider-live/codex_run.py` (`8fa73f2`) | I did not verify that the exact diff was reviewed. Codex hooks are marked PENDING in the plan (argv injection wired, never confirmed live) |
| 4 | W3 merged: `usage_limit_exceeded` parsed from the captured real error; Floor shows "out until"; no quota text becomes content | MERGED; parsing fixture-proven; Floor display NOT CONFIRMED by me | `670c7ed` (W3 merge), `4bba3ee`, `b8c280a`, `34a1d11` (live `exec --json` stream shape); `tests/test_allowance_state.py` | The gate wants Clayrune showing the real exhausted state for Codex today. I did not read Ron's instance (`/api/agent/providers`); needs one look. Plan row `allowance` for Codex is still UNVERIFIED |
| 5 | W6 merged: Codex install + auth detection pass on the clean VM | **NOT MET** | W6 code merged (`42de096`, later F1/F2/F7-F12 fixes) but plan rows `multi-vendor-first-run` and `first-run` are UNVERIFIED for every vendor: "clean VM not reached" | Ron's clean-VM run. Runbook is named by the driver as `docs/_journal/provider-live/w6-vm-runbook.md`; I did not find it in this worktree |
| 6 | Every Codex cell has an exact scripted step, runnable in order by one driver, with estimated token cost | **MET (offline)**; never run against a live Codex | `tools/provider-live/codex_run.py`, `live_gates.py`, `fixture_mcp_server.py`, `tests/test_provider_live_driver.py` (37 tests), commit `8fa73f2`. Dry-run: 15 live cells (2 manual clean-VM and 3 cross-vendor cells excluded), 24 model calls, ~1,572,000 gross context tokens in, ~5,100 out (cross-vendor cells excluded; `--cross-vendor` adds 12 calls) | Live paths (HTTP against a real disposable instance, real transcript ingestion, Codex rollout `token_count` shape) are unexercised: no CLI may run. Expect first-run fixes on paid time unless the non-Codex runs (item 7) flush them out. Estimates are gross context processed, not billed tokens |
| 7 | Same cells already green on Claude, Gemini, Qwen | **NOT MET** | Plan matrix in `docs/PROVIDER_LIVE_TEST_PLAN.md` | Below |

## Item 7: non-Codex cells not green today

From the plan matrix at 6604b08. "Green" = PASS / VERIFIED / CORRECTED-to-PASS. None of them carries the new token-efficiency or alignment blocks yet; that scope was added after they were recorded, so **every** existing PASS is missing those two blocks.

- **Claude** (6 green, 12 not): first-run, new-chat, follow-up, image-paste, workflow, schedule, memory, hire ("not re-tested"), stop-interrupt, mcp, usage, allowance. `mixed-workflow` BLOCKED by design.
- **Gemini** (8 green, 10 not): first-run, new-chat, follow-up, workflow, schedule, memory, stop-interrupt, questions ("not re-tested"), mcp ("not re-tested"), allowance. `mixed-workflow` BLOCKED by design.
- **Qwen** (8 green, 10 not): first-run, new-chat, follow-up, image-paste (PARTIAL), workflow, schedule, memory, stop-interrupt, allowance, handoff (default refusal only).
- **All vendors:** `multi-vendor-first-run` UNVERIFIED (clean VM).
- `mixed-workflow` is BLOCKED for Claude/Gemini/Qwen because workflow CRUD refuses agent callers; the driver reports it UNVERIFIABLE, not PASS.

Path to green: run `codex_run.py --vendor claude|gemini|qwen --model <m>` once each. That spends those vendors' allowance (Ron's call) and fills `docs/_journal/provider-live/<vendor>/<cell>.md`.

## Driver behaviour Ron should know

- `allowance` cannot be induced; it is INCONCLUSIVE unless a limit is genuinely hit during the run.
- `memory`: the Scribe write leg uses a Claude summarizer, so it is UNVERIFIABLE on a non-Claude pass unless `--allow-scribe-claude` is given (spends Claude).
- First `usage_limit_exceeded` stops the run, writes `STOPPED-usage-limit.md`, marks the rest NOT-RUN. Exit code 3.
- A preflight refuses to spend anything if the vendor is already exhausted or signed out.
