# Zoomed-out browser pane tap verification

## Finding

The failure on base `b83dd905` was premature test input, not an incorrect
picture-to-page transform. The smoke accepted any decoded JPEG and then used
the backend's newer `pageScaleFactor` to calculate a tap. The decoded viewer
frame at that tap was still blank: the expected field was not yet visible.

The original request coordinates were approximately `(273.265, 147.143)` in
a `412x809` frame at scale `0.420408`. The backend correctly translated them
to layout coordinates `(650, 350)`, inside the textarea. On the failing pipe
run, these commands preceded the initial navigation response and produced no
DOM mouse event. Once the field was visible, the same commands focused it and
typing `hello` arrived. A port-transport comparison also passed without changing
the mapping. Attaching a diagnostic CDP connection before the tap masked the
race, so it is not used as the new readiness condition.

## Change

`tools/smoke/browser-pane-mobile-typing.mjs` waits for the fixture's white
textarea at its expected position in the actual decoded JPEG. It samples
image pixels through a local canvas, accounting for the JPEG's dimensions
relative to the server frame. It then runs the existing tap and soft-keyboard
typing checks. There is no fixed startup delay and no extra connection to the
remote page.

Production JavaScript and Python are unchanged. The existing inverse-scale
mapping, visual viewport handling, ordered input queue, EEPR and timing
collector are unchanged. No new production module or feature was added.

## Validation

| Command | Return code |
| --- | --- |
| `node tools/smoke/browser-pane-mobile-typing.mjs` | 0; zero failures, including EEPR and real-backend typing |
| `node tools/smoke/browser-pane-coords.mjs` | 0 |
| `node tools/smoke/browser-pane-mobile.mjs` | 0 |
| `node tools/smoke/browser-pane-ime-shortcuts.mjs` | 0 |
| `node --test tools/smoke/browser-input-timing.test.mjs` | 0; 4 passed |
| `python -m pytest tests/test_browser_routes.py -q` | 0; 188 passed |

The main checkout's existing smoke dependencies were linked with a junction;
no install was run. Managed test processes were registered. The older coords
and IME smokes used a scratch Playwright preload solely to register their
isolated Chromium PIDs; their test bodies and assertions were unchanged.

Controls:

- Original smoke and production code: real-backend check returned 1.
- Corrected readiness and unchanged production code: isolated and full typing
  smoke returned 0.
- Corrected readiness with inverse-scale mapping temporarily removed:
  real-backend check returned 1 after confirming the field was decoded.
  Production code was restored before the six final checks.

## Limits and handoff

This is a local branch test repair. It establishes that the existing mapping
works on the local real Chromium fixture; it does not diagnose Ron's actual
phone or any authenticated site. No live profile, sign-in, merge, push or
server restart was performed. It clears the false red typing gate recorded
in `docs/BROWSER_INPUT_TIMING.md`; live phone timing measurement remains open.

Rollback: revert the smoke readiness change; the original premature-tap
failure returns, with production behavior unchanged.
