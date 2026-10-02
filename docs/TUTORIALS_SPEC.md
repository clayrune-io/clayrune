# Learn: Floor pilot

Status: specification, 2026-10-01. Owner: Dave. Scope changed to one pilot before expanding the curriculum. Q1-Q3 DECIDED by Dave 2026-10-01 (see DECISIONS at the end); where a decision differs from a recommended contract in the body, the DECISIONS section wins.

## Outcome and scope

Learn is a permanent place to practice Clayrune. The pilot teaches a user to find a session figure, open its chat, and hire an agent type from the Bench onto a practice project. Success requires those actions, not advancing explanatory cards. Claydo is the narrator; Clayrune is the product.

The release contains the lesson engine, Learn hub shell, and one three-step Floor lesson. It will not add other lessons, replace provider setup, run agents, create credentials, publish, render media, or schedule execution. Practice never changes the user's work. No prerequisite lesson or provider login is required.

## Current implementation anchors

These files were read on the spec branch. They are implementation anchors, not evidence that Learn already exists.

| Source | Existing behavior and implication |
| --- | --- |
| `static/js/walkthrough.js` | `WT_STEPS` has 12 declared steps with viewport skips, despite the older 10-step description. `target`, `demo`, `onEnter`, `onLeave`, `skip`, measurable-target checks and viewport clamping exist. `wtNext()` advances without verifying actions; missing targets can fall back to demo markup or centered cards. |
| `static/css/app.css`, `.wt-*` | Overlay allows pointer passthrough, backdrop blocks input, highlight pulses indefinitely, cards capture input. Reuse visual primitives only after fixing input regions, bounded motion and mobile placement. Setup shares these styles. |
| `static/js/first-run.js` | Setup is separate; `setupTakeTour()` finishes setup then starts the tour. Server `setup_completed` differs from browser `walkthrough_done`. Learn must not alter either setup gate. |
| `mc/blueprints/guide_routes.py`, `create_sample_project()` | `POST /api/walkthrough/sample-project` idempotently seeds ID `clayrune`. Its workspace and backlog are real, editable, dispatchable and explicitly repurposable. It is not an isolated sandbox. Never reset it for a lesson. |
| `static/js/floor.js` | `_floorFigure` renders sessions; `_floorBench` renders types. `floorOpenFigure` opens a conversation. `_hireCharacter` persists roster membership; drag uses `_hireDrop`. Bench picker `floorPlace` only selects a type for a new chat, so picker navigation alone must not count as hiring. Polls rebuild `#floor-body`; drag activation suppresses competing refreshes. |
| `static/js/claydo.js` | Ask Claydo uses `/api/guide/stream` and parses a fixed marker allowlist (`goto`, `open-modal`, `highlight`, etc.). There is no lesson action today. |
| `tools/smoke/walkthrough.mjs` | Hermetic real-page/real-module browser harness with intercepted fixture APIs. Extend this testing pattern, not its Next-button completion assumption. |

## Learn hub and entry

Add one persistent **Learn** navigation item, reachable in the desktop sidebar and mobile navigation drawer, plus a command-palette entry. Hub title: **Learn with Claydo**. One card: **The Floor**, subtitle **Find a figure, open a chat, hire a type.**, metadata **3 actions · Practice only**. States: Start, Resume, Replay. Completion does not hide the card. No locked cards, empty categories, streaks or placeholder lessons.

On the first user-initiated Floor opening, after setup has closed, offer a nonblocking Claydo bubble: **Meet the Floor in three actions. Want to try?** Controls: **Start practice**, **Not now**. Show this offer once per progress owner; dismissal must survive reload. Never auto-start or cover a dialog, active drag or unsaved editor. Add **Learn the Floor** to the Floor header for replay. Hub, offer, header and Claydo all start the same lesson ID `floor-v1`.

Ask Claydo gains an allowlisted lesson-launch action with a registered lesson ID only. A question such as "Teach me the Floor" produces a **Start Floor practice** chip. Clicking it launches the engine; model output alone cannot launch, reset or complete practice. Reject unknown IDs and arbitrary selectors/commands. One active lesson at a time; switching offers Resume or Leave practice without losing progress.

## Engine contract

Recommended under Q1: a lesson engine beside `walkthrough.js`, sharing extracted presentation utilities but no tour index, demo factories or completion flag. Keep the legacy tour and setup behavior during the pilot. The tour is a viewport-skipping orientation; lessons require action evidence, persistent state, sandbox routing and recoverable failures. Combining their state machines risks setup regressions.

Each lesson declares stable lesson/version/step IDs, preparation, desktop and mobile target resolvers, Claydo copy, accepted user action, authoritative completion predicate, effects and cleanup. State sequence: preparing → active → verifying → acknowledged → next step or completed. Paused and target-unavailable are resumable states, never completion.

