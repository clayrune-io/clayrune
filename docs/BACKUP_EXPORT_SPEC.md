# Backup, Export/Import & Restore Points — Spec

Status: **DRAFT v1.2 (2026-09-08)** · Author: spec session 2026-09-08, from
Ron's ask: *"backs up all data … import / export … at either individual
project level or master CR level … recover everything (maybe also recovery
points per project like windows recovery points?) and also migrate to other
systems."*

> **v1.1 changelog — Ron's ruling on scope selection (same day).** (1) The
> vault is a **user choice presented at export time, unset until answered**
> — not a default this spec picks (§4.2). (2) The fixed tier ladder becomes
> a **per-export category checklist** with live sizes, including a new
> **agent artifacts** category (§4.6); the manifest records the chosen
> categories and import announces what an archive does NOT contain (§4.5);
> restore semantics for an absent category are stated (§4.7).

> **v1.2 changelog — Ron reverses the category default (same day):**
> *"Everything should be backed up unless user explicitly selects
> otherwise."* The checklist becomes **opt-OUT**: every category ships ON;
> the user unticks. A backup nobody configured must be complete. The
> default archive on this install is therefore **≈4.3 GB** — or **≈50 GB**
> counting non-git project directories (§4.8) — and the spec states that
> plainly rather than hiding transcripts behind opt-in because the number
> is uncomfortable. Untracked checkout files (v1.1 Open Q1) resolve to
> **INCLUDED**, with the bounding rule in §4.8. Two things v1.2 does NOT
> change: the vault stays the explicit unset question of §4.2 (ruled
> separately; "everything" does not silently reverse it), and the
> never-travels list stands — browser profiles, jobsearch profiles, logs
> are live session state / machine detritus, not the user's data.

Companion precedents this spec builds on rather than reinventing:
`tools/memory-snapshot.py` (snapshot/restore discipline for the memory
corpus), `tools/backlog-key-backfill.py:117-128` (file-by-file safety copy
into `data/projects_backup_<stamp>/`), and the DATA_DIR pollution rule
(CLAUDE.md; `mc/blueprints/project_routes.py:145-163`).

---

## 0. Three jobs, kept distinct

The ask is three features that share a file format but have different
requirements. Conflating them is how backup features end up doing none of
the jobs well.

| Job | Question it answers | Same machine? | Granularity |
|---|---|---|---|
| **Backup / restore** | "Get me back to yesterday" | Yes | Whole install |
| **Export / import** | "Move this to another machine" | No | Project or whole install |
| **Restore points** | "Undo what the last session did to this project" | Yes | One project |

- Backup optimizes for *completeness* — restore onto the same paths, same
  user, same keyring. Path remapping is a non-problem.
- Export optimizes for *portability* — everything machine-specific must be
  remapped or excluded, and secrets need an explicit story.
- Restore points optimize for *speed and honesty* — small, named, instant,
  and brutally clear about what a rollback does NOT undo.

All three produce/consume the same archive format (§4.5) so the code is one
subsystem, not three.

---

## 1. State inventory — measured 2026-09-08 on this install

Every size below is a real `du` measurement, not an estimate. Classes:
**P** = portable (safe to move between machines), **O** = operator-specific
(correct only for this operator, but *their* new machine wants it),
**X** = machine-specific (wrong or dangerous on any other machine),
**BIG** = size disqualifies it from a default backup.

### Clayrune-owned state (under the repo's `data/`, all gitignored)

| Path | What | Size | Class |
|---|---|---|---|
| `data/projects/*.json` | Project records: backlog, activity, settings | 12.9 MB | O |
| `data/projects/*_{agent_log,scribe_stats,router_stats,skill_stats,skill_stats_summary,topics,topic_state}.json` + `*_skill_stats_archive.jsonl` | Per-project sidecars (`EXCLUDED_SIDECAR_SUFFIXES`, `mc/blueprints/project_routes.py:151-163`) | ~2 MB (dir total 15 MB) | O |
| `data/config.json` | Global config, ~80 keys | 4 KB | O |
| `data/settings.json`, `data/grid_layout.json` | UI state | <1 KB | O |
| `data/schedules.json` | Scheduler routines | 96 KB | O |
| `data/agent_labels.json`, `data/session_labels.json` | Naming state | small | O |
| `data/notifications.json` | Notification log | 100 KB | O |
| `data/SHARED_RULES.md` | Operator rules, injected into every agent | 12 KB | O |
| `data/hiveminds/` | Hivemind state | 3.9 MB | O |
| `data/skills/` | Built-in + user + `_proposed` skills | 3.1 MB | O |
| `data/mcp/`, `data/mc_builtin_mcps_global.json` | MCP configs | small | O |
| `data/uploads/` | User uploads (attachments) | 242 MB | O, BIG |
| `data/media/` | Media | 6.5 MB | O |
| `data/logs/` | Server logs | 111 MB | X, BIG |
| `data/projects_backup_*/` | One-off safety copies from `tools/backlog-key-backfill.py:119` — NOT a backup feature | 31 MB | X |
| `data/push_subscriptions.json`, `data/push_vapid.json`, `data/local_auth.json` | Push + auth key material | small | X (per-install) |

