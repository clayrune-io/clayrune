# MC-1062 / 07: Adapt the existing site/profile Read grant

Status: proposed, not dispatched. [Parent design](../CONNECT_FLOW_SIMPLIFY.md).

> **Presentation update, 2026-10-28 branch candidate:** [13c: Connections language](13c-connections-language.md) moves reading-off into the common Read row and next-action sentence. The chip uses the connection's readiness from the shared five-status set, and Edit remains the one account-permission editor. The grant and sharing rules below are unchanged.

> **Superseded in part, 2026-10-28 (Ron).** The per-site Read row, its editor (`bind`/`html`) and its sharing list were retired:
> the Permissions step now shows ONE list for every account (Read this account's activity, Publish), and no site or domain
> is shown or typed. When the account is read through a browser sign-in, ticking Read and saving still ends in this module's
> `apply`, which adds (untick: removes) only the service's own domain on that sign-in's agent-read list behind the same
> passcode prompt. `load`, `apply`, `outcome`, `plan`, `covering` and `facts` (Details only) remain; the
> `desk-v1-connect-browser-permission.css` stylesheet is gone. A card whose reading is not allowed yet says "Reading is off"
> and its one button opens that same Permissions step (`static/js/desk-v1-reads-off.js`, `mc/desk_read_gate.py`).
>
> **Review fixes, 2026-10-28 (Dave's calls after Fenn's review of 30433660).** (1) Unticking Read removes ONLY the exact service
> domain, and only when no other saved connection on the same sign-in (`sharing()`) is using it; a covering parent is never removed.
> When the domain stays, the Desk read scopes still go off and the result says why (`staysText`). (2) The site grant targets the
> sign-in the account will have AFTER save: a reading section reports its drafted `{via, profile}` through an optional
> `reading()` hook, and `saved` is reported only if a fresh read of that profile covers the site; the old profile's list is never touched.
> (3) The one Read tick commits the `read_own` scopes of every route the account is configured to read through (`_offers().reads`).

## Depends on

03, 05.

## Ownership

New `static/js/desk-v1-connect-browser-permission.js`; `tools/smoke/desk-v1-connect-browser-permission.mjs`. Reuse existing browser-agent-read routes and DeskV1ConnectAgentRead; no replacement permission store.

Future workers are not alone in the repository: do not revert others' edits; keep wiring narrow and adapt to concurrent changes.

## Work

Show Read for the detected site on the selected named profile. Read the existing policy, preserve other domains, and submit only the intended change through the existing PUT and its own humanProofFetch. Show shared profile/site consequences. A profile that does not yet exist defers this grant until sign-in; no early implicit launch/grant. Keep one-time approval behavior outside this editor.

## Acceptance

Grant/revoke the target domain while preserving unrelated sites; two connections sharing a profile display the same grant. Cancel/wrong passcode leaves policy unchanged. Saved connection plus failed permission shows partial success. Browser Read never becomes raw-page access, posting, click/type or a claim of automatic LinkedIn feed coverage.

## Boundary

Small adapter/UI slice. It is a profile-domain permission, not a new per-account browser sandbox.
