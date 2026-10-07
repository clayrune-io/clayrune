# Agent-owned browser tabs (MC-1079)

Branch build, not deployed. New ownership logic lives in `mc/browser_agent_tabs.py`; Desk profile selection lives in `mc/desk_browser_profiles.py`. The browser blueprint has thin launch/input/read/stop/teardown wiring; `_run_cdp` is unchanged so MC-1078 can add its observer independently.

## API contract

An HTTP launch without a browser Origin uses the existing agent routing signal. When its resolved named profile is already running, `POST /api/browser/launch` creates a `Target.createTarget` with `background:true` in that Chromium. It returns HTTP 200 with:

```json
{"session_id":"agent-tab-...","browser_session_id":"existing-pane-id","target_id":"CDP-target-id","agent_tab":true,"reused":true,"url":"https://example.com/","profile":"main"}
```

Use the returned `session_id` for `/api/browser/input`, `/api/browser/read`, and `/api/browser/stop`. The handle pins to a target, never the pane's current tab or a URL-matched tab. Read uses the existing hidden-content filter, HTML refusal and untrusted-content envelope; it captures the final URL in the same target evaluation and refuses Clayrune's own origin. A lost target fails closed. Input supports the existing page navigation, mouse, wheel, typing, key, history and reload operations; pane-wide viewport/site/screencast commands are not accepted on a tab handle.

Stop closes only the owned target and its CDP connections. A failed close retains the handle for retry; it never falls back to closing the browser. Closing the pane drops its handle connections; named browser teardown continues to close Chromium gracefully to flush cookies. Handles are runtime-only and disappear on server restart.

Targets use the proven hidden-read marker and disposition, registered before creation; they do not enter the pane's tab strip or steal focus. Popups from those hidden tabs follow the existing hidden-tab popup refusal. Dashboard Origin launches keep today's existing-session navigation behavior. This is routing isolation, not a permission system: no fence, policy, passcode or human-only sign-in gate is expanded.

Launch reuse metadata is returned per call rather than consumed from the shared session record. Concurrent HTTP callers therefore both receive owned handles, even when they adopt the same profile at the same time.

## Desk profiles

