# Clayrune — Claude Code project notes

**This file is auto-loaded into EVERY session for this project.** Its size is a
per-session context tax on every agent, forever — so it holds only what an agent
must know *before* it starts, not the history of how things got this way.

The bar for a section here: **an agent that reads the code carefully still could
not derive it.** Binding constraints, invariants whose violation is silent, and
incidents that already happened. Design narrative, build orders and shipped-work
status belong in `docs/` — see `docs/CLAUDE_MD_ARCHIVE.md` for what was moved
out on 2026-08-06 and why.

## Commit discipline — stay scoped to your own session (added 2026-06-08)

When asked to commit "the work we did," stage **only the files you edited this
session, by explicit path** (`git add <path> …`):

- **Never** `git add -A`, `git add .`, or `git commit -a`. Name the paths.
- **Don't sweep, don't narrate.** The working tree always carries unrelated
  dirty files — other MC-managed projects' data under `data/projects/`,
  backups, `_scratch/`, mobile/store assets. Don't stage them; don't list them
  back. A commit report names only what you committed. Enumerate other dirty
  files **only** if the user asks "what else is uncommitted?"
- Scratch/throwaway artifacts go in **`_scratch/`** (gitignored), not in
  `tools/`, `docs/`, or `data/`.
- Pairs with the standing rule to commit your own completed work without asking
  — but *only your own*.

`.gitignore` enforces the structural half: backups (`*.bak`/`*.broken`),
runtime (`data/mc_child_pids.json`, `data/skills/_proposed/`), `_scratch/`,
`tools/_*` scratch, served build artifacts, and mobile-app assets are all
untracked so they never reach a commit candidate.

## BINDING — `master` is the release channel: keep it pushed (added 2026-07-12)

`/api/system/update` is `git pull --ff-only` on the user's **current branch** —
for everyone who isn't us, that's `master`. So **an unpushed `master` means every
other user is frozen on old code**, silently. There is no other release channel.

On 2026-07-12 `origin/master` was found **138 commits behind local `master`** and
the working branch was 33 commits beyond that. Months of shipped work — the
conversation redesign, Inbox, mobile fixes, safety rails — had reached nobody.
Nothing warned us; "it works on this machine" is exactly what that failure looks
like.

**The rule:** when a feature branch is done and green, land it on `master` and
**push `master`** in the same breath. Do not let the local `master` accumulate
commits that `origin/master` doesn't have. Merging locally is not shipping;
pushing is.

**Check it costs one command** — run it at the end of any session that landed
work, and whenever the user asks whether something "is out there":

```bash
git rev-list --left-right --count origin/master...master   # want: 0  0
```

Left number > 0 = we're behind the remote. **Right number > 0 = users are behind
us, and won't know it.** That second case is the dangerous one.

## BINDING — nothing operator-specific goes in the repo (added 2026-07-12)

This repo is public and its files are consumed by other people's machines *and
other people's agents*. Anything true only of **this** install is user data, not
source. Before committing, ask: "would this be wrong on a stranger's machine?"

The three that actually bit us (all fixed 2026-07-12, commit `3a1fd04`):

- **Rules files are the sharpest edge.** `data/SHARED_RULES.md` is read verbatim
  into the system prompt of **every agent on every project**, and a project's
  `AGENT_RULES.md` does the same for that project. Committing them injected one
  operator's personal working preferences — and their email — into every other
  install's agents. Both are now gitignored. A fresh install starting with no
  rules is the **correct** default; the Rules editor writes them.
- **Gitignoring is not enough if a build bundles the file.** `build-macos.spec`
  packaged `data/SHARED_RULES.md` *"if present"* — and it is still present on
  the builder's disk after being ignored, so it got baked into the shipped
  `.app` anyway. When you untrack something, **also check the build specs and
  installer for it.**
- **Personal identity/paths belong in the environment.** Signing identity →
  `tools/signing.env` (gitignored). Machine paths → derived, or `MC_DIR` /
  `JAVA_HOME` / `ANDROID_HOME` / `CLAYRUNE_MOBILE_REPO`. Recipients → config,
  never a hardcoded default. Private ops tooling that audits *our* accounts
  (`tools/gcp-cost-review/`, its billing-account ID) stays untracked.

Legitimately public and intentionally kept: the `LICENSE` copyright line, and
the Play Store `PRIVACY_POLICY.md` / `LISTING_COPY.md` contact address.

## BINDING — credentials go in the vault, never in a command line (2026-08-01)

`mc/secrets_store.py` is the secrets vault. Full detail: `docs/SECRETS.md`.

