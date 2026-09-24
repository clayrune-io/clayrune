# Secrets vault

Encrypted credential storage so a Clayrune agent can perform real authenticated
work — logging into an account, posting to a social platform, calling an
authenticated API — without a password ever entering a transcript, a memory
file, a distilled skill, or the git repo.

- Module: `mc/secrets_store.py`
- HTTP surface: `mc/blueprints/secrets_routes.py`
- Agent-facing runner: `tools/with-secret.py`
- Tests: `tests/test_secrets_store.py`, `tests/test_secrets_routes.py`

---

## Where things are stored

Everything lives under `~/.clayrune/` — **outside the checkout**:

| Path | Contents | Mode |
|---|---|---|
| `~/.clayrune/secrets.json` | ciphertext + metadata | 0600 |
| `~/.clayrune/secrets.key` | master key, plaintext, *only* if no OS keyring backend is usable at all | 0600 |
| `~/.clayrune/secrets.key.dpapi` | master key, DPAPI-sealed self-heal mirror — **Windows only**, written whenever the keyring is healthy | 0600 |
| `~/.clayrune/secrets_audit.jsonl` | append-only access log | 0600 |

This is deliberate and is stronger than gitignoring. Clayrune has already been
bitten once by "gitignored but bundled anyway": `build-macos.spec` packaged
`data/SHARED_RULES.md` *because the file was still on disk* after being
untracked. A file that never exists inside the repo cannot be swept in by a
future `git add -f`, build spec, or installer glob.

`.gitignore` still carries defensive patterns (`secrets.json`, `secrets.key`,
`secrets_audit.jsonl`, `data/secrets*`, `*.secrets.json`) to catch a hand-placed
copy or a debug dump.

Override the root with `CLAYRUNE_HOME` (tests use this).

## Crypto

The master key is 32 random bytes held in the OS keyring — Windows Credential
Manager, macOS Keychain, or SecretService on Linux. If no keyring backend is
usable at all (typically headless Linux) it degrades to a plaintext 0600 key
file, and `GET /api/secrets` returns a `key_at_rest_warning` so the UI can say
so.

### The wipe incident and the self-heal mirror (2026-09-14)

A wiped Windows keystore made `keyring.get_password()` return `None` — the
same shape as "never had a key" — so `load_master_key()` minted a fresh key
over live ciphertext with no log line, orphaning 8 of 10 saved logins (the
dry-run `/api/secrets/check` only confirmed a name existed, never decrypted,
so it kept reporting the orphaned entries as fine). Two fixes, both in
`load_master_key()` (`mc/secrets_store.py`):

1. **Fail closed before minting.** If the store already holds sealed secrets
   and no key is found anywhere (keyring empty, no mirror), `load_master_key`
   raises `SecretsUnavailable` instead of silently minting a replacement.
   Minting stays automatic only for a genuinely empty store, and is now
   always logged. `is_readable(name)` / `list_secrets(check_readable=True)`
   actually attempt a decrypt, so `/api/secrets` can no longer report an
   orphaned entry as fine.
2. **A self-heal mirror, scoped to what the OS can actually protect.** A
   first attempt at this mirrored the master key into a plaintext 0600 file
   on *every* successful keyring read, on every OS — durable, but a silent
   at-rest downgrade: the key would sit in plaintext next to the ciphertext
   it protects, forever, even on a perfectly healthy box. The shipped design
   instead scopes the mirror to what each OS actually offers:
   - **Windows**: sealed with **DPAPI**, user scope (`CryptProtectData` /
     `CryptUnprotectData` via `ctypes.windll.crypt32` — no new dependency,
     `pywin32` is not required). Written to `secrets.key.dpapi` on every
     successful keyring read. A pre-existing plaintext `secrets.key` is
     removed only after the DPAPI mirror is written **and** read back to
     confirm it holds the same key — never before, so a verification failure
     leaves the plaintext copy in place rather than deleting the only
     working mirror on a guess.
   - **macOS/Linux**: there is no OS primitive equivalent to DPAPI here —
     Keychain/SecretService already *are* the keyring backend in use, so
     there is nothing further to seal a local file with. **No mirror is
     written** while the keyring is healthy; a wipe with nothing to self-heal
     from fails closed (case 1 above) instead of degrading at-rest security
     to cover for it. Any plaintext mirror found on such a box (e.g. left
     over from the first, since-revised fix) is removed the next time the
     keyring is confirmed healthy — it has no self-heal benefit left to
     justify the exposure.
   - **No keyring backend at all**: unchanged from before this mirror
     existed — the plaintext 0600 key file is the sole copy, and
     `key_at_rest_warning` fires for exactly this case.

