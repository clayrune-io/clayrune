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