**If a task needs a password, token, or API key, reference it by name and let
the server resolve it.** Never type a credential into a command, a config file
under the repo, or a message — anything you type lands in the transcript, and
from there in `MEMORY.md` and possibly a distilled artifact, permanently.

```bash
python tools/with-secret.py --env GH_TOKEN=github.token -- gh api ...
```

Placeholders (`{{secret:name}}`) resolve server-side; `tools/with-secret.py`
injects into the child's environment and scrubs dispensed values back out of
its output.

**A login is one entry, not two.** The username lives on the same secret as the
password — `{{user:name}}` / `--user VAR=name` for the username,
`{{secret:name}}` / `--env VAR=name` for the password, `{{totp:name}}` /
`--totp VAR=name` for a 2FA code. `curl -s localhost:5199/api/secrets` lists
what exists (metadata only — names, usernames, scope; never a value):

```bash
python tools/with-secret.py --user U=reddit --env P=reddit -- python post.py
```

If `{{user:name}}` errors with "no username stored", the entry is half-filled —
say so and ask for it. Do **not** substitute a guessed username: an anonymous
or wrong login attempt is worse than a refused command.

Three rules that hold this together — **don't weaken them without a review:**

1. **Nothing under the repo ever holds a secret.** Store, master key, and audit
   log live in `~/.clayrune/`. Gitignoring is not sufficient on its own — see
   the `SHARED_RULES.md`-in-`build-macos.spec` case above.
2. **No route returns a plaintext value.** The management API is metadata-only.
   If you add an endpoint, keep it that way.
3. **Agents use credentials; only humans create them.** There is no agent-facing
   write path, for the same reason the learning system has an authority guard:
   machinery must never expand the agent's own capability set.

Per-secret `scope` (global / one project) and `allow_unattended` (blocks steward
and scheduled cycles) are the policy backstop. By Ron's decision agents may use
secrets unattended by default; the per-task gate lives in the agent rules.

## Learning-system safety rails — LOAD-BEARING (added 2026-07-11)

Steward mode put an autonomous agent on the consuming end of the same artifact
stream the Distiller produces. Three rules now hold the loop together
(`mc/distiller.py`, tests in `tests/test_distiller_safety.py`). **Do not weaken any
of them without a committee review** — each pins a violation that actually
happened on this machine, not a hypothetical.