### Per-project state living OUTSIDE the repo

| Path | What | Size | Class |
|---|---|---|---|
| `<project_path>/AGENT_RULES.md` | Per-project agent rules (`project_routes.py:1777`) | ~KB each | O |
| `<project_path>/.mcp.json` | Per-project MCP config | ~KB | O |
| `<project_path>/` itself | The user's actual git repo | arbitrary | out of scope, §4.1 |

### Claude Code / Codex home-dir state

| Path | What | Size | Class |
|---|---|---|---|
| `~/.claude/projects/<encoded>/memory/` | Memory vaults — MEMORY.md, topic files, `position_*.md`, archive | 75 MB across all 418 encoded dirs; 29 MB for mission-control alone | O |
| `~/.claude/projects/**/*.jsonl` | CC transcripts | **≈3.7 GB** (3.8 GB total minus 75 MB memory) | O, BIG |
| `~/.codex/sessions/` | Codex rollouts | 78 MB | O, BIG-ish |
| `~/.claude/agents/` | Characters (10 files) | 52 KB | O |
| `~/.claude/skills/` | Installed skills | 97 MB — but 96 MB is ONE skill's bundled assets (`video-shotcraft`); text is ~1 MB | O |
| `~/.claude.json` | CC's own global config (MCP servers + CC-internal state) | 70 KB | X-ish — CC-owned, not audited; see Open Q2 |

### `~/.clayrune/` — durable operator state

| Path | What | Size | Class |
|---|---|---|---|
| `secrets.json` | Encrypted vault | 4 KB | **flag-gated**, §4.2 |
| master key | **OS keyring** (Windows Credential Manager / Keychain), file fallback `secrets.key` (`mc/secrets_store.py:13,24-26,204-226`) | — | X — never leaves the machine |
| `secrets_audit.jsonl` | Vault audit log | 8 KB | X (audit is per-install history) |
| `browser_profiles_named/` | Persistent browser profiles = **live session cookies** | 1.7 GB | X, BIG — never exported |
| `jobsearch_profiles/` | Another project's profile data | 3.9 GB | X, BIG |
| `memory-snapshots/` | Existing memory-corpus snapshots (`tools/memory-snapshot.py:48`) | 2.5 MB | X (already are backups) |
| `mcp_installs/` | Installed MCP binaries — reinstallable | 32 MB | X |

### Agent-authored artifacts — where agent output actually lands (measured)

Agent-produced work is scattered across five locations, none of which git
protects (all gitignored or outside any repo):

| Location | What | Measured |
|---|---|---|
| `<project_path>/docs/_journal/` | Unattended-run journals (the CLAUDE.md backlog-notes rule sends all steward/night-review logs here) | 2.26 MB across 6 projects with journals; mission-control alone 1.68 MB / 165 files |
| `data/media/` | Agent-generated media (renders, screenshots, video assets) | 6.5 MB |
| `data/reply_archive/` | Archived agent replies | 120 KB |
| `data/maintenance_reports/` | Maintenance/night-review audit reports | 48 KB |
| `data/projects/<id>/` workspace dirs | Per-project agent workspaces (one exists: `engulfing-analyst`) | 84 KB |

**Total ≈ 9 MB.** This is one selectable category, not several — the
locations are enumerable, individually tiny, and a user has no reason to
want journals without reports. What the category does NOT cover, stated so
nobody assumes otherwise: agent-written docs that are **committed** in a
project's repo (git protects those; §4.1's repo pointer is their story).
Files that **no repo protects** — untracked checkout files and whole
non-git project dirs — are their own category since v1.2; see §4.8.

### Unprotected work files — measured per registered project (2026-09-08)

Untracked-and-not-ignored files (`git ls-files --others
--exclude-standard`) across every registered project with a git checkout:

| Project | Untracked files | Size |
|---|---|---|
| mission_control | 453 | 39.8 MB |
| fl3_v1 | 343 | 19.5 MB |
| daytrading | 151 | 2.4 MB |
| clayrune_website, discord_reader, fl3_v2, market_replay, polymarket, clayrune_cloud | ≤5 each | <1 MB combined |
| **Total (git checkouts)** | ~950 | **≈62 MB — zero files over 10 MB** |

