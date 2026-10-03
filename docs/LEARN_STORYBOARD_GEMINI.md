# Learn story scenes: storyboard for Gemini (Ron, 2026-10-03)

Production storyboard for the two scenes specified in
`docs/LEARN_STORY_SCENES.md`. Ron generates the clips with Gemini; Clayrune
plays them in the Learn intro and overlays the captions itself.

## How to use this

- **One clip per shot.** Each shot below is 4 to 8 seconds, so it fits a
  single Gemini video generation. Paste the STYLE block, then the CAST lines
  for the characters in that shot, then the shot prompt.
- **Attach the reference images** listed in CAST to every shot that uses
  that character, so the figures stay on model from shot to shot.
- **No text in the video.** Speech bubbles and captions are added by
  Clayrune on top (AI video garbles lettering, and we need to translate and
  edit copy without regenerating). Leave empty space where a bubble goes,
  marked "bubble space" below.
- **No audio.**
- **Frame:** 16:9, 1280x720 or larger. Keep the characters inside the
  centre 9:16 strip, because phones crop to it.
- **Keep the last frame of every shot.** It doubles as the still panel for
  reduced motion and Quiet effects.

## STYLE (paste first, every shot)

> Stop-motion claymation look. Small rounded clay figures with faceted,
> hand-sculpted surfaces, soft matte finish, subtle fingerprint texture.
> Miniature physical set built from clay, card and wood, like a tabletop
> diorama. Warm soft studio lighting, gentle shadows, shallow depth of field.
> Muted earthy palette: terracotta, cream, sage, slate grey, warm wood.
> Gentle, slightly bouncy stop-motion movement at about 12 frames per second.
> A normal office: desks, a corridor, doors, filing cabinets, a coffee mug.
> No computers with readable screens, no logos, no written words anywhere,
> no text, no captions, no signs with letters.

## CAST

Reference images are in the repo under `assets/avatars/`.

| Role | Reference | Look |
| --- | --- | --- |
| Manager | `claydo.webp` | Terracotta clay figure, long hair, holding a clay gear with a small sparkle |
| Teammate (developer) | `smith.webp` | Dark red-brown figure, leather apron, hammer, frowning |
| HR officer | `librarian.webp` | Sage-green figure, curly hair, round glasses, pushes a wooden cart of folders |
| New QA engineer | `scholar.webp` | Slate-grey figure, headband, round glasses, holds an open book, focused look |
| Numbers collector | `courier.webp` | Teal figure, cap, satchel, carries an envelope |
| Report writer | `scribe.webp` | Grey figure, sleepy eyes, holds a quill and a paper scroll |

---

## Scene 1: "We need a QA engineer" (Floor lesson, about 26 s)

| Shot | Length | Bubble (overlaid by Clayrune) |
| --- | --- | --- |
| 1A | 6 s | Manager: "This project keeps shipping bugs." |
| 1B | 4 s | Manager: "We need a QA engineer." |
| 2 | 5 s | (none) |
| 3A | 5 s | HR: "Nobody like that on staff yet." |
| 3B | 6 s | (none; caption: "Which agency? How senior?") |
| 4 | 6 s | New hire: "Hired. Nobody has given me a task yet." |
| 5 | 2 s | Caption: "Now you do it." |

**1A.** Wide shot of a small clay office, one project desk with a few
papers. The teammate (smith) sits at the desk, holding up a cracked clay
gadget that sparks and falls apart in his hands; he frowns. The manager
(claydo) walks in from the left, stops beside the desk, looks at the broken
gadget and sighs. Static camera. Bubble space above the manager's head.

**1B.** Medium shot of the manager turning toward the camera and raising one
finger, as if deciding something. The teammate nods behind her. Slow push in.
Bubble space upper left.

**2.** The manager walks out of the office into a corridor and along it,
camera tracking alongside. She stops at a door with a small clay emblem of a
folder on it (no letters) and knocks. Ends as the door opens.

**3A.** Inside the HR office: shelves and a filing cabinet. The HR officer
(librarian) looks up, pushes her cart of folders forward, and flips through
the folders one by one, then shakes her head slowly. The manager waits. Bubble
space above HR.

**3B.** Close shot of the HR officer's desk. She lays out two small clay
cards from two different agencies, picks one, then sets a small three-step
clay slider (low, middle, high) to the high notch, and stamps a fresh empty
folder. Caption space along the bottom.

**4.** Back in the project office. The new QA engineer (scholar) walks in
through the door, opens the book, walks to an empty chair at the project desk
next to the teammate, sits down, and looks up at the camera expectantly with
the book open on the desk. The teammate gives a small wave. Bubble space above
the new hire.

**5.** Hold on the final frame of shot 4, then a slow fade toward the cream
background colour. (Clayrune can build this from 4's last frame; generate only
if a separate clip looks better.)

## Scene 2: "The Monday report" (Workflows lesson, about 24 s)

| Shot | Length | Bubble (overlaid by Clayrune) |
| --- | --- | --- |
| 1 | 7 s | Manager: "Every Monday: numbers, then a summary, then I sign off." |
| 2A | 5 s | (none) |
| 2B | 5 s | Tag on the folder: "Waiting for you" |
| 2C | 4 s | (none) |
| 3 | 5 s | Manager: "Let's write that down once, so it runs itself." |
| 4 | 2 s | Caption: "Now you do it." |

**1.** Small clay office with three desks in a row. A wall calendar with a
single page and a coloured mark on one day (no letters or numbers). The
manager (claydo) stands at the front, gesturing along the row of desks. The
numbers collector (courier) and the report writer (scribe) sit at their
desks. Bubble space above the manager.

**2A.** The collector stacks small clay bar-chart blocks into a folder,
closes it, and slides it along the desk to the writer. Camera pans with the
folder.

**2B.** The writer opens the folder, writes on the scroll with the quill,
tucks the scroll inside, and carries the folder to the manager's desk. He
hangs a small blank paper tag on the folder. Leave the tag blank, Clayrune
writes on it.

**2C.** Close shot: the manager picks up the folder, reads, nods, and presses
a round clay seal onto it. A small satisfying squash of clay.

**3.** Medium shot: the manager takes a blank sheet and draws three boxes
joined by arrows (shapes only, no writing), pins it to the wall beside the
calendar, and taps it. The two teammates lean in to look. Bubble space above.

**4.** Hold on the pinned sheet and fade toward cream, as in Scene 1 shot 5.

---

## What we need back

- One MP4 per shot, named `<scene>-<shot>.mp4` (for example `qa-1a.mp4`,
  `monday-2b.mp4`), dropped into `data/uploads/` or this chat.
- If a figure drifts off model, regenerate that shot rather than patching.
  The figures must read as the same characters users later meet on the Floor.

## Mapping (for the builder, not shown to users)

See the tables in `docs/LEARN_STORY_SCENES.md`. In short: HR's folders are the
Bench, the two agency cards are the vendor choice, the three-notch slider is
the model choice, sitting down without a task is hiring. The folder passed
down the row is a workflow's steps, the seal is the human approval gate, the
pinned sheet is saving the workflow.
