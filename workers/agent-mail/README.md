# Agent mail: Worker transport and inbound plain-text library

Cloudflare Email Worker candidate behind per-install agent mail
(`docs/AGENT_MAIL_SPEC.md`, MC-1166 units 2 and 3). **Not deployed or enabled;
no Cloudflare account touched.** Unit 2 adds `worker.js` fetch/email/scheduled
handlers, separate Ed25519 signing, owner-only outbound construction, ingress,
and a D1 ledger/replay loop. Unit 3 remains the authentication/strip library.

The exact wire, custody, schema and enrollment handoff contract is in
[`docs/AGENT_MAIL_TRANSPORT.md`](../../docs/AGENT_MAIL_TRANSPORT.md).
`MAIL_ENABLED` and `MAIL_INGRESS_VERIFIED` default off; unit 7 must supply verified
owner/public-key/generation and service-authoritative tunnel bindings, and unit
4 must implement the signed receiver plus durable custody receipt. An account
hostname suffix or an install-provided callback is never ownership evidence.

## What it does

Ron's rules (spec "Inbound hardening"): a reply is owner-only, and only plain
text ever reaches custody. A failed check is a **drop**, not a hold.

| File | Concern |
| --- | --- |
| `signing.js` | Mail-only Ed25519 v1, exact-body digest, scoped method/path/generation/direction, timestamp and durable nonce claim seam. |
| `outbound.js` | Allowlisted note/question payload; server-derived recipient, From, Reply-To and Message-ID; send-intent deduplication and uncertainty. |
| `ingress.js` | Bounded raw stream and ordered headers; provider header agreement, loop suppression, unit-3 auth/strip, stable event identity and held missing ids. |
| `ledger.js`, `schema.sql` | Dedicated D1 state, transactional send/correlation reservation, queue bounds, leased claims, generation revocation and retention. |
| `replay.js` | Scheduled or authenticated recovery replay; proven routes only, fresh signing nonce, manual redirects, exact durable-custody receipts. |
| `worker.js` | Thin fetch/email/scheduled entry and synthetic dependency injection factory; no enrollment or dashboard-auth bypass. |
| `manifest-http.js` | `GET /v1/mail/manifest`: serves the PRE-SIGNED service manifest from the `MAIL_SERVICE_MANIFEST` var, verbatim. Never signs, never holds the root key; a missing or malformed var is a 503 with a closed code. |
| `auth.js` | `decide(meta, ownerRecord, tokenLookup)` -> `{accept:true, thread, evidence}` or `{accept:false, reason_code}`. `rejectionRecord()` is the only thing kept about a drop: time, reason code, auth result words. |
| `mailbox.js` | Strict one-mailbox From parsing. Domain case-insensitive, local part exact. No substring or display-name matching. |
| `authres.js` | Cloudflare `Authentication-Results` (authserv-id `mx.cloudflare.net` only) and the D6=A rule: DMARC pass with aligned SPF **or** DKIM. |
| `content-id.js` | `strictMsgId(value)`: the bare id of a plain msg-id (atext, `.`, `@`, optional simple `[literal]`), CFWS outside the brackets skipped; `null` for anything else. Used for Content-ID / `start` matching. |
| `headers.js` | Ordered header-list helpers. |
| `canonical.js` | Canonical-form gate helpers for `strip()` (bare CR, boundary-line shape, boundary nesting, header grammar, header allowlists). Pure. |
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
the match is not unique (case-insensitively), no root is read. A padded
Content-ID name now refuses the entire message before root selection (round 13).

**Refusals, checked before any text is taken** (including
subtrees the allowlist would drop). Each returns `{ok:false, reason_code}` with no
text and no counts:

- `ambiguous_mime`: any part, the top level included, has more than one
  `Content-Type`, `Content-Disposition`, `Content-Transfer-Encoding` or `Content-ID` header.
  Counted on each part's raw header lines (name case-insensitive, folded lines
  grouped, whitespace before the colon ignored), not on the parsed first-wins view,
  so a second header cannot show one parser a body and another a filename.
