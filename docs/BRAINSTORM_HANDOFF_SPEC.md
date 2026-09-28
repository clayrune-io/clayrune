# MC-990: Brainstorm handoff from Claydo to a project

**Status:** APPROVED by Ron 2026-09-28: all five decisions accepted as recommended. This extends [MC-957](BRAINSTORM_MODE_SPEC.md); its conversation, research, SWOT, and Exploration brief contract remain binding. 

## Problem and user

Brainstorm this currently starts in a project's chat composer. A person with a raw idea and no project cannot enter a durable, research-capable Brainstorm conversation. Claydo is available outside a project, but its guide panel is a toolless text transform: `/api/guide/stream` rebuilds each prompt from at most 6 ask-mode history messages (12 for workshop modes) and caps the prompt at 8,000 or 14,000 characters. It cannot own the MC-957 exploration.

The user must be able to start with Claydo, explore the idea without naming a project, then review a proposal to create a project or add the result to an existing one. The human's action is the only event that writes to a user project or its backlog.

## 1. Entry and session home

Claydo's default ask view shows a **Brainstorm an idea** chip alongside its existing workshop chips. When a user describes an idea that needs exploration, Claydo may include a `[clayrune:brainstorm-offer]` control marker in its completed answer. The client strips the marker and renders the same chip below that answer. An explicit request to brainstorm also gets the offer. A product help question, a request for a quick answer, or a quoted idea in material Claydo is explaining must not trigger it. No text classifier dispatches Brainstorm automatically.

Clicking the chip opens an editable seed composer headed **Start Brainstorm**. It carries the triggering user message only; it does not copy Claydo's history, assistant answer, or hidden project context. A user may replace the seed or choose an installed provider/model before submitting. Submission starts a fresh `global:brainstorm` session through the normal agent dispatch path. If the character or engine is unavailable, the composer stays open with a specific error and the seed intact. Existing MC-957 entry points inside a project continue to use that project.

Projectless sessions live in one reserved, system-owned **Ideas** workspace with a real folder and project record. It appears as an Ideas conversation home, separate from user projects and their backlog, and cannot be selected as an output project. The workspace is provisioned by the application as infrastructure, not by Claydo or Brainstorm. It must use the ordinary persistent conversation, transcript, resume, provider, and agent-log paths. It must not use `_incognito`: that pseudo-project forces incognito and omits the agent log and memory needed for a durable exploration. The Ideas record must not inject any operator's other project rules or memory into an unrelated idea. Its workspace and records are user data outside the shipped source tree.

**Why this shape:** `/api/project/<id>/agent/dispatch` loads a project and refuses a missing or invalid `project_path`. Creating a user project at entry would force the user to name one before exploration and break the human-click boundary. A reserved Ideas workspace satisfies the runtime requirement without creating a user project.

## 2. Exploration brief and proposal card

Brainstorm follows MC-957 through multiple turns. A provisional or full Exploration brief remains Markdown in the Brainstorm chat. For a business or competitive idea, a full brief must contain the SWOT built across phases 1–4, and its recommendation must cite the quadrants that drove it. An early provisional brief shows each unfinished quadrant as untested and makes no unsupported verdict. A missing live research pass stays labeled provisional.

After a complete brief, or a provisional brief the user explicitly requests, Brainstorm emits a terminal `[clayrune:exploration-ready]` marker. The marker carries no project name, path, backlog text, or executable instruction. The chat renderer binds a **Use this exploration** card to that exact, completed assistant message and its source conversation ID. A later revised brief produces a new card version; older cards remain visible but clearly say a newer brief exists. The card is rebuilt from the transcript after refresh or resume, like the `mc:team` proposal card. It is a proposal surface, not an `mc:question` turn-pausing answer. A marker without a valid brief renders no write action and a visible error.

The card offers **Create a new project from this** and **Add to an existing project**. Either opens a review form with the exact brief version, proposed destination, canonical document path, and editable backlog draft. Creating a project also shows editable name, ID, domain, folder, and short description. The final button names the write action. Nothing is written when the marker appears, when the card renders, or when the review form opens. Closing the form or declining the proposal leaves the Ideas conversation available.

