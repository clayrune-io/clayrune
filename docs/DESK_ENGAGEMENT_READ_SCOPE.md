# Engagement coverage and account scope

## Coverage after Check now

`poll_project` obtains coverage again after each platform poll, including a handled
read failure or unexpected poll exception. Its `state`, `reason` and `message`
describe the resulting coverage record rather than the pre-poll snapshot.

Comparing parsed `last_ok_at >= last_attempt_at` instants identifies a latest successful attempt in records
that retain historical error text. Such text, including a stale sign-in error
kind, is suppressed. A later failure remains visible. Page-address gaps override
an older successful read, because the old read cannot establish current scope.
Fractional-second precision and timezone offsets are compared chronologically;
missing or malformed timestamps cannot suppress a recorded error.

A current read error cannot report `state: ok` merely because a historical
`last_ok_at` exists. Generic failures (including an unusable activity page) report
`not_read_yet` with **Last read failed** and retain that historical timestamp.
Bundles treat the platform as a gap, not a successfully read empty feed. A later
successful read, including zero rows, clears the error and restores `ok`. Existing
login-wall, page-address and capability-specific gaps retain their own wording.

## Attribution

Presence bindings hold `channel_id` and read settings. The workspace account holds
identity, organization ID and saved destination kind. `desk_engagement_account`
joins this metadata without reading secrets; presence read settings retain priority.

Both pane readers return `account` and `account_id`. The poll persists those fields
on new engagement items, with the bound account as fallback. Existing deduplication
and human-reviewed item state are unchanged; old rows are not automatically relabeled.

Conversations resolve a channel by `account_id` first. Without it, exactly one
matching platform/identity is required. Deleted IDs and ambiguous identities remain
unknown. Engagement channel filters retain unknown rows for the selected platform
with an **Account unknown** label, while excluding rows attributed to another account.
This does not expand the current first-account-per-platform polling model.

## LinkedIn member versus Company Page

`desk_pane_pages` contains the member notifications default. The independent
`desk_pane_activity_scope` policy permits it only when the account's saved browser
setup or connection binding explicitly identifies a member, without an organization ID.
Company Pages and legacy untyped accounts require a user-supplied activity URL under
LinkedIn's `/company/<destination>/admin/` path. When `organization_id` exists,
the destination must match it. A supplied address without a stored ID is authoritative;
no company number or URL is inferred from the display name.

A supplied bare `/company/<destination>/admin` or `/admin/` URL is expanded to
`/admin/notifications/all/`. The same helper in `desk_pane_activity_scope` runs
when activity addresses are saved to the account/presence copies and when the
reader selects an address, so older stored bare addresses work without a store
migration. It retains query strings and fragments. Other sites, non-admin paths,
already-specific admin paths (including published posts), and non-activity roles
are not rewritten. Existing validation and matching-company redirect checks apply
before any extraction; normalization does not repair invalid URLs or grant reads.

The editor's Review and Save use the expanded destination. Opening an old bare
address performs no write; an explicit human Save normalizes it under the usual
passcode gate. Reopening the canonical address does not cause repeated PATCHes.
Notifications is the selected engagement activity source; published-post statistics
remain a separate read and are not substituted for activity.

Company reads ignore project/platform discovery records, which may contain member
pages. Without an explicit admin activity URL, they return a `pages_needed` gap
before opening a page or calling the extractor. After navigation, the final URL must
still match the configured URL's company segment, as well as the organization ID
when present, before extraction. There is no personal-feed
fallback and no automatic discovery for a company account.

This guard establishes destination scope, not that every admin page has readable
notifications. The generic activity schema still validates the page's content.
No authenticated company admin read was exercised in the regression suite.

The extractor contract explicitly includes Company Page activity and empty states
such as `0 notifications total`. A classified activity page with `rows: []` is a
successful read: zero new items, no read error, and a refreshed successful coverage
record. It does not invent activity or trigger discovery. A login wall, unrelated
or blank/loading page, malformed row list, or wholly rejected rows remains a gap.
Synthetic Company Page poll checks cover these cases; they do not establish what
an authenticated LinkedIn page or the real extraction model returns.

### Editing a saved LinkedIn destination

