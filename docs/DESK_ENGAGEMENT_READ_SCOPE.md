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

Company reads ignore project/platform discovery records, which may contain member
pages. Without an explicit admin activity URL, they return a `pages_needed` gap
before opening a page or calling the extractor. After navigation, the final URL must
still match the configured URL's company segment, as well as the organization ID
when present, before extraction. There is no personal-feed
fallback and no automatic discovery for a company account.

This guard establishes destination scope, not that every admin page has readable
notifications. The generic activity schema still validates the page's content.
No authenticated company admin read was exercised in the regression suite.

### Editing a saved LinkedIn destination

Connections > the account > **Edit settings** > **Permissions** always shows
**Company Page admin address** when reading LinkedIn through a browser sign-in,
including a saved member with no page-address gap. The optional field is a draft;
Review shows the address before Save uses the existing human-only account PATCH.
It accepts a plain HTTPS address on `linkedin.com` or `www.linkedin.com` with
`/company/<digits>/admin/` (or a path ending at `/admin`). The company number must
contain 1–20 digits, matching the account store's ID limit. Names/slugs, member
notifications and non-admin paths are refused with a plain correction message.
The field is not a general site or domain editor.

Save derives `organization_id` from the user-supplied URL and sends it together
with `read_pages: [{role: 'activity', url: <address>}]`. Changing the company
address replaces the saved activity-page list. Clearing sends an empty organization
ID and `read_pages: []`; the local account also drops the omitted list from the
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