- `malformed_mime`, padded shaping-header names (round 13): whitespace between
  `Content-Type`, `Content-Disposition`, `Content-Transfer-Encoding` or `Content-ID`
  and its colon refuses the whole message, at the top level or in any nested part,
  including dropped subtrees. postal-mime ignores these padded names during
  extraction while validation trims them; accepting one could keep an attachment,
  raw HTML or an undecoded body as plain text. Duplicate-header refusal retains
  priority. Ordinary value whitespace/folding and body text are unaffected.
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
  indented `: proceed` line is accepted. A section whose first line is a fold
  (space/tab) is refused: postal-mime reads it as a fold of nothing, Thunderbird keeps
  the whitespace in the field name, so `<TAB>Content-ID: <x>` is an ID to one and not
  the other. A legitimate reply never trips it.
- `malformed_mime`, canonical form (`canonical.js`, round 9), checked on the raw bytes
  and bodies included. postal-mime and Thunderbird disagree on which lines are
  delimiters (Thunderbird ends a line at a lone CR, ignores FF/VT after a boundary and
  asks the innermost multipart first; postal-mime splits on LF, accepts only space/tab
  after a boundary and asks the outermost first), so a part could be a body part to
  one and a hidden resource to the other. Refused outright rather than reconciled:
  1. any CR not immediately followed by LF (this also refuses CR CR LF);
  2. any line that starts `--` + a declared boundary and continues with anything other
     than an optional `--` and then only space/tab (`--Bx`, `--B--x`, `--B` FF, ...);
  3. a nested multipart whose boundary equals, is a prefix of, or has as a prefix, any
     ancestor's boundary (RFC 2046 5.1.1 forbids a delimiter inside the parts it
     encloses; the equal half is a real split, the prefix halves are defence in depth
     because Thunderbird matches a delimiter at exactly the boundary's length; sibling
     boundaries are not compared with each other, but rule 2 refuses a line that
     continues any declared boundary);
  4. a header section with a fold as its first line (above).
  Gmail, Apple Mail, Outlook and Thunderbird send CRLF with random distinct boundaries,
  so none of these refuses ordinary mail. Not refused: text after the closing
  `--B--` (epilogue); postal-mime ignores it, so it is never returned.
- `malformed_mime`, header grammar (`canonical.js`, rounds 10-11), checked on the
  raw header values of every part, top level and nested, dropped subtrees included.
  postal-mime and Thunderbird read a Content-Type differently (postal-mime drops
  comments, lets a plain `boundary=` beat RFC 2231 `boundary*=`, trims a quoted value
  and unquotes a quoted type; Thunderbird stops all parameter parsing at the first
  parameter it cannot read and keeps a quoted type quoted), so the two would split one
  message on different delimiters. A home-grown scanner for those differences was a
  third reader and did not converge, so the WHOLE value must match a strict grammar
  and anything else is refused (round 11):
  5. Content-Type = `token "/" token *( OWS ";" OWS token "=" ( token / quoted ) ) OWS
     [";"]`; Content-Disposition = the same with a single leading token and no `/`;
     Content-Transfer-Encoding = one token. token = RFC 2045 token; quoted = `"` +
     anything but `"`, backslash, CR, LF + `"`; OWS may include a fold. So no comment,
     no backslash, no space in a name or around `=`, no junk after a value, no quoted
     type, no empty parameter;
  6. any `boundary*` parameter, or more than one `boundary` parameter;
  7. a `boundary` whose value is not an RFC 2045 token or a quoted string with no
     leading or trailing space inside;
  8. a `multipart/*` with no `boundary` parameter.
  `filename*`, `name*`, `x-unix-mode`, `creation-date="..."` and similar parameters match
  the grammar, so Gmail, Apple Mail, Outlook and Thunderbird output (including
  non-ASCII attachment names) still passes; a legal but rare message with a comment,
  a backslash escape or an RFC 2231 boundary is refused on purpose.
