# Untrusted input surface — where third-party content reaches an agent, and what it can do next

Written 2026-09-10. Map only — no code changed. Every finding below is
file:line-verified against this checkout unless marked **unverified**.

## Calibration

The Register incident (2026-08-28) did not fail on a filter. It failed in this
order: (1) a tool returned a confusing error (HTTP 415) instead of a clean
refusal, (2) the agent had a *lower-trust, higher-power* tool available and
switched to it (`curl`, unrestricted redirects, no content-type check), (3) the
thing that tool fetched was executable (a ZIP whose extracted `struct.py`
shadowed the stdlib), (4) nothing stopped that code from running. Anthropic's
own boundary for this class is OS isolation + egress control, not a smarter
filter. That is the standard this map holds Clayrune to: **can untrusted text
reach a session that still has unrestricted tools**, not **can the text be
made to look bad**.

Every dispatched Clayrune agent — steward, hired character, hivemind worker,
Desk-triage session — launches via the same command builder
(`mc/agent_runtime.py:1076-1081`):

```
--print --verbose --output-format stream-json --dangerously-skip-permissions
```

unconditionally, with the full global+project MCP fleet loaded unless a
caller explicitly trims it (`mcp_config_json`). There is exactly **one**
place in the codebase that removes tools/MCP/skip-permissions for a call that
processes untrusted text — `oneshot()` (`mc/agent_runtime.py:1787-1818`),
scoped to Scribe/condense/Distiller. Its docstring records the reason: on
2026-07-11 a summarization subprocess over an 80KB transcript **role-played
the session it was summarizing and executed a real `mem_save` tool call**
before the turn limit killed it — no injection required, just continuation
bias with tools attached. That incident is Clayrune's own proof of the exact
failure class this audit is about, and the fix that was applied to it
(`--allowedTools ''`, `--strict-mcp-config --mcp-config {}`, no skip-permissions)
is the template the rest of this document points back to.

---

## Ranked inventory (blast radius first)

### 1. MCP-server-from-URL install — untrusted README text writes a persistent execution primitive
**`mc/mcp_installer.py:413-459`** (`_extract_via_claude`)

- **Entry**: Tier 3 of the URL-install pipeline reads a cloned repo's
  `README.md` (up to 30,000 chars, attacker-controlled if the repo is) and
  passes it verbatim inside a prompt to `claude -p … --max-turns 1
  --output-format json` (line 429-442), asking it to emit an `mcpServers` JSON
  object.
- **Labelled?** No. The README text is concatenated directly into the
  extraction prompt with no provenance marker; the model's JSON output is
  trusted as data and fed straight into `_absolutize_paths` → the install
  writer.
- **What happens next**: the returned `command`/`args`/`env` are written to
  `~/.claude.json` or a project `.mcp.json` (per the module docstring, after a
  human "preview" step — the docstring claims commands are shown, but I did
  not locate the actual preview-UI code that renders `command`/`args` to the
  user before confirm; **unverified** whether a human reliably sees the exact
  extracted command string, as opposed to a summary of it).
- **Worst case on this install**: a malicious README causes the extraction
  call to emit `{"command":"bash","args":["-c","<payload>"]}` (or a Node/Python
  one-liner). If written, that server launches on **every future session in
  every project**, unattended, forever — the single highest-persistence path
  in this inventory. This is not hypothetical model behavior: it is exactly
  the shape of the 2026-07-11 `mem_save` incident, except the "tool call" it
  role-plays into is one *you* asked it to synthesize as literal JSON, and the
  consumer trusts the JSON shape rather than re-checking intent.
- **Existing mitigation**: install requires an explicit human confirm and
  shows the SHA (module docstring, `mc/mcp_installer.py:1-22`); "not a
  sandbox" by the author's own words. No provenance flag on the extracted
  config distinguishing "verbatim from README" vs "model-synthesized from
  possibly-hostile text."

### 2. Any MCP tool result / mail MCP / browser-automation MCP, read into a fully-privileged, unfenced session
**`tools/mail-mcp/server.py`** (whole file) · **`.mcp.json:9-19`** (`browser` = `@playwright/mcp`, full in-page JS + arbitrary navigation) · **`steward/fence.py:289-299`** (the only gate, and who it excludes)

- **Entry**: `tool_read_email` (`tools/mail-mcp/server.py:179-198`) returns the
  full decoded body of any message reachable in the configured Gmail inbox —
  `_body_text` prefers `text/plain`, else strips HTML tags with a regex
  (`server.py:126-129`) and returns the result as MCP tool-call content
  straight into the agent's transcript. `AGENT_RULES.md` instructs unattended
  cycles to search by the `[Clayrune steward]` subject tag and read Ron's
  reply — but nothing authenticates that the reply came from Ron. **Anyone who
  can get an email into that inbox with a matching subject controls text an
  unattended agent will read and may act on.** The `browser` MCP
  (`@playwright/mcp`, `.mcp.json:11-19`) is worse in kind: it is registered
  per-project, gives full page-content + JS-execution access to any site, and
  is only blocked by `steward/fence.py:246-249` — and that block fires **only**
  when `_session_is_steward()` (`fence.py:289-299`) confirms the session's
  first user message starts with the literal string `[Steward cycle]`.