Registered projects whose `project_path` is **not a git repo at all** —
whole-directory measurement, because nothing protects any of it:

| Project (non-git dir) | Whole-dir size |
|---|---|
| day_trading_engulfing_scanner | **44.4 GB** |
| cad3d | 825 MB |
| find_ron_a_job | 112 MB |
| find_teya_a_job_as_pmo | 91 MB |
| apex_trader | 64 MB |
| eden_s_math_learning_project | 58 MB |
| options_trader | 14 MB |
| **Total (non-git dirs)** | **≈45.6 GB** |

The split in these two tables — 62 MB bounded vs. 45.6 GB dominated by
one directory — is why §4.8 treats git-checkout strays and non-git dirs
under one category but with per-directory line items.

### The totals that matter

| Category (opt-out checklist, §4.6) | Measured size (uncompressed) | Default |
|---|---|---|
| **Records + memory** (records + sidecars + config + schedules + hiveminds + rules + `data/skills` + memory vaults + characters + `~/.claude/skills`) | **≈195 MB** (≈100 MB if the one 96 MB skill-asset blob is size-capped) | ON |
| **Agent artifacts** (table above) | **≈9 MB** | ON |
| **Media / uploads** (`data/uploads`) | **≈242 MB** | ON |
| **Transcripts** (CC `.jsonl` 3.7 GB + Codex sessions 78 MB) | **≈3.8 GB** | ON |
| **Unprotected work files** (§4.8 — untracked checkout files ≈62 MB + non-git dirs ≈45.6 GB, per-dir line items) | **≈62 MB – 45.7 GB** | ON |
| **Vault** (re-encrypted, §4.2) | 4 KB | **unset — must answer** |
| Never offered (browser profiles, jobsearch, logs, mcp_installs) | 5.8 GB never travels | — |

**The default archive on this install weighs ≈4.3 GB** (everything ON,
untracked checkout files included, before any untick) — **≈50 GB if all
seven non-git project directories stay ticked**, of which a single
project (`day_trading_engulfing_scanner`, 44.4 GB) is 89%. That is the
honest cost of "everything"; the spec states it rather than quietly
keeping transcripts opt-in because the number is uncomfortable. The
running total in the export UI (§4.6) is what makes unticking an
informed act — the user, not this spec, decides where between 200 MB and
50 GB each export sits.

---

## 2. Prior art — what already exists, and why none of it is this feature

Checked so the spec doesn't reinvent or collide:

- **`/api/project/<id>/import`** (`project_routes.py:1689-1755`) imports a
  *CHANGELOG.md* from `project_path` into the record (done→activity,
  next→backlog). It is not a data importer. **The route name is taken** —
  this feature lives under a `/api/backup/*` namespace and never touches it.
- **`data/projects_backup_<stamp>/`** dirs are pre-migration safety copies
  made by `tools/backlog-key-backfill.py` (`:117-128`). One-shot, records
  only, no restore path. Its file-by-file copy discipline (never
  `copytree` — a stray `nul` file aborts a whole tree copy) carries into
  §6.
- **`tools/memory-snapshot.py`** is the closest real ancestor: labeled
  snapshots of one project's memory corpus into `~/.clayrune/`, sha256
  manifest, verify, and a *deliberately crippled* restore. Three of its
  findings are binding on this design:
  1. Snapshot artifacts live in `~/.clayrune/` — not `data/projects/`
     (DATA_DIR pollution), not `_scratch/` (disposable), not the repo
     (operator data). (`tools/memory-snapshot.py:10-19`)
  2. Copy order is load-bearing for the memory corpus: MEMORY.md → archive
     → topic files, so a concurrent floor-eviction duplicates a line at
     worst instead of losing one. (`:21-25`)
  3. **A whole-dir restore of MEMORY.md is not a rollback** — it silently
     reintroduces watermarks and superseded state the system correctly
     retired. (`:27-35`) This shapes restore points (§4.3).

---

## 3. Non-goals

- **The system does not back up the git-tracked content of the user's
  repos.** §4.1. (Since v1.2 it DOES sweep what git leaves unprotected —
  untracked checkout files and non-git project dirs, §4.8.)
- **The system will not export browser profiles**, ever. A saved profile is
  a live credential (CLAUDE.md, browser-pane section); 1.7 GB of session
  cookies in a portable zip is a credential-theft artifact.
- **No field-level merge on import.** Whole-object replace-or-skip only
  (§4.4). Merging two divergent backlogs line-by-line is a sync engine;
  this is not one.