## 3. Human-approved transfer

On the final UI click, the system validates the source conversation and brief version, destination permissions and folder, unique project ID, safe document path, and required backlog text. It rejects agent-origin requests server-side, using the existing character/team mutation boundary as the minimum precedent; a `source: ui` value in an agent-supplied body does not confer permission. The operation must not accept an arbitrary filesystem path from the model. A repeated submission for the same brief version and destination returns the prior result rather than creating a duplicate.

The canonical transferred artifact is a Markdown file under the destination project's `docs/brainstorm/` folder, with a stable source-conversation-ID-and-brief-version filename. It contains the Exploration brief as reviewed, its version, source conversation reference, research dates and links, and whether it is provisional. This folder is distinct from `.claude/explorations/`, which is used by Clayrune's learning system. The new project's description is a short editable hypothesis, not the entire brief. An existing project's description is unchanged.

The destination backlog receives one **open** item seeded from the brief's next one-day experiment and observable learning signal. Its text links to the canonical brief and source conversation; it does not paste the entire analysis into the backlog item. For a pause/stop recommendation, the draft states the specific observation that would justify revisiting the surviving variant. The user can edit that task before clicking. The transfer returns the project, document, and item handles and opens the destination project on success. If any part fails, the UI reports which part failed and does not claim a completed handoff; the implementation must prevent a half-created project, orphan document, or duplicate item (transaction or explicit rollback with a recoverable failure record).

The Brainstorm transcript remains under its original provider and session identity: Ideas for a projectless start, or the source project for an MC-957 in-project start. No destination conversation pretends to resume it, and no full transcript is copied into the target project. The destination document and backlog item link back to that source conversation; deleting or archiving either destination artifact does not delete the original chat. The brief is portable Markdown, so the handoff does not depend on which vendor ran Brainstorm.

## Boundaries

Out of scope: running Brainstorm inside Claydo's guide stream; automatic project, file, or backlog creation by an agent; choosing a destination from model output; copying all Claydo or Brainstorm turns into a new project; silently changing a resumed chat's persona or provider; autonomous experiments or scheduled follow-up. The handoff changes where an approved brief is saved, not MC-957's method of reaching its recommendation.

## Acceptance criteria

1. With no user project, the Claydo chip opens an editable seed and dispatches a durable Brainstorm chat in Ideas only after submit; refresh and resume preserve its persona, brief, transcript, and provider.
2. An idea request gets an offer; an ordinary product-help question and quoted idea do not. Neither an offer nor a completed brief causes a project, document, or backlog write.
3. Brainstorm can research with the selected installed provider. If research is unavailable, the brief says so and the card retains that limitation; no Claude-specific runtime path is required.
4. A complete business brief includes the mandatory SWOT and a recommendation derived from it. A provisional brief is labeled provisional in both chat and destination document.
5. Both card actions show a review form. The final click creates exactly one canonical brief file and one seeded backlog item in the chosen user project; the create path also creates exactly one project. An agent-origin call cannot perform that write.
6. Duplicate clicks return the same result. A failed validation or mid-transfer error cannot leave an unreported partial result. A revised brief creates a distinct version and does not silently overwrite an earlier transfer.
7. After either handoff, the source transcript remains reopenable and linked from the destination; an existing project's description and unrelated conversations remain intact.

## Decisions (accepted as recommended by Ron, 2026-09-28)

1. **Projectless home:** A reserved Ideas workspace (**recommend**) or a user project created before exploration. The first preserves the user-project creation gate.
2. **Claydo entry:** An always-visible chip plus a context offer (**recommend**) or a context offer alone. The chip makes the path discoverable without requiring precise phrasing.
3. **Transfer artifact:** Canonical Markdown file plus concise backlog item (**recommend**) or backlog text alone. The file preserves evidence without turning the backlog into a transcript.
4. **Card protocol:** A lightweight `exploration-ready` marker rendered as a persistent proposal card (**recommend**) or a generic `mc:question` form. The card keeps action separate from a paused conversational answer.
5. **Transcript:** Keep it in the source conversation and link to it (**recommend**) or copy all turns into the destination. The brief carries the decision; the original chat remains the audit trail.
