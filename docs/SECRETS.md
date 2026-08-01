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
| `~/.clayrune/secrets.key` | master key, *only* if the OS keyring is unusable | 0600 |
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
usable (typically headless Linux) it degrades to a 0600 key file, and
`GET /api/secrets` returns a `key_at_rest_warning` so the UI can say so.

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
    --env REDDIT_USER=reddit.user \
    --env REDDIT_PASS=reddit.password \
    --project mission_control \
    -- python tools/post_reddit.py --subreddit selfhosted
```

Other injection shapes for tools that don't read env vars:

| Flag | Effect |
|---|---|
| `--stdin gh.token` | pipe one secret to the child's stdin (`gh auth login --with-token`) |
| any arg containing `{{secret:x}}` | resolved in place before exec |
| `--unattended` | steward/scheduled cycle — enforces the attended-only flag |
| `--raw` | stream child output unmodified (interactive commands only) |

Child output is scrubbed of any dispensed value before it is echoed, so a
chatty tool that prints its own password cannot leak it into the transcript. If
a secret cannot be resolved the child is **never started** (exit 2) — a missing
credential must not silently become an anonymous login attempt.

## Policy controls

Per secret:

- **`scope`** — `global`, or a single `project_id`. A project-scoped secret is
  invisible to, and unreadable by, every other project.
- **`allow_unattended`** — when false, steward and scheduled cycles are refused;
  only an attended session may use it.

Per Ron's 2026-08-01 decision, agents may use secrets unattended by default.
The per-task gate lives in the agent rules; these flags are the backstop for
credentials that should never be touched by an autonomous cycle.

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

## HTTP surface

| Verb | Path | Purpose |
|---|---|---|
| GET | `/api/secrets` | metadata + key backend (never values) |
| POST | `/api/secrets` | create or rotate |
| PATCH | `/api/secrets/<name>` | edit metadata / policy |
| DELETE | `/api/secrets/<name>` | delete |
| GET | `/api/secrets/audit` | recent access records |
| POST | `/api/secrets/check` | dry-run a template: which secrets does it use, and would each resolve? |

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
