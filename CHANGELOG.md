# Clayrune — Changelog

> Renamed from "Mission Control" 2026-05-01; the GitHub repo was renamed
> `mission-control` → `clayrune` 2026-06-09 (in-repo URLs updated; GitHub
> redirects the old path). Backend identifiers (`mc_remote`, `MC_*` env vars,
> Cloud Run service, keystore namespace) intentionally remain "mission-control"
> to avoid breaking existing installs.

## [2026-08-29] — Clayrune starts itself at boot, before you log in

Ron: *"setting Clayrune to auto load when the PC restart or reboot ... This is
mainly for cases where I'm remote and do not have physical access to the PC."*

`tools/clayrune-autostart.ps1` already started Clayrune at logon. Its header
explained why logon and not boot: an at-startup task must run as SYSTEM or store
a password, and SYSTEM has a different profile — no `~/.clayrune`, no Claude CLI
auth — so Clayrune would boot and fail at the first agent dispatch. It closed by
handing the remaining gap to Windows' "automatically sign in after an update
restart".

That gap is the whole case for remote access: a 3am Windows Update reboot that
stops at the lock screen leaves the phone with nothing to talk to. The reasoning
had missed a third option — **LogonType S4U**, which runs as the real user with
no password stored anywhere. `-Install -AtBoot` now registers the task that way.

S4U carries one real risk, so it was measured rather than assumed. An S4U logon
cannot always decrypt DPAPI-protected data, and the remote-access device identity
lives in Windows Credential Manager, which is DPAPI-backed — that failure would
be the worst shape available: Clayrune up, tunnel dead, reachable only from the
machine you are not sitting at. A probe task read all six
`mission-control-remote` keys (WinVaultKeyring), and a full server booted and
served in ~4s under a real S4U task. Both the skip path and the start path were
then verified against the live install.

Three things this shook out:

**`Resolve-Port` was reading a file that does not exist.** It looked for
`data\config.json`; `server.py` resolves `CONFIG_PATH = _DATA_ROOT / 'config.json'`
— the repo root. So the port probe always fell back to 5199. Harmless while the
port *is* 5199, and a split-brain the day it isn't: the probe would find nothing
on 5199 and start a second server on the real port. It now reads the root file,
with `data\` kept as a fallback.

**The server's output was going nowhere.** `Start-Process pythonw` with no
redirect meant a server that started and died left only "port never answered"
and no cause — after an unattended reboot, that log is the only thing readable
remotely. Output now lands in `data/logs/clayrune-autostart-server.log`.
Deliberately not `clayrune.log`: `start.bat` holds that one open with a share
mode that denies a second writer, and when cmd cannot open a `>>` target it
prints an error, skips the command, and leaves `ERRORLEVEL` at 0 — a launcher
sharing that file reports success while starting nothing.

**`_check_port_conflict` does not catch a second instance on Windows (MC-908).**
With a live Clayrune on 5199, a second one booted all the way through and
announced "Clayrune running at http://localhost:5199" — no conflict banner, no
exit 2. The guard bind-tests `0.0.0.0`, and that bind succeeds against a
Werkzeug listener holding `SO_REUSEADDR`. Filed rather than fixed here; the
autostart script does not depend on it, because it connect-probes instead, which
answers the question a bind test cannot: is something already serving?

## [2026-08-25h] — Claydo survives a refresh

Ron: *"Claydo interaction box does not survive screen refresh."*

`mc_open_modals` deliberately skips every `__`-prefixed modal as transient.
That is right for a terminal or a hivemind view and wrong for a conversation,
which is exactly the thing you were part-way through: an accidental F5 cost the
whole exchange, and in a builder mode that is an interview you then have to redo.

Claydo keeps its own snapshot now: the mode (a builder mode has its own greeting
and header), the transcript, whether it was minimized, and whatever was
half-typed in the box. Saved after each turn and on `beforeunload` +
`pagehide` — minimize, an unsent draft and a close all happen without a turn,
and only the unload hooks catch those. Closed is recorded as a decision, so a
dismissed conversation is not resurrected.

The ready-card kind rides along on the assistant message. The stored text has
its markers stripped on purpose (so Claydo does not re-emit its own highlights
next turn), which also means a restore cannot re-derive "this reply had a draft
to save" from the text — and a draft you can read but no longer save would be
worse than no restore at all.

**The smoke guard earned its keep immediately.** `openClaydo` and
`_claydoResetConversation` both clear `_claydoHistory`, and the former saves on
the way out, so a restore left an *empty* session in storage. One reload looked
perfect; the second lost everything. Verified against the real app end to end,
then caught by the guard's "did the restored session stay saved?" check.

## [2026-08-25g] — hover a face, see the whole figure

The cast went from 9 figures to 25, and at the picker's 38px a beekeeper's
smoker, an astronomer's star chart and a courier's satchel are all the same
brown smudge. Picking a face was guessing.

Hovering a chip now lifts the full 256px render out of the row, with the
figure's name under it.

It is a **floating node**, not a scale on the chip. The picker lives inside
`.pe-scroll`, which clips its overflow, so anything that grows in place is cut
off by its own container. `pointer-events: none` for the same class of reason a
hover panel usually flickers: one that can take the pointer steals the hover
that summoned it. It flips above or below the chip depending on room, clamps to
the viewport, and hides on scroll so a face cannot end up floating over an
unrelated field.

Smoke guards it against the real editor: 4 chips listed, preview shown at full
size, more than twice the chip, **not** a descendant of `.pe-scroll`, fully
on-screen, and gone on mouseleave.

## [2026-08-25f] — the bench is the roster, not the free half

Ron: *"when an agent is working in a project you cannot edit its details… we
need to be able and launch Dave on more than one project."*

One cause under both. The bench was defined as **"types with nothing running
anywhere"**, so the moment Dave started working he vanished from the board — and
with him the only two things you can do to a type, because both live on a bench
card: **put it in a room**, and the **✎** that opens the persona editor. Nothing
said he was gone or why.

That filter also contradicted the view's own premise. The module docstring says
one character appears in several rooms at once *because it is running in several
places* — "that is the fact the header pill cannot express and the reason this
view exists." The bench was hiding exactly that case.

- **The bench is now the whole roster.** Where a type is already working is a
  line **on** the card (`already in Mission Control`), not a filter. Free types
  still sort first, because "who can I put on this?" is the question the bench
  is read with — a busy one is the weaker answer, not an invalid one.
- **A room it is already in stays pickable**, marked `· again` and dashed, so a
  second figure there reads as a decision rather than a misclick.
- **The pencil rides with the type, wherever the type appears** — figures in
  rooms have it now, so a working agent is editable from where you can see it.

Smoke guards both: two bench pencils and two figure pencils, each reaching the
shared persona editor with the right persona, and a busy card naming its room.

## [2026-08-25e] — a resumed conversation keeps its agent

Ron: *"I resumed the conversation which started with Dave… but it brought up
Vector."*

The persona picker is hidden on a resume, so `character` arrives **empty** at
dispatch and `_resolve_character` fell through to the **project default**. That
is not a cosmetic pill — the same value builds the system prompt, so the
resumed process was genuinely told it was somebody else.

The spawn persona was already written to every `agent_log` entry, *so the header
pill would survive a restart*. Nothing read it back. `_prior_character` does, and
returns three distinct answers because they mean three different things:

- `"scope:name"` — restore it.
- `''` — that conversation deliberately ran with **no** persona. The same bug
  mirrored: a plain chat must not acquire one because the project default
  changed since.
- `None` — no record; leave normal precedence alone.

Verified against the real log: `212c2fe3` → `global:dave`.

## [2026-08-25e2] — the faces come back, and the type is readable

Every figure in a room drew a broken image. `_AVATAR_CHARS = 8` in
`floor_routes` was a hand-copied "mirror" of `characters.MAX_AVATAR_LEN`; when
that grew to 40 to hold `fig:<name>`, the copy stayed at 8 and cut `fig:guard`
to `fig:guar` on the way out of `/api/floor` — a face that never resolved, with
the correct value sitting untouched in `dave.md`.

Two caps cannot mirror each other by comment, so now there is one rule in one
place: `characters.clean_avatar` keeps the **short** cap for an emoji (what
stops the field becoming a second name) and the long one only for `fig:` — the
shape that is checkable, since a bogus figure name draws nothing rather than a
sentence.

Also: the type chip was the faintest thing on a card at `--text-faint`, under
3:1 on cream, and it is what you scan for. It is a real chip now.

## [2026-08-25d] — the Floor scrolls

Ron, on a phone: *"trying to open the floor screen on mobile but I cannot scroll
down."*

`.modal-content` is a flex column with `overflow: hidden` **by design** — every
surface owns its own scroll area — and the Floor never had one. Measured at a
390×780 viewport: a 728px window holding **1,339px of board**, `overflow-y:
hidden`, 611px unreachable and nothing to swipe. Desktop hid it completely,
because there the board happened to fit.

`#floor-body` is now the scroller (`flex: 1 1 auto; min-height: 0; overflow-y:
auto`), verified by driving `scrollTop` rather than by reading a computed style:
0 → 400 with 588px of travel. Side padding also drops from 52px to 24px below
960px, where 52px is 13% of the screen.

The smoke guard now checks both halves — that the body is a scroll area, and
that `.modal-content` is not overflowing — because a surface that forgets its
scroller looks perfect on a desktop and truncates silently on a phone.

## [2026-08-25c] — the persona editor: reach Save, and resize the panel

Ron: *"when I edit the agent, there is no save button. So if I click anywhere
else to close the screen, nothing is saved… also cannot increase or change the
size of that edit popup."*

Three causes, and the resize one was worse than it looked.

**The action bar fell off the bottom.** The panel is a fixed height with
`overflow: hidden`, and its children simply flow past. Adding the face picker
and the skills row pushed Delete / Cancel / **Save changes** past the end, where
nothing could scroll to them. The fields now live in a `.pe-scroll` between a
pinned title and a pinned action bar.

**The resize handles were clipped.** `makeResizable` places them at negative
offsets so they straddle the border — and `overflow: hidden` on the same element
removed them entirely.

**And they were anchored to the wrong element.** `.claydo-save-inner` had no
`position`, so absolutely-positioned handles resolved against the nearest
positioned ancestor: the full-screen backdrop. Every grab target sat at the edge
of the SCREEN, nowhere near the dialog, which is why dragging its corner did
nothing at all. The panel is `position: relative` now, and a check confirms the
south-east handle lands on its own bottom-right corner.

**Closing the backdrop no longer discards silently.** Ron lost an edit to
exactly that: with Save off-screen, clicking away looked like the only exit. A
dirty check compares the five editable fields against their opening values and
asks before throwing the work away — clean edits still close on one click.

The panel is also a little larger by default (620×680 from 560×560), and the
instructions textarea can be dragged vertically on its own.

## [2026-08-25b] — the faces get a column, and 56px in it

Ron: *"the characters are still too small, we should be comfortable to increase
them a bit."*

The number was 40, but the number was not the constraint. The face sat **inline
in the name row**, so growing it grew that row's line-height and pushed the name
sideways — which is why 40 felt like a ceiling when the real ceiling was the
layout. It has a column of its own now: a left column on a figure card, a
float-left on a bench card so the description still wraps to full width under
it. The card gets no taller and the face gets 16px bigger.

Also fixed while looking at it: the empty slot. A dotted-circle glyph read as
"unassigned" at 18px; at 56 it is a speck floating in an empty box, which reads
as broken. It fills its slot as a dashed ring instead — clearly a placeholder,
clearly deliberate, and the same shape as the figure that will replace it.

## [2026-08-25] — an agent can wear a figure, not just an emoji

Nine clay characters, generated from a style brief built off Claydo, cut and
keyed by `tools/avatars/slice_sheet.py`, and now reachable from the app.

**One field, two shapes.** `avatar` is still the single "face" fact; a `fig:`
prefix says it names a figure in `assets/avatars/` rather than being an emoji.
Two separate keys would have doubled the precedence logic at every site that
resolves a face — session label, then character, then default — and each of
those sites would have had to agree about which kind wins.

**A bare name, never a path.** `fig:wizard` resolves through `/api/avatars/
<name>`, which validates against the directory listing. An absolute path would
bake this machine's checkout location into a character file that is meant to be
portable, and put an arbitrary filesystem string on a public API. Traversal is a
404, not a read.

**`MAX_AVATAR_LEN` was 8** — sized for one emoji including a ZWJ sequence — so
it silently truncated `fig:wizard` to `fig:wiza` and produced a face that simply
never resolved. Caught live: the first assignment through the running server
wrote `fig:scho` for all four personas before the raised cap was loaded.

**One renderer, in `render-core.js`.** Three copies of a five-line helper is how
the surfaces end up disagreeing about what an avatar is — which already happened
once, when the chat header showed a generic mask while the Floor showed the
persona's own face.

The Floor's face slot goes 18px → **40px** (Ron: *"on the floor area we can show
them in bigger size"*). It stays a FIXED box so every card's name still starts at
the same x; only the size changed, and the smoke guard now pins that 40 as well
as checking that a `fig:` avatar renders as an `<img>` and an emoji does not.

The persona editor gains a row of the nine figures beside the emoji field,
fetched from `/api/avatars` rather than hardcoded — a hardcoded list and the
directory would disagree the first time anyone drops a new file in.

**No portrait crop.** One was built and dropped: sweeping the crop fraction
showed 78% is barely distinguishable from the full figure, and tighter crops cut
through the mouth — these are blob figures whose faces sit lower than a human
head. The tightest usable setting traded a slightly bigger head for the lute,
the anvil and the spear, and the props carry more identity than the face does.

## [2026-08-24p] — a live session picks up config that changed under it

Ron, straight after the relocation: *"we need to guarantee that when invoking
Dave for a project another time it will grab any changes that happened."* He was
right, and it was broader than skills.

`_respawn_sysprompt_args` preferred `session['_system_prompt']` — a blob stashed
**once at spawn** — and rebuilt only when the stash was missing. So a long-lived
chat froze its rules, its memory index, its positions, its roster and its skill
list at the moment it started, permanently. The session writing this changelog
is still listing the 22 skills that moved projects an hour ago.

**The stash exists for a real reason**, stated in its own comment: byte-identical
content keeps the resumed prefix cache-friendly, and rebuilding every turn throws
that away every turn for nothing. So the fix is not "always rebuild" — it is
**invalidate on change**. `_context_fingerprint` stat-stamps everything the
context is built from (both rules files, the memory dir including positions and
continuity, the global and project skills dirs, both agents dirs, and the config
keys that reach the prompt) and the stash is kept while that holds, dropped the
moment it moves. mtime+size rather than content hashes deliberately: this runs
on every respawn, and stat-ing ~60 paths is microseconds where reading a 1 MB
vault is not.

**Two bugs fell out of making the rebuild trustworthy**, both invisible until
now because the stash was almost always present so the rebuild path never really
ran:

- **It rebuilt with no character.** A resumed persona chat that ever fell through
  would have silently lost its persona mid-conversation.
- **It rebuilt with no session id**, so the agent lost the ability to name its
  own figure on the Floor.

Both now come off the session, with the persona's body re-read from disk — which
also means an **edited persona takes effect on the next turn** instead of never.

Failure posture unchanged and now tested: a fingerprint failure reuses the
stash, and a failed rebuild falls back to the stale context rather than sending
a turn with no rules and no memory at all. Two stubs in
`test_resume_sysprompt.py` had fixed signatures that the new kwargs made raise —
they take `**kw` now, so they fail for their own reasons rather than for this.

## [2026-08-24o] — skills belong to a project, and a type declares its own

Two halves of one answer to *"connect agent type / character with a set of
skills, or leave them at project level?"*

**The bulk of it was a misfiling, not a missing feature.** Measured: 63 global
skills cost **~3,258 tokens in every prompt on every project**, and 22 of them
were the job-hunting toolkit — `resume-ats-optimizer`, `salary-negotiation-prep`,
`cover-letter-generator` and the rest — loaded into sessions about the Floor, a
trading scanner and a 3D modelling project alike. `skills.py` has had
`project_skills_dir()` and a `project` scope the whole time; nobody had moved
them. Relocated to `find_ron_a_job/.claude/skills/`: global is now 41 skills
(~2,670 tokens), **~590 tokens back on every prompt of every other project**,
and the job project still sees all 22. The mover names each skill explicitly
rather than pattern-matching a prefix, and writes a manifest so it reverses.

**Then the character half, which is a declaration and never a gate.** Claude
Code decides which skills it exposes and nothing here narrows that — saying
otherwise in a prompt would claim an enforcement the system does not have, and
an agent that believes it cannot reach a skill it can reach is worse off than
one told nothing. What `skills:` on a character buys is two real things: the
agent is told which of the available skills are ITS OWN (a list of sixty says
nothing about who you are; three named ones do), and a bench card can show what
a type can reach for rather than only what it is for — which is the
"abilities are not observable enough" gap from two rounds ago.

Stored comma-separated, not as a YAML list, for the reason the frontmatter
already documents: the minimal parser has no list type and hands `['a','b']`
back as a string that then iterates character by character. `clean_skills`
accepts either, because the editor sends a string and an API caller sends a
list, and rejecting one would only move the bug to the caller. Same three-state
contract as `agent_name` and `avatar` — absent carries forward, empty clears —
so a plain description edit cannot wipe a toolkit the editor never showed.

## [2026-08-24n] — the running agent looks like one, and every type has a face

Ron: *"emphasize the colors of the running agent inside the project… make all
agents tiles the same size, and add the option to edit them… Avatars are a must.
Right now none of them has one."*

**State now owns the figure card.** It was carried entirely by a 13px dot, so a
figure mid-turn looked identical to one that had been idle for twenty hours. The
card's left edge, its background tint and a new uppercase word all carry it:
green for working, amber for needs-you, plain for idle.

**Bench tiles are one shape.** They were a wrapping flex row, and flex items
size to their content — four types with four description lengths made four
different tiles. A grid of `1fr` columns with a stretched row fixes the width,
`-webkit-line-clamp: 3` fixes the text, and `margin-top: auto` on the footer
puts every card's last line at the same y.

**Every bench card has a pencil**, opening the same persona editor the composer
uses — not a Floor-flavoured second one. It takes no project id, because a
global type belongs to no room, and `reloadCharacters` is now guarded for that
path rather than refetching a picker for `null`.

**And the editor can set a face**, which is why none of them had one: the route
accepted `avatar` and an agent could set its own, but no human-facing surface
ever asked. There is a text box and a row of ten one-click picks — typing an
emoji on a desktop keyboard is the entire reason a field like this stays empty
forever. Seeded the four existing types through the API: 🦉 Fenn, ✍️ Marlow,
🔍 Quill, 📈 market-analyst-investor.

**The stylesheet block was rewritten whole.** Five rounds of surgical patching
had left four duplicate selectors (`.fl-fig:hover`, `.fl-bench-list`,
`.fl-bench-main`, `.fl-bench-foot .fl-cta`) where the second silently won —
a bug factory, since the next edit lands on whichever copy you happen to find
and it may be the one that loses. 75 rules, no duplicates, checked by the patch
that wrote them.

## [2026-08-24m] — the board reads as a board

Ron: *"definitely improvement… but I think it still looks bit dull."* The
screenshots showed five bugs behind the dullness, not a palette problem.

**The quiet list was never collapsing.** `.fl-quiet-list { display: flex }`
beats the `hidden` attribute — `hidden` works through a UA `display: none`, and
an author rule wins at the same specificity. So the arrow said collapsed while
twenty project chips said otherwise, which is exactly the "sits there in mid
screen" complaint from the round before: moving the section to the bottom did
not help because it was never closing. Toggled by class now.

**And the smoke guard was passing the whole time**, because it asserted
`:not([hidden])` — the mechanism — instead of what rendered. It reads
`getComputedStyle().display` now. A test that checks the attribute you set
cannot see a rule that overrides it.

**A long type name broke its card.** `market-analyst-investor` has no
`agent_name`, so the file name IS the display name: it wrapped to three lines
and shoved the type tag into a tall box beside it. The name truncates on one
line now, the tag never shrinks below its own text, and a type whose display
name equals its file name stops printing the same string twice.

**The engine string wrapped mid-token** — `claude-sonnet-` / `5 · high` — and
dragged "Put in a room →" into two lines with it. A model id is one token: one
line, ellipsis, never a break.

**Descriptions and tasks were cut mid-word.** "real design probl", "a rough idea
turned i", "at the very top ti". `_clip` cuts on a word boundary and marks it;
a card that ends mid-word reads as broken rather than abbreviated, and the
reader stops to work out whether something is missing.

Then the actual dullness:

- **A lone figure looked lost.** `max-width: 340px` left two-thirds of a wide
  room blank, so one agent working read as a mostly-empty box.
- **Every type looked identical.** A stable hue derived from the character name
  tints each bench card's edge — same colour on every machine and after every
  restart, because it is an identity cue and not a palette.
- **A room wears its colour where the eye lands**, as a swatch beside the name,
  not only on the far-left edge.
- **The working dot pulses.** The only thing on the board that is actually
  happening, so the only thing that moves — and it respects
  `prefers-reduced-motion`.
- **An empty board is a composed state**, not a grey sentence: it says nobody is
  working and points at the bench, which is the one thing you can do from there.

## [2026-08-24l] — the persona is visible where you actually need it

Ron used the Floor and reported six things. Two were defects, not styling.

**Placing a type set the persona on a screen he was not looking at.**
`setComposerCharacter` sets `pendingDispatchCharacter[pid]`, which applies to
the next dispatch from the +New composer — but `openProjectModal` lands on
whatever chat was already open. So the only evidence was a toast, and, in his
words, *"I don't know if the chat I already have on is with the new persona or
continue with the one from before."* Placing now opens the **+New chat screen**,
where the persona row is on display with the choice in it. Seeing beats being
told, and there is no longer a question about which chat it applied to.

**The chat header wore a generic mask.** Every persona rendered 🎭, which
answers "there is a persona" but not "which one" — the question you actually
have three messages in. `character_meta` now carries the persona's `avatar`, so
the badge shows the same face the Floor draws. One persona, one face, wherever
it appears.

The four presentation problems, each taken literally:

- **"the 19 quiet projects sits there in mid screen and looks very confusing"** —
  it sat between the rooms and the bench, so the eye hit a collapsed grey count
  on its way to the thing it wanted. Quiet is the least important section on the
  page and is now last, under its own rule, reading "N projects with nobody in
  them" rather than a bare count.
- **"the entire pane lacks some color distinction"** — every project already
  carries `modal_color`, the endpoint has always sent it, and the board was
  throwing it away. A room wears it on its left edge now, which is the same
  identity cue the project modal uses. A room waiting on a human also gets a
  warm tint and a "needs you" flag.
- **"the hire button is too small"** — it was a 10px dashed ghost floated right
  of a section label. It is the only creative act on the page and now looks like
  a button: "+ Hire someone new".
- **"it was not even clear that I could click on agent name to launch it"** — a
  hover border is not an affordance on a card that is already bordered. Cards
  say what they do in words now ("Open this chat →", "Put in a room →"), and the
  hover just confirms it.
- **"agent abilities and definitions are not observable enough"** — a bench card
  led with its engine string and buried the one sentence describing what the
  type is FOR in 10px grey. Reversed: the description is the card, the engine is
  a footnote, and a type with no description says so rather than showing blank.

## [2026-08-24k] — the Floor can put someone in a room, and hire

Ron: *"right now it is only a view and nothing more."* Two actions, both routed
to machinery that already existed.

**A bench card places a type in a room and does not dispatch.** Click a type,
pick a project, and that chat opens with the persona already selected. A bench
click knows WHO but not WHAT — inventing a task so the button feels decisive is
how an agent ends up doing something nobody asked for. Ron types the task;
everything before it is done for him.

**"+ Hire" opens Claydo's character workshop.** That has existed since the
Prompt Builder — a brief, a fenced draft, a `[clayrune:character-ready]` marker
and a "Save character…" panel — and was simply unreachable from the Floor, with
nothing hinting it existed. Not a second builder: two creation flows would
disagree about what a character is within a week.

Still deliberately absent: stop/kill from the Floor. It is a read surface with
one write. Killing work from a board you skim is how you kill the wrong session,
and the chat is one click away with the control already on it.

**A correction, and the roster block it produced.** I had said nothing tells an
agent that the hired types exist. Wrong, and checking cost thirty seconds: a
character IS a Claude Code subagent file in `~/.claude/agents/`, so the harness
already lists every one as an available agent type in every prompt.

The two gaps that were real:

- **Names.** Ron says "Fenn"; the agent-types list says `code-reviewer`. Nothing
  mapped them, so a request in Ron's vocabulary landed on nothing.
- **Visibility.** A Task-tool subagent runs in-process, never becomes a session,
  and never appears on the Floor. Dispatching through MC makes a real figure
  with its own chat. A genuine choice with a genuine consequence that nothing
  was mentioning.

`_roster_block` states both, generated from the live roster, and prints nothing
at all when no type has a self-chosen name — a heading over an empty list is
worse than silence.

## [2026-08-24j] — a figure can have a face

Ron: *"let's give the agents the ability to choose their avatar."* One emoji, on
the same route and with the same two authors as the name.

**Emoji, not an image.** The Floor design's own mockup already drew them
(`✍ Marlow`, `🔍 Quill`, `🦉 Fenn`). An emoji needs no upload path, no
storage, no serving route, no allowlist entry and no mobile sizing pass, and it
cannot 404 — and the same string renders in the Floor, the bench, the chat
header pill and the persona picker without any of them knowing about the others.
An image avatar is a strictly larger feature and can arrive later behind the
same key, since a value that looks like a path is distinguishable from one that
does not.

`avatar` lives in the character's **frontmatter**, for the reason
`mc/characters.py` already gives for the engine keys: the file stays the single
artifact, so copying it to another machine keeps the type intact, and Claude
Code ignores keys it has never heard of — the file remains a valid subagent.

Three things worth their comments:

- **The cap is 8 characters, not 1.** A "single emoji" is frequently several
  codepoints — a ZWJ sequence, a skin-tone modifier — so a 1-char cap silently
  truncates 👩‍💻 into 👩. Long enough for those, short enough that nobody
  fits a word in it. It is also the only guard: no allowlist decides what counts
  as an emoji, because any list we write is wrong by the next Unicode release
  and the failure mode of being wrong is refusing a face somebody picked.
- **Absent means "leave it", not "clear it".** The session-label record is
  rewritten whole, so a caller setting only a face must not wipe the name — and
  the self-naming path on a character file carries the avatar forward for the
  same reason, or naming itself would delete the face it had chosen. Both
  directions are pinned by test.
- **A figure with no face gets a neutral dotted circle, never a random one.**
  Absence is a finding on this board — the same discipline that shows "no type"
  rather than papering over it.

The agent sets its own through the route it already names itself with; Ron
clicks the face on the card. `by` records which, as before.

## [2026-08-24i] — a figure has a name, and it can be changed

Ron: *"the floor should allow the agent to either name itself or let the user
name him."* Building it surfaced a bug shipped hours earlier.

The card printed **"no type"** where the name goes — while that same session's
system prompt says *"Your name is Vector"*. Two surfaces disagreeing about who
someone is, which is exactly the failure `_figure_state` was copied from
`_project_live_agent` to avoid.

They were two facts wearing one slot. A figure always has a **name**; it
separately may or may not have a **type**. "no type" still shows, because it is
still the gap Frame 1 exists to make visible — it just stops standing in for a
name it never was.

Precedence mirrors the prompt's, with an override on top: explicit label >
the persona's own `agent_name` > the configured default. `name_from` says which
fired, so a chosen name renders differently from an inherited one; otherwise the
default stamped on every anonymous session reads like somebody's decision.

`POST /api/floor/figure/<sid>/name` serves both paths, because they are one act
with different authors. `by` distinguishes them: a name the agent chose is a
statement about itself, one Ron typed is an instruction. Empty clears it.
Naming a dead session is a 404 — a name for a figure that no longer exists is a
leak in a file nothing prunes. Labels live in `data/agent_labels.json`, outside
`DATA_DIR`, keyed by session id so they survive the revival that rebuilds
sessions from the agent log.

Self-naming needed one piece of plumbing: the session id was minted *after* the
system prompt was built, so the prompt could not tell an agent which figure it
is. `_planned_sid` already computed that id — it exists so a worktree can be
named before the lock is taken — and just sat forty lines too late. Moved above
the context build; the worktree call still runs where it did, outside the lock.

The prompt line offers rather than demands. A board where every session renamed
itself would be as unreadable as one where none did.

## [2026-08-24h] — the Floor: who is doing what, everywhere

MC-897 phase 1. Until now the only way to learn what is running across twenty
projects was to open twenty modals, and a session that had been idle for twenty
hours was invisible until you happened to look at it.

Rooms are projects; **a figure is a session, not a type**. That distinction is
the whole design: one character can be running in three projects at once, and
the chat header pill — one persona, one chat — structurally cannot say so. Two
agents on the same project render as two figures inside one room, which is the
shape no existing surface could draw.

`GET /api/floor` is one call, not twenty. The build order had said "poll the
endpoints that already exist", but `/api/project/<id>/agent/status` is
per-project by design and ships each session's full log tail — twenty of those
on a 30s poll to render twenty one-line cards. The new endpoint walks the
in-memory `agent_sessions` map once and returns only what a card shows.

What it refuses to do, each for a reason:

- **It never renames an unnamed session.** "no type" is a finding, not a
  placeholder — labelling every anonymous session with the configured default
  name would hide the exact gap the board exists to make visible.
- **It never shows incognito or housekeeping sessions.** Incognito's promise is
  staying off the public indicators; a cross-project board is the most public
  indicator there is.
- **It never puts an activity string on an idle figure.** A stale "thinking…" is
  a lie about a live system. With `activity_states_enabled` off there is no such
  signal at all, so the board says so once at the bottom rather than leaving
  every card looking stalled.

State priority (`asking > working > idle`) is copied from `_project_live_agent`
rather than reinvented, and a test pins it: two surfaces disagreeing about
whether a project needs you is worse than either being wrong.

Rooms with someone waiting on you sort first. Quiet projects are a collapsed
count. Clicking a figure opens that project's chat **on that session** —
hierarchy is for delegation, not for inspection. The bench (hired types with
nothing running) renders read-only; dispatching from it is phase 2, and a button
that does nothing is worse than no button.

Lives in the sidebar beside Inbox, not under Workspace — both are
cross-project, everything under Workspace is scoped to one. 14 endpoint tests
plus a boot-smoke guard that drives the real `sidebarNav('floor')` route.

## [2026-08-24g] — phase 4 ends as a weekly watch, not a screen

Phase 4 was going to end in a report screen and an automatic mover. Neither is
being built, and the measurement it produced is the reason.

**The mover has almost nothing to move.** 529 of 586 archive lines are never
delivered, and that costs nothing — they are already in the coldest tier, so a
line nobody retrieves spends no tokens. The §5 rule was *never delete, demote*,
and these are already demoted. The only real lever the counters expose is
promotion into the resident index, and across 189 tasks that came to **two
notes** (`research_competitor_gtm_channels.md` at 50 deliveries and
`decision_free_tier_feeds_adoption.md` at 34, both with no index line — added by
hand; the index sits at 17.2 KB against its 24 KB cap). Automating two
promotions a quarter is scaffolding.

**What is worth keeping is the watch.** The position that had been reaching 57%
of tasks was wrong from the day positions shipped, and the code, the tests and
the UI could not have shown it — only a count against real tasks did. So
`tools/memory-eval/delivery_review.py` runs weekly beside the memory health
check and reports four things: a note fetched constantly with no index line, a
position that has become prompt furniture, an indexed note gone dark, and the
archive's share of delivered slots (which is the `expires_when` on the
archive-quota position, so the review re-opens that ruling on its own terms).

It reports; it never promotes, demotes, or edits a note — same rule as
`positions_review`, and for the same reason. **A flag is raised once**, keyed to
the finding's own content, so a known condition stays quiet and re-arms when it
changes; `preference-5c17ba9d` is what that rule is for. Below 60 recorded tasks
it refuses to judge at all, because a rate over five tasks is noise.

One trap it is built to avoid, and has a test for: the vault's slugs drifted, so
`[[feedback-grep-memory-dir]]` and `feedback_grep_memory_dir.md` are the same
note. A naive string comparison calls that note unindexed *and* the link
dangling — two false findings from one wrong match. It matches through
`_mem_link_key`, the way the vault itself does.

**And the archive-quota promise is answered.** Replaying the same 188 tasks at
quota 0 / 1 / 2 / 3 / 6: starved tasks 0, topic reach 70/74, positions fired 22
— identical at every setting, with archive holding 3.9–5.2% of slots regardless.
The knob went in when archive lines held 34% of slots; read-time dedupe (2,222
raw lines collapsing to 586 units) removed that flood, so the cap now caps
something that no longer overflows. Recorded as a standing position: leave it at
2 and stop tuning, re-open if archive share climbs back over 15% or dedupe is
weakened.

## [2026-08-24f] — the delivery counters get a baseline, and immediately catch one

The counters shipped empty, which would have meant designing the residency
decision against a blank file and waiting weeks for live traffic. They did not
have to: the read floor is deterministic — no model, ranked grep — so replaying
it over the tasks we already dispatched reproduces exactly what those sessions
were served. `tools/memory-eval/delivery_backfill.py` does that over **188 real
dispatched tasks** from 281 sessions (90 acknowledgement-only follow-ups
excluded), writing under its own `backfill` context so live traffic stays
separable.

The baseline, against a 675-unit corpus:

| | |
|---|---|
| tasks that surfaced nothing | **0** (was 17 before the archive-quota fix) |
| topic notes ever reached | **70 / 74** |
| archive lines never reached | **529 / 586** |
| reached only by a `[[wikilink]]` hop | 2 |

Two of those are the answer to a question that has been open since MC-892. The
topic layer is essentially all reachable — the BM25 rewrite, the title boost and
link expansion did their job. **The demotion candidates are in the archive**, and
there are 529 of them.

**What it caught on the first run.** The MC-898 position was being delivered on
**108 of 188 tasks — 57%** — because its subject contains the word "agent", which
appears in 32.7% of this corpus. The coverage gate was an OR over subject tokens,
so a single ubiquitous word was enough, and a standing ruling about a nightly
research job was riding along on every task that mentioned an agent. That is
precisely the permanent-prompt-furniture failure the gate was built to prevent,
and it had been live since positions shipped with nothing to reveal it.

An English stopword list cannot fix it — "agent" is not a stopword, it is a word
this project says constantly. Commonness is a property of the corpus, so the test
now is too: a **subject-derived** trigger only fires if it appears in ≤10% of the
corpus (with a floor of 5 documents, since a fraction is meaningless on a small
vault). An **explicit** `triggers:` list is exempt — a human naming a term is
stating intent, and second-guessing it would make the field pointless.

Re-measured after the fix, same 188 tasks:

- MC-898 position: 108 → **19** (57% → 10.1%)
- Obsidian position: 31 → **3** (16.5% → 1.6%)
- Both still fire on their own subject: *"should we adopt obsidian…"* and
  *"lets build the nightly field research agent as a hivemind"* each surface
  their ruling; *"add a new agent type to the roster"*, *"improve the memory
  index"* and *"fix the cloudflare tunnel quota alarm"* now surface neither.

One test fixture changed with it: `test_a_matching_position_survives_a_busy_query`
built its forty noise notes containing the trigger word itself, which made the
term ubiquitous in the fixture and masked the property it was pinning. The noise
notes now share every query term except the distinguishing one — the query is
just as busy, and the reserve is actually exercised.

## [2026-08-24e] — continuity belongs to an agent, not to the project

Ron, looking at the Memory modal: *"the memory index does not yet differentiate
between agents, so Dave and Vector all look the same and seem to be sharing the
same memory."* Two of the three layers share correctly and one did not.

**Facts and positions are the project's, deliberately.** A gotcha Vector learned
must reach Dave, and a ruling Vector recorded must *bind* Dave — an agent not
bound by a standing ruling is the exact failure that produced the positions
feature in the first place. Per-agent positions would rebuild the bug.

**Continuity is worker state, and it was shared by accident.** One `continuity.md`,
five slots, written by whichever agent checkpointed last. On this project that
meant five threads from four different sessions, none marked done, two of them
describing work that had already landed — and every session was served all five
as its own. A shared working-state record does not degrade into no record; it
degrades into confident wrongness.

The record now carries an owner:

- Five in-flight and five promised **per agent**, and an agent's write only ever
  replaces its own. Its prompt shows its own slots in full, then other agents'
  named and capped at three lines under a heading that says they are not yours —
  hiding them would be wrong (two agents about to edit the same file is worth
  knowing) but presenting them as yours is what made the record misleading.
- **The ownerless bucket is the project's**, not a rival's: it holds whatever a
  human typed and everything written before owners existed, and it merges into
  every agent's view. Exiling it to the capped block would have made every
  existing install lose its continuity on upgrade. An agent that keeps a shared
  line in its own rewrite *claims* it, which stops the legacy lines duplicating.
- **Eviction stays structural** — the four most recently written owners keep a
  bucket. No remover, no curator, same lever as the slot caps.
- Read and write must agree on *who*: `_session_owner` resolves to the same
  string the prompt's "Your name is …" line uses. If they diverged, every agent
  would silently read an empty record and nothing anywhere would say so — the
  same silent-death shape as the `weekly` schedule that stored fine and never
  ran.
- The Memory modal now edits one group per agent, so a human correction lands in
  the bucket it is about instead of overwriting what Dave is part-way through.

Also fixed: `test_read_floor_is_gated_on_task_and_logs_failures` matched inside a
fixed 12,000-byte window of `_build_agent_context` and started failing for the
wrong reason as the function grew. It anchors on the call site now.

## [2026-08-24d] — memory starts measuring which of it actually gets used

**Dave phase 4, step one: the counters.** Residency is the only scarce resource
in the vault — 21.9 KB always-resident against a 1.1 MB corpus — and nothing
measured which units earn it. That is precisely why MC-892's eviction failed its
safety review: with no delivery signal the only lever was an editor's judgement,
and 29–30 of the 67 lines it proposed cutting had no surviving delivery channel
at all. The answer is not a better editor. It is a cache keyed on retrieval:
promote what gets delivered, demote what never does, **delete nothing**.

`mc/memory_delivery.py` counts one thing — a unit reached a real prompt — into a
JSON sidecar beside the notes (never under `DATA_DIR`; a stray `*.json` there
becomes a malformed project and 500s both restart endpoints).

Three properties it exists to hold:

- **Identity is the unit, not the file.** `MEMORY_ARCHIVE.md` is ~2.5k
  separately ranked lines under one label; keying on the label would credit
  every line with its neighbours' hits. Line classes get a content hash
  (`_unit_uid`), so an edited line correctly starts a fresh history — it is a
  different claim — while a topic note keeps its history across edits.
- **Recording is opt-in per call site.** The read floor records; the
  memory-search box does not. A human looking a note up is not evidence that it
  earns residency, and counting it would let anyone inflate a note by searching
  for it.
- **It can never cost the read floor.** The floor is the only retrieval channel
  that actually runs (agents open a memory file in 5% of sessions), so every
  entry point swallows and logs, and a test pins that a failing writer still
  returns hits.

The task counter is the denominator that makes a zero readable: never-delivered
over three tasks is noise, never over three hundred is a demotion. `summary()`
pairs the counters against a live corpus scan, because the never-delivered half
is the one that matters and the counters only know what arrived.

Not built yet, deliberately: the mover. Nothing promotes or demotes anything —
that step reports to a human first, for the same reason `positions_review` does.

## [2026-08-24c] — positions get reviewed, and `weekly` schedules get to run

**Dave phase 3.** A position records a verdict, a reason, and `expires_when` —
the condition that would re-open it. Without anything testing those conditions a
position only gets older, and a ruling whose reason quietly stopped being true is
worse than no ruling, because it still outranks the notes around it in every
agent's prompt.

The reviewer **is** MC-898's daily field sweep, given a query set. "What's new in
agentic mission control" has no stopping condition and no way to separate an
interesting finding from a relevant one. "Has anything changed that trips one of
our own rulings" has an answer.

- `mc/positions_review.py` — which positions are due (7-day rest, or immediately
  after a human edit), content-hash dedupe, and a JSON sidecar beside the notes.
- `tools/position-review.py` — `brief` / `record` / `flags`.
- The `mc-position-review` builtin skill, and a weekly schedule.

Three properties it exists to guarantee:

- **It reports; it never edits a position.** An unattended agent rewriting the
  rulings that steer every other agent is the authority-guard violation in a
  different hat. `record_review` writes only its own sidecar, and a sidecar
  cannot change what any prompt says.
- **A flag is raised once.** Keyed to the position's content hash, so it re-arms
  when a human edits the reason and stays quiet while a known condition keeps
  holding. A nightly "still tripping" mail is how a channel stops being read.
- **A position with no trigger is still reviewed** — more worth a look, not
  less. The useful output there is "this needs a trigger", not a stale-check.

**And `schedule_type: "weekly"` never worked.** It had no branch in
`_compute_next_run`, so it stored fine, returned 201, read `enabled: true` and
never ran. The pre-existing weekly MEMORY HEALTH CHECK had `next_run: null`,
`last_run: null` and zero runs in its entire life; nothing anywhere said so. The
scheduler UI only offers daily/interval/once/cron — but the API reference in
every agent's system prompt lists `weekly`, so agents kept choosing the one type
that silently did nothing.

Fixed: a real weekly branch (it is daily-restricted-to-days), `days` normalised
to accept names or numbers because the two live rows disagreed with each other
(`[1]` vs `["sunday"]`), a **400** on any `schedule_type` the engine cannot
schedule, and a log line when an enabled row computes no `next_run`. Both weekly
schedules self-heal on the next scheduler tick. `SCHEDULE_TYPES` is now the one
list, asserted by a test that every declared type actually computes a run.

`tools/backlog-journal-export.py` also indexes agent-written journals now.
`AGENT_RULES.md` tells unattended cycles to write `docs/_journal/<id>-<slug>.md`
and calls INDEX.md the map of every item to its file — but it only listed items
carrying legacy backlog *notes*, so the journals written the documented way were
the ones missing from it.

1540 tests pass; pyright clean on both touched modules.

## [2026-08-24b] — you can finally read what the agents remember

Working state and standing positions had no surface at all. Only agents could
see them, which made "are the positions any good?" a question you could answer
only by grepping the vault — and a memory layer nobody can inspect is a memory
layer nobody can correct.

**Both now live in the Memory modal**, above MEMORY.md rather than in a new
sidebar entry: they are three layers of one thing, and split across three places
nobody would ever compare them.

- **Working state** — the understanding paragraph, the in-flight list and the
  promised list, each editable. The panel says plainly that agents rewrite this
  at checkpoints, so an edit is a correction to the base they fold into, not a
  permanent note.
- **Standing positions** — one card per ruling: verdict, subject, decision date,
  and editable reason / expiry / trigger terms. **Save** re-posts the same slug,
  so it supersedes in place and keeps the old reasoning under `## Previously`.
  **Forget** is a real DELETE.

`delete_position()` is new, and it is deliberately the opposite of the rule for
notes ("never delete to save tokens — demote"). A note is an observation and a
cold one costs nothing. A position is a *ruling*: it renders in its own prompt
block and outranks the notes around it on its subject, so a wrong one is not
dead weight, it actively misdirects every future turn. It takes a filename from
the UI — the one string in this feature that reaches the filesystem — so it
refuses anything that is not a bare `position_*.md` resolving inside the memory
dir.

The `memory-panel` smoke guard pins the three things that fail silently here:
every inline handler needs its `window` bridge (ES modules), Save must supersede
rather than fork a second contradictory ruling, and Forget must DELETE rather
than edit a reason to "never mind". 1520 tests and 12 smoke scenarios pass.

## [2026-08-24] — positions get a caller, and continuity stops competing with itself

Positions shipped the day before with storage, retrieval, a note class that
outranks ordinary notes on its own subject, and two REST routes. What they did
not ship with was a **caller**. A day later the vault held exactly two
positions, both hand-written while the feature was being designed — nothing had
recorded one since, because nothing told any agent the route existed.

Capture stays explicit rather than mined from transcripts; that part of the
design is right (most "no" in a conversation is not a decision, so a scan buries
the twenty entries that matter). But explicit only works if the agent is told,
and an API nobody is told about is dead code. `render_position_capture()` now
puts a short directive in every project's system prompt, next to continuity —
**not** in the API reference, because reference material failing to fire is the
exact failure positions exist to fix. It carries the project's own URL, marks
`reason` as mandatory and says why, explains that recording supersedes rather
than appends, and warns off ordinary preferences: every entry costs prompt space
in every future turn that touches its subject.

**And the continuity record no longer competes with itself.** It is injected
verbatim into every prompt, yet it was also sitting in the read-floor corpus —
so on two of three probe queries it won one of six retrieval slots with text the
agent was guaranteed to already have, displacing a real note to do it. Excluded
from the corpus; it is delivered, not retrieved.

Verified live before the fix: a position ranks **first** on its own subject
("should we adopt Obsidian as our memory vault?", "how heavy should the nightly
research agent be?") and is **absent** from an unrelated one ("fix the
cloudflare tunnel quota alarm"). 1517 tests pass.

## [2026-08-22] — the image viewer is a window you can pinch, not a modal

Opening a picture used to take the whole dashboard hostage: a full-screen dim
backdrop that swallowed every click, and a window sized 95vw x 92vh whether the
picture was a screenshot or a 120px thumbnail. Zoom worked from the toolbar and
nowhere else — the wheel handler returned early unless Ctrl/Cmd was held, and
there was no touch handling at all, so pinching on a phone did nothing.

**It is now an ordinary window.** No backdrop, no pointer capture: drag it by
the toolbar, resize it from any edge, click the app behind it, open two at once.
Below the 960px mobile breakpoint it keeps the full-screen treatment, where a
floating window would be worse than what it replaces.

**It opens at the picture's size.** `_ivFitBox` shrinks by aspect until the
whole image fits in 80% of the viewport, and never enlarges past 1:1 — so a
thumbnail opens as a thumbnail and a 4K screenshot opens showing all of itself.
The old code clamped width and height independently, which for anything larger
than the screen meant "maximised".

**And it answers the pointer.** One shared gesture layer (`_ivGestures`) drives
both the image and the Mermaid viewer:

- plain wheel zooms, no modifier, anchored at the cursor
- two-finger pinch, anchored at the midpoint
- double-tap toggles 100% ↔ 250% at the tap point
- click-drag pans once the picture overflows its canvas
- toolbar buttons and `+` / `-` / `0` route through the same anchored path, so
  every control agrees on which pixel stays still
- only the front window answers the keyboard, now that two can be open

`touch-action: pan-x pan-y` on the canvas is what hands the pinch to us instead
of to the browser's page zoom while keeping native one-finger scrolling.

`tools/smoke/image-viewer-gestures.mjs` drives all of it in headless Chromium:
each input path, cursor-anchoring to within 4px, the window geometry, and a
listener balance-sheet that fails if closing a viewer leaks a `document`
handler. Wired into `npm test` under `tools/smoke/`.

## [2026-08-23e] — the continuity record: what you were part-way through

The third memory layer, and the one that was missing entirely. **Facts** work —
the read floor reaches 84% of turns that previously got nothing. **Episodic** is
thin. **Continuity** did not exist: what a session was mid-way through, and what
it had promised, evaporated the moment that session ended. That absence is most
of why an agent reads as a stranger rather than a colleague, and no amount of
better retrieval fixes it, because it is working state rather than knowledge.

`continuity.md` per project, three slots:

```
## Where things stand      one short paragraph
## In flight               up to 5 lines — started, not finished
## Promised                up to 5 lines — said it would, hasn't yet
```

It is injected **directly** into every turn's context, not retrieved. "What am
I part-way through" is relevant to every task by definition, so making it
compete for a read-floor slot would be asking the wrong question.

**Bounded by construction, which is the entire safety argument.** MEMORY.md
needs a remover because it is an open-ended curated list, and MC-892 proved the
remover is the hard part: the proposed eviction would have dropped 29–30 lines
with no surviving delivery channel, and the gate built to catch exactly that
returned green. A fixed-slot record cannot have that problem. Every write
**replaces** the whole record, the caps are enforced in code rather than by the
model, and finished work leaves by being omitted. There is no eviction policy
because there is no growth — the file is held under 3 KB by a test.

**Written by the checkpointer**, on the same transcript delta it has already
rendered. No second transcript read, no second debounce, one cheap model call
at a boundary that has already earned one — and it runs after the checkpoint
entry is committed, so a failed extraction loses nothing. The extraction prompt
asks for the **complete** record rather than a diff, on purpose: a model that
emits "add this thread" makes the caller decide what falls off, which is the
curation problem this design exists to avoid.

Stored as markdown sections in the body, not frontmatter lists. The vault's
minimal frontmatter parser has no list type and hands `['a','b']` back as a
string, which then iterates character by character — nine tests caught it. The
body format is also readable and hand-editable in the vault, which the rest of
the memory design already leans on.

Incognito sessions get no continuity block; leaking working state into a mode
that exists to skip memory would defeat it.

`GET`/`PUT /api/project/<id>/memory/continuity`. Config: `continuity_enabled`
(default on).

## [2026-08-23d] — remembering what we decided NOT to do

Every capture path Clayrune has is downstream of an **artifact**. The
checkpointer summarises what happened; the Scribe extracts from outcomes; the
Distiller looks for recurrence. Deciding *not* to build something produces no
commit, no file, no diff — so all three are structurally blind to it, while
re-proposing a settled question costs an entire conversation.

Demonstrated the hard way. Ron named two decisions the project's agent should
have known. One of them — *"we evaluated Obsidian and declined, because we
already have the vault shape and built the graph machinery ourselves"* — **was
in the vault and in the always-loaded index**, and the very next turn the agent
proposed adopting Obsidian and manufactured a justification for it.

So this was never a storage gap. It was stored as *history*, and history does
not fire when someone re-proposes the thing it settled.

**Positions** are a new note class: `position_<slug>.md`, carrying a `subject`,
a `verdict`, a **`reason`**, and `expires_when`. They ride the existing vault —
same retrieval, same links, same archive machinery — with three differences:

- **The reason is mandatory.** A bare verdict is dogma an agent can only obey.
  A reason is checkable, which is what lets a position be re-opened honestly
  instead of either ignored or followed blindly.
- **They get their own block in the prompt**, above relevant memory, headed
  "STANDING POSITIONS — already decided". Mixed into ordinary memory a ruling
  reads as background, which is precisely how the Obsidian one failed.
- **They fire on explicit `triggers:`**, defaulting to the subject's own words
  minus stopwords, and reach the prompt *only* through gated reserved slots —
  never the ordinary ranking.

That last point took three attempts, and the failures are worth recording.
Term-count coverage cannot tell "should we adopt Obsidian?" from a bare
"memory": against the subject *"Obsidian as the memory substrate"* both cover
one term of three. IDF weighting is better in principle and still wrong in
practice — it makes firing depend on how often a word happens to appear
elsewhere in the vault, so a position silently starts or stops working as
unrelated notes get written. Explicit triggers are deterministic, and a misfire
is a line you can read and fix. That is the same argument that settled agent
routing earlier the same day.

**Recording a position supersedes in place.** A reversal reads as one current
ruling, not two contradictory ones, and the superseded reasoning is kept under
a `## Previously` heading — "declined in August, reversed in November because
Y" is worth more than either half alone.

`GET`/`POST /api/project/<id>/memory/positions`. Two real positions recorded to
start: the Obsidian ruling, and the nightly research agent's weight.

Also fixed here: a first version of the "don't hijack unrelated queries" test
asserted only that a position was not ranked *first*. Measured against the real
vault, both recorded positions rode along on *"fix the cloudflare tunnel quota
alarm"*. The assertion is absence now.

## [2026-08-23c] — the checkpoint pile-up, at the root

`[2026-08-23b]` stopped the read floor *choking* on 2,222 near-duplicate
archive lines. This is why they existed.

Step-6 checkpointing folds each transcript delta into a **cumulative** running
summary, so every `_(live)_` entry it writes is a strict superset of the one
before. `supersede_sid` was built for exactly that: drop the session's previous
entry in the same atomic write, keyed on the `last_entry_hash` stashed on its
watermark. **It was implemented, and it was correct.**

It worked right up until the entry left `MEMORY.md`. The floor evicts
oldest-first into `MEMORY_ARCHIVE.md` — append-only cold storage that is never
truncated — with no regard for whether a *live* session was still going to
replace that line. The moment one got relocated, the supersede-by-hash lookup
found nothing, and every subsequent checkpoint appended instead of replacing.
One conversation left **47 copies** that way.

So the fix is one rule: **the floor may not evict a line a live watermark still
points at.** Protected lines are skipped and eviction continues past them, so
ordering is preserved and unrelated history still overflows normally. Nothing
is pinned permanently — protection ends when the session does and its marker is
removed by teardown or `_gc_stale_watermarks`. If *every* remaining entry
belongs to a live session the index sits over its floor for a few turns and
says so in the log; that is the cheaper failure, because the alternative is
archiving a line that is still being written.

Worth stating plainly, because it shapes where to look next time: this was not
a missing feature. Supersession, watermarks, the hash pointer and the atomic
write were all present and working. The defect was that two correct mechanisms
— in-place supersession and oldest-first overflow — had no knowledge of each
other, and the one that ran second silently disabled the first.

Regression tests fail with the guard removed (verified, not assumed).

## [2026-08-23b] — the read floor was serving the agent its own stale first guesses

One task in eight was getting **no memory at all** — six slots on the briefing
card, all six filled with session-log lines, zero topic notes. Measured by
replaying the read floor over 140 real task prompts from this project's log.

**Why: 76% of the archive is superseded.** The Step-6 checkpointer appends a
fresh session-log line every time it runs, so one long conversation leaves a
trail of near-identical entries. 1,684 of 2,222 lines are superseded by a later
line in the same day/task group; the worst group has **47 copies**; 1,561 are
`_(live)_` — mid-session checkpoints rather than finished runs.

It is not just waste, it is **wrong**. The early line in a group is the agent's
first guess. Asked "do we have a `/goal` command?", the read floor returned six
lines from one afternoon — the first saying *"found no /goal command"*, the
last saying *"verified working in MC"*. The ranker had no way to prefer the
later one, and the stale answer matched the query perfectly, so it swept the
card.

**Dedupe happens on the way into the corpus, never on the file.** The archive
is append-only cold storage and is never truncated; this changes only what
retrieval sees. Grouping is by `(day, task)` — the same task on a *different*
day is a genuinely separate occasion and survives (57 tasks recur across days).
The only false merges are same-day generic prompts (`ok`, `Hi`, `restarted`):
45 groups, 149 lines, all worthless as retrieval keys.

**And the archive quota is now on.** Archive lines outnumbered topic notes
~30:1 and competed uncapped for the same six slots; `read_floor_archive_quota`
existed but had never been turned on. Measured over the same 140 prompts:

| | archive share of slots | tasks with zero topic notes |
|---|---|---|
| before | 34% | **17** |
| dedupe only | 20% | 4 |
| dedupe + quota 2 | 15% | **0** |

Dedupe does most of the work; the quota finishes it. Set to **2** rather than 1
deliberately — archive lines are the episodic layer, the record of *when we did
what*, and cutting them to a single slot would strip the thing that makes an
agent sound like it remembers events rather than only facts.

## [2026-08-23] — the PLAN tab was losing plans

Two defects, found because a design doc written during a session never appeared
in the tab. Both were silent, and both applied to **every project**.

**A session could only ever register one plan.** `plan_file` was a scalar, so a
session that wrote three of them kept the third and the other two were
unreachable from every surface — no error, no partial list, they simply were
not there. It is `plan_files` now, a list, with the scalar kept as "most
recent" because the in-chat plan link and the approval banner are single-valued
by nature. The read path falls back to the scalar, so the years of agent-log
entries that predate the list keep working; `<id>_agent_log.json` is untracked
user data and there is no migration to run.

**Only `Write` and `Edit` registered a plan at all.** A plan created by a
heredoc, a `cp`, or a `tee` produces no Write tool call, so it registered
nowhere — permanently invisible. Bash commands are now scanned too.

That scan deliberately does **not** carry its own copy of the plans-dir path.
It looks for markdown-shaped tokens and lets the existing containment check
decide, because two places that both know where plans live are two places that
can disagree — and this one would fail *open*, registering nothing, which is
exactly the kind of silence that hid the bug in the first place. `~` is
expanded before the check: unexpanded, the common `~/.claude/plans/x.md` never
resolves inside the plans dir and the detector sees nothing.

Deleting a plan now also clears it from the list, not just the scalar.

Also fixed: the empty-state copy blamed `EnterPlanMode` / `ExitPlanMode` —
which agents are explicitly told not to use, since it hangs without a TTY. It
now says what actually makes a plan: any markdown file written into
`~/.claude/plans/`.

## [2026-08-22c] — agents name themselves

An agent type was addressed by its filename. `prd-writer` says what the thing
is for; it does not say who is talking to you, and a chat header full of
kebab-case reads like a directory listing rather than a team.

**A type now chooses its own name.** In the persona editor there is a *Goes by*
field and a **🎲 Let it choose** button: the type is handed its own role
definition and picks. You can also type one, or clear it and fall back to the
file name. It shows up in the chat header pill, the composer picker, and the
project-default picker — name first, role in parentheses.

The three seeded types named themselves on the model each is pinned to:
**Marlow** (prd-writer, Fable), **Quill** (market-researcher, Opus), **Fenn**
(code-reviewer, Sonnet).

Three details that took a second pass, because each failed the obvious way
first:

- **The chosen name outranks the global assistant name.** `_build_agent_context`
  already emitted `Your name is …` from config — and a local reassignment a few
  lines below the function signature silently clobbered the new parameter, so
  every persona was still being told it was the global assistant. There is one
  line now, character-first. Emitting both would tell the agent it has two
  names, and it would pick one per turn.
- **The roster is part of the prompt.** Naming three types independently
  produced `Marlow` and `Marlowe` — each call is blind to the others, so
  warning off the generic cluster (Atlas, Nova, Sage, Echo…) is not enough. The
  model is now shown the names already in use across both scopes and told not
  to pick a near-miss.
- **An unusable answer is refused, not trimmed.** Asked for one word, a model
  sometimes writes a sentence; pilling a fragment of "I would suggest the name
  Vector" is worse than leaving the type on its filename. Wrapping quotes are
  stripped first, including asymmetric smart quotes — the exact shape a model
  is most likely to emit, and the one a naive first-equals-last check misses.

Also fixed while here: a plain description edit used to **wipe** the chosen
name. The file is rewritten whole on every save, so "don't touch this field"
had to become an explicit carry-forward rather than simply not setting the key.

## [2026-08-22b] — a project can have a default agent, and an agent can have an engine

Personas existed, and almost nobody used them. They were chosen per chat, they
were opt-in, and the choice reset every time — so using one meant remembering
to switch it on, every single conversation. The design doc that shipped them
had named the missing layer itself and put it out of scope:

> *"Optional later layer, NOT in scope: a project-level default character that
> new chats inherit."*

That layer is this release.

**A project can name a default agent type.** Project profile → Default agent
type. New chats start as that persona; picking one in the composer still wins,
and picking "None" is still available. Where a persona came from is now visible
in the header pill — an inherited one is dashed and labelled `default`, because
a chat that silently adopts a personality nobody selected reads as the agent
having changed on its own.

**And an agent type can pin its own engine.** Three optional keys in the
character's frontmatter:

```yaml
provider: claude
model: claude-fable-5
effort: high
```

Fable writes the PRDs, Opus does the market research — the engine belongs to
the *type*, not to whatever project the type is running in. Set them in the
persona editor's new Engine row; leave them on Default and nothing changes.

- **Precedence, top down:** the composer's per-chat pick, then the character's
  pin, then the project setting, then the complexity router, then the global
  default. The character outranks the project because a type is meant to travel
  between projects; it loses to an explicit pick because that is the user
  speaking about this one turn.
- **The model pill says `character`** when the type chose the engine, not
  `manual` — otherwise the type's choice is indistinguishable from yours.
- **An unknown provider is refused at save time**, not silently defaulted. A
  character pinned to a provider that isn't registered would spawn on whatever
  the project happens to use, while the pill claimed otherwise; that is worse
  than a save that fails.
- **Absent and empty are different.** An omitted key leaves a pin alone; an
  empty one clears it. Without that split a pin could be set from the editor
  and never removed.
- Nothing migrates. Every key is optional, and every character already on disk
  keeps behaving exactly as it did — the file also stays a valid Claude Code
  subagent, so `@`-mention and auto-delegate are unaffected.

**A stale default degrades quietly.** Nobody typed it, so nobody is watching
for it to break: a deleted default resolves to no persona and logs, rather than
failing the dispatch.

Design and the reasoning behind the routing order: `docs/AGENT_TYPES_DESIGN.md`
(MC-895). This is Phase 1 of five; deterministic `{project, trigger}` bindings
and work-kind routing come next.

## [2026-08-22] — backlog items have keys (MC-01), and can point at each other

An item's `id` is an eight-character uuid slice. It is stable, and it is
unspeakable: nobody says "see 4b7738a1" out loud. So there was no way for a
person *or* an agent to say one item duplicates another, continues it, or is
waiting on it — the relationship existed only in someone's head.

**Every item now has a key**: the project's prefix, a dash, a number.
`MC-01`, `MC-02`, `MC-142`. It sits at the head of the row; click to copy.
Existing backlogs key themselves on first open — no migration to run, and
nothing to run it on a machine that isn't this one.

The prefix is the part that earns its keep. A bare `#12` is ambiguous the
moment two projects are on screen together, which the cross-project backlog
view does by default — `MC-12` names exactly one item on the machine, so it
survives being pasted into a chat, an email, or another project's item.

- **Derived from the name, then frozen.** "Mission Control" → `MC`,
  "clayrune_website" → `CW`, "MarketReplay" → `MR` (camelCase humps count as
  words). Prefixes are unique across projects; a collision widens the key
  rather than being tolerated. Once derived it is *stored*, so renaming a
  project doesn't silently re-key every item and invalidate every `MC-12`
  anyone has written down.
- **Editable**, in Project profile → Backlog key. Blank hands it back to the
  deriver. Changing it re-stamps every item in that project.
- Numbers are zero-padded to two so a young backlog reads `MC-01` and a column
  of keys lines up; past 99 they widen to `MC-100` rather than truncating.

**And items can be linked.** The 🔗 button opens a panel: pick a relation, type
a key.

| you store        | reads on this item | reads on the other |
|------------------|--------------------|--------------------|
| `blocked_by`     | Blocked by MC-12   | Blocks MC-04       |
| `duplicate_of`   | Duplicate of MC-12 | Duplicated by MC-04|
| `continues`      | Continues MC-12    | Continued by MC-04 |
| `relates_to`     | Relates to MC-12   | Relates to MC-04   |

- **Only one direction is ever written.** The inverse is derived from the list
  at render time, so the two halves of a pair cannot drift out of agreement —
  a half-written relationship isn't representable.
- **A number is retired, never recycled.** `backlog_seq` is a high-water mark,
  so deleting `MC-12` does not hand `MC-12` to the next item. If it did, every
  link pointing at the old one would silently start meaning something else, and
  a link that quietly lies is worse than one that's gone. Deleting an item also
  sweeps the links that pointed at it.
- Targets accept `MC-12`, `mc-12`, `MC-012`, `#12`, `12`, or the raw id — all
  of them things someone actually types. **A key with another project's prefix
  404s** instead of falling through to the number: resolving `CW-3` against
  this backlog would link the wrong item and look like it worked.
- Agents get the same handle, and the API reference now tells them to say
  `MC-12` rather than `4b7738a1`.

### Fixed: in_progress and blocked items rendered nowhere

Found while wiring the above, and older than it. The backlog tab kept items
matching `status === 'open'` and, behind the toggle, `'done'`. The PATCH route
has always accepted `in_progress` and `blocked` — both are in the documented
API — and `/api/projects` counted only `'open'` as well. So moving an item to
either status removed it from the list, from the tile's open count, and from
the cross-project view at once. The work list dropped exactly the items being
worked on, and said nothing.

The test is now inverted: `_BACKLOG_CLOSED` names the two closed states
(`done`, `wontdo`) and everything else is live. A status nobody anticipated
shows up by default instead of disappearing — which is the failure that hid
these. Live-but-not-open items render with a status badge and a coloured rail.
`done_at` follows suit and tracks closure rather than the literal string
`'done'`, so a `wontdo` item is stamped and a reopened one is cleared.

## [2026-08-16] — long-press a calendar slot to schedule something there

The calendar could only ever *show* you the week. Creating a run meant leaving
it, finding the Scheduled Tasks modal, and typing back in the day and time you
were already looking at.

Now an empty hour cell offers to become one: **tap and hold** on touch, plain
**click** with a mouse (the verb every calendar already uses). The scheduler
opens with the form pointed at that moment.

- **A past slot gets Daily, not Once.** A one-shot in a time that has already
  gone by can never fire, so pressing Tuesday 09:00 after Tuesday 09:00 means
  "this time of day" — the form opens recurring, on that weekday. Future slots
  get a fire-and-forget Once at that exact date and time.
- **A hold that travels is a scroll.** More than 10px of movement cancels it, so
  dragging the grid never creates anything. The pressed cell tints while the
  hold is counting down and the press lands with a haptic tick — without either,
  a long-press has no feedback at all and reads as the page glitching.
- On touch it is hold-only. A click follows every tap, and a stray tap on the
  grid opening a form would be maddening.

**`showScheduleForm` now takes a draft.** It used to treat "was I given an
object?" as "am I editing?", so a prefilled *new* schedule came up labelled
**Update**, offered **Run Now** for a row that didn't exist, and rendered
`runScheduleNow(undefined)`. Editing keys off the id now, which is the only
thing that actually distinguishes the two.

**Fixed on the way past: editing a one-shot moved it.** `run_at` was fed to
`<input type="datetime-local">` by slicing the ISO string — but that input
speaks *local* wall time with no zone, so it showed the UTC reading, and saving
that back rewrote the run to a different hour. The smoke guard caught it at a
7-hour skew; it now converts through the Date.

The form also scrolls into view when it opens. It sits below the Stewards
section and the whole schedule list, so on anything narrower than a desktop it
was opening off-screen — true for Edit-from-the-calendar too, which is why that
path felt like it did nothing.

## [2026-08-16] — the mobile keyboard closes and half the screen stays dead

Ron: *"too many cases on mobile where the keyboard is closed but the screen does
not extend back to full screen, leaving only half window."* This had been
patched three times already, each against a different guess, and it kept coming
back — because the thing that goes wrong is the **device's** viewport reporting,
which reading our code cannot reveal.

So it is simulated now. `tools/smoke/mobile-keyboard-viewport.mjs` replaces
`window.visualViewport` with a fake it drives by hand and reproduces each
real-world misbehaviour in headless Chromium. Against the previous code it fails
two checks; both are the reported bug.

**The stick.** `mcViewportHeightSync` sized the app *as* `visualViewport.height`
whenever a text field had focus. A down-button keyboard dismiss leaves focus on
the field, fires no `focusout`, and on some Android WebViews never updates
`vv.height` or fires `resize`. The watchdog compared `vv.height` against what we
had allocated — and they were now **equal**, because the stale reading was the
thing we'd allocated from. Every signal agreed the keyboard was still up. The
app stayed at half height indefinitely; nothing in the app could walk it back.

**The fix — size from the layout viewport, minus a keyboard inset.** The layout
viewport is the honest number: untouched by the keyboard in `resizes-visual`
(Chrome/Safari default), genuinely resized in `resizes-content` (WebView
adjustResize), never a stale leftover. The inset is only believed when a field
has focus **and** it is ≥120px (a 56px delta is a collapsing URL bar, not a
keyboard — the old code shrank for those too), and it is capped at 60% of the
screen so a bad reading can never eat it.

- **A tap breaks the deadlock.** When every keyboard-open signal is still true,
  only the user can tell us otherwise: a tap on ordinary content blurs the
  field, which fires `focusout` and settles back to full. Movement is measured
  so a scroll isn't mistaken for a tap, and taps on controls are left alone so
  pressing Send doesn't yank the keyboard away.
- **A second watchdog on the layout viewport**, which cannot go stale, catches
  any under-allocation while nothing is focused.
- **`sizeAgentChat` is re-run on the way back up.** It latches explicit pixel
  heights onto the tab content, panel, chat and output; the modal's
  ResizeObserver gets there eventually, but a direct call refills the reclaimed
  space in the same frame instead of leaving a dead band under the thread.
- `contenteditable` now counts as a text field.

**Surfaces that never got routed through the var.** Settings is full-bleed on
touch and has a search field at the top, so it had the original bug untouched —
it was still on a raw `100dvh`, which Android WebView doesn't reliably recompute
after a dismiss. Same for the `(pointer: coarse)` `.modal-content` fallback
(touch tablets above 960px) and the three-dot menu's `max-height`. All three now
use `var(--mc-app-vh, 100dvh)`.

## [2026-08-12] — the Model picker was claude-only, and a claude model id was reaching every other CLI

Ron opened the +New composer with **Codex CLI** selected and found no way to set
the model for that one conversation — only the project default, which is sticky
and easy to forget you changed. The picker existed; it just returned nothing for
any provider that wasn't claude.

Model ids do not cross runtimes, so the catalog now lives with the runtime that
knows the flag it feeds: `AgentRuntime.MODEL_CHOICES`, served per provider on
`/api/agent/providers` as `models: [{id,label}]`. The composer rebuilds its
options whenever the Agent select changes, and keys its pending pick by
**project+provider** so switching Agent cannot leave a foreign id armed.

- **`kiro`'s catalog is deliberately empty.** `kiro-cli` headless takes no model
  flag, so the picker stays hidden rather than offering a dead control.
- **A `Custom…` entry accepts anything.** These CLIs ship new model ids faster
  than we can track them; the catalog is a convenience, never a whitelist.

**Three bugs shared one root cause.** A project's `agent_model` is *always* a
claude id — the Agent-settings picker offers nothing else — and it was being
forwarded to every runtime unconditionally:

- Dispatch passed it verbatim, so a project pinned to Opus spawned
  `codex -m claude-opus-5` and died at the CLI. `_resolve_runtime_model()` now
  inherits a default only if the target runtime accepts it; an explicit per-chat
  pick still passes through untouched (it may name a model newer than our list).
- **Every non-claude followup dropped the model.** Mode A respawns the CLI per
  turn and `--model` is not sticky, but each `write_followup` called
  `build_command()` with no model — so a chat started on a chosen model silently
  reverted to the CLI default from turn 2. The model is stamped on the session at
  dispatch and re-stated on every respawn.
- `/agent/status` applied the project default as a fallback for *any* session, so
  a codex chat's header pill claimed `codex · claude-opus-5` for a run that got no
  `--model` at all. Claude-only now; elsewhere empty is the truth.

Guarded by unit tests (catalog shape, cross-provider rejection, ids reaching the
CLI flag, model surviving a respawn) **and a live `boot-smoke.mjs` guard** that
drives the real composer in Chromium: options rebuild per provider, no claude id
leaks into codex's list, the pick is per-provider sticky, and the chosen model
reaches the dispatch POST. The per-provider state is module-scoped to
`conversation.js`, which is exactly the shape of the cross-module bug the
neighbouring dispatch guard already exists for.

## [2026-08-09] — the vault's `[[wikilinks]]` were decoration; now they retrieve

Ron asked whether Obsidian's second-brain model could accommodate what Clayrune
already does. Auditing the actual state rather than the concepts: the memory dir
**is** an Obsidian-shaped vault (73 atomic notes with YAML frontmatter, a MOC-style
`MEMORY.md` index, wikilinks, full-text search) with two advantages Obsidian has
no answer to — capture is automatic (the Scribe, not a human who must remember to
write), and there is a *push* path (the read floor injects into every dispatch,
where Obsidian only ever pulls). One thing was genuinely worse, and it was a live
defect, not a philosophical gap.

**The links were never parsed.** 97 `[[wikilinks]]` across 46 notes, and no code
path in `mc/` or `server.py` matched `[[`. Two consequences:

- A past session's explicit "the companion note is over there" — the highest-value
  relevance signal in the vault, hand-authored — reached retrieval as nothing.
- **6 links had rotted to non-existent targets and nothing went red.** Another 5
  only *looked* broken: slug drift between kebab links and snake_case filenames.

Both halves fixed:

- **Tolerant resolution** (`_mem_link_key`) ignores every non-alphanumeric
  character, so `[[arch-mobile-ui]]` / `[[arch_mobile_ui]]` / `[[Arch Mobile UI]]`
  all reach `arch_mobile_ui.md`. Aliases and anchors are stripped.
- **One-hop expansion** (`_mem_link_graph` + `_mem_expand_links`, new
  `read_floor_link_expand`, default 2): after BM25 picks its topk the read floor
  follows links out of each hit — out-links first, then back-links — and appends
  those notes tagged `(linked from X)`. This reaches notes that share *no*
  vocabulary with the task, which is precisely what BM25 structurally cannot do.
  **Additive by design:** neighbours are appended, never substituted, so turning
  it up cannot push a lexical match out of the floor.
- **`tools/memory-link-check.py`** — the missing red. Reports dangling links, slug
  collisions, `name:`-vs-filename drift and orphan notes; `--strict` exits 1.
- Vault sweep on this install: 6 dangling links repaired (1 retargeted to the note
  that actually covers it, 5 de-linked to plain text — they referenced engram
  observations, not vault notes) and 19 `name:` fields canonicalized to the
  filename stem, prose names preserved as a new `title:` field. **0 dangling,
  92 resolved.**
- Settings → Memory → Retrieval now surfaces `read_floor_topk` (which had never
  been exposed, despite a session summary once telling Ron it was) alongside the
  new link-expansion dial.

10 new tests (`tests/test_memory_link_graph.py`); suite 1295 green.

## [2026-08-08b] — 24 cloudflared connectors on one tunnel

Ron, from Task Manager: "any idea why there are so many cloudflare tunnels
opened?"

**24 `cloudflared.exe`, all running the SAME tunnel token, 23 of them orphans
of dead MC servers.** Measured before cleanup: **910 MB** resident, **3,698
CPU-seconds**, and **96 live QUIC sessions** to Cloudflare's edge (each
connector opens 4). Every one was still a *registered connector* on
`ronl.clayrune.io`, so CF was load-balancing real traffic across 23 dead
servers' leftovers — which also means tunnel and zone analytics have been
reading as noise for days. They also accounted for 24 of the 54 `conhost.exe`
entries (one console host each), the second half of the same question.

**Root cause: teardown existed on exactly one exit path.** The 2026-06-03 leak
fix hooked `_graceful_stop_all()`, which only runs on `/api/system/restart`.
cloudflared is spawned detached (`CREATE_NO_WINDOW`), so **any** other exit —
crash, Task-Manager kill, closing the console, a power cut — orphans it, and
nothing on the next start ever looked for leftovers. There was no `atexit`
hook, no signal handler, and no reaper. One orphan per unclean exit, forever.

Two layers now:

- **PID ledger + `reap_orphans()`** (`~/.clayrune/cloudflared_pids.json`,
  atomic write). Every spawn records its PID; `start()` reaps the ledger
  *before* spawning. Survives any exit, including SIGKILL, because it doesn't
  depend on our teardown running at all.
- **`atexit` teardown** for the ordinary-exit case.

We only ever kill PIDs **we recorded**, and only after `_is_cloudflared_pid()`
confirms the PID is still a cloudflared image — a recycled PID must never be
killed, and `test_reap_never_kills_a_recycled_pid` asserts exactly that.
A kill that fails stays in the ledger so the next start retries rather than
leaking it permanently.

Cleaned up live: **24 → 1** connector (the live server's own), conhost 54 → 33,
875 MB returned. Tunnel stayed up throughout — `/api/remote/status` reports
`online: true`. Tests: `tests/test_cloudflared_reaper.py` (8).

## [2026-08-08] — a worktree chat that outlived its worktree wouldn't open

The "generate the Claydo figure image" chat sat in the rail with its 2 turns and
its timestamp, and clicking it did nothing.

**Root cause: the list and the open path resolved worktree transcripts by
different means, and only one of them survives cleanup.** An agent isolated into
`<project>/.clayrune/agents/<sid>` makes the CLI write its transcript to
`~/.claude/projects/<encoded>--clayrune-agents-<sid>`. `list_sessions()` finds
those by **globbing `~/.claude/projects`** — filesystem-independent, so the chat
is listed. The open path (`ClaudeRuntime.transcript_path` → the fallback in
`mc.memory._find_transcript_file`) instead **enumerated the local
`<project>/.clayrune/agents/*` dirs** and re-encoded each one. That worktree is
reaped when the session ends; the transcript is not. Once the directory was
gone there was no candidate to re-encode, so `/transcript/<csid>` and
`/session/<id>/reconstruct` both 404'd and `openConversation()` has no error
path — hence the silent click.

`transcript_path()` now runs the same `<encoded>--clayrune-agents-*` glob as
`list_sessions()`, after the primary lookup misses. Being the single chokepoint,
this also un-blinds resume and Scribe for any already-cleaned worktree session.
Regression test asserts the transcript resolves with **neither** the local
worktree nor the project-encoded dir present, and that the glob can't reach
another project's worktree chats.

## [2026-08-07b] — a revived chat opened 85% truncated

Ron, on the scanner conversation he had just recovered: "cut half way through
its history, I can only scroll so much up and not seeing the actual beginning."

Four call sites build the restored chat buffer and they disagreed about how much
to keep. Dispatch-preload, `/reconstruct` and the preview endpoint all passed
`max_messages=300`; the **revival** path silently took the function default of
**40**. Measured on the live chat: 269 messages, 40 restored — **85% of the
conversation missing**, the visible history starting mid-thread on "Minute OHLC
per contract…" instead of his actual opening question.

One number now, not four: `_transcript_buffer_default()` reads
`transcript_buffer_max_messages` (default 300) and every path shares it. The
same chat now opens on "I think the daily tracker email has an in built flaw…" —
the real beginning.

**And when history genuinely is cut, the buffer now says so.** Scrolling up and
simply running out of conversation is indistinguishable from having lost it,
which is why this was reported as data loss rather than a display limit. The
notice names the count and states that the agent still has the full
conversation — because it does: `claude -r <csid>` restores complete context to
the model regardless of what the buffer shows. That gap between what the agent
knows and what the user can see is the whole bug.

Not related to yesterday's steward split, though the symptom rhymed: that was a
resume-target choosing the wrong session, this is a display cap.

## [2026-08-07] — "I keep losing chats": they were there, labelled by the wrong end

Ron could not find a conversation with the Day Trading Scanner about the daily
email and P&L, in either the chat list or Topics. Nothing was lost: the chat was
sitting at **row 5** of the rail the whole time.

**The row title was the LAST user message.** That chat opens with "wait... the
daily email should also do the same" and had since wandered onto institutional
accumulation — so its card read "I agree, the intention is to measure over
time". A chat is remembered by what you *opened* it with, not by whatever was
said most recently, and a long conversation drifts away from its own label.

**And the rail's instant filter tested only that same title**, so typing
"daily email" matched nothing while the chat was on screen. That is what turned
a labelling problem into a "the chat is gone" problem.

Three changes, all display-side:

- Row title (and thread title) now use the conversation's **opening** message,
  preamble-stripped, falling back to the old label when there is no usable one.
- The sub-line carries the **latest** turn, so the row still answers "where did
  this get to?" alongside "what is this about?".
- The filter matches **both ends** of the conversation plus the recent line.

`_bestConvLabel` deliberately keeps its last-message behaviour: the `_keep()`
noise filters (trivial-ack, resume-nudge, agent-label) are tuned against it, and
re-pointing those at the opening message would change which conversations are
hidden — a far riskier edit than a title.

Verified against the real project: the chat now displays as "wait... the daily
email should also do the same", and the filter returns it (3 rows, was 0).

**Worth noting what this was NOT.** The first suspicion was my own auth-probe
filter hiding real chats, or the 20-item `/conversations` window dropping them.
Neither: 46 real user chats exist in that project, only 5 fall inside the
window, and the agent-log merge was correctly surfacing all 65 rows. The data
layer was doing its job; only the label was wrong.

## [2026-08-06i] — the Topics toggle reaches mobile

Shipped on desktop only, which left phones on the 128-row chat list the digest
exists to replace — the surface where the saving matters most. Mobile's Layer-2
list is the same surface in a different shape, not a different feature.

`_mode` is now computed once at the top of `agentPanelHTML` and used by both
paths, rather than declared inside the desktop branch where mobile could not see
it (and where a second declaration would have drifted). The desktop rules are
scoped to `.agent-3pane`, which does not exist in the phone layout, so the
toggle and topic rows carry their own mobile CSS with bigger touch targets.

One guard worth naming: `_mobileListMode` required a non-empty conversation
list. In topics mode the list stands on the digest instead, so that condition
would have dropped you into the composer with no way back to the list on a
project with no user chats.

Verified at 390x844: toggle renders, tapping Topics swaps 5 conversation rows
for 14 topic rows, no horizontal overflow, no JS errors, "+ New conversation"
still pinned to the bottom edge.

**And the session-end auto-refresh is confirmed live.** It was the one piece
that could not be tested on demand — it only fires when a chat ends. The digest
now reads `generated_at 2026-08-07T13:04:54Z` with 14 topics, replacing the
12-topic one written by hand the day before. Nobody pressed Refresh.

## [2026-08-06h] — topic rail: recency order + "you are here"

Two changes to the Topics rail, both about orientation.

**Sorted by last touched.** The rail kept the digest's own order, which is
whatever the synthesizer emitted — so the topic you were working in five
minutes ago could sit below one last touched in March. Each topic now resolves
its chats to the newest timestamp among them, preferring the live conversation
row's mtime and falling back to the timestamp the digest cached for that chat,
so a topic whose chats have aged out of `/conversations` still sorts sensibly
instead of sinking. Ties keep digest order, so the list doesn't reshuffle
between renders.

**The topic containing the open conversation is highlighted**, with the same
treatment a row gets on hover plus an accent edge (so it stays readable when
the pointer is elsewhere) and a small "you are here" marker. Matched on the
claude session id — the identity that survives the mc-session churn a resume
causes — which is the same basis the chat rail uses to pick its active row.
Switching Chats to Topics now shows you where you are instead of making you
hunt for it.

Verified by driving the real UI, including the check that matters: the
highlight *follows* the conversation. Opening a chat from a different topic
moves it, rather than sticking to the first row — which is what a naive
implementation and a passing eyeball test would both look like.

## [2026-08-06g] — the digest was clustering by opening prompt, not by subject

Ron asked a question that turned out to be a bug report: "where will this
conversation fit in the topic bar?" It fitted badly.

**Automation prompts were seeding the clusters.** `_gather_signals` used each
chat's first user message as its topic seed. For a scheduled or steward run that
is a boilerplate operating prompt, near-identical across every such session — so
the clusterer grouped unrelated work by how the sessions START. Measured: an
entire load-time investigation was filed under "Autonomous agents", because that
work had been appended to a transcript opening with `[Steward cycle] …` — the
same first-message-wins defect that mislabelled the conversation list, surfacing
in a second place. Such a chat now seeds from its completion summary, falling
back to what the user actually typed. The pattern is pinned to
`steward/fence.py: STEWARD_MARKER` by test, matching the discipline in
`test_distiller_safety.py`.

Effect on the real digest: **5 topics became 12**, "Autonomous agents" shrank
from 20 chats to 15, and the two halves of one split thread finally clustered
together instead of landing in unrelated buckets. Synthesis also got faster
(157 s to 59 s) — the seeds are shorter.

**The Refresh button was not broken, it was silent.** The synthesis is a
synchronous request measured at 157 s on this project; `reviewTopics` painted
its loading state onto the topic *board* but not the rail, so the rail's button
did nothing visible for two and a half minutes. It now paints the rail too, and
says how long it takes rather than showing a bare spinner.

**An in-progress chat no longer marks the digest stale.** A conversation you are
currently in has its transcript touched every turn, so staleness counted it and
the "Out of date" banner would have been on for the entire duration of any
active chat — a banner that is always on is one you stop reading. Only settled
chats count (`topics_settle_seconds`, default 180); the live one is re-clustered
when it ends, which is exactly when auto-refresh fires.

## [2026-08-06f] — the topics digest now follows the chats

The digest had exactly one writer: a button. So it aged silently — nine days and
177 chats out of date on this install while the UI called it fresh. It now also
refreshes on **session end**, alongside Scribe / Distiller / Beacon in
`_write_session_memory`.

**Session end, not a nightly job.** A clock refreshes when nothing has happened
and fails to when a busy afternoon has; session end *is* the event the digest
cares about ("a chat changed"), and it costs nothing on an idle project.

**Not the coordination loop, either** — worth recording, since that was the
other candidate. It only acts on projects with >= 2 live agents, so a
single-agent project would never refresh. What the coordination layer does get
is the *result*: a completed refresh publishes a `TOPICS_REFRESHED` event to the
per-project event log and SSE stream, so it lands in the same channel as every
other cross-agent signal instead of being a silent file write.

Four gates, because the thing being gated is a model call:

- kill switch (`topics_auto_refresh_enabled`, default on)
- **only if a digest already exists** — no cache means the user has never opened
  Topics for that project, and building one unbidden would be a model call per
  project on the first session after an update, for a surface they may never use
- only if genuinely stale (the real `_staleness`, not a timestamp guess)
- per-project debounce (`topics_refresh_min_interval_seconds`, default 900) so a
  burst of short sessions is one refresh, not five

**Wired, not imported.** `mc.memory` must never import a blueprint — an
invariant `tests/test_memory_module.py::test_import_smoke` enforces, and which
caught the first version of this. Beacon can `from beacon.hooks import ...` only
because beacon is a top-level package. The hook is passed through
`memory.wire(topics_refresh_hook=...)` instead; `None` = no-op.

Same never-load-bearing posture as its neighbours, and the worker is wrapped so
nothing can surface as an unhandled exception on a daemon thread next to a
completion it had nothing to do with.

## [2026-08-06e] — Tier 3 step 1: the rail can show topics instead of chats

A `Chats | Topics` toggle at the top of the conversation rail. Same rail, same
click targets; only the unit changes. Measured on mission_control: **121 chat
rows becomes 12 topic rows**, and the modal drops from 1,613 elements to 524.

Offered as a toggle rather than a replacement, deliberately. The digest is
Haiku-synthesized and best-effort, so the chat list has to stay one click away —
and the two are worth comparing side by side before either becomes the default.
The preference is global rather than per project: it is a way of working, not a
property of a project. Clicking a topic opens the newest chat in it, falling
back to the first listed when none are in the loaded conversation cache.

A topic row shows its gist, which is what makes the digest navigable without
opening anything, and the row carries the chat count. Search filters topics by
title and gist in this mode (the transcript-content search behind the chat list
does not apply to a 13-row digest). The full topic board is still reachable from
the toggle.

**Fixes a regression the staleness work introduced.** `_loadTopics` did
`topics: d.stale ? null : d.topics` — survivable only while `stale` meant "no
cache at all". Once staleness became honest, that expression would have blanked
the board almost permanently and hidden a digest that is still the best map
available. It now keeps the topics and shows an "Out of date / Refresh" bar
instead — the UI half of the backend fix, and the thing that makes the digest
trustworthy: it admits when it isn't current.

Still the open Tier 3 question: nothing refreshes the digest automatically.
`POST /topics/refresh` remains the only writer. The banner makes that visible
rather than fixing it.

## [2026-08-06d] — the auth probe was manufacturing conversations

Ron: "the single conversation thread I just had with you to repair the loading
time got split into a few separate conversations, and the continuation is now
under the Steward chat." Two unrelated bugs produced that one symptom, and the
transcripts on disk showed the thread was never actually split.

**1. `claude -p ok` was writing into a project's chat history.**
`_run_claude_auth_probe()` shells out to `claude -p ok --max-turns 1` — and the
CLI writes a full transcript for *every* invocation into
`~/.claude/projects/<encoded CWD>/`. The probe passed no `cwd=`, so it inherited
the **server's** working directory: the Clayrune checkout, which is itself an MC
project. Every probe therefore deposited a real `.jsonl` in that project's
transcript dir. `/conversations` reads transcripts directly, so each one
surfaced in the chat rail as its own conversation (turn 1 `ok`, turn 2 "I'm here
— what would you like to work on?"), and the startup backfill synthesized an
`interrupted` agent-log row for it. **Six accumulated in one day**, interleaved
with the real chat by timestamp.

Fixed by running the probe in a scratch dir (`data/_auth_probe`, outside
`DATA_DIR` per the pollution rule) so its transcripts land under a path that is
nobody's project. Leftovers already on disk — here and on every install — are
hidden from the conversation list, but **only when MC has no record of
dispatching them**: no live session, and no agent-log row that wasn't itself
synthesized by the backfill. A real one-word "ok" chat that MC ran still shows.

**Follow-up (same day):** redirecting the probe fixed the pollution but moved
the accumulation — a probe fires on page boot, so the scratch dir grows by
~6 x 48 KB a day on an active install. It is now capped at the 3 newest.

That prune shipped broken first and the test exists because of it: MC's
`_encode_project_path` keeps underscores (`..._claude-...-data-_auth_probe`)
while the CLI writes the dashed variant (`...--claude-...-data--auth-probe`),
so the first version pruned a directory that does not exist and reported
success. Only counting files in the real directory before and after caught it.
Use `_encoded_dir_candidates` — the same helper `list_sessions` uses — for any
code that has to find a transcript directory.

**2. A manual message silently continued the steward's session.**
`getDefaultResumeId()` returned the newest agent-log row, whatever it was. A
steward cycle finished at 12:54 and left `f9774c2a` on top — so the next thing
typed in that project resumed *it*, and the loading-time investigation was
appended to the steward's transcript. The list labels a conversation by its
FIRST user message, so the whole thread then displayed under `[Steward cycle]`.
Nothing was lost; it was filed under the machine's name.

Scheduled / steward / night-review sessions are the machine's threads and are no
longer auto-continued — they stay one explicit click away. `synthesized` rows
are skipped too: defaulting into a transcript MC never dispatched resumes a
session with no shared context, which (before fix 1) included every auth probe.
With no attended history at all, a new message now starts fresh rather than
joining a machine thread.

## [2026-08-06c] — Tier 2: the 220 ms endpoint, and two items declined on evidence

Follow-on to Tier 1. Three items shipped, two declined with the measurement that
decided them, and one earlier finding corrected.

Verified live after restart:

| | before | after |
|---|---|---|
| `list_sessions(limit=20)` | 258.5 ms | **2.4 ms** (identical output) |
| `/conversations?limit=20` | 220 ms | **29 ms** |
| warm reload, wire bytes | 2,286 KB | **172 KB** |
| warm reload, `/static/` cached | 0 of 43 | **43 of 43, 0 bytes** |
| warm reload, first tile | — | **92 ms** |

**`list_sessions()` is now cached per (path, mtime, size).** The investigation
under-sold this one: `/conversations?limit=20` measured **220 ms** — the slowest
endpoint in the app, paid on every project-modal open — because it re-opened the
newest 20 of 207 transcripts and JSON-parsed every line of each, every call.
Transcripts are append-only, so `(mtime, size)` pins content exactly; a hit is
correct, not merely likely. Bounded at 512 rows, handed out as copies so a
caller can't poison the cache. Test covers both halves: a hit must not re-open
the file, an append must not return a stale row.

**Versioned static assets are `immutable`.** `/` already injects
`?v=<asset_version>` (newest static mtime, so any change moves every URL) into
every `/static/*.js|css` reference — exactly the precondition for
`max-age=31536000, immutable`. All 41 JS + 2 CSS files previously carried
`no-cache` and cost a revalidation round trip per load. **The gate matters more
than the header**: `immutable` is granted only when `?v=` is present, so a bare
URL can never pin a client to an asset no deploy can reach. `/assets/*` (no
`?v=`) gets a bounded `max-age=3600` instead, which stops `claydo-idle.webp`
being fetched twice per boot.

**Declined, with numbers:**

- *Caching `load_projects()`*: read+parse is 24.4 ms of a ~28 ms endpoint, but
  it is a 30-second poll — ~0.08% of a core. Meanwhile every caller gets a
  mutable dict and several mutate in place, so a correct cache needs deep
  copies, and deep-copying 5 MB of nested dicts in Python costs more than
  `json.loads` does in C. Removed the dead first sort while in there.
- *Swapping Werkzeug for waitress*: the item most likely to help the phone
  (`Connection: close` means a fresh connection per request, and the tunnel is
  where that hurts) — but waitress buffers responses by default and this app is
  built on SSE. Measure a real phone over the tunnel first; don't risk the core
  feature for an unmeasured cost.

**Correction to the investigation's Root cause 6.** The duplicate `Date` header
is not an app bug and my first fix for it was a no-op. Flask emits exactly one
(`send_file` sets it); the second comes from the WSGI server *below* the WSGI
layer, unreachable from `after_request`. The working fix is for the app to
contribute none on static paths. It is cosmetic either way — these responses now
carry explicit `Cache-Control`, which overrides the heuristic caching the
malformed header set was suspected of blocking.

## [2026-08-06b] — load time: 420 requests → 73, modal DOM 8,270 → 1,613

Tier 1 of `docs/LOAD_TIME_INVESTIGATION.md`, all six items. The investigation's
headline held up: the server was never slow (boot 0.24–2.62 s, every endpoint
<100 ms) — the client was, and its cost scaled with accumulated history.

| | before | after |
|---|---|---|
| cold-boot requests | 420 | **73** |
| cold-boot bytes | 6,900 KB | **3,137 KB** |
| requests to the public internet | 353 | **5** (fonts) |
| modal DOM (mission_control) | 8,270 elements | **1,613** |
| one modal re-render | 41.1 ms | **13.5 ms** |
| `backlog` in `/api/projects` | 687 KB | **6.4 KB** |

- **`/api/projects` ships a backlog summary, not the backlog.** It was 91% of
  that 789 KB response, re-sent on boot and every 30 s, per open tab, to render
  three scalars. Now `backlog_open_count` / `backlog_done_count` /
  `backlog_total_count` / `backlog_next_text`. Everything that renders item
  bodies already lazy-loaded them per project; the cross-project view now does
  the same on open. Two tests guard it — putting the array back would silently
  re-inflate every boot.
- **Diagram / terminal / QR libraries load on first use**, not in `<head>`.
  Excalidraw alone was 327 of the 353 external requests (esm.sh serves every
  transitive dependency as its own module). Since the bridge is no longer warm
  when the first diagram lands, the renderer waits for it with an 8 s bound, and
  a new `excalidraw-failed` event ends that wait immediately on an offline
  network rather than stalling before the Mermaid fallback.
- **In-flight guards on `loadConversations` / `loadAgentLog`.** Their guard was
  `if (!cache[id])` and the cache is written *after* the fetch resolves, so
  every render in the request window fired another — and each completion called
  `refreshModal()`, which re-rendered, which re-entered the guard. Measured 7×
  on `/conversations` and 2× on `/agent/log` (1,631 KB each). Now 1 and 1.
- **`_resyncOpenModalsFromServer` no longer fires at boot.** It was bound to
  `pageshow`, which fires on every load, not just a bfcache restore; it now
  checks `event.persisted`. It also early-returns when nothing is open and
  nothing is streaming — the grid refresh on resume is kept, the heartbeat probe
  and SSE sweep for zero sockets are not.
- **Inactive tabs are not built.** 6,001 of the modal's 8,270 elements were the
  Agent Log tab and 630 the Backlog tab — ~84% behind a tab nobody was looking
  at, rebuilt via `innerHTML` on every SSE turn event and poll tick. Every tab
  switch already re-renders, so gating is invisible. The `agent` panel is
  deliberately exempt: it owns the live streaming nodes that `refreshModalById`
  works to carry across the wipe.
- **Agent Log pages at 25 rows**, and a row's continue-composer is built when
  opened rather than pre-rendered hidden — it was 500 live `<textarea>` + send
  buttons in a closed panel.

**One claim in the investigation is now settled, in the good direction.** It
could not determine whether a real browser warm-caches the 353 esm.sh modules
and refused to assume; Ron read the Network panel and the rows show
`(disk cache)` at 0 KB. So that was a first-visit / post-update / offline tax
plus ~350 main-thread cache lookups per boot — not the per-reload byte tax it
looked like. Worth removing, correctly re-ranked.

Not fixed, and named rather than buried: the conversation rail is still 1,147 of
the remaining 1,613 modal elements — 126 rows derived from the 500-row agent-log
merge. That is Tier 3 (promote Topics) and needs the product decision.

## [2026-08-06] — agents didn't know the browser pane existed

The browser pane has shipped for months, and no agent has ever been told about
it. It appears nowhere in `_clayrune_universal_capabilities()` and nowhere in
the injected `CLAYRUNE_API.md`. So the entire capability was invisible from
inside a session: asked to visit a page, an agent reached for the only browser
it knew about — `start` / `open` / `xdg-open` / `webbrowser.open` — which opens
a window on the host machine that the user may not be sitting at and that the
agent can neither see nor drive. Ron had to name the pane by hand every time.

Fixed by documenting it in the two places every agent reads:

- **`_clayrune_universal_capabilities()`** — one `Browser:` entry covering when
  to use the pane over the host browser, the two chat markers
  (`[browser:<url>]` to show a page, `[browser-attach:<sid>]` to surface a
  session the agent launched), the `input` / `selection` / `status` / `stop`
  endpoints, and named profiles for anything you sign into. Being the universal
  block, it reaches hivemind workers too, not just project agents.
- **`data/agent_reference/CLAYRUNE_API.md`** — a `## Browser pane` section
  alongside Terminal pop-out. The reference is curated, not generated
  (`tools/regen-api-reference.py` reports drift, it doesn't overwrite), so the
  section had to be written; the browser routes had simply never been added.

Both state the limit as plainly as the capability: the pane is a viewing and
interaction surface, **not a scraper** — there is no read-whole-page endpoint,
only the current selection, so reading content for the agent's own reasoning
stays with WebFetch/WebSearch. Without that line the next failure mode is an
agent trying to scrape through a screencast.

Takes effect for new sessions. The `CLAYRUNE_API.md` half is read from disk per
session (live now); the `agent_routes.py` half needs a server restart.

**And the API reference was never shipping at all.** `data/agent_reference/` was
gitignored on the stated belief that `tools/regen-api-reference.py` rebuilds it.
It doesn't — that tool only *reports drift*; the doc is hand-curated and nothing
on disk regenerates it. Nor did `build-macos.spec` bundle it. So the entire
reference existed on exactly one machine, and every other install — git-cloned
or frozen `.app` — ran with `_clayrune_api_reference()` returning `''`, i.e. no
API block in any agent's system prompt, silently. Same shape as the
`origin/master`-138-commits-behind case: nothing warns you, it just works here.
Now tracked in git and added to the macOS spec's `datas`. It contains no
operator-specific content (checked before un-ignoring — that's the rule that
made it suspect in the first place).

## [2026-08-05d] — closing out the memory subsystem: two false alarms and one real blocker

A sweep to repair everything known broken in memory/indexing/search. Two of the
things I had reported as broken turned out not to be, and finding that out
produced the one fix that mattered most.

**"Condense is broken — 22% success rate" was wrong.** `scribe-stats` counters
are lifetime totals with no recency signal, so `condense_rejected:model_error`
96 + `model_timeout` 58 against 46 successes reads as a subsystem in freefall.
It isn't: 91+58 of those failures predate the 2026-07 switch to the haiku
default (the code comment recording that switch cites the exact figures), so the
post-fix record is ~41 successes to 5 errors. A direct `_condense_plan` call
against the real 16.5KB MEMORY.md returned `ok` in 58s. Condense is healthy.

**"The diagnostic logs aren't captured" was also wrong.** `[scribe]` (51 lines)
and `[distiller]` (718) reach the log fine. Zero `[condense]` lines means zero
condense *failures* in that window — the success path logs nothing.

Both misreadings came from the same missing thing, so that's what got fixed:
**failure-class counters now self-date.** `_scribe_stat` stamps
`counters_last[key]`, exactly as `distiller._increment_counter` already did —
its docstring describes this precise trap ("is this still bleeding, or is it a
six-week-old backlog?"). The scribe half never got it. The same change makes the
read-modify-write locked and atomic; it was a plain `write_text` that could lose
a concurrent bump or leave a torn file, and a torn file 500s `/scribe-stats`.

**`read_floor_topk` 3 → 6.** Now that BM25 makes the ranking trustworthy, more
slots are worth buying: replaying 142 real tasks, topk=3 reaches 40/75 topic
files and topk=6 reaches 59/75. The dark set more than halves for ~300 extra
tokens per prompt against a ~6k-token index. The knee is at 5–6; beyond 8 it
flattens.

**The BM25 "regression" wasn't one.** `remote_access_device_naming.md` ranks #1
by a wide margin when actually queried — it simply never came up in the 142-task
sample. Sample coverage, not a ranking defect.

### A third false alarm, caught by re-validating: the index is not load-bearing

An earlier draft of this entry claimed the two-level index redesign was blocked
because 37 of the 95 front-page pointer lines (5.9KB) carry no `.md` link, so
collapsing them under a category heading would *delete* knowledge rather than
page it out. **That was wrong**, and it is recorded here because it was nearly
shipped as a design constraint.

The link count itself held up on re-check (37 lines genuinely have no markdown
link and no `[[wikilink]]` either). What didn't hold up was the inference. Two
successive tests were needed to see it:

- Comparing each line's vocabulary against whole topic files said "all 37 are
  covered" — worthless, because common words dominate the overlap.
- Restricting to *distinctive* tokens (document frequency ≤ 3) and matching
  against the **actual retrieval units** — archive lines are indexed
  individually, not as one 734KB document — gave the real answer: **33 of 37
  are recoverable from a single retrievable unit**, nearly all of them
  `MEMORY_ARCHIVE.md` entries.

Reading the matched entries settles it. The index line `IPv6 localhost
resolution stalls ~200ms… fix: dual-stack socket (AF_INET6+IPV6_V6ONLY=0)`
matches an archive entry that records the same fix *in more detail*, and the
same holds for the `wake_lock.py` and `cmd.exe 8191-char` lines. The curated
index is a hand-written summary layer over facts the archive already holds —
not a sole copy. Only 4 lines fall below the recovery threshold, which is a
small tractable set, not a blocker.

So the redesign is **not** blocked on promoting those lines, and the earlier
"prerequisite" is withdrawn. What remains true: `_memory_search` excludes the
curated region by construction, which is correct while the whole index is
auto-loaded and would need revisiting if anything is ever paged out.

With BM25 + topk=6 the read floor reaches 59/75 files unaided, and the front
page's navigation function is measurably barely used (agents open a topic file
in 6% of sessions, search in 3%). Whether 16.5KB of pointers still earns its
place every prompt is a live cost question — but it is only a cost question.

Cross-corpus check that should have happened before shipping BM25 and now has:
on the DayTrading (58 notes) and clayrune-website (20 notes) corpora, the
largest file wins top-1 on 0/12 and 1/12 queries respectively — no length
dominance, so the ranker isn't overfit to this repo's corpus.

7 tests in `tests/test_scribe_stat_recency.py`; suite green at 1217.

## [2026-08-05c] — the memory corpus wasn't cold, it was unreachable

Chasing the memory-index size problem (`[2026-08-05b]`) to its root. Ron
corrected the premise first: the ~24KB limit was never a harness cap, it's a
token budget we chose — so nothing was ever being silently lost, and trimming
harder was never the answer. That reframed the question from "how do we shrink
the index" to "why does the index have to carry 69 pointers at all", and the
answer turned out to be measurable from data already on disk.

`_memory_search` is a pure deterministic ranked search, so the read floor can be
replayed exactly against the first user message of every past session. Over 142
real dispatched tasks from 209 transcripts:

| scorer | topic files reachable | dark | top-3 units' share of slots |
|---|---|---|---|
| old (raw term frequency) | 17 / 75 | 58 | 93% |
| BM25 | 40 / 75 | 35 | 53% |

**55 of 75 notes never entered a top-3 for any task we have ever run.** The
three files winning almost every query were, exactly, the three *largest* topic
files (11.5–19.4KB against a 3.2KB median). The scorer was
`sum(text.count(term))` with no document-length normalization, so a long file
wins on ordinary words alone. Length was beating relevance, and the corpus only
looked cold.

That also settles a live design question: the proposal to evict notes by access
count would have read those 55 files as dead and deleted them, when what it was
measuring was a length bias in the ranker.

`_memory_search` is now BM25 (IDF + saturation + length normalization), with one
adaptation. Textbook BM25 assumes a homogeneous corpus; ours mixes whole topic
files with ~30× more numerous one-line archive entries, so a single global
`avgdl` would be set by the short lines and score every topic file as
pathologically long — the mirror image of the bug. Length is normalized **per
unit class**, IDF stays global. A topic file's own name is indexed into its
token stream (boosted ×3), which is what lets a note match on its title alone —
a post-hoc filename bonus can't, because a document with no body hit is never
scored at all. The tokenizer splits on underscores so `memory_system` is
reachable from "memory system".

Two measurement traps, both paid for and written down in
`tools/memory-eval/README.md` so nobody re-derives them: the injected read-floor
block is **not** in transcripts (it arrives via `--append-system-prompt-file`),
so grepping for it scores only the current session echoing itself back; and
taking every session's first user message verbatim includes openers like `ok`,
which produced a confident, wrong "29% of tasks surface nothing" — the real
zero-result rate is 0% for both scorers.

The probes ship as `tools/memory-eval/`. They are the search-precision telemetry
the standing gate asked for, with no new subsystem: rerun `scorer_ab.py` after
any retrieval change and it reports reachability before/after.

Still open, and still the real problem: the curated region is 69 flat pointers
resident every prompt, O(topics). But the growth was downstream of this — the
front page has been doing retrieval's job by hand because retrieval didn't work.
Fix the ranker first, then re-measure, then size the index.

11 tests in `tests/test_memory_search_bm25.py`; suite green at 1210.

## [2026-08-05b] — the session log was crowding out the memory it was meant to keep

A steward cycle filed a backlog note (`dae8d6e7`) saying the memory index is
structurally doomed: `MEMORY.md` is 23.5 KB against a ~24 KB harness read cap,
16.2 KB of that is the curated region that machinery is not allowed to touch,
and everything below the cut vanishes from agent context with no error. All
true, and verified — `_INDEX_BYTE_FLOOR` is 23 KB, so eviction is already
running on every write. The note proposed a two-level index. That redesign is
still the right end state, but reviewing the actual file first turned up
something cheaper and more urgent: **the managed half wasn't full of history.
It was full of the same session, sixteen times.**

Every one of the 16 managed entries was a same-day steward `_(live)_`
checkpoint — 6.2 KB of a 6.9 KB region. Two independent causes:

- **Titles were the raw prompt.** The entry title is `task[:80]`, and a
  harness-generated steward task starts with 85 characters of boilerplate
  (`[Steward cycle] You are the autonomous STEWARD of this project — run ONE
  cycle n`). Sixteen entries, sixteen identical titles, 1.4 KB describing
  nothing. `_entry_label()` now collapses a *leading* known harness marker to
  its short form; a task that merely mentions steward cycles keeps its own
  title verbatim.
- **Checkpointing appended where it meant to replace.** Step-6 folds each
  transcript delta into a *cumulative* `running_summary`, so every `_(live)_`
  entry is a strict superset of the one before it — but each was written as a
  new line. One long session emitted N entries carrying one session's worth of
  information. `_commit_managed_entry(supersede_sid=…)` now drops that
  session's previous entry in the same atomic write, found via a
  `last_entry_hash` stashed on its watermark record. The
  self-contained-breadcrumb property that made appending look correct is kept:
  the newest entry is always complete on its own, so a hard kill still leaves a
  valid one behind. The terminal completion entry supersedes the last live
  checkpoint too, instead of sitting next to it repeating it.

A third guard covers the general case: `_collapse_duplicate_entries` keeps the
newest three entries per `(date, label)` group *before* the byte floor runs.
The floor evicts strictly oldest-first, which is blind to repetition — that's
how a burst of same-day cycles was pushing real multi-day history into the
archive. Surplus duplicates are demoted to the archive verbatim, not deleted;
superseded checkpoints are the one exception, since the surviving entry already
contains their text and archiving them would just re-add the bloat elsewhere.
Legacy boilerplate titles fold into the same group as the new short labels, so
the existing pile-up collapses on the next write rather than needing a
migration.

Net effect on this repo: ~5 KB of the managed region returns to being usable,
and it stops refilling. The curated-region problem the note identified is
untouched by this — that still needs the two-level index, and the standing gate
(`decision_step7_semantic_search_deferral`: build search-precision telemetry
before leaning harder on retrieval) still applies. One correction to the note's
second proposal, recorded on the backlog item: evicting by access count is not
independent of its own stated label risk, it compounds it — a badly-labelled
note never gets opened, so the counter reads it as cold and evicts it. Log
access statistics for observability; don't wire them to eviction until
discovery is known-good.

14 new tests in `tests/test_memory_entry_dedup.py`; suite green at 1199.

## [2026-08-05] — the browser pane keeps your logins, and takes a paste

Two unrelated-looking complaints about the browser pane, both real, both with
one-line causes.

**Saved profiles never actually saved anything.** A named profile was supposed
to be the thing you log into once — and the directory did survive teardown, and
`/api/browser/profiles` listed it with a plausible size, so it looked like it
worked. But `_kill_browser_session` ended Chromium with `proc.kill()`, and
Chromium keeps cookies and localStorage in memory, writing them on clean
shutdown or on a lazy ~30s timer. Killing it right after a login threw that
login away. Measured on the dev box, same profile dir, same launch args:

| teardown | cookie | localStorage |
|---|---|---|
| `proc.kill()` immediately | lost | lost |
| `proc.kill()` after 45s | survived | survived |
| `Browser.close` | survived | survived |

The middle row is why this read as flaky rather than broken: leave a pane open a
while and the login sticks; close it right after signing in — the normal thing
to do — and it's gone. Teardown now sends `Browser.close` for named profiles
and only hard-kills as a fallback (the process exits in ~0.1s, so nothing got
slower). Throwaway profiles are still killed outright — their dir is deleted
moments later, so there is nothing to flush.

**Ctrl+V did nothing.** The pane called `preventDefault()` on the Ctrl+V
keydown and then read the clipboard via `navigator.clipboard.readText()`. But
cancelling that keydown also cancels the native `paste` event — the only
clipboard path that needs no permission, works over plain http on the LAN, and
exists in every browser. So the pane suppressed the reliable path in order to
use the fragile one, and pasting failed with "Paste blocked …" wherever
`readText()` was unavailable or previously denied. The `paste` event is now the
primary path, `readText()` is the 200ms fallback, and a 📋 toolbar button covers
touch (which has no Ctrl+V at all) by pasting into a prompt box.

Also in this change:

- **`browser_default_profile`** (config, default `''` = unchanged behaviour).
  Set it to a name and unnamed launches — including the 🌐 Browser button —
  reuse one persistent signed-in profile instead of starting logged out every
  time. `POST /api/browser/launch {"ephemeral": true}` opts a single launch out.
- **`sweep_orphan_profiles()` is now server-process-only**
  (`browser_routes.SWEEP_ENABLED`). It decides what is an orphan by diffing the
  throwaway root against this process's `browser_sessions`, and only the server
  has the real registry — so importing the module in a script and launching a
  browser deleted live panes' profile dirs out from under them. That happened
  while diagnosing this very bug.
- **`tools/smoke/browser-paste.mjs`** — loads the real module, fires a genuine
  Ctrl+V, asserts the text arrives exactly once. Confirmed failing on the old
  code before the fix. Wired into `npm test`.

## [2026-08-04] — toasts coalesce, and you can swipe them away

Six agent-opened browser sessions produced six separate toasts, each a
full-width card on a phone. Stacked, they covered the entire screen with no
gesture to clear them: `#toast-container` had **no mobile rules at all**, so a
360px desktop card ran nearly edge to edge, and nothing capped the stack.

Three changes, all in the toast layer:

- **Keyed coalescing.** `showActionToast(msg, actions, {key})` now *replaces*
  the live toast carrying the same key instead of appending a second one. The
  browser-session poller (`_bpNotifyNewSessions`) uses one key and counts up —
  "6 browser sessions opened / Newest: …", with **View newest** opening the
  most recent. The count resets once that toast is gone, so a later single
  session doesn't announce itself as #7.
- **Swipe to dismiss, either direction.** Any toast follows the finger past
  70px and leaves. Vertical intent is handed back to the page (`touch-action:
  pan-y`), so scrolling still works, and a tap still hits the buttons.
- **Stack cap + mobile styles.** At most 3 toasts are on screen, oldest
  evicted first; ≤960px gets a full-bleed, compact card with a taller tap
  target on the action buttons.

Verified in headless Chromium at 390×844: 6 keyed toasts → 1, flood of 8 → 3,
swipe left and right each dismiss, vertical drag does not, buttons still fire.

## [2026-08-02] — the scribe records the why, not only the what

Session memory captured events and nothing else: *"fixed the mobile toast,
committed 92464ab"*. A later session reading that learns the fact and not the
lesson — the cause has to be rediscovered from scratch every time.

Meta's Hyperagents paper (arXiv `2603.19461`, March 2026) is the prompt for
this. Left to self-improve across four unrelated domains, their agents kept
independently inventing the same memory shape: not raw scores but **"causal
diagnoses and forward-looking plans"** — an entry reasoning *why* a generation
regressed and what to do about it. That is the memory type that compounds.
Teardown and the rest of the reading: `docs/RESEARCH_HYPERAGENTS.md`.

So the session-end scribe now asks for a second part, appended to the entry as
`_why:_ <note>`:

```
- [2026-08-02] **mobile toast** — Fixed mobile browser toast + with-secret
  encoding bug; committed 92464ab. _why:_ The toast told mobile to click a
  button the ≤960px breakpoint hides — check breakpoint parity before
  trusting UI copy.
```

Four things keep it from becoming noise or a liability:

- **Most sessions get no why, by instruction.** The prompt says routine work
  has none and an invented one is worse than none; `NONE`, stubs, and anything
  under 12 chars are dropped in `_scribe_split_why`. `scribe_why_present` /
  `scribe_why_absent` counters exist so the rate is observable — ~0% means the
  prompt isn't landing, ~100% means the model is confabulating.
- **Terminal entries only.** A checkpoint is a mid-session running summary that
  gets superseded; a diagnosis is a retrospective. `want_why` defaults to
  `False`, so the Step-6 checkpoint path is byte-identical to before.
- **No new writer.** It rides the existing scribe call (no extra model call, no
  extra cost) and the existing `_commit_managed_entry`, preserving the
  lock+atomic+shared-archive discipline. Entries stay single `- [` lines, so
  `_mem_split` / trim / condense need no change.
- **Refusal still wins.** A body that trips the refusal guard returns `None`
  and cannot smuggle a why into memory alongside it. Capped at 160 chars to
  respect the ~24KB index byte cap.

Kill switch: `scribe_why_enabled: false`. 9 tests in
`tests/test_memory_module.py`.

**Not** taken from the same paper: metacognitive self-modification — the agent
editing its own improvement mechanism — which is exactly what
`_authority_violation()` forbids, and for a reason this repo already lived
through.

## [2026-08-02] — browser profiles that stay signed in

Every browser-pane launch got a throwaway Chromium profile, deleted on teardown.
Correct for one-off browsing, and the whole cost of the feature for anything
else: an agent posting to the same social account every week faced a fresh
login — plus 2FA, plus whatever anti-bot challenge a "new device" triggers — on
every single run.

A launch may now name a profile (`{"profile": "reddit"}`), which persists at
`~/.clayrune/browser_profiles_named/<name>` and keeps its cookies. Unnamed
launches are unchanged, so ephemeral stays the default and persistence is
something you ask for by name.

Two properties keep it safe:

- **The named root is a sibling of the throwaway root, not a subdirectory.**
  `sweep_orphan_profiles()` deletes everything under the throwaway root that no
  live session owns, so a saved login nested inside it would be swept the moment
  its session ended. A filter would hold until someone edited the filter; a
  separate directory cannot be reached by that walk at all. Pinned by test.
- **One Chromium per profile dir.** Naming an already-open profile returns the
  running session (`reused: true`) instead of launching a second browser onto
  the same dir, which would corrupt it.

Closing the pane no longer signs you out; `DELETE /api/browser/profiles/<name>`
does, and it refuses (409) while the profile is open rather than deleting the
directory out from under a live browser. `GET /api/browser/profiles` lists names
and sizes — never a cookie jar, same rule the vault holds for values.

Verified end-to-end against a real Chromium and a local probe site: first visit
sent no cookie, teardown, relaunch of the same profile sent `mc_probe=signed-in`.

The honest caveat, in the module docstring: Chromium encrypts its cookie DB
under the OS keychain, but on a headless Linux box with no keyring it falls back
to a hardcoded key — the same shape as the vault's file-key-backend warning.

## [2026-08-01b] — the other half of a login: usernames

The vault stored a password and no username, which is only half a login. The
obvious workaround — two entries, `site.user` and `site.password` — makes the
pairing implicit: nothing holds them together, and drift only surfaces as a
failed login.

So a secret now carries an optional **username** alongside its sealed value,
referenced as `{{user:name}}` (`--user VAR=name` in `tools/with-secret.py`).

It is stored as **metadata, not ciphertext**, and it *is* returned by
`GET /api/secrets` and shown in the list. Deliberate: a username is an
identifier the site displays back to you, and it is what tells two accounts on
the same site apart in the panel — encrypting it would mean no route could ever
show it. Precedent already existed: a TOTP entry's `account` is returned in the
clear for the same reason. The rule that holds is unchanged — **no route returns
a plaintext value.**

- Scope *is* enforced on `{{user:…}}`; `allow_unattended` is not, because the
  password half of the same login carries that gate. An unattended run that
  learns a username still cannot log in.
- `{{user:x}}` on an entry with no username raises rather than resolving to an
  empty string — an anonymous login attempt is worse than a refused command.
- `POST /api/secrets/check` reports `no_username` separately from `not_found`:
  `{{secret:x}}` resolving is not proof `{{user:x}}` will, and conflating them
  moves the failure to mid-login.
- Usernames are not registered for output redaction — scrubbing one out of agent
  output would mangle ordinary text.

Detail: `docs/SECRETS.md`. 7 further tests (89 total across the vault).

## [2026-08-01] — 2FA codes, and the vault UI

Two follow-ups that turned the vault from a library into something usable.

**The panel (sidebar → Secrets).** Shipping API-only left the actual question
unanswered: how does a password get *in*? Typing it in the composer puts it in
the transcript; having the agent curl it in puts it in a tool call. Both are the
exact thing the vault exists to prevent, so the form — browser → server, agent
never in the path — is the missing half, not polish. It never displays a value,
not even a last-4 (a partial reveal is still a reveal), and editing metadata
leaves the value sealed. "Copy ref" copies `{{secret:name}}`, which *is* safe to
paste into chat.

**TOTP (`mc/totp.py`).** Real logins need a second factor. There is no "sync
with Google Authenticator" API and none is needed — Authenticator is a client
for an open standard (RFC 6238), not a service. Clayrune holds the same seed and
derives the same code offline. Correctness is pinned to the RFC's reference
vectors, which is what "agrees with the phone" actually means.

Three intake paths, all through the one Value field: an `otpauth://` setup link,
a bare base32 seed, or `otpauth-migration://` — Google Authenticator's own
*Transfer accounts → Export* payload, which imports every account at once
(decoded by a ~40-line protobuf reader rather than a new dependency).

- `{{totp:name}}` yields a code; `{{secret:name}}` on the same entry yields the
  seed. Separate keywords on purpose — each would fail confusingly in the
  other's place.
- `--totp VAR=name` injects a code, waiting for the next window if fewer than 5
  seconds remain, so it can't expire mid-form.
- The verify route returns *whether* a code matched, never a code. Otherwise it
  would be the plaintext hole the design refuses.
- HOTP is deliberately unsupported — dispensing counter-based codes without
  tracking the counter desynchronises the account.
- Seeds are accepted grouped/lowercase/padded, as sites actually print them.

**The tradeoff is documented, not hidden:** storing the seed beside the password
collapses two factors into one. That is inherent to unattended 2FA, but TOTP
entries are a distinct kind, badged in the UI, and can be marked attended-only.
For a bank or registrar the right answer is still to let the agent ask.

**Two real bugs caught while wiring:** `PATCH` defaulted `kind`, silently
downgrading a TOTP entry to a password on a description edit — breakage that
would only surface as a failed login much later. And `seconds_remaining`
truncated sub-second remainders to `0`, reporting a live code as expired.

Also registered the new module in `tools/smoke/boot-smoke.mjs`; the harness
aborts any module not listed there, so without that the panel would have gone
unexercised while the suite still reported green.

Detail: `docs/SECRETS.md`. 41 further tests (82 total across the vault).

## [2026-08-01] — Secrets vault: agents can log in without seeing the password

Clayrune had no credential store. The only pattern was plaintext on disk —
`~/.clayrune/night-mail.json` holds a Gmail app password in the clear, and
`data/provider_env.json` holds API keys — which meant any agent action needing
a real login either couldn't be automated or had the credential typed into a
command line, where it lands in the transcript forever.

`mc/secrets_store.py` is an encrypted vault. The agent writes the *name* of a
credential; the *server* resolves it at the moment of use.

**Design decisions, and why each is the way it is:**

- **Nothing is stored inside the repo — by construction, not by gitignore.**
  Store, master key, and audit log all live under `~/.clayrune/`. This project
  has already been bitten once by "gitignored but bundled anyway":
  `build-macos.spec` packaged `data/SHARED_RULES.md` *because it was still on
  disk* after being untracked (CLAUDE.md, 2026-07-12). A file that never exists
  under the checkout cannot be swept in by a future `git add -f`, build spec, or
  installer glob. Defensive `.gitignore` patterns are there anyway, to catch a
  hand-placed copy.
- **AES-256-GCM with the secret's name as AAD**, so a ciphertext cannot be moved
  between entries and silently hand back the wrong credential. Master key in the
  OS keyring (Credential Manager / Keychain / SecretService), degrading to a
  0600 key file only when no backend is usable — and saying so, via
  `key_at_rest_warning`.
- **No HTTP route returns a plaintext value.** Values leave the process only
  into a child process's environment or a resolved command, never back into a
  browser tab. That deletes the whole "vault page was left open / screenshotted
  / proxied" class. Pinned by `test_no_route_returns_the_plaintext`.
- **Only dispensed values are redacted.** `redact()` scans for values this
  process has actually handed out — cheap, bounded, and it means a secret that
  was never used can't be fingerprinted through the redactor.
- **A missing secret aborts before exec.** `tools/with-secret.py` exits 2 rather
  than starting the child, so an unresolved `{{secret:...}}` never becomes an
  anonymous login attempt or a literal password sent somewhere.
- **Agents may use a credential; only a human may create one.** There is no
  agent-facing write path — the same authority-guard principle the learning
  system uses: machinery must never expand the agent's own capability set.

**Policy per secret:** `scope` (global or one project) and `allow_unattended`
(steward/scheduled cycles refused when false). Per Ron's decision the default is
that agents *may* use secrets unattended; the per-task gate lives in the agent
rules, and these flags are the backstop.

**Honest limit, documented rather than papered over:** this is not a sandbox. An
agent with a shell can read whatever the server can. What the vault buys is that
credentials stay out of the durable, exfiltrating surfaces — transcripts,
MEMORY.md, distilled artifacts, logs, the repo — and that every access is
audited.

Full detail: `docs/SECRETS.md`. 41 new tests; suite green.

**Follow-ups:** UI panel for the vault; migrating `night-mail.json` and
`provider_env.json` into it (both already gitignored, so hardening rather than
exposure).

## [2026-08-01] — `/` autocomplete in the composers, from the installed CLI

Typing `/` at the start of a composer now opens a picker of the Claude CLI's
built-in slash commands, filtered as you type. Enter or Tab completes; ↑/↓ move;
Esc dismisses.

**Why it was worth building.** A mistyped or non-existent slash command doesn't
error — it degrades to plain text the model just reads, which looks like it
worked. There was no way to discover the real names from inside MC.

**Two rules the implementation is built around:**

1. **Only headless-capable commands are offered.** MC always spawns the CLI with
   `--print` (`ClaudeRuntime.build_command`). The CLI tags each command with
   `supportsNonInteractive`; of ~96 built-ins only ~32 carry it. Offering the
   rest would suggest commands that silently do nothing — the exact failure the
   feature exists to prevent. Verified empirically first: driving the CLI through
   both spawn shapes MC uses, `/cost` returns `num_turns=0` (a real local
   command, no model inference).
2. **The trigger mirrors the server's own rule.** The CLI only parses a slash
   command when it is the FIRST bytes of the turn — already encoded in
   `_SLASH_COMMAND_RE` (`agent_routes.py`). The popup fires only when the caret
   sits in a leading `/token`, so it never appears where the command wouldn't
   dispatch. `hello /go` and `/usr/bin` don't trigger it.

- **`mc/slash_commands.py` (new)** — extracts the registry from the user's own
  `claude` binary (the list is version-specific, so a list baked into MC would
  be wrong for anyone on a different Claude Code release). Cached against a
  path+size+mtime fingerprint: a cold scan of the 265 MB binary takes ~0.2s, and
  every later boot is a `stat()` (~7ms). A CLI upgrade invalidates it
  automatically. Degrades to the last good cache, then to a small hand-verified
  fallback — never to nothing.
- **`GET /api/slash-commands`** — headless subset by default; `?all=1` includes
  interactive-only entries, `?refresh=1` forces a re-scan.
- **`static/js/slash-autocomplete.js` (new)** — fully delegated from `document`,
  because `refreshModal()` rebuilds the composers wholesale and per-element
  listeners would not survive. keydown is bound in **capture** phase so it beats
  the textarea's inline `onkeydown="handleInputEnter(…)"`; in bubble phase Enter
  would dispatch the half-typed command as a chat message.
- Internal plumbing (`__remote-workflow`, `workflow-launch-exec`, `heapdump`)
  and entries the CLI itself marks `(removed)` / `Renamed to …` are kept in the
  inventory but out of the picker.

**Naming constraint worth knowing:** every module-scoped binding is `_sac`-
prefixed. `tools/smoke/inline-handler-scope-check.mjs` builds a single map of
module-scoped names across all modules, so a bare `esc` or `active` here made it
report *pre-existing* inline handlers in five other files as unbridged.

Tests: `tests/test_slash_commands.py` (11, incl. chunk-boundary and
headless-variant-wins-over-interactive-duplicate) and
`tools/smoke/slash-autocomplete.mjs` (11 behaviours, incl. "Enter completes and
does NOT dispatch" and the two negative triggers).

**Requires a server restart** — the endpoint is new.

## [2026-07-31b] — Worktree-isolated chats wouldn't open: the CLI flattens dots too

A conversation run by a worktree-isolated agent could not be opened from the
conversation list. Clicking it did nothing; the row rendered fine and every
*other* chat in the same project opened normally.

**Root cause: `_encode_project_path` doesn't substitute `.`.** It maps `:`, `\`
and `/` to `-`, and call sites separately try an `_`→`-` variant. The CLI
flattens **both** `_` and `.`. That difference never mattered, because no
project path had a dot-prefixed directory component — until per-agent worktree
isolation (`b264200a`) started running agents from
`<project>/.clayrune/agents/<sid>`. The CLI keys its transcript directory on the
process CWD, so it wrote to:

```
…-engulfing-scanner--clayrune-agents-0f7687efce3f    ← CLI (dot flattened)
…-engulfing-scanner-.clayrune-agents-0f7687efce3f    ← what MC looked for
```

The worktree fallback added in `mc.memory._find_transcript_file` was therefore
**dead on arrival** — it scanned `<project>/.clayrune/agents/*` and handed each
worktree path to the same encoder that produced the wrong name. Both
`/session/<mc>/reconstruct` and `/transcript/<csid>/reconstruct` returned
`404 transcript not found or empty`, and `openConversation()` has no
error branch past that — it exhausts its routes and returns, which is exactly
"clicking does nothing".

- **`ClaudeRuntime._encoded_dir_candidates()` (new)** — returns every plausible
  encoded directory name (base, `_`→`-`, `.`→`-`, both), deduped, most-likely
  first. `transcript_path()` and `list_sessions()` now use it instead of each
  hand-rolling the underscore variant.
- The base encoding is **unchanged**. Only the *lookup* candidate set widened,
  so `_build_transcript_path()` (used for writes: size checks, watermarks) and
  `_native_memory_path()` keep their existing behaviour.

**Blast radius: one session** — worktree isolation ships default-OFF and only
arms for the 2nd+ concurrent agent, so this was its first real occurrence.
Nothing was lost: Scribe still wrote a full memory entry for the session via its
documented stdout-tail fallback (the transcript is its preferred, higher-fidelity
source, not its only one). The 2 MB / 863-line transcript was intact on disk the
whole time and opens once the server is restarted onto this build.

Regression tests in `tests/test_claude_runtime.py`
(`test_transcript_path_checks_dot_dash_variant`,
`test_encoded_dir_candidates_covers_both_substitutions`), mutation-verified —
the first fails with `assert None == …` against the previous encoder.

## [2026-07-31] — Modal header: pin button and status row removed

The project-modal header carried a second line (`● IN PROGRESS · 2m ago`) and a
**pin** button in the window controls. The pin was originally a data-sheet
collapse — it hid the path row, summary, description, and the Current task /
Next up cards. The `[2026-07-06]` desktop header cleanup then hid all of those
*unconditionally* (min-width:961px block), and the mobile rules already did the
same below 960px. What was left: the pin toggled exactly one row, and that row
duplicated status the conversation panel already shows (COMPLETED / Stop /
token counts). It cost ~48px of vertical space on every project, every session
— most visible on mobile, where screen height is scarcest.

Both are gone. The header is now permanently compact: back button → name row →
content.

- **`render-core.js`** — dropped the `.modal-pin` button and the
  `.modal-status-row` div from `modalContentHTML`. The now-unused
  `const fs = friendlyStatus(p)` went with it; tiles, mobile rows, and list
  rows still call `friendlyStatus` — this is a modal-only removal.
- **`interactions.js`** — `toggleModalPin()` and its `window.` bridge deleted.
- **`modal-manager.js`** — pin-state restore removed, and `unpinned` dropped
  from the `mc_open_modals` snapshot. Stale `unpinned` keys left in
  `mc_modal_prefs` / `mc_open_modals` from before this change are ignored, not
  migrated — no `is-unpinned` class exists to apply them to.
- **`app.css`** — `.modal-pin`, all `.modal-window.is-unpinned` rules, and the
  now-dead mobile `.modal-status-row` hide deleted. `.modal-dashboard-btn`
  margin-bottom folded to the tight 6px the unpinned state used to set.

Verified with `tools/smoke/boot-smoke.mjs` (5 scenarios + dispatch guard) and
`inline-handler-scope-check.mjs` (39 modules, no inline handler left pointing at
the removed `toggleModalPin`).

**Follow-up — the gap left underneath.** With the status row gone the name row
became the *last* element in the header, so the spacing that used to separate it
from the row below turned into dead space above the content. Measured against
the live server with Playwright rather than eyeballed: header was **99px**, now
**81px** (short viewports ≤750px: **77px**), gap under the name row 21px → 7px.

- `.card-title-row` in `modalContentHTML` lost its inline `margin-bottom:8px`.
  The base `.card-title-row` rule has no bottom margin, so the modal title row
  is now flush; moving it out of an inline style also lets media queries win
  normally instead of needing `!important`.
- `.modal-header` padding `16px 24px 12px 28px` → `12px 24px 6px 28px`.
- Dropped `.modal-header .card-title-row { margin-bottom: 4px !important }` from
  the `@media (max-height: 750px)` compaction block. It existed to compact 8px
  down to 4px; with the base now 0 it would have *added* 4px on exactly the
  short viewports that block exists to tighten.

## [2026-07-27c] — Awaiting-input conversation vanished from the list after a page refresh

A conversation that was **waiting on the user** (an `mc:question` was pending)
disappeared from the conversation list after a reload, so there was no card to
click to answer — the project tile still flagged `AWAITING INPUT`, but the chat
itself was gone.

**Root cause: a mid-conversation `/skill` turn poisoned the card's label.** The
list label is the last user-role turn. When the user (or the harness) runs a
skill after their real message, the skill's injected turn —
`Base directory for this skill: …` — becomes the most recent "user" text. That
string matches `_AGENT_LABEL_RE`, and `_userInitiatedConvos` filters out any
source-less row whose label looks like an agent/system marker. So a genuine user
chat got misread as a system chat and dropped from the list on every rebuild
(before the refresh it survived only via the transient live-session/SSE path).

- **`_bestConvLabel(c)` (`conversation.js`)** — new label resolver used by the
  filter, the row render, and the search text. Prefers the cleaned last user
  message; if that's empty or is itself an injected marker, falls back to the
  FIRST real user message (`Few things…`). A true agent chat (first *and* last
  are markers) still resolves to a marker, so it stays hidden — no regression.
  Fixes the disappearance on a hard reload with no server restart.
- **`/api/project/<id>/conversations`** now carries `waiting_for_question` /
  `waiting_for_plan_approval` per row, and **`_convLiveState`** honors them as a
  fallback — so an awaiting-input chat keeps its "Waiting for you" badge and
  top-of-list bubbling immediately on reload, before the `/agent/status` poll
  repopulates the live cache (takes effect after the next server restart).

## [2026-07-27e] — Auth banner false positive: "Reached max turns" misread as sign-out

Ron saw the "Authenticate Claude" banner while mid-conversation with a running
agent. Live state confirmed the bug:
`{ok:false, reason:"unknown", last_error_text:"Error: Reached max turns (1)"}`.

Root cause — the startup auth probe runs `claude -p ok --max-turns 1`. When the
CLI wants a second turn (e.g. it calls a tool), it hits the 1-turn limit, prints
"Error: Reached max turns (1)", and exits **non-zero**. The probe's fail-closed
`else` branch then marked it as an `unknown` auth error and latched the banner
ON — but reaching max turns *proves* the CLI authenticated and the model ran.
Same `--max-turns 1 → rc=1` brittleness the oneshot path already documents.

- **Backend** (`agent_routes.py`): the probe now treats a `_MAX_TURNS_RE`
  ("reached max turns") non-zero exit as a POSITIVE auth signal
  (`_mark_claude_auth_ok`), not a failure. Real auth sentinels and other
  non-zero exits still fail closed. Four regression tests
  (`test_agent_routes.py`): max-turns→ok, clean→ok, real-auth-error→fail,
  unknown-nonzero→fail-closed.
- **Frontend** (`provider-auth.js`): `_hasLiveClaudeAgent()` — the banner is now
  suppressed whenever a claude session is running/idle, since a live run is
  itself proof of working auth. Belt-and-suspenders against any future
  probe/telemetry false positive.

Backend needs a restart; the frontend guard activates on a hard reload and also
clears the currently-latched banner as soon as a running agent is detected.

## [2026-07-27d] — Desktop Inbox: Outlook-style mail list (Sender · Subject · Time)

Reworked the desktop Inbox surface from a stacked timeline into a mail-client
list, on Ron's request to "view by conversation or by sender like Outlook."

- **Columns:** Sender (the project) · Subject · Time, unread shown with an accent
  dot + bold, hover-reveal dismiss. Every field already existed on the
  `/api/notifications` payload — no backend change.
- **Subject derivation:** turn-complete notifications set `title` to the project
  name (== sender), so it was useless as a subject. `_inboxSubjectPreview()` now
  pulls the subject from the first sentence/line of the body and shows the
  remainder as a greyed "— preview…", the way a mail client does.
- **View toggle:** `Conversation | Sender` in the header (persisted to
  `mc_inbox_view`). The store already collapses to one row per conversation
  thread, so **Conversation** is the flat, day-grouped, newest-first list, and
  **Sender** re-groups those same rows under a per-project header (with a
  `N · M new` count), groups ordered by most-recent activity.
- Scoped entirely to `#desktop-inbox-list` + new `.dib-*` classes; the mobile
  `.mib-*` overlay is untouched. `static/js/mobile.js` + `static/css/app.css`.
  Frontend-only; hard reload to activate.

## [2026-07-27c] — Rail: sibling conversations sharing one mc_session_id

Reported on Day Trading Scanner: several rail rows highlighted white at once,
and clicking some of them did nothing.

Root cause — a **resumed** session reuses ONE `mc_session_id` across multiple
Claude transcripts (a live idle tail + the completed run it continued from both
carry the same mc id). Two independent bugs fell out of that:

- **Highlight:** `isActive` matched rows on `mc_session_id`, so every row sharing
  the open tab's mc id lit up white. Now, when the open conversation's
  `claude_session_id` is known, the highlight disambiguates on that (unique per
  transcript) and only falls back to the mc-id match when the csid is unknown.
- **Click does nothing:** `openConversation()` opened the fast path
  `switchAgentTab(mcSessionId)` whenever that mc id was cached — but for a
  *sibling* row that mc id is already the open tab, so it re-selected the same
  tab and nothing changed. It now takes the fast path only when the cached tab
  actually represents THIS conversation (`claude_session_id` matches, or the row
  has no csid); otherwise it opens the specific transcript by csid. The
  csid-reconstruct logic was split into `_openConversationByCsid()` so both
  paths share it. (The mc-based `/session/<id>/reconstruct` route resolves to the
  mc id's *current* transcript, so it can't be used for a sibling — hence the
  csid route.)

Frontend-only (`static/js/conversation.js`); hard reload to activate.

## [2026-07-27b] — Model list cleanup, first-run onboarding on upgrades, tour steps that pointed at nothing

**1. Opus 4.8 retired from the pickers; Opus 5 is the new-install default.**
- `MC_MODEL_CHOICES` (`modal-manager.js`) and the Settings model picker
  (`settings-drill.js`) no longer offer `claude-opus-4-8`.
- Retiring an id from the list has two edges, both now covered:
  a project or conversation still *pinned* to it would have rendered the raw
  `claude-opus-4-8` string in its badge (new `MC_LEGACY_MODEL_LABELS` lookup
  keeps the friendly label), and merely *opening* Settings with a retired model
  saved would have silently snapped the `<select>` to its first option and
  written that back on the next save (the picker now appends the saved value as
  a "… (retired)" option).
- `CONFIG['agent_model']` default flips `''` → `'claude-opus-5'`, so a fresh
  install starts on the flagship instead of inheriting whatever the CLI happens
  to default to. Existing `config.json` files keep their saved value.

**2. First-run onboarding never fired on installs carrying the legacy starter.**
The `sample-project` starter (pre-2026-05-08 name for what is now `clayrune`)
counted as a real user project on both sides of the check — so
`_has_real_user_project()` skipped the startup seed *and* the frontend's
`realProjectCount === 0` gate never opened. The result on an upgraded install:
no Clayrune project, and no auto-starting tour; the project only appeared if the
user manually ran the tour, whose step 6 creates it. Both
`_has_real_user_project()` (`guide_routes.py`) and `isOnboardingProject()`
(`index.html`) now treat `sample-project` the same as `clayrune`. The existing
one-shot heal marker (`onboarding_heal_v1.flag`) carries the repair.

**3. Three tour steps spotlighted invisible elements.** All three drew a small
glowing box in the top-left corner instead of highlighting anything — the
"screens with no action on screen" report.
- **Hivemind step** targeted `[data-nav="hivemind"]`, which lives in the
  sidebar's `Advanced` group — `display:none` by default. The step now expands
  the group on enter and restores it on leave (without touching the user's
  saved preference).
- **Tabs step** targeted `.modal-tab-bar`, which the conversation redesign made
  `display:none !important` on desktop *and* mobile; the tabs moved into the
  three-dot menu. The step now spotlights the in-menu tab section and its copy
  says so.
- **Three-dot-menu step** asked for `demo: 'modal'`, so the dropdown that the
  step describes was never rendered — `wtDemoMenuHTML()` had been dead code.
  Now `demo: 'modal-menu'`.
- **Generic guard:** `wtShow` treats any target measuring 0×0 as absent, so this
  whole class degrades to a plain centered card instead of a highlight pointing
  at the corner. A CSS change can no longer silently break a tour step.
- The demo modal also stopped rendering the `.card-summary` and `.modal-tab-bar`
  rows that the real app hides, and the sidebar / scheduler step copy was
  refreshed for the current nav (Inbox, Automation, the Advanced group).

**4. A restarted server logged nowhere (observability).** The launcher holds
`data/logs/clayrune.log` open with a share mode that denies a second writer, so
the windowless restart path's `open(log_path, 'ab')` reliably raised
`PermissionError` — and the handler only logged the failure *to the dying
process*. Every restart since blinded us to exactly the boot we most wanted to
read. It now falls back to `clayrune-restart-<pid>.log` and announces the path.

**5. Boot-phase timings** (`_boot_phase` in `server.py`): each pre-serve startup
phase logs its duration when ≥0.25s, plus a `[boot] ready to serve after Ns`
line. Added to make "the app took two minutes to come back" a measurable claim —
nothing in the warm-server measurements (import 0.34s, `/api/projects` 43ms,
transcript endpoint 0.13s on a 59MB session, full transcript scan 1.3s across 20
projects) accounted for it.

## [2026-07-24] — First-run UX: Claude auth gate + walkthrough auto-start

Three first-run fixes surfaced by a clean-VM install test (the installer flow
itself is unchanged — these are all app-side).

**1. First-run Claude auth gate.** A fresh install could complete, open a
project, and dispatch a prompt before ever learning the CLI wasn't signed in —
the user only saw a cryptic mid-run "unauthenticated" error. Root cause:
`_claude_auth_state` defaults optimistically to `ok:True` and is only flipped by
a *failing* run, and `/api/claude/auth-status` returns that cached optimism, so
the sign-in banner stayed hidden until after the first doomed dispatch. Fixes:
- **Startup probe** (`server.py`): a best-effort background `_run_claude_auth_probe`
  fires once at boot so the auth state reflects reality before any dispatch —
  covers all clients (web + mobile), no frontend needed.
- **Boot-time probe fallback** (`provider-auth.js`): if the frontend reads an
  unverified optimistic state (`ok:true`, no `last_probe_at`) it kicks a one-time
  probe and re-renders, closing the startup race.
- **Dispatch gate** (`resume-preview.js`): a *confirmed* not-signed-in claude
  verdict refuses to fire the dispatch and surfaces the sign-in CTA instead
  (never blocks on unknown state or other providers).
- Friendlier banner copy: "Log in to Claude to get started…".
- *Installer finding (not changed, by design):* `install.ps1`'s `Test-ClaudeAuth`
  only greps for `not logged in` / `please run /login` and returns `$true` on any
  other output or non-zero exit — a false-positive-prone check that lets an
  unauthenticated state whose message differs slip through as "authenticated".
  The robust fix is the app-side gate above.

**2. Onboarding project name.** Already on-brand ("Clayrune", since the
2026-05-08 rename) — no "Sample Project" string remains in source. No change
needed; verified.

**3. Walkthrough not auto-starting on fresh installs.** The onboarding
`clayrune` project is seeded at startup, so `realProjectCount` was always ≥ 1 and
the `=== 0` first-run gate never fired. Fix: `isOnboardingProject()` excludes the
seeded starter from the first-run count (mirrors the incognito/steward
exclusions); the project dict now carries an `_is_onboarding_project` marker,
with an `id === 'clayrune'` fallback for already-seeded installs.

## [2026-07-23] — Installer + auto-update: three clean-VM smoke-test bugs

A real clean-VM install (2026-07-23) completed end to end — download → deps →
Claude OAuth → clone → dashboard — but surfaced three defects on the way. The
happy path is unchanged; these are all failure-path fixes.

**The update channel was bricked by our own force-push (CRITICAL).**
`git pull --ff-only` *aborts* when the remote branch has had its history
rewritten:

```
+ b82654a...a4a55a8 master (forced update)
hint: Diverging branches can't be fast-forwarded
fatal: Not possible to fast-forward, aborting.
```

`master` **has** been force-pushed, and ff-only is what both the installer's
existing-checkout path *and* `/api/system/update` ran — so every already-installed
copy could never update again, silently, with no error surfaced anywhere. All
three call sites (`installer/install.ps1`, `installer/install.sh`,
`mc/blueprints/system_routes.py`) now try ff-only first and fall back to
`git fetch` + `git reset --hard origin/<branch>`.

Safe because `reset --hard` rewrites **tracked** files only, and every piece of
Clayrune user state inside the checkout is untracked/gitignored —
`data/projects/`, `config.json`, `data/settings.json`, `data/logs/`, `.venv/`.
**Never add `git clean` to these paths**; that would delete all of it. The
endpoint returns `resynced` + `previous_commit` so a hard re-sync is visible and
reversible (`git reset --hard <previous_commit>`). A dirty tree is still refused
with 409 rather than reset.

**Untracked files made updates impossible, permanently (found while testing the
above).** The dirty-tree guard used `git status --porcelain`, which lists
*untracked* files — so one stray file in the install dir answered 409 forever and
set `update_available: false`. It's `-uno` now: the question is only about
modified tracked files. Same silent-brick class as the bug above.

**Truthful failure diagnosis in the installer .exe.** Every non-zero exit was
reported as *"Most often this means Claude CLI isn't logged in yet… just pick
L"*. On the smoke test the real failure was git; the user logged in and hit the
identical error. `install.ps1` now has a documented **exit-code contract**
(`1` prereq / `2` install step / `3` not authenticated) and
`ClayruneInstaller.cs` keys its remediation menu off it — `[L]` is only offered
when login can actually be the fix.

**One launch = one installer.** A single double-click produced two
"Clayrune Installer" windows; the stray second run raced the first one's clone
and left the checkout diverged — which is what set up the bug above. There is no
self-relaunch in `ClayruneInstaller.cs`, so the duplicate originated outside it;
a named mutex makes the symptom impossible whatever the cause, and makes two
concurrent installs writing the same directory impossible. The loser prints one
line and exits immediately.

**Also:** `installer/win-exe/build.ps1` hard-failed on
`src-tauri\icons\icon.ico`, which no longer exists in the repo — the installer
.exe could not be rebuilt by anyone. It uses the tracked `assets\clayrune.ico`
now, and a missing icon is a warning rather than a build failure.

New: `tests/test_system_update_resync.py` (5 tests) builds a real force-pushed
upstream and asserts recovery, user-data survival, that the happy path still
takes the plain ff-only route, and that a dirty tree is still refused. It
includes a control test proving the scenario genuinely breaks bare ff-only, so
it can't silently stop testing anything.

## [2026-07-13b] — Control plane: session-auth hardening (night-review findings)

The session-JWT code below shipped unreviewed. The 2026-07-13 night review found
**ten** issues in it; all ten are fixed here, each with a regression test that was
**verified to fail against the shipped code** (a test that passes both ways proves
nothing). 25 new tests; `control_plane` 32 → 57, all green. No behaviour change for
a healthy session.

Nothing here was exploitable *today* — billing enforcement is off and the Worker is
not live. Every one of them becomes real the day either of those flips.

**Would have locked out or broken paying users**
- `entitlement.py` **failed CLOSED on a broken billing row.** An `active` sub whose
  `current_period_end` was missing or unparseable returned `False` — locking out the
  paying customer in exactly the "our webhook died" case the module's own docstring
  says must never happen. Now fails **open** and logs loudly. A period end that is
  present and *past* still denies: failing open on missing data must not become
  failing open on bad news. Suspension still outranks everything.
- `jwt_es256.py` **would have failed ~50% of logins at random, off Cloud Run.** The
  "don't invent an ephemeral signing key in production" guard only fired on
  `K_SERVICE`. On gunicorn/a VM/**Fly** (where the hosted product is heading) every
  worker generates its *own* key under the *same* `kid`; `/v1/jwks` returns whichever
  worker answered, the edge caches it 10 minutes, and tokens from the other workers
  fail. Reads as a network flake; costs a day. Ephemeral keys are now **opt-in**
  (`CLAYRUNE_ALLOW_EPHEMERAL_KEY=1`, dev only) and the guard fails closed.

**Security**
- `sessions.py` **refresh tokens now rotate, with reuse detection.** The same secret
  was replayed for a whole session (30d browser, **365d phone**): a stolen token was
  a full-lifetime bearer credential that produced *zero* signal — thief and victim
  refreshing side by side forever. Now the secret is retired on every use; presenting
  a superseded one outside a 60s race window is proof two parties hold it, and the
  session is revoked. **Mobile is deliberately excluded** (`sessions.rotates()`): the
  shipped APK replays its `pair_token` forever, so rotating would revoke every paired
  phone on its second renewal — and it would look exactly like the attack. Mobile
  rotation lands **with** the APK rework (backlog `ee94a17e`); the refresh response
  already returns the new token, so that stays a client-side change.
- `sessions.py` an **unparseable `expires_at` was treated as NON-EXPIRING** — one bad
  write and the refresh token was immortal. Now treated as expired. Cost of failing
  closed: one sign-in. Cost of failing open: unbounded.
- `routes_auth.py` the **sign-in page now ships a CSP** with a per-response nonce. It
  is where the Google ID token is obtained and the `Domain=.clayrune.io` cookie is
  minted — the highest-value injection target in the product. The Firebase SDK is an
  ES module, so SRI is impossible; the CSP pins the origin instead.
- `main.py` **CORS tightened on what is now the auth boundary**: `allow_headers=["*"]`
  with `allow_credentials=True` → pinned to `Content-Type`/`Authorization`, and the
  origin list is stripped (`"a, b"` silently never matched any `Origin`).
- `sessions.py` a **malformed `cr_refresh` cookie returned HTTP 500, not 401.** The
  session id went unvalidated into Firestore's `.document()`, so `"a/b.secret"` raised
  `ValueError`. `resolve_refresh` is documented to give one undifferentiated failure so
  a probe cannot learn *why* it failed — a 500 among 401s is exactly that signal. Ids
  are now shape-checked before any Firestore call.

**Correctness / ops**
- `sessions.py` `revoke_all` is **one atomic batch**, not a write-per-session loop. It
  runs on the *abuse* path: a partial failure left some of a suspended abuser's sessions
  alive while returning a count claiming they were all gone.
- `routes_auth.py` every error envelope hardcoded `"request_id": "x"` (7 sites). The one
  field whose job is to be unique was a constant, so "I can't sign in" handed you an id
  matching every failure ever. Now uses the real `_request_id(request)`.
- `routes_auth.py` the `cr_refresh` cookie max-age hardcoded the browser's 30d while
  `sessions.create()` picks 365d for mobile. Latent (the phone gets its token in the
  body) but a loaded gun for whoever wires the phone up. Now derived from the session's
  actual expiry.

Unchanged and still blocking the Worker deploy: the reserved-subdomain bypass, the APK's
dead CF-Access headers, and the SSE/WebSocket cap.

## [2026-07-13] — Control plane: session JWT at the edge, Cloudflare Access removed

Enrollment provisioned a **Cloudflare Access application + email policy per user**.
Access is priced **per seat** — free to 50 users, then **$7/user/month for every
user** — and caps the account at **500 applications**. Against our $6.99/mo price,
user #51 turned the whole book **negative margin** ($0 → $357/mo overnight) and put
a 500-user ceiling under a 1,000-tunnel design. This blocked the paid launch. It is
an economics *correctness* bug, not an optimization.

Replaced by **one Cloudflare Worker** on a single wildcard route
(`*.clayrune.io/*`, **$5/mo flat, account-wide**) that verifies our own ES256
session JWT in CPU at the edge. Worker source lives in the `clayrune-cloud` repo;
this is the control-plane half of that contract.

**⚠️ Access was doing TWO jobs, and the money was the boring one.** Its email policy
was also the **authorization** check — it is what stopped `alice` reaching
`bob.clayrune.io`. Removing Access without replacing that would not have saved
$7/user; it would have published every customer's dev machine to the internet. The
replacement is the `u` claim: minted from the enrolled username in Firestore, never
client-supplied, compared by the Worker against the requested subdomain
(`claims.u !== want → 403`).

- **`app/jwt_es256.py`** — ES256 keyring (kid-addressed, rotatable), sign, JWKS.
  Raw `r||s` signatures, not DER — WebCrypto silently rejects DER. Verified
  end-to-end against the Worker's actual `crypto.subtle.verify()` code path.
- **`app/entitlement.py`** — `is_entitled()`, the ONE predicate, enforced at exactly
  two chokepoints (JWT mint/refresh, `/v1/attest`). Fail **open** on billing (7-day
  `past_due` grace — never kill a paying customer because our webhook broke); fail
  **closed** on identity (the Worker 503s if it can't reach the JWKS). Ships behind
  `CLAYRUNE_BILLING_ENFORCED=0` because billing doesn't exist yet and enforcing the
  predicate literally today would lock out every enrolled user.
- **`app/sessions.py`** — session = revocable hashed refresh token (Firestore) +
  30-min access JWT. `GET /v1/jwks`, `GET /v1/signin`, `POST /v1/session/{start,refresh,logout}`.
  **Refresh is the live-entitlement chokepoint** — it re-reads the user row every
  time, so a cancellation takes effect within one TTL.
- **`app/denylist.py`** — the TTL is a deliberate lag for *billing* and useless
  against *fraud*. Suspension writes `u:{user_id}` to the Worker's KV namespace →
  ~1 ms edge cutoff. `POST /v1/admin/users/{id}/suspend` now does all three: flag +
  KV + revoke-all, and reports `edge_cutoff_immediate: false` if the KV write failed.
- **`/v1/sessions*` rebased.** These were a *proxy over Cloudflare Access's session
  API* — not our sessions at all. CF's per-session revoke was unreliable enough that
  the old code tried four URL shapes and fell back to nuking *every* session the user
  had. Now a session is a Firestore doc and revoking one revokes one.
- **Mobile pairing rebased.** It was built entirely on CF Access **service tokens +
  a policy on the user's Access app** — deleting Access deleted the phone's whole
  credential. A paired phone is now a `kind: mobile` session (1-yr refresh token →
  same 30-min cookie), which means it now passes the entitlement check the CF service
  token (valid 8760h, checked nothing) never did.
- **`teardown_access.py`** — dry-run by default; deletes the legacy Access apps +
  service tokens. **Run only after the Worker is deployed and verified** — until
  then those apps are the only authorization in front of enrolled machines.
- Docs: `03-control-plane-api.md` §3.5 / §3.15 (new) / §4.1a / §5.1, `error_codes.md`.
- 25 new tests. `test_enroll` now asserts enrollment touches **no** `/access/` endpoint.

**Three things that must land before this ships — see §3.15:**
1. **The Worker's own route matches the control plane.** `*.clayrune.io/*` includes
   `api.clayrune.io`, so the Worker would gate its own sign-in page and its own JWKS
   — infinite redirect. Needs a reserved-subdomain bypass (patch in §3.15.4).
2. **Every paired phone stops working.** The shipped APK sends CF-Access headers that
   now authorize nothing. `E:\clayrune-mobile` + MC's pairing surface need the
   refresh-token flow; existing pairs must re-pair.
3. **SSE/WebSocket authorize once, at setup** — a user who cancels mid-stream holds
   the socket indefinitely. Must be capped in the Worker (§3.15.7). Not fixed here;
   written down, not papered over.

## [2026-07-11b] — Resume no longer drops the injected context (9 `-r` paths fixed)

A 2026-07-11 re-test on CLI 2.1.206 reversed the 2026-06-04 canary finding:
`claude -r` does NOT restore the original system prompt — the prompt is rebuilt
from flags on **every** invocation, so any resume that omits
`--append-system-prompt-file` silently drops the whole injected context
(AGENT_RULES, memory read-floor, CLAYRUNE API reference, character, system
awareness). MC deliberately omitted it on all resume branches, so every
respawned conversation ran as a "naked" Claude. Memory:
`discovery_claude_resume_ignores_append.md` (rewritten with the reversal).

- **All 9 resume paths now re-append the context**: dispatch-resume (B+A),
  MC-restart revival (B+A), Mode-A auto-followup, Mode-B dead-process respawn
  (the most frequent — idle-eviction + crashes), sticky-settings respawn,
  auto-router tier switch, Mode-A per-turn follow-up (incl. `--continue`), and
  interrupt-resume.
- **Stash-first, rebuild-fallback** (`_respawn_sysprompt_args`): the spawn
  context is stashed on the session dict (`_system_prompt`) and re-appended
  verbatim on respawn — instant (safe under `mgr.lock`, no RC-2 regression)
  and byte-identical (cache-prefix-friendly). Rebuilt fresh only where that's
  the point: the sticky-settings respawn (applies changed settings), revival,
  and dispatch-resume (no session dict). Best-effort: a rebuild failure
  degrades to the old context-less resume.
- **Tier-1a/1b split collapsed** (`_RESPAWN_TRIGGER_KEYS`): the split existed
  only because of the reversed finding. `brief_replies_always_enabled` is now
  a respawn trigger; the sticky respawn's rebuilt context carries it.
- **Character personas now survive respawns** via the stash. Known limit: a
  conversation resumed from the history list (no live session dict) rebuilds
  context without a character.
- **Startup temp-file sweep** (`sweep_orphan_tmpfiles`, wired into
  `_startup_memory_maintenance`): removes crash-stranded atomic-write temps
  (`.{name}.tmp{pid}`) under `data/` and stale `clayrune-sysprompt-*.txt`
  files in the OS temp dir, age-gated at 24h.
- Tests: `tests/test_resume_sysprompt.py` (8 green; affected suites 144 green).
- Hygiene: `data/reply_archive/`, `data/skills/_promoted|_rejected|
  _soak_baseline.json` gitignored; `tools/smoke/` scratch previews moved to
  `tools/smoke/_scratch/` (now ignored).

Side effect worth knowing: the steward's task-text refresh block and the
non-Claude `_system_prompt` re-injection were both workarounds for this bug on
their surfaces; they stay (the stdin-append path still needs them) but their
comments now point at the real rule.

## [2026-07-11] — Live activity states: thinking vs. writing (experimental, default OFF)

The typing dots (§4, 2026-07-05) meant "process alive, nothing emitted yet" —
which is almost never *typing*. Without `--include-partial-messages` the CLI only
hands us whole assistant messages, so there was no live signal to differentiate
"the model is reasoning" from "the model is writing the answer".

New config key **`activity_states_enabled`** (default `false`, Settings → Agent →
"Live activity states"). When ON:

- `ClaudeRuntime.build_command(partial_messages=True)` adds
  `--include-partial-messages` (single choke point: `_build_claude_flags`).
- Both stream readers handle `type: "stream_event"` via `_note_activity_state()`:
  `content_block_start` / `content_block_delta` map to a transient
  `session['activity_state']` of `thinking` | `writing` | `tool`. Envelope shape
  verified live against the CLI (`event.type`, `event.content_block.type`,
  `event.delta.type`).
- The SSE loop emits `{'type':'activity','state':…}` on change (running only).
- FE: `setAgentActivity()` repaints the indicator — dots for `writing`, a spinning
  ring for `thinking`/`tool` (`.act-spinner`).

**Reversibility (the whole point).** `stream_event` never touches `log_lines`, is
never persisted, and never reaches Scribe or the transcript. Flag off → no CLI
flag → no `stream_event` → no `activity` event → the indicator is the same three
dots as before. It's in `_RESPAWN_TRIGGER_KEYS` (a launch flag, so live sessions
only pick it up on respawn). Nothing to unwind but the toggle.

## [2026-06-25] — Workflows tab: live CC Workflow progress in MC

CC's Workflow tool (`/workflow`) never streams per-agent progress to MC's agent
channel — only the launch `tool_use` and the final `<task-notification>` arrive,
so a workflow ran "invisibly" inside an MC session. The live progress is written
to disk instead, at
`~/.claude/projects/<enc>/<csid>/subagents/workflows/<wf_id>/journal.jsonl`
(append-only `started`/`result` events per subagent). New surface tails that.

- **Backend** (`mc/blueprints/agent_routes.py`): `GET /api/project/<id>/workflows`
  + `_scan_project_workflows` / `_wf_render_ascii` / `_wf_agent_label`. Globs the
  per-session workflow journals (≤24h), counts started-vs-result per agent,
  best-effort labels each agent from its `agent-<id>.jsonl`, and returns a
  pre-rendered monospace ASCII tree (`[x]` done / `[~]` running + progress bar).
  Read-only, best-effort — returns `{workflows: []}` on any miss.
- **Frontend** (`render-core.js`, `agent-console.js`): a "Workflows" tab
  (desktop tab bar + mobile menu) that renders each tree in a `<pre>` and polls
  every 3s while any workflow is `running` (self-cancels on tab switch / close).
- Validated against the live engulfing-scanner workflows (13/13 and 9/9
  reconstructed correctly). Backlog `7c1808c2`.
- **Known gaps (v1):** the journal has no phase grouping (flat fan-out only);
  identical-prefix fan-outs fall back to a generic label; MC's `live_agent`
  still reads `idle` during a workflow (detached subagents — separate fix).
- Needs a server restart to expose the endpoint; static JS is served no-cache.

## [2026-06-13d] — Agent follow-up messages ignored the chat zoom

On mobile, a follow-up agent message rendered at the CSS default size instead of
the font size the user had pinch-zoomed to. `applyModalZoom` set the size inline
on the lines *present at the time*, but streamed/appended lines never pass back
through it — and the mobile rule `.agent-output .agent-line { font-size: 12.5px }`
has an explicit size that blocks inheritance from `.agent-output`, so new bubbles
snapped back to 12.5px.

- `applyModalZoom` (`static/js/interactions.js`) now also sets a `--mc-zoom-font`
  CSS var on `.modal-content`.
- The agent-chat font-size rules (`static/css/app.css`: `.agent-output` base
  desktop+mobile, `.agent-line`, `.agent-line-prompt`, the tool/status/error/
  queued/followup chips) read `var(--mc-zoom-font, <default>)`. The var cascades
  to every current AND future line, so appended messages match with no per-append
  hook; unset (no zoom) falls back to the original defaults.
- Frontend-only; cold app reopen to activate.

## [2026-06-13c] — Fail-fast on all recurring polls (Doze hardening)

Follow-up to `[2026-06-13b]`: the same dead-socket hang existed on every other
recurring poll. New shared `window.fetchFailFast(url, opts, timeoutMs=8000)`
(AbortController, no-store default); routed `fetchProjects` (refactored),
`fetchSystemStatus`/`fetchSystemUsage`, `refreshScheduleBanner`,
`refreshAuthStatus`, `fetchAgentStatus`, and `_reconcileAgentBuffer` through it.
No retry on the polls — the interval is the retry. Skipped `_pingPresence`
(fire-and-forget POST) and on-demand modal loads (user-triggered). Commit
`7080f33`. Frontend-only; cold app reopen to activate.

## [2026-06-13b] — Mobile dashboard 50s "Failed to load" — real fix

The payload trim below was a real perf win but **did NOT fix the ~50s mobile
load** (Ron felt no difference after deploying it). Measurement settled it:
`/api/projects` is **~0.3s warm** (2.6s only on the very first cold hit;
read+parse of all project JSON is 31ms). The 50s lives in the **connection
path**, not the payload or server compute.

Root cause: Android Doze parks the WebView's sockets while backgrounded; on
resume an existing socket reports "alive" but is dead, so the plain
`fetchProjects()` fetch HANGS for the OS TCP timeout (tens of seconds) before
throwing — the "Failed to load — is server running?" flash — and the grid then
waits up to 30s for the next poll tick. POSTs already dodge this via the native
`HttpURLConnection` bridge (APK v1.5); the project-list **GET was the
unprotected path**.

- `fetchProjects()` (`static/index.html`): `AbortController` fail-fast (8s) + 3
  retries on a FRESH socket; in-flight guard so the 30s poll doesn't stack;
  don't clobber an already-rendered grid on a transient blip (error shown only
  on a cold/empty grid).
- `_resyncOpenModalsFromServer` (visibility/focus): also refetch the dashboard
  list on resume — it previously healed open-modal SSE but left the grid
  waiting for the next 30s tick.
- Frontend-only; activates on a **cold app reopen** (no server restart).
- Commit `fc6e28b`. Rollback: `git revert fc6e28b`.

## [2026-06-13] — `/api/projects` payload trim (2.5MB → 0.8MB)

A real perf win (smaller payload, less cold-start serialization) but **not** the
mobile-load fix — see `[2026-06-13b]` for that. `/api/projects` shipped every
project's **full backlog inline**, including note bodies — 2.5MB total (note
bodies alone ~1.4MB on `mission_control`, where 842/870 items are done).

- **Server (`mc/blueprints/project_routes.py`):** each backlog item now carries
  `notes_count`/`attachments_count`; the `notes`/`attachments` arrays are popped.
  Removed the dead per-item `ts_relative` loop (~1,400 `time_ago` calls/request
  that nothing rendered). `load_projects()` returns fresh per-request dicts, so
  in-place mutation is safe.
- **Lazy-load:** note/attachment **bodies** render only in an open project
  modal, so the modal lazy-loads the full backlog on open (`refreshProjectBacklog`,
  sets `p._backlogFull`). `_preserveOpenBacklogs()` keeps that loaded backlog
  across the 30s poll + `refreshSilent` so panels don't collapse back to counts.
- **Badges** use `count ?? (arr||[]).length` — correct under both the trimmed
  list payload and the full `/api/project/<id>/backlog` payload.
- **Measured:** 2.49MB → 0.79MB (3.1×). Boot smoke green. Frontend is
  backward-compatible with the un-restarted server (count fields absent →
  array-length fallback); **server half needs a restart to activate.**
- **Rollback:** `git revert b32c1ee`.

## [2026-06-12e] — Voice input: record-on/record-off (inline auto-restart)

The 2026-06-11 dictation-mode tweak wasn't enough — Google's recognizer still
finalized too eagerly mid-conversation. Activating the fallback that the prior
entry kept in reserve: a true record-on / record-off toggle the user controls.

- **Root cause (confirmed against plugin Java):** in `popup: true` mode Google's
  fullscreen RecognizerIntent owns the whole lifecycle and ends the take on its
  own silence detection — our "tap to stop" can't override it, and the
  `EXTRA_SPEECH_INPUT_*SILENCE*` knobs are ignored.
- **Fix:** `popup: false` (inline streaming) + a silence-driven **auto-restart
  loop** in `static/js/composer-extras.js`. Android's stock `SpeechRecognizer`
  always finalizes on a silence gap (no true continuous mode), so each finalize
  is treated as the end of a *cycle*, not the recording — text is committed to a
  growing base and a fresh pass starts. Dictation continues until the user taps
  the mic off. Verified against `SpeechRecognition.java`: in `partialResults`
  mode `start()` resolves immediately, the final transcript arrives as the last
  `partialResults` event, and `listeningState:'stopped'` marks each utterance
  end; `ERROR_NO_MATCH`/`SPEECH_TIMEOUT` are swallowed (no event), so a 6 s
  no-activity watchdog catches silent-death cycles and a 6-empty-cycle streak
  auto-stops. Tap-off sets `active=false`, fires native `stop()`, lets the
  in-flight cycle finalize (1.5 s backstop).
- **Scope:** JS-only — no APK rebuild; the shell loads the web UI from the
  server, so a cold app reopen picks it up.
- **Rollback:** revert `static/js/composer-extras.js` (the prior popup+dictation
  build) — `git revert` of this commit.

## [2026-06-12c] — Three-dot project menu revamp: declutter + consolidate

The per-project three-dot menu had grown to ~21 desktop items with duplicates
inside the menu (two color pickers, two hivemind entries) and settings that
shadowed the main Settings modal (Enter Key). Revamped to ~12 items: the menu
is now navigation + quick actions, with configuration consolidated into two
dialogs.

- **Appearance ▸ (merged):** Change Color + Change Domain (which carried its
  own second color picker) became one submenu — accent color, domain list,
  domain color, new-domain input.
- **Agent Settings… dialog (new):** Model, Effort, Default Provider, and
  Process mode (A/B) move out of the menu into one dialog
  (`openAgentSettingsDialog`, settings-row styling). Selects apply
  immediately, as the menu pickers did. Process mode is now a 3-way select
  whose **Default (global)** posts `use_streaming_agent: null` — clearing the
  per-project override was previously impossible once toggled.
- **Edit Profile… dialog (new):** Description / Emoji / Auto-Generate Profile
  merged (`openProjectProfileDialog`). Description editing graduates from
  `window.prompt()` to a real textarea; emoji button opens the existing
  picker, which re-syncs the dialog on pick.
- **Removed:** Enter Key (exact duplicate of Settings → Appearance →
  Interface — global localStorage setting, never per-project), Start Hivemind
  (the Hiveminds panel has "+ New Hivemind"), Remote Control (global toggle
  remains in Settings → Agent; the per-project field stays honored
  server-side, just has no menu UI).
- **Plumbing:** new shared `.mc-dialog-overlay`/`.mc-dialog` CSS (z 9900,
  below the emoji picker's 10000 so it stacks on top of the profile dialog);
  static overlay shells in index.html; dead `toggleProjectStreaming` /
  `toggleProjectRemoteControl` / prompt-based `editProjectDescription`
  removed in favor of `setProjectStreamingMode` + the dialogs. The
  providers-not-yet-fetched refresh kick (which the composer's provider
  picker silently relied on) survives as a UI-less IIFE in the menu template.
  Walkthrough "Three-Dot Menu" step + demo mock updated to match.
- **Fix (follow-up, same day):** an open menu no longer closes itself ~2s
  after opening. Root cause was pre-existing, not the revamp:
  `refreshModalById` rebuilds the modal's innerHTML with no preservation of
  menu state, and any live agent activity (SSE turn events, freshness
  reconciler, completion polls) lands a rebuild within seconds — wiping the
  `.open` class, expanded submenus, half-typed inputs, and the
  outside-click closer's captured node. Now `refreshModalById` defers the
  rebuild while that modal's three-dot menu is open (next tick after close
  catches up). Streaming output is unaffected — the SSE append path writes
  into `#agent-output-<sid>` directly.
- **Verified:** node --check on all touched JS, boot smoke 5/5, plus a
  41-assertion Playwright interaction check (menu contents, both dialogs,
  POST payloads incl. the null-override, and menu survival across
  refreshSilent/refreshModal ticks with rebuild-resume after close).
- **Rollback:** revert render-core.js / modal-manager.js / walkthrough.js /
  index.html / app.css from this commit — no backend or data-shape changes.

## [2026-06-12c] — Prompt Builder Phase 2: per-chat persona

A character can now drive a whole conversation. When you start a NEW chat
in a project, a **Persona** dropdown in the composer lists that project's
characters + globals (default **None** = today's plain agent). The pick is
injected into the agent's system prompt **at spawn** beside AGENT_RULES and
is **immutable for the chat's lifetime** — switching personas = a new chat.
This sidesteps the `claude -r` limitation (a resumed session can't change
its system prompt) by construction.

- **Backend (`mc/blueprints/agent_routes.py`):** `_build_agent_context`
  gains `character_body`, injected as a `--- CHARACTER ---` block after the
  rules. `_resolve_character()` maps a `"scope:name"` pick to (meta, body)
  via `mc/characters.py` (best-effort: a stale pick never blocks dispatch).
  Threaded through `_dispatch_agent_internal` + `_dispatch_via_runtime`;
  stored on the session dict, both agent_log entries (pending + completion,
  so the pill survives restart), and the `/agent/status` payload. The
  dispatch endpoint accepts `character`; ignored on resume.
- **Frontend:** Persona picker in the new-chat composer
  (`conversation.js`, lazy-loaded per project, hidden on resume / when the
  project has no characters); `dispatchAgent` sends it + optimistically
  renders the pill. Header **🎭 persona pill** (purple, with a "fixed for
  this chat" tooltip) beside provider·model, and a persona marker on the
  conversation-list row. Both survive reload via the agent_log field.
- **Visibility is intentional:** a persona chat is unmistakable (header
  pill + row marker); no-persona chats show nothing (no "None" noise).
- **Tests:** `tests/test_character_persona.py` (12 cases:
  `_resolve_character` happy/invalid/scope-miss + context injection on/off).
  Full suite green, pyright clean, boot smoke 5/5.
- **Not yet:** project-level *default* character new chats inherit (out of
  scope); Phase 3 library/import surface.

## [2026-06-12b] — Prompt Builder Phase 1: Claydo workshops + agent characters

Claydo grows from help desk into the prompt-builder surface
(docs/PROMPT_BUILDER_DESIGN.md). Two chips under the greeting — **"✍️ Help
me write a prompt"** and **"🎭 Create an agent character"** — switch the
modal into workshop modes that interview briefly, then hand back the
artifact.

- **Backend:** `/api/guide/stream` gains `mode=ask|prompt|character` +
  `project_id`. Builder modes run in their own sandboxes
  (`data/claydo/builder-*/`) with briefs from `docs/claydo/` materialized
  as CLAUDE.md (same no-tools engine as ask mode); a compact project
  context block (name, summary, AGENT_RULES head, skill names) rides the
  per-request prompt. Ask mode is byte-identical to before.
- **Characters:** standard Claude Code subagent files (`.claude/agents/
  <name>.md`, frontmatter name+description, body = system prompt) — saved
  characters are natively `@`-mentionable by dispatched sessions, and
  community subagents import unmodified. New `mc/characters.py` +
  `mc/blueprints/character_routes.py`: GET/POST `/api/characters`,
  GET/PUT/DELETE `/api/characters/<scope>/<name>`; dual scope
  (project default / global), 6 KB body cap (Windows 32 KB CreateProcess
  headroom), kebab-name validation, 409-then-overwrite flow. Never
  touches DATA_DIR.
- **Frontend (`claydo.js` + `app.css`):** mode chips + subtitle swap,
  marker whitelist extended with `prompt-ready`/`character-ready` (artifact
  = last fenced block, never marker attrs), fenced blocks render as code,
  handoff cards (Copy / Insert into project chat / Save character…), and a
  numbered save panel (name/description/where) inside the Claydo modal.
- **Tests:** `tests/test_character_routes.py` (12 cases) +
  `TestGuideStreamModes` in `tests/test_guide_routes.py`; pyright clean on
  the new modules; boot smoke 5/5.
- **Packaging:** `build-macos.spec` now bundles `docs/claydo/` and (fixing
  a pre-existing frozen-app gap) `docs/USER_GUIDE.md` + `CHANGELOG.md`,
  which Claydo reads at runtime.
- **Not in this phase:** activating a character as the main agent's
  persona (per-chat picker, Phase 2) and the library/import surface
  (Phase 3).
- **Rollback:** the chips are additive UI; ask mode untouched. Delete the
  two new modules + revert guide_routes/claydo.js if needed.

## [2026-06-11] — Voice input: dictation mode so the mic rides out thinking pauses

The Android mic dialog finalized the recording after ~1s of silence — pausing
to think mid-sentence ended the take.

- **Root cause:** `popup: true` delegates end-of-speech entirely to Google's
  system recognizer dialog; neither our code nor the
  `@capacitor-community/speech-recognition` plugin passes a silence-patience
  knob (and Google's recognizer ignores the documented
  `EXTRA_SPEECH_INPUT_*SILENCE*` extras anyway).
- **Fix:** pass `partialResults: true` in `_startAgentMic`
  (`static/js/composer-extras.js`). In popup mode that delivers no partial
  events — the plugin forwards it as the undocumented
  `android.speech.extra.DICTATION_MODE` intent extra, switching the dialog to
  long-form dictation, which tolerates much longer pauses. Result delivery
  path unchanged (activity-result promise). JS-only — no APK rebuild; apps
  pick it up on next cold open.
- **Caveat:** actual patience depends on the device's Google app version. If
  it still cuts off too early, the fallback plan is inline mode
  (`popup: false`) with an accumulate-and-auto-restart loop that only
  finalizes on mic-button tap — bigger change, kept in reserve.
- **Rollback:** flip `partialResults` back to `false`.

## [2026-06-11] — Built-in dashboard backgrounds (Settings → Appearance gallery)

Image mode previously required users to bring their own file. The Background
section now ships a small gallery of built-in patterns, WhatsApp/Telegram
chat-wallpaper style — tone-on-tone line-art doodles drawn from an MC icon set
(terminal, git branch, robot, rocket, chat bubble, bell, coffee…).

- **New assets:** `static/backgrounds/` — `doodle-dark` (flat, dark theme),
  `doodle-aurora` (pattern over a soft blue/purple gradient), `doodle-warm`
  (flat cream for the warm tone). WebP, 33–37 KB each + 2 KB gallery thumbs;
  generated by the new `tools/generate-backgrounds.py` (procedural Pillow,
  fixed seeds → reproducible). Re-run it after editing a recipe and keep ids
  in sync with `BUILTIN_BGS` in `static/js/appearance.js`.
- **UI:** a Gallery row above "Your image" in Settings → Appearance →
  Background (Image mode); the active built-in gets an accent ring. Selecting
  one stores its **URL** in `mc_bg_image` (custom uploads keep storing a
  data-URL), so the theme scrim, Dim slider, and crop/framing editor work
  unchanged — and localStorage stays tiny. Remove / file-pick behave as before.
- **Backend:** none — Flask's existing static route serves the files.
- **Verified:** boot smoke 5/5; live Playwright check (apply → correct
  `background-image` URL, 3 thumbs render, 1 active). `bg-framing-check.mjs`
  still exits on the pre-existing `setBgZoom` landmine (documented in
  `docs/_tracks/frontend_progress.md`) — unrelated to this change.
- **Rollback:** revert the commit; a device that had selected a built-in falls
  back to the theme background on its own (missing URL just paints the scrim).

## [2026-06-11] — Mobile/desktop chat freeze: SSE cursor overshoot starved the stream after revive or restart

The chronic "agent replied but the chat never updates" bug — patched ~16× at
the transport layer — had a second, non-transport root cause that survived
every prior fix including the 2026-06-07 freshness reconciler:

- **Root cause:** server `log_lines` can be rebuilt SHORTER under the same
  session id — `_revive_from_agent_log` (after a server restart or stale-purge)
  reseeds from the transcript (last 40 messages, **no tool lines**), and the
  2000→1500 memory cap slams the array. The client's line cursor then exceeds
  the server array forever, and BOTH delivery paths die on the same one-way
  comparison: the SSE generator's `sent < len(lines)` never fires again (it
  serves heartbeats only — which keeps the zombie reaper and freshness
  reconciler disarmed, so the freeze happens **in focus** too), and
  `_reconcileAgentBuffer`'s `length <= have → return` no-ops. Only an app
  cold-start (cursor reset) healed it — until the next revive re-broke it.
  Reproduced live: `GET /agent/stream?since=9999` on a running session yields
  zero content events.
- **Fix (invariant-based, heals all shrink causes):** the stream generator
  detects `sent > len(lines)`, emits a new `{type:'reset'}` SSE event, and
  replays from zero (`mc/blueprints/agent_routes.py`); the client handles
  `reset` by dropping buffer + cursor + DOM and letting the replay repaint
  (`static/js/resume-preview.js`); `_reconcileAgentBuffer` treats
  `serverLines.length < have` as shrink → wholesale reseed + repaint instead
  of returning (same file); `sendFollowup` clears `_readOnlyRevived` on a
  successful send so the freshness reconciler resumes guarding push-tap
  reconstructed sessions (`static/js/conversation.js` — the flag was set by
  the deep-link reconstruct path and never cleared).
- **Tests:** 3 new request-level SSE tests in `tests/test_agent_routes.py`
  pin reset-and-replay, normal-cursor, and exact-cursor behavior (11/11 pass).
- **Deploy note:** server half needs an MC restart; open phone/desktop SPAs
  pick up the client half on next cold open (no APK rebuild — SPA is served).
  Old cached clients ignore the unknown `reset` type and may briefly render
  duplicated history after a replay — self-resolves on reload.
- **Rollback:** revert the four-file commit; no config flag.

## [2026-06-12b] — First-run tour: real tile spotlight, naming, seed-on-boot

Three VM-validation reports on the first-run walkthrough:

- **Tour step 6 now spotlights the REAL project tile** (`.card[data-id=
  "clayrune"]`) instead of injecting a fake demo tile at a hardcoded offset
  next to it. The demo tile remains as the fallback when the grid isn't in
  the DOM (mobile list view). Elevation guard generalized: real targets
  elevate above the backdrop; injected demos (inside `.wt-demo`) already are.
- **Naming unified on "Clayrune"** — the demo tile/modal/menu mockups and the
  closing step said "Sample Project" while the actual created project is
  named "Clayrune" (the in-app help-desk project). Step copy now introduces
  it by name.
- **Onboarding project seeds on first boot** (`seed_onboarding_on_startup`,
  called from server startup), not just from tour step 6's onEnter — so
  skipping or never starting the tour no longer leaves a fresh install with
  zero projects. Marker-gated via `data/onboarding_seeded.flag` (outside
  DATA_DIR — load_projects() must never see it; gitignored): deleting the
  project sticks, and established installs just get the marker stamped so
  upgrades never resurrect it. The walkthrough endpoint stays as an
  idempotent backup (shared `_seed_onboarding_project()`).

## [2026-06-11] — Windows launcher: Clayrune taskbar icon on Edge-only machines

Fresh-install report (v2.1.0 VM validation): the launched app window showed
the browser's icon, not Clayrune's. Root cause, verified by reading both
browsers' window property stores: Chromium gives `--app=` windows a derived
per-app AppUserModelID, but **Edge sets the group's RelaunchIconResource to
msedge.exe** (relaunch name "Microsoft Edge"), while Chrome leaves it empty
(taskbar falls back to the window icon = our favicon). Fresh Windows machines
have only Edge → Edge logo. Dev machines with Chrome never showed it.

- **New `installer/launch-app-window.ps1`** — replaces start.bat's inline
  port-poll + `--app` launch one-liner. Same poll + launch, then finds the
  new window (class `Chrome_WidgetWin_1`, bare "Clayrune" title) and stamps
  `AppUserModelID=io.clayrune.app` (matches the macOS bundle id) +
  `RelaunchIconResource=assets\clayrune.ico` + relaunch name via
  `SHGetPropertyStoreForWindow`. Cross-process stamping verified working;
  best-effort posture (any failure → browser-icon window, today's behaviour).
- **installer/install.ps1 Step 5 no longer opens a duplicate tab** — start.bat
  (Step 4) already opens the app window; the extra `Start-Process <url>` put a
  second, browser-iconed plain tab on top of it.
- **App window opens maximized** (same VM validation pass, follow-up report):
  Chromium app windows default to ~half the work area on first run and
  `--start-maximized` is ignored for `--app=` windows, so the launcher
  maximizes via `ShowWindow(SW_MAXIMIZE)` in the same find-the-window loop
  that stamps identity. Maximize runs even when the .ico is missing (the
  identity stamp is icon-gated, the size fix isn't).
- Gotcha for future probing: reading `PKEY_AppUserModel_ID` from PowerShell 5.1
  with a hand-rolled 16-byte PROPVARIANT silently returns empty — `GetValue`
  needs the full 24-byte struct + propsys `PropVariantToStringAlloc`
  (`_scratch`-grade C# probe). Writes with the small struct DO work.

## [2026-06-10] — Frontend modernization: store.js pass complete (index.html 11,761 → 2,939)

The final phase of the frontend track. The remaining monolith core — conversation
model, dispatch/SSE machinery, modal/window manager, render core, interactions,
and the leaf families — extracted into 15 new ES modules (≈9,000 lines moved),
finishing what modules 1–21 started. index.html is now **2,939 lines** (25,165
at track start): the inline script is the designed residue — a labeled STORE
block (74 shared globals, membership derived programmatically), the boot
skeleton, and shared glue.

**Architecture (docs/STORE_JS_DESIGN.md, "Option A"):** shared mutable state
anchors INLINE in one consolidated block; feature code moves out as ordinary
deferred modules referencing it bare-name through the global lexical
environment. Zero accessor/identity bridges were introduced across the entire
pass — the platform fact that modules can read AND assign classic-script
top-level bindings carried every cut.

**Verification per cut (18×):** byte-verbatim region moves with two-sided
reassembly assertions; formal scans (store-shadowing, duplicate-decl shadow
traps, generated-handler targets incl. conditional-template emission,
parse-time top-level code, strict-mode `this`); boot-smoke 5/5; bg-framing
baseline-only; throwaway real-server exact-byte checks; headless feature
exercises (synthetic SSE through the real 3-module pipeline, full modal
lifecycle, drag via relocated listener arms, 390px mobile chat list). Agent
write endpoints were tripwired throughout — zero dispatches.

**Real bug found & fixed (latent, cold-boot):** with 30+ deferred modules, the
`fetchProjects()` boot continuation could resolve before late modules
evaluated → intermittent `ReferenceError` restoring modals/deep links. Fixed
with a parse-time-armed `_modulesReady` promise (resolves on DOMContentLoaded;
`document.readyState` is NOT a valid gate — it reads 'interactive' before
deferred modules execute). Verified under 600ms forced module lateness.

**Also:** one provably-dead shadowed duplicate (`timeAgoShort`) deleted;
`hivemindTabHTML` flagged as apparently-uncalled (deletion deferred — needs an
operator decision, it is user-facing surface). sw.js v23→v37. Full per-cut
detail: docs/_tracks/frontend_progress.md ("Phase 4").

## [2026-06-09] — v2.0.2 (security + hardening)

Follow-up to v2.0.1 from a second code-inspection pass. **All users should update.**
- **`/api/terminal/launch` restricted to loopback** — the free-form `shell=True`
  command sink now rejects non-loopback callers (defense-in-depth on the RCE
  surface; only host-local agents use it).
- **Dependency CVEs:** control-plane `fastapi`/`cryptography` bumped past 8 known
  CVEs (incl. a starlette multipart-upload DoS); main-app floors raised (Pillow
  libwebp heap-overflow, requests, flask, cryptography).
- Continuous dependency auditing in CI (`pip-audit` + `cargo audit`), committed
  `mc_tunnel/Cargo.lock`, narrowed a bare `except` in `time_ago`, removed dead
  imports (ruff), and added an exception-swallowing policy.

> The control-plane CVE fix is live only after the Cloud Run service is redeployed.

## [2026-06-09] — v2.0.1 (security release)

Patch release rolling the 2026-06-09 security hardening (detailed below) into a
tagged build. **All users should update.**
- Closes a LAN **unauthenticated-RCE chain** — a forgeable `Cf-Access-*` header
  could bypass the passcode gate and reach `/api/terminal/launch`. CF-tunnel
  trust now requires a loopback peer, and CORS is a strict allowlist (no more
  drive-by from any website the user visits).
- `git clone` argument-injection guards; secrets-at-rest file permissions;
  serve-image drive-root confinement; control-plane dev-auth fail-closed on
  Cloud Run; opt-in CF Access JWT signature verification.
- Drops the unused **Tauri** desktop target (also removes its unscoped shell
  capability + CSP `unsafe-inline`). Browser, PyInstaller app, and installer are
  unaffected; mobile (Capacitor) unaffected.

## [2026-06-09] — Security hardening: LAN auth bypass, CORS, git-arg injection

Closes findings from an internal security review (each independently verified
against source before fixing). The two criticals were a single chain.

**Critical — unauthenticated RCE chain closed.** The dashboard binds `0.0.0.0`
and exempts the host (loopback) and CF-tunnelled requests from the LAN passcode
gate. But `_is_cf_tunneled_request()` trusted the mere *presence* of a
`Cf-Access-*` header, which a LAN device can forge — bypassing the gate and
reaching `/api/terminal/launch` (which runs `shell=True`). It now requires a
**loopback TCP peer AND** the header: cloudflared forwards over loopback, so
genuine tunnel traffic still passes, but a forged header from a LAN IP does not.

**Critical — permissive CORS removed.** `add_cors_headers` reflected any
`Origin`, so any website the user visited could drive the API cross-site
(loopback is auth-exempt → drive-by `/api/terminal/launch`). CORS is now an
allowlist: native app shells (Tauri/Capacitor/Ionic) + loopback origins only.
Same-origin access over the CF tunnel is unaffected.

**git argument-injection hardening.** The `git clone` calls in `skills.py` and
`mcp_installer.py` now place `--` before the positional URL so a hostile
URL/ref can't smuggle a git flag (e.g. `--upload-pack=…`); `project_sync.py`
rejects leading-dash remote/branch names.

**Control-plane admin allowlist fail-closed.** `MC_CP_ADMIN_EMAILS` no longer
defaults to a personal email — unset now means *nobody* is an operator.

**Private data untracked + gitignore hardened.** Removed proprietary
`data/projects/engulfing-analyst/` content and the private `engulfing-diagnostic`
builtin skill from tracking (kept locally). `data/projects/*/` is now ignored so
per-project workspaces can never be committed again. NOTE: this stops *future*
exposure only — the data remains in public git history; a history rewrite +
force-push is a separate, operator-approved step.

**Repo renamed → `clayrune`.** The GitHub repo was renamed `mission-control` →
`clayrune` to match the product; all in-repo `github.com/ronle/mission-control`
URLs (README, installer, marketing) were updated. GitHub redirects the old path,
and backend identifiers (`mc_*`, Cloud Run, keystore) are intentionally unchanged.

## [2026-06-08] — Background crop editor, boot-crash fixes, preference learning

**Custom background — drag-box crop & framing.** The custom dashboard background
(Settings ▸ Appearance ▸ Background) now uses a direct-manipulation crop box
instead of zoom / across / up-down sliders: drag the box to move, drag the corner
(or scroll) to zoom. The box marks the region kept in view and its aspect matches
your viewport, so the framing cover-fits every screen — one setting works on
desktop and phone, with no letterboxing. Storage/apply are unchanged
(`bgZoom`/`bgPosX`/`bgPosY`), so existing image backgrounds keep working;
legacy images get their natural dimensions back-filled on first open.

**Two boot crashes fixed (temporal dead zone).** The background code twice
declared a `let` *below* the top-level `_applyAppearanceOnInit()` call that reads
it, so on a fresh load a `ReferenceError` aborted the entire boot script: the
dashboard hung on "Loading…" with no projects (bug #1, `bgMode`), and — for an
image background with no stored dimensions — the whole UI went dead with
clicks/modals/Settings unresponsive (bug #2, `_bgDimsLoading`). Both fixed by
hoisting the declarations above the call. These shipped unseen because an
already-open tab keeps the old working JS (server restart ≠ tab reload).

**Boot smoke test (new) — `tools/smoke/`.** A headless Playwright check loads the
real `static/index.html` and asserts the project grid renders across 5 appearance
scenarios (including image-background-without-dimensions, the exact bug-#2
trigger) — catching runtime boot throws that `node --check` can't. Runs in CI on
every `static/index.html` change and is now a required step in the
document-commit-deploy playbook.

**Self-learning: preferences now generate.** The distiller captured stated user
preferences but never turned them into reviewable proposals — the recurrence-3
gate starved them, then the renderer refused single-sighting ones and leaked the
refusals to the queue as junk. Preferences now generate on first clear statement
(human review at promotion is the quality gate); refusal detection and
code-fence stripping were hardened, and previously-captured preferences are
rescued from storage.

**Simulated demo.** A self-contained, fully-simulated dashboard demo for
clayrune.io/demo (no backend), with a theme-aware centered project modal and
Skills/MCP samples.

## [2026-06-07] — Fresh-install defaults: Warm theme + Enter-sends

Two first-run defaults changed to match the website look and the preferred
out-of-box behavior:

- **Theme defaults to Warm** (the cream/light theme) instead of Dark. Because
  Warm is a *light* theme over a dark `:root` CSS base, an anti-FOUC bootstrap
  script was added as the first child of `<body>` to apply the tone class before
  first paint — otherwise a fresh install would flash dark → cream every load.
- **Enter key defaults to "Enter sends"** (Shift+Enter = newline) instead of
  Ctrl+Enter.

Both are fallback-only (`localStorage.getItem(...) || default`), so any explicit
choice a user already made is preserved. Users who never touched these settings
adopt the new defaults on their next **hard reload**. Change either anytime in
Settings ▸ Appearance.

## [2026-06-07] — Custom dashboard background (Settings ▸ Appearance)

You can now personalize the space behind your projects. **Settings ▸ Appearance
▸ Background** adds a Theme / Color / Image picker:

- **Theme** (default) — the current theme's base color, unchanged.
- **Color** — a solid color of your choosing (native color picker).
- **Image** — upload any image; it's downscaled (≤2560px long edge, JPEG) and
  stored on **this device** in `localStorage`, then applied to `<body>` as a
  fixed, cover-fit background. A **Dim for readability** slider lays a
  theme-colored scrim over it (`--scrim-rgb`, theme-aware) so text on
  transparent surfaces stays legible; the scrim re-tints automatically when you
  switch themes.

Per-device by design — same posture as the existing tone/accent/density/voice
preferences (all `localStorage`, applied in `_applyAppearanceOnInit`). The
dashboard's empty space is `body`'s `var(--bg)` showing through the transparent
`.main-area`/`.content-area`/`.content-main`; sidebar, header, cards and modals
keep their solid `var(--surface)`, so they stay readable over any background.
No server changes; an already-open tab needs a hard reload to pick it up.

## [2026-06-07] — Mobile live updates no longer freeze until app restart

The Android app's chat and project updates could stop refreshing entirely —
frozen until a force-close + reopen or a pull-to-refresh. This had been patched
many times; the root cause was structural. Live updates ride on SSE over the
Capacitor WebView, which Android Doze kills silently (the socket dies while the
`EventSource` handle still reports "open" and fires no `onerror`). And **every**
recovery path had a blind spot:

- the resurface heal (`_resyncOpenModalsFromServer`) is event-driven only —
  `visibilitychange`/`focus`/`pageshow`, documented unreliable on Android
  WebViews, so no event ⇒ no heal;
- its heartbeat probe is a *separate* fetch, so it can't detect a zombie stream
  (returns 200 but delivers nothing);
- the 15s fallback poll skips idle sessions (Chromium slot-cap discipline) and
  is gated on `!agentEventSources[sid]` — a zombie left in that map blocks every
  path.

So a chat whose stream zombied, with no user action to trigger recovery, just
sat there frozen.

**Durable fix** (`static/index.html`): a **foreground freshness reconciler** — a
5s, visible-only timer that, for each open modal's active session, repaints
straight from `/agent/status` (server truth) whenever there's no live handle.
SSE is now a latency accelerator; the poll is the always-on backstop, so the
visible chat self-heals within a tick no matter what the transport did. It costs
no SSE slot, is cursor-deduped against the live stream, and runs **only** when
no healthy stream exists — so it never races or duplicates the live path
(`appendAgentLine`/`_reconcileAgentBuffer`). Separately it reaps a zombie stream
(handle present but silent > 25 s, safely above the server's 15 s heartbeat) and
reconnects it if the session is still running; idle streams are never reopened
(slot discipline preserved). New global `agentLastEventAt` mirrors the SSE event
timestamp so the timer can spot a zombie.

**Validated** on the Android-emulator harness (`tools/mobile-test/`): S1–S4 all
pass, including S3's no-duplication gate. Plus an isolation probe — kill the SSE,
let the agent go idle, then take no action — confirming an idle session that
missed its tail lines self-heals (1→2 lines, status `running`→`idle`, no
duplicate render) with **no** user action, the exact case the old 15s poll
couldn't cover. Web-layer only; no APK rebuild needed (the shell loads the SPA
live).

## [2026-06-06b] — Session-lifecycle timers doubled: 30 min → 60 min

Both 30-minute session-lifecycle timers are now 60 minutes, so warm sessions
and their chat tabs survive twice as long before MC reclaims them.

- **Idle-eviction** (`idle_eviction_minutes`): a warm Mode B fleet (claude.exe +
  its MCP-server tree) is now torn down after **60 min** of inactivity instead
  of 30; the next message still transparently respawns it with `-r <csid>` (full
  context preserved). Live-editable via `/api/config` (no restart) — already
  pushed to the running server, and the in-code default + guardian fallbacks bumped
  to 60 to match.
- **Stale-session purge** (the scheduler's "Purge stale sessions from memory"
  sweep): non-running sessions are dropped from the in-memory `agent_sessions`
  map after **60 min** instead of 30 (hardcoded `timedelta`). **Takes effect on
  next restart.**

## [2026-06-06] — Three user-reported fixes: LAN passcode gate, Dashboard = minimize all, Settings anchors to its sidebar item

Three pieces of user feedback, smallest to largest.

**1. The sidebar "Dashboard" button now does something (desktop).** It was a
no-op on desktop. Clicking it now minimizes every open chat/modal to the tray
(`showDesktop()` — the same action behind the command-palette "Minimize All"),
giving you a clean board. Mobile is unchanged (there "Home" still closes modals
back to the project grid).

**2. The Settings panel opens next to its sidebar item.** It used to float
dead-center (and, because it's measured against its short "Loading…" state
before the body renders, sat low and ran off the bottom once filled). It now
anchors just to the right of the sidebar **Settings** item it launched from,
aligned with it, clamped to stay fully on-screen — and re-anchors after the
async render once the true height is known. Works whether the sidebar is
hover-expanded (sidebar click) or collapsed (command palette), since it reads
the item's live rect. Desktop only; mobile Settings is full-screen.

**3. Direct LAN access now requires a passcode (the security gap).** The
dashboard binds `0.0.0.0:PORT`, so any device on the same Wi-Fi could open
`http://<host-ip>:PORT` and get **full control with no authentication** — the
only gate was Cloudflare Access on the *remote* tunnel. Closed with a local
passcode gate (`@app.before_request` in `server.py`):

- **Always exempt:** loopback (this machine) and CF-tunneled requests. The
  tunnel terminates at cloudflared on localhost, so remote traffic both arrives
  as `127.0.0.1` *and* carries `Cf-Access-*` headers, and has already passed CF
  Access OTP. Remote access is unchanged.
- **Gated:** every other origin (a real LAN IP). `request.remote_addr` is the
  real TCP peer — we deliberately ignore `X-Forwarded-For` so a LAN client can't
  forge a loopback source.
- **Locked by default, no LAN bootstrapping.** Until a passcode is set, LAN
  devices are locked out and shown an informational *"set it on the host"* page
  — they can **not** create the first passcode (otherwise the first stranger to
  reach the dashboard could claim it). Only an exempt context (the host, or a
  CF-tunneled session) can set the first passcode. Once one exists, LAN devices
  get a *login* page. Auth is a 30-day HMAC-signed `httponly` cookie; changing
  the passcode rotates the signing secret and invalidates every existing
  session. Light per-IP brute-force throttle. PBKDF2-SHA256 (200k) passcode hash.
- **Host control:** Settings → Connectivity → **Network access** is where the
  owner sets/changes the passcode (the host is exempt, so no current-passcode
  needed). This is the *only* way to set the first one.
- Storage: `data/local_auth.json` (gitignored — holds the hash + signing
  secret; lives in `data/`, not `data/projects/`, so `load_projects()` ignores
  it). Endpoints: `/api/local-auth/{status,set,login}`; pages `/_mc/local-{locked,login}`.

  **Requires a server restart to activate** (it's a `before_request` gate + new
  routes). On restart, LAN devices that previously had open access are locked
  until the owner sets a passcode on the host — the host (localhost) and phone
  (tunnel) are unaffected.

## [2026-06-05] — Fix the macOS app: broken Claydo images + the Claude-CLI dead-end (winget on Mac)

Two macOS-only bugs in the frozen `.app`, both fixed in shared source so every
future Mac build inherits them. (Surfaced by a user whose fresh download bounced
in the Dock and never opened.) Windows behavior is unchanged.

**Broken Claydo images.** The UI loads the mascot from `/assets/claydo-*.webp`
(the floating "Ask Claydo" button + the agent avatar), but `build-macos.spec`
only bundled `static/` and `installer/clayrune.png` — the `assets/` dir was never
shipped, so `/assets/...` returned **404** inside the bundle and the WebView drew
its broken-image placeholder. (Verified live against the shipped build:
`/assets/claydo-idle.webp` → 404 while `/` → 200.) Fix: bundle `assets/` in the
spec, and serve the `/assets/<file>` route from `_APP_DIR` (== `sys._MEIPASS`
when frozen) instead of `Path(__file__).parent`, which resolves into the PYZ
archive in a frozen app.

**"Claude CLI not found → Installing Node.js via winget…".** `_install_claude_cli()`
hardcoded `winget` (a *Windows* package manager) when npm was missing — a
guaranteed dead-end on macOS/Linux. Worse, the real problem was usually **PATH**,
not a missing CLI: macOS GUI apps launched from Finder/Dock inherit launchd's
minimal PATH (`/usr/bin:/bin:/usr/sbin:/sbin`), so a `claude` in `~/.local/bin`
and a `node` in `/opt/homebrew/bin` are invisible — both to our `claude --version`
check and to the `claude` Node shim when it tries to exec `node`. Fixes:
- New `_augment_unix_path()` prepends the standard user binary dirs that actually
  exist (`/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`, `~/.claude/bin`,
  `~/.npm-global/bin`, `~/.nvm/current/bin`); called at startup on macOS/Linux.
- `_install_claude_cli()` now dispatches per platform: Windows keeps winget→npm;
  macOS/Linux use npm when present, else Anthropic's official native installer
  (`curl -fsSL https://claude.ai/install.sh | bash`) — no Node or Homebrew
  required. winget is never invoked off Windows.

**Rollout.** These are source fixes; the installed `.app` is frozen, so users
only get them via a **new signed + notarized macOS build** (`pyinstaller
build-macos.spec` → `tools/notarize-macos.sh`; see `docs/MACOS_NOTARIZATION.md`).

## [2026-06-04] — v2.0.0 (major release)

First major version since v1.5.1. Headline themes: a redesigned settings
surface, sticky/cheaper agent behavior, and runtime efficiency for long-lived
sessions.

**Highlights**
- **Settings, redesigned.** WhatsApp-style three-level drill-down (categories →
  sub-list → settings) with live search and depth-aware hardware-back.
- **Sticky agent settings (default on for new installs).** Brief-replies
  "Everywhere" is baked into each chat's spawn system prompt — cached and
  authoritative instead of re-sent every turn. Flipping a CLI-flag setting
  (model/effort/…) mid-session resumes the live session so it takes effect.
  (System-prompt settings apply to fresh chats only — `claude -r` restores the
  original prompt; verified.)
- **Brief replies on desktop** — 3-way Off / Phone / Everywhere, prose-only
  brevity (code, edits, and tool work are never shortened).
- **Search past chats by transcript content** (project-scoped).
- **PLAN tab revived** — detects plan docs without needing plan mode.
- **`--effort` knob** — per-agent + per-project effort control.
- **Self-learning skills (Phase 4) + SQLite migration foundation (Phase 0)** land
  as new internal subsystems (`distiller.py`, `db.py`) with test coverage.

**Efficiency**
- **Per-project MCP trimming** — load only the servers a project needs.
- **Idle-eviction of warm Mode B sessions** to reclaim their MCP fleet.
- Windowless launch by default on Windows; restart + shut-down power menu.

**Fixes**
- Keep SSE open while blocked on AskUserQuestion (turn_complete race); the form
  no longer silently fails to reappear after a DOM wipe.
- Resumed sessions keep their transcript across a process death.
- Restart/crash no longer orphans child processes.
- Mobile uploads + agent-text URL linkification.
- Modal no longer re-docks to the right after you drag it free; settings modal
  sizes to its content.

## [2026-06-04] — Keep a conversation's window open after its process dies (detach, don't delete)

A conversation tab used to **vanish on its own** once the server stopped listing
its session in `/agent/status`. That happens in normal operation: the guardian
**purges any non-running/non-idle session from the in-memory `agent_sessions`
after 30 min** (`server.py` "Purge stale sessions from memory"), and a restart
clears the dict entirely (revival is lazy, on the next message). The frontend
treated "absent from `/agent/status`" as **stale** and *deleted* the entry from
`agentHistory` — so a finished chat the user might still want to continue would
silently disappear ~30 min later, with no restart and no warning. The
conversation itself was never gone (transcript on disk + `agent_log`; a follow-up
resumes it via `claude -r`), but the open window was — which "feels like it's
gone."

Fix is frontend-only: the stale-handler now **detaches instead of deletes**. For a
session the server no longer lists we keep the `agentHistory` entry, its rendered
output buffer, and its status cache; we tear down only the live SSE + watchdog and
mark the entry **`stopped`**. `stopped` is already a first-class status — it shows
the "Type to resume conversation…" input, draws no Stop button, and is *not* in the
`wantsLiveStream` set, so it never triggers a doomed SSE reconnect to a session the
server forgot. Manual conversations stay visible as a tab (automated schedule/
hivemind runs still drop to the Runs panel when terminal); the user can still
✕-close one deliberately. Typing in the detached tab routes through the existing
`/agent/send` → `_revive_from_agent_log` → `claude -r` path (same session_id), so
it reactivates in place with full context.

Scope note: this covers the common "window vanishes while the dashboard stays open"
case (the 30-min purge, transient desync). A **server restart** still reloads the
page (heartbeat detects `started_at` changed), and the `mc_open_modals` snapshot
restores modal position but not the active chat — after a restart the conversation
is reachable from the picker but isn't auto-restored into the chat view. Closing
that gap (persist + restore the active conversation across a restart) is a separate,
larger change.

Follow-up — no "Blocked" flash on the way to STOPPED. A detaching window briefly
showed a **"Blocked"** pill before settling on STOPPED, because two client paths
marked a *dead-but-resumable* session `error` (which renders as "Blocked" via the
`error → status_blocked` label map) just before the detach flipped it to `stopped`:
the `es.onerror` retries-exhausted branch ("server lost the session") and the 15s
watchdog's "session vanished" branch. Both now mark **`stopped`** directly —
consistent with the detach — so a lost/resumable session goes straight to STOPPED.
Genuine turn errors (server `status:error`, SSE `type:error`) are untouched and
still surface as "Blocked". If a session is actually still alive (a transient SSE
blip), the next status poll restores its real running/idle status.

Files: `static/index.html` (the `fetchAgentStatus` stale-handler: `_isDetached`
replaces `_isStale`; detach loop preserves entry/buffer/cache and sets `stopped`;
`activeAgentTab` no longer cleared for these; plus the `es.onerror` and 15s-watchdog
"lost session" branches now set `stopped` instead of `error`). **Activates on a hard
tab reload** (SPA HTML only — no server restart needed).

## [2026-06-03] — Brief replies on desktop too (Settings → Interface → Brief replies)

The "mobile brief replies" feature (hidden Telegram-style directive prepended to
the Claude-bound copy of each message, user's chat bubble unchanged) was gated to
phone-sized viewports only (`client="mobile"`, set when `innerWidth<=960`). Ron
wanted it forced on desktop: answer short, elaborate only when asked.

The single phone-only toggle is now a **3-way segmented control — Off / Phone /
Everywhere** — backed by two server bools so it stays backward compatible:
- `mobile_brief_replies_enabled` (existing) → **Phone** (only `client="mobile"`).
- `brief_replies_always_enabled` (new, default off) → **Everywhere**: applies the
  directive on every Claude dispatch, desktop included. **Supersedes** the phone gate.

`_apply_mobile_brief()` is the single chokepoint all 8 dispatch paths flow through
(including every auto-router branch — same-tier write, tier-switch respawn,
router-off), so the new switch covers them all. The "Everywhere" path uses a
**device-neutral** directive (`_BRIEF_REPLY_DIRECTIVE_ALWAYS`) — it can't say "from
a phone" / "switch to PC" — and explicitly scopes brevity to **prose only**: code,
file edits, and tool work are never truncated. Scope is the Claude path only (same
as the original feature; non-claude providers are unaffected).

Files: `server.py` (config default, neutral directive, gate logic, editable-keys
whitelist), `static/index.html` (`briefMode` compute, seg control,
`setBriefRepliesMode()` writer). **Activates on next MC restart + a hard tab reload**
(server-side whitelist + SPA HTML).

## [2026-06-03] — Resumed sessions keep their transcript across a process death (amnesia fix)

A resumed (`-r`) Mode B session used to **reset to a fresh, context-less session**
on its next process death — losing the entire conversation. This bit hardest with
**AskUserQuestion**: in Mode B, asking a question deliberately `proc.kill()`s the
process (the headless turn auto-resolves) and waits for the user's answer to respawn.
For a session that had been resumed (every session revived from the agent_log after
a restart carries `_resume_id`), the answer-followup hit the `was_resume` guard at
`server.py` and started fresh — so the agent lost all in-flight context and had to
retrace everything. Idle-eviction (just shipped) revives through the same path, so
it would have triggered the same amnesia for any revived-then-idle session.

Root cause: the guard meant to stop a resume **death-loop** (an `-r` that dies
instantly) was over-broad — it treated *any* session that was ever a resume as
"fragile," even one that resumed cleanly, ran many turns, and only died later.

Fix — distinguish a genuinely fragile resume from a healthy one:
- New `_resume_confirmed` flag, set the first time a (possibly resumed) process
  produces assistant output. A resume that produced output has proven it loads.
- `_resume_is_fragile(was_resume, resume_confirmed)` (pure, unit-tested): a dead
  session is only abandoned-to-fresh if it was a resume that **never produced
  output**. A confirmed resume that dies later (AskUserQuestion kill, idle-eviction,
  or a crash) now **resumes with `-r`**, preserving the transcript (subject to the
  existing 5 MB cap).
- `_resume_confirmed` defaults `False` in both `_revive_from_agent_log` dicts.

Tests: `tests/test_resume_revival.py` (5 cases — predicate truth table + the reader
flips the flag on output + a no-output resume stays fragile). Activates on next MC
restart (stream-reader + followup-path change).

## [2026-06-03] — Per-project MCP trimming: load only the servers a project needs (efficiency)

Step 1 of cutting the per-session process fleet (companion to the idle-eviction
entry below). Until now MC passed **no** `--mcp-config` at dispatch, so every
session inherited the *full* global+project+plugin MCP fleet regardless of the
project — tradingview + sequential-thinking (global `~/.claude.json`), filesystem
(project `.mcp.json`), the engram memory plugin, and the claude.ai remotes
(Gmail/Calendar/Drive/Uber). A trading project that never opens a chart still
spawned tradingview; a docs project still carried the lot.

A project may now declare **`enabled_mcp_servers`** (a list of server names). When
present, the dispatch builds an inline `--mcp-config` containing exactly those
servers and adds `--strict-mcp-config`, so Claude Code loads **only** that subset.

- **Default-OFF, opt-in, fail-open.** A project without a *list-valued*
  `enabled_mcp_servers` resolves to `None` → no flags emitted → byte-identical to
  the previous behavior (full fleet). Any error while resolving the config also
  returns `None` — trimming can never break a dispatch. `_resolve_project_mcp_config`
  (server.py) is the gate; `ClaudeRuntime.build_command(mcp_config_json=…)`
  (agent_runtime.py) is the single flag-emit site.
- **Catalog** = merge of global `~/.claude.json` mcpServers + the project's
  `.mcp.json` + a built-in re-declaration of **engram**. `--strict-mcp-config`
  drops *plugin* MCP servers too (verified: an empty config → engram tools report
  `TOOL_MISSING`), so engram — a plugin, not an mcpServers entry — is re-declared
  as `{"command":"engram","args":["mcp","--tools=agent"]}` (binary on PATH, stable
  across plugin bumps) and is selectable by name. A trimmed project that lists
  `engram` keeps full memory; its tools just move to the `mcp__engram__*` namespace.
- **Empty list `[]`** is a valid maximal trim (`{"mcpServers":{}}` → no MCP servers).
  Unknown names are logged at `warn` and skipped.
- **Pilot (this deploy):** `mission_control` + `clayrune_website` →
  `["filesystem","engram"]`; `engulfing-analyst` → `["pg","filesystem","engram"]`
  (its Postgres MCP is load-bearing for the diagnostic skill). Each drops
  tradingview + sequential-thinking + the four claude.ai remotes while keeping
  memory. Reversible: delete the field to restore the full fleet.
- Verified end-to-end against CC 2.1.158: a real `claude` run with the pilot
  config reported `mcp_servers: [filesystem connected, engram connected]` (no TV /
  seq-thinking / claude.ai) and successfully invoked both `mcp__filesystem__*` and
  `mcp__engram__mem_search`. Tests: `tests/test_mcp_trim.py` (15 cases).

The new code activates on the next MC restart (same restart that arms idle-eviction
below); the per-project field itself is read live at dispatch.

## [2026-06-03] — Idle Mode-B sessions evicted to reclaim their MCP fleet (efficiency)

Step 2 of cutting the steady-state process footprint: every warm Mode B session
holds a `claude.exe` + a ~20-process MCP-server fleet, and they accumulate as
projects are touched. A new guardian check (`_should_evict_idle_session` →
"State 8" in `_guardian_check_session`) tears down a warm session's process tree
after `idle_eviction_minutes` of inactivity; the next user message transparently
respawns it via the existing followup path with `claude -r <csid>`, so the
conversation continues with full context.

- Only **`idle`** Mode B sessions with a live process are evicted — a `running`
  session (mid-work), one with queued followups, or one waiting on the user
  (question / plan approval) is never touched.
- The `evicted` flag makes guardian State 1 skip the now-dead-process session
  instead of marking it `error`; it's cleared on respawn so a genuine later
  crash is still surfaced.
- New config: `idle_eviction_enabled` (default **false**) + `idle_eviction_minutes`
  (default 30). Ships off — same posture as `scribe_checkpoint`; enable live via
  `/api/config` (no restart). Tests: `tests/test_idle_eviction.py` (12 cases).

Step 1 (per-project MCP server trimming) and any shared-daemon reuse (step 3)
are tracked separately.

## [2026-06-03] — Restart/crash no longer orphans child processes (leak fix)

A process-leak audit found ~239 stray processes (~5.8 GB RAM) accumulated across
two weeks of MC restarts: orphaned `claude.exe` agent trees + their MCP servers
(node/cmd/engram/conhost) and — worst — **29 of 30 `cloudflared.exe` connectors**,
all with dead parents. Root cause: the restart path (`_perform_server_restart_async`
→ `_stop_all_sessions_for_restart`) re-execs via `os._exit()`, and (a) never stopped
the Cloudflare tunnel, (b) bounded the agent tree-kill to 4s before exiting, and
(c) kept tracked child PIDs only in memory — so the new instance had no knowledge
of, and no way to reap, whatever the old instance failed to kill.

Three fixes (all best-effort, fail-safe, no new dependencies):

- **Tunnel stop on restart/shutdown.** `_stop_all_sessions_for_restart` now calls
  `tunnel_supervisor.get().stop()`, so `cloudflared.exe` is torn down instead of
  orphaned on every restart/shutdown.
- **Child PID ledger.** `_register_process` / `_unregister_process` persist the live
  child PIDs (+ OS image name & creation time) to `data/mc_child_pids.json`
  (atomic write, OUTSIDE `data/projects/` so `load_projects()` never sees it).
- **Startup reaper.** `_reap_prior_instance_strays()` runs before any subsystem
  spawns, tree-killing any ledgered PID still alive AND still the same process.
  PID reuse is guarded by an image-name + creation-time (±2s) match, so it can
  never friendly-fire an unrelated process — proven in `tests/test_pid_reaper.py`
  (incl. the live MC surviving a mismatched-identity ledger entry). Identity uses
  dependency-free ctypes (`OpenProcess` / `GetProcessTimes`); psutil not required.

Rollout: activates from the NEXT restart onward — the currently-running old
instance writes no ledger, so the first restart's reaper is a no-op; every restart
after that reaps cleanly. Server changes need an MC restart.

## [2026-06-02] — Windowless launch by default + power menu (restart / shut down)

Two related launcher/runtime changes.

**Windowless launch (Windows).** End users launching via the Desktop / Start
Menu shortcut saw a cmd console streaming the Flask server log. The shortcut
now targets a new `installer/start-hidden.vbs` (run via `wscript.exe`), which
starts `start.bat` with a hidden window and sets `CLAYRUNE_HIDDEN=1`. In that
mode `start.bat` redirects server output to `data/logs/clayrune.log` (no
console exists to show it) and skips the icon-setting powershell flash + the
otherwise-invisible `pause`. Developers still get a live console by running
`start.bat` (or `python server.py`) directly — unchanged. `install.ps1` and
`install-prompt.md` point both the `.lnk` and the post-install launch at the
VBS via the absolute `System32\wscript.exe` path; both fall back to `start.bat`
if the VBS is missing. `data/logs/` is gitignored.

**Power menu.** With the launcher windowless there was no obvious way to stop
the server, and restart was buried in Settings. A new **Power** sidebar item
(⏻) and a Settings → Server "Restart / shut down…" button both open
`openPowerDialog()` (refactor of `openRestartConfirmation`), which shows the
shared active-session/hivemind warning plus **Restart** and **Shut down**
actions. New `POST /api/system/shutdown` mirrors `/api/system/restart`'s
confirm + 409-blocker semantics, then `_perform_server_shutdown_async` stops
all sessions (bounded, hard-watchdog) and `os._exit()`s WITHOUT respawning.
Shutdown renders a terminal "powered off" overlay (no reconnect poll); it is
audited via the shared restart log (`action: "shutdown"`).

Server changes need an MC restart; FE changes need a hard browser refresh.

Files: `installer/start-hidden.vbs` (new), `installer/start.bat`,
`installer/install.ps1`, `installer/install-prompt.md`, `installer/README.md`,
`.gitignore`, `server.py` (`_perform_server_shutdown_async` +
`/api/system/shutdown`), `static/index.html` (`openPowerDialog` /
`performShutdown` / `showPoweredOffOverlay`, sidebar Power item, Settings
Server row).

## [2026-06-02] — AskUserQuestion form not shown until a resync (SSE turn_complete race)

Recurring bug: an agent calls `AskUserQuestion`, the chat shows **COMPLETED**
with no form, and the question only appears after some unrelated action (Ron's
case: taking a screenshot → `visibilitychange` → `fetchAgentStatus` reconnects
the stream and re-emits the form).

Root cause is a TOCTOU race in the SSE generator. On `AskUserQuestion` the reader
thread runs, in order (`server.py` Mode B reader): `pending_questions.append` →
`waiting_for_question=True` → `status='idle'` → `proc.kill()`. The SSE generator
runs on a separate thread and, within one poll, reads `pending_questions` (loop
top) and *then* `status`. The reader can flip `status='idle'` *between* those two
reads, so that iteration emits `turn_complete` (idle) **without** the `question`
event. The FE `turn_complete` handler clears `waitingForQuestion` and calls
`es.close()` — so the next poll that would carry the question never reaches the
client. The form is lost until a reconnect re-emits it. Semantically the bug is
broader: a session idle *only* because it's blocked on user input is not
"turn complete".

Fix — suppress the idle/`turn_complete` teardown while the session is blocked on
user input (`waiting_for_question` or `waiting_for_plan_approval`), keeping the
stream open so the `question` event is delivered/re-delivered:
- `server.py` Mode B SSE loop: gate `turn_complete` on `not waiting_on_user`.
- `server.py` Mode A SSE loop: don't close-on-idle while waiting (symmetric).
- `static/index.html` `turn_complete` handler: ignore while the cache shows
  waitingForQuestion/PlanApproval (guards event-ordering + old-server races).
- `static/index.html` `status` handler: ignore a stray non-terminal `idle` while
  waiting.

Server change needs an MC restart; FE change needs a hard browser refresh.

Files: `server.py` (SSE `generate()` Mode A/B branches), `static/index.html`
(SSE `onmessage` `turn_complete` + `status` handlers).

## [2026-05-28] — Mobile resume wedge: heartbeat-probe + fail-fast send

Android Doze / App Standby (worst on Samsung One UI / Z Fold) was parking
the Capacitor WebView's network stack while the app was backgrounded.
When it came back, every existing `EventSource` and `fetch` handle
reported "alive" but the underlying sockets were dead — POSTs hung
silently and SSE never delivered. Symptom Ron hit in the Day Trading
Engulfing chat: typed prompt, input cleared, no echo, status frozen on
"COMPLETED", and even subsequent agent replies in *other* chats didn't
arrive until the app was killed and restarted. The existing
visibilitychange handler was running but only calling
`fetchAgentStatus()` — which itself uses the wedged fetch path, so the
resync went nowhere.

`_resyncOpenModalsFromServer` now opens with a 4s-timeout heartbeat
probe to `/api/system/heartbeat`. If the probe fails (network parked
state), it force-closes every `agentEventSources[]` + watchdog, clears
stale `_sendInFlight` markers older than the 8s gate, and shows a
"Reconnecting after sleep…" toast — then `fetchAgentStatus()` rebuilds
fresh SSE for running / active-tab idle sessions. Fail-fast on the user
action side too: `sendFollowup`'s POST gets a 12s `AbortController`
timeout. On abort or any catch we delete the in-flight marker, restore
the typed message into the textarea, repaint the local echo as
`.agent-echo-failed` (so reconcile's `.agent-echo` wipe doesn't erase
it), and surface a toast — instead of vanishing the user's prompt with
no trace.

Files: `static/index.html` `_resyncOpenModalsFromServer` + `sendFollowup`
catch path.

## [2026-05-22] — System-status popover renders above project cards

The header system-status popover (rate limit, agent providers, install
health) was painting *behind* the project-card grid — unreadable. Root
cause: the popover `<div>` lived nested inside the header subtree, which
traps it in a stacking context that the card grid paints over. A
high `z-index` (9000) can't escape that — and the prior `position:fixed`
blur fix didn't help either, since `fixed` escapes the *containing block*
but not the *stacking context*.

Fix: moved `#sys-status-popover` to be a direct child of `<body>`, a
sibling of `#modal-layer` — the same proven pattern modals use. It is
still positioned at runtime by `_positionSysStatusPopover()`. Now
reliably paints above the dashboard.

Also sharpened `_positionSysStatusPopover()`: it now snaps to the
*device* pixel grid (`× dpr → round → ÷ dpr`) instead of CSS pixels. On
Windows at 125%/150% display scaling an integer CSS px still maps to a
fractional device px, leaving the composited panel slightly soft. Device
snapping makes the text crisp at any scale.

### AskUserQuestion rendered as plain text in Gemini-configured projects

A Claude agent's `AskUserQuestion` showed up as a grey
`[Agent question: … — answer via follow-up message]` line (often
duplicated on every SSE reconnect) instead of the interactive option
card — whenever the *project* was configured with a non-Claude provider.

Root cause: the `question` SSE handler gated the interactive form on the
**project's** configured provider (`_capsForProject`). But the running
**session** is what emits the event — a Claude session in a
Gemini-configured project would wrongly fall back to text. And the
fallback path (`appendAgentLine`) doesn't dedupe, so SSE reconnects
stacked copies.

Fix: a `question` event only ever fires when an agent actually emitted a
structured `AskUserQuestion` (providers lacking the tool have the
instruction stripped from their prompt and never reach this path), and
the form's answer round-trips as a provider-agnostic follow-up message.
So the gate was both wrong and unnecessary — removed it; the handler now
always calls `renderAgentQuestion()`, which dedupes by `question_id`.

### Project ⋯-menu "Default Provider" picker could stay hidden

The per-project provider picker (⋯ menu → Default Provider) is gated on
`_agentProviders.length > 1`. That list is fetched once on load — but the
fetch is fire-and-forget, and the modal ⋯-menu HTML is built once when the
modal opens. If the modal opened before the fetch resolved (common on a
mobile cold-boot), the picker was built with an empty list and stayed
hidden until the modal was closed and reopened. A failed fetch was also
cached as `[]` forever, hiding it permanently.

Fix: `_ensureAgentProviders()` no longer caches a failed fetch (leaves
the list `null` so the next call retries). And the ⋯-menu builder, when
the list isn't loaded yet, kicks the fetch and calls `refreshModal()`
when it lands — so the picker appears on its own instead of needing a
modal reopen.

### Removed the long-session advisory toast

The "⏳ … has run N turns — may be losing early context" toast is gone.
It was well-intentioned (a nudge to restart so a long session reloads
its Step-6 memory fresh) but in practice a 14-second nag. The server
still computes `long_session_advisory`; nothing renders it now. If the
nudge is wanted back, it belongs as an inline, dismissable session-panel
hint — not a toast.

## [2026-05-21b] — Voice input on the Android APK

Mic-to-text in the chat input on the Clayrune Android app. Tap the 🎤
button → Android's system mic dialog appears → talk → dialog closes and
the recognized text drops into the textarea. You still hit Send.

### Hard-won gotchas (don't relitigate)

- **Inline streaming mode (`popup: false`, `partialResults: true`) is
  broken in the upstream plugin.** Two bugs combine to make it unusable:
  (1) when `partialResults: true`, the plugin resolves `start()`
  immediately, then any subsequent `onError` does `call.reject(...)` on
  an already-resolved call → SWALLOWED, no toast, button stuck red;
  (2) Android's `SpeechRecognizer` instance gets sticky between sessions
  — first call works, second silently hangs (Promise never resolves AND
  never rejects). Tried watchdog timers; couldn't get reliable
  back-to-back recordings.
- **`popup: true` (the system RecognizerIntent fullscreen dialog) is
  bulletproof.** Google's system UI handles the entire lifecycle: mic
  prompt, silence detection, retry, error UX. Repeat invocations Just
  Work. Tradeoff: a system dialog instead of inline streaming — worth
  it.
- **`language: 'en-US'` works universally on the Google recognizer.**
  Using `navigator.language` (e.g. `en-IL` / `he-IL`) throws
  ERROR_LANGUAGE_NOT_SUPPORTED (code 12) which the plugin's
  `getErrorText` switch doesn't cover → surfaces as the misleading
  "Didn't understand, please try again" fallback. Stay on `en-US`
  unless we ship Hebrew packs.
- **Errors that look like "Didn't understand, please try again" are the
  plugin's fallback for unknown error codes**, not actual no-match. Real
  no-match maps to "No match". If you see this string, it's an Android
  error code the plugin doesn't have a case for (10, 11, 12, 13, 14).

### How it works

- **Plugin**: `@capacitor-community/speech-recognition@^7.0.1` added to
  `<mobile repo>\package.json`. Wraps Android's built-in
  `SpeechRecognizer` (Google's cloud recognizer when available — accurate,
  sub-second). No API key, no extra service.
- **Permission**: `android.permission.RECORD_AUDIO` + the
  `RecognitionService` `<queries>` block added to
  `android/app/src/main/AndroidManifest.xml`. The plugin handles the
  runtime prompt on first use.
- **UI** (`static/index.html`):
  - `.btn-mic` styled to match `.btn-attach`.
  - `micAvailable()` gates rendering to **native Capacitor only** —
    desktop / mobile-browser sessions never see the button.
  - `micBtnHTML(textareaId)` injected next to the attach button in both
    the dispatch row (`agent-task-<pid>`) and the followup row
    (`agent-followup-<sid>`).
  - `toggleAgentMic` / `_startAgentMic` / `_stopAgentMic`: request perms,
    invoke `SR.start({ popup: true, partialResults: false })`, append
    the returned match to the textarea (preserving any pre-typed text).
  - `_micToast(msg)` surfaces every failure path as a visible toast —
    invaluable for diagnosing what's going wrong on a phone with no
    devtools. Costs nothing in the success path.
- **Language**: hard-coded `en-US`. See gotchas below.

### Where the APK is

`<mobile repo>\android\app\build\outputs\apk\debug\app-debug.apk`
(5.9 MB). Install via `adb install -r <path>` or sideload.

### Rollback

- Remove `@capacitor-community/speech-recognition` from `package.json`,
  `npm i`, `npx cap sync android`, rebuild.
- Drop the `RECORD_AUDIO` permission + `<queries>` from
  `AndroidManifest.xml`.
- Delete the mic CSS/JS/HTML blocks in `static/index.html` (search
  `btn-mic` and `micAvailable`).

## [2026-05-21] — Multi-provider agent runtime (prototype, branch `feat/multi-provider-agents`)

**Status:** Prototype on `feat/multi-provider-agents` — DO NOT MERGE TO MASTER
until smoke-tested per §Test plan below.

First cut of the AgentRuntime abstraction (`agent_runtime.py`) that lets MC
drive any agent CLI through one interface. Claude remains the default; one
alternative provider (Gemini CLI) is wired end-to-end as proof-of-concept.

### What works

- **`agent_runtime.py` is functional** (was a stub). Exposes the `AgentRuntime`
  ABC, `SessionHandle`, `AgentEvent`, `ProviderCapabilities`, `HealthStatus`,
  `AuthState`, `OneshotResult`, and a registry (`register_runtime` /
  `get_runtime` / `available_runtimes` / `installed_runtimes` /
  `runtime_for_project`). Both `ClaudeRuntime` and `GeminiRuntime` auto-register
  at import time. See `docs/MULTI_PROVIDER_DESIGN.md` for the full design.
- **ClaudeRuntime** is a thin delegator. It does NOT yet take over the existing
  claude code path — when a project's `provider` is unset OR `claude`, the
  legacy `_dispatch_agent_internal` / `_read_agent_stream*` / `agent_followup`
  / `agent_interrupt` paths run unchanged. The runtime exposes a fallback
  `health_check()` (probes `claude` on PATH) so the provider-listing endpoint
  works without server.py needing to register hooks for this prototype.
- **GeminiRuntime** is a self-contained driver for Google's `gemini` CLI:
  - `resolve_binary()` finds `gemini` on PATH or in npm-global locations.
  - `health_check()` runs `gemini --version` and reports `installed`,
    `version`, and `auth_state` (best-effort — checks `GEMINI_API_KEY`).
  - `dispatch()` spawns `gemini --prompt <text>` with the project as cwd,
    mints a reader thread, and writes events into the MC session dict
    (`log_lines`, `status`, `proc`, ...) using the same shape claude
    sessions use.
  - The reader parses `--output-format stream-json` envelopes when present
    and falls back to plain-text lines.
  - `write_followup()` kills the prior process and spawns a fresh one with
    a short transcript-tail prepended for continuity (Mode A only — Gemini
    has no native persistent stream-json mode).
  - `interrupt()` / `stop()` kill the subprocess and emit a synthetic
    `[interrupted]` line in the log.
  - `oneshot()` runs a non-streaming `gemini --prompt` for Scribe-style
    cheap calls (returns plain stdout).
- **server.py routing** (`_dispatch_agent_internal`): a new branch near the
  top checks the project's `provider` field. If non-claude, it routes to a
  new `_dispatch_via_runtime` helper that uses the AgentRuntime; otherwise
  the legacy claude path runs untouched. Mirror branches were added to
  `agent_followup` and `agent_interrupt` so they hand off to
  `runtime.write_followup()` / pseudo-respawn on non-claude sessions.
  `agent_stop` works as-is — it kills `session['proc']` regardless of
  provider.
- **New endpoint** `GET /api/agent/providers` returns the registered
  runtimes with `installed`, `version`, `install_hint`, `capabilities`,
  and which is the default. The UI uses this to populate the per-project
  provider dropdown.
- **UI** (`static/index.html`): the project-modal three-dot menu shows a
  new "Agent Provider" submenu (only when ≥2 runtimes are registered;
  with claude only, it's hidden). The dropdown is built from
  `/api/agent/providers`; not-installed providers are visible but
  clicking them shows a toast with the install hint instead of switching.
  `setProjectProvider(projectId, provider)` POSTs `{provider: ...}` to
  `/api/project/<id>` — the existing update endpoint accepts arbitrary
  fields so no schema change was needed.
- **Per-project provider with global default.** A project without an
  explicit `provider` field is treated as `claude` everywhere
  (`(p.get('provider') or 'claude').lower()`). `agent_runtime.default_runtime_name()`
  is the global default.

### What does NOT work / known limitations

- **Skills / MCP / Memory hooks are gated by construction**, not yet by an
  explicit `runtime.capabilities()` check at the integration points. For
  Gemini this is fine because the non-claude dispatch path simply never
  builds `--mcp-config` / `--append-system-prompt` flags. But if MC adds
  new claude-specific call sites later, they should consult capabilities
  rather than assuming claude.
- **MEMORY.md auto-loading.** For Gemini the system_prompt (MEMORY +
  AGENT_RULES) is **prepended to the task body**, not written to
  `GEMINI.md` as the design doc recommends. Functional, but the context
  text doesn't survive across the per-turn prompt the way `CLAUDE.md`
  does for claude. Follow-up: implement `runtime.context_file_path()`
  writes at dispatch time.
- **Session resume.** Gemini sessions cannot truly resume — `write_followup`
  re-spawns fresh with a short transcript-tail as context. Cross-turn
  continuity is approximate.
- **Scribe / condense / hivemind / mcp_installer security scan** still
  shell out to `_resolve_claude()` directly. The prototype scope is the
  user-facing dispatch loop; housekeeping call sites stay claude-only
  in v1 (consistent with the design doc's PR-1 scope).
- **Auth UI.** No `/api/agent/<provider>/auth-*` endpoints yet. Gemini
  auth (API key / oauth) is the user's responsibility — set
  `GEMINI_API_KEY` or run `gemini auth login` once in a terminal.
- **agent_log entries** are not written for non-claude sessions (the
  log-completion path lives inside the claude reader thread). The session
  still appears in MC's UI and history; it just won't show up in the
  per-trigger run history.
- **`claude_session_id` is absent on non-claude sessions** — that field
  is claude-specific. UI fallbacks (`session_id` instead) keep working.

### How to test (smoke plan)

Set up:
1. Check out `feat/multi-provider-agents`. Restart MC.
2. Confirm `GET /api/agent/providers` lists both `claude` and `gemini`.
3. Install Gemini CLI if missing: `npm install -g @google/gemini-cli`,
   then set `GEMINI_API_KEY` or run `gemini` once in a terminal to auth.

Existing claude users unchanged:
4. Pick any existing claude project. Confirm the project-modal menu still
   shows "Agent Model" and (if multiple runtimes registered) a new "Agent
   Provider" submenu with **Claude Code** selected and active dot green.
5. Dispatch a task: `What's 2+2?`. Verify it streams and completes as
   today. Verify `git diff master server.py` shows the legacy claude path
   was untouched apart from the early branch.

Gemini end-to-end:
6. Create a NEW project pointing at any folder. Open its menu → Agent
   Provider → Gemini CLI. Verify the toast confirms the switch.
7. Dispatch: `List three small files in this folder and describe them.`
   Verify the session goes `running` → text streams into the log →
   `completed`.
8. Followup: send another message. Verify the prior turn's text appears
   in the new prompt context (look for `[Prior turn excerpt for context only]`
   prepend on inspection — the model should respond as if continuing).
9. Interrupt: while a turn is running, send a different message via the
   interrupt button. Verify the old process is killed and a new one
   spawns with the new message.
10. Stop: while running, click Stop. Verify status flips to `stopped`
    and the process is killed.

Rollback:
- Either: `git checkout master` (this branch is throwaway-able).
- Or: per-project — open the project menu and switch the provider back
  to **Claude Code**. Existing sessions on the prior provider keep
  running; new dispatches will use claude.
- Or: globally — delete the `provider` key from every project JSON in
  `data/projects/*.json`; default falls back to claude.

### Files changed

- `agent_runtime.py` — rewritten from stub to functional module (~640 lines).
- `server.py` — import, new `_dispatch_via_runtime()`, provider branches in
  `_dispatch_agent_internal` / `agent_followup` / `agent_interrupt`, new
  `/api/agent/providers` endpoint (~140 lines added).
- `static/index.html` — provider cache + `setProjectProvider()` + project-
  modal submenu (~50 lines added).
- `docs/MULTI_PROVIDER_DESIGN.md` — already shipped by ws_architect; the
  prototype implements its PR-1 scope.
- `CHANGELOG.md` — this entry.

## [2026-05-19c] — Mobile hamburger drawer for full global nav

Mobile UI (≤960 px) was missing entry points for Skills, MCP, Shared Rules,
and Processes — none of those have a slot in the 5-item bottom tab bar and
only Settings was reachable (via the avatar). Added a hamburger drawer that
mirrors the full sidebar.

- **Hamburger button** in `.mc-app-bar` (top-left, opposite the existing
  avatar/Settings shortcut). Three-bar icon, matches the avatar's circular
  outline aesthetic.
- **Slide-in drawer** from the left with backdrop dim. Contains Dashboard,
  Skills, MCP, Backlog, Hivemind, Scheduler, Settings, Shared Rules,
  Processes, divider, Incognito. No Projects list — the Home tab is the
  project browser; drawer is for global nav only.
- **Coexists with the bottom tab bar.** Bottom bar stays as the
  quick-access strip; items appearing in both surfaces is by design
  (standard Material/iOS pattern). Future: let users customize which
  surfaces pin into the bottom bar.
- **Android hardware back** integrated as a third sentinel
  (`_mcDrawerHistoryActive`) at higher priority than the existing
  modal/conv sentinels — back closes the drawer first if open, then
  unwinds modal/conv as before. UI-initiated close (× / backdrop tap /
  item tap) synthetically pops the sentinel via `_mcUnwindHistory(1)` so
  the next back press isn't swallowed by a dead entry. Same discipline as
  the modal/conv handlers.
- **Active row mirrors `.sidebar-item.active`** on every open, so the
  highlighted drawer item matches whatever surface the user is currently
  on (sidebarNav already keeps sidebar-item active state in sync at all
  viewport widths).

Frontend-only — no server changes. USER_GUIDE updated.

## [2026-05-19b] — Agent panel: inline images + mobile conversation drill-down + truthful status

Five-in-one bundle on top of [2026-05-18 status-badge consolidation] — most of
the day's WIP shipped together so the agent panel reads honestly again on both
desktop and mobile.

**Inline image rendering in agent output.** Absolute paths to image files
(`.png/.jpg/.gif/.webp/.bmp/.svg/.ico/.tiff/.avif`) in agent output now render
inline at natural size, capped to chat width. Click → zoom lightbox (reuses
the mermaid-viewer chrome — backdrop, +/-/Fit toolbar, Esc, scroll-wheel zoom
with Ctrl). Backend: new `GET /api/serve-image?path=...` (`realpath`-based
allow-list: must live under a project working dir, the uploads dir, or the
data root; ext must be in `_IMAGE_EXTS`; 415/403/404 otherwise; 1h cache).
Frontend: `formatAgentText` tokenizes images FIRST so subsequent code/bold/URL
regexes can't shred the `<img>` markup, then swaps the tokens back in just
before return. Two regex guards prevent URL-shaped strings from being matched
as filesystem paths — negative lookbehind `(?<![\w:/%])` blocks `p://` inside
`http://`, negative lookahead `(?!:\/\/)` blocks phantom-drive `p://`. Two
non-obvious gotchas committed in code comments: (1) `loading="lazy"` must NOT
be set — img starts at `display:none` and only flips to `block` `onload`, but
a `display:none` element never intersects, so lazy + display:none = perma-
deadlock (IntersectionObserver never fires); (2) the path regex previously
matched `p:/` inside `http://...png` and generated phantom requests — the two
guards above fix it.

**Stale attachment 404 quieting.** Backlog attachments whose stored file no
longer exists on disk were generating an `<img>` request → console 404 on
every render. Added `_decorate_attachments(project)` (server.py) that stamps
`_present: bool` on each backlog attachment by `is_file()`-checking the
stored name. Called from both `load_project` and `load_projects`. `save_project`
strips runtime-only fields before persisting so the flag never leaks into
the JSON. `attHTML` (frontend) skips the `<img>` when `_present === false`
and shows a `⚠️` icon with a "File missing on disk" tooltip — row stays
visible and deletable.

**Multi-conversation drill-down (mobile, WhatsApp-Communities style).** When
a project has >1 active conversation on mobile (≤960px), the Agent panel
shows a vertical list of rows (avatar + status ring + name + time + status
sub-line + badges) instead of a horizontal tab strip. Tap to drill in;
"← All conversations" back bar returns. Single conversation auto-opens the
chat directly; 0 → dispatch screen. Desktop is unchanged — classic horizontal
tab strip remains (the whole modal is visible at once, so forcing a list/back
dance just adds clicks). New: `conversationListHTML`, `conversationRowHTML`,
`backToConvList`, `mcBackFromConv`, `agentConvNew[projectId]` flag (forces
the dispatch screen even with sessions present).

**Two-level Android hardware-back history.** The single-sentinel scheme from
the mobile chat ship would swallow the second back press on a drilled-in
conversation. Replaced with two sentinels mirroring UI depth — L1 "modal"
(pushed by `openProjectModal`) and L2 "conv" (pushed at drill-in). Hardware
back unwinds innermost-first: conversation → list → modal close → leave app.
`_mcSuppressPop` lets the on-screen "X / Esc / ← All conversations" buttons
synthetically pop sentinels via `_mcUnwindHistory` so the next hardware back
isn't swallowed by a now-dead entry. Critical gotcha (in-code comment):
Android WebView silently drops `history.pushState()` called *inside* a
popstate handler — sentinels MUST be pushed on the forward navigation, not
re-armed on back, or the next back falls through.

**Status truth-telling — between-turns idle = "done", not "Awaiting input".**
Completes the consolidation that started in [2026-05-18 03c6503]. `friendlyStatus`
now maps `idle-agent` (a session alive but between turns with NOTHING pending)
to `done` instead of `working`. "Awaiting input" is reserved for cases we
*actually detect* the agent is blocked on the user — a pending plan
(`waitingForPlanApproval`) or question (`waitingForQuestion`). Labelling
plain between-turns idle "Awaiting input" was a fabricated claim: the agent
finished its turn, it isn't necessarily waiting for anyone. The console
status label (new `consoleStatusLabel(status, session)`) now speaks the same
vocabulary and makes the same detected-wait distinction, so the project
badge and the in-modal console no longer contradict. Two cleanups: removed
the now-redundant `.agent-running-badge` (the single status pill is the sole
authoritative indicator), and `computeLiveStatus` promotes idle→running when
the client SSE signal is fresher than the lagging server poll (fixes the
"console says In progress but tile says Awaiting input mid-turn" gap).

**SHARED_RULES.md addition.** Bullet-pointed lists preferred over prose blocks.

**Engulfing analyst supporting artifacts.** New built-in skill
`data/skills/builtin/engulfing-diagnostic/` (auto-installs on next MC startup
via `_install_builtin_skills`) + `data/projects/engulfing-analyst/`
subdirectory with `_phase0_findings.md` baseline and first dated report
(`reports/2026-05-19.md`). The skill is read-only Postgres analysis only —
never proposes patches, never deploys, never writes to the DB. Reads the
most-recent report in `reports/` as the diff baseline so each weekly run
reports deltas, not just absolutes. The `_` prefix on `_phase0_findings.md`
is intentional — DATA_DIR exclusion rule prevents `load_projects` from
treating it as a malformed project JSON.

**Files touched:** `server.py` (~80 lines), `static/index.html` (~628 lines),
`data/SHARED_RULES.md` (one rule), `data/skills/builtin/engulfing-diagnostic/`
(NEW), `data/projects/engulfing-analyst/` (NEW).

## [2026-05-19a] — Skills Curation: Phase A close + committee review (RATIFY-WITH-CONDITIONS) + v2 design

Three-in-one Skills Curation milestone (no app code touched; design + skill body
+ documentation only).

**Phase A close.** `mc-distill` visibility bug root-caused (description-length
overflow: 920 chars exceeded CC's `skill_listing` budget under accumulated
context) and fixed by shrinking source description to 519 chars (safe-zone
between `document-commit-deploy`'s 418 and `mc-memory-search`'s 526).
`_install_builtin_skills()` propagated the fix to the installed copy via hash-
marker on restart. Verified across ~30 post-restart sessions in this project —
mc-distill consistently in every `skill_listing`. End-to-end validated: the
shipped skill produced a real, high-quality proposal (`frontend-render-hang-
diagnostic`, 70 lines, paste-safe DOM probe + 4-way diagnostic table +
loopback-curl-≠-real-network discriminator) which was reviewed and promoted
globally. The proactive trigger and the v2 promotion-on-instruction path both
work end-to-end. `data/skills/_proposed/` is empty after the promotion — the
cleanup step was missed by the promoting agent and corrected manually.

**Committee review (2026-05-19) — RATIFY-WITH-CONDITIONS.** Four-seat review
(pattern-integrity / agent-behavior / concurrency-lifecycle /
config-ops-rollback-cost) against the design + the shipped skill + the live
proposal. Unanimous RATIFY-WITH-CONDITIONS, no blockers. 15 conditions
classified into must-fix-in-design (11), must-fix-in-implementation (2),
soak-gate (2). Full synthesis appended to `docs/SKILLS_CURATION_DESIGN.md`
as the new `## Committee review (2026-05-19)` section. Brief at
`docs/SKILLS_CURATION_COMMITTEE_BRIEF.md` defines the four-seat structure for
reproducibility on future Skills Curation revisions.

**Design v2 (post-committee-review).** All 11 must-fix-in-design conditions
closed in this commit. Headline changes:
- Fingerprint design (Cond 1): single-phrase-hash REJECTED; two-stage
  deterministic normalization (canonical phrase → lowercase/sort/stopword-
  strip/hash) lands in v1; embedding-based collapse via bge-m3 reserved for v3
  if/when Step 7 ships.
- UPDATE.md schema (Cond 2): `target_files`/`target_action`/`target_rename`
  added; v1 ships `edit`-only, other actions rejected at writer.
- `Later` semantics honest (Cond 3): SKILL.md disclosure that v1 `Later` ≡
  `No` until silent Distiller (Phase 4) ships.
- Per-provenance collision rules (Cond 4): auto-authored = quick OK;
  manual/distilled = show 10-line preview first; Clayrune built-ins
  REJECTED outright (managed via source-file edit only); auto-mode aborts +
  audits, never auto-renames.
- Reverse-promotion within session (Cond 5): SKILL.md authorizes
  same-session undo with explicit instruction.
- Shared lock domain (Cond 6): `_get_skill_stats_lock(project_id)` between
  in-session push (CC process) and silent Distiller (MC process); mirrors
  Memory System §3.A.MID post-committee resolution.
- Push fingerprint dedupe (Cond 7): per-(project, fingerprint, day) 24h
  suppression by fingerprint, not session_id.
- Atomic proposal write (Cond 8): `.tmp` + rename pattern; Phase 3 audit GC
  for stale `.tmp` >24h.
- Cost cap (Cond 9): default 5k → **100k tokens/project/day** (single call
  is ~10k tokens — 5k would silently disable after first session each day).
  Mandatory `distiller_cost_cap_hit:<project>:<date>:<tokens>` structured
  log + counter on cap fire — no silent disables.
- Single kill-switch gate (Cond 10): `_distiller_should_proceed(project_id)`
  sole authority for "Distiller may fire"; regression test enumerates all
  entry points.
- Auto-mode rollback discovery (Cond 11): `/api/skills/auto-authored?since=`
  endpoint + UI badge required before Phase 5 can ship.

Open items #7 (committee — RESOLVED v2) and #9 (visibility bug — RESOLVED
2026-05-19) closed.

**Still tracked but not closed in v2** (gates appropriate phases):
- Must-fix-in-implementation: Cond 12 (PATCH.md `unable_to_backtest` allow-list
  validator) → Phase 2 of engulfing-supervisor work. Cond 13
  (`test_load_projects_excludes_underscored_sidecars` regression test) → must
  land in same commit as `_skill_stats.json`.
- Soak-gate: Cond 14 (proactive-bar calibration evidence) → gates Phase 5
  `auto`-mode default-on. Cond 15 (per-provenance no-invocation thresholds in
  audit) → gates Phase 3 audit default-on.

**Files touched:**
- `docs/SKILLS_CURATION_DESIGN.md` — v2 revision (now ~660 lines) + committee
  synthesis appendix.
- `docs/SKILLS_CURATION_COMMITTEE_BRIEF.md` — NEW (284 lines).
- `docs/PHASE_A_HANDOFF.md` — NEW (178 lines).
- `data/skills/builtin/mc-distill/SKILL.md` — description shortened from 920
  → 519 chars (Phase A); Conds 3, 4, 5 SKILL.md changes (Phase B v2). Now 212
  lines, was 158.

**Phase 2 (backend telemetry — `_skill_stats.json` + `_distiller_should_proceed`
gate + DATA_DIR regression test) unblocked by this commit but not started.**
Suggested 1-2 week soak of Step 1 in organic use before starting Phase 2 — soak
data informs Phase 2 design choices and contributes to Cond 14 evidence.

## [2026-05-18t] — Fix: Clayrune picks a stale orphan claude.exe (the actual root cause)

The real cause of the persistent fresh-PC `Cannot find module
'...\@anthropic-ai\claude-code\cli.js'`, proven on-box and fixed.

A machine with an **old** `@anthropic-ai/claude-code` install (here: a
failed bulk-exe attempt months ago) has a top-level `claude.exe` at the
npm prefix root that runs `node …\cli.js`. The current package no longer
ships `cli.js` (it uses `bin\claude.exe` + `cli-wrapper.cjs`). npm
generates `claude` / `claude.cmd` / `claude.ps1` but **never** a top-level
`claude.exe`, so a clean reinstall regenerates the `.cmd`/sh shims
correctly (→ `bin\claude.exe`) yet leaves the orphan `claude.exe`
untouched. `shutil.which()` and PATHEXT prefer `.exe`, so Clayrune's
`_resolve_claude()` selects the orphan → MODULE_NOT_FOUND — even though
`claude --version` works in a shell (different resolver). Node version,
"missing cli.js", and the EPERM lock were all symptoms/red herrings;
this is the cause. Verified: removing only the orphan flips
`shutil.which('claude')` from `claude.EXE` → `claude.CMD` and Clayrune's
exact resolution test then returns `2.1.144`.

- `server.py _resolve_claude()`: on Windows, when `shutil.which` returns a
  `claude.exe` that has a sibling `claude.cmd` in the same dir (the orphan
  signature — npm never makes a top-level `claude.exe`), return the
  npm-managed `.cmd` instead. No subprocess; a lone `claude.exe` (native
  `~/.claude/bin` installer, no sibling `.cmd`) is left untouched. This
  self-heals every existing affected install on the next Clayrune update —
  no manual claude surgery required.
- `install.ps1 Clear-ClaudeNpmLeftovers`: also deletes the prefix-root
  `claude.exe` orphan before reinstall, so fresh installs/repairs can't
  inherit a shadowing stale shim.

Committed via hunk-isolation; `server.py` pre-existing WIP untouched.

## [2026-05-18s] — Installer: correct the Claude CLI repair (supersedes [2026-05-18r])

`installer/install.ps1` only. `[2026-05-18r]` mis-diagnosed this twice
(Node-non-LTS, then "missing cli.js") and shipped a check that is wrong for
the current package — **this entry corrects both**.

On-box evidence settled it: `npm uninstall` failed with
`EPERM: operation not permitted, unlink ...\claude.exe.old...`, and after
killing claude + purging the leftover dir, `npm install` → `claude
--version` = `2.1.144` **works** while
`node_modules\@anthropic-ai\claude-code\cli.js` is **absent**.

Conclusions:
- **Real root cause:** the package ships a native `claude.exe`. A live
  `claude` process (typically one **Clayrune itself spawned** — i.e. the
  app being open during install) locks the exe, so npm's
  extract-then-rename hits EPERM, aborts the finalize, and leaves bin
  shims + a `@anthropic-ai/.claude-code-*` leftover that breaks the next
  install too. Node version was never relevant (winget "LTS" is itself
  24.15.0 here).
- **`[2026-05-18r]`'s `Test-ClaudeIntact`/`Get-ClaudeCliPath` were
  actively harmful**: they gate health on `…/claude-code/cli.js`, a path
  the *current* package no longer uses — so a perfectly working install
  would be declared broken and loop/fail for everyone. **Removed.**

Fix: health is judged **only** by `claude --version` (`Test-ClaudeWorks`,
layout-agnostic). Before every (re)install: `Stop-ClaudeProcesses` (kills
the lock — safe, runs in the bootstrap before the `claude -p` handoff) and,
on the clean path, `Clear-ClaudeNpmLeftovers` (purges the EPERM remnant
`@anthropic-ai` dir). Failure message now names the lock/EPERM cause, the
close-Clayrune + purge + reinstall steps, and the AV-exclusion / reboot
fallback. Parse-checked under Windows PowerShell 5.1.

## [2026-05-18r] — Installer: detect & repair partial Claude CLI npm installs

`installer/install.ps1` only. Triggered by a real fresh-PC failure: install
"finished" but launching `claude` crashed with
`Cannot find module '...\@anthropic-ai\claude-code\cli.js'`.

Root cause (confirmed via on-box diagnostics): a **partial global npm
install** — npm created the three bin shims but never wrote `cli.js`
(suspected trigger: Node v24, a non-LTS "Current" release, breaking the
package's install step; npm prefix and AV were both ruled out). The old
flow couldn't self-heal: `Test-ClaudeWorks` only ran `claude --version`,
and the recovery path was a bare `npm install -g` — which npm skips when
it believes the package is already present.

- `Test-ClaudeIntact` — new strong check: shim runs cleanly **and**
  `cli.js` exists at the resolved npm-prefix path (`Get-ClaudeCliPath`).
  Step-1 precheck and both install methods now use it.
- `Invoke-ClaudeNpmInstall` — npm install with an automatic one-shot
  **clean** fallback (`npm uninstall -g` + `npm cache clean --force` +
  reinstall) when the result is incomplete or a broken shim was already
  present. Plain reinstall-over-the-top no longer masks a partial install.
- Failure message now names the exact missing `cli.js` path, the clean
  reinstall commands, and — when Node ≥ 23 — flags the non-LTS Node as the
  likely cause with the LTS install command.

No app/server code; affects only fresh installs via clayrune.io /
`Clayrune-Installer.exe`. Parse-checked under Windows PowerShell 5.1.

## [2026-05-18q] — Human version display in Update Clayrune (deferred from [2026-05-18n])

Now unblocked — committed via hunk-isolation so it carries none of the
unrelated pre-existing WIP still live in `server.py` / `static/index.html`.

Settings → Update Clayrune (and the dashboard "update available" toast)
now show a synthetic human version — `vX.Y.Z build N` — for installed
vs latest, instead of only an opaque "N commits behind". Derived from the
nearest `v*` semver tag via `git describe` (base tag + commits-since +
short SHA); SHA + commit date kept as dimmed secondary detail; `✓
identical` shown when current.

- `server.py`: `_git_version()` parser; `version` / `remote_version` /
  `commit_date` / `remote_commit_date` added to
  `/api/system/update/status`, the cached endpoint, and
  `_refresh_update_cache`.
- `static/index.html`: `refreshUpdateStatus()` leads with the version
  line; `checkClayruneUpdateAvailable()` toast uses it too. Degrades to
  SHAs if the new fields are absent.

**Needs a server restart** for the new JSON fields (frontend is
restart-safe until then). `docs/USER_GUIDE.md` "Update Clayrune" section
still pending an update to describe the new display.

## [2026-05-18p] — Remove the dead PyInstaller/Inno bundle path

Follow-up to `[2026-05-18o]`. No app/server code; no restart.

The `MissionControl-Windows.zip` / `MissionControlSetup.exe` distribution
(PyInstaller bundle wrapped by Inno Setup) **never worked** and the README
Quick Start was sending Windows users straight at it.

- **Deleted** (repo + disk): `installer.iss`, `build.bat`, `build.spec`,
  `pre_build_fix.py` — the entire dead bundle build chain. `dist/` was
  already untracked + gitignored (build output, left on disk).
- **README Quick Start (Windows)** now points at the working path: the
  `Clayrune-Installer.exe` from clayrune.io. Project-structure listing and
  the "prebuilt exe in Releases" line corrected.
- **`BUILD_INSTRUCTIONS.md` → local-only** (revises the keep-public call in
  `[2026-05-18o]`): it documented only the now-deleted bundle build, so it
  has no public value. `git rm --cached` + gitignored; still on disk.
- **Kept:** `app.py` — it's the from-source desktop entry point
  (`python app.py`, README Option A), not bundle-only.

Reversible via git history / `git add -f`.

## [2026-05-18o] — Repo hygiene: drop dead install scripts, unpublish internal design docs

No app/server code; no restart. Public-repo surface cleanup.

Two confusions removed:

1. **Dead root install path.** Root `install.bat` / `install.sh` (plus
   `start.bat` / `start.sh`, `rebuild_icon.ps1`) were the pre-rename
   "Mission Control" from-source wizard — superseded by the hosted
   `installer/` flow (one-click `.exe` / `clayrune.io`). Having
   `install.sh` at root *and* `installer/install.sh` was the exact
   funnel ambiguity we'd been fixing. **Deleted** (repo + disk). README
   "Option C" replaced with: from source = `python server.py` + in-app
   Settings; guided install = clayrune.io.

2. **Internal design/planning/scratch docs unpublished.** `git rm
   --cached` (kept on disk, added to `.gitignore`) for
   `IMPROVEMENT_PLAN_V2*.md`, `*_DotNet_Diagnosis*.md`, `RESUME_HERE.md`,
   `CLAUDE_KB.md`, and the internal `docs/` set (`MEMORY_SYSTEM*`,
   `SKILLS_CURATION_DESIGN`, `HIVEMIND_SPEC`, `SERVER_SPLIT_PLAN`,
   `CONDENSE_STRUCTURED_DESIGN`, `MAINTENANCE_*`, `HOSTING`,
   `web-push-handoff`, `design_system_extracted/`, and `remote-access/`
   except `07-licensing.md`). ~11k lines off the public repo; all files
   remain locally so `CLAUDE.md` / agent-session references still
   resolve. **Kept public:** `README`, `CHANGELOG`, `CLAUDE.md`,
   `LICENSE`, `BUILD_INSTRUCTIONS.md`, `docs/USER_GUIDE.md` (Ask Claydo
   source), `docs/remote-access/07-licensing.md` (README-linked),
   `installer/*`, shipped `data/skills/builtin/*`.

Reversible: history retains everything; `git add -f <path>` re-publishes
any single doc. Not touched: `server.py` + `static/index.html` (carry
unrelated pre-existing WIP + the held-back version-number feature).

## [2026-05-18n] — Thin Windows .exe installer (replaces the .bat double-click path)

Installer-only change (`installer/`); no server or app code, no restart.

Symptom: the Windows "easy" path was *download `Clayrune-Setup.bat`,
double-click it*. A downloaded `.bat` triggers an even harsher
SmartScreen/AV reaction than an unsigned exe and reads as untrustworthy to
non-technical users — the install funnel's weakest link.

Change: ship `installer/Clayrune-Installer.exe` — a **thin native console
launcher** that does no install work itself. It discloses what will
happen, then hands off to the canonical `install.ps1` fetched fresh from
GitHub raw (cache-busted), with the same built-in `claude /login` retry
loop the `.bat` had. A faithful port of `Clayrune-Setup.bat`; the install
logic stays in exactly one place and can never go stale inside a shipped
binary.

Build posture (consistent with the no-code-signing / no-build-pipeline
ethos): compiled by the .NET Framework `csc.exe` already present on every
Windows 10/11 box — no SDK, no third-party module, no cert spend. Still
unsigned, so SmartScreen shows a normal "More info → Run anyway" *app*
prompt once (a milder, more familiar dialog than the script one). Source
(`installer/win-exe/ClayruneInstaller.cs`) is linked from the landing-page
download note so the binary is auditable.

- `installer/win-exe/ClayruneInstaller.cs` — launcher source.
- `installer/win-exe/build.ps1` — `csc.exe` build → `installer/Clayrune-Installer.exe` (commit the rebuilt binary alongside source).
- `installer/index.html` — Windows download now points at the `.exe`.
- `installer/README.md` — files + hosting tables updated; `Clayrune-Setup.bat` retained as a plain-text fallback for binary-averse users.

Validated end-to-end offline (banner/UTF-8, disclosure→handoff→exit-code→
L/R/Q menu, graceful quit on stdin-EOF — the one bug found and fixed in
review).

Rollback: revert the `installer/index.html` link to `/Clayrune-Setup.bat`
(the `.bat` and its hosting are untouched).

> **Not in this commit:** the Settings "Update Clayrune" / dashboard-toast
> human-version display (`vX.Y.Z build N` installed-vs-latest) is complete
> and validated but lives in `server.py` + `static/index.html`, which carry
> unrelated pre-existing WIP. Held back per the "stage only what belongs"
> rule; ships separately once that tree is clean. Needs a server restart
> and a `docs/USER_GUIDE.md` "Update Clayrune" update when it lands.

## [2026-05-18m] — Alive-between-turns agent is 'working', not 'idle'

Frontend-only (`static/index.html`, `friendlyStatus()`); no server change,
no restart — page reload only. Follow-up to `[2026-05-18l]`.

Symptom: Mission Control's tile showed "● IDLE" *next to* an "AGENT
RUNNING" badge, and its modal showed "IDLE" beside a live Stop button +
ticking token counter (15m · 9.2k) — a self-contradiction. Desktop also
flickered to IDLE whenever the agent paused between turns, while the
mobile chat ring (caught mid-turn) looked correct.

Root cause: `friendlyStatus()` mapped `c === 'idle-agent'` → `'idle'`.
`idle-agent` is a Mode-B agent **session that is alive but momentarily
between turns** (server `live_agent.state === 'idle'`) — not a dormant
project. Every other live affordance (Stop button, token counter, "AGENT
RUNNING" badge, mobile green ring) correctly treats it as active; only the
status pill disagreed, because the persistent Mode-B process is `idle`
between turns far more often than it is mid-`running`.

Fix: `c === 'idle-agent'` → `'working'`. An attached, resumable session is
working; only the genuine no-live-session case (the final `return 'idle'`)
is idle. Stabilises the desktop pill (no more between-turns flicker),
removes the contradictory double-badge, and makes desktop match the
mobile behaviour the user confirmed correct — all at the single resolver,
so tile / modal / mobile row / list row / filter counts move together.

## [2026-05-18l] — Modal header status: route through the single resolver

Frontend-only (`static/index.html`, `modalContentHTML()`); no server
change, no restart — page reload only. Follow-up to `[2026-05-18k]`.

Symptom: same project, same moment — tile showed "● IDLE" (correct) while
its open modal header showed "IN PROGRESS". Root cause: the modal header
status pill was driven by **raw `p.status`** (`vl('status_' + p.status)`,
class `status-${p.status}`) — a parallel, un-consolidated code path that
never went through `friendlyStatus()`. So `status:active` → "IN PROGRESS"
regardless of live-agent state: the same lifecycle-vs-live conflation
fixed in `[2026-05-18k]`, surviving in the modal.

Fix: the modal header pill now uses the single consolidated resolver
(`friendlyStatus(p)` + `FRIENDLY_TO_VOICE` + `friendly-${fs}`), identical
to the tile / mobile row / list row. The now-unused `sc` lifecycle-class
local was removed from `modalContentHTML`. Design principle codified in a
comment: opening a modal is a VIEW action — it must never change the
status badge; every surface shows one shared truth.

## [2026-05-18k] — Stop conflating lifecycle-'active' with live 'working'

Frontend-only (`static/index.html`, `friendlyStatus()`); no server change,
no restart — page reload only. Follow-up to `[2026-05-18j]`.

Symptom: every project tile showed a permanent "● IN PROGRESS / In
progress: the current task." badge, regardless of whether any agent was
running. Root cause (survived the `[2026-05-18j]` consolidation): the
`switch (p.status)` in `friendlyStatus()` had `case 'active': return
'working'`. `p.status === 'active'` is a project-**lifecycle** state ("in
play", not parked/completed) — almost every project sits there
permanently — so the badge was effectively hardcoded to "working" and
never reflected real activity. This is the "inefficient badge that's
almost never updated" the user flagged.

Fix: removed the `case 'active'` arm. Genuine activity is already resolved
*above* the switch via the server-authoritative `live_agent`
(`c === 'running' | 'plan-approval' | 'question'` → working/asking), so a
truly-running 'active' project still shows "working". A no-agent 'active'
project now falls through to the live-evidence tail: `'done'` if there's
completed history, else `'idle'` ("resting, ready") — truthful. Other
lifecycle states (waiting/blocked/completed/parked) keep their mappings.
Fixes the pill, `friendlySummary` line, mobile chat row, list-view row,
and filter-pill counts in one place (single resolver).

## [2026-05-18j] — Full status-badge consolidation (single server-authoritative resolver)

Completes `[2026-05-18i]`. That fix made `friendlyStatus()`/`friendlySummary()`
prefer the server-authoritative `p.live_agent`, but left `computeLiveStatus()`
— the resolver every *other* surface uses (desktop tile tech-line, project
modal current-task, the "Agent running" badge, the "Needs you" attention
list) — still deriving the live-agent state from the lazily-refreshed client
`agentHistory`. Result: the same stale-`agentHistory` smell that produced the
false "Error" rows persisted on those surfaces, and each consumer had to learn
about `live_agent` separately (per-consumer divergence). Full consolidation
collapses this to **one server-authoritative resolver** that every surface
already calls.

- `server.py`: `_project_live_agent()` now also returns `reason`
  (`'plan'` / `'question'` / `None`) so a **closed** project's asking
  sub-state is labelled correctly without this client's `agentStatusCache`
  (which is only fresh for projects whose modal this client has open — the
  exact staleness the helper exists to defeat).
- `static/index.html`:
  - `computeLiveStatus()` is now server-first: when `p.live_agent` is
    present it wins (working / asking+reason / idle) at the single resolver;
    `agentHistory` is demoted to a supplementary detail source (richer
    client task string) and the sole source of completed/error *history*,
    which the server's live map (running/idle only) doesn't retain. The
    error/completed fallback is reached only when there is no server signal,
    so a stale errored session can no longer mask a live agent **at the
    source** — not patched per-consumer downstream.
  - `friendlyStatus()`: removed the bespoke `p.live_agent` short-circuit and
    the `c === 'error' && !la` guard — both subsumed by the resolver. One
    code path now.
  - `hasRunningAgent()` (drives the tile "Agent running" badge): server-aware,
    so the badge is correct for closed projects, not just modal-open ones.
  - `_buildAttentionList()`: plan-vs-question icon/text now read from the
    resolver's `currentTaskClass` (server-fed) instead of the stale
    per-session `agentStatusCache`.

**Requires a server restart** to activate the `reason` field; frontend is
regression-free pre-restart (`p.live_agent` undefined → every path degrades
to exactly the prior `agentHistory` behavior). No new polling — rides the
existing `/api/projects` cycle.

## [2026-05-18i] — Server-authoritative live-agent state (fixes stale "Error" rows)

Fixes the WhatsApp chat list showing a project as "Error on …" with no live
"working" presence while an agent was in fact actively running on it (e.g.
Mission Control during its own UI work). **Requires a server restart** to
activate (backend change); frontend is regression-free pre-restart
(`p.live_agent` undefined → `friendlyStatus()` falls back to prior behavior).

Root cause (pre-existing, not a chat-list bug — the prominent per-row status
just exposed it): the client's `agentHistory` is only refreshed by
`fetchAgentStatus()`, which after cold-start runs **only for projects whose
modal this client has open** (+ focus-resync of open modals). There is no
periodic refresh of live agent state for the closed projects shown in the
list. So a closed project's row froze at cold-start state; `computeLiveStatus()`
found no running session and fell through to a **stale errored session**,
and `friendlyStatus()` checked that client `error` class *before* the
server-authoritative `p.status` — so a months-old errored session overrode
`status: active`.

Fix A (proper, server-authoritative — benefits desktop tiles too, not just
the chat list; no new polling, rides the existing `/api/projects` cycle):
- `server.py`: `_project_live_agent(pid)` derives `{state, task}` from the
  in-memory `agent_sessions` map (the source of truth, fresh for all
  projects) — priority asking > working > idle; housekeeping/incognito
  excluded. Wired into `/api/projects` as `p['live_agent']`.
- `static/index.html`: `friendlyStatus()` trusts `p.live_agent`
  (working/asking) over `computeLiveStatus()`, and a stale client `error`
  class no longer returns `stuck` when the server reports a live agent
  (`c === 'error' && !la`). `friendlySummary()` falls back to
  `p.live_agent.task` for the subtitle on closed projects.

## [2026-05-18h] — Mobile chat list: horizontal overflow + Android back

Two on-device fixes to `[2026-05-18g]` (frontend-only, `static/index.html`).

- **Horizontal overflow** (timestamp shoved off-screen, needed sideways
  scroll): root cause was `.projects-col` staying `display: grid` on mobile —
  a lone grid item has default `min-width: auto`, so a long unbroken subtitle
  blew the track past the viewport. The original `:has(.mc-chat-list)` rule
  only touched padding and isn't reliable in Android WebView anyway. Fix:
  `renderProjects()` now toggles an explicit `.mc-chat-mode` class on
  `#projects-col`; CSS overrides it to `display: block` + `overflow-x: hidden`,
  and `min-width: 0` was added down the `.cr-top`/`.cr-bot` flex chain so the
  name/subtitle ellipsis actually clips.
- **Android hardware back** now closes an open project modal and returns to
  the chat list instead of exiting the app. Pure History API — no native
  APK change (Capacitor's default back handler calls `history.back()`, which
  fires `popstate`). One sentinel `pushState` per modal stack on mobile
  (`mcPushModalHistory()` after `openModals.set`); `popstate` closes all open
  modals; UI-close paths (X / Esc / Home) consume the sentinel via
  `history.back()` in `closeModalById` so back-press count stays correct (the
  popstate guard makes that a no-op on the back path — no double-close).
  Desktop unaffected (gated on `isMobileChatList()`).

## [2026-05-18g] — Mobile: WhatsApp-style project chat list

Frontend-only (`static/index.html`), scoped entirely to the existing
`@media (max-width: 960px)` block + the main app script. Desktop is byte-for-byte
unchanged (the chat list is an early-return branch in `renderProjects()` gated on
`isMobileChatList()`).

On mobile the card grid is replaced by a contact-list of projects:
**avatar · name · live-status subtitle · time-ago · unread badge**, pinned
section (`asking` → `stuck`) on top, everything else by `last_updated` recency.

- **Avatar**: `p.emoji` if the project has one set (the existing modal emoji),
  else two-letter initials from the project name (`projectInitials()`). Avatar
  carries a status-colored ring; `friendly-working` adds a CSS pulse ring as
  live "agent working" presence (no SSE — pure CSS, always live).
- **Unread model** (the only real new state): `projectLastSeen` map in
  `localStorage['mc_proj_seen']`; `markProjectSeen(pid)` stamps on
  `openProjectModal()` (open = read, WhatsApp semantics). `unreadCount(p)`
  counts **actionable** events newer than last-seen: (a) standing
  `friendlyStatus==='asking'` counted once (keyed to `p.last_updated` onset so
  it doesn't inflate while away), plus (b) autonomous-trigger
  (`triggerType !== 'manual'`, hivemind workers excluded) sessions that
  completed/idled/errored. Interactive turns the user drove are not counted.
  Derived on every poll-`render()` — deliberately NOT SSE-incremented, because
  closed projects have no live connection (Chromium 6-slot cap closes SSE on
  `turn_complete`), so an SSE counter would silently miss them.
- **Filter**: new "Unread" chip in `renderMobileFilterPills()` + `unread`
  branch in `filterProjects()`.
- **No server changes.** `triggerType`/`triggerId` already on `agentHistory`
  rows; `p.last_updated` already an ISO8601 sortable string on the payload.

Rollback: revert the `[2026-05-18g]` hunks, or set the mobile media query
`max-width: 960px` → `0` to fall back to the grid (existing rollback lever).
Store key `mc_proj_seen` is client-local and self-clearing.

## [2026-05-18f] — Skills Curation design + `mc-distill` skill (Step 1 shipped)

Design-and-skill entry (no backend code). Memory System open item #5 was
"deferred, depends on Steps 6/7, not designed yet." With Step 6 live and
Step 7 deliberately deferred, the principles locked in that item have
been turned into a full design and the first cheapest-possible experiment
has shipped as a built-in skill.

**Design:** `docs/SKILLS_CURATION_DESIGN.md` (388 lines, parallel
architecture to `MEMORY_SYSTEM.md`). Captures the three trigger paths
(manual `/distill`, conversational push, silent Distiller), the three
per-project modes (`off` / `proposed` / `auto`), the explicit reuse map
against Scribe infrastructure (`_scribe_render_transcript`, `_scribe_call`,
`_atomic_write_text`, per-project leaf locks, bounded semaphore,
telemetry shape, audit infra — backend Distiller estimated at ~600–900
lines vs. ~2000+ without the reuse), load-bearing rules, and seven open
items including required committee review before any backend code lands.

**Step 1 shipped:** `data/skills/builtin/mc-distill/SKILL.md` (158 lines).
Auto-installs on next MC startup via `_install_builtin_skills()`. Handles
both explicit user invocation (`/distill`, "propose a skill", "do we have
a pattern here") AND proactive agent-initiated proposals at natural
breakpoints. Hard rules for proactive triggering: recurrence ≥ 2 observed
in-session, natural breakpoint reached (end of task / after commit /
wrap-up; never mid-task or mid-debug), specificity (one-line name +
concrete observations), max one proactive push per session. Inline
format: `[Yes / Later / No]`. Writes only to
`data/skills/_proposed/<sid>/SKILL.md` (or `UPDATE.md` for patches);
never to `~/.claude/skills/` or any `<project>/.claude/skills/` directly.

**Key resolved decisions during design:**
- **No `production` flag.** User chooses `distiller_mode` per project;
  the system imposes no rules about which modes are allowed where. Mode
  selector UI shows a warning on `auto` selection; otherwise hands-off.
  Treats the user as competent to make their own safety calls.
- **Scope is authored skills only.** No "learned behavior" drift in the
  curated MEMORY.md region — blurs curated/managed boundary, hard to
  roll back.
- **Auto-authored skills are project-local only.** Never global. Bad
  auto-skill is annoying, not dangerous.
- **Conversational push and silent Distiller are complementary, not
  alternatives.** Push catches obvious in-session patterns; Distiller
  catches cross-session recurrence the in-session agent can't observe.
  `No` to a conversational push writes a suppression marker so the
  silent Distiller doesn't re-propose the same pattern that session.
- **Step 7 is NOT a hard prerequisite** for the dispatch skill hint —
  v1 can ship today using the existing keyword-scoring
  `/api/skills/search` endpoint that `mc-skill-broker` already uses;
  Step 7 (if/when it ships) upgrades the scoring backend in place.

**Files touched (all uncommitted at session end):**
- NEW: `data/skills/builtin/mc-distill/SKILL.md` (158 lines)
- NEW: `docs/SKILLS_CURATION_DESIGN.md` (388 lines)
- UPDATED: `docs/MEMORY_SYSTEM.md` (open item #5 rewritten — design
  drafted, Step 1 shipped, backend pending, committee-review-required)
- UPDATED: `CLAUDE.md` (new "Skills Curation — design + Step 1 shipped"
  section, 73 lines appended)

**No app code touched. No restart needed for this entry itself.** The
new built-in skill takes effect on the next MC restart (when
`_install_builtin_skills()` installs `mc-distill` into the global skills
dir; user edits would then be preserved across future updates per the
checksum-based mechanism documented in CHANGELOG `[2026-05-10]`).

**Next steps (decision points):**
- Committee review against `docs/SKILLS_CURATION_DESIGN.md` before any
  backend code (Distiller, telemetry, `_proposed/` CRUD, dispatch hint).
- Live test: restart MC, run a real session, see whether the proactive
  trigger fires at the right bar.
- Build order steps 2–7 follow opportunistically after committee review.

## [2026-05-18e] — Leg C structured condense executor (default-OFF)

Root-cause fix for the memory-condense fragility. The pain (the original
`--max-turns 5` ERROR bug, the 11-step prompt, the entire
`_condense_integrity_check` ok/heal/restore guard, spurious `condense_*` ERROR
sessions) all traced to ONE choice: `_dispatch_condense` shells out a free
`claude -p` agent holding the Write tool to rewrite a load-bearing file in
place. The necessary judgment is small and structured — per managed entry,
`keep | demote | fold` — and needs a model's judgment, not an autonomous
file-surgery agent.

**New executor (`condense_mode='structured'`):** `_condense_plan` assembles a
bounded read-only input (curated headings, `- [` entries each with a
`_sha8` id, a 4 KB archive-tail for dedupe context, the line budget) and makes
ONE non-agentic call (reuses `_scribe_call`: `--max-turns 1`, no tools, stdin,
180 s) returning strict JSON. `_validate_condense_payload` is a **pre-write**
gate (schema + every invariant; a reject leaves MEMORY.md untouched — no
pre-image, no restore, no heuristic revert). `_condense_apply` applies the
plan **rebased** under the same leaf lock the completion scribe + Step-6 use:
decisions keyed by `_sha8(entry)`, any decision whose entry vanished meanwhile
(concurrent Step-6/teardown) is silently skipped, unmentioned entries default
to keep, `clayrune:wm:` watermarks pass through byte-identical, the model
never touches the filesystem and never sees a watermark. `fold` inserts one
pointer line under a named curated heading **and** archives the raw entry
(fact preserved); a heading that vanished since plan-time downgrades to demote
(never loses the fact). Mechanical line-floor remains the backstop.

The model never writes files → the turn budget, corruption surface,
heal/restore code, and ERROR-session noise are all designed out (the legacy
`agent` path + its committed-pending stopgap remain the safe default until
this is telemetry-validated).

**Line-keyed trigger (closes the one caveat).** `_should_condense` now
branches on `condense_mode`: structured fires on the auto-loaded MEMORY.md
**line count vs. `index_line_budget`** (the thing structured actually
controls), not combined bytes. This removes the recurring-no-op gap where a
large `CLAUDE.md` (which structured v1 deliberately doesn't touch) would keep
a byte trigger permanently hot, and makes the structured trigger + target
agree in units (resolves design Open Question #5 for this path). The legacy
`agent` path keeps its combined-byte trigger unchanged.

**Settings:** new "Condense Executor" select under Memory & Condensation
(Agent / Structured). **Telemetry:** `condense_structured_ok`,
`condense_rejected:<reason>`, `condense_entries_{kept,demoted,folded}`,
`condense_decisions_skipped_rebased`, `condense_fold_downgraded`, `model_ms`
in condense-status.

**Design:** `docs/CONDENSE_STRUCTURED_DESIGN.md` (status → v1 implemented).
`docs/MEMORY_SYSTEM.md` component + config tables trued up. **Tests:**
`tests/test_condense_structured.py` (9 — parse, every validate-reject branch,
keep/demote/fold + wm-preserved, rebase-skips-vanished, fold-downgrade
(heading-gone AND heading-ambiguous), archive-append-only,
structured-trigger-ignores-huge-CLAUDE.md, agent-trigger-unchanged); full
suite 41 green.

**Committee review (4 seats) — RATIFY-WITH-CONDITIONS, no blockers, no
data-loss path.** Conditions fixed here: bounded telemetry key (no raw
exception text in `condense_rejected:`); fence-aware curated-heading
collection + unique-match-or-demote in `_condense_apply` (never misplace a
pointer, never lose the fact); duplicate-id intentional-collapse documented;
new `curated_lines` status gauge for soak; trigger read-guard widened;
CLAUDE.md "one writer" rule + `_commit_managed_entry` docstring + design-doc
atomicity claim trued up (it is two atomic writes archive→managed = transient
duplication never loss, identical to `_commit_managed_entry`). Conditions
DEFERRED to the default-flip gate (ship default-off so soak surfaces them):
trigger hysteresis and curated-index monotonic-drift watch — see
`docs/CONDENSE_STRUCTURED_DESIGN.md` "Committee review" §6–7.

**Config (defaults + `_CONFIG_EDITABLE_KEYS`):** `condense_mode` (`agent`).
**Rollback:** set `condense_mode=agent` (Settings select, no restart once both
paths resident). Server.py changed → **restart required** for the new path to
be server-side selectable (frontend picks up the selector on browser reload).

## [2026-05-18d] — Long Mode-B session restart advisory

Closes the human-driven half of the within-session self-recall gap (docs/
MEMORY_SYSTEM.md Open item #6): a long persistent Mode-B process can compact
away its *own* early-session context, and (unlike a fresh process) it does
not auto-reload MEMORY.md mid-life. Step 6 has captured that learning
durably, so the fix is just "restart it" — a fresh process reloads the
accumulated memory + read-floor at near-zero loss.

**New:** `_long_session_advisory(s)` — a soft, server-computed signal:
Mode-B + alive (`running`/`idle`) + not housekeeping/incognito +
`num_turns ≥ long_session_advisory_turns`. Exposed as `long_session_advisory`
in `/api/project/<id>/agent/status`. Frontend shows a dismissible,
**deduped-per-session** toast recommending a restart (cleared when the
session ends). Advisory only — nothing auto-restarts.

**Design notes:** keyed on **turns**, not transcript bytes — it deliberately
fires far earlier than the existing 5 MB `_session_too_large` hard
auto-fresh (that's a resume-perf cap; this tracks context-window fill /
amnesia). It does NOT fork `_session_too_large` — it's a sibling helper.
Human-in-loop only; autonomous long runs can't act on a toast, so the
durable fix for those remains the deferred per-turn read-floor refresh
(Open item #6, still tech debt).

**Config (defaults + `_CONFIG_EDITABLE_KEYS`):**
`long_session_advisory_enabled` (true), `long_session_advisory_turns` (25).
**Rollback:** set `long_session_advisory_enabled=false`. Server.py changed →
**restart required** for the server-side signal to activate (frontend picks
up on browser reload).

## [2026-05-18c] — Memory architecture: memsearch retired, layers clarified

Operational/architecture decision (no app code change). **memsearch
retired**: verified non-functional here (no plugin payload in
`~/.claude/plugins/`, no `~/.memsearch/`, no Milvus index, one stale
2026-05-15 capture file — now deleted; no registry entry existed in
`~/.claude.json` to remove). It was installed thinking it could be the
headless-agent (CR) memory layer, but headless MC-dispatched agents can't
load Claude Code plugins at all — which is the founding reason the Scribe
system exists — and for the only role it *could* serve (direct
operator↔assistant recall) engram already covers it. **engram kept** —
verified active/healthy (`mem_doctor`), it's the conflict-aware long-term
operator-collaboration layer (and the one that corrects Scribe mistakes).
`docs/MEMORY_SYSTEM.md` gains a **"Memory layers & audiences"** section so
the CR-vs-direct-channel distinction is explicit and nobody mis-consolidates.

Also recorded: a **Scribe-poisoning incident** — a CR session resolved a
transient doc contradiction (original `[2026-05-18]` changelog title said
"default-OFF" vs the trued-up state) by guessing instead of checking
`/api/config`; the Scribe immortalized the wrong conclusion into MEMORY.md
`_(live)_` entries + a live watermark, and later sessions re-asserted it.
Cleaned (false entries + watermark removed via the Leg-0 helpers, curated
region byte-preserved, one authoritative correction added). Durable lesson
captured as the `feedback-verify-volatile-state` memory: verify volatile
operational state at the live source; never let memory enshrine an
unverified claim — `condense` compresses, it does not fact-check. engram
side-note: `mem_doctor` flags 7 cloud-sync mutations missing `title`
(local recall unaffected; cloud replication of those is blocked) — parked
for an explicit `engram cloud upgrade doctor` decision.

## [2026-05-18b] — Step 6 live-enabled + validated end-to-end

Follow-up to the entry below (the original recorded Step 6 as committed
default-off with behavioral validation "not yet run" — true at that commit;
this records what happened after). Restarted onto `9683996`, confirmed it
booted **OFF** (default-off inertness held), then flipped
`scribe_checkpoint_enabled=true` / `scribe_checkpoint_kb=8` and drove a real
2-turn Mode-B session. Result: **ALL PASS, first try, no iteration** —
`checkpoint_extracted` 0→1→2; the `<!-- clayrune:wm:<sid> … -->` marker
appeared with `byte_offset` advancing across turns (49316→58431);
`running_summary` changed between turns (the reduce ran); append-only
`_(live)_` entries written (real summaries); on stop the marker was removed
(clean teardown) + terminal entry written + agent_log `scribed=True`.

**Step 6 is now ENABLED on this deployment** (`scribe_checkpoint_enabled=true`,
`scribe_checkpoint_kb=8`) per Ron's call — it ships default-off in code;
revert any time via the Settings toggle (`scribe_checkpoint_enabled=false`,
no restart). Soak signal to watch: the `checkpoint_*` counters in
`/scribe-stats` under real parallel/long-session load. Docs trued up
(CLAUDE.md, `docs/MEMORY_SYSTEM.md`, `docs/MEMORY_SYSTEM_SPEC.md` header,
USER_GUIDE Memory & Rules). Step 7 (bge-m3 semantic search) remains
deliberately deferred — see `decision_step7_semantic_search_deferral` memory.

## [2026-05-18] — Step 6: mid-session note-taker (implemented, default-OFF)

Mode B (the global `use_streaming_agent` default) keeps one persistent
process alive across turns, so the Leg A Scribe only fired at session
*teardown* — a long/idle/killed Mode-B session captured nothing until
stopped. Step 6 adds per-turn capture. **Implemented, offline-verified,
ships DEFAULT-OFF** (`scribe_checkpoint_enabled=false`,
`scribe_checkpoint_kb=0`); fully inert until both are set. Committee-hardened
design + rationale in `docs/MEMORY_SYSTEM_SPEC.md` §3.A.MID; at-a-glance map
in the new `docs/MEMORY_SYSTEM.md`. **Server restart required (server.py).**

**Trigger.** A fast, model-free gate (`_maybe_checkpoint`) clones the
existing `_auto_snapshot_notes_on_turn` precedent at the Mode-B `result`→idle
boundary: config flags, incognito/housekeeping, real-boundary (not
AskUserQuestion/plan-approval/dead-proc), KB-delta debounce, one-in-flight
per session — then spawns a daemon worker.

**Worker.** `_scribe_render_delta(path, byte_offset)` renders only the new
transcript bytes (leading+trailing partial-line rules; EOF-reset on
rotation). The delta is summarized and folded into a cumulative
`running_summary` via `_SCRIBE_CHECKPOINT_REDUCE`; a self-contained
`_(live)_` entry is appended. Append-only by design (no rewrite-in-place) —
Leg C dedups a session's checkpoint lines. Thin/refused deltas advance the
offset with no entry. Per-project `BoundedSemaphore` (cap 2) + coalescing
bound fan-out without re-serializing cheap model calls.

**Watermark folded into MEMORY.md (D6).** A single `<!-- clayrune:wm:<sid>
… -->` marker per live session inside the managed region carries
`{transcript_path, byte_offset, slice_hash, running_summary}` — one atomic
write per checkpoint eliminates the watermark/file atomicity gap and the
DATA_DIR-sidecar 500 class. Leg-0 contract: `_mem_split_full` buckets wm
markers (back-compat `_mem_split` 2-tuple unchanged), `_mem_compose(...,
wm_markers)` re-emits them (falsy ⇒ byte-identical to pre-Step-6), the
mechanical floor never relocates them, and the Leg C condense prompt is told
to preserve `<!-- clayrune:wm:` lines verbatim.

**One shared writer.** `_commit_managed_entry` is now the single leaf-locked
(`_get_mem_write_lock`, per-project) + atomic (`_atomic_write_text`,
temp+os.replace) MEMORY.md mutator used by the completion scribe, the
checkpoint worker, AND reconcile-finalize — they cannot diverge. The scribe
model call and condense dispatch stay OUTSIDE the leaf lock; strict
outer(manager RLock)→inner(leaf) ordering, no deadlock. This also hardens
the already-shipped completion path (parallel same-project teardowns no
longer serialize across a ≤180s scribe call).

**Teardown / Fix-B coordination.** A clean terminal write drops the
session's wm marker in the same atomic write. A marker still present at boot
⇒ killed mid-flight: `_reconcile_unscribed_sessions` finalizes from the
marker's `running_summary` (NO model call, `_(reconciled)_` tag, wm-remove)
instead of a full re-scribe; absent ⇒ existing behavior. Baseline /
`scribed` / `_has_running_agent` invariants unchanged.

**Config (defaults + `_CONFIG_EDITABLE_KEYS`):** `scribe_checkpoint_enabled`
(false), `scribe_checkpoint_kb` (0; ≈8 recommended once enabled).
Telemetry: `checkpoint_extracted` / `checkpoint_coalesced` /
`checkpoint_skipped:<reason>` / `checkpoint_offset_reset` /
`checkpoint_finalized` via the existing `/scribe-stats`.

**Verification.** spike-2 passed (every `.jsonl` line is a standalone JSON
object → partial-line cut is sound; 6 real transcripts incl. 3.9 MB).
`py_compile` clean throughout; Leg-0 regression intact after the
`_commit_managed_entry` refactor; wm round-trip / floor-safety / trigger &
branch wiring / default-off inertness / bounded-semaphore integrity all
pass. **Behavioral (live-enabled) validation is the deliberate user-gated
step per the SPEC enablement criterion — not yet run.**

**Rollback.** It's default-off — nothing changes until
`scribe_checkpoint_enabled=true`. Setting it back to `false` (or
`scribe_checkpoint_kb=0`) fully disables the path. The Leg-0 wm support is
forward-safe (falsy ⇒ byte-identical output); the leaf-lock/atomic-write
refactor is independent of the flag and beneficial regardless.

## [2026-05-17b] — server.py split P1-1 Tier 1a: marketing_preview blueprint

First extraction of the `server.py` blueprint split
(`IMPROVEMENT_PLAN_V2.md` P1-1, sequenced per `docs/SERVER_SPLIT_PLAN.md`
Tier 1). No behavior change — code moved verbatim.

**Done.** The `/marketing/` + `/marketing/<path:filename>` preview route
moved out of `server.py` into a new `marketing_preview.py` Flask
Blueprint. `server.py` imports it and calls `_marketing_preview.register(app)`
where the route used to live. `Path(__file__).parent` resolves to the
same dir (module sits in repo root next to server.py; co-bundled at the
same _MEIPASS root when frozen), so the `marketing/` folder resolves
identically in dev and frozen builds.

**Files changed.** `marketing_preview.py` (new), `server.py` (import +
register call, route block removed), `CHANGELOG.md`.

**Verification.** `pytest -q` 16/16 green; `server.app.url_map`
confirmed to still expose both `/marketing/` rules via the blueprint.

**Rollback.** Revert this commit (re-inlines the route). No persisted
state or schema involved. Anchor: tag `plan-v2-sprint4-base` (= the
commit before Tier 1 started); off-repo copy in `_plan_v2_backups/`.

## [2026-05-17] — Memory system: server-side Scribe + self-learning pipeline

Headless project agents can't use CC memory plugins, and the old write path
appended *the last stdout line* (often a sign-off sentence) as the only
cross-session memory — so agents didn't meaningfully build on prior work.
This replaces that with a server-side pipeline. Full design + committee
review in `docs/MEMORY_SYSTEM_SPEC.md`. **Server restart required
(server.py changed).** Verified end-to-end on the live server.

**Leg 0 — format contract.** `MEMORY.md` now has a sentinel-delimited
*managed region* (`<!-- clayrune:managed:begin/end -->`, `## Session Log`)
below the human/condense-*curated* pointer index. Migration is lazy +
idempotent + additive: the curated region is byte-preserved, never touched
by machinery. Helpers `_mem_split/_mem_compose/_mem_migrate`.

**Leg A — the Scribe.** On session end MC reads the CLI's full-fidelity
on-disk `.jsonl` transcript (the only place tool results / thinking survive
— MC's in-memory `log_lines` keeps neither) and a cheap model (`scribe_model`,
default haiku) extracts one dense memory line, chunked map-reduce for huge
transcripts, base64/image stripped. Robustness guards: an activity-thin
transcript or a model refusal falls back to the stdout tail rather than
writing a hallucinated entry. Any failure falls back; completion never
breaks. Outcome telemetry at `GET /api/project/<id>/scribe-stats`. The old
`server.py` "update MEMORY.md yourself" agent instruction is removed (B8) —
the agent is told memory is maintained for it and not to hand-edit.

**Leg B — retrieval.** `GET /api/project/<id>/memory/search?q=&k=` ranked
grep over topic files + archive + the managed region (curated index excluded
— agents auto-load it). A deterministic top-k read-floor is injected into
fresh-dispatch system prompts (`_build_agent_context`, `RELEVANT MEMORY`),
plus a `mc-memory-search` built-in skill for on-demand pulls.

**Leg C — trim.** The condense agent prompt is rewritten: a **line budget**
(`index_line_budget`, not a KB target), curated-vs-managed aware, fold/
demote/keep by value not recency, never hard-delete except dupes/strict
supersede, and the archive is now **never deleted/truncated** (it is the
permanent searchable cold store Leg B depends on). A judgment-free,
lossless, line-keyed mechanical floor (`index_line_hard_floor`) relocates
the oldest managed entries to the archive — replacing the old byte-keyed
20 KB / keep-last-20 logic, which silently truncated past the ~200-line
harness load limit.

**Fix A** — the scribe fires for `error`/`stopped` sessions too (tagged
`_(error)_`/`_(stopped)_`), not only clean completion; it reads the
transcript, not stdout, so it needs no clean summary.

**Fix B** — startup reconciliation closes the hard-MC-kill gap (completion
never ran): `_reconcile_unscribed_sessions` baseline-stamps pre-existing
agent_log entries `scribed:true` **without** scribing history (cost guard:
verified 0 boot-scribes across 16 projects / 441 entries), then captures
only genuinely-missed terminal sessions, capped per project per boot. Shared
`_write_session_memory` is the single write path for completion + reconcile;
`scribed` marker prevents double-capture; dedicated `_scribe_lock` distinct
from condense.

**Bug found & fixed in the verify loop.** `_scribe_stat` wrote
`{pid}_scribe_stats.json` into `DATA_DIR` (= `data/projects/`, the
project-records dir). `load_projects()` only excluded `_agent_log.json`, so
the stats file parsed as a malformed project and 500'd **both
`/api/system/restart` and `/restart/status`** (via
`_get_active_restart_blockers`). Fixed at source: `load_projects` excludes
`('_agent_log.json', '_scribe_stats.json')`; `_get_active_restart_blockers`
hardened to skip entries without `id`. Regression-closure verified (restart
path stays 200 after the stats file is recreated). Rule: anything written
into `DATA_DIR` MUST be suffix-excluded in `load_projects`.

**Known limitation.** With `use_streaming_agent` (Mode B, the global
default) the persistent process doesn't exit at turn end, so the Scribe
fires at session *teardown* (stop/error/kill/reap), not per turn. Long idle
Mode B sessions capture nothing until torn down. Mid-session checkpointing
(SPEC §3.A.MID / "Step 6", default-off, not yet built) is the planned fix.

**Config (all in `_CONFIG_EDITABLE_KEYS`, editable via Settings):**
`scribe_enabled` (T), `scribe_model` (''→haiku), `scribe_reconcile_enabled`
(T), `scribe_reconcile_cap` (5), `read_floor_topk` (3),
`index_line_budget` (160), `index_line_hard_floor` (185),
`scribe_checkpoint_kb` (reserved, Step 6).

**Rollback.** Set `scribe_enabled=false` (no scribe; legacy stdout-tail
write resumes) and/or `scribe_reconcile_enabled=false` (no startup
reconcile). The Leg 0 format is forward-safe and idempotent; the mechanical
floor and condense changes are independent of the scribe flag. New surfaces
(`/memory/search`, `/scribe-stats`, the skill) are additive and inert when
unused.

## [2026-05-16] — Push policy: "waiting for me" + focus-suppression gate

Implements the notification policy Ron chose ("option 1 — notify me when
the agent is waiting for me; but stay silent if I already have that chat
open and focused"). `server.py` + `static/index.html`; server restart
required (server.py changed).

**turn-complete push is ON by default.** Previously `notify_turn_complete`
defaulted `False` on both the per-project gate (`_handle_push_signal`) and
the per-subscription gate (`_notify_push`), so the "agent finished, waiting
for you" buzz never fired unless explicitly opted in — the gap Ron hit
("no notification when you responded; I opened after waiting a while").
Both defaults flipped to `True`; a project can still explicitly opt out
via `notify_turn_complete=False`. Payload text changed `Turn complete`
→ `Waiting for you`. The agent-decided deep push (`kind='agent'`,
PushNotification tool) is unchanged — it stays "only when something
important happens".

**Focus-suppression gate (new).** A dashboard with a session's chat open
in a non-minimized modal, while the tab is `visibilityState==='visible'`
*and* `document.hasFocus()`, pings `POST /api/presence` every 15s with the
watched `[{project_id, session_id}]`. Server keeps an in-memory
`_presence_state` (lock-guarded, global — any device watching suppresses
all devices, since if Ron is at a screen his phone shouldn't buzz either).
`_handle_push_signal` calls `_is_being_watched()` before delivering
*either* kind and skips if a ping is fresher than `PRESENCE_FRESH_SEC`
(25s). The frontend stops pinging on blur/hide, so presence goes stale
and push resumes automatically — no explicit "I left" signal.

**Internal agents excluded (new guard).** `_handle_push_signal` now bails
when `agent_sessions[session_id]` has `housekeeping` or `incognito` set.
Without it, flipping the turn-complete default ON would have spammed a
push for every scribe / condense / hivemind worker+orchestrator `result`
(all set `housekeeping=True`).

New: `_presence_state`/`_presence_lock`/`PRESENCE_FRESH_SEC`,
`_presence_touch()`, `_is_being_watched()`, `POST /api/presence`,
frontend `_watchedSessions()` / `_pingPresence()`.

## [2026-05-15] — Activity feed redesign, focus-theft fix, AskUserQuestion status wiring, mobile missing-prompt reconciliation

A single-session bundle: one feature reshape + three correctness fixes.
All changes are `static/index.html` only — no `server.py` changes, so a
hard refresh picks everything up (no restart).

**Activity feed redesign — bucketed + time-aware (`static/index.html`)**
The feed was a flat, equal-weight reverse-chron list — nothing rose to
the top, so it carried no signal. `renderFeed()` now splits into two
buckets. **"Needs you"** is derived live from project state (not the
lagging `activity_log`): `friendlyStatus(p)` of `asking` or `stuck`,
with the reason resolved from plan-approval / question-pending / blocked
/ error. It renders with an accent rail and pins to the top, and the
collapsed feed tab carries an attention-count badge so urgency survives
a hidden feed. **"Recent"** collapses to one rolling line per project
(newest event + `+N earlier`) and is time-bucketed by the age of that
newest event: `Fresh · last hour` / `Today` / `This week`, with
progressive CSS opacity fade and a 7-day cutoff (the feed is a
"what's alive" surface; Agent Log remains the archive). New helpers:
`classifyFeedEvent` (msg-text → icon/kind), `_buildAttentionList`,
`_feedAgeBucket`, `_updateFeedAttentionBadge`.

**Focus-theft regression — fixed (`static/index.html`)**
Regression introduced in `e473323` (the Android-IME chat-input
preservation). `refreshModalById` detaches the focused
`agent-followup-${sid}` textarea and reattaches it across the
`innerHTML` wipe; removing a node from the DOM blurs it, and the
focus-restore block deliberately *skipped* the preserved input
(re-assigning `.value`/selection would desync the IME). Net effect:
any cross-modal `refreshModal()` — e.g. an SSE `turn_start` from a
different agent — silently blurred whatever textarea the user was
typing in. Fix: the restore block now re-focuses the preserved input
when it isn't already `document.activeElement`, **without** touching
`.selectionStart/.selectionEnd` (the reattached node still carries the
correct selection + IME compose buffer).

**AskUserQuestion status pipeline (`static/index.html`)**
`waiting_for_question` was fully tracked server-side and exposed on
`/agent/status`, but the frontend never propagated it, so an agent
blocking on `AskUserQuestion` showed as `working`/`idle` instead of
`asking` — tiles and the new Needs-you bucket couldn't surface it.
Wired the full chain: `fetchAgentStatus` now reads
`s.waiting_for_question` into `agentStatusCache[sid].waitingForQuestion`;
`computeLiveStatus` emits a new `currentTaskClass = 'question'`;
`friendlyStatus` maps `'question'` → `'asking'`. The flag is set on
the SSE `question` event (with a `refreshModal()` so tiles repaint)
and cleared on `submitQuestionAnswer`, `turn_start`, and the terminal
`turn_complete`/`status` handlers (alongside the existing
`waitingForPlanApproval` clears).

**Mobile missing-prompt — latent bug + silent reconciliation (`static/index.html`)**
Symptom: a follow-up sent from the mobile shell never appeared in the
chat even though the server received it and the agent replied
(confirmed against a live session's `log_lines`). Two fixes. (1)
Latent bug: `fetchAgentStatus` populated `agentOutputBuffers[sid]`
from server `log_lines` but never set `agentServerLines[sid]`, so a
later `connectAgentStream` used `since=0` and replayed every line on
top of the populated buffer — silent double-render. It now anchors
`agentServerLines[sid] = log_lines.length`. (2) New
`_reconcileAgentBuffer(projectId, sessionId)`: fetches `/agent/status`,
diffs server `log_lines.length` against `agentServerLines[sid]`, and
silently appends any missed tail entries through the normal
echo-dedup + `appendAgentLine` path. Per-session `_reconcileBusy`
lock + an in-loop race guard prevent double-apply if SSE catches up
mid-iteration. Triggered at the three moments a hole is most likely
to have just opened: `sendFollowup`'s POST resolution (+1.5 s), the
SSE watchdog reconnect (+1.5 s), and `visibilitychange → visible`
(fan-out over every visible modal's active session — covers mobile
backgrounding the tab and killing the EventSource without a close
event). No console/toast/flash — the recovered line appears as if
SSE delivered it.

**Rollback**: revert this commit. No persisted state or schema
changed; the feed/status changes are pure render-path, and the
reconciliation is additive (best-effort, silent on error).

## [2026-05-14b] — Modal snap layouts, tile-all button, pin/unpin, AskUserQuestion + mobile SSE fixes, Clayrune onboarding project

A single-session bundle of three usability issues + two larger features.

**AskUserQuestion render reliability (`server.py`, `static/index.html`)**
The question form was getting dropped on first ask if the SSE wasn't open, the
DOM wasn't ready, or the modal hadn't been built yet. Server now stamps each
`AskUserQuestion` with a `question_id` (uuid) and **keeps `pending_questions`
populated until the user actually answers** (cleared in `/agent/followup` +
`/agent/interrupt`). The SSE generator dedupes per-stream by `question_id` so
the 0.3 s poll doesn't spam. `/agent/status` now exposes `waiting_for_question`.
Client tracks rendered question_ids in `_renderedQuestionIds[sessionId]` and
skips re-rendering an already-shown form. Cleared on submit. `fetchAgentStatus`
also reconnects SSE for idle sessions that are either waiting on a question
or are the active tab in an open modal (still skips background idle sessions
to preserve Chromium's 6-slot per-origin cap).

**Mobile modal status stuck on IDLE (`static/index.html`)**
After a send on mobile, the modal would sit on IDLE because: (a) SSE wasn't
auto-reconnected for idle sessions on cold modal open, and (b) the post-POST
reconnect could miss `turn_start` if the server flipped through `running` →
`idle` before the new SSE opened. Fix: `sendFollowup` now eagerly opens SSE
**before** the POST, plus a `_sendInFlight[sessionId]` gate so the eager-open
SSE's stale `turn_complete` (reflecting the *prior* idle state) doesn't close
the connection on the client. Gate clears on `turn_start` or after 8 s timeout.
The `status` handler honors the gate too (except for user-initiated `stopped`).

**Android IME backspace requiring many taps (`static/index.html`)**
`refreshModalById`'s `innerHTML` wipe was destroying the focused chat
textarea's IME compose buffer, causing the next backspace to need several
presses (the IME thought the word was still in compose; the rebuilt DOM had
no such buffer). `refreshModalById` now detaches the focused
`agent-followup-${sid}` textarea before the wipe and reattaches it after —
same pattern already used for `agent-output`. The value/focus restoration
loop skips the preserved input so we don't overwrite its `.value` (which
would reset cursor + re-trigger the desync).

**Aero-Snap for modal windows (`static/index.html`)**
Drag a `.modal-window` toward a viewport edge → translucent accent preview
shows the target zone → release commits the snap. Dragging a snapped modal
more than 24 px tears it off back to its pre-snap geometry. Zones: full
(top edge), left-half, right-half, four corner quarters. Detection uses
viewport edges (cursor must reach the screen edge, not the workspace edge —
the natural crossing into the sidebar / header strip would otherwise kill
detection). Snap target uses the workspace rect with the sidebar pinned to
its collapsed width so hover-expand doesn't shift zones mid-drag. State
persists in `mc_modal_prefs.snap` + `mc_modal_prefs.preSnap` and per-instance
in the `mc_open_modals` snapshot, so reload restores the layout. Window
resize re-applies all current snaps debounced 100 ms. Mobile is full-screen
by CSS; the snap engine no-ops there.

**Header "Tile open modals" button (`static/index.html`)**
Small grid icon in the header (next to the system-status pill) opens a
popover with layout templates filtered to the current visible-modal count:
1 → maximize · 2 → side-by-side or top/bottom · 3 → three columns or
large-left+stack or stack+large-right · 4 → 2×2 quadrants · 5+ → "no
layout available." Thumbnails are numbered cells (1, 2, 3…) showing which
slot each modal will take — assignment is by zIndex descending (focused
modal → cell 1). Each cell calls the existing `applySnap`, so persistence,
the `is-snapped` class, and the resize-grip lockout all carry over. New
zone types added to `_zoneRect`: `top-half`, `bottom-half`,
`left-third`, `center-third`, `right-third`.

**Per-modal pin / unpin (`static/index.html`)**
New pin button in `.modal-window-controls` (between menu and minimize).
Unpinned collapses the middle data-sheet section: status pill row, path
row, summary, description, and the Current task / Next up grid. The
project name row at top, the window controls, **the tab bar, and the
active tab's content** all stay — handy when tiled modals only need a
title bar + the conversation visible. State persists in
`mc_modal_prefs.unpinned` + the `mc_open_modals` snapshot.

**Clayrune onboarding project replaces "Sample Project" (`server.py`)**
First-run walkthrough now seeds a real `clayrune` project at
`~/MissionControl/clayrune/`. Endpoint URL stays
`/api/walkthrough/sample-project` for compatibility; project ID is now
`clayrune`. Seeded files (only if absent — won't trample edits): a friendly
`README.md` and an `AGENT_RULES.md` that primes the dispatched agent as the
in-app help desk, with absolute paths to *this install's*
`docs/USER_GUIDE.md`, `CHANGELOG.md`, and source root (resolved via
`Path(__file__).parent`). `_build_agent_context` already reads
`AGENT_RULES.md`, so every session dispatched from Clayrune behaves as a
platform expert with no schema or dispatch-flow change. 11 backlog items
cover drag-snap, tile button, pin button, scheduler, hivemind, skills,
MCP, GitHub sync, compact mode, first-real-project, and the
tour-the-agent prompt.

**Deferred**: stale "running" status detection — the server's guardian
only fires after 600 s of stdout silence *and* CPU idle, so a wedged agent
shows "running" for up to 10 minutes. Will be its own session.

**Rollback**: revert this commit. The pre-existing `sample-project.json`
in older installs is untouched (the new endpoint creates `clayrune.json`
alongside if the user re-runs the walkthrough).

## [2026-05-14] — Native FCM push for the Clayrune Android APK shell

Web push hit a wall on Android Chrome: every notification carried a
"possible spam from clayrune.io" warning and click-through landed on the
generic dashboard, not the specific agent. With the native APK shell now
shipping (CF service-token bypass landed yesterday), the right path is
Firebase Cloud Messaging through Capacitor's `push-notifications` plugin —
no spam toast, proper deep-link routing, and the server can deliver to a
killed app via the OS push channel.

**Server (`server.py`):**
- `_push_send_fcm(sub, payload)` — lazy-inits `firebase_admin` from
  `data/firebase_admin.json` (gitignored), sends via
  `messaging.send(messaging.Message(token=…, notification=…, data=…))`.
  Hybrid `notification`+`data` payload: Android auto-renders in the tray
  when the app is backgrounded; the `data` block carries `project_id` /
  `session_id` / `url` so taps route deep. `AndroidConfig` adds
  `priority=high` + `ttl=300` + a per-project notification `tag` so a
  chatty agent doesn't carpet-bomb the tray.
- `_notify_push()` now dispatches per-subscription. `sub.type == 'fcm'`
  → FCM path (handles `NotFoundError` / `InvalidArgumentError` as
  "drop this token"); everything else stays on the existing pywebpush
  path. Lock + persistence + the auto-removal-of-stale-subs accounting
  is shared, so a mixed fleet of browser PWAs and native APKs all
  funnel through one delivery loop.
- New endpoint **`POST /api/push/register-fcm`** — accepts
  `{token, label?, project_filter?, notify_agent_push?,
  notify_turn_complete?}`. Storage key prefers the CF Access nonce; if
  absent, falls back to `fcm:<sha1(token)[:16]>` so the row survives a
  CF re-OTP. Dedups by token across keys (same logic as web
  endpoint-based dedup).
- **`POST /api/push/unsubscribe`** extended with a `token` field — same
  pattern as the existing `endpoint` field but matches FCM rows.
- **`GET /api/push/subscriptions`** now surfaces `type` (`'web'` or
  `'fcm'`) per row so the Settings UI can label them distinctly.
- `requirements.txt` adds `firebase-admin>=6.5.0`. Import is lazy
  inside `_fcm_initialize()` — if the SDK isn't installed or the
  service-account JSON is missing, FCM delivery silently no-ops and
  the web push path still works.

**Mobile (`<mobile repo>`, separate repo):**
- `android/app/google-services.json` (gitignored) drops in to wire
  the existing `apply plugin: com.google.gms.google-services` line
  that Capacitor's template already had.
- `AndroidManifest.xml` adds `POST_NOTIFICATIONS` (Android 13+
  runtime grant — Capacitor's plugin prompts on first `register()`)
  and `WAKE_LOCK` (brief wake to render incoming pushes when screen
  is off).
- No Java changes needed — Capacitor's `@capacitor/push-notifications`
  plugin ships its own `FirebaseMessagingService` subclass; FCM
  payloads route through that and into the JS bridge.

**Dashboard JS (`static/index.html`):**
- New top-level `_initNativePush()` block right after the service
  worker registration. Runs only when `Capacitor.isNativePlatform()`
  reports true — web/PWA browsers never see this code path.
- Requests `POST_NOTIFICATIONS` via the plugin's `requestPermissions()`,
  registers, listens for `registration` → POSTs the FCM token to
  `/api/push/register-fcm`.
- Wires the plugin's three events:
  - `pushNotificationReceived` (foreground delivery — FCM suppresses
    the system tray when the app is open) → `showToast(title: body)`
    so the user notices without a duplicate-looking system bubble.
  - `pushNotificationActionPerformed` (tap from tray) → reads
    `notification.data.url` (or rebuilds it from
    `project_id`/`session_id`) and hands off to the existing
    `_handleDeepLinkFromUrl()` helper — same one the service-worker
    `mc-deeplink` postMessage already calls, so behavior is
    identical to web push tap-through.
  - `registrationError` → logs to `_pushState.native.error` for
    Settings-panel diagnostics.

**Verified end-to-end 2026-05-14:**
1. APK installed → CF pre-auth Toast → Android 13 permission prompt → grant.
2. JS posts token → `data/push_subscriptions.json` gets a row with
   `type:'fcm'`, label `'Android'`.
3. `POST /api/push/test` → `{sent:1, failed:0}`.
4. App foreground: in-app toast renders via `pushNotificationReceived`.
5. App backgrounded: system tray notification renders; tap opens the app.
6. (Deep-link routing with real `project_id`/`session_id` deferred to
   the first agent-emitted `PushNotification` tool call in the wild.)

**Rollback recipe:**
- Server: revert the four hunks in `server.py` (`_push_send_fcm`,
  `_notify_push` dispatch, `/api/push/register-fcm`, `/api/push/unsubscribe`
  token branch, `/api/push/subscriptions` type field). `requirements.txt`
  can keep `firebase-admin` (harmless if unused) or drop it.
- Mobile: revert AndroidManifest permissions + delete
  `android/app/google-services.json` to short-circuit the
  `com.google.gms.google-services` plugin apply (it's wrapped in a try
  block that no-ops on missing JSON).
- Dashboard: delete the `_initNativePush()` IIFE from `static/index.html`.
- `data/firebase_admin.json` stays gitignored; can be deleted from disk
  without breaking anything (web push keeps working).

**Open follow-ups:**
- Settings UI: add a "Send test" row that targets a specific subscription
  by nonce (currently `/api/push/test` fans out to everyone matching).
- Optional `@capacitor/device` install to get a real model label
  ("Galaxy Z Fold7") instead of the `'Android'` fallback.
- iOS APK shell (Capacitor supports it; needs Apple developer cert).
- Cleanup of the now-redundant `pywebpush` path on Android once the APK
  is everyone's primary surface — keep it for desktop browsers and iOS PWA.

## [2026-05-13b] — MCP servers management surface

Users asked to add MCP (Model Context Protocol) servers from the dashboard
instead of hand-editing `~/.claude.json` / `.mcp.json`. Built on the same
pattern as the Skills surface — MC manages the files, Claude Code reads them
natively at next session start (no preamble injection, no restart of CC
required for newly-added servers).

- **`mcp.py`** new module. `list_servers` / `read_server` / `write_server` /
  `delete_server`. Three transport types validated:
  - `stdio` → `{command, args?, env?}` (defaults; no `type` key, since stdio
    is CC's default)
  - `http`  → `{type: "http", url, headers?}` (streamable HTTP — most
    modern hosted MCP servers)
  - `sse`   → `{type: "sse",  url, headers?}` (legacy HTTP+SSE — still
    common in the wild)
- **Atomic writes** via `tempfile.mkstemp` + `os.replace`. `~/.claude.json`
  is owned by Claude Code and holds lots of unrelated state — we
  read-modify-write under a single `_global_write_lock` and never truncate
  other top-level keys. Project `.mcp.json` files use the same lock for
  simplicity.
- **Server endpoints** in `server.py` between the Skills block and the
  Global config block (`# ── MCP server endpoints`):
  - `GET    /api/mcp?project_id=…`       — list (with `shadowed_by_project`
    flag if a project entry overrides a global of the same name)
  - `GET    /api/mcp/<scope>/<name>`     — read one
  - `POST   /api/mcp`                    — create (409 on duplicate)
  - `PUT    /api/mcp/<scope>/<name>`     — update (always overwrite)
  - `DELETE /api/mcp/<scope>/<name>`     — remove
- **Frontend** (`static/index.html`, section comment
  `// ── MCP servers (global + per-project Model Context Protocol manager)`):
  - Sidebar entry "MCP" (🔌) directly below Skills, wired through
    `sidebarNav('mcp') → openAllMCP()`.
  - Per-project menu entry "MCP servers" in the three-dot dropdown, calls
    `openAllMCPForProject(pid)`.
  - List modal mirrors the Skills shell — scope filter, project filter,
    free-text search, scope/transport badges, shadow badge.
  - Editor modal with a transport `<select>` that swaps the field set
    between stdio (command/args/env) and http/sse (url/headers). Env vars
    and headers entered as one-per-line key=value / Key: value text.
- **Name rule**: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` — looser than Skills
  (which is strict kebab) because real-world MCP names use dots and
  underscores (e.g. `mcp.local.dev`, `github_actions`).
- **What v1 does NOT do**: connection test (spawn the stdio server / hit
  the URL to verify), OAuth helper flow, marketplace browser, mass-import
  from a paste. All deferred until users hit real friction.
- **Restart needed**: changes only take effect after restarting the Flask
  process (new module import + new routes).
- **Rollback**: delete `mcp.py`; remove `import mcp as _mcp` from
  `server.py` line 18; revert the `# ── MCP server endpoints` block in
  `server.py`; in `static/index.html` remove the sidebar `data-nav="mcp"`
  entry, the `else if (target === 'mcp')` line in `sidebarNav`, the
  `openAllMCPForProject` menu item in the project three-dot menu, and the
  whole `// ── MCP servers …` JS block.

## [2026-05-13b] — MCP "Add from URL" with security pre-flight

New install path for non-technical users: paste a GitHub repo URL (or npm
package name, or raw JSON config URL), MC does the rest. Bolted onto the
existing MCP editor as a mode toggle, no new modal.

- **Backend module** `mcp_installer.py` (new, ~520 lines):
  - `classify_url()` — accepts `github.com/x/y[/tree/<ref>]`, `npmjs.com/...`,
    bare `@scope/name`, raw `.json` URLs. Unknown is a valid kind.
  - `fetch_github_signals()` — stars, age, last-commit recency, license,
    archived flag, default branch. Uses `GITHUB_TOKEN` / `GH_TOKEN` if present.
  - `stage_clone()` — shallow `git clone --depth 1` into
    `~/.clayrune/mcp_installs/<owner>-<repo>/`, pins to the resolved SHA,
    drops a `.meta.json` with `{url, sha, staged_at}`.
  - `extract_config()` — three-tier fallback: (1) committed example files
    (`claude_desktop_config.json`, `mcp.json`, `examples/*.json`), (2) regex
    the README for the first ```json fence containing `mcpServers`, (3) one
    Claude call with the README as input + structured extraction prompt.
    Tier 3 only fires when 1 and 2 miss.
  - `_absolutize_paths()` — replaces `/path/to/<repo>`-style placeholders in
    the extracted config's `args` / `command` with the real install dir so
    the resulting `~/.claude.json` entry Just Works.
  - `detect_secrets()` — placeholder regex (`your-api-key`, `paste-here`,
    etc.) + heuristic on env var names ending in `api_key` / `token` /
    `secret` / `password` to surface required user input.
  - `dependency_audit()` — `npm audit --json` (generates a lockfile via
    `--package-lock-only --ignore-scripts` if missing) or `pip-audit -f
    json`, returns critical/high/moderate/low counts + top 20 findings.
  - `security_scan()` — gathers up to 20 KB of source from the repo
    (excludes `node_modules`, `.git`, `dist`, build dirs), sends to Claude
    with a structured prompt asking for a 4-row table:
    Network / Filesystem / Shell / Secrets + a free-form `flags` list of
    anything that doesn't match the README's claimed purpose. Cached on
    `<install_dir>@<sha>` so re-previewing the same commit is free.
  - `install_commands()` / `stream_install()` — runs `npm install
    --no-audit --no-fund` or `uv sync` / `pip install -e .` /
    `pip install -r requirements.txt`, streamed via a callback.
- **Backend endpoints** (`server.py`, between the existing MCP DELETE and
  Global config sections):
  - `POST /api/mcp/url/preview` — runs the whole preview pipeline (classify
    → clone → extract → audit → scan), returns one JSON blob the frontend
    renders. Does NOT install.
  - `POST /api/mcp/url/install` — SSE stream that runs the install commands
    line-by-line then writes the final config via the existing
    `mcp.write_server()`.
  - `DELETE /api/mcp/url/staged` — cleans up the staged clone if the user
    cancels after preview. Defense in depth: rejects paths outside
    `~/.clayrune/mcp_installs/`.
- **Frontend** (`static/index.html`):
  - `openMCPEditor` gets a **Manual / From URL** mode toggle at the top of
    the modal (only shown for new servers; editing always uses manual).
  - URL mode state machine: **input** (URL field + Preview button) →
    **preview** (GitHub trust row + audit banner + security-scan table +
    secrets form + commands to run + final config preview + name/scope/
    project pickers) → **installing** (live SSE log streamed into a `<pre>`)
    → **done** (success card + collapsible install log).
  - Required-secret check before Install button fires.
  - Back button cleans up the staged clone server-side.

**Smoke test:** paste `https://github.com/tradesdontlie/tradingview-mcp` →
Preview → MC clones (~3s) + extracts the README JSON block (tier 2) +
runs `npm audit` + Claude scans the source (~5s) + shows you what's about
to run. Click Install → live `npm install` output → "Done." card.

**Restart needed:** the new module + endpoints only load after a Flask
restart.

**Rollback:** delete `mcp_installer.py`, remove the `import mcp_installer
as _mcpinst` line and the three `/api/mcp/url/*` route handlers, delete
the `_mcpEditorSetMode` / `_mcpUrl*` block in `index.html`, revert the
`openMCPEditor` mode-toggle change.

## [2026-05-13] — In-dashboard Claude auth surface

Ron hit a 401 from the dashboard this morning — the `claude` CLI had no
valid credentials and there was no UI hint, just silent failure. Added
detect-and-launch auth recovery:

- **Server-side sentinel scan** (`server.py`, just above the agent-endpoints
  section): every line read by `_read_agent_stream` (Mode A) and
  `_read_agent_stream_b` (Mode B) is run through `_scan_for_auth_error()`,
  which matches "Please run /login", "not logged in", "Invalid (api) key",
  and `authentication_error`. A hit calls `_mark_claude_auth_error(reason,
  line)` which flips a global `_claude_auth_state` dict (lock-guarded).
  (The "credit balance is too low" sentinel was removed — Clayrune users sign
  in via subscription, not API billing, so that warning was always a false
  positive coming from stray API-style errors.)
- **`GET /api/claude/auth-status`** — returns the dict. Cheap, no subprocess.
- **`POST /api/claude/auth-probe`** — actively runs
  `claude -p ok --max-turns 1` (20s timeout) and updates the dict from the
  combined stdout+stderr. Costs a few tokens when authed; only invoked when
  the user clicks "Re-check".
- **`POST /api/claude/login-launch`** — opens `claude` in a NEW OS-level
  terminal window. Why not the existing in-app terminal pop-out? That pop-out
  uses `subprocess.Popen` with `stdin=PIPE` (not a PTY), and claude's `/login`
  slash command refuses without a real TTY ("/login isn't available in this
  environment"). Windows: `start "" cmd /k claude`. macOS: AppleScript to
  Terminal.app. Linux: tries `x-terminal-emulator` / `gnome-terminal` /
  `konsole` / `xfce4-terminal` / `xterm` in that order.
- **Frontend banner** (`static/index.html`, sibling of `schedule-banner`):
  warm-orange bar above the project grid. Two buttons:
  - **Authenticate Claude** → `POST /api/claude/login-launch`, then a toast
    instructing the user to type `/login` in the new window and click Re-check
    when done.
  - **Re-check** → `POST /api/claude/auth-probe`, banner hides on success.
  - **No credit** variant swaps the primary button to "Open Billing"
    (`console.anthropic.com/settings/billing`).
- **Settings → Claude Code Integration → "Sign in to Claude"**: explicit
  Sign in + Check status buttons. Always visible regardless of whether the
  auto-banner detection fires — this is the dependable escape hatch when the
  agent-stream sentinel scan misses the actual error format claude printed.
- Polls `/api/claude/auth-status` on dashboard load, every 90s, and after
  every agent SSE `error` event so a fresh 401 surfaces within seconds.

Restart needed: changes only take effect after restarting the Flask process.

Rollback recipe: revert the `# ── Claude CLI auth-status tracking` block in
`server.py`, the two new routes, the two `_scan_for_auth_error` calls in the
stream readers; drop the `auth-banner` HTML + CSS + JS block in
`index.html`; remove the `refreshAuthStatus()` calls (initial-load chain,
`setInterval`, and SSE-error branch).

## [2026-05-11c] — Single-instance guard for browser tabs

Follow-up to the launch_handler fix in `[2026-05-11b]`. On a fresh
Windows install where the user pinned `localhost:5199` (or
`clayrune.io`) to the Start menu **as a browser shortcut** — i.e. they
hadn't actually installed the PWA — each click of the icon spawned a
new browser tab. `launch_handler` in `manifest.json` doesn't help here
because the PWA isn't installed; the click is just Chrome opening a
URL bookmark.

Added a `BroadcastChannel`-based single-instance guard at the top of
`static/index.html <head>`:

- New tab announces itself with a timestamped instance ID.
- The existing primary tab acks and calls `window.focus()` to pull
  itself forward.
- The newcomer tab, on receiving the ack, replaces its UI with a
  "Clayrune is already open" panel + "Close this tab" button, then
  attempts `window.close()` after 1.5s (works for fresh tabs that
  have no history entry).
- Tiebreaker by ID timestamp handles the case where the user double-
  clicks the Start menu icon and two tabs race to claim primary.
- Skipped when `display-mode: standalone` matches — installed PWAs use
  `launch_handler` instead, and we don't want to interfere with their
  deep-link navigation.

`index.html` is already served with `Cache-Control: no-cache` + mtime
ETag (`server.py:8331`), so a normal browser refresh on the other
install will revalidate and pick up the new script — no hard refresh
needed.

Known gap: if a user *wants* multiple Clayrune tabs open intentionally
(e.g. side-by-side comparison), this guard fights them. Acceptable for
now since the dashboard isn't designed for split-view workflows; if it
ever becomes painful, expose a `localStorage.clayrune_allow_multitab`
flag.

Rollback: remove the `<script>` block in `static/index.html` between
"Single-instance guard" and the closing `</script>` just below the
apple-mobile-web-app-title meta.

## [2026-05-11b] — PWA shell + deep linking + push-sub dedup

Follow-up to `[2026-05-11]` after live testing on Android Chrome. Three
specific problems came up:

1. Notifications were stamped **"Possible spam from clayrune.io"** by
   Chrome — its heuristic for low-traffic web-push origins.
2. Tapping a notification landed the user at the dashboard root, not at
   the project + session that fired the push.
3. CF Access re-OTP (new nonce every ~24h) would orphan the push
   subscription, gradually accumulating duplicates.

All three addressed in this change. The mechanical web-push pipeline
itself was already correct (see `[2026-05-11]`).

### PWA shell (kills the "Possible spam" warning)

- New `static/manifest.json` — name/short_name "Clayrune", `display:
  standalone`, theme/background colors, 192 + 512 PNG icons (also one
  `maskable` variant). `start_url: /`.
- New PNG icons rendered via Pillow: `static/icon-192.png`,
  `icon-512.png`, `icon-badge-72.png`. Orange rounded square + white "C"
  (matches existing inline-SVG favicon). Generated once with the script
  in CHANGELOG `[2026-05-11]`; living source kept in
  `static/icon.svg` if the brand evolves.
- `index.html <head>` now links the manifest + Apple touch icon meta +
  apple-mobile-web-app-* meta for iOS A2HS parity.
- Service worker (`static/sw.js`) — uses the new PNGs for `icon` and
  `badge` instead of the previous 404s. Notifications shown from inside
  an installed PWA are credited to **Clayrune** (the app), not to the
  website, so Chrome's spam classifier doesn't trigger.
- Settings → Push Notifications: new install-state row.
  - Installed → green checkmark + explanation.
  - `beforeinstallprompt` captured → "Install" button that triggers
    Chrome's native install flow. Listener also re-renders the section
    on `appinstalled` so the row flips to "installed" state.
  - Neither yet → hint pointing the user at Chrome's menu → Install app.

### Deep linking from notification clicks

- `_handleDeepLinkFromUrl(url)` parses `?project=X&session=Y`, calls
  `openProjectModal(X)`, waits one paint, then `switchAgentTab(X, Y)`.
  Cleans the URL with `history.replaceState` so manual refresh doesn't
  re-fire. Called once at boot from `fetchProjects().then(...)` after
  agent-session restore, and on every `mc-deeplink` postMessage from the
  service worker (notification click while the PWA is already open).
- `sw.js notificationclick`: instead of `client.navigate()` (unreliable
  for standalone PWA windows, can stomp on in-flight UI), now focuses
  the existing client and `postMessage({type:'mc-deeplink', url})` to
  the SPA. Cold start still uses `clients.openWindow(targetUrl)`.

### Push-sub dedup-by-endpoint (survives re-OTP)

- `POST /api/push/subscribe`: before writing the new record, scans
  existing subs for a matching `endpoint` under a *different* nonce. If
  found, the old nonce-keyed record is dropped and its prefs
  (`label`, `notify_*`, `created_at`) carry over. Logged with
  `[push] migrated subscription X… → Y…`. Browsers reuse the same
  PushSubscription.endpoint across CF re-OTPs even though MC's nonce
  changes, so this keeps the device count honest.

### localStorage device-name auto-submit (silences re-OTP UX)

- `/_mc/name-device` page now writes `localStorage.mc_device_name` on
  successful submit. On reload (e.g. after re-OTP gives the device a
  fresh nonce), if that key is set, the page auto-submits without UI —
  the user sees a brief *"Recognized this device as <name>.
  Reconnecting…"* card instead of being asked to name again. The
  CF-Access OTP step itself is untouched (it's CF's auth boundary, not
  ours).

### Files touched

- `static/manifest.json` (new)
- `static/icon-192.png`, `icon-512.png`, `icon-badge-72.png` (new)
- `static/sw.js` — PNG icons, postMessage on click
- `static/index.html` — manifest link, deep-link handler, SW message
  listener, install button + state, push section render
- `server.py` — `push_subscribe` dedup-by-endpoint, name-device page
  auto-submit JS

### Windows PWA: single-instance launch (`launch_handler`)

Observed on Windows after installing the PWA: clicking the Start menu /
taskbar icon while Clayrune was already open spawned a **second
standalone window** instead of focusing the existing one. Chrome's
default for `display: standalone` is `navigate-new` — a new window per
launch.

Fix: added `launch_handler.client_mode: "focus-existing"` to
`static/manifest.json`. Now a second click on the Start menu icon
focuses the open window without navigating or reloading, so session
state in the SPA is preserved. (Service-worker-driven deep links from
notification clicks are unaffected — they go through the existing
`postMessage` path, not the launch URL.)

Rollback: drop the `launch_handler` block from `manifest.json` and
re-install the PWA. Note that PWAs cache the manifest aggressively;
uninstall + reinstall is the reliable way to pick up manifest changes.

### Open follow-ups

- iOS PWA install path (requires Safari "Add to Home Screen", different
  install affordance — no `beforeinstallprompt` on iOS).
- CF Access "Session Duration" — left at user's CF policy default. Ron
  can bump it to 7d/30d in the Cloudflare dashboard if re-OTPs become
  noisy.

## [2026-05-11] — Web push notifications (Android-first)

Wires Claude's `PushNotification` tool to actual phone-side delivery via
VAPID web push. Solves the "I have no idea when the agent is done" problem
without building a Telegram bot — taps on the push land in the existing
clayrune.io chat where `/agent/send` already handles follow-ups.

### Why

Claude's built-in Remote Control (claude.ai "Code" surface) only registers
*interactive* (TTY) sessions, so MC-managed sessions never show up there
even though `--remote-control` is accepted by the CLI. We confirmed this in
testing: a real TTY `claude --remote-control "tty-test"` registered fine,
but a `claude --print --remote-control ...` from MC did not. The
`agent_remote_control` toggle is now marked **EXPERIMENTAL** in Settings;
web push is the supported notification path.

Claude's `PushNotification` tool (deferred tool, see the verbatim
description in `docs/web-push-handoff.md`) is model-aware: the model knows
when to call it (long task done, build ready, decision needed) and when
NOT to (routine progress, just-answered questions). MC intercepts the
`tool_use` event in stream-json and delivers the push itself, since the
"push to phone" half of the tool relies on Remote Control discovery that
MC sessions don't get.

### Backend (`server.py`)

- New module: `# ── Web push notifications` block (just above the per-CF
  session-labels block). Self-contained: VAPID keypair generation,
  subscription storage, dispatch helper, stream-reader hook, endpoints.
- VAPID keypair lazily generated via `py_vapid` on first call to
  `_load_vapid_keys()`. Public key serialized as base64url-encoded 65-byte
  uncompressed P-256 point (what `PushManager.subscribe` expects in
  `applicationServerKey`). Private key persisted as PEM PKCS8 (what
  `pywebpush.webpush(vapid_private_key=...)` accepts). File:
  `data/push_vapid.json`. Survives restarts; only generated once.
- Subscriptions persisted at `data/push_subscriptions.json`, keyed by CF
  Access session nonce (same key the session-label system uses, so
  subscriptions get cleaned up alongside revoked CF sessions). Non-CF
  callers fall back to `local:<sha1(endpoint)[:16]>`.
- `_notify_push(title, body, *, url, project_id, session_id, kind)`
  encrypts + signs via `pywebpush.webpush()`, fires to every subscription
  that opted in for `kind` (`'agent'` or `'turn_complete'`), removes 404/410
  subscriptions automatically (browser unsubscribed or push service
  evicted), records `last_used_at` on success.
- `_handle_push_signal(project_id, session_id, msg)` is called once per
  parsed stream-json message in **both** stream readers
  (`_read_agent_stream` Mode A, `_read_agent_stream_b` Mode B):
  - `type=assistant` with a `tool_use` block where `name=='PushNotification'`
    → fire `kind='agent'` push with `input.message` as body.
  - `type=result` → fire `kind='turn_complete'` push iff the project has
    `notify_turn_complete=True` and `notify_push_enabled` (default `True`).
- Endpoints (mirror the `# ── Remote access` block style):
  - `GET  /api/push/vapid-public-key` — returns base64url public key.
  - `POST /api/push/subscribe`        — body `{endpoint, keys{p256dh,auth}, label?}`.
  - `POST /api/push/unsubscribe`      — body `{nonce}` or `{endpoint}`.
  - `GET  /api/push/subscriptions`    — list (no endpoint exposed).
  - `PATCH /api/push/subscription/<nonce>` — toggle `notify_agent_push` /
    `notify_turn_complete` / `project_filter` / rename.
  - `POST /api/push/test`             — fire a test push to every subscriber.

### Service worker (`static/sw.js`)

- Served at `/sw.js` (not `/static/sw.js`) via a new `service_worker()` route
  in `server.py`, with `Service-Worker-Allowed: /` header so the worker
  scope covers the whole origin (`/?project=...&session=...` deep links
  need root scope).
- `push` event handler reads JSON payload `{title, body, url, project_id,
  session_id, kind, ts}` and calls `showNotification()`. Tag is
  `mc-<session_id>` so re-pushes for the same session collapse instead of
  stacking.
- `notificationclick` handler tries to focus + navigate an existing tab on
  this origin to the `url` (typically `/?project=X&session=Y`), falls back
  to `clients.openWindow()`. (Deep-link routing on the SPA side is not yet
  wired — clicking lands you on `/` for now; routing into a specific
  project + session tab is a follow-up.)

### Frontend (`static/index.html`)

- New `pushNotificationsSettingsHTML()` section rendered right under
  Remote Access in Settings. Detects browser support; shows the right CTA
  for `Notification.permission` (default / granted / denied). The
  "Enable on this device" flow runs:
  - `Notification.requestPermission()`
  - `navigator.serviceWorker.register('/sw.js', {scope: '/'})`
  - `pushManager.subscribe({userVisibleOnly: true, applicationServerKey: ...})`
  - `POST /api/push/subscribe` with the resulting endpoint + keys + a
    guessed device label (e.g. "Chrome · Android").
- "Subscribed devices" list shows label, UA, last-used / created times, a
  Remove button (calls `/api/push/unsubscribe` AND `subscription.unsubscribe()`
  if it's this device), and per-device toggles for "Agent push" and
  "Turn complete" (PATCH `/api/push/subscription/<nonce>`).
- "Send test" button calls `/api/push/test`.
- Existing "Remote Control" toggle in Claude Code Integration is now
  badged `EXPERIMENTAL` with a hint explaining the non-TTY caveat.
- `_renderSettings()` now also calls `refreshPushSection()` after the
  settings panel renders.

### Storage shapes

```jsonc
// data/push_vapid.json
{ "public": "BO…(87 chars b64url)", "private": "-----BEGIN PRIVATE KEY-----\n…", "created_at": 1715432400 }

// data/push_subscriptions.json — keyed by CF Access nonce (or local:<hash>)
{
  "<nonce>": {
    "label": "Chrome · Android",
    "ua": "Mozilla/5.0 …",
    "endpoint": "https://fcm.googleapis.com/fcm/send/xyz",
    "keys": { "p256dh": "…", "auth": "…" },
    "project_filter": null,
    "notify_agent_push": true,
    "notify_turn_complete": false,
    "created_at": 1715432400,
    "last_used_at": 0
  }
}
```

### Per-project flags (optional, default behavior is correct)

- `notify_push_enabled` (default `True`) — project-level kill-switch.
- `notify_turn_complete` (default `False`) — opt-in for end-of-turn pushes
  (spammy by default).

Not yet exposed in the per-project menu — defer until users ask. The
server reads them straight from the project JSON via `load_project(...).
get(key, default)`.

### Dependencies

- `pywebpush>=2.0.0` added to `requirements.txt` (pulls `py-vapid`,
  `http-ece` transitively). Tested with `pywebpush==2.3.0`.
- `cryptography>=43.0` was already in `requirements.txt` for mc_remote;
  pywebpush uses it for VAPID + ECE encryption.

### Rollback

- Revert this commit, remove `data/push_vapid.json` and
  `data/push_subscriptions.json`. The `pywebpush` import is lazy inside
  `_notify_push` / `_load_vapid_keys`, so leaving the package installed
  while the code is reverted is harmless.

### Follow-ups (not blocking)

- SPA deep-link routing for `/?project=X&session=Y` from notification clicks.
- Per-project notify toggles in the three-dot menu (server already
  supports them).
- iOS PWA install path (requires "Add to Home Screen" first; spec'd in
  `docs/web-push-handoff.md`).
- Test on Android Chrome end-to-end (Ron, this needs a server restart and
  a phone). After restart: open Settings → Push Notifications → Enable on
  this device on the phone via clayrune.io → tap "Send test" from the
  desktop dashboard → notification should ring on the phone.

## [2026-05-10c] — Skills import: GitHub tree-URL parsing + Anthropic plugin detection

Two related improvements to skills import. First, **GitHub web URLs that point
at a subfolder of a repo now work** — earlier the importer rejected them with
the raw `git clone` error (`repository '...not found'`). Second, **Anthropic
plugins are detected as a distinct shape**: the importer now offers "Install
full plugin" alongside "Install this skill" when a `.claude-plugin/` folder
is present.

### URL parsing (`skills.py`)

- `_GH_TREE_RE` matches `github.com/<owner>/<repo>/(tree|blob)/<ref>/<subpath>`.
- `normalize_git_url(url)` returns `{clone_url, ref, subpath}` — tree/blob URLs
  get split into bare clone URL + branch + subdirectory; bare repo URLs pass
  through.
- `git_clone_to_staging` now uses the normalized parts: clones the bare URL,
  applies the parsed branch via `--branch`, and after clone trims the staging
  tree to just the requested subpath so the rest of the pipeline (scan,
  candidate selection, install) stays unchanged.
- Error messages updated: when no SKILL.md is found under a subpath, the
  message says so plainly instead of leaving the user to guess.

### Plugin detection (`skills.py`)

- `detect_plugin_at(root)` returns `{name, manifest, readme_excerpt,
  skill_dirs, command_files, agent_files, hook_files, has_hooks, root_path}`
  when `.claude-plugin/` exists; `None` otherwise.
- `install_full_plugin(plugin_root)` copies `skills/`, `commands/`, and
  `agents/` to their respective `~/.claude/` directories. **Hooks are not
  installed**: registration requires modifying `~/.claude/settings.json`
  with author-supplied event bindings, which is arbitrary shell-code
  execution and a stronger trust statement than copying data files. The
  result includes a `skipped.hooks` list and the summary message points
  the user at CC's `/plugin` command for hook installation.
- Both `git_clone_to_staging` and `import_from_folder` now attach a
  `plugin: {...}` field to their response when a plugin is detected. The
  git endpoint also skips auto-install of single-skill clones when a
  plugin is present, so the user always sees the picker and can choose
  between "skill-only" and "full plugin" modes.
- New error path: when a plugin is detected but contains no SKILL.md, the
  message is now: *"This is the Anthropic plugin "<name>" but contains no
  skills (only N command(s), M sub-agent(s)). Clayrune manages skills;
  for the rest, install via CC's /plugin command instead."*

### Endpoint (`server.py`)

- `POST /api/skills/import/plugin` — body `{staging_id?, path?, overwrite?}`.
  Either `staging_id` (from a prior `/api/skills/import/git` call) or `path`
  (a local folder) is accepted. Full-plugin install goes to GLOBAL scope
  only; project-scope full-plugin install is not supported in v1.
- The existing `/api/skills/import/git` endpoint no longer auto-installs a
  single skill when a plugin is detected — the response includes the
  plugin info so the frontend can prompt the user.

### Frontend (`static/index.html`)

- `_renderPluginBanner(plugin)` — small accent-bordered banner with a
  PLUGIN badge, plugin name, component counts (skills · commands ·
  sub-agents · hooks), an optional README excerpt (first 360 chars), and
  an amber warning line when hooks are present.
- `_renderFullPluginButton(modalId)` — full-width "Install full plugin
  (skills + commands + sub-agents)" button rendered above the per-skill
  candidate rows.
- `_doSkillImportFullPlugin(modalId)` — POSTs to `/api/skills/import/plugin`
  using `win_importPluginSource[modalId]` (set by the multi-skill picker
  when `plugin` is in the response). Shows the summary message in the
  status line and as a toast.
- Both the Git import picker and the Folder import picker now show the
  banner + full-plugin button when applicable. The per-skill candidates
  remain below.

### Trust model

The deliberate carve-out for hooks isn't about JSON merge fragility — it's
about the trust statement. Skills, commands, and sub-agents are data the
model reads; their execution path is mediated by the model + user
permission system. Hooks are shell scripts that run automatically on
lifecycle events, with no model and no permission prompt between author
intent and execution. We auto-install the first three; we defer hooks to
CC's `/plugin` command, which (presumably) has its own confirmation step
for that stronger trust statement.

### Rollback

Remove the plugin detection block in `skills.py` (`# ── Anthropic-plugin
detection + full-plugin install ──`), the `import_full_plugin_route` and
plugin-info attachment in `server.py`, the `_renderPluginBanner` /
`_renderFullPluginButton` / `_doSkillImportFullPlugin` helpers in
`static/index.html`, and the banner-render code inside the two candidate
pickers. URL parsing changes are independent and can stay or be removed
separately.

## [2026-05-10b] — Skills import (paste / folder / Git URL / cross-project)

Follow-up to the morning's Skills surface. Adds 4 import paths so users can
bring in skills from outside Clayrune instead of authoring everything from
scratch. All four ship together because they cover non-overlapping sources
and share the same destination-scope picker.

### Backend (`skills.py`)

- `import_from_paste(content, scope, ...)` — parses pasted SKILL.md,
  validates frontmatter, calls `write_skill`. Name comes from frontmatter
  or an explicit override.
- `import_from_folder(src_path, scope, ..., selected_rel_dir?)` — scans
  the folder (depth-capped at 3) for SKILL.md files. Single hit installs
  immediately; multi-hit returns `{multiple: True, candidates: [...]}`
  so the caller can re-invoke with `selected_rel_dir`.
- `git_clone_to_staging(url, ref?, timeout=60)` — shallow `git clone` into
  `~/.claude/skills.staging/<uuid>/`, strips `.git`, scans for SKILL.md
  files. Returns `{staging_id, candidates}`.
- `install_from_staging(staging_id, rel_dir, scope, ...)` — copies the
  chosen candidate from a previously-staged clone. Path-traversal-checked
  (rel_dir must stay inside staging_path).
- `cleanup_stale_staging(max_age_hours=24)` — sweeps abandoned staging
  dirs at startup so they don't accumulate.
- `_install_skill_dir` (private) — shared helper that copies a skill
  folder + normalizes the destination SKILL.md's frontmatter `name` to
  match the install name (so the install name and the frontmatter never
  diverge).
- `_scan_for_skills(root, max_depth)` — finds all SKILL.md files,
  returns `{name, rel_dir, abs_dir, description, has_subassets}` for
  each. Used by both folder and git flows.

### Backend (`server.py`)

- `POST /api/skills/import/paste` — body `{content, scope, project_id?, name?}`
- `POST /api/skills/import/folder` — body `{path, scope, project_id?, name?, selected_rel_dir?}`;
  returns `{multiple: true, candidates: [...]}` when ambiguous.
- `POST /api/skills/import/git` — body `{url, ref?, scope, project_id?, name?, auto_install?}`.
  Auto-installs when exactly one SKILL.md found; otherwise returns
  `{staging_id, candidates}` for the picker.
- `POST /api/skills/import/git/install` — body `{staging_id, rel_dir, scope, ...}`.
  Path-checked so a malicious `rel_dir` can't escape the staging dir.
- `POST /api/skills/import/git/cancel` — discards a staging dir without
  installing.
- Startup hook: `_skills.cleanup_stale_staging(max_age_hours=24)` runs
  from `__main__` alongside `_install_builtin_skills()`.

### Frontend (`static/index.html`)

- New **Import ▾** dropdown beside "+ New Skill" in the Skills modal
  header. 4 menu entries: Paste SKILL.md / From a folder / From a Git URL
  / Browse other projects.
- `_importContextHTML` shared component renders the scope radio + project
  picker — used by all 4 import modals so destination-scope UX is uniform.
- Defaults: when the Skills modal is filtered to a specific project, the
  import context defaults to that project; else global.
- **Paste modal**: large monospace textarea, optional name override,
  scope picker. Single click installs.
- **Folder modal**: path text input (Windows + POSIX accepted), optional
  name override. If backend reports `multiple`, inline candidate picker
  shows below the input with one-click install per candidate.
- **Git modal**: URL input, optional branch/tag, optional name override
  (single-skill repos only). Single-skill repos auto-install. Multi-skill
  repos show inline candidate picker. Cancel cleans up the staging dir.
- **Browse modal**: fan-out search across global pool + every loaded
  project's pool, dedup + sort by score. Each result has "Read body"
  (toggles inline body preview) and "Install here" (copies into the
  chosen destination scope).

### Notes / design decisions

- Cross-project copy reuses existing `POST /api/skills` — no new endpoint
  needed. Frontend fetches the source skill with `include_body=true`,
  POSTs the same name/description/body to the destination.
- Git clone is shallow + 60s timeout. `.git` is stripped after clone so
  the skill folder looks like any other on-disk skill.
- Multi-skill repo case routes through a staging dir to avoid double-clone
  on candidate selection. Stale staging dirs are swept at startup.
- Private repos: deliberately not supported in v1. Users can clone
  manually with system git (which has their credentials) and import via
  the folder path, or wait for a follow-up.
- Marketplace / Anthropic registry: deliberately skipped. No registry to
  point at yet; placeholder UI would be a liability.

### Rollback

Remove the `# ── Skills import (paste / folder / Git URL ...)` block in
`server.py`, the `# ── Import (paste / folder / git URL)` block in
`skills.py`, and the `// ── Skills import (paste / folder ...)` section in
`static/index.html`. Also remove the staging cleanup call from `__main__`
and the Import dropdown HTML inside `openAllSkills`. Existing skills are
unaffected — only the import paths disappear.

## [2026-05-10] — Skills surface (Anthropic-format skill management)

Adds a first-class Skills surface to Clayrune so users can author, organize,
and (eventually) share Anthropic-format skills the way they already manage
backlog, scheduler routines, and hiveminds. Skills are the lazy-loadable
instruction packs Claude Code reads from `~/.claude/skills/<name>/SKILL.md`
(global) and `<project_path>/.claude/skills/<name>/SKILL.md` (project-local).
Clayrune does NOT teach CC about skills — CC already loads them natively. The
new surface is purely management (CRUD + archive + search + usage stats).

**Why now.** Anthropic's skill ecosystem matured around `/loop`, `/schedule`,
`/review`, `/security-review`, etc. Going full-live without a way to view /
author / manage skills would leave a visible product gap; pre-launch is the
right window to add it.

**Distinct from the March 2026-03-17i removal.** That removal deleted MC's
own homegrown "Skills" feature (markdown-blob injection — Memory replaced it).
The new feature is a wrapper around CC's native skill system, not a re-do of
the old one.

### Backend — new module `skills.py`

- `parse_skill_md` / `dump_skill_md` — tiny YAML-frontmatter parser/dumper
  (no PyYAML dep). Handles `key: value`, block scalars (`|`, `>`), folded
  multi-line continuations.
- `list_skills(project_path, include_archived, include_body)` — merges
  global pool + a named project's pool (+ optionally archived), annotates
  `shadowed_by_project=True` when a global is overridden by a project skill
  of the same name (CC's own resolution rule).
- `read_skill` / `write_skill` / `delete_skill` / `restore_skill` —
  filesystem CRUD. `delete_skill` archives globals by default (moves to
  `~/.claude/skills.archive/`); project skills hard-delete (archiving them
  globally would move files out of the user's project tree).
- `search_skills(query, project_path, limit)` — keyword search over
  name (×3) + description (×2) + body (×1). Deterministic, cheap. Used by
  the `mc-skill-broker` skill for cross-project discovery.
- `install_builtins(builtin_root)` — checksum-aware install of bundled
  skills. For each `<name>/` in `data/skills/builtin/`: if not installed,
  copy + write `.mc-builtin-hash` marker. On subsequent boots, if the
  marker matches the installed SKILL.md hash AND the source has changed,
  update it; if the user has modified the file (hash drift from marker),
  leave alone and log `preserved=[...]`. Users always win.
- `skill_usage_stats(days)` — greps `~/.claude/projects/*/*.jsonl` for
  `Skill` tool-use blocks; returns `{name -> {invocations, last_invoked_at,
  project_count}}`. Same transcript-parsing path the Agent Log tab already
  uses (CHANGELOG `[2026-04-28]`). Surfaces dead skills.

### Backend — endpoints (`server.py`)

- `GET /api/skills?project_id=&include_archived=&q=` — list (no body)
- `GET /api/skills/<scope>/<name>?project_id=&include_body=` — read one;
  scope ∈ {`global`, `project`, `archive`}
- `POST /api/skills` — create; body `{name, description, body, scope, project_id?}`
- `PUT /api/skills/<scope>/<name>` — update
- `DELETE /api/skills/<scope>/<name>?project_id=&archive=true|false` —
  archive (global default) or hard-delete
- `POST /api/skills/archive/<name>/restore` — move back to global pool
- `GET /api/skills/search?q=&project_id=&limit=` — ranked keyword search
- `GET /api/skills/usage?days=30` — invocation stats from transcripts

All endpoints validate name format (kebab-case via `_NAME_RE`), require a
non-empty description, and refuse project scope when the named project has
no `project_path` set.

### Backend — built-in install hook

`_install_builtin_skills()` runs from `__main__` on startup. Source-of-truth
under `data/skills/builtin/`; safe to run on every boot. Logs `installed=`,
`updated=`, `preserved=` to stdout.

### Built-in skill set (`data/skills/builtin/`)

Five skills ship with Clayrune:

1. **`mc-clayrune-apis`** — teaches agents the localhost:5199 API surface
   (process registration, backlog, scheduler, hivemind, terminal). This is
   the wedge that — once skills prove reliable in production — will let us
   trim the `_build_agent_context()` preamble from ~40 lines to a pointer.
2. **`document-commit-deploy`** — concrete playbook for the
   "update docs, commit, push" workflow that SHARED_RULES requires but
   that today's agents only inconsistently follow.
3. **`mc-project-status`** — pulls backlog + recent activity + active
   hiveminds + scheduled jobs + registered processes into a structured
   project-state summary.
4. **`mc-changelog-update`** — guided CHANGELOG.md entry that matches the
   existing project's date-stamp / section style / voice.
5. **`mc-skill-broker`** — cross-project skill discovery. Calls
   `/api/skills/search` so a project-A agent can find a useful skill
   authored in project-B without polluting every session's catalog. The
   scaling story past ~80 skills.

### Frontend (`static/index.html`)

- New sidebar entry "Skills" with puzzle-piece icon, positioned **above
  Backlog** (per user preference). `data-nav="skills"` → `sidebarNav('skills')`
  → `openAllSkills()`.
- New project modal three-dot menu entry "Skills" (next to Memory & Rules)
  → `openAllSkillsForProject(projectId)` which pre-filters the global view
  to that project's scope.
- **Global Skills modal** (`__all_skills`): search box, scope filter (all
  / global / project / archive), project dropdown, "Include archived"
  checkbox, "+ New Skill" button, scrollable list.
- **Skill row UI** (`_renderSkillRow`): name, scope badge (global / project:
  X / archived), shadowed badge when global is overridden, 30-day
  invocation count from `_skillUsageCache`, full path + last-edited
  timestamp, Edit / Archive (or Delete for non-global) buttons.
- **Skill editor modal** (`openSkillEditor`): name (kebab-case, locked when
  editing), scope radio + project picker (only on create), description
  textarea with **live linter** (`lintSkillDescription` — warns on
  short descriptions, missing TRIGGER, vague trigger language), body
  textarea, Save / Cancel.
- Saves call `POST/PUT /api/skills` and refresh the list on success.
- Archive / restore / delete confirmations via standard `confirm()` +
  `showToast` flash.

### State (frontend)

- `_allSkillsCache = {items, loaded, loading}`
- `_allSkillsFilter = {scope, project, search, includeArchived}`
- `_skillUsageCache = {stats, loaded}`

### Decisions captured during scoping (memory: `project_skills_for_launch.md`)

- Sidebar position: above Backlog
- Built-ins ship globally (one install in `~/.claude/skills/`, every project
  sees them) rather than copying into each project's tree
- Project skills shadow globals of the same name; surface "shadowed" badge
- Skills broker is the answer to the scaling concern — keyword search over
  the full pool, so the broker becomes *more* valuable past ~80 skills
- Per-project enable/disable of globals is **NOT** in this release; deferred
  until usage stats prove globals are bloating sessions
- Built-in update propagation: only when user hasn't edited the file. Hash
  marker `.mc-builtin-hash` decides.

### Rollback

- Remove sidebar entry (line ~3507 in `static/index.html`), `sidebarNav`
  dispatch (line ~4995), three-dot menu item (line ~4413).
- Delete the Skills section in `static/index.html` (search comment
  `// ── Skills (global + per-project`).
- Delete the Skills endpoints in `server.py` (search comment
  `# ── Skills endpoints`).
- Remove `_install_builtin_skills()` call from `if __name__ == '__main__':`
  block and the `import skills as _skills` line at the top.
- Existing `~/.claude/skills/mc-*/` folders can be archived or deleted
  manually; CC will simply stop seeing them.

## [2026-05-09] — Proactive update notification + marketing site mockups

**Proactive Clayrune update notification** (`server.py`, `static/index.html`).
The Update Clayrune button only ever fired if the user happened to click
Settings → Update — so most updates went unseen. Now the dashboard signals
updates passively + actively without needing a click.

- New background daemon `_update_check_loop()` in `server.py` runs `git fetch`
  + computes the behind count every 6 hours, stores result in
  `_UPDATE_CHECK_CACHE` under `_UPDATE_CHECK_LOCK`. First check fires 60s
  after server boot.
- New `/api/system/update/cached` endpoint reads the cache (no git
  operations on the request path). Existing `/api/system/update/status`
  unchanged — still does a fresh fetch when the user actively clicks
  "Check now" in Settings.
- Frontend: new `checkClayruneUpdateAvailable()` runs once after
  `fetchProjects()` resolves on dashboard load. If `update_available`:
    1. `.has-update` class on `.sidebar-item[data-nav="settings"]` → small
       accent dot with a 2.4s pulse, always visible until the user updates
    2. One-time `showActionToast()` toast with three actions:
       **Update** (opens Settings → Update flow), **Later** (snoozes 24h
       via `mc_update_remind_after_ts`), **Dismiss** (silences this
       specific commit via `mc_update_dismissed_for`; new commits land a
       fresh toast)
- New `showActionToast(message, actions, opts)` utility — richer toast
  variant with primary/secondary buttons, auto-dismiss, optional
  click-to-close. Used by the update toast; reserved for future similar
  prompts.
- After `performClayruneUpdate()` succeeds, sidebar dot is cleared and
  both localStorage markers are reset so the next update lands cleanly.

**Marketing-site URL routing fix** (`server.py`).
Flask's `<path:filename>` matched `/marketing/v2/` as `filename='v2/'` and
404'd because `send_from_directory` expects a file. `serve_marketing` now
detects directory-style requests and rewrites to `<dir>/index.html`. Same
trick applies to any future subdir under `marketing/`.

## [2026-05-08g] — Marketing site groundwork (warm template + operator-console v2)

Two-track design exploration so the public website can be A/B'd.

- `marketing/index.html` (+ about / docs / download / styles.css) — imported
  unmodified from the Claude-design "Mission Control Design System" Apr 23
  bundle (`14 KB`, distinct from the in-app UI redesign already at
  `docs/design_system_extracted/`). Warm-cream tone (`#f6f0e4` bg + `#e8824a`
  accent), Nunito display + Inter body, hand-drawn brutalism. Source zip
  stays in `~/Downloads/` as the canonical reference; this is the working
  copy. Branding pass (Mission Control → Clayrune) and feature-list
  swap-in deferred — clean baseline first.
- `marketing/v2/index.html` — single-page from-scratch alternative pitched
  in conversation. Operator-console aesthetic: dark base (`#0c0e12`) with
  the same terracotta accent, Inter + JetBrains Mono. Hero is a specific
  scenario ("Tuesday, 3pm. 14 agents running. 3 waiting on you.") + a
  CSS-rendered mockup of the actual dashboard with 6 project tiles in
  mixed states. Differentiator hierarchy from `RESUME_HERE.md` §3:
  3 hero blocks (multi-project / persistence / plan-approval-gate) +
  3 secondary blocks (mobile remote / memory / backlog) + the vs.
  matrix from §5 (Claude CLI / Cursor / Devin / Aider) + a for/not-for
  callout + clean install section.
- `server.py:serve_marketing` — `/marketing/<path:filename>` route plus
  the implicit `/marketing/` handler so users can hit
  `http://localhost:5199/marketing/` (and `/marketing/v2/`) in a browser
  without spinning up a separate http server. Also reachable through
  the Cloudflare tunnel for mobile review. Pure dev convenience; the
  real public site will be served by Cloudflare Pages off `marketing/`
  directly.

## [2026-05-08f] — Mascot rename: Playdo → Claydo

Codebase rename of the in-app helper. Product name "Clayrune" unchanged —
only the mascot character. ~215 occurrences touched across user-facing
strings, code identifiers, CSS classes, HTML IDs, and helper paths.

- `static/index.html` (~120) — modal title, FAB id, CSS classes, JS
  identifiers (`_claydoHistory`, `openClaydo`, `_claydoFormatText`, etc.),
  walkthrough step, localStorage keys (`claydo_opened`, `claydo_fab_pos`).
- `server.py` (~20) — `_claydo_cwd`, `_looks_like_claydo_entry` helpers,
  `/api/guide/{stream,ask}` internal references.
- `docs/USER_GUIDE.md` (10), `installer/index.html` (1), `RESUME_HERE.md` (44).

Migration logic so existing installs upgrade cleanly (no manual steps):

- localStorage one-shot migration in `static/index.html`: reads old
  `playdo_*` keys, writes to `claydo_*` if not already set, deletes the
  old. Idempotent.
- `data/claydo/` (Claude transcript sandbox for the Ask Claydo helper):
  `_claydo_cwd()` renames `data/playdo/ → data/claydo/` if the old dir
  exists, preserving Claude's stored conversation continuity (transcripts
  are keyed off cwd path).

Intentionally untouched:
- `assets/clayrune.png` / `clayrune.ico` — same image is product mark
  AND mascot likeness; one file, two roles.
- `[clayrune:...]` marker prefix — product-namespaced, kept as-is.
- `CHANGELOG.md` history — past entries describe pre-rename work
  accurately for that point in time.

Memory file `naming_playdo_clayrune.md` orphaned (delete-permission
issue); replacement `naming_claydo_clayrune.md` created and indexed in
`MEMORY.md`.

## [2026-05-08e2] — Windows taskbar icon (clayrune.ico + console icon helper)

User report: *"the clayrune icon on taskbar appears as bat file icon."*
Two compounding issues:

1. `assets/clayrune.ico` did not exist. Only `clayrune.png` was checked
   in. `install.ps1` was setting `IconLocation = ...\clayrune.ico` on the
   `.lnk` shortcut, but the file was missing — Windows fell back to the
   `.bat`'s default cmd.exe icon. Generated a multi-resolution
   `assets/clayrune.ico` from the source PNG (16/24/32/48/64/128/256)
   covering all of Windows' icon contexts.
2. Even with the `.lnk` fixed, the *running* cmd window's taskbar entry
   uses cmd.exe's icon, separately from the `.lnk`. New
   `installer/set-console-icon.ps1` sends `WM_SETICON` to the console
   window via Win32 to replace it in-place (both `ICON_SMALL` and
   `ICON_BIG`). The icon is owned by the window so it persists after
   the helper exits. `start.bat` invokes the helper at the top.

Also added `title Clayrune` so the cmd window's title bar (and taskbar
hover) reads "Clayrune" instead of the path to `start.bat`.

## [2026-05-08e] — Working-tree cleanliness so Update Clayrune doesn't get stuck

Two compounding bugs blocked **Update Clayrune** showing "Blocked" right
after a fresh install on every test VM.

1. **`data/claydo/` not gitignored.** Server materializes USER_GUIDE.md as
   `CLAUDE.md` inside `data/claydo/` (formerly `data/playdo/`) every time
   anyone asks Claydo, so Claude auto-loads it as project context. The dir
   wasn't in `.gitignore`, so `git status --porcelain` reported it
   untracked → update endpoint refused to pull → button "Blocked".
   `.gitignore` now lists `data/claydo/`, `data/playdo/` (pre-rename
   compat), and `install-launch.log` / `install.log`.
2. **Installer shell scripts had mode 100644 in the index.** `install.sh`
   STEP 3 ran `chmod +x installer/start.sh` (and the others) on Linux so
   the `.desktop` launcher could execute them. Working tree went 100755,
   git compared against 100644 in the index, reported "modified" — same
   "Blocked" UX. `installer/install.sh`, `installer/start.sh`, and
   `installer/start.command` now stored as 100755. Future `chmod +x` is
   a no-op.

For users with the dirty state at upgrade time: `git pull --ff-only`
applies both fixes to the working tree (mode change is metadata-only).
Documented `git checkout -- installer/start.sh` recovery path for VMs
that hit the modified-content blocker before fix-pull.

## [2026-05-08d] — Vanilla-VM installer validation (Windows 11 Home + Ubuntu 22.04)

End-to-end install testing on freshly-snapshotted VMs caught a long tail of
real-world OS quirks. Two new VMs are kept clean for re-testing per
`CLAUDE.md`. Big arc; subsections by failure surface.

**Deterministic install (no Claude handoff)** —
`installer/install.{ps1,sh}`. Original design piped `install-prompt.md` (24 KB
markdown) into `claude --dangerously-skip-permissions -p`. Newer Claude
models flag that as a prompt-injection attack pattern (*"I won't follow
those instructions because…"*) and refuse, then exit 0 — letting the
wrapper falsely declare success. Every step in the prompt was deterministic
shell anyway (git clone, venv, pip, shortcut, server launch). Both
installers now do the install directly:
- `[STEP 1/5]` git clone; auto-installs git via apt/dnf/pacman/winget on
  Linux/Windows if missing.
- `[STEP 2/5]` Python 3.11+ + venv + pip install. Handles Ubuntu's
  separate `python3-venv` package, Windows App Execution Alias stubs at
  `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`, the Python launcher
  fast path (`py -3.11`).
- `[STEP 3/5]` Launcher: `~/.local/share/applications/clayrune.desktop`
  (Linux), `~/Applications/Clayrune.command` (macOS), Desktop +
  Start Menu `.lnk` shortcuts (Windows).
- `[STEP 4/5]` Launch the server. Linux uses `setsid` to fork into a new
  session so the daemon survives `curl | sh` parent-shell exit (`nohup`
  alone catches only SIGHUP, not the SIGTERM/SIGPIPE from session
  termination). Windows uses `Start-Process -WindowStyle Minimized`.
  30s poll on localhost:5199. Captures stdout/stderr to
  `install-launch.log` (Linux) so server-startup failures leave a
  forensic trail.
- `[STEP 5/5]` Open browser via `xdg-open` / `open` / `Start-Process`.

**Linux: import-time keyring → D-Bus deadlock** (`mc_remote/__init__.py`).
On a fresh Ubuntu desktop pre-first-login (and headless server VMs, WSL
without DBUS_SESSION_BUS_ADDRESS), `import mc_remote` triggered
`tunnel_supervisor.maybe_start()` → `device_keys.load_identity()` →
`keyring.get_password()` → secretstorage trying to talk to
`org.freedesktop.secrets` over D-Bus → blocks indefinitely waiting for
a reply that never comes. server.py never reached `app.run()`. Now the
auto-start runs on a daemon thread so the keyring call can hang forever
without blocking server startup; remote-access stays "not yet started"
until the user clicks Enable.

**Windows: ASCII-only `.ps1` files + UTF-8 BOM unaware reader**
(`installer/install.ps1`, `installer/Clayrune-Nuke.ps1`). Two compounding
bugs caused `iex : Variable reference is not valid. ':' was not followed
by a valid variable name character`:
1. `${lnk}` braces missing on `Write-Host "  WARN could not create $lnk: $_"`
   — `$lnk:` parsed as a drive-qualified variable.
2. Files were UTF-8 sans BOM. PowerShell on Windows reads BOM-less
   scripts as Windows-1252; em-dashes (`—`) and box-drawing (`─`) were
   mangled into byte sequences that sometimes happened to look like
   brace/quote characters to the parser → spurious `Missing closing }`
   errors at unrelated lines. All non-ASCII replaced with ASCII
   equivalents; `Parser.ParseInput` now reports zero errors.

**Windows: `App Execution Alias` Python stubs** (`install.ps1`).
`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` and `python3.exe` are
Microsoft Store redirect stubs, not real Pythons. `Get-Command` finds
them, version-check runs them, the stub prints
`"Python was not found; run without arguments to install from the Microsoft
Store..."` to stderr, and PowerShell's `$ErrorActionPreference = 'Stop'`
turns that into a terminating error → script halts before reaching the
winget Python install fallback. `Find-Python311` now skips paths matching
`\WindowsApps\` and adds a `py -3.12 / -3.11` fast path. ErrorActionPref
relaxed to `Continue` for the install phase since each step does its own
exit-code + Test-Path checks.

**LF/CRLF line endings + cmd.exe parse fragility**
(`.gitattributes` new, `installer/Clayrune-Setup.bat`). Bat files were
checked in with LF (no `.gitattributes` so `text=auto` normalized to LF in
the blob; GitHub raw served LF; Chrome saved LF). cmd.exe silently
misparses LF-only `.bat` files, particularly multi-line `^` continuation
in the powershell.exe call — the cmd window flashed and died before any
`pause` could hold it open. `.gitattributes` now stores `*.bat / *.cmd /
*.ps1` as `-text` (no normalization) with CRLF bytes in the index, and
`*.sh / *.command` as `text eol=lf`. The PowerShell call's `^` continuation
is also collapsed to a single line as belt + suspenders.

**Cache-busting GitHub raw** (`installer/Clayrune-Setup.bat`).
GitHub raw's CDN holds files for several minutes post-push. We were
shipping hotfixes faster than the cache expired, so users running
`Clayrune-Setup.bat` would get stale `install.ps1`. Bat now appends
`?t=$(Get-Date)` to the URL — origin ignores the param but the CDN keys
on the full URL.

**`claude /login` flow when CLI install succeeds but auth missing**
(`installer/Clayrune-Setup.bat`). `[L]` option spawns a separate window
running `claude /login`. Old version used `cmd /c "claude /login"`, but
the spawned cmd inherited the parent .bat's pre-install PATH (which
didn't yet have `%APPDATA%\npm` from the just-completed npm install).
Now spawns via PowerShell which rebuilds `$env:Path` from the registry
on each call, so the freshly-installed `claude.cmd` is visible. Also
adds a final `Read-Host` to keep the window open even on error or
"command not found", so the user always sees what happened.

**Install verification** (`install.ps1`). After Claude's old prompt-based
handoff exits, don't trust the exit code — Claude could have refused or
crashed and exited 0 anyway. Post-Claude check now verifies
`server.py` + `installer/start.bat` exist on disk; if missing, prints a
red FAIL block and exits 2 (which the .bat treats as failure → routes to
the [L]/[R]/[Q] recovery menu instead of showing fake success). Now mostly
moot since the deterministic install replaced the Claude handoff, but
kept as belt + suspenders.

## [2026-05-08a] — Walkthrough + Sample Project + Update button reliability + Windows Claude CLI shim resolution + Playdo command-line-too-long

Multi-thread polish + bug-fix arc surfaced by the same fresh-VM testing.

**Walkthrough fixes** (`static/index.html`).
- Step 10 (Three-Dot Menu) body was a runaway sentence. Now a bulleted
  list of menu entries.
- `<strong>` tags in step bodies were `esc()`-d into literal text. Stop
  escaping — bodies are author-controlled hardcoded HTML.
- Step 13 (Agent Console) was pointing to top-left of viewport because
  `#agent-console` is `.hidden` by default. `onEnter` force-shows it,
  `onLeave` restores; skipped on mobile (covered by bottom tab bar).
- Step 15 (Command Palette) toggled the wrong class — `open` instead of
  the CSS-gated `visible`. Highlighted empty space because the palette
  stayed hidden. Fixed + pre-renders results so the palette has visible
  content.
- cmd-overlay z-index 9999 vs wt-card 2001 caused "two step 14s, second
  blank square" — when the user clicked Next on the wt-card, the click
  was intercepted by the transparent cmd-overlay backdrop, which fired
  its `toggleCommandPalette()` handler and closed the palette. The
  walkthrough didn't know; kept the highlight glowing around an empty
  space. Fix: `pointer-events:none` on the overlay during the
  walkthrough step (with `pointer-events:auto` on the palette itself
  so it stays visible).
- Skip-aware step numbering: `visiblePos / visibleTotal` computed from
  `WT_STEPS.filter(s => !(s.skip && s.skip()))`. On desktop the
  mobile-only bottom-tabs step no longer creates a 13 → 15 gap.

**Sample Project** (`server.py` + `static/index.html`).
Auto-assigns `project_path` to `<auto_workspace_base>/sample-project` so
agent dispatch works from the walkthrough's first interaction. Without
this the user opened the sample, typed a prompt, and got "Set
project_path to enable agent dispatch". New `/api/browse/folders` +
`/api/browse/create_folder` endpoints + a "Browse..." button beside the
Path field opens a folder-picker modal with parent nav,
Workspace/Home shortcuts, and inline "+ Create" for new folders.

**Windows: subprocess can't find `claude.cmd`** (`server.py`).
Root cause: `subprocess.Popen(['claude', ...])` only resolves `.exe` by
default — npm-installed Claude CLI is `claude.cmd` (a batch shim).
`shutil.which` respects PATHEXT and returns the full `.cmd` path, which
subprocess CAN execute. New `_resolve_claude()` helper used at all 22
cmd-list construction sites. Re-resolves per call so a Claude install
AFTER server startup is picked up without restart. Falls back to common
Windows install paths (`%APPDATA%\npm`, `~/.claude/bin`) before giving
up. Fixes both agent dispatch and Ask Playdo on fresh Windows installs.

**Update Clayrune endpoint hangs + races** (`server.py`,
`static/index.html`).
- `git fetch` hung on Windows for 30s waiting for Git Credential Manager
  (GCM) to pop a hidden auth dialog (which never appears because we run
  git with `STARTF_USESHOWWINDOW=SW_HIDE`). `_git()` now sets
  `GIT_TERMINAL_PROMPT=0` + `GCM_INTERACTIVE=Never` in subprocess env;
  fetch timeout dropped 30s → 12s.
- Settings UI hint stuck on "Checking for updates…" because
  `setTimeout(refreshUpdateStatus, 100)` fired BEFORE
  `body.innerHTML = ...` was assigned → `getElementById` returned null
  → helper bailed silently. Moved the call to the end of
  `_renderSettings()`.

**Playdo "command line is too long" on Windows** (`server.py`).
24 KB USER_GUIDE.md piped via `--append-system-prompt` blew past
**cmd.exe's 8191-char limit** (not CreateProcess's 32 KB; cmd.exe wraps
`claude.cmd` calls and has its own smaller cap). Fix: send the question
through stdin via `--input-format stream-json` + a JSONL user message.
Command line drops to ~150 chars regardless of question length. Both
`/api/guide/stream` and `/api/guide/ask` updated.

**Streaming installer progress** (`installer/install.{ps1,sh}`).
`claude -p` only prints the FINAL response — the user saw nothing for
3-5 minutes during the Claude handoff (mostly obsolete now that the
install is deterministic, but the streaming path was kept for the
Claude-CLI-install step). Both installers parse `--output-format
stream-json` and surface assistant text + tool-call indicators in real
time.

## [2026-05-08c] — Claydo state animations (thinking) + sheet-slicing pipeline

First state-driven Claydo animation lands: when the user submits a
question, the FAB and chat-avatar swap from the static idle PNG to
an animated WebP that loops through 4 thinking poses (chin-on-hand
->  eyes-closed -> chart in the code window -> COMPLETED checkmark).
Reverts to idle when the answer is done or errors. Adds two new
tools to make this repeatable for future states.

`tools/sheet-to-frames.sh` (new): slice a 2x2 (or NxM) character
sheet from Gemini / DALL-E / etc. into separate PNG frames.
ffmpeg-based, preserves alpha, autodetects gutter widths so panel
boundaries don't bleed into each other. Output: <name>_frames/frame_N.png.

`tools/frames-to-animation.sh` (new): stitch a sequence of stills
into a looping animated WebP (or GIF / APNG via -f). Takes any
number of frame files, holds each for the configured duration
(default 250ms), loops forever. Hardened for Git Bash on Windows
where ffmpeg.exe needs Windows-format paths even though the shell
uses POSIX paths -- two-step realpath + cygpath -w.

Pipeline: Gemini sheet -> sheet-to-frames -> N PNGs ->
frames-to-animation -> assets/claydo-<state>.webp -> drop into
_CLAYDO_STATE_SRC map.

`static/index.html` wiring:
- `_CLAYDO_STATE_SRC` map: { idle: clayrune.png, thinking: claydo-thinking.webp }
- `_setClaydoState(state)` helper: swaps both the FAB img AND the
  chat-modal avatar (newly given id="claydo-avatar"). Skips DOM
  writes when the basename hasn't changed so animated WebPs don't
  reset their loop on incidental re-renders.
- submitClaydo flips to 'thinking' on entry and back to 'idle' in
  the finally block (covers success, error, and disconnect paths).

Followups (subsequently shipped — see [2026-05-08c2] below):
- White-background fix landed via Python chroma-key (white → transparent
  with soft-edge alpha ramp).
- 4-state set (idle / thinking / working / error) shipped, sourced from
  a Gemini-generated state-variants video instead of the original
  still sheet, giving each state real frame-by-frame motion.

## [2026-05-08c2] — Claydo 4-state animation set (sourced from Gemini video)

Replaced the still-sheet-derived `claydo-thinking.webp` with a 4-state
animation set (`idle`, `thinking`, `working`, `error`) sourced from a
Gemini-generated animated video where each cell of the state-variants
sheet bounces / blinks / changes expression in place. Pipeline:
extract video frames → auto-detect cell layout (4 mascot columns in
the top row) → crop the same cell out of every frame → chroma-key
white to transparent → stitch each cell's 16 frames into its own
animated WebP. Each state file is 126–140 KB, 4-second loop at 250ms
per frame.

Wiring (`static/index.html`):
- `_CLAYDO_STATE_SRC` populated with all 4 states.
- FAB and chat-modal avatar default to `claydo-idle.webp` (instead of
  the still `clayrune.png`) so the mascot feels alive from page load.
- `submitClaydo()` finally block: on `errored=true`, holds
  `claydo-error` for 3s before reverting to idle, so the user notices.

`clayrune.png` preserved for installer / favicon / brand mark; only
the in-app personality moved to the animated WebPs.

## [2026-05-08b] — Video frame extraction for Claude Code sessions

`tools/extract-frames.sh` (new) + `CLAUDE.md` (new at project root).

Claude (this model) doesn't read videos natively — only images, PDFs,
notebooks. When a user attaches an `.mp4` (typically as
`data/uploads/agent_*.mp4` from Mission Control's upload pipeline) we'd
just say "I can't see this." Now there's a one-command path that gets
the model useful frames:

- **`tools/extract-frames.sh <video> [fps] [max_frames]`** — wraps ffmpeg.
  Defaults to 2 fps capped at 24 frames; writes
  `<basename>_frames/frame_001.png ... frame_NNN.png` next to the source.
  When the naive fps would exceed `max_frames`, switches to even
  sampling across the full duration so we get coverage rather than just
  the opening clip. Prints the output paths so the caller can grep.
- Tells the user how to install ffmpeg per OS if it's not on PATH.
- **`CLAUDE.md`** at the repo root: a one-paragraph instruction that
  any Claude Code session running in this repo automatically picks up
  ("when given a video file, run the extractor first"). No more
  "I can't see videos" friction during dev work.

Why this design over alternatives:
- Not server-side: keeping it as a dev-time utility means it doesn't
  depend on MC running, doesn't slow down upload, and works for any
  video the user wants Claude to look at, not just MC uploads.
- Not auto-extracting in the upload handler: most videos uploaded to
  MC are user reference material the agent doesn't need to see; we'd
  burn disk on every reference clip.
- Not video-native models (Gemini / GPT-4o): the frame-extract approach
  preserves the same Claude session, no provider switch, no separate
  context. Costs an ffmpeg invocation per video, which is free.

## [2026-05-07c] — Ask Playdo helper + walkthrough rewrite + USER_GUIDE.md

Three pieces shipped together to close the "new user has no idea what's possible" gap:

### `docs/USER_GUIDE.md` (new)

Comprehensive user-facing reference for every Clayrune surface. ~310 lines, sections:

```
What is Clayrune
Your first 5 minutes
Surfaces overview (Dashboard / Sidebar / Header / Mobile)
Project modal (tabs + 3-dot menu inventory)
Agent dispatch (sessions, plan approval, stop/continue, pop-out)
Hivemind (sidebar surface, workings, stale heuristic, Start from project)
Scheduler (recurring + Run Now + Runs panel + paginated history)
Backlog (per-project + cross-project + GitHub sync)
Memory & Rules (per-project + shared)
Plans / Activity / Run history & transcripts
Mobile remote access (clayrune.io tunnel)
Settings
Keyboard shortcuts
Common tasks (10 recipes — each ends with the [clayrune:...] marker recipe Playdo emits)
Glossary (12 terms)
Troubleshooting (4 known issues with version pointers)
Marker syntax for the assistant (Playdo-only — explains the inline UI control markers)
```

The doc plays double duty: a human reference AND Playdo's system prompt. The Common-tasks section is the load-bearing piece — each recipe ends with the exact `[clayrune:...]` marker, so Playdo highlights the right UI element while it explains.

### Walkthrough rewrite (`WT_STEPS` in `static/index.html`)

Old walkthrough was 19 steps with stale content (Tabs step still listed "Hivemind" as a tab — no longer true; menu steps didn't include Hiveminds + Start Hivemind; no Hivemind sidebar / Scheduler / Run Now / Runs panel coverage). Rewritten to 16 hand-curated steps reflecting current UI:

```
1.  welcome              — opening screen
2.  advanced-picker      — pick power-user features (kept)
3.  sidebar              — Dashboard / Backlog / 🐝 Hivemind / Scheduler / Settings / Shared Rules / Processes
4.  header               — Ctrl+K + agent count + live badge + ? button
5.  toolbar              — Grid/List toggle + filter + density + + New Project
6.  sample-tile          — virtual demo tile (sample project auto-created)
7.  open-modal           — virtual modal demo
8.  tabs                 — Agent / Backlog / Agent Log / Plans / Activity (NO Hivemind here)
9.  agent                — dispatch input + plan approval mention
10. menu                 — three-dot menu: Hiveminds + Start Hivemind + Memory & Rules + Status/Color/Domain/Model + GitHub Sync (mobile: tabs in menu)
11. hivemind-sidebar     — global cross-project Hivemind view (desktop only)
12. scheduler            — Run Now + Runs panel + transcript viewer (desktop only)
13. console              — bottom agent console
14. bottom-tabs          — mobile bottom tab bar (mobile only)
15. cmd-palette          — Ctrl+K
16. ask-playdo           — points at the floating button (NEW)
17. done                 — Settings/cmd-palette/? to re-run; mascot pulse continues until first open
```

(Counts to 17 with the new step — net change vs old: removed 4 granular menu sub-steps + redundant backlog/agent demo steps, added hivemind-sidebar / scheduler / ask-playdo.)

### Ask Playdo — in-app guide assistant (new)

Floating circular button bottom-right of every viewport, always visible. Pulses on first visit until the user opens it once (persisted in `localStorage.playdo_opened`). Mobile sits 70 px above the bottom tab bar.

**Surface** (`static/index.html`):
- Floating FAB: 56 px desktop / 50 px mobile, Playdo mascot icon, accent border.
- `__playdo` modal: chat history + input pinned bottom. Each open is a fresh conversation (no per-session memory in v1, by design — keeps it simple).
- `submitPlaydo()` POSTs to the new endpoint and renders the response.
- `_playdoParseMarkers()` strips `[clayrune:goto/open-modal/highlight]` from the answer + queues the actions.
- `_playdoDispatchActions()` runs them with 350 ms stagger so the user can follow what's happening.
- `_playdoFormatText()` light markdown (bold, inline code, newlines).

**Backend** (`server.py`):
- `POST /api/guide/ask` — single-shot call. Reads `docs/USER_GUIDE.md` as system prompt, runs `claude -p <question> --append-system-prompt <guide> --max-turns 1`, returns `{answer}`. 60 s timeout, 2000-char question cap. No project context, no memory writes, no agent_log entry.
- `GET /assets/<filename>` — new static-file route to serve the mascot icon (and any other repo assets the FE needs).

**Marker protocol** (Playdo emits these inline; FE parses + dispatches):
```
[clayrune:goto view="hivemind"]
[clayrune:open-modal project="abc123"]
[clayrune:highlight selector="#sidebar-item-hivemind" duration=2500]
```
All read-only — no destructive actions in v1. Highlight uses a CSS pulse class (`.clayrune-highlight`) and `scrollIntoView` so the user sees what Playdo means.

**Naming convention** (saved as memory `naming_playdo_clayrune.md`): Playdo = mascot character, Clayrune = product. The marker prefix stays `clayrune:` (product-namespaced); only the user-facing helper is "Ask Playdo."

### Walkthrough trigger fix (was broken since the incognito project was added)

The trigger checked `allProjects.length === 0`, but the auto-created `_incognito` pseudo-project always counts as 1 — so the first-run walkthrough never fired on a fresh install. Fix: filter via `isIncognitoProject` before counting. Surfaced during installer end-to-end testing on a clean WSL Ubuntu where the dashboard rendered empty but no walkthrough kicked in.

### Server restart

Required for the new `/api/guide/ask` and `/assets/...` endpoints. Frontend changes apply on next page load.

### Rollback

- USER_GUIDE.md: just delete `docs/USER_GUIDE.md`. Playdo will return `guide not available` errors but nothing else breaks.
- Walkthrough rewrite: revert the `WT_STEPS` block.
- Ask Playdo: revert `<button id="playdo-fab">` HTML, the `.playdo-*` CSS block, the `// ── Ask Playdo` JS block, the `/api/guide/ask` and `/assets/<path:filename>` server routes.

---

## [2026-05-07b] — Installer scaffold (Claude-driven, browser-only v1)

A new install path designed around Clayrune's own pitch: the user runs one terminal command, Claude CLI does the install. No installer pipeline to build, sign, or maintain across three OSes; cross-platform "for free" because Claude detects the OS, package manager, and Python/Node install paths.

### Architecture

```
user runs:                             ┌─────────────────────────────┐
  curl -sSL clayrune.io/install.sh \   │ install.sh / install.ps1    │
       | sh                            │ (~110 lines each)           │
                                       │  1. verify/install Claude   │
                                       │     CLI if missing          │
                                       │  2. fetch install-prompt.md │
                                       │  3. show 5s abort window    │
                                       │  4. claude --dangerously-   │
                                       │     skip-permissions -p ... │
                                       └────────────┬────────────────┘
                                                    │
                                                    ▼
                                       ┌─────────────────────────────┐
                                       │ Claude executes 6 STEPs:    │
                                       │  1. detect env              │
                                       │  2. clone/pull repo         │
                                       │  3. python venv + deps      │
                                       │  4. node.js (safety net)    │
                                       │  5. create OS launcher      │
                                       │  6. start server + browser  │
                                       └────────────┬────────────────┘
                                                    │
                                                    ▼
                                       Clayrune at localhost:5199
                                       Desktop / Start Menu / Apps
                                       has a clickable shortcut.
```

### New files

`installer/`:
- `install-prompt.md` — the prescriptive Claude prompt, ~200 lines, 6 STEPs. Conservative: does git, pip, package-manager calls, and launches the app. Does NOT modify dotfiles, change system PATH, write outside the install dir, or `sudo` without explanation.
- `install.sh` — macOS/Linux bootstrap.
- `install.ps1` — Windows PowerShell bootstrap.
- `start.sh` — Linux launcher (activates `.venv`, runs `python server.py`, opens browser via `xdg-open`).
- `start.command` — macOS launcher (same role; opens via `open`).
- `start.bat` — Windows launcher (same role; opens via `start http://...`).
- `README.md` — architecture diagram + hosting plan + testing checklist.

`assets/`:
- `clayrune.png` — 1024×1024 RGBA. The Playdo mascot character; doubles as the product / install-shortcut icon. Source for all per-platform icon variants (`.ico`, `.icns`, scaled PNGs); the install prompt generates these on-the-fly with ImageMagick / `sips`.

### Hosting plan

| URL | Source |
|---|---|
| `clayrune.io/install.sh` | `installer/install.sh` |
| `clayrune.io/install.ps1` | `installer/install.ps1` |
| `clayrune.io/install-prompt.md` | `installer/install-prompt.md` |

Domain not yet up. Pre-domain testing uses `raw.githubusercontent.com/.../installer/<file>` with `CLAYRUNE_PROMPT_URL` env var pointing the bootstrap at the right URL.

### Disclosure model

The bootstrap prints the exact `claude --dangerously-skip-permissions -p "<prompt>"` line it's about to execute, with a 5-second Ctrl-C abort window. The install prompt is publicly hosted at `clayrune.io/install-prompt.md` so anyone can audit before authorizing.

### What's not in v1

- **Tauri desktop wrapper** — browser-only for now. The Tauri build path adds a Rust toolchain dependency to step 6 that's not worth the fragility for v1; deferred to a Settings → "install desktop wrapper" follow-up.
- **`.ico` / `.icns` pre-baked** — the install prompt generates these from `clayrune.png` on-the-fly when ImageMagick / `sips` is available. If neither is, the OS launcher uses the default icon (still works). Pre-baking is a polish add.
- **Auto-updater** — not yet. Updates use the same model (`claude "update Clayrune in ~/Clayrune"`); a formal `clayrune.io/update.sh` is a future enhancement.

### Rollback

Delete `installer/` and `assets/clayrune.png`. The existing zip + `install.bat`/`install.sh` source-setup paths in the README continue to work. The Claude-driven install is purely additive.

### Testing checklist

A new install on a clean VM (Windows 11, macOS 14+, Ubuntu 22.04) should:

- [ ] Complete in under 5 minutes with no manual intervention beyond the initial `curl … | sh`
- [ ] End with the browser open at `http://localhost:5199`
- [ ] Place a clickable launcher on Desktop and in the OS app menu
- [ ] Survive a re-run (idempotent — clone becomes pull, deps re-install cleanly)
- [ ] Leave nothing in `/etc`, `/usr`, or system-wide locations
- [ ] Not modify `.bashrc`, `.zshrc`, or system PATH

---

## [2026-05-07] — Scheduled-task UI hang + empty Runs panel

Two related symptoms users hit when the scheduler ran heavily over hours:

1. **Page becomes unresponsive** every so often. Closing & reopening the tab restored it.
2. **No actual run registered in a schedule's "Runs" panel** even after the schedule had clearly fired.

### Symptom 1 — root cause: SSE slot exhaustion via the 15s fallback poll

The 2026-04-27 SSE-slot fix closes the EventSource on `turn_complete` so idle Mode B sessions don't burn one of Chromium's 6 per-origin connection slots. `fetchAgentStatus` was updated to only auto-reconnect for `running`. But the 15s "fallback for missed completions" loop at `static/index.html` (the one that piggybacks `_checkServerRestart`) was still reconnecting for both `running` AND `idle`:

```js
} else if ((ss.status === 'running' || ss.status === 'idle') && !agentEventSources[rh.sessionId]) {
  connectAgentStream(h.projectId, rh.sessionId);
}
```

Server-side, the 30-min stale-session sweep (`server.py:_scheduler_loop` purge block) explicitly skips `running` and `idle` — so idle Mode B sessions accumulate forever (until restart). Each scheduler fire that completes a turn leaves another idle session in `agentHistory`. Within hours, 6+ idle sessions had a live SSE re-opened by the 15s poll → all 6 Chromium slots saturated → `/api/processes`, `/api/config`, `/api/project/<id>/agent_log` etc. queued forever → page hung. Rebuilding `agentHistory` from a fresh page load cleared the slots and the page worked again until the next accumulation.

**Fix** (`static/index.html`): drop the `=== 'idle'` branch from the 15s-poll reconnection block. Mirrors the `fetchAgentStatus` fix. `sendFollowup()` already reopens the stream when the user sends a message.

### Symptom 2 — root cause: trigger info doesn't survive long-lived idle sessions

A scheduler-dispatched session in Mode B finishes its turn → goes idle → process stays alive forever. The stream reader's `finally` block (where `_log_agent_completion` lives) only runs on process exit, so the agent_log entry — the one carrying `trigger_type='schedule'` and `trigger_id=<schedule_id>` — is never written. When the server eventually restarts, the next-startup `_backfill_agent_log_from_transcripts` recreates a row from the Claude transcript on disk, but that helper has no way to recover the trigger info — it's not in the transcript. The `/api/schedule/<id>/runs` filter (`trigger_type==schedule AND trigger_id==X`) then finds nothing, even though the schedule clearly fired.

Verified on `data/projects/day_trading_engulfing_scanner_agent_log.json`: the `3d9ba6f0` schedule had ~10 dispatches in a single day, **0** of which carried `trigger_type='schedule'` in the agent_log; all were `synthesized: True` with empty trigger fields.

**Fix** (`server.py`):

- New `_log_agent_dispatch_pending(session)` helper: at dispatch time, drops a placeholder row into the project's agent_log with `status='in_progress'` and full trigger info (session_id, trigger_type, trigger_id, hivemind ids if present, etc.). `claude_session_id` is empty until completion — Claude assigns it after the first message.
- `_dispatch_agent_internal` calls the helper for non-manual triggers only (manual dispatches don't need correlation and would just double the agent_log write traffic).
- `_log_agent_completion` upserts: looks for an existing row with the same `session_id` and `status=='in_progress'`, removes it, and inserts the finalized entry at the top. Preserves trigger info even though the in-flight row gets replaced.
- New `_reconcile_pending_agent_log_entries()` runs at server startup: any leftover `in_progress` entry is by definition orphaned (no live sessions exist yet), so it gets flipped to `interrupted`. Hooked in `__main__` before the existing transcript backfill so the two helpers don't race.
- Frontend (`static/index.html`): `_runStatusIcon` shows the live accent dot for `in_progress` (matches `running`/`idle`).

**Effect**: a scheduled run shows up in the Runs panel the moment the dispatch happens. Marked `in_progress` while live (accent dot), `completed`/`stopped`/`error` once the session finalizes, or `interrupted` if the server was killed mid-run. Hivemind-orchestrator and hivemind-worker triggers benefit from the same path.

**Rollback**: revert this commit. The existing `manual`-default path in `_log_agent_completion` is unchanged for manual dispatches, so reverting only loses the new pending-row behavior — agent_log shape stays compatible.

**Restart**: server restart required for the backend pieces (helpers + dispatch hook + startup reconcile). Frontend changes apply on next page load.

### Tab strip filter — completed/stopped automated tabs hidden

**Why**: opening a project that had a schedule firing repeatedly showed 8+ near-identical agent tabs ("Run python scripts/he..."). Unusable on mobile, noisy on desktop. Now that scheduled runs surface in the Scheduler's Runs panel + Agent Log, completed automated tabs in the strip are pure noise.

**Files**:
- `server.py` — `agent_status` endpoint now also returns `trigger_type` + `trigger_id` per session.
- `static/index.html` — `fetchAgentStatus` captures the new fields into `agentHistory[].triggerType` + `agentStatusCache[sid].triggerType`. New `getProjectTabSessions(projectId)` filters out `trigger_type ∈ {'schedule', 'hivemind_worker'}` whose status ∈ `{'completed', 'stopped', 'error'}`. `agentPanelHTML` uses this filtered list for the tab strip.

**Behavior**: scheduled / hivemind-worker runs only show as tabs while running. Manual + hivemind-orchestrator tabs unaffected. Completed automated tabs are still reachable via the Scheduler's Runs panel and the Agent Log.

### Runs panel timestamp fix — `started_at` over `ts`

**Why**: after a restart, the Runs panel showed every shutdown-finalized session as "12m ago" because `renderRunRows` was reading `ts` (= finalize time, which becomes uniform for all sessions stopped during shutdown) instead of `started_at` (= dispatch time, which preserves real chronology).

**Fix** (`static/index.html`): `renderRunRows` now picks `r.started_relative || r.started_at || r.ts_relative || r.ts`. Comment explains the pitfall.

### agent_log retention cap (500 entries) + Runs pagination (50 per page)

**Why**: agent_log files grew unbounded — for a schedule firing every 30 min that's ~17k entries/year. Plus the Runs panel was a single scrollable list of up to 200 rows; too much to scan.

**Disk retention** (`server.py`):
- New config `agent_log_max_entries`, default **500**. Set to `0` to disable.
- `_save_agent_log` slices to the most recent N before persisting (newest at index 0). Existing oversized files don't get retroactively trimmed; they shrink the next time anything writes.

**Endpoint pagination** (`server.py`):
- `/api/schedule/<id>/runs` and `/api/hivemind/<id>/runs` now accept `?limit=` (default 50, max 200) and `?offset=` (default 0).
- Response shape changed from a flat array to `{runs, total, offset, limit}` — `total` is the across-all-pages count so the FE can render pagination controls.

**Pagination UI** (`static/index.html`):
- New `renderRunsPagination(total, offset, limit, pageFnTemplate)` helper renders `« ‹ Prev   Page X of Y · N total   Next › »` below the rows. Buttons disabled at bounds.
- `toggleScheduleRuns` delegates to `loadScheduleRunsPage(scheduleId, projectId, offset)`.
- `openHmRunsModal` delegates to `loadHmRunsPage(hivemindId, projectId, role, wsId, offset)`.
- Each panel resets to page 1 on (re-)open.
- New CSS class `.runs-pagination`.

**Restart**: server restart required (response shape change). Frontend on next page load.

---

## [2026-05-06] — Hivemind global surface, trigger-aware run history, sizeAgentChat fix

Three threads of work in one session.

### Hivemind: global cross-project surface (replaces per-project tab)

**Why**: Hivemind was tucked into a per-project modal tab. The cross-project comms / orchestration story is the differentiator that justifies a first-class surface, parallel to Backlog and Scheduler in the sidebar — not a tab inside a single project.

**`static/index.html`**:

- **Sidebar entry "Hivemind"** between Backlog and Scheduler (🐝 icon). `sidebarNav('hivemind')` → `openAllHiveminds()` → synthetic modal `__all_hivemind`.
- **Cross-project list view** (`renderAllHiveminds`): status filter (Active / Paused / **Stale** / Completed / All), project filter (auto-populated from data), search box, count, **+ New Hivemind** action.
- **Card per hivemind**: status pill, short ID hash (`#abc12345` so visually-identical titles in the same project are distinguishable), title, project badge (clickable → filter), updated-relative, pause/stop/resume controls. Below: a **planner/worker tree mini-viz** — orchestrator badge → trunk → row of workstream chips colored by status (✓ done, ● active, ⏳ blocked, ✖ failed, ○ pending). Stats row: workstreams / done / active / findings.
- Click a card → existing `openHivemindDashboard()` detail modal (left untouched in this pass).
- **Mobile bottom-tab bar**: Settings slot replaced with Hivemind. Settings remains reachable via the avatar tap on the mobile app bar (`mc-avatar-btn` already routed there).
- **Per-project Hivemind tab REMOVED** from the modal tab strip (`validTabs` no longer includes `'hivemind'`; stale `modalActiveTab` values auto-migrate to `'agent'`). Replaced with two entries in the project's 3-dot menu, separated by a divider:
  - **🐝 Hiveminds** → opens global view filtered to this project (status: All).
  - **✨ Start Hivemind** → switches to Agent tab, opens a fresh session, **auto-dispatches** the setup prompt so the user lands directly in an active conversation (not a populated form). Earlier draft just filled the textarea; users mistook it for a misdirected new-session screen.

**Stale heuristic**:

- Frontend `_hmEffectiveStatus(hm)` in `static/index.html`: if `status === 'active' || 'paused'` and `updated_at > 24h` ago, render as **stale** (grey badge, separate filter option, **▶ Restart** control, tooltip explains: "Marked stale because no activity for >24h"). Keeps underlying status intact in the data — only display + filter behavior changes.
- Server-side reconciliation (`server.py:_hm_reconcile_stale_on_startup`): one-shot pass at startup that transitions any `active` hivemind whose `updated_at > _HM_STALE_HOURS (24)` to `status='stale'` in the manifest on disk. Only touches `active` — `paused` is intentional idle. Prints `[hivemind-reconcile] marked N long-active hivemind(s) as 'stale' (>24h idle)` if any transitions happen.

### Trigger-aware run history (scheduler + hivemind)

**Why**: scheduled / hivemind-spawned runs were invisible after restart — the live conversation context disappeared, the agent log entries weren't tagged with what triggered them, and there was no surface that said "show me the last 10 runs of *this* schedule" or "what did each worker actually do?". Server log persisted but wasn't navigable.

**`server.py`**:

- **Two new fields on every `agent_log` entry** (`_log_agent_completion`): `trigger_type` (`manual` | `schedule` | `hivemind_orchestrator` | `hivemind_worker`) and `trigger_id` (schedule_id, hivemind_id, or workstream_id depending on type). Default `'manual'`/`''` for direct user dispatch. Old entries continue to work (defaults applied at read time).
- **`_dispatch_agent_internal` extended** with `trigger_type='manual'`, `trigger_id=''` kwargs that flow into the session dict; both Mode A and Mode B are stamped.
- **Scheduler (`_scheduler_loop`)** now passes `trigger_type='schedule'`, `trigger_id=sched['id']` on every fire.
- **Hivemind orchestrator + worker spawn paths** stamp `trigger_type='hivemind_orchestrator'`/`'hivemind_worker'` directly on the inline session dicts (those paths construct sessions inline, not via `_dispatch_agent_internal`).
- **New endpoints**:
  - `GET /api/schedule/<id>/runs?limit=` — agent_log entries where `trigger_type='schedule'` and `trigger_id=<id>`. Resolves project via the schedule record.
  - `GET /api/hivemind/<id>/runs?role=&ws_id=&limit=` — falls back to existing `hivemind_id`/`hivemind_ws_id`/`hivemind_role` fields, so historical entries (predating this session) work too. `role=orchestrator` / `role=worker` filter by role; `ws_id` scopes to a specific workstream.
  - `GET /api/project/<pid>/transcript/<csid>` — read-only parsed transcript (user msgs + assistant text + `[tool: X]` markers) for the read-only viewer. Uses new helper `_parse_transcript_messages` + `_find_transcript_file` (resolves Claude Code's `~/.claude/projects/<encoded-cwd>/<csid>.jsonl` with both `_`→`-` encoding variants).
- **`POST /api/schedule/<id>/run-now`** — manually fire a schedule's task without disturbing its cadence. Updates `last_run` for visual feedback, leaves `next_run`/`enabled` alone (it's an *extra* dispatch on top of the normal cycle). Stamps `trigger_type='schedule'` so the resulting run shows up in the schedule's Runs panel.

**`static/index.html`**:

- **Shared transcript viewer modal** (`openTranscriptViewer`, `__transcript_<csid>` synthetic id): renders user/assistant blocks with role labels and inline `[tool: X]` markers. Cached per csid in `_transcriptCache`.
- **Shared row renderer** (`renderRunRows`): timestamp · status icon · summary, click → transcript viewer.
- **Scheduler card "Runs" button** + inline expanding panel (`toggleScheduleRuns`). Panel sits below the card with surface2 background.
- **Scheduler "▶ Run Now" button** at the far right of the action row (kept apart from "Runs" by Edit + Del to avoid label collision). Also available in the Edit form (only when editing existing).
- **Hivemind detail dashboard**:
  - Workstream detail view: **Runs** button next to the workstream title → opens `__hm_runs_<hivemind>_worker_<ws>` modal listing runs for that workstream.
  - Overview view: **Orchestrator Runs** button in the actions row → opens orchestrator-only runs modal.
- New CSS for `.run-row`, `.transcript-msg`, `.transcript-tool`, `.runs-panel`, `.runs-empty`.

### Fix: sizeAgentChat over-allocation cut Send button bottom border

**Symptom**: Send button's bottom green border was clipped by ~6 px after some refresh cycles. Diagnostic showed `agent-output: h=521` when it should have been 500 — over-allocated by exactly 21 px, matching agent-chat's `scrollH − clientH` overflow.

**Cause**: `sizeAgentChat` set `agent-output` to `flex: 0 0 <X>px !important` based on `desiredOutH = chatHeight − sepH − inputH`. `inputH` came from `chatInputEl.offsetHeight`, which returned the **squashed** value left over from the previous over-allocation (47 instead of natural 68). Each refresh fed back the smaller value → desiredOutH grew by 21 px → chat-input got squashed *more* → Send button's bottom border drifted past `agent-chat`'s `overflow: hidden` boundary. Classic measurement feedback loop.

**Fix** (`static/index.html:sizeAgentChat`):

1. Before measuring, `removeProperty` on output's `height` / `max-height` / `flex` / `min-height` so the natural-flex layout is what gets measured.
2. Compute `inputH` as `Math.max(offsetHeight, scrollHeight, rowOffsetHeight + computedPadding, 80)`. Three independent signals plus an 80 px safety floor (well above natural ~68 px). Pathological measurement can no longer over-allocate the output area.

### Rollback

- **Hivemind elevation**: revert the `static/index.html` block search-anchored at `// ── Cross-project Hivemind view ──` plus the sidebar HTML entry, the `sidebarNav('hivemind')` branch, and the `_hm_reconcile_stale_on_startup` call in `server.py`'s `__main__`. Re-add the modal-tab `<div>Hivemind</div>` line and the `<div data-tab="hivemind">` panel in `modalContentHTML`.
- **Run history**: drop `trigger_type`/`trigger_id` from `_log_agent_completion`, the kwargs from `_dispatch_agent_internal`, the scheduler/hivemind dispatch sites' stamping, and the four new endpoints (`schedule_runs`, `hivemind_runs`, `get_project_transcript`, `schedule_run_now`). Frontend: revert the Runs/Run Now buttons in `refreshScheduleList`, the buttons in `buildWsDetailHTML`/`buildHmOverviewHTML`, and the shared `openTranscriptViewer`/`renderRunRows`/`openHmRunsModal`/`runScheduleNow`/`toggleScheduleRuns` block.
- **sizeAgentChat fix**: revert the `removeProperty` block + replace the multi-signal `inputH` calc with the original `chatInputEl.offsetHeight`. Note: doing this revives the Send-button-clipping feedback loop.

---

## [2026-05-05] — Sticky modals, conversation drag fix, and remote server restart

Three threads of work shipped together (commit `5ce48eb`):

### Modal persistence (`static/index.html`)

- **`mc_open_modals` snapshot in `localStorage`**: stores `[{projectId, left, top, minimized}]` for every open project modal. Saved on open / close / minimize / restore / drag-end / `beforeunload`. Restored on page load right after `fetchProjects()` resolves. Skipped on mobile (full-screen modals + bottom-tab nav assume a clean slate). Filters out transient synthetic modals (`__terminal_*`, `__hivemind_*`, `__settings`, etc.).
- **`mc_modal_prefs` in `localStorage`**: per-project `{width, height, zoom}`, applied every time the modal opens. Captured by the existing `ResizeObserver` on `.modal-content` (catches corner-drag + pinch-resize), the `Ctrl+wheel` zoom handler, and pinch-zoom. Debounced 250 ms; flushed on `beforeunload` and before any in-app restart so the snapshot survives.
- Open-project modal helper extended with optional `restoreState` arg so startup restore (per-instance position) and Settings-sidebar reopen (centered, prefs only) can share the same code path.

### Conversation input drag (`static/index.html`)

- Dragging the agent chat input separator now resizes the output area in lock-step with the textarea instead of leaving it frozen and snapping a few seconds later (the snap was the deferred flex-layout finally catching up on the next periodic refresh).
- `sizeAgentChat` now drives `agent-output` height **explicitly** via `style.setProperty('height', …, 'important')` + matching `flex: 0 0 <h>px`. CSS `flex: 1` alone wasn't reliably reflowing when the textarea's inline `style.height` changed; `!important` beats whatever cached layout the browser still had from when the textarea was its smaller size.
- `separatorDragMove` now (a) updates `textareaHeights[id]` in lock-step with the live drag so any refresh that fires mid-drag restores the in-progress height instead of the default `rows="1"`, and (b) calls `sizeAgentChat` on every step so the layout follows the drag instead of waiting for the next periodic refresh.
- The "scroll position jumps up" bug: tightened the resize re-pin tolerance to ≤8 px (vs. the lazy 80 px window `_isAgentOutputPinned` uses for new-line auto-scroll, which is left untouched). Without this, a user reading 30–70 px above the bottom got snapped to the absolute bottom every refresh, which they perceived as the text jumping up by the gap they had scrolled.

### Remote server restart (`server.py`, `static/index.html`)

The user can now restart the Mission Control Python process from any open dashboard, including mobile via the `clayrune.io` tunnel. Designed for the "I just deployed a fix and I'm on my phone — let me restart" workflow.

**Endpoints** (`server.py`, just before `if __name__ == '__main__'`):

- `GET /api/system/restart/status` — returns `{active_sessions: [...], active_hiveminds: [...]}` with project names and task previews. Powers the warning modal so the user sees what would be killed before confirming.
- `POST /api/system/restart` body `{confirmed: true, force?: bool}`:
  - 400 if not confirmed.
  - 429 if a restart was triggered in the last 30 s (rate limit).
  - **409 with the live blocker list** if anything is still active and `force` isn't set — closes the GET → POST race window where a cron or hivemind could spawn a fresh session between the user seeing the modal and clicking confirm.
  - 202 + audit log + async restart thread otherwise.
- `GET /api/system/heartbeat` — `{started_at, pid, uptime_seconds}`. Cheap probe (no disk/DB). Dashboards compare `started_at` against their first-seen value to detect a restart.

**Restart thread** (`_perform_server_restart_async`):

1. Sleeps 400 ms so the 202 actually reaches the client.
2. Calls `_stop_all_sessions_for_restart` → graceful `_stop_session` (Mode B closes stdin, Mode A flips status), then `_kill_proc_background` (existing tree-kill helper).
3. Waits up to 3 s for children to die.
4. Appends to `data/restart_log.json` (capped at 200 entries; gitignored).
5. **`subprocess.Popen([sys.executable] + sys.argv, close_fds=True, …)`** then `os._exit(0)`.

**Why Popen instead of `os.execv`** — *the non-obvious lesson of this session.* On Windows, `os.execv` is implemented as spawn-new-then-exit-old AND the new process inherits open file handles. Worse, every child process we spawned (Mode B agents, terminal sessions) **also** held the listening socket FD via inheritance — so port 5199 stayed bound until every descendant died, well past the 15 s the new instance was willing to wait. Symptom: the new process bailed in `_check_port_conflict` saying `Held by PID(s): X (claude.exe)`. `subprocess.Popen([…], close_fds=True)` starts the new instance with a clean handle table, sidestepping the whole inheritance chain. POSIX uses `start_new_session=True`; Windows uses `CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE` so the new server gets a visible terminal window the user expects.

**Restart-aware port-conflict bypass** (`_check_port_conflict`):

- Before exec/spawn the parent sets `MC_RESTART_FROM_PID = <our_pid>` in env.
- The new instance recognizes the marker and polls the port every 300 ms for up to 15 s, waiting for the dying parent to release it. Only after that timeout does it fall through to the hard-abort path.
- Marker is cleared on successful bind so a normal subsequent launch (no in-progress restart) behaves like before.
- Conflict-message diagnostics enriched on Windows: now lists image name next to PID (`Held by PID(s): 42836 (claude.exe)`). POSIX equivalents (`ss -lntp`, `lsof -i`) noted as TODOs in code.

**Cross-dashboard restart detection** (`static/index.html`):

- Every dashboard probes `/api/system/heartbeat` (`_checkServerRestart`) on SSE drop AND in the existing 15 s fallback poll. If `started_at` changed since first-seen, calls `_handleServerRestart` which reuses the same `showRestartingOverlay` flow as the device that triggered the restart.
- Without this, dashboards that didn't trigger the restart would see SSE drop, retry 3×, mark sessions `'error'`, and the project tile turns "Blocked" (via `friendlyStatus` mapping `c==='error'` → `'stuck'`) until manual refresh.
- The SSE error handler now probes the heartbeat **before** incrementing the retry counter. If a restart is detected, skips the retry/error cascade entirely and reloads instead of marking the session `'error'`.

**UI** (`static/index.html`):

- Settings → new **"Server"** section with a red **"Restart server"** button.
- `openRestartConfirmation` fetches the live blocker list, builds a modal showing each active project + task preview + hivemind worker counts. Two-button confirm: "Cancel" / "Stop all and restart" (or just "Restart" if nothing's active).
- `performRestart(force)` POSTs and handles 202 / 409 / 429. On 409 (race) the modal auto-reopens with refreshed state.
- `showRestartingOverlay` flushes `mc_open_modals` + `mc_modal_prefs` synchronously before the page reload, draws a backdrop spinner overlay, polls `/api/projects` every 1 s starting at +1.2 s, reloads when it 200s. The modal-restore code then brings back open conversations and their positions/zoom from `localStorage`.

**Auth model**: same as the rest of the app — localhost is unauthenticated by design (your machine), tunneled requests have already passed CF Access OTP. No new auth surface introduced.

### System-prompt awareness (`server.py:_clayrune_universal_capabilities`)

- New **Scheduler** entry: every agent now sees Clayrune's local `/api/schedules` endpoints in its preamble, framed as the long-term option next to the Anthropic `/schedule` skill (short, in-session). Picker rule: "if it should still fire after this conversation ends, use Clayrune's local scheduler; if it's a tight loop tied to current work, use `/schedule`."
- New **API discovery** hint: tells agents to grep `server.py` for `@app.route` instead of guessing endpoint names like `/api/cron` or `/api/jobs`. Triggered by an observed failure mode where an agent probed five wrong paths before finding the real one.

### Rollback

The four pieces are independent enough to revert separately:

- **Modal persistence**: clear `mc_open_modals` + `mc_modal_prefs` from `localStorage`; remove the helper block in `static/index.html` (search for `_loadModalPrefs`).
- **Drag fix**: revert the `sizeAgentChat` block (search for `setProperty('height'`) and the changes inside `separatorDragMove` (live `sizeAgentChat` call + cache write).
- **Remote restart**: remove the four endpoints (`/api/system/restart{,/status}`, `/api/system/heartbeat`) and helpers from `server.py`, plus the Settings "Server" section + restart-related JS in `static/index.html`. Optionally also remove the `_check_port_conflict` `MC_RESTART_FROM_PID` branch.
- **System-prompt awareness**: remove the two new entries from `_clayrune_universal_capabilities`.

---

## [2026-05-04] — Diagram polish: Excalidraw bridge restored, de-sketched, orphan-error sweep

Iterative tightening of the Mermaid → Excalidraw rendering pipeline introduced
on 2026-05-03. Commit `44772f2` had brought in the Excalidraw bridge for a
polished aesthetic; commit `3a088cc` reverted it after sequence-diagram
rendering bugs (strikethrough lifelines, literal `<br/>` text, mono-color
output). This session restores the bridge but pivots away from the hand-drawn
look that made the diagrams read as childish.

### Diagram rendering (`static/index.html`)

- **Restore Excalidraw bridge** (commit `b63ec46`, revert of `3a088cc`). Keeps
  the Excalidraw layout + element model — but with the changes below, no
  longer with the Roughjs sketch effect.
- **Mermaid `look: 'handDrawn'` → `'classic'`** (line ~25). Fallback path
  (sequence/state diagrams that Excalidraw can't parse) now renders with
  clean strokes instead of wobbly Roughjs lines.
- **Excalidraw element post-processor** in `_renderViaExcalidraw` (line ~6967):
  after `convertToExcalidrawElements(skeleton)`, every element is mutated
  before the SVG export:
  - `roughness = 0` — straight strokes, no sketch wobble
  - `fillStyle = 'solid'` — kills hachure / cross-hatch fills
  - `strokeStyle = 'solid'` (preserving any explicit `dashed` / `dotted`
    intent the source author set)
  - `fontFamily = 2` (Helvetica) — replaces Excalidraw's default Virgil
    "hand-drawn" font on text + arrow labels. Without this, diagrams still
    read as whiteboard scribbles even with clean strokes.
- **Orphan "Syntax error" sweep** — Mermaid v11 (and `parseMermaidToExcalidraw`
  which uses Mermaid's parser internally) injects an error SVG into `<body>`
  when its parser fails, and never cleans it up. They accumulate on the page
  over the lifetime of the tab as visible toast-like cards.
  - New helper `_sweepOrphanMermaidNodes()` (line ~6961). Selector matches
    both `<svg>` and `<div>` direct children of `<body>` whose id starts with
    `mermaid-` / `dmermaid-`, gated by a textContent regex
    `/Syntax error|mermaid version/i`. The textContent gate is critical:
    Mermaid v11 keeps its own *working* sandbox div on body with the same
    id prefix and reuses it across renders. Removing that crashes the next
    render with `Cannot read properties of null (reading 'firstChild')`.
    The sandbox is empty between renders, so the error-text gate never
    matches it.
  - Called before + after every render attempt (Excalidraw and Mermaid
    paths), plus a one-shot sweep at lib-load to clear pre-existing orphans.

### User-facing color guidance

- `classDef` color rules in flowcharts are often stripped by the Excalidraw
  bridge — the polished look comes at the cost of some Mermaid styling
  expressiveness. Inline `style <node> fill:#...,stroke:#...,color:#...` per
  node is the reliable path; classDef should be treated as best-effort.
- Color-by-role convention used in this session's example diagrams:
  - Cream + brand-orange = your own services / compute
  - Tan + burnt-orange = caching layer
  - Sage green = persistent storage / data layer
  - Purple = async / messaging
  - Blue = observability
  - Red = secrets / crypto
  - Pale slate = external systems

### Limitations (intentional, not in scope)

- AWS architecture icons via Mermaid's `architecture-beta` diagram type are
  not enabled. Would require `mermaid.registerIconPacks([...])` wiring + an
  Iconify pack import + a bypass of the Excalidraw bridge for that diagram
  type (Excalidraw can't parse `architecture-beta`). Deferred.
- Mobile rendering of the Excalidraw bridge has known visual issues; the
  fix above is desktop-first. Tracked separately.

## [2026-05-01] — Rebrand to Clayrune + operator dashboards + scheduler timezone fix

Multi-day milestone session. Public-alpha gate is unblocked, ops surfaces are
in place, branding is unified.

### Major

- **Rebrand: Mission Control → Clayrune** (Model B, full product rename). All
  user-visible surfaces flip:
  - Window/page `<title>`, sidebar logo (orange tile + serif "C" + "CLAYRUNE"
    wordmark — both hardcoded `#e8824a` so they're independent of the user's
    selectable theme accent)
  - Settings → Remote Access labels, walkthrough copy, sample-project text
  - `/v1/connect` page header + footer + redirect toast
  - `/v1/admin` operator-dashboard title + footer
  - `/api/mc-callback` page (success + error templates)
  - `/_mc/name-device` page footer
  - OpenAPI title (`Clayrune — control plane`)
  - Tauri window title
  - CF Access app name template (`Clayrune - <hostname>` — visible in CF
    dashboard + OTP emails)
  - Attestation error messages (`Clayrune version not registered`, etc.)
  - Cloud Monitoring dashboard JSON `displayName`
  - Favicon SVG (white "C" on orange tile)
  - Mobile app-bar avatar fallback ("C")

  Backend identifiers explicitly kept as Mission Control:
  - Python packages `mc_remote`, `mc_remote_iface`, `mc_tunnel`
  - Env-var prefix `MC_*`
  - Windows Credential Manager namespace `mission-control-remote`
  - Tauri `productName` + bundle identifier `com.missioncontrol.desktop`
  - GitHub repo, Cloud Run service name, GCP project name
  - Agent system prompts mentioning "managed by Mission Control"
    (`server.py:1417-1498`)

- **Operator dashboard** at `https://api.clayrune.io/v1/admin` (`routes_admin.py`):
  - Self-contained HTML page; Firebase Google signin gated by email allowlist
    in `MC_CP_ADMIN_EMAILS` env
  - Aggregates Firestore `users/` + `devices/` in a single scan
  - Summary cards (users / devices / online now / as-of) + per-user expandable
    section with device tables, online/offline pills, tier + bandwidth use
  - Endpoint `GET /v1/admin/data` returns JSON; HTML page consumes it
  - Wired into `main.py` (was commented-out skeleton)

- **Cloud Monitoring dashboard** for the control plane:
  - 8-tile mosaic: request rate (stacked by 2xx/4xx/5xx), error rate
    (4xx + 5xx), latency p50/p95/p99, active container instances, CPU
    utilization, memory utilization, Firestore reads, Firestore writes
  - Reproducible JSON config at `control_plane/monitoring/control_plane_dashboard.json`;
    re-create via `gcloud monitoring dashboards create --config-from-file=...`
  - Live at https://console.cloud.google.com/monitoring/dashboards/builder/76f6aa3d-607a-4646-a043-192faf6bb527?project=clayrune

### Bug fixes

- **Scheduler timezone fix** (`server.py:_compute_next_run`):
  - Previously, daily-schedule "time" field and cron expressions were
    interpreted as UTC time-of-day. User entered "09:00" intending wall-clock
    PT, schedule fired at 02:00 PT.
  - Now uses `datetime.now().astimezone()` (local-aware) as the time-of-day
    reference for `daily` + `cron`. `interval` and `once` paths are
    tz-agnostic so unchanged. Returned `next_run` is still UTC ISO+Z so the
    scheduler loop and frontend `new Date(...)` continue working.
  - Frontend form labels show host TZ abbreviation (e.g. "Time (PDT)") via
    `Intl.DateTimeFormat`-derived short name; schedule list descriptions
    append the same.
  - Migration: pre-fix daily/cron schedules will fire at the literal time the
    user originally typed (their original intent). Re-saving recomputes
    `next_run` correctly.

- **Device-token auth on `/api/remote/{devices,sessions,…}`** (commit `492309a`):
  - After Firebase Auth shipped, `/api/remote/devices` and `/sessions` still
    required `MC_REMOTE_DEV_EMAIL` env var to authenticate to the CP. Settings
    panel showed "Couldn't load devices: MC_REMOTE_DEV_EMAIL not set" after a
    successful Firebase enrollment.
  - CP `_resolve_user()` now accepts a third auth path: `X-MC-Device-Auth:
    <device_id>:<enrollment_token>`. Header verifies the device row exists,
    isn't revoked, and the enrollment_token hash matches; resolves to the
    owner's user_id from the device row.
  - MC client `_auth_headers()` picks device-token from keystore; falls back
    to email if keystore is empty. New helper `_cp_auth_kwargs()` in
    `server.py` encapsulates the fallback chain. All four `/api/remote/*` call
    sites + the auto-cleanup loop now use it.

## [2026-04-30] — Firebase Auth + custom domain + CI/CD

### Major

- **Browser-mediated enrollment via Firebase Auth** — replaces the
  `MC_CP_DEV_AUTH=1 + X-Dev-User-Email` shim with a real Google-signin flow.
  - New CP endpoints: `GET /v1/connect` (HTML signin page with Firebase Web
    SDK), `POST /v1/signin/start` (registers enrollment_intent), `POST /v1/signin/complete`
    (verifies Firebase ID token + drives provisioning).
  - `_verify_firebase_token()` uses `firebase_admin.auth.verify_id_token()`;
    lazy SDK init reads `FB_PROJECT_ID` env so token verification matches the
    Firebase project (`clayrune-49e57`) which is distinct from the GCP project
    (`clayrune`).
  - Extracted `_do_enroll_after_auth()` from `/v1/enroll` so the new flow
    reuses the same CF + Firestore choreography.
  - MC client: `connect_url()` builds `<cp>/v1/connect?pub=...&nonce=...&callback=...`;
    callback flow unchanged.
  - End-to-end verified: Disconnect → click Enable → Google signin → username
    pick → redirect → green Online.

- **Custom domain `api.clayrune.io`** — Cloud Run domain mapping with
  Google-managed cert. CF Origin Rules path was abandoned (Host-header
  override is paid-plan-only on CF); DNS-only CNAME → `ghs.googlehosted.com`
  with no CF proxy works on free tiers both sides.

- **CI/CD via GitHub Actions** — push-to-main on `control_plane/**` triggers
  Cloud Build + Cloud Run deploy via Workload Identity Federation (no JSON
  keys committed). After hitting Cloud Build's source-upload bucket legacy-IAM
  wall, the workflow uses `docker build` directly on the runner instead.
  Service account `ci-control-plane@clayrune.iam.gserviceaccount.com` with
  least-privilege roles. WIF pool restricted to `ronle/*` repos via attribute
  condition.

### Polish

- Added CP-warmup ping at MC startup (`_warmup_control_plane` daemon) to mask
  Cloud Run cold-start on first user interaction.
- New admin CLI `python -m control_plane.force_cleanup --username X` for
  emergency state wipes (CF + Firestore for a given username, `--dry-run`,
  `--keep-username`).
- `_force_cleanup_for_hostname()` confirmed collision-only (was already, but
  doc was stale).

## [2026-04-29] — Device naming + auto-cleanup loop

### Major

- **Per-device naming flow** — when a phone/browser hits `<user>.clayrune.io`
  after CF Access OTP, MC's `before_request` hook detects the CF tunnel
  headers, extracts the session nonce from the JWT, and if unlabeled redirects
  to `/_mc/name-device`. Self-contained HTML form with UA-derived suggestion
  chips ("My iPhone", "My Phone", "Work Laptop"…). Labels stored at
  `data/session_labels.json` keyed by CF Access nonce.
- **Retroactive renaming** — clickable "Name this session…" link on each
  unnamed row + small "rename" link on labeled rows.
- **Auto-cleanup loop** (`_session_label_enforcer_loop`, 60 s interval) tries
  strict per-session revoke for unnamed sessions older than 10 min. Aborts
  pass on first `per_session_unsupported` so named sessions are never nuked.
  Verified: CF doesn't expose per-session revoke for our token (4 API shapes
  return 405); loop fails safe and surfaces a "per-session revoke unsupported
  by CF" hint in the UI. "Sign out everywhere" remains the working tear-down.
- **CP `/v1/sessions/{id}/revoke?strict=1`** mode — returns 503 instead of
  falling back to revoke-all when per-session is unsupported. Tries 4 known
  CF API shapes (POST/DELETE × full-name/nonce-only) before giving up.

## [2026-04-28d] — Revert [2026-04-28c]: restore live auto-pin during agent streaming

User tried `[2026-04-28c]`'s "stay put while the agent streams" behavior and didn't like it. Reverted `appendAgentLine` to its prior policy: when the user is pinned (within 80 px of bottom), every new agent line snaps `scrollTop` to `scrollHeight`. The "scroll up to read older content" guard from `[2026-04-27c]` is still in place — only the `wasPinned` branch is unchanged. No code from `[2026-04-28b]` (the chat-drift fix in `sizeAgentChat`) was touched.

## [2026-04-28b] — Stop the agent chat from drifting up a few lines every poll

### Symptom
Every few seconds the conversation window jumped a few lines above where it had been. Worse when the user had dragged the chat-input separator to make the textarea taller.

### Root cause
The agent-panel header (`<div style="display:flex;...flex-wrap:wrap">` containing the status dot + label + Stop + token badge + activity ticker + plan-file btn + popout) is wrap-enabled. The `token-badge` text changes every second as elapsed time updates ("1m 30s" → "1m 31s") — when its rendered width crosses the wrap threshold by even a pixel, the row flips between 1-line and 2-line layout, changing the header height by ~24 px.

`sizeAgentChat` runs on every `refreshModalById` call (status polling tick, focus, etc.) and computes `used = Σ panel.children.offsetHeight (excluding chat) + paddings`. When the header flipped layout, `used` shifted by 24 px, `chat.style.height = available - used - 8` shifted, the `.agent-output`'s `clientHeight` (`flex: 1` inside chat) shifted, and the auto-scroll branch — `if (wasPinned) out.scrollTop = out.scrollHeight` — re-snapped to bottom on a smaller/larger viewport. Result: the visible content appeared to drift up or down by a few lines on every poll. With the textarea dragged taller, output was smaller, so the same 24 px shift was a bigger fraction of view → more obvious.

### Fix (`static/index.html`, `sizeAgentChat`)
- Guard the height write: only set `chat.style.height` if the new value differs from the existing one by more than 4 px. Steady-state polls become no-ops; legitimate resizes still apply.
- Auto-scroll only fires when the chat height actually changed (or on fresh mount). The `requestAnimationFrame` follow-up is also gated on fresh mount, since the post-frame re-snap was masking the same drift.

### What this does NOT fix
- The header itself can still wrap. If you want to *prevent* the wrap entirely, set `flex-wrap: nowrap` on the status bar or hide the activity ticker on narrow modals. Out of scope here — the goal was just to stop the wrap from cascading into the chat scroll.
- `appendAgentLine` is unchanged. New SSE output still pins the user to bottom (when they're already there). Only the polling-driven re-pin is gone.

### Rollback
Revert `sizeAgentChat`'s tail block back to:
```js
chat.style.height = chatHeight + 'px';
if (out && wasPinned) {
  out.scrollTop = out.scrollHeight;
  requestAnimationFrame(() => {
    out.scrollTop = out.scrollHeight;
    if (freshMount && out.scrollHeight > 0) out.dataset.scrollInitialized = '1';
  });
}
```

## [2026-04-28] — Backfill agent_log from Claude transcripts on startup

### Symptom
Sessions that ran for hours via the MC interface but were still mid-flight when the server was restarted disappeared from the Agent Log tab. The Claude transcript on disk was intact, but Mission Control had no record of the session because `_log_agent_completion()` only runs from the stream reader's `finally` block — and that block never fires when the Python process is killed before the agent ends. The user observed this after talking to MC overnight on mobile, then restarting the desktop app the next morning: the session was gone from Agent Log even though the conversation transcript still existed.

### Why it happened
MC's `<pid>_agent_log.json` is the only data source for the Agent Log tab. It is written exclusively by `_log_agent_completion()`, called from the Mode A and Mode B stream readers when their `proc.wait()` returns. A killed server process kills the reader threads before they reach that call. The Claude transcript in `~/.claude/projects/<encoded-cwd>/<csid>.jsonl` survives because Claude Code writes line-by-line, but MC's "I dispatched this" record was strictly in-memory until finalization. This also blocks `_revive_from_agent_log` (added in `[2026-04-27e]`) from finding the session: with no log entry, there's nothing to revive from.

### Fix (`server.py`)
- **New `_backfill_agent_log_from_transcripts(project_id, project)`** (placed right above `_revive_from_agent_log`): scans `~/.claude/projects/<encoded-cwd>/*.jsonl` for the project, compares each transcript's `claude_session_id` (the .jsonl filename) against the set of `claude_session_id`s already in `<pid>_agent_log.json`, and inserts a synthesized entry for any missing transcript newer than the configured age cutoff. Entries are tagged `synthesized: True` and `status: 'interrupted'`. `session_id` is left empty (MC never owned them); the "Continue" button in the Agent Log tab keys off `claude_session_id` so it still works.
- **New `_backfill_all_agent_logs()`** iterates every project and runs the per-project backfill. Called once at server startup in a daemon thread so `app.run()` isn't blocked.
- **Three new config flags**:
  - `agent_log_backfill_enabled` (default `True`) — gates the whole feature.
  - `agent_log_backfill_max_per_project` (default `200`) — caps how many transcripts to scan per project.
  - `agent_log_backfill_max_age_days` (default `60`) — only synthesize entries for transcripts modified within this window. Older transcripts stay invisible to keep the Agent Log focused on recent work.

### Verification
Dry-run against `mission_control_agent_log.json` (291 existing entries, 41 known `claude_session_id`s) found 25 missing transcripts within the 60-day window, including `03ffec41-b384-4bcd-88a5-c2c066e9a308` — the overnight conversation that prompted this fix. After server restart, those 25 will appear in the Mission Control project's Agent Log tab with their first user message as the task label, last user message as the summary, real turn counts, and `[interrupted]` status.

### Edge cases worth watching
- **Synthesized entries are NOT revivable via `_revive_from_agent_log`**: that helper looks up by MC `session_id`, and synthesized entries leave `session_id` empty (since MC never owned the session). The "Continue" button in the Agent Log tab is the supported path and works because it keys off `claude_session_id`. If you want synthesized entries to be revivable too, give them a fresh `session_id = 'synth-' + csid[:8]` and the existing revive lookup will find them — left out of this commit because synthesized sessions in flight could still be running in another MC process and we don't want to fight over them.
- **Duplicate entries on later finalization**: if a synthesized entry's session is still alive in another MC process and that process eventually finalizes it, `_log_agent_completion` will insert a *new* entry with the same `claude_session_id`. They coexist; the latest entry sorts to the top, the synthesized entry stays as historical record. Acceptable for now.
- **System-reminder noise in last_user labels**: `_extract_user_text` returns the raw user text including `<system-reminder>` blocks attached by the harness. Some synthesized entries' summaries will start with `<system-reminder>...`. Pre-existing issue (the Resume Picker shows the same data) — punted to a future polish pass.
- **Performance**: 200-transcript cap × O(turns) per scan. On a project with 35 transcripts the dry-run completed in well under a second. Scales to a few hundred projects fine.

### Rollback
Three options, increasing in cost:
1. **Toggle off**: edit `data/config.json`, add `"agent_log_backfill_enabled": false`. Restart MC. The synthesized entries from prior boots stay in the log files (you can identify them by `synthesized: true` and remove them by hand if desired); no new synthesis happens.
2. **Remove the call site**: delete the `threading.Thread(target=_backfill_all_agent_logs, ...)` line in `if __name__ == '__main__'`. Helpers stay but are unused.
3. **Full revert**: also delete `_backfill_agent_log_from_transcripts` and `_backfill_all_agent_logs` (the two functions added right above `_revive_from_agent_log`).

## [2026-04-27i] — Race-condition consolidation, Phase 2: server-decides + idempotent Stop

Phase 2 of the structural rewrite. Phase 1 (`[2026-04-27h]`) gated stale state emissions at the source. Phase 2 removes the frontend's role as a state-decision-maker entirely.

### Pattern being killed
The frontend used to read its own (potentially stale) `agentStatusCache[sessionId].status` to choose between `/agent/followup` and `/agent/interrupt`. When the cache disagreed with the server (which is exactly what races produce), the wrong endpoint got called and the server had to compensate. Same idea for the Stop button: cache-aware visibility, error response when "agent not running", optimistic cache writes that conflicted with reality.

### Server changes (`server.py`)
- **New endpoint `POST /api/project/<pid>/agent/send`** is the only intent endpoint the frontend calls now. Inside `get_manager(pid).lock`, it reads live `agent_sessions[session_id].status` and routes:
  - missing session (or no session_id) → revive from `agent_log` if possible, else dispatch fresh
  - `status == 'running'` → `agent_interrupt` (atomic stop+resume, Phase 1's `_interrupting` gate already in place)
  - any other status → `agent_followup` (queues for Mode A, stdin-write for Mode B, respawns purged sessions via `_revive_from_agent_log`)
  Response is the upstream handler's response with a `route` field appended (`'interrupt'` / `'followup'` / `'revive'` / `'dispatch'`) for debugging.
- **`/agent/stop` is now idempotent.** Pressing Stop on a session that's already stopped, missing, or in any non-running state returns `200 {ok: true, already_stopped: true, reason: <state>}` instead of 400/404. The frontend can call it without first checking cached status.
- **New SSE event `turn_start`** emitted by `/agent/stream` whenever `session['status']` transitions into `'running'`. Without it, the FE (which no longer flips status optimistically) would have no way to learn a new turn began until `turn_complete` fired at the end. `turn_start` is non-terminal — the SSE handler updates UI but does NOT close the stream.
- The existing `/agent/dispatch`, `/agent/followup`, `/agent/interrupt` endpoints are kept as internal helpers (still used by cron, scheduler, hivemind, and called by `/agent/send` itself). Frontend no longer calls them directly for the input box / interrupt flow.

### Frontend changes (`static/index.html`)
- **`sendFollowup` simplified.** Removed: the `currentStatus` read, the `useInterrupt` branch, the endpoint selection, the optimistic `agentStatusCache[sessionId].status = 'running'` write, the `updateHistoryStatus`/`updateAgentStatusUI` to `'running'`. Kept: prompt history, image upload, echo line, guardian guards, followup timeout. New behavior: always POST `/agent/send`, let the server pick the route, let SSE deliver the status flip via the new `turn_start` event.
- **`stopAgent` simplified.** Removed: optimistic `agentStatusCache[sessionId].status = 'stopped'`, optimistic `updateHistoryStatus(sessionId, 'stopped')`, the immediate `refreshModal`/`renderAgentConsole`. Kept: SSE close (so reconnect picks up post-stop state cleanly), timeout cancel, `_recentlyStoppedSessions` marker. Server's idempotent `/agent/stop` makes the button safe to spam.
- **New SSE handler `turn_start`** updates `agentStatusCache[sessionId].status = 'running'` and refreshes UI without closing the stream.

### Net effect
- Frontend has zero state-decision logic for the agent-send flow. All routing happens server-side under the lock.
- Cache-vs-server desync (the root of #6, #13, #16) becomes architecturally impossible for these flows: the FE doesn't hold a state opinion that can desync. Cache is reactive-only.
- Adding a new state (e.g. "queued", "recovering", "interrupting") becomes a single branch in `agent_send` — no new endpoint, no FE change.

### What this does NOT remove
- Other optimistic UI updates outside the agent-send path (e.g. backlog edits, project status changes) are unaffected; those have their own desync risks but are out of scope.
- The Phase 1 single-emit gate (`_session_owned_by`, `_interrupting` flag) is still required — Phase 2 routes work fine, but the SSE stream still needs Phase 1 to suppress dying-thread emissions. The two phases are complementary.

### Server restart required
Both Phase 1 and Phase 2 changes are server-side. The running Flask process (started 2026-04-24) won't pick them up until restart.

### Rollback
1. **Cheapest** (revert behaviour, keep code): in `static/index.html` `sendFollowup`, change `'/agent/send'` back to `'/agent/followup'`. Stop button reverts to working as before because the server's idempotent change is backward-compatible (a `200 {already_stopped: true}` response still triggers the FE's existing "ok" path).
2. **Clean**: also delete the `/api/project/<project_id>/agent/send` route in `server.py`, the `turn_start` emit block in `/agent/stream`, and the `turn_start` handler in `static/index.html`. Restore the `currentStatus`/`useInterrupt` logic and the optimistic cache writes in `sendFollowup`/`stopAgent`. Restore `/agent/stop`'s 404/400 responses.

## [2026-04-27h] — Race-condition consolidation, Phase 1: single-emit gate

After 16 distinct race-condition fixes accumulated in this codebase, the user asked for a structural fix instead of another point patch. The pattern across most of them is: **a thread (usually a stream reader's `finally` block) emits authoritative session state (`status`, `process_alive`, terminal events) for a session it no longer owns**, because either (a) a follow-up replaced the proc, or (b) an interrupt is mid-flight (kill issued, new proc not yet spawned).

Phase 1 consolidates the identity check into one helper and closes the kill-→-respawn gap that #16 was abusing. Fixes #1, #2, and #16 from the inventory in MEMORY.md ("Mode B reader's stale process_alive flag", "AskUserQuestion guardian race", "Interrupt-resume stale-status emit"). Phase 2 (server-as-only-source-of-truth on the frontend) is *not* in this commit — it's the larger refactor and deserves its own pass.

### What changed (`server.py`)
- **`_session_owned_by(session, my_proc)`** helper added next to `_read_agent_stream`. Returns True iff `my_proc` is still the live proc for this session AND the session is not mid-interrupt. All places that previously did `session.get('proc') is my_proc` (or its negation) in the agent stream readers now go through this helper.
- **`agent_interrupt`** now sets `session['_interrupting'] = True` *under the lock, before* killing the old proc. The respawn thread clears it (`session.pop('_interrupting', None)`) under the lock immediately after `session['proc'] = new_proc`. The exception path also clears the flag, so a respawn failure doesn't leave the session permanently gated.
- **Stream readers** (Mode A `_read_agent_stream` + Mode B `_read_agent_stream_b`):
  - Loop-break check (`if session.get('proc') is not my_proc: break`) → `if not _session_owned_by(session, my_proc): break`.
  - Exception block's "should I log?" gate → `_session_owned_by(...)`.
  - `finally` block's "should I emit terminal status?" gate → `_session_owned_by(...)`. This is the gate that fixes #16: between the old proc dying and the new one being assigned, `_interrupting=True`, so the dying reader's `finally` skips the `status='error'`/`status='completed'` write that was flipping the UI to "stopped".
- **Terminal session reader** (`_read_terminal_stream`) was *not* changed — it operates on a different `session` dict (`terminal_sessions`), has no interrupt path, and the existing `proc is my_proc` check is correct there.

### Why this is structural, not another point fix
The previous 15 race fixes were each "spot the bug, add a check at one site". This one consolidates the check itself. Any future code path that wants to emit session state from a thread can call `_session_owned_by(session, my_proc)` and get correct behavior, including during interrupt-resume, without reasoning about which proc is current. New emit sites added later are forced to confront ownership at the type-system level (you can't emit without a `my_proc` in scope, and you can't be sure of ownership without the helper).

### Phase 2 (deferred): frontend trust-server-only
Currently `sendFollowup` does optimistic `agentStatusCache[sessionId].status = 'running'` writes before the server confirms. When the server's truth conflicts (e.g., the interrupt-resume gap, or a 404 from a purged session), the cache stays wrong. Phase 2 will drop optimistic writes — UI status flips only when an SSE `status` event arrives. The local "echo" line for the user's typed message stays, since that's a UI affordance, not a state claim. Deferred because it touches roughly a dozen sites in `static/index.html` and benefits from Phase 1 having stabilized the server side first.

### Server restart required
The new code is in `server.py`; the running Flask process (started 2026-04-24) won't pick it up until restart. Old in-flight sessions survive restart via `_revive_from_agent_log` from `[2026-04-27e]`.

### Rollback
1. **Cheapest** (revert behaviour, keep code): in `_session_owned_by`, change the body to `return session.get('proc') is my_proc` — drop the `_interrupting` check. The flag still gets set/cleared but is no longer consulted; behaviour reverts to pre-`[h]`.
2. **Clean**: replace each call site of `_session_owned_by(session, my_proc)` with the original `session.get('proc') is my_proc` (or its negation), delete the helper, delete the three `_interrupting` set/pop sites in `agent_interrupt`.

## [2026-04-27g] — Mobile UI iteration: tabs into 3-dot menu, compact bottom bar, modal trim

Follow-up tightening of the mobile UI from `[2026-04-27f]`, driven by Galaxy Z Fold 7 cover-screen testing (~410 px CSS width).

### What changed (`static/index.html`)
- **Modal tab bar moved into the three-dot menu on mobile**. The 6 tabs (Agent / Backlog / Agent Log / Plans / Activity / Hivemind) are injected at the top of `.modal-menu-dropdown` inside a `<div class="mc-tabs-in-menu">` block. Each menu item calls `_mcMenuSwitchTab(projectId, tab)` — a thin wrapper that closes the open dropdown and delegates to `switchModalTab`. The active tab is highlighted with `--accent-dim` background. The original `.modal-tab-bar` at the top of the modal is `display: none` on mobile. Desktop unchanged.
- **Three-dot menu readability** (mobile only): items 13 → 15 px, padding 10/16 → 12/18 px, icons 16 px, sub-items 14 px. `min-width: 240px`. `max-height: calc(100dvh - 120px)` with `overflow-y: auto` + thin scrollbar so the menu can scroll when tabs + Status + Color + Memory + Rules + Pop-out + Delete overflow the viewport.
- **Modal header trim** (mobile only): hides the domain tag, the status-pill + relative-time row (now classed `.modal-status-row` on the inline div), the project summary, and the standalone `.card-summary` grid below the header. Added `.modal-status-row` class to the inline `<div>` in `modalContentHTML`. Padding tightened to `6px 14px 4px 16px`. What remains: project name input + 3-dot / minimize / close.
- **Per-session sub-tabs row + "+ New" stay inline**: `.agent-tab-bar` is now `flex-wrap: nowrap; overflow-x: auto` on mobile (was wrapping when two long session names + the New button overflowed), each `.agent-tab` capped at `max-width: 110px`, both tabs and `.agent-tab-new` get `flex-shrink: 0` and small horizontal padding.
- **Hide noisy session metrics on mobile**: `.token-badge` (elapsed · tokens · cached · turns), `.agent-activity` (live activity ticker), `.btn-popout`, `.btn-hm-dash` all `display: none` at ≤960 px. The status row then collapses to just `agent-status-dot` + label + `Stop` button.
- **Bottom tab bar shrunk** from ~60 → ~52 px tall: padding `8/12/14` → `4/8/6`, icons 22 → 18 px, label gap 3 → 1, FAB 44 → 36 px with `margin-top: -12px` (was `-16`) and `box-shadow: 0 2px 0` (was `0 3px 0`). Looks balanced on the Z Fold cover screen and similar narrow phones.
- **Modal/console offsets re-aligned to 52 px**: `.modal-content`, `.modal-window`, `.agent-console`, and the `@media (hover:none),(pointer:coarse)` modal sizing all use `calc(100dvh - 52px)` / `bottom: 52px`. This eliminates the phantom `===` line that was visible below the modal when the modal extended further than the tab bar's actual height.
- **Modal corner-resize grip + chat-resize handle hidden on mobile**: `.modal-content::after { display: none }`, `.modal-content { resize: none }`, `.agent-chat-separator { display: none }`. None of them are usable on a touch screen.
- **Home tab actually goes home now**: `sidebarNav('dashboard')` had no handler — only updated active-state. On mobile (`innerWidth <= 960`) it now closes every entry in `openModals` so tapping Home from inside a project modal returns to the project grid. Desktop behaviour unchanged.

### Galaxy Z Fold 7 / "Desktop site" gotcha
The cover screen is ~410 px CSS wide, but **Chrome and Samsung Internet often default to "Desktop site" mode on foldables**, which fakes a ~980 px viewport — causing `@media (max-width: 960px)` to never fire. Toggle off "Desktop site" in the browser menu to see the mobile UI. Documented in MEMORY.md.

### Files
- `static/index.html`: ~80 net new CSS lines inside the existing `MOBILE FRIENDLY UI` block + `@media (hover: none),(pointer: coarse)` updates; `_mcMenuSwitchTab` helper added beside `switchModalTab`; tab-list `<div class="mc-tabs-in-menu">` injected into `modalContentHTML`'s menu dropdown; `modal-status-row` class added to the inline header div.

### Rollback
The cheapest and clean rollback paths from `[2026-04-27f]` still work — they delete the entire `MOBILE FRIENDLY UI` CSS block, which now contains all of these tightening rules too. The `_mcMenuSwitchTab` helper and the `mc-tabs-in-menu` block in `modalContentHTML` are inert on desktop (the section is `display: none` at >960 px), so leaving them in place after a partial rollback is harmless.

## [2026-04-27f] — Mobile UI: friendly app bar, filter pills, rounded cards, FAB tab bar

Adapted the mobile design system handoff (`Mission Control Design System (1).zip`, `ui_kits/mobile/`) into the dashboard at ≤960 px widths. All changes are additive, scoped to a single CSS block and a couple of HTML/JS hooks — desktop is untouched.

### What changed (`static/index.html`)
- **App bar** (`#mobile-app-bar`): new `<div class="mc-app-bar">` above the project grid with an eyebrow line ("Monday afternoon"), display heading ("Hi 👋"), and circular avatar button (initials, taps to Settings). The slim desktop `.header` is hidden on mobile (it had no useful content there once the metric pill / search were already hidden at ≤600 px).
- **Filter pills row** (`#mobile-filter-pills`): horizontal-scroll row of pills — `Needs you`, `All`, `Working`, `Done`, `Resting` — each with a count derived from `friendlyStatus(p)`. `Needs you` is amber-bordered to flag attention. Clicks call `setFilter(...)`. `filterProjects()` was extended to handle the new `urgent` value (waiting + blocked + asking + stuck) and the existing `completed` status.
- **Project tile restyle**: tiles get 18 px corners, 1.5 px text-colored border, a 4 px solid drop-offset shadow (warm/editorial) or soft shadow (dark), 40 px rounded-square emoji avatar, and a chip-style status pill (rounded, colored bg, dot). Asking → amber border + amber drop shadow; Stuck → red border + red drop shadow. The desktop `::before` accent strip is suppressed (the shadow carries the cue). Domain tag and per-tile "agent running" badge are hidden on mobile (the chip already conveys it).
- **Bottom tab bar redesign**: 5 slots (Home / Backlog / **+ FAB** / Activity / Settings) instead of the old 4. Center FAB is a circular accent-colored button with a 3 px solid drop-offset shadow that floats above the bar (`margin-top: -16px`). Tapping the FAB calls `openNewProjectForm()`. `sidebarNav()`'s active-class loop now uses each tab's `data-nav` attribute instead of its index, so reorders are safe.
- **Agent console / modal sizing** bumped from `48px` to `64px` to fit the taller FAB tab bar (later re-tightened to 52 px in `[2026-04-27g]` after shrinking the bar).
- New JS: `renderMobileAppBar()` (eyebrow + greeting + avatar initials) and `renderMobileFilterPills()` (count + active state). Both bail when `window.innerWidth > 960`. Wired into `render()` and re-run on `window.resize`.

### What was deliberately *not* taken from the handoff
- Lockscreen-notifications screen: no native push surface in MC.
- Chat composer / Orchestrator chat screen: superseded by the existing per-project agent panel.
- New-project wizard suggestion grid: MC has a real `openNewProjectForm()` flow.

### Tone behaviour
The block applies in all tones; the warm/editorial palettes match the design 1:1, dark inherits the same layout with palette-appropriate shadows. The accent color (FAB / active pill / avatar) follows the user's chosen `data-accent` — pick `sunset` in Settings → Appearance to see the orange-on-cream look from the handoff exactly.

### Rollback
1. **Cheapest** (hide everything): in `static/index.html`, change `@media (max-width: 960px)` on the `MOBILE FRIENDLY UI` block (search "MOBILE FRIENDLY UI") to `@media (max-width: 0)`. Tiles/tab bar revert to pre-change desktop styling instantly.
2. **Clean**: delete the `MOBILE FRIENDLY UI` CSS block (the one starting at the comment "MOBILE FRIENDLY UI (≤960px, all tones)") + the closing `@media (min-width: 961px) { .mc-app-bar, .mc-pill-row { display: none !important; } }` rule directly after it.
3. **Full revert**: also delete the `<div class="mc-app-bar">` and `<div class="mc-pill-row">` HTML inside `.content-main`, restore the old 4-tab `<div class="bottom-tab-bar">` HTML (`Dashboard / Scheduler / Settings / Processes`), revert `sidebarNav()`'s tab-bar loop to the index-based version, drop `renderMobileAppBar` / `renderMobileFilterPills` and their `render()` / resize hooks, and remove the `urgent` / `completed` branches from `filterProjects()`.

## [2026-04-27e] — Revive finalized agent sessions from agent_log on follow-up

### Symptom
Press Stop on a Mode B agent, type a follow-up, hit send → "session not found" → frontend flips to `error` → permanent dead end. Same trap whenever an `agent_sessions` entry was gone but the conversation transcript still existed (server restart, 24 h scheduler purge, manual tab close, etc.).

### Why it happened
`/api/project/<id>/agent/followup` only looked in the in-memory `agent_sessions` dict. If the entry was missing, it returned 404 — even though `data/<id>_agent_log.json` typically still held the same `session_id` mapped to a resumable `claude_session_id`. The follow-up's `-r` resume path (already wired for `process_alive=False`) never got a chance to fire because the session vanished before the lookup.

### Fix (`server.py`)
- New `_revive_from_agent_log(project_id, session_id, message, p)` (placed right after `_save_agent_log`): looks up the most recent matching log entry, grabs its `claude_session_id`, spawns a fresh process with `-r <claude_sid>` (or `--append-system-prompt` fallback if the transcript is too large), and reuses the same `session_id` so the frontend's open UI tab stays addressed.
- `agent_followup` now does a pre-check: if the session_id is missing from `agent_sessions`, it tries `_revive_from_agent_log` *before* returning 404. On success it returns `{ok:true, revived:true}`; the frontend's existing `connectAgentStream` reconnect handles the SSE resume.
- Both Mode A and Mode B handled. Stream reader threads (`_read_agent_stream` / `_read_agent_stream_b`) are reused as-is.
- New config flag `agent_revive_from_log` (default `True`) gates the behavior.

### Rollback
Three options, increasing in cost:
1. **Toggle off**: edit `data/config.json` and add `"agent_revive_from_log": false`. Restart MC. Behavior reverts to "session not found" → frontend `error`. No code changes needed.
2. **Remove the call site**: delete the pre-check block in `agent_followup` (the `_has_session` block right above the existing `with get_manager(project_id).lock:` line). The helper function stays but is unused.
3. **Full revert**: also delete `_revive_from_agent_log` (the function added after `_save_agent_log`).

### Edge cases worth watching
- A revival creates a *new* `agent_log` entry when the new process eventually finalizes — the same `session_id` will appear multiple times in `agent_log`, newest first. The lookup picks the newest, so chained revivals work.
- If the original session was Mode A and the project's `use_streaming_agent` has since been flipped to True (or vice-versa), the revived session uses the *current* setting. The Claude transcript itself doesn't care which mode reads it.
- A revived session with `claude_session_id` whose `.jsonl` is now > 5 MB will start fresh and prepend a context note (same auto-fresh path used elsewhere).
- Tab-close (`closeAgentTab` → DELETE `/agent/session`) intentionally finalizes; subsequent follow-ups to that session will *also* now revive it. If that's undesirable, add an "intentionally closed" marker to the log entry and skip those in the helper.

## [2026-04-27d] — Pin chat to bottom on first open

Follow-up to 2026-04-27c: the new "respect user scroll" guard was *too* respectful — newly-opened agent chats started at the top because their initial `scrollTop` was 0, which `_isAgentOutputPinned` treats as "user scrolled up". Added a `dataset.scrollInitialized` flag on each agent-output element. Until that flag is set, the next scroll-to-bottom is forced (treating the mount as fresh); after the first successful pin, normal "respect user scroll" behavior takes over. Applied in `sizeAgentChat`, `appendAgentLine`, and `updateConsoleOutput`.

## [2026-04-27c] — Stop yanking the agent chat back to the bottom while user is scrolled up

### Symptom
Scrolling up in an agent's chat output to read earlier text would snap back to the bottom every couple seconds, even when the agent wasn't producing new output. Modal refreshes (status polling tick, focus events) re-ran `sizeAgentChat`, which unconditionally wrote `out.scrollTop = out.scrollHeight`.

### Fix (`static/index.html`)
- New `_isAgentOutputPinned(el)` helper: true when the user is within 80 px of the bottom.
- All agent-output auto-scrolls now capture the pinned state *before* mutating the DOM and only scroll when the user was already pinned. Touched: `appendAgentLine` (3 sites), `sizeAgentChat`, plan-approve / stuck-plan banners, `renderAgentQuestion`, and `updateConsoleOutput` (the bottom console strip).
- User-initiated echoes (`approvePlan` confirmation, `sendFollowup`) intentionally still snap to the bottom — the user just took an action and wants to see the result.

## [2026-04-27b] — Process Manager: agent status column

`/api/processes` now joins each tracked process to its `agent_sessions` (or `terminal_sessions`) entry and returns an `agent_status` field. The Process Manager UI renders a colored pill (`running` / `idle` / `error` / `stopped` / `completed`) next to each row, so it's clear which "alive" agent process is actively working vs sitting idle waiting for a follow-up.

- Server (`server.py:list_processes`): snapshot tracked_processes under the lock, then look up `agent_sessions[sid].status` outside the lock; falls back to alive/exited for non-agent rows.
- Frontend (`refreshProcessList`): new `Status` column, `.process-status-pill` styled green/orange/red/gray.

## [2026-04-27] — Free idle SSE slots so Settings / Process Manager / Agent Log stop hanging

### Symptom
Settings menu, Process Manager, and the Agent Log tab would occasionally get stuck on "Loading..." forever. New agent dispatches under projects that already had agents would silently appear to do nothing. The pattern correlated with how many projects had agents running or idle in the background.

### Root cause
Chromium / WebView2 caps HTTP/1.1 connections at **6 per origin**. Mission Control opened one long-lived `EventSource` per session whose status was `running` *or* `idle`, and Mode B turn completion didn't close that stream — only a terminal `status` event did. Once 4–6 idle agents accumulated their SSE sockets, ordinary fetches like `/api/processes`, `/api/config`, and `/api/project/<id>/agent_log` queued behind those streams indefinitely.

### Fix (`static/index.html`)
- **`turn_complete` handler (~line 6033)**: now closes the `EventSource`, deletes it from `agentEventSources`, clears `sseRetryCount`, and stops the watchdog. The agent process stays alive — only the browser-side socket is released.
- **`fetchAgentStatus` auto-reconnect (~line 6770)**: only reconnects SSE for sessions whose status is `running`. Idle sessions wait for a follow-up to reopen the stream.
- **`sendFollowup`**: already calls `connectAgentStream` after the POST resolves (line 6664-6667), so the reconnect path was already correct — idle sessions stream output normally on the next message, after a sub-second reconnect.

### Tradeoff
First output line on a follow-up arrives ~200-500 ms later than before (one SSE handshake), in exchange for never running out of browser connection slots regardless of how many idle agents are open.

## [2026-04-24] — Transcript-derived Conversations + Zero-gap Resume Picker

### Why
- The "Recent agent sessions" list (both in system prompts for new agents and in the Resume picker) was sourced from the completion log `<pid>_agent_log.json`. That log only records sessions that end cleanly, so interrupted / hung / crashed / in-flight sessions never appeared on restart — exactly the conversations the user most needs to recover after a reboot.
- Labels were the *first* user message (`task`), almost always a boot / condensation prompt the user doesn't recognize. The user's *last* message is the meaningful memory anchor.

### Source of truth: Claude Code's `.jsonl` transcripts
Claude Code already writes every conversation to disk as `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. Those files survive server reboots. Mission Control now reads them directly.

### Backend (`server.py`)
- **`_extract_user_text(msg_field)`** — returns plain user text from a transcript line, skipping tool_result blocks. Handles both string and list content forms.
- **`_recent_claude_transcripts(project_path, limit=5)`** — scans `~/.claude/projects/<encoded>/*.jsonl`, covers both `_`→`-` encoding variants, dedups by filename, extracts `first_user` / `last_user` / `turns` per file, sorted by mtime desc.
- **`build_claude_context`** "Recent agent sessions" block replaced with a transcript-derived "Recent conversations" block. Label now shows the user's *last* message; status enriched from live `agent_sessions` → `_agent_log` → `'interrupted'` fallback. Old log-only block kept as fallback when `project_path` is blank.
- **New endpoint `GET /api/project/<pid>/conversations?limit=20`** — returns `[{claude_session_id, mc_session_id, status, label, last_user, first_user, turns, size, mtime, ts, ts_relative, live}]`.

### Frontend (`static/index.html`)
- **`conversationsCache[projectId]`** + **`loadConversations(projectId)`** — fetched on agent-panel render; invalidated alongside `agentLogCache` on SSE `result`/`error` + on tab close.
- **`sessionPickerHTML`** rewritten to merge transcript list with the completion log (transcript wins). Shows status dot, last-user label, turn count. Now surfaces interrupted / mid-flight sessions.
- **Resume indicator** reads the label from the transcript cache first.
- **`agentStatusCache[sid].claudeSessionId`** — populated from `/api/project/<pid>/agent/status` so the frontend knows each live session's Claude session id.

### Zero-gap picker updates
The picker reflects the user's latest message *without* waiting for a server round-trip:
- **`upsertConversationCache(projectId, claudeSessionId, lastUser, status)`** — in-place patch of `conversationsCache`: updates `last_user` / `label` / `status`, bumps `ts_relative='just now'`, moves entry to top, increments `turns`.
- **`_lastUserFromBuffer(sessionId)`** — reconstructs the last user prompt by scanning `agentOutputBuffers` backward for the local-echo `"> …"` line.
- **`closeAgentTab`** — before nuking local state, snapshots `claudeSessionId` + last user line and upserts (`status='stopped'`). Then the backend `DELETE` chains `loadConversations(projectId)` to reconcile with authoritative data (~200 ms later).
- **`sendFollowup`** — after local echo, upserts with `status='running'`. When the session later stops, the picker already has the real last line.
- **`dispatchAgent`** with resume — seeds `claudeSessionId = resumeId` in the status cache and upserts immediately so the resumption's prompt shows up in the picker even before the first SSE event.

### Known limitation
Fresh sessions don't have a `claude_session_id` locally until the next status-fetch tick (~≤2 s after first SSE event). Closing a brand-new tab before that tick skips the optimistic upsert and relies on `loadConversations` only — still fast, just not zero-gap.

## [2026-04-23b] — Auto-create project folder on new project

### Auto workspace folder
- **New projects with no path get their own folder.** On `POST /api/project/<id>`, if this is the project's first write and `project_path` is blank, server creates `<auto_workspace_base>/<project_id>/` and assigns it. Collisions get `_1`, `_2`, etc. suffixes.
- **Each project needs its own folder.** On any write that sets `project_path`, server scans other project JSONs and rejects with **409** if the resolved path already belongs to another project. Windows paths compared case-insensitively.
- **`auto_workspace_base` config key** (default: `~/MissionControl`). Exposed in Settings → Paths & Server as "Auto Workspace Base".

### New-Project form copy
- Path placeholder changed from `C:\Users\...\MyProject` to `Leave blank to auto-create a folder`.
- Inline hint under the field: *"If blank, a dedicated folder will be created under your auto-workspace base. Each project needs its own folder."*
- `createProject()` now surfaces server errors correctly by checking `res.ok` in addition to `data.ok` (so the 409 path-collision message reaches the user).

## [2026-04-23] — Tile Redesign, Mode-C/Audio Split, Favicon, Cross-Project Backlog

### Tile redesign (design-handoff aligned)
- **Flat surface background.** Removed the per-project `modal_color.bg` tint + color-mix/backdrop-filter transparency that made tiles look blobby. Every tile now uses `var(--surface)` like the rest of the app.
- **Project Color → border color.** The color selected in a project's three-dot menu ("Color") now paints the tile's border via inline `style="border-color:..."`. Border width normalized to **2 px** in all three tones (Dark, Warm, Editorial); Warm and Editorial use the stronger `--border2` line token for better definition on light backgrounds.
- **Status borders still win.** `.card.friendly-stuck` / `.card.friendly-asking` border rules now carry `!important` so red/amber status indication overrides the inline project color.
- **Flexible tile height.** Dropped fixed `aspect-ratio: 5/4`. Grid now uses `grid-auto-rows: 1fr` + `align-items: stretch` so every row sizes to the tallest tile — long titles (e.g. "DayTrading — Engulfing Dashboard") no longer clip the summary or backlog badges. `min-height: 200px` floor (140 px in compact mode).
- **Scoped status-pill rules.** `.status-active / .status-blocked / .status-waiting / .status-parked / .status-unknown` rules were bare selectors and were bleeding green/amber/red backgrounds onto the `.card` element (the card also has these classes). Rescoped to `.status-pill.status-*` so only the pill chip is tinted.

### Favicon
- Inline SVG data-URI favicon: rounded square in brand accent `#e8824a` with a bold white **M** (Nunito/Inter). Matches the design handoff's `.fc-brand-mark`. Added `<meta name="theme-color" content="#e8824a">` so mobile browser chrome matches.

### Cross-project Backlog view
- New sidebar nav item "Backlog" (`sidebarNav('backlog')` → `openAllBacklog()`).
- Modal `__all_backlog` aggregates backlog items across every project with filters: text search, status (Open / Done / All), priority (High / Normal / Low / All).
- Each row shows the owning project name in accent color; clicking a row jumps to that project's modal and scrolls the item into view.
- Preserves existing badges: `agent` / `doing` source tags, priority pill, notes count.

### Advanced-features toggles (off by default)
- Settings → new "Advanced features" section. Hides under-development or power-user surface by default:
  - Token usage counter (header pill)
  - Tool call lines (`[tool: Read]` / `[tool: ExitPlanMode]` markers in agent output)
  - GitHub sync badges (issue links, `#N` badges)
  - Agent Log tab (per-project history)
  - Memory & Rules menu entries (inside three-dot)
- Stored in `localStorage` as `mc_advanced_flags`; applied via `body.adv-hide-*` classes and `!important` display:none rules.
- Rationale: keeps the first-run surface simple; matches design handoff's philosophy of a friendly, minimal dashboard.

### Metrics row removed
- Deleted the 4-card metrics strip (Active Agents / Cost / Tasks Completed / Errors). The header's agent-count pill already conveys "active" count; cost/errors can be surfaced on demand rather than permanently eating vertical space.
- Removed associated CSS (`.metrics-row`, `.metric-card`, `.mc-value`, etc.), mobile overrides, `renderStats()` metrics code path, and `VOICE_LABELS.metric_*` entries.

### Mode-C & audio work moved to a side branch
- **`mode-c-audio` branch** now owns all voice-conversation work (STT via faster-whisper, TTS via Web Speech API, voice-selection UI, voice-behavior prompt, per-turn dedup) plus the Mode-C duplication/ERROR-status fixes. Pushed to origin.
- **`master`** reverted to remove Mode C entirely — `interactive_agent.py` deleted, `/api/interactive*` endpoints stripped, Chat button and all `interactiveSessions` / `voiceMode` frontend code removed (auto-merge handled everything except the file delete, which was resolved by accepting the removal).
- Rationale: Mode C and voice are still flaky. Keeping them off master unblocks shipping polish work without waiting on their stabilization. They can be re-merged from `mode-c-audio` once they're solid.
- Today's tile redesign was committed on master first, then the Mode-C revert layered on top. Final master state: fast-forward of `origin/master` — no force-push.

## [2026-04-16] — Tauri Launcher, CORS, AskUserQuestion Race Fix & Resume Recovery

### Tauri Launcher: Silent Server Death Fix
- **Root cause**: `lib.rs` spawned Flask with `Stdio::piped()` but never read from the pipes. After hours of printing, the ~64 KB OS pipe buffer filled, `print()` blocked, Flask deadlocked, and the Python process eventually exited on `BrokenPipeError`. This was invisible — no traceback, no error, just a dead server.
- **Fix**: `Stdio::piped()` → `Stdio::inherit()` in `lib.rs:40-43`. Flask stdout/stderr now flows directly into the Tauri parent terminal. No buffer, no drainage needed, and crash tracebacks are visible.
- **Related**: removed `devUrl` from `tauri.conf.json` so `npx tauri dev` doesn't block waiting for an external HTTP server before running Cargo. The Rust app spawns Flask itself via the `setup()` hook; the webview loads `static/index.html` from `frontendDist` (disk) instead of HTTP.
- **Launch workflow changed**: user runs `npx tauri dev` only — no separate `python server.py` in another terminal. The old dual-terminal setup caused port conflicts (two Flask instances on 5199, requests routing unpredictably, `port_conflict.log` accumulating entries).

### CORS: Tauri Webview Origin Fix
- **Symptom**: after removing `devUrl`, the webview loaded from Tauri's internal scheme (`http://tauri.localhost` or similar) instead of `http://localhost:5199`. API fetches returned 200 at the Flask layer but were blocked by the browser's CORS policy because the Origin didn't match the `ALLOWED_ORIGINS` set.
- **Fix**: replaced the origin allowlist with an echo-back pattern — `Access-Control-Allow-Origin` is set to whatever Origin the caller sends. Safe because Mission Control binds localhost only and has no auth layer. Added `Vary: Origin` header for proper cache behavior.

### Guardian Race Fix: AskUserQuestion
- **Symptom**: when Claude called `AskUserQuestion`, the agent went into error state instead of showing the question UI. The user saw the question text flash briefly, then `[Guardian: process found dead]` followed by repeated `[Guardian: question may have been missed]` messages.
- **Root cause**: the stream reader set `waiting_for_question=True` and called `proc.kill()` while `status` was still `'running'`. The guardian's 10s tick landed in the gap before the reader's `finally` block could reacquire the lock and set `status='idle'`. Guardian State 1 saw "dead process + running status" and marked the session `'error'`. When the reader finally got the lock, its `if status in ('running', 'idle')` check failed, so the graceful question-handling branch never ran.
- **Fix (two layers)**:
  1. Both Mode A and Mode B stream readers now set `status='idle'` and update `last_status_change_time` **before** calling `proc.kill()` for `AskUserQuestion`. This closes the race window — the guardian sees a fresh idle session, not a stale running one.
  2. Guardian State 1 now checks `waiting_for_question` and `waiting_for_plan_approval` flags as a safety net. If either is set, the dead-process → error transition is skipped entirely.

### Auto-Recovery for Failed Session Resumes
- **Problem**: dispatching with `claude -r <session_id>` across server restarts is fragile. The CLI's internal state (turn counter, context budget) was set during the original session and may not survive a fresh process reading the transcript file. Two failure modes:
  1. **Immediate death**: process exits within seconds, before producing any output.
  2. **Post-turn death**: process completes one turn successfully, then exits. Follow-up respawn tries `-r` on the same session → same failure → silent error loop.
- **Fix for immediate death** (`_auto_recover_failed_resume`):
  - Each session now tracks `_resume_id` and `_dispatch_time` at dispatch.
  - Both Mode A and Mode B readers: if a resumed session dies within 60s with `status='error'` and `num_turns=0`, `_auto_recover_failed_resume()` fires automatically.
  - Reuses the same session object (seamless to frontend), spawns fresh `claude -p` with context note: `[Continuing from a previous conversation (session X) that could not be resumed. Start fresh.]`
  - One-shot: `_resume_recovery_attempted` flag prevents infinite loops.
- **Fix for post-turn death** (Mode B followup respawn):
  - When a Mode B process dies after a turn that came from a `-r` resume, the follow-up respawn now **starts fresh** instead of trying `-r` on the same fragile session.
  - Log message: `[Resumed session process exited — restarting fresh]`
  - If `claude_session_id` was never emitted by the CLI, falls through to fresh start instead of returning 400 error.
- **Verbose respawn logging**: every decision point in the Mode B follow-up respawn path prints to stdout (`[followup]`, `[respawn-B]` prefixed) so failures are visible in the Tauri terminal.

## [2026-04-15] — Per-Project Agent Isolation & Guardian Overhaul

### Per-Project Agent Manager (eliminates cross-project blocking)
- **Root cause**: every agent operation (dispatch, follow-up, stop, guardian state mutation) routed through a single global `agent_lock`. A slow process-tree kill in Project X blocked stdin writes, status reads, and SSE events for every other project — Mode A and Mode B "isolation" was illusory because both modes ultimately serialized on the same mutex.
- **`ProjectAgentManager`** (server.py:329) — new class, one instance per `project_id`, owns its own `RLock`, `session_ids` set, and lazily-spawned guardian thread.
- **`get_manager(project_id)`** + **`get_manager_for_session(session_id)`** + **`all_managers()`** — registry helpers. `_managers_lock` is held only for microseconds to mutate the registry dict; never held across any subprocess, kill, or stdin write.
- **All 30 `with agent_lock:` call sites** replaced with `with get_manager(<project_id>).lock:` — covers `_dispatch_agent_internal`, follow-up endpoints, stop / interrupt-resume, hivemind dispatches, terminal broadcast, scheduler purge, Process Manager kill, and every guardian state mutation.
- **`agent_lock` deleted entirely.** No shared mutex remains anywhere on the agent execution path.

### Per-Project Guardian Threads
- **`_project_guardian_loop(manager)`** (server.py:5494) — one guardian thread per `ProjectAgentManager`, lazily spawned via `manager.ensure_guardian()` on first dispatch.
- Each loop iterates only its own project's `session_ids` — has zero visibility into other projects, by construction.
- Legacy global `_session_guardian_loop` is now a no-op stub kept for compatibility with startup callers.
- A hung kill, slow check, or recovery sequence in one project cannot affect any other project.

### Guardian Hung-Process Detection: CPU-Aware
- **`GUARDIAN_HUNG_TIMEOUT`: 180s → 600s.** The old 3-minute threshold was killing healthy thinking turns mid-stream.
- **New `_proc_is_cpu_idle(session, proc, now)`** (server.py:5448) — uses psutil to compare cumulative CPU times of the process *tree* (parent + children) across guardian ticks. Kill only fires if the tree burned <0.05 CPU-seconds per wall-second since the previous sample.
- **State 2 (hung process) now requires both stdout silence AND CPU idleness.** Long WebFetch / Bash / Read tool calls survive — they burn syscall/network time that psutil sees.
- **psutil missing → kill never fires.** Without psutil, `_proc_is_cpu_idle` returns `False` and the guardian falls back to dead-process detection only. No false positives possible.
- Lock is never held across `_kill_proc_background` — flag flips happen under the lock, the kill runs after release.

### Critical Bug: Stale `last_output_time` on Resume
- **Symptom**: prompting any idle Mode B session triggered an instant guardian kill ("no output for 609s — killing hung process") even though the agent had no chance to produce a single chunk.
- **Cause**: `last_output_time` was set at session creation and only advanced when the stream reader saw stdout. When a turn completed and the session sat idle, the timestamp froze. The five resume paths flipped `status` back to `'running'` without resetting the timestamp, so the guardian's next tick computed `now - last_output_time = (entire idle gap)` and killed.
- **Fix**: every site that sets `status='running'` on a resume now also sets `last_output_time = _time.time()`:
  - Mode A initial follow-up (server.py:1606)
  - Mode A interrupt-resume (server.py:2367)
  - Mode B respawn after auto-fresh (server.py:2082)
  - Mode B stdin write to alive process (server.py:2108)
  - Mode B follow-up via `_start_followup` (server.py:2149)

### Frontend: Honest Mode Display
- **Bug**: project context menu showed `Mode B (Streaming) OFF` when a project had no `use_streaming_agent` key, but dispatch fell back to global config (which is `True`) and ran Mode B regardless. UI lied about which mode would run.
- **`_globalConfig` cache** in `index.html` — populated by `refreshSilent()` from `/api/config`, used to compute the *effective* mode (per-project override if set, else global default).
- **Menu redesign** (index.html:3034): `⚡ Agent: Mode A (global)    switch → B`. Always shows the mode that will actually run, with a `(global)` badge when the project is inheriting and a `switch → A/B` hint for the next click. One click writes an explicit per-project override.

### Migration Notes
- No data migration required; sessions remain in the global `agent_sessions` dict (GIL-safe for reads). Only locking and guardian iteration moved per-project.
- `agent_lock` is removed; any external code importing it will break (none in-tree).
- Server restart required to pick up the timestamp resets and CPU-aware guardian.

## [2026-04-14] — Session Guardian, Plan Visibility & Tab Fixes

### Session Guardian (replaces Health Monitor)
- **`_session_guardian_loop()`** — new 10-second tick background thread replaces the old `_health_monitor_loop()`
- Detects 7 stuck states across both Mode A and Mode B sessions:
  1. Dead process with stale running/idle status (was Mode B only, now covers Mode A too)
  2. Hung process — alive but no output for 3+ minutes → kills process, marks needs_attention
  3. Stuck `waiting_for_plan_approval` / `waiting_for_question` flags (>2 min, no SSE client)
  4. Stuck `pending_followups` queue (>30s, not running, not dispatching)
  5. Stuck `_dispatching_followup` flag (>30s)
  6. Rapid error loop — circuit breaker trips after 3 failures within 60s
  7. Popen failure — session stuck in `running` with dead/missing process (>15s grace)
- **Auto-recovery**: preserves user's message, kills zombie process tree, retries `claude -r` with exponential backoff (5s→10s→20s)
- **Circuit breaker**: after 3 rapid failures, stops retrying, sets `guardian_state='needs_attention'`
- Recovery is scoped to individual sessions — parallel agents in other projects are never affected
- Per-session tracking: `last_output_time`, `last_status_change_time`, `guardian_state`, `recovery_attempts`, `pending_recovery_message`, `circuit_breaker_tripped`

### Critical Bug Fix: `_start_followup` Error Handling
- Wrapped `_start_followup()` body in try/except — previously, if `subprocess.Popen` failed (wrong PATH, disk full, etc.), the session would get permanently stuck in `running` with no process, no reader thread, and no way to recover
- On failure: sets `status='error'`, logs the error, guardian can then auto-recover

### Pending Message Capture
- `agent_followup` endpoint now saves the user's message as `pending_recovery_message` before spawning
- If the spawn fails, the guardian has the message to retry automatically
- Cleared on successful session completion (rc=0)

### SSE & API Integration
- New `guardian` SSE event type with `state` and `circuit_breaker` fields
- `_last_sse_poll_time` tracked in SSE loop for stuck gate flag detection
- Guardian state included in `/api/project/<id>/agent/status` response
- New endpoint: `POST /api/project/<id>/agent/guardian-reset` — `action: "retry"` resets circuit breaker, `"dismiss"` clears notification

### Frontend: Guardian UI
- New status dot states: `.recovering` (yellow pulsing), `.needs-attention` (orange pulsing)
- Guardian banner above chat input when circuit breaker trips:
  - "Try Again" button — resets circuit breaker, allows retry
  - "Start Fresh" button — dispatches new session
  - "Recovering..." banner during active recovery
  - "Needs attention" banner with retry/dismiss options
- `sendFollowup` guards: blocks input during recovery or when circuit breaker is tripped
- `updateAgentStatusUI` reflects guardian state on dots and labels
- `agentStatusCache` populated with `guardianState` and `circuitBreakerTripped` from status API

### Fix: Plan Content Hidden Before User Can Read
- `collapseIntoPlanButton()` no longer auto-collapses plan text on first `ExitPlanMode`
- Plan text stays visible in agent output so user can read it before deciding to approve
- "Approve Plan" + "Collapse Plan" buttons shown at bottom of visible plan
- On second ExitPlanMode (stuck loop), plan auto-collapses as before with warning

### Fix: Plans Tab Rendering
- Removed `setTimeout(() => renderPlansTab(...), 50)` — now called synchronously after `refreshModal()`
- `refreshModalById` re-renders plans tab content after DOM rebuild when cache exists
- Prevents race where SSE-triggered `refreshModal` could overwrite plans tab content

## [2026-04-12] — Stale Session Cleanup After Server Restart

### Frontend Session Reconciliation
- **`fetchAgentStatus()`** now compares server-returned sessions against locally cached sessions
- Sessions in `agentHistory` / `agentStatusCache` / `agentOutputBuffers` that the server doesn't know about (e.g., after server restart) are cleaned up automatically
- `activeAgentTab[projectId]` is cleared if it points to a stale session, so the dispatch input (not the follow-up input) is shown
- Associated SSE streams and watchdogs for stale sessions are closed
- `refreshModal()` + `renderAgentConsole()` triggered after stale cleanup so UI updates immediately

### Root Cause
- After server restart, in-memory `agent_sessions` is empty, but the frontend still held references to old sessions
- `activeAgentTab` pointed to a dead session ID → UI showed follow-up input instead of dispatch row
- Follow-ups sent to the dead session ID → server returned 404 → silently failed
- User saw a working chat UI but couldn't start new conversations or get responses

## [2026-04-04] — Agent Stability: Health Monitor & Error Recovery

### Process Health Watchdog
- **`_health_monitor_loop()`** — new background thread runs every 12 seconds
- Checks all Mode B sessions where `process_alive=True`, verifies PID is actually alive via `proc.poll()` + `_pid_is_alive()`
- If process is dead but flag says alive: sets `process_alive=False`, `status='error'`, logs `[Health check: process {pid} found dead]`
- Registered with `atexit` for clean shutdown via `_health_monitor_stop` Event

### Race Condition Fixes (process_alive flag)
- **`_read_agent_stream_b` finally block**: moved `session['process_alive'] = False` inside the `if session.get('proc') is my_proc:` guard — old reader threads from replaced processes can no longer falsely mark new processes as dead
- **`sendFollowup` endpoint selection**: `currentStatus` now captured BEFORE the optimistic UI update to 'running', fixing a bug where `useInterrupt` was always `true` (idle Mode B sessions were being killed and respawned instead of writing to stdin)

### Robust Followup Path
- **PID verification before stdin write**: `agent_followup` now checks `proc.poll()` / `_pid_is_alive()` before trusting `process_alive=True` flag — if process is dead, redirects to respawn path instead of silently failing
- **Old process cleanup on respawn**: Mode B respawn in followup now closes old proc's stdin and kills old process in background (prevents zombie processes)

### Frontend Unresponsive Agent Detection
- **`followupTimeouts`**: 20-second timer starts after every follow-up send; if no SSE output arrives, shows toast: "Agent appears unresponsive"
- Timer cancelled on: output received, turn_complete, status change, error event, or user stop
- Non-blocking warning — user can ignore if agent is just slow (e.g., large context resume)

### Static File Cache Busting
- `index.html` now served with `ETag` header based on file mtime+size
- Switched from `no-store` to `no-cache` — allows conditional GET (304) so Tauri WebView2 always revalidates
- Fixes stale frontend code being served after server-side changes

## [2026-03-25] — Active Context Auto-Trimming

### Context Budget → Active Condensation
- **`_check_context_budget()`** now triggers auto-condensation instead of just logging a passive warning
- Pre-dispatch check: when total context (CLAUDE.md + MEMORY.md + prompt) > 20KB, condensation fires immediately
- Post-completion check: also includes CLAUDE.md in size calculation (was MEMORY-only)
- Message changed from `[context warning]` to `[context trim]` with actionable status

### CLAUDE.md Condensation
- **`_dispatch_condense()`** now handles CLAUDE.md alongside MEMORY.md
- Only condenses CLAUDE.md when > 8KB (preserves small project configs)
- Housekeeping agent instructions: preserve rules/constraints verbatim, merge duplicates, compress verbose explanations, keep code snippets exact
- Target: under 8KB per file

### `_should_condense()` Expanded
- New `include_claude_md` parameter — includes project CLAUDE.md in size threshold check
- Used by both pre-dispatch (context budget) and post-completion triggers
- Skips running-agent guard when called from pre-dispatch (agent hasn't started yet)

## [2026-03-24] — Major UI Redesign

### Layout Overhaul
- **Collapsible sidebar** (52px → 220px on hover): Logo, nav items (Dashboard, Scheduler, Settings, Shared Rules, Processes), project shortcuts with status-colored dots
- **Slim header** (48px): Breadcrumb, Ctrl+K search trigger, token counter, agent count metric pill, Live badge
- **Metrics row** replaces stats bar: Active Agents, Cost Today, Tasks Completed, Errors — with live data
- **Toolbar** replaces filter row: Grid/List view toggle, filter dropdown with active pills, density toggle, + New Project button
- **Content area** with proper flex scroll (replaces body scroll)

### New Features
- **List view**: 7-column table (indicator, project, status, current task, next up, agent, updated) — toggle with Grid view
- **Command palette** (Ctrl+K): Search projects, actions (Scheduler, Settings, etc.), and view toggles with keyboard navigation (arrow keys + Enter)
- **Collapsible feed**: Click toggle to hide/show Activity Feed column (state persisted in localStorage)
- **Clickable feed entries**: Click any activity entry to open that project's modal
- **Mobile responsive design**: Bottom tab bar at ≤960px, single-column tiles at ≤600px, metrics row wraps at ≤768px
- **View persistence**: Grid/List mode, feed collapsed state, and density all saved to localStorage

### Mobile Fixes
- **Modal height**: Account for bottom tab bar — `calc(100vh - 48px)` at ≤960px viewport
- **Modal positioning**: Full-width, top-aligned on mobile (no center offset)
- **Agent chat input**: Fixed text entry box hidden below screen — `sizeAgentChat()` now constrains tab content and agent panel heights
- **Hide tile details on mobile**: Current Task and Next Up hidden at ≤960px

### Visual Refinements
- Refined color palette: darker backgrounds (#0c0e14), less saturated borders (#252a38), softer text (#e8ecf4)
- Tile aspect ratio: 1:1 → 5:4 (more information-dense)
- Tighter tile padding: header 14px, body 16px, footer 10px
- Feed column: 380px → 320px, clickable entries with hover accent border
- Left indicator on tiles: 4px → 3px

## [2026-03-24] — Fix process registration & plan approval reliability

### Process Registration — Windows-safe PID operations
- **New `_pid_is_alive()`**: uses `ctypes.windll.kernel32.OpenProcess()` on Windows instead of unreliable `os.kill(pid, 0)`
- **New `_kill_pid()`**: uses `taskkill /F /PID` on Windows instead of broken `os.kill(pid, 9)`
- Registration endpoint now warns-but-registers when PID not detected alive (handles race where process exits quickly)
- System prompt now includes explicit PID capture instructions for agents (Bash `$!` and Python `p.pid`)
- Process listing and kill operations use new cross-platform helpers

### Plan Approval — Server-side flag clearing
- **Root cause fix**: server now clears `waiting_for_plan_approval = False` when any followup is received
- Previously the flag was set on ExitPlanMode but never cleared — subsequent status polls re-set frontend state to "waiting"
- Frontend SSE handlers (`turn_complete`, `status`) now also clear `waitingForPlanApproval` locally
- `approvePlan()` rewritten: always sends directly via fetch API (no dependency on input element existing in DOM)
- Added double-click guard — button removed immediately before any async work

## [2026-03-24] — Live status on tiles & modals, UX fixes

### Live Auto-Populated Status
- **Current Task** and **Next Action** fields are now fully auto-computed from live state
- `computeLiveStatus(projectId)` inspects running agents, hiveminds, errors, completions, and backlog
- Priority: Hivemind > Running agent > Error > Last completed > Idle
- Next action: Hivemind pending workstreams > Top backlog item > —
- Color-coded: green (running), accent (idle agent), red (error), dim (idle/completed)
- Applies to both project tiles and modal summary section
- Replaces stale manual `current_task` / `next_action` fields

## [2026-03-24] — Plan approval gate, error recovery, UX fixes

### Plan Approval — No More Auto-Approve
- **Removed auto-approve**: `ExitPlanMode` no longer auto-approves plans — both Mode A and Mode B now set `waiting_for_plan_approval` flag and wait for user to click "Approve Plan"
- Removed `_auto_approve_plan_b()` function entirely
- Mode A no longer queues approval in `pending_followups`
- Mode B no longer sends approval via stdin automatically
- User retains full control over plan review before implementation starts

### Error Recovery — Continue from Errored Sessions
- Agent follow-up input bar now visible on errored sessions (was hidden before)
- Placeholder text: "Type to continue from where it stopped..."
- Sends follow-up via existing resume mechanism (`-r` for Mode A, stdin respawn for Mode B)

### Flexible Modal Textareas
- Memory, Rules, and Shared Rules modals use flex layouts — textareas grow/shrink with modal resize
- New `.memory-editor` and `.rules-editor` CSS classes (same pattern as `.shared-rules-editor`)
- Modals start at `60vh` height, resizable via drag corner

### Universal Ctrl+Scroll Zoom
- Ctrl+Scroll now zooms all modal content (was agent output only)
- `applyModalZoom()` helper sets `font-size` on `.modal-content` for full cascade
- Zoom levels persist per modal across refreshes

### Memory Path Resolution Fix
- `_native_memory_path()` now checks both underscore and dash encodings
- Prefers most recently modified file when both exist (fixes stale memory on projects with `_` in path)

### Agent Chat Overflow Fix
- Keep `.modal-scroll-body` overflow hidden while agent tab is active
- Prevents follow-up input bar from being pushed below the modal

### Hivemind Improvements (from prior session)
- Agent context now includes hivemind API instructions for chat-first creation
- `startHivemindChat()` — switches to Agent tab with prefilled setup prompt
- Open questions: "Respond" button prefills directive, resolves question after sending
- New endpoint: `POST /api/hivemind/{id}/knowledge/questions/{qid}/resolve`
- `_hm_read_open_questions()` now filters out resolved questions
- Findings displayed in dashboard overview and workstream detail views

## [2026-03-23] — Hivemind Phase 2+3: Agent Integration & Frontend

### Backend — Agent Integration (Phase 2)
- **Worker spawn**: `POST /api/hivemind/{id}/workstreams/{ws_id}/spawn` dispatches a standard MC agent session as a hivemind worker, with full workstream-specific context injection (handoff, findings, bus messages, decisions)
- **Handoff endpoint**: `POST /api/hivemind/{id}/workstreams/{ws_id}/handoff` — workers submit structured handoff documents (what was done, key findings, next worker instructions); written to `{ws_id}_handoff.md`
- **Orchestrator CLI sessions**: Short-lived `claude -p` subprocesses for goal decomposition (on create), synthesis, and re-planning — same pattern as memory condensation housekeeping agents
- **Auto-decomposition**: Creating a hivemind auto-dispatches an orchestrator CLI session to break the goal into workstreams
- **Auto-spawn**: Orchestrator background loop automatically spawns workers for ready workstreams (dependencies met, under max_concurrent_workers)
- **Worker lifecycle**: Detects finished/crashed workers, auto-retries up to max_retries_per_workstream, sets failed status when exhausted
- **Auto-completion**: When all workstreams complete, hivemind status set to completed and final synthesis triggered
- **Worker context builder**: `_hm_build_worker_context()` injects handoff, accumulated context, recent findings, bus messages, decisions, and API capabilities into the worker system prompt

### Frontend — Hivemind Tab & Dashboard (Phase 3+4)
- **Hivemind tab** in project modal — shows all hiveminds for a project with status, workstream list, activity feed
- **Create dialog** — goal input, title, max workers, model selection; orchestrator auto-decomposes
- **Workstream list** with status icons (completed/active/pending/blocked/paused/failed)
- **Activity feed** — recent bus messages with timestamps
- **Pause/Stop/Resume controls** on hivemind cards
- **Full dashboard modal** — standalone 900x600 modal with sidebar (workstream selector), overview, and per-workstream detail views
- **Per-workstream detail** — description, findings count, session count, messages, manual worker spawn button, directive input
- **Synthesis viewer** — modal showing the current knowledge synthesis markdown
- **Directive inputs** — send messages to orchestrator or specific workstreams via the bus
- **SSE live updates** — hivemind dashboard auto-refreshes on bus events; escalation toasts
- **Proper cleanup** — SSE connections closed when dashboard modal is closed

## [2026-03-23] — Fix drag-and-drop in Tauri window
- Disable Tauri's native drag-drop interception (`dragDropEnabled: false`) so JS drop events fire
- Add document-level `dragover`/`drop` preventers to stop browser file-open on missed drops

## [2026-03-23] — Drag-and-drop file attachments in agent chat

### Drag-and-drop files into agent chat
- Drag files (images, documents, any file type) onto the dispatch or follow-up textarea
- Visual highlight on drag-over (accent border + dim background)
- Images show thumbnail previews; documents show filename with file icon
- Files uploaded via existing upload pipeline, referenced as `[Attachment: path]` (or `[Screenshot: path]` for images)
- Works alongside existing paste-to-attach functionality

## [2026-03-23b] — Fix native window in bundled app (root cause .NET fix)

### Build fixes (pre_build_fix.py)
- **Bug 1 fixed**: Replaced `net462` WinForms DLL with `netcoreapp3.0` variant from NuGet
  - The bundled `Microsoft.Web.WebView2.WinForms.dll` was targeting classic .NET Framework
  - pythonnet loads a .NET Core CLR, so the Framework DLL caused the crash
- **Bug 2 fixed**: Added `Python.Runtime.runtimeconfig.json` with `LatestMajor` roll-forward
  - Without this file, hostfxr refuses to roll forward across .NET major versions
  - Now works on .NET 6, 7, 8, 9, or any future version
- New `pre_build_fix.py` script automates both fixes before PyInstaller runs

### Graceful browser fallback
- `import clr` runs early to fail fast if .NET CLR can't load
- If native window fails for any reason, falls back to browser mode (no crash)
- .NET Desktop Runtime pre-detection with guided install dialog (auto-install or manual)

### .NET Desktop Runtime pre-detection
- Checks for .NET Desktop Runtime BEFORE attempting to load pywebview
- Detection via `dotnet --list-runtimes` (checks for `Microsoft.WindowsDesktop.App`)
- Fallback: Windows registry check at `HKLM\SOFTWARE\dotnet\Setup\InstalledVersions`

### Guided setup dialog when .NET is missing (dev mode)
- Three-button MessageBox: **Yes** (auto-install), **No** (open download page), **Cancel** (use browser)
- Auto-install via `winget install Microsoft.DotNet.DesktopRuntime.8`
- Manual install option opens the .NET 8.0 download page in browser
- Browser fallback always available — app fully functional without native window

## [2026-03-22c] — Global Settings UI, Agent Process Registration

### Global Settings modal
- New "Settings" button in header opens a 600px modal with all configuration options
- Organized into 5 sections: Identity, Agent Defaults, Claude Code Integration, Memory & Condensation, Paths & Server
- Toggle switches for boolean settings (streaming agent, remote control, auto-condense)
- Dropdowns for model and permission mode selection
- Settings save on change with toast notification
- API: `GET /api/config` and `PUT /api/config` endpoints for reading/writing config.json

### Per-project Remote Control toggle
- New "Remote Control" toggle in project three-dot menu (after Agent Model)
- Shows ON/OFF status; per-project override for the global setting
- When enabled, agents get `--remote-control` flag for claude.ai control

### Agent-reported process registration
- Agents can register spawned processes via `POST /api/processes/register`
- System prompt teaches agents to call the API when spawning background processes
- External processes visible in Process Manager with kill support

## [2026-03-22b] — .NET fallback, Process Manager

### .NET runtime fallback
- Desktop app now gracefully handles missing .NET Desktop Runtime on target machines
- Shows a Windows MessageBox explaining the issue instead of crashing with a raw traceback
- Falls back to opening Mission Control in the default browser so the app is still usable
- Provides download link for .NET Desktop Runtime

## [2026-03-22] — Process Manager (PID Tracker)

### Process Manager
- Centralized PID tracker for all subprocess spawns (agents, terminals, housekeeping)
- Each process registered with human-readable name, type, project, session ID, and task preview
- Header "Processes" button opens 800px modal with live process table
- Table shows: status dot (green=alive, red=dead, gray=exited), PID, name, project, task/command, duration, kill button
- Toolbar displays running/total count with Refresh and "Cleanup Orphaned" buttons
- Kill button terminates individual processes and updates corresponding agent/terminal session status
- "Cleanup Orphaned" kills all processes that are alive but whose sessions are gone or completed
- Scheduler liveness sweep auto-removes dead processes every 30 seconds
- API endpoints: `GET /api/processes`, `POST /api/processes/<pid>/kill`, `POST /api/processes/cleanup`
- All 7 Popen call sites instrumented: Mode A/B agents, followups, respawns, housekeeping, terminals
- Process unregistered at all kill/cleanup/completion points (stream reader finally blocks, stop, delete, atexit)

## [2026-03-22a] — Claude Code channels, remote control, cron schedules, token display

### Claude Code Channels support
- New `agent_channels` config option (global or per-project)
- Appends `--channels <value>` to agent spawn command
- Supports Telegram, Discord, and custom MCP channel plugins

### Remote Control flag
- New `agent_remote_control` config option (global or per-project)
- When enabled, appends `--remote-control` to agent spawn
- Allows controlling MC-managed agent sessions from claude.ai or mobile app

### Cron expression support for scheduler
- New "Cron" schedule type alongside Daily/Interval/Once
- Standard 5-field cron expressions: minute hour day-of-month month day-of-week
- Supports wildcards, ranges, steps, comma-separated lists
- Vixie-cron semantics for day matching

### Scheduler modal now draggable
- Added `.modal-header` to scheduler window for grab-and-drag

### Enhanced token/context usage display
- Status bar shows token breakdown with cache read info during and after runs
- Turn count shown in status bar and agent log entries
- Metrics update live every second during running sessions

## [2026-03-21a] — Mobile touch fix, auto-fresh sessions, TTY shim, toast notifications

### Mobile tile drag fix
- Tile reordering now requires a 300ms long-press before drag starts
- Scroll, swipe, and pinch-to-zoom gestures pass through to browser normally
- Multi-finger touches (pinch) are ignored by the drag handler entirely
- Visual scale feedback on long-press activation
- Separate tile order for mobile vs desktop (mobile is local-only, desktop is source of truth)
- Insert-and-shift tile reorder: dragging a tile between others pushes them right instead of swapping

### Auto-fresh large sessions
- Sessions with transcripts > 5 MB are auto-started fresh instead of resumed
- Prevents slow startup from loading massive conversation history
- Context note injected so agent knows it's continuing from a prior session
- Covers all resume paths: main dispatch, Mode A followup, Mode B respawn
- Activity log entry notifies user of auto-fresh with size info
- Toast notification shown in UI when auto-fresh triggers

### Toast notification system
- Lightweight toast notifications slide in from top-right corner
- Auto-dismiss after 5 seconds with fade-out animation
- Used for auto-fresh session alerts; available for future notifications via `showToast()`

### TTY shim improvements (`mc_tty_shim/sitecustomize.py`)
- Added `_FakeBuffer` wrapper — preserves `isatty()=True` through `TextIOWrapper` re-wrapping
- Auto-flush on buffer write — fixes Rich `Live` display buffering with `line_buffering=True`
- Patched `os.get_terminal_size()` and `shutil.get_terminal_size()` to read `COLUMNS`/`LINES` env vars when pipe fd fails
- Root cause: dashboard's `sys.stdout = io.TextIOWrapper(sys.stdout.buffer)` was overwriting the TTY shim

### Agent tab ordering
- New agent tabs now appear on the right side of existing tabs (chronological order)
- Sessions sorted by `startedAt` ascending in the tab bar

### GitHub Issues sync (Phase 1) — `github_sync.py`
- Bidirectional sync between MC backlog items and GitHub Issues via `gh` CLI
- Security: `sanitize()` strips HTML, dangerous protocols, control chars from all GitHub text
- 4 new API endpoints: setup, disconnect, sync, status
- Auto-sync every 5 minutes via scheduler
- Sync badge in backlog header, `#N` issue links on items, three-dot menu integration
- Activity Stream integration for all sync events

## [2026-03-20a] — Fix ExitPlanMode infinite loop in agents

- Agents spawned by Mission Control could get stuck calling ExitPlanMode in an infinite loop
  (known Claude CLI bug: `--dangerously-skip-permissions` does not auto-approve ExitPlanMode)
- System prompt now instructs agents to NEVER use EnterPlanMode or ExitPlanMode
- Mode A: if ExitPlanMode is detected in tool_use output, a follow-up message is queued
  telling the agent to proceed directly with implementation
- Mode B: `_auto_approve_plan_b()` sends an approval message via stdin immediately when
  ExitPlanMode is detected, breaking the loop

## [2026-03-19e] — TTY shim for Rich color support in terminal pop-outs

- `mc_tty_shim/sitecustomize.py` auto-injected via `PYTHONPATH` into terminal processes
- Child Python processes see `isatty()=True` via monkey-patched stdout/stderr
- Rich's legacy Windows detection patched — emits ANSI escape codes instead of Console API calls
- Full Rich table colors (truecolor), Live display, and styled output now render in xterm.js
- Terminal launch sets `MC_FORCE_TTY=1`, `TERM=xterm-256color`, `COLUMNS=120`, `LINES=30`
- Centralized `_kill_terminal_session()` helper for cleanup

## [2026-03-19d] — Two-tier memory with auto-condensation

- Session log overflow now archived to `MEMORY_ARCHIVE.md` instead of being deleted
- Archive is a sibling file to `MEMORY.md` — agents are told about it in system awareness
- Auto-condensation: when combined memory + archive exceeds threshold (default 15KB), a housekeeping agent runs to fold session insights into organized knowledge sections, keep last 5 session entries, and delete the archive
- Condensation uses a separate `claude -p` process with `--max-turns 5` and configurable model (default: sonnet)
- Housekeeping sessions visible in agent log but marked `housekeeping: True` — their completion does NOT trigger further memory appends or condensation (prevents circular triggers)
- New config options: `condense_threshold_kb` (default 15), `condense_model` (default sonnet), `condense_enabled` (default true)
- `_condensing_projects` set prevents double-dispatch of condensation for the same project
- Condensation skipped if any non-housekeeping agent is running/idle for the project

## [2026-03-19c] — Context budget auto-reduction

- MEMORY.md session log auto-pruned to last 20 entries when file exceeds 10KB
- Agent system awareness text compressed (~60% shorter) — removed instructional paragraphs
- Recent activity and agent session history reduced from 5 → 3 entries in appended context
- Session task truncation tightened from 80 → 60 chars in context
- Pre-dispatch context budget warning when CLAUDE.md + MEMORY.md + prompt exceeds 20KB

## [2026-03-19b] — Enhanced Plans tab with management tools

- Plans tab now shows checkboxes for multi-select, toolbar with Select All / Delete / Export
- Individual delete button (×) on each plan card
- Bulk delete with confirmation prompt — removes files from disk and scrubs agent log references
- Export selected plans as .md file downloads
- Plan cards show filename in faint text below the metadata
- New `POST /api/plans/delete` server endpoint with path security validation

## [2026-03-19a] — Embedded terminal pop-out windows

- Agents can launch CLI processes in visual pop-out terminal windows inside Mission Control
- Full ANSI color support via xterm.js (loaded from CDN) — dashboards, colored output, box-drawing all render correctly
- Agent uses `curl` to POST `/api/terminal/launch` — system prompt teaches this automatically
- Terminal appears as a draggable pop-out window (same pattern as Plan Viewer)
- Stdin input bar below terminal for sending input to running processes
- Stop button to kill processes, status dot shows running/completed/error/stopped
- Terminal sessions survive page refresh — only running sessions reconnect
- SSE streaming for real-time output (same 0.3s poll pattern as agent output)
- Server-side cleanup: atexit kills all terminal processes, delete_project cleans up sessions
- `[terminal:sessionId:command]` marker injected into agent SSE stream triggers auto-open on frontend
- Closing pop-out with X deletes session from server (won't reappear on refresh)
- Minimize/close controls positioned on right side of header bar
- Fixed: newlines in commands no longer break the terminal marker detection

## [2026-03-18c] — AskUserQuestion tool support

- Agent questions now appear as interactive forms in the chat (radio buttons, checkboxes, "Other" text input)
- Server extracts question data from `AskUserQuestion` tool_use blocks in both Mode A and Mode B stream readers
- New `question` SSE event type delivers structured question data to the frontend
- `renderAgentQuestion()` builds interactive form with options matching the tool's schema
- `submitQuestionAnswer()` formats selected answers and sends as follow-up message
- Single-select (radio) and multi-select (checkbox) modes supported
- Form greys out after submission with answer summary
- `_format_tool_activity()` now shows question preview text instead of bare `[tool: AskUserQuestion]`

## [2026-03-18b] — Walkthrough tour improvements

- Header highlight split into two focused steps (logo area + action buttons) instead of one broad highlight
- Enhanced demoTarget sub-element highlighting with accent outline on tab bar and menu button
- Added 4 new menu feature steps: Change Status, Color & Domain, Agent Model, GitHub Sync
- New `wtDemoMenuHTML()` renders virtual modal with menu dropdown open for the menu feature steps
- Tour now has 18 steps (was 13)

## [2026-03-18a] — Snap-to-grid tile arrangement

- Project tiles can be dragged to any grid cell position (Android home screen style)
- Dropping a tile onto another tile swaps their positions
- Empty grid cells (spacers) are invisible but occupy space — creating gaps between tiles
- Ghost preview follows cursor during drag with drop-target highlight
- Double-click an empty cell to remove the gap
- "Compact" button in filter row removes all gaps at once
- Grid layout persisted to server (`/api/grid-layout`) and localStorage
- Touch drag support for mobile devices
- Backlog dispatch triangle now fills current session's input (or +New via textareaValues)

## [2026-03-17i] — Remove Skills system

- Removed Skills tab from project modals (unused — Memory serves the same purpose)
- Removed global Skills manager (header button + modal)
- Removed all Skills API endpoints (global CRUD, project CRUD, attach/detach)
- Removed Skills helper functions and agent context injection from server.py
- Removed Skills CSS styles and JS functions from index.html

## [2026-03-17h] — First-run walkthrough tour

- Spotlight-style walkthrough highlights UI areas one at a time with dimmed backdrop
- 13 steps: welcome, header, new button, stats, project tile, modal, tabs, backlog, agent, menu, feed, console, done
- Sample project created automatically during tour via `POST /api/walkthrough/sample-project` (idempotent)
- Clip-path cutout on backdrop with pulsing accent-glow highlight ring around target elements
- Smart card positioning (top/bottom/left/right) with viewport clamping
- "Don't show again" checkbox on skip — lets users dismiss without completing
- Auto-triggers on first run (zero projects + no localStorage flag)
- Re-triggerable anytime via "Tour" button in header
- Escape key and window resize handling
- Mobile responsive card layout
- Virtual demo tile and modal shown during tour steps (not reliant on real DOM elements)

### Bug fixes
- Plans tab now shows plans from live running sessions, not just completed ones
- Stuck ExitPlanMode loop detection: after 3 consecutive calls, shows warning banner with recovery instructions
- `/api/project/<id>/plans` endpoint checks live `agent_sessions` in addition to on-disk agent log

## [2026-03-17g] — GitHub Issues sync (Phase 1)

### New module: `github_sync.py`
- Bidirectional sync between MC backlog items and GitHub Issues via `gh` CLI
- `sanitize()` strips HTML tags, `javascript:` URIs, null bytes, control chars from all GitHub text
- `validate_repo()` checks format + existence via `gh repo view`
- `gh_run()` safe subprocess wrapper (no shell=True, 30s timeout)
- `_pull_issues()` fetches GitHub issues, maps labels to priority, creates/updates backlog items
- `_push_items()` creates GitHub issues for unlinked MC items, syncs open/closed status
- `sync_project()` orchestrator with 60s rate limit and per-project threading locks

### Backend (`server.py`)
- 4 new endpoints: `/github/setup`, `/github/disconnect`, `/github/sync`, `/github/status`
- Scheduler auto-syncs every 5 minutes for projects with GitHub sync enabled
- All sync events logged to Activity Stream via `_log_agent_activity()`

### Frontend (`static/index.html`)
- GitHub Sync submenu in three-dot menu: connect (owner/repo input), sync now, disconnect
- Sync badge in Backlog section header (clickable to trigger sync)
- `#N` issue link badges on backlog items linked to GitHub issues
- `githubConnect()`, `githubDisconnect()`, `githubSyncNow()` JS functions

### Security
- All GitHub text sanitized before storage (HTML strip, dangerous protocol removal, char limit)
- Repo name validated with strict regex before any subprocess calls
- Subprocess uses argument list (never shell=True)

## [2026-03-17f] — Plan button persistence from agent log

- Plan file button in agent status row now populated from agent log entries
- After agent log loads, any session with a `plan_file` gets it set in status cache
- Ensures plan button shows for sessions that generated plans (even if loaded after initial fetch)

## [2026-03-17e] — Textarea preservation + charmap fix

- Textarea content now preserved across tab switches via global `textareaValues` cache
- Delegated `input` event listener on modal-layer captures values as user types
- Cache cleared on submit (dispatch, followup, backlog add, continue)
- Fixed Windows charmap codec error (`\u2192` arrow) crashing agent dispatch
- Replaced Unicode arrow in scheduler print with ASCII `->` equivalent

## [2026-03-17d] — Resume conversation after stop

- Stop kills the process (both modes), but conversation can be resumed via follow-up
- Mode B followup handler respawns process with `claude -r` when process is dead
- Reverted `CREATE_NEW_PROCESS_GROUP` flag that was breaking Mode B on Windows
- Input placeholder shows "Type to resume conversation..." for stopped sessions

## [2026-03-17c] — Plans History tab + UI polish

### Plans History tab
- New "Plans" tab in project modal shows all historical plan files generated under the project
- Backend persists `plan_file` path in agent log entries on session completion
- `GET /api/project/<id>/plans` endpoint scans agent log for entries with plan files
- `GET /api/plan-file?path=` endpoint reads plan file content (restricted to `~/.claude/plans/`)
- Plan cards show title (extracted from `# heading`), task, and relative timestamp
- Clicking a plan card opens the plan viewer modal with full formatted content
- Empty state shown when no plans exist for a project

### UI polish
- Agent chat follow-up input: added bottom padding to avoid clipping at modal edge
- Default modal tab changed from Backlog to Agent
- Modal resize corner grip made larger (14px desktop, 18px touch) with border-based indicator
- Scheduler modal: restructured header layout so "+ Add Schedule" button doesn't overlap window controls
- Tile dim colors made more vivid/saturated (amber, green, red, purple, accent)
- Plan button title now lazy-fetches the actual plan file `# heading` instead of showing session task text

## [2026-03-17b] — Scheduled Tasks

### Scheduler
- New Scheduled Tasks system: automate agent dispatch at configured times
- Three schedule types: Once (specific datetime), Daily (time + day-of-week), Interval (every N minutes)
- Background scheduler thread checks every 30 seconds and dispatches due tasks
- Extracted `_dispatch_agent_internal()` helper from endpoint for shared use by HTTP and scheduler
- CRUD API: GET/POST/PUT/DELETE `/api/schedules` with `data/schedules.json` storage
- `_compute_next_run()` calculates next execution time for each schedule type
- Scheduler auto-starts on server boot, auto-stops on shutdown via atexit

### Frontend
- "Scheduler" button in header opens modal with schedule list and add/edit form
- Schedule cards show project name, task, schedule description, last/next run times
- Enable/disable toggle per schedule, edit and delete actions
- Add/edit form with project dropdown, task textarea, type selector, day checkboxes (daily), interval input
- **Upcoming jobs banner**: top-of-page bar showing next 5 scheduled tasks with relative countdown times
- Banner auto-refreshes every 60 seconds, hidden when no upcoming schedules

## [2026-03-17a] — Persistent agent process (Mode B) + mobile touch support

### Persistent agent process (Mode B)
- New `use_streaming_agent` config toggle (default: false) enables Mode B alongside existing Mode A
- Mode B uses `--input-format stream-json` to keep a single Claude CLI process alive across turns
- Follow-ups write directly to stdin — no queuing, no process respawn, faster responses
- New `_read_agent_stream_b()` reader treats `result` messages as turn boundaries, not process exit
- New `idle` status: process alive and waiting for input (accent-colored dot with glow)
- SSE sends `turn_complete` events on idle, keeps stream open between turns
- `atexit` handler cleans up persistent processes on server shutdown
- Mode A (spawn-per-turn) unchanged — toggle off to use original behavior

### Mobile touch support
- Modal drag-to-move now works on touch devices (touchstart/touchmove/touchend)
- Separator drag (resize input area) works on touch devices
- Bottom-right corner touch resize for modals (40px hit zone with visual indicator)
- Pinch-to-resize: two-finger gesture scales modal width and height proportionally
- CSS `resize: both` disabled on touch devices (replaced by touch handlers)

### UI fixes
- Send button stays fixed size when expanding textarea (flex align-items: flex-end)
- Image previews now clear from DOM after sending follow-up
- Textarea resize handle removed (resize: none) — separator bar is the only resize control
- Agent output gets `flex: 1; min-height: 0` for proper flex sizing
- Queued follow-up echo shows yellow border + hint text (Mode A only)

## [2026-03-16d] — Full-height agent chat + performance overhaul

### Full-height agent chat
- Agent chat now fills the entire modal window height instead of fixed 450px
- `sizeAgentChat()` calculates available height dynamically and sets explicit pixel height
- ResizeObserver on modal content triggers re-sizing on window/modal resize
- Chat opens scrolled to the bottom showing latest messages
- 8px buffer between input area and modal bottom edge

### Draggable separator
- Replaced counter-intuitive bottom-corner resize handle with a draggable separator bar
- Separator sits between output and input areas — drag up/down to resize input
- Visual indicator (thin bar) with hover highlight

### Follow-up performance — non-blocking sends
- `sendFollowup()` is now fire-and-forget — no `await`, no `refreshModal()` call
- Local echo: user message appears instantly in DOM (`.agent-echo` class) before API responds
- Echo removed when server's version arrives via SSE (deduplication)
- Lightweight `updateAgentStatusUI()` replaces full modal rebuild for status changes

### Server-side performance
- Flask runs with `threaded=True` — SSE streams no longer block other requests
- Follow-up subprocess spawned in background thread — endpoint returns immediately
- SSE `since` parameter prevents replay of all historical lines on reconnect

### Long-running session optimizations
- DOM preservation in `refreshModalById()` — agent output element detached before `innerHTML` wipe, reattached after rebuild
- `_skipAgentOutput` flag skips expensive output line processing during preserved rebuilds
- Agent output DOM limited to 500 lines in modal, 200 in console tile, with "click to load all" button
- `agentOutputBuffers` capped at 2000 entries (trimmed to 1500 when exceeded)
- `renderAgentConsole()` optimized: skips line processing when panel is closed, efficient reverse-loop for lastTool

## [2026-03-16c] — Use Claude's native MEMORY.md for project memory

### Native memory integration
- Memory tab now reads/writes Claude Code's native `~/.claude/projects/<encoded-path>/memory/MEMORY.md`
- Path derived from project's `project_path` — same file the agent writes to with its Edit tool
- Fallback to `data/memory/<project_id>.md` for projects without a project_path
- Memory tab shows the resolved file path for transparency
- Auto-memory on session completion writes to the native location
- Agent system prompt simplified: tells agent the memory file path, no more curl API instructions
- Single source of truth — agents and dashboard share the same memory file

## [2026-03-16b] — Robust memory: append endpoint + auto-memory

### Memory append endpoint
- New `POST /api/project/<pid>/memory/append` — safely appends content without overwriting
- Agents can append to memory in one call instead of read-then-write
- Agent system prompt updated with all three memory API commands (read, append, replace)

### Auto-memory on session completion
- `_log_agent_completion()` now auto-appends a `## Session Log` entry to project memory
- Each entry: date, task name, brief summary (first 300 chars)
- Fails silently — never blocks the completion flow
- Memory builds passively even if the agent doesn't explicitly write to it

## [2026-03-16a] — Skills + Memory system

### Memory system
- New **Memory tab** in project modals — persistent per-project markdown memory
- Memory content injected into agent context as `--- PROJECT MEMORY ---`
- Backend: `GET/PUT /api/project/<pid>/memory` endpoints
- Storage: `data/memory/<project_id>.md` (one markdown file per project)
- Lazy-loaded on first tab visit, textarea with save button

### Skills system
- New **Skills tab** in project modals — manage project-scoped and attached global skills
- **Global Skills Manager** — header-level "Skills" button opens dedicated modal for managing global skills
- Skills are reusable prompt templates with name, description, and markdown content
- Skills injected into agent context as `--- SKILL: <name> ---` sections
- Two scopes: **project skills** (specific to one project) and **global skills** (shared, attachable to any project)
- Attach/detach global skills per project from the Skills tab
- Inline create/edit forms for both project and global skills
- Filter support in Skills tab via existing search bar
- Backend: Full CRUD for global skills (`/api/skills/global`), project skills (`/api/project/<pid>/skills`), and attach/detach endpoints
- Storage: `data/skills/global/*.json`, `data/skills/project/<pid>/*.json`, `data/skills/attachments.json`

### Context injection
- `_build_agent_context()` now includes project memory and resolved skills in agent system prompt
- Skills resolved per-project: all project-scoped skills + explicitly attached global skills

## [2026-03-15d] — Package as standalone Windows .exe

### Desktop mode (app.py)
- New `app.py` entry point: starts Flask in daemon thread, opens native pywebview window
- First-run creates `%APPDATA%\MissionControl\data\{projects,uploads}\` and `config.json`
- Auto-installs Claude CLI if missing (via npm, or winget→Node.js→npm fallback)
- Shows non-blocking alert in webview if CLI install fails (app still usable)
- Web interface remains accessible at `http://localhost:5199` while native window is open

### Dual-directory system (server.py)
- Replaced `BASE_DIR` with `_APP_DIR` (bundled assets) and `_DATA_ROOT` (user data)
- Frozen mode: `_APP_DIR = sys._MEIPASS`, `_DATA_ROOT = %APPDATA%\MissionControl`
- Dev mode: both point to repo root — fully backward-compatible
- `MC_DATA_DIR` env var overrides data root for custom deployments

### Build & packaging
- `build.spec` — PyInstaller `--onedir` spec (bundles server.py + static/index.html, console=False)
- `installer.iss` — Inno Setup script (per-user install, Start Menu + Desktop shortcuts, post-install launch)
- `build.bat` — Automated build: pip install deps → pyinstaller → prints Inno Setup instructions
- `requirements.txt` — Added `pywebview>=5.0`

## [2026-03-15c] — User-configurable modal header color

### Modal accent color
- Modal header left accent bar is now user-configurable per project (decoupled from status)
- "Change Color" submenu added to three-dot menu between "Change Status" and "Change Domain"
- Shows 6 color swatches (Blue, Purple, Green, Amber, Red, Gray) using existing `COLOR_PRESETS`
- Current color highlighted with thicker border
- Color saved as `modal_color: {color, bg}` on project JSON
- Default: Blue (`var(--accent)`) for projects without a chosen color
- CSS: Replaced 4 `.modal-header.status-*::before` rules with single `var(--modal-accent)` custom property
- Tile cards in grid also use chosen color via `--card-accent` inline override (falls back to status color)
- Status pill text in modal unchanged — still shows status with correct styling
- Function: `setProjectColor(projectId, color, bg)`

## [2026-03-15b] — Token tracking, live timer, enter key mode, UX refinements

### Three-dot modal menu
- Added three-dot menu button (vertical ellipsis) to project modal header controls
- Menu items: Change Status (Active/Waiting/Blocked/Parked submenu), Edit/Add Description, Delete Project
- Status submenu shows colored dots and highlights current status
- Delete Project is danger-styled with confirmation dialog
- Functions: `toggleModalMenu()`, `toggleModalMenuSub()`, `setProjectStatus()`, `editProjectDescription()`, `deleteProject()`
- CSS: `.modal-menu-btn`, `.modal-menu-dropdown`, `.modal-menu-item`, `.modal-menu-sep`, `.modal-menu-sub`, `.modal-menu-sub-item`, `.modal-menu-sub-dot`

### Token usage tracking
- Captures `usage`, `cost_usd`, `num_turns` from Claude CLI `result` message in `_read_agent_stream()`
- Persists usage data in agent log entries via `_log_agent_completion()`
- Exposes usage in `agent_status()` API and SSE completion messages
- Global token counter in header bar (lightning bolt badge) with total tokens + cost
- Per-session token/cost badge in Agent tab status row (appears on session completion)
- Token/cost inline in Agent Log entries (after timestamp)
- Helper functions: `formatTokens()` (1.2k/1.2M), `formatCost()`, `tokenBadgeHTML()`, `sessionMetricsHTML()`
- CSS: `.token-counter-global`, `.tc-icon`, `.tc-cost`, `.tc-mode`, `.token-badge`, `.agent-log-usage`

### Token counter time range selector
- Click the global token counter to switch between: All Time, Today, This Week, This Month
- Context menu with checkmark on active mode
- Mode persisted in `localStorage` (`tc_mode` key)
- Server: `/api/usage` endpoint accepts `?since=<ISO timestamp>` for time-filtered aggregation
- Functions: `getTokenSince()`, `fetchGlobalUsage()`, `openTokenContextMenu()`, `setTokenMode()`
- `TOKEN_MODES` constant; `tokenCounterMode` state variable

### Live elapsed timer for running sessions
- Running agent sessions show `⏱ 0s` → `⏱ 1m 23s` → `⏱ 1h 5m` ticking every second
- Transitions to token count + cost when session completes
- Functions: `formatElapsed()`, `sessionMetricsHTML()`
- 1-second `setInterval` updates all running session timer elements

### Enter key mode toggle
- Configurable send behavior: "Ctrl+Enter sends" (default) or "Enter sends" (Shift+Enter for newline)
- Accessible from three-dot modal menu → "Enter Key" submenu (shows current mode inline)
- Global setting persisted in `localStorage` (`enter_mode` key)
- Applied to all 4 textareas: agent dispatch, follow-up, agent log continue, backlog input
- Functions: `handleInputEnter()`, `setEnterMode()`
- Removed standalone right-click context menu — native right-click restored on textareas

### Project delete endpoint
- Server: `DELETE /api/project/<project_id>` — cleans up attachment files, agent log JSON, kills running agent sessions, deletes project file
- Frontend: `deleteProject()` calls API, closes modal, refreshes dashboard

### Bug fixes
- Fixed stale token count showing on follow-up dispatch (usage/cost cleared from cache when session resumes)
- Fixed `agent_session_delete` — stream reader thread handles completion logging, delete handler just removes from tracking

### Files Changed
- server.py: `_read_agent_stream()` usage capture, `_log_agent_completion()` usage persistence, `agent_status()` usage fields, SSE status message includes usage, `DELETE /api/project/<id>` endpoint, `GET /api/usage` endpoint with `?since=` filter, `agent_session_delete` logging fix
- static/index.html: Three-dot menu system, token counter with click-to-switch time range, live elapsed timer, enter key mode toggle, session metrics badge, context menu CSS/JS, all textarea onkeydown handlers unified

---

## [2026-03-15] — Domain management moved to three-dot menu and new project form

### Done
- Moved domain selection from clickable pill to three-dot menu "Change Domain" submenu
- Domain submenu shows all domains with colored dots, color picker swatches, and "New domain..." input
- Domain pill in modal header is now display-only (no longer clickable)
- Replaced `<select>` in new project form with rich domain picker matching the menu style
- New project domain picker includes domain list, color swatches, and new domain creation
- Removed old `toggleDomainDropdown()`, `saveDomain()`, `addDomainFromDropdown()`, `setDomainColor()` functions
- Added `saveDomainFromMenu()`, `addDomainFromMenu()`, `setDomainColorFromMenu()` for modal menu
- Added `toggleNewProjDomain()`, `selectNewProjDomain()`, `addNewProjDomainEntry()`, `setNewProjDomainColor()`, `refreshNewProjDomainTrigger()` for new project form
- `newProjDomain` state variable tracks selection; reset to `'general'` on form open and after creation
- Removed old CSS: `.domain-select-wrap`, `.domain-tag.editable`, `.domain-dropdown`, `.domain-dropdown-item`
- Added new CSS: `.new-proj-domain-wrap`, `.new-proj-domain-trigger`, `.new-proj-domain-dd`, `.new-proj-domain-item`

### Files Changed
- static/index.html: Domain submenu in three-dot menu, display-only pill, rich domain picker in new project form, replaced old domain CSS with new `.new-proj-domain-*` classes

---

## [2026-03-14] — Three-dot menu, token tracking, session resume, enter key mode, dynamic domains

### Three-dot modal menu (new)
- Built the three-dot menu system for project modals (button, dropdown, submenus)
- Menu items: Change Status (submenu), Change Domain (submenu), Agent Model (submenu), Edit/Add Description, Delete Project
- CSS: `.modal-menu-btn`, `.modal-menu-dropdown`, `.modal-menu-item`, `.modal-menu-sep`, `.modal-menu-sub`, `.modal-menu-sub-item`, `.modal-menu-sub-dot`
- Functions: `toggleModalMenu()`, `toggleModalMenuSub()`, `setProjectStatus()`, `editProjectDescription()`

### Token usage tracking (new)
- Global token counter in header showing input/output tokens and USD cost
- Right-click context menu to switch time range: All, Today, This Week, This Month
- Per-session token badge in agent status row (tokens + cost after completion)
- Token/cost display in agent log entries
- `tokenCounterMode` persisted in localStorage; `TOKEN_MODES` constant
- Functions: `formatTokens()`, `formatCost()`, `tokenBadgeHTML()`, `getTokenSince()`, `fetchGlobalUsage()`, `openTokenContextMenu()`, `setTokenMode()`, `formatElapsed()`, `sessionMetricsHTML()`
- CSS: `.token-counter-global`, `.tc-icon`, `.tc-cost`, `.tc-mode`, `.tc-context-menu`, `.token-badge`, `.agent-log-usage`
- Server: new `GET /api/usage` endpoint aggregates tokens/cost across all agent logs and running sessions (supports `?since=` filter)
- Server: `_read_agent_stream()` captures `usage`, `cost_usd`, `num_turns` from Claude result messages
- Server: `_log_agent_completion()` persists usage data; SSE status messages include usage; `agent_status()` exposes usage

### Session resume picker
- Session picker UI when opening Agent tab or clicking "+ New": radio buttons for prior sessions to resume
- Most recent session pre-selected by default; "Fresh session" available as explicit choice
- Deduplicated entries (follow-ups no longer show as separate entries)
- Dispatch button label changes to "Continue" when resuming; default task text becomes "Continue where we left off."
- `pendingResumeId` state; `getDefaultResumeId()`, `selectResumeSession()`, `sessionPickerHTML()` functions
- `agentHistory` entries store `resumedFrom` field; `dispatchAgent()` sends `resume_conversation_id`
- CSS: `.session-picker`, `.session-picker-opt`, `.resume-indicator`

### Per-project agent model
- Agent Model submenu in three-dot menu (Sonnet 4.5, Opus 4.6, Haiku 4.5, or global default)
- Per-project `agent_model` overrides global config for all dispatch/follow-up paths
- Server: `_build_claude_flags(project)` accepts per-project override; all 4 Popen call sites pass project

### Enter key mode toggle (new)
- Configurable send behavior: "Enter sends" vs "Ctrl+Enter sends" (default)
- Right-click context menu on all agent/backlog textareas to switch mode
- `enterKeyMode` persisted in localStorage; `handleInputEnter()`, `openInputContextMenu()`, `setEnterMode()` functions
- Applied to backlog input, agent task input, agent follow-up, agent log continue textareas

### Dynamic domain system (new)
- Domains fetched from server settings instead of hardcoded CSS classes
- Domain filter buttons dynamically rendered via `renderDomainFilters()`
- `domainsList` state; `fetchDomains()`, `getDomainConfig()`, `renderDomainFilters()` functions
- `COLOR_PRESETS` constant (Blue, Purple, Green, Amber, Red, Gray)
- Domain tags in tiles and modals use inline styles from `getDomainConfig()` instead of CSS classes
- Server: `SETTINGS_PATH` (`data/settings.json`), `DEFAULT_DOMAINS`, `_load_settings()`, `_save_settings()`
- Server endpoints: `GET /api/settings/domains`, `POST /api/settings/domains/add`, `PATCH /api/settings/domains/<id>`, `DELETE /api/settings/domains/<id>`

### Project delete
- Delete Project option in three-dot menu (danger-styled, with confirmation dialog)
- `deleteProject()` function calls `DELETE /api/project/{id}`, closes modal, refreshes
- Server: `DELETE /api/project/<id>` cleans up attachment files, agent log, kills running sessions, deletes project JSON

### Plan file label
- `planFileLabel()` generates a display label from task description (truncated, capitalized)
- `openPlanFileViewer()` extracts first markdown heading from plan content as viewer title

### Windows process window hiding
- `_POPEN_FLAGS` uses `CREATE_NO_WINDOW` (not `DETACHED_PROCESS`); `_STARTUPINFO` with `SW_HIDE`
- `_hide_process_windows()` uses ctypes to enumerate and hide windows by PID
- `_hide_windows_delayed()` runs in background thread, calling hide 6 times over ~2.5 seconds
- Background thread spawned after every Popen call (4 sites: dispatch, followup, auto-followup, and agent_followup)
- `stdin=subprocess.DEVNULL` added to all Popen calls

### Misc fixes
- Fixed agent image preview remove button not appearing on hover (CSS selector mismatch)
- Agent dispatch activity log now includes resume label
- 1-second interval timer updates elapsed time displays for running sessions

### Files Changed
- server.py: Three-dot menu backend (delete project, domain CRUD, usage endpoint), `_build_claude_flags(project)` per-project model, token/usage capture in stream reader and completion logger, `_POPEN_FLAGS`/`_STARTUPINFO`/`_hide_process_windows()`/`_hide_windows_delayed()`, `stdin=DEVNULL` on all Popen calls
- static/index.html: Three-dot menu system, token counter UI + context menu, session resume picker, enter key mode toggle, dynamic domain system, plan file labels, CSS for all new components

---

## [2026-03-13] — User and agent name settings

### Done
- Added `user_name` and `agent_name` to config.json defaults
- User name replaces hardcoded "Ron" in agent log lines (falls back to "User")
- Agent name and user name injected into agent system prompt context
- Added settings 7 (Your name) and 8 (Agent name) to both installer scripts
- Settings shown in post-install summary

### Files Changed
- server.py: New config defaults, replaced hardcoded "Ron" with `user_name`, inject names into `_build_agent_context()`
- install.bat: Added prompts 7-8, updated config.json writer and summary
- install.sh: Added prompts 7-8, updated config.json writer and summary

---

## [2026-03-13] — Open-source release preparation

### Done
- Replaced hardcoded user paths (`<install dir>`) with `config.json` configuration system
- `config.json` auto-created on first run with sensible defaults (gitignored)
- Server port configurable via `config.json` or `MC_PORT` environment variable (default 5199)
- Set Flask `debug=False` for production
- Removed test injection function (`injectTestPlan`)
- Deleted personal/temporary files (helper scripts, session context, zip artifacts)
- Created `.gitignore`, `requirements.txt`, `LICENSE` (MIT), comprehensive `README.md`
- Created installer scripts: `install.bat` (Windows) and `install.sh` (macOS/Linux)
- Created launcher scripts: `start.bat` (Windows) and `start.sh` (macOS/Linux)
- Installers check prerequisites (Python, pip, Claude CLI), install dependencies, create data dirs
- Added `.gitkeep` files for `data/projects/` and `data/uploads/` directories

### Files Changed
- server.py: Replaced hardcoded `SHARED_RULES_PATH` and `PROJECTS_BASE` with config.json loader; port from config/env; `debug=False`
- static/index.html: Removed `injectTestPlan()` test function

### Files Added
- `.gitignore`, `requirements.txt`, `LICENSE`, `README.md`
- `install.bat`, `install.sh`, `start.bat`, `start.sh`
- `data/projects/.gitkeep`, `data/uploads/.gitkeep`

### Files Removed
- `fix_feed.py`, `patch_attachments.py`, `files.zip`, `frve.json`
- `patch_err.txt`, `patch_out.txt`, `.claude_session_context.md`, `SHARED_RULES_SNIPPET.md`

---

## [2026-03-13 16:30 ET] — Tab search/filter field

### Done
- Search input in the tab bar (right-aligned) for Backlog, Agent Log, and Activity tabs
- Live filtering on keystroke — hides non-matching items via DOM (no re-render)
- Searches backlog item text, agent log task+summary, activity log messages
- Per-project state persists across tab switches and auto-refreshes
- Clear (X) button appears when query is active
- Hidden on Agent tab (agent output is better served by different UX)

### How it works
- `modalSearchQuery[projectId]` stores the filter string per project
- `applyTabFilter()` reads query + active tab, shows/hides matching DOM elements
- Filter reapplied at end of `refreshModalById()` so it survives periodic re-renders
- Input focus and value preserved via extended textarea save/restore in refresh cycle

### Files Changed
- static/index.html: CSS `.modal-tab-search`, search input in tab bar template, `applyTabFilter()` / `clearTabSearch()` / `findModalIdForProject()` functions, `refreshModalById()` filter reapplication + input preservation

---

## [2026-03-13 16:15 ET] — Fix agent session hang on server restart

### Problem
When `server.py` was edited (triggering Flask's debug auto-reloader), the server process restarted and wiped all in-memory `agent_sessions`. Running agent sessions in the browser UI would freeze in a permanent "running" state because:
1. SSE connection broke → frontend retried indefinitely with no cap
2. Polling fallback silently skipped sessions not found on the server (`if (!ss) continue`)
3. No code path transitioned "running" → error when the server lost the session

### Fixes
- **Polling fallback** — when a session the frontend thinks is "running" is missing from the server entirely, mark it as `error` and refresh the UI (instead of silently skipping)
- **SSE reconnect retry cap** — max 3 retries with increasing delay (2s, 4s, 6s); after that, mark the session as errored and stop retrying
- **Retry counter cleanup** — `sseRetryCount[sessionId]` resets on successful data, and is deleted on normal completion or error

### Files Changed
- static/index.html: polling fallback (setInterval block), `connectAgentStream()` es.onerror/onmessage handlers, new `sseRetryCount` state variable

---

## [2026-03-13 16:00 ET] — Continue session from Agent Log

### Done
- "Continue" button on each Agent Log entry (when claude_session_id exists)
- Clicking expands an inline textarea to type a follow-up message
- Dispatches a new agent session that resumes the old conversation via `claude -r <id>`
- Automatically switches to Agent tab to show the running session
- Ctrl+Enter shortcut to send from the textarea

### How it works
- Backend `agent_dispatch()` accepts optional `resume_conversation_id` in POST body
- When present, builds `claude -r <id> -p <message>` instead of `claude -p <task>` (skips `--append-system-prompt` since resumed conversation already has context)
- Frontend `dispatchContinue()` mirrors `dispatchAgent()` but passes `resume_conversation_id` and switches tab

### Files Changed
- server.py: `agent_dispatch()` — read `resume_conversation_id`, conditional cmd build
- static/index.html: CSS for `.agent-log-continue-btn` and `.agent-log-continue-input`, updated `agentLogPanelHTML()` entries, new `toggleContinueInput()` and `dispatchContinue()` functions

---

## [2026-03-13 14:30 ET] — Plan file viewer button

### Done
- When an agent edits a `.md` file and then calls `ExitPlanMode`, a purple button with the filename appears in the agent status row
- Clicking the button opens the actual plan file content in a dedicated viewer modal (reads the `.md` file from disk)
- Separate from the "Pop Out" button which still shows the full conversation
- Button persists across page refreshes (plan_file stored in session status)

### How it works
- Server tracks the last `.md` file touched by Write/Edit tool calls during agent stream
- When `ExitPlanMode` is called, the tracked file path is stored as `plan_file` on the session
- New endpoint `GET /api/project/{pid}/agent/plan-file?session={sid}` reads and returns the file content
- Frontend detects the plan file both on live SSE (fetches status after ExitPlanMode) and on re-render (from cached status)

### Files Changed
- server.py: Track `.md` edits in `_read_agent_stream()`, new `/agent/plan-file` endpoint, `plan_file` in status response
- static/index.html: `openPlanFileViewer()` function, `.btn-plan-file` CSS, plan file button in status row, live detection on ExitPlanMode

---

## [2026-03-13 10:39 ET] — Ctrl+Scroll zoom on agent output

### Done
- Ctrl+Scroll over agent chat output areas zooms text in/out (8px–24px range, default 12px)
- Applies to both `.agent-output` and `.ac-session-output` elements
- Zoom level is per-modal — each window maintains its own independent zoom
- Zoom persists through content refreshes (SSE updates, tab switches, etc.)

### Files Changed
- static/index.html: Added `modalZoomLevels` state (per-modal), `wheel` event listener on `#modal-layer` with Ctrl detection, zoom reapply in `refreshModalById()`

---

## [2026-03-12 15:00 ET] — Plan Viewer window

### Done
- Agent plan output is now hidden from the chat window — replaced by a purple **"Show Plan"** button
- Clicking the button opens a dedicated **Plan Viewer** modal (1000px wide, 85vh tall) for easier reading
- Detection: when `[tool: ExitPlanMode]` appears in the stream, all preceding non-tool text lines are identified as the plan and collapsed
- Plan viewer renders with full rich formatting: markdown headers, tables, code blocks, lists
- **"Pop Out"** button always visible in the agent panel status row — opens any session's output in the wider viewer
- Works on page refresh: static HTML builder also detects and collapses plan content
- Plan viewer is draggable, minimizable, resizable — follows the same modal system as project windows

### Files Changed
- static/index.html: Added `.plan-viewer-content`, `.plan-show-btn`, `.plan-hidden-block`, `.btn-popout` CSS; added `planViewerContent` state; modified `appendAgentLine()` to detect `[tool: ExitPlanMode]`; new `collapseIntoPlanButton()` function; modified static output builder in `agentPanelHTML()` for refresh-safe plan detection; new `openPlanViewer()` function; added Pop Out button to agent status row

---

## [2026-03-12 14:00 ET] — Tabbed modal layout + auto-size name input

### Done
- Modal sections now organized into 4 tabs: **Backlog**, **Agent**, **Agent Log**, **Activity**
- Tab bar sits between the header/summary and scrollable content area
- Header (name, status, domain, path, description) and summary (current task, next action) stay always visible above tabs
- Each tab gets full scroll area — no more scrolling past unrelated sections
- Agent Log tab lazy-loads completed sessions on first click
- Rules panel stays inside Agent tab (collapsible)
- Activity log expanded from 6 to 20 entries
- Project name input auto-sizes to fit text content (removed `flex: 1`)
- More drag area in header since name input no longer stretches full-width
- Backlog count badge shown in tab bar
- Modal structure changed from single scroll to flex column (fixed header + tab bar, scrollable body)

### Files Changed
- static/index.html: Added `modalActiveTab` state, `switchModalTab()`, `autoSizeNameInput()` functions; new CSS for `.modal-tab-bar`, `.modal-tab`, `.modal-tab-content`, `.modal-scroll-body`, `.name-measure`; restructured `modalContentHTML()` return template; `.modal-content` now flex column with `overflow: hidden`; `.modal-header` no longer sticky (not needed — it's in non-scrolling region); simplified `agentLogPanelHTML()` (removed collapsible wrapper); updated `refreshModalById()`, `minimizeModal()`, `restoreModal()` for new scroll container

---

## [2026-03-12 13:15 ET] — Proper HTML table rendering for pipe-delimited tables

### Done
- Pipe-delimited markdown tables (`| col | col |`) now render as actual HTML `<table>` elements with proper column alignment
- Header rows detected via separator lines (`|---|---|`) and styled with blue text + bold weight
- Box-drawing tables (Unicode `┌─┬─┐`) still render as pre-formatted blocks with colored borders
- Sticky modal header: project name, status, domain, path all stay pinned at top when scrolling modal content
- Modal header has distinct background (`#1e2230`) to visually separate from content
- Minimize/close buttons moved inside the sticky header
- User prompts with `\n` wrapping (follow-ups) now correctly match prompt styling via `trim()`
- Queued follow-up detection fixed (check order was shadowed by general `> ` match)
- Page refresh no longer kills running agent processes (removed `sendBeacon` kill in `beforeunload`)

### Files Changed
- static/index.html: Replaced `formatTableLine` pre-rendering with `buildPipeTable()` HTML table parser; added `isPipeTable()`, `isSeparatorLine()` helpers; updated all 4 render paths; new `.hl-table table/th/td` CSS; `.hl-table-pre` for box-drawing fallback; sticky `.modal-header`; controls moved inside header; `agentLineCls` uses `trim()` and reordered checks; removed `sendBeacon` kill from `beforeunload`

---

## [2026-03-12 12:35 ET] — Fix agent chat resize direction

### Done
- Moved resize handle from top edge to bottom edge of agent chat box
- Flipped drag direction so dragging down = expand, dragging up = shrink (matches visual result)

### Files Changed
- static/index.html: Changed `.agent-chat-resize` from `top: -4px` to `bottom: -4px`; flipped `dy` calculation in mousemove handler

---

## [2026-03-12 12:30 ET] — ASCII table rendering in agent chat

### Done
- ASCII tables (pipe-delimited and Unicode box-drawing) now render in a styled block with preserved alignment
- Consecutive table lines are grouped into a single `<div class="hl-table">` with `white-space: pre` and `overflow-x: auto`
- Blank lines between table rows stay inside the table block instead of breaking it apart
- Pipes colored blue, border characters in slate gray for visual clarity
- Table lines skip `formatAgentText()` regex to prevent corruption (e.g., `-` as bullet, `*` as bold)
- Applied to all 4 render paths: modal live stream, console live stream, modal batch, console batch
- Added `overflow-x: hidden` and `min-width: 0` on `.agent-output` so wide tables scroll within their own `.hl-table` block instead of clipping
- Added `max-width: 100%` on `.hl-table` to constrain to parent and show horizontal scrollbar

### Files Changed
- static/index.html: Added `.hl-table` CSS for both `.agent-output` and `.ac-session-output`; added `isTableLine()` and `formatTableLine()` functions; updated `appendAgentLine()`, `updateConsoleOutput()`, and both batch renderers to group table lines; added overflow containment on `.agent-output`

---

## [2026-03-12 11:15 ET] — Resizable agent chat panel

### Done
- Agent chat area (`.agent-chat`) now has a draggable resize handle at its bottom edge
- Drag downward to expand, upward to shrink (min 120px, max 80vh)
- Handle shows a subtle bar indicator that highlights blue on hover

### Files Changed
- static/index.html: Changed `.agent-chat` from `max-height: 450px` to `height: 450px` with `min-height`/`max-height`; added `.agent-chat-resize` handle element + CSS; added `chatResize` mousedown/mousemove/mouseup logic

---

## [2026-03-12 11:00 ET] — Multi-modal windows with minimize

### Done
- Converted single-overlay modal to floating window manager: multiple project modals can be open simultaneously
- Each modal top bar now has minimize (horizontal bar) + close (X) buttons
- Minimize collapses modal to a chip in a bottom tray; click chip to restore, chip X to close
- Focus management: clicking a modal brings it to front (accent border), ESC closes only the focused modal
- Modals cascade-offset (+30px) when opened so they don't stack directly on top of each other
- Grid remains visible and scrollable underneath open modals (no blocking overlay)
- Drag-to-move and resize preserved per-modal
- Shared Rules editor and New Project form also participate in the multi-modal system
- All existing features preserved: agent panels, editable fields, textarea value preservation across refresh

### Files Changed
- static/index.html: Replaced `.modal-overlay` with `.modal-layer` + `.modal-window` system; added `.minimized-tray` and `.minimized-chip` CSS; new state (`openModals` Map, `focusedModalId`, `nextModalZ`); new functions (`openProjectModal`, `closeModalById`, `minimizeModal`, `restoreModal`, `focusModal`, `refreshModalById`, `centerModalElement`); updated drag handler for multi-modal delegation; converted `openSharedRulesEditor` and `openNewProjectForm`

---

## [2026-03-11 20:30 ET] — Agent log: Claude session ID tracking

### Done
- Capture real Claude CLI session UUID from stream-json `init`/`result` messages
- Persist `claude_session_id` in agent log entries and agent status API
- Display session ID in agent log UI with `claude -r <uuid>` hint and copy button
- Feed last 5 agent sessions (with resume IDs) into agent context prompt for continuity

### Files Changed
- server.py: `_read_agent_stream` (capture UUID), `_log_agent_completion` (persist), `agent_status` (expose), `_build_agent_context` (include in prompt)
- static/index.html: CSS for `.agent-log-session-id`, agent log entry template updated

---

## [2026-03-11 20:20 ET] — Project changelog created

### Done
- Created CHANGELOG.md for Mission Control project

### State
- Mission Control is a Tauri v2 desktop app with a Flask (Python) backend on port 5199
- Single-page dashboard (static/index.html) with dark theme, Inter/JetBrains Mono fonts
- Backend features: project CRUD, backlog management, file attachments, agent dispatch via Claude CLI, SSE streaming, follow-up/stop, agent log, project import from CHANGELOG.md, rules editor (AGENT_RULES.md + SHARED_RULES.md), project reordering
- Data stored as JSON files in data/projects/, uploads in data/uploads/

### Next
- Multi-session agent tabs, agent log, image paste, project import (current task per system context)

### Files Changed
- CHANGELOG.md (created)