- **No incremental/differential backups** in any phase here. The records category is
  ~200 MB; zip it whole.
- **No cloud/off-site target.** That is `clayrune_cloud`'s product surface;
  this feature writes local archives the user can put anywhere.
- **No scheduled auto-backup in v1** (Phase 4 candidate, after the manual
  path has soaked).
- **Backups are never committed.** Nothing in this design writes under the
  repo; there is nothing for git to see.

---

## 4. Decisions

### 4.1 A project export is the Clayrune-side record, not the repo

**Decision: export the Clayrune half only.** The archive carries the
project record + sidecars, memory vault, `AGENT_RULES.md`, `.mcp.json`,
that project's schedules/hivemind references — plus a *pointer* to the
repo: `project_path`, git remote URL(s), current branch and HEAD sha
(captured at export time, advisory only).

Why: the repo already has a first-class portability mechanism (its remote;
`git bundle` for the offline case) that preserves history better than any
zip we would write. Duplicating an arbitrary-size working tree into the
archive breaks the size budget (§1), snapshots uncommitted junk, and
creates a second divergent copy of something git is authoritative for. The
failure mode of the chosen answer — user imports on a new machine and
finds an empty `project_path` — is handled honestly: import ends with a
per-project checklist "clone `<remote>` at `<sha>` into `<mapped path>`",
and the UI shows the project as **degraded (repo missing)** until the path
exists, rather than pretending the import was complete.

An `--include-repo-bundle` flag (runs `git bundle create` into the
archive) is a Phase-4 nicety, not v1.

### 4.2 Secrets — Ron's call; the design makes it a flag either way

`~/.clayrune/secrets.json` is 4 KB of AES-encrypted entries whose master
key lives in the **OS keyring** (`mc/secrets_store.py:24-26`). Two
consequences fall out before any policy choice:

1. **Naively copying `secrets.json` can never work** — the destination
   machine does not have the master key, and the key must not travel
   (keyring→file export of key material is exactly the leak class the
   vault exists to prevent).
2. Therefore a secrets-bearing export must **re-encrypt**: dispense each
   entry server-side, encrypt the bundle under a **user-supplied
   passphrase** (scrypt-derived key, AES-GCM), and on import write entries
   through `secrets_store` so they land under the *destination's* own
   master key. No plaintext ever crosses an HTTP response — the archive
   member is passphrase-ciphertext, which keeps faith with vault rule 2
   ("no route returns a plaintext value", CLAUDE.md).

**Ron's ruling (2026-09-08): the user chooses, per export — the spec does
not pick a default.** The vault appears on the export checklist (§4.6) as
a **tri-state that starts unset**: the export does not proceed until the
user answers include or omit. Both failure modes are real and symmetric —
a leaked credential bundle, and a user who assumed their backup was
complete and finds at restore time it wasn't. A silent default fails one
of them quietly; an unavoidable question fails neither. The question is
shown with the trade-off in one line each:

| | Include vault | Omit vault |
|---|---|---|
| Migration | complete — new machine just works | user re-enters credentials once |
| Risk | archive is a credential bundle; passphrase is the only lock; file may sit in Downloads/USB/cloud sync forever | archive is safe to handle carelessly |
| Blast radius if leaked | every stored credential | zero |

When included: passphrase required at export time (the mechanism above,
unchanged). A secrets-bearing archive is marked loudly: `-SECRETS`
filename suffix, `contains_secrets: true` in the manifest, and the import
UI states what is inside before the passphrase prompt. When omitted:
`contains_secrets: false`, and import says so (§4.5) — an archive is
never quietly incomplete. Secret-bearing export is **attended-only**
(refused for steward/scheduled trigger types — same enforcement point as
`allow_unattended`); an unattended backup therefore records the vault
question as `"vault": "not_asked"` in the manifest, not as an answer.

**v1.2 note:** the everything-by-default ruling (§4.6) does **not**
reverse this section. Ron ruled on the vault specifically and separately;
a credential bundle created by a default nobody chose is the one case
where completeness is not the safer error. The vault remains the single
unset item on an otherwise all-ON checklist. If Ron means "everything"
to cover the vault too, that is a new ruling to be taken back to him —
not an inference this spec makes.

Open: whether `allow_unattended: false` entries and per-project-scoped
entries export at all, or only under a second flag. Default proposal:
scope rules travel with the entry; nothing is widened by transit.

### 4.3 Restore points — what a rollback reverses, and what it cannot

A restore point is a named per-project snapshot: record + sidecars +
memory vault + `AGENT_RULES.md`, taken into
`~/.clayrune/restore-points/<project_id>/<stamp>-<label>/` (location per
§2 precedent). Cheap: ~1–30 MB, seconds.

