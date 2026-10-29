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
PATCH response so reopening cannot resurrect it. Untouched fields send no update.
An empty field selects personal activity only for an explicitly saved member;
legacy/untyped or organization bindings still require a company address.

The organization ID makes `needs_company_page()` true even when an old connection
binding still says `member`. This edit does **not** rewrite bindings or grant
permissions. That stale kind has separate consequences: `purpose_view.account_kind`
still reports `member`; permission policy identity validation sees both `member`
and `organization` and refuses a kind change with `wrong_kind` (existing explicit
consent becomes invalid/fails closed); saving member browser setup also refuses
the organization ID with `kind_mismatch`. These conflicts need separate connection
repair, not an implicit consent migration in the reading editor. Legacy accounts
without explicit permission records retain the existing legacy permission path.

## Regression checks

Backend (synthetic readers and model replies, temporary stores):

```powershell
python -m pytest tests/test_desk_account_read_pages.py tests/test_desk_accounts.py tests/test_desk_engagement.py tests/test_desk_engagement_pane.py tests/test_desk_engagement_pane_digest.py tests/test_desk_engagement_permission_execution.py tests/test_desk_conversations.py tests/test_desk.py -q
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