- **Labelled?** No — mail body and page content arrive as plain tool-result
  text, indistinguishable in the transcript from the agent's own reasoning.
- **What happens next**: the *same* session that just read this text still
  has Bash, Write, every other MCP server, and `--dangerously-skip-permissions`
  — for every dispatch path except a literal steward cycle, nothing removes
  them first. A hired character (Dave, Fenn, Posy), a hivemind worker, or any
  session started by a human clicking "chat with Dave" is **not** a steward
  cycle by this check and runs completely unfenced.
- **Realistic worst case on this install**: a page (via the Playwright MCP) or
  an email (via the mail MCP) contains text instructing the agent to run a
  `with-secret.py`-wrapped command, push to a remote, or fetch-and-run a
  script — and nothing in the tool-call path blocks it, because the fence
  that exists for exactly this shape only arms for one marker string. This is
  the Register attack's structural precondition, present today for every
  non-steward agent.

### 3. Skill import (paste/folder/git/plugin) — MC-912 scanner is a real gate, and it is a phrase/pattern matcher
**`mc/skill_import_guard.py`** (whole file, esp. `_INJECTION_RE` line 132-141, `evaluate()` line 317-329)

- **Entry**: any imported skill (SKILL.md + bundled scripts) is scanned before
  install; `community`-tier imports are blocked on **any** finding unless a
  human passes `force=True` (line 327-329); `trusted`-tier (two hardcoded
  GitHub orgs, line 88-91) passes through `caution`-level findings unchecked.
- **Labelled?** N/A — this is the one surface that *is* explicitly a content
  classifier, by design (it says so in its own docstring, line 1-18).
