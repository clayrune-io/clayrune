# Human-proof guard — spec (not code)

**Status:** SPEC ONLY. Nothing in this document changes, weakens, or touches
any route. Backlog `0958937a`. Ron picks an option; a separate task implements it.

## 0. The bug, in one line

Every "human-only" route on this server decides human-vs-agent by checking
for an `Origin` header on the incoming request. An agent's own HTTP client
(`curl -H 'Origin: http://localhost:5199'`, Python `urllib`/`requests`) sets
that header itself. The check is not a proof of anything — it is a request
the caller is trusted to fill in honestly, and the caller is exactly the
party it exists to distrust.

## 1. Inventory

Two guard functions, both Origin-header-based, no other structural signal
used anywhere in the codebase:

- **`workflow_routes._is_agent_caller()`** (`mc/blueprints/workflow_routes.py:49`) —
  `return not request.headers.get('Origin')`. Agent ⇒ no Origin ⇒ `True`.
- **`mc.unattended.is_unattended_caller()`** (`mc/unattended.py:63`) — returns
  `False` ("treat as human, never refuse") unconditionally if `Origin` is
  present, *before* it even looks at what agent sessions are running. Origin
  present overrides every other signal.

No other Referer/User-Agent-based human check exists in the codebase (grepped
`Origin|Referer|User-Agent` across `mc/`; the only other hits are CF Access
JWT machinery — §1c — and browser-pane UA-spoofing code unrelated to this
question).

### 1a. Routes gated by `_is_agent_caller` (workflow_routes.py + 3 importers)

