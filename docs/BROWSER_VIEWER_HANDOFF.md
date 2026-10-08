# Browser viewer handoff

## Finding

On base `4b5aba42`, a throwaway pane launched in phone mode and attached
from a 1440×1000 desktop dashboard reported a desired 1120×685 viewport,
but its actual page was 1828×561 with `navigator.maxTouchPoints === 5`.
The page had already reloaded as `DESKTOP-SITE`, and UA client hints said
desktop. The hypothesis that the stored base UA itself remained mobile is
therefore rejected for this reproduction: the handoff failed in metrics and
touch, while the existing desktop UA reversal and reload worked.

`_apply_view` previously called `_fit_and_rearm` before clearing mobile
metrics. Its separate CDP connection measured the still-overridden phone
page and calculated incorrect window dimensions. Merely moving a queued
clear ahead of fitting would still race the reader; the fitting connection
must acknowledge the clear before its first measurement. The touch-off
payload contained `maxTouchPoints: 0`, which Chromium rejects even with
`enabled: false`. The old unit test blessed this invalid payload.

The dock restore path also omitted `mobile`. A viewer restoring a pane at
its previous size could leave the other viewer's mode in place, because
the ResizeObserver report was deduplicated on size alone.

## Change

- `mc/browser_viewer_mode.py` owns the extracted viewport/mode transition,
  serialises complete transitions, keeps launch state aligned with current
  mode, and retains existing reload-on-mode-switch semantics.
- `browser_routes._fit_windows(..., reset_device=True)` clears device metrics
  and touch on its own acknowledged connection before measuring desktop
  windows. Per-target resets run even when tabs share a window; physical
  fitting runs once per window.
- Touch off sends only `enabled: false`. Desktop sessions that never entered
  phone mode still receive no metrics override. The deliberate
  `NO Emulation.setDeviceMetricsOverride` comments remain intact.
- `static/js/browser-pane.js` includes mode in viewport deduplication and
  explicitly reports mode on dock restore.
- No edits to `mc/browser_agent_tabs.py`: its inherited mode reads the
  current `device_mode`, and adoption grants no new authority. Agent-only
  background tabs are not viewer takeovers.

No saved profile was opened or used, and no sign-in occurred. The regression
uses the real blueprint, two Playwright dashboard viewers, temporary
Chromium storage and a local fixture serving different HTML by UA. It checks
actual inner dimensions, touch, client hints and served HTML in both
directions, including same-size dock restoration. One shared pane follows
the latest attach/restore; independent simultaneous device renders are not
implemented by this shared-session design.

## Verification

Before edits:

```text
python -m pytest tests/test_browser_routes.py -q
node tools/smoke/browser-pane-mobile.mjs
node tools/smoke/browser-pane-coords.mjs
node tools/smoke/browser-pane-mobile-typing.mjs
```

All rc=0. New device handoff regression on the old production paths: rc=1,
with the measured 1828×561 / touch=5 defect above.

After edits:

```text
python -m pytest tests/test_browser_routes.py tests/test_browser_viewer_mode.py tests/test_browser_agent_tabs.py tests/test_browser_auth_diagnostics.py tests/test_browser_stream.py tests/test_browser_cdp_pipe.py -o addopts= -q
pyright mc/browser_viewer_mode.py
node tools/smoke/browser-pane-device-handoff.mjs
node tools/smoke/browser-pane-mobile.mjs
node tools/smoke/browser-pane-coords.mjs
node tools/smoke/browser-pane-mobile-typing.mjs
```

283 Python tests passed (rc=0); pyright reported 0 errors (rc=0); all four
smokes returned rc=0. The handoff smoke passed phone → desktop → phone →
desktop, verifying each viewer's inner dimensions, touch, client hints and
UA-served HTML. It does not treat a reported session mode alone as success.

Process registration is enabled with
`MC_TEST_PROCESS_REGISTER_URL=http://localhost:5199/api/processes/register`.
The original smoke dependency junction pointed at the main checkout; no npm or
Chromium installation. Its removal was initially refused by the unattended
tool fence. The original fix was subsequently committed as `939a4aa4` and
integrated by Dave in `c710ffe9` before this follow-up began.

