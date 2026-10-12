# last30days keyless trial, 2026-10-12

**Verdict: (b) useful for ad-hoc research (Brainstorm/Posy), not ready to add to Quill's nightly pass.**

The raw results supplied **9 verified additions absent from the competitor journal**: four projects, two plugins, and three implementation details for an already tracked competitor. Eight predate the three daily windows being compared. This demonstrates useful catch-up discovery, not improved daily recall. The short Markdown briefs omitted all nine credited additions. At least **53 of 127 raw result occurrences (41.7%)** were off-topic for the watch. Meanwhile, the tool missed the two-point HN launch that led Quill to hermitd's strategic significance.

Four successful, anonymous runs took **52.88 seconds combined**, with no paid API or model calls by the engine. Safe execution required an isolated environment, explicit source plans, and helper-process denial; source flags alone still attempted credential helpers. Do not install the skill or paste its raw output into an unattended, fully tooled agent. No schedule, application code, browser profile, credential, or installed skill was changed. This is a report-only experiment, not an integration.

## Scope and evidence

Author: Kestrel, dispatched by Dave. Sources allowed: Reddit, Hacker News, GitHub, Polymarket. No X search, API keys, browser-cookie reads, ScrapeCreators, Perplexity, Brave, setup wizard, or CLI installation.

Baseline: Quill's nightly competitor-watch schedule and the supplied website project's `docs/_journal/competitor-watch.md`, read-only. Read the full October 10 entry (lines 11143–11313), October 11 entry and addenda (11314–11577), and October 12 entry and addendum (11578–11840). Searches for candidate names, repository owners, aliases and capability descriptions covered the **whole journal**, not just those entries. Line numbers refer to the snapshot read during this trial.

The baseline's three morning passes cover roughly October 9–12, ending 09:00Z October 12. The experiment ran approximately 17:47–17:48Z October 12 with a **30-day** search window, September 12 through October 12. Results appearing after the baseline cutoff are not credited as misses. Old discoveries are explicitly distinguished from timely finds. A result outside the three daily windows can improve a catch-up pass without proving a nightly pass failed its date scope.

Verified means the returned source URL was opened and its contents supported the described project or capability. It does **not** mean the competitor was installed, its tests rerun, or performance claims reproduced. Merged PRs are labelled as implementation evidence, not proof of a release containing them. Newness means absent from this journal after the checks below, not globally novel.

## What Quill covered

The journal uses numbered narrative headings rather than literal A/B/C/E labels. This table maps them to the requested watch sections.

| Date | A: feature gaps | B: adoption / underlying tools | C: entrants | E: motifs |
|---|---|---|---|---|
| Oct 10 | RemainderBot spends weekly allowance before reset; AO/orca quote-to-prompt; prompt history; inline graphs; squidbrake emergency stop; AgentDock next steps | Claude Code 2.1.296 cache pricing and configuration changes, private Artifact publishing, `claude agents --json`, SDK version reporting; Codex 0.162.1 fixes | AgentDock, RemainderBot, Mudroom, CircleIt, srh-session-broker, claude-bridge | Rooms 14; allowance gauges 25; quote-to-prompt opened with two makers |
| Oct 11, including addenda | agentglass skill-token attribution; squidbrake BOM fence defect; CodeAlta retained worktrees; codeg chart cards; a verified OpenCode event-parser mismatch | OpenCode 1.19/2 event/schema changes; managed orchestration beta; SineFrame MCP compatibility and reference MCP implementation | morndo, Littleguys, CodeAlta | Skill bloat reached 3; agent-drawn views reached 3; rooms 15; characters 8; allowance gauges 28 |
| Oct 12 | hermitd's ongoing responsibilities, heartbeat, readable rules and caps; CodeAlta 1.7 disk management; squidbrake proof checks/undo/passkeys; clodfarm attach; IronDeck evals; openrig identity; oh-my quota resume | Claude Code still at 2.1.296; Qwen blocking hooks checked in source; OpenCode binary/store concerns; Claude Tag discovered as an existing vendor counterpart | hermitd, IronDeck, Den, tracon, a Windows review app | Rooms 18; caps 13; allowance gauges 31; chat 16; no new three-maker crossing |