Each value is sealed with **AES-256-GCM**, a fresh 12-byte nonce, and the
secret's own name as additional authenticated data — so a ciphertext cannot be
moved between entries. `tests/test_secrets_store.py::test_ciphertext_is_bound_to_its_name`
pins that.

Set `CLAYRUNE_SECRETS_KEY_BACKEND=file` to force the file backend (useful on
boxes where the keyring prompts interactively, which would hang a headless
server).

## Using a secret from an agent

Reference the credential by **name**; the server resolves it at the moment of
use. Placeholder syntax is `{{secret:name}}`.

The general-purpose path is `tools/with-secret.py`, which injects values into a
child process's environment:

```bash
python tools/with-secret.py \
    --user REDDIT_USER=reddit.password \
    --env  REDDIT_PASS=reddit.password \
    --project mission_control \
    -- python tools/post_reddit.py --subreddit selfhosted
```

### The username lives on the same entry

A login has two halves, so one entry carries both: the encrypted value plus an
optional **username**, referenced as `{{user:name}}`. Keeping them together is
the point — two entries (`site.user` + `site.password`) can drift apart, and
nothing would notice until a login failed.

The username is stored as **metadata, not ciphertext**, and is returned by
`GET /api/secrets` and shown in the list. That is deliberate: it is an
identifier the site itself displays back to you, and it is what tells two
accounts on the same site apart. (Precedent: a TOTP entry's `account` is
already returned in the clear.) Scope is still enforced on `{{user:…}}`;
`allow_unattended` is not, because the password half of the same login carries
that gate — an unattended run that learns the username still cannot log in.

`POST /api/secrets/check` reports `no_username` when text references
`{{user:x}}` on an entry that has none, so a dry run catches it before the
command runs.

Other injection shapes for tools that don't read env vars:

| Flag | Effect |
|---|---|
| `--user VAR=name` | inject the username stored with a secret |
| `--stdin gh.token` | pipe one secret to the child's stdin (`gh auth login --with-token`) |
| any arg containing `{{secret:x}}` | resolved in place before exec |
| `--unattended` | explicit opt-in to unattended treatment (see below — omitting it no longer implies attended) |
| `--raw` | stream child output unmodified (interactive commands only) |

Child output is scrubbed of any dispensed value before it is echoed, so a
chatty tool that prints its own password cannot leak it into the transcript. If
a secret cannot be resolved the child is **never started** (exit 2) — a missing
credential must not silently become an anonymous login attempt.

## Two-factor codes (TOTP)

Most real logins need a second factor, so the vault generates them —
`mc/totp.py`, tests in `tests/test_totp.py`.

**There is no "sync with Google Authenticator", and none is needed.** Google
Authenticator is not a service; it is a client for an open standard (TOTP,
RFC 6238). It and Clayrune hold the same shared seed and each derive the same
6-digit code from the current 30-second window, independently and offline.
Nothing is fetched from Google. Correctness is pinned against the RFC 6238
reference vectors — agreeing with those is what "agrees with the phone" means.

Three ways to get a seed in, all through the same **Value** field:

| Paste this | Result |
|---|---|
| `otpauth://totp/…` | one code generator (this is what the enrolment QR encodes — use the site's "can't scan it?" link) |
| `otpauth-migration://offline?data=…` | every account at once, from Google Authenticator's *Transfer accounts → Export* QR |
| a bare base32 seed | one code generator (pick "2FA" explicitly) |

Grouped, lowercase, and padded seeds are all accepted — sites print them
inconsistently, and rejecting a correct paste over formatting is a bad trade.

Reference a code with `{{totp:name}}`, or inject one:

```bash
python tools/with-secret.py --env GH_PASS=github.password \
                           --totp GH_OTP=github.totp -- python tools/login.py
```

If the current code has under 5 seconds left, the runner waits for the next
window rather than handing out one that expires mid-form.

`{{totp:name}}` yields a **code**; `{{secret:name}}` on the same entry yields the
**seed** (for re-enrolling elsewhere). They are separate keywords on purpose —
substituting a seed into a login form would fail confusingly, and generating a
code where a seed was wanted would too.

`POST /api/secrets/totp/<name>` checks a code you read off your phone and
returns only whether it matched. It never returns our own code: a route that
minted live second factors would be exactly the plaintext hole the rest of this
design refuses.

### The tradeoff, stated plainly

Storing the TOTP seed beside the password **collapses two factors into one** —
anything that can read the vault can now produce both. That is inherent to
unattended 2FA automation, not a flaw in this implementation; every CI system
holding an OTP seed makes the same trade.

It is made visible rather than hidden: TOTP entries are a distinct `kind`, the
UI badges them, and `allow_unattended` can be turned off per secret. For a
high-value account — a bank, a domain registrar, the Apple developer account —
the right answer is usually to leave 2FA un-automated and let the agent ask.

HOTP (counter-based) is deliberately unsupported: handing out codes without
tracking the counter would desynchronise the account.

## Policy controls

Per secret:

- **`scope`** — `global`, or a single `project_id`. A project-scoped secret is
  invisible to, and unreadable by, every other project.
- **`allow_unattended`** — when false, steward and scheduled cycles are refused;
  only an attended session may use it.

Per Ron's 2026-08-01 decision, agents may use secrets unattended by default.
The per-task gate lives in the agent rules; these flags are the backstop for
credentials that should never be touched by an autonomous cycle.

**`allow_unattended=False` is enforced against detected context, not a
caller-typed flag (MC-923, fixed 2026-08-31).** `--unattended` used to be the
*only* signal `with-secret.py` passed — an unattended cycle that simply forgot
the flag silently dodged the gate. `with-secret.py` now calls
`mc.secrets_store.detect_effective_unattended()`, which ORs the flag with
server-side detection: the Claude Code CLI sets `CLAUDE_CODE_SESSION_ID` on
every tool subprocess it spawns (not something a command line can set), which
is looked up against the `trigger_type` MC recorded at dispatch time via
`GET /api/session/trigger-type`. Fails **closed** at every step — no session
id, server unreachable, or session unknown all mean unattended. `--unattended`
remains a valid explicit opt-in (steward code still passes it) but can no
longer be defeated by omission. Detection is NOT wired into
`get_secret_value()` itself, deliberately: the human-facing Secrets panel
(`PATCH /api/secrets/<name>`, the TOTP-verify probe) calls it directly from
inside the Flask server process, where there is no `CLAUDE_CODE_SESSION_ID` —
auto-detecting there would refuse a real human editing a secret in the
browser. Any *other* future CLI-spawned consumer should call
`detect_effective_unattended()` itself, the same way `with-secret.py` does.

## What this does and does not protect against

**Does:** keeps credentials out of the durable, exfiltrating surfaces —
transcripts, `MEMORY.md`, distilled artifacts, logs, the repo — and makes every
access auditable.

**Does not:** sandbox the agent. An agent with a shell can read anything this
process can read. That is a property of giving an agent a shell, not a flaw in
the vault. The real gate is the per-task agent rules plus the two policy flags
above.

An agent may *use* a credential; **only a human may create one.** There is no
agent-facing write path. This mirrors the learning-system authority guard
(CLAUDE.md): machinery must never be able to expand the agent's own capability
set.

## Passphrase lock (MC backlog 503edfe4)

Agents run as the same OS user as the server, so a master key the server can
read unattended, an agent can read too — the keyring/DPAPI/file backends
above all share that property. The passphrase lock closes it: the key is
never written to disk unwrapped. It exists in plaintext only in a
process-memory variable (`mc.secrets_store._unlocked_key`), set by a
**human-only** unlock call, and now leaves memory sooner than "the life of
the process": an idle auto-lock and a manual "Lock now" control (MC-949
follow-up, both below) can relock it before a restart ever happens. The
server starts **locked** after every restart until a human unlocks it from
the dashboard (Settings → Vault).