**Rollback reverses, fully:** the project record (backlog, activity,
settings, description), its sidecars, `AGENT_RULES.md`.

**Rollback does NOT and CANNOT reverse — and the confirm dialog must list
these, not bury them:**

- **Anything in the project's repo.** Commits, pushes, edited files,
  deleted files. The snapshot records HEAD sha at snapshot time and the
  rollback report shows "repo was at `abc123`, is now at `def456` — this
  tool does not touch git"; moving the repo is the user's deliberate act.
- **Side effects in the world.** Sent emails, social posts, API calls,
  fired schedules, dispensed secrets, files written outside the project.
- **Cross-project and global state.** Global skills, characters,
  `config.json`, other projects a hivemind touched.
- **The memory corpus, except surgically.** Per §2 finding 3: rolling
  MEMORY.md back reintroduces retired/superseded state. Rollback therefore
  restores **topic files only** by default (the restore
  `tools/memory-snapshot.py` proved sound); MEMORY.md /
  MEMORY_ARCHIVE.md are snapshotted but restored only behind an explicit
  "restore memory index (unsafe — read the diff)" toggle that shows the
  diff first.

A restore point that silently failed to undo a git push would be worse
than none; the design's answer is that it never claims to. The UI copy is
part of the spec: *"Restores Clayrune's record of this project. Does not
touch your code, your git history, or anything an agent already did in the
outside world."*

Retention: keep the last N=10 per project + any the user pins;
auto-snapshot before every rollback (so rollback is itself reversible).

### 4.4 Import onto a machine that already has state

**Default: refuse-and-report.** Import first runs as a dry run and
produces a collision report; nothing is written until the user picks a
resolution **per collision class, whole-object granularity**:

| Collision | Detection | Options |
|---|---|---|
| Same project id | `data/projects/<id>.json` exists | **skip** (default) / **replace** (auto-restore-point of the loser first) / **import-as-copy** (new id `<id>-imported`, memory vault re-keyed) |
| Same character name | `~/.claude/agents/<name>.md` exists | skip / replace (byte-identical ⇒ silent skip) |
| Same skill name | `~/.claude/skills/<name>` or `data/skills/` | skip / replace — and every imported skill passes `mc/skill_import_guard.py` regardless (import may never expand what the agent is allowed to do, `skill_import_guard.py:9`) |
| Same schedule id | id in `data/schedules.json` | skip / replace; **imported schedules always arrive `enabled: false`** — a schedule that starts firing on a machine that didn't knowingly enable it is an incident |
| Global singletons (`config.json`, `SHARED_RULES.md`, grid) | always present | keep-mine (default) / take-theirs; never key-merged in v1 |
| Memory vault dir | encoded target dir exists | follows the project-record decision |

No merge mode. "Replace" of a project always writes an automatic restore
point of the existing one first, so a wrong answer is recoverable.

### 4.5 Format and stability

Single zip, extension **`.crbackup`**, containing relative paths under
role prefixes (`data/`, `memory/<project_id>/`, `home/agents/`,
`home/skills/`, `secrets/`), plus a root **`manifest.json`**:

```json
{
  "format": 1,
  "created_at": "2026-09-08T12:00:00Z",
  "clayrune_version": "<git describe / release tag>",
  "kind": "full | project | restore_point",
  "categories": {"records": true, "artifacts": true, "media": true,
                 "transcripts": true, "unprotected": true, "vault": false},
  "contains_secrets": false,
  "projects": [{"id": "...", "project_path": "...",
                "git_remote": "...", "git_head": "..."}],
  "files": {"<relpath>": {"sha256": "...", "bytes": 123}}
}
```

Stability rules — the part that makes it a backup rather than a file:

- **`format` is a major version. Format 1 is readable forever.** Any
  future change that a format-1 reader would misread bumps to 2; additive
  keys do not.
- Reader semantics: `format` **greater** than the importer supports ⇒
  refuse with *"this backup was made by a newer Clayrune — update first"*
  (never a partial import). `format` ≤ supported ⇒ import; unknown
  manifest keys are ignored; files present in the zip but absent from
  `files` are **not restored** (the manifest is the allowlist — a tampered
  or half-written zip fails closed).
- Every restore verifies sha256 before writing anything; one bad hash
  aborts the whole restore pre-write, not mid-write.
- Round-trip test in CI: create → import into a temp tree → byte-compare.
  A format change that breaks the round-trip cannot merge.
