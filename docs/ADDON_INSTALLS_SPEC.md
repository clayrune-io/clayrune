# Add-on installs: agents request, the user approves (MC-1022)

Status: DRAFT for Dave, 2026-10-01. Nothing in sections 1 to 7 is decided unless it is marked
**DECIDED (Ron)**. Each open point lists options and a recommendation; Dave picks.

**DECIDED (Ron, 2026-10-01):** the user approves each install via a card (what, why, licence, size);
one tap installs; unattended runs wait for the tap. No auto-install, OSS or otherwise.
Standing position: Clayrune never bundles or resells a vendor licence; Remotion is dropped; Desk video
stitching and 1:1 crops use ffmpeg on the Clayrune host (`docs/desk_v1/R1W_WIRING_PLAN.md` §5 item 4).

## Summary

| # | Open decision | Options | Recommendation |
|---|---|---|---|
| 1 | Licence filter | (a) OSI-approved only, copyleft allowed; (b) permissive + LGPL only; (c) anything, shown on card | **(a)**, AGPL flagged on the card; ffmpeg ships as the GPL build |
| 2 | Install source | (a) pinned static builds into our dir; (b) OS package managers (winget/brew/apt); (c) both, static first | **(a)** for v1; package managers only as a copy-paste fallback |
| 2b | Verification | (a) SHA-256 pinned in the repo catalogue; (b) trust the upstream checksum file at fetch time | **(a)**; mismatch fails closed, no alternate source tried |
| 3 | Where installs live | (a) `~/.clayrune/addons/` user-level; (b) system-wide | **(a)**; PATH injected per launch, never edited globally |
| 4a | What an agent may request | (a) catalogue entries only; (b) any named package | **(a)** for v1 |
| 4b | Human proof on approve | (a) passcode re-entry on the card; (b) one tap, no proof | **(a)**, the existing `_require_human_passcode`; (b) loosens a human-only gate, so it is Ron's call, not Dave's |
| 4c | Agents installing via their own shell | (a) guard refuses global package-manager installs; (b) leave as today | **(a)**, global/system installs only; project-local `npm install`/venv `pip` stay allowed |
| 5 | Settings UI | single "Add-ons" page: pending, installed, available | as described in §5 |
| 6 | Existing system ffmpeg | (a) use it when found on PATH, no card; (b) always our own copy | **(a)** |
| 7 | First consumer | Desk stitch + 1:1 crop, `mc/desk_stitch.py` | as described in §7 |

## 1. Licence filter

The obligation that matters in GPL/LGPL is triggered by **distributing** a copy. Under every option
below, Clayrune does not distribute: the user's own machine (or their pod) downloads the binary from
the upstream publisher on the user's click. Running GPL software server-side is not conveying it; AGPL
is the exception, because its network clause binds a modified copy served to users.

- **(a) OSI-approved SPDX licence only, copyleft allowed.** ffmpeg GPL builds qualify. The card shows
  the SPDX id plus one plain line ("GPL-3.0: free to use; obligations apply only if you redistribute it").
  AGPL entries carry an extra line. Non-OSI ("source-available", custom EULA) are refused at catalogue
  review, which keeps vendor-licensed tools like Remotion out by rule, not by memory.
- **(b) Permissive + LGPL only.** For ffmpeg this means the LGPL build, which lacks libx264/libx265
  (BtbN build notes). Concat by stream copy needs no encoder and still works; the 1:1 crop re-encodes and
  would need a hardware or OpenH264 encoder. Whether BtbN's LGPL build carries OpenH264 is **dark**.
- **(c) Anything, licence shown.** Fails the standing position's spirit; an EULA would be one tap away.

Recommendation: **(a)**. The licence check is a field on each catalogue entry, checked by a unit test
against an SPDX allow-list, so a catalogue PR with a non-OSI id fails CI.

Open, not blocking v1: H.264 **patent** licensing is separate from copyright licence. Encoding H.264 at
scale on hosted pods is the one place it could bite; it needs a legal read before hosted launch, not
before local v1.

## 2. Install sources and verification

The catalogue is a repo file, `data/addons/catalogue.json` (public source, nothing operator-specific).
Each entry, per OS/arch: download URL pinned to a **release tag** (never `latest`, which rotates daily),
SHA-256, download size, installed size, binary names, a version-probe command, SPDX licence, one-line
description, homepage.

