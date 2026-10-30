# Saved browser destinations on Where

An older browser connection can have a platform URL and verified sign-in but
no `account_id`. It is a saved site, not yet a channel. Where labels it
**Needs account**, names the platform, and opens the existing saved-site
Permissions form with **Link account ›**. The user selects an existing
account or enters a handle, reviews, then supplies the passcode to save.
Read/Post changes retain their separate gates.

## Diagnosis

The legacy `browser_connection.service:null` record already resolved correctly
through `/api/desk/connect/types`, using its URL. No resolver or backend change
was needed. Where previously reused the browser check's **Ready** word and
opened the service detail card, leaving **Edit** as an unexplained extra step.

Fixture attachment also exposed a navigation race: Connections' asynchronous
post-save refresh repainted its shared parent after the user returned to Where.
The renderer now checks that Connections still owns the parent before repainting.
The direct handoff also checks ownership after loading the record and after
resolving its setup, so a late read cannot reopen an abandoned editor.

## Ownership

- `static/js/desk-v1-where-services.js`: missing-account copy and destination link.
- `static/js/desk-v1-connect-site-handoff.js`: direct read-only handoff; wired by
  Connect integration and one call in the Connections renderer.
- `static/js/desk-v1-connect-saved-reopen.js`: existing editor resume, with an
  optional caller ownership check.
- `static/js/desk-v1-connections.js`: refresh ownership check; existing account
  and service reloads still supply the newly attached channel.

## Verification and screenshots

`node tools/smoke/desk-v1-where-attach.mjs` uses the production SPA in fresh
headless Chromium contexts at 1440×1000 and 390×844, backed by fixture APIs.
Its saved Instagram record has `service:null`, profile `main`, no account,
Read/Post off and verified status. That profile name is fixture metadata;
no real saved profile or browser session is opened.

The smoke covers direct entry, required account choice, cancellation, a failed
setup read, leaving during a delayed setup read, a failed attachment and retry,
default-off permissions, account refresh without document reload, delayed
post-save refresh after navigation, and a real pointer drag that persists
campaign placement. Backend tests use a temporary Desk store to exercise actual
attachment, source validation, atomicity, human/passcode and unattended gates.

Screenshots are written under `_scratch/where_attach/`:

| View | Baseline | Candidate |
| --- | --- | --- |
| Where tile | `where_before_{1440,390}.png` | `where_after_{1440,390}.png` |
| Account attachment | `attach_before_{1440,390}.png` | `attach_after_{1440,390}.png` |
| Initial landing | `landing_before_{1440,390}.png` | Opens attachment directly |
| Attached channel | — | `attached_after_{1440,390}.png` |

Baseline captures were made before production edits. The smoke's `--baseline`
mode observes the prior extra Edit click; run it against the parent code when
recreating those captures. The fixture remains synthetic: authenticated
Instagram behavior and the operator's real attachment are not exercised.

Verified on the candidate branch:

| Command | Result |
| --- | --- |
| `node tools/smoke/desk-v1-where-attach.mjs` | rc=0; both widths, including pointer drag |
| `node tools/smoke/desk-v1-where-services.mjs` | rc=0; both widths |
| `node tools/smoke/desk-v1-where.mjs` | rc=0 |
| `node tools/smoke/desk-v1-connect-saved-edit.mjs` | rc=0; both widths |
| `node tools/smoke/desk-v1-connect-save-refresh.mjs` | rc=0; both widths |
| `node tools/smoke/desk.mjs` | rc=0 |
| `node tools/smoke/boot-smoke.mjs` | rc=0; seven boot scenarios and interaction guards |
| `python -m pytest -q tests/test_desk_saved_site_account.py` | rc=0; 17 passed |
| `git diff --check` | rc=0 |

Only fixture APIs and temporary test stores were used. No merge, push, restart,
real sign-in, real passcode entry or live Desk mutation was performed.