| Route | File:line | Protects | Blast radius if forged |
|---|---|---|---|
| `POST /api/workflows` | workflow_routes.py:83 | create a workflow definition | agent authors a definition that dispatches tooled, characterized agents on a schedule — self-expansion, the exact case CLAUDE.md's authority guard exists to block |
| `PUT /api/workflows/<id>` | workflow_routes.py:96 | edit a definition | same, on an existing (possibly human-authored) workflow |
| `DELETE /api/workflows/<id>` | workflow_routes.py:111 | delete a definition | agent erases a human's workflow |
| `POST /api/workflows/draft` | workflow_routes.py:121 | draft-authorship act (never persisted, but gated as authorship per MC-962) | agent bypasses the "ask" gate on drafting itself |
| `POST /api/workflow-runs/<run_id>/decision` | workflow_routes.py:201 | approve/reject a paused run's decision gate | agent self-approves its own workflow's gated step |
| `POST /api/workflow-runs/<run_id>/cancel` | workflow_routes.py:224 | cancel a run | agent kills a human-started run (or hides its own) |
| `POST /api/characters` , `/team`, `/voice`, `/identity` | character_routes.py:197,331,523,605 | create a character/team | agent creates a new character — a system prompt + engine + tool access something else will later run under |
| `PUT /api/characters/<scope>/<name>` | character_routes.py:680 | edit a character | agent rewrites an existing character's persona/engine, including its own |
| `POST .../name`, `.../avatar`, `.../move` | character_routes.py:808,938,1011 | rename/re-face/rescope a character | lower severity but same class — agent alters what Ron sees on the Floor |
| `DELETE /api/characters/<scope>/<name>` | character_routes.py:1073 | delete a character | agent removes a character type |
| `POST /api/project/<id>/brainstorm/transfer` | guide_routes.py:1066 | promote a brainstorm exploration into a durable doc + backlog item | agent creates backlog work from its own idea, bypassing the human review the handoff form exists for |
| `POST /api/system/update` (stash=true branch only) | system_routes.py:1965 area | discard uncommitted local changes before a git pull | agent silently shelves (and can lose) uncommitted work — its own or someone else's |
| `POST /api/project/<id>/agent/<sid>/attend` | agent_routes.py:6080 | re-stamp a session `trigger_type` to `manual`, lifting the unattended fence | **currently dead**: the route hard-refuses every caller (`if True: return … 410`, disabled 2026-09-28 per Fenn's review). The forgeable `_is_agent_caller()` check still sits unreachable below it — a landmine if anyone ever deletes the `if True` guard without replacing the check underneath. |

### 1b. Routes gated by `is_unattended_caller` alone (forgeable)

| Route | File:line | Protects | Blast radius if forged |
|---|---|---|---|
| `POST /api/secrets` | secrets_routes.py:249 | create/overwrite a vault secret | agent plants or overwrites a credential entry later consumed by an unattended flow — value integrity, not confidentiality (no read path returns plaintext) |
| `PATCH /api/secrets/<name>` | secrets_routes.py:276 | edit a secret's metadata/policy (incl. `allow_unattended`) | agent flips a secret from human-only-unattended-use to agent-usable |
| `DELETE /api/secrets/<name>` | secrets_routes.py:314 | delete a secret | agent removes a credential, denial-of-service on whatever depended on it |
| `POST /api/secrets/import-authenticator` | secrets_routes.py:327 | bulk-import TOTP secrets from a QR export | agent seeds the vault with attacker-supplied 2FA seeds |
| `PUT /api/config` | settings_routes.py:312 | **every** editable config key: `fence_unattended_enabled`, `codex_unattended_sandbox`, `agent_permission_mode`, `scheduler_paused`, … | **sharpest single item in this inventory** — an agent flips off its own fence or sandbox in one forged PUT, full self-expansion, no further step needed |
| `POST /api/distiller/promote` | distiller_routes.py:110 | promote a `_proposed/` learning artifact into a live, auto-loaded `SKILL.md` or global preference | agent self-installs a learning artifact — this is the literal 2026-07-11 incident (`preference-1ba8d678`, "no permission/go-ahead needed") the authority guard exists to prevent |
| `POST /api/backup/restore` | backup_routes.py:540 | overwrite a project's live state from a backup snapshot | agent reverts a project to an older state, discarding newer work |
| `POST /api/backup/import` | backup_routes.py:595 | import an external backup archive | agent replaces project state with an attacker/agent-supplied archive |
| `POST /api/backup/rollback/<project_id>/<snap_id>` | backup_routes.py:674 (non-dry-run) | roll a project back to a snapshot | same as restore |

**27 live routes total** gated only by an Origin-header check (18 via
`_is_agent_caller`, 9 via bare `is_unattended_caller`), plus 1 dead-code
landmine (`attend_session`).

### 1c. What is already fixed, and why it's not enough here

- **Vault-lock set/change/unlock + recovery-key display** (secrets_routes.py
  ~442–518): double-gated — `is_unattended_caller()` first (fast, forgeable,
  effectively a no-op guard here), *then* `_require_human_passcode()`, which
  requires the **local dashboard passcode retyped in the request body** on
  every call, verified server-side (`local_auth._local_auth_verify_passcode`).
  This is the one pattern in the codebase an agent cannot forge with a header
  — see §3 Option A, which is this pattern generalized.
- **Browser-pane same-origin fetch** (`browser_routes.py:230`,
  `_is_clayrune_own_origin`): closed 2026-09-15 (Wren, MC 503edfe4). An agent
  that writes an `.html` file and opens it in the built-in Chromium pane gets
  a **real, cryptographically genuine** `Origin` header on a same-origin
  `fetch()` — indistinguishable from a human's own tab to any header check.
  The fix blocks the pane from ever navigating to Clayrune's own origin,
  closing that one entry point. **It does nothing for the Bash-tool
  `curl`/`urllib` vector**, which is simpler for an agent to use anyway and
  is what every route in §1a/1b remains exposed to.
- **`local_auth.py`'s passcode+cookie LAN gate**: `_local_auth_exempt()`
  returns `True` for **any loopback request**
  (`mc/core.py:_is_loopback_request`), unconditionally, before the cookie is
  even checked. It protects the dashboard from other devices on the Wi-Fi. It
  provides **zero** protection against a co-resident agent process, because
  that process's HTTP calls to `localhost:5199` are loopback by definition —
  the same trust boundary the human dashboard sits inside.
- **CF Access JWT** (`remote_routes.py:_cf_jwt_verified`,
  `_is_cf_tunneled_request`): cryptographic RS256 verification of the tunnel
  assertion, but **only when `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` are
  set** in the environment. Unconfigured (the common case on a box that
  hasn't set up remote access), it falls back to trusting header presence +
  loopback-peer — which a co-resident agent also satisfies. Even fully
  configured, this only authenticates *remote* traffic through the tunnel; it
  says nothing about a local dashboard session or a local agent process,
  which both arrive as loopback.

## 2. Threat model

**Attacker:** an agent process (Claude Code, Codex, or any dispatched CLI)
running as the **same OS user** as the Clayrune server, on the **same
machine**, communicating over **loopback HTTP** to `localhost:5199`. This
covers every dispatched session in this codebase — attended or unattended,
armed or fenced, in a worktree or the main checkout — because `--dangerously-
skip-permissions` gives every one of them an unrestricted Bash/HTTP client
and the steward fence (`fence.py`) explicitly allows `curl` to
`_LOCAL_HOSTS`.

**What this attacker CAN do, structurally, on this OS/process model:**
- Send any HTTP request to any local route with any headers it wants,
  including `Origin`, `Referer`, `User-Agent`, `Cf-Access-*`.
- Read any file the human user can read — including cookie jars, config
  files, and (if unencrypted) any locally-stored session token or key.
- Drive a real Chromium tab (the browser-pane tool) that produces a
  genuine, non-forged `Origin` header — already accounted for in §1c.
- Run indefinitely, unattended, with no human present to interrupt it.
- Read its own source of truth about what "looks like" a human request,
  because that source (this codebase) is not a secret from the agent that
  can read the repo.

**What this attacker CANNOT do, on the same model:**
- Type a secret that was never written to disk or to a value the agent's own
  file-read access can reach (a value that exists only in a human's head, or
  only in volatile memory the agent process cannot inspect).
- Satisfy an interactive OS-level prompt that requires a human input device
  event the OS itself attributes to a logged-in, present user (mouse/keyboard
  interrupt at the OS level, Windows Hello, a physical Yubikey tap) — *unless*
  the agent can also script input injection on the same session, which on an
  unattended desktop session with no lock screen is a real risk to flag, not
  dismiss (see Option D).
- Forge a signature over a secret it never had (a passphrase-derived key it
  cannot read from disk, an HMAC over a server-held secret it cannot read).

**The load-bearing conclusion:** any scheme whose secret material is
readable from disk by the agent's own OS user, or is a value the agent's own
process can compute or observe (a header, a cookie set by a JS your own
process wrote, an env var, a config file value) is not a barrier — it's a
detour. A real guard needs a secret that exists **only** in a place this
attacker's read access does not reach: a human's memory, an OS-mediated
input event, or a channel this attacker was never given (e.g. a second
device).

## 3. Options

### Option A — dashboard passcode, retyped in the request body (generalize the existing pattern)

Already shipped for the vault-lock routes (§1c). Apply `_require_human_
passcode()` (or an equivalent) to every route in §1a/§1b instead of, or in
addition to, the Origin check.

- **Defeats the agent?** Yes, on the load-bearing question: the passcode is
  never in a cookie, header, or file the agent's OS-level read access
  reaches (it's a PBKDF2 hash + salt on disk — the plaintext is only ever in
  the human's head and in the POST body at the moment of use). An agent
  cannot compute or read it. It *can* still trigger a route with a wrong
  passcode and get refused, or — if it can read `~/.clayrune/local_auth.json`
  and brute the passcode offline — recover a weak one; the throttle only
  slows *online* attempts.
- **Cost, desktop:** one extra field per human-only action; must set a
  passcode before first use (`_local_auth_is_configured()` — currently only
  gates vault-lock routes and the LAN dashboard, not most routes in this
  inventory).
- **Cost, phone (Capacitor APK over the CF tunnel):** works identically —
  the passcode travels in the POST body over the already-TLS'd tunnel; no
  new mobile-specific plumbing.
- **Cost, Tauri launcher:** no difference from any other embedded webview
  hitting the same local API.
- **Fresh install, no passcode set:** every gated route refuses outright
  until one is set (matches the existing vault-lock behavior) — a
  fail-closed default, not fail-open.
- **Migration:** none — additive; routes keep working for humans who type
  the passcode, agents get a clear 403 either way.
- **UX cost, real:** re-typing a passcode for every workflow edit, every
  character tweak, every config change is friction Ron will feel constantly
  if applied broadly — this is the "how the Allow-once pass proves a human
  granted it" position already on record, generalized wider than it was
  scoped for (vault only).

### Option B — HttpOnly SameSite=Strict cookie minted only by a real browser navigation

A session cookie set only when the SPA is loaded via a real top-level
navigation (not `fetch`/XHR), scoped `HttpOnly` so JS (and thus anything an
agent-authored page could read) can't see it, `SameSite=Strict` so it's never
sent cross-site.

- **Defeats the agent?** **No**, on this OS/process model, and this is the
  central honest finding of this spec: the cookie is a file on disk (browser
  profile storage) or in server memory, both of which the agent's OS user can
  read. Worse than the passcode: it requires **no interaction at all** to
  steal, just a file read — `local_auth.py`'s own cookie already demonstrates
  this (§1c: loopback is unconditionally exempt from it, precisely because a
  cookie proves nothing about who currently holds the loopback socket). Google
  Chrome's cookie store, Clayrune's own server-side session table, a Tauri
  webview's local storage — all readable by the same user the agent runs as.
  Minting it only on "real navigation" only raises the bar to *stealing* the
  cookie once set, not to producing a fresh valid one, and a same-user process
  can always read what's already been minted.
- **Cost:** meaningful engineering (a nonce-based navigation-vs-fetch
  distinction, cookie rotation, CSRF-token pairing) to build a mechanism that
  this threat model shows does not clear the bar. Not recommended to build.

### Option C — confirm via the remote-access session JWT (CF Access) or a new local equivalent

Reuse cryptographic verification the codebase already has for remote
traffic (`_cf_jwt_verified`, RS256 against CF's JWKS) as the human-proof for
local routes too, or build an equivalent local JWT minted only by a real
login flow.

- **Defeats the agent?** For **remote** traffic, when `CF_ACCESS_TEAM_DOMAIN`
  + `CF_ACCESS_AUD` are configured: yes — CF Access performs the OTP/SSO
  challenge outside this box entirely, and the JWT is signature-verified
  against Cloudflare's keys, which the agent cannot forge without CF's
  private key. This is the strongest primitive in the current codebase.
  For **local/loopback** traffic it is a non-answer: `_is_cf_tunneled_
  request` requires a loopback peer by design (cloudflared forwards to the
  origin over loopback), so a co-resident agent hitting `localhost:5199`
  directly satisfies the same peer check the genuine tunnel traffic does,
  and — on a box that hasn't set the two env vars — sails through with a
  self-forged `Cf-Access-Authenticated-User-Email` header, no cryptography
  involved (§1c).
- **Cost:** near-zero for remote traffic (already built); does not solve
  the actual attacker in §2, who is local by definition. Only useful as a
  **second, independent factor** layered on top of Option A for the subset
  of routes reachable remotely — not a standalone fix.

### Option D — OS-level human-presence prompt (Windows Hello / a native confirm dialog)

A native OS prompt (Windows Hello biometric/PIN, or a plain "Clayrune wants
to confirm — click OK" dialog spawned outside the browser) gates the action;
the server waits on a signal only the OS attributes to the interactive
desktop session.

- **Defeats the agent?** Closest to a real answer, with real caveats. A
  Windows Hello biometric/PIN challenge genuinely requires a human at the
  keyboard — a headless/unattended agent process has no session token that
  satisfies it and cannot forge the biometric. **But**: (a) on an unlocked,
  logged-in desktop with no screen lock, an agent that can shell out could in
  principle script `SendInput`/UI-automation to click through a *plain*
  confirm dialog (not Hello, which needs the actual credential) if the
  dialog offers no distinguishing secret — so the dialog alone is weaker than
  the biometric variant; (b) this only exists on the desktop the server runs
  on — no equivalent for the phone app or a fully headless server install;
  (c) meaningful new integration surface (a native helper process/binary per
  OS) for one gate.
- **Cost, desktop:** real engineering — a small native helper (Windows Hello
  via `Windows.Security.Credentials.UI`, or platform equivalent on
  macOS/Linux) the Flask app shells out to or IPCs with, blocking the request
  until it returns.
- **Cost, phone/Tauri/fresh install:** no phone equivalent exists (Capacitor
  can't invoke Windows Hello on the host machine — the phone isn't the host);
  Tauri could invoke the same native helper as a bundled desktop app; a
  fresh install has no enrolled biometric until the user sets one up in the
  OS itself, outside Clayrune's control — fails open or refuses outright in
  the gap.

## 4. Recommendation

**Option A, generalized** — the retyped-passcode pattern already shipped for
the vault-lock routes — extended to every route in §1a/§1b, is the
recommendation. It is the only option in §3 that is (a) already proven not
to trust anything the agent's OS-level read access can reach, (b) already
built and battle-tested on the most sensitive routes in the codebase, (c)
uniform across desktop/phone/Tauri with no new native surface, and (d)
fail-closed on a fresh install. Its honest cost is retyping friction, not a
security gap — and that friction is tunable (see Ron's decision 2 below).

Pair it optionally with **Option C** as a second factor for routes reachable
through the CF tunnel, since that machinery is already built and adds a
real independent signal for remote callers specifically. Do **not** build
**Option B** — this spec's own threat model shows a cookie cannot clear the
bar on this OS/process model, and local_auth.py's existing cookie already
demonstrates why (loopback-exempt by necessity). **Option D** is the one
path that could eventually be *more* than a cost-raiser, but it's
desktop-only, needs new native integration, and should be evaluated only if
Option A's retyping friction proves unacceptable in practice.

**No option in this document is agent-proof against an attacker who can also
read the disk your passcode hash lives on and brute-force offline, or who
runs before a passcode is ever set.** Every option in §3 is honestly a
cost-raiser against a same-OS-user co-resident agent, not an impermeable
wall — §2 names why that ceiling exists structurally, not as an
implementation gap this design could close.

### Decisions Ron owes (≤3)

1. **Which routes get gated, and how broadly.** All 27 in §1a/§1b at once
   (highest coverage, highest one-time retyping cost as they roll out), or
   phased by blast radius (start with `PUT /api/config` and
   `/api/distiller/promote` — the two routes in this inventory that
   directly touch the authority guard — then the rest)?
2. **Retyping friction: passcode every call, or a short-lived
   re-confirmation window** (e.g. "passcode unlocks the next N minutes of
   human-only actions," a middle ground between per-call friction and the
   permanent unlock already rejected in MC-994)? A time-boxed window is
   still a value the agent's disk-read access could observe if stored
   server-side in a way tied only to elapsed time, not to a fresh proof each
   use — worth Ron's explicit call on whether that tradeoff is acceptable.
3. **Whether to also build Option D (OS-level prompt) as a future desktop-only
   upgrade**, given it needs new native integration and has no phone/fresh-
   install story, or park it and treat Option A as the standing answer.

---

*Inventory: 27 live human-only routes on a forgeable Origin-header guard (18
via `_is_agent_caller`, 9 via `is_unattended_caller`), 1 dead-code route with
the same forgeable check unreachable behind a hard refusal, 4 routes already
correctly gated with the retyped-passcode pattern this spec recommends
generalizing.*