Preparation may navigate and load fixtures. It may not perform the taught action. A step advances only after a user action made while that step is active and its predicate succeeds. Clicks, elapsed time, a Next control, optimistic DOM changes and automatic navigation do not prove mutations. Match evidence to practice project, session/type, run ID and step ID. Ignore stale, duplicate and unrelated events. Failed operations keep the current step and show the actual error with Retry; retries must be idempotent.

The bubble has **Pause**, **Leave practice**, and **Hint**. Hint repeats the cue, never performs the action. Navigation controls and the explicit Hire control below perform real operations; none marks a step complete directly. After verification, show the short acknowledgment, then reveal the next task without a Next button. Navigation away pauses. Leaving removes overlays, listeners and fixture routing and restores the prior surface/focus. It does not reset progress.

## Practice contract

Recommended under Q2: persistent tutorial records in a separate server store outside `data/projects/`, selected by an explicit practice context. No tutorial sidecars may pollute project-record discovery. Proposed practice operations are new implementation work, not existing API claims.

Seed exactly one practice project **Learn practice**, one idle session figure **Pip** and one project-scoped Bench type **Guide**, initially absent from the roster. Pip's canned chat reads **This is a practice conversation. No agent is running.** The fixture must render through the actual Floor, project tile/list, chat and Channel components with their normal layout and labels. An additional persistent **Practice only** banner identifies the context. Do not fabricate screenshots, duplicate card markup or represent Pip as a real running process.

Practice context scopes all reads and mutations, including background refresh and asynchronous callbacks, to the fixture. No real projects/types appear in practice pickers. The hire service records Guide in the practice roster and returns the usual roster result; the execution boundary refuses dispatch, publish, render, credentials and scheduled execution in practice, even if the normal control is reachable. Other tabs retain their own context. A late practice callback cannot update live caches after exit.

Resume reuses the same fixture. Replay creates a fresh practice run and empty practice roster while preserving historical completion; it never overwrites or deletes real projects, including `clayrune`. Fixture migration failure offers **Restart practice**, clearly naming that only practice state resets. Do not silently claim old evidence is valid against new fixtures.

## Fully written pilot: The Floor

The selectors below exist in the read source. While practicing, `#floor-body` contains exactly the one fixture room, figure and Bench type, making their resolution unambiguous. Require exactly one match; do not use `first-child` against a live board. Proposed stable hooks in the maintenance section are additional implementation work.

### 1. Find Pip and open the chat

- **Preparation:** mount the actual Floor with the fixture. Target `#floor-body .fl-room .fl-fig .fl-cta`, rendered by `_floorFigure`; its ancestor `.fl-fig` calls `floorOpenFigure`. Highlight the whole `.fl-fig` so name/avatar edit controls are not the suggested hit area.
- **Claydo:** **Find Pip in Learn practice. Open this chat to see what this figure is doing.**
- **Action and advance:** user activates the figure's chat area. Verify the practice project and Pip session are the selected conversation and its canned transcript has rendered through `openConversation` in `static/js/conversation.js`. A renamed avatar, a drag, or a failed transcript load does not advance. Acknowledge: **That's Pip's session. A figure opens a conversation.**
- **Effect:** Claydo arrival and two arrow nudges toward `.fl-cta`; success tick when the transcript is visible. Preserve reading time: step 2 appears in the chat, without automatically closing it.
- **Mobile:** same card and predicate; tap rather than hover. Keep the bubble clear of the chat area; scroll the figure into view only before interaction starts. No skipped step or desktop sidebar target.

### 2. Choose a type from the Bench

- **Preparation:** bubble navigation control **Back to Floor** calls `openFloor()`; it does not complete the step. On return, target `#floor-body .fl-bench-card .fl-bench-main`, rendered by `_floorBench`.
- **Claydo:** **Back on the Floor, find Guide on the Bench. Open its card. Types are who you can hire.**
- **Action and advance:** user activates Guide's `.fl-bench-main`, invoking `floorTogglePicker`. Verify its card has `.fl-bench-open` and its `.fl-pick-row` is visible. This is type selection, not a hire; do not instruct the user to select the `.fl-pick` room link, which calls `floorPlace`.
- **Effect:** one soft Bench outline and a speech-bubble tail toward the card. Tick on picker opening; acknowledgment **Guide is a type. Now give it a project.** No confetti.
- **Mobile:** tap the same Bench card after **Back to Floor**; retain native vertical scrolling and do not require long press for this step. Keyboard activation must perform the same operation.

### 3. Hire Guide into Learn practice

