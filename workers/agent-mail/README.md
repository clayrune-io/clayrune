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
| `strip.js` | `strip(raw)` -> plain text plus counts of what was removed (never the removed bytes). MIME-tree allowlist, no HTML conversion. |

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

`{ok:true, text, has_text, source: 'plain'|'none', stripped:{attachments, inline_images, calendar_parts, nested_messages, html_parts, remote_image_refs}}`
or `{ok:false, reason_code, stripped?}` (`html_only`, `mime_too_large`,
`text_too_large`, `mime_limits`, `mime_parse_error`, `bad_input`). Default limits
are the provisional D4 numbers (64 KiB MIME, 16 KiB text) and can be passed in `opts`.

**Contract: allowlist, not blocklist.** Text is taken **only** from `text/plain`
leaves where every ancestor is the root or `multipart/mixed|alternative|related`,
and every node on the path (leaf and containers) has an absent or `inline`
`Content-Disposition` (every such header, duplicates included) and no `filename`
or `name` parameter on its `Content-Disposition` or `Content-Type`. Anything else
on the path (attachment, unrecognised disposition, another multipart subtype such
as `signed`/`digest`/`report`, a named part) drops the whole subtree; the dropped
text parts are counted under `attachments`. Inside `multipart/alternative` only
the `text/plain` alternative is read.

**HTML is never converted or kept.** A message with `text/html` and no surviving
`text/plain` returns `{ok:false, reason_code:'html_only', stripped}`: a drop with
a visible reason, no text. (Dave's call 2026-11-08: deciding what HTML hides is
an arms race; every mainstream client sends a plain alternative.) `html_parts`
and `remote_image_refs` are counts only.

## Known limits

- **Ingress trust is positional and unverified against real Cloudflare output.**
  The spec requires real provider fixtures before launch. `authres.js` believes
  the one `mx.cloudflare.net` result that is the first Authentication-Results
  header and rejects two claimants.
- **Alignment is stricter than RFC 7489 relaxed**: no public-suffix list, so
  sibling subdomains do not align. Fails closed.
- `strip.js` walks postal-mime's `parser.root` (internal MimeNode tree), because
  `email.text` merges HTML-derived text into the plain body and `textMap` does not
  say which container or disposition a leaf sat under. postal-mime is pinned
  exactly; a test fails if the version pin or the tree shape moves, and then the
  allowlist must be re-reviewed against the new tree.
- Plain text is still untrusted data: invisible Unicode (zero-width, bidi, tag
  characters) and command-shaped text pass through; the injection screen (unit 5)
  owns them.

## Test

```bash
cd workers/agent-mail
npm install        # postal-mime is the only dependency
npm test           # node --test test/*.test.js
```