New accounts default to configured `browser_default_profile`, or `main`. The first account on a different site shares it. A second same-site account gets a distinct default (X OAuth uses its stable account-id suffix so a held sign-in's profile stays the same across Save). Explicit LinkedIn sharing remains allowed for personal profiles, Pages and unknown destinations.

Saved browser setup/read references remain authoritative. Legacy store binding keeps its old per-account profile names. New OAuth accounts record an internal creation-time `oauth_profile_binding`, independent of the credential reference, so attachment still refuses a corrupted or foreign reference and changing the configured default cannot redirect an existing OAuth binding. Legacy OAuth accounts retain their deterministic profile names. Profile directories are never moved or deleted by this change. OAuth bindings take precedence over separate browser-read/sign-in bindings. Per-account vault naming remains unchanged. Shared default profiles are never a cancelled flow's disposable profile; the Higgsfield singleton engine keeps its existing lifecycle.

The sign-in options metadata includes `default_profile`, selected against workspace accounts. The login screen uses it while retaining explicit choices and saved-account defaults. Connect profile validation accepts the native browser profile contract (up to 64 lowercase letters/digits/dots/hyphens/underscores, no `..`), so configured defaults such as `shared.main` work. Desk's X pane reader selects the established `HiddenTabReader` when the profile is running, rather than treating that as fatal. Actual failure to create a tab can still return `profile_in_use`.

## Verification

New backend tests use temporary profile roots and local HTML only; no account sign-in or operator profile contents are used. Existing pane smokes include public LinkedIn/Google navigation in temporary profiles.

- `pyright mc/browser_agent_tabs.py mc/desk_browser_profiles.py`: rc=0, 0 errors/warnings.
- `node --check static/js/desk-v1-connect-login-step.js`: rc=0.
- `git diff --check`: rc=0.
- Broad post-rebase run: `python _scratch/mc1079_checks.py pytest`: **3060 passed, 2 failed**, 586.23s, rc=1. One assertion enumerating stored fields needed the new independent OAuth binding field (corrected); the other was a transient `non_html_content` result in the real-Chromium WebRTC negative-control probe. No test was skipped or weakened.
- Both failed checks are rerun together with the exact command below; the browser-setup module independently passes 107 checks, and the WebRTC control independently passes its isolated rerun. Latest browser-route/adoption/auth coverage: **245 passed**, rc=0, including the concurrency fix made during the broad run. This is full coverage plus targeted correction, not a claim that the broad run itself returned rc=0.
- Pane smokes: 17 of 18 distinct files pass. `browser-pane-mobile-typing.mjs` returns rc=1 on the real-backend, no-viewport-meta field tap (typing never reaches the field); the unchanged main checkout reproduces the same coordinates and failure. No MC-1079 adoption occurs on that fresh throwaway launch. It remains a separate existing defect. The initial public LinkedIn navigation timeout passed on the branch rerun; baseline also showed this public-site timing failure.
- No operator sign-in, live named-profile modification, server restart, merge, push or deployment was performed.

Pane matrix after rebasing onto master (each command is `node <file>.mjs` from `tools/smoke`; the registered runner passes the equivalent absolute path to Node):

| File | rc |
|---|---:|
| browser-pane-batch-b | 0 |
| browser-pane-binary-stream | 0 |
| browser-pane-coords | 0 |
| browser-pane-fit | 0 |
| browser-pane-ime-shortcuts | 0 |
| browser-pane-maximize | 0 |
| browser-pane-minimize-downloads | 0 |
| browser-pane-mobile | 0 |
| browser-pane-mobile-navigate | 0 |
| browser-pane-mobile-render | 0 |
| browser-pane-mobile-site | 0 |
| browser-pane-mobile-typing | 1 (baseline defect above) |
| browser-pane-real-frames | 0 |
| browser-pane-tabs-dialog | 0 |
| browser-paste | 0 |
| browser-pick | 0 |
| desk-v1-connect-login-step | 0 |
| desk-v1-connect-browser-permission | 0 |

The main checkout's smoke dependency directory was linked with a junction, without installing packages. The junction is removed before the final commit. Dashboard boot smoke is not required here: `static/index.html` is unchanged; the two Connect smokes exercise the edited login UI. Auth-observer integration is also checked with `tests/test_browser_auth_diagnostics.py`.

Exact foreground commands (the scratch runners register their PID with Process Manager before invoking pytest/Node):

```text
python _scratch/mc1079_checks.py pytest
  rc=1; 3060 passed, 2 failed (reasons above)
python _scratch/mc1079_focused.py tests/test_browser_agent_tabs.py tests/test_browser_routes.py tests/test_browser_auth_diagnostics.py -o addopts=-ra -q
  rc=0; 245 passed
python _scratch/mc1079_focused.py tests/test_desk_connect_browser_setup.py -o addopts=-ra -q
  rc=0; 107 passed
python _scratch/mc1079_focused.py tests/test_desk_connect_browser_setup.py::test_a_setup_grants_nothing_and_leaves_the_read_route_alone tests/test_desk_connect_real_chromium.py::test_negative_control_the_old_flag_name_leaks -o addopts=-ra -q
  rc=0; 2 passed in 11.37s
python _scratch/mc1079_checks.py smokes
  rc=1; 17/18 pass, mobile-typing baseline defect
pyright mc/browser_agent_tabs.py mc/desk_browser_profiles.py
  rc=0; 0 errors/warnings
node --check static/js/desk-v1-connect-login-step.js
  rc=0
git diff --check
  rc=0
```

The broad runner's pytest argument list is reproduced here without its local process-registration wrapper, for use from any checkout:

```python
from pathlib import Path
import pytest
raise SystemExit(pytest.main([
    'tests/test_browser_agent_tabs.py', 'tests/test_browser_routes.py',
    'tests/test_browser_agent_read_hidden.py', 'tests/test_desk_browser_profiles.py',
    *[str(p) for p in sorted(Path('tests').glob('test_desk*.py'))],
    '-o', 'addopts=-ra', '-q',
]))
```

## Ron's five-line UI test (after Dave integrates and restarts)

1. Keep the `main` pane on a page and note its address; run an authorized agent launch using `profile:main` to another page.
2. Confirm the pane stays on your page; the agent receives a different `agent-tab-...` handle.
3. Have the agent navigate/read with that handle, then Stop it; your page and browser remain open.
4. Add the first account on a new site: the suggested profile is `main` (or your configured default); add a second on that site: it suggests a separate profile.
5. Reopen an older saved connection: its original profile stays selected; cancel an OAuth setup on `main` and confirm the shared profile remains saved.
