# Learn story scenes: storyboard for Gemini (Ron, 2026-10-03)

Production storyboard for the Floor scene specified in
`docs/LEARN_STORY_SCENES.md` (one scene for now, Ron 2026-10-03; the
Workflows scene waits). Ron generates the clips with Gemini; Clayrune
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

> Match the attached character reference images exactly: the same rendered
> 3D characters with smooth, faceted low-poly surfaces, matte finish, simple
> dot eyes and the same proportions and colours. They are our existing cast,
> not new designs. Do not turn them into clay or plasticine, no fingerprints,
> no stop-motion jitter. Smooth, gentle animation.
> The set is a normal office built in the same faceted low-poly style:
> desks, a corridor, doors, filing cabinets, a coffee mug. Soft warm studio
> lighting, gentle shadows, muted earthy palette (terracotta, cream, sage,
> slate grey, warm wood). No computers with readable screens, no logos, no
> written words anywhere, no text, no captions, no signs with letters.

## CAST

Ron has the reference drawings; the same figures are in the repo under
`assets/avatars/`.

| Role | Reference | Look |
| --- | --- | --- |
| Manager (sits at the project desk) | `smith.webp` | Dark red-brown figure, leather apron, frowning |
| Teammate | `claydo.webp` | Terracotta figure, long hair, holding a gear with a small sparkle |
| HR officer | `librarian.webp` | Sage-green figure, curly hair, round glasses, pushes a wooden cart of folders |
| New QA engineer | `scholar.webp` | Slate-grey figure, headband, round glasses, holds an open book, focused look |

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

**1A.** Wide shot of a small office, one project desk with a few
papers. The manager (smith) sits at the desk, holding up a cracked
gadget that sparks and crumbles in his hands; he frowns and sighs. The
teammate (claydo) stands beside the desk holding her gear and tilts her head
at the broken gadget. Static camera. Bubble space above the manager's head.
(Ron's reference still: `data/uploads/agent_b73f6499cc.png`.)

**1B.** Medium shot of the manager, still seated, putting the broken gadget
down and raising one finger, as if deciding something. The teammate nods.
Slow push in. Bubble space above the manager.

**2.** The manager gets up from the desk, walks out of the office into a
corridor and along it, camera tracking alongside. He stops at a door with a small emblem of a
folder on it (no letters) and knocks. Ends as the door opens.

**3A.** Inside the HR office: shelves and a filing cabinet. The HR officer
(librarian) looks up, pushes her cart of folders forward, and flips through
the folders one by one, then shakes her head slowly. The manager waits. Bubble
space above HR.

**3B.** Close shot of the HR officer's desk. She lays out two small
cards from two different agencies, picks one, then sets a small three-step
slider (low, middle, high) to the high notch, and stamps a fresh empty
folder. Caption space along the bottom.

**4.** Back in the project office. The new QA engineer (scholar) walks in
through the door, opens the book, walks to an empty chair at the project desk
next to the manager, sits down, and looks up at the camera expectantly with
the book open on the desk. The teammate gives a small wave. Bubble space above
the new hire.

**5.** Hold on the final frame of shot 4, then a slow fade toward the cream
background colour. (Clayrune can build this from 4's last frame; generate only
if a separate clip looks better.)

---

## What we need back

- One MP4 per shot, named `<scene>-<shot>.mp4` (for example `qa-1a.mp4`,
  `qa-3b.mp4`), dropped into `data/uploads/` or this chat.
- If a figure drifts off model, regenerate that shot rather than patching.
  The figures must read as the same characters users later meet on the Floor.

## Shots received

| Shot | File | Accepted | Notes |
| --- | --- | --- | --- |
| 1A | `data/uploads/learn_scenes/qa-1a.mp4` | Ron, 2026-10-03 | Gemini, 1280x720, 10 s with an audio track. The action ends at about 5 s and the rest is a still hold, so the builder trims it to the 6 s slot. |

## Mapping (for the builder, not shown to users)

See the tables in `docs/LEARN_STORY_SCENES.md`. In short: HR's folders are the
Bench, the two agency cards are the vendor choice, the three-notch slider is
the model choice, sitting down without a task is hiring.
