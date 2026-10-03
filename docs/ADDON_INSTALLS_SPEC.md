# Add-on installs: agents request, the user approves (MC-1022)

Status: REVISED 2026-10-02 after Fenn's adversarial review (`_scratch/review_addon_spec_2026-10-02.md`).
Rulings carry their source: **DECIDED (Ron)** or **DECIDED (Dave)**. Dave's calls are logged under
"DECIDED WITHOUT RON" in `docs/_journal/6f53f808-addon-installs.md`; Ron may overrule any of them.

**DECIDED (Ron, 2026-10-01):** the user approves each install via a card (what, why, licence, size);
one tap installs; unattended runs wait for the tap. No auto-install, OSS or otherwise.
Standing position: Clayrune never bundles or resells a vendor licence; Remotion is dropped; Desk video
stitching and 1:1 crops use ffmpeg on the Clayrune host (`docs/desk_v1/R1W_WIRING_PLAN.md` §5 item 4).

**Prerequisite (DECIDED, Dave):** `POST /api/agent/provider/<name>/install-launch` and its `update`
branch (`mc/blueprints/agent_routes.py`) get `_require_human_passcode` before any add-on code lands.
Today any agent can call it and install Node plus a vendor CLI with no human. Tracked as its own
backlog item, not part of this build.

## Summary

| # | Topic | Ruling |
|---|---|---|
| 1 | Licence filter | **DECIDED (Dave):** OSI-approved only, copyleft allowed, AGPL flagged on the card. ffmpeg ships as the BtbN **GPL** build. |
| 2 | Install source | **DECIDED (Dave):** static builds pinned to **BtbN month-end tags**; winget/brew/apt shown only as commands for the user. macOS source still open. |
| 2b | Verification | **DECIDED (Dave):** SHA-256 pinned in the catalogue; mismatch is a hard stop, no alternate source. |
| 3 | Catalogue and manifest | **DECIDED (Dave):** catalogue ships as code, `mc/addons/catalogue.json`. Manifest `~/.clayrune/addons/installed.json`; the fence blocks agent writes there. |
| 3b | Invocation | **DECIDED (Dave):** add-ons run by **absolute path** from the manifest. No PATH prepend anywhere. |
| 4 | Human gate | **DECIDED (Dave):** passcode + tap via `_require_human_passcode`; catalogue entries only; never approval by email reply. Tap-only would loosen a human-only gate: Ron's call. |
| 4c | Agent shell side door | **DECIDED (Dave):** guard against agent `winget/choco/scoop/brew/apt/npm -g` installs is a separate follow-up item (MC-1042). **BUILT as a patch to `steward/fence.py`** (`_agent_shell_install`, called from `classify_bash`): armed sessions only; system-level installs refused with a pointer at `POST /api/addons/requests`; project-local installs (`npm install`, `pip install -r/-e .`, a venv, `--target` inside the project) stay allowed. Rule table and known gaps are in the comment above `_INSTALL_ADDON_POINTER`. Attended sessions are not blocked (the fence does not arm for them). |
| 5 | Settings UI | Single "Add-ons" page: pending, installed, available (§5). |
| 6 | ffmpeg already on the system | **DECIDED (Dave), reverses the 2026-10-02 first pick:** never used silently. Adopted through the same card (path, version, SHA-256), then pinned by absolute path + hash; re-carded if the hash changes. |
| 7 | First consumer | Desk stitch + 1:1 crop, `mc/desk_stitch.py` (§7). |

## 1. Licence filter

The obligation that matters in GPL/LGPL is triggered by **distributing** a copy. Clayrune does not
distribute: the user's machine (or their pod) downloads the binary from the upstream publisher on the
user's approval. Running GPL software server-side is not conveying it. AGPL is the exception: its
network clause binds a **modified AGPL program** offered to users over a network.

- Only OSI-approved SPDX ids are accepted. A unit test checks each catalogue entry against the
  allow-list. Non-OSI licences (source-available, custom EULA) are refused, which keeps vendor-licensed
  tools such as Remotion out by rule.