This is opt-in and additive — a box that has never called `set_passphrase()`
behaves exactly as every section above describes, unchanged. Setting a
passphrase for the first time reuses the existing keyring/file-backend lookup
in `load_master_key()` to find whatever key already protects the store and
wraps it — the legacy key material is never deleted, only joined by the new
wrapped copy at `~/.clayrune/secrets.key.wrapped`.

That file holds the master key twice: once wrapped by a KEK derived
(`scrypt`) from the human's passphrase, once wrapped by a KEK derived from a
160-bit recovery key shown exactly once, at setup time. Either unwrap is
checked against an HMAC fingerprint of the key before being trusted — a wrong
passphrase or recovery key is **detected and refused**, never silently
accepted as a fresh key (the failure mode that produced the 2026-09-14
silent-remint incident this fingerprint scheme was built to catch — see
`docs/_journal/vault-keyring-remint-2026-09-15.md`).

Every read path funnels through `load_master_key()`, so a locked vault fails
closed everywhere at once: `get_secret_value`, TOTP code generation,
`list_secrets(check_readable=True)` (each entry reports `readable: false`
without raising), `GET /api/secrets` (reports `locked: true` and metadata
only). A locked vault fires **one** push notification per lock period
(`_lock_notified`), not one per job that hits it.

The unlock, set-passphrase, change-passphrase, and lock-now routes all refuse
an unattended caller the same way every other vault write route does (see
"Policy controls" above) — an agent that could unlock the vault would defeat
the whole point of a lock it can't read past on its own, and an agent that
could lock it on demand could just as easily unlock it, since both depend on
the same passcode gate to prove a human is asking. The status route
(`GET /api/secrets/vault-lock`) is the one exception: it reveals only
`unconfigured` / `locked` / `unlocked`, never a key or a value, so a job that
hits `VaultLocked` downstream can at least report *why* to a human.

### Idle auto-lock and manual "Lock now" (MC-949 follow-up)

Two ways the key now leaves memory before a restart:

- **Idle auto-lock.** `vault_idle_lock_minutes` (Settings → System → Security,
  `config.json`, default 120) — minutes of no credential use before the
  vault relocks itself. Idle is measured from the last successful
  `load_master_key()` read (or the unlock itself), tracked as a monotonic
  timestamp under the module's existing lock. `0` disables auto-lock
  entirely, same convention as every other 0-disables minutes knob in this
  codebase. Enforced in two places so the key can't outlive the window by
  accident: lazily, the next time anything calls `load_master_key()` after
  the window has elapsed (checked and cleared *before* deciding whether to
  hand back the key); and by a background sweeper
  (`mc.secrets_store.start_idle_lock_sweeper()`, started once from
  `server.py`'s `boot()`, server-process only) that ticks every 60s so the
  key is cleared even if nothing reads it in the meantime.
- **Manual lock.** `POST /api/secrets/vault-lock/lock` clears the key right
  now — the "Lock now" button next to the lock state in the Secrets window,
  shown only while unlocked. Human-only, same passcode gate as
  unlock/set/change. A no-op (still `200`) if the vault is already locked or
  was never configured.

Either path resets `_lock_notified`, so the next job that hits the newly
relocked vault fires exactly one push alert for the new lock period — the
same once-per-period throttle the restart-triggered lock already relies on.
Both are logged and audited with a `reason` of `idle` or `manual` so a relock
is traceable to which path caused it.

### Residual risk — stated plainly

The passphrase lock raises the bar; it does not remove the "same OS user"
property this document opens with. Two things stay true even with the lock
in place:

- **A same-user agent with a raw shell can still rewrite the files the fence
  doesn't see.** `steward/fence.py` only gates tool calls made through *this*
  process's own tool-use path (Claude Code sessions running here). It has no
  authority over a different CLI (Codex, another agent runtime, a plain
  script) invoked with the same OS-user privileges — that process can open
  and overwrite `secrets.key.wrapped`, `local_auth.json`, or the audit log
  directly, with no fence in front of it at all. The vault's guarantee is
  against *this* codebase's own tooled agents, not against every process
  that could ever run as this user.
- **The passphrase itself is the real secret now.** Once set, unwrapping the
  master key is reduced to "does the caller know the passphrase" — nothing
  else, no state on disk, no file to steal, stands between a shell and the
  vault. Guard it accordingly: it is not a password to reuse, share, or store
  in another tool's config.

One window is structural rather than architectural: **the first-time
`vault-lock/set` call has no prior passphrase to check against** (there is
nothing to check yet), so whoever calls it first *becomes* the passphrase
holder. That window is open from server start until Ron sets the passphrase,
and it closes the moment he does — running `vault-lock/set` immediately after
deploy, before anything else touches the box, is what closes it. The
tamper-evidence notification above exists specifically so a hijack of that
window is visible to him even if he didn't do the setting himself.

## HTTP surface

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/secrets` | metadata + key backend (never values) |
| POST | `/api/secrets` | create or rotate |
| PATCH | `/api/secrets/<name>` | edit metadata / policy |
| DELETE | `/api/secrets/<name>` | delete |
| GET | `/api/secrets/audit` | recent access records |
| POST | `/api/secrets/check` | dry-run a template: which secrets does it use, and would each resolve? |
| GET | `/api/secrets/vault-lock` | lock state (`unconfigured`/`locked`/`unlocked`) — any caller |
| POST | `/api/secrets/vault-lock/set` | first-time passphrase setup — human-only |
| POST | `/api/secrets/vault-lock/change` | rotate the passphrase — human-only |
| POST | `/api/secrets/vault-lock/unlock` | unlock with passphrase or recovery key — human-only |
| POST | `/api/secrets/vault-lock/lock` | lock now, immediately (idle auto-lock also does this in the background) — human-only |

**There is deliberately no route that returns a plaintext value.** A value only
ever leaves the process into a child process's environment or a resolved
command — never back over HTTP into a browser tab. That removes the whole class
of "the vault page was left open / screenshotted / proxied" exposure, and it is
pinned by `test_no_route_returns_the_plaintext`.

## Audit log

Every read, write, delete, and denial appends one JSONL record with the
timestamp, secret name, consumer label, project, attendedness, and outcome.
Values never appear — `test_audit_log_holds_no_plaintext` pins that.

```json
{"ts": "...", "event": "read", "name": "reddit.password",
 "consumer": "with-secret", "project": "mission_control", "unattended": false}
{"ts": "...", "event": "denied", "name": "bank.password",
 "consumer": "steward", "unattended": true, "reason": "unattended_blocked"}
```

## Migrating existing plaintext credentials

Two known plaintext stores predate the vault and should move into it:

- `~/.clayrune/night-mail.json` — the Gmail app password used by
  `tools/night-review/send_mail.py` and `tools/mail-mcp/server.py`
- `data/provider_env.json` — per-provider API keys

Both are already gitignored and outside the commit path, so this is a hardening
follow-up rather than an exposure. Not done in the initial build.
