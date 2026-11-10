# API credits offload: build spec, phase 1

Dave, 2026-10-30. Design and research: `docs/API_CREDITS_OFFLOAD.md` (read it first).

## Goal

Let a Max/Ultra/Team subscriber point Clayrune's background toolless calls
(`ClaudeRuntime.oneshot()`: Scribe, condense, Distiller, mail_launder,
browser_digest, router) at the monthly API credits instead of the plan, with a
user-set budget and stop point, and a way to measure the cost first.

## Decisions (Dave, logged DECIDED WITHOUT RON)

1. **Transport: direct Messages API over `requests`**, not `claude -p` with an
   injected key. The Messages API is explicitly covered by the credits; `-p`
   with a key is the open question in the research note. It also returns exact
   `usage`, and has no tools by construction. No new dependency (`requests` is
   already in requirements.txt; do NOT add the `anthropic` SDK).
   Reverse: swap the transport function; the budget module does not change.
2. **The key is a vault entry.** The settings card lets the human pick an
   existing vault secret, or paste a key that the existing secret form saves
   into the vault (human UI path, same as Settings > Secrets). Clayrune stores
   only the secret NAME. The key never goes into `provider_env.json`, never
   into `os.environ`, never into any Claude child env, so the
   `provider_env_scope` guard is untouched. Read with
   `secrets_store.get_secret_value(name, consumer='api-credits-offload', unattended=True)`.
3. **Default OFF.** Three modes: `off`, `measure` (subscription path stays,
   Clayrune records an ESTIMATED cost per oneshot so the user sees what a
   cycle would cost before moving anything; estimate = chars/4 tokens, labelled
   "estimate" everywhere), `on` (offload).
4. **Any failure falls back to the subscription path for that same call** (no
   key, vault locked, HTTP error, timeout, "credit balance too low", budget at
   stop point). A caller never sees a failure the old path would not have had.
   "Credit balance too low", the org's Console spend limit ("You have reached
   your specified API usage limits", a 400) or a 401 also flips state to
   `stopped` with a reason, and fires ONE notice per cycle (existing notification path, no
   email).
5. Phase 1 covers oneshots only. Per-schedule "bill to API credits" is
   phase 2. Hiding the "Reset 5-hour session" button is out of scope.

## Units (one unit, one file)

- `mc/api_credits_plan.py`: plan detection. Reads ONLY `subscriptionType` and
  `rateLimitTier` from `~/.claude/.credentials.json` > `claudeAiOauth` (never
  touch or log tokens). Map: `max_20x` or `ultra` -> Ultra $200; `max_5x` or
  `max` (new name) -> Max $100; `team` -> Team (pooled, up to $500, amount
  user-entered); else not eligible. Match old and new labels. Missing file ->
  `unknown`, not an error.
- `mc/api_credits_budget.py`: settings + ledger + gate.
  Settings `data/api_credits.json` (OUTSIDE `data/projects/`): mode, secret
  name, plan credit (editable, prefilled), budget $, stop %, cycle reset day
  (1-31), default model. Ledger `data/api_credits_ledger.json`: per cycle,
  entries {ts, caller, model, input/output/cache tokens, cost_usd, estimated
  bool}; keep current + previous cycle only. `can_spend()`, `record()`,
  `status()`. Cycle boundary from reset day (clamp to month length). Price
  table per model per MTok input/output/cache-read/cache-write, taken from
  the "Price table" appendix below (do not use remembered numbers), in one
  constant with a dated source comment. Unknown model -> refuse to offload
  (fallback), do not guess a price.
- `mc/api_credits_transport.py`: one POST to `https://api.anthropic.com/v1/messages`
  (`x-api-key`, `anthropic-version`), system + one user message, returns
  text + usage or a typed error. Timeout from the caller. Scrub the key from
  any error string before logging.
- Hook in `ClaudeRuntime.oneshot()` (`mc/agent_runtime.py` ~3153): a few lines
  that ask the budget module first; on `on` + `can_spend()` try the transport,
  record usage, return `OneshotResult`; else run the existing path unchanged;
  in `measure`, record an estimate after the existing path. `describe_image`
  and stream paths untouched. Do not grow agent_runtime.py beyond the hook.
