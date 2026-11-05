# MC-1152 P4: Channel impact module and routes (dark)

Status: **dark, branch only**. Base `c69a371b` (P3 merged). Backlog `e4796e7b`.
Spec: `CHANNEL_IMPACT_SPEC.md` sections 10, 12 (row P4), 13. D1 = A.

## What it adds

- Config key `desk_impact_compare_enabled`, default **off** (`mc/desk_impact_view.py`
  `CONFIG_KEY`, same shape as `desk_impact_links_enabled`). While off, every route but
  `status` answers 404 and the page module draws nothing, takes no space.
- `mc/desk_impact_view.py`: service layer over the P1 store, P3 `comparison()` and
  P3 `collect_campaigns()`. Adds a default window (whole UTC days from campaign start to
  today, or "today" for a campaign that began today), a connection-availability field,
  and refuses a definition/refresh whose connection is not available.
- `mc/blueprints/desk_impact_compare_routes.py` (registered by 4 lines in `server.py`):
  `GET compare/status`, `GET compare/<id>` (local only, never collects),
  `GET|PUT definition/<id>`, `POST refresh/<id>`. The two writes need the retyped
  dashboard passcode and refuse unattended callers (same gate as `desk_routes._human_only`).
- `static/js/desk-v1-channel-impact.js` + `static/css/desk-v1-channel-impact.css`:
  one row per plan account (plus removed accounts with history), per-channel post
  metrics, conversion cells reading `Not attributable by this source`, one separate
  **Repository context** block, states in words (no definition, reason-not-zero, stale,
  error + retry, connection unavailable). Rows are cards inside the 680px Goal column.
- Mount seam: 5 lines in `static/js/desk-v1-results.js` (an empty `#desk-v1-ci-mount`
  and a call guarded by `typeof`), plus one `<link>` and one `<script>` in `index.html`.

P1-P3 behaviour is unchanged. P4 calls into `goal_binding`, `comparison`,
`collect_campaigns` and `GitHubTransport` as they are.

## Connection: none exists

A GitHub read connection does not exist in Clayrune today: the GitHub profile's
`github-api` route is `info_only`, and the Desk store holds no GitHub service or account
(checked 2026-11-05 on this machine: services = one Instagram site; accounts = blog,
linkedin, x, instagram, youtube). So the default resolver is P1's `no_connection_resolver`:
no definition can be saved and a refresh reads nothing. A later connection flow plugs in
through `desk_impact_view.wire(connection_resolver=...)`. **The spec's "real cached backend
data from an existing authorized connection" check FAILED**: not faked, not substituted.
What ran instead is labelled below as isolated-backend evidence.

## Evidence (all run in this worktree)

- `python -m pytest tests/test_desk_impact_view_routes.py -o addopts='' -q`: 19 passed.
- `python -m pytest tests -o addopts='' -q -k "desk_impact or desk_routes or desk_pieces or index_html or static"`:
  504 passed, 19931 deselected.
- `pyright mc/desk_impact_view.py mc/blueprints/desk_impact_compare_routes.py`: 0 errors.
- `node tools/smoke/desk-v1-channel-impact.mjs` (real Desk store, ledger, impact store, P3
  comparison and P4 routes in a temp dir, injected `conn-1`; PASS at 1440 and 390; flag off
  draws nothing, every state in words, keyboard Tab reaches the refresh button with a visible
  focus ring, no horizontal overflow, no transport ever built).
- After integration: `boot-smoke`, `desk-v1-results`, `desk-v1-live-results`,
  `desk-v1-campaign`, `desk-v1-campaign-ended` rc=0; `desk-v1-campaign-content` rc=0 on two
  reruns after one failure from a pre-existing race (`refreshAuthStatus is not defined`,
  `static/index.html:3875` calls it before the deferred `provider-auth.js` defines it).

## Left for the human

- Turning the flag on, and building the connection flow, are separate decisions. The
  refresh button is never exercised from the browser (needs the passcode prompt); its route is
  covered by `test_refresh_*` and `test_both_writes_are_human_only`.
- No UI to create a definition yet: nothing can be chosen until a connection exists.
