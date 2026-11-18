"""`condense_mode: 'agent'`: is there anything for a run to do, and what the
agent is told (MC-1179 ML16).

Agent mode launches a tool-capable `claude -p` agent to curate a project's
memory files. It is no longer the default (`mc/condense_mode.py`); a config
that says `'agent'` still gets it. Two things about it were wrong and are owned here.

**The trigger could not clear.** `_should_condense` summed MEMORY.md +
SESSION_LOG.md + the permanent archive (+ CLAUDE.md) against
`condense_threshold_kb`. The archive is append-only, and it is where a run
MOVES what it demotes, so a run left the sum where it found it. Past the
threshold a project launched one agent per cooldown for the rest of its life,
whether or not there was anything to curate. `index_over_budget` is the
missing "is there anything to do" test: the file the run is asked to bring
under a budget must be over that budget. It is the structured branch's test
(lines against `index_line_budget`, bytes against the byte trigger), so a run
that does its job clears its own trigger. The caller keeps the old size gate
in front of it, so this can only ever stop a launch, never add one.

**The prompt described a layout that is gone.** It told the agent that
MEMORY.md has a managed region (sentinels, a `## Session Log` heading,
watermark lines) and to keep that structure. Since the SESSION_LOG.md split
MEMORY.md is the curated pointer index and nothing else; entries and
watermarks live in SESSION_LOG.md, which the agent was never told exists.
`build_prompt` describes the layout that is on disk: the split one, or, for a
MEMORY.md that has not been migrated yet and still carries the region inline,
the old one, word for word.

Stdlib only. Paths and budgets are passed in; this module does not import
`mc.memory`.
"""
from __future__ import annotations

from pathlib import Path


def index_over_budget(mem_path: Path, line_budget: int, byte_trigger: int) -> bool:
    """True when MEMORY.md is over the line budget or the byte trigger: the
    two targets the agent is given. Missing or unreadable file: False (there
    is nothing a run could curate, and a trigger check must never raise)."""
    try:
        if not mem_path.exists():
            return False
        text = mem_path.read_text(encoding='utf-8')
    except Exception:
        return False
    return (len(text.splitlines()) > line_budget
            or len(text.encode('utf-8')) > byte_trigger)


def _layout_inline() -> str:
    """MEMORY.md still carries the managed region inline (not yet migrated)."""
    return (
        "3. MEMORY.md has two regions, treat them differently:\n"
        "   - CURATED region (everything ABOVE the "
        "`<!-- clayrune:managed:begin -->` sentinel): the hand-curated pointer "
        "index. You ARE permitted to compact THIS region (you are the only "
        "agent allowed to): merge overlapping pointers/sections covering the "
        "same subsystem, drop stale 'as of YYYY-MM-DD' notes clearly superseded "
        "by a later section, cut narration but keep the fact.\n"
        "   - MANAGED region (between `<!-- clayrune:managed:begin -->` and "
        "`<!-- clayrune:managed:end -->`, under `## Session Log`): raw "
        "machine-written session entries. For EACH entry decide, by value: "
        "(a) fold its durable insight into the matching curated pointer/topic "
        "then remove the raw entry; (b) if it has no lasting value, DEMOTE it "
        "(move it) to the archive; (c) keep it in the managed region only if "
        "it's recent and not yet foldable. Never keep/drop by recency alone.\n"
        "4. KEEP THE FORMAT: the rewritten file must still have the "
        "`<!-- clayrune:managed:begin -->` / `## Session Log` / "
        "`<!-- clayrune:managed:end -->` structure intact. The managed region "
        "may legitimately end up EMPTY after folding — that is fine; keep the "
        "sentinels and header. CRITICAL: any line beginning "
        "`<!-- clayrune:wm:` is a live-session watermark — PRESERVE IT "
        "VERBATIM, do not fold/move/delete/reformat it (deleting one loses a "
        "running session's progress and forces a re-scribe from zero).\n"
    )


