# The Desk: account layer, reactions, and platform identity

**Status:** design proposal, 2026-09-09. No production code was written for this.
**Answers:** Ron's four gaps against the shipped Desk (branch
`clayrune/agent/99f017b2244f`): no reactions on past posts, platforms not
visually separated, no tie-in to the secrets manager, and "so much more this can
cover".
**Binds to:** the two standing positions of 2026-09-09 (publish through the
platform APIs, listen through the browser pane; `ron` owns X, `clayrune` owns
LinkedIn) and the CLAUDE.md rule that agents use credentials and only humans
create them.

Three registers, never blended: **Read** is what the code says. **Inferred** is
what I conclude from it. **Unverified** is what I could not open or test.

---

## 0. Recommendation

Build four things, in this order, and skip most of what the category sells.

1. **Close the receipt chain before anything else.** Release does not write the
   ledger. `approve_social_queue_item` (`mc/blueprints/project_routes.py`, Desk
   branch) flips `status` to `approved` and logs; nothing calls
   `desk.record_published`. Every downstream feature Ron asked for, reactions
   above all, hangs off a ledger row with a permalink, and today no ledger row is
   ever created by the UI. Add a **"Mark as posted"** action on an approved
   draft that takes the permalink, sets the queue item to `posted` (a status
   that already exists in `_SOCIAL_STATUSES` and is never set) and calls
   `record_published(url=…)`. This is the cheapest change in this document and
   it unblocks everything else.
2. **A per-platform "connection" row on the Board**, computed at read time from
   three existing sources (vault metadata, named profiles, and a naming
   convention for API credentials), with no new store and no vault write path.
   The Desk *surfaces* and *deep-links*; it never *creates*. Section 2 draws the
   line exactly.
3. **One narrow new pane endpoint, `POST /api/browser/text`**, returning the
   visible text of the current page, capped, with the URL it was read from. It
   is the read-side twin of the existing `/api/browser/selection` and reuses its
   short-lived-CDP shape. Engagement capture becomes a deterministic script (not
   an LLM agent) that opens the permalink in the named profile and appends a
   snapshot to the ledger row's `outcome`. Without this endpoint, agents route
   around the pane by attaching raw CDP, which has already happened on this
   machine (section 3).
4. **Platform marks as SVG symbols with per-theme tint tokens**, following the
   provider-mark precedent (`ic-prov-*`, commit `a1a26eb`) but resolving the
   colour argument from commit `3b31443` in a way that survives all three tones.

What I would not build: an analytics dashboard, best-time-to-post, hashtag or
image generation, cross-posting, multi-account, reply automation, a drag-drop
calendar, and Reddit. Reasons in section 5.

---

## 1. Account inventory: one "connection" per platform

### 1.1 The three signals that exist today

**Read.** Three independent facts about "do I have X" live in three places,
none of which knows about the others:

