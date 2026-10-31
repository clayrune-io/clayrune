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