- **Targets:** source `#floor-body .fl-bench-card.fl-draggable`; desktop destination `#projects-col .card[data-id="<practiceProjectId>"]`, mobile destination `#projects-col .mc-chat-row[data-id="<practiceProjectId>"]`. These are the real source and destination shapes used by `floorFigDown`/`HIRE_TILE_SEL`; `data-id` is rendered by `render-core.js` and `mobile.js`. Resolve the actual fixture ID, not a hardcoded `clayrune` ID.
- **Claydo:** **Give Guide a place to work. Drag it onto Learn practice, or use Hire to practice. Hiring adds the type. It does not start a task.**
- **Action and advance:** accept a valid drag/drop using `_hireDrop`, or the new lesson bubble control **Hire to practice**, invoking existing `floorHireMenu(practiceProjectId, 'project', guideTypeName, 'Guide')`. The type is project-scoped, so this existing handler selects its own project without a native project-number prompt. The control is a tutorial accessibility fallback, not a fake Bench control. Disable it while a hire is pending.
- **Verification:** successful practice hire response plus Guide in the authoritative practice roster, correlated to this run. `_hireOpenChannel` may navigate automatically; that navigation alone does not count. Already-hired state on replay must be reset before activation. Cancelled drag, wrong target or refused write leaves the step active.
- **Effect:** two slow arrow nudges from source toward destination, stopping at drag activation. On verified hire, one progress seal and the completion celebration. Completion copy: **You found a figure, opened its chat, and hired a type. Your real projects are untouched.** Controls: **Return to Floor**, **Replay**, **Learn**.
- **Mobile:** **Hire to practice** is the primary path and uses the same predicate; long-press drag remains optional. On desktop, position the real Floor and dashboard so the practice tile is visible before activation; if the viewport cannot expose both, use the same fallback. Never move windows while a pointer is held. Keyboard users can complete all three steps without dragging.

## Delight, placement and accessibility

| Cue | Exact use | Bound and reduced-motion behavior |
| --- | --- | --- |
| Claydo arrival | Narrator enters once when starting/resuming; 180 ms rise, 6 px travel. Speech bubble: one task, no typewriter text. | Static face and fully visible text with reduced motion. No arrival on every poll. |
| Pointer arrow | Two 6 px nudges, 600 ms each, on steps 1 and 3; optional Hint repeats once. | Static arrow and outline with reduced motion. No continuous flashing, strobe, sound or cursor chasing. Stops on input/drag. |
| Soft emphasis | Step 2 outline fades in over 200 ms; bubble tail points at target. | Static high-contrast outline; never rely on color alone. |
| Action tick | 180 ms checkmark pop after each verified action; labeled progress changes to 1/3, 2/3, 3/3. | Instant checkmark and polite status announcement. Never replay a tick after refresh. |
| Finish | Claydo waves once; six small paper dots disperse around the completion bubble for 700 ms. | Static completion seal. No full-screen confetti, audio, automatic replay or animation blocking controls. |

Desktop uses a bubble beside the target with a restrained local spotlight. The target, required navigation and Pause/Leave remain operable. Pointer decorations never intercept input. Do not reuse the existing backdrop unchanged: drag must cross the backdrop to reach the destination.

At widths <=960 px use a bottom-docked compact bubble above navigation and safe-area insets, with a collapse control leaving the task/progress visible. Use visual viewport measurements for the keyboard. Keep both the task and required action visible; permit scrolling, avoid fixed `touch-action: none`, and recompute anchors after scroll, resize, orientation and rerender without repeating preparation or effects. Tutorial controls have >=44 px touch targets. Honor `prefers-reduced-motion`; **Quiet effects** within the lesson also disables motion. Respect current theme and contrast.

Bubble text is accessible by keyboard and screen reader. Convert the pilot's clickable figure and Bench affordances to keyboard-operable semantic controls without changing their visual composition. Do not steal focus during typing or drag; announce verified progress politely and return focus on exit. Escape pauses, never completes. All new user-facing copy must contain no em-dashes.

## Persistence and maintenance

Recommended under Q3: install-shared progress, matching the current single-install setup model. Persist lesson/version, offered/dismissed, run/fixture version, active step, verified action IDs, completion and quiet-effects preference. Keep this outside `data/projects/`. Save each verified action before advancing; a failed save stays retryable without repeating the hire. Local recovery buffers are not completion evidence. Starting from another browser offers Resume; only one client may mutate a practice run at once, with an explicit takeover that pauses the previous client. Reload resumes the saved step and validates fixture identity. Completed history survives lesson revisions; changed unfinished step contracts require an explained restart.