The baseline cited **GitHub release notes, READMEs and implementation files; Hacker News; Reddit r/ClaudeAI and r/LocalLLaMA; Product Hunt; vendor documentation and npm metadata**. It also compared the capabilities with Clayrune's own code and ran local checks. Examples include [RemainderBot's HN launch](https://news.ycombinator.com/item?id=50023930), [its repository](https://github.com/arimwilson/openremainderbot), [CodeAlta's HN launch](https://news.ycombinator.com/item?id=50034333), [hermitd's HN launch](https://news.ycombinator.com/item?id=50048120), and [hermitd's repository](https://github.com/gtapps/hermitd). Those are baseline citations, not discoveries credited to last30days.

Coverage was not perfect: Oct 10's Reddit newest-page limit reached only 15:28Z the previous day after a 429, later entries describe cache/rollover and connection gaps, and Discord remained dark. Nevertheless, the baseline answers “what changed, is it real, and does our product already have it?” The trial primarily retrieves discussion and issue/PR candidates. These are different jobs.

## Download, startup audit and execution boundary

Cloned [mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill) into a new empty scratch directory, `_scratch/last30days/trial-20261012`, at upstream commit **`50998cd1ba583f13a63b20e3fb5d906c71c31655`**. The checkout reports **3.28.0**, MIT, Python >=3.12, and no mandatory Python package dependencies. The current engine is **`skills/last30days/scripts/last30days.py`**, not the older root-level `scripts/last30days.py` in the brief. This layout correction did not change the requested repository or research topics. Repository popularity was not used as a quality or safety signal.

Before execution, reviewed the engine's imports/bootstrap, argument handling, setup route, configuration loaders, source routing, planner/fallback paths, the four selected source implementations, HTTP layer, cookie/setup helpers and output handling. Also AST-parsed the Python files without importing them and inventoried URL literals and 167 disk/process call sites. This was a focused execution-path review, **not a full security audit of every upstream line**.

Created `_scratch/last30days/venv` using `py -3.12 -m venv --without-pip`. No packages were installed. The engine checkout was not modified. A separate scratch wrapper installed a Python audit hook before `runpy` executed the engine. This is a useful guard on the reviewed standard-library execution path, **not an OS sandbox or a containment claim for arbitrary malicious native code**.

### Network: complete observed engine host set

All recorded research requests were anonymous HTTPS GETs. No Authorization, Cookie, or API-key headers were permitted.

| Host contacted | Purpose | Status |
|---|---|---|
| `www.reddit.com` | Public search, post/comment enrichment | Allowed Reddit source |
| `arctic-shift.photon-reddit.com` | The engine's built-in Reddit archive fallback/enrichment, including score backfill | Keyless Reddit-derived data; **not a direct Reddit host**, disclosed separately |
| `hn.algolia.com` | HN story search and comments | Allowed HN source |
| `api.github.com` | Anonymous issue/PR search and related metadata | Allowed GitHub source |
| `gamma-api.polymarket.com` | Public market search | Allowed Polymarket source |

The allowlist additionally permitted `reddit.com`; it was not observed in the four request traces. No engine request to X, a paid search provider, or a general web search endpoint was observed. Clone traffic went to `github.com`. Separate reviewer verification opened GitHub, Reddit and source links, and fetched the HN item from Algolia; those are not research-engine traffic or included in the engine timings. Local process-registration calls used `localhost:5199` from the trusted runner.

Arctic Shift was upstream's existing anonymous Reddit path, not a substitute dataset secretly supplied by this experiment. Anyone interpreting “Reddit only” as requiring **Reddit-owned infrastructure only** must exclude that backend in a future run; these measurements include it.

### Disk reads and writes

| Surface found in review | Trial behavior |
|---|---|
| Engine and imported Python modules; Python runtime/stdlib, including bytecode caches | Read from the scratch clone and installed Python runtime. Imports alone do not imply a source ran. |
| Supplied JSON search plan | **The only non-code file successfully opened for reading during each research run**, according to the audit hook |
| Default `~/.config/last30days/.env`; optional trusted project `.claude/last30days.env`; inherited credential variables | Clean child environment, scratch `LAST30DAYS_CONFIG_DIR`, project-config trust disabled; no real user config opened |
| macOS Keychain via `security`; Unix password store via `pass`; GitHub credentials via `gh auth token`; X status via `xurl auth status` | Keychain disabled; helpers unavailable on restricted PATH and all helper subprocess launches denied |
| Firefox profile discovery, `profiles.ini`, `cookies.sqlite`, temporary database copy; Linux/WSL profile discovery including `/proc/version` | No browser path read; browser discovery disabled |
| macOS Chromium-family cookie databases and Safe Storage Keychain entries: Chrome, Brave, Edge, Vivaldi, Opera, Arc, Chromium; Safari binary cookie storage | No browser path or Keychain read; cookie extraction disabled |
| Live Chrome CDP cookie path and optional browser sidecars | Not allowed or used; loopback browser ports were outside research egress allowlist |
| Saved JSON, run diagnostics, config `last-run.json` / `last-report.json`, scratch bytecode/cache/temp artifacts | Confined to `_scratch/last30days`; `--save-dir` alone would **not** have confined the config cache, so config/profile/temp roots were redirected too |

OS/Python initialization before the audit hook, such as interpreter/DLL loading and certificate handling, is outside the hook's file-open inventory. No claim of a full OS filesystem trace is made. Successful recorded reads after the hook were code and the scratch plan, not browser databases, the vault, user configuration or tokens.

Critical upstream references at the pinned SHA: `lib/env.py:29–57,285–300,368–475,547–597` for config and credentials; `lib/github.py:49–66` for token resolution; `lib/pipeline.py:2015` for startup diagnosis; `lib/cookie_extract.py:101–119,128–275,438–488` for browser stores; `lib/setup_wizard.py:71–183` for first-run behavior. Paths are relative to `skills/last30days/scripts/`.

### Setup paths deliberately not executed

The skill's first-run wizard can opt into browser-cookie extraction, install `yt-dlp` through Homebrew, and install Printing Press CLIs through `npx` for Digg/arXiv/Techmeme/Trustpilot. Its device-auth path names `api.scrapecreators.com`. The normal research configuration also contains optional provider endpoints: `api.openai.com`, `generativelanguage.googleapis.com`, `api.groq.com`, `openrouter.ai`, `api.x.ai`, `api.perplexity.ai`, `api.search.brave.com`, `api.exa.ai`, `api.parallel.ai`, `search.parallel.ai`, and `google.serper.dev`; other fallbacks include DuckDuckGo/Startpage and `r.jina.ai`. None were contacted in this trial.

The repository also contains optional X/browser integrations (`x.com`, `twitter.com`, `api.x.com`, `upload.twitter.com`, `xquik.com`, local CDP) and other source adapters. These are **possible paths, not observed contacts**. Package managers and arbitrary source URLs can introduce transitive hosts, so a literal-URL scan cannot honestly enumerate every host an unrestricted wizard could ever contact. The wizard and arbitrary egress were excluded entirely; the five-host trace above is the complete observed research set. The full static literal inventory, including examples/test placeholders that are not destinations, is retained in scratch as `static-hosts.json`.

### Source flags were insufficient on their own

Even with only the four sources selected and `--no-browser-cookies`, startup tried `xurl auth status`, and GitHub tried `gh auth token`. The wrapper denied **29 helper subprocess attempts** across the four runs before execution. The first run's trace captured only the Windows executable field (`None`); the next three also captured command lines and establish these two helper names. Do not attribute all first-run attempts to a named command based on that incomplete trace.

All four runs still exited zero using the engine's supported anonymous fallback. There were no recorded file-read or network-denial events. Thus the selected sources **can run keylessly**; no forbidden dependency was installed or supplied to make them work. The wrapper prevented optional credential discovery rather than replacing results. A normal installation without equivalent isolation has not been approved by this experiment.

## Run method and measurements

Clean child environment retained Windows runtime variables only, restricted PATH to the venv, and redirected profile/appdata/temp directories to scratch. Relevant flags/environment:

```text
--search=reddit,hackernews,github,polymarket
--no-browser-cookies --plan <scratch-plan.json>
--as-of=2026-10-12 --days=30
--emit=json --json-profile=raw
--save-dir <scratch-run-dir> --no-verify-freshness
LAST30DAYS_CONFIG_DIR=<scratch-config-dir>
LAST30DAYS_SKIP_KEYCHAIN=1
LAST30DAYS_TRUST_PROJECT_CONFIG=false
BROWSER_CONSENT=false FROM_BROWSER=off AGENTCOOKIE=off
LAST30DAYS_HOST=codex LAST30DAYS_STRICT_EXIT=1
```

Explicit plans kept every subquery on the four permitted sources and prevented a short competitor-name query from adding general-web fallback. Plans requested news intent, strict freshness and equal source weights. They supplied these exact subqueries:

| Topic | Subqueries |
|---|---|
| Claude Code multi-agent dashboard | Same topic; `Claude Code agent dashboard` |
| Claude Code orchestration tool | Same topic; `Claude Code agent orchestrator` |
| Claude Code new release features | Same topic; `Claude Code release` |
| hermitd | `hermitd` |

These are source-restricted external plans, **not an evaluation of the skill's default all-source planner**. The engine still normalizes/relaxes source searches; actual request URLs are in the audit files. That behavior explains some noise and is part of what was tested. Freshness network verification was disabled to keep arbitrary source-page requests out of the engine; source dates and credited claims were checked separately.

An initial invocation used the wrong flag `--as-of-date`, exited 2 before network access in 0.58 seconds, and is preserved under `runs/dashboard/json-argument-error`. It produced no research result. The four measurements below use the corrected documented flag `--as-of`.

| Topic | Reddit | HN | GitHub | Polymarket | Total | Clearly off-topic | Related/adjacent remainder | Seconds | Exit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Dashboard | 12 | 13 | 5 | 0 | 30 | 6 (20.0%) | 24 | 18.74 | 0 |
| Orchestration | 12 | 19 | 3 | 10 | 44 | 17 (38.6%) | 27 | 11.34 | 0 |
| Releases | 12 | 12 | 2 | 7 | 33 | 18 (54.5%) | 15 | 11.39 | 0 |
| hermitd | 12 | 0 | 8 | 0 | 20 | 12 (60.0%) | 8 | 11.41 | 0 |
| **Total occurrences** | **48** | **44** | **18** | **17** | **127** | **53 (41.7%)** | **74** | **52.88** | **4/4 successful** |

There are **100 distinct exact URLs**, so 27 occurrences repeat across topics. Counts use the raw `items_by_source` arrays before ranking; they are not counts of verified useful facts. “Related/adjacent” generously retains requests, workflow discussions and unverified claims, and must not be read as 74 signals. The classification concerns Quill's concrete competitor/capability watch, not whether a post could interest someone generally.

Manual off-topic ledger, using **one-based positions in each raw source array**:

| Run | Rejected positions | Reason |
|---|---|---|
| Dashboard | GitHub 2,4; HN 1,3; Reddit 7,8 | Energy dashboard; browser input stealth; planet discovery; general Opus tips; personal/home-automation dashboards |
| Orchestration | HN 1,3,4,5,8,11,15; Polymarket 1–10 | Planet stories, Linux work, general tips/job sentiment, agent-written package manager, Siri story; forecasts are not released orchestration capabilities |
| Releases | HN 1,3,4,5,8,11; Reddit 2,5,6,7,12; Polymarket 1–7 | General use/productivity/sentiment/planet stories; music/product-release markets and unreleased-model forecasts |
| hermitd | Reddit 1–12 | Literal hermits, hermit lifestyle and similarly named entertainment; none concern the competitor |

Borderline adjacent tools such as Agent.reviews and Tofu remain in the related bucket, making the rejection count conservative. Polymarket contributed **zero verified watch items**, and all 12 hermitd Reddit hits were irrelevant. The engine reported a thin/concentrated evidence warning for hermitd, but the other three successful runs had no warning that conveyed their noise burden.

Cost: **$0 paid API usage attributable to these engine invocations**, no model-backed planning/reranking. Runtime metadata reported deterministic planning/local reasoning/local-score reranking. This excludes the host research session's model/web-tool costs, which are not measured, and the human/agent effort of audit, deduplication and verification. Do not describe the complete experiment as costless or a 53-second analyst task.

### JSON and Markdown artifacts

Each run saved raw JSON through `--emit=json`, plus the engine's named `*-raw.json`. The requested Markdown brief was generated **offline from that exact raw report** using upstream `schema.report_from_dict` and `render.render_brief`, the renderer selected by `--emit=brief` (`last30days.py:518–519`). This was not a second research run with a drifting corpus. The rendering pass recorded zero network requests and zero denials.

The raw ranked-candidate counts were 21/40/32/3. The brief shows at most eight ranked storylines for each of the first three topics and three for hermitd. None of the nine credited additions below appeared in those brief storylines. The dashboard brief elevated the planet story and general Opus advice; the release brief led with “I hate claude code.” Hermitd's brief showed the already known version and two repairs, omitting the three feature details credited below. Raw retrieval, rather than the convenient brief, produced the useful incremental coverage.

## Verified additions and exclusions, by topic

### Dashboard: three additions, all older catch-up

| Item and date in returned result | What opening the source established | Journal check / credit |
|---|---|---|
| [Bough](https://github.com/andreylukin/bough), Sep 15 HN discovery | A coding engine with a browser session control room, transcripts, questions and images via `bough serve`; macOS/Linux focus. Adjacent competition, not simply a Claude Code wrapper. | No `Bough` word-boundary or `andreylukin` match. **1 project**. A loose substring search finds “bought”; that is not the project. |
| [Agent Workspace Hub runtime identity PR #98](https://github.com/sawroop1242/Agent-workspace-hub/pull/98), Sep 23 | Merged into its `rust` branch: profiles, registry, persisted agent sessions and validated caller/workspace ownership. Evidence of an implemented workspace capability; no stable-release claim. | No `sawroop1242`, `Agent-workspace-hub`, or equivalent spaced-name match. **1 project with merged implementation evidence**. |
| [Command & Context Reddit launch](https://www.reddit.com/r/ClaudeCode/comments/1wpzzm8/seeing_what_others_managed_with_a_single_prompt_i/), Sep 25; [repository](https://github.com/geekgreg/command-and-context) | Windows-first isometric monitor of Claude sessions, subagents, processes and ports. README distinguishes real monitoring from simulated demo mode; other platforms experimental. | No `geekgreg` or Command-and-Context spelling variants. **1 project**; another maker for the already known graphical-room motif, not a new motif. |

Quill had things this run missed: RemainderBot's reset-aware allowance use, AO/orca quote-to-prompt changes, CodeAlta's disk/worktree controls and several named new entrants. It did retrieve Littleguys, OpenRig, Pi pod and the Chief of Staff pattern, but those already occur in the journal (for example lines 11400, 8036 and 4579). Those receive no discovery credit.

### Orchestration: two additions, both older catch-up

| Item and returned date | Verified capability | Credit |
|---|---|---|
| [Terse](https://github.com/lowenbjer/claude-terse), Oct 7 HN | Claude Code plugin using output-style/prompt instructions and hooks to reinforce concise replies. Its advertised percentage savings were **not** validated. | No `lowenbjer` / `claude-terse` match. **1 plugin**. |
| [Paste-preview](https://github.com/alan890104/claude-code-paste-preview), Oct 7 HN | Preview/mark up/crop pasted images and pass the edited image to the model, using the Claude Code mods surface. | No `alan890104` / paste-preview variants. **1 plugin**. |

These are useful capability leads, not proof of improved orchestration itself. RondoFlow, returned as “Visually orchestrate Claude Code AI agents,” is extensively covered from September 23 onward (journal around line 4955), so it is **not** new. T3 Code's [side-chat issue](https://github.com/pingdotgg/t3code/issues/15758) is a feature request, and [OAuth PR #15220](https://github.com/pingdotgg/t3code/pull/15220) is closed without a merged status. Neither gets implemented-capability credit. T3's actual V2 evidence is credited under releases instead. A reported Claude cross-session send cap is a user report, not a verified new released feature.

Quill had RemainderBot, hermitd's operating model, IronDeck/Den/tracon, and specific tracked-orchestrator release changes that this run did not retrieve. Broad Reddit workflow discussions do not substitute for those named changes.

### Releases: three additions, two shared with orchestration

The run found Terse and Paste-preview again, plus **[T3 Code Orchestrator V2](https://github.com/pingdotgg/t3code/issues/14871)** (Oct 2), verified against its migration announcement and [repository](https://github.com/pingdotgg/t3code). The announcement describes the nightly V2 rollout, native steering/queue behavior, provider switching, fork/rollback, scheduling and usage capabilities. It also explains migration and protocol compatibility. This is **nightly/pre-stable evidence**, not a claim that every listed feature is in a current stable build.

No `pingdotgg`, `T3 Code` or `t3code` match appeared anywhere in the journal. It is not the already tracked Agent Orchestrator: that row names `Untrivial-ai/agent-orchestrator`. Credit **1 additional project**, giving three per-topic additions here but only one new unique addition after orchestration deduplication.

This run did **not** deliver Quill's concrete 2.1.296 cache-price/configuration findings, private Artifact publishing, `claude agents --json`, SDK initialize version field, or the OpenCode event changes. Its other GitHub result was a CI problem involving 2.1.280. The MCP compatibility Reddit result overlaps Quill's October 11 SineFrame investigation. Third-party plugins and an old CI issue do not answer “what did the vendor just release?”

### hermitd: three implementation details; only one timely addition

| Source | Verified detail | Window / credit |
|---|---|---|
| [PR #1184](https://github.com/gtapps/hermitd/pull/1184), Oct 10 | Merged to main: bind a task's channel thread to its spawned helper, preserving follow-ups with that helper's working context. | Within baseline daily windows. **1 feature detail** absent from the journal's general spawn-session coverage. |
| [PR #1172](https://github.com/gtapps/hermitd/pull/1172), Oct 8 | Merged to main: watchdog detects an upgrade and restarts an idle resident to finish adopting it. | Older catch-up. **1 feature detail**. |
| [PR #1165](https://github.com/gtapps/hermitd/pull/1165), Oct 7 | Merged to main: pass an assignment's effort to its task worker; also adopts the new one-step plugin-install syntax. | Older catch-up. **1 feature detail**, counting per-task effort, not claiming discovery of the vendor's install syntax. |

The journal already knows hermitd, its v1.4.13, background goals/heartbeat, caps, rules, chat and spawned helpers. None of those is a new find. Whole-journal checks also covered its old `claude-code-hermit` name, thread/helper wording, upgrade/watchdog and per-task-effort terms. The unrelated older per-spawn effort entry at line 5317 is not a hermitd implementation report. These three details are incremental implementation coverage, not three new competitors or proof Clayrune lacks them.

The remaining GitHub results are the known [v1.4.13 bump](https://github.com/gtapps/hermitd/pull/1192), [a helper-follow-up repair](https://github.com/gtapps/hermitd/pull/1186), [cached channel-failure repair](https://github.com/gtapps/hermitd/pull/1183), a [pricing bug report](https://github.com/gtapps/hermitd/issues/1170), and the [rename](https://github.com/gtapps/hermitd/pull/1153). The repair/rename PR pages were opened and confirmed merged, but receive no separate competitor/capability credit. The pricing issue is not independently validated and receives no credit. No operational instructions in those pages were followed.

**Material miss:** HN returned zero items. A separate verification request to [Algolia item 50048120](https://hn.algolia.com/api/v1/items/50048120) returned the October 11 21:43:27Z hermitd launch with **2 points**. Upstream `lib/hackernews.py:34,136–140` retains only points **greater than 2**. That threshold excludes this launch at its observed score. It is precisely the low-engagement new entrant Quill did catch. The trial also did not recover the Claude Tag comparison or establish the whole ongoing-responsibility/rules/caps bundle; Quill's primary-document read did.

### Verification limits

Several exact GitHub PR URLs returned a cache-miss error through the web tool. Anonymous direct GETs of those **same URLs**, not alternate sources, returned HTTP 200; their visible text and merge states were inspected and retained in scratch. The hermitd release-tag URL did not verify through the web tool, so this report does not infer that every merged PR was shipped in that tag. No authenticated browser session or cookies were used.

The nine credited additions are a conservative verified count. Other related raw items may deserve later investigation; they are not silently counted as discoveries. No quantitative recall percentage is given: neither this journal nor one retrieval run is a complete ground-truth universe.

## Why ad-hoc, and what nightly adoption would require

This tool helps a researcher widen a month-long search and rediscover projects outside a daily feed. That is useful for Brainstorm/Posy. This experiment does **not** justify putting its brief into the nightly watch: ranking hid the useful additions, eight of nine additions were old catch-up, Polymarket and exact-name Reddit search added noise, and HN suppressed a low-score launch. Retain Quill's release/docs/source verification and product comparison.

Adoption as a nightly extra source would need a separately approved implementation, not merely installing this skill:

1. **Constrained fetch worker:** pin the reviewed revision; use OS-level filesystem/egress isolation with no secrets, browser profiles, helper credential CLIs or automatic package installation. Explicit source selection must cover startup diagnosis as well as searches. Prefer Reddit/HN/GitHub for this watch; Polymarket had 0 useful results here. Treat Arctic Shift as a disclosed backend. This is a recommendation for future work, not a source restriction silently applied to these four runs.
2. **Raw-result selection and state:** normalize canonical repo/URL aliases, retain provenance and publication timestamps, distinguish requests/closed PRs/merged work/releases, and compare with the full journal/tracked set before scoring novelty. Keep a reviewed monthly-discovery path separate from the nightly date window. Revisit HN's engagement floor for low-score entrants; avoid another popularity filter that buries the useful tail.
3. **Launder before an unattended agent sees text:** route titles, bodies, snippets, comments and linked-page text from the isolated fetcher directly through a certified **toolless** transform. Reuse the pattern documented in `docs/UNTRUSTED_INPUT_SURFACE.md`, the current `mc/mail_launder.py:171–191` use of `run_text_transform`, and `mc/desk_connect/readme_servers.py`. A prompt saying “ignore instructions” is not the boundary. No tools, MCP fleet, inherited credentials or unrestricted filesystem/network access may be available to the model reading raw social content.
4. **Fail closed and preserve provenance:** emit a bounded schema of URL, source, date, claimed change, evidence status and uncertainty; validate it in code and keep the digest marked untrusted. If the certified transform is unavailable or fails validation, report the source unavailable; do not fall back to raw text in Quill's tool-enabled context. A digest is not authenticated truth. Subsequent page verification must follow the same isolated-read path, or it reopens the hole.
5. **Measure before schedule changes:** compare several paired daily windows, including empty days, with accepted net-new items after deduplication, false positives, reviewer time and actual transform cost. One month-wide trial cannot establish daily marginal value. No estimates of paid transform cost or integration effort are presented as measured facts.

No rollout choice is needed to finish this experiment. **Recommendation to Dave: use it ad hoc now; leave the nightly schedule unchanged.** If Dave later selects nightly adoption, the five requirements above define a separate integration task. This report authorizes none of that work and changes no human-only gate.

## Reproducibility and retained artifacts

Only this report is intended for the commit. Scratch is gitignored and was not staged. Paths below are relative to this worktree so no operator-specific home path enters the public repository.

```text
_scratch/last30days/trial-20261012/       pinned, unmodified upstream checkout
_scratch/last30days/venv/                 Python 3.12, no pip packages installed
_scratch/last30days/trial_runner.py       explicit plans, clean env, timing, registration
_scratch/last30days/guarded_engine.py     additive file/network/helper guards
_scratch/last30days/inspect_results.py    offline upstream brief renderer / text inspection
_scratch/last30days/static-hosts.json     literal inventory, including non-destination examples
_scratch/last30days/static-disk-calls.json
_scratch/last30days/runs/<topic>/json/    invocation, plan, measurement, audit, stdout/stderr,
                                       saved raw JSON, brief.md, isolated config/profile
_scratch/last30days/render-audit/         offline rendering trace, zero requests
_scratch/last30days/*verify.txt           additional exact-URL verification text
_scratch/last30days/hermitd-hn-verification.json
```

Raw stdout JSON SHA-256 hashes:

| Topic | SHA-256 |
|---|---|
| Dashboard | `0d634bd14b9f7ddf5ac5953659c91d985c244ee0f74a7bc13661b8456092b79d` |
| Orchestration | `5cdfa5ab36a314dd9ec6457fa17ae8952ddad8fdfd89f8b7b6a1c265e85c191c` |
| Releases | `bb12bceb80a056c53b3b0492ad076beeb7268f4fb939327b2df7c6ff3da54ef1` |
| hermitd | `dc95efb6bf517a521c12cc9787ed1dbbbef9dcfe671894981394d50ea6171990` |

No runtime-attributed memory write was made: the available runtime did not have confirmed registration. The committed report is the durable handoff; scratch evidence remains local and would not follow a branch merge automatically.
