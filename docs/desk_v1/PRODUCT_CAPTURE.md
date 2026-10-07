# Studio product capture

Ticket 3ad3c1fb. The live **Capture from the product** body takes a real
screenshot, rather than attaching a fixture. Demo smokes keep their explicitly
seeded screens; Record keeps its previous behavior. No video recorder was added.

## Contract

- `GET /api/desk/capture/projects/<project_id>` returns project name,
  `app_address`, `clayrune` and `pages`. Missing projects are refused.
- `PUT` on that address saves `{app_address}` per product under
  `capture_apps` in the existing Desk store. No sidecar enters `data/projects`.
  A corrupt store is refused before writing. This setting is edited in the
  capture form; existing project `app_address`/`app_url` fields are read if no
  capture setting exists, but do not grant private network access.
- `POST /api/desk/capture {project_id, page}` returns `{item}` with the
  ordinary library image shape: `{id, kind, title, path, src}`. The PNG lands
  under `data/uploads/desk/library/image/Studio/`, appears in materials/Recent,
  and is attached by its saved path. The bridge creates a valid asset id rather
  than sending the library path as a campaign asset id. Studio does not reupload
  an already saved capture.
- Clayrune pages are a fixed server-owned allowlist: Projects, Floor, Desk,
  Studio and Connections. The server address derives from its bound port,
  never from a caller's Host header. Navigation waits for dashboard config and
  the live Desk store, refusing unavailable pages instead of capturing legacy
  or loading views. Other projects accept relative paths beginning with `/`;
  scheme/origin changes, backslashes and control characters are refused.

## Browser and network confinement

`mc/desk_capture.py` reuses the browser binary locator and anonymous CDP pipe.
Each capture has its own temporary profile, two-capture concurrency limit and
35-second command budget. Chromium is registered immediately after spawn,
closed gracefully, and its exact Popen PID is killed only if its own close
times out. No debugging port or named profile is shared with the user. Missing
Chromium or process tracking is an explicit refusal. No dependency was added.

`mc/desk_capture_network.py` extends the existing guarded proxy's socket pump.
HTTP page reads and HTTPS tunnels resolve once per connection; all returned
addresses must be public unless the exact hostname and port were entered by
the user. The proxy connects to the vetted IP, preventing a second DNS lookup
from changing the destination. The saved exception comes from server-side
caller attribution plus the dashboard's browser Origin (including HTTPS
tunnel termination and the native mobile shell), not a body `user_entered` flag;
known agent callers and unavailable attribution cannot grant private access.
An explicitly entered private origin covers its own pages and assets, not a
subnet or another port. The local server's 127.0.0.1/localhost port is allowed.

Chromium proxy flags remove loopback bypass, disable QUIC, prevent direct DNS
fallback and confine WebRTC UDP. Downloads are denied. Redirects, frames,
subresources and script fetches are checked at the same socket boundary. HTTP
reads use GET/HEAD and close each upstream connection; arbitrary page scripts
are never clicked or used as navigation commands. The existing attribution
limitation for deliberately detached callers still applies; Origin alone is
not proof of a user. Credentials and saved browser profiles are not used.

Capture is a viewport image, not an interactive recording. External pages
requiring sign-in may show the actual sign-in page. Assets hosted on a second
private origin are blocked until a future explicitly scoped product feature
supports them; no exception is inferred from links or page text.

## Validation

- `tests/test_desk_capture.py`: address/path/provenance refusals, corrupt-store
  preservation, library/Recent save, DNS mixtures, pinned connections, and real
  Chromium capture with pixel/dimension checks and cleanup. The real-browser
  case blocks redirects, images and script fetches to an unselected private port.
- `tools/smoke/desk-v1-studio-capture.mjs`: full live SPA with a hermetic API,
  1440/390 layouts, one-time address, page choice, failed capture/retry, saved
  path, Recent, campaign asset-id bridge and no duplicate image upload.
- Actual server page captures are verified through the new Flask route with
  an isolated temporary Desk store/library, using the running dashboard as
  the real product. Operator-specific screenshots/logs stay in `_scratch`.

Revert the capture modules and their three registration/integration sites to
roll back the feature; ordinary Studio image files remain usable library files.
