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
The smoke dependency junction points at the main checkout; no npm or
Chromium installation. Its required pre-commit removal was refused by the
unattended tool fence. The tested changes remain uncommitted pending cleanup.

## Delivery

Uncommitted on the assigned branch. Python changes require a server restart after integration; the
dashboard must reload the changed JavaScript. No merge, push or restart is
performed here. Physical phone/PC hardware and authenticated sites are not
verified. No new reload policy decision: reloads in both directions already
existed on the base branch.