| OS | Static source for ffmpeg (v1) | Package-manager fallback (copy-paste only) |
|---|---|---|
| Windows x64 | BtbN FFmpeg-Builds, GPL static zip (`winget` id `BtbN.FFmpeg.GPL.<ver>` is the same build) | `winget install --id BtbN.FFmpeg.GPL.8.0 -e` |
| Linux x64/arm64 (incl. hosted pod) | BtbN linux64/linuxarm64 GPL static tarball | `sudo apt install ffmpeg` |
| macOS arm64/x64 | **dark**: BtbN does not build macOS; evermeet.cx / osxexperts static builds need vetting | `brew install ffmpeg` |

- **(a) Static builds into our dir.** No admin anywhere, one code path, exact version known, clean
  remove. Cost: we maintain pinned URLs and hashes (a tool, `tools/addons-pin.py`, recomputes them).
- **(b) OS package managers.** Verification is delegated (winget manifest hash, apt GPG, brew bottle
  hash), but apt needs root (no root on a pod), winget machine-scope may raise UAC, the binary lands
  outside our dir so "remove" means driving a second tool, and versions drift.

Recommendation: **(a)**. When (a) has no entry for this OS (macOS until vetted), the card says so and
shows the fallback command for the user to run themselves, the way the provider installer already
returns `command` when it cannot run (`agent_provider_install_launch`).

Verification: hash pinned in the catalogue, which lives in git. Mismatch deletes the download and
fails the request with "checksum mismatch". The installer never tries a mirror or another build: that
would be a substitution the card did not show.

## 3. Where installs live, and PATH

- Root: `~/.clayrune/addons/` (next to the vault and `hooks/`; outside `DATA_DIR`, so the DATA_DIR
  pollution rule is not touched). Override: `CLAYRUNE_ADDONS_DIR`.
- Layout: `<root>/<id>/<version>/…`, `<root>/staging/` for in-flight work, `<root>/installed.json`
  manifest (id, version, licence, size on disk, sha256, installed_at, requested_by, approved_at).
- **Hosted pod:** `~/.clayrune` must sit on the pod's persistent volume, or `CLAYRUNE_ADDONS_DIR` must
  point at it. That is clayrune_cloud's half; the code half is the env override and a startup check
  that marks an entry `missing` when the manifest names a version whose directory is gone.
- **PATH:** never edited globally (same line `mc/guardrail_hooks.py` holds for hooks). The server
  prepends each installed add-on's bin dir to the environment of processes it spawns (agent CLIs,
  terminal pop-outs). Server-side consumers call `mc.addons.which('ffmpeg')`, never bare
  `shutil.which`, so a just-installed add-on works without a restart.

## 4. The approval card and the authority guard

The precedent is vault rule 3 (agents use credentials, only humans create them) and the learning
rails (learning may change how an agent works, never what it may do). Installing software is a
capability change on a machine the user owns or pays for.

Flow:

1. Agent or server feature calls `POST /api/addons/requests {addon_id, feature, reason, project_id,
   session_id}`. Agent-callable, attended or unattended. It creates a request; it installs nothing.
   Same `addon_id` already pending: returns the existing request. Already installed: returns `installed`.
2. Card renders in the requesting chat and as a blocking Inbox event. **Trusted fields come from the
   catalogue only** (name, what it is, licence, sizes, source host, version). The agent's `reason` is
   shown as quoted, length-capped plain text under a "The agent says" label: it is untrusted.
