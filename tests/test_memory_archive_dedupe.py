"""Archive dedupe semantics (MC-964 Step A, docs/MEMORY_OVERHAUL_PLAN.md #6).

RC1: the legacy `_dedupe_archive_lines_legacy` kept only the LAST line per
(day, task[:120]) and silently deleted 62% of the archive, including the
2026-09-17 Codex top-up fact (superseded by an unrelated later line filed
under the same chat title, "Where do we stand?"). Content-containment dedupe
replaces it as the default: an earlier line is dropped only when a later
line in its group substantially CONTAINS its content, not merely shares a
title. Threshold (0.6) measured against the live archive — see
`mc/memory.py`'s `_ARCHIVE_DEDUPE_CONTAINMENT_THRESHOLD` docstring and
docs/_journal/b2d85e51-memory-overhaul.md.

This file also freezes the 2026-07-29 `/goal` stale-first-guess case (13
near-duplicate progress lines under one chat title, real archive text) that
justified the ORIGINAL dedupe — the acceptance bar for Step A is that this
case still collapses meaningfully under the new rule, not that it produces
byte-identical output to the legacy one.
"""
import mc.memory as m


def _e(date, task, body):
    return f"- [{date}] **{task}** — {body}"


# ── basic containment semantics ──────────────────────────────────────────────

def test_full_duplicate_collapses_to_one():
    lines = [_e("2026-01-01", "t", "alpha beta gamma"),
             _e("2026-01-01", "t", "alpha beta gamma")]
    out = m._dedupe_archive_lines_containment(lines)
    assert out == [lines[1]]


def test_later_line_containing_earlier_drops_the_earlier():
    earlier = _e("2026-01-01", "t", "the sky is blue today")
    later = _e("2026-01-01", "t", "the sky is blue today and it may rain later")
    out = m._dedupe_archive_lines_containment([earlier, later])
    assert out == [later]


def test_distinct_facts_under_same_day_task_both_survive():
    """The RC1 regression test: two genuinely different facts filed under the
    same chat title on the same day must NOT collapse into one, unlike the
    legacy last-wins rule. Modeled on the real Codex top-up miss — a top-up
    note and an unrelated vendor-decoupling note, same (day, task) key."""
    topup = _e("2026-09-17", "Where do we stand?",
                "Understood, the access problem cleared after adding Codex "
                "credits, not through a code repair.")
    unrelated = _e("2026-09-17", "Where do we stand?",
                    "Vendor-decoupling review found editor and legacy-condense "
                    "writes that bypass the shared protection.")
    out = m._dedupe_archive_lines_containment([topup, unrelated])
    assert topup in out
    assert unrelated in out


def test_empty_content_line_is_absorbed():
    bare = _e("2026-01-01", "t", "")
    later = _e("2026-01-01", "t", "some real content here")
    out = m._dedupe_archive_lines_containment([bare, later])
    assert out == [later]


def test_unparseable_lines_never_collide():
    a = "- [malformed line one"
    b = "- [malformed line two"
    out = m._dedupe_archive_lines_containment([a, b])
    assert out == [a, b]


def test_different_days_are_never_merged():
    d1 = _e("2026-01-01", "t", "alpha beta gamma")
    d2 = _e("2026-01-02", "t", "alpha beta gamma")
    out = m._dedupe_archive_lines_containment([d1, d2])
    assert out == [d1, d2]


# ── config flag routes to legacy vs containment ──────────────────────────────

def test_default_config_uses_containment(monkeypatch):
    monkeypatch.setitem(m.state.CONFIG, 'archive_dedupe_legacy_enabled', False)
    topup = _e("2026-09-17", "Where do we stand?", "top-up fact")
    unrelated = _e("2026-09-17", "Where do we stand?", "unrelated vendor note")
    out = m._dedupe_archive_lines([topup, unrelated])
    assert topup in out and unrelated in out


def test_legacy_flag_restores_last_wins(monkeypatch):
    monkeypatch.setitem(m.state.CONFIG, 'archive_dedupe_legacy_enabled', True)
    topup = _e("2026-09-17", "Where do we stand?", "top-up fact")
    unrelated = _e("2026-09-17", "Where do we stand?", "unrelated vendor note")
    out = m._dedupe_archive_lines([topup, unrelated])
    assert out == [unrelated]          # legacy rule: only the last line survives


# ── the frozen 2026-07-29 /goal stale-first-guess case ───────────────────────

