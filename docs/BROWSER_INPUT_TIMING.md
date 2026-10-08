# Browser input timing from a phone

This branch adds an opt-in observer for the **phone's dashboard browser**, so
the measurements include its real connection through the Cloudflare tunnel.
It is off by default. It does not change input ordering, edit merging, the
five-second input timeout, or the CDP receive poll. It has not yet measured
Ron's phone. A simulated delay is an instrumentation test, not a tunnel result.

## Enable on Ron's phone

After this branch is integrated and the Clayrune server is restarted:

1. In the phone browser's address bar, add `?bp_input_timing=1` to the current
   Clayrune dashboard address. If it already contains `?`, append
   `&bp_input_timing=1` instead. Put it before any `#fragment`. For example:
   `https://YOUR-CURRENT-TUNNEL-HOST/?bp_input_timing=1`.
2. Reload the dashboard fully to fetch the new JavaScript modules. If the
   phone still has the old page cached, close that dashboard tab and open the
   flagged address in a new tab. No change to the remote site's address is needed.
3. Open/reopen the browser pane normally and type a short test phrase into an
   ordinary page field. Type several characters quickly, then pause. Leave the
   pane visible for **at least 12 seconds** so missing frames are also recorded.
4. Read the results below. Each input event has its own `sample_id`; events
   merged into one POST share a `request_id` and sent/ack/frame times.
5. Turn it off by changing the flag to `bp_input_timing=0` and reloading the
   dashboard. Removing the flag also turns it off unless storage enabled it.

Optional persistent opt-in, from that device's dashboard developer console:

```javascript
localStorage.setItem('mc_bp_input_timing', '1'); location.reload();
// Disable persistent collection:
localStorage.removeItem('mc_bp_input_timing'); location.reload();
```

The query flag `0` overrides persistent opt-in. These flags apply only to this
dashboard document/device. No profile, account, passcode or permission setting
is changed, and no named profile or live pane is opened by the diagnostics API.

## Read the samples

On the Clayrune host (PowerShell):

```powershell
curl.exe -s "http://localhost:5199/api/browser/input-timing"
curl.exe -s "http://localhost:5199/api/browser/input-timing?after=100"
```

`after` is the previous result's last `index`, not a device timestamp.
The response contains `samples`, `capacity`, `retained`, `total_received`,
and `evicted`. Only the newest **1,000 samples globally** remain; an increasing
`evicted` value means older samples are gone. Restarting the server clears all
results and resets indexes. GET is `Cache-Control: no-store`.

## Interpret the stages

All `t_*` values are milliseconds on the **phone document's monotonic
`performance.now()` clock**. They are not server wall-clock timestamps and
must not be subtracted across different `client_id` values/documents.

| Field / difference | Meaning |
| --- | --- |
| `t_key` | Capture-listener time of the originating keyboard/input/composition/paste event; enqueue time for ordered actions without a synchronous event |
| `t_sent` | Immediately before the ordered input `fetch` starts |
| `t_ack` | Input fetch resolves with response headers; null for a rejected/aborted fetch without a response |
| `t_frame` | Animation-frame callback after the first newly decoded visible image whose load event occurred **after** `t_ack` |
| `t_sent - t_key` | Waiting behind earlier ordered input, including client merging |
| `t_ack - t_sent` | Phone-to-server request and response plus server input acceptance/enqueue work |
| `t_frame - t_ack` | Waiting for a newly decoded displayed frame after acknowledgement |
| `t_frame - t_key` | Full observed event-to-frame time for that sample |

The input response acknowledges **queueing**, not CDP dispatch or page rendering.
No server dispatch timestamp is added in this ticket. Actual dispatch/frame
sub-stages cannot be isolated from these four timestamps alone.

An image load plus the next animation-frame callback approximates display
readiness; it cannot measure physical screen scanout. The frame may be an
animation or other unrelated update. It does not prove the character appeared.
A frame can arrive before the acknowledgement; it is deliberately excluded
from `t_frame`. If the page then stops updating, the sample reports no frame.
Hidden, minimized, detached or closed images are not counted as displayed.
Frames from a reopened pane cannot complete samples belonging to the old pane.

| Numeric code | Meaning |
| --- | --- |
| `outcome=1` | Newly decoded frame observed after ack |
| `outcome=0` | No qualifying frame within ten seconds after ack; `t_frame=null` |
| `outcome=2` | Input HTTP failure or fetch rejection/timeout; `t_frame=null` |
| `outcome=3` | Pane observer closed before a qualifying frame; `t_frame=null` |
| `transport=1 / 2 / 0` | Binary / SSE / unknown transport at send time |

`http_status=0` means no HTTP response. `queue_depth` is the number of queued
ordered requests at enqueue, capped at 500; the active request is separate.
Random numeric `client_id` distinguishes dashboard documents; it is not a
durable identity. `request_id` is local to that document and also includes
ordered mouse, IME, zoom and site actions. No input kind or key value is saved.