- **`categories` is load-bearing, format-1, day one.** Every archive
  records exactly which categories it contains, and every import/restore
  **announces what is absent before it runs**: *"This archive has no
  media and no transcripts. The vault question was answered: omit."* A
  partial backup that doesn't announce what it is missing is the same
  silent-truncation failure class as the backlog-note caps — the user
  discovers the hole at the worst possible moment, restore time.

### 4.6 The checklist is opt-OUT: everything ships ON (Ron, 2026-09-08, v1.2)

Ron's ruling, verbatim: *"Everything should be backed up unless user
explicitly selects otherwise."* **A backup nobody configured must be
complete.** Every category starts checked; the user unticks what they
don't want. The failure mode this ruling picks: a too-big archive the
user trims, over a too-small archive the user discovers at restore time.

What keeps a 4–50 GB default from being a trap is *visibility*, not a
quieter default: each category (and each per-directory line in §4.8)
shows its size **measured live before the export starts**, and the UI
shows a **running total** that updates as items are ticked and unticked —
unticking is an informed act or it is nothing.
`GET /api/backup/size-preview` walks the candidate paths and returns
per-category (and per-directory) bytes; the UI and CLI `--preview`
render the checklist from it. Nothing is bundled invisibly, and nothing
is omitted invisibly.

**Per-directory rows are per-category, not just §4.8** (added 2026-09-09,
after the shipped panel gave a breakdown for `unprotected` alone and a bare
total for the other four). Every category returns `directories: [{path,
bytes, files}]`, bucketed by the real directory each file was enumerated from
and sorted largest-first; `unprotected` keeps its extra `kind`/`project_id`
fields. The panel renders all five with the SAME disclosure, **collapsed by
default** — auto-expanding `unprotected` alone was dozens of rows that pushed
the Create button off-screen on a real install.

| Category | Contents | Measured (this install) | Default |
|---|---|---|---|
| **Records + memory** | project records + sidecars, config, schedules, hiveminds, rules, `data/skills`, memory vaults, characters, `~/.claude/skills` | ≈195 MB | ON |
| **Agent artifacts** | `docs/_journal/` across all registered projects, `data/media/`, `data/reply_archive/`, `data/maintenance_reports/`, `data/projects/<id>/` workspaces (§1 table) | ≈9 MB | ON |
| **Media / uploads** | `data/uploads/` | ≈242 MB | ON |
| **Transcripts** | CC `.jsonl` + `~/.codex/sessions/` — history rather than operating state (agent-log backfill reconstructs run history from `*_agent_log.json`), and the most privacy-dense artifact on the machine. Still ON: "everything" that is the user's data is the ruling, and 3.8 GB is its measured, stated cost | ≈3.8 GB | ON |
| **Unprotected work files** | untracked checkout files + non-git project dirs, per-directory line items — §4.8 | ≈62 MB + up to 45.6 GB | ON |
| **Vault** | re-encrypted secrets (§4.2) | 4 KB | **unset — export refuses to proceed until answered** (v1.2 does not reverse this, §4.2) |

Unticking every category is refused (an empty archive is not a backup).
The archive filename encodes the shape — full default:
`clayrune-2026-09-08-full.crbackup`; anything unticked:
`clayrune-2026-09-08-full-minus-transcripts.crbackup` — so a folder of
backups is legible without opening manifests, and a trimmed one is
visibly trimmed.

### 4.7 Restore semantics when a category is absent

**Restore is per-category and additive; an absent category is left
untouched, never deleted to match the archive.** Restoring a
records-only archive onto an install with media does not delete the
media; restoring onto a fresh machine simply leaves those surfaces
empty. The alternative — mirror semantics, delete-to-match — turns
"restore my records" into "and silently destroy 242 MB of uploads the
archive never contained", which is the wrong default for a recovery
tool and is **not offered** in any phase of this spec.

The user is told which semantics they got, twice: the pre-restore
announcement (§4.5) lists absent categories, and the post-restore report
states per category `restored` / `not in archive — existing files
untouched`. A project restored without its media renders normally but
shows **degraded (media not in backup)** on surfaces that reference
missing uploads, same pattern as the missing-repo state in §4.1.

### 4.8 Unprotected work files — the bounding rule (v1.2)

The class Ron's everything-rule protects is **files that are real work
and in no repo**. §4.1's exclusion was never "the checkout is not our
problem" — it was "**git-protected files are git's job**". Files git
does not protect fall to us. Two sub-classes, one category, measured in
§1:

**(a) Untracked files in a git checkout** — `git ls-files --others
--exclude-standard`, i.e. untracked AND not ignored. This is bounded in
practice: **≈62 MB across all registered checkouts, zero files over
10 MB** (mission_control 39.8 MB/453 files is the largest). The rule:

- **Respect `.gitignore`.** An ignored file is one the operator already
  classified as not-work (`_scratch/`, backups, build output,
  `node_modules`). The known ignored-but-real-work cases — journals,
  reports — are already named locations in the agent-artifacts category,
  which is exactly where an exception to this rule must go: **by name,
  never by loosening the sweep**.
- **Per-file ceiling 10 MB.** A stray dataset or video does not silently
  balloon the default. Over-ceiling files are **listed by path and size
  in `manifest.warnings` and in the export report** — named, not
  silently dropped (today's count on this install: zero).

**(b) Registered projects whose `project_path` is not a git repo** —
nothing protects any byte of these, so the whole directory is in scope,
and this is where "unbounded" is measured fact, not caution:
**45.6 GB across seven dirs, 44.4 GB of it one project**
(`day_trading_engulfing_scanner`). The narrowest honest rule under
everything-by-default:

- **Each non-git dir is its own checklist line, with its measured size,
  default ON.** Per-directory granularity is what stops one 44 GB
  directory from forcing an all-or-nothing choice on the other six
  (325 MB combined). No size-based auto-exclusion — the ruling says the
  user unticks, not the spec — but any single line over **1 GB** is
  visually flagged in the preview so the number cannot be scrolled past.
- **Junk-dir name exclusions apply inside non-git dirs** (`node_modules`,
  `.venv`/`venv`, `__pycache__`, `.cache`, `dist`, `build`) — these are
  regenerable tool output by construction, the same class `.gitignore`
  filters in sub-class (a), and each exclusion that fires is listed in
  the export report with the bytes it skipped.
- The per-file 10 MB ceiling does **not** apply inside non-git dirs — a
  45 GB data directory is mostly large files, and a ceiling would gut
  the very content the line item exists to carry. The line-item size is
  the honest signal instead.

Restore of this category writes files back to their original absolute
paths (same-machine) or through the §8 path remap (migration), additive
per §4.7 — an existing file with different content is **not
overwritten**; it is listed in the restore report as `conflict — kept
existing, archived copy at <path>.crbak-restored`. Unprotected files
have no version history by definition; a restore that overwrites them
would be the only destructive write in this design, so it is not one.

---

## 5. Where things live, and DATA_DIR discipline

- Archives: **`~/.clayrune/backups/`** (durable operator state, §2).
  Uploaded import archives stage in `~/.clayrune/backups/_incoming/`.
- Restore points: **`~/.clayrune/restore-points/<project_id>/…`**.
- **Nothing new is written under `data/projects/`** — no manifest, no
  sidecar, no marker. Restore *writes* project records there and must
  write only `<id>.json` + suffixes already in `EXCLUDED_SIDECAR_SUFFIXES`
  (`project_routes.py:151-163`); the restore path validates every filename
  it is about to place in DATA_DIR against that rule and refuses strays
  (this is also the tamper defense for hand-edited archives).
- The repo never sees a backup; there is no `.gitignore` change to make
  because no artifact lands under the repo.

## 6. Mechanism notes (binding on implementation)

- **File-by-file copy, never `copytree`** (§2, the `nul` incident). One
  unreadable stray must skip-and-log, not abort the backup.
- **Consistency without stopping the server:** each JSON is read, parsed,
  and re-serialized into the archive; a parse failure retries once then
  records the file in `manifest.warnings` — a backup with a named gap
  beats a silent one. The memory corpus follows memory-snapshot.py copy
  order.
- **Restore is staged:** unpack + verify hashes into a temp dir, then move
  files into place per-file; an auto-restore-point (or full backup, for
  whole-install restore) is taken first. Whole-install restore requires
  the server-restart approval flow — it is the one operation here that
  needs a restart, and restarts require Ron's explicit go-ahead.
- **Attended-only surface.** Backup *creation* may run unattended
  (reversible, additive). Restore, import, and rollback are refused for
  unattended trigger types (steward/scheduled) — same gate style as
  `with-secret --unattended` detection. Machinery must not roll back its
  own history.
- **New endpoints, all under one namespace** (avoids the taken
  `/api/project/<id>/import`): `GET /api/backup/size-preview`,
  `POST /api/backup/create` — **no `categories` object means the full
  default: everything ON, vault `not_asked`** (§4.6, v1.2); an explicit
  object opts OUT per category and is echoed into the manifest; **`async:
  true` returns `{job_id}` immediately (202)** and writes on a worker
  thread, with `GET /api/backup/create/status/<job_id>` reporting
  bytes/files written against their totals, the current member and the
  warning count (a ~48 GB default archive is minutes of writing, and the
  synchronous default stays byte-for-byte what the CLI and tests use) —
  `GET /api/backup/list`, `POST /api/backup/restore`,
  `POST /api/backup/export-project/<id>`, `POST /api/backup/import`
  (dry-run by default, `apply: true` to commit),
  `POST/GET /api/backup/restore-point/<project_id>`,
  `POST /api/backup/rollback/<project_id>/<snap>`.
- Logic lives in `mc/backup.py`, importable without `server.py` (same
  isolation rule as `distiller.py`); routes in
  `mc/blueprints/backup_routes.py`; `tools/clayrune-backup.py` CLI drives
  the same module so recovery works when the server won't start — **the
  CLI restore path is the whole point of a backup and must not require a
  running server.**

## 7. Build order — smallest shippable first

**Phase 1 — full-install backup + restore, complete by default.**
`mc/backup.py` + CLI + `size-preview/create/list/restore` endpoints.
Same-machine semantics (no path remap). Manifest format 1 **including the
`categories` field** — it cannot be retrofitted without a format bump, so
it ships first. All categories ship in Phase 1 — records, artifacts,
media, transcripts, **and the §4.8 unprotected-files sweep** — because
under v1.2 the default backup must be complete, and a Phase 1 that
silently lacked a default-ON category would violate the ruling it ships
under. `create` with no `categories` object produces the full default;
the CLI prints the size preview (with per-directory §4.8 lines and the
running total) before writing. The vault is not yet includable — Phase 1
records `"vault": "not_available"` and the CLI says so out loud rather
than silently omitting. Restore is per-category additive (§4.7/§4.8)
with the absent-category announcement. Ships alone: "Clayrune can save
and restore itself, and every archive says what it is."

**Phase 2 — per-project export/import + collision handling + vault.**
`export-project`, `import` with dry-run collision report (§4.4), path
remap + memory-vault re-encoding (§8), repo-pointer checklist (§4.1).
The **vault category lands here** (passphrase re-encrypt machinery,
§4.2) — migration is its use case, and once it exists the export flow
asks the unset-until-answered question everywhere. This is the migration
story.

**Phase 3 — restore points + the checklist UI.**
Snapshot/rollback endpoints + UI (list, label, pin, the cannot-reverse
dialog of §4.3), and the export checklist surface: live per-category
sizes from `size-preview`, the tri-state vault question, filename
shaping. Built on Phase 1/2 primitives; cheap because the archive code
already exists.

**Phase 4 — deferred conveniences, each gated on demand:**
scheduled auto-backup + retention (unattended runs record
`"vault": "not_asked"`, §4.2), `--include-repo-bundle`.

## 8. The hardest problem: project identity is an absolute path

Named here so no phase pretends it away. A project's memory vault
directory *is* its encoded absolute `project_path`
(`mc/memory.py:116,264-273`); transcripts key the same way; the record
stores the raw path; worktrees encode differently again
(`mc/memory.py:228`). On import to a machine where the path differs
(a different username is enough), every one of these must be remapped
*consistently*: rewrite `project_path` (interactive mapping step in the
import dry-run), re-encode and move the vault dir, and accept that
transcripts (if included) reference dead paths internally. A wrong or
missed remap does not error — it **silently orphans the project's entire
memory**, which is the exact failure class (state present but invisible)
this codebase keeps paying for. Phase 2's acceptance test is therefore:
export on path A, import onto path B, and the imported project's next
agent session *loads its memory index* — asserted, not assumed.

## 9. Open questions

~~1. Secrets flag default~~ — **ruled by Ron 2026-09-08:** user choice at
export time, tri-state unset until answered, no spec-picked default
(§4.2). v1.2's everything-default explicitly does not reverse this.

~~2. Untracked checkout files~~ — **resolved by v1.2's
everything-default: INCLUDED**, with the §4.8 bounding rule
(respect-.gitignore + 10 MB per-file ceiling for checkouts; per-dir
line items + junk-dir name exclusions for non-git dirs). Measured:
≈62 MB (checkouts) + ≈45.6 GB (non-git dirs, one project = 44.4 GB).

1. **Ron:** does `data/notifications.json` (timeline) belong in records,
   or is history-on-a-new-machine noise? (100 KB either way; records for
   now.)
2. `~/.claude.json` contents were not audited this session. If per-user
   MCP servers live only there (not in `.mcp.json` / `data/mcp/`), Phase 2
   needs a scoped extract of its `mcpServers` key rather than the file.
3. Whether hivemind archives (`data/hiveminds/_archived`) travel, or only
   live hiveminds.
4. Restore-point auto-triggers (before steward cycles? before schedule
   runs?) — deferred until Phase 3 usage shows where rollbacks actually
   point.