## Delivery

The follow-up below is a local branch change based on `c710ffe9`. The dashboard
must reload the changed JavaScript after integration. No merge, push or restart
is performed in this follow-up. Physical phone/PC hardware and authenticated
sites are not verified. No new reload policy decision: reloads in both
directions already existed on the base branch.

## 2026-10-08 follow-up: visibility/focus return and visible mobile close

The original smoke covered dock restore, which explicitly sends a viewport.
It did not cover a phone whose dashboard document returned to visibility while
its pane remained at exactly the same size. `sendView` had no visibility/focus
caller and retained its local size+mode dedupe. The extended regression failed
on `c710ffe9` (rc=1): phone return left desktop HTML, touch=0 and 1120x685
desktop metrics in place.

`browser-pane.js` now reuses `sendView` on `visibilitychange` to visible and
window focus, clearing its local dedupe. Hidden, minimized and detached panes
are guarded; close, detach and stop remove the listeners and pending resize
timer. No pointerdown/keydown takeover was added. The backend's existing
device-mode transition guard makes repeated same-mode reports non-reloading;
the smoke counts actual UA fixture loads to verify zero extra reloads on both
phone and desktop. The change stays with the existing viewport lifecycle in
`browser-pane.js`; it adds no independent concern or new teardown module.

The mobile top bar now has a visible X after the spinner, with a 40x40px tap
target and `aria-label="Close browser"`. Both X and the retained menu item are
wired to `closeBrowserPane`, which uses the existing `/api/browser/stop`
graceful close path. No profile handling changed; saved-profile/sign-in
persistence was not exercised. The typing smoke retains its Back to chat and
menu-close checks; the mobile smoke taps the top X and verifies pane removal
and exactly one stop request against an isolated throwaway session.

### Bottom toolbar measurement

At 390x844, the real mobile pane's toolbar is y=792..844 (52px), fully on-screen.
With the typing shadow focused, reducing the viewport to 390x500 models the
visible area above a soft keyboard. The pane intentionally freezes at its
original height to prevent the frame moving under the finger; the toolbar
stays y=792..844, below the visible area. The top X remains visible and tappable.
No keyboard-layout change was made: Ron asked for measurement and disclosure.

Screenshots generated and visually inspected (gitignored):

- `_scratch/browser-return-viewer/mobile-390x844.png`
- `_scratch/browser-return-viewer/mobile-keyboard-visible-area.png`

This is headless geometry with simulated keyboard occlusion, not a native
Android/iOS keyboard test. The screenshot shows the reduced visible area,
not a fabricated keyboard. Neither physical phone behavior nor sign-in is
claimed as verified.

### Follow-up verification

All commands below returned rc=0 on this branch. Python: 192 passed. The handoff
checks actual inner dimensions, touch, UA client hints and UA-served HTML for
phone -> PC -> phone visible and PC -> phone -> PC focus, plus same-mode no
reload and existing dock restore. Mobile checks labelled X/tap and toolbar
geometry. Coordinates and the complete typing/IME/paste/ordering smoke pass.

```text
python -m pytest tests/test_browser_routes.py tests/test_browser_viewer_mode.py -o addopts= -q
node tools/smoke/browser-pane-device-handoff.mjs
node tools/smoke/browser-pane-mobile.mjs
node tools/smoke/browser-pane-coords.mjs
node tools/smoke/browser-pane-mobile-typing.mjs
```

Smoke environment: `MC_TEST_PROCESS_REGISTER_URL=http://localhost:5199/api/processes/register`.
Each smoke registers its owned viewer/harness/browser PIDs. All smokes use the
main checkout's `tools/smoke/node_modules` through a temporary junction. No
installation, named profiles, live panes, unrelated process termination or
production server restart.

### Follow-up delivery blocker

Changes are tested but not committed. The unattended tool fence rejected
removal of `tools/smoke/node_modules` as destructive deletion outside scratch.
The inspected path is a junction
created by this session, targeting the main checkout's dependencies. No retry
or alternate deletion was attempted. Project instructions require its removal
before committing. The integration owner must arrange cleanup; after the junction is
removed, commit only the seven changed source/test/documentation paths.