- The card shows the SPDX id plus one plain line, e.g. "GPL-3.0: free to use; obligations apply only
  if you redistribute it". AGPL entries carry an extra line.
- **Why GPL, not LGPL, for ffmpeg (DECIDED, Dave):** BtbN's LGPL variant drops libx264 but keeps
  libopenh264 (`scripts.d/50-openh264.sh` is enabled for every variant). Cisco's patent cover applies
  only to Cisco's own binary module (openh264.org FAQ), not to openh264 compiled into a third-party
  ffmpeg. So LGPL carries the same patent position as GPL, and GPL keeps libx264's quality.
- Open, not blocking local v1: H.264 **patent** licensing for encoding at scale on hosted pods. That is
  a legal read before hosted launch.

## 2. Install sources and verification

Each catalogue entry, per OS/arch: download URL, SHA-256, download size, installed size, the binary
names Clayrune may run (`ffmpeg`, `ffprobe`; never `ffplay`), a version-probe command, SPDX licence,
one-line description, homepage, and `last_verified`.

| OS | Static source for ffmpeg (v1) | Command shown for the user to run |
|---|---|---|
| Windows x64 | BtbN FFmpeg-Builds GPL static zip, month-end tag | `winget install --id BtbN.FFmpeg.GPL.8.0 -e` |
| Linux x64/arm64 (incl. hosted pod) | BtbN linux64/linuxarm64 GPL static tarball, month-end tag | `sudo apt install ffmpeg` |
| macOS arm64/x64 | **open**: BtbN does not build macOS; a static source needs vetting, including Gatekeeper quarantine | `brew install ffmpeg` |

- **Month-end tags only (DECIDED, Dave).** BtbN keeps the last 14 daily builds and the last build of
  each month for two years. A daily pin 404s within two weeks. `tools/addons-pin.py` refuses any tag
  that is not a month's last build.
- **Rot check.** `tools/addons-pin.py --verify` fetches each pinned URL and re-hashes it. It runs on a
  schedule, and its date becomes `last_verified`, which the card shows.
- **Trust on first use.** BtbN publishes no signatures. The first pin is trust-on-first-use, and the
  catalogue entry says so.
- **Fail closed.** A hash mismatch deletes the download and fails the request with "checksum mismatch".
  The installer never tries a mirror or another build: that would be a substitution the card did not show.
- When an OS has no static entry, the card says so and shows the command for the user to run. If the
  user runs it, the result is a system copy, which needs adopting (§3).

## 3. Catalogue, manifest, invocation

- **Catalogue is code (DECIDED, Dave):** `mc/addons/catalogue.json`, shipped in the app (inside the
  frozen bundle on macOS/Windows builds, so `build-macos.spec` datas must list it). Never under `data/`.
  It changes by commit and review like any other source file.
- **Manifest:** `~/.clayrune/addons/installed.json` (id, version, source `catalogue|system`, absolute
  binary paths, SHA-256 of each binary, licence, size on disk, installed/adopted at, approved at,
  requested by). Installed copies go in `~/.clayrune/addons/<id>/<version>/`; in-flight work in
  `~/.clayrune/addons/staging/`. All outside `DATA_DIR`. Override root: `CLAYRUNE_ADDONS_DIR`.
- **Fence (DECIDED, Dave):** `steward/fence.py` adds `~/.clayrune/addons/` and `mc/addons/` to its
  human-owned write list, same posture as `data/skills/`.
- **Absolute-path invocation (DECIDED, Dave):** consumers call `mc.addons.resolve('ffmpeg')`, which
  returns the manifest's absolute path after re-hashing the file. A changed hash marks the entry
  `broken` and raises a new card; nothing runs. No add-on directory is ever prepended to any PATH,
  so a file dropped there cannot shadow `git`, `python` or `claude` in agent CLIs or terminal pop-outs.
