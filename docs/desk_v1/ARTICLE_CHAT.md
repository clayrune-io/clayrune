# Studio article agent conversation

Branch candidate, 2026-10-29, Sol_Tobin. Branch `clayrune/agent/13af1dec6dad`.
No merge, push, restart, named-profile access or real model calls.

## Behavior and boundaries

The standalone Studio article writer mounts the shared `DeskV1Kit.posyBoxHTML`
and `bindPosyBox` with a real conversation adapter. The title is its fixed scope;
the stable article id owns its history, unsent message and in-flight state.
The existing campaign article editor retains its campaign conversation surface.
The writer and its library/save/campaign actions remain available while chatting.
Accepting a proposal briefly locks writing while its save and apply complete.
On desktop the conversation is beside the text; claims stay beneath the text.
At widths at or below 960 pixels, the conversation follows the writer.

The saved article's project controls identity: `presence.desk_agent`, resolved
through the existing Studio character and engine helpers. A missing project,
planner or deleted character is an explicit error, not a different agent.
A draft without a project uses the same default engine as an unassigned Studio
storyboard and labels it **Studio agent**. Client-supplied identity is ignored.
No campaign agent can replace the project's default planner for this surface.

The active tab's complete current text, title, tab label, recent conversation
and message are supplied as JSON stdin data to the toolless Studio transform.
The latest proposal for the active tab is also included whole with its status,
so a follow-up can refine unaccepted text without pretending it was applied.
Other tabs stay untouched. Text is never shortened silently: the ordinary
100,000-character body limit is validated before calling the model. The model
can return prose and a full replacement proposal for this tab. Only a saved
proposal can be accepted; its text comes from the server's record, not a second
client submission. Existing claim and media markers are retained in context and
the model is instructed to preserve them unless asked otherwise.

Conversation records live in `studio_articles[id].chat` in the existing Desk
store, outside project-record `DATA_DIR`. Ordinary article PUT preserves this
field, and trash/restore carries it with the draft. Conversations keep the
latest 100 turns; model context uses up to 12 turns at 1,500 characters each.
Chat does not increment the article revision or alter any tab. Accept/Undo
compare revision, tab id, expected text and proposal state under the store lock,
then update only the target tab and increment the revision. A proposal awaiting
acceptance also survives reload. Header Undo uses the existing session command
bus, so its button/history has the same lifetime as other Desk commands.
No publishing, approval or passcode gate changes.

## Files and routes

- `static/js/desk-v1-article-chat.js`: conversation, proposal UI and Undo adapter.
- `static/css/desk-v1-article-chat.css`: desktop/mobile layout and proposal styles.
- `static/js/desk-v1-studio-article.js`: mount host and bridge to existing saves.
- `mc/desk_article_chat.py`: model call, persisted thread and guarded proposals.
- `mc/blueprints/desk_article_chat_routes.py`: independent route group.
- `static/index.html` and `server.py`: registration only; no new concern bodies
  added to either monolith. `desk-v1-studio.js` is unchanged.

| Method | Route | Contract |
| --- | --- | --- |
| GET | `/api/desk/studio/articles/<id>/chat` | `{thread}` |
| POST | same | `{message, tab_id, body}` → `{turns, agent, provider, model}` |
| POST | `/api/desk/studio/articles/<id>/chat/<turn_id>` | `{action: accept\|undo, rev}` → `{article, turn}` |

Model and malformed-context failures are explicit errors; invalid proposed text
keeps the agent's prose with `proposal_error` and exposes no acceptance action.
A deleted draft is never recreated when a delayed answer lands. Failed sends
restore the typed message. Late answers only repaint their own draft, even when
two drafts have the same title.

## Verification

All commands run against this worktree, not the live :5199 checkout. Model calls
are stubbed; browser API fixtures load the real SPA and real writer modules.

| Check | Result |
| --- | --- |
| `python -m pytest tests/test_desk_article_chat.py tests/test_desk_studio_articles.py tests/test_desk_story.py tests/test_desk_storyboard.py tests/test_desk_story_chat.py tests/test_desk_campaign_chat.py -q` | 130 passed |
| `pyright mc/desk_article_chat.py mc/blueprints/desk_article_chat_routes.py` | 0 errors |
| `node tools/smoke/desk-v1-article-chat.mjs` | passed at 1440 and 390 |
| `node tools/smoke/desk-v1-studio-article.mjs` | passed demo desktop/mobile and live API fixture |
| `node tools/smoke/boot-smoke.mjs` | passed |
| `node tools/smoke/desk-v1-studio.mjs` | passed all three tones and desktop/mobile checks |
| `npm test` (in `tools/smoke`) | passed full configured frontend suite |

The new browser smoke covers current unsaved text, default/project labels,
proposal preview without writing, Accept/Undo, refusal after manual edits,
failed-send recovery/retry, failed acceptance without text loss, in-flight
same-title draft isolation, persisted conversation/proposals after reload,
shared large view, Undo after navigation/cache replacement and no horizontal
viewport overflow. Backend coverage includes
revision and text conflicts for both Accept and Undo, other-tab preservation,
server-owned planner resolution, history persistence through PUT/trash/restore,
malformed replies/proposals, input limits, bounded history, deleted drafts and
model failures. No new dependency or agent convention.

Review captures (gitignored): `_scratch/article-chat/writer_1440.png`,
`writer_390.png`, `draft_1440.png`, `draft_390.png`, `default_1440.png`,
`default_390.png` in the same directory.
The main checkout's smoke dependencies are borrowed by a temporary junction,
removed before committing; no npm/Chromium install.

Unattended-fence notes: writing the required home `.claude/plans` file was
refused because that directory is human-owned supply-chain configuration;
the plan is preserved in `docs/_journal/13af1dec6dad-studio-article-chat.md`.
Restoring tracked legacy smoke-generated PNGs with `git restore` was also
refused as a working-tree overwrite. Those images remain unstaged and are not
part of the commit. The exact refused restoration command is recorded, without
execution, in `_scratch/article-chat/cleanup-legacy-captures.ps1`. No retry or
alternative overwrite was attempted. Main Playwright dependencies remain intact.

Integration owner: Dave. After integration, restart the server and hard reload
the SPA through the normal authorized flow, then try one real draft and rewrite
with both a configured project planner and No project. Confirm the model reply,
explicit acceptance, Undo and draft reopen. Live model/provider behavior remains
unverified on this branch. No open design decisions.