def _layout_split(session_log_path) -> str:
    """MEMORY.md is the curated index only; the session log is its own file."""
    return (
        "3. MEMORY.md is the hand-curated pointer index and nothing else. You "
        "ARE permitted to compact it (you are the only agent allowed to): "
        "merge overlapping pointers/sections covering the same subsystem, drop "
        "stale 'as of YYYY-MM-DD' notes clearly superseded by a later section, "
        "cut narration but keep the fact.\n"
        "4. KEEP THE FORMAT: MEMORY.md holds no session log. Do not add a "
        "`## Session Log` section, an HTML comment line, or raw session "
        "entries to it. Machine-written session entries and live-session "
        f"progress markers live in {session_log_path}, which Clayrune's own "
        "writer maintains (it moves old entries to the archive itself): do NOT "
        "modify, move, rename or delete that file.\n"
    )


def build_prompt(*, mem_path, archive_path, session_log_path,
                 line_budget: int, byte_budget_kb: int,
                 inline_managed_region: bool, claude_md_path=None) -> str:
    """The housekeeping agent's whole prompt.

    `inline_managed_region`: MEMORY.md still contains the managed-region
    sentinel (a project not yet migrated to SESSION_LOG.md).
    `claude_md_path`: set only when CLAUDE.md is large enough to condense.
    """
    if inline_managed_region:
        layout, not_listed = _layout_inline(), ''
    else:
        layout = _layout_split(session_log_path)
        not_listed = f" {session_log_path} is NOT one of them."
    parts = [
        "You are a memory housekeeping agent (SPEC Leg C model tier). Your ONLY "
        "job is to curate the project context files so they stay concise and "
        "effective. You decide by VALUE, never by recency.\n",
        f"## MEMORY.md curation — target: the WHOLE file under {line_budget} "
        f"LINES and under {byte_budget_kb} KB\n"
        f"(The harness only auto-loads ~200 lines; staying under {line_budget} "
        f"keeps headroom.)\n"
        f"1. Read {mem_path}\n"
        f"2. Read {archive_path} (if it exists)\n"
        + layout +
        "5. NEVER hard-delete a fact. The only permitted deletions are exact "
        "duplicates or an entry STRICTLY superseded by a newer one that wholly "
        "contains it. 'Not worth a curated slot' means DEMOTE to the archive "
        "(still searchable cold storage), never erase.\n"
        "6. DO NOT lose hard-won facts. Preserve verbatim: file paths, line "
        "numbers, function/class names, config keys, exact numeric thresholds, "
        "API signatures, command snippets, and any 'gotcha' warnings.\n"
        f"7. Append demoted/overflow entries to {archive_path} (create it if "
        f"needed). NEVER delete or truncate the archive — it is permanent "
        f"searchable cold storage (SPEC D3).\n"
        f"8. Write the curated result back to {mem_path}. Target under "
        f"{line_budget} lines; if after honest folding it is still slightly "
        f"over, that is acceptable — do NOT delete critical facts just to hit "
        f"a number.\n",
    ]
    if claude_md_path:
        parts.append(
            f"\n## CLAUDE.md condensation — target under 15KB\n"
            f"9. Read {claude_md_path}\n"
            "10. This file contains project instructions and context that Claude CLI loads natively. "
            "Condense it while preserving ALL critical information:\n"
            "   - Keep all instructions, rules, and constraints verbatim.\n"
            "   - Merge duplicate/overlapping sections.\n"
            "   - Remove redundant examples, excessive formatting, and verbose explanations.\n"
            "   - Compress session logs / historical notes into brief summaries.\n"
            "   - Preserve code snippets, API references, and config patterns exactly.\n"
            f"11. Write the condensed result back to {claude_md_path}. Target under 15KB; do NOT "
            f"strip critical rules just to hit a number.\n"
        )
    parts.append(
        "\nBE TURN-EFFICIENT (you have a limited turn budget): read EVERY "
        "input file you need in your FIRST turn using parallel tool calls, "
        "do all the folding/demotion reasoning, then write each output file "
        "EXACTLY ONCE. Do not re-read a file you have already read. The write "
        "step is what matters — do not spend the whole budget exploring.\n"
        "\nDo NOT create any other files. Do NOT modify any code. Only touch the files listed above."
        + not_listed
    )
    return '\n'.join(parts)