_GOAL_CASE_LINES = [
    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — Found no /goal slash command in Mission Control. Goal "
    "functionality split: Hivemind (decompose goals→workstreams), Steward "
    "(autonomous agent), Backlog (tasks). .claude/commands/ registry empty.",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — `/goal` exists in CLI v2.1.220 (condition-gated) but "
    "MC-blocked by hook+headless constraints. Workarounds: steward, /loop, "
    "hivemind. Next: test MC compatibility or spec native mode.",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "— \"/goal\" is real CLI v2.1.220 feature (condition-gated loop until "
    "condition met) but likely blocked in MC sessions (headless + hooks); MC "
    "has three goal-equivalents (hivemind/decompose, steward/autonomous-cycles, "
    "backlog/tracked-tasks) but no native condition-gated mode; test if /goal "
    "works in MC or",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — `/goal` (stop-gate command) confirmed in v2.1.220; MC relays "
    "all slash-commands verbatim to CLI; headless-capable built-ins (goal, "
    "model, effort, context, config, mcp, usage, import, rename, fast, stop, "
    "reload-skills) execute but `/goal` likely blocks due to MC's hook system "
    "(refuses \"while hooks ru",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — `/goal` confirmed v2.1.220; MC relays slash-commands via "
    "JSON stdin; headless built-ins work (goal, model, effort, context, "
    "config, mcp, usage, import, rename, fast, stop, reload-skills) but "
    "`/goal` blocks (hooks); `-p \"/cmd\"` path-mangles in Git Bash "
    "(workaround: MSYS_NO_PATHCONV=1/`//cmd`); `/clea",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — `/goal` v2.1.220 verified live in MC (bare usage & "
    "`<condition>`-loop both working); MC relays slash-commands via JSON "
    "stdin; headless built-ins functional; Git Bash path-mangle workaround "
    "known (MSYS_NO_PATHCONV=1)",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — `/goal` v2.1.220 verified live in MC; bare & "
    "`<condition>`-loop working; MC relays slash-commands via JSON stdin; "
    "headless built-ins OK; GOTCHA—slash-commands parse session-open only; "
    "mid-session re-entry executes but returns text not re-parsed; Git Bash: "
    "MSYS_NO_PATHCONV=1",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — `/goal` v2.1.220 verified live in MC; bare & "
    "condition-loop working; MC relays slash-commands via JSON stdin; "
    "GOTCHA—slash-cmd parse session-open only; mid-session text not "
    "re-parsed; mobile fails (_apply_mobile_brief prepends directive breaking "
    "`/` parser—preserve `/cmd` or move directive after); G",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — `/goal` v2.1.220 live; slash-cmd parsing fixed via "
    "`_SLASH_COMMAND_RE` guard in `agent_routes.py` (commit f11c61d—reused "
    "`_re_auth`); mobile brief-reply no longer eats `/`; MC restart needed; "
    "GOTCHA: session-open parse window may limit mid-session re-eval",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(reconciled)_ — `/goal` v2.1.220 live; slash-cmd parsing fixed via "
    "`_SLASH_COMMAND_RE` guard in `agent_routes.py` (commit f11c61d—reused "
    "`_re_auth`); mobile brief-reply no longer eats `/`; MC restart needed; "
    "GOTCHA: session-open parse window may limit mid-session re-eval",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — Fixed mobile brief-reply directive breaking "
    "`/slash-command` dispatch in MC; added `_looks_like_slash_command()` "
    "guard to skip prepend when message starts with `/name`, `/plugin:skill`, "
    "etc. (commit f11c61d). Prevents `/goal`, `/context`, `/model` from being "
    "buried behind the phone directive and thu",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — Fixed `/slash-command` dispatch (f11c61d); fixed "
    "per-project agent_model override (was opus-4-8, now opus-5). Gotcha: "
    "per-project settings override global; sticky_agent_settings persists "
    "until next reload.",

    "- [2026-07-29] **can you check if we have equivalent to \"/goal\" command?** "
    "_(live)_ — Fixed slash-command dispatch & per-project agent_model "
    "override (f11c61d). Migrated 3 projects to opus-5; clayrune_cloud "
    "unchanged (opus alias). Gotchas: per-project settings override global; "
    "sticky_agent_settings persists; model changes apply to new sessions only.",
]


def test_goal_case_is_13_real_lines():
    assert len(_GOAL_CASE_LINES) == 13


def test_goal_case_collapses_meaningfully_under_containment():
    out = m._dedupe_archive_lines_containment(_GOAL_CASE_LINES)
    # Measured 2026-09-24 at threshold 0.6: 13 -> 7 survivors. The bar is
    # "still collapses" (per MEMORY_OVERHAUL_PLAN.md #6), not "collapses to
    # exactly 1 like the legacy rule" — containment only merges lines that
    # actually restate each other, and this chat's early guesses diverge in
    # content from its own later ones (wrong answer vs. right answer), not
    # just in length.
    assert len(out) < len(_GOAL_CASE_LINES) * 0.6

    # the LAST line is always the definitive, most-complete answer for this
    # incident and must never be the one that goes missing
    assert _GOAL_CASE_LINES[-1] in out


def test_goal_case_legacy_rule_collapses_to_one():
    """Documents what the legacy rule did to this exact case, for contrast."""
    out = m._dedupe_archive_lines_legacy(_GOAL_CASE_LINES)
    assert out == [_GOAL_CASE_LINES[-1]]
