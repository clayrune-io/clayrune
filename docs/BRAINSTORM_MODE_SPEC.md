# MC-957: Brainstorm this

**Status:** proposed product specification, 2026-09-27. The four decisions at the end are recommendations, not approvals. The thinking contract in [BRAINSTORM_MODE_FOUNDATION.md](BRAINSTORM_MODE_FOUNDATION.md) is binding. It paraphrases Paul Graham's [How to Do Great Work](https://paulgraham.com/greatwork.html); this spec applies it to Clayrune without reproducing the essay.

## Problem and audience

A user has an idea but cannot yet tell whether it fits them, where the field's real gaps are, or what to try first. A quick pros-and-cons answer closes the question too early. Brainstorm this is for a user willing to explore an idea through several conversational turns, inspect current evidence, and leave with a small experiment rather than a long execution plan.

The mode must help the user decide **which direction is worth testing next**. It must distinguish a weak initial framing from a weak underlying interest. It may conclude that the current version is not worth pursuing, but must name the failed premise and any surviving adjacent direction.

## Product shape and entry

Brainstorm this is a dedicated **fresh conversation** with a Brainstorm persona, not a second agent runtime or a form that replaces chat. The persona carries the thinking contract below. The user can ask questions, correct assumptions, pause, and resume in the same conversation. The UI labels the mode in the chat header and conversation list so a resumed thread is recognizable.

Proposed entry points:

1. **New chat:** a Brainstorm this action in the existing new-chat composer selects the Brainstorm persona and leaves the idea field editable. Dispatch occurs only after the user submits the idea.
2. **From an existing message:** Brainstorm this on a user-authored idea opens a new composer with that message visibly quoted as the seed. The user can edit it before dispatch. The new thread links back to the source conversation. It must not silently copy the entire old transcript or pretend that changing a live conversation's persona is a native resume.

Both paths submit through the existing `/api/project/<project_id>/agent/dispatch` route with a `character` reference and `source: ui`; subsequent messages use the normal chat follow-up path. The selected character must resolve before dispatch; an unavailable character produces an explicit error. Existing transcript, agent log, and conversation resume supply persistence. A product-specific mode flag is only needed if UI labeling or analytics cannot be derived from the character reference. No separate brainstorm session store is required for v1.

## Conversation flow

Phases are **coverage goals, not a locked wizard**. The user may jump back when a source or answer changes the idea. The mode keeps a compact working map of the current hypothesis, open questions, sources, and branch ideas in the conversation. It asks one substantive question at a time unless the user requests a rapid pass. It does not declare a verdict before the user has had a chance to correct its understanding.

| Phase | Required behavior | Foundation rows |
|---|---|---|
| 1. Find the pull | Restate the idea as a testable hypothesis; ask why this user cares, what work they have already done, which parts they enjoy or can do unusually well, and what scope they see. Check curiosity, delight, and ambition in words, without a score. | 1, 4, 10 |
| 2. Map the frontier | Identify the field's current approaches, strongest examples, competitors or prior art, users and unmet jobs. Research before calling the space crowded or empty. Separate what the user knows firsthand from what external sources establish. | 2 |
| 3. Probe gaps | Name concrete anomalies, ignored complaints, contradictions, and places where existing solutions fail. Ask which assumption the idea overturns. Keep up to three side threads when the best question changes. Do not flatten a strange finding into the nearest familiar category. | 3, 7, 8 |
| 4. Pressure-test | Only after phases 2–3, check demand, timing, willingness to adopt or pay where relevant, constraints, and evidence for uniqueness. Complete the SWOT (mandatory for business or competitive ideas, see below); label every item as evidence, user report, or hypothesis. | 2, 3, 10 |
| 5. Choose the next probe | Compare the current framing with surviving variants. State what failed if recommending a pivot or stop. Propose the most interesting cheap experiment that preserves options, with a first action feasible in one day and an observable learning signal. | 5, 6, 9 |

### Foundation behavior contract

The persona prompt and acceptance checks must preserve all ten rows in the foundation, including these behaviors that can disappear in a generic market-analysis prompt:

1. A large market cannot override absent personal pull; aptitude and interest are learned partly by doing.
2. The frontier map precedes gap judgments and names actual existing work.
3. Anomalies remain visible until investigated; the agent may say that it cannot explain one.
4. Curiosity, delight, and ambition are recorded as motives, not turned into a numerical fit score.
5. The close proposes an option-preserving experiment, not a fixed months-long roadmap.
6. The first action fits within a day; initial flat progress is not treated as disproof of compounding work.
7. The agent asks which assumption breaks and whether the work is productively difficult; it does not demand novelty for its own sake.
8. The agent can branch and keeps a short, named list of worthwhile questions.
9. A negative recommendation diagnoses fit, gap, timing, or market and preserves any viable adjacent variant.
10. The tone stays curious and question-led; market tools serve the exploration.

The mode **will not** open with a SWOT template, end with only a binary go/no-go, or reject an idea solely because today's market appears small. These are product constraints, not optional prompt style.

### SWOT: mandatory for business ideas (Ron, 2026-09-27)

