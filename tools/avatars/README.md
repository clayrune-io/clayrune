# Avatar pipeline

Turns a generated 3×3 character sheet into nine transparent avatars the app can
serve. The generator emits RGB on a soft grey backdrop with a baked ground
shadow; this produces square RGBA WebPs.

```bash
python tools/avatars/slice_sheet.py <sheet.jpg> \
  --names "wizard,chef,smith,gardener,bard,guard,scholar,dancer,angler"
```

Output: `assets/avatars/<name>.webp`, 256px, ~10-17 KB each. That directory sits
under the repo root, which is on `/api/serve-image`'s allowlist.

## Two things this got wrong first, both worth knowing

**Do not propagate the reference colour.** The first version carried each
pixel's colour forward as the reference for its neighbours, which makes the
tolerance cumulative — neighbours on a smoothly shaded figure are always within
tolerance of each other, so the fill walked off the backdrop, through the body
and out the far side. It left about 1% of each figure standing. The reference is
a fixed palette sampled from the border.

**Distance alone cannot separate a cool figure from a warm backdrop.** The
scholar is blue-grey at almost exactly the backdrop's brightness; sum-of-
absolute difference across that boundary is ~30, so the fill ate its face and
the top of its head at *every* tolerance in the sweep, including the lowest.
`R − B` separates them cleanly (backdrop ≈ +13, figure ≈ −12), so a pixel counts
as background only when it matches in distance **and** in warmth. Scholar went
24% → 35.7% coverage, in line with the other eight.

## Pick the tolerance by sweeping, not by eye

Coverage per figure against tolerance shows a plateau where only backdrop is
being removed, then an uneven collapse where figures start being eaten:

```
 tol  wizard    chef   smith  garden    bard   guard  schola  dancer  angler
   8   38.9%   39.9%   39.0%   36.6%   36.5%   40.1%   29.4%   39.3%   34.2%
  11   37.2%   39.5%   38.6%   35.6%   36.0%   38.5%   24.2%   37.1%   33.8%
  20   32.9%   22.0%   35.3%   23.7%   35.1%   35.7%   20.7%   21.9%   29.0%
  30   30.1%   17.2%   27.4%   19.4%   29.1%   29.5%   18.7%   18.8%   17.9%
```

The default (10) is that knee. The script prints coverage per figure and flags
anything outside 20–55%: below means the fill ate the body, above means it never
started. Eyeballing is what let the first bug ship.

## Coverage is a smoke alarm, not a verdict — LOOK at the output

The 20–55% flag catches a fill that ran away. It does **not** catch a figure
that was hollowed out while staying in band. On the 2026-08-25 sheet a grey
`locksmith` keyed at **24.8%** — inside the pass band, no warning — with the
whole lit half of its body and its shield gone. It was found by compositing the
cast onto a cream card and looking, which is now the last step of the pipeline.

**Some cells cannot be keyed at all, and that is the generator's problem.**
Measured on that figure: its lit side is `rgb(166,159,151)`, warmth `15`;
the backdrop beside it is `rgb(172,166,154)`, warmth `18`. Sum-of-absolute
difference **16**, warmth delta **3** — inside *both* gates at every tolerance
in the sweep, including the lowest. No colour rule can separate a neutral figure
lit to the backdrop's own value. Sweeping the tolerance on that cell is wasted
effort (24.8% → 23.8% across tol 4–7, hollow throughout).

When that happens, **re-render the cell** — a different body colour, or a darker
backdrop — rather than hand-patching alpha. Six cells were dropped for this on
2026-08-25: `locksmith` and `navigator`'s compass from the 3x3 sheet, then
`king` (5.3% at tol 10, still hollow at 15.3% by tol 6), `queen`, and the
blonde `potter`, whose hair is the backdrop's own colour.

The pattern in all of them: a **neutral or pale** figure. Warm terracotta,
green and deep blue cells key at the default every time; grey robes, grey
shields and blonde hair are the ones to check first.

## "Transparent background" gets you a PAINTED checkerboard — the tool refuses it

Asked for a transparent background, the generator draws the transparency
checker instead, in ordinary opaque pixels. Three sheets in a row came back
that way on 2026-08-25, every one reporting `alpha extrema (255, 255)` — fully
opaque. The failure is invisible without checking: the file opens, the tool
runs, the output is quietly ruined.

`detect_checker()` now spots it and `slice_sheet.py` **refuses to run**, naming
the square size and both tones, unless you pass `--force`.

**A checker cannot be keyed, even though it looks like the easy case.** It
spans TWO tones, so whichever one you tune for the other collides with
something. On the dark sheet (74 / 112 grey) the fill entered ten of twelve
figures — not through their lit sides but through their own *shaded* sides,
which are dark and neutral and sit exactly on the dark square. No tolerance
helps: at 6 the figures are already chewed, at 18 they are gone.

**Keying against the pattern instead of a palette was tried, and does not
rescue it.** The idea is sound — a dark figure pixel standing in a LIGHT square
is protected outright, and a fill that slips through a dark square is stopped
by the next light one. But the squares are *drawn*, not computed: transitions
run 19, 19, 20, 19… so a phase fixed at the sheet origin has drifted half a
cell by the bottom row and the model inverts, clearing the light squares and
leaving the dark ones as a residual grid. Re-detecting the phase per cell
narrows the drift, still leaves that grid, and still destroyed one figure.

**Ask for this instead** — and never use the word "transparent", which is what
makes it draw the checker:

> Same character sheet, same figures. **No ground shadow, no contact shadow.**
> Background must be **one flat solid pure magenta (#FF00FF)**, uniform edge to
> edge. **No text labels.**

Magenta because nothing in a clay palette is near it, so the key stops being a
judgement call. It also fixes the ground shadow for free: a shadow cast on
magenta is magenta-tinted and keys out with the rest of the background, where
against a warm grey it survives as the grey smear you can see under every
current figure the moment it is composited onto anything but cream.

## Two figures on one prop is one figure too many

`navigator` (lantern **and** pocket compass) keyed cleanly apart from the
compass face, which is a small pale disc that went with the backdrop. It was
dropped anyway: `lamplighter` already carries a lantern and keyed clean, so the
pair was redundant at 40px regardless of the compass.

## No wordmarks in a prop

Two cells put the figure on a plinth reading CLAYRUNE / CLAYDO. At the bench's
40px the text is mush and the figure reads as "blob beside a white box" — it has
no prop of its own left. Brand furniture is not an avatar; both were dropped.

## One crop, not two — the head crop was tried and dropped

WhatsApp's 49px circular avatars settle the size question: that is plenty for a
figure to read, so "too small" was never the problem.

A head-and-shoulders variant was built anyway, on the theory that a full-body
figure wastes a circular slot. Sweeping the crop fraction against the cast
showed otherwise: **78% is barely distinguishable from the full figure**, and
tighter crops start cutting through the mouth — these are blob figures whose
faces sit on the body, lower than a human head would be. The tightest usable
setting bought a slightly larger head in exchange for losing the lute, the
anvil, the spear and the ribbons, which are most of what makes each character
recognisable at a glance.

So: one file per character, shown at whatever size the slot allows. Do not
re-add a portrait crop without re-running that comparison — the finding is that
the props carry more identity than the face does.
