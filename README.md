# Clayrune

**Mission control for your [Claude Code](https://docs.anthropic.com/en/docs/claude-code) agents.**
Run many agents, across every project, and keep working while they do.

[![License: MIT](https://img.shields.io/badge/license-MIT-1f6feb?style=flat-square)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/clayrune-io/clayrune?style=flat-square&color=1f883d&label=last%20commit)](https://github.com/clayrune-io/clayrune/commits)
[![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-6e7781?style=flat-square)](https://clayrune.io/download.html)
[![Live demo](https://img.shields.io/badge/live%20demo-clayrune.io-8250df?style=flat-square)](https://clayrune.io/demo)

Clayrune is a dashboard over the Claude Code sessions you already run. Every
project on one board, every agent named, and the ones waiting on you flagged.
It runs on your machine against your own Claude CLI.

![Clayrune — the board, one project flagged as waiting on you, and thirty agent sessions running underneath](docs/assets/demo.gif)

**[▶ Try the live demo — nothing to install](https://clayrune.io/demo)** &nbsp;·&nbsp; **[⬇ Download](https://clayrune.io/download.html)** — Windows, macOS, Linux

---

## What it does

### 🎛️ Main features

- 🔒 Runs on your machine against the Claude CLI you are already signed into. No account, no telemetry, MIT.
- 📱 Connect from anywhere in the world using the dedicated mobile app, running over a secure tunnel.
- One dashboard, as many projects as you want. Multi-agent conversations inside each project, with as many running in parallel as you need.
- 🔔 Agents run autonomously until they absolutely **need you**, then clearly signal they are waiting for an answer.
- Converse with individual agents, each with their own persona, voice and face.
- Hire new agents as you need them through Floor management, where you see everything happening right now.
- 🔀 Multi-vendor and multi-model at the same time: Claude Code first-class, plus Gemini, Codex, Aider, OpenCode and Goose.
- Read output as it streams; interrupt mid-task without killing the session.
- Agents working on the same project are protected by git worktree isolation when you choose it.

### 🌙 Working without you

- ⏰ Scheduler: once, daily, interval or cron.
- Standing briefs — hand an agent a charter and it sets its own next task.
- 🛡️ A hard-coded trust mechanism keeps agents from going rogue: 21 irreversible commands (`git push`, `DROP TABLE`, `npm publish`, `terraform destroy`, cloud spend verbs) stay blocked even when permissions are skipped.
- Blocked commands and open questions are emailed to you; your reply resumes the same run.
- Starts with the machine, so a 3am reboot doesn't take it offline.

### 🧠 Memory

- Long-term memory that adapts and keeps learning.
- Agents learn not only from what worked, but from what did not — lessons learned.
- Per-project memory agents read before starting, instead of beginning cold.
- Standing positions, so settled decisions don't get re-proposed.
- Continuous inspection scripts that score how effective your memory files and index actually are.

### 🧰 Everything else

- Backlogs with priorities, attachments and two-way GitHub Issues sync.
- Skills and MCP servers managed per project or globally.
- Token and cost tracking across sessions.
- Multi-window layouts that survive a refresh and a reboot.
- 🐝 Hivemind mode for fanning out tasks and explorations.

## Screens

| | |
|---|---|
| ![The Floor — every live agent, grouped by project](docs/assets/shot-floor.png) | ![An agent mid-run, with the conversation rail and the interrupt box](docs/assets/shot-chat.png) |
| **The Floor.** Every agent currently running, grouped by project, with a face, a name and whichever model it's pinned to. Anything marked `NEEDS YOU` is stuck waiting on an answer. | **Inside a run.** You can read what it decided as it goes, and cut in mid-task without killing the session. |

![The dashboard — five projects, with every running session listed underneath](docs/assets/shot-board.png)

---

## Install

**Windows** — open PowerShell, paste one line:

```powershell
iwr https://clayrune.io/install.ps1 -useb | iex
```

**macOS / Linux:**

```bash
curl -fsSL https://clayrune.io/install.sh | sh
```

That installs the Claude CLI, clones the repo and opens the dashboard on
`http://localhost:5199`. If you'd rather double-click something, there's a
[zip on clayrune.io](https://clayrune.io/download.html) — it's unsigned, so
Windows will grumble about an "unrecognized app" the first time.

<details>
<summary><b>Running from source</b></summary>

Requires Python 3.9+ and the [Claude CLI](https://docs.anthropic.com/en/docs/claude-code/getting-started).

```bash
git clone https://github.com/clayrune-io/clayrune.git
cd mission-control
pip install -r requirements.txt
python app.py        # native desktop window
# or: python server.py   → http://localhost:5199
```

On first run Settings walks you through the port, your projects directory and
which model to use.
Full configuration reference: [`docs/reference-config-and-features.md`](docs/reference-config-and-features.md).

</details>

---

## Common questions

<details>
<summary><b>"Isn't this just Claude Code?"</b></summary>

It doesn't replace Claude Code, it runs it. Claude Code is the engine and this
is the cockpit. You'd reach for it once you've got more than one project on the
go, more than one agent, or work you'd rather not sit and babysit.

| Bare Claude Code | Clayrune |
|---|---|
| One repo, one terminal | Every project, one board |
| You're at the keyboard | Scheduled + unattended agents |
| Session ends, context dies | Cross-session memory |
| Desk-bound | Full control from your phone |

</details>

<details>
<summary><b>"What happens when two agents touch the same repo?"</b></summary>

Mostly they don't — the parallelism is across projects, and each project's
agent gets its own directory and its own session.

If two really do need the same repo, switch on **Worktree isolation** in
Settings (it's off by default). The second agent and any after it get dropped
into their own git worktree on their own branch. Committed work merges back
when the session ends, and if it won't apply cleanly the branch is kept and
you get told, rather than something clever happening behind your back.

What that doesn't fix is two agents thinking about the same file at the same
time. Worktrees stop them overwriting each other's bytes. They don't stop the
second one building on something the first just made untrue. I haven't solved
that.

</details>

<details>
<summary><b>Everything else in there</b></summary>

Multi-provider agents (Claude Code first-class, plus Gemini, Codex, Aider,
OpenCode, Goose) · Hivemind multi-agent orchestration · Scheduler (once / daily /
interval / cron) · autonomous Steward · backlogs with GitHub Issues sync ·
Skills and MCP server management · token and cost tracking · multi-window
layouts that survive a reboot · phone access with push over the clayrune.io
tunnel · plan viewer · shared rules across projects.

Detail: [`docs/reference-config-and-features.md`](docs/reference-config-and-features.md).

</details>

---

## Incidents

Failures we hit building this, written up for people who do not use it. Each one
reported success while doing nothing, which is the only reason they are worth
reading: no error, no red build, nothing in a log.

- [Your agent's commits may not be on the branch it told you](docs/incidents/agent-commits-wrong-branch.md)
- [A CI job that passed for three months without testing anything](docs/incidents/ci-job-that-tested-nothing.md)
- [The tests covered the endpoint, so nobody clicked the button](docs/incidents/tests-that-never-clicked.md)

All of them in [`docs/incidents`](docs/incidents/).

## How it's built

Flask on the back, plain JS on the front with no build step, JSON files on disk
instead of a database, and the `claude` CLI spawned as a subprocess. One Python
process, one HTML file, no database.

Contributions welcome — fork, branch, run `python server.py`, check it, open a PR.
Notes in [`docs/reference-config-and-features.md`](docs/reference-config-and-features.md).

## License

[MIT](LICENSE), except two source-available-but-proprietary directories:
[`mc_remote/`](mc_remote/PROPRIETARY.md) and
[`mc_tunnel/`](mc_tunnel/PROPRIETARY.md), the closed platform-binding modules for
clayrune.io. The open-core seam is `mc_remote_iface/` (MIT) — implement that
contract to plug in your own remote-access provider.
Rationale: [`docs/remote-access/07-licensing.md`](docs/remote-access/07-licensing.md).
