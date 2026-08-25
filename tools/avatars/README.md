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