- **System copy (DECIDED, Dave):** a ffmpeg found on PATH is offered as an **adoption** card showing
  its absolute path, version and SHA-256, under the same passcode gate. Once adopted it is pinned like
  an install; if the file changes (winget upgrade, replacement), it is re-carded. It has no licence
  check (the user installed it); the card says "installed outside Clayrune, licence not checked".
- **Hosted pod:** `~/.clayrune` must sit on the pod's persistent volume, or `CLAYRUNE_ADDONS_DIR` must
  point at it (clayrune_cloud files that item). At startup an entry whose files are missing is marked
  `missing`; one whose hash fails is marked `broken`.

## 4. The approval card and the human gate

Precedents: vault rule 3 (agents use credentials, only humans create them) and the learning rails
(learning may change how an agent works, never what it may do).

1. `POST /api/addons/requests {addon_id, reason}` is agent-callable, attended or unattended. It
   installs nothing. `project_id`, `session_id` and "requested by" are **derived server-side** from
   the calling session's dispatch record, never taken from the body. `addon_id` must be a catalogue
   entry or `system:ffmpeg` (adoption). An existing pending request for the same `addon_id` is returned.
2. The card renders in the requesting chat and as a blocking Inbox event. Trusted fields come from
   the catalogue or from the server's own hashing (name, what it is, licence, sizes, source host,
   version, path for adoption). The agent's `reason` appears as quoted, length-capped plain text under
   "The agent says": it is untrusted.
3. `POST /api/addons/requests/<id>/approve {passcode}` gates through `_require_human_passcode`, then
   installs or adopts. Concurrent approves on one request: the first wins, the second gets `409`.
4. `…/decline` closes it. **Decline is keyed on `addon_id` alone**, with a 30-day TTL, so an agent
   cannot raise a new card by rewording its feature. The user can still install from Settings.
5. A pending request expires after 14 days with no live job waiting on it, which clears the Inbox event.

Binding:

- **No passcode configured = no approval possible.** `_require_human_passcode` refuses everything
  until the user sets a dashboard passcode. The card says so and links to Settings > Connectivity >
  Network access. This is the default state of a fresh install.
- No config key, no allow-list, and no route that installs without the human gate. The route-inventory
  test (§7) enforces this across the whole app, not just `/api/addons/*`.
- Approval is never by email reply (`mc/question_channel.py` cannot authenticate the sender).
  Unattended runs get an email **notice** with a link; approval happens in the UI.
- An unattended run that hits a missing add-on parks its job (`waiting_on_addon`) and ends its turn.
  Approved and installed: the job resumes. Declined: it fails with the reason.
- The Distiller's authority-guard fixtures gain an install phrasing ("always approve add-on installs").
- Known shared limit: the passcode guess budget is per source IP (10 per 300 s), and agent and human
  are both `127.0.0.1`. An agent spending ten wrong guesses locks the human out for five minutes.
  This applies to every passcode route; this card is the first one an unattended agent waits on.

## 5. Settings UI: Settings > Add-ons

- **Waiting for you:** pending requests, each the same card as in chat.
- **Installed:** name, version, source (Clayrune / system), licence, size on disk, installed date,
  requested by (chat link), last verified, status (`ok` / `missing` / `broken`), **Remove**. Remove
  refuses while a job holds the add-on (Windows file locks) and names the job. Removing an adopted
  system copy only forgets it; Clayrune never deletes files it did not install.
- **Available:** catalogue entries not installed, with **Install** (same passcode gate).

## 6. Failure modes

Install runs in a server worker thread, reporting progress over SSE to the card. Every failure lands
the request in `failed` with a one-line reason and a **Retry**; none is retried automatically.

