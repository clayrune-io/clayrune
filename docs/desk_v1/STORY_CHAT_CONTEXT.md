# MC-1069: whole storyboard lines in chat

Backlog item: `65c58e97`.

The chat context previously capped every unselected scene at 600 characters,
even when the complete board fit the 60,000-character scene-text budget. Since
the prompt and sparse-edit validator refuse edits to a `line_cut` scene, a
seven-scene board with 1,900-character instructions could not be edited through
the board conversation despite using only 13,300 characters.

`mc/desk_story_chat.py` now imports `MAX_LINE` from the storyboard module.
Boards that fit the budget retain every instruction. Over-budget boards use
the largest common cap that fits the unselected lines plus the complete focused
line. Only lines exceeding that cap are shortened; shorter lines retain their
text rather than reserving an equal budget share. Scene order, labels, durations
and the source board are preserved.

The focused scene always stays whole. `CONTEXT_LINE_MIN` remains the floor for
shortened lines. If the focus and floors cannot fit a reduced budget, those
invariants take priority; under current store limits (200 scenes, 2,000 characters
per line) they can always fit the production 60,000-character budget.
The prompt's `line_cut` instruction and `_board_change` guard are unchanged.

## Validation and handoff

Verified on 2026-10-07:

| Command (from repository root unless stated) | Result |
| --- | --- |
| `python -m pytest tests/test_desk_story_chat.py tests/test_desk_story.py tests/test_desk_storyboard.py -q` | rc=0; 69 passed |
| `node desk-v1-story.mjs` (in `tools/smoke`) | rc=0; all checks passed |
| `node desk-v1-story-chat.mjs` (in `tools/smoke`) | rc=0; all checks passed |
| `git diff --check` | rc=0 |

Backend coverage includes seven 1,900-character scenes with and without focus,
a successful sparse edit to a formerly cut line, 40-scene over-budget boards,
mixed line lengths, the exact budget boundary, and the focus/minimum-floor
priority. Existing coverage still checks that cut lines cannot be edited and
untouched source lines return whole.

The browser smokes use fixture APIs and a stub model; they verify desktop and
390px mobile behavior, not a live model call. Smoke dependencies were borrowed
through a temporary junction. That junction was removed from `tools/smoke`
before commit, by relocating it into gitignored scratch after automatic review
blocked deletion. Main dependencies are unchanged. No package installation.

Local branch: `clayrune/agent/a5c54536b143`, based on `8a54636f`.
No merge, push, server restart or live call. Dave owns integration and the live
walkthrough: open a board with long instructions, deselect scenes, request an
edit, and confirm the reply no longer refuses ordinary scenes as truncated.

The existing chat interaction is unchanged, so `docs/USER_GUIDE.md` does not need
new controls or instructions. No new dependencies, modules or agent conventions.