| Signal | Source of truth | What it proves | Fields available without a network call |
|---|---|---|---|
| Login credential | Vault, `mc/secrets_store.py`, via `GET /api/secrets` (`_public()` at line 417) | A human stored a password for this site | `name`, `username`, `kind`, `scope`, `allow_unattended`, `use_count`, `last_used_at`, `created_at`, `updated_at`, `description`, `hint`, `placeholder` |
| Signed-in browser session | Named profile dir under `~/.clayrune/browser_profiles_named/<name>`, via `GET /api/browser/profiles` (`browser_profiles()` in `mc/blueprints/browser_routes.py`) | Someone logged in through the pane at least once and Chromium persisted the cookie jar | `name`, `size_mb`, `last_used` (directory mtime), `in_use_by` (session id from the server's in-memory `browser_sessions`, or null) |
| Publish credential | Nowhere today | Ability to post through the API | none |

**Read, live on this machine (2026-09-09):**

| Platform | Vault entry | Username | Named profile | Profile last used | API credential |
|---|---|---|---|---|---|
| X | `x.com` | `@RanLevi15` | `x` | 2026-09-10T04:12Z | none |
| LinkedIn | `linkedin` | `leviran1@gmail.com` | `linkedin` | 2026-09-09T15:06Z | none |
| GitHub | `github`, `github-token` | email / `x-access-token` | `github` | 2026-09-09T17:52Z | `github-token` (gho_) |
| Discord | `discord` | email | `discord` | 2026-09-09T15:07Z | none |
| Facebook | `facebook` | email | `facebook` | 2026-08-29 | none |
| Reddit | `reddit` | `cannonfidler1` | none | n/a | none |

**Read.** `data/provider_env.json` holds only `GEMINI_API_KEY`. A grep of
`mc/`, `server.py`, `tools/*.py`, `static/js/` and the Desk branch for
`api.x.com`, `api.linkedin.com`, `w_member_social`, `X_API_KEY` found nothing.
There is no X API or LinkedIn OAuth handling anywhere in the codebase. The
publish leg of the standing position is unbuilt, which is consistent with the
Desk branch's module docstrings ("nothing in this module publishes").

**Inferred.** The vault's name for X is `x.com` while the profile is `x` and
the Desk's platform key is `x` (`desk_brief.VOICE_PLATFORM`). The three
namespaces were never reconciled because nothing ever needed to join them. The
join has to be declared, not guessed.

### 1.2 The model

One record per platform, **computed on read**, never stored as state except for
the small human-declared binding block. Proposed shape, served by a new
`GET /api/desk/connections`:

```json
{
  "platform": "x",
  "voice": "ron",
  "label": "X",
  "handle": "@RanLevi15",
  "login":   {"secret": "x.com", "username": "@RanLevi15", "last_used_at": "…", "allow_unattended": true},
  "session": {"profile": "x", "last_used": "…", "open": true, "open_known_to_server": false},
  "publish": {"secret": null, "ready": false},
  "state": "listen_only",
  "next": {"kind": "add_publish_credential", "label": "Add X API credential", "secret_name": "x.api"}
}
```

The binding block that has to be declared by a human, stored in
`data/desk.json` under a new top-level `connections` key (the store already
tolerates unknown keys; `_read_store` only `setdefault`s the four it knows):

```json
"connections": {
  "x":        {"login_secret": "x.com",   "profile": "x",        "publish_secret": "x.api",        "profile_url": "https://x.com/RanLevi15"},
  "linkedin": {"login_secret": "linkedin", "profile": "linkedin", "publish_secret": "linkedin.api", "profile_url": null}
}
```

Defaults for the two v1 platforms can be seeded in code
(`desk.DEFAULT_CONNECTIONS`) so a fresh install renders the rows before anything
is bound. The `profile_url` for LinkedIn is **Unverified**: the vault username
is an email, not a public slug, and nothing on disk holds the profile URL. It is
one field Ron types once.

### 1.3 States and how each is detected

Ordered from "nothing" to "fully able". Every test below is a pure function of
the three JSON payloads above plus the binding block; none needs the network.

| State | Meaning | Detection (all offline) |
|---|---|---|
| `unbound` | The Desk does not know which vault entry or profile belongs to this platform | No `connections[platform]` entry and no name-convention match |
| `no_credential` | No login stored | `login_secret` not present in `GET /api/secrets` |
| `never_signed_in` | Password exists, no pane session was ever saved | Vault entry present; no profile dir with the bound name |
| `signed_in_stale` | A profile exists but has not been opened recently | Profile present; `last_used` older than a threshold (proposal: 14 days) |
| `signed_in` | Profile present and recently used | Profile present; `last_used` within threshold |
| `listen_only` | Can read through the pane; cannot post through the API | `signed_in` or `signed_in_stale`, and `publish_secret` absent from the vault |
| `ready` | Can listen and publish | Above, plus `publish_secret` present |
| `attended_only` | Any state, but an unattended cycle cannot use it | `allow_unattended == false` on the login or publish secret (`google` is already in this state today) |

**What is knowable offline, precisely:**

- **Read.** That a password exists, its username, when it was last dispensed
  (`last_used_at` increments in `get_secret_value`, line 688), and its policy.
- **Read.** That a profile directory exists and when its top-level mtime last
  changed. `last_used` is `os.path.getmtime(path)` on the profile root, which
  Chromium touches when it writes; it is a proxy for "last opened", not "still
  logged in".
- **Read.** Whether the *server* thinks a session holds the profile
  (`in_use_by`).

**What is not knowable offline, and I checked:**

- **Whether the session is still valid.** The only offline candidate is the
  cookie DB's expiry column. `~/.clayrune/browser_profiles_named/x/Default/Network/Cookies`
  exists (20 KB, mtime 21:56 today) but a read-only copy attempt returned
  `PermissionError` because Chromium holds it open. Even when it is not locked,
  reading the cookie jar server-side crosses the line `browser_profiles()`
  draws in its own docstring ("a cookie jar is never served over HTTP"), and a
  cookie's expiry date says nothing about server-side revocation. **Do not
  build this.** Validity is learned the moment the pane actually loads the
  site: `Page.frameNavigated` already updates `session['live_url']`, so a
  redirect to `/login` or `/i/flow/login` is observable there. The honest state
  machine treats `signed_in` as "probably" and lets the first navigation
  correct it.
- **Whether a profile is actually open.** `in_use_by` comes from the server's
  in-memory `browser_sessions` dict. Measured while writing this: `GET
  /api/browser/profiles` reported `in_use_by: null` for `x` while ten
  `chrome.exe` processes were running with
  `--user-data-dir=…\browser_profiles_named\x` on the command line. **Inferred:**
  a session from before the last server restart, or an orphan. Either way the
  registry is not the truth about liveness, and a Desk row that says "not open"
  next to a running Chromium is the kind of bland-and-wrong Ron reacted to. The
  connection endpoint should report `open_known_to_server` separately from a
  process-scan `open` (a `Win32_Process`/`ps` filter on the profile path is
  cheap and read-only), or `browser_routes` should reconcile on startup. That
  is a bug in the existing pane, not a Desk feature, and it belongs in a
  backlog item of its own.
- **Whether the publish credential works.** A stored token that has been
  revoked looks identical to a live one until the first API call. `ready`
  means "present", and the first publish failure downgrades it (the spec
  already requires publish failures to be loud and terminal).

### 1.4 The name convention for publish credentials

**Inferred, a proposal.** The vault distinguishes `kind` only between
`password` and `totp`. A publish credential is a third thing (an API token, not
a login) and the vault does not need a new kind to represent it: it needs a
**name convention** the Desk can join on, `<platform>.api`, with the
`description` field carrying what it is. The `username` field can hold the
non-secret half where the platform has one (X OAuth 1.0a has a consumer key;
LinkedIn has a client id). Where a credential is genuinely multi-part (X OAuth
1.0a user context is four strings) the value can be a small JSON object; the
consumer is `tools/with-secret.py --env`, which does not care about shape.

**Unverified.** I did not verify which X API auth mode pay-per-use posting
requires (OAuth 1.0a user context vs OAuth 2.0 user token with PKCE), so the
exact number of strings is open. The convention holds either way.

---

## 2. The human/agent line for "modify it directly"

### 2.1 Where the line actually sits in the code

**Read.** The vault has exactly one human-facing write surface:
`POST /api/secrets` and `PATCH /api/secrets/<name>` in
`mc/blueprints/secrets_routes.py`, driven by `static/js/secrets-panel.js`
(`openSecretEditor`, `saveSecret`). The form posts browser to server; the value
never enters a transcript. `test_no_route_returns_the_plaintext` pins that no
route returns a value. `docs/SECRETS.md` states the rule: "An agent may *use* a
credential; **only a human may create one.** There is no agent-facing write
path."

**Inferred, and this is the crux.** The rule is about *who* originates the
write, not *which pane* the form lives in. `POST /api/secrets` is already
reachable by an agent with `curl` on this machine; the rule holds because the
agent rules and the secrets panel's design keep humans as the ones typing. What
the rule forbids is a **server-side path that writes to the vault on an agent's
behalf**, or a Desk route that proxies to the vault, because `/api/desk/*` is
exactly what a drafting agent is told to call (`desk_brief.build_brief` hands
Posy a `curl` to `/api/project/<pid>/social/queue`).

So the boundary is:

| The Desk may | The Desk may not |
|---|---|
| Render vault **metadata** (name, username, policy, last used) via `GET /api/secrets` | Render, cache, or proxy a value (there is no route to do so anyway) |
| **Deep-link** into the existing editor: `openSecretEditor('x.com')` to edit, `openSecretEditor(null)` to add (both exported on `window` by `secrets-panel.js`) | Add any `/api/desk/*` route that calls `vault.set_secret`, `vault.delete_secret`, or `vault.get_secret_value` |
| Pre-fill the editor's **name** and **description** when adding (e.g. name `x.api`, description "X API credential for The Desk"), which requires a small optional-args change to `openSecretEditor` | Pre-fill a value, ever |
| Deep-link into the pane with the right profile: `openBrowserPane(url, projectId, null, 'x')` (`static/js/browser-pane.js:76`) so "Sign in" opens the signed-in profile at the site | Sign in on the human's behalf with `{{user:}}`/`{{secret:}}` from a Desk route |
| Show "Forget session" as a confirmed, destructive action that calls `DELETE /api/browser/profiles/<name>` (the pane already does this with a confirm) | Delete a profile from any unattended or agent path |
| Show the audit tail filtered to the platform's secrets (`GET /api/secrets/audit`) so Ron can see the Desk's own usage | Write audit records itself |
| Store the **binding** (which secret name and profile name belong to which platform) in `desk.json` | Store anything the vault stores |

In one sentence: **the Desk composes existing human UI; it never adds a server
path.** The Secrets modal is `sidebarNav('secrets')` → `openSecretsVault()`
(`static/index.html:1567`). The Desk's connection row gets three buttons and
all three land in surfaces that already exist: *Edit login* (secrets editor),
*Open* (browser pane with the named profile), *Add publish credential* (secrets
editor, name pre-filled).

### 2.2 The one case that needs a decision: OAuth callbacks

**Inferred.** LinkedIn's `w_member_social` and X's OAuth flows end with a
redirect carrying a code that must be exchanged for a token. The natural
implementation is a server route that receives the callback and writes the
token into the vault. That route would be a **machine write path into the
vault**, triggered by a human completing consent in a browser, with the value
never transiting an agent. It is in the spirit of the rule and against its
letter, and CLAUDE.md says the three vault rules are not to be weakened
without a review.

Recommendation: **v1 does not build the callback.** The human completes the
OAuth flow in the browser pane (or any browser), copies the token from the
developer console, and pastes it into the secrets editor with the name the Desk
pre-filled. That is one paste per platform per token lifetime, and it keeps the
vault's write surface exactly where it is. If token refresh turns out to need
automation, that is the moment to run the review, with a concrete refresh
cadence to justify it.

### 2.3 What stays a human action, listed flatly

- Creating or rotating any credential (secrets editor).
- Completing an OAuth consent screen.
- Signing into a site the first time (pane, named profile).
- Forgetting a profile (`DELETE /api/browser/profiles/<name>`).
- Flipping `allow_unattended` on a secret.
- Binding a platform to a different secret name (this is a Desk `PATCH`, but it
  is reversible and holds no value, so it can be a plain human action in the
  Desk UI without touching the vault).

---

## 3. Reactions: from the pane into `outcome`

### 3.1 What the pane actually offers

**Read**, from `mc/blueprints/browser_routes.py`:

| Route | What it does |
|---|---|
| `POST /api/browser/launch` | Starts Chromium (or adopts a running session on the same named profile, `reused: true`) |
| `GET /api/browser/stream` | SSE of JPEG screencast frames plus `live_url`, `frame_w/h` |
| `POST /api/browser/input` | `mouse`, `wheel`, `text`, `key`, `navigate`, `back`, `forward`, `reload`; queued onto the single CDP sender thread |
| `POST /api/browser/selection` | `Runtime.evaluate('window.getSelection().toString()')` over a **short-lived second CDP connection**, off the reader thread (`_read_page_selection`, line 801) |
| `POST /api/browser/stop`, `GET /api/project/<pid>/browser/status`, `GET/DELETE /api/browser/profiles[/<name>]` | lifecycle and profiles |

**Read.** There is no screenshot route. The latest JPEG frame lives in
`session['frame']` and is only delivered through the SSE stream. There is no
read-page route, and the system prompt tells agents so in as many words
(`agent_routes.py:2226`: "It is a viewing/interaction surface, not a scraper:
there is no read-whole-page endpoint (only the current selection)").

**Read, and it matters.** The ceiling has already been breached once. The
distilled exploration
`data/skills/_proposed/clayrune_website/2026-08-26T17-08-38-…-why-did-the-aug-26-linkedin-post-massively-underperform-54/EXPLORATION.md`
records an agent that launched the LinkedIn profile through the pane, then
"detect[ed] browser process and Chrome Debug Protocol port" and "use[d] CDP
websocket to query LinkedIn Analytics panel" directly, pulling 3,395
impressions / 36 reactions for one post and 54 / 0 for another. **Inferred:**
when the sanctioned surface lacks a read, an agent with a shell attaches raw
CDP to the same port and reads anyway, unaudited and unbounded. A narrow
sanctioned read endpoint is *less* capability than what agents already take,
not more.

### 3.2 Three tiers, honestly labelled

**Tier A: human, no new code beyond a form.** The Ledger row gets a
"How did it do?" control with four number fields (impressions, reactions,
comments, reposts) posting to the existing
`POST /api/desk/ledger/<id>/outcome`. X shows these on the post page for the
author; LinkedIn shows them in the post's analytics panel. This is the whole
feature for a solo builder posting a few times a week, and it ships in an hour.
It is also the fallback every other tier degrades to.

**Tier B: pane-assisted, using what exists.** "Open" launches the permalink in
the named profile (`openBrowserPane(row.url, null, null, 'x')`). Ron highlights
the stats line, presses Ctrl+C; the pane already reads the selection through
`/api/browser/selection` (`browser-pane.js:271`). A small parser on the Ledger
side ("paste what you copied") turns "1,204 Views 12 Reposts 40 Likes" into
the four numbers. No new endpoint, no automation, no DOM selectors to maintain.

**Tier C: automatic, needs one new endpoint.** Specified below. A deterministic
script (not an LLM agent) walks the ledger rows that have a `url`, opens each in
the platform's named profile, reads the page text, extracts the counts, and
appends a snapshot. The script is the important part: the field scan's
survivors' rule is "never automate engagement", and a fixed script that only
navigates, scrolls, and reads cannot like, reply, or follow. An LLM agent
holding the same session could.

### 3.3 The smallest new endpoint

```
POST /api/browser/text
{ "session_id": "…", "max_chars": 20000 }
→ 200 { "url": "<live_url at read time>", "text": "<document.body.innerText, truncated>", "truncated": false }
```

- Implemented exactly like `_read_page_selection`: a short-lived CDP connection
  picked with `_pick_page_target`, one `Runtime.evaluate` with `returnByValue`,
  closed after. No new thread, no change to the reader loop.
- Returns `innerText`, not HTML, so the payload is what a human sees and
  nothing hidden.
- Returns the `url` it read from so the caller can refuse a result whose URL is
  not the permalink it asked for (a login redirect is the common case).
- `max_chars` hard-capped server-side (proposal: 50,000) so a feed page cannot
  become a 2 MB transcript entry.
- Not arbitrary JS. An `expression` parameter would be a general-purpose eval
  against a signed-in session, which is a capability expansion of the kind the
  authority guard exists to refuse. If per-platform extraction later proves to
  need DOM structure, add a second fixed-expression endpoint then, with a
  server-owned expression per platform. Start with text.

**The leak to name plainly.** `innerText` of a signed-in social page includes
notification counts, DM previews, and whoever else is in the sidebar. Two
mitigations, both cheap: the reader script calls it only on permalink URLs
(never the home feed), and it stores only the extracted numbers, never the
text. The audit precedent is `secrets_audit.jsonl`; a one-line
`[browser] text read <url> <n chars>` in the server log is enough here.

### 3.4 The outcome shape

**Read.** `record_outcome(post_id, outcome)` overwrites `row['outcome']` with
whatever dict it is given; the only test (`test_outcome_roundtrip`) round-trips
`{'likes': 9}`. Engagement changes over days, and the mentor function wants the
curve, not the last value.

Proposal: keep the route, make it additive.

```json
"outcome": {
  "latest": {"impressions": 1204, "reactions": 40, "comments": 3, "reposts": 12},
  "history": [
    {"at": "2026-09-10T…", "source": "manual", "impressions": 310, "reactions": 9, "comments": 0, "reposts": 2},
    {"at": "2026-09-12T…", "source": "pane",   "url_seen": "https://x.com/…", "impressions": 1204, "reactions": 40, "comments": 3, "reposts": 12}
  ]
}
```

`record_outcome` appends to `history` and recomputes `latest`; a `replace: true`
flag preserves today's behaviour for the test and for corrections. `source` is
`manual` or `pane`, and `url_seen` is only present on pane reads. The Ledger row
renders `latest` as four small numbers with the platform mark, and a
"last read 2d ago" note; that is the entire reactions UI and it deliberately
stops short of a chart.

### 3.5 What must exist first, and does not

- A ledger row with a `url`. Nothing creates one today (section 0, item 1).
- A way to know the post's own permalink. The API publish path returns it; the
  hand-post path needs Ron to paste it. "Mark as posted" takes it.
- For LinkedIn, the analytics panel is a separate page from the post
  (`…/analytics/post-summary/urn:li:activity:…`). **Unverified:** whether the
  impression count is present in the post page's `innerText` for the author or
  only in the panel. The Aug-26 exploration read the panel. The reader script
  may need to navigate to the panel URL, which it can derive from the activity
  URN in the permalink; that derivation is unverified.

### 3.6 Scheduling the reader

The read is free (standing position) and the profile is already signed in, so
a scheduled run is the natural home. Two constraints the pane does not enforce
and the design must:

- A browser profile has no `allow_unattended` flag. The login secret does, but
  the reader never dispenses it; the cookies are the credential. The policy
  backstop for "may an unattended cycle open this profile" does not exist. For
  v1, the reader is a script the scheduler runs, so "unattended" is by
  construction and the only gate is whether the schedule exists. If a profile
  should never be opened unattended, the human-facing control is to not
  schedule it. Worth a `desk.json` `connections[platform].read_unattended`
  boolean that the script honours, so the switch is on the Desk row rather
  than in a schedule's task text.
- Two Chromiums on one profile corrupt it. `_launch_browser` already adopts a
  running session (`reused`), but section 1.3 shows the registry can miss a
  live one. The reader must launch with the profile name and treat a non-2xx
  as "skip this run", never fall back to a throwaway profile (which would read
  logged-out numbers and record zeros as an outcome).

---

## 4. Platform identity in the UI

### 4.1 What exists

**Read.** `static/index.html` carries the sidebar icon set `ic-*` (lines
435-478; outline strokes, `currentColor`) and seven provider marks
`ic-prov-claude|gemini|codex|opencode|goose|aider|kiro` (lines 486-492; solid
fills). `ic-github` exists as an outline. There is no X or LinkedIn mark.

**Read.** The provider marks are coloured by `.fl-prov-badge.prov-*` in
`static/css/app.css:7624-7630` with **hardcoded brand hexes**, and the comment
above them says why: "Brand colours, not theme colours - the whole point of
these marks is that Ron recognises the vendor at a glance, and --accent is this
theme's blue, which made Claude read as a second Gemini." Commit `3b31443`
made that change over the earlier token-based version. The same commit message
says "No dark-theme block exists in this stylesheet (zero prefers-color-scheme
rules), so near-black is safe on the cream background."

**Read, and it contradicts that message.** `:root` in `app.css` *is* the dark
theme (`--bg: #0c0e14`, `--surface2: #1a1e28`); `body.tone-warm` and
`body.tone-editorial` are the two light tones. There are no
`prefers-color-scheme` rules because tones are class-driven, not media-driven.
So `prov-codex { color: #1a1a1a }` renders near-invisible on the default dark
tone. **Inferred:** that is an existing bug in the provider marks, outside this
proposal, and it is the exact failure the "all colours must be theme tokens"
constraint is guarding against.

**Read.** The Desk's current platform chip is text: `.desk-platform` at
`app.css:7955` on the branch is an uppercase pill in `--surface3`/`--text-dim`,
identical for every platform. The feed's draft buttons are the literal strings
"X" and "in". This is the bland part.

### 4.2 Proposal: brand shapes, theme-owned tints

The two precedents disagree only if "brand colour" and "theme token" are
treated as exclusive. They are not: define the brand colour **as a token, per
tone**.

```css
:root               { --plat-x: #e8ecf4; --plat-linkedin: #4d9be6; }   /* dark: X is white-ish, LinkedIn lifted for contrast */
body.tone-warm      { --plat-x: #201c16; --plat-linkedin: #0a66c2; }   /* light: X is near-black, LinkedIn brand blue */
body.tone-editorial { --plat-x: #151310; --plat-linkedin: #0a66c2; }
```

- X's mark is monochrome by design, so its "brand colour" is whatever the text
  colour is; binding it to the tone's `--text` value is faithful and legible
  everywhere. LinkedIn's blue reads on all three backgrounds with a slight lift
  on dark.
- Two new symbols, `ic-plat-x` and `ic-plat-linkedin`, drawn in the
  `ic-prov-*` style (24-box, `fill: currentColor`, `stroke: none`), placed
  beside the provider marks in `index.html`. Future platforms add a symbol and
  two token lines; nothing else changes.
- A `.desk-plat` chip class replaces `.desk-platform`: 16px glyph plus the
  platform name, `color: var(--plat-<name>)`, background `--surface3`. The
  glyph is the identifier; the colour is support. That is the same argument
  `a1a26eb` made for the provider marks.
- The same tint becomes a **2px left rule** on Ledger and Calendar rows and on
  Queue rows, so a column of mixed posts reads as lanes without a layout
  change. The voice chip stays as it is (`--accent-dim`), so voice and platform
  are two visibly different kinds of chip rather than two colours of one.
- The feed's "X" / "in" draft buttons become the two glyphs with a title. The
  KPI "Went out" splits into "3 on X, 1 on LinkedIn" under the number.
- The connections rows (section 1) lead with the same glyph at 20px, so the
  mark is learned once and recognised in four places.

Nominative use of a platform's mark to say "this goes to that platform" is the
same basis `a1a26eb` cites for the vendor marks, and it should be drawn as an
approximation the same way, not pasted from a brand kit.

### 4.3 Separation beyond colour

Colour alone will not fix "platforms are not clearly separated". Two structural
changes, both small:

- **Calendar becomes two lanes** when both platforms have posts in the window:
  a per-day row with an X cell and a LinkedIn cell. The current grouping is by
  day only. With two fixed platforms and two fixed voices, the lane is also the
  voice, which is why the spec split them.
- **Ledger and Calendar get a platform filter** (all / X / LinkedIn) using the
  `platform=` query the routes already accept (`list_ledger(platform=…)`).

---

## 5. What else is worth building, ranked

Scored as value to one builder broadcasting his own work, divided by effort.
"Value" is weighted toward what the spec says the Desk is for: getting Ron to
say the right thing, and telling him whether anyone heard it.

| # | Build | Why it earns its place | Effort | Verdict |
|---|---|---|---|---|
| 1 | **Mark as posted** (permalink → ledger row, queue status `posted`) | Every other feature needs a ledger row with a URL; none exists today. Also the only way the repeat-check (`similar_published`) ever has data. | Half a day | Build first |
| 2 | **Connections panel** (section 1, 2) | Answers "which accounts exist and what can I do with each" from data already on disk. Zero new stores beyond the binding block. | 1-2 days | Build |
| 3 | **Edit-rate meter on the Board** | The spec makes this a build requirement ("The Desk tracks its own edit rate and says something when it collapses"). `record_edit` already stores every rewrite with `similarity`; the Board can compute released-unedited vs released-edited from the queue and the voice store today. The learning system's 80-vs-2 precedent says the drift will happen. | Half a day | Build; it is already specified |
| 4 | **`POST /api/browser/text` + deterministic reader** (section 3) | Closes the loop the field scan could not find closed anywhere. Free reads per the standing position. | 2-3 days | Build after 1-3 |
| 5 | **Platform marks and lanes** (section 4) | Directly answers gap (b). Cheap once the tokens exist. | 1 day | Build |
| 6 | **Reply digest for the mentor** | The spec's mentor needs replies to have an opinion. Once the text endpoint exists, the same reader can pull the reply text under a permalink into a `replies` snapshot; Posy reads it on the mentor clock. | 1 day on top of #4 | Build after #4; do not let it answer replies |
| 7 | **UTM-tagged links on release** | The only reach signal that survives platform suppression (a shadowbanned or slop-flagged post still shows clicks if anyone saw it). **Unverified:** whether `clayrune.io` has analytics that would count them; if not, this is worthless. | Hours if analytics exist | Conditional |
| 8 | **Publish through the API** | The standing position's other half. Needs the credential convention (1.4), a `desk_publish.py` behind the explicit release action, and Ron's two developer-app registrations (X pay-per-use, LinkedIn `w_member_social`). Not blocked by anything in this document, but the LinkedIn app-review timeline is still dark per the scan. | 3-5 days plus waiting on platform review | Build; it was always the plan |

### What I would not build, and why

- **An analytics dashboard.** The spec says no, and the ledger's stated job is
  to stop the Desk repeating itself. Four numbers per row and "last read 2d
  ago" is the ceiling. Buffer and Hootsuite sell charts because charts are
  what a team reports upward; Ron reports to nobody.
- **Best-time-to-post.** Every incumbent has it; it is a heuristic over
  audience timezone that needs volume to mean anything and Ron posts a few
  times a week. Volume is a risk here, not a goal.
- **Hashtag, headline, or image generation.** LinkedIn's slop classifier is
  trained on exactly this output. The spec's detection defense is Ron's edit;
  generating more surface for him to not edit is the wrong direction.
- **Cross-posting or "post to all".** The identity decision forbids it: a story
  is written twice from one signal. The UI should make cross-posting awkward,
  not easy.
- **Multi-account or a Company Page.** Rejected 2026-09-09 with reasons that
  still hold.
- **Reply automation, auto-like, auto-follow.** "Never automate engagement" is
  the one rule every survivor in the scan agrees on and the one LinkedIn
  enforces against vendors. Tier C's reader is a script precisely so this
  cannot creep in.
- **Drag-and-drop calendar scheduling.** Scheduling implies a queue of
  pre-written posts waiting for a slot, which is the cadence-over-signal
  failure the incubator function exists to avoid. Release is the schedule.
- **Reddit, Discord, Facebook rows in the connections panel beyond
  "not a Desk platform".** The vault has them; the Desk should show them
  greyed with that label so the inventory is honest, and nothing more. Reddit
  is a different function and a shadowban risk.
- **A vault write path from the Desk, including an OAuth callback route, in
  v1.** Section 2.2.
- **Reading the cookie DB for session validity.** Section 1.3; it is locked,
  it is a credential store, and it cannot see revocation.

One thing that is not mine to decide but should be looked at: the attribution
guard in `project_routes.py` refuses to release an originated post unless it
contains the literal line `Written by me - Edited by Claude`
(`_ATTRIBUTION_LINE`). That line is an AI disclosure on a platform whose
classifier suppresses AI-associated content by 40%. It came from
`AGENT_RULES.md`, so it is Ron's rule and a good one ethically; it also means
every LinkedIn post carries a tell. Worth a deliberate decision, with the
detection numbers in front of him, rather than an accident of two rules never
having met.

---

## 6. What I could not verify

- The exact auth mode and number of secret strings X's pay-per-use posting
  needs. Affects section 1.4's value shape, not the convention.
- Whether LinkedIn impressions are in the post page's `innerText` for the
  author or only on the analytics page. Affects section 3.5's reader logic.
- Ron's LinkedIn public profile URL. Not on disk anywhere I looked; one typed
  field.
- Whether `clayrune.io` has click analytics. Decides #7.
- Why ten Chromium processes hold the `x` profile while the server reports it
  free. I did not kill or inspect them; it is outside this task and the fix
  belongs in `browser_routes`.
- I did not open `static/js/floor.js`, `render-core.js`, or the Desk branch's
  smoke test `tools/smoke/desk.mjs`; nothing above depends on them.
- I did not read `mc/totp.py` or the backup/import paths in `secrets_store.py`
  beyond the function list; they are not on the path of anything proposed.

## 7. What was read, for the record

Fully read: `docs/THE_DESK_SPEC.md`, `docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md`,
`docs/SECRETS.md`, `mc/blueprints/secrets_routes.py`, `static/js/secrets-panel.js`,
the Desk branch's `mc/desk.py`, `mc/desk_harvest.py`, `mc/desk_brief.py`,
`mc/blueprints/desk_routes.py`, `static/js/desk.js`, the social-queue section of
its `mc/blueprints/project_routes.py`, its `server.py` and `index.html` diffs, and
the two standing-position memory files.

Read in part: `mc/secrets_store.py` (docstring, function index, `_public`,
`list_secrets`, `get_username`), `mc/blueprints/browser_routes.py` (profiles,
CDP loop, all routes from `launch` to `profile_delete`), `static/css/app.css`
(theme blocks, provider marks, Desk chips), `static/index.html` (symbol sheet,
sidebar), `mc/blueprints/agent_routes.py` (the pane paragraph of the system
prompt), `static/js/browser-pane.js` (by grep), `static/js/social-actions.js`
(`releaseSocialItem`), `tests/test_secrets_routes.py` and
`tests/test_desk_routes.py` (the relevant tests), and the two distilled
explorations on the pane.

Live state queried: `GET /api/secrets`, `GET /api/browser/profiles`,
`GET /api/config`, `data/provider_env.json` key names, the named-profile cookie
file's existence and lock state, and the process list for Chromium holding a
named profile.
