# Agent mail: inbound authentication + plain-text strip

Library code for the Cloudflare Email Worker behind per-install agent mail
(`docs/AGENT_MAIL_SPEC.md`, MC-1166 unit 3). **Not deployed. No `email()`
handler, no forwarding, no Cloudflare account touched.** Pure functions with
unit tests; the handler (unit 2) and the injection screen (unit 5) call them.

## What it does

Ron's rules (spec "Inbound hardening"): a reply is owner-only, and only plain
text ever reaches custody. A failed check is a **drop**, not a hold.

| File | Concern |
| --- | --- |
| `auth.js` | `decide(meta, ownerRecord, tokenLookup)` -> `{accept:true, thread, evidence}` or `{accept:false, reason_code}`. `rejectionRecord()` is the only thing kept about a drop: time, reason code, auth result words. |
| `mailbox.js` | Strict one-mailbox From parsing. Domain case-insensitive, local part exact. No substring or display-name matching. |
| `authres.js` | Cloudflare `Authentication-Results` (authserv-id `mx.cloudflare.net` only) and the D6=A rule: DMARC pass with aligned SPF **or** DKIM. |
| `headers.js` | Ordered header-list helpers. |
| `strip.js` | `strip(raw)` -> plain text plus counts of what was removed (never the removed bytes). |
| `html-text.js` | HTML -> text, used only when a message has no `text/plain` part. |

### `decide` inputs

- `meta.rcptTo`: envelope recipient `<install>+<token>@<domain>`, token 26-64 chars `[a-z0-9]`.
- `meta.headers`: **ordered list** `[{key, value}]` (postal-mime's `email.headers` has this shape). Not a `Headers` object: it folds duplicates and loses order, and both matter.
- `ownerRecord`: `{active: true, install_id, generation, owner_address}`.
- `tokenLookup(token)`: sync or async; returns `{thread_id, install_id, owner_generation, expires_at (epoch ms), revoked: false, message_ids: [...]}` or null. Verifier hashing is the caller's.

Checks, cheapest first: owner active, token shape, exactly one From that equals
the owner, Cloudflare ingress result, then (only then) the lookup: unknown /
revoked / expired / wrong install / wrong owner generation, and finally that
`In-Reply-To`/`References` names one of the thread's outbound Message-IDs.

### `strip` output

`{ok:true, text, has_text, source: 'plain'|'html_fallback'|'none', stripped:{attachments, inline_images, calendar_parts, nested_messages, html_parts, hidden_dropped, remote_image_refs}}`
or `{ok:false, reason_code}` (`mime_too_large`, `text_too_large`, `mime_limits`,
`mime_parse_error`, `bad_input`). Default limits are the provisional D4 numbers
(64 KiB MIME, 16 KiB text) and can be passed in `opts`.

## Known limits

- **Ingress trust is positional and unverified against real Cloudflare output.**
  The spec requires real provider fixtures before launch. `authres.js` believes
  the one `mx.cloudflare.net` result that is the first Authentication-Results
  header and rejects two claimants.
- **Alignment is stricter than RFC 7489 relaxed**: no public-suffix list, so
  sibling subdomains do not align. Fails closed.
- `strip.js` reads postal-mime's `textMap` (internal) because `email.text` merges
  HTML-derived text into the plain body. postal-mime is pinned exactly; a test
  fails if that shape moves.
- The HTML fallback is best effort. Its output is still untrusted data.

## Test

```bash
cd workers/agent-mail
npm install        # postal-mime is the only dependency
npm test           # node --test test/*.test.js
```