- `malformed_mime`, header allowlists (`canonical.js` `headerAllowlistRefusal`, round
  12). Thunderbird truncates a header value at NUL, decodes uuencode/yEnc bodies,
  prefers RFC 2231 `start*`/`charset*` to the plain parameter and reads `X-Sun-Charset`;
  postal-mime does none of those. Closed as classes, each an allowlist:
  9. a C0 control (0x00-0x1F except HTAB) or DEL (0x7F) in the name or unfolded value of
     any `Content-*` header, in any part;
  10. a Content-Transfer-Encoding other than `7bit`, `8bit`, `binary`,
      `quoted-printable`, `base64` (case-insensitive; absent is 7bit);
  11. on Content-Type / Content-Disposition, a parameter whose name contains `*` unless
      the part before the first `*` is `name` or `filename`;
  12. an `X-Sun-Charset` header on any part.
  Apple `name*=`, Gmail/Thunderbird `filename*0*=` and every other fixture still pass.
  Cost: a legal `start*=` / `charset*=` / `boundary*=` message is refused.

**HTML is never converted or kept.** A message with `text/html` and no surviving
`text/plain` returns `{ok:false, reason_code:'html_only', stripped}`: a drop with
a visible reason, no text. (Dave's call 2026-11-08: deciding what HTML hides is
an arms race; every mainstream client sends a plain alternative.) `html_parts`
and `remote_image_refs` are counts only.

## Publishing the signed service manifest

`GET /v1/mail/manifest` (no query string, GET only) returns the root-signed manifest the dashboard
verifies against its pinned root (`mc/agent_mail_manifest_fetch.py`). The Worker does **not** sign:
it serves the JSON in the plain var `MAIL_SERVICE_MANIFEST` byte for byte, `cache-control: no-store`.
The var is public by design (only the root key can produce a document the dashboard accepts), so it
is not a secret; the root PRIVATE key never goes near Cloudflare.

- Unset var: `503 {"code":"manifest_not_configured"}`. Wrong shape, bad signature encoding or over
  32 KiB: `503 {"code":"manifest_malformed"}`. The shape check is not a signature check.
- Sign offline with `tools/agent-mail-manifest-sign.py` (the key is read from a file path outside
  every git checkout, never from argv or env), then set the output as `MAIL_SERVICE_MANIFEST` in your
  own `wrangler.toml` or the dashboard and deploy. The signed `sequence` must exceed the previous one,
  and a new manifest must be published before the old one expires: the dashboard refuses a stale one.

## Known limits

- Synthetic transport tests use real SQLite through a minimal D1 facade and
  generated test-only Ed25519 keys. They do not verify Cloudflare's runtime,
  provider delivery, actual tunnel ownership, installed custody or completed
  turns. No real destination verification, DNS/DKIM or mail has been performed.
- Unit 2 keeps the stricter unit-3 no-HTML-fallback decision. The broader draft
  spec's HTML fallback does not override Dave's recorded refusal decision.
- Scheduled replay and the install recovery call are both required. A failed
  Email Worker invocation is **not** claimed to provide a durable provider retry
  guarantee; queue-capacity/storage failure is an explicit custody failure.

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
  second header parser, with its boundaries taken from the tree. `canonical.js` does
  the same for the whole message (bodies included), refusing non-canonical input rather
  than reconciling it with Thunderbird's parsing.
- Plain text is still untrusted data: invisible Unicode (zero-width, bidi, tag
  characters) and command-shaped text pass through; the injection screen (unit 5)
  owns them.

## Test

```bash
cd workers/agent-mail
npm install        # postal-mime is the only dependency
npm test           # node --test test/*.test.js
```

Node 24.11+ is needed for the transport suite's test-only `node:sqlite` adapter.
Run repository verification through the installation's `tools/test-run.py`
launcher, preserving this working directory and the exact `npm test` command.
