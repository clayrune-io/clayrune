# Clayrune — Claude Code project notes

**This file is auto-loaded into EVERY session for this project.** Its size is a
per-session context tax on every agent, forever — so it holds only what an agent
must know *before* it starts, not the history of how things got this way.

The bar for a section here: **an agent that reads the code carefully still could
not derive it.** Binding constraints, invariants whose violation is silent, and
incidents that already happened. Design narrative, build orders and shipped-work
status belong in `docs/` — see `docs/CLAUDE_MD_ARCHIVE.md` for what was moved
out on 2026-08-06 and why.

## Commit discipline — stay scoped to your own session (2026-06-08)

When asked to commit "the work we did," stage **only the files you edited this
session, by explicit path** (`git add <path> …`):

- **Never** `git add -A`, `git add .`, or `git commit -a`. Name the paths.
- **Don't sweep, don't narrate.** The working tree always carries unrelated
  dirty files (other MC-managed projects' data under `data/projects/`,
  backups, `_scratch/`, mobile/store assets). Don't stage or list them back —
  only enumerate other dirty files if the user asks "what else is uncommitted?"
- Scratch/throwaway artifacts go in **`_scratch/`** (gitignored), not
  `tools/`, `docs/`, or `data/`.
- Pairs with the standing rule to commit your own completed work without
  asking — but *only your own*.

`.gitignore` enforces the structural half: backups (`*.bak`/`*.broken`),
runtime (`data/mc_child_pids.json`, `data/skills/_proposed/`), `_scratch/`,
`tools/_*` scratch, served build artifacts, and mobile-app assets are all
untracked so they never reach a commit candidate.

## BINDING — `master` is the release channel: keep it pushed (2026-07-12)

`/api/system/update` is `git pull --ff-only` on the user's **current branch** —
for everyone who isn't us, that's `master`. No other release channel exists,
so an unpushed `master` freezes every other user on old code, silently.
Incident 2026-07-12: `origin/master` was 138 commits behind local `master`
(local 33 more beyond that) — months of shipped work reached nobody, no
warning.

**The rule:** when a feature branch is done and green, land it on `master`
and **push `master`** in the same breath — merging locally is not shipping.
One-command check, run at the end of any session that landed work:

```bash
git rev-list --left-right --count origin/master...master   # want: 0  0
```

Left > 0 = we're behind the remote. **Right > 0 = users are behind us, and
won't know it** — the dangerous case.

## BINDING — nothing operator-specific goes in the repo (2026-07-12)

This repo is public, consumed by other people's machines *and agents*.
Anything true only of **this** install is user data, not source — ask "would
this be wrong on a stranger's machine?" before committing. Three that bit us
(fixed 2026-07-12, commit `3a1fd04`):

- **Rules files are the sharpest edge.** `data/SHARED_RULES.md` is read
  verbatim into the system prompt of **every agent on every project**; a
  project's `AGENT_RULES.md` does the same for that project. Committing them
  had injected one operator's personal preferences (and email) into every
  other install's agents. Both now gitignored; a fresh install with no rules
  is the correct default — the Rules editor writes them.
- **Gitignoring isn't enough if a build bundles the file.** `build-macos.spec`
  packaged `data/SHARED_RULES.md` *"if present"* — still present on the
  builder's disk after being ignored, so it baked into the shipped `.app`
  anyway. When you untrack something, **also check build specs/installer.**
- **Personal identity/paths belong in the environment.** Signing identity →
  `tools/signing.env` (gitignored). Machine paths → derived, or `MC_DIR` /
  `JAVA_HOME` / `ANDROID_HOME` / `CLAYRUNE_MOBILE_REPO`. Recipients → config,
  never hardcoded. Ops tooling auditing our own accounts
  (`tools/gcp-cost-review/`, billing-account ID) stays untracked.

Legitimately public and kept: the `LICENSE` copyright line, and the Play
Store `PRIVACY_POLICY.md` / `LISTING_COPY.md` contact address.