1. **The authority guard is the constitutional bright line.** Learning may
   change *how* the agent works; it must NEVER change *what the agent is allowed
   to do*. `_authority_violation()` refuses any artifact granting autonomy,
   removing an approval gate, or expanding the agent's own capability set —
   refused in `_generate_and_write_artifact` **before** it reaches the human
   queue, because the queue is where rubber-stamping happens (80 promoted vs 2
   rejected as of 2026-07-11). Deterministic, fails closed. A human can still
   type such a rule by hand into CLAUDE.md; the *learning system* cannot author
   it. **Why:** one sentence in one session ("Full autonomy, no permission/
   go-ahead needed, by any means necessary") became a global always-loaded
   PREFERENCE skill telling every agent in every project to stop asking
   permission. Six such artifacts had accumulated; quarantined to
   `~/.claude/skills_quarantine_2026-07-11/`.
2. **A human must be on at least one side of every learning loop**
   (`_UNATTENDED_LOOP_RULE`). Artifacts carry `origin: interactive|unattended`
   (conservative OR over evidence signals — one steward witness taints the
   candidate). `exploration_read_floor(consumer_unattended=True)` withholds
   unattended-origin artifacts from steward cycles, so autonomous output can
   never become autonomous input. Unstamped (pre-2026-07-11) artifacts fail
   closed. `distiller.STEWARD_TASK_MARKER` is pinned to `fence.STEWARD_MARKER`
   by test — a rename must not silently un-gate this.
3. **"No" must be durable.** `_suppress_artifact` used to record nothing for a
   cross-project artifact (no owning project stats file), so the Distiller
   re-proposed it — `preference-1ba8d678` was live in `~/.claude/skills/` while
   sitting in `_rejected/`. Global rejections now persist to
   `_GLOBAL_SUPPRESSION_PID` (`data/projects/_global_skill_stats.json`, covered
   by `EXCLUDED_SIDECAR_SUFFIXES`) and bind every project via `_is_suppressed`.

**Still true / still watch:** `distiller_mode: auto` is a comment, not code —
promotion is human-only, and the steward's PreToolUse fence blocks all writes
under `.claude/`, so it cannot self-install a skill. Keep both properties.

## BINDING — unattended agents never write backlog notes (added 2026-08-15)

A backlog item holds the **ask** and its **current state**. It is a work list, and
it stops being one the moment it fills with a log. Stewards, night-review, and any
scheduled or autonomous cycle must write their running log to a **journal file** —
`docs/_journal/<item-id>-<slug>.md`, gitignored, no cap — and never to
`POST …/backlog/<id>/note`. Changing what the item *says* (`PATCH text`/`status`)
is a state change, always allowed. An attended session may add a note, sparingly.

**Why it's a rule, not a preference:** `_append_note_to_backlog_item` truncates at
`text[:2000]` and keeps `notes[-50:]` — both of which *destroyed data in total
silence*. Measured 2026-08-15: mission_control's 28 live items carried 16 KB of
task text against 132 KB of notes; the steward charter sat at exactly 50 notes
with 24 cut mid-sentence, its oldest survivor three weeks newer than the charter
itself, and `data/projects/<id>.json` is untracked so those findings are gone.
Dozens of *done* items had also silently hit the same 50-note ceiling. The steward
had even noticed the truncation and started splitting findings into "(cont.)"
notes — spending extra slots out of the same 50, accelerating its own data loss.

Both caps now `_log` when they bite. `tools/backlog-journal-export.py` extracts
notes to journals (re-runnable); `tools/backlog-journal-migrate.py` strips
unattended notes off live items. `PATCH` accepts `notes` as the only removal path.

## Exception-swallowing policy (added 2026-06-09)

When touching any function containing `except Exception: pass`, decide: if the
try-body is pure best-effort cosmetics (cleanup of a temp file, optional Pillow
shrink), leave it. If it wraps **subprocess, file I/O on state files, JSON state
load/save, or network**, convert to:

```python
except Exception as e:
    _log(f"[<subsystem>] <operation> failed: {e}", flush=True)
```

(keep swallowing — just make it observable.) Do **not** do a bulk sweep — apply
only when already editing the function. There are ~178 such blocks (104 in
`server.py`, 24 in `mc/agent_runtime.py`); a mass rewrite is out of scope.

**Resumability anchors** (updated 2026-05-27):
- `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` — CURRENT authoritative spec
- `docs/SKILLS_CURATION_PHASE4_SPEC.md` — v1.1, reference-only
- `docs/SKILLS_CURATION_DESIGN.md` — parent design + Conditions 1–11
- `<project memory dir>/decision_learning_definition.md` — locked def
- `docs/_committee/SKILLS_CURATION_PHASE4_seat<N>_*.md` — v1.1 committee assessments

## Browser pane — name a profile when you log in (2026-08-02)

`POST /api/browser/launch` gets a **throwaway** Chromium profile by default:
every launch starts logged out. That is right for one-off browsing and wrong for
any site you sign into — a weekly steward run would face a fresh login, a 2FA
prompt, and an anti-bot challenge every single time.

**If the task involves signing in, pass a profile name:**

```bash
curl -s -X POST localhost:5199/api/browser/launch -H 'Content-Type: application/json' \
  -d '{"project_id":"mission_control","url":"https://reddit.com","profile":"reddit"}'
```

The profile lives at `~/.clayrune/browser_profiles_named/<name>` and keeps its
cookies across sessions and restarts, so the *next* run is already signed in —
log in once (credentials from the vault, `{{user:}}` + `{{secret:}}`) and reuse
the profile after that. `GET /api/browser/profiles` lists what already exists;
**check it before logging in** — the account may already be signed in.

Set **`browser_default_profile`** in `config.json` (e.g. `"main"`) to make
*unnamed* launches persistent too, so the plain 🌐 Browser button stops starting
logged out. `{"ephemeral": true}` opts a single launch back out.

- Naming a profile that is already open **adopts that session** (`reused: true`)
  rather than starting a second browser. Two Chromiums on one profile dir
  corrupt it.
- Closing the pane no longer signs you out. Only
  `DELETE /api/browser/profiles/<name>` does — that is the sign-out, so treat it
  as destructive and ask first.
- **A profile is saved by CLOSING Chromium, never by killing it** (fixed
  2026-08-05). Chromium holds cookies and localStorage in memory and writes them
  on clean shutdown or a lazy ~30s timer, so the old `proc.kill()` teardown
  discarded whatever the profile had just learned. Measured: kill immediately →
  login lost; kill after 45s → login kept; `Browser.close` → always kept, exits
  in ~0.1s. That middle row is why it read as flaky rather than broken. Teardown
  now closes named profiles gracefully and only hard-kills as a fallback — if
  you add another teardown path, it must do the same or it silently signs the
  user out.
- **`sweep_orphan_profiles()` may only run in the server process**
  (`browser_routes.SWEEP_ENABLED`, set in `server.py`). It calls any dir in the
  throwaway root that `browser_sessions` doesn't know about an orphan, and only
  the server's registry knows what is live — so importing this module in a test
  or debug script and launching a browser used to delete running panes' profiles
  out from under them.
- A saved profile is a live credential (session cookies). Don't create one for a
  site the task didn't ask you to log into.

## Showing the user an image in chat

To display an image to the user, **output its absolute path on its own line** —
the agent-chat renderer (`formatAgentText` → `/api/serve-image`) turns it into an
inline thumbnail (click to enlarge). The file must resolve under the repo root,
`data/uploads/`, or a registered project path (the `/api/serve-image` allowlist).
**Markdown `![](...)` does NOT render** — the generic "your output is GitHub
markdown in a terminal" framing is misleading for MC's web chat. Full detail +
gotchas: memory `reference-show-image-in-chat`.

## Type checking (added 2026-06-10)

New/moved modules under `mc/` must pass `pyright` basic (scope:
`pyproject.toml` `[tool.pyright]`; CI: `.github/workflows/pyright.yml`,
non-blocking until the 23-error baseline in
`mc/distiller.py`/`mc/agent_runtime.py` is cleared).

## Hosted SaaS lives elsewhere now (added 2026-07-11)

The hosted-compute product (Fly + Tigris, pricing/tiers, investor material) was
split into its own Clayrune project: **`clayrune_cloud`**, at
`<clayrune-cloud checkout>`. Its docs
(`HOSTED_CLOUD_*.md`, the `_committee/HOSTED_CLOUD_*` seats, and the `docs/poc/`
pricing models) moved with it — don't re-create them here.

**The split is by artifact type, not topic.** Any *code* the hosted product needs
(session resume, dormancy hooks, multi-tenancy, auth) still lands in **this**
repo; the cloud project files a backlog item here when it needs one. The
remote-access (Cloudflare tunnel) feature stays here too — it's a shipped local
feature, not the SaaS.

## Memory + self-learning systems — pointers, and one rule

Both subsystems are SHIPPED and their design/build history lives in `docs/`, not
here: `docs/MEMORY_SYSTEM.md` (Scribe, read-floor, condense, Step-6
checkpointing) and `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` (the four-artifact
learning loop; backend live since `d2dc8a6`). The locked definition of
"learning" is in the project memory dir, `decision_learning_definition.md`.
Read those when you touch the code; they are not needed to do unrelated work.

The one part that must be in front of every agent, because breaking it takes
down both restart endpoints:

**LOAD-BEARING RULE — DATA_DIR pollution.** `DATA_DIR` (`data/projects/`)
is the project-records dir; `load_projects()` treats every `*.json` there
as a project. Anything else written into `DATA_DIR` (telemetry, sidecars)
**MUST be suffix-excluded in `load_projects()`** (it already excludes
`_agent_log.json` and `_scribe_stats.json`). A stray file there becomes a
malformed "project" and 500s `_get_active_restart_blockers` → both restart
endpoints. New per-session/sidecar state belongs OUTSIDE `DATA_DIR`.

Same rule for any new sidecar: `data/projects/<id>_topics.json`,
`_topic_state.json`, `_skill_stats.json` etc. are all suffix-excluded in
`EXCLUDED_SIDECAR_SUFFIXES`. Add yours there or put it outside `DATA_DIR`.

## Build, release + platform notes — pointers

- **macOS signing/notarization:** the `.app` is signed + notarized + stapled.
  Per release: `pyinstaller installer/build-macos.spec --noconfirm` then
  `tools/notarize-macos.sh`, and **replace** the unsigned zip CI attaches to the
  release or users still hit Gatekeeper. Identity lives in `tools/signing.env`
  (gitignored), never the repo. Playbook + gotchas: `docs/MACOS_NOTARIZATION.md`.
- **Video attachments:** this model cannot read video. Run
  `tools/extract-frames.sh <path>` and read the PNGs it writes next to the file.
- **Skills surface:** built-ins live in `data/skills/builtin/<name>/SKILL.md` and
  install on startup with checksum-based update preservation (user edits are
  kept). Backend `mc/skills.py`; architecture in CHANGELOG `[2026-05-10]`.
- **Live test VMs:** a clean Windows 11 Home VM and Ubuntu 22.04 VM exist for
  end-to-end install testing. Re-test on a fresh snapshot after any
  `installer/` change.

## Retired — do not reinstate

- **memsearch.** Verified non-functional and retired 2026-05-18, but this file
  went on instructing every agent to use it for months; the 2026-08-06 night
  review flagged the contradiction. Use the Scribe/memory system instead
  (`mc-memory-search` skill, or grep the project memory dir).