Connections > the account > **Edit settings** > **Permissions** always shows
**Company Page admin address** when reading LinkedIn through a browser sign-in,
including a saved member with no page-address gap. The optional field is a draft;
Review shows the address before Save uses the existing human-only account PATCH.
Company Page Save and Clear use the existing `_human_only` /
`_require_human_passcode` gate and shared `humanProofFetch` prompt. An unattended
caller is refused; manual sessions and forged Origin headers cannot substitute
for the retyped dashboard passcode. Cancelling sends no PATCH. The proof is
discarded before the account store sees the update.
It accepts a plain HTTPS address on `linkedin.com` or `www.linkedin.com` with
`/company/<digits>/admin/` (or a path ending at `/admin`). The company number must
contain 1–20 digits, matching the account store's ID limit. Names/slugs, member
notifications and non-admin paths are refused with a plain correction message.
The field is not a general site or domain editor.
Validation checks the normalized admin path and refuses all literal and encoded
dot segments (including mixed-case `%2e` and literal/encoded combinations).
The server applies the same restriction before any store or identity mutation,
including a direct pages-only PATCH on an existing company account. A supplied
company ID must match the address; personal notifications cannot be saved as
company activity.

Save derives `organization_id` from the user-supplied URL and sends it together
with `read_pages: [{role: 'activity', url: <address>}]`. Changing the company
address replaces the saved activity-page list. Clearing sends an empty organization
ID and `read_pages: []`; the local account also drops the omitted ID and list from the
PATCH response so reopening cannot resurrect it. An untouched field sends no
update unless a previously saved company address still has a member kind: that
repair waits for the human to press Save, never runs just by opening the editor.

On the existing human account PATCH, `linkedin_destination.apply` synchronizes
the account, browser setup, existing purpose bindings and policy identity to
`organization` for a Company Page or `member` for Clear. Kind is returned in
account metadata, including older binding-only accounts, so reopen and permission
retries use the repaired kind. Clearing explicitly selects personal activity even
for an older account; leaving an untyped empty field untouched makes no choice.

Routes, refs, Read/Post values and exact permission scopes are not added or
expanded. A valid explicit policy gets its fingerprint updated for the new kind;
invalid/malformed consent is not re-signed and continues to fail closed. Legacy
accounts retain their legacy permission path. Binding fingerprints reflect the
changed kind, so verification of a member is not evidence for an organization.
The `wrong_kind`, `kind_mismatch`, route-coverage and already-bound setup checks
are unchanged. An agent cannot change the organization ID even if it omits all
other reading fields from its PATCH.

The consent-preservation Read test uses a synthetic LinkedIn executor registration:
this branch does not yet include MC-1118's pane-executor registration. The real
identity and setup tests use this branch's runtime. Cross-branch integration and
an authenticated Company Page read remain the integrator's checks.

## Regression checks

Backend (synthetic readers and model replies, temporary stores):

```powershell
python -m pytest tests/test_desk_account_read_pages.py tests/test_desk_accounts.py tests/test_desk_engagement.py tests/test_desk_engagement_pane.py tests/test_desk_engagement_pane_digest.py tests/test_desk_engagement_permission_execution.py tests/test_desk_conversations.py tests/test_desk.py -q
python -m pytest tests/test_desk_account_read_pages.py tests/test_desk_connect_permission_policy.py tests/test_desk_connect_browser_setup.py tests/test_desk_purposes.py -q
pyright mc/desk_connect/linkedin_destination.py
pyright mc/desk_engagement_account.py mc/desk_pane_activity_scope.py mc/desk_engagement.py mc/desk_engagement_pane_digest.py mc/desk_engagement_poll.py
```

Headless browser smokes (real static application, mocked API):

```powershell
node tools/smoke/desk.mjs
node tools/smoke/desk-v1-engagement.mjs
node tools/smoke/desk-v1-live-engagement.mjs
node tools/smoke/desk-v1-conversations.mjs
node tools/smoke/desk-v1-live-read-pages.mjs
node tools/smoke/desk-v1-connect-company-page.mjs
node tools/smoke/desk-v1-reads-off.mjs
node tools/smoke/boot-smoke.mjs
```

The live-engagement smoke covers stable IDs after a display-name change, null and
removed accounts, ambiguous duplicate identities, and platform boundaries under
channel filters. In a worktree, temporarily link the main checkout's smoke
dependencies and remove that junction before committing; do not install another copy.