Add stable identity hooks for practice room/project, session figure and Bench type, plus semantic action hooks for open-chat/open-type. These are proposed additions: `.fl-fig` currently lacks a session data attribute. Resolve by identity and action hook, not text, array order or inline `onclick` parsing. Reacquire nodes after Floor polls; freeze highlights during an active drag. Runtime guards require one connected, visible, measurable, unobscured target. After 5 seconds of unsuccessful resolution, pause with **This lesson needs an update. Your progress is saved.** Offer Retry, Leave practice, Learn. Never substitute demo markup or mark an absent step completed.

Implement `tools/smoke/learn-floor.mjs` using the existing hermetic browser pattern. One release guard for this lesson must exercise all actions and predicates on desktop, 390 px touch and 960 px boundary, plus reduced motion and keyboard fallback. Assert fixture-only writes, no dispatch/execution, failed-hire nonadvance, missing/duplicate/hidden target pause, poll replacement, mid-drag refresh, reload/resume, offer dismissal, replay isolation and cross-client takeover. Capture each step against actual components. Target-hook changes must update the lesson and its smoke in the same change. Run existing walkthrough/setup smokes as regression guards.

## Acceptance and pilot review

Release only when every entry point reaches this single lesson; all three actions are verifiable; completion cannot be earned through Next/click counting; and a before/after sentinel of real projects, rosters and settings is unchanged. A fresh install with no provider must finish the pilot. Touch and keyboard users must finish without drag. Pausing, returning and replaying must work after reload.

Before adding lessons, Dave reviews the pilot with Ron: where help was needed, whether figure versus type became clear, whether hiring felt real, and which cues became annoying. Record step starts, verified completions, pauses, target failures and Hint use locally, without chat contents or remote analytics. No additional lesson implementation until this review adjusts or accepts the pilot.

## LATER

- Backlog: create, prioritize and complete a practice item.
- Workflows: place nodes, connect them and save a practice canvas.
- Scheduler/Automation: configure and inspect an inert practice schedule.
- Desk campaigns: create and edit a practice campaign draft.
- Desk storyboards: add and revise scenes with real product pictures.

## OPEN QUESTIONS for Dave

1. **Engine boundary.** A: lesson engine beside the tour, sharing presentation helpers (**recommended**); cost: new state machine and helper extraction, preserves tour/setup semantics. B: extend the tour with lesson mode; cost: fewer modules but mode branches across Next, skips, demos, persistence and shared setup styles. If B is selected, it must implement every lesson contract above with separate state/flags; no behavioral shortcuts.
2. **Sandbox storage.** A: dedicated persistent server practice store and explicit scoped adapters (**recommended**); cost: backend isolation, fixture handlers and executor guards, strongest cross-browser resume. B: browser-only practice store/adapters using the same real renderers; cost: no backend fixture store, but extensive client routing/cache isolation and browser-local recovery; authoritative evidence means committed practice-store state, not a DOM click. B cannot share a practice run across browsers; Q3 must then use browser-local progress. Neither option can use/reset the existing `clayrune` starter.
3. **Progress owner.** A: install-shared state and one-client practice lease (**recommended**); cost: server persistence and takeover handling, continuity across remote clients, people sharing an install also share lesson completion. B: browser-local state; cost: simpler persistence, independent progress on each device/profile, no cross-browser resume. Use the same fields and verification contracts; do not invent a user account system for this pilot.

## DECISIONS (Dave, 2026-10-01, decided without Ron on his standing instruction)

1. **Engine boundary: A.** Lesson engine beside the tour, sharing extracted presentation helpers. The tour and setup flow keep their current behaviour untouched. Reversing later means folding two state machines together; nothing in the pilot blocks that.
2. **Sandbox storage: B, browser-only.** For the pilot, practice lives entirely in the client: while the practice context is active, the lesson engine routes the Floor's reads (`/api/floor` and whatever the dashboard grid/list reads to draw the practice tile) and the hire write to an in-browser practice store, using the same intercept pattern `tools/smoke/walkthrough.mjs` already uses for fixtures. Nothing practice-related is created on the server, so there is no practice project for an agent, scheduler or publish path to execute against: safety comes from construction, not from new executor guards. Any request that would leave the practice context for the real server (dispatch, send, publish) is refused client-side with a visible "Practice only" message. The real renderers still draw everything; the "never invent UI" rule holds. Why not A: a server store plus scoped adapters plus executor guards is the most expensive part of the spec, and the pilot exists to test how the lesson FEELS, which B tests just as well. Reversal: when more lessons land, swap the in-browser store for a server store behind the same adapter interface; lesson definitions do not change.
3. **Progress owner: B, browser-local** (forced by decision 2). Same fields as the Persistence section, stored in localStorage under a `learn.*` namespace. No cross-browser resume, no takeover lease; drop those requirements and their smoke assertions. Completion survives reload and lesson revisions as specified.

Everything else in this spec stands as written, including every acceptance criterion that does not depend on a server practice store or cross-client lease.
