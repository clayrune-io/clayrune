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
| `content-id.js` | `strictMsgId(value)`: the bare id of a plain msg-id (atext, `.`, `@`, optional simple `[literal]`), CFWS outside the brackets skipped; `null` for anything else. Used for Content-ID / `start` matching. |
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
or `{ok:false, reason_code, stripped?}` (`html_only`, `ambiguous_mime`,
`malformed_mime`, `mime_too_large`, `text_too_large`, `mime_limits`,
`mime_parse_error`, `bad_input`). Default limits
are the provisional D4 numbers (64 KiB MIME, 16 KiB text) and can be passed in `opts`.

**Contract: allowlist, not blocklist.** Text is taken **only** from `text/plain`
leaves where every ancestor is the root or `multipart/mixed|alternative|related`,
and every node on the path (leaf and containers) has an absent or `inline`
`Content-Disposition` (every such header, duplicates included) and no `filename`
or `name` parameter on its `Content-Disposition` or `Content-Type`. Anything else
on the path (attachment, unrecognised disposition, another multipart subtype such
as `signed`/`digest`/`report`, a named part) drops the whole subtree; the dropped
text parts are counted under `attachments`. Inside `multipart/alternative` only
the `text/plain` alternative is read. Inside `multipart/related` only the **root**
is read (RFC 2387: the child whose `Content-ID` matches the `start` parameter, or
the first child when there is no `start`); the other children are resources of the
root, counted and never read. A `start` that names no child (or several) reads
nothing under that container. Content-IDs and `start` are read strictly
(`content-id.js`): the id inside the brackets must be plain atext, `.` and `@`,
optionally ending in a `[literal]` of letters, digits, `.`, `:` and `-`; comments
and whitespace outside the brackets are skipped. Quoted local parts, comments
inside the id and other exotic RFC 5322 spellings are not interpreted. Root
selection **fails closed**: if `start` or any child's Content-ID is not strict, or
the match is not unique (case-insensitively), no root is read. A child's
Content-ID is found by trimmed header name, so `Content-ID : <x>` (whitespace
before the colon, which postal-mime does not parse) still counts.

**Three refusals, checked before any text is taken** (including
subtrees the allowlist would drop). Both return `{ok:false, reason_code}` with no
text and no counts:

- `ambiguous_mime`: any part, the top level included, has more than one
  `Content-Type`, `Content-Disposition`, `Content-Transfer-Encoding` or `Content-ID` header.
  Counted on each part's raw header lines (name case-insensitive, folded lines
  grouped, whitespace before the colon ignored), not on the parsed first-wins view,
  so a second header cannot show one parser a body and another a filename.
- `malformed_mime`: a part's raw `Content-Type` declares `multipart/*` but postal-mime
  parsed it as a leaf (it rewrites a multipart with a missing, empty or non-ASCII
  boundary to `text/plain`), which would hide a declared `signed`/`digest`/... type
  from the subtype exclusion. The declared type is read the way postal-mime reads a
  Content-Type value (comments carry no meaning, quoted strings are honoured), so
  `(a; b) multipart/signed` and `multipart(x)/signed` are multipart and
  `text/plain (multipart/signed)` is not. `Content-Type : multipart/...` (space before
  the colon) counts as a Content-Type here, as in the duplicate check.
- `malformed_mime`, header sections: every header section of the message (the top
  level, and each part after a delimiter line of a boundary the tree declares) is read
  from the raw bytes, not from postal-mime's parsed view. It must consist only of
  header-field lines (`name:` with optional space/tab before the colon) and space/tab
  folds, contain no bare CR, and end on an empty line (LF or CRLF); otherwise the whole
  message is refused. postal-mime ends a header block at the first line that is neither
  a field nor a fold and splits lines on LF only, while Thunderbird ends it at a
  blank line, skips colon-less lines and splits on a bare CR. A Content-ID behind a
  junk line, a bare CR, a folded name (`Content-ID` CRLF TAB `: <x>`), or a form feed /
  vertical tab before the colon was therefore read by one and not the other. Only
  header sections are inspected, so body text such as `Approved` followed by an
  indented `: proceed` line is accepted. A blank line made of CR CR is refused. A
  legitimate reply never trips it.

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
  allowlist must be re-reviewed against the new tree. The duplicate-header check
  reads each node's `rawHeaderLines` from that same tree, so it sees exactly the
  header block postal-mime split. The header-section check is the one place strip.js
  reads the raw bytes itself: a line-shape check (field / fold / empty line), not a
  second header parser, with its boundaries taken from the tree.
- Plain text is still untrusted data: invisible Unicode (zero-width, bidi, tag
  characters) and command-shaped text pass through; the injection screen (unit 5)
  owns them.

## Test

```bash
cd workers/agent-mail
npm install        # postal-mime is the only dependency
npm test           # node --test test/*.test.js
```
