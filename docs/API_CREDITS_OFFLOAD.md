# Offloading Clayrune work onto subscriber API credits

Research note, 2026-10-30 (Dave). Nothing is built yet.

## What the credits are

Source: platform.claude.com/docs/en/about-claude/api-credits-for-subscribers
and support.claude.com/en/articles/17154008 (both read 2026-10-30).

- Max: $100/month. Ultra: $200/month. Team: $20 per Standard seat and $100
  per Premium seat, pooled, capped at $500/month.
- Unused credits expire at the end of each billing cycle. Nothing rolls over.
- **Covered:** the Messages API, the Batch API (50% cheaper), Managed Agents,
  the Agent SDK, and `claude -p` when it runs with an API key from the linked
  Console org.
- **Not covered:** interactive Claude Code, and `-p` runs started by the
  GitHub Action, IDE extensions or the desktop app.
- **To claim:** go to claude.ai, Settings > Billing > API credits > Link
  organization. Only a human can do this, and the link is permanent
  (changing it needs support).
- When the credits run out, requests stop. Nothing is ever charged to the
  plan.

## What Clayrune could move

Every Claude run Clayrune makes is `claude --print`
(`mc/agent_runtime.py` ~2152, ~3093). Today those runs are signed in with
OAuth, so they draw on the plan's allowance.
`mc/provider_env_scope.py` deliberately strips `ANTHROPIC_API_KEY` from
Claude runs, because a stray key used to move every run onto API billing.

Ranked by fit:

1. **Toolless oneshots** (`ClaudeRuntime.oneshot()`): Scribe, condense,
   Distiller, `mc/mail_launder.py`, `mc/browser_digest.py`, and the
   auto-model router. Small, frequent and background, so they are the best
   fit for direct Messages API calls. The Distiller can use the Batch API.
2. **Unattended scheduled runs**: competitor watch, Posy's two daily
   passes, memory health, position review, the backlog timer, CLI check.
   These are headless `-p` runs; they need a per-schedule "bill to API
   credits" option.
3. **Not a fit**: attended chats. They are interactive, they are the most
   likely to be classed as Claude Code, and the plan already covers them.

## Open before building

- Whether a credits-only org accepts Clayrune's streaming `--print` runs, or
  rejects them as Claude Code with "Credit balance too low". This needs one
  real test call.
- The key has to be a vault entry created by Ron (agents never create
  credentials). The provider_env_scope guard needs a named, per-route
  exception, not a global env var.
- How much of $100 the oneshots would use per month. Measure one week of
  token counts before moving anything.

## Feature shape: detect the plan, ask for a budget and a stop point

Ron, 2026-10-30: the feature should detect the plan by itself and ask the
user how much of the credit to use and where to stop.

### What Clayrune can detect

- `~/.claude/.credentials.json` > `claudeAiOauth` holds `subscriptionType`
  and `rateLimitTier`. On Ron's box (Ultra): `max` / `default_claude_max_20x`.
- So the CLI still labels Ultra as Max 20x. Confirmed 2026-10-30 by
  Anthropic's rename (x.com/claudeai/status/2116220869479428500): Max 5x ->
  Max ($100), Max 20x -> Ultra ($200), prices unchanged. Mapping:
  `max_20x` -> Ultra, `max_5x` -> Max, `team` -> Team (pooled, up to $500),
  anything else -> not eligible. Match on both old and new labels; the CLI
  may switch to the new names later.
- Same announcement: the 5-hour limit is removed for verified Ultra
  accounts, rolling out from 2026-10-30; weekly limits unchanged. Ron's
  account still reported a 5-hour window that day (12%). Clayrune copes when
  it goes null: the usage bar hides empty windows, pace routing reads only
  the weekly window. Left over: the "Reset 5-hour session" button and the
  5h default in the usage breakdown should hide when the window is absent.
- Detection only prefills the amount. The user confirms or edits it,
  because the label can lag a rename, and Clayrune cannot see whether a
  Console org is linked or what balance is left.

### What Anthropic controls (checked 2026-10-30)

- **Org spend limit:** Console > Settings > Limits. Set by hand, cannot
  exceed the usage tier's cap.
- **Workspace spend limit:** per workspace, below the org limit, not on the
  Default Workspace. No Admin API endpoint for it, so Clayrune cannot set
  it. Anthropic's own advice: give the project its own workspace and cap it.
- **Mismatch:** spend limits reset on the 1st of the calendar month;
  credits refresh on the billing-cycle date.
- **Hard stop:** an org with no card and no purchased credits stops at zero
  ("Your credit balance is too low ..."). Nothing is charged to the plan.
  If auto-reload or purchased credits exist, spending continues past the
  included credit, which is the case the user's stop point must guard.

### The setting

1. Detected plan and credit, editable ("Ultra, $200/month").
2. Budget for Clayrune: dollars, default the full credit.
3. Stop at: percent of budget, default 90%.
4. Cycle reset day: prefilled from the credit expiry the user reads off the
   Console Billing page.

Clayrune keeps its own ledger: each API response's `usage` tokens times the
model price, summed per cycle. At the stop point, offloaded calls go back to
the subscription and the user gets one notice. On "credit balance too low"
the same fallback fires early. The setup card tells the user to also set a
workspace spend limit in Console as the backstop, since only that one is
enforced by Anthropic.

Lives in its own module (`mc/api_credits_budget.py`, one unit one file),
read by the oneshot route only.