## Storage and failure behavior

- New blueprint: `mc/browser_input_timing.py`, registered in `server.py`.
  New collector: `static/js/browser-input-timing.js`, called through small hooks
  in the existing pane queue and renderer. Metadata uses WeakMaps and never
  joins the `/api/browser/input` payload.
- Only numeric fields (nullable ack/frame) and the user-agent string are
  accepted. Unknown fields, typed text, key values, URLs, session/profile names,
  request bodies and nonfinite numbers are rejected without echoing them.
  UA is at most 512 printable characters; samples/batch at most 50; body at
  most 64 KiB. Entire batches validate before any insertion.
- Client batches once per second with one upload in flight and a five-second
  upload timeout. It keeps at most 500 unfinished and 500 completed samples;
  extra events are not traced while full. Failed uploads remain bounded for
  retry and emit a generic console warning. Typing continues independently.
  Tracing adds small diagnostic POSTs, which can itself perturb a busy link.
- The ring is process-local RAM only. Nothing is written to DATA_DIR, a log,
  profile or disk. Device timings are self-reported diagnostic data, not trusted
  telemetry or proof of page state. Existing dashboard authentication still
  applies; the route grants no browser operations.

## Verification and integration handoff

Run in the branch checkout, using the main checkout's existing smoke dependencies
via a temporary junction (do not install another Playwright/Chromium):

```powershell
python -m pytest tests/test_browser_input_timing.py
$browserTimingTests = @(Get-ChildItem -LiteralPath tests -Filter 'test_browser*.py' | ForEach-Object { $_.FullName })
python -m pytest @browserTimingTests
pyright mc/browser_input_timing.py
node --test tools/smoke/browser-input-timing.test.mjs
$env:MC_TEST_PROCESS_REGISTER_URL = 'http://localhost:5199/api/processes/register'
node tools/smoke/browser-pane-mobile.mjs
node tools/smoke/browser-pane-mobile-typing.mjs
```

The typing smoke uses real phone-viewport events and the real collector, checks
default-off, merged events, delayed queue/request timing, visible decoded frames,
numeric privacy, unchanged input payload and upload failure. Its real-backend
portion and the mobile smoke use throwaway temporary profiles only. Their owned
harness/Chromium processes are registered with Process Manager.
Set `MC_TEST_PROCESS_REGISTER_URL` as above for managed runs; without that
variable the test harness remains independent of a running Clayrune server.

Branch verification: the backend contract has **44 passing tests**, the browser
pytest subset has **503 passing tests**, and new-module pyright has **0 errors**.
The mobile layout smoke passed. The collector lifecycle tests passed (4 checks).
The full mobile typing smoke's new trace checks passed, but its existing
real-browser zoomed-out tap check returned **rc=1**: the remote page did not
receive `hello`. The same check returned rc=1 with the original HEAD pane
JavaScript served through the same harness; outgoing picture coordinates were
`273.265,147.143` with a `412x809` server frame and page scale about `0.420`.
No authenticated root cause is claimed. A server frame readiness check plus a
decoded-viewer-frame check did not resolve it. The suite is **not all green**.

To repeat the isolated failing check (diagnosis only; not a replacement for the
full required smoke):

```powershell
node tools/smoke/browser-pane-mobile-typing.mjs --real-only
# For comparison only, SMOKE_PANE_JS can name an original-source copy.
```

Open handoff decision for Dave: **a separate input-path repair (recommended)**
keeps this observer change isolated but delays the all-green gate; expanding this
ticket to repair the pre-existing input path couples a behavior fix to the trace
and requires overriding this ticket's explicit no-input-semantics-change limit.

| Check command | Result |
| --- | --- |
| `python -m pytest tests/test_browser_input_timing.py` | rc=0; 44 passed |
| `python -m pytest @browserTimingTests` (PowerShell array above) | rc=0; 503 passed |
| `pyright mc/browser_input_timing.py` | rc=0; 0 errors |
| `node --test tools/smoke/browser-input-timing.test.mjs` | rc=0; 4 passed |
| `node tools/smoke/browser-pane-mobile.mjs` | rc=0; mobile and desktop checks passed |
| `node tools/smoke/browser-pane-mobile-typing.mjs` | rc=1; trace checks passed, original real-browser tap failed |
| `node tools/smoke/browser-pane-ime-shortcuts.mjs` | rc=0; shortcuts and IME passed |
| `node tools/smoke/boot-smoke.mjs` | rc=0; all boot scenarios and guards passed |

**Integration needs a server restart** for the blueprint and a **full dashboard
reload on the phone** for the collector/import. A reload alone cannot load the
new backend endpoint into an already-running server. No restart, merge, push,
live session access or authenticated retry was performed by this build.

Rollback: revert the branch commit, then restart the server and fully reload
the dashboard. For collection only, `bp_input_timing=0` plus reload suffices.
