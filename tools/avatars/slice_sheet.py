"""Cut a character sheet into one transparent avatar per cell.

The generator emits a contact sheet on a soft grey backdrop with a baked
ground shadow, in RGB with no alpha. This turns that into square RGBA WebPs
the app can use. Grid is `--grid RxC` (3x3 by default); sheets that print a
caption under each figure need `--caption <px>` so the word is cropped off
BEFORE keying.

WHY FLOOD FILL AND NOT A COLOUR THRESHOLD. The backdrop is a gradient, so a
single "everything near #E8E4DE is background" rule either eats the pale
figures (the sand and grey ones sit inside the same range) or leaves a halo.
Filling inward from the border only removes pixels actually CONNECTED to the
edge, so an enclosed pale region — a face, a scroll — is never touched no
matter how close its colour is to the backdrop.

The ground shadow is deliberately kept where it touches the figure and dropped
where it does not: a hard cut at the feet reads as an amputation, and the
alternative (erasing everything soft) eats the figure's own contact edge.
"""
import argparse
import colorsys
from collections import deque
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'assets' / 'avatars'


def detect_checker(im, probe=6):
    """(period, tone_lo, tone_hi) when the sheet's background is a PAINTED
    transparency checker, else None.

    The generator cannot emit alpha. Asked for a transparent background it
    draws the checkerboard instead, in ordinary opaque pixels — three sheets in
    a row came back that way, every one reporting alpha 255 everywhere. This
    exists to say so out loud, because the failure is otherwise invisible: the
    file opens, the tool runs, and the output is quietly ruined.

    WHY A CHECKER CANNOT BE KEYED, even though it looks like the easy case. It
    spans TWO tones, so whichever you tune for, the other collides with
    something: the dark sheet (74/112 grey) ate ten of twelve figures — not
    through their lit sides but through their own SHADED sides, which are dark
    and neutral and sit exactly on the dark square.

    Keying against the PATTERN instead of a palette was tried and does not
    rescue it. The squares are drawn, not computed: transitions run 19, 19, 20,
    19… so a phase fixed at the sheet's origin has drifted half a cell by the
    bottom row and the model inverts — the light squares clear and the dark
    ones stay behind as a residual grid. Re-detecting the phase per cell
    narrows it and still leaves that grid, and still destroyed one figure.

    The fix is one flat colour the figures do not contain, and it belongs in
    the prompt, not here.
    """
    im = im.convert('RGB')
    w, h = im.size
    px = im.load()

    def runs(vals):
        out, cur, n = [], vals[0], 0
        for v in vals:
            if v == cur:
                n += 1
            else:
                out.append(n)
                cur, n = v, 1
        out.append(n)
        return out[1:-1]        # drop the partial runs at both ends

    tops = [px[x, probe][0] for x in range(w)]
    lo, hi = min(tops), max(tops)
    if hi - lo < 20:
        return None             # one flat tone: not a checker, carry on
    mid = (lo + hi) / 2
    rl = runs([v > mid for v in tops]) + runs([px[probe, y][0] > mid for y in range(h)])
    if len(rl) < 8:
        return None
    per = sorted(rl)[len(rl) // 2]
    if per < 4 or sum(1 for r in rl if abs(r - per) > 1) > len(rl) * 0.15:
        return None             # not a regular grid

    def tone(bright):
        # MEDIAN, not mean: the border strip crosses figures and caption text
        # on some sheets, and an average lands a few levels off the real tone.
        chans = ([], [], [])
        for x in range(0, w, 3):
            for y in (probe, h - 1 - probe):
                c = px[x, y]
                if (c[0] > mid) == bright:
                    for i in range(3):
                        chans[i].append(c[i])
        if not chans[0]:
            return (0, 0, 0)
        return tuple(sorted(ch)[len(ch) // 2] for ch in chans)

    return per, tone(False), tone(True)


def detect_screen(im, probe=4):
    """(hue, sat) of a saturated flat backdrop — a chroma-key screen — or None.

    WHY HUE AND NOT DISTANCE. The palette key measures how far a pixel is from
    the backdrop's colour, which cannot see a SHADOW: a shadow cast on the
    screen keeps the screen's hue exactly and loses only brightness, so it sits
    far outside any sane tolerance and survives. On the old warm-grey sheets
    that residue was a grey smear under every figure, invisible on a cream card
    and obvious the moment an avatar was composited onto anything else — the
    "not cropped properly, it shows at full size" report.

    Against a magenta screen the same residue is unmistakably magenta, and hue
    separates it in one step: no clay figure in this cast is magenta at any
    brightness, so "is this pixel the screen's hue, saturated?" clears the
    screen AND its shadow AND leaves the figures untouched.

    Returns None unless the border is genuinely saturated, so every
    grey-backdrop sheet still goes through the palette key.
    """
    im = im.convert('RGB')
    w, h = im.size
    px = im.load()
    hs, ss = [], []
    for x in range(0, w, 3):
        for y in (probe, h - 1 - probe):
            r, g, b = px[x, y]
            hh, ss_, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            hs.append(hh)
            ss.append(ss_)
    if not hs:
        return None
    sat = sorted(ss)[len(ss) // 2]
    if sat < 0.45:
        return None             # a muted backdrop: not a screen
    hue = sorted(hs)[len(hs) // 2]
    return hue, sat


def key_out(im, tol=10, fringe=2, screen=None, screen_floor=0.85):
    """RGBA copy with the border-connected backdrop made transparent.

    FIXED reference, never a propagating one. Comparing each pixel to its
    NEIGHBOUR makes the tolerance cumulative — neighbours on a smoothly shaded
    figure are always within tolerance of each other, so the fill walks off the
    backdrop, through the body and out the far side. The first version of this
    did exactly that and left about 1% of each figure standing.

    The backdrop is a gradient, so the reference is a small PALETTE sampled
    from the border rather than one corner colour. Connectivity still does the
    real work: an enclosed pale region (a face, a scroll) is never reached, no
    matter how close its colour is to the backdrop.
    """
    im = im.convert('RGBA')
    w, h = im.size
    px = im.load()
    assert px is not None

    # Border palette, thinned to distinct entries so the comparison stays cheap.
    pal = []
    for x in range(0, w, 4):
        for y in (0, h - 1):
            c = px[x, y][:3]
            if all(abs(c[0]-q[0]) + abs(c[1]-q[1]) + abs(c[2]-q[2]) > 12 for q in pal):
                pal.append(c)
    for y in range(0, h, 4):
        for x in (0, w - 1):
            c = px[x, y][:3]
            if all(abs(c[0]-q[0]) + abs(c[1]-q[1]) + abs(c[2]-q[2]) > 12 for q in pal):
                pal.append(c)

    def is_bg_screen(c):
        """The screen's hue, at any brightness. That last part is the point —
        it is what takes the cast shadow with it."""
        hue, sat = screen
        hh, ss_, vv = colorsys.rgb_to_hsv(c[0] / 255, c[1] / 255, c[2] / 255)
        # The floor is RELATIVE to the screen's own saturation, and it is the
        # setting that matters. A screen BOUNCES onto the figures standing on
        # it: a grey shield or a pale scroll picks up real magenta, lands on
        # the screen's hue, and an absolute floor of 0.22 ate them (the warden
        # lost half its body, the astronomer its legs). A cast SHADOW, by
        # contrast, scales value and leaves saturation alone — so a high floor
        # keeps the shadow-clearing that hue keying is here for, and lets the
        # bounce-lit figure through.
        if ss_ < sat * screen_floor or vv < 0.06:
            return False        # a figure lit by the screen, not the screen
        d = abs(hh - hue)
        return min(d, 1.0 - d) <= 0.055

    def is_bg(c):
        """Close in distance AND in warmth.

        Distance alone is not enough. The scholar is a COOL blue-grey figure on
        a WARM grey backdrop at almost the same brightness — sum-of-absolute
        difference is ~30 across that boundary, so the fill walked through its
        lit face and ate the top of its head at every tolerance in the sweep,
        including the lowest.

        R-B separates them cleanly where total distance cannot: the backdrop
        sits around +13 (warm), the figure around -12 (cool). Requiring both
        keeps every cool-toned character intact without loosening the fill
        anywhere else.
        """
        warmth = c[0] - c[2]
        for q in pal:
            if (abs(c[0]-q[0]) + abs(c[1]-q[1]) + abs(c[2]-q[2]) <= tol * 3
                    and abs(warmth - (q[0] - q[2])) <= 10):
                return True
        return False

    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        q.append((x, 0)); q.append((x, h - 1))
    for y in range(h):
        q.append((0, y)); q.append((w - 1, y))

    cleared = 0
    while q:
        x, y = q.popleft()
        i = y * w + x
        if seen[i]:
            continue
        seen[i] = 1
        r, g, b, _ = px[x, y]
        ok = is_bg_screen((r, g, b)) if screen else is_bg((r, g, b))
        if not ok:
            continue
        px[x, y] = (r, g, b, 0)
        cleared += 1
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                q.append((nx, ny))

    # ── fringe ───────────────────────────────────────────────────────────
    # A 1-2px ring survives the hard key: pixels that are a BLEND of backdrop
    # and figure, too far from the backdrop to clear at the knee and too grey
    # to belong to the figure. On a cream card that ring is a dirty halo.
    #
    # Bounded on purpose — only pixels already touching transparency, only
    # against a looser tolerance, only twice. Unbounded is exactly how the
    # first version of this function ate the figures.
    for _ in range(fringe):
        edge = []
        for y in range(h):
            for x in range(w):
                if px[x, y][3] == 0:
                    continue
                if any(0 <= x + dx < w and 0 <= y + dy < h
                       and px[x + dx, y + dy][3] == 0
                       for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                    edge.append((x, y))
        loose = tol * 3 * 2.6
        for x, y in edge:
            r, g, b, _ = px[x, y]
            warmth = r - b
            if screen:
                hh, ss_, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
                d = abs(hh - screen[0])
                hit = ss_ >= screen[1] * screen_floor * 0.5 and min(d, 1.0 - d) <= 0.10
            else:
                hit = any(abs(r-q[0]) + abs(g-q[1]) + abs(b-q[2]) <= loose
                          and abs(warmth - (q[0] - q[2])) <= 14 for q in pal)
            if hit:
                px[x, y] = (r, g, b, 0)
                cleared += 1

    kept = 1.0 - cleared / float(w * h)
    return im, kept


def despill(im, screen, strength=0.9):
    """Take the screen's colour back OUT of the figure.

    A chroma screen does not just sit behind the subject, it bounces onto it:
    against magenta the gardener's trowel came out pink, the prospector's
    scroll came out pink, and the pale figures picked up a lilac cast down one
    side. No keying threshold fixes that — the contamination is in the render,
    and it survives a perfect key.

    The discriminator has to be the SCREEN's shape, not brightness. Magenta is
    high red AND high blue with green sitting below both, so `min(R, B) > G`
    catches a magenta cast and leaves terracotta alone (terracotta is high red
    with LOW blue, which is the whole cast's base colour — a naive
    "is it warm?" despill would drain every figure we have).
    """
    hue = screen[0]
    # Only implemented for the magenta/green screens this pipeline sees; a hue
    # it does not know is left untouched rather than damaged.
    magenta = min(abs(hue - 5 / 6), 1 - abs(hue - 5 / 6)) < 0.12
    green = min(abs(hue - 1 / 3), 1 - abs(hue - 1 / 3)) < 0.12
    if not (magenta or green):
        return im
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            if magenta:
                excess = min(r, b) - g
                if excess > 0:
                    cut = int(excess * strength)
                    px[x, y] = (max(0, r - cut), g, max(0, b - cut), a)
            else:
                excess = g - max(r, b)
                if excess > 0:
                    px[x, y] = (r, max(0, g - int(excess * strength)), b, a)
    return im


def square(im, size, pad=0.06):
    """Trim to the subject, centre it in a square, resize.

    Consistent framing is the thing that makes a ROW of avatars look composed;
    the generator's own framing varies cell to cell.
    """
    bb = im.getbbox()
    if bb:
        im = im.crop(bb)
    w, h = im.size
    side = int(max(w, h) * (1 + pad * 2))
    canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sheet')
    ap.add_argument('--names', required=True,
                    help='comma-separated, row-major, one per cell')
    # The generator does not always emit 3x3. A later sheet came back 4x4 with
    # a caption printed under every figure, and a hardcoded 3x3 quietly sliced
    # nine wrong rectangles out of it rather than failing.
    ap.add_argument('--grid', default='3x3', help='ROWSxCOLS, e.g. 4x4')
    # Baked-in captions are DARK TEXT ON THE BACKDROP, so the key leaves them
    # standing as a floating word under the figure. Crop them off before
    # keying, never after: `square()` trims to the subject's bbox, and the
    # caption is part of that bbox.
    ap.add_argument('--caption', type=int, default=0,
                    help='px of caption band to drop off the bottom of each cell')
    ap.add_argument('--size', type=int, default=256)
    ap.add_argument('--tol', type=int, default=10,
                help='the knee: sweep it against coverage, do not guess')
    ap.add_argument('--fringe', type=int, default=2)
    ap.add_argument('--no-despill', action='store_true',
                    help='keep the screen colour that bounced onto the figures')
    ap.add_argument('--screen-floor', type=float, default=0.85,
                    help='chroma-key saturation floor, as a fraction of the '
                         'screen own saturation: lower eats bounce-lit figures, '
                         'higher leaves the cast shadow behind')
    ap.add_argument('--force', action='store_true',
                    help='slice anyway when the sheet has a painted checker background')
    a = ap.parse_args()

    names = [n.strip() for n in a.names.split(',') if n.strip()]
    rows, _, cols = a.grid.lower().partition('x')
    rows, cols = int(rows), int(cols)
    cells = rows * cols
    if len(names) != cells:
        ap.error(f'--grid {a.grid} is {cells} cells but --names has {len(names)}')
    sheet = Image.open(a.sheet)
    screen = detect_screen(sheet)
    if screen:
        print(f'  (chroma screen detected at hue {screen[0]*360:.0f}°, '
              f'sat {screen[1]:.2f} — keying on hue, which takes the cast '
              f'shadow with it)')
    ck = None if screen else detect_checker(sheet)
    if ck and not a.force:
        ap.error(
            f'this sheet has a PAINTED transparency checker ({ck[0]}px squares, '
            f'{ck[1]} / {ck[2]}) — the alpha channel is opaque everywhere, so the '
            'checkerboard is just pixels. It cannot be keyed: it spans two tones, '
            'and whichever you tune for, the other collides with the shading inside '
            'the figures. Re-generate on ONE flat saturated colour the clay palette '
            'does not contain (pure magenta), and do not use the word "transparent" '
            'in the prompt — that is what makes it draw the checker. --force to '
            'slice anyway.')
    sheet = sheet.convert('RGB')
    W, H = sheet.size
    cw, ch = W // cols, H // rows
    OUT.mkdir(parents=True, exist_ok=True)

    for i, name in enumerate(names):
        col, row = i % cols, i // cols
        cell = sheet.crop((col * cw, row * ch,
                           (col + 1) * cw, (row + 1) * ch - a.caption))
        keyed, kept = key_out(cell, a.tol, a.fringe, screen=screen,
                              screen_floor=a.screen_floor)
        # Eyeballing is what let the propagating-reference bug ship. Measured
        # across all nine at the tolerance knee, a figure holds 33-40% of its
        # cell; well outside that band is a keying failure, not a slim
        # character. Low = the fill ate the body, high = it never started.
        flag = '' if 0.20 <= kept <= 0.55 else '   <-- CHECK: keyed badly'
        if screen and not a.no_despill:
            keyed = despill(keyed, screen)
        out = square(keyed, a.size)
        p = OUT / f'{name}.webp'
        out.save(p, 'WEBP', quality=88, method=6)
        print(f'  {name:14} {p.stat().st_size // 1024:>3} KB   kept {kept*100:4.1f}%{flag}')
    print(f'\n{len(names)} avatars -> {OUT}')


if __name__ == '__main__':
    main()
