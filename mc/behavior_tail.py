"""Per-turn behaviour-rule tail — extends the MC-944 split (mc/memory_turn.py:
"behaviour rules re-deliver every turn, facts deliver once") to the standing
CONDUCT rules themselves, not just retrieved memory.

THE DEFECT THIS CLOSES. `data/SHARED_RULES.md` carries the reply-length /
no-narration / no-dangling-promise rules, ~46KB into the system prompt built
once by `_build_agent_context()`. For a Mode-B session that stays alive for
days, that block is set at spawn and never resent — by the time the model is
several hundred turns deep, the rule that governs THIS reply is thousands of
tokens back, past however much attention decays with distance. The Stop hook
(`~/.claude/hooks/reply-length-guard.py`) catches the miss after the fact,
every time, which is exactly why it is needed: a rule the model can still see
and still ignore is a preference, not a constraint. This module is the
prevention half — the same "re-state it right before generation" fix
`mc/memory_turn.py` already applies to retrieved facts, applied here to the
handful of rules that shape HOW a reply is built rather than what it knows.

WHAT IS PROMOTED (and therefore REMOVED from SHARED_RULES.md, so it is never
stated twice): reply length/shape (answer first, ~5 bullets, ~150-word
ceiling), never ending a turn on a promise of future work, and not narrating
your own diligence/intent. Explicitly NOT promoted: project facts,
architecture, commit discipline, the authority/reversibility rules, the
backlog-journal rule — those are knowledge or policy, not per-turn reply
shape, and stay exactly where they are. Voice (register, rhythm, what a
persona never says) stays owned by the CHARACTER block per
`_build_agent_context`'s existing split — this module governs conduct, not
tone, and must not be merged with it.

WHERE THIS RUNS. Two independent injection points, mirroring memory_turn's own
split between dispatch (system-prompt) and live-turn (stdin) delivery:
  1. `_build_agent_context()` appends `render()` as the LAST part of the
     system prompt, after everything else (current_task included). This is
     provider-agnostic — Claude, Gemini, Codex, and every other AgentRuntime
     dispatch/respawn goes through this one function — and it is what a fresh
     dispatch, a model-switch respawn, or a non-Claude followup (which
     rebuilds the whole context every turn — see `agent_followup`'s non-claude
     branch) gets automatically.
  2. The two Claude Mode-B direct-stdin-write sites that ALSO call
     `mc.memory_turn.refresh_for_turn` (no context rebuild happens on those
     paths at all — that is the B5 break memory_turn.py's docstring
     describes). `render()` is prepended there too, positioned AFTER the
     memory-turn block (background facts) and BEFORE the mobile-brief-
     augmented message (a per-message, device-specific directive that stays
     closest to the user's own words, unchanged). This is the one case where
     "on every turn" is not free: without it, a long Mode-B chat only ever
     saw this block once, at spawn, then never again.
The two model-switch/interrupt respawn sites are deliberately NOT touched —
they already call `_fresh_context_for`, which re-runs point 1, so wiring this
there too would duplicate the block on the same turn.

WHAT THIS DELIBERATELY DOES NOT DO: gate on provider, grow with usage, persist
any state, or touch the reply-length Stop hook (unchanged — it remains the
backstop for whatever gets through). Never raises: a bug in `render()` must
not cost a live turn its stdin write, exactly like `memory_turn.refresh_for_turn`.
"""
from __future__ import annotations

from mc.core import _log

# Kept intentionally under ~750 bytes — this is a per-turn, per-provider cost
# paid by every agent on every project, forever. Anything longer belongs in
# SHARED_RULES.md (delivered once) or a doc, not re-stated every turn.
_TAIL_TEXT = (
    "--- REPLY SHAPE (binding, re-stated every turn) ---\n"
    "Reply is a summary, not a log: lead with the answer/outcome in the "
    "first line, then at most 5 one-line bullets (numbers over adjectives), "
    "then stop. ~150-word ceiling unless the user asked for depth. No task "
    "recap, no process narration, no closing summary. Never end a turn on a "
    "promise of future work -- do the next tool call now or ask a direct "
    "question. Don't announce diligence or intent (\"to be safe...\", "
    "\"let me check...\"); just do it and report what you found.\n"
    "Your task is a GOAL, not a topic: the turn ends when it is achieved "
    "and verified, not described. Asking whether to take a REVERSIBLE "
    "step never satisfies it -- take it, then report. Save the ask for "
    "irreversible steps."
)


def _cfg(key, default):
    try:
        from mc import state
        return state.CONFIG.get(key, default)
    except Exception:
        return default


def enabled() -> bool:
    """Master rollback lever. Default ON — flip to False to restore the
    pre-this-change behaviour (rules live only in the once-per-spawn bulk
    block) with no code change or respawn required; both injection sites
    read this live."""
    try:
        return bool(_cfg('behavior_tail_enabled', True))
    except Exception:
        return True


def render() -> str:
    """The tail block, or '' when disabled. Never raises."""
    try:
        return _TAIL_TEXT if enabled() else ''
    except Exception as e:
        _log(f"[behavior-tail] render failed: {e}")
        return ''
