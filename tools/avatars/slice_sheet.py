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
from collections import deque
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'assets' / 'avatars'


def key_out(im, tol=10, fringe=2):
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
        if not is_bg((r, g, b)):
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
            if any(abs(r-q[0]) + abs(g-q[1]) + abs(b-q[2]) <= loose
                   and abs(warmth - (q[0] - q[2])) <= 14 for q in pal):
                px[x, y] = (r, g, b, 0)
                cleared += 1

    kept = 1.0 - cleared / float(w * h)
    return im, kept


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
    a = ap.parse_args()

    names = [n.strip() for n in a.names.split(',') if n.strip()]
    rows, _, cols = a.grid.lower().partition('x')
    rows, cols = int(rows), int(cols)
    cells = rows * cols
    if len(names) != cells:
        ap.error(f'--grid {a.grid} is {cells} cells but --names has {len(names)}')
    sheet = Image.open(a.sheet).convert('RGB')
    W, H = sheet.size
    cw, ch = W // cols, H // rows
    OUT.mkdir(parents=True, exist_ok=True)

    for i, name in enumerate(names):
        col, row = i % cols, i // cols
        cell = sheet.crop((col * cw, row * ch,
                           (col + 1) * cw, (row + 1) * ch - a.caption))
        keyed, kept = key_out(cell, a.tol, a.fringe)
        # Eyeballing is what let the propagating-reference bug ship. Measured
        # across all nine at the tolerance knee, a figure holds 33-40% of its
        # cell; well outside that band is a keying failure, not a slim
        # character. Low = the fill ate the body, high = it never started.
        flag = '' if 0.20 <= kept <= 0.55 else '   <-- CHECK: keyed badly'
        out = square(keyed, a.size)
        p = OUT / f'{name}.webp'
        out.save(p, 'WEBP', quality=88, method=6)
        print(f'  {name:14} {p.stat().st_size // 1024:>3} KB   kept {kept*100:4.1f}%{flag}')
    print(f'\n{len(names)} avatars -> {OUT}')


if __name__ == '__main__':
    main()
