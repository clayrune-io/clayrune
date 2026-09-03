# Configuration, features and architecture

Moved out of README.md on 2026-09-02 so the front page could lead with what
Clayrune does rather than an inventory of it. Nothing here changed.

## Configuration

On first run, a `config.json` file is created with defaults:

```json
{
  "port": 5199,
  "shared_rules_path": "data/SHARED_RULES.md",
  "projects_base": "/home/you",
  "agent_model": "",
  "agent_max_turns": 0,
  "use_streaming_agent": false,
  "user_name": "",
  "agent_name": ""
}
```

| Setting | Description | Default |
|---------|-------------|---------|
| `port` | Server port | `5199` |
| `shared_rules_path` | Path to shared rules file (injected into all agent prompts) | `data/SHARED_RULES.md` |
| `projects_base` | Base directory for project path validation | User home directory |
| `agent_model` | Default Claude model for all projects | `""` (CLI default) |
| `agent_max_turns` | Max agent turns per session (0 = unlimited) | `0` |
| `use_streaming_agent` | Enable Mode B persistent agent process | `false` |
| `user_name` | Your name (shown in agent context) | `""` |
| `agent_name` | Agent display name | `""` |

You can also set the port via environment variable: `MC_PORT=8080 python server.py`

## Features

### Project Dashboard
- Tile-based overview of all projects with status indicators (Active, Waiting, Blocked, Parked)
- Snap-to-grid tile arrangement — drag tiles to any position, leave gaps, swap tiles
- Domain categorization with customizable colors
- Per-project accent color theming
- Activity stream across all projects
- Filter by status or domain
- Compact button to remove grid gaps

### Agent Management
- Dispatch tasks to Claude Code agents with one click
- Real-time streaming output with syntax highlighting
- Send follow-up messages to running or idle agents
- Multiple concurrent agent sessions per project
- Stop/resume agent sessions
- Paste screenshots directly into agent prompts
- Per-project model selection (Sonnet 4.5, Opus 4.6, Haiku 4.5)
- Interactive question forms when agents call AskUserQuestion
- Plan approval button for agents stuck in ExitPlanMode
- Token usage and cost tracking per session
- Live elapsed timer for running sessions

### Agent Modes
- **Mode A** (default): Spawns a new `claude` process per turn. Follow-ups queue and auto-dispatch.
- **Mode B** (`use_streaming_agent: true`): Persistent process with `--input-format stream-json`. Follow-ups write directly to stdin for faster responses. Process stays alive between turns.

### Scheduler
- Automate agent dispatch on a schedule
- Three schedule types: Once (specific datetime), Daily (time + day-of-week), Interval (every N minutes)
- Enable/disable individual schedules
- Upcoming jobs banner shows next 5 scheduled tasks

### Backlog
- Per-project task backlog with priorities (low/normal/high/critical)
- Drag-and-drop reordering
- File attachments with drag-and-drop upload
- Mark items done/reopen
- Dispatch backlog items directly to an agent session
- GitHub Issues sync (bidirectional)

### GitHub Issues Sync
- Connect any project to a GitHub repository (`owner/repo`)
- Backlog items sync with GitHub Issues in both directions
- Priority labels mapped automatically (high/medium/low)
- Open/closed status synchronized
- Manual sync button or automatic sync every 5 minutes
- Requires `gh` CLI for authentication

### Plan Viewer
- Agent plans open in a dedicated wider window for easier reading
- Auto-detection of plan mode output
- Plans History tab shows all historical plans per project
- Pop-out button for viewing any agent output in a larger window
- Ctrl+scroll zoom support

### Memory
- Per-project persistent memory using Claude Code's native `MEMORY.md`
- Memory content injected into agent context automatically
- Auto-memory: session summaries appended on completion
- Shared between Clayrune and direct Claude CLI usage

### Multi-Window System
- Open multiple project modals simultaneously
- Drag, resize, minimize, and restore windows
- Keyboard navigation (Escape to close)
- Minimized tray at bottom of screen
- Touch support: drag, resize, and pinch-to-zoom on mobile devices

### Token Tracking
- Global token counter in header (input/output tokens + USD cost)
- Click to filter by time range: All Time, Today, This Week, This Month
- Per-session token badge after completion

### Walkthrough Tour
- 18-step guided tour for new users
- Auto-triggers on first run (zero projects)
- Re-triggerable via "Tour" button in header
- Virtual demo tile and modal shown during tour

## Architecture

```
mission-control/
  app.py                 Desktop entry point (pywebview + Flask)
  server.py              Flask backend (API + static serving)
  mc/github_sync.py      GitHub Issues sync module
  static/
    index.html           Single-page app (HTML + CSS + JS, no build step)
  data/
    projects/            Project JSON files (auto-created)
    uploads/             File attachments
  config.json            User configuration (auto-created, gitignored)
  installer/             Hosted one-command installer (clayrune.io)
```

- **Backend**: Python Flask server on configurable port (default 5199)
- **Frontend**: Vanilla HTML/CSS/JS single-page app (no framework, no build step)
- **Data**: JSON files on disk (no database required)
- **Agent**: Spawns `claude` CLI as subprocess with streaming JSON output
- **Desktop**: Native window via pywebview (WebView2); run `python app.py` from source

## Contributing

Contributions are welcome! Here's how to get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes
4. Test locally: `python server.py` and verify in browser
5. Submit a pull request

### Development Notes

- The entire frontend is in a single `static/index.html` file — no build step needed
- The server is a single `server.py` file with Flask
- GitHub sync logic is in `mc/github_sync.py` (imported by server.py)
- Data is stored as JSON files in `data/projects/`
- Agent sessions use Server-Sent Events (SSE) for real-time streaming
- The `claude` CLI is invoked via `subprocess.Popen` with `--output-format stream-json`