## BINDING — credentials go in the vault, never in a command line (2026-08-01)

`mc/secrets_store.py` is the secrets vault. Full detail: `docs/SECRETS.md`.

**Reference a credential by name and let the server resolve it.** Never type
one into a command, a repo config file, or a message — anything typed lands
in the transcript, then `MEMORY.md`, possibly a distilled artifact, permanently.

```bash
python tools/with-secret.py --env GH_TOKEN=github.token -- gh api ...
```

Placeholders (`{{secret:name}}`) resolve server-side; `tools/with-secret.py`
injects into the child's environment and scrubs dispensed values from output.

**A login is one entry, not two** — username lives on the same secret as the
password: `{{user:name}}` / `--user VAR=name` (username), `{{secret:name}}` /
`--env VAR=name` (password), `{{totp:name}}` / `--totp VAR=name` (2FA code).
`curl -s localhost:5199/api/secrets` lists what exists (metadata only — names,
usernames, scope; never a value):

```bash
python tools/with-secret.py --user U=reddit --env P=reddit -- python post.py
```

If `{{user:name}}` errors "no username stored", the entry is half-filled — say
so and ask for it. Do **not** substitute a guessed username: an anonymous or
wrong login attempt is worse than a refused command.

Three rules — **don't weaken without a review:**

1. **Nothing under the repo ever holds a secret.** Store, master key, audit
   log live in `~/.clayrune/`. Gitignoring alone is not sufficient (see the
   `SHARED_RULES.md`-in-`build-macos.spec` case above).
2. **No route returns a plaintext value.** Management API is metadata-only.
3. **Agents use credentials; only humans create them.** No agent-facing write
   path — same reason the learning system has an authority guard: machinery
   must never expand the agent's own capability set.