3. `POST /api/addons/requests/<id>/approve` installs. `…/decline` closes it.
4. A declined request is durable (the learning rails' "no must be durable" rule): a new request for
   the same add-on and feature returns `declined` without raising a card. The user can still install
   from Settings.

**4a. What an agent may request.** (a) Catalogue only: the trusted card fields exist only for
catalogue entries. (b) Any package: the card would show agent-supplied licence and size. Recommend
**(a)**; an unknown tool becomes a backlog item to add a catalogue entry, not an install.

**4b. Proving a human tapped.** Every human-only route in Clayrune now uses
`_require_human_passcode` (`mc/blueprints/secrets_routes.py`), because an Origin-header check is
forgeable by an agent's `curl` (the 2026-09-28 `attend_session` incident, route disabled since).
Localhost has no other proof of a human. Options: (a) passcode field on the card, approve = passcode
+ tap; (b) tap only. **(b) lets any agent approve its own request with one curl.** Recommend **(a)**.
This reads Ron's "one tap" as one action on the card. Dropping the passcode would loosen a human-only
gate, which `AGENT_RULES.md` reserves to Ron.

Also binding under either option:

- No config key, no allow-list, and no route that approves without the human gate. A test asserts it.
  (The standing position names an allow-list as its reopen condition; adding one is Ron's call.)
- Approval is never by email reply. `mc/question_channel.py` turns a reply into an answer, and the
  sender is unauthenticated. Unattended runs get an email **notice** with a link; the approval itself
  happens in the UI.
- An unattended run that hits a missing add-on parks its job (`waiting_on_addon`) and ends its turn.
  When the request is approved and installed, the job resumes; declined, it fails with the reason.
- The Distiller's authority guard already refuses artifacts that remove an approval gate; add an
  install-specific phrasing to its test fixtures ("always approve add-on installs").

**4c. The side door.** Today any agent with Bash can run `winget install …` itself; nothing in
`mc/guardrail_hooks.py` or `mc/process_guard.py` looks at package managers. The card gates only the
path through Clayrune. Options: (a) the PreToolUse guard refuses **global/system** installs (`winget`,
`choco`, `scoop`, `brew install`, `apt`/`dnf`/`yum install`, `npm i -g`, `pip install` outside a venv,
`curl … | sh`) with a message naming `POST /api/addons/requests`; project-local installs pass.
(b) Leave it. Recommend **(a)**, accepting that a shell guard is a speed bump, not a sandbox.

## 5. Settings UI: Settings > Add-ons

- **Waiting for you:** pending requests, each the same card as in chat.
- **Installed:** name, version, licence, size on disk, installed date, requested by (feature + chat
  link), status (`ok` / `missing` / `broken`), **Remove**. Remove refuses while a job holds the add-on
  (Windows file locks) and says which job.
- **Available:** catalogue entries not installed, with **Install** (same human gate as approve).
- A system copy found on PATH shows as "Using system ffmpeg <version>" with no Remove.

## 6. Failure modes

Install runs in a server worker thread, reporting progress over SSE to the card. Every failure lands
the request in `failed` with a one-line reason and a **Retry**; none is retried automatically.

| Failure | Behaviour |
|---|---|
| No admin rights | Not reachable for static installs (user dir). Fallback commands say when they need admin. |
| Offline / DNS / HTTP error | Fail fast with the HTTP status; no mirror. |
| Disk full | Preflight needs free space of 2 × (download + installed size); `ENOSPC` mid-install cleans staging. |
| Partial install | Download to `staging/*.part`, verify hash, extract in staging, atomic rename into `<id>/<version>`, then write the manifest. Startup deletes leftovers in `staging/`. |
| Checksum mismatch | Delete, fail, log both hashes. |
| Antivirus quarantine (Windows) | Post-install version probe fails: status `broken`, card names the likely cause. |
| Pod restart, volume not mounted | Startup marks entries `missing`; consumers raise `AddonMissing`, which raises a new request. |
| Two requests at once | One install per add-on id at a time; the second request attaches to the first. |

## 7. First consumer: Desk stitch and 1:1 crop

`mc/desk_stitch.py`:

- `stitch(clips, out)`: if all clips share codec, resolution and frame rate, ffmpeg's concat demuxer
  with `-c copy` (no re-encode); otherwise re-encode to the first clip's parameters.
- `crop_square(src, out)`: centre crop to 1:1, re-encode.
- Both resolve ffmpeg via `mc.addons.which('ffmpeg')`, which checks the system PATH first (decision 6)
  and our dir second. Missing: raise `AddonMissing('ffmpeg')`. The Desk render job catches it, files
  the request with `feature: "Desk video stitch"` and the campaign name in `reason`, and parks.

Done when:

1. With ffmpeg absent, a Desk render of two scenes creates exactly one request and installs nothing.
2. Approving with the passcode installs ffmpeg into `~/.clayrune/addons/ffmpeg/<ver>/`, the manifest
   records it, and the parked job resumes.
3. Stitching two fixture clips (small files under `tests/fixtures/desk_stitch/`) gives a duration
   within 0.1 s of their sum; the crop's output is square.
4. Approve without a passcode returns 403 (test). Decline is durable (test).
5. Remove in Settings deletes the directory and the manifest entry; the next render raises a request.

Out of scope for v1: any add-on besides ffmpeg; macOS static builds until a source is vetted;
updating installed add-ons (a later version is a new catalogue entry and a new card); hosted-pod
volume wiring (clayrune_cloud).

## Still dark

- macOS ffmpeg static build source and its signing/notarization (Gatekeeper may quarantine it).
- Whether BtbN's LGPL build carries an H.264 encoder (matters only if decision 1 goes to (b)).
- H.264 patent exposure for hosted pods.

Sources: BtbN FFmpeg-Builds README and releases (variants, sizes); winstall.app / wingetly.io listings
for `BtbN.FFmpeg.GPL.*` / `BtbN.FFmpeg.LGPL.*`; read 2026-10-01.
