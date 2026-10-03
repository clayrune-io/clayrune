# Learn: story scenes (Ron, 2026-10-02)

Ron's direction for lesson animation, replacing the micro-cue idea as the
headline effect: each lesson opens with a short story that acts out the
concept with characters, then the practice steps let the user do what the
story showed.

**The rule (Ron, 2026-10-02):** every scene is a real-life situation people
already know from work, so the concept sticks by familiarity. "Real life
scenarios which users can relate to and memorize because they are familiar
with these cases." Not abstract diagrams, not product tours. Each scene needs
a one-row mapping table (story to product) so it never teaches something
false.

**Outside Clayrune entirely (Ron, 2026-10-02):** the scene shows a normal
office, never Clayrune screens. HR is an animated HR department with its own
clay figure, not the Bench redrawn. "The entire scenario should relate real
life scenarios outside Clayrune." The cast is the existing clay figures
(`fig:*` sprites), which makes the characters familiar once the user meets
them again on the Floor. The link to the product is made only at the cut to
practice ("Now you do it"); the mapping tables below are for whoever builds
the scene and are not shown to the user.

## Floor scene: "We need a QA engineer"

Ron's words: "show animation of Claydo as manager talking to someone else that
they need to hire quality assurance engineer for example and then goes to HR
who hires that someone".

Draft beats (about 15 s, skippable, plays once per lesson version, Replay in
the hub):

(Ron, 2026-10-03: the manager is the one SITTING at the project desk, the
smith figure; Claydo is the teammate standing beside it. Supersedes "Claydo as
manager" below.)

1. The manager at a project desk with a teammate. Bubble: "This project
   keeps shipping bugs. We need a QA engineer."
2. Claydo walks down the hall to the HR department (an office with its own
   clay HR figure and a filing cabinet of staff folders).
3. HR looks through the folders: no QA engineer on staff. HR recruits one,
   choosing which agency to hire from (vendor) and how senior (model).
4. The new figure walks to the project and sits down. Bubble: "Hired. Nobody
   has given me a task yet."
5. Cut to practice: "Now you do it."

Mapping to the product, so the story never teaches something false:

| Story | Product |
| --- | --- |
| Manager | The seated figure at the project desk (smith) |
| HR department and its staff folders | The Bench (agent types you can hire) |
| HR recruits a QA engineer | Create agent in the character editor (Floor v2) |
| Which agency, how senior | Provider and model fields |
| Sits at the project desk | Hire onto a project; does not start a task |

## Workflows scene: "The Monday report" (draft)

1. Claydo (manager) to a teammate: "Every Monday someone pulls the numbers,
   someone writes the summary, and I sign off before it goes out."
2. The teammates pass a folder down the line: collect, write, then it lands
   on Claydo's desk with a "Waiting for you" tag. He signs.
3. "Let's write that down once, so it runs itself every Monday."
4. Cut to practice: build that same line as a workflow.

| Story | Product |
| --- | --- |
| The line of people passing the folder | Workflow steps, in order |
| Each person | The agent assigned to a step |
| Claydo signing before it goes out | Approval gate (a human-only step) |
| "Every Monday" | Schedule; only a human may turn it on |

## Constraints

- Uses the real Floor figure sprites (`fig:*` avatars), not new art styles.
- `prefers-reduced-motion` and Quiet effects: show the beats as 4 still
  panels with the same captions, advanced by the user.
- Skippable at any point; never blocks the practice steps; no audio.
- No em-dashes in user-facing copy.
- Lives in its own module (`static/js/learn-scenes/floor.js`), registered by
  the lesson; the engine plays any lesson's scene through one hook.

## Sequencing

Lands after Floor v2 (create agent, vendor, model) merges, because beats 3-4
show those steps and both touch the Floor lesson file.