Per-secret `scope` (global/one project) and `allow_unattended` (blocks steward
and scheduled cycles) are the policy backstop. Agents may use secrets
unattended by default (Ron's decision); per-task gate lives in agent rules.

## Learning-system safety rails — LOAD-BEARING (2026-07-11)

Steward mode put an autonomous agent on the consuming end of the same
artifact stream the Distiller produces. Three rules hold the loop together
(`mc/distiller.py`, tests `tests/test_distiller_safety.py`). **Do not weaken
any without a committee review** — each pins a real past violation.

1. **The authority guard is the constitutional bright line.** Learning may
   change *how* the agent works, never *what it's allowed to do*.
   `_authority_violation()` refuses any artifact granting autonomy, removing
   an approval gate, or expanding capability — refused in
   `_generate_and_write_artifact` **before** the human queue (rubber-stamping
   happens there: 80 promoted vs 2 rejected as of 2026-07-11). Deterministic,
   fails closed; a human can type such a rule by hand into CLAUDE.md, the
   *learning system* cannot author it. **Why:** one sentence in one session
   ("Full autonomy, no permission/go-ahead needed, by any means necessary")
   became a global always-loaded PREFERENCE skill telling every agent to stop
   asking permission. Six such artifacts quarantined to
   `~/.claude/skills_quarantine_2026-07-11/`.
2. **A human must be on at least one side of every learning loop**
   (`_UNATTENDED_LOOP_RULE`). Artifacts carry `origin: interactive|unattended`
   (conservative OR over evidence). `exploration_read_floor
   (consumer_unattended=True)` withholds unattended-origin artifacts from
   steward cycles, so autonomous output can never become autonomous input.
   Unstamped (pre-2026-07-11) artifacts fail closed.
   `distiller.STEWARD_TASK_MARKER` is pinned to `fence.STEWARD_MARKER` by
   test — a rename must not silently un-gate this.
3. **"No" must be durable.** `_suppress_artifact` used to record nothing for
   a cross-project artifact (no owning project stats file), so the Distiller
   re-proposed it — `preference-1ba8d678` was live in `~/.claude/skills/`
   while sitting in `_rejected/`. Global rejections now persist to
   `_GLOBAL_SUPPRESSION_PID` (`data/projects/_global_skill_stats.json`,
   covered by `EXCLUDED_SIDECAR_SUFFIXES`), bound via `_is_suppressed`.

**Still true:** `distiller_mode: auto` is a comment, not code — promotion is
human-only, and the steward's PreToolUse fence blocks writes under
`.claude/`, so it cannot self-install a skill.

## BINDING — unattended agents never write backlog notes (2026-08-15)

A backlog item holds the **ask** and its **current state** — it stops being a
work list once it fills with a log. Stewards, night-review, and any
scheduled/autonomous cycle must write their running log to a **journal
file** — `docs/_journal/<item-id>-<slug>.md`, gitignored, no cap — never to
`POST …/backlog/<id>/note`. Changing what the item *says* (`PATCH
text`/`status`) is always allowed. An attended session may add a note,
sparingly.

**Why:** `_append_note_to_backlog_item` truncated at `text[:2000]` and kept
`notes[-50:]` — both *destroyed data in total silence*. Measured 2026-08-15:
mission_control's 28 live items carried 16 KB of task text against 132 KB of
notes; the steward charter sat at exactly 50 notes with 24 cut mid-sentence
(`data/projects/<id>.json` is untracked, so those findings are gone). Dozens
of *done* items had also silently hit the ceiling — the steward had even
noticed the truncation and started splitting findings into "(cont.)" notes,
accelerating its own data loss.

Both caps now `_log` when they bite. `tools/backlog-journal-export.py`
extracts notes to journals (re-runnable); `tools/backlog-journal-migrate.py`
strips unattended notes off live items. `PATCH` accepts `notes` as the only
removal path.

## Run the smokes AFTER each merge, not after the last one (2026-09-10)

Two agents' branches can both be green and still break on merge. Measured
2026-09-10: one branch renamed `_channelPersonFilter` to `_channelExpanded`
(Channel accordion); a same-day branch added the drag-to-hire thread shell
against the OLD name. Different lines, so git merged both cleanly, and
neither branch's own green run could have caught it — surfaced only as a
runtime `ReferenceError` that took out five `drag-to-hire.mjs` checks.
Worktree isolation prevents agents colliding *while* they work; nothing
checks the combination afterward (`coordination_enabled` is awareness-only).

So: after merging an agent branch, run the smokes covering what it touched
**before** merging the next one — testing once after two merges tells you
something broke, not which. `tools/smoke/*.mjs` is seconds; a bisect across
two merges is not.

**If Playwright looks missing, you're in a worktree — do not install it.**
`tools/smoke/node_modules/` and the Chromium cache live in the MAIN checkout,
gitignored, so every `.clayrune/agents/<id>/` worktree starts without them.
Link the main checkout's `node_modules` in instead, run the smoke, remove
the link before committing (four Chromium builds were already cached on this
box as of 2026-09-11 from agents that read the gap as machine-level and ran
`npm install`).

## Exception-swallowing policy (2026-06-09)

When touching any function containing `except Exception: pass`, decide: if
the try-body is pure best-effort cosmetics (temp-file cleanup, optional
Pillow shrink), leave it. If it wraps **subprocess, file I/O on state files,
JSON state load/save, or network**, convert to:

```python
except Exception as e:
    _log(f"[<subsystem>] <operation> failed: {e}", flush=True)
```

(keep swallowing — just make it observable.) Do **not** bulk-sweep — apply
only when already editing the function. ~178 such blocks exist (104 in
`server.py`, 24 in `mc/agent_runtime.py`); mass rewrite is out of scope.

**Resumability anchors** (2026-05-27):
- `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` — CURRENT authoritative spec
- `docs/SKILLS_CURATION_PHASE4_SPEC.md` — v1.1, reference-only
- `docs/SKILLS_CURATION_DESIGN.md` — parent design + Conditions 1–11
- `<project memory dir>/decision_learning_definition.md` — locked def
- `docs/_committee/SKILLS_CURATION_PHASE4_seat<N>_*.md` — v1.1 committee assessments

## Browser pane — name a profile when you log in (2026-08-02)

`POST /api/browser/launch` gets a **throwaway** Chromium profile by default —
every launch starts logged out. Fine for one-off browsing, wrong for any
site you sign into (a weekly steward run would face a fresh login/2FA/
anti-bot challenge every time). **If signing in, pass a profile name:**

```bash
curl -s -X POST localhost:5199/api/browser/launch -H 'Content-Type: application/json' \
  -d '{"project_id":"mission_control","url":"https://reddit.com","profile":"reddit"}'
```

Profile lives at `~/.clayrune/browser_profiles_named/<name>`, keeps cookies
across sessions/restarts — log in once (vault creds, `{{user:}}` +
`{{secret:}}`) and reuse after that. `GET /api/browser/profiles` lists what
exists; **check before logging in**, the account may already be signed in.

Set **`browser_default_profile`** in `config.json` (e.g. `"main"`) to make
*unnamed* launches persistent too; `{"ephemeral": true}` opts a single launch
back out.

- Naming a profile that's already open **adopts that session** (`reused:
  true`) instead of starting a second browser — two Chromiums on one profile
  dir corrupt it.
- Closing the pane no longer signs you out. Only `DELETE
  /api/browser/profiles/<name>` does — treat as destructive, ask first.
- **A profile is saved by CLOSING Chromium, never killing it** (fixed
  2026-08-05). Chromium writes cookies/localStorage on clean shutdown or a
  lazy ~30s timer; the old `proc.kill()` teardown discarded what the profile
  had just learned. Measured: kill immediately → login lost; kill after 45s
  → login kept; `Browser.close` → always kept (~0.1s). Teardown now closes
  named profiles gracefully, hard-kills only as fallback — any new teardown
  path must match or it silently signs the user out.
- **`sweep_orphan_profiles()` may only run in the server process**
  (`browser_routes.SWEEP_ENABLED`, set in `server.py`) — it treats any dir in
  the throwaway root that `browser_sessions` doesn't know as an orphan, and
  only the server's registry knows what's live. Importing this module in a
  test/debug script used to delete running panes' profiles out from under
  them.
- A saved profile is a live credential (session cookies) — don't create one
  for a site the task didn't ask you to log into.

**`/api/browser/read` (2026-09-10) reads the pane's visible page text — no
longer read-only.** Designed around a real attack, not a keyword filter:
theregister.com 2026-08-28 showed Claude Code compromised not by hidden page
text but by a TOOL-DOWNGRADE CHAIN — a confusing HTTP 415 led the agent to
`curl`, which followed a redirect into a malicious archive. So every failure
mode (non-HTML content, timeout, CDP error) returns a structured error whose
`guidance` field says explicitly: do not retry with curl/wget/requests,
report the failure. Success wraps text in a `content` envelope naming the
origin URL and stating it's untrusted third-party data, never an
instruction. Non-HTML documents refused outright. Hidden-but-DOM-present
text (zero-opacity, off-screen, tiny-font, low-contrast, alt/title/aria,
HTML comments) is stripped and counted in `hidden_content`, never silently
passed through. See `mc/blueprints/browser_routes.py`
(`_build_read_envelope`, `_READ_JS_TEMPLATE`), `tests/test_browser_routes.py`.

## Showing the user an image in chat

Output its absolute path **on its own line** — the agent-chat renderer
(`formatAgentText` → `/api/serve-image`) turns it into an inline thumbnail
(click to enlarge). File must resolve under the repo root, `data/uploads/`,
or a registered project path (the `/api/serve-image` allowlist). **Markdown
`![](...)` does NOT render** — MC's web chat isn't the generic "GitHub
markdown in a terminal" case. Detail: memory `reference-show-image-in-chat`.

## Type checking (2026-06-10)

New/moved modules under `mc/` must pass `pyright` basic (scope:
`pyproject.toml` `[tool.pyright]`; CI: `.github/workflows/pyright.yml`,
non-blocking until the 23-error baseline in `mc/distiller.py` /
`mc/agent_runtime.py` is cleared).

## Hosted SaaS lives elsewhere now (2026-07-11)

The hosted-compute product (Fly + Tigris, pricing/tiers, investor material)
split into its own Clayrune project: **`clayrune_cloud`**. Its docs
(`HOSTED_CLOUD_*.md`, `_committee/HOSTED_CLOUD_*` seats, `docs/poc/` pricing
models) moved with it — don't re-create them here.

**The split is by artifact type, not topic.** Any *code* the hosted product
needs (session resume, dormancy hooks, multi-tenancy, auth) still lands in
**this** repo; the cloud project files a backlog item here when it needs one.
The remote-access (Cloudflare tunnel) feature stays here too — shipped local
feature, not the SaaS.

## Memory + self-learning systems — pointers, and one rule

Both subsystems are SHIPPED; design/build history lives in `docs/`, not here:
`docs/MEMORY_SYSTEM.md` (Scribe, read-floor, condense, Step-6 checkpointing)
and `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` (four-artifact learning loop;
backend live since `d2dc8a6`). Locked def of "learning" is in the project
memory dir, `decision_learning_definition.md`. Read those when touching the
code; not needed for unrelated work.

The one part that must be in front of every agent, because breaking it takes
down both restart endpoints:

**LOAD-BEARING RULE — DATA_DIR pollution.** `DATA_DIR` (`data/projects/`) is
the project-records dir; `load_projects()` treats every `*.json` there as a
project. Anything else written into `DATA_DIR` (telemetry, sidecars) **MUST
be suffix-excluded in `load_projects()`** (already excludes `_agent_log.json`
and `_scribe_stats.json`). A stray file there becomes a malformed "project"
and 500s `_get_active_restart_blockers` → both restart endpoints. New
per-session/sidecar state belongs OUTSIDE `DATA_DIR`.

Same rule for any new sidecar: `data/projects/<id>_topics.json`,
`_topic_state.json`, `_skill_stats.json` etc. are all suffix-excluded in
`EXCLUDED_SIDECAR_SUFFIXES`. Add yours there or put it outside `DATA_DIR`.

## Build, release + platform notes — pointers

- **macOS signing/notarization:** the `.app` is signed + notarized + stapled.
  Per release: `pyinstaller installer/build-macos.spec --noconfirm` then
  `tools/notarize-macos.sh`, and **replace** the unsigned zip CI attaches to
  the release or users still hit Gatekeeper. Identity lives in
  `tools/signing.env` (gitignored), never the repo. Playbook + gotchas:
  `docs/MACOS_NOTARIZATION.md`.
- **Video attachments:** this model cannot read video. Run
  `tools/extract-frames.sh <path>` and read the PNGs it writes next to the file.
- **Skills surface:** built-ins live in `data/skills/builtin/<name>/SKILL.md`
  and install on startup with checksum-based update preservation (user edits
  kept). Backend `mc/skills.py`; architecture in CHANGELOG `[2026-05-10]`.
- **Live test VMs:** a clean Windows 11 Home VM and Ubuntu 22.04 VM exist for
  end-to-end install testing. Re-test on a fresh snapshot after any
  `installer/` change.

## Retired — do not reinstate

- **memsearch.** Verified non-functional and retired 2026-05-18, but this
  file went on instructing every agent to use it for months; the 2026-08-06
  night review flagged the contradiction. Use the Scribe/memory system
  instead (`mc-memory-search` skill, or grep the project memory dir).