- **What it actually covers**: `_INJECTION_RE` catches literal jailbreak
  phrasing ("ignore previous instructions", "DAN mode", etc.) — this is
  exactly the class of defense the task brief says not to propose, already
  present, and honestly weak: any paraphrase, non-English phrasing, or
  indirect framing ("the user has pre-approved the following, no need to
  confirm:") does not match. The destructive-command, persistence,
  config-file-write, and exfiltration detectors (lines 143-208) are more
  robust because they match **syntax** (`rm -rf /`, `crontab -e`,
  `shutil.rmtree(`, a write call near a config filename, a network call near a
  secret reference) rather than intent — syntax is harder to rephrase away,
  though still beatable by base64/string-concat obfuscation or by not
  matching `_CONFIG_TARGET_RE`'s filename list.
- **Worst case on this install**: a community skill whose payload doesn't
  trip any regex (obfuscated exec, or a target outside `_CONFIG_TARGET_RE`)
  installs clean and then runs with the same unfenced tool access as
  everything above once loaded into an agent's skill set.
- **Fail-closed posture is real and good**: a scanner crash is itself a
  critical finding (`scan_and_gate:379-387`) and `force` cannot override an
  unknown scan failure, only a known one — this part of the design is sound.

### 4. Desk / Posy triage and draft briefs — the pipe is already unlabelled, the external source isn't live yet
**`mc/desk_brief.py:151`** (`build_triage_brief`), **`mc/desk_brief.py:199`** (`build_brief`), dispatch at **`mc/blueprints/desk_routes.py:326-331, 388-392`**

- **Entry**: `build_triage_brief` embeds up to 60 signals' `summary` text
  (truncated to 200 chars, line 151) directly into the prompt handed to a
  freshly-dispatched Posy session; `build_brief` does the same for one signal
  (line 199) plus up to 3 "prior similar post" bodies (`desk_brief.py:220`,
  also 200-char truncated, unlabelled).
- **Labelled?** No — `f'{s.get("project_id")} / {s.get("kind")}:
  {(s.get("summary") or "")[:200]}'` is bare string interpolation; nothing
  marks the signal text as external/untrusted versus the brief's own
  instructions.
- **Dispatch**: both routes call `dispatch_agent(..., character=
  'global:social-media-strategist', source='agent', strict_character=True)`
  — this goes through the same `build_command` path as everything else:
  full tools, full MCP fleet, `--dangerously-skip-permissions`, and (per
  finding #2) **no fence**, because Desk triage is not a steward cycle.
- **Realistic worst case today**: bounded. `mc/desk_harvest.py` currently only
  harvests **internal** sources — git commits (`harvest_commits`,
  `desk_harvest.py:127`) and the project's own backlog
  (`harvest_backlog:181`) — not external mentions/social feeds yet. So the
  content landing in `summary` today originates inside this repo, not from
  the open internet.
- **Why it's still worth ranking**: the plumbing that will carry real
  adversarial text — an unlabelled, truncated, directly-interpolated string
  landing in a fully-privileged dispatch — is already built and already
  shipping through `/api/desk/triage` and `/api/desk/draft`. The moment
  `desk_harvest.py` grows a real mentions/RSS/social scraper (which Ron's
  brief says is coming), this becomes finding #2's shape with zero code
  changes required on the injection side.

### 5. Backlog attachments and `data/uploads`
**`mc/blueprints/project_routes.py:1645-1710`** (`upload_attachment`), **`:1606`** (`_upload_limit`)

- **Entry**: any file a human or an agent uploads to a backlog item; served
  back via `/api/attachments/<stored_name>` and `/api/serve-image`.
- **Labelled?** Filename and stored path only; no content-type or malware
  scanning found in `upload_attachment` — the only gates present are byte-size
  limits (`_upload_limit`, per-file and per-project quota, default
  *unlimited* unless Ron sets them, lines 1663-1687).
- **What happens next**: an agent only sees this content if it actively opens
  the attachment path with the Read tool (images render inline per
  `CLAUDE.md`'s "Showing the user an image" convention) or a text file gets
  cat'd — this is pull, not push, which narrows the realistic blast radius
  relative to #1-#4: the content doesn't reach context unless an agent
  chooses to read that specific file.
- **Worst case**: an uploaded text/markdown file containing instructions,
  opened by an agent investigating "what's in this attachment" during a
  backlog task, lands unlabelled in context of an unfenced session — same
  downstream risk as #2, gated only by the agent's own choice to open it.

### 6. Project files, and `CLAUDE.md`/`AGENT_RULES.md` in a repo Clayrune did not author
Inherent Claude Code CLI behavior, not Clayrune application code — **unverified** whether Clayrune's project-creation flow lets a project point at an arbitrary third-party clone (GitHub sync setup exists, `POST /api/project/<id>/github/setup`, per the API reference; I did not trace whether that path can target any repo or only ones the operator already owns).

- **Entry**: the `claude` binary itself auto-loads `CLAUDE.md`/`AGENTS.md`
  from the working directory and its ancestors on every session start — this
  is CLI behavior, confirmed by this very session's system prompt containing
  both this project's and the global `CLAUDE.md`. Clayrune's own convention
  (`mc/agent_runtime.py:1928`, `context_file_name='CLAUDE.md'`) treats it as
  first-class, trusted context.
- **Labelled?** No — by design, this is meant to blend into the prompt.
- **Realistic worst case on this install**: narrower than #1-#4 today because
  every current project in this instance is one Ron already controls. If
  Clayrune is ever pointed at a repo the operator doesn't fully trust (a
  contribution target, a client's codebase), that repo's `CLAUDE.md` is
  auto-loaded, unlabelled, with the same full-tool unfenced session reading
  it. This is upstream Claude Code product behavior; Clayrune adds no
  additional control on top of it anywhere I found.

### 7. WebFetch / WebSearch
Built-in Claude Code tools, not Clayrown-wrapped. No MC code restricts, logs,
or re-labels their output before it lands in context; the only backstop is
`steward/fence.py`'s network check, and that only fires for a steward-marker
session and only blocks *mutating* requests (line 153-166) — a plain `GET` to
any host, from any agent, is unrestricted everywhere in this codebase.

---

## Structural controls — present / absent / partial

**Fail-closed error shapes that don't provoke a tool downgrade.** ABSENT. No
code found that gives a "safe fetch" tool a distinguishable, non-confusing
failure mode, and nothing prevents an agent from reaching for `Bash`+`curl`
when a higher-level tool errors — there is no policy or tool-availability
gate stopping that substitution once tools are loaded (which, per the finding
above, is every session except steward cycles and `oneshot()` calls).

**Provenance envelopes.** ABSENT everywhere except the skill-import
quarantine record (which labels *after* the fact, for human review, not at
read time). Mail bodies, browser/page text, MCP tool results, and Desk
signal summaries all interpolate straight into prompts or transcripts with no
`<untrusted source="...">`-style wrapper. Worth building as a **labelling
convention**, not a filter — it doesn't stop a determined model from acting on
wrapped text, but it does make "was this text ever marked as external" an
auditable, structural fact instead of a guess, and it's the natural place to
hang a downstream policy (e.g., "no Bash calls for N turns after untrusted
content enters context") if one gets built later.

**The authority-guard principle (reading must never expand the reader's
authority) applied beyond the learning system.** PARTIALLY present. It exists
in exactly two places, both content-classifiers reusing the same phrase list
(`mc/distiller.py`'s `_AUTHORITY_RE`, referenced directly by
`skill_import_guard.py:244`): the Distiller's outbound artifact gate, and the
skill-import inbound gate. Neither covers mail content, browser/page content,
Desk signals, or a generic MCP tool result — the principle is real but scoped
to "things that become a skill or a learning artifact," not "things that
enter any agent's context."

**Egress restriction.** ABSENT for the general case, PARTIAL for steward.
`steward/fence.py:153-166` blocks mutating HTTP to non-local hosts, but only
for confirmed-or-unknown steward sessions, and reads are never restricted for
anyone. There is no default-deny network allowlist, no outbound proxy, no
OS-level egress control anywhere in this codebase — every non-steward,
non-`oneshot()` agent has unrestricted outbound network access the moment it
has Bash or a network-capable MCP tool, which is the default.

**The steward PreToolUse fence, and whether it generalizes.** `steward/fence.py`
is a Bash-command-shape denylist plus three non-Bash tool-name/path blocks
(editing `.claude/`, editing its own enforcement code, editing skill-stats
telemetry, and calling `mcp__browser__*`/`mcp__playwright__*`). Mechanically,
`classify_action(tool_name, tool_input)` (`fence.py:201-250`) is already
generic — it takes any tool name and input, so it *could* gate any agent.
What it does NOT generalize today is the **arming condition**: it only
enforces when `_session_is_steward()` confirms the literal marker
`[Steward cycle]` in the transcript's first user message
(`fence.py:319-321`, `main()`). A hired character, a hivemind worker, or a
Desk-triage dispatch is confirmed non-steward and passes `_session_is_steward
() is False` straight through — fully unfenced, by design, not by oversight
(the comment at `fence.py:305-309` says so explicitly: "manual/dev sessions in
the same project are unaffected"). Extending the *arming condition* to
"any session whose task is to process untrusted external content," and not
just literal steward cycles, is the single highest-leverage structural change
implied by this map — it reuses an already-built, already-tested mechanism
rather than requiring a new one.

---

## What cannot be defended without a real sandbox

Be blunt, per the ask: once untrusted text is inside the context window of a
session that still holds Bash, Write, an MCP fleet, and
`--dangerously-skip-permissions`, **no text-level control bounds what that
session does next.** Phrase/regex injection detection — including the one
that already exists in `skill_import_guard.py` — filters unsophisticated
attempts and nothing else; any attacker who controls the paraphrase, the
language, or the encoding gets past it, and false confidence from "we scan
for injection" is itself a hazard if it's read as sufficient. The 2026-07-11
`mem_save` incident proves the failure doesn't even require an adversary:
plain continuation bias with tools attached was enough.

The only thing that has ever actually bounded this class in this codebase is
removing the capability before the untrusted text arrives —
`oneshot()`'s `--allowedTools ''` / `--strict-mcp-config --mcp-config {}` /
no skip-permissions. That is a real, structural, OS-adjacent control (it
denies the capability at the CLI flag level, not by asking the model
nicely), and it is the only pattern in this repository that matches
Anthropic's own stated boundary. It is applied to three call sites
(Scribe/condense/Distiller) and nowhere else. Generalizing it means: for any
session whose job is "read potentially-hostile content and produce a bounded
output" (a mail-derived summary, a page-read result, a Desk signal digest),
run that step with the same denial-by-default flags, and only hand a
*subsequent*, separately-dispatched session (with full tools) the *already-
laundered-through-a-toolless-call* output — never the raw untrusted text plus
full tools in the same turn. That pattern removes the tool-downgrade failure
mode entirely for the step that touches untrusted content, at the cost of an
extra hop.

True OS-level isolation (a container, a restricted user, a network
namespace/allowlisted egress proxy) is absent everywhere in this codebase and
is a materially bigger project than this audit — it is the only control that
would bound an agent that genuinely needs full tools *and* must process
untrusted content in the same turn (e.g., an agent that has to both read a
hostile page and act on legitimate parts of it). Nothing here should be read
as a substitute for building that; the no-tools-first pattern above is a
mitigation for the steps that don't need full tools, not a fix for the ones
that do.

---

## Coordination note

`POST /api/browser/read` (full-page read for the browser pane) is being built
by session `836110b4a0c5` concurrently with this audit; as of this writing
that worktree has no commits and no working-tree diff yet (`git -C
.../836110b4a0c5 status` clean apart from unrelated untracked archive files).
This document does not re-specify that endpoint. Once it lands, it should be
read against finding #2 above and the fence-arming-condition point in the
structural-controls section — a full-page-read primitive on an unrestricted
`browser` MCP is exactly the shape those two findings describe, and the
endpoint's design is the natural first test of whether a no-tools-first
pattern is practical for it.