| Failure | Behaviour |
|---|---|
| No admin rights | Not reachable for static installs (user dir). Commands shown for the user say when they need admin. |
| Offline / DNS / HTTP error | Fail fast with the HTTP status; no mirror. A 404 on a pinned URL also flags the entry for re-pinning. |
| Disk full | Preflight needs free space of 2 × (download + installed size); `ENOSPC` mid-install cleans staging. |
| Partial install | Download to `staging/*.part`, verify hash, extract in staging, set the exec bit (Linux/macOS) on the catalogued binaries, run the version probe, record each binary's SHA-256, atomic rename into `<id>/<version>`, then write the manifest. Startup deletes leftovers in `staging/`. |
| Hostile archive | Tarballs extract with `tarfile` `filter='data'` (no `..`, no absolute paths, no links out); zip via `zipfile`. |
| Checksum mismatch | Delete, fail, log both hashes. |
| Antivirus quarantine (Windows) | Version probe fails: status `broken`, card names the likely cause. |
| Pod restart, volume not mounted | Startup marks entries `missing`; `resolve()` raises `AddonMissing`, which raises a new request. |
| Binary changed after install/adoption | `resolve()` hash check fails: `broken`, new card, nothing runs. |

## 7. First consumer: Desk stitch and 1:1 crop

`mc/desk_stitch.py`:

- `stitch(clips, out)`: if all clips share codec, resolution and frame rate, the concat demuxer with
  `-c copy`; otherwise re-encode to the first clip's parameters (`libx264`, `aac`).
- `crop_square(src, out)`: centre crop to 1:1, re-encode (`libx264`, `aac`).
- Both run ffmpeg through `mc.addons.resolve('ffmpeg')` by absolute path. Missing or broken: raise
  `AddonMissing('ffmpeg')`. The Desk render job files the request with the campaign name in `reason`
  and parks.

Done when:

1. **No install without approval.** With ffmpeg absent, a two-scene Desk render creates exactly one
   request, and afterwards `installed.json` and the `~/.clayrune/addons/` tree are byte-identical
   to before, and no download or installer subprocess was started (asserted by a patched spawner).
2. **Approve installs and resumes.** Approving installs into `~/.clayrune/addons/ffmpeg/<ver>/`, the
   manifest records it with hashes, and the parked job resumes.
3. **The catalogued binary does the work.** The stitch/crop test runs the binary from the catalogue
   install by its absolute path, with the system PATH masked so a system ffmpeg cannot answer.
   Stitching two fixture clips (`tests/fixtures/desk_stitch/`) gives a duration within 0.1 s of their
   sum; the crop is square; `ffprobe` reports video codec `h264` and audio codec `aac`. One CI job runs
   this against a real catalogue install; unit runs may use a cached copy of the same pinned archive.
4. **The gate holds, with a passcode configured.** The test configures a dashboard passcode first,
   then asserts: no passcode → 403; wrong passcode → 403 `bad_passcode`; forged `Origin: http://localhost`
   header with no passcode → 403; right passcode → install runs. A route-inventory test enumerates every
   registered route whose handler reaches `_launch_install_terminal` or the add-on installer and
   asserts each is passcode-gated; `/api/terminal/launch` is listed as a known-open exception with a
   pointer to its own item. Decline is durable: a second request with a different `reason` returns
   `declined`.
5. **Remove.** Remove deletes the directory and the manifest entry; the next render raises a request.
6. **Tamper.** Replacing the installed `ffmpeg` binary makes the next `resolve()` mark it `broken` and
   raise a card instead of running it. Same for an adopted system copy.

Out of scope for v1: any add-on besides ffmpeg; macOS static builds until a source is vetted;
updating installed add-ons (a later version is a new catalogue entry and a new card); hosted-pod
volume wiring (clayrune_cloud); the agent-shell install guard (separate item).

## Still open

- macOS ffmpeg static source and its Gatekeeper behaviour.
- H.264 patent exposure for hosted pods (legal read before hosted launch).
- Same-user limit: the fence arms only for unattended sessions. An attended agent with Bash can still
  edit `mc/addons/catalogue.json` or `installed.json`. The card's catalogue-sourced fields and the hash
  re-check narrow this; they do not close it.

Sources: BtbN FFmpeg-Builds README, releases and `scripts.d/50-openh264.sh` / `50-x264.sh`;
openh264.org FAQ; winstall.app / wingetly.io listings for `BtbN.FFmpeg.GPL.*`; read 2026-10-01/02.