- `mc/blueprints/api_credits_routes.py`: `GET /api/api-credits/status`
  (plan detection, settings, cycle spend/budget/stop, state, last error, no
  secret values), `PUT /api/api-credits/settings`, `POST /api/api-credits/test`
  (one human-clicked call, haiku, max_tokens 1; reports ok / credit too low /
  invalid key). Register via the existing blueprint pattern.
- `static/js/api-credits-settings.js`: a section in Settings > Agent Providers
  under the Claude row (follow provider-settings.js/provider-auth.js shape;
  numbered inputs top-down per the modal convention, only the final Save uses
  `btn-add`). Shows: 1. detected plan + credit (editable), 2. API key (pick
  vault secret / paste into vault via the existing secret form), 3. budget,
  4. stop at %, 5. reset day, 6. mode (Off / Measure first / On), Test key,
  Save. Fixed help line: claim credits at claude.ai Settings > Billing > API
  credits > Link organization; set a workspace spend limit in Console as the
  backstop, since only that one is enforced by Anthropic.
- `static/js/api-credits-usage.js`: a section in the usage popup
  (`system-status.js`, import, don't inline): mode, spent / budget this cycle
  (bar with stop marker), resets on <date>, state + reason, and in measure
  mode "estimated, not billed".

## Tests

Unit tests for plan mapping (old + new labels, missing file), cycle math
(reset day 31 in February), stop gate, fallback on each error type, price
lookup refusing unknown models, key scrubbing, and that the oneshot hook with
mode `off` calls the original path byte-for-byte. Run the boot smoke
(`tools/smoke/`) plus a Playwright check of the settings card and popup
section at 1440 and 390, screenshots in `_scratch/api_credits/`. pyright basic
0 errors on the new modules.

## Not allowed

No live paid call except the test route's code path in tests (mock it). No
server restart, no merge, no push. Do not change provider_env_scope.py,
the transform sandbox flags, or any human-only gate.


## Appendix: price table (USD per million tokens)

Copied 2026-10-30 by Dave from the bundled `claude-api` skill
(`shared/models.md`, `shared/model-migration.md`, `shared/prompt-caching.md`,
skill build 2.1.312). Builders on engines without that skill use this table.
Only explicitly stated figures are listed; a model or field missing here is
"price unknown" and must fall back to the subscription path, not be guessed.
Cache writes: 5-minute TTL = 1.25x input, 1-hour TTL = 2x input (stated as a
rule for all models). Phase 1 writes 5-minute caches only.

| Model id | Input | Output | Cache read | Cache write 5m | Note |
|---|---|---|---|---|---|
| `claude-fable-5-2` | 8.00 | 40.00 | 0.20 | 10.00 | read 2026-11-10 from platform.claude.com/docs/en/about-claude/pricing (1h write $16) |
| `claude-fable-5-1` | 10.00 | 50.00 | 0.25 | 12.50 | |
| `claude-fable-5` | 10.00 | 50.00 | 1.00 | 12.50 | |
| `claude-opus-5-5` | 4.00 | 20.00 | 0.20 | 5.00 | |
| `claude-sonnet-5-5` | 2.00 | 10.00 | 0.20 | 2.50 | |
| `claude-sonnet-5` | 2.00 | 10.00 | 0.20 | 2.50 | "same prices" as Sonnet 5.5 |
| `claude-haiku-5-5` | 0.10 | 0.50 | 0.01 | 0.125 | prompt <= 100K tokens |
| `claude-haiku-5-5` | 0.50 | 2.50 | 0.05 | 0.625 | prompt > 100K tokens |

`claude-opus-5` is $5 / $25 but its cache-read price is not stated: treat as
unknown (fallback). Haiku 5.5 picks the rate card from the request's total
prompt tokens (input + cache read + cache write). The skill says to check the
live Anthropic pricing page before budgeting on these numbers, so make the
table overridable from `data/api_credits.json` (`prices` key, same shape) and
show "estimated" in the UI for any cost computed from it.

---

# Phase 2: unattended agent sessions bill to API credits

Dave, 2026-11-09. Ron unblocked it the same day ("we're leaving money on the
table"). His gate was that phase 1 had to work on real traffic first. It has:
4,971 offloaded oneshots from 2026-10-31 to 2026-11-09, $6.81 spent, and the
Nov cycle at $6.41 of $200. Projected use is ~$22/month, so ~$178 expires unused.

## Measured 2026-11-09 (the open question from the research note)

- `claude -p --model claude-haiku-5-5 --output-format json` with the vault
  key `anthropic_api` in `ANTHROPIC_API_KEY`: `is_error:false`,
  `total_cost_usd 0.0054`. The credits org ACCEPTS headless Claude Code runs.
- Same call with a bogus key: fails, 0 tokens. So the env key OVERRIDES the
  OAuth login for that child, and the call above was billed to the key.
- Side effect printed by the CLI: "claude.ai connectors are disabled because
  ANTHROPIC_API_KEY ... takes precedence". Local/project MCP servers still
  load; only claude.ai-hosted connectors drop. Acceptable for unattended runs.
- Anthropic cloud routines (claude.ai/code) are NOT covered by the credits
  (they are interactive Claude Code on the subscription). The credits are
  spent locally, through this feature.

## Decisions (Dave, DECIDED WITHOUT RON)

1. **Eligible = unattended.** Scheduled runs, steward, night-review, and
   dispatched sessions started by an agent (MC-946 `notify_session`
   dispatches) bill to credits by default while `mode == "on"` and budget
   remains. Attended chats a human opened stay on the subscription. A
   per-schedule `billing` field: `auto` (default, = credits when eligible) |
   `subscription` (opt out). Reverse: flip the default to `subscription`.
2. **Injection is a named, per-spawn exception** to `provider_env_scope`.
   New unit `mc/api_credits_session.py`: `env_for_session(session, env)`
   returns the child env with `ANTHROPIC_API_KEY` read by
   `secrets_store.get_secret_value('anthropic_api' (settings.secret_name),
   consumer='api-credits-session', unattended=True)`. Only into that one
   child env. Never `os.environ`, never `provider_env.json`, never logged.
   `scope_claude_env` keeps stripping the key from every other Claude child.
3. **Billing is sticky per session.** Chosen at first spawn, stored on the
   session record (`billing: credits|subscription`), re-used on every
   respawn/resume of that session, so a session never flips engines mid-run.
4. **Gate before spawn:** `budget.can_spend()` AND remaining headroom to the
   stop point >= `session_reserve_usd` (setting, default $3). Otherwise
   subscription, with the reason on the session record.
5. **Record actual cost** from the stream's `result` event (`total_cost_usd`,
   `usage`, model) into the existing ledger with
   `caller = "session:<project>:<schedule_id or dispatch>"`, `estimated:false`.
   One entry per turn.
6. **Failure fallback:** if a credits turn ends with credit-too-low / spend
   limit / 401 (same matchers as phase 1), flip state to `stopped` (existing
   one-notice-per-cycle path), mark the session `subscription`, and re-run
   that same turn once on OAuth. The caller sees no new failure mode.
7. **Mode B persistent children:** the billing choice is fixed at spawn; if
   it changes (stopped mid-cycle), the next turn respawns. Add `billing` to
   the respawn trigger keys.

## UI

- Scheduler form: "Billing: Auto (API credits) / Subscription". One select.
- Settings > Agent Providers > API credits card: add `session_reserve_usd`,
  and split the cycle spend into "background calls" vs "agent sessions"
  (group ledger by caller prefix). Session chat header shows a small
  "API credits" badge when `billing == credits`.

## Done means

- Tests: eligibility matrix (attended vs schedule vs steward vs dispatch),
  env injection only on eligible child and stripped everywhere else, sticky
  billing across respawn, reserve gate, fallback re-run, ledger record from a
  fixture `result` event. Pyright 0 on new module.
- Live: one real scheduled run on Haiku shows `billing: credits` on its
  session and a `session:` ledger entry with non-zero cost. Ron restarts
  nothing: Dave restarts :5199 (standing approval) after merge.
- Do not grow `mc/agent_runtime.py` beyond the hook lines.