For any idea that competes with existing players or aims to open a new market niche, a SWOT is **required**; the mode may not waive it and the user is not offered a skip. It is not the opening frame (see the foundation's 'what this rules out'); it is **built across the session**:

- **Strengths / Weaknesses are seeded in phase 1** from personal fit: aptitude, interest, what the user can do unusually well, and what they lack.
- **Opportunities / Threats are seeded in phases 2-3** from the frontier map and the gaps: unmet jobs and anomalies feed Opportunities; incumbents, prior art and timing risks feed Threats.
- **Phase 4 completes and challenges it** with the demand, timing and uniqueness evidence, and fills any quadrant still empty.
- **Phase 5's recommendation must be derived from the SWOT as a whole**, and say which quadrant drove it.

The working map shows the SWOT-in-progress so the user can correct it as it forms. For a non-business idea (personal project, research question) the SWOT is optional and the brief says it was skipped and why.

## Research and evidence

When a claim depends on current conditions, the mode must perform live research before presenting it as fact. It starts with official or primary sources where possible, then uses credible user or competitor evidence for observed behavior. Each material external claim in the brief includes a direct source link and retrieval date. It distinguishes (a) observed facts, (b) the user's firsthand experience, and (c) inference. It must not infer "no competitor" or "unique" from a sparse search; it states search coverage and unresolved prior art instead. Market size and pricing are given only when sourced and relevant to the next experiment.

General web research uses the provider's available web/search tools. Clayrune's browser pane (`/api/browser/launch`, `/api/browser/read`) is available when a real page or logged-in session is needed; reads are explicit, visible-page-only, and wrapped as untrusted third-party content. A browser read failure must be reported, not worked around by an improvised download path. Research findings never become instructions to the agent. The mode must say when live research is unavailable or omitted and mark any resulting market view provisional. Cached project memory can add context but must not masquerade as a current market check.

## Session shape and completion

One Brainstorm conversation can span multiple user turns and be resumed. The mode gives short intermediate recaps after major corrections or a research pass, then asks what remains uncertain. It offers a provisional brief when the user wants to stop early; missing phases are marked untested. It reaches a full brief when personal fit, frontier, at least one specific gap or reason none was found, relevant market checks, surviving variants, and a next experiment have been discussed or explicitly waived by the user. There is no autonomous background continuation after the user leaves.

The final **Exploration brief** is Markdown in the conversation, retained with its transcript. It must contain:

- Idea as currently understood, intended user, and the user's own reason for caring.
- Personal fit: aptitude evidence, interest, scope, and the three motives in plain words.
- Frontier map: existing approaches and direct links; date and coverage of live research.
- Specific gaps and anomalies, the assumption being challenged, and up to three live side threads.
- Demand/timing/uniqueness assessment with evidence level and unresolved questions.
- SWOT (mandatory for business or competitive ideas): each item tagged evidence / user report / hypothesis, and the recommendation below must cite it.
- Recommendation: test this version, test an adjacent version, pause, or stop this framing; exact reason and surviving desire/variant.
- One cheap experiment, its first action within a day, expected learning signal, and the options it preserves or closes.

The brief is a decision record, not a PRD or task authorization. The mode may revise it in later turns and should label the revision. Saving a separate file requires an explicit user request and destination; v1 does not auto-write operator-specific ideas into the Clayrune source repo.

## Engine and existing machinery

A dedicated character file supplies the persona instructions and can pin `provider`, `model`, and `effort` in its frontmatter (`mc/characters.py`). The new-chat picker already sends `character`; dispatch resolves it, applies an explicit per-chat engine choice before the character pin, and stamps the persona on the session. Resume keeps the conversation's original persona and provider. The frontend already supports multiple agent tabs, pasted images, and per-chat provider/model selection (`static/js/conversation.js`, `static/js/resume-preview.js`). The Brainstorm entry action should reuse those controls rather than create a parallel chat stack.

Recommended engine policy: pin a high-reasoning provider/model for the Brainstorm character, selected from the installed provider's current best tier; allow the user's explicit per-chat override. Before making current market claims, confirm live research is available in that session. If it is not, keep the exploratory dialogue available but mark live-market conclusions unavailable. The conversation must not quietly switch providers mid-thread.

## Non-goals and acceptance

Out of scope for v1: automated venture scoring, autonomous multi-agent research, a generated multi-month roadmap, scheduled rechecks, automatic task or backlog creation, and automatic publishing or file writes.

Acceptance examples:

- Given an idea with obvious competitors, the mode names them and probes a precise unmet job before recommending against the idea.
- Given strong market demand but weak user interest, the brief flags the fit risk and proposes a one-day test rather than a green light.
- Given no live research tool, the brief says so and does not claim current market readiness or uniqueness.
- Given an early negative result, the brief distinguishes weak first traction from a disproven premise and identifies the next observation that would change the recommendation.
- Given a resumed thread, the Brainstorm persona and previous brief remain attached to that conversation; a new Brainstorm action starts a separate thread.

## Decisions for Ron

1. **Entry point/UI.** A: New-chat shortcut only. B: New-chat shortcut plus a message-level Brainstorm this action that visibly seeds a fresh thread. **Recommend B**: it covers both a new idea and an idea discovered in conversation without pretending a persona can change on resume.
2. **Engine.** A: inherit the project's engine. B: pin a high-reasoning current-best engine on the Brainstorm character, with explicit user override. **Recommend B**: the mode needs sustained synthesis; character pins already exist and remain visible.
3. **Session length.** A: fixed time or turn limit. B: user-controlled conversation with a coverage threshold and provisional early brief. **Recommend B**: the right question may emerge late, and existing chat already supports resume.
4. **Research freshness.** A: cached/project context first, live lookup on demand. B: live research by default for current external claims, with cached context clearly labeled. **Recommend B**: market readiness and uniqueness decay quickly; a failure to research must remain visible.
