"""Agent dispatch family — blueprint 1.12 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py (app-to-bp route-decorator swap; CONFIG reads
rewritten to state.CONFIG — the 1.7/1.9/1.11 precedent). 33 routes (the plan
table said 9 /api/agent + 3 /api/claude — that count predates the provider
generalization and the run-history growth, same undercount story as
1.3/1.4/1.10):

  • claude binary resolution (_resolve_claude/_claude) + pid/kill/window
    helpers (_pid_is_alive/_kill_pid/_hide_process_windows/_hide_windows_delayed)
  • the global incognito pseudo-project (INCOGNITO_PROJECT_ID +
    _ensure_incognito_project)
  • per-project MCP trim (_mcp_server_catalog/_resolve_project_mcp_config) +
    _build_claude_flags + the auto-model router (classifier pool,
    _resolve_dispatch_model/_dispatch_with_routing*/_route_dispatch_model,
    _router_stat telemetry, /api/router/stats)
  • sysprompt temp-file plumbing (_sysprompt_file_args/_sysprompt_cleanup)
  • ProjectAgentManager + get_manager(+_for_session)/all_managers + the
    per-project guardian loop, plus the Session Guardian check family
    (GUARDIAN_* consts, _guardian_check_session & co., idle eviction)
  • the process ledger writers _register_process/_unregister_process (the
    reaper + _proc_identity/_persist_pid_ledger STAY in server.py — startup
    family — and are wired in)
  • /api/agent/upload-image (+_downscale_image_if_huge; quota helpers
    cross-imported from project_routes)
  • claude auth-state tracking + /api/agent/providers + provider env store +
    the generic /api/agent/<provider>/auth-* family + legacy /api/claude/*
    shims (3)
  • the agent context builders (_clayrune_api_reference/
    _clayrune_universal_capabilities/_skills_catalog_block/
    _build_agent_context) and the TodoWrite->backlog sync
  • BOTH stream readers (_read_agent_stream/_read_agent_stream_b — now
    heartbeating as 'stream-reader:a'/'stream-reader:b' in
    /api/system/loops, Phase 2) + _auto_recover_failed_resume
  • the agent_log store (_load_agent_log/_save_agent_log) + completion/
    dispatch-pending writers + _session_usage_payload + revive machinery
  • _dispatch_via_runtime + _dispatch_agent_internal + the 11 /api/.../agent/*
    routes incl. the 492-line agent_followup (moved WHOLE — decomposition is
    explicitly out of scope per the plan) + plan-file pair + transcript/
    reconstruct + recent-runs/search-chats/conversations/plans/usage

THE LOAD-BEARING LINE (CLAUDE.md "Memory system"): ALL scribe/condense/
checkpoint/MEMORY.md-write machinery stays in server.py. Every call from
moved code into that family is WIRED (see wire(): maybe_checkpoint_fn,
write_session_memory_fn, dispatch_condense_fn, should_condense_fn,
get_condense_status_fn, scribe_call_fn, memory_search_fn,
get_memory_path_fn/get_archive_path_fn). Nothing in this module takes
_get_mem_write_lock or writes MEMORY.md.
"""

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flask import Blueprint, Response, jsonify, request

from mc import media as _media
from mc import obs, state
from mc import state as _mc_state  # readers write _mc_state._LAST_SYSTEM_STATUS verbatim
from mc.core import _harden_secret_perms, _log, now_iso, time_ago
from mc.state import (
    _backlog_sync_lock,
    _claude_auth_lock,
    _claude_auth_state,
    _condense_lock,
    _condensing_projects,
    _guardian_stop,
    _managers,
    _managers_lock,
    _provider_env_lock,
    agent_sessions,
    process_tracker_lock,
    terminal_sessions,
    tracked_processes,
)

import agent_runtime as _agent_runtime  # Multi-provider abstraction
import distiller as _distiller          # exploration read-floor (registered by server.py)
import skills as _skills                # _skills_catalog_block
import agent_worktree as _agent_worktree  # per-agent worktree isolation (b264200a)

# Cross-blueprint imports (the 1.4/1.5/1.11 precedent — defs, not wire
# placeholders; called at request/stream time only, long after server.py has
# wired every blueprint):
from mc.blueprints.project_routes import (
    _incoming_file_size,
    _is_secret_file,
    _log_agent_activity,
    _upload_limit,
)
from mc.blueprints.push_mobile import _handle_push_signal      # re-homed 1.2 shim
from mc.blueprints.system_routes import _capture_system_init   # re-homed 1.6 shim
from mc.blueprints.terminal_routes import launch_pty_session    # MC-928
from mc import pty_backend

bp = Blueprint('agent_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
DATA_DIR: Path = None  # type: ignore[assignment]
UPLOADS_DIR: Path = None  # type: ignore[assignment]
_APP_DIR: Path = None  # type: ignore[assignment]
PORT: int = 0
SHARED_RULES_PATH: Path = None  # type: ignore[assignment]
PROVIDER_ENV_PATH: Path = None  # type: ignore[assignment]
CLAUDE_HOME: Path = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None
load_project: Callable[[str], Optional[dict]] = None  # type: ignore[assignment]
save_project: Callable[[str, dict], Any] = None  # type: ignore[assignment]
load_projects: Callable[[], list] = None  # type: ignore[assignment]
# Memory/scribe/condense family — STAYS in server.py (CLAUDE.md lock+atomic
# discipline); these are the wired call seams:
_get_memory_path: Callable[[dict], Path] = None  # type: ignore[assignment]
_get_archive_path: Callable[[dict], Path] = None  # type: ignore[assignment]
_memory_search: Callable[..., list] = None  # type: ignore[assignment]
_maybe_checkpoint: Callable[[dict], None] = None  # type: ignore[assignment]
_write_session_memory: Callable[..., bool] = None  # type: ignore[assignment]
_dispatch_condense: Callable[[dict], None] = None  # type: ignore[assignment]
_should_condense: Callable[..., bool] = None  # type: ignore[assignment]
_get_condense_status: Callable[[str], dict] = None  # type: ignore[assignment]
_scribe_call: Callable[[str, str, str], str] = None  # type: ignore[assignment]
# Transcript/scan helpers — shared with the scribe/backfill stayers:
_find_transcript_file: Callable[..., Any] = None  # type: ignore[assignment]
_parse_transcript_messages: Callable[..., list] = None  # type: ignore[assignment]
_recent_claude_transcripts: Callable[..., list] = None  # type: ignore[assignment]
_session_too_large: Callable[..., tuple] = None  # type: ignore[assignment]
_long_session_advisory: Callable[[dict], Any] = None  # type: ignore[assignment]
_resume_is_fragile: Callable[..., bool] = None  # type: ignore[assignment]
_encode_project_path: Callable[..., Any] = None  # type: ignore[assignment]
_extract_transcript_telemetry: Callable[..., dict] = None  # type: ignore[assignment]
# PID-ledger internals — the reaper family stays in server.py:
_proc_identity: Callable[..., tuple] = None  # type: ignore[assignment]
_persist_pid_ledger: Callable[[], None] = None  # type: ignore[assignment]


def wire(*, data_dir, uploads_dir, app_dir, port, shared_rules_path,
         provider_env_path, claude_home, popen_flags, startupinfo,
         load_project_fn, save_project_fn, load_projects_fn,
         get_memory_path_fn, get_archive_path_fn, memory_search_fn,
         maybe_checkpoint_fn, write_session_memory_fn, dispatch_condense_fn,
         should_condense_fn, get_condense_status_fn, scribe_call_fn,
         find_transcript_file_fn, parse_transcript_messages_fn,
         recent_claude_transcripts_fn, session_too_large_fn,
         long_session_advisory_fn, resume_is_fragile_fn,
         encode_project_path_fn, extract_transcript_telemetry_fn,
         proc_identity_fn, persist_pid_ledger_fn):
    """Late-bind cross-family deps. Called once by server.py after the
    memory/scribe/condense machinery (which stays there) is defined."""
    global DATA_DIR, UPLOADS_DIR, _APP_DIR, PORT, SHARED_RULES_PATH
    global PROVIDER_ENV_PATH, CLAUDE_HOME, _POPEN_FLAGS, _STARTUPINFO
    global load_project, save_project, load_projects
    global _get_memory_path, _get_archive_path, _memory_search
    global _maybe_checkpoint, _write_session_memory, _dispatch_condense
    global _should_condense, _get_condense_status, _scribe_call
    global _find_transcript_file, _parse_transcript_messages
    global _recent_claude_transcripts, _session_too_large
    global _long_session_advisory, _resume_is_fragile, _encode_project_path
    global _extract_transcript_telemetry, _proc_identity, _persist_pid_ledger
    DATA_DIR = data_dir
    UPLOADS_DIR = uploads_dir
    _APP_DIR = app_dir
    PORT = port
    SHARED_RULES_PATH = shared_rules_path
    PROVIDER_ENV_PATH = provider_env_path
    CLAUDE_HOME = claude_home
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    load_project = load_project_fn
    save_project = save_project_fn
    load_projects = load_projects_fn
    _get_memory_path = get_memory_path_fn
    _get_archive_path = get_archive_path_fn
    _memory_search = memory_search_fn
    _maybe_checkpoint = maybe_checkpoint_fn
    _write_session_memory = write_session_memory_fn
    _dispatch_condense = dispatch_condense_fn
    _should_condense = should_condense_fn
    _get_condense_status = get_condense_status_fn
    _scribe_call = scribe_call_fn
    _find_transcript_file = find_transcript_file_fn
    _parse_transcript_messages = parse_transcript_messages_fn
    _recent_claude_transcripts = recent_claude_transcripts_fn
    _session_too_large = session_too_large_fn
    _long_session_advisory = long_session_advisory_fn
    _resume_is_fragile = resume_is_fragile_fn
    _encode_project_path = encode_project_path_fn
    _extract_transcript_telemetry = extract_transcript_telemetry_fn
    _proc_identity = proc_identity_fn
    _persist_pid_ledger = persist_pid_ledger_fn
    # Moved module-level side effect (see the tombstone in the provider-env
    # section below): hydrate persisted provider env vars into os.environ now
    # that PROVIDER_ENV_PATH is bound. Runs during server.py module exec,
    # before app.run() and before any agent spawn — timing-equivalent.
    _hydrate_provider_env_into_os()

# ── Claude CLI binary resolution ────────────────────────────────────────────
# Delegates to ClaudeRuntime.resolve_binary_str() — single source of truth.
# The full resolution logic lives in agent_runtime.ClaudeRuntime.resolve_binary()
# and handles the Windows .exe-vs-.cmd orphan case + common fallback paths.
# Re-resolved on each call (cheap) so a Claude install after server startup
# is picked up without restart.
def _resolve_claude():
    """Return absolute path to the claude executable, or 'claude' as last
    resort (will then FileNotFoundError, matching prior behavior).
    Delegates to ClaudeRuntime.resolve_binary_str() — single source of truth."""
    return _agent_runtime.get_runtime('claude').resolve_binary_str()  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)


def _claude(*args):
    """Build a subprocess command list with claude resolved to its full path."""
    return [_resolve_claude(), *args]


def _pid_is_alive(pid):
    """Check if a PID is alive. Works reliably on both Windows and Unix."""
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_pid(pid, tree=False):
    """Kill a process by PID. Works reliably on both Windows and Unix.
    If tree=True, also kills all child processes (Windows: taskkill /T)."""
    if sys.platform == 'win32':
        try:
            cmd = ['taskkill', '/F']
            if tree:
                cmd.append('/T')
            cmd.extend(['/PID', str(pid)])
            subprocess.run(cmd, capture_output=True, timeout=10,
                           creationflags=_POPEN_FLAGS)
            return True
        except Exception:
            return False
    else:
        if tree:
            # Kill process group if possible
            try:
                os.killpg(os.getpgid(pid), 9)
                return True
            except OSError:
                pass
        try:
            os.kill(pid, 9)
            return True
        except OSError:
            return False


def _hide_process_windows(pid):
    """Hide any console windows created by a process (Windows only)."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _):
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == pid:
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
            return True

        user32.EnumWindows(_cb, 0)
    except Exception:
        pass


def _hide_windows_delayed(pid):
    """Hide windows after a short delay to catch late-created consoles."""
    import time
    for _ in range(5):
        time.sleep(0.3)
        _hide_process_windows(pid)
    # One final check after a longer wait
    time.sleep(1)
    _hide_process_windows(pid)

# Global incognito pseudo-project. Lives at data/projects/_incognito.json with
# `_is_incognito_project: True`. All sessions dispatched into it are forced
# incognito. Auto-created on first use.
INCOGNITO_PROJECT_ID = '_incognito'


def _ensure_incognito_project():
    """Lazily create the global incognito project record + workspace folder.

    Returns the project dict (loaded fresh from disk on each call so callers
    see any updates the user has made, e.g. renamed it).
    """
    fp = DATA_DIR / f'{INCOGNITO_PROJECT_ID}.json'
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding='utf-8'))
        except Exception:
            pass
    base = Path(state.CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
    workspace = base / '_incognito'
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    rec = {
        'id': INCOGNITO_PROJECT_ID,
        'name': 'Incognito',
        'emoji': '\U0001F576️',  # detective/sunglasses face
        'description': 'Ephemeral scratch space. Sessions here skip MEMORY.md, '
                       'AGENT_RULES.md, and the agent log. Useful for one-off '
                       'questions you do not want polluting a project.',
        'project_path': str(workspace),
        'status': 'active',
        'domain': 'general',
        'activity_log': [],
        'backlog': [],
        'current_task': '',
        'next_action': '',
        '_is_incognito_project': True,
        'last_updated': now_iso() if 'now_iso' in globals() else datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    try:
        fp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass
    return rec

# Built-in re-declaration for the engram memory plugin. engram is a *plugin*
# (settings.json enabledPlugins), NOT an mcpServers entry — so --strict-mcp-config
# drops it along with everything else. Re-declaring it here lets a trimmed project
# keep memory. The `engram` binary is on PATH, so this spec is stable across
# plugin-version bumps (verified 2026-06-03 against CC 2.1.158: this exact spec
# restores mcp__engram__* under --strict-mcp-config). Mirrors the plugin's own
# .mcp.json.
_ENGRAM_MCP_SPEC = {'command': 'engram', 'args': ['mcp', '--tools=agent']}


def _mcp_server_catalog(project):
    """Merge every MCP server this project *could* load into {name: spec}.

    Sources, later overriding earlier on name collision:
      1. global  ~/.claude.json  → mcpServers
      2. project <project_path>/.mcp.json → mcpServers
      3. built-in `engram` re-declaration (so trimming can keep memory)

    Best-effort: an unreadable / malformed source is skipped, never raised."""
    catalog = {}
    try:
        gp = Path.home() / '.claude.json'
        if gp.exists():
            g = json.loads(gp.read_text(encoding='utf-8'))
            for k, v in (g.get('mcpServers') or {}).items():
                if isinstance(v, dict):
                    catalog[k] = v
    except Exception:
        pass
    try:
        ppath = (project or {}).get('project_path')
        if ppath:
            mp = Path(ppath) / '.mcp.json'
            if mp.exists():
                pj = json.loads(mp.read_text(encoding='utf-8'))
                for k, v in (pj.get('mcpServers') or {}).items():
                    if isinstance(v, dict):
                        catalog[k] = v
    except Exception:
        pass
    catalog.setdefault('engram', dict(_ENGRAM_MCP_SPEC))
    return catalog


def _resolve_project_mcp_config(project):
    """Per-project MCP trimming → a `--mcp-config` JSON string, or None.

    None → the project did NOT opt in (`enabled_mcp_servers` absent or not a
           list): no flags are emitted and the session inherits the full
           global+project+plugin fleet, byte-identical to pre-trim behavior.
           This is the default-off invariant — most projects hit this path.
    str  → opt-in. A JSON `{"mcpServers": {…}}` naming exactly the selected
           servers, paired by build_command with `--strict-mcp-config`. An
           empty selection → `{"mcpServers": {}}` (loads nothing — a valid
           maximal trim). Names not in the catalog are logged and skipped.

    Best-effort: any failure returns None (fail-open to the full fleet) — MCP
    trimming must never break a dispatch."""
    try:
        sel = (project or {}).get('enabled_mcp_servers')
        if not isinstance(sel, list):
            return None  # not opted in → unchanged behavior
        catalog = _mcp_server_catalog(project)
        chosen = {}
        for name in sel:
            if name in catalog:
                chosen[name] = catalog[name]
            else:
                _log(f"[mcp-trim] {(project or {}).get('id', '?')}: unknown MCP "
                     f"server '{name}' skipped (catalog: {sorted(catalog)})",
                     level='warn')
        return json.dumps({'mcpServers': chosen})
    except Exception as e:
        _log(f"[mcp-trim] resolve failed ({e!r}); inheriting full fleet",
             level='warn')
        return None

def _build_claude_flags(project=None, streaming=False, model_override=None,
                        effort_override=None):
    """Build common Claude CLI flags from config, with optional per-project overrides.
    Delegates to ClaudeRuntime.build_command()[1:] — single source of truth.
    Returns flags only (no binary prefix), matching the legacy contract.

    `model_override` lets a caller force a specific model (e.g. picked by the
    auto-router via _resolve_dispatch_model). Pass-through when None — existing
    callers that don't know about routing keep their original behavior.

    `effort_override` is the same idea for reasoning effort, used by an agent
    type that pins one (docs/AGENT_TYPES_DESIGN.md §3). Kept a separate arg
    rather than read off `project` because the character is not the project.
    """
    model = model_override or (
        (project or {}).get('agent_model', '') or state.CONFIG.get('agent_model', '')
    )
    effort = (effort_override
              or (project or {}).get('agent_effort', '')
              or state.CONFIG.get('agent_effort', ''))
    return _agent_runtime.get_runtime('claude').build_command(
        model=model,
        max_turns=state.CONFIG.get('agent_max_turns', 0),
        streaming=streaming,
        perm_mode=state.CONFIG.get('agent_permission_mode', ''),
        channels=(project or {}).get('agent_channels', '') or state.CONFIG.get('agent_channels', ''),
        remote_control=bool(
            (project or {}).get('agent_remote_control', False) or
            state.CONFIG.get('agent_remote_control', False)
        ),
        effort=effort,  # pyright: ignore[reportCallIssue]  # moved-verbatim typing debt (1.12)
        mcp_config_json=_resolve_project_mcp_config(project) or '',  # pyright: ignore[reportCallIssue]  # moved-verbatim typing debt (1.12)
        partial_messages=bool(state.CONFIG.get('activity_states_enabled', False)),  # pyright: ignore[reportCallIssue]
    )[1:]  # strip binary — _build_claude_flags() contract is flags-only


def _resolve_dispatch_model(project, prompt):
    """Pick the model to use for a user-facing dispatch, given the prompt.

    Returns (model_name, source) where source is one of:
      'manual'   — auto-router off; using the configured/project model verbatim
      'auto'     — classifier ran and picked this model
      'fallback' — classifier ran but errored; using the configured model

    Caller threads `model_name` into _build_claude_flags via `model_override`
    and surfaces a per-bubble pill when source != 'manual'. Side-effect free.

    Synchronous path. For parallel classification overlapping with
    `_build_agent_context`, use `_dispatch_with_routing_parallel`.
    """
    fallback = (project or {}).get('agent_model', '') or state.CONFIG.get('agent_model', '') or 'sonnet'
    if not prompt or not state.CONFIG.get('auto_model_enabled', False):
        return fallback, 'manual'
    return _route_dispatch_model(prompt, fallback)


# Background pool for parallel classifier calls during dispatch. Keeps the
# router off the dispatch thread's critical path so context build and
# classification overlap (see docs/DISPATCH_AND_ROUTING_ANALYSIS.md §B.3.b).
# Daemon threads so the pool never blocks shutdown.
_classifier_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix='router-cls'
)


def _dispatch_with_routing(project, prompt, streaming=False, effort_override=None):
    """One-shot helper: resolve model + build flags.

    Returns (model, source, flags). Caller stamps session['model'] and
    session['model_source'] for SSE emission. Used by dispatch sites that
    don't have a separate expensive context-build step to parallelize with.
    """
    model, source = _resolve_dispatch_model(project, prompt)
    flags = _build_claude_flags(project, streaming=streaming, model_override=model,
                                effort_override=effort_override)
    return model, source, flags


def _dispatch_with_routing_parallel(project, prompt, context_builder, streaming=False,
                                    effort_override=None):
    """Same as `_dispatch_with_routing` but runs `context_builder` in parallel
    with the classifier when the router is on.

    `context_builder` is a zero-arg callable returning the context string
    (typically `lambda: _build_agent_context(p, incognito=…, task=…)`). Net
    latency add when router is on: ~0–500 ms (classifier vs. context overlap)
    instead of 600–1500 ms serially.

    Returns (model, source, flags, context). Caller stamps session fields.
    When router is off or prompt is empty, runs context_builder synchronously
    and short-circuits to the manual model.
    """
    if not state.CONFIG.get('auto_model_enabled', False) or not prompt:
        context = context_builder() if context_builder else ''
        model, source, flags = _dispatch_with_routing(project, prompt, streaming=streaming,
                                                      effort_override=effort_override)
        return model, source, flags, context, ''

    fallback = (project or {}).get('agent_model', '') or state.CONFIG.get('agent_model', '') or 'sonnet'
    fut = _classifier_pool.submit(_route_dispatch_model, prompt, fallback)
    context = context_builder() if context_builder else ''
    timeout = max(1, int(state.CONFIG.get('auto_model_classifier_timeout_secs', 8) or 8))
    _fallback_reason = ''
    try:
        model, source = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        model, source = fallback, 'fallback'
        _fallback_reason = 'timeout'
    except Exception as _exc:
        model, source = fallback, 'fallback'
        _fallback_reason = type(_exc).__name__
    flags = _build_claude_flags(project, streaming=streaming, model_override=model,
                                effort_override=effort_override)
    return model, source, flags, context, _fallback_reason


def _sysprompt_file_args(context):
    """Return (cli_args, tmp_path) for passing a system prompt via a temp file.

    On Windows, npm-installed `claude.cmd` is invoked through cmd.exe, which
    enforces an 8191-char command-line cap (vs. CreateProcess's 32 KB). A
    multi-KB context (CLAYRUNE_API_REFERENCE + rules + read-floor + recent
    activity) passed inline as `--append-system-prompt <context>` blows past
    that cap and the spawn fails with "The command line is too long" + rc=1.

    The hidden `--append-system-prompt-file <path>` flag is exactly the
    escape hatch — Claude CLI supports it (verified locally; absent from
    `claude --help` but documented in `--bare`'s help text). The temp file
    is created here; the caller MUST call _sysprompt_cleanup(path, proc)
    right after Popen so the file is deleted when the process exits.

    Returns ([], None) for empty/missing context so callers can splat
    unconditionally.
    """
    if not context:
        return [], None
    import tempfile
    fd, path = tempfile.mkstemp(prefix='clayrune-sysprompt-', suffix='.txt')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(context)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return ['--append-system-prompt-file', path], path


def _context_fingerprint(project):
    """Cheap signature of everything `_build_agent_context` reads that can
    change while a session is alive. Empty string = could not compute.

    Deliberately mtime+size rather than content hashes: this runs on every
    respawn, and stat-ing ~60 paths is microseconds where reading a 1 MB vault
    is not. The failure mode of mtime is a same-second same-size edit, which
    costs one stale turn, not a wrong answer.
    """
    import hashlib
    h = hashlib.sha1()
    def stamp(p):
        try:
            st = os.stat(p)
            h.update(f'{p}|{st.st_mtime_ns}|{st.st_size}|'.encode('utf-8', 'replace'))
        except OSError:
            h.update(f'{p}|-|'.encode('utf-8', 'replace'))
    def listing(d):
        try:
            for n in sorted(os.listdir(d)):
                stamp(os.path.join(d, n))
        except OSError:
            h.update(f'{d}|-|'.encode('utf-8', 'replace'))

    pp = (project or {}).get('project_path') or ''
    try:
        from mc import memory as _mem
        mp = _mem._get_memory_path(project)
        if mp:
            # The index and the working state, which every prompt carries.
            stamp(str(mp))
            stamp(str(Path(mp).parent / 'continuity.md'))
            # Positions are a whole class of prompt content; the dir stamp
            # catches one being added, edited or forgotten.
            listing(str(Path(mp).parent))
    except Exception:
        pass
    if pp:
        stamp(os.path.join(pp, 'AGENT_RULES.md'))
        listing(os.path.join(pp, '.claude', 'skills'))
        listing(os.path.join(pp, '.claude', 'agents'))
    # The wired path, not a derived one: SHARED_RULES.md is read verbatim into
    # every agent's prompt on every project, so guessing its location here would
    # silently stop noticing edits to the single most widely-injected file.
    try:
        stamp(str(SHARED_RULES_PATH))
    except Exception:
        pass
    try:
        import skills as _sk
        from mc import characters as _ch
        listing(str(_sk.GLOBAL_SKILLS_DIR))
        listing(str(_ch.GLOBAL_AGENTS_DIR))
    except Exception:
        pass
    # Config values that reach the prompt directly.
    for k in ('agent_name', 'user_name', 'read_floor_topk',
              'read_floor_link_expand', 'sticky_agent_settings'):
        h.update(f'{k}={state.CONFIG.get(k)}|'.encode('utf-8', 'replace'))
    return h.hexdigest()[:16]


def _session_character_parts(project, session):
    """(body, agent_name, skills) for the persona a session is running.

    Re-read from disk rather than trusted from the session dict, so an EDITED
    persona takes effect on the next rebuild instead of never. Returns empty
    parts for a session that deliberately has no persona.
    """
    ch = (session or {}).get('character') or {}
    if not (isinstance(ch, dict) and ch.get('name')):
        return '', '', []
    pp = (project or {}).get('project_path') or ''
    meta, body = _resolve_character(
        pp, f"{ch.get('scope') or 'global'}:{ch['name']}", project)
    return body, (meta or ch).get('agent_name') or '', (meta or ch).get('skills') or []


def _fresh_context_for(project, session, task=''):
    """`_build_agent_context` for a session that is being RESPAWNED FRESH.

    The `-r` branches go through `_respawn_sysprompt_args`, which reproduces the
    session's own persona. The start-fresh branches beside them used to call
    `_build_agent_context(p, task=…)` bare — which drops the persona, so a chat
    that hit one came back under the project's plain agent name. Ron started a
    chat with Dave, left it, resumed, and was answered by Vector.

    Also carries `incognito`: an incognito session that rebuilt its context the
    bare way had MEMORY and rules injected back into it, which is the one thing
    incognito exists to prevent.
    """
    body, name, sk = '', '', []
    try:
        body, name, sk = _session_character_parts(project, session)
    except Exception as e:
        _log(f"[respawn] persona lookup failed on fresh rebuild: {e}")
    return _build_agent_context(
        project, incognito=bool((session or {}).get('incognito')), task=task,
        character_body=body, character_name=name,
        session_id=(session or {}).get('session_id', ''),
        character_skills=sk, source=(session or {}).get('source', ''))


def _respawn_sysprompt_args(session, project, task=''):
    """Sysprompt args for a `-r` / `--continue` respawn of an existing session.

    CLI ≥2.1.206 rebuilds the system prompt from flags on EVERY invocation:
    a resume that omits --append-system-prompt-file silently LOSES the whole
    injected context (rules, memory read-floor, API reference, character).
    See memory discovery-claude-resume-ignores-append-system-prompt (reversed
    2026-07-11) — the old "resume restores the original prompt" rule is false.

    INVALIDATE ON CHANGE, not rebuild every turn. The stash exists because
    byte-identical content keeps the resumed prefix cache-friendly, and
    rebuilding on every turn throws that away on every turn for nothing. But
    reusing it unconditionally froze a long-lived chat's rules, memory index,
    roster and skill list at the moment it spawned — forever. So the stash is
    kept while `_context_fingerprint` holds and dropped the moment it moves.

    A rebuild reproduces the session's own persona and id rather than a generic
    context: without that, a resumed persona chat that ever rebuilt would
    silently lose its persona mid-conversation. Re-reading the persona's body
    from disk also means an EDITED persona takes effect next turn rather than
    never. Best-effort throughout: a rebuild failure degrades to reusing the
    stash, and a missing stash to a context-less resume — never a blocked turn.
    """
    context = (session or {}).get('_system_prompt') or ''
    fp = ''
    try:
        fp = _context_fingerprint(project)
    except Exception as e:
        _log(f"[respawn] fingerprint failed, reusing stash: {e}")
    stale = bool(fp) and (session or {}).get('_system_prompt_fp') != fp

    if context and not stale:
        return _sysprompt_file_args(context)

    try:
        body, name, sk = _session_character_parts(project, session)
        rebuilt = _build_agent_context(
            project, incognito=bool((session or {}).get('incognito')),
            task=task, character_body=body, character_name=name,
            session_id=(session or {}).get('session_id', ''),
            character_skills=sk)
    except Exception as e:
        _log(f"[respawn] sysprompt rebuild failed: {e}")
        # A stale context beats no context: the alternative is a turn with no
        # rules and no memory at all.
        return _sysprompt_file_args(context) if context else ([], None)

    if session is not None:
        session['_system_prompt'] = rebuilt
        session['_system_prompt_fp'] = fp
        if stale and context:
            _log(f"[respawn] context refreshed for {session.get('session_id')} "
                 f"— project config changed since spawn")
    return _sysprompt_file_args(rebuilt)


def _sysprompt_cleanup(path, proc):
    """Schedule deletion of the temp system-prompt file when `proc` exits.

    Daemon thread; survives until the process actually terminates so we
    never delete the file out from under claude.cmd mid-startup. No-op if
    `path` is None (empty context).
    """
    if not path:
        return
    def _wait_and_unlink():
        try:
            proc.wait()
        except Exception:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
    threading.Thread(target=_wait_and_unlink, daemon=True,
                     name='sysprompt-cleanup').start()

# ── Agent session tracking ───────────────────────────────────────────────────
# agent_sessions moved to mc/state.py (Phase 0).


# ── Per-project agent isolation ──────────────────────────────────────────────
# Each project gets its own ProjectAgentManager with its own lock and guardian.
# A hung kill or slow operation in one project cannot block any other project,
# because no lock is ever shared across project_ids.
class ProjectAgentManager:
    def __init__(self, project_id):
        self.project_id = project_id
        self.lock = threading.RLock()
        self.session_ids = set()  # session_ids belonging to this project
        self._guardian_thread = None
        self._guardian_stop = threading.Event()

    def add_session(self, session_id):
        with self.lock:
            self.session_ids.add(session_id)

    def remove_session(self, session_id):
        with self.lock:
            self.session_ids.discard(session_id)

    def iter_sessions(self):
        """Snapshot of (sid, session) tuples for this project. Briefly takes self.lock."""
        with self.lock:
            ids = list(self.session_ids)
        out = []
        for sid in ids:
            s = agent_sessions.get(sid)
            if s is not None:
                out.append((sid, s))
        return out

    def ensure_guardian(self):
        """Lazy-start this project's guardian thread on first use."""
        with self.lock:
            if self._guardian_thread is not None and self._guardian_thread.is_alive():
                return
            t = threading.Thread(
                target=_project_guardian_loop,
                args=(self,),
                daemon=True,
                name=f'guardian-{self.project_id[:12]}',
            )
            self._guardian_thread = t
            t.start()

    def shutdown(self):
        self._guardian_stop.set()


# _managers / _managers_lock moved to mc/state.py (Phase 0).


def get_manager(project_id):
    """Get or create the ProjectAgentManager for a project. Cheap to call."""
    with _managers_lock:
        m = _managers.get(project_id)
        if m is None:
            m = ProjectAgentManager(project_id)
            _managers[project_id] = m
    return m


# _mem_write_locks(+guard) + _get_mem_write_lock moved to mc/state.py;
# _atomic_write_text moved to mc/core.py (Phase 0). SPEC §3.A.MID lock
# ordering + atomicity rationale documented at the definitions.


# _harden_secret_perms moved to mc/core.py (step 1.1).


def _live_agent_count(project_id):
    """Non-housekeeping sessions currently running/idle for a project."""
    try:
        return sum(1 for _sid, s in get_manager(project_id).iter_sessions()
                   if s.get('status') in ('running', 'idle')
                   and not s.get('housekeeping'))
    except Exception:
        return 0


def _maybe_isolate_worktree(project, session_id, incognito=False):
    """Resolve the working directory for a new agent — the shared project tree,
    or a private git worktree when this is a CONCURRENT agent.

    Returns (cwd, isolated). Best-effort by design: ANY failure falls back to
    the shared tree (today's behavior), so isolation can never break a dispatch.

    Only the 2nd+ concurrent agent is isolated. A project with one agent — the
    overwhelmingly common case — takes the identical code path it always has,
    which is what keeps this safe to enable globally. Backlog b264200a; design
    docs/COORDINATION_LAYER_DESIGN.md §5d.
    """
    pp = project.get('project_path', '')
    if incognito:
        return pp, False
    if not state.CONFIG.get('worktree_isolation_enabled', False):
        return pp, False
    if project.get('worktree_isolation') is False:  # per-project opt-out
        return pp, False
    try:
        if _live_agent_count(project.get('id', '')) < 1:
            return pp, False  # first agent — no sibling to collide with
        ok, path = _agent_worktree.create(project, session_id)
        if not ok:
            _log(f"[worktree] isolation unavailable, using shared tree: {path}")
            return pp, False
        # Cosmetic logging gets its OWN guard: a failure here must never
        # discard a worktree we already built successfully (that bug sent an
        # isolated agent back to the shared tree — silently un-isolating it).
        try:
            _log_agent_activity(project.get('id', ''),
                                f'Agent isolated in worktree: {session_id[:8]}')
        except Exception as e:
            _log(f"[worktree] activity log failed (non-fatal): {e}")
        return path, True
    except Exception as e:
        _log(f"[worktree] isolation failed, using shared tree: {e}")
        return pp, False


def _worktree_merge_back_on_end(session):
    """Merge an isolated agent's committed work back into the base branch when
    its session ends. Without this the work sits stranded on the agent branch
    and isolation silently becomes work loss. Best-effort; on conflict the
    branch is preserved and the user is told."""
    if not session or not session.get('_worktree_isolated'):
        return
    sid = session.get('session_id', '')
    pid = session.get('project_id', '')
    try:
        p = load_project(pid)
        if not p:
            return
        status, detail = _agent_worktree.merge_back(p, sid)
        if status == 'clean':
            _agent_worktree.remove(p, sid, delete_branch=True)
        elif status in ('conflict', 'dirty'):
            # Preserve everything and surface it — never auto-resolve, never
            # delete work.
            _log_agent_activity(
                pid, f'Agent worktree needs manual merge ({status}): '
                     f'branch {_agent_worktree.branch_name(sid)} — {detail[:120]}')
        elif status == 'nothing':
            _agent_worktree.remove(p, sid, delete_branch=True)
    except Exception as e:
        _log(f"[worktree] merge-back failed for {sid[:12]}: {e}")


def get_manager_for_session(session_id):
    """Find the manager that owns a given session. Returns None if not tracked."""
    s = agent_sessions.get(session_id)
    if not s:
        return None
    pid = s.get('project_id')
    if not pid:
        return None
    return get_manager(pid)


def all_managers():
    """Snapshot of all current managers. The dict lock is held only for the copy."""
    with _managers_lock:
        return list(_managers.values())


def _project_guardian_loop(manager):
    """Per-project guardian loop. One thread per ProjectAgentManager.

    Iterates only this project's sessions. A hung kill or slow check in this
    project cannot affect any other project — there is no shared lock.
    """
    while not manager._guardian_stop.is_set() and not _guardian_stop.is_set():
        if manager._guardian_stop.wait(GUARDIAN_CHECK_INTERVAL):
            break
        if _guardian_stop.is_set():
            break
        now = _time.time()
        # Snapshot under this project's lock only — never global.
        snapshots = []
        with manager.lock:
            for sid in list(manager.session_ids):
                session = agent_sessions.get(sid)
                if session is None:
                    continue
                if session['status'] in ('completed', 'stopped'):
                    continue
                if session.get('housekeeping'):
                    continue
                snapshots.append((sid, session))
        for sid, session in snapshots:
            try:
                _guardian_check_session(sid, session, now)
            except Exception as e:
                _log(f"[guardian:{manager.project_id[:8]}] Error checking {sid[:8]}: {e}")

def _register_process(proc, name, proc_type, session_id, project_id, command_preview=''):
    """Register a spawned process in the PID tracker."""
    project_name = project_id
    try:
        p = load_project(project_id)
        if p:
            project_name = p.get('name', project_id)
    except Exception:
        pass
    _img, _ct = _proc_identity(proc.pid)
    with process_tracker_lock:
        tracked_processes[proc.pid] = {
            'pid': proc.pid,
            'name': name,
            'type': proc_type,
            'session_id': session_id,
            'project_id': project_id,
            'project_name': project_name,
            'command_preview': (command_preview or '')[:80],
            'started_at': now_iso(),
            'os_image': _img,
            'create_time': _ct,
            'proc': proc,
        }
    _persist_pid_ledger()


def _unregister_process(pid):
    """Remove a process from the PID tracker."""
    with process_tracker_lock:
        tracked_processes.pop(pid, None)
    _persist_pid_ledger()

@bp.route('/api/router/stats', methods=['GET'])
def get_router_stats_aggregate():
    """Cross-project auto-router counters. Sums totals and by_pair across
    every project's _router_stats.json. Surfaces last_fallback as the most
    recent across projects. Read-only; never mutates state.

    Response shape:
      {
        "totals": {"manual": N, "auto": N, "fallback": N},
        "by_pair": {"opus->haiku": N, ...},
        "last_fallback": {"ts": "...Z", "reason": "...", "project_id": "..."},
        "projects": N            # how many had a stats file
      }
    """
    agg_totals = {}
    agg_by_pair = {}
    last_fb = None
    project_count = 0
    for f in DATA_DIR.glob('*_router_stats.json'):
        try:
            data = json.loads(f.read_text(encoding='utf-8') or '{}')
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        project_count += 1
        for k, v in (data.get('totals') or {}).items():
            agg_totals[k] = int(agg_totals.get(k, 0)) + int(v or 0)
        for k, v in (data.get('by_pair') or {}).items():
            agg_by_pair[k] = int(agg_by_pair.get(k, 0)) + int(v or 0)
        fb = data.get('last_fallback')
        if isinstance(fb, dict) and fb.get('ts'):
            if last_fb is None or fb['ts'] > last_fb.get('ts', ''):
                # Derive project_id from the filename suffix-strip.
                pid = f.name[:-len('_router_stats.json')]
                last_fb = {**fb, 'project_id': pid}
    return jsonify({
        'totals': agg_totals,
        'by_pair': agg_by_pair,
        'last_fallback': last_fb,
        'projects': project_count,
    })

# ── Agent image upload ────────────────────────────────────────────────────────

# Anthropic's many-image request limit: each image's long edge must be ≤ 2000px,
# else the API drops it with "an image in the conversation could not be
# processed and was removed." Shrinking on save keeps the original aspect ratio
# and saves the agent from tripping the limit later in the conversation.
_UPLOAD_IMAGE_MAX_EDGE = 2000


def _downscale_image_if_huge(path: Path) -> dict:
    """Resize an on-disk image to ≤ _UPLOAD_IMAGE_MAX_EDGE on the long edge.

    Best-effort: returns {'resized': bool, 'original': (w,h), 'final': (w,h)}
    on success, or {'resized': False, 'error': str} if Pillow is missing,
    the file isn't a recognized image, or the resize fails. Never raises —
    the upload itself must not fail because of an opportunistic shrink.
    """
    try:
        from PIL import Image  # local import: Pillow is optional
    except ImportError:
        return {'resized': False, 'error': 'pillow_missing'}
    try:
        with Image.open(path) as im:
            orig = im.size
            w, h = orig
            if max(w, h) <= _UPLOAD_IMAGE_MAX_EDGE:
                return {'resized': False, 'original': orig, 'final': orig}
            im.thumbnail((_UPLOAD_IMAGE_MAX_EDGE, _UPLOAD_IMAGE_MAX_EDGE),
                         Image.LANCZOS)  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
            final = im.size
            # Preserve original format (PNG stays PNG, JPEG stays JPEG, …)
            save_kwargs = {}
            fmt = (im.format or '').upper()
            if fmt in ('JPEG', 'JPG'):
                save_kwargs['quality'] = 90
                save_kwargs['optimize'] = True
            elif fmt == 'PNG':
                save_kwargs['optimize'] = True
            im.save(path, **save_kwargs)
            return {'resized': True, 'original': orig, 'final': final}
    except Exception as e:
        return {'resized': False, 'error': f'{type(e).__name__}: {e}'}


@bp.route('/api/agent/upload-image', methods=['POST'])
def agent_upload_image():
    """Save a pasted image and return its absolute path for agent consumption."""
    if 'file' not in request.files:
        return jsonify({'error': 'no file'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'empty filename'}), 400
    # P2-2: per-file cap only — this endpoint has no project context for a
    # cumulative quota. 0 = unlimited (default).
    max_file = _upload_limit(None, 'upload_max_file_bytes')
    if max_file:
        incoming = _incoming_file_size(f)
        if incoming > max_file:
            return jsonify({'error': 'file too large',
                            'limit_bytes': max_file,
                            'file_bytes': incoming}), 413
    ext = Path(f.filename).suffix.lower() or '.png'
    stored_name = f'agent_{uuid.uuid4().hex[:10]}{ext}'
    dest = UPLOADS_DIR / stored_name
    f.save(str(dest))
    resize_info = _downscale_image_if_huge(dest)
    resp = {'ok': True, 'path': str(dest.resolve())}
    if resize_info.get('resized'):
        resp['resized_from'] = list(resize_info['original'])
        resp['resized_to'] = list(resize_info['final'])
    return jsonify(resp)

# ── Claude CLI auth-status tracking ───────────────────────────────────────────
# Sentinels emitted by `claude` when the user isn't logged in or the stored
# OAuth/api credentials are stale. Detected on every agent-stream line; a hit
# flips a global flag the dashboard polls so we can surface a "sign in" banner
# instead of letting the user stare at a silent 401.
import re as _re_auth
_AUTH_ERROR_PATTERNS = [
    (_re_auth.compile(r'please\s+run\s*/login', _re_auth.I), 'not_logged_in'),
    (_re_auth.compile(r'not\s+logged\s+in', _re_auth.I), 'not_logged_in'),
    (_re_auth.compile(r'invalid\s+(?:api\s+)?key', _re_auth.I), 'invalid_api_key'),
    (_re_auth.compile(r'(?:oauth\s+)?(?:access\s+)?token\s+(?:has\s+)?expired', _re_auth.I), 'not_logged_in'),
    (_re_auth.compile(r'\bunauthorized\b', _re_auth.I), 'invalid_api_key'),
    (_re_auth.compile(r'authentication_error', _re_auth.I), 'invalid_api_key'),
    # 401 only in an error/auth context — bare "401" in agent prose must not trip it.
    (_re_auth.compile(r'(?:error|status|http)[^0-9]{0,8}401\b', _re_auth.I), 'invalid_api_key'),
    (_re_auth.compile(r'\b401\b[^0-9]{0,20}(?:unauthorized|authentication|oauth|api\s*key)', _re_auth.I), 'invalid_api_key'),
]
# "Reached max turns" — an OPERATIONAL (not auth) non-zero exit. Reaching the
# turn limit proves the CLI authenticated and the model actually ran, so the
# auth probe treats it as a positive signal instead of failing closed.
_MAX_TURNS_RE = _re_auth.compile(r'reached\s+max\s+turns', _re_auth.I)
# _claude_auth_state / _claude_auth_lock moved to mc/state.py (Phase 0).


def _scan_for_auth_error(text):
    """Return the reason code for the first matching sentinel, or None."""
    if not text:
        return None
    for pat, reason in _AUTH_ERROR_PATTERNS:
        if pat.search(text):
            return reason
    return None


def _mark_claude_auth_error(reason, snippet):
    with _claude_auth_lock:
        _claude_auth_state['ok'] = False
        _claude_auth_state['reason'] = reason
        _claude_auth_state['last_error_text'] = (snippet or '')[:300]
        _claude_auth_state['detected_at'] = _time.time()


def _mark_claude_auth_ok():
    with _claude_auth_lock:
        _claude_auth_state['ok'] = True
        _claude_auth_state['reason'] = None
        _claude_auth_state['last_error_text'] = None
        _claude_auth_state['last_probe_at'] = _time.time()


def _scan_result_event_for_auth(msg):
    """Detect auth failure from a Mode-A/B `result` stream-json event.

    The line-level scanner (_scan_for_auth_error) runs ONLY on non-JSON stderr
    lines — deliberately, because regex-scanning every stream-json line made an
    agent's own assistant prose about auth ("the user might not be logged in")
    trip the banner. But an API 401 / expired-OAuth / invalid-key in Mode B
    arrives as a *structured* `result` event that parses as JSON, so it never
    reached that scanner and `_claude_auth_state` stayed at its optimistic
    `ok:True` default (root cause of "Settings says signed in, real run is 401").

    Keying on the structured `is_error` flag (not raw text) is what lets us
    scan safely: only genuine error envelopes are inspected, never prose. A
    clean success is a positive auth confirmation, so it clears stale errors.
    """
    if not isinstance(msg, dict):
        return
    subtype = str(msg.get('subtype') or '')
    if msg.get('is_error') or subtype.startswith('error'):
        text = str(msg.get('result') or msg.get('error') or '')
        reason = _scan_for_auth_error(text)
        if reason:
            _mark_claude_auth_error(reason, text)
    elif subtype == 'success' and not msg.get('is_error'):
        # Verified working auth — clears any stale error and stamps last_probe_at.
        _mark_claude_auth_ok()


# ── Multi-provider agent runtime — discovery endpoint ───────────────────────


def _cli_missing_message(provider: str = '') -> str:
    """Spawn-failure text for the runtime that ACTUALLY failed.

    A FileNotFoundError out of dispatch used to be reported as "Claude CLI not
    found. Install it with: npm install -g @anthropic-ai/claude-code" no matter
    which provider was picked — so a codex or gemini spawn failure sent the
    operator off installing the wrong CLI. Ask the runtime for its own hint.
    """
    name = (provider or '').strip().lower() or _agent_runtime.default_runtime_name()
    try:
        rt = _agent_runtime.get_runtime(name)
        label = rt.display_name or name
        hint = ''
        try:
            hint = rt.health_check().install_hint or ''
        except Exception:
            pass
    except Exception:
        label, hint = name, ''
    msg = f'{label} not found on this server (spawn failed).'
    if hint:
        msg += f' Install it with: {hint}'
    return msg


@bp.route('/api/agent/providers')
def agent_providers():
    """List all registered agent runtimes (claude + alternatives) with their
    install / capability state. The project-settings UI reads this to build
    the per-project provider dropdown.

    Returns: [{name, display_name, installed, version, install_hint,
               capabilities: {...}, default: bool}]
    """
    out = []
    default_name = _agent_runtime.default_runtime_name()
    for rt in _agent_runtime.available_runtimes():
        try:
            h = rt.health_check()
        except Exception as e:
            h = _agent_runtime.HealthStatus(
                installed=False, binary_path=None, version=None,
                auth_state=_agent_runtime.AuthState(status='unknown', last_checked=''),
                install_hint='', diagnostic=str(e),
            )
        try:
            caps = rt.capabilities()
            caps_dict = {
                'supports_mode_a': caps.supports_mode_a,
                'supports_mode_b': caps.supports_mode_b,
                'mode_b_kind': caps.mode_b_kind,
                'default_mode': caps.default_mode,
                'supports_session_resume': caps.supports_session_resume,
                'supports_mcp': caps.supports_mcp,
                'supports_skills': caps.supports_skills,
                'supports_plan_mode': caps.supports_plan_mode,
                'supports_ask_user_question': caps.supports_ask_user_question,
                'supports_streaming_text': caps.supports_streaming_text,
                'emits_usage': caps.emits_usage,
                'emits_cost': caps.emits_cost,
                'emits_num_turns': caps.emits_num_turns,
                'emits_rate_limit': caps.emits_rate_limit,
                'image_input': caps.image_input,
                'context_window': caps.context_window,
                'context_injection': caps.context_injection,
                'context_file_name': caps.context_file_name,
                'oneshot_supported': caps.oneshot_supported,
            }
        except Exception:
            caps_dict = {}
        # Model ids this runtime accepts. Provider-specific by nature (codex
        # takes gpt-*, opencode takes provider/model) — the composer's Model
        # picker rebuilds itself from this whenever the Agent picker changes.
        # Empty list = this CLI has no model flag → no picker.
        try:
            models = [{'id': mid, 'label': label}
                      for mid, label in rt.model_choices()]
        except Exception:
            models = []
        out.append({
            'name': rt.name,
            'display_name': rt.display_name,
            'models': models,
            'installed': h.installed,
            'binary_path': str(h.binary_path) if h.binary_path else None,
            'version': h.version,
            'install_hint': h.install_hint,
            'auth_status': h.auth_state.status if h.auth_state else 'unknown',
            'capabilities': caps_dict,
            'default': (rt.name == default_name),
        })
    return jsonify({'providers': out, 'default': default_name})


# ── Provider env-var storage (Gemini API key etc.) ──────────────────────────

# PROVIDER_ENV_PATH is wired by server.py (see wire()) — the 1.7
# SESSION_LABELS_PATH wired-placeholder pattern (declared above).
# _provider_env_lock moved to mc/state.py (Phase 0).


def _load_provider_env_file() -> Dict[str, Dict[str, str]]:
    if not PROVIDER_ENV_PATH.exists():
        return {}
    try:
        return json.loads(PROVIDER_ENV_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_provider_env_file(data: Dict[str, Dict[str, str]]) -> None:
    PROVIDER_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROVIDER_ENV_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    _harden_secret_perms(PROVIDER_ENV_PATH)


def _hydrate_provider_env_into_os() -> None:
    """Inject persisted provider env vars into os.environ so child agent
    processes inherit them. Shell-set vars win — we only fill blanks."""
    for _provider, kv in (_load_provider_env_file() or {}).items():
        if not isinstance(kv, dict):
            continue
        for k, v in kv.items():
            if k and v is not None and k not in os.environ:
                os.environ[k] = str(v)


# _hydrate_provider_env_into_os() is called from wire() — the module-level
# call moved there because PROVIDER_ENV_PATH is wire-time state (1.6/1.7
# pattern: module-level side effects move into wire(); still runs before
# app.run() / any spawn, so child-env inheritance timing is equivalent).


@bp.route('/api/agent/provider/<name>/auth')
def agent_provider_auth_status(name):
    """Re-probe one provider's install + auth state. Cheaper than the full
    /api/agent/providers list when the user just clicked Refresh on one row."""
    try:
        rt = _agent_runtime.get_runtime(name)
    except KeyError:
        return jsonify({'error': f'unknown provider {name}'}), 404
    try:
        h = rt.health_check()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({
        'name': name,
        'installed': h.installed,
        'version': h.version,
        'binary_path': str(h.binary_path) if h.binary_path else None,
        'auth_status': h.auth_state.status if h.auth_state else 'unknown',
        'auth_method': h.auth_state.method if h.auth_state else None,
        'auth_error_text': h.auth_state.error_text if h.auth_state else None,
        'install_hint': h.install_hint,
    })


@bp.route('/api/agent/provider/<name>/env', methods=['POST'])
def agent_provider_set_env(name):
    """Save and inject one env var for a provider. Body: {key, value}.

    Value is persisted to data/provider_env.json AND written to os.environ
    so the next agent dispatch picks it up without an MC restart. The key
    must look like a normal env-var name (paranoid filter — no PATH writes
    via this surface)."""
    if name not in _agent_runtime._RUNTIMES:
        return jsonify({'error': f'unknown provider {name}'}), 404
    body = request.get_json(force=True, silent=True) or {}
    key = (body.get('key') or '').strip()
    value = body.get('value')
    if not key or value is None:
        return jsonify({'error': 'key and value required'}), 400
    if not key.replace('_', '').isalnum() or not key[0].isalpha():
        return jsonify({'error': 'invalid env var name'}), 400
    # Allowlist: only credentials-flavored names. Stops the surface from
    # being used to clobber PATH / HOME / USERPROFILE / etc.
    SAFE_SUFFIXES = ('_API_KEY', '_TOKEN', '_KEY', '_SECRET',
                     '_PROFILE', '_REGION', '_CREDENTIALS', '_ENDPOINT')
    if not any(key.upper().endswith(s) for s in SAFE_SUFFIXES):
        return jsonify({
            'error': 'env var name not allowed — must end in _API_KEY, '
                     '_TOKEN, _KEY, _SECRET, _PROFILE, _REGION, '
                     '_CREDENTIALS, or _ENDPOINT',
        }), 400

    val_str = str(value)
    with _provider_env_lock:
        data = _load_provider_env_file()
        data.setdefault(name, {})[key] = val_str
        _save_provider_env_file(data)
    if val_str:
        os.environ[key] = val_str
    else:
        # Empty value = clear the override (let shell env take over).
        os.environ.pop(key, None)
    return jsonify({'ok': True, 'key': key})


@bp.route('/api/agent/provider/<name>/login-launch', methods=['POST'])
def agent_provider_login_launch(name):
    """Open the provider's CLI in a NEW OS terminal so the user can complete
    interactive login (OAuth flow for gemini, etc.). Same pattern as
    /api/claude/login-launch — needs a real TTY, not a piped subprocess.

    Preserved for backward compat; prefer /api/agent/<provider>/auth-login.
    """
    try:
        rt = _agent_runtime.get_runtime(name)
    except KeyError:
        return jsonify({'error': f'unknown provider {name}'}), 404
    bin_path = rt.resolve_binary()
    if not bin_path:
        return jsonify({'error': f'{name} CLI is not installed'}), 400
    err = _launch_terminal_for_binary(str(bin_path))
    if err:
        return jsonify({'error': err}), 500
    return jsonify({'ok': True})


def _auth_probe_cwd() -> str:
    """A scratch directory for `claude -p ok` to run in, so its transcript is
    written somewhere that is not a project. Created on demand; falls back to
    the temp dir, and finally to None (inherit) if even that fails — a probe
    that can't run is worse than one that leaves a stray file."""
    try:
        # DATA_DIR is data/projects — deliberately go to its PARENT. Anything
        # dropped inside DATA_DIR is treated as a project record by
        # load_projects() (the DATA_DIR-pollution rule in CLAUDE.md).
        d = Path(DATA_DIR).parent / '_auth_probe'
        d.mkdir(parents=True, exist_ok=True)
        # Absolute: the CLI derives its transcript directory name from the CWD,
        # so a relative path here would encode differently depending on where
        # the server was started and the prune could never find it again.
        return str(d.resolve())
    except Exception:
        try:
            return tempfile.gettempdir()
        except Exception:
            return None  # type: ignore[return-value]


def _prune_auth_probe_transcripts(keep: int = 3) -> None:
    """Keep the probe's own transcript dir from growing without bound.

    Redirecting the probe out of the project (above) fixed the pollution but
    moved the accumulation: a probe fires on page boot, so this is roughly
    6 x ~48 KB a day on an actively-used install — ~100 MB a year of files
    whose entire content is "ok" / "I'm here". They are MC's own throwaway
    output in a directory MC created, never user data, so pruning them is
    cache eviction rather than deletion of anything anyone wrote.

    Best-effort and never raises: failing to tidy up must not fail an auth
    check.
    """
    try:
        rt = _agent_runtime.get_runtime('claude')
        # Must be _encoded_dir_candidates, NOT _encode_project_path: MC's
        # primary encoding keeps underscores (`..._claude-mission-control...`)
        # while the CLI writes the dashed variant (`...--claude-...--auth-probe`).
        # The single-encoding version of this pruned a directory that does not
        # exist and reported success — verified against the real dir before and
        # after. `_encoded_dir_candidates` is the same helper list_sessions uses
        # for exactly this reason.
        root = Path.home() / '.claude' / 'projects'
        cwd = str(Path(_auth_probe_cwd()).resolve())
        for encoded in (rt._encoded_dir_candidates(cwd) or []):  # type: ignore[attr-defined]
            d = root / encoded
            if not d.is_dir():
                continue
            files = sorted(d.glob('*.jsonl'),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[keep:]:
                try:
                    f.unlink()
                except OSError:
                    pass
    except Exception as e:
        _log(f"[auth-probe] transcript prune failed: {e}")


def _run_claude_auth_probe() -> dict:
    """Run `claude -p ok` to actively probe auth and update _claude_auth_state.

    Extracted so both the legacy shim and the new generic /api/agent/claude/auth-probe
    share the same implementation. Returns a snapshot of _claude_auth_state.
    """
    try:
        cmd = [_resolve_claude(), '-p', 'ok', '--max-turns', '1']
        # RUN IT SOMEWHERE THAT ISN'T A PROJECT.
        #
        # The CLI writes a full transcript for every invocation into
        # ~/.claude/projects/<encoded CWD>/. With no cwd= this inherited the
        # SERVER's cwd — the Clayrune checkout, which is itself an MC project —
        # so every auth probe deposited a real .jsonl there. /conversations
        # reads transcripts directly, so each one surfaced in that project's
        # chat rail as a conversation labelled "ok" (turn 1: "ok", turn 2:
        # "I'm here — what would you like to work on?"), and the startup
        # backfill then synthesized an `interrupted` agent-log row for it.
        # Six had accumulated in one day. That is what "my conversation split
        # into several conversations" looked like from the outside.
        #
        # Auth state is global (~/.claude), so the probe has no reason to run in
        # a project directory at all. Point it at a dedicated throwaway dir and
        # its transcripts land under an encoded path that is nobody's project.
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=20,
            cwd=_auth_probe_cwd(),
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        combined = (result.stdout or '') + (result.stderr or '')
        reason = _scan_for_auth_error(combined)
        if reason:
            _mark_claude_auth_error(reason, combined)
        elif result.returncode == 0:
            _mark_claude_auth_ok()
        elif _MAX_TURNS_RE.search(combined):
            # Non-zero exit, but "Reached max turns" is a POSITIVE auth signal,
            # not a failure: the CLI authenticated, the model ran, and it simply
            # exhausted the 1-turn budget (e.g. it chose to call a tool, which
            # needs a 2nd turn). Marking this as an auth error latched the
            # "Authenticate Claude" banner ON for a fully signed-in user — the
            # exact false positive Ron hit, with last_error_text literally
            # "Error: Reached max turns (1)". Same `--max-turns 1 → rc=1`
            # brittleness the oneshot path already documents.
            _mark_claude_auth_ok()
        else:
            # Non-zero exit with no recognized sentinel — the CLI itself failed
            # the probe. Fail closed: an explicit "Check" must not leave a stale
            # ok:true reading "✓ Signed in". Record it as an unverified error.
            _mark_claude_auth_error(
                'unknown', combined or f'claude probe exited {result.returncode}')
    except subprocess.TimeoutExpired:
        with _claude_auth_lock:
            _claude_auth_state['last_probe_at'] = _time.time()
    except FileNotFoundError:
        _mark_claude_auth_error('cli_not_found', 'claude CLI not on PATH')
    except Exception:
        with _claude_auth_lock:
            _claude_auth_state['last_probe_at'] = _time.time()
    # Runs on every path, including the failure ones — a CLI that errored may
    # still have written its transcript before dying.
    _prune_auth_probe_transcripts()
    with _claude_auth_lock:
        return dict(_claude_auth_state)


def _launch_terminal_for_binary(bin_str: str) -> Optional[str]:
    """Open `bin_str` in a new OS terminal for interactive auth flows.

    Returns None on success or an error string on failure. Callers return 500
    when this is non-None. A real TTY is required because provider CLIs like
    claude use /login which refuses to run inside a piped subprocess.
    """
    try:
        if sys.platform == 'win32':
            subprocess.Popen(
                f'start "" cmd /k "\"{bin_str}\""',
                shell=True,
                creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0),
            )
        elif sys.platform == 'darwin':
            script = f'tell application "Terminal" to do script "{bin_str}"'
            subprocess.Popen(['osascript', '-e', script])
        else:
            for emu in ('x-terminal-emulator', 'gnome-terminal', 'konsole',
                        'xfce4-terminal', 'xterm'):
                if shutil.which(emu):
                    subprocess.Popen([emu, '-e', bin_str])
                    break
            else:
                return (f'No terminal emulator found. Run `{bin_str}` '
                        'manually in a terminal to sign in.')
        return None
    except Exception as e:
        return str(e)


# ── Remote/captured login — MC-927 URL-surfacing fallback ───────────────────
#
# _launch_terminal_for_binary (above) opens a NEW OS TERMINAL WINDOW on the
# host — over the tunnel, a remote user taps "log in" and nothing observable
# happens, because the window opened on a machine they aren't sitting at.
#
# Verified 2026-08-31: `claude auth login` prints its OAuth URL and reads the
# pasted code back over a PLAIN PIPED subprocess, no PTY required at all.
# Gemini does not — a piped, non-TTY `gemini` produces zero output before
# hanging, because its account-picker is an interactive TUI that needs
# raw-mode keyboard input to render in the first place. So this path is
# opt-in per runtime via `auth_login_argv()` (agent_runtime.py); providers
# that return None there keep the host-terminal button as their only option
# until MC-928 (a real PTY for the pop-out) ships.
_captured_login_lock = threading.Lock()
_captured_login_sessions: Dict[str, dict] = {}  # provider -> session dict

_LOGIN_URL_RE = re.compile(r'https?://\S+')


def _extract_login_url(text: str) -> Optional[str]:
    m = _LOGIN_URL_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip('.,)>\'"')


def _read_captured_login_stream(provider: str, proc, session: dict) -> None:
    """Reader thread: raw chunk-reads the login subprocess's merged
    stdout/stderr into session['output'], flipping status to 'url_ready' as
    soon as a URL appears.

    Chunk reads (not readline()) because `claude auth login` never terminates
    "Paste code here if prompted > " with a newline — a line-based reader
    would block there forever and never see the URL two lines above it.
    Mirrors terminal_routes._read_terminal_stream's approach for the same
    reason; kept separate rather than sharing that session store because this
    flow's semantics (single session per provider, URL extraction, no
    project/SSE bridging) don't fit the general terminal pop-out.
    """
    fd = proc.stdout.fileno()
    try:
        while True:
            with _captured_login_lock:
                if _captured_login_sessions.get(provider) is not session:
                    return
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            text = chunk.decode('utf-8', errors='replace')
            with _captured_login_lock:
                session['output'] += text
                if session['status'] == 'waiting_url':
                    url = _extract_login_url(session['output'])
                    if url:
                        session['url'] = url
                        session['status'] = 'url_ready'
    except Exception as e:
        with _captured_login_lock:
            session['output'] += f'\n[reader error: {e}]'
    finally:
        rc = proc.wait()
        with _captured_login_lock:
            if _captured_login_sessions.get(provider) is session:
                session['exit_code'] = rc


def _expire_captured_login(provider: str, session: dict) -> None:
    """Timer callback: kill a login subprocess nobody finished signing in to.

    Only touches PID this server spawned, and only if it's still the CURRENT
    session for `provider` — a retry that replaced it owns its own timer.
    """
    with _captured_login_lock:
        if _captured_login_sessions.get(provider) is not session:
            return
        if session.get('exit_code') is not None:
            return
    try:
        session['proc'].kill()
    except Exception:
        pass
    with _captured_login_lock:
        if _captured_login_sessions.get(provider) is session:
            del _captured_login_sessions[provider]


def _captured_login_status_payload(session: dict) -> dict:
    with _captured_login_lock:
        return {
            'ok': True,
            'remote_capable': True,
            'status': session['status'],
            'url': session.get('url'),
            'exit_code': session.get('exit_code'),
        }


@bp.route('/api/agent/<provider>/auth-login-remote', methods=['POST'])
def agent_auth_login_remote(provider):
    """Remote-friendly login: capture the OAuth URL from a piped subprocess
    instead of opening a host terminal window. Returns remote_capable:False
    (200, not an error) for providers whose CLI needs a real console AND no
    real-PTY backend is available — see the module comment above and
    AgentRuntime.auth_login_argv(). When a real PTY IS available (MC-928),
    those providers get a PTY-backed terminal pop-out session instead of a
    plain 'no' — that's the general fix the URL-capture path above was a
    stopgap for.
    """
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    bin_path = rt.resolve_binary()
    if not bin_path:
        return jsonify({'error': f'{provider} CLI is not installed'}), 400
    argv = rt.auth_login_argv(str(bin_path))
    if not argv:
        if pty_backend.pty_available():
            session_id, err = launch_pty_session(
                '_auth_probe', str(bin_path), cwd=_auth_probe_cwd())
            if err:
                return jsonify({'ok': False, 'remote_capable': False, 'error': err}), 200
            return jsonify({
                'ok': True,
                'remote_capable': True,
                'pty': True,
                'session_id': session_id,
                'command': str(bin_path),
            }), 200
        return jsonify({
            'ok': False,
            'remote_capable': False,
            'error': (f'{provider} needs a real console to sign in — its '
                      'interactive login can\'t be driven over a plain pipe, '
                      'and this machine has no real-PTY backend installed '
                      '(pip install pywinpty on Windows). '
                      'Use "Launch terminal login" on the host machine.'),
        }), 200

    with _captured_login_lock:
        existing = _captured_login_sessions.get(provider)
    if existing is not None and existing.get('exit_code') is None:
        # Already in flight — hand back what we have instead of spawning a
        # second CLI process to fight the first one for the same stdin.
        return jsonify(_captured_login_status_payload(existing))

    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=_auth_probe_cwd(),
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    session = {
        'proc': proc, 'status': 'waiting_url', 'url': None,
        'output': '', 'exit_code': None, 'started_at': _time.time(),
    }
    with _captured_login_lock:
        _captured_login_sessions[provider] = session
    threading.Thread(target=_read_captured_login_stream,
                      args=(provider, proc, session), daemon=True).start()
    timer = threading.Timer(600, _expire_captured_login, args=(provider, session))
    timer.daemon = True
    timer.start()

    # Give the CLI a short window to print its URL before responding —
    # observed 1-3s for `claude auth login`. The frontend polls the status
    # route below if we come back empty-handed (cold start / slow machine).
    deadline = _time.time() + 5
    while _time.time() < deadline:
        with _captured_login_lock:
            if session['status'] != 'waiting_url' or _captured_login_sessions.get(provider) is not session:
                break
        _time.sleep(0.15)

    return jsonify(_captured_login_status_payload(session))


@bp.route('/api/agent/<provider>/auth-login-remote/status')
def agent_auth_login_remote_status(provider):
    with _captured_login_lock:
        session = _captured_login_sessions.get(provider)
    if not session:
        return jsonify({'ok': False, 'remote_capable': True, 'status': 'none'}), 404
    return jsonify(_captured_login_status_payload(session))


@bp.route('/api/agent/<provider>/auth-login-remote/code', methods=['POST'])
def agent_auth_login_remote_code(provider):
    """Write a pasted OAuth code back into the waiting login subprocess's
    stdin. A wrong code leaves the CLI re-prompting rather than exiting
    (verified 2026-08-31 for claude), so callers may retry against the same
    session."""
    data = request.get_json() or {}
    code = (data.get('code') or '').strip()
    if not code:
        return jsonify({'error': 'code required'}), 400
    with _captured_login_lock:
        session = _captured_login_sessions.get(provider)
    if not session:
        return jsonify({'error': 'no login session in progress'}), 404
    proc = session['proc']
    try:
        proc.stdin.write((code + '\n').encode('utf-8'))
        proc.stdin.flush()
    except Exception as e:
        return jsonify({'error': f'failed to submit code: {e}'}), 500
    with _captured_login_lock:
        session['status'] = 'code_submitted'
        session['output'] = ''  # reset so the tail below reflects THIS attempt
    # Give the CLI a moment to react (success message, or "Invalid code").
    _time.sleep(1.5)
    with _captured_login_lock:
        out_tail = session['output'][-500:]
        still_running = session.get('exit_code') is None
    return jsonify({'ok': True, 'still_running': still_running, 'output_tail': out_tail})


# Wire claude auth hooks into AgentRuntime so generic routes share the same
# in-memory state. Must be at module level after _claude_auth_state and
# _run_claude_auth_probe are defined, before any request can be served.
_agent_runtime._CLAUDE_HOOKS['auth_status'] = lambda: dict(_claude_auth_state)
_agent_runtime._CLAUDE_HOOKS['auth_probe'] = _run_claude_auth_probe


# ── Generic /api/agent/<provider>/auth-* routes ──────────────────────────────


@bp.route('/api/agent/<provider>/auth-status')
def agent_auth_status(provider):
    """Cheap cached auth state for any provider (no subprocess).

    For 'claude' the response shape is {ok, reason, last_error_text, detected_at,
    last_probe_at} — byte-identical to the legacy /api/claude/auth-status shim.
    Other providers return {ok, status, method, error_text, last_checked}.
    """
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    return jsonify(rt.auth_status())


@bp.route('/api/agent/<provider>/auth-probe', methods=['POST'])
def agent_auth_probe(provider):
    """Actively probe auth state for any provider. May spawn a subprocess.

    For 'claude' the payload is identical to the legacy /api/claude/auth-probe shim.
    """
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    try:
        return jsonify(rt.auth_probe())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/agent/<provider>/auth-login', methods=['POST'])
def agent_auth_login(provider):
    """Open the provider CLI in a new OS terminal for interactive login.

    Requires a real TTY — MC's in-app terminal pop-out is a piped subprocess
    and most provider CLIs refuse interactive auth flows without a console.
    Equivalent to the legacy /api/claude/login-launch shim.
    """
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    bin_path = rt.resolve_binary()
    if not bin_path:
        return jsonify({'error': f'{provider} CLI is not installed'}), 400
    err = _launch_terminal_for_binary(str(bin_path))
    if err:
        return jsonify({'error': err}), 500
    return jsonify({'ok': True})


@bp.route('/api/agent/<provider>/auth-logout', methods=['POST'])
def agent_auth_logout(provider):
    """Revoke / clear stored credentials for a provider."""
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    try:
        return jsonify(rt.auth_logout())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Legacy /api/claude/auth-* shims ──────────────────────────────────────────
# Kept indefinitely so Tauri launcher, mobile APK, and dashboards built before
# ws_007 updates the UI keep working with zero behavioral change.


@bp.route('/api/claude/auth-status')
def claude_auth_status():
    """Backward-compat shim → /api/agent/claude/auth-status."""
    with _claude_auth_lock:
        return jsonify(dict(_claude_auth_state))


@bp.route('/api/claude/login-launch', methods=['POST'])
def claude_login_launch():
    """Backward-compat shim → /api/agent/claude/auth-login."""
    return agent_auth_login('claude')


@bp.route('/api/claude/auth-probe', methods=['POST'])
def claude_auth_probe():
    """Backward-compat shim → /api/agent/claude/auth-probe.

    Actively probes Claude CLI auth by running `claude -p ok --max-turns 1`.
    Only invoked when the user clicks 'Re-check' in the banner.
    """
    return agent_auth_probe('claude')


# ── Agent endpoints ──────────────────────────────────────────────────────────

def _clayrune_api_reference() -> str:
    """Return the pre-authored Clayrune API reference for agent system prompts.

    Sourced from `data/agent_reference/CLAYRUNE_API.md`. Injected once per
    session by `_build_agent_context()` and `_hm_build_worker_context()` so
    agents don't have to curl-probe endpoints at runtime. Anthropic's prompt
    cache covers the cost after the first turn.

    Returns an empty string if the file is missing — failure here must never
    break a session (mirrors the AGENT_RULES.md / SHARED_RULES.md posture).
    """
    try:
        path = _APP_DIR / 'data' / 'agent_reference' / 'CLAYRUNE_API.md'
        if path.exists():
            return path.read_text(encoding='utf-8')
    except Exception:
        pass
    return ''


_PLANS_DIR = Path.home() / '.claude' / 'plans'


def _is_plan_path(fp: str) -> bool:
    """True if fp is a .md file under ~/.claude/plans/.

    Headless-safe plan detection: ExitPlanMode hangs without a TTY (agents are
    told not to use it), so any markdown file written into ~/.claude/plans/ is
    registered as the session's plan instead. Feeds session['plan_file'] → the
    PLAN tab (/api/project/<id>/plans) and the in-chat plan link.
    """
    try:
        p = Path(fp)
        if p.suffix.lower() != '.md':
            return False
        p.resolve().relative_to(_PLANS_DIR.resolve())
        return True
    except Exception:
        return False


# A plan written by something OTHER than the Write/Edit tool — a heredoc, a
# `cp`, a `tee`. The detector below only ever saw tool calls, so a plan created
# from the shell registered NOWHERE and the PLAN tab never mentioned it,
# silently and forever.
#
# This scans for markdown-looking TOKENS and lets `_is_plan_path` decide, rather
# than re-encoding the plans-dir path in a second regex. Two places that both
# know where plans live is two places that can disagree — and the one here
# would fail open (registering nothing), which is invisible.
_MD_TOKEN_RE = re.compile(r"""[^\s'"<>|&;]+\.md""", re.IGNORECASE)
_QUOTE_CHARS = "'\""


def _plan_paths_in_command(cmd: str) -> list[str]:
    """Plan-file paths referenced by a shell command.

    Existence is NOT checked: `tool_use` is emitted before the command runs, so
    the file does not exist yet. `get_project_plans` filters missing paths at
    read time, which is the right place — a command that failed simply never
    produces a file to list.
    """
    out = []
    for m in _MD_TOKEN_RE.finditer(cmd or ''):
        raw = m.group(0).strip(_QUOTE_CHARS)
        # Strip a leading redirection operator the token regex may have kept.
        raw = raw.lstrip('>')
        try:
            p = os.path.expanduser(raw)
        except Exception:
            continue
        if _is_plan_path(p) and p not in out:
            out.append(p)
    return out


def _register_plan_file(session, fp: str) -> bool:
    """Attach a plan file to a session. Idempotent, order-preserving.

    `plan_file` used to be a SCALAR, so a session that wrote three plans kept
    only the third and the other two were unreachable from every UI. The list
    is the real answer; the scalar stays as "most recent" because the in-chat
    plan link and the approval banner are single-valued by nature.
    """
    if not fp or not _is_plan_path(fp):
        return False
    files = session.setdefault('plan_files', [])
    if fp not in files:
        files.append(fp)
    session['plan_file'] = fp
    return True


def _session_plan_files(rec) -> list[str]:
    """Every plan on a session record or agent-log entry, newest-registered
    last. Falls back to the scalar so entries written before `plan_files`
    existed keep working — there is no migration for the agent log."""
    files = list((rec or {}).get('plan_files') or [])
    scalar = (rec or {}).get('plan_file') or ''
    if scalar and scalar not in files:
        files.append(scalar)
    return files


def _clayrune_universal_capabilities(port: int | None = None) -> list[str]:
    """Universal Clayrune-aware behaviors that apply to EVERY agent —
    regular project agents, hivemind workers, future agent types.

    THIS IS THE CANONICAL PLACE for "things every agent should know about
    how Clayrune works". Both `_build_agent_context()` and
    `_hm_build_worker_context()` (and any future builders) splice the
    output of this function into their system prompts.

    Add a new universal capability HERE, not in the per-context builders.
    Project-specific items (backlog API with project_id, memory paths,
    workstream bus endpoints) belong in the per-context builders.

    Each entry becomes one section of the agent's appended system prompt.
    """
    if port is None:
        port = PORT
    return [
        # Plan mode hangs in headless Claude Code regardless of agent type, so
        # plans are captured as files instead (see _is_plan_path + the PLAN tab).
        "IMPORTANT — Plans: Do NOT use EnterPlanMode or ExitPlanMode — you run "
        "headless with no interactive terminal, so plan-mode approval hangs "
        "indefinitely. Instead, when you form a non-trivial plan: (1) describe it "
        "inline in your chat reply, AND (2) write the full plan as a markdown "
        "file into your home directory's .claude/plans/ folder (an absolute "
        "path, e.g. ~/.claude/plans/<short-descriptive-name>.md; create the "
        "folder if needed). Clayrune auto-detects plans written there and shows "
        "them in the project's PLAN tab with a link in the chat — there is no "
        "approval step, just keep working after you write the file.",

        # Questions — the MC Tool Protocol fence, NOT the native tool.
        #
        # This used to say "use the AskUserQuestion tool". That tool is a Claude
        # Code *interactive-mode* built-in; Clayrune runs agents HEADLESS, where
        # it is absent from the toolset entirely. So the instruction was a
        # promise the harness could not keep: every agent that tried to ask a
        # question found no such tool and fell back to burying the question in
        # prose — worst of all on unattended runs, where nobody is reading.
        #
        # `mc:question` is the text-protocol emulation that actually works, and
        # it is provider-agnostic (Gemini has used it since multi-provider).
        # The runtime parses the fence out of the finished turn into the SAME
        # session state the native tool produced, so the interactive form, the
        # SSE event, and the follow-up resume are all unchanged.
        "Questions: when you need the user to CHOOSE between concrete options, "
        "emit a fenced ```mc:question``` block and STOP (end your turn). Body:\n"
        "  {\"questions\": [{\"header\": \"Topic\", \"question\": \"Which one?\", "
        "\"options\": [{\"label\": \"A\", \"description\": \"what it means\"}, "
        "{\"label\": \"B\", \"description\": \"what it means\"}], "
        "\"multiSelect\": false}]}\n"
        "Clayrune renders it as an interactive form and sends the answer back as "
        "your next message. If the user is watching, they answer in the chat; if "
        "the run is UNATTENDED (scheduled, steward, night-review), Clayrune "
        "delivers the question over their configured channel (email) so they can "
        "reply offline — so it is safe to ask even when nobody is at the screen. "
        "Do NOT call an `AskUserQuestion` tool: it does not exist headless. For "
        "open-ended input, just ask in plain text.",

        # Mermaid blocks render inline in the chat panel.
        "Diagrams: Clayrune renders ```mermaid fenced blocks INLINE in your "
        "chat response — the user sees a rendered diagram (hand-drawn style, "
        "click to enlarge), NOT raw text. PREFER putting Mermaid diagrams "
        "directly in your assistant response over writing them to a separate "
        "file, unless the user explicitly asks for a file. Supported types: "
        "flowchart, sequence, state, class, ER, gantt, journey, pie. The "
        "Clayrune theme (cream nodes, orange borders, clay-brown text) is "
        "applied automatically — do not override it.",

        # The chat renderer inlines absolute image paths as thumbnails via
        # /api/serve-image (allowlist: project root, the Clayrune repo,
        # Clayrune's data/uploads). Markdown image syntax is NOT rendered.
        "Images: To SHOW the user an image, output its ABSOLUTE file path on "
        "its own line in your chat reply — Clayrune renders it as an inline "
        "thumbnail (click to enlarge). This is a platform fact, not something "
        "to test or hedge about. Constraints: (1) the file must live under "
        "the project root or Clayrune's data/uploads — paths outside those "
        "(e.g. the user's Pictures folder) are refused by the server and "
        "degrade to a plain link; copy such files into the project first, "
        "then output the new path. (2) Do NOT use markdown image syntax "
        "![alt](path) — it does not render as markdown here. (3) Screenshots "
        "or images the user attaches arrive as data/uploads paths you can "
        "open with your file tools.",

        # Deep links to files — /api/serve-file, works over the tunnel too.
        "Files: To give the user a clickable link to a file that works BOTH "
        "locally AND from a remote/phone console, put a marker on its own line: "
        "[file:<ABSOLUTE_PATH>]  — or [file:<ABSOLUTE_PATH>|Label] to set the "
        "link text. Clayrune renders it as a download link served over HTTP "
        "(this is why it reaches remote consoles; a bare file:// path only opens "
        "on the host and is blocked from web pages). Same allowlist as images: "
        "the file must live under the project root or Clayrune's "
        "data/uploads|media, and secrets (.env, *.key/*.pem, id_rsa, "
        "*credential*) are refused. Use this for logs, reports, exports, or any "
        "artifact the user should open — NOT for images (use a bare image path) "
        "or diagrams (use a ```mermaid block).",

        # Browser pane — agents kept shelling out to the HOST browser
        # (`start`/`open`/`webbrowser.open`), which opens a window on a machine
        # the user may not be sitting at and that the agent cannot see or
        # drive. The pane is the Clayrune-native answer and was simply absent
        # from every system prompt until 2026-08-06.
        f"Browser: Clayrune has a BUILT-IN browser pane — a real Chromium "
        f"rendered live INSIDE the user's dashboard (and over the tunnel, on "
        f"their phone), which you can drive over HTTP. Whenever a task means "
        f"opening a web page — showing the user a site, filling a form, "
        f"working inside a web app, anything needing a real logged-in session "
        f"— use THIS. Do NOT shell out to `start` / `open` / `xdg-open` / "
        f"`webbrowser.open` / a local Playwright window: that opens a window "
        f"on the host only, the user may not be at that screen, and you can "
        f"neither see nor control it.\n"
        f"  • Just show the user a page: put `[browser:https://example.com]` "
        f"on its OWN LINE in your reply — Clayrune opens the pane there.\n"
        f"  • Drive it yourself: POST http://localhost:{port}/api/browser/launch "
        f"with {{\"project_id\":\"<pid>\",\"url\":\"https://…\"}} → returns "
        f"{{\"session_id\":…}}. Then emit `[browser-attach:<session_id>]` on its "
        f"own line so the user sees what you're doing. Steer it with POST "
        f"/api/browser/input {{\"session_id\":…,\"type\":\"navigate|mouse|wheel|"
        f"text|key|back|forward|reload\", …}}. POST /api/browser/selection "
        f"returns the page's selected text; GET /api/project/<pid>/browser/"
        f"status lists live sessions; POST /api/browser/stop ends one.\n"
        f"  • Sites you sign into: pass a profile name "
        f"(`\"profile\":\"reddit\"`) — a persistent Chromium profile that keeps "
        f"cookies across sessions and restarts, so the next run is already "
        f"logged in. UNNAMED launches are throwaway and start logged out every "
        f"time. Check GET /api/browser/profiles FIRST — the account may already "
        f"be signed in, and naming a profile that's already open adopts that "
        f"session instead of starting a second Chromium. DELETE "
        f"/api/browser/profiles/<name> IS the sign-out — destructive, ask "
        f"first.\n"
        f"  • It is a viewing/interaction surface, not a scraper: there is no "
        f"read-whole-page endpoint (only the current selection). To read page "
        f"content for your own reasoning, use WebFetch/WebSearch. Use the pane "
        f"when a human needs to SEE it, when you must be logged in, or when the "
        f"page needs real interaction.",

        # Two schedulers exist — pick the right one for the job.
        f"Scheduler — TWO options, pick by lifespan:\n"
        f"  • Clayrune LOCAL scheduler — for LONG-TERM, REPEATABLE jobs scoped "
        f"to a project that must outlive any single session and re-run an agent "
        f"inside THIS Clayrune environment (daily standups, weekly "
        f"reports, recurring cleanups, one-shots scheduled hours/days out). "
        f"List: GET http://localhost:{port}/api/schedules  "
        f"Create: POST http://localhost:{port}/api/schedules with "
        f"{{\"project_id\":\"...\",\"task\":\"...\",\"schedule_type\":\"daily|weekly|interval|once|cron\","
        f"\"time\":\"09:00\",\"days\":[],\"interval_minutes\":60,\"run_at\":\"ISO8601\",\"cron_expr\":\"...\"}}  "
        f"Update: PUT /api/schedules/<id>  Delete: DELETE /api/schedules/<id>.\n"
        f"  • Anthropic /schedule skill — for SHORT-INTERVAL polling/follow-ups "
        f"that live inside the CURRENT session lifespan (e.g. \"check the build "
        f"every 5 min\", \"poll this PR until merged\"). Cloud-side; cannot reach "
        f"local Clayrune state, but perfect for in-session tick work.\n"
        f"Rule of thumb: if it should still fire after this conversation ends, "
        f"use the Clayrune local scheduler; if it's a tight loop tied to the "
        f"work you're doing right now, use /schedule.",

        # API discovery hint — when an unfamiliar Clayrune feature is needed,
        # don't guess endpoint names; list them.
        f"API discovery: When you need a Clayrune feature you haven't used "
        f"before, do NOT guess endpoint names (e.g. /api/cron, /api/jobs). "
        f"Grep server.py for `@app.route` to enumerate the real endpoints, "
        f"or curl http://localhost:{port}/ and inspect the served HTML. "
        f"For the curated, current shape of the API, see the "
        f"'--- CLAYRUNE API REFERENCE ---' block in your system prompt.",

        # User-facing answers: when the user asks how to manage MCP/Skills/etc.,
        # they mean inside Clayrune, NOT via the upstream Claude CLI.
        "Management surfaces — you are running inside Clayrune, not bare Claude "
        "Code. MCP servers, Skills, Scheduler, Settings, and project Memory are "
        "all owned and managed by Clayrune via its UI (sidebar entries: MCP, "
        "Skills, Scheduler, Settings; Memory via the project modal). Underlying "
        "files: `~/.claude.json` (mcpServers), per-project `<project>/.mcp.json`, "
        "`~/.claude/skills/` and `<project>/.claude/skills/`, "
        "`~/.claude/settings.json`. When the user asks how to add, edit, or "
        "remove any of these, point them at the Clayrune UI surface — do NOT "
        "tell them to run `claude mcp add`, `claude skill ...`, or hand-edit the "
        "underlying JSON. If asked 'how do I X', assume X is a Clayrune action "
        "first; only fall back to upstream Claude CLI advice if Clayrune "
        "genuinely doesn't surface it.",

        # Leg B priming — name the skill so it's reached for at the right moment.
        "Project memory: when you hit an unknown about this project's history, "
        "a prior decision, or a convention, use the mc-memory-search skill "
        "before guessing — it ranks the project's topic files, archive, and "
        "session log. Relevant memory for the current task is also "
        "auto-surfaced in your context under 'RELEVANT MEMORY'.",
    ]


def _skills_catalog_block(project):
    """Skill catalog for non-Claude agents (full-parity Stage 3).

    Claude Code auto-discovers skills from ~/.claude/skills/ and the project's
    .claude/skills/; other provider CLIs don't. So MC injects the catalog —
    each skill's name, description and SKILL.md path — into the system prompt.
    The agent reads the full SKILL.md with its own file tools when a task
    matches. Returns '' for Claude (native discovery) or when there are no
    skills. Best-effort: any failure yields '' — skills are never load-bearing.
    """
    if (project.get('provider') or 'claude').lower() == 'claude':
        return ''
    try:
        skills = _skills.list_skills(project.get('project_path') or None,
                                     project.get('id'))
    except Exception:
        return ''
    visible = [s for s in skills
               if s.get('scope') != 'archive'
               and not s.get('shadowed_by_project')]
    if not visible:
        return ''
    lines = []
    for s in visible:
        desc = (s.get('description') or '').strip().replace('\n', ' ')
        lines.append(f"- {s.get('name')}: {desc}\n  SKILL.md: {s.get('path')}")
    return ("--- AVAILABLE SKILLS ---\n"
            "Reusable skills are available to you. When the current task "
            "matches a skill's description, read its SKILL.md with your "
            "file tools and follow it.\n" + "\n".join(lines))

def _is_position_hit(h):
    """A read-floor hit that is a standing position rather than a note."""
    try:
        from mc.memory import _is_position_file
        import os as _os
        return _is_position_file(_os.path.basename(str(h.get('file', ''))))
    except Exception:
        return False


def _render_position(project, h):
    """One line: verdict, subject, the REASON, and what would reopen it.

    The reason is the load-bearing part. A bare verdict is dogma an agent can
    only obey; the reason is checkable, which is what lets it be re-opened
    honestly instead of either ignored or followed blindly.
    """
    try:
        from mc.memory import _get_memory_path, _parse_position
        import os as _os
        fp = _get_memory_path(project).parent / _os.path.basename(str(h.get('file')))
        rec = _parse_position(fp.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        rec = {}
    if not rec:
        return f"[{h.get('file')}] {h.get('snippet', '')}"
    bits = [f"{(rec.get('verdict') or 'decided').upper()}: {rec.get('subject')}"]
    if rec.get('reason'):
        bits.append(f"because {rec['reason']}")
    if rec.get('expires_when'):
        bits.append(f"REOPEN IF {rec['expires_when']}")
    if rec.get('decided'):
        bits.append(f"({rec['decided']})")
    return " — ".join(bits) + f"  [{h.get('file')}]"


def _model_label(provider, model):
    """Friendly name for a model id ('claude-opus-5' → 'Opus 5').

    Reuses the provider runtime's own MODEL_CHOICES so this never drifts from
    the picker the user sees. Falls back to the raw id — an unknown model is
    still worth naming, and a blank would read as "no engine pinned", which is
    a different fact entirely.
    """
    m = (model or '').strip()
    if not m:
        return ''
    try:
        rt = _agent_runtime.get_runtime((provider or 'claude').strip().lower())
        for mid, label in (getattr(rt, 'MODEL_CHOICES', None) or []):
            if mid == m:
                return label
    except Exception:
        pass
    return m


def _engine_label(rec):
    """The engine a character type runs on, as a short scannable string.

    '' means the type pins nothing and inherits the project default — the
    CALLER decides how to say that, because "no engine" and "inherits" read
    very differently on the Floor versus in a roster line.
    """
    eng = (rec or {}).get('engine') or {}
    bits = []
    lbl = _model_label(eng.get('provider'), eng.get('model'))
    if lbl:
        bits.append(lbl)
    elif eng.get('provider'):
        # No model pinned but a provider is — that is still a real constraint.
        bits.append(str(eng['provider']).strip().title())
    if eng.get('effort'):
        bits.append(f"effort {eng['effort']}")
    return ' · '.join(bits)


def _roster_block(project, port):
    """Who the hired types are, which engine each one runs on, and which of the
    two ways to call them shows up.

    NOT "these types exist" — the harness already lists them as agent types,
    because a character IS a Claude Code subagent file in ~/.claude/agents/.
    What it does not say is who they are (Ron says "Fenn", the list says
    `code-reviewer`), that the two ways of calling one differ in whether the
    work is visible, or — the part that made delegation guesswork — what
    ENGINE each one is about to spend. A dispatcher choosing between Quill
    (Opus at max effort) and Tobin (Sonnet) was choosing blind: the roster
    named the specialists and hid their price, so "who fits the words in this
    task" was the only signal available to rank them by (Ron, 2026-09-01).
    """
    try:
        from mc import characters as _chars
        pp = (project or {}).get('project_path') or ''
        recs = _chars.list_characters(project_path=pp or None,
                                      project_id=(project or {}).get('id'))
    except Exception as e:
        _log(f"[roster] unavailable: {e}")
        return ''
    named = [r for r in (recs or []) if r.get('agent_name')]
    if not named:
        return ''   # a heading over an empty list is worse than silence
    lines = []
    for r in sorted(named, key=lambda r: (r.get('agent_name') or '').lower()):
        face = (r.get('avatar') or '').strip()
        eng = _engine_label(r) or 'project default'
        lines.append(
            f"  • {face + ' ' if face else ''}{r['agent_name']} — `{r['name']}`"
            f" ({r.get('scope', 'global')}) [{eng}] —"
            f" {(r.get('description') or '').strip()[:100]}")
    pid = (project or {}).get('id') or ''
    return (
        "--- THE ROSTER (hired agent types, what Ron calls them, and what "
        "engine each one spends) ---\n"
        + "\n".join(lines)
        + "\n  The backticked name is the agent type; the other is what the type "
          "calls itself, and it is the one Ron will say. The bracket is the "
          "ENGINE that type runs on — a pinned model, and its effort level "
          "where one is set. `project default` means the type pins nothing and "
          "inherits whatever this project is configured to use, so it costs "
          "whatever you cost.\n"
          "  Two ways to call one, and they differ in something that matters:\n"
          "  • The Task tool runs it IN THIS PROCESS. Fast, and invisible — it "
          "never becomes a session, so it never appears on the Floor and Ron "
          "cannot open it. Right for a quick helper whose answer you fold into "
          "your own.\n"
          f"  • curl -s -X POST http://localhost:{port}/api/project/{pid}"
          "/agent/dispatch -d '{\"task\":\"…\",\"character\":\"global:<type>\"}' "
          "spawns a REAL session: its own figure on the Floor, its own chat Ron "
          "can open and argue with. Right for work that outlives your turn or "
          "that he should be able to watch.")


def _build_agent_context(project, incognito=False, task='', character_body='',
                         character_name='', session_id='', character_skills=None,
                         source=''):
    """Build system prompt context for the agent.

    character_body, when set, is the markdown body of a per-chat "character"
    (a Claude Code subagent persona the user picked at new-chat time). It is
    injected here at spawn beside AGENT_RULES and rides along on -r respawns
    via the session's '_system_prompt' stash (re-appended verbatim — CLI
    ≥2.1.206 rebuilds the system prompt from flags on every invocation, see
    _respawn_sysprompt_args). The stash is written once at spawn, which is
    why the character stays immutable for the chat's lifetime (Prompt
    Builder Phase 2). Caveat: a conversation resumed from history (no live
    session dict) rebuilds the context WITHOUT a character.

    incognito=True keeps the full project context (rules, memory pointer,
    recent activity, recent conversations, current task) so the agent knows
    what's been done and can answer side questions. It only changes the
    output side: Mission Control will not log the session to the agent log
    and will not append a summary to project memory on completion. The
    notice block tells the agent so it doesn't write to MEMORY/rules itself.

    The global incognito pseudo-project (`_is_incognito_project`) doesn't
    have meaningful "what's been done" context anyway, so this still works
    naturally — the lack of activity/recent-conversations is just the truth.

    source='agent' marks a session another agent dispatched programmatically
    (MC-925) rather than the human's own chat. One of those with no persona
    used to inherit the project's default agent_name anyway — a delegated
    Sonnet worker introduced itself as "Vector" and the human-facing session
    that spawned it then reported Vector as the author of work Vector never
    did. A personaless delegated session gets no name here instead.
    """
    parts = []
    # Non-Claude agents (Gemini etc.) get a slimmer context. Claude treats a
    # rich context dump as background; weaker models read prompt-history-shaped
    # sections (the MEMORY.md session log, recent conversations, recent
    # activity) as a TASK LIST and go off doing phantom work on a plain "Hi".
    # So those sections are Claude-only; non-Claude still gets the targeted
    # read-floor (RELEVANT MEMORY) which is small and task-scoped.
    _is_claude = (project.get('provider') or 'claude').lower() == 'claude'
    # A persona that named itself outranks the global assistant name: for this
    # chat, that IS who is speaking. Emitting both would tell the agent it has
    # two names, and it would pick one at random per turn.
    #
    # A DELEGATED session (source='agent' — dispatched by another agent, not
    # the human's own chat) with no persona does NOT inherit the global
    # assistant name (MC-925): "Vector" is this project's one human-facing
    # identity, and a personaless worker claiming it is how a delegated
    # Sonnet run got attributed to Vector for work Vector never did.
    _delegated_unnamed = bool(source == 'agent' and not character_name)
    agent_name = character_name or ('' if _delegated_unnamed
                                    else state.CONFIG.get('agent_name', ''))
    user_name = state.CONFIG.get('user_name', '')
    if agent_name and character_name:
        parts.append(
            f"Your name is {agent_name}. Use it when you introduce yourself or "
            f"sign off; do not call yourself an assistant, a model, or the name "
            f"of your role.")
    elif agent_name:
        parts.append(f"Your name is {agent_name}.")
    elif _delegated_unnamed:
        parts.append(
            "You were dispatched programmatically with no persona assigned. "
            "You are not the project's default agent and have no name of your "
            "own for this session — if asked who you are, say you are an "
            "unnamed worker session and name the model you are running on, "
            "do not answer with the default agent's name.")
    # Naming your own figure. The Floor shows a figure per live session and a
    # name is how Ron tells two of them apart at a glance; the default one is
    # inherited, not chosen, so an agent doing something distinct should say so.
    # Offered rather than demanded — a board where every session renamed itself
    # would be as unreadable as one where none did.
    if session_id and not incognito:
        parts.append(
            f"You appear on the Floor as a figure named {agent_name or 'unnamed'}. "
            f"If a name or a one-emoji face would tell Ron at a glance what you "
            f"are doing here, set it once: curl -s -X POST "
            f"http://localhost:{state.CONFIG.get('port', 5199)}/api/floor/figure/"
            f"{session_id}/name -H 'Content-Type: application/json' "
            + '-d \'{"name":"...","avatar":"<one emoji>","by":"self"}\'. Both '
            "fields are optional and an absent one is left as it was. Only worth "
            "doing when it distinguishes you from the other figures — a board "
            "where every session renamed itself would be as unreadable as one "
            "where none did.")
    _has_roster = False
    if session_id and not incognito:
        _ros = _roster_block(project, state.CONFIG.get('port', 5199))
        if _ros:
            parts.append(_ros)
            _has_roster = True
    # A declared toolkit. NOT a restriction — the harness decides which skills
    # exist, and nothing here narrows that. It says which of them are YOURS,
    # because a list of sixty says nothing about who you are and three named
    # ones do.
    if character_skills and not incognito:
        parts.append(
            "Your own skills, as declared on your type: "
            + ', '.join(f'`{k}`' for k in character_skills)
            + ". Others are still available if a task genuinely calls for one — "
            "this names the ones you were hired for, it does not fence you in.")
    if user_name:
        parts.append(f"The user's name is {user_name}. Address them accordingly.")
    # Sticky brevity: when sticky_agent_settings is on, the device-neutral brief
    # directive lives HERE (cached, once per spawn) instead of being prepended to
    # every user turn by _apply_mobile_brief. Flipping the toggle mid-session is
    # handled by the respawn-on-flip path (see update_config / agent_followup).
    if (state.CONFIG.get('sticky_agent_settings', False)
            and state.CONFIG.get('brief_replies_always_enabled', False)):
        parts.append(_BRIEF_REPLY_DIRECTIVE_SYSTEM)
    parts.append(f"You are working on {project.get('name', project['id'])}.")
    pp = project.get('project_path', '')
    if pp:
        parts.append(f"Project root: {pp}")

    if incognito:
        parts.append(
            "--- INCOGNITO MODE ---\n"
            "This is an incognito session. You can read everything about the project "
            "(rules, memory, recent activity, files) so you have full context to answer. "
            "However, Clayrune will NOT log this session to the agent log and will "
            "NOT append a summary to MEMORY.md on completion. Treat this as an off-the-record "
            "side conversation: do not modify MEMORY.md, AGENT_RULES.md, or SHARED_RULES.md "
            "and do not push commits unless the user explicitly asks. "
            "Note: Claude still writes a transcript to ~/.claude/projects/, so incognito "
            "hides this session from Clayrune surfaces, not from disk."
        )

    # Load rules
    if pp:
        agent_rules_path = Path(pp) / 'AGENT_RULES.md'
        if agent_rules_path.exists():
            parts.append(f"--- AGENT_RULES.md ---\n{agent_rules_path.read_text(encoding='utf-8')}")
    if SHARED_RULES_PATH.exists():
        parts.append(f"--- SHARED_RULES.md ---\n{SHARED_RULES_PATH.read_text(encoding='utf-8')}")

    # Per-chat character/persona (Prompt Builder Phase 2). Still AFTER the
    # rules — but the old comment here said the rules retain primacy "over a
    # chosen persona's voice", and that was the bug Ron reported on 2026-09-01:
    # every agent sounded identical. The rules block is ~90% of a persona's
    # prompt and it legislates tone ("always condense", "bullet points always
    # wins", "never narrate your own diligence"), so subordinating voice to it
    # collapsed nine characters into one bulleted telegram. Ordering is kept —
    # conduct really does outrank persona — and the ownership is now split
    # explicitly instead of being decided by position alone.
    if character_body:
        parts.append(
            "--- CHARACTER (active persona for this chat) ---\n"
            + character_body.strip()
            + "\n\n  This character owns your VOICE: your register, your rhythm,"
              " what you lead with, what you never say. The rules above own your"
              " CONDUCT and the CONTENT of your answers — what you may do, what"
              " you must verify, what you must not claim. Where the two meet,"
              " follow the rules and say it in your own voice; they constrain"
              " what is true, not how you sound.\n"
              "  Brevity is a rule, not a voice. Short does not mean"
              " interchangeable — the shortest version of you should still be"
              " recognisably you. If your reply could have been written by any"
              " other agent on the roster, you have dropped the character, not"
              " obeyed the rules.")

    # NOTE: Project memory (MEMORY.md) is NOT injected here — the Claude CLI
    # already reads ~/.claude/projects/<path>/memory/MEMORY.md natively.
    # Injecting it via --append-system-prompt would duplicate it in every API call.
    mem_path = _get_memory_path(project)

    # System awareness
    pid = project['id']
    port = PORT
    mem_file = str(mem_path) if mem_path else 'MEMORY.md'
    archive_path = _get_archive_path(project)
    archive_file = str(archive_path)
    awareness = [
        "You are managed by Clayrune.",
        f"Memory: {mem_file} is auto-loaded and maintained for you by Clayrune. "
        f"To retrieve older context, search it; do NOT hand-edit it.",
        f"Archive: {archive_file} — older session logs, read if needed.",
    ]
    if pp:
        rules_file = str(Path(pp) / 'AGENT_RULES.md')
        awareness.append(f"Rules: {rules_file} — add critical constraints here.")
    awareness.extend([
        f"Terminal: curl -s -X POST http://localhost:{port}/api/terminal/launch "
        f'-H "Content-Type: application/json" '
        f"-d '{{\"project_id\":\"{pid}\",\"command\":\"<CMD>\"}}'",
        f"MANDATORY — Process Registration: Every time you spawn a background process, server, bot, "
        f"or any long-running command, you MUST register it with the Process Manager IMMEDIATELY after spawning. "
        f"This is NOT optional. Unregistered processes cannot be monitored or stopped by the user. "
        f"Steps: 1) Spawn the process. 2) Capture the PID (Bash: `cmd & echo $!` — Python: `p = subprocess.Popen(...); p.pid`). "
        f"3) Register: curl -s -X POST http://localhost:{port}/api/processes/register "
        f'-H "Content-Type: application/json" '
        f"-d '{{\"pid\":PID_NUMBER,\"name\":\"Short description\",\"project_id\":\"{pid}\","
        f"\"command\":\"the command that was run\"}}' "
        f"— PID must be an integer. Do NOT skip this step.",
        # Universal Clayrune awareness — see _clayrune_universal_capabilities().
        # Add new universal entries THERE, not here.
        *_clayrune_universal_capabilities(port=port),
        f"Backlog: This project has a Clayrune backlog (prioritized task list with notes, "
        f"attachments, and status). When the user says \"backlog\", \"backlog items\", \"the list\", "
        f"or similar, they mean THIS list — do NOT grep the filesystem. "
        f"Read it: curl -s http://localhost:{port}/api/project/{pid}/backlog "
        f"Update an item: curl -s -X PATCH http://localhost:{port}/api/project/{pid}/backlog/<item_id> "
        f'-H "Content-Type: application/json" -d \'{{"status":"done"}}\' '
        f"(status values: open, in_progress, blocked, done). "
        f"Add a note: POST /api/project/{pid}/backlog/<item_id>/note with {{\"text\":\"...\"}}.",
        f"Hivemind: You can launch multi-agent coordinated analysis on this project. "
        f"To create a hivemind, call: curl -s -X POST http://localhost:{port}/api/hivemind/create "
        f'-H "Content-Type: application/json" '
        f"-d '{{\"project_id\":\"{pid}\",\"goal\":\"GOAL_TEXT\",\"max_concurrent_workers\":3,"
        f"\"orchestrator_model\":\"sonnet\",\"worker_model\":\"sonnet\"}}' "
        f"— The orchestrator will decompose the goal into workstreams and spawn workers automatically. "
        f"Before creating, ask the user clarifying questions about scope, priorities, and constraints.",
    ])
    parts.append("--- SYSTEM ---\n" + "\n".join(awareness))

    # Stage 3 full-parity: non-Claude agents don't auto-discover skills —
    # inject the catalog so they can read + follow the relevant SKILL.md.
    _skills_block = _skills_catalog_block(project)
    if _skills_block:
        parts.append(_skills_block)

    # NOTE: the full MEMORY.md is NOT injected here for non-Claude agents.
    # An earlier attempt (Stage 4 "memory-in") dumped the whole index in —
    # but its Session Log is a wall of past prompts, which Gemini read as a
    # live task list. The targeted read-floor below ("RELEVANT MEMORY") is
    # the memory mechanism for every provider: small, task-scoped, safe.

    # Pre-authored Clayrune API reference — agents inside Clayrune used to
    # curl-probe endpoints every session. Injecting the curated reference
    # once eliminates that turn-cost; Anthropic's prompt cache makes it free
    # after the first turn.
    api_ref = _clayrune_api_reference()
    if api_ref:
        parts.append("--- CLAYRUNE API REFERENCE ---\n" + api_ref)

    # Continuity — what this project was part-way through, and what was
    # promised. Injected DIRECTLY rather than retrieved: "what am I mid-way
    # through" is relevant to every turn by definition, so making it compete
    # for a read-floor slot would be asking the wrong question. Affordable
    # because it is capped by construction (fixed slots, replace-never-append),
    # not because it is small by luck.
    if not incognito and state.CONFIG.get('continuity_enabled', True):
        try:
            from mc.memory import render_continuity as _render_cont
            # Scoped to THIS agent (same string as "Your name is …", which is
            # what keeps read and write asking for the same bucket).
            _cont = _render_cont(project, owner=agent_name)
            if _cont:
                parts.append(_cont)
        except Exception as e:
            _log(f"[continuity] render failed for {project.get('id')}: {e}")

    # Positions had storage, retrieval and a route but no caller. Capture stays
    # explicit (mining transcripts buries the twenty that matter under every
    # "no, use tabs"), so the agent has to be TOLD — directive, and here rather
    # than in the API reference, because reference material failing to fire is
    # the exact failure positions exist to fix.
    if not incognito and state.CONFIG.get('positions_enabled', True):
        try:
            from mc.memory import render_position_capture as _render_pos
            _pos = _render_pos(project, port)
            if _pos:
                parts.append(_pos)
        except Exception as e:
            _log(f"[positions] render failed for {project.get('id')}: {e}")

    # Leg B.3 — deterministic read floor (no model; ranked grep). The agent
    # already auto-loads the curated index; this surfaces relevant topic-file /
    # archive / session-log detail for THIS task so the read side never depends
    # solely on the probabilistic search skill. SPEC §3 Leg B.
    if task:
        try:
            hits = _memory_search(
                project, task,
                int(state.CONFIG.get('read_floor_topk', 6) or 6),
                expand=int(state.CONFIG.get('read_floor_link_expand', 2) or 0),
                record='read_floor')  # phase-4 delivery telemetry
        except Exception as e:
            # Was a bare `pass`. The read floor is the ONLY retrieval channel that
            # actually runs — agents open a memory file in 5% of sessions — so a
            # silent failure here degrades every agent's context with no signal at
            # all. The project's exception-swallowing policy says subsystem I/O
            # failures get logged; this subsystem was violating it.
            _log(f"[read-floor] {project.get('id')}: memory search failed: {e}")
            hits = []
        # Positions get their OWN block, above the rest. Mixed into "relevant
        # memory" a standing ruling reads as background history — which is
        # exactly what happened on 2026-08-23, when the Obsidian position was in
        # context and the agent proposed Obsidian anyway. A decision that has
        # already been made is not the same kind of thing as a relevant note,
        # and it must not look like one.
        _pos_hits = [h for h in hits if _is_position_hit(h)]
        _mem_hits = [h for h in hits if not _is_position_hit(h)]
        if _pos_hits:
            pl = "\n".join(f"  • {_render_position(project, h)}" for h in _pos_hits)
            parts.append(
                "--- STANDING POSITIONS (already decided — do NOT re-propose "
                "these without first checking whether the stated reason still "
                "holds; if it has expired, say so explicitly and reopen it) "
                "---\n" + pl)
        if _mem_hits:
            # `via` marks a note reached by a [[wikilink]] hop rather than a
            # lexical match — say so, so the agent can weigh it accordingly.
            rl = "\n".join(
                f"  • [{h['file']}] {h['snippet']}" if not h.get('via')
                else f"  • [{h['file']}] (linked from {h['via']}) {h['snippet']}"
                for h in _mem_hits)
            parts.append(
                "--- RELEVANT MEMORY (auto-surfaced for this task; "
                "use the mc-memory-search skill to dig deeper) ---\n" + rl)

    # Exploration read-floor — closes the learning loop by feeding the
    # Distiller's captured EXPLORATION.md proposals back into context. Without
    # this, _proposed/ explorations are write-only and never change behavior.
    # Best-effort, gated, and never load-bearing (same posture as the Distiller
    # write side). Skipped for incognito sessions (no memory leakage).
    if task and not incognito and state.CONFIG.get('exploration_readback_enabled', True):
        try:
            # _UNATTENDED_LOOP_RULE: a steward cycle is an unattended consumer,
            # so the read-floor withholds unattended-authored artifacts from it
            # (no autonomous→autonomous circuit). Attended sessions see all.
            expl = _distiller.exploration_read_floor(
                project['id'], task,
                int(state.CONFIG.get('exploration_read_floor_topk', 2) or 2),
                consumer_unattended=_distiller.is_unattended_task(task))
        except Exception:
            expl = []
        if expl:
            el = "\n".join(
                f"  • [{e['scope']}] {e['snippet']}  (full: {e['path']})"
                for e in expl)
            parts.append(
                "--- RELEVANT PAST EXPLORATIONS (a prior session already "
                "investigated something like this; read the full file before "
                "re-deriving) ---\n" + el)

    # Sibling activity — cross-agent coordination read-floor (Phase 0). Surfaces
    # OTHER live agents' current tasks (open intentions) + recently-landed
    # commits (completions) on THIS project, ranked by overlap with this task,
    # so concurrent siblings stop duplicating/contradicting each other. Same
    # delivery shape as RELEVANT MEMORY; gated behind coordination_enabled
    # (default OFF); best-effort, never load-bearing. Skipped for incognito.
    # Design: docs/COORDINATION_LAYER_DESIGN.md. Backlog 9518ec62.
    if task and not incognito and state.CONFIG.get('coordination_enabled', False):
        try:
            from mc.blueprints import coordination_routes as _coord
            _coord.reconcile_commits(project)  # publish any just-landed commits
            sib = _coord.sibling_activity(
                project['id'], (project.get('_coord_session_id') or ''), task,
                int(state.CONFIG.get('coordination_read_floor_topk', 3) or 3))
            block = _coord.render_readfloor(sib)
            if block:
                parts.append(block)
        except Exception as e:
            _log(f"[coord] read-floor injection failed: {e}")

    # Recent activity — Claude-only: a non-Claude agent reads these past
    # "Agent dispatched: <task>" lines as things it still has to do.
    log = project.get('activity_log', [])[:3]
    if log and _is_claude:
        lines = [f"  - {e.get('ts','')}: {e.get('msg','')}" for e in log]
        parts.append("Recent activity:\n" + "\n".join(lines))

    # Recent conversations — read directly from .jsonl transcripts so interrupted
    # sessions (never reached completion log) are still discoverable. Display the
    # LAST user message, not the first, since the first is usually a meta prompt
    # (context condensation, boot text) that the user won't recognize.
    # Claude-only: these are Claude transcripts, listed with `claude -r <id>`
    # resume hints. For a non-Claude agent they are both wrong (not its CLI)
    # and actively harmful — it reads them as "our last chat" and tries to
    # continue tasks from them.
    project_path = project.get('project_path', '')
    convos = (_recent_claude_transcripts(project_path, limit=5)
              if (project_path and _is_claude) else [])
    if convos:
        live_by_csid = {}
        try:
            for s in agent_sessions.values():
                if s.get('project_id') != project['id']:
                    continue
                csid = s.get('claude_session_id', '')
                if csid:
                    live_by_csid[csid] = s.get('status', 'unknown')
        except Exception:
            pass
        log_by_csid = {}
        try:
            for e in _load_agent_log(project['id']):
                csid = e.get('claude_session_id', '')
                if csid and csid not in log_by_csid:
                    log_by_csid[csid] = e.get('status', '')
        except Exception:
            pass
        sess_lines = []
        for c in convos:
            sid = c['session_id']
            st = live_by_csid.get(sid) or log_by_csid.get(sid) or (
                'interrupted' if c['turns'] > 0 else 'empty'
            )
            label = c['last_user'] or c['first_user'] or '(empty)'
            label = ' '.join(label.split())[:80]
            sess_lines.append(f"  - [{st}] {label} | claude -r {sid}")
        parts.append(
            "Recent conversations (use 'claude -r <id>' to resume any of these — "
            "label is the user's LAST message):\n" + "\n".join(sess_lines)
        )
    elif _is_claude:
        agent_log = _load_agent_log(project['id'])[:3]
        if agent_log:
            sess_lines = []
            for e in agent_log:
                csid = e.get('claude_session_id', '')
                sid_part = f" | claude -r {csid}" if csid else ''
                sess_lines.append(f"  - [{e.get('status','')}] {e.get('task','')[:60]}{sid_part}")
            parts.append("Recent agent sessions (use 'claude -r <id>' to resume a prior conversation):\n" + "\n".join(sess_lines))

    # What a delegation actually costs (Ron, 2026-09-01: "the agents do not
    # really understand their responsibility in overall token management and
    # delegation"). The roster told an agent WHO exists and, as of the same
    # day, which engine each one spends — but nothing anywhere told it that
    # spawning one re-pays this entire context first. So delegation got chosen
    # on name-fit ("this smells like research, call Quill") with no sense that
    # the lookup it wanted cost more to hand off than to do.
    #
    # The number is MEASURED, not asserted: everything above is already in
    # `parts`, so the floor a fresh sibling re-pays is exactly what has been
    # assembled here. A hardcoded figure would be wrong on every other project
    # and stale on this one the first time the rules file grew.
    if _has_roster:
        _floor = len("\n\n".join(parts))
        parts.append(
            "--- WHAT A DELEGATION COSTS (read before you spawn one) ---\n"
            "  Every dispatched session re-pays the full project context — "
            "rules, roster, memory read-floor, the API reference, continuity — "
            f"BEFORE it reads its task. Right now that floor is ~{_floor // 1024} KB "
            f"(~{_floor // 4000}k tokens), and it is paid again per agent, per "
            "dispatch. A Task-tool helper pays it too; it just does not show up "
            "on the Floor.\n"
            "  That cost is flat, so it dominates small asks and vanishes into "
            "large ones. Which gives the rule:\n"
            "  • Do it yourself when the answer is a grep, a file read, or one "
            "command. Spawning an agent to look something up costs more than "
            "looking it up, every time.\n"
            "  • Delegate a genuine UNIT OF WORK — something with its own "
            "investigation, its own verification, and a report worth reading on "
            "its own.\n"
            "  • Match the engine to the question, not the job title. A "
            "max-effort Opus specialist is the wrong tool for a lookup that "
            "happens to fall in their subject.\n"
            "  • Fan-out multiplies the floor. Five parallel agents is five full "
            "contexts before any of them does anything — parallelise work that "
            "is genuinely independent, not work you could hand one agent as "
            "three steps.\n"
            "  • You own what you hand out. Do not dispatch work you will not "
            "read, and do not leave a spawned session unreported.")

    ct = project.get('current_task', '')
    if ct:
        parts.append(f"Current task: {ct}")

    return "\n\n".join(parts)

# ── Agent → backlog sync (TodoWrite interception) ───────────────────────────
# When an agent calls the TodoWrite tool, we upsert its todo list into the
# project's backlog so that in-flight tasks survive agent crashes / reboots.
# Items are keyed by (session, content-hash) so repeated TodoWrite calls in the
# same session update the same rows rather than duplicating.

# _backlog_sync_lock moved to mc/state.py (Phase 0).


def _agent_todo_ref(session_key, content):
    """Stable dedup key for a TodoWrite item within a session."""
    norm = (content or '').strip().lower()
    h = hashlib.md5(f"{session_key}|{norm}".encode('utf-8')).hexdigest()[:12]
    return f"agent:{h}"


# _append_note_to_backlog_item ── moved to mc/blueprints/project_routes.py (1.11)
# with its only caller (the backlog note route).


def _apply_mc_tool_blocks_for_turn(session):
    """Turn boundary: scan the finished turn for MC Tool Protocol fences.

    Claude Code's native `AskUserQuestion` exists only in INTERACTIVE mode. MC
    runs agents headless, where it is absent from the toolset — so for years the
    system prompt promised a tool that was not there and every question an agent
    tried to ask quietly died in prose. `mc:question` is the text-protocol
    emulation (already used by Gemini); this is where Claude's turns get scanned
    for it. The parsed result lands in the SAME session state the native tool
    produced, so the SSE event, the interactive form, and the follow-up resume
    are all unchanged.

    Best-effort: a failure here must never break the run.
    """
    try:
        buf = session.pop('_mc_turn_buf', None)
        if not buf:
            return
        res = _agent_runtime.apply_mc_tool_blocks(session, '\n'.join(buf))
        if res.get('paused'):
            # A question was raised. If nobody is watching this run, hand it to
            # the user's configured channel so it can be answered offline
            # instead of hanging until the guardian notices.
            _question_channel_notify(session)
    except Exception as e:
        _log(f"[mc-tool] turn scan failed: {e}", flush=True)


def _question_channel_notify(session):
    """Route a freshly-raised question to the offline channel if unattended.

    Deliberately thin: the decision (attended? which channel? already sent?) and
    the delivery all live in `mc.question_channel`, which is best-effort and
    fully self-disabling. Never let it break a turn.
    """
    try:
        from mc import question_channel
        question_channel.on_question_raised(session)
    except Exception as e:
        _log(f"[question-channel] notify failed: {e}", flush=True)


def _auto_snapshot_notes_on_turn(session):
    """At a turn boundary, append the last substantive assistant text as a note
    on every in_progress agent-sourced backlog item owned by this session."""
    try:
        sk = (session.get('claude_session_id')
              or session.get('id')
              or session.get('session_id'))
        pid = session.get('project_id')
        if not sk or not pid:
            return
        lines = session.get('log_lines', []) or []
        start = session.get('_last_result_log_index', 0)
        session['_last_result_log_index'] = len(lines)
        if start >= len(lines):
            return
        fragments = []
        for ln in lines[start:]:
            s = (ln or '').strip()
            if not s or s.startswith('['):
                continue
            fragments.append(s)
        if not fragments:
            return
        summary = ' '.join(fragments)[:300].strip()
        if len(summary) < 20:
            return
        with _backlog_sync_lock:
            try:
                p = load_project(pid)
            except Exception:
                return
            if p is None:
                return
            agent_code = sk[:8] if isinstance(sk, str) else 'agent'
            updated = False
            now = now_iso()
            for it in p.get('backlog', []) or []:
                if (it.get('agent_session_id') == sk
                        and it.get('agent_status') == 'in_progress'):
                    notes = it.setdefault('notes', [])
                    if notes and notes[-1].get('text') == summary:
                        continue
                    notes.append({'ts': now, 'agent_code': agent_code, 'text': summary})
                    if len(notes) > 50:
                        it['notes'] = notes[-50:]
                    updated = True
            if updated:
                p['last_updated'] = now
                try:
                    save_project(pid, p)
                except Exception:
                    return
    except Exception:
        pass


def _sync_todowrite_to_backlog(project_id, session_key, todos):
    """Upsert a TodoWrite list into the project's backlog.

    TodoWrite is called with the agent's full current task list each time,
    so we upsert every item and leave items no longer present untouched
    (the user can clean them up; we don't auto-delete agent context).

    session_key: stable identifier (claude_session_id preferred) so the same
                 logical session updates the same rows across TodoWrite calls.
    todos: list of {content, status, activeForm} dicts from tool_input.
    """
    if not project_id or not session_key or not todos or not isinstance(todos, list):
        return 0
    with _backlog_sync_lock:
        try:
            p = load_project(project_id)
        except Exception:
            return 0
        if p is None:
            return 0
        backlog = p.setdefault('backlog', [])
        existing_by_ref = {i.get('agent_ref'): i for i in backlog if i.get('agent_ref')}
        now = now_iso()
        touched = 0

        for td in todos:
            if not isinstance(td, dict):
                continue
            content = (td.get('content') or '').strip()
            if not content:
                continue
            agent_status = td.get('status', 'pending')  # pending | in_progress | completed
            active_form = (td.get('activeForm') or '').strip()
            ref = _agent_todo_ref(session_key, content)
            backlog_status = 'done' if agent_status == 'completed' else 'open'

            if ref in existing_by_ref:
                item = existing_by_ref[ref]
                item['text'] = content
                item['status'] = backlog_status
                item['agent_status'] = agent_status
                item['agent_activity'] = active_form if agent_status == 'in_progress' else ''
                item['updated_at'] = now
                if backlog_status == 'done' and not item.get('done_at'):
                    item['done_at'] = now
                elif backlog_status == 'open':
                    item['done_at'] = None
            else:
                backlog.insert(0, {
                    'id': str(uuid.uuid4())[:8],
                    'text': content,
                    'priority': 'normal',
                    'status': backlog_status,
                    'created_at': now,
                    'updated_at': now,
                    'done_at': now if backlog_status == 'done' else None,
                    'source': 'agent:todowrite',
                    'agent_ref': ref,
                    'agent_session_id': session_key,
                    'agent_status': agent_status,
                    'agent_activity': active_form if agent_status == 'in_progress' else '',
                    'attachments': [],
                })
            touched += 1

        if touched:
            p['last_updated'] = now
            try:
                save_project(project_id, p)
            except Exception:
                return 0
        return touched


def _note_activity_state(session, msg):
    """Update session['activity_state'] from a `stream_event` envelope.

    Only fires when the CLI was spawned with --include-partial-messages (config
    `activity_states_enabled`); otherwise no stream_event ever arrives and the
    key stays absent, which the SSE layer reads as "no activity signal" and the
    UI falls back to the plain typing dots. Purely transient: never touches
    log_lines, never persisted, so turning the flag off reverts cleanly.

    States: 'thinking' (extended-thinking deltas) | 'writing' (answer text
    deltas) | 'tool' (a tool_use block is being assembled).
    """
    ev = msg.get('event')
    if not isinstance(ev, dict):
        return
    et = ev.get('type', '')
    state_ = ''
    if et == 'content_block_start':
        bt = (ev.get('content_block') or {}).get('type', '')
        state_ = {'thinking': 'thinking', 'redacted_thinking': 'thinking',
                  'text': 'writing', 'tool_use': 'tool'}.get(bt, '')
    elif et == 'content_block_delta':
        dt = (ev.get('delta') or {}).get('type', '')
        state_ = {'thinking_delta': 'thinking', 'signature_delta': 'thinking',
                  'text_delta': 'writing', 'input_json_delta': 'tool'}.get(dt, '')
    elif et == 'message_stop':
        state_ = ''
    else:
        return
    session['activity_state'] = state_


def _tool_preview(text, limit):
    """Single-line, length-capped preview of a tool argument.

    Collapses ALL whitespace FIRST, then truncates. Both halves matter:

    - A Bash command is frequently multi-line (a heredoc — `python - <<'PY'`…).
      Slicing the raw string kept the newlines, and the frontend splits a
      multi-line log entry into separate lines: only the first stayed a
      `[tool: …]` chip while the rest ("COGS = 0.52", "p") rendered as ordinary
      agent bubbles — stray script fragments littering the chat.
    - Cutting mid-token gave no signal it was truncated, so a fragment read like
      real (broken) output. Mark it with an ellipsis instead.
    """
    s = ' '.join((text or '').split())
    return s if len(s) <= limit else s[:limit].rstrip() + '…'


def _format_tool_activity(name, inp):
    """Format a tool_use block into a compact, single-line activity line."""
    if name in ('Read', 'Edit', 'Write'):
        fp = inp.get('file_path', '')
        short = Path(fp).name if fp else '?'
        return f'[tool: {name}] {short}'
    if name == 'Bash':
        cmd = _tool_preview(inp.get('command', '') or inp.get('description', ''), 80)
        return f'[tool: Bash] {cmd}'
    if name in ('Grep', 'Glob'):
        pat = _tool_preview(inp.get('pattern', ''), 80)
        return f'[tool: {name}] {pat}'
    if name == 'Task':
        desc = _tool_preview(inp.get('description', ''), 50)
        return f'[tool: Task] {desc}'
    if name == 'WebSearch':
        q = _tool_preview(inp.get('query', ''), 60)
        return f'[tool: WebSearch] {q}'
    if name == 'AskUserQuestion':
        qs = inp.get('questions', [])
        preview = _tool_preview(qs[0].get('question', ''), 60) if qs else ''
        return f'[tool: AskUserQuestion] {preview}'
    if name == 'TodoWrite':
        todos = inp.get('todos', []) or []
        total = len(todos)
        done = sum(1 for t in todos if isinstance(t, dict) and t.get('status') == 'completed')
        in_prog = next((t.get('content', '') for t in todos
                        if isinstance(t, dict) and t.get('status') == 'in_progress'), '')
        summary = f'{done}/{total}'
        if in_prog:
            summary += f' — now: {_tool_preview(in_prog, 60)}'
        return f'[tool: TodoWrite] {summary}'
    return f'[tool: {name}]'


# ── Single-emit gate ─────────────────────────────────────────────────────────
# Phase 1 of the 2026-04-27 race-condition consolidation: every place that
# wanted to write session['status'] / 'process_alive' / emit a status event
# from a stream-reader thread now goes through this one check. Returns True
# iff `my_proc` is still the authoritative process for this session AND the
# session isn't mid-interrupt (kill in flight, new proc not yet registered).
#
# Rationale: the old `session.get('proc') is my_proc` check was correct as
# far as it went, but `agent_interrupt` kills the old proc BEFORE the new
# one is spawned and registered, so the old reader's finally block could
# still pass that check during the kill→respawn gap and emit a stale
# terminal status (`error`/`completed`) that flipped the UI to "stopped".
# The `_interrupting` flag closes that gap: it is set under the lock at the
# top of `agent_interrupt`, cleared under the lock when the new proc is
# assigned to `session['proc']`. While set, the old reader's writes are
# discarded, the new reader's writes are still legitimate (it always passes
# `proc is session['proc']`).
def _session_owned_by(session, my_proc):
    """True iff `my_proc` is still the authoritative proc for this session."""
    if session.get('_interrupting'):
        return False
    return session.get('proc') is my_proc


def _read_agent_stream(proc, session):
    """Reader thread: captures stdout lines into session log_lines."""
    # Snapshot the proc we were launched with so we can detect if a follow-up
    # replaced us with a newer process while we were still draining stdout.
    my_proc = proc
    try:
        for raw_line in proc.stdout:
            obs.heartbeat('stream-reader:a')  # Phase 2 loop observability (1.12)
            # If session proc changed (or interrupt in flight), a follow-up
            # superseded us — stop writing.
            if not _session_owned_by(session, my_proc):
                break
            line = raw_line.rstrip('\n\r')
            if not line:
                continue
            # Try to parse stream-json output
            try:
                msg = json.loads(line)
                # Defensive: json.loads on a bare-string line returns a str
                # (e.g. a quoted error blob), which would crash msg.get()
                # below with `'str' object has no attribute 'get'` and kill
                # the reader. Treat any non-dict envelope as non-JSON noise.
                if not isinstance(msg, dict):
                    raise json.JSONDecodeError('non-dict JSON envelope', line, 0)
                msg_type = msg.get('type', '')
                # Capture Claude CLI session UUID from init or result messages
                if 'session_id' in msg:
                    _note_claude_sid(session, msg['session_id'])
                # Refresh the account-global system status cache from
                # `system/init` and `rate_limit_event` messages (every claude
                # session emits these). No-op for any other message type.
                _capture_system_init(msg)
                _mc_state._LAST_SYSTEM_STATUS['provider'] = session.get('provider', 'claude')
                if msg_type == 'stream_event':
                    _note_activity_state(session, msg)
                    continue
                if msg_type == 'assistant' and isinstance(msg.get('message'), dict):
                    # First assistant output proves a `-r` resume loaded OK (not a
                    # fragile resume that dies instantly), so a LATER process death
                    # (the Mode-B AskUserQuestion proc.kill(), idle-eviction, or a
                    # crash) re-resumes with -r instead of resetting to a context-
                    # less fresh session. Harmless for Mode A (never consulted).
                    # See _resume_is_fragile + the followup respawn path.
                    session['_resume_confirmed'] = True
                    for block in msg['message'].get('content', []) or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') == 'text':
                            # The RAW text feeds the MC Tool Protocol scanner at the
                            # turn boundary (Claude has no native AskUserQuestion when
                            # run headless — see _agent_capability_lines).
                            session.setdefault('_mc_turn_buf', []).append(block['text'])
                            # The CHAT gets it with the fence removed. Without this the
                            # user watches raw ```mc:question``` JSON scroll past and
                            # THEN gets the rendered form — confirmed against a live
                            # agent. The fence is a tool call, not prose.
                            _visible = _agent_runtime.strip_mc_tool_blocks(block['text'])
                            if _visible.strip():
                                session['log_lines'].append(_visible)
                                # Media gallery: index diagrams/images off the
                                # VISIBLE text — the same string the chat
                                # renders. Feeding it the raw transcript instead
                                # would drag in every image path sitting inside
                                # source code and tool output (measured: ~90% of
                                # such "paths" were never images). Best-effort
                                # by contract; it cannot break the turn.
                                _media.record_from_text(
                                    session.get('project_id', ''),
                                    session.get('session_id', ''),
                                    _visible,
                                    session.get('task', '') or '',
                                )
                            session['last_output_time'] = _time.time()
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name', '')
                            tool_input = block.get('input', {})
                            if not isinstance(tool_input, dict):
                                tool_input = {}
                            activity = _format_tool_activity(tool_name, tool_input)
                            session['log_lines'].append(activity)
                            session['last_output_time'] = _time.time()
                            # Track .md file edits for plan file detection
                            if tool_name in ('Write', 'Edit'):
                                fp = tool_input.get('file_path', '')
                                if fp.lower().endswith('.md'):
                                    session['_last_md_file'] = fp
                                    # A plan written into ~/.claude/plans/
                                    # registers immediately (headless-safe; no
                                    # ExitPlanMode). Feeds the PLAN tab + link.
                                    _register_plan_file(session, fp)
                            elif tool_name == 'Bash':
                                # A plan written by heredoc / cp / tee never
                                # produced a Write tool call, so it registered
                                # nowhere and the PLAN tab never showed it.
                                for _pf in _plan_paths_in_command(
                                        tool_input.get('command', '')):
                                    _register_plan_file(session, _pf)
                            elif tool_name == 'ExitPlanMode':
                                # Same containment rule as the Write/Edit path
                                # above: a plan is a .md file under
                                # ~/.claude/plans/. Without this check this
                                # branch registered ANY .md the agent had
                                # touched, and /agent/plan-file then served it.
                                _register_plan_file(session,
                                                    session.get('_last_md_file', ''))
                                session['waiting_for_plan_approval'] = True
                                session['log_lines'].append('[Plan mode exit detected — waiting for user approval]')
                            elif tool_name == 'TodoWrite':
                                try:
                                    sk = (session.get('claude_session_id')
                                          or session.get('id')
                                          or session.get('session_id'))
                                    n = _sync_todowrite_to_backlog(
                                        session.get('project_id'), sk,
                                        tool_input.get('todos', []))
                                    if n:
                                        session['log_lines'].append(
                                            f'[backlog: synced {n} item(s) from TodoWrite]')
                                except Exception as e:
                                    session['log_lines'].append(f'[backlog-sync error: {e}]')
                            elif tool_name == 'AskUserQuestion':
                                # Stable question_id so the SSE can re-emit the question
                                # to a late-connecting / reconnecting client and the
                                # client can dedupe by id (instead of dropping it).
                                _q = dict(tool_input)
                                _q['question_id'] = uuid.uuid4().hex
                                session.setdefault('pending_questions', []).append(_q)
                                session['waiting_for_question'] = True
                                # Transition to 'idle' BEFORE killing so the guardian
                                # doesn't race in and mark us 'error' when it sees a
                                # dead process with status still 'running'.
                                session['status'] = 'idle'
                                session['last_status_change_time'] = _time.time()
                                # Kill process — the auto-resolved turn is wasted.
                                # User's answer will resume the session via follow-up.
                                try:
                                    proc.kill()
                                except OSError:
                                    pass
                elif msg_type == 'result':
                    # Capture session_id from result as fallback
                    if 'session_id' in msg:
                        _note_claude_sid(session, msg['session_id'])
                    # Accumulate token usage across turns (result fires once per turn
                    # in Mode B; overwriting would discard all prior turns).
                    if 'usage' in msg:
                        _accumulate_session_usage(session, msg['usage'])
                    if 'cost_usd' in msg:
                        session['cost_usd'] = (session.get('cost_usd') or 0.0) + (msg['cost_usd'] or 0.0)
                    if 'num_turns' in msg:
                        session['num_turns'] = msg['num_turns']
                    _apply_mc_tool_blocks_for_turn(session)
                    _auto_snapshot_notes_on_turn(session)
                    _scan_result_event_for_auth(msg)
                # Web push hook: intercept PushNotification tool_use + turn results.
                _handle_push_signal(
                    session.get('project_id', ''),
                    session.get('session_id', ''),
                    msg,
                )
            except json.JSONDecodeError:
                # Non-JSON lines are claude's raw stderr — that's where real
                # auth-error sentinels appear ("Please run /login", "Invalid
                # API key"). Scanning stream-json lines was a false-positive
                # magnet: an agent's own assistant text discussing auth flows
                # ("the user might not be logged in") triggered the banner.
                _auth_reason = _scan_for_auth_error(line)
                if _auth_reason:
                    _mark_claude_auth_error(_auth_reason, line)
                session['log_lines'].append(line)
                session['last_output_time'] = _time.time()
    except Exception as e:
        # Only log stream errors if we're still the active reader
        # and the process wasn't intentionally killed (question/stop)
        if _session_owned_by(session, my_proc):
            if not session.get('waiting_for_question') and session.get('status') not in ('stopped',):
                session['log_lines'].append(f"[stream error: {e}]")
    finally:
        rc = proc.wait()
        _unregister_process(proc.pid)
        # Acquire per-project lock to prevent race with agent_stop setting 'stopped'
        with get_manager(session['project_id']).lock:
            # Single-emit gate: only update session status if we still own it.
            # Covers normal replacement (new proc assigned) AND in-flight interrupt
            # (kill issued, new proc not yet spawned — `_interrupting` flag set).
            if _session_owned_by(session, my_proc):
                # Never overwrite 'stopped' — that's a user-initiated terminal state
                if session['status'] == 'running':
                    if session.get('waiting_for_question'):
                        # Process was intentionally killed after AskUserQuestion —
                        # not an error, just waiting for user's answer
                        session['status'] = 'idle'
                        session['last_status_change_time'] = _time.time()
                    else:
                        session['status'] = 'completed' if rc == 0 else 'error'
                        session['last_status_change_time'] = _time.time()
                        if rc != 0:
                            session['log_lines'].append(f"[exited with code {rc}]")
                        if rc == 0:
                            session['recovery_attempts'] = 0
                            session['guardian_state'] = None
                            session['pending_recovery_message'] = None
                            session['circuit_breaker_tripped'] = False
                elif session['status'] == 'stopped':
                    pass  # User stopped — don't change status regardless of rc
                _log_agent_completion(session)

                # Auto-dispatch pending follow-ups
                pending = session.get('pending_followups', [])
                if pending:
                    session['_dispatching_followup'] = True
                    followup_msg = pending.pop(0)
                    _auto_dispatch_followup(session, followup_msg)
                    session.pop('_dispatching_followup', None)

        # Auto-recover failed resume (Mode A)
        if (session.get('_resume_id')
                and session.get('status') == 'error'
                and not session.get('_resume_recovery_attempted')
                and _time.time() - session.get('_dispatch_time', 0) < 60
                and not session.get('num_turns')):
            _auto_recover_failed_resume(session)


def _read_agent_stream_b(proc, session):
    """Reader thread for Mode B: persistent process with stream-json I/O.

    Unlike Mode A, the process does NOT exit after each turn.
    A 'result' message signals the end of a turn, not the end of the process.
    """
    my_proc = proc
    try:
        for raw_line in proc.stdout:
            obs.heartbeat('stream-reader:b')  # Phase 2 loop observability (1.12)
            if not _session_owned_by(session, my_proc):
                break
            line = raw_line.rstrip('\n\r')
            if not line:
                continue
            try:
                msg = json.loads(line)
                # See Mode A reader: any non-dict JSON envelope crashes the
                # reader at msg.get() with `'str' object has no attribute
                # 'get'`. Treat as non-JSON noise instead.
                if not isinstance(msg, dict):
                    raise json.JSONDecodeError('non-dict JSON envelope', line, 0)
                msg_type = msg.get('type', '')
                if 'session_id' in msg:
                    _note_claude_sid(session, msg['session_id'])
                # See Mode A reader: refresh the system-status cache.
                _capture_system_init(msg)
                _mc_state._LAST_SYSTEM_STATUS['provider'] = session.get('provider', 'claude')
                if msg_type == 'stream_event':
                    _note_activity_state(session, msg)
                    continue
                if msg_type == 'assistant' and isinstance(msg.get('message'), dict):
                    # First assistant output proves a `-r` resume loaded OK (not a
                    # fragile resume that dies instantly), so a LATER process death
                    # (the Mode-B AskUserQuestion proc.kill(), idle-eviction, or a
                    # crash) re-resumes with -r instead of resetting to a context-
                    # less fresh session. Harmless for Mode A (never consulted).
                    # See _resume_is_fragile + the followup respawn path.
                    session['_resume_confirmed'] = True
                    for block in msg['message'].get('content', []) or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') == 'text':
                            # The RAW text feeds the MC Tool Protocol scanner at the
                            # turn boundary (Claude has no native AskUserQuestion when
                            # run headless — see _agent_capability_lines).
                            session.setdefault('_mc_turn_buf', []).append(block['text'])
                            # The CHAT gets it with the fence removed. Without this the
                            # user watches raw ```mc:question``` JSON scroll past and
                            # THEN gets the rendered form — confirmed against a live
                            # agent. The fence is a tool call, not prose.
                            _visible = _agent_runtime.strip_mc_tool_blocks(block['text'])
                            if _visible.strip():
                                session['log_lines'].append(_visible)
                                # Media gallery: index diagrams/images off the
                                # VISIBLE text — the same string the chat
                                # renders. Feeding it the raw transcript instead
                                # would drag in every image path sitting inside
                                # source code and tool output (measured: ~90% of
                                # such "paths" were never images). Best-effort
                                # by contract; it cannot break the turn.
                                _media.record_from_text(
                                    session.get('project_id', ''),
                                    session.get('session_id', ''),
                                    _visible,
                                    session.get('task', '') or '',
                                )
                            session['last_output_time'] = _time.time()
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name', '')
                            tool_input = block.get('input', {})
                            if not isinstance(tool_input, dict):
                                tool_input = {}
                            activity = _format_tool_activity(tool_name, tool_input)
                            session['log_lines'].append(activity)
                            session['last_output_time'] = _time.time()
                            if tool_name in ('Write', 'Edit'):
                                fp = tool_input.get('file_path', '')
                                if fp.lower().endswith('.md'):
                                    session['_last_md_file'] = fp
                                    # A plan written into ~/.claude/plans/
                                    # registers immediately (headless-safe; no
                                    # ExitPlanMode). Feeds the PLAN tab + link.
                                    _register_plan_file(session, fp)
                            elif tool_name == 'Bash':
                                # A plan written by heredoc / cp / tee never
                                # produced a Write tool call, so it registered
                                # nowhere and the PLAN tab never showed it.
                                for _pf in _plan_paths_in_command(
                                        tool_input.get('command', '')):
                                    _register_plan_file(session, _pf)
                            elif tool_name == 'ExitPlanMode':
                                # Same containment rule as the Write/Edit path
                                # above: a plan is a .md file under
                                # ~/.claude/plans/. Without this check this
                                # branch registered ANY .md the agent had
                                # touched, and /agent/plan-file then served it.
                                _register_plan_file(session,
                                                    session.get('_last_md_file', ''))
                                session['waiting_for_plan_approval'] = True
                                session['log_lines'].append('[Plan mode exit detected — waiting for user approval]')
                            elif tool_name == 'TodoWrite':
                                try:
                                    sk = (session.get('claude_session_id')
                                          or session.get('id')
                                          or session.get('session_id'))
                                    n = _sync_todowrite_to_backlog(
                                        session.get('project_id'), sk,
                                        tool_input.get('todos', []))
                                    if n:
                                        session['log_lines'].append(
                                            f'[backlog: synced {n} item(s) from TodoWrite]')
                                except Exception as e:
                                    session['log_lines'].append(f'[backlog-sync error: {e}]')
                            elif tool_name == 'AskUserQuestion':
                                _q = dict(tool_input)
                                _q['question_id'] = uuid.uuid4().hex
                                session.setdefault('pending_questions', []).append(_q)
                                session['waiting_for_question'] = True
                                # Transition to 'idle' BEFORE killing so the guardian
                                # doesn't race in and mark us 'error' when it sees a
                                # dead process with status still 'running'.
                                session['status'] = 'idle'
                                session['last_status_change_time'] = _time.time()
                                # Kill process — the auto-resolved turn is wasted.
                                # User's answer will resume via follow-up (respawns process).
                                try:
                                    proc.kill()
                                except OSError:
                                    pass
                elif msg_type == 'result':
                    if 'session_id' in msg:
                        _note_claude_sid(session, msg['session_id'])
                    if 'usage' in msg:
                        _accumulate_session_usage(session, msg['usage'])
                    if 'cost_usd' in msg:
                        session['cost_usd'] = (session.get('cost_usd') or 0.0) + (msg['cost_usd'] or 0.0)
                    if 'num_turns' in msg:
                        session['num_turns'] = msg['num_turns']
                    _apply_mc_tool_blocks_for_turn(session)
                    _auto_snapshot_notes_on_turn(session)
                    _scan_result_event_for_auth(msg)
                    # Turn boundary — process stays alive
                    session['status'] = 'idle'
                    session['last_status_change_time'] = _time.time()
                    # Step 6: mid-session note-taker (default-off; fast-gated).
                    _maybe_checkpoint(session)
                # Web push hook: intercept PushNotification tool_use + turn results.
                _handle_push_signal(
                    session.get('project_id', ''),
                    session.get('session_id', ''),
                    msg,
                )
            except json.JSONDecodeError:
                # Auth-sentinel scan only on non-JSON lines (claude's raw
                # stderr). See Mode A reader for the false-positive history.
                _auth_reason = _scan_for_auth_error(line)
                if _auth_reason:
                    _mark_claude_auth_error(_auth_reason, line)
                session['log_lines'].append(line)
                session['last_output_time'] = _time.time()
            # Cap log_lines to prevent unbounded memory growth
            if len(session['log_lines']) > 2000:
                session['log_lines'] = session['log_lines'][-1500:]
    except Exception as e:
        if _session_owned_by(session, my_proc):
            if not session.get('waiting_for_question') and session.get('status') not in ('stopped',):
                session['log_lines'].append(f"[stream error: {e}]")
    finally:
        rc = proc.wait()
        _unregister_process(proc.pid)
        # Acquire per-project lock to prevent race with agent_stop setting 'stopped'
        with get_manager(session['project_id']).lock:
            # Single-emit gate (see _session_owned_by). Skip when interrupt
            # is in flight — the new reader will set process_alive=True/status
            # legitimately and there's no point flipping it False between.
            if _session_owned_by(session, my_proc):
                session['process_alive'] = False
                # Never overwrite 'stopped' — that's a user-initiated terminal state
                if session['status'] in ('running', 'idle'):
                    if session.get('waiting_for_question'):
                        # Process was intentionally killed after AskUserQuestion —
                        # not an error, just waiting for user's answer
                        session['status'] = 'idle'
                        session['last_status_change_time'] = _time.time()
                    else:
                        # rc!=0 while 'idle' = the turn ALREADY ended cleanly
                        # (the result event set 'idle' at the turn boundary);
                        # the nonzero exit is post-turn teardown — e.g.
                        # claude-fable-5 exits 1 after every turn under the
                        # full Mode B flag set — not a task failure. Logging
                        # it 'error' painted a red "Blocked" tile after each
                        # successful turn (2026-06-10). Mid-turn deaths still
                        # classify as error: they die with status 'running'.
                        _post_turn = session['status'] == 'idle'
                        session['status'] = 'completed' if (rc == 0 or _post_turn) else 'error'
                        session['last_status_change_time'] = _time.time()
                        if rc != 0:
                            session['log_lines'].append(f"[exited with code {rc}]")
                        if rc == 0 or _post_turn:
                            session['recovery_attempts'] = 0
                            session['guardian_state'] = None
                            session['pending_recovery_message'] = None
                            session['circuit_breaker_tripped'] = False
                elif session['status'] == 'stopped':
                    pass  # User stopped — don't change status regardless of rc
                _log_agent_completion(session)

        # Auto-recover failed resume: if we tried to resume a prior session and
        # it died quickly without producing meaningful output, restart fresh.
        if (session.get('_resume_id')
                and session.get('status') == 'error'
                and not session.get('_resume_recovery_attempted')
                and _time.time() - session.get('_dispatch_time', 0) < 60
                and not session.get('num_turns')):
            _auto_recover_failed_resume(session)


def _auto_recover_failed_resume(session):
    """When a resumed session dies immediately, silently restart fresh.

    Reuses the same session object so the frontend sees seamless recovery.
    """
    session['_resume_recovery_attempted'] = True
    project_id = session['project_id']
    task = session.get('task', '')
    mode = session.get('mode', 'A')
    resume_id = session.get('_resume_id', '')

    p = load_project(project_id)
    if not p:
        return
    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return

    session['log_lines'].append(
        f'[Resume of session {resume_id[:12]} failed — restarting fresh]')
    _log(f"[dispatch] Resume {resume_id[:12]} failed for {project_id}, retrying fresh")
    _log_agent_activity(project_id, f"Resume failed, restarting fresh: {task[:80]}")

    context = _fresh_context_for(p, session, task or '')
    fresh_task = (f"[Continuing from a previous conversation (session {resume_id}) that could not "
                  f"be resumed. Start fresh but continue the user's request below.]\n\n{task}")

    try:
        if mode == 'B':
            _sp_args, _sp_path = _sysprompt_file_args(context)
            cmd = [_resolve_claude(), *_build_claude_flags(p, streaming=True),
                   *_sp_args]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
            _sysprompt_cleanup(_sp_path, proc)
            initial_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": fresh_task}
            }) + '\n'
            proc.stdin.write(initial_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
            proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)

            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Agent (Mode B, fresh retry)', 'agent',
                              session['session_id'], project_id, task[:80])

            mgr = get_manager(project_id)
            with mgr.lock:
                session['proc'] = proc
                session['status'] = 'running'
                session['process_alive'] = True
                session['stdin_lock'] = threading.Lock()
                session['last_output_time'] = _time.time()
                session['last_status_change_time'] = _time.time()
                session['_resume_id'] = None  # no longer a resume
                session['guardian_state'] = None
                session['recovery_attempts'] = 0
                session['circuit_breaker_tripped'] = False

            threading.Thread(target=_read_agent_stream_b, args=(proc, session), daemon=True).start()

        else:
            # Mode A
            _sp_args, _sp_path = _sysprompt_file_args(context)
            cmd = [_resolve_claude(), '-p', fresh_task, *_build_claude_flags(p),
                   *_sp_args]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
            _sysprompt_cleanup(_sp_path, proc)
            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Agent (Mode A, fresh retry)', 'agent',
                              session['session_id'], project_id, task[:80])

            mgr = get_manager(project_id)
            with mgr.lock:
                session['proc'] = proc
                session['status'] = 'running'
                session['last_output_time'] = _time.time()
                session['last_status_change_time'] = _time.time()
                session['_resume_id'] = None
                session['guardian_state'] = None
                session['recovery_attempts'] = 0
                session['circuit_breaker_tripped'] = False

            threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True).start()

    except Exception as e:
        session['log_lines'].append(f'[Fresh restart also failed: {e}]')
        session['status'] = 'error'
        _log(f"[dispatch] Fresh retry failed for {project_id}: {e}")


def _load_agent_log(project_id):
    """Load the agent summary log for a project."""
    filepath = DATA_DIR / f'{project_id}_agent_log.json'
    if not filepath.exists():
        return []
    try:
        return json.loads(filepath.read_text(encoding='utf-8'))
    except Exception:
        return []


@bp.route('/api/session/trigger-type')
def get_session_trigger_type():
    """Look up the trigger_type MC recorded for a Claude session at dispatch
    time, given its claude_session_id.

    This is what `mc.secrets_store` calls to auto-detect unattended context
    (MC-923) instead of trusting a `--unattended` flag the calling agent typed
    itself — trigger_type is set server-side when MC dispatches the session and
    the agent process cannot rewrite it, so a claude_session_id it can prove it
    holds (via the CLI-set `CLAUDE_CODE_SESSION_ID` env var, not anything the
    agent's own command line controls) is a trustworthy lookup key.

    Checked in two places because `claude_session_id` lands on the persisted
    agent_log row late (`_note_claude_sid` only backfills it early for non-
    manual triggers; a manual row's csid stays '' until the session completes)
    — the live `agent_sessions` dict has it as soon as Claude assigns it, which
    is exactly the in-progress case a mid-session `with-secret.py` call needs.
    Falls back to every project's persisted log for sessions that already
    exited. No auth (matches this project's existing localhost-trust posture,
    e.g. /api/config — see MC-914).
    """
    csid = (request.args.get('claude_session_id') or '').strip()
    if not csid:
        return jsonify({'found': False, 'error': 'claude_session_id required'}), 400
    for s in agent_sessions.values():
        if s.get('claude_session_id') == csid:
            return jsonify({'found': True, 'trigger_type': s.get('trigger_type') or 'manual'})
    for log_file in DATA_DIR.glob('*_agent_log.json'):
        try:
            entries = json.loads(log_file.read_text(encoding='utf-8'))
        except Exception:
            continue
        for e in entries:
            if e.get('claude_session_id') == csid:
                return jsonify({'found': True, 'trigger_type': e.get('trigger_type') or 'manual'})
    return jsonify({'found': False})


def _save_agent_log(project_id, log):
    """Persist the agent log, trimming to the most recent N entries.

    Entries are inserted at index 0 (newest first), so list[:N] keeps the newest.
    Cap is `agent_log_max_entries` in config.json (default 500). Set to 0 to
    disable trimming (keep everything — file grows unbounded).
    """
    filepath = DATA_DIR / f'{project_id}_agent_log.json'
    cap = int(state.CONFIG.get('agent_log_max_entries', 500) or 0)
    if cap > 0 and len(log) > cap:
        log = log[:cap]
    filepath.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')

def _session_usage_payload(session: dict) -> dict:
    """Build the usage/cost/turns slice of an SSE payload, gated on provider capabilities.

    Providers that don't emit cost or turns (e.g. Gemini) must NOT fabricate zeros —
    the frontend reads absence of the key as "this provider doesn't support it" and
    hides the corresponding counter rather than showing 0.

    Always includes 'usage' when emits_usage is True (it is the authoritative token
    counter).  cost_usd and num_turns are only included when their respective
    capability flags are set.
    """
    provider = (session.get('provider') or 'claude').lower()
    try:
        caps = _agent_runtime.get_runtime(provider).capabilities()
    except Exception:
        # Fallback: treat as Claude (full telemetry) so we never silently drop data.
        caps = _agent_runtime.get_runtime('claude').capabilities()

    out: dict = {}
    if caps.emits_usage:
        out['usage'] = session.get('usage', {})
    if caps.emits_cost:
        out['cost_usd'] = session.get('cost_usd', 0)
    if caps.emits_num_turns:
        out['num_turns'] = session.get('num_turns', 0)
    return out

def _transcript_buffer_default() -> int:
    """How many transcript messages a restored chat buffer carries.

    ONE number, because four call sites disagreeing is what caused the bug:
    dispatch-preload, /reconstruct and the preview endpoint all passed 300,
    while the revival path silently took the old default of 40. A revived
    conversation therefore opened 85% truncated — measured on a live 269-message
    chat, the visible history began mid-thread and the user could not scroll
    back to their own opening question.

    Only the VISIBLE buffer is affected either way: the model keeps full context
    through `claude -r <csid>`, which is why this reads as a UI bug rather than
    amnesia.
    """
    try:
        return max(1, int(state.CONFIG.get('transcript_buffer_max_messages', 300)))
    except Exception:
        return 300


def _revive_history_lines(project_path, claude_sid, user_label, max_messages=None):
    """Reconstruct prior-conversation log_lines from the on-disk Claude transcript.

    On server restart a session is revived with `-r <claude_sid>` so the model
    keeps context, but the visible chat buffer would otherwise start empty —
    the user sees only their own new message, not the agent's earlier reply
    (this is what makes a tapped push land on a one-sided chat). Re-render the
    last `max_messages` turns from the transcript in the same buffer format the
    live stream uses ('> Label: ...' for user turns, raw text for assistant).

    Returns [] on any failure (no transcript, parse error) — revival still
    proceeds, just without restored history.
    """
    lines = _transcript_buffer_lines(project_path, claude_sid, user_label,
                                     max_messages=max_messages)
    if lines:
        # Say so at the TOP when there is more above. Scrolling up and simply
        # running out of conversation is indistinguishable from having lost it.
        notice = _buffer_truncation_notice(
            project_path, claude_sid,
            max_messages if max_messages is not None else _transcript_buffer_default())
        if notice:
            lines.insert(0, notice)
        lines.append('[— restored from transcript; conversation continues below —]')
    return lines


def _buffer_truncation_notice(project_path, claude_sid, shown):
    """A line saying history was cut, when it was — silence here is what made
    the truncation read as lost data rather than a display limit."""
    try:
        f = _find_transcript_file(project_path, claude_sid)
        if not f:
            return None
        total = len(_parse_transcript_messages(f, max_messages=100000))
        if total > shown:
            return (f'[— earlier history not shown: {total - shown} of {total} '
                    f'messages above this point. The agent still has the full '
                    f'conversation. —]')
    except Exception:
        pass
    return None


def _transcript_buffer_lines(project_path, claude_sid, user_label, max_messages=None):
    """Render a Claude .jsonl transcript into chat-buffer lines (user+assistant).

    Same line format the live stream/log_lines uses: '> Label: ...' for user
    turns (single buffer entry, leading/trailing newline like the live seed),
    raw text for assistant turns. tool_call/error rows are skipped — the chat
    renders user/assistant bubbles and tool noise isn't wanted here. Returns
    [] on any failure so callers can degrade gracefully.
    """
    if max_messages is None:
        max_messages = _transcript_buffer_default()
    try:
        f = _find_transcript_file(project_path, claude_sid)
        if not f:
            return []
        msgs = _parse_transcript_messages(f, max_messages=max_messages)
        lines = []
        for m in msgs:
            role = m.get('role')
            if role == 'user':
                # Harness-injected user turns are NOT something the user typed:
                # a <task-notification> from a finished background agent, a
                # <system-reminder>, the resume/brevity/phone preambles. Rendered
                # verbatim they become "> Ron: <task-notification>..." bubbles, and
                # since every revive re-renders the WHOLE transcript, each revive
                # replays the entire backlog of subagent pings as fake user
                # messages (observed: one task id shown 9x over 3 days).
                # Same two helpers the conversation-label path already uses:
                # strip closed blocks, then drop a turn that is nothing but an
                # unclosed/truncated one.
                txt = _agent_runtime.strip_injected_preamble(m.get('text') or '')
                if txt and not _agent_runtime.is_nonuser_message(txt):
                    lines.append(f"\n> {user_label}: {txt}\n")
            elif role == 'assistant':
                txt = (m.get('text') or '').strip()
                if txt:
                    lines.append(txt)
        return lines
    except Exception as e:
        _log(f"[transcript-render] failed: {e}")
        return []

def _revive_from_agent_log(project_id, session_id, message, p):
    """Revive a finalized/purged session by spawning a fresh process with -r <claude_session_id>.

    Looks up the most recent agent_log entry whose session_id matches; if it has a
    claude_session_id we can resume from, builds a new session dict that reuses the
    same session_id so the frontend's UI tab stays addressed.

    Roll back: set CONFIG['agent_revive_from_log'] = False (the only call site checks
    this flag before calling). Or delete this function and the gated block in
    agent_followup.

    Returns the new session dict on success, None if not revivable (no matching
    log entry, no claude_session_id, missing project_path, or spawn failure).
    """
    if not state.CONFIG.get('agent_revive_from_log', True):
        return None

    log = _load_agent_log(project_id)
    entry = next((e for e in log if e.get('session_id') == session_id), None)
    if not entry:
        return None
    claude_sid = entry.get('claude_session_id')
    if not claude_sid:
        return None

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return None

    use_streaming = p.get('use_streaming_agent', state.CONFIG.get('use_streaming_agent', False))

    # WHO this conversation is. A revive re-spawns the process from scratch, so
    # the system prompt is rebuilt from flags — and it used to be rebuilt with
    # no persona at all, which is how a chat started with Dave came back after a
    # pause as Vector (the project's plain agent name). Same conversation,
    # different agent, and no event anywhere to explain it.
    #
    # `_prior_character` is keyed on the claude_session_id and skips entries
    # that carry no persona, so it also self-heals rows this bug already
    # blanked. A persona is resolved once, at spawn, and is immutable for the
    # chat's lifetime — so any entry of this conversation that names one names
    # THE one.
    _revive_character = None
    _revive_char_body, _revive_char_name, _revive_char_skills = '', '', []
    try:
        _prior = _prior_character(project_id, claude_sid)
        if _prior:
            _revive_character, _revive_char_body = _resolve_character(pp, _prior, p)
            _revive_char_name = (_revive_character or {}).get('agent_name') or ''
            _revive_char_skills = (_revive_character or {}).get('skills') or []
    except Exception as e:
        # Never block a revive on this: coming back as the wrong agent is bad,
        # not coming back at all is worse.
        _log(f"[revive] {project_id}: could not recover the persona for "
             f"{claude_sid[:12]}: {e}")

    too_large, size_bytes = _session_too_large(pp, claude_sid)
    resume_flags = []
    context = None
    revival_msg = message
    if too_large:
        size_mb = size_bytes / (1024 * 1024)
        context = _build_agent_context(p, task=message or '',
                                       character_body=_revive_char_body,
                                       character_name=_revive_char_name,
                                       character_skills=_revive_char_skills)
        revival_msg = (f"[Resuming a previous conversation that grew too large to "
                       f"resume directly ({size_mb:.0f} MB). Start fresh but continue "
                       f"the user's request below.]\n\n{message}")
    else:
        resume_flags = ['-r', claude_sid]
    if context is None:
        # A -r revival must re-append the context too — CLI ≥2.1.206 rebuilds
        # the system prompt from flags on every invocation, so a bare -r drops
        # rules/read-floor/API reference (see _respawn_sysprompt_args). The
        # in-memory stash died with the old MC process, so rebuild fresh.
        try:
            context = _build_agent_context(p, task=message or '',
                                           character_body=_revive_char_body,
                                           character_name=_revive_char_name,
                                           character_skills=_revive_char_skills)
        except Exception as e:
            _log(f"[revive] {project_id}: context rebuild failed: {e}")

    mgr = get_manager(project_id)
    mgr.ensure_guardian()
    user_label = state.CONFIG.get('user_name') or 'User'
    revive_note = f'[Session revived from agent log — resuming claude_session={claude_sid[:12]}]'
    # Restore the prior conversation into the visible buffer so a tapped push
    # (about the agent's pre-restart reply) doesn't land on a one-sided chat.
    # Skipped when the transcript was too large to resume directly (we started
    # fresh, so there's no coherent -r history to show anyway).
    history_lines = [] if too_large else _revive_history_lines(pp, claude_sid, user_label)
    seed_lines = history_lines + [revive_note, f"\n> {user_label}: {message}\n"]

    if use_streaming:
        cmd = [_resolve_claude(), *resume_flags, *_build_claude_flags(p, streaming=True)]
        _sp_path = None
        if context:
            _sp_args, _sp_path = _sysprompt_file_args(context)
            cmd.extend(_sp_args)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except Exception as e:
            _log(f"[revive] {project_id}: spawn failed: {e}")
            if _sp_path:
                try:
                    os.unlink(_sp_path)
                except OSError:
                    pass
            return None
        _sysprompt_cleanup(_sp_path, proc)
        threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
        _register_process(proc, 'Agent revived (B)', 'agent', session_id, project_id, message[:80])

        session = {
            'proc': proc,
            'status': 'running',
            'task': entry.get('task', ''),
            'log_lines': list(seed_lines),
            'started_at': now_iso(),
            'session_id': session_id,
            'project_id': project_id,
            'mode': 'B',
            'stdin_lock': threading.Lock(),
            'process_alive': True,
            'last_output_time': _time.time(),
            'last_status_change_time': _time.time(),
            'guardian_state': None,
            'recovery_attempts': 0,
            'last_recovery_time': 0,
            'pending_recovery_message': None,
            'circuit_breaker_tripped': False,
            'claude_session_id': claude_sid,
            '_resume_id': claude_sid,
            '_resume_confirmed': False,   # a just-spawned resume hasn't proven itself yet
            '_dispatch_time': _time.time(),
            'usage': entry.get('usage', {}),
            'cost_usd': entry.get('cost_usd', 0),
            'num_turns': entry.get('num_turns', 0),
            '_system_prompt': context or '',
            # Carried so the header pill, the NEXT respawn and every agent_log
            # entry from here on still know who is talking. Without it the
            # revive would fix one turn and the turn after it would lose the
            # persona again — which is exactly what the null `character` rows
            # in the log are.
            'character': _revive_character,
        }
        with mgr.lock:
            agent_sessions[session_id] = session
            mgr.session_ids.add(session_id)
        threading.Thread(target=_read_agent_stream_b, args=(proc, session), daemon=True).start()
        stdin_msg = json.dumps({"type": "user", "message": {"role": "user", "content": revival_msg}}) + '\n'
        with session['stdin_lock']:
            try:
                proc.stdin.write(stdin_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
            except Exception as e:
                session['log_lines'].append(f'[stdin write error on revive: {e}]')
        _log(f"[revive] {project_id}: Mode B revived session {session_id} via -r {claude_sid[:12]}")
        return session

    # Mode A
    _sp_args, _sp_path = _sysprompt_file_args(context)
    cmd = [_resolve_claude(), *resume_flags, '-p', revival_msg,
           *_build_claude_flags(p), *_sp_args]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=pp,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
    except Exception as e:
        _log(f"[revive] {project_id}: spawn failed: {e}")
        if _sp_path:
            try:
                os.unlink(_sp_path)
            except OSError:
                pass
        return None
    _sysprompt_cleanup(_sp_path, proc)
    threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
    _register_process(proc, 'Agent revived (A)', 'agent', session_id, project_id, message[:80])

    session = {
        'proc': proc,
        'status': 'running',
        'task': entry.get('task', ''),
        'log_lines': list(seed_lines),
        'started_at': now_iso(),
        'session_id': session_id,
        'project_id': project_id,
        'mode': 'A',
        'last_output_time': _time.time(),
        'last_status_change_time': _time.time(),
        'guardian_state': None,
        'recovery_attempts': 0,
        'last_recovery_time': 0,
        'pending_recovery_message': None,
        'circuit_breaker_tripped': False,
        'claude_session_id': claude_sid,
        '_resume_id': claude_sid,
        '_dispatch_time': _time.time(),
        'usage': entry.get('usage', {}),
        'cost_usd': entry.get('cost_usd', 0),
        'num_turns': entry.get('num_turns', 0),
        '_system_prompt': context or '',
        'character': _revive_character,   # same reason as Mode B above
    }
    with mgr.lock:
        agent_sessions[session_id] = session
        mgr.session_ids.add(session_id)
    threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True).start()
    _log(f"[revive] {project_id}: Mode A revived session {session_id} via -r {claude_sid[:12]}")
    return session


def _revive_non_claude_from_agent_log(project_id, session_id, message, p):
    """Start a BRAND-NEW session for a dead non-Claude conversation (MC-929).

    Unlike `_revive_from_agent_log`, this is not a resume — Mode A providers
    (Gemini, ...) keep no transcript and the CLI has no `-r` equivalent, so
    there is nothing to reattach to. It adopts the same MC session_id so the
    UI tab and agent-log stay addressed to one conversation, but the process
    starts cold, with no prior turns as context.
    This exists so the honest trailing line `reconstruct_dead_session` writes
    for a read-only non-Claude history ("sending a message starts a
    brand-new session") is actually true, instead of the reply just 404ing.

    Returns the session_id on success, None if not revivable (no matching log
    entry, entry belongs to the Claude path, unknown provider, or dispatch
    failure).
    """
    entries = [e for e in _load_agent_log(project_id) if e.get('session_id') == session_id]
    if not entries:
        return None
    entries.sort(key=lambda e: e.get('ts', ''))
    entry = entries[-1]
    if entry.get('claude_session_id'):
        return None  # the Claude -r path owns this one
    provider = (entry.get('provider') or '').lower()
    if not provider or provider == 'claude':
        return None
    character_name = ((entry.get('character') or {}).get('name')) or ''
    try:
        _dispatch_agent_internal(project_id, message, incognito=False,
                                 reuse_session_id=session_id,
                                 provider_override=provider,
                                 character=character_name)
    except Exception as e:
        _log(f"[revive-non-claude] {project_id}: dispatch failed: {e}")
        return None
    return session_id


def _accumulate_session_usage(session, turn_usage):
    """Merge a single turn's usage dict into the running session total.

    CC emits one `result` event per turn in Mode B (persistent). Each event
    carries only THAT turn's token counts, not a cumulative total. Overwriting
    session['usage'] discards all prior turns; instead we sum the numeric
    fields so the final value reflects the whole session.
    """
    _INT_FIELDS = ('input_tokens', 'output_tokens',
                   'cache_read_input_tokens', 'cache_creation_input_tokens')
    prev = session.get('usage') or {}
    merged = dict(prev)
    for k in _INT_FIELDS:
        merged[k] = int(prev.get(k) or 0) + int(turn_usage.get(k) or 0)
    # Carry non-numeric metadata from the latest turn (service_tier, etc.)
    for k, v in turn_usage.items():
        if k not in _INT_FIELDS:
            merged[k] = v
    session['usage'] = merged


def _note_claude_sid(session, sid):
    """Record the Claude CLI session UUID on the live session and, the first time
    it becomes known for a non-manual (scheduled/hivemind) trigger, backfill it
    into the still-'in_progress' agent_log row.

    Chain fix (Defect B): _log_agent_completion is the only OTHER writer of
    claude_session_id into the log, and for a persistent Mode-B session it does
    not run until the process tears down (CLAUDE.md "Mode B caveat"). Without
    this backfill, _latest_claude_sid_for_schedule / _revive_from_agent_log find
    an empty csid on the pending row and every scheduled fire cold-starts a new
    conversation. Called from both stream readers on every message carrying a
    session_id; the prev==sid early-out makes it a cheap no-op after the first
    capture (no repeated agent_log IO on the hot path)."""
    if not sid:
        return
    prev = session.get('claude_session_id')
    session['claude_session_id'] = sid
    if prev == sid:
        return
    tt = session.get('trigger_type')
    if not tt or tt == 'manual':
        return
    if session.get('incognito') or session.get('housekeeping'):
        return
    pid = session.get('project_id')
    msid = session.get('session_id', '')
    if not pid or not msid:
        return
    try:
        log = _load_agent_log(pid)
        for e in log:
            if e.get('session_id') == msid and e.get('status') == 'in_progress':
                if e.get('claude_session_id') != sid:
                    e['claude_session_id'] = sid
                    _save_agent_log(pid, log)
                break
    except Exception as ex:
        _log(f"[csid-backfill] {pid}: {ex}")

def _log_agent_dispatch_pending(session):
    """Write a placeholder agent_log row at dispatch time so trigger correlation
    survives a server restart that kills the session before _log_agent_completion
    can run.

    Without this, scheduled / hivemind sessions that are still running (or are
    Mode B sessions sitting idle forever) appear in the log only after either
    (a) a clean finalization (rare for long-lived idle Mode B), or (b) a startup
    transcript backfill — and the backfill cannot recover trigger_type/trigger_id,
    so the schedule's "Runs" panel filter (`trigger_type==schedule AND trigger_id==X`)
    finds nothing. By dropping a row immediately, the trigger info is durable from
    the moment we spawn the process.

    Caller: _dispatch_agent_internal, only when trigger_type != 'manual'.
    Manual dispatches don't need correlation and would just double the agent_log
    write traffic for the common case.
    """
    project_id = session.get('project_id')
    if not project_id or session.get('incognito') or session.get('housekeeping'):
        return
    sid = session.get('session_id', '')
    if not sid:
        return
    entry = {
        'ts': now_iso(),
        'task': session.get('task', ''),
        'status': 'in_progress',
        'summary': '',
        'session_id': sid,
        'claude_session_id': '',  # populated on completion (Claude assigns this after first message)
        'started_at': session.get('started_at', ''),
        'usage': {},
        'cost_usd': 0,
        'num_turns': 0,
        'plan_file': '',
        'plan_files': [],
        'hivemind_id': session.get('hivemind_id', ''),
        'hivemind_ws_id': session.get('hivemind_ws_id', ''),
        'hivemind_role': session.get('hivemind_role', ''),
        'trigger_type': session.get('trigger_type', 'manual'),
        'source': session.get('source', ''),
        'trigger_id': session.get('trigger_id', ''),
        'character': session.get('character'),
    }
    try:
        log = _load_agent_log(project_id)
        # Upsert: a continued scheduled run reuses the prior run's session_id
        # (see _dispatch_agent_internal reuse_session_id). Refresh that row in
        # place — reset it to in_progress for this fire, preserve any csid
        # already captured — instead of orphaning a fresh row every cadence
        # tick. New session_ids fall through to insert(0) as before.
        existing_i = next((i for i, e in enumerate(log)
                           if e.get('session_id') == sid), None)
        if existing_i is not None:
            prev = log[existing_i]
            entry['claude_session_id'] = prev.get('claude_session_id', '')
            entry['started_at'] = prev.get('started_at', '') or entry['started_at']
            log.pop(existing_i)
            log.insert(0, entry)
        else:
            log.insert(0, entry)
        _save_agent_log(project_id, log)
    except Exception as e:
        _log(f"[dispatch-log] {project_id}: pending write failed: {e}")

def _log_agent_completion(session):
    """Save a summary entry when an agent session finishes."""
    project_id = session.get('project_id')
    if not project_id:
        return

    # Worktree isolation (b264200a): merge this agent's committed work back
    # into the base branch before anything else. Runs FIRST and outside the
    # incognito/housekeeping early-returns below, because stranding an agent's
    # commits on a private branch would turn isolation into silent work loss —
    # the exact failure this feature exists to prevent. Best-effort; a conflict
    # preserves the branch and tells the user rather than auto-resolving.
    if session.get('_worktree_isolated'):
        try:
            _worktree_merge_back_on_end(session)
        except Exception as e:
            _log(f"[worktree] merge-back hook failed: {e}")

    # Incognito sessions are fully ephemeral from MC's perspective: no agent_log
    # entry, no memory append, no condense trigger. The Claude transcript on
    # disk is unaffected (that's outside MC's control).
    if session.get('incognito'):
        return

    # Skip memory append and condense for housekeeping sessions (prevents circular triggers)
    is_housekeeping = session.get('housekeeping', False)

    # Take the last non-empty text block as the summary
    lines = session.get('log_lines', [])
    # Find the last substantial text (skip tool/status markers)
    summary = ''
    for line in reversed(lines):
        if line and not line.startswith('[') and not line.startswith('\n---'):
            summary = line
            break
    if not summary and lines:
        summary = lines[-1]

    # Extract token telemetry from the transcript before building the entry.
    # Best-effort: failures silently produce empty telemetry.
    _telemetry = {}
    if not is_housekeeping and not session.get('incognito'):
        try:
            _tp = load_project(project_id)
            _pp = (_tp or {}).get('project_path', '')
            _csid = session.get('claude_session_id', '')
            if _pp and _csid:
                _tf = _find_transcript_file(_pp, _csid)
                _telemetry = _extract_transcript_telemetry(_tf)
        except Exception:
            pass

    entry = {
        'ts': now_iso(),
        'task': session.get('task', ''),
        'status': session.get('status', 'unknown'),
        'summary': summary[:2000],
        'session_id': session.get('session_id', ''),
        'claude_session_id': session.get('claude_session_id', ''),
        'started_at': session.get('started_at', ''),
        'usage': session.get('usage', {}),
        'cost_usd': session.get('cost_usd', 0),
        'num_turns': session.get('num_turns', 0),
        'plan_file': session.get('plan_file', ''),
        'plan_files': _session_plan_files(session),
        'hivemind_id': session.get('hivemind_id', ''),
        'hivemind_ws_id': session.get('hivemind_ws_id', ''),
        'hivemind_role': session.get('hivemind_role', ''),
        # Trigger correlation: lets us list runs by what spawned them.
        # trigger_type: 'manual' | 'schedule' | 'hivemind_orchestrator' | 'hivemind_worker'
        # trigger_id: schedule_id, hivemind_id, or workstream_id depending on type
        'trigger_type': session.get('trigger_type', 'manual'),
        'source': session.get('source', ''),
        'trigger_id': session.get('trigger_id', ''),
        # Provider that ran this session ('claude', 'gemini', ...). Absent on
        # pre-multi-provider entries; treat missing as 'claude' when reading.
        'provider': session.get('provider', 'claude'),
        # Per-chat persona {name,scope,display_name} or None — survives restart
        # so the header pill + conversation marker render on reload.
        'character': session.get('character'),
        # Fix B marker: whether this session's memory was captured. Presence of
        # this key on ANY entry means the log was written by Fix-B-aware code
        # (used by the reconciler to distinguish first-boot baseline).
        'scribed': False,
        # Token telemetry from transcript (indicative; populated going forward).
        'model': _telemetry.get('model', ''),
        'input_tokens': _telemetry.get('input_tokens', 0),
        'output_tokens': _telemetry.get('output_tokens', 0),
        'cache_read_tokens': _telemetry.get('cache_read_tokens', 0),
        'model_tokens': _telemetry.get('model_tokens', {}),
    }
    log = _load_agent_log(project_id)
    # Upsert: if a pending entry was written at dispatch time (non-manual trigger),
    # replace it in place so trigger_type/trigger_id survive the rewrite. Otherwise
    # insert at the top as before. Move the row to position 0 on update so newest-
    # finalized stays at the top (matches the "log.insert(0, ...)" convention).
    sid = entry['session_id']
    replaced = False
    if sid:
        for i, e in enumerate(log):
            if e.get('session_id') == sid and e.get('status') == 'in_progress':
                log.pop(i)
                replaced = True
                break
    log.insert(0, entry)
    _save_agent_log(project_id, log)

    if is_housekeeping:
        return

    # Auto-append session summary to project memory (native Claude MEMORY.md).
    # NOT gated to 'completed' only: error/stopped sessions are exactly where
    # "what was tried / why it broke" is most valuable, and the scribe reads
    # the .jsonl (not stdout) so it doesn't need a clean summary. The hard
    # MC-kill case (this function never runs) is closed by the startup
    # scribe-reconciliation pass, not here. SPEC §3 Leg A.
    status = session.get('status')
    if status in ('completed', 'error', 'stopped'):
        try:
            p = load_project(project_id)
            if p and _write_session_memory(p, session, status, summary,
                                           entry['ts'][:10]):
                # Persist the Fix B marker so the startup reconciler never
                # re-scribes a session the completion path already captured.
                entry['scribed'] = True
                _save_agent_log(project_id, log)
        except Exception:
            pass  # never fail the completion flow for memory


def _runtime_log_completion(ev, session):
    """`on_process_exit` hook for runtime-owned (non-claude) readers — MC-930.

    The two claude stream readers call `_log_agent_completion` from their own
    `finally` blocks; nothing did for a runtime-dispatched session, because the
    runtime owns its reader thread. So a finished Gemini chat wrote NO agent-log
    row at all, which starved BOTH downstream fixes: MC-929's conversation-rail
    union has no rows to union, and MC-922's log_lines Scribe fallback never
    fires because the Scribe is triggered from the completion path.

    This is a hook, not a second writer. `_log_agent_completion` stays the ONE
    place an agent-log row is written and the ONE place the Scribe is triggered
    — two independent writers of that log is how this drift started.

    Fires once per PROCESS EXIT, which for Mode A (every non-claude runtime
    today, one respawned process per turn) means one row per turn — exactly the
    shape `_non_claude_conversation_rows` groups on. Both readers already gate
    the callback on `session['proc'] is proc`, so a turn whose process was
    replaced mid-flight is skipped, matching the claude readers'
    `_session_owned_by` gate.

    Takes the per-project lock for the same reason the claude readers do: it
    serialises the read-modify-write of the agent log against a concurrent
    agent_stop. Never raises — a completion hook must not kill the reader
    thread during teardown.
    """
    try:
        project_id = session.get('project_id')
        if not project_id:
            return
        with get_manager(project_id).lock:
            _log_agent_completion(session)
    except Exception as e:
        _log(f"[runtime-completion] agent-log write failed: {e}")


# Wired onto every SessionHandle MC hands a non-claude runtime. Module-level
# and stateless on purpose: the runtime passes the session dict back into the
# callback, so there is nothing per-session to close over — and the followup
# paths rebuild their own SessionHandle, so a per-dispatch closure would be
# silently dropped from turn 2 onward (which is how a fix here logs the first
# turn of a chat and nothing after it).
_RUNTIME_CALLBACKS = {'on_process_exit': _runtime_log_completion}


def _auto_dispatch_followup(session, message):
    """Auto-dispatch a queued follow-up after the current task completes."""
    project_id = session.get('project_id')
    p = load_project(project_id)
    if not p:
        session['log_lines'].append('[follow-up skipped: project not found]')
        return
    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        session['log_lines'].append('[follow-up skipped: project path invalid]')
        return

    claude_sid = session.get('claude_session_id')
    if claude_sid:
        resume_flags = ['-r', claude_sid]
    else:
        resume_flags = ['--continue']

    _sp_args, _sp_path = _respawn_sysprompt_args(session, p, message)
    # Honor a per-chat model pin (Mode A respawns per turn — without this the
    # pinned conversation would drop back to the project/global model).
    _pin = session.get('pinned_model') or None
    cmd = [_resolve_claude(), *resume_flags, '-p', message,
           *_build_claude_flags(p, model_override=_pin), *_sp_args]
    if _pin:
        session['model'] = _pin
        session['model_source'] = 'manual'

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=pp,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=_POPEN_FLAGS,
            startupinfo=_STARTUPINFO,
        )
    except Exception as e:
        session['log_lines'].append(f'[follow-up failed: {e}]')
        if _sp_path:
            try:
                os.unlink(_sp_path)
            except OSError:
                pass
        return
    _sysprompt_cleanup(_sp_path, proc)

    threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
    old_proc = session.get('proc')
    if old_proc:
        _unregister_process(old_proc.pid)
    session['proc'] = proc
    session['status'] = 'running'
    session['last_status_change_time'] = _time.time()
    session['last_output_time'] = _time.time()
    session['pending_recovery_message'] = None
    _register_process(proc, 'Agent followup (A)', 'agent',
                      session['session_id'], session['project_id'], message[:80])
    user_label = state.CONFIG.get('user_name') or 'User'
    session['log_lines'].append(f"> {user_label}: {message}")

    t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
    t.start()


def _check_context_budget(project, appended_prompt):
    """Measure context files; if total exceeds 20KB, trigger condensation and return info string."""
    sizes = {}
    pp = project.get('project_path', '')
    # CLAUDE.md in project root
    if pp:
        claude_md = Path(pp) / 'CLAUDE.md'
        if claude_md.exists():
            try:
                sizes['CLAUDE.md'] = claude_md.stat().st_size
            except OSError:
                pass
    # MEMORY.md (native path)
    mem_path = _get_memory_path(project)
    if mem_path and mem_path.exists():
        try:
            sizes['MEMORY.md'] = mem_path.stat().st_size
        except OSError:
            pass
    sizes['prompt'] = len(appended_prompt.encode('utf-8'))
    total = sum(sizes.values())
    if total > 40 * 1024:
        parts = ', '.join(f'{k}: {v/1024:.1f}k' for k, v in sizes.items())
        # Actively trigger condensation instead of just warning
        if _should_condense(project, include_claude_md=True):
            _dispatch_condense(project)
            return f'[context trim] Auto-condensing context files ({parts}) — will be smaller next session.'
        # If condensation is already running or disabled, just note it
        pid = project['id']
        with _condense_lock:
            if pid in _condensing_projects:
                return f'[context trim] Condensation in progress ({parts}).'
        return None  # Don't warn if we can't act on it
    return None

# Coarse model-tier extraction for telemetry. Even though the router emits
# full model IDs like 'claude-haiku-4-5-20251001', we bucket by tier in
# /api/router/stats so by_pair stays small (3x3 + fallback) instead of
# exploding per model snapshot.
_ROUTER_TIER_KEYWORDS = ('haiku', 'sonnet', 'opus')


def _router_model_tier(model_name):
    s = (model_name or '').lower()
    for k in _ROUTER_TIER_KEYWORDS:
        if k in s:
            return k
    return s or 'unknown'


def _router_stat(project_id, requested_model, chosen_model, source, reason=''):
    """Auto-router telemetry — bumps a per-project counter on every dispatch.

    Shape (per docs/DISPATCH_AND_ROUTING_ANALYSIS.md §B.5):
      {
        "totals": {"manual": N, "auto": N, "fallback": N},
        "by_pair": {"opus->haiku": N, "opus->sonnet": N, ...,
                    "fallback:opus": N},
        "last_fallback": {"ts": "...Z", "reason": "..."}
      }

    Best-effort; never raises. Telemetry must never break dispatch.
    """
    try:
        fp = DATA_DIR / f'{project_id}_router_stats.json'
        stats = {}
        if fp.exists():
            try:
                stats = json.loads(fp.read_text(encoding='utf-8') or '{}')
            except Exception:
                stats = {}
        if not isinstance(stats, dict):
            stats = {}
        totals = stats.setdefault('totals', {})
        totals[source] = int(totals.get(source, 0)) + 1
        by_pair = stats.setdefault('by_pair', {})
        req_tier = _router_model_tier(requested_model)
        chosen_tier = _router_model_tier(chosen_model)
        if source == 'fallback':
            key = f'fallback:{req_tier}'
        else:
            key = f'{req_tier}->{chosen_tier}'
        by_pair[key] = int(by_pair.get(key, 0)) + 1
        if source == 'fallback':
            stats['last_fallback'] = {
                'ts': now_iso(),
                'reason': reason or 'unknown',
            }
        stats['_updated'] = now_iso()
        fp.write_text(json.dumps(stats, indent=2), encoding='utf-8')
    except Exception:
        pass  # telemetry must never break dispatch

# ── Auto model router (classifier) ──────────────────────────────────────────
# Cheap Haiku oneshot that picks one of Haiku / Sonnet / Opus for a given user
# prompt. Used to right-size dispatches and stop burning Opus budget on trivial
# Q&A. Fail-open: any error returns the caller's fallback so a flaky classifier
# never breaks the dispatch path.
#
# Single token output keeps the call small (~5 tokens total). The prompt biases
# conservative — when in doubt, pick the larger model. The classifier is opt-in
# via CONFIG['auto_model_enabled']; when off, _route_dispatch_model is a no-op
# pass-through.

_AUTO_MODEL_CLASSIFIER_PROMPT = (
    "You classify a coding-assistant request into one of three Claude models: "
    "Haiku (H), Sonnet (S), or Opus (O).\n\n"
    "Output EXACTLY one character — H, S, or O. No other text, no punctuation.\n\n"
    "Pick H ONLY when the request is clearly trivial: a single fact question, "
    "a one-line lookup, casual chat, a yes/no, or a tiny one-file edit with "
    "obvious intent.\n\n"
    "Pick O ONLY when the request is clearly complex: multi-step refactor, "
    "architecture or design decision, cross-file debugging, deep code generation, "
    "long planning, or anything that explicitly asks for careful reasoning.\n\n"
    "Pick S for everything else — most normal coding tasks land here.\n\n"
    "Bias CONSERVATIVE: prefer S over H when unsure; prefer S over O when unsure."
)

_AUTO_MODEL_VALID = {'H': 'claude-haiku-4-5-20251001', 'S': 'claude-sonnet-5', 'O': 'claude-opus-5'}


def _route_dispatch_model(prompt, fallback_model):
    """Return (model_name, source) where model_name is a full Claude model ID.

    Returns one of _AUTO_MODEL_VALID's values (explicit full IDs, not aliases)
    from the classifier result. source is 'auto' when the classifier ran,
    'manual' when auto is off, 'fallback' when the classifier errored.
    fallback_model is used verbatim when auto is off and as the safety net
    when the classifier fails.
    """
    if not state.CONFIG.get('auto_model_enabled', False):
        return fallback_model, 'manual'
    if not prompt or not prompt.strip():
        return fallback_model, 'fallback'
    classifier_model = state.CONFIG.get('auto_model_classifier_model', '') or 'haiku'
    try:
        raw = _scribe_call(classifier_model, _AUTO_MODEL_CLASSIFIER_PROMPT, prompt.strip())
    except Exception:
        return fallback_model, 'fallback'
    token = (raw or '').strip().upper()[:1]
    chosen = _AUTO_MODEL_VALID.get(token)
    if not chosen:
        return fallback_model, 'fallback'
    return chosen, 'auto'

# Mobile brief replies — the directive is silently prepended to messages from
# clients that POST `client="mobile"` when the global toggle is on. Only the
# augmented message reaches claude; the user's chat bubble (log_lines + the
# frontend's local echo) shows the original verbatim. This is the entire
# "Telegram-mode" mechanism: no dedicated UI, no new endpoints, no per-turn
# state — the agent simply gets a one-line nudge per user message.
_BRIEF_REPLY_DIRECTIVE = (
    "[the user is messaging you from a phone — reply in Telegram style: "
    "short, conversational, one idea per message; avoid headers, bullets, "
    "and long code blocks. They can switch to PC and ask follow-ups if they "
    "want more detail. This instruction is hidden from the user.]"
)

# Device-neutral variant for `brief_replies_always_enabled` (applies on desktop
# too, so it can't say "from a phone" / "switch to PC"). Per-turn prepend used
# when sticky_agent_settings is OFF; mirrors the hard framing of the
# system-baked variant. Brevity targets PROSE only — necessary code, file
# edits, and tool work are never truncated.
_BRIEF_REPLY_DIRECTIVE_ALWAYS = (
    "[BINDING for this reply — brevity is a hard rule, not a preference. Lead "
    "with the answer; hard ceiling ~4 sentences of prose (more only if the user "
    "asked for detail); no preamble, no restating the question, no closing "
    "offers. Bullets only to enumerate 3+ discrete items. Before sending, cut "
    "every non-load-bearing sentence. This caps PROSE ONLY — never shorten "
    "necessary code, file edits, tool work, or findings; completeness means "
    "substance, not length. This instruction is hidden from the user.]"
)

# System-prompt variant of the device-neutral brevity nudge, used when
# `sticky_agent_settings` is on: baked once into _build_agent_context (cached,
# system-level authority) instead of re-prepended to every user turn. Framed as
# a BINDING rule (imperative, with a pre-send self-check and an explicit
# carve-out so it can't be rationalized away against the "be complete" rules).
# Governs PROSE only.
_BRIEF_REPLY_DIRECTIVE_SYSTEM = (
    "REPLY LENGTH — BINDING RULE for this session (this is a hard constraint, "
    "not a stylistic preference): default to the SHORTEST reply that fully "
    "answers. Lead with the answer in the first sentence. Hard ceiling: ~4 "
    "sentences of prose per reply (more only if the user explicitly asks for "
    "detail). No preamble, no restating the question, no recap of what you just "
    "did, no closing offers. Use bullets ONLY to enumerate 3+ discrete items, "
    "never to pad. Before sending, re-read your draft and delete every sentence "
    "that is not load-bearing to the answer. "
    "This caps PROSE ONLY — never shorten or omit necessary code, file edits, "
    "tool calls, or actual findings. 'Complete and fully analyzed' refers to "
    "SUBSTANCE, not word count: a correct answer in two sentences outranks a "
    "thorough-sounding one in ten. When in doubt, cut."
)


# A leading slash command the CLI should parse: `/name`, `/multi-word`, or a
# plugin skill `/plugin:name`, followed by whitespace, args, or end-of-line.
# Deliberately does NOT match a bare unix path like `/usr/bin/x` (the second
# `/` breaks the token), so real slash commands are caught without eating paths.
_SLASH_COMMAND_RE = _re_auth.compile(r'^/[A-Za-z][\w-]*(:[\w-]+)?(\s|$)')


def _looks_like_slash_command(message: str) -> bool:
    """True when `message` begins with a CLI slash command (ignoring leading WS)."""
    return bool(_SLASH_COMMAND_RE.match(message.lstrip()))


def _apply_mobile_brief(message: str, request_data: dict) -> str:
    """Return `message` augmented with a hidden brief-reply directive.

    Two independent server toggles drive this:
      * `brief_replies_always_enabled` — when on, EVERY client (desktop too)
        gets the device-neutral directive. Supersedes the phone-only gate.
      * `mobile_brief_replies_enabled` — when on, only requests that declared
        `client="mobile"` get the phone-worded directive.
    If neither applies, `message` is returned unchanged.

    Callers MUST use the returned (augmented) string for whatever reaches
    claude (stdin write, spawn arg, _dispatch_agent_internal task) and keep
    the ORIGINAL `message` for anything user-visible (log_lines, telemetry).

    SLASH-COMMAND CARVE-OUT: the CLI only parses a message as a slash command
    (`/goal`, `/context`, …) when the command is the FIRST bytes of the turn.
    Prepending the brief directive would push it off the front, so the command
    silently degrades to plain text the model just reads. When the message is a
    slash command, skip the prepend entirely so it dispatches as intended — a
    command turn produces no prose reply to keep brief anyway.
    """
    if isinstance(message, str) and _looks_like_slash_command(message):
        return message
    if state.CONFIG.get('brief_replies_always_enabled'):
        # When sticky_agent_settings is on, this directive is baked into the
        # spawn-time system prompt (_build_agent_context) — don't also prepend
        # it per turn, or it doubles up.
        if state.CONFIG.get('sticky_agent_settings', False):
            return message
        return f"{_BRIEF_REPLY_DIRECTIVE_ALWAYS}\n\n{message}"
    if not state.CONFIG.get('mobile_brief_replies_enabled'):
        return message
    if not isinstance(request_data, dict):
        return message
    if request_data.get('client') != 'mobile':
        return message
    return f"{_BRIEF_REPLY_DIRECTIVE}\n\n{message}"

# ─────────────────────────────────────────────────────────────────────────────
# Multi-provider dispatch (non-claude providers go through this)
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_runtime_model(runtime, p, model_override=''):
    """Pick the model id to hand a non-claude runtime.

    An explicit per-chat pick (composer Model picker) wins and is passed
    through verbatim — the user may name a model newer than our catalog.

    Otherwise we may only inherit the project/global `agent_model` if THIS
    runtime actually accepts it. That guard is the point: `agent_model` is
    always a claude id (the Agent-settings picker offers nothing else), and
    the old code forwarded it unconditionally — so a project pinned to Opus
    spawned `codex -m claude-opus-5`, which the CLI rejects outright.
    """
    if model_override:
        return model_override
    inherited = p.get('agent_model', '') or state.CONFIG.get('agent_model', '')
    try:
        if inherited and runtime.model_supported(inherited):
            return inherited
    except Exception as e:
        _log(f"[runtime-dispatch] model_supported({inherited!r}) failed: {e}")
    return ''


def _dispatch_via_runtime(p, task, *, provider_name,
                          incognito=False, trigger_type='manual',
                          trigger_id='', reuse_session_id='',
                          display_task=None, character_meta=None,
                          character_body='', model_override=''):
    """Dispatch a session through the AgentRuntime abstraction (non-claude).

    The runtime owns: binary resolution, subprocess.Popen, reader thread,
    output parsing. MC keeps owning the agent_sessions dict — the runtime
    writes into it (proc, log_lines, status, ...) using the same shape the
    claude path uses, so the rest of MC (status badge, SSE generator, stop
    button, agent_log) keeps working without per-provider branching.
    """
    try:
        runtime = _agent_runtime.get_runtime(provider_name)
    except KeyError:
        raise ValueError(f"unknown provider: {provider_name!r}")

    pp = p.get('project_path', '')
    project_id = p.get('id', '')
    model = _resolve_runtime_model(runtime, p, model_override)

    mgr = get_manager(project_id)
    mgr.ensure_guardian()

    with mgr.lock:
        if reuse_session_id and reuse_session_id not in agent_sessions:
            session_id = reuse_session_id
        else:
            session_id = uuid.uuid4().hex[:12]

        # Seed log_lines with the user's prompt so the frontend chat shows it
        # even after /agent/status overwrites the buffer with server log_lines.
        # Same fix as the claude path — see `_dispatch_agent_internal`.
        user_label = state.CONFIG.get('user_name') or 'User'
        _seed_task = display_task if display_task is not None else task
        # Pre-create the session dict so SSE clients can attach instantly
        session = {
            'status': 'running',
            'task': task,
            'log_lines': [f"> {user_label}: {_seed_task}"],
            'started_at': now_iso(),
            'session_id': session_id,
            'project_id': project_id,
            'mode': 'A',
            'process_alive': True,
            'last_output_time': _time.time(),
            'last_status_change_time': _time.time(),
            'guardian_state': None,
            'recovery_attempts': 0,
            'last_recovery_time': 0,
            'pending_recovery_message': None,
            'circuit_breaker_tripped': False,
            '_dispatch_time': _time.time(),
            'incognito': bool(incognito),
            'trigger_type': trigger_type,
            'trigger_id': trigger_id,
            'provider': provider_name,
            'agent_model': model,
            'pinned_model': model_override or '',
            'character': character_meta,
        }
        agent_sessions[session_id] = session
        mgr.session_ids.add(session_id)

    # Build system_prompt blob (MEMORY/AGENT_RULES). Skip when incognito.
    system_prompt = ''
    try:
        if not incognito:
            system_prompt = _build_agent_context(p, incognito=False, task=task,
                                                 character_body=character_body,
                                                 character_name=(character_meta or {}).get('agent_name') or '',
                                                 session_id=session_id,
                                                 character_skills=(character_meta or {}).get('skills') or [])
    except Exception as e:
        _log(f"[runtime-dispatch] context build failed: {e}")

    try:
        handle = runtime.dispatch(
            project_path=pp,
            task=task,
            system_prompt=system_prompt,
            resume_id='',
            mode='A',
            model=model,
            incognito=incognito,
            mc_session_id=session_id,
            session_dict=session,
            project_id=project_id,
            register_process=_register_process,
            # MC-930: the runtime owns its reader thread, so completion has to
            # be handed to it as a hook or no agent-log row is ever written for
            # this session. See _runtime_log_completion.
            callbacks=_RUNTIME_CALLBACKS,
        )
    except Exception as e:
        session['status'] = 'error'
        session['log_lines'].append(f"[{provider_name} dispatch failed: {e}]")
        session['process_alive'] = False
        session['last_status_change_time'] = _time.time()
        raise

    if trigger_type and trigger_type != 'manual':
        try:
            _log_agent_dispatch_pending(session)
        except Exception:
            pass

    _log_agent_activity(project_id, f"Agent dispatched (provider={provider_name}): {task[:100]}")
    return session_id


def _character_near_matches(pp, name, limit=3):
    """Cheap close-name suggestions for an unresolvable character, across both
    scopes. Best-effort — an empty list just means the error carries no hint.
    """
    try:
        import difflib
        from mc import characters as _characters
        names = sorted({r['name'] for r in
                        _characters.list_characters(project_path=pp, include_body=False)})
        return difflib.get_close_matches(name, names, n=limit, cutoff=0.4)
    except Exception:
        return []


def _resolve_character(pp, character, project=None, strict=False):
    """Resolve a chat's character to (meta, body).

    `character` is a "scope:name" string from the new-chat picker
    (e.g. "project:code-reviewer", "global:docs-writer"). When it is empty the
    project's `default_character` is used instead — the agent-types Phase 1
    layer (docs/AGENT_TYPES_DESIGN.md §4), and the thing that stops a persona
    being something the user has to remember to switch on every single time.

    Best-effort by default: an unknown or stale value yields (None, '') so a
    deleted character never blocks dispatch. A stale PROJECT DEFAULT matters
    more than a stale pick — nobody typed it, so nobody is watching for it to
    break — hence the miss is logged rather than swallowed.

    strict=True (MC-925) flips that for an EXPLICITLY requested character only
    (source stays 'picked' below) — a misspelled or deleted persona someone
    actually typed raises ValueError naming what was not found, instead of
    silently dispatching personaless. Never raises for the 'project' (default)
    source, and never for an empty `character` — both stay best-effort, since
    those are not a fresh, explicit ask. Callers that recover a PRIOR run's
    persona (resume, revive) must pass strict=False (the default) for the same
    reason — a deleted character there must degrade, not block.

    meta = {'name','scope','display_name','source','engine'} for the session
    record and the header pill. `source` is 'picked' | 'project'; the pill has
    to be able to say WHY this persona is running, or an inherited one looks
    like the agent silently changed personality.
    """
    source = 'picked'
    if not character or not isinstance(character, str):
        character = ((project or {}).get('default_character') or '')
        source = 'project'
    if not character or not isinstance(character, str):
        return None, ''
    scope, _, name = character.partition(':')
    scope = (scope or '').strip().lower()
    name = (name or '').strip()
    if scope not in ('project', 'global') or not name:
        if strict and source == 'picked':
            raise ValueError(
                f"character {character!r} is not a valid 'project:<name>' or "
                f"'global:<name>' reference")
        return None, ''
    try:
        from mc import characters as _characters
        rec = _characters.read_character(
            scope, name, project_path=(pp if scope == 'project' else None),
            include_body=True)
    except Exception as e:
        _log(f"[dispatch] character resolve failed for {character!r}: {e}")
        if strict and source == 'picked':
            raise ValueError(f"character {character!r} could not be resolved: {e}")
        return None, ''
    if not rec:
        if source == 'project':
            _log(f"[dispatch] project default character {character!r} not found "
                 f"— dispatching with no persona", level='warn')
            return None, ''
        if strict:
            near = _character_near_matches(pp, name)
            hint = f" — did you mean: {', '.join(near)}?" if near else ''
            raise ValueError(f"character '{character}' not found{hint}")
        return None, ''
    meta = {'name': rec.get('name') or name, 'scope': scope,
            'display_name': rec.get('display_name') or rec.get('name') or name,
            'source': source}
    engine = rec.get('engine') or {}
    if engine:
        meta['engine'] = engine
    # The name the type chose for itself. Distinct from `name` (the file stem,
    # an identifier) and `display_name` (what the library lists it under) —
    # this is who is speaking, and it is what the chat header shows.
    agent_name = rec.get('agent_name')
    if agent_name:
        meta['agent_name'] = agent_name
    # The face rides along so the chat header can show WHO is speaking rather
    # than a generic mask. Same string the Floor draws — one persona, one face,
    # wherever it appears.
    avatar = rec.get('avatar')
    if avatar:
        meta['avatar'] = avatar
    skills = rec.get('skills')
    if skills:
        meta['skills'] = skills
    return meta, (rec.get('body') or '')


def _prior_character(project_id, resume_id):
    """The persona a conversation was ALREADY running, for a resume to keep.

    Returns "scope:name", or `''` when that conversation deliberately had none,
    or None when we have no record of it (leave the caller's normal precedence
    alone).

    WHY THIS EXISTS. `character` is only read off a FRESH dispatch — the picker
    is hidden on a resume — so a resumed chat rebuilt its system prompt from the
    PROJECT DEFAULT. Ron resumed a conversation he had started with Dave and it
    came back as Vector: same conversation, different agent, no event anywhere
    to explain it. The persona is already written to the agent log on every
    entry (so the header pill survives a restart); nothing was reading it back.

    The `''` case is the same bug mirrored: a chat that ran with no persona must
    not silently acquire one because the project default changed since. A resume
    continues what the conversation WAS, never what the project is now.
    """
    if not resume_id:
        return None
    try:
        seen = False
        for e in _load_agent_log(project_id):
            if e.get('claude_session_id') != resume_id:
                continue
            seen = True
            ch = e.get('character') or {}
            scope = (ch.get('scope') or '').strip().lower()
            name = (ch.get('name') or '').strip()
            if scope in ('project', 'global') and name:
                return f'{scope}:{name}'
        return '' if seen else None
    except Exception as e:
        _log(f"[dispatch] could not recover the persona for "
             f"{resume_id[:12]}: {e}")
        return None


def _character_engine(character_meta, key):
    """One engine key off a resolved character, or '' when unpinned."""
    v = ((character_meta or {}).get('engine') or {}).get(key)
    return v.strip() if isinstance(v, str) else ''


def _model_provider_mismatch(provider_name, model):
    """Name of the OTHER provider `model` belongs to, or '' if there's no
    detectable conflict with `provider_name`.

    Deliberately conservative: only flags a model id that is a KNOWN entry in
    some other runtime's own catalog (MODEL_CHOICES) and is NOT accepted by
    the resolved provider's runtime. An id absent from every catalog is not
    flagged — it may simply be newer than ours (the composer's "Custom..."
    box exists for exactly that case), and guessing wrong there would block a
    legitimate dispatch instead of a genuinely incoherent one.
    """
    try:
        runtime = _agent_runtime.get_runtime(provider_name)
    except KeyError:
        return ''  # unknown provider — let dispatch's own error handling catch it
    if runtime.model_supported(model):
        return ''
    for other in _agent_runtime.available_runtimes():
        if other.name == provider_name:
            continue
        if any(m == model for m, _ in other.model_choices()):
            return other.name
    # Catalog lookup alone is not enough, and this bit is load-bearing: on
    # 2026-08-31 every gemini-2.5-* id was retired from the Gemini catalog
    # (the API 404s them), and the MC-924 repro — a gemini id handed to the
    # claude runtime — stopped being flagged the moment those ids left the
    # list. A guard that forgets an id as soon as it is deprecated is exactly
    # backwards: a RETIRED id is MORE likely to show up in a stale pin, not
    # less. So fall back to the id's family prefix, which does not churn.
    #
    # Still conservative in the way the docstring promises: an id matching no
    # known family is not flagged, because it may simply be newer than us.
    _FAMILY_PREFIXES = (('gemini-', 'gemini'), ('claude-', 'claude'))
    for prefix, owner in _FAMILY_PREFIXES:
        if not model.startswith(prefix) or owner == provider_name:
            continue
        # Only flag if the RESOLVED provider does not claim the id itself —
        # a runtime that routes another vendor's models (openrouter-style
        # 'google/gemini-...') legitimately accepts them, and model_supported
        # above already gave it the chance to say so.
        return owner
    return ''


def _dispatch_agent_internal(project_id, task, resume_id='', incognito=False,
                             trigger_type='manual', trigger_id='',
                             reuse_session_id='', provider_override='',
                             display_task=None, character='', source='',
                             model_override='', strict_character=False):
    """Core dispatch logic shared by HTTP endpoint and scheduler.

    Returns session_id on success, raises ValueError on error.

    When incognito=True (or the project itself is the global incognito project),
    MEMORY/AGENT_RULES are skipped from --append-system-prompt and the session
    is flagged so _log_agent_completion will not write to the agent log or
    append to MEMORY.md.

    trigger_type/trigger_id annotate the resulting agent_log entry so callers
    (scheduler, hivemind dispatch) can later list "all runs for this trigger".
    Defaults are 'manual'/'' for direct user dispatch.

    reuse_session_id: when set, the new process adopts this existing MC
    session_id instead of minting a fresh uuid. Used by the scheduler when a
    continued run cold-respawns `-r <csid>` against the SAME Claude
    conversation — reusing the prior run's session_id keeps it on one agent_log
    row / one UI tab / one resolvable transcript instead of orphaning a new
    csid-less row per fire.

    strict_character (MC-925, default False): reject dispatch with a ValueError
    when `character` is a non-empty value that fails to resolve, instead of
    silently continuing personaless. Opt-in and OFF by default on purpose — a
    schedule or hivemind row pins a persona once, at WRITE time (already
    validated there; see test_scheduler_character.py), and every FIRE after
    that is a replay of an already-accepted decision, not a fresh ask; a
    persona deleted after the fact must still degrade quietly, the same as it
    always has. Only the interactive `/agent/dispatch` endpoint sets this,
    for the one case that IS a fresh, explicit ask: a caller (human or agent)
    naming a character THIS turn. Do not set it from scheduler/hivemind/revival
    call sites.
    """
    p = load_project(project_id)
    if not p:
        if project_id == INCOGNITO_PROJECT_ID:
            p = _ensure_incognito_project()
        else:
            raise ValueError('project not found')

    # Global incognito project always forces incognito on, regardless of caller.
    if p.get('_is_incognito_project') or project_id == INCOGNITO_PROJECT_ID:
        incognito = True

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        raise ValueError('project_path not set or invalid')

    # Resolve the per-chat character (persona) now, at spawn — the only point
    # a system prompt can be set. Immutable for this chat's lifetime; switching
    # personas = a new chat.
    #
    # On a RESUME the picker is hidden, so `character` arrives empty and the
    # project default would take over mid-conversation. Recover what this
    # conversation was actually running instead — see `_prior_character`.
    #
    # strict_character (MC-925) only ever applies to what the CALLER passed
    # in directly, never to a `_prior_character` recovery — a resume must stay
    # best-effort even when the caller opted in, since a deleted persona there
    # is the SAME "coming back is more important than coming back right" case
    # `_resolve_character`'s docstring already covers, not a fresh ask.
    _strict_this_call = strict_character and bool(character)
    _prior = _prior_character(project_id, resume_id) if resume_id else None
    if _prior is not None and not character:
        character = _prior
    if _prior == '' and not character:
        # It ran with no persona. Keep it that way — don't hand it the project
        # default just because a resume passed through here.
        character_meta, character_body = None, ''
    else:
        character_meta, character_body = _resolve_character(
            pp, character, project=p, strict=_strict_this_call)

    # ── Multi-provider routing ──────────────────────────────────────────────
    # If the conversation selects a non-claude provider, dispatch through the
    # AgentRuntime abstraction instead of the legacy claude path. Provider is
    # bound per-conversation: `provider_override` (chosen in the new-chat
    # composer) wins, then the project's default seed, then the global default.
    # Default behavior (all unset OR claude) is unchanged.
    # Precedence: explicit per-chat pick > the character's own provider >
    # project default > global. The character sits above the project because
    # "Grok for coding" is a property of the TYPE, not of the repo it runs in.
    provider_name = (provider_override
                     or _character_engine(character_meta, 'provider')
                     or p.get('provider')
                     or state.CONFIG.get('default_provider') or 'claude').lower()
    # A character's pinned model must be resolved BEFORE the provider branch
    # below, not after it — the non-claude return happens right here, and a
    # merge placed after it (as this used to be) never reaches a non-claude
    # dispatch at all, silently dropping the character's model pin. Same
    # precedence as provider: explicit per-chat pick > character's own pin.
    _char_model = _character_engine(character_meta, 'model')
    if not model_override and _char_model:
        model_override = _char_model
    # Refuse an incoherent provider/model pair instead of handing a foreign
    # model id to a CLI that will reject it (e.g. a claude-code process
    # launched with a gemini-* model id). Only fires when we can positively
    # identify the model as belonging to a DIFFERENT provider's own catalog —
    # an id we don't recognize at all (newer than our catalog, or a
    # provider-specific alias) is passed through, since the composer's
    # "Custom..." box exists precisely for that case.
    if model_override:
        _mismatch = _model_provider_mismatch(provider_name, model_override)
        if _mismatch:
            raise ValueError(
                f"model '{model_override}' belongs to provider '{_mismatch}', "
                f"not '{provider_name}' — pick a matching model or provider")
    if provider_name != 'claude':
        try:
            return _dispatch_via_runtime(p, task, provider_name=provider_name,
                                         incognito=incognito,
                                         trigger_type=trigger_type,
                                         trigger_id=trigger_id,
                                         reuse_session_id=reuse_session_id,
                                         display_task=display_task,
                                         character_meta=character_meta,
                                         character_body=character_body,
                                         model_override=model_override)
        except Exception as e:
            _log(f"[dispatch] runtime '{provider_name}' failed, no fallback: {e}")
            raise

    use_streaming = p.get('use_streaming_agent', state.CONFIG.get('use_streaming_agent', False))

    # Check session transcript size — auto-start fresh if too large
    original_resume = resume_id
    if resume_id:
        too_large, size_bytes = _session_too_large(pp, resume_id)
        if too_large:
            size_mb = size_bytes / (1024 * 1024)
            _log(f"[dispatch] Session {resume_id} transcript is {size_mb:.1f} MB — starting fresh")
            _log_agent_activity(project_id,
                                f"Auto-fresh: previous session too large ({size_mb:.0f} MB)")
            # Prepend context about the previous session
            task = (f"[Continuing from a previous conversation (session {resume_id}) that grew too large "
                    f"to resume ({size_mb:.0f} MB). Start fresh but continue the user's request below.]\n\n{task}")
            resume_id = ''

    # Resume → pre-load the prior conversation into log_lines so the chat
    # displays the full history when the user taps Continue, not just the new
    # prompt. Same renderer the read-only /reconstruct endpoint uses, so the
    # live and read-only views are byte-identical. Cap at 300 messages —
    # enough for any practical session, bounded so a runaway transcript can't
    # spike memory. Skipped when resume_id was reset above (transcript-too-large
    # path) since we're starting fresh and the prior history isn't relevant.
    #
    # IMPORTANT: also append the new task as a user line so the continuation
    # prompt shows up in the chat. Without this, the frontend pulls the
    # server's log_lines (transcript only) which overwrites its local
    # `[prefix]` seed — and the user's new prompt disappears from the view.
    _seed_log_lines = []
    user_label = state.CONFIG.get('user_name') or 'User'
    # `display_task` is the user-visible original (caller-supplied so the mobile
    # brief-reply directive doesn't leak into the chat bubble). When not given,
    # fall back to `task` — for non-mobile dispatches they're identical.
    _seed_task = display_task if display_task is not None else task
    if resume_id:
        try:
            _seed_log_lines = _transcript_buffer_lines(
                pp, resume_id, user_label, max_messages=300)
            _seed_log_lines.append(f"\n> {user_label}: {_seed_task}\n")
        except Exception as e:
            _log(f"[dispatch] transcript preload failed for {resume_id[:12]}: {e}")
    else:
        # Fresh dispatch: persist the user's prompt so the frontend chat shows
        # it even after /agent/status overwrites the buffer with server log_lines.
        # Without this, the locally-seeded `> {task}` prefix gets wiped on the
        # first poll and the user only sees the agent's reply (no question).
        _seed_log_lines.append(f"> {user_label}: {_seed_task}")

    # ── Auto-router + context build, OUTSIDE mgr.lock ───────────────────────
    # RC-2 constraint (ws_003): the classifier subprocess + context build are
    # both slow (seconds) and must NOT pin mgr.lock — that's how mobile sends /
    # interrupts wedge the project for hours when claude rate-limits.
    #
    # Resume paths build + re-append the context too: CLI ≥2.1.206 rebuilds
    # the system prompt from flags on EVERY invocation, so a bare `-r` DROPS
    # the whole injected context (rules, read-floor, API reference). See
    # memory discovery-claude-resume-ignores-append-system-prompt (reversed
    # 2026-07-11) — the old "resume restores the original prompt" rule is false.
    _sp_args = []
    # The id this session WILL get. Minted before the system prompt is built
    # because the prompt has to name it — an agent cannot name its own figure
    # without knowing which figure it is. `_maybe_isolate_worktree` consumes the
    # same value further down, still outside the lock. If the lock path ends up
    # choosing reuse_session_id instead, the unused tree is reaped by gc_stale
    # (it holds no work).
    _fresh_sid = uuid.uuid4().hex[:12]
    _planned_sid = (reuse_session_id
                    if (reuse_session_id and reuse_session_id not in agent_sessions)
                    else _fresh_sid)
    _sp_path = None
    _router_fallback_reason = ''
    # A character's pinned model is an explicit choice by whoever authored the
    # type ("Fable writes PRDs"), so it outranks the complexity classifier for
    # the same reason the composer's Model picker does — but NOT the picker
    # itself, which is the user speaking about this one turn. (`_char_model`
    # itself, and its merge into `model_override`, happen earlier — before the
    # non-claude provider branch above, which returns before reaching here.)
    _char_effort = _character_engine(character_meta, 'effort') or None
    _char_agent_name = (character_meta or {}).get('agent_name') or ''
    _char_skills = (character_meta or {}).get('skills') or []
    if resume_id:
        routed_model, routed_source, base_flags, context, _router_fallback_reason = (
            _dispatch_with_routing_parallel(
                p, task,
                context_builder=lambda: _build_agent_context(
                    p, incognito=incognito, task=task,
                    character_body=character_body, character_name=_char_agent_name,
                    session_id=_planned_sid, character_skills=_char_skills,
                    source=source),
                streaming=use_streaming, effort_override=_char_effort))
        _sp_args, _sp_path = _sysprompt_file_args(context)
    elif model_override:
        # Composer "Model" picker, or the character's pinned model: an explicit
        # choice either way, so the auto-router is bypassed entirely. The
        # source is reported distinctly so the per-bubble pill can say whether
        # the user or the agent type chose the engine.
        routed_model = model_override
        routed_source = 'character' if model_override == _char_model else 'manual'
        base_flags = _build_claude_flags(p, streaming=use_streaming,
                                         model_override=model_override,
                                         effort_override=_char_effort)
        context = _build_agent_context(p, incognito=incognito, task=task,
                                       character_body=character_body,
                                       character_name=_char_agent_name,
                                       session_id=_planned_sid,
                                       character_skills=_char_skills,
                                       source=source)
        _sp_args, _sp_path = _sysprompt_file_args(context)
    else:
        routed_model, routed_source, base_flags, context, _router_fallback_reason = (
            _dispatch_with_routing_parallel(
                p, task,
                context_builder=lambda: _build_agent_context(
                    p, incognito=incognito, task=task,
                    character_body=character_body, character_name=_char_agent_name,
                    session_id=_planned_sid, character_skills=_char_skills,
                    source=source),
                streaming=use_streaming, effort_override=_char_effort))
        _sp_args, _sp_path = _sysprompt_file_args(context)
    # Per-dispatch telemetry — best-effort; never raises. requested = the
    # model the user configured; chosen = what actually went to --model
    # (post-router). See docs/DISPATCH_AND_ROUTING_ANALYSIS.md §B.5.
    _router_stat(
        project_id,
        requested_model=(model_override or p.get('agent_model', '')
                         or state.CONFIG.get('agent_model', '') or 'sonnet'),
        chosen_model=routed_model,
        source=routed_source,
        reason=_router_fallback_reason,
    )

    mgr = get_manager(project_id)
    mgr.ensure_guardian()

    # ── Per-agent worktree isolation (b264200a) ─────────────────────────────
    # Decided and CREATED OUTSIDE mgr.lock: `git worktree add` takes seconds
    # and must never pin the project lock (the RC-2 constraint above). The id
    # itself is minted further up (it also has to reach the system prompt, so
    # the agent can name its own figure); only the tree is created here.
    _agent_cwd, _isolated = _maybe_isolate_worktree(p, _planned_sid, incognito)

    with mgr.lock:
        # Reuse the prior run's id (continued scheduled thread) unless that id is
        # somehow still a live session — never clobber a running session dict.
        if reuse_session_id and reuse_session_id not in agent_sessions:
            session_id = reuse_session_id
        else:
            session_id = _fresh_sid

        if use_streaming:
            # Mode B: persistent process with stream-json stdin
            if resume_id:
                cmd = [_resolve_claude(), '-r', resume_id, *base_flags, *_sp_args]
            else:
                cmd = [_resolve_claude(), *base_flags, *_sp_args]

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=_agent_cwd,  # worktree when isolated, else pp
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )
            _sysprompt_cleanup(_sp_path, proc)

            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Agent (Mode B)', 'agent',
                              session_id, project_id, task[:80])

            # Build initial message but DO NOT write it under mgr.lock — see
            # below. claude can stall before reading stdin (rate-limit at
            # startup, large transcript replay, auth probe, etc.) and a
            # blocking write here would hold the project lock indefinitely,
            # wedging every subsequent /agent/* call for the project.
            # Diagnosed 2026-05-28 — scheduler dispatch stuck on a rate-
            # limited claude pinned the manager lock for 5h, blackholing all
            # mobile sends/interrupts/followups to day_trading.
            _initial_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": task}
            }) + '\n'

            session = {
                'proc': proc,
                'status': 'running',
                'task': task,
                'log_lines': list(_seed_log_lines),
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': project_id,
                # Worktree isolation (b264200a): the cwd this agent actually
                # runs in, and whether it is a private worktree. Read by the
                # merge-back-on-end path and by respawns so a resumed turn
                # stays in the SAME tree it started in.
                '_agent_cwd': _agent_cwd,
                '_worktree_isolated': _isolated,
                'mode': 'B',
                'stdin_lock': threading.Lock(),
                'process_alive': True,
                'last_output_time': _time.time(),
                'last_status_change_time': _time.time(),
                'guardian_state': None,
                'recovery_attempts': 0,
                'last_recovery_time': 0,
                'pending_recovery_message': None,
                'circuit_breaker_tripped': False,
                '_resume_id': resume_id or None,
                '_dispatch_time': _time.time(),
                'incognito': bool(incognito),
                'trigger_type': trigger_type,
                'trigger_id': trigger_id,
                # Who dispatched this: '' (legacy/unknown) · 'ui' (app) · 'agent'
                # (programmatic / agent self-dispatch). Lets the mobile
                # conversations list route agent-initiated chats to the side flow.
                'source': source or '',
                'agent_model': p.get('agent_model', '') or state.CONFIG.get('agent_model', ''),
                # Auto-router attribution — `model` is what actually got
                # passed via --model (after override); `model_source` is
                # 'manual' / 'auto' / 'fallback'. Frontend pill reads these.
                'model': routed_model,
                'model_source': routed_source,
                # Per-chat model PIN. An explicit choice (+New picker or the
                # in-chat switcher) pins this conversation to one model and
                # BYPASSES the auto-router for its whole life — until the user
                # changes or clears it. Empty = follow project/global/auto.
                'pinned_model': model_override or '',
                # Per-chat persona (Prompt Builder Phase 2): {name,scope,
                # display_name} or None. Immutable; drives the header pill.
                'character': character_meta,
                # Spawn context stash — re-appended verbatim on every `-r`
                # respawn (see _respawn_sysprompt_args). Byte-identical
                # content keeps the resumed prefix cache-friendly.
                '_system_prompt': context or '',
            }
            agent_sessions[session_id] = session
            mgr.session_ids.add(session_id)

            t = threading.Thread(target=_read_agent_stream_b, args=(proc, session), daemon=True)
            t.start()

            # Initial stdin write — deferred to a daemon thread so a stalled
            # claude (rate-limited startup, etc.) can't pin mgr.lock and wedge
            # the whole project. Followups also serialize on stdin_lock, so
            # the initial message always lands first.
            def _write_initial(_proc=proc, _msg=_initial_msg, _sess=session):
                lk = _sess.get('stdin_lock')
                if lk:
                    lk.acquire()
                try:
                    _proc.stdin.write(_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                    _proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                except Exception as _e:
                    _sess['log_lines'].append(f'[stdin write error on dispatch: {_e}]')
                    _sess['status'] = 'error'
                    _sess['last_status_change_time'] = _time.time()
                    _sess['process_alive'] = False
                finally:
                    if lk:
                        lk.release()
            threading.Thread(target=_write_initial, daemon=True).start()
        else:
            # Mode A: spawn-per-turn (existing behavior). base_flags / _sp_args
            # were resolved above the lock (outside RC-2's danger zone).
            if resume_id:
                cmd = [_resolve_claude(), '-r', resume_id, '-p', task, *base_flags,
                       *_sp_args]
            else:
                cmd = [_resolve_claude(), '-p', task, *base_flags, *_sp_args]

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=_agent_cwd,  # worktree when isolated, else pp
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )
            _sysprompt_cleanup(_sp_path, proc)

            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Agent (Mode A)', 'agent',
                              session_id, project_id, task[:80])

            session = {
                'proc': proc,
                'status': 'running',
                'task': task,
                'log_lines': list(_seed_log_lines),
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': project_id,
                # Worktree isolation (b264200a) — see the Mode B note above.
                '_agent_cwd': _agent_cwd,
                '_worktree_isolated': _isolated,
                'mode': 'A',
                'last_output_time': _time.time(),
                'last_status_change_time': _time.time(),
                'guardian_state': None,
                'recovery_attempts': 0,
                'last_recovery_time': 0,
                'pending_recovery_message': None,
                'circuit_breaker_tripped': False,
                '_resume_id': resume_id or None,
                '_dispatch_time': _time.time(),
                'incognito': bool(incognito),
                'trigger_type': trigger_type,
                'trigger_id': trigger_id,
                # Who dispatched this: '' (legacy/unknown) · 'ui' (app) · 'agent'
                # (programmatic / agent self-dispatch). Lets the mobile
                # conversations list route agent-initiated chats to the side flow.
                'source': source or '',
                'agent_model': p.get('agent_model', '') or state.CONFIG.get('agent_model', ''),
                'model': routed_model,
                'model_source': routed_source,
                # Per-chat model PIN. An explicit choice (+New picker or the
                # in-chat switcher) pins this conversation to one model and
                # BYPASSES the auto-router for its whole life — until the user
                # changes or clears it. Empty = follow project/global/auto.
                'pinned_model': model_override or '',
                # Per-chat persona (Prompt Builder Phase 2): {name,scope,
                # display_name} or None. Immutable; drives the header pill.
                'character': character_meta,
                # Spawn context stash — re-appended verbatim on every `-r`
                # respawn (see _respawn_sysprompt_args). Byte-identical
                # content keeps the resumed prefix cache-friendly.
                '_system_prompt': context or '',
            }
            agent_sessions[session_id] = session
            mgr.session_ids.add(session_id)

            t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
            t.start()

        # Drop a pending row in the agent log immediately for non-manual triggers
        # so the schedule/hivemind "Runs" panel can correlate even if the session
        # never gets to call _log_agent_completion (long-lived idle Mode B session
        # killed by a server restart, etc.). Manual dispatches don't need this.
        if trigger_type and trigger_type != 'manual':
            _log_agent_dispatch_pending(session)

        # Context budget check — triggers auto-condensation if context too large
        if not resume_id:
            notice = _check_context_budget(p, context)
            if notice:
                session['log_lines'].append(notice)

        # Notify user if session was auto-started fresh due to transcript size
        if original_resume and not resume_id:
            session['log_lines'].append(
                f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')

    resume_label = f" (resuming {resume_id})" if resume_id else ""
    try:
        _log(f"[dispatch] cmd: {' '.join(cmd)}")
    except (UnicodeEncodeError, UnicodeDecodeError):
        _log(f"[dispatch] cmd: {' '.join(cmd).encode('ascii', 'replace').decode()}")
    _log_agent_activity(project_id, f"Agent dispatched{resume_label}: {task[:100]}")
    return session_id

@bp.route('/api/project/<project_id>/agent/dispatch', methods=['POST'])
def agent_dispatch(project_id):
    data = request.get_json() or {}
    task = data.get('task', '').strip()
    if not task:
        return jsonify({'error': 'task required'}), 400
    resume_id = data.get('resume_conversation_id', '').strip()
    incognito = bool(data.get('incognito'))
    provider_override = (data.get('provider') or '').strip().lower()
    # Per-chat model (composer "Model" picker). Fresh chats only — a resume
    # keeps the conversation's existing model; the picker is hidden there.
    model_override = (data.get('model') or '').strip() if not resume_id else ''
    # Per-chat character/persona ("scope:name", e.g. "project:code-reviewer").
    # Only meaningful on a FRESH chat — a resume keeps the original spawn's
    # persona (claude -r can't change the system prompt), so ignore it there.
    character = (data.get('character') or '').strip() if not resume_id else ''
    # Mobile brief replies: augmented version goes to the agent. The frontend's
    # local echo already shows the original task as the user's chat bubble.
    claude_task = _apply_mobile_brief(task, data)
    # Who dispatched: the app UI sends source='ui'; a programmatic/agent caller
    # sends source='agent' (routed to the side flow). Absent = '' (legacy → user).
    # Heuristic: a raw agent/curl dispatch has no browser Origin and no mobile
    # client tag → treat as 'agent' so it auto-routes to the side flow without
    # the caller having to remember. Real UI requests always carry an Origin.
    source = (data.get('source') or '').strip().lower()
    if not source and not request.headers.get('Origin') and not data.get('client'):
        source = 'agent'
    try:
        session_id = _dispatch_agent_internal(project_id, claude_task, resume_id,
                                              incognito=incognito,
                                              provider_override=provider_override,
                                              display_task=task, character=character,
                                              source=source,
                                              model_override=model_override,
                                              # A fresh, explicit ask this turn
                                              # (character is '' on a resume,
                                              # above) — the one call site
                                              # where an unresolvable pick
                                              # should refuse rather than
                                              # silently run personaless
                                              # (MC-925). Scheduler/hivemind
                                              # replay a persona pinned once
                                              # at write time and must stay
                                              # best-effort; they don't set this.
                                              strict_character=True)
    except ValueError as e:
        code = 404 if 'not found' in str(e) else 400
        return jsonify({'error': str(e)}), code
    except FileNotFoundError:
        return jsonify({'error': _cli_missing_message(provider_override)}), 500
    except Exception as e:
        return jsonify({'error': f'dispatch failed: {e}'}), 500
    return jsonify({'ok': True, 'session_id': session_id})


@bp.route('/api/project/<project_id>/agent/<session_id>/model', methods=['POST'])
def agent_set_model(project_id, session_id):
    """Pin (or clear) the model for ONE live conversation — the in-chat switcher.

    Body: {"model": "<id>"} to pin, or {"model": ""} to clear (follow the
    project/global default, or the auto-router when it's on). A pin is user
    intent and beats the classifier: while set, the conversation stays on that
    model every turn until changed. Claude-only (other runtimes ignore --model).

    The switch is LAZY: we set the pin and let the next follow-up respawn the
    process with `-r` + the new model (via the same _respawn_sysprompt_args path
    the auto-router uses, so memory/rules survive). The header pill reflects the
    pin immediately; `pending` says whether the running model still differs.

    Live-session scoped: the pin lives on the in-memory session (persists across
    -r respawns). It is not restored after an MC restart — a revived chat falls
    back to default/auto until re-pinned.
    """
    data = request.get_json(silent=True) or {}
    model = (data.get('model') or '').strip()
    # Guard against flag injection (this goes to --model): allow empty, or a
    # conservative id shape. Mirrors the models the +New picker offers.
    if model and not _re_auth.fullmatch(r'[A-Za-z0-9._-]{1,60}', model):
        return jsonify({'error': 'invalid model id'}), 400

    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session:
            return jsonify({'error': 'session not found'}), 404
        if (session.get('provider') or 'claude').lower() != 'claude':
            return jsonify({'error': 'model switch is claude-only'}), 400
        session['pinned_model'] = model
        current = session.get('model') or ''
        # Empty pin + auto off ⇒ next turn uses the project/global default; we
        # can't know that target here without the prompt, so just report current.
        pending = bool(model) and (_router_model_tier(model) != _router_model_tier(current))
        running_status = session.get('status')

    _log_agent_activity(project_id,
                        f"Model {'pinned to ' + model if model else 'unpinned (follow default)'}")
    return jsonify({
        'ok': True, 'session_id': session_id,
        'pinned_model': model,
        'model': current,
        'pending': pending,          # running model still differs → switches next turn
        'status': running_status,
    })


@bp.route('/api/project/<project_id>/agent/send', methods=['POST'])
def agent_send(project_id):
    """Single user-intent endpoint. The server reads live session state
    under the per-project lock and routes to the correct internal handler:

      - no session_id, or session missing  → revive from agent_log if possible,
                                              else dispatch fresh
      - session exists, status == 'running' → interrupt-and-resume (atomic)
      - session exists, any other status    → followup (queues for Mode A,
                                              writes stdin for Mode B,
                                              respawns purged sessions)

    Frontend never picks the route. It just sends intent. Phase 2 of the
    2026-04-27 race-condition consolidation — see CHANGELOG `[2026-04-27i]`.
    """
    p = load_project(project_id)
    if not p and project_id == INCOGNITO_PROJECT_ID:
        p = _ensure_incognito_project()
    if not p:
        return jsonify({'error': 'project not found'}), 404
    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set'}), 400

    data = request.get_json() or {}
    message = (data.get('message') or '').strip()
    session_id = (data.get('session_id') or '').strip()
    incognito = (
        bool(data.get('incognito'))
        or bool(p.get('_is_incognito_project'))
        or project_id == INCOGNITO_PROJECT_ID
    )
    if not message:
        return jsonify({'error': 'message required'}), 400

    # Decision under the lock — this is the ONLY place that picks the route.
    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id) if session_id else None
        if session and session.get('project_id') != project_id:
            session = None  # session belongs to a different project — ignore
        if not session:
            decision = 'fresh_or_revive'
        elif session.get('status') == 'running':
            decision = 'interrupt'
        else:
            decision = 'followup'

        # Pre-persist the user's prompt to log_lines INSIDE this same lock so
        # it survives even if the downstream handler races with a status
        # change and bails out (lost-prompt race: between this decision and
        # agent_interrupt/agent_followup acquiring their own lock, the agent
        # can transition to 'completed', which agent_interrupt rejects with
        # 400 — without this safety net, the prompt is silently dropped).
        # The handlers honor `_send_already_logged` and skip their own
        # append. fresh_or_revive paths log via their own mechanisms and are
        # not affected.
        if session and decision in ('interrupt', 'followup'):
            user_label = state.CONFIG.get('user_name') or 'User'
            session['log_lines'].append(f"\n> {user_label}: {message}\n")
            session['_send_already_logged'] = True
            # Mark followup as in-flight under the same lock so the SSE generate()
            # loop sees the signal immediately. Without this, an eagerly-opened
            # SSE can close on stale terminal status before agent_followup gets
            # a chance to flip status to 'running'. Cleared by the followup
            # handler (and by _get_active_restart_blockers stuck-flag recovery)
            # once the new turn is actually live.
            if decision == 'followup':
                session['_dispatching_followup'] = True

    # Route to the appropriate handler. Each does its own lock acquisition
    # for the actual mutation; the decision above is just to pick the path.
    # The existing handlers read `request.get_json()` themselves; they get
    # the same body we got. We tag the response so the frontend can log the
    # route taken (useful for debugging; FE doesn't act on it).
    if decision == 'interrupt':
        resp = agent_interrupt(project_id)
        # Race recovery: if the agent transitioned to a state interrupt
        # rejects (e.g., 'completed' between decision and handler entry),
        # fall back to followup so the user's message actually gets
        # processed. The prompt is already pre-persisted, so this is purely
        # about routing the live agent, not about data preservation.
        if resp.status_code == 400:  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
            try:
                body = resp.get_json(silent=True) or {}  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
                if isinstance(body, dict) and body.get('error') == 'agent not active':
                    # Re-mark as already-logged so followup doesn't double-log
                    # (the pop in agent_interrupt only ran if it reached the
                    # log_lines line; on early-bail at the status check, the
                    # flag is still set).
                    resp = agent_followup(project_id)
                    decision = 'interrupt_to_followup'
            except Exception:
                pass
    elif decision == 'followup':
        resp = agent_followup(project_id)
    else:  # fresh_or_revive
        if session_id:
            try:
                revived = _revive_from_agent_log(project_id, session_id, message, p)
            except Exception as e:
                revived = None
                _log_agent_activity(project_id, f"Revive error in /send: {e}")
            if revived:
                return jsonify({'ok': True, 'session_id': session_id,
                                'revived': True, 'route': 'revive'})
        # Last resort before starting over: the client may address a chat by its
        # CLAUDE session id (the transcript-reconstruct thread keys its tab on the
        # csid, since no MC session id exists for a transcript-only conversation).
        # That id is in neither agent_sessions nor agent_log, so both routes above
        # miss — and dispatching fresh here silently drops the whole conversation
        # and answers in an empty chat. If the id resolves to a real transcript,
        # resume it instead.
        resume_from = ''
        if session_id and not session_id.startswith('_pending_'):
            try:
                if _find_transcript_file(pp, session_id):
                    resume_from = session_id
            except Exception as e:
                _log_agent_activity(project_id, f"csid transcript probe failed in /send: {e}")
        # Otherwise dispatch a fresh session. Augmented message goes to claude;
        # the original is preserved for the chat bubble (the frontend echo +
        # any log_lines pre-persist above — currently none on this path since
        # there is no prior session yet).
        try:
            claude_message = _apply_mobile_brief(message, data)
            new_session_id = _dispatch_agent_internal(
                project_id, claude_message, resume_id=resume_from, incognito=incognito,
                provider_override=(data.get('provider') or '').strip().lower())
        except ValueError as e:
            code = 404 if 'not found' in str(e) else 400
            return jsonify({'error': str(e)}), code
        except FileNotFoundError:
            return jsonify({'error': _cli_missing_message(
                (data.get('provider') or '').strip().lower())}), 500
        except Exception as e:
            return jsonify({'error': f'dispatch failed: {e}'}), 500
        return jsonify({'ok': True, 'session_id': new_session_id,
                        'route': 'resume' if resume_from else 'dispatch'})

    # Tag the upstream response with the route we took. Flask Response objects
    # support get_json(); we rebuild and return.
    try:
        body = resp.get_json(silent=True) or {}  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
        if isinstance(body, dict):
            body.setdefault('route', decision)
            return jsonify(body), resp.status_code  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
    except Exception:
        pass
    return resp


@bp.route('/api/project/<project_id>/agent/stream')
def agent_stream(project_id):
    """SSE endpoint streaming agent output for a specific session."""
    session_id = request.args.get('session', '')
    since = request.args.get('since', '0')

    def generate():
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'no active session'})}\n\n"
            return

        is_mode_b = session.get('mode') == 'B'
        sent = int(since) if since.isdigit() else 0
        tick = 0
        idle_sent = False  # track whether we've sent turn_complete for current idle
        last_guardian_state = None
        # Phase 2 (2026-04-27): the FE no longer flips status optimistically,
        # so we need to tell it when a new turn starts (status: idle -> running)
        # so the UI reflects reality without closing the stream. Sent as
        # `turn_start` so the existing `status` handler (which closes on
        # terminal states) is unaffected.
        last_emitted_status = None
        last_emitted_activity = None  # '' | 'thinking' | 'writing' | 'tool'
        emitted_qids = set()  # per-stream: don't re-emit same question_id
        while True:
            session['_last_sse_poll_time'] = _time.time()
            lines = session['log_lines']
            # Cursor-overshoot guard: `sent` can exceed len(lines) whenever
            # log_lines was REBUILT shorter under the same session_id —
            # revive-from-agent-log reseeds from the transcript (40 msgs, no
            # tool lines) after a restart/purge, and the 2000→1500 cap slams
            # the array. Without this, `sent < len` never fires again: the
            # stream serves heartbeats forever while the chat looks frozen
            # even in focus (a healthy-looking stream disarms every client
            # recovery path, and the reconcile poll no-ops on the same
            # comparison). Tell the client to drop its buffer, then replay.
            if sent > len(lines):
                yield f"data: {json.dumps({'type': 'reset'})}\n\n"
                sent = 0
            if sent < len(lines):
                for line in lines[sent:]:
                    yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
                sent = len(lines)

            # Send pending AskUserQuestion data. We keep `pending_questions`
            # populated until the user answers (cleared in /agent/followup) so
            # a client that wasn't connected at emit time (mobile cold reopen,
            # SSE dropped mid-emit, modal not yet built) can still see the
            # question on reconnect. Within one stream we dedupe by question_id
            # so the 0.3s poll doesn't spam the same form. Client also dedupes.
            for pq in (session.get('pending_questions') or []):
                qid = pq.get('question_id', '')
                if qid and qid in emitted_qids:
                    continue
                if qid:
                    emitted_qids.add(qid)
                yield (
                    "data: " +
                    json.dumps({
                        'type': 'question',
                        'question_id': qid,
                        'questions': pq.get('questions', []),
                    }) + "\n\n"
                )

            status = session['status']

            # Emit a `turn_start` event whenever status transitions INTO 'running'.
            # FE relies on this for the running-state UI flip post-Phase-2.
            if status == 'running' and last_emitted_status != 'running':
                yield f"data: {json.dumps({'type': 'turn_start', 'status': 'running'})}\n\n"
            last_emitted_status = status

            # Activity state (thinking / writing / tool) — only ever populated
            # when `activity_states_enabled` put --include-partial-messages on
            # the CLI. Flag off → key absent → no event → the FE keeps its plain
            # typing dots, exactly as before.
            act = session.get('activity_state') if status == 'running' else ''
            if act != last_emitted_activity:
                yield f"data: {json.dumps({'type': 'activity', 'state': act or ''})}\n\n"
                last_emitted_activity = act

            if is_mode_b:
                # A session that is idle ONLY because it is blocked on an
                # AskUserQuestion (or plan approval) is NOT "turn complete" —
                # the agent is parked waiting on the user. Suppress turn_complete
                # in that state. The FE turn_complete handler closes the SSE and
                # clears the asking-state, which races the `question` event
                # emitted just above: there is a TOCTOU between the
                # pending_questions read (loop top) and this status read — the
                # reader thread can flip status to 'idle' in between, so an
                # iteration emits turn_complete WITHOUT the question, the FE tears
                # the stream down, and the form is lost until a resync reconnects
                # (status shows "Completed" with no form). Keeping the stream
                # open lets the question reach the client; the form is driven by
                # the `question` event, never by turn_complete.
                waiting_on_user = (session.get('waiting_for_question')
                                   or session.get('waiting_for_plan_approval'))
                if status == 'idle' and not idle_sent and not waiting_on_user:
                    # Turn finished but process is still alive
                    yield f"data: {json.dumps({'type': 'turn_complete', 'status': 'idle', **_session_usage_payload(session)})}\n\n"
                    idle_sent = True
                elif status == 'running':
                    idle_sent = False  # reset for next turn
                elif status not in ('running', 'idle'):
                    if session.get('guardian_state') == 'recovering':
                        pass  # Wait for guardian recovery to complete
                    else:
                        yield f"data: {json.dumps({'type': 'status', 'status': status, **_session_usage_payload(session)})}\n\n"
                        break
            else:
                # Mode A: close stream on terminal states immediately;
                # for non-terminal non-running, wait only if followups pending
                if status == 'stopped':
                    yield f"data: {json.dumps({'type': 'status', 'status': status, **_session_usage_payload(session)})}\n\n"
                    break
                elif status != 'running':
                    if session.get('guardian_state') == 'recovering':
                        pass  # Wait for guardian recovery to complete
                    elif (session.get('waiting_for_question')
                          or session.get('waiting_for_plan_approval')):
                        # Blocked on user input — turn isn't complete. Keep the
                        # stream open so the `question` form is (re)delivered
                        # instead of closing on the idle status (same race the
                        # Mode B branch above guards against).
                        pass
                    elif not session.get('pending_followups') and not session.get('_dispatching_followup'):
                        # Eager-followup grace window: sendFollowup() opens the
                        # SSE BEFORE POSTing /agent/send, so the very first
                        # iterations here can read a stale terminal status (the
                        # prior turn) before write_followup has had a chance to
                        # flip status back to 'running'. Closing in that gap
                        # leaves the FE's status pill stuck on "Completed" for
                        # the ~2s reconnect window — exactly the
                        # COMPLETED-while-running bug Gemini Mode A exhibits.
                        # First ~3s of the stream: hold off; if a followup is
                        # actually incoming, status will flip to 'running' and
                        # turn_start will be emitted normally. If nothing
                        # arrives, fall through and close.
                        if tick < 10:  # 10 * 0.3s ≈ 3s grace
                            pass
                        else:
                            yield f"data: {json.dumps({'type': 'status', 'status': status, **_session_usage_payload(session)})}\n\n"
                            break

            # Emit guardian state changes
            g_state = session.get('guardian_state')
            if g_state != last_guardian_state:
                yield f"data: {json.dumps({'type': 'guardian', 'state': g_state, 'circuit_breaker': session.get('circuit_breaker_tripped', False)})}\n\n"
                last_guardian_state = g_state

            # Heartbeat every ~15s to keep connection alive
            # Sent as data event (not comment) so browser onmessage fires
            # and frontend watchdog can detect silent connection death.
            tick += 1
            if tick % 50 == 0:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

            _time.sleep(0.3)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/project/<project_id>/agent/followup', methods=['POST'])
def agent_followup(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set'}), 400

    data = request.get_json() or {}
    message = data.get('message', '').strip()
    session_id = data.get('session_id', '')
    if not message:
        return jsonify({'error': 'message required'}), 400
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    _respawn_b = None  # set if Mode B needs to respawn outside lock
    _model_route_state = None  # set when alive+auto_model_enabled; handled post-lock

    # Pre-check: if session is gone from agent_sessions (server restart, tab close,
    # 24h purge), try reviving from agent_log via -r <claude_session_id>.
    # Roll back: set CONFIG['agent_revive_from_log'] = False.
    mgr_pre = get_manager(project_id)
    with mgr_pre.lock:
        _has_session = (session_id in agent_sessions
                        and agent_sessions[session_id].get('project_id') == project_id)
    if not _has_session:
        revived = _revive_from_agent_log(project_id, session_id, message, p)
        if revived:
            _log_agent_activity(project_id, f"Agent revived from log: {message[:100]}")
            return jsonify({'ok': True, 'session_id': session_id, 'revived': True})
        # Not a Claude conversation (no claude_session_id) — no `-r` to revive
        # with, so start fresh instead of 404ing. This is what makes the
        # read-only reconstruct's "sending a message starts a brand-new
        # session" line (MC-929) true rather than a lie.
        fresh_sid = _revive_non_claude_from_agent_log(project_id, session_id, message, p)
        if fresh_sid:
            _log_agent_activity(project_id, f"Agent follow-up started a new session (no transcript to resume): {message[:100]}")
            return jsonify({'ok': True, 'session_id': fresh_sid, 'revived': False, 'fresh': True})
        # No revivable entry — fall through to original 404 below.

    with get_manager(project_id).lock:
        existing = agent_sessions.get(session_id)
        if not existing or existing['project_id'] != project_id:
            return jsonify({'error': 'session not found'}), 404

        # Clear plan approval / question flags — user has responded
        existing['waiting_for_plan_approval'] = False
        existing['waiting_for_question'] = False
        # Drop any persisted questions; the user's reply consumes them. (We
        # keep `pending_questions` populated until this point so a late /
        # reconnecting SSE client still sees the form — see the SSE generator.)
        existing.pop('pending_questions', None)

        # ── Multi-provider followup ─────────────────────────────────────────
        # Non-claude providers route through the runtime; their write_followup
        # owns process kill + respawn. We just append the user line and hand off.
        session_provider = (existing.get('provider') or 'claude').lower()
        if session_provider != 'claude':
            user_label = state.CONFIG.get('user_name') or 'User'
            if not existing.pop('_send_already_logged', False):
                existing['log_lines'].append(f"\n> {user_label}: {message}\n")
            existing['status'] = 'running'
            existing['last_status_change_time'] = _time.time()
            existing['last_output_time'] = _time.time()
            # Clear the in-flight marker set by agent_send: we're no longer
            # "dispatching", we're now "running". Leaving it set would prevent
            # the Mode A SSE from closing on the NEXT terminal status.
            existing.pop('_dispatching_followup', None)
            # Refresh the stashed system context so MEMORY / AGENT_RULES edits
            # made mid-conversation ride along on the next per-turn respawn.
            # The runtime re-injects existing['_system_prompt'] every followup
            # (see _compose_respawn_prompt in agent_runtime.py).
            if not existing.get('incognito'):
                try:
                    existing['_system_prompt'] = _build_agent_context(
                        p, incognito=False, task=message)
                except Exception as e:
                    _log(f"[followup] context refresh failed: {e}")
            try:
                runtime = _agent_runtime.get_runtime(session_provider)
                handle = _agent_runtime.SessionHandle(
                    mc_session_id=session_id,
                    provider=session_provider,
                    mode=existing.get('mode', 'A'),
                    project_path=pp,
                    project_id=project_id,
                    session_dict=existing,
                    # MC-930: this rebuilds the handle from scratch rather than
                    # reusing the dispatch one, so the completion hook has to be
                    # re-attached here or every turn after the first goes
                    # unlogged — the chat would show one row and then stop
                    # growing.
                    meta={'callbacks': _RUNTIME_CALLBACKS},
                )
                runtime.write_followup(handle, message)
            except Exception as e:
                existing['log_lines'].append(f"[{session_provider} followup error: {e}]")
                existing['status'] = 'error'
                existing['last_status_change_time'] = _time.time()
            _log_agent_activity(project_id, f"Agent follow-up (provider={session_provider}): {message[:100]}")
            return jsonify({'ok': True, 'session_id': session_id})

        if existing.get('mode') == 'B':
            # Mode B: verify process is actually alive before trusting the flag
            if existing.get('process_alive'):
                proc = existing.get('proc')
                if proc and (proc.poll() is not None or not _pid_is_alive(proc.pid)):
                    existing['process_alive'] = False
                    existing['log_lines'].append(
                        f'[Process {proc.pid} found dead on followup — will respawn]')
            if not existing.get('process_alive'):
                # Process died (hard stop or crash) — respawn
                claude_sid = existing.get('claude_session_id')
                was_resume = bool(existing.get('_resume_id'))
                resume_flags = []
                context = None

                if not claude_sid and not was_resume:
                    # No session ID at all and wasn't a resume — can't continue
                    _log(f"[followup] {project_id}: no claude_session_id, starting fresh")
                    context = _fresh_context_for(p, existing, message or '')
                    message = (f"[Previous conversation had no session ID to resume. "
                               f"Starting fresh.]\n\n{message}")
                elif not claude_sid and was_resume:
                    # Was a resume but CLI never emitted a session_id — start fresh
                    _log(f"[followup] {project_id}: resume never emitted session_id, starting fresh")
                    context = _fresh_context_for(p, existing, message or '')
                    message = (f"[Resumed session did not provide a continuable session ID. "
                               f"Starting fresh.]\n\n{message}")
                elif _resume_is_fragile(was_resume, existing.get('_resume_confirmed')):
                    # The resume itself proved fragile — it died BEFORE producing any
                    # output, so -r-ing it again would just loop. Start fresh.
                    # (A resume that already produced output is healthy and falls
                    # through to the -r path below, so an AskUserQuestion kill /
                    # idle-eviction / later crash keeps the full transcript.)
                    _log(f"[followup] {project_id}: resume {claude_sid[:12]} died before any output, starting fresh")
                    context = _fresh_context_for(p, existing, message or '')
                    existing['log_lines'].append(
                        '[Resume produced no output before exiting — restarting fresh]')
                    message = (f"[Continuing from a previous conversation (session {claude_sid}) whose "
                               f"process exited. Start fresh but continue the user's request.]\n\n{message}")
                else:
                    # Normal session, OR a resume that already produced output
                    # (healthy — it just died later). Resume with -r to keep context.
                    too_large, size_bytes = _session_too_large(pp, claude_sid)
                    if too_large:
                        size_mb = size_bytes / (1024 * 1024)
                        _log(f"[followup] Session {claude_sid} is {size_mb:.1f} MB — starting fresh")
                        _log_agent_activity(project_id,
                                            f"Auto-fresh: session too large ({size_mb:.0f} MB)")
                        existing['log_lines'].append(
                            f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')
                        context = _fresh_context_for(p, existing, message or '')
                        message = (f"[Continuing from a previous conversation that grew too large "
                                   f"to resume ({size_mb:.0f} MB). Start fresh.]\n\n{message}")
                    else:
                        resume_flags = ['-r', claude_sid]
                        _log(f"[followup] {project_id}: respawning Mode B with -r {claude_sid[:12]}")

                user_label = state.CONFIG.get('user_name') or 'User'
                if not existing.pop('_send_already_logged', False):
                    existing['log_lines'].append(f"\n> {user_label}: {message}\n")
                existing['status'] = 'running'
                existing['last_status_change_time'] = _time.time()
                existing['last_output_time'] = _time.time()
                existing.pop('evicted', None)  # respawned from idle-eviction → clear the State-1 skip flag
                existing.pop('_needs_respawn', None)  # this respawn already rebuilds flags+context
                # Clear the in-flight marker set by agent_send. Otherwise the
                # flag leaks past status='running' and, once this turn
                # completes (status flips back to idle/completed), Guardian
                # State 5 mops it up 30s later with a misleading
                # "[Guardian: cleared stuck dispatching flag]" line on every
                # message. Mode A clears it at the parallel spot below.
                existing.pop('_dispatching_followup', None)
                old_proc = existing.get('proc')
                if old_proc:
                    _unregister_process(old_proc.pid)
                    try:
                        old_proc.stdin.close()
                    except Exception:
                        pass
                # Build command while under lock, spawn outside to avoid blocking.
                # Honor a per-chat model pin across the death/respawn so the
                # conversation keeps its chosen model (else it silently reverts
                # to the project/global default on the next crash-respawn).
                _pin = existing.get('pinned_model') or None
                cmd = [_resolve_claude(), *resume_flags,
                       *_build_claude_flags(p, streaming=True, model_override=_pin)]
                if _pin:
                    existing['model'] = _pin
                    existing['model_source'] = 'manual'
                _sp_path = None
                if resume_flags:
                    # -r respawn: re-append the spawn context (stash-first —
                    # instant under mgr.lock; see _respawn_sysprompt_args).
                    _sp_args, _sp_path = _respawn_sysprompt_args(existing, p, message)
                    cmd.extend(_sp_args)
                elif context:
                    existing['_system_prompt'] = context
                    _sp_args, _sp_path = _sysprompt_file_args(context)
                    cmd.extend(_sp_args)
                _respawn_b = {
                    'cmd': cmd, 'pp': pp, 'message': message,
                    'existing': existing, 'session_id': session_id,
                    'project_id': project_id,
                    'old_proc': old_proc,
                    'sysprompt_path': _sp_path,
                    # Carry the request data so the closure can decide whether
                    # to apply the mobile-brief directive at the stdin write.
                    'request_data': data,
                }
                # Fall through — spawn happens after lock release
            else:
                # Process alive — optionally re-classify model before writing to stdin
                user_label = state.CONFIG.get('user_name') or 'User'
                if not existing.pop('_send_already_logged', False):
                    existing['log_lines'].append(f"\n> {user_label}: {message}\n")
                existing['status'] = 'running'
                existing['last_status_change_time'] = _time.time()
                existing['last_output_time'] = _time.time()
                # Clear the in-flight marker set by agent_send — see note on
                # the parallel respawn branch above for why this matters.
                existing.pop('_dispatching_followup', None)

                # Sticky-settings respawn: a spawn-baked (Tier-1) setting was
                # flipped mid-session (see update_config). A live CLI can't see
                # CLI/system-prompt changes, so resume it into a fresh process
                # that rebuilds flags + context from current CONFIG. Mirrors the
                # auto-router's alive-process respawn just below.
                if (state.CONFIG.get('sticky_agent_settings', False)
                        and existing.pop('_needs_respawn', None)):
                    _sticky_sid = existing.get('claude_session_id')
                    _sticky_resume = ['-r', _sticky_sid] if _sticky_sid else []
                    # A per-chat pin outranks the project/global model even while
                    # applying other changed settings — keep it across the respawn.
                    _sticky_pin = existing.get('pinned_model') or None
                    _sticky_cmd = [_resolve_claude(), *_sticky_resume,
                                   *_build_claude_flags(p, streaming=True,
                                                        model_override=_sticky_pin)]
                    if _sticky_pin:
                        existing['model'] = _sticky_pin
                        existing['model_source'] = 'manual'
                    # Rebuild the context (not the stash): the whole point of
                    # this respawn is applying changed settings, and since CLI
                    # ≥2.1.206 the -r re-append DOES take effect — so
                    # system-prompt (Tier-1b) settings now ride along too.
                    _sticky_sp = None
                    try:
                        _sticky_ctx = _fresh_context_for(p, existing, message or '')
                        existing['_system_prompt'] = _sticky_ctx
                        _sargs, _sticky_sp = _sysprompt_file_args(_sticky_ctx)
                        _sticky_cmd.extend(_sargs)
                    except Exception as _se:
                        _log(f"[sticky-respawn] context rebuild failed: {_se}")
                    existing['process_alive'] = False
                    existing['log_lines'].append('[Settings changed — applying via resume]')
                    _sticky_old = existing.get('proc')
                    if _sticky_old:
                        _unregister_process(_sticky_old.pid)
                        try:
                            _sticky_old.stdin.close()
                        except Exception:
                            pass
                    _respawn_b = {
                        'cmd': _sticky_cmd, 'pp': pp, 'message': message,
                        'existing': existing, 'session_id': session_id,
                        'project_id': project_id,
                        'old_proc': _sticky_old,
                        'sysprompt_path': _sticky_sp,
                        'request_data': data,
                    }
                    # Fall through — spawn happens after lock release.
                elif existing.get('pinned_model') and message:
                    # Per-chat manual pin — user intent beats the classifier, so
                    # the auto-router is bypassed. Respawn only when the running
                    # model differs from the pin (post-lock, mirroring the auto
                    # path). The 'pinned' key signals the fixed target.
                    _model_route_state = {
                        'current_model': existing.get('model') or 'sonnet',
                        'claude_sid': existing.get('claude_session_id'),
                        'proc': existing['proc'],
                        'existing': existing,
                        'pinned': existing['pinned_model'],
                    }
                    # Fall through to post-lock routing section
                elif state.CONFIG.get('auto_model_enabled', False) and message:
                    # Defer stdin write — classify model post-lock to avoid
                    # blocking mgr.lock for the ~0.5s Haiku classifier call.
                    _model_route_state = {
                        'current_model': existing.get('model') or 'sonnet',
                        'claude_sid': existing.get('claude_session_id'),
                        'proc': existing['proc'],
                        'existing': existing,
                    }
                    # Fall through to post-lock routing section
                else:
                    # Router off — write stdin directly (original path)
                    claude_content = _apply_mobile_brief(message, data)
                    stdin_msg = json.dumps({
                        "type": "user",
                        "message": {"role": "user", "content": claude_content}
                    }) + '\n'

                    def _write_stdin():
                        lock = existing.get('stdin_lock')
                        if lock:
                            lock.acquire()
                        try:
                            existing['proc'].stdin.write(stdin_msg)
                            existing['proc'].stdin.flush()
                        except Exception as e:
                            existing['log_lines'].append(f'[stdin write error: {e}]')
                            existing['status'] = 'error'
                            existing['last_status_change_time'] = _time.time()
                            existing['process_alive'] = False
                        finally:
                            if lock:
                                lock.release()

                    threading.Thread(target=_write_stdin, daemon=True).start()
                    _log_agent_activity(project_id, f"Agent follow-up: {message[:100]}")
                    return jsonify({'ok': True, 'session_id': session_id})

        else:
            # Mode A: existing behavior
            # If agent is still running, queue the follow-up instead of killing
            if existing['status'] == 'running':
                pending = existing.setdefault('pending_followups', [])
                pending.append(message)
                user_label = state.CONFIG.get('user_name') or 'User'
                existing['log_lines'].append(f"> [queued] {user_label}: {message}")
                _log_agent_activity(project_id, f"Agent follow-up queued: {message[:100]}")
                return jsonify({'ok': True, 'queued': True, 'session_id': session_id})

            # Mark as running and return quickly — spawn process in background
            existing['status'] = 'running'
            existing['last_status_change_time'] = _time.time()
            existing['last_output_time'] = _time.time()
            existing['pending_recovery_message'] = message
            # Clear the in-flight marker set by agent_send (or left by an auto-
            # dispatched queued followup): we're no longer "dispatching", we're
            # now "running". Leaving it set would prevent the Mode A SSE from
            # closing on the NEXT terminal status.
            existing.pop('_dispatching_followup', None)
            user_label = state.CONFIG.get('user_name') or 'User'
            if not existing.pop('_send_already_logged', False):
                existing['log_lines'].append(f"\n> {user_label}: {message}\n")
            claude_sid = existing.get('claude_session_id')

    # Mode B per-turn model routing (process alive, auto_model_enabled=True)
    # Classifier runs here — outside mgr.lock — to avoid blocking other requests.
    if _model_route_state:
        mrs = _model_route_state
        _pinned = mrs.get('pinned')
        if _pinned:
            # Manual per-chat pin: the target IS the pin, no classifier call.
            new_model, new_source = _pinned, 'manual'
        else:
            new_model, new_source = _resolve_dispatch_model(p, message)
        current_model = mrs['current_model']
        _router_stat(project_id,
                     requested_model=current_model,
                     chosen_model=new_model,
                     source=new_source)
        if _router_model_tier(new_model) != _router_model_tier(current_model):
            # Model tier changed — kill current process, respawn with -r + new model
            _switch_kind = 'pin' if _pinned else 'auto-router'
            _log(f"[followup-B-route] {project_id}: {_switch_kind} model switch {current_model} → {new_model}")
            claude_sid = mrs['claude_sid']
            resume_flags = ['-r', claude_sid] if claude_sid else []
            _sp_path = None
            cmd = [_resolve_claude(), *resume_flags,
                   *_build_claude_flags(p, streaming=True, model_override=new_model)]
            if resume_flags:
                _sp_args, _sp_path = _respawn_sysprompt_args(mrs['existing'], p, message)
            else:
                _route_context = _fresh_context_for(p, existing, message or '')
                _sp_args, _sp_path = _sysprompt_file_args(_route_context)
            cmd.extend(_sp_args)
            with get_manager(project_id).lock:
                _route_existing = agent_sessions.get(session_id)
                if _route_existing:
                    _route_existing['model'] = new_model
                    _route_existing['model_source'] = new_source
                    _route_existing['process_alive'] = False
                    _route_existing['log_lines'].append(
                        (f'[Model pinned: switching {current_model} → {new_model}]'
                         if _pinned else
                         f'[Auto-router: switching {current_model} → {new_model}]'))
            _respawn_b = {
                'cmd': cmd, 'pp': pp, 'message': message,
                'existing': mrs['existing'],
                'session_id': session_id, 'project_id': project_id,
                'old_proc': mrs['proc'],
                'sysprompt_path': _sp_path,
                'request_data': data,
            }
        else:
            # Same tier — write stdin directly
            _rs_existing = mrs['existing']
            claude_content = _apply_mobile_brief(message, data)
            stdin_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": claude_content}
            }) + '\n'

            def _write_stdin_routed():
                lock = _rs_existing.get('stdin_lock')
                if lock:
                    lock.acquire()
                try:
                    _rs_existing['proc'].stdin.write(stdin_msg)
                    _rs_existing['proc'].stdin.flush()
                except Exception as e:
                    _rs_existing['log_lines'].append(f'[stdin write error: {e}]')
                    _rs_existing['status'] = 'error'
                    _rs_existing['last_status_change_time'] = _time.time()
                    _rs_existing['process_alive'] = False
                finally:
                    if lock:
                        lock.release()

            threading.Thread(target=_write_stdin_routed, daemon=True).start()
            _log_agent_activity(project_id, f"Agent follow-up: {message[:100]}")
            return jsonify({'ok': True, 'session_id': session_id})

    # Mode B respawn — spawn outside the lock to avoid blocking stop/other ops
    if _respawn_b:
        rb = _respawn_b
        # Kill the old process if still alive (outside lock)
        if rb.get('old_proc'):
            _kill_proc_background(rb['old_proc'])
        def _do_respawn_b():
            try:
                _log(f"[respawn-B] {rb['project_id']}: spawning cmd={' '.join(rb['cmd'][:5])}...")
                proc = subprocess.Popen(
                    rb['cmd'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=rb['pp'],
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                _sysprompt_cleanup(rb.get('sysprompt_path'), proc)
                _log(f"[respawn-B] {rb['project_id']}: spawned PID {proc.pid}")
                threading.Thread(target=_hide_windows_delayed,
                                 args=(proc.pid,), daemon=True).start()
                _register_process(proc, 'Agent respawn (B)', 'agent',
                                  rb['session_id'], rb['project_id'],
                                  rb['message'][:80])
                with get_manager(rb['project_id']).lock:
                    rb['existing']['proc'] = proc
                    rb['existing']['process_alive'] = True
                    rb['existing']['stdin_lock'] = threading.Lock()
                    rb['existing']['pending_recovery_message'] = None
                    rb['existing']['_resume_id'] = None  # clear resume context for future follow-ups

                threading.Thread(target=_read_agent_stream_b,
                                 args=(proc, rb['existing']), daemon=True).start()

                # Send message to stdin. Mobile-brief directive applied here so
                # rb['message'] keeps the original for telemetry / log_lines.
                claude_content = _apply_mobile_brief(rb['message'], rb.get('request_data') or {})
                stdin_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": claude_content}
                }) + '\n'
                lock = rb['existing']['stdin_lock']
                with lock:
                    proc.stdin.write(stdin_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                    proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
            except Exception as e:
                _log(f"[respawn-B] {rb['project_id']}: FAILED — {e}")
                rb['existing']['log_lines'].append(f'[respawn error: {e}]')
                rb['existing']['status'] = 'error'
                rb['existing']['last_status_change_time'] = _time.time()
                rb['existing']['process_alive'] = False
                # Pop the temp sysprompt file: spawn never reached the
                # cleanup wiring, so the watchdog thread doesn't exist.
                _sp_orphan = rb.get('sysprompt_path')
                if _sp_orphan:
                    try:
                        os.unlink(_sp_orphan)
                    except OSError:
                        pass

        threading.Thread(target=_do_respawn_b, daemon=True).start()
        _log_agent_activity(project_id, f"Agent resumed: {message[:100]}")
        return jsonify({'ok': True, 'session_id': session_id, 'resumed': True})

    # Mode A: Spawn process outside the lock to avoid blocking other requests
    def _start_followup():
        _sp_path = None  # bound here so the except below can sweep on early failure
        try:
            followup_msg = message
            if claude_sid:
                too_large, size_bytes = _session_too_large(pp, claude_sid)
                if too_large:
                    size_mb = size_bytes / (1024 * 1024)
                    _log(f"[followup-A] Session {claude_sid} is {size_mb:.1f} MB — starting fresh")
                    _log_agent_activity(project_id,
                                        f"Auto-fresh: session too large ({size_mb:.0f} MB)")
                    with get_manager(project_id).lock:
                        existing['log_lines'].append(
                            f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')
                    context = _fresh_context_for(p, existing, message or '')
                    followup_msg = (f"[Continuing from a previous conversation that grew too large "
                                    f"to resume ({size_mb:.0f} MB). Start fresh.]\n\n{message}")
                    resume_flags = []
                else:
                    resume_flags = ['-r', claude_sid]
            else:
                resume_flags = ['--continue']
            # Mobile-brief applied here so log_lines + telemetry use the
            # unaugmented message; only the claude -p arg gets the directive.
            claude_followup_msg = _apply_mobile_brief(followup_msg, data)
            # Honor a per-chat model pin (Mode A rebuilds the command each turn).
            _pin = existing.get('pinned_model') or None
            cmd = [_resolve_claude(), *resume_flags, '-p', claude_followup_msg,
                   *_build_claude_flags(p, model_override=_pin)]
            if _pin:
                existing['model'] = _pin
                existing['model_source'] = 'manual'
            if resume_flags:
                _sp_args, _sp_path = _respawn_sysprompt_args(existing, p, followup_msg)
            else:
                existing['_system_prompt'] = context
                _sp_args, _sp_path = _sysprompt_file_args(context)
            cmd.extend(_sp_args)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=pp,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )
            _sysprompt_cleanup(_sp_path, proc)
            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            old_proc = existing.get('proc')
            if old_proc:
                _unregister_process(old_proc.pid)
            existing['proc'] = proc
            existing['pending_recovery_message'] = None
            _register_process(proc, 'Agent followup (A)', 'agent',
                              session_id, project_id, followup_msg[:80])
            threading.Thread(target=_read_agent_stream, args=(proc, existing), daemon=True).start()
        except Exception as e:
            with get_manager(project_id).lock:
                existing['log_lines'].append(f'[follow-up process failed: {e}]')
                existing['status'] = 'error'
                existing['last_status_change_time'] = _time.time()
            # Popen may have raised before sysprompt cleanup was wired — sweep.
            if _sp_path:
                try:
                    os.unlink(_sp_path)
                except OSError:
                    pass

    threading.Thread(target=_start_followup, daemon=True).start()

    _log_agent_activity(project_id, f"Agent follow-up: {message[:100]}")
    return jsonify({'ok': True, 'session_id': session_id})


def _stop_session(session, session_id):
    """Internal helper: stop a session and kill its process.
    Must be called with the project's manager lock held. Returns the proc to kill outside the lock."""
    proc = session['proc']
    session['status'] = 'stopped'
    session['last_status_change_time'] = _time.time()
    session['log_lines'].append('[Agent stopped by user]')
    # Clear any pending followups — they're stale after a user-initiated stop
    session.pop('pending_followups', None)
    session.pop('_dispatching_followup', None)
    if session.get('mode') == 'B':
        try:
            proc.stdin.close()
        except Exception:
            pass
        session['process_alive'] = False
    _unregister_process(proc.pid)
    return proc


def _kill_proc_background(proc):
    """Kill a process and its tree in a background thread."""
    def _do_kill():
        _kill_pid(proc.pid, tree=True)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    threading.Thread(target=_do_kill, daemon=True).start()


@bp.route('/api/project/<project_id>/agent/stop', methods=['POST'])
def agent_stop(project_id):
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    # Idempotent: pressing Stop is always safe — if there's nothing to stop,
    # we return 200 with `already_stopped: true` instead of an error. This lets
    # the frontend treat the button as "ensure stopped" rather than reasoning
    # about cached status. (Phase 2 of the 2026-04-27 race consolidation.)
    proc = None
    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'ok': True, 'already_stopped': True, 'reason': 'no session'})
        if session['status'] not in ('running', 'idle', 'error'):
            return jsonify({'ok': True, 'already_stopped': True, 'reason': session['status']})
        proc = _stop_session(session, session_id)

    if proc is not None:
        # Kill outside the lock — taskkill can take seconds on Windows
        _kill_proc_background(proc)
        _log_agent_activity(project_id, "Agent stopped by user")

    return jsonify({'ok': True})


@bp.route('/api/project/<project_id>/agent/interrupt', methods=['POST'])
def agent_interrupt(project_id):
    """Atomic stop + immediate resume with a new prompt.
    Kills the current process and respawns with -r <session_id> in one operation.
    This avoids the broken intermediate 'stopped' state."""
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set'}), 400

    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    message = data.get('message', '').strip()
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400
    if not message:
        return jsonify({'error': 'message required'}), 400

    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'error': 'session not found'}), 404
        if session['status'] not in ('running', 'idle', 'error'):
            return jsonify({'error': 'agent not active'}), 400

        # ── Multi-provider interrupt ──────────────────────────────────────
        # Non-claude providers: kill via runtime.interrupt(), then re-dispatch
        # the new message via runtime.write_followup() — the runtime owns
        # both halves of the respawn dance.
        session_provider = (session.get('provider') or 'claude').lower()
        if session_provider != 'claude':
            user_label = state.CONFIG.get('user_name') or 'User'
            if not session.pop('_send_already_logged', False):
                session['log_lines'].append('[Got your message]')
                session['log_lines'].append(f"\n> {user_label}: {message}\n")
            session.pop('pending_followups', None)
            # Refresh stashed context (see the followup path for rationale).
            if not session.get('incognito'):
                try:
                    session['_system_prompt'] = _build_agent_context(
                        p, incognito=False, task=message)
                except Exception as e:
                    _log(f"[interrupt] context refresh failed: {e}")
            try:
                runtime = _agent_runtime.get_runtime(session_provider)
                handle = _agent_runtime.SessionHandle(
                    mc_session_id=session_id,
                    provider=session_provider,
                    mode=session.get('mode', 'A'),
                    project_path=pp,
                    project_id=project_id,
                    session_dict=session,
                    # MC-930: this rebuilds the handle from scratch rather than
                    # reusing the dispatch one, so the completion hook has to be
                    # re-attached here or every turn after the first goes
                    # unlogged — the chat would show one row and then stop
                    # growing.
                    meta={'callbacks': _RUNTIME_CALLBACKS},
                )
                runtime.write_followup(handle, message)
            except Exception as e:
                session['log_lines'].append(f"[{session_provider} interrupt error: {e}]")
                session['status'] = 'error'
                session['last_status_change_time'] = _time.time()
            _log_agent_activity(project_id, f"Agent interrupt (provider={session_provider}): {message[:80]}")
            return jsonify({'ok': True, 'session_id': session_id})

        old_proc = session['proc']
        claude_sid = session.get('claude_session_id')

        # Mark as interrupting BEFORE killing the old proc. The old reader's
        # finally block will see this flag (via _session_owned_by) and skip
        # all status / process_alive writes, eliminating the stale-status
        # flash that flipped the UI to "stopped" between kill and respawn.
        # Cleared by the respawn thread once the new proc replaces session['proc'].
        session['_interrupting'] = True

        # Stop the current process
        # Shown in the chat as a system-style line when the user interrupts a
        # running turn with a new message. Friendlier than "Agent interrupted
        # by user" — the user already knows they interrupted; this is the
        # acknowledgement bubble.
        session['log_lines'].append('[Got your message]')
        session.pop('pending_followups', None)
        session.pop('_dispatching_followup', None)
        session['waiting_for_plan_approval'] = False
        session['waiting_for_question'] = False
        session.pop('pending_questions', None)
        if session.get('mode') == 'B':
            try:
                old_proc.stdin.close()
            except Exception:
                pass
        _unregister_process(old_proc.pid)

        # Immediately set status to running for the new prompt
        user_label = state.CONFIG.get('user_name') or 'User'
        if not session.pop('_send_already_logged', False):
            session['log_lines'].append(f"\n> {user_label}: {message}\n")
        session['status'] = 'running'
        session['last_status_change_time'] = _time.time()
        session['last_output_time'] = _time.time()
        session['process_alive'] = True

    # Kill old process in background
    _kill_proc_background(old_proc)

    # Respawn with the new message
    is_mode_b = session.get('mode') == 'B'

    def _do_respawn():
        _sp_path = None  # bound here so the except below can sweep on early failure
        try:
            # Check transcript size
            resume_flags = []
            context = None
            respawn_msg = message
            if claude_sid:
                too_large, size_bytes = _session_too_large(pp, claude_sid)
                if too_large:
                    size_mb = size_bytes / (1024 * 1024)
                    session['log_lines'].append(
                        f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')
                    context = _fresh_context_for(p, session, message or '')
                    respawn_msg = (f"[Continuing from a previous conversation that grew too large "
                                   f"to resume ({size_mb:.0f} MB). Start fresh.]\n\n{message}")
                else:
                    resume_flags = ['-r', claude_sid]
            else:
                context = _fresh_context_for(p, session, message or '')

            if is_mode_b:
                cmd = [_resolve_claude(), *resume_flags,
                       *_build_claude_flags(p, streaming=True)]
                if resume_flags:
                    _sp_args, _sp_path = _respawn_sysprompt_args(session, p, respawn_msg)
                    cmd.extend(_sp_args)
                elif context:
                    session['_system_prompt'] = context
                    _sp_args, _sp_path = _sysprompt_file_args(context)
                    cmd.extend(_sp_args)
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=pp,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                _sysprompt_cleanup(_sp_path, proc)
                threading.Thread(target=_hide_windows_delayed,
                                 args=(proc.pid,), daemon=True).start()
                _register_process(proc, 'Agent interrupt-resume (B)', 'agent',
                                  session_id, project_id, message[:80])
                with get_manager(project_id).lock:
                    session['proc'] = proc
                    session['process_alive'] = True
                    session['stdin_lock'] = threading.Lock()
                    # New proc is now the authoritative one — clear the
                    # interrupt gate so its reader's writes are accepted.
                    session.pop('_interrupting', None)

                threading.Thread(target=_read_agent_stream_b,
                                 args=(proc, session), daemon=True).start()

                # Send the new message
                # Mobile-brief applied only to the claude-bound payload —
                # log_lines + telemetry above keep the unaugmented message.
                claude_content = _apply_mobile_brief(respawn_msg, data)
                stdin_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": claude_content}
                }) + '\n'
                with session['stdin_lock']:
                    proc.stdin.write(stdin_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                    proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
            else:
                # Mode A
                claude_respawn_msg = _apply_mobile_brief(respawn_msg, data)
                if resume_flags:
                    _sp_args, _sp_path = _respawn_sysprompt_args(session, p, respawn_msg)
                    cmd = [_resolve_claude(), *resume_flags, '-p', claude_respawn_msg,
                           *_build_claude_flags(p), *_sp_args]
                else:
                    if not context:
                        context = _fresh_context_for(p, session, message or '')
                    session['_system_prompt'] = context
                    _sp_args, _sp_path = _sysprompt_file_args(context)
                    cmd = [_resolve_claude(), '-p', claude_respawn_msg, *_build_claude_flags(p),
                           *_sp_args]

                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=pp,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                _sysprompt_cleanup(_sp_path, proc)
                threading.Thread(target=_hide_windows_delayed,
                                 args=(proc.pid,), daemon=True).start()
                _register_process(proc, 'Agent interrupt-resume (A)', 'agent',
                                  session_id, project_id, message[:80])
                with get_manager(project_id).lock:
                    session['proc'] = proc
                    # New proc is now authoritative — clear the interrupt gate.
                    session.pop('_interrupting', None)

                threading.Thread(target=_read_agent_stream,
                                 args=(proc, session), daemon=True).start()

        except Exception as e:
            session['log_lines'].append(f'[interrupt-resume error: {e}]')
            session['status'] = 'error'
            session['last_status_change_time'] = _time.time()
            session['process_alive'] = False
            # Clear the interrupt gate on failure too — otherwise the session
            # stays permanently gated and no future reader can update status.
            session.pop('_interrupting', None)
            # Popen may have raised before sysprompt cleanup was wired — sweep.
            if _sp_path:
                try:
                    os.unlink(_sp_path)
                except OSError:
                    pass

    threading.Thread(target=_do_respawn, daemon=True).start()

    _log_agent_activity(project_id, f"Agent interrupted: {message[:100]}")
    return jsonify({'ok': True, 'session_id': session_id})


@bp.route('/api/project/<project_id>/agent/session', methods=['DELETE', 'POST'])
def agent_session_delete(project_id):
    """Kill process (if running), wait for exit, and remove session entirely.
    Accepts POST in addition to DELETE for navigator.sendBeacon compatibility."""
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get('session_id', '')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    proc = None
    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'ok': True})  # Already gone — idempotent
        if session['status'] in ('running', 'idle'):
            proc = session['proc']
            session['status'] = 'stopped'
            session['last_status_change_time'] = _time.time()
            session['log_lines'].append('[Agent stopped — tab closed]')
            if session.get('mode') == 'B':
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                session['process_alive'] = False
            _kill_pid(proc.pid, tree=True)
            try:
                proc.kill()
            except Exception:
                pass
            _unregister_process(proc.pid)

    # Wait outside lock for process to fully exit
    if proc:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    # Remove session from tracking.
    # The stream reader thread has already called _log_agent_completion()
    # in its finally block after proc.wait(), so usage data is persisted.
    mgr = get_manager(project_id)
    with mgr.lock:
        agent_sessions.pop(session_id, None)
        mgr.session_ids.discard(session_id)

    return jsonify({'ok': True})


@bp.route('/api/project/<project_id>/agent/plan-file')
def agent_plan_file(project_id):
    """Read and return the plan .md file content for a session."""
    session_id = request.args.get('session', '')
    session = agent_sessions.get(session_id)
    if not session or session['project_id'] != project_id:
        return jsonify({'error': 'session not found'}), 404
    plan_path = session.get('plan_file', '')
    if not plan_path:
        return jsonify({'error': 'no plan file'}), 404
    p = Path(plan_path)
    # Same containment gate as /api/plan-file below. Belt and braces: the two
    # writers of session['plan_file'] both check _is_plan_path now, but this
    # endpoint hands file CONTENT to the browser, so it should not depend on
    # every future writer remembering to.
    if not _is_plan_path(plan_path):
        return jsonify({'error': 'access denied'}), 403
    if not p.is_file():
        return jsonify({'error': 'file not found'}), 404
    try:
        content = p.read_text(encoding='utf-8')
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'path': str(p), 'filename': p.name, 'content': content})


@bp.route('/api/plan-file')
def read_plan_file():
    """Read a plan file by path (for plan history viewer)."""
    plan_path = request.args.get('path', '')
    if not plan_path:
        return jsonify({'error': 'path required'}), 400
    p = Path(plan_path)
    # Security: only allow reading from ~/.claude/plans/
    plans_dir = Path.home() / '.claude' / 'plans'
    try:
        p.resolve().relative_to(plans_dir.resolve())
    except ValueError:
        return jsonify({'error': 'access denied'}), 403
    if not p.is_file():
        return jsonify({'error': 'file not found'}), 404
    try:
        content = p.read_text(encoding='utf-8')
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'path': str(p), 'filename': p.name, 'content': content})


@bp.route('/api/plans/delete', methods=['POST'])
def delete_plans():
    """Delete plan files from disk and scrub references from agent logs."""
    data = request.get_json(force=True) or {}
    paths = data.get('paths', [])
    if not paths or not isinstance(paths, list):
        return jsonify({'error': 'paths array required'}), 400
    plans_dir = Path.home() / '.claude' / 'plans'
    resolved_plans_dir = plans_dir.resolve()
    deleted = 0
    deleted_paths = set()
    for plan_path in paths:
        p = Path(plan_path)
        try:
            if not p.resolve().is_relative_to(resolved_plans_dir):
                continue
        except Exception:
            continue
        if p.is_file():
            try:
                p.unlink()
                deleted += 1
            except Exception:
                pass
        deleted_paths.add(str(p))
    # Scrub plan_file references from all agent logs
    if deleted_paths:
        for log_file in DATA_DIR.glob('*_agent_log.json'):
            try:
                log = json.loads(log_file.read_text(encoding='utf-8'))
                changed = False
                for entry in log:
                    if entry.get('plan_file', '') in deleted_paths:
                        entry['plan_file'] = ''
                    if entry.get('plan_files'):
                        entry['plan_files'] = [p for p in entry['plan_files']
                                               if p not in deleted_paths]
                        changed = True
                if changed:
                    log_file.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass
    return jsonify({'ok': True, 'deleted': deleted})


def _session_model_field(s, proj_default_model):
    """Model string to report for a session, with the project default applied
    only where it is actually meaningful.

    A project's `agent_model` is always a claude id — the Agent-settings picker
    offers nothing else — so it must not be used as the fallback for a session
    running on another runtime. Doing so made a codex session's header pill read
    "codex · claude-opus-5" for a spawn that got no --model at all. For those,
    an empty string is the truth: the provider's own default applies.
    """
    own = s.get('agent_model') or ''
    if own:
        return own
    return proj_default_model if (s.get('provider') or 'claude') == 'claude' else ''


@bp.route('/api/project/<project_id>/agent/status')
def agent_status(project_id):
    sessions = []
    # Hoist project lookup + per-project default model out of the loop. Used
    # as a fallback for legacy sessions that were dispatched before
    # session['agent_model'] was captured per-dispatch.
    _proj = load_project(project_id) or {}
    _proj_default_model = (_proj.get('agent_model')
                           or state.CONFIG.get('agent_model') or '')
    # Per-conversation pin state is computed SERVER-SIDE: the server always knows
    # a live session's claude_session_id (the durable pin key), whereas the
    # client's cached copy can lag a freshly-assigned id (Mode B receives it via
    # the stream init event, possibly after the client's last poll). The client
    # renders the pin marker from this flag instead of resolving identity itself.
    _pinned_csids = _proj.get('pinned_conversations') or []
    for sid, s in agent_sessions.items():
        if s['project_id'] == project_id:
            sessions.append({
                'session_id': s['session_id'],
                'claude_session_id': s.get('claude_session_id', ''),
                'status': s['status'],
                'task': s['task'],
                'log_lines': [l for l in s['log_lines']
                              if not l.startswith('[terminal:')
                              or l.split(':')[1] in terminal_sessions],
                'started_at': s['started_at'],
                'plan_file': s.get('plan_file', ''),
                'plan_files': _session_plan_files(s),
                'usage': s.get('usage', {}),
                'cost_usd': s.get('cost_usd', 0),
                'num_turns': s.get('num_turns', 0),
                'mode': s.get('mode', 'A'),
                'long_session_advisory': _long_session_advisory(s),
                'process_alive': s.get('process_alive', False) if s.get('mode') == 'B' else (s['status'] in ('running',)),
                'hivemind_id': s.get('hivemind_id', ''),
                'hivemind_ws_id': s.get('hivemind_ws_id', ''),
                'hivemind_role': s.get('hivemind_role', ''),
                'trigger_type': s.get('trigger_type', 'manual'),
                'trigger_id': s.get('trigger_id', ''),
                'waiting_for_plan_approval': s.get('waiting_for_plan_approval', False),
                'waiting_for_question': s.get('waiting_for_question', False),
                # Mirror pending_questions so the FE can re-render the form on
                # reconcile even when SSE silently buffered the question event
                # (mobile WebView / CF Tunnel). Dedupe is by question_id on
                # the client, so re-delivery is safe.
                'pending_questions': s.get('pending_questions', []) if s.get('waiting_for_question') else [],
                'guardian_state': s.get('guardian_state'),
                'circuit_breaker_tripped': s.get('circuit_breaker_tripped', False),
                'incognito': s.get('incognito', False),
                'provider': s.get('provider') or 'claude',
                # Per-session snapshot, with project-level fallback so older
                # sessions (dispatched before the field was captured) still
                # surface a usable model string. Empty = provider default.
                #
                # The fallback is claude-ONLY: `agent_model` on a project is
                # always a claude id, so applying it to a codex/gemini session
                # made the header pill claim "codex · claude-opus-5" for a run
                # that never received a --model at all.
                'agent_model': _session_model_field(s, _proj_default_model),
                # Auto-router attribution. `model` is what actually got
                # passed via --model (may differ from agent_model when the
                # router picked something else). `model_source` is one of
                # 'manual' / 'auto' / 'fallback'. Frontend pill reads
                # both to decide whether to render an auto-router badge.
                'model': s.get('model') or _session_model_field(s, _proj_default_model),
                'model_source': s.get('model_source', 'manual'),
                # Per-chat model pin (empty = follow default/auto). Drives the
                # header pill's "pinned" state + the in-chat model switcher.
                'pinned_model': s.get('pinned_model', ''),
                # Per-chat persona {name,scope,display_name} or None → header pill.
                'character': s.get('character'),
                # Chat-level pin marker — server-authoritative (see _pinned_csids).
                'pinned': bool(s.get('claude_session_id')
                               and s.get('claude_session_id') in _pinned_csids),
            })
    # Sort: running first, then newest first (ISO timestamps sort lexically)
    sessions.sort(key=lambda s: (
        0 if s['status'] == 'running' else 1,
        '~' if not s.get('started_at') else s['started_at']
    ), reverse=False)
    # Within each group, newest first
    sessions.sort(key=lambda s: s.get('started_at', ''), reverse=True)
    sessions.sort(key=lambda s: 0 if s['status'] in ('running', 'idle') else 1)
    # P2-1: surface memory-condensation state (default {'state':'idle'} so
    # the field is always present for the frontend).
    return jsonify({'sessions': sessions,
                    'condense': _get_condense_status(project_id)})


@bp.route('/api/project/<project_id>/agent/guardian-reset', methods=['POST'])
def agent_guardian_reset(project_id):
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    action = data.get('action', 'retry')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400
    retry_message = None
    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'error': 'session not found'}), 404
        if action == 'retry':
            session['circuit_breaker_tripped'] = False
            session['recovery_attempts'] = 0
            session['guardian_state'] = 'recovering'
            session['log_lines'].append('[Guardian: retry requested by user]')
            retry_message = session.get('pending_recovery_message')
            if not retry_message:
                retry_message = 'Continue where you left off.'
                session['pending_recovery_message'] = retry_message
        elif action == 'dismiss':
            session['guardian_state'] = None
            session['pending_recovery_message'] = None

    if retry_message:
        threading.Thread(
            target=_guardian_attempt_recovery,
            args=(session,), daemon=True).start()

    return jsonify({'ok': True})


# ── Agent log endpoint ────────────────────────────────────────────────────────

def _looks_like_claydo_entry(entry):
    """Heuristic: does this agent_log entry look like a Claydo conversation
    that ended up in a project's log via transcript-backfill pollution?

    We catch the unmistakable signature: tasks that start with
    "Previous exchange in this conversation:" — that's the prefix WE
    generate when sending Claydo's history context. No real MC user task
    would ever start that way.

    First-turn Claydo entries (no history prefix) are indistinguishable
    from real questions, so we leave them. The cwd fix in `_claydo_cwd`
    prevents any new Claydo transcripts from leaking into project
    agent_logs going forward; this filter just suppresses the leftover
    follow-up entries that landed before the cwd fix.
    """
    task = (entry.get('task') or '').lstrip()
    return task.startswith('Previous exchange in this conversation:')


@bp.route('/api/project/<project_id>/agent/log')
def get_agent_log(project_id):
    log = _load_agent_log(project_id)
    log = [e for e in log if not _looks_like_claydo_entry(e)]
    for entry in log:
        entry['ts_relative'] = time_ago(entry.get('ts'))
        entry['started_relative'] = time_ago(entry.get('started_at'))
    return jsonify(log)


def _enrich_run_entries(entries):
    """Add ts_relative + started_relative for FE display. Also fill in
    `claude_session_id` for in-progress rows by looking up the live
    `agent_sessions[session_id].claude_session_id` — these rows are written
    at dispatch time before claude assigns a csid, and Mode B idle sessions
    that never finalize would otherwise have an empty csid forever, leaving
    the FE with no way to open the transcript even though it exists on disk.
    """
    for e in entries:
        e['ts_relative'] = time_ago(e.get('ts'))
        e['started_relative'] = time_ago(e.get('started_at'))
        if not e.get('claude_session_id'):
            sid = e.get('session_id')
            if sid:
                live = agent_sessions.get(sid)
                if live:
                    live_csid = live.get('claude_session_id', '')
                    if live_csid:
                        e['claude_session_id'] = live_csid
    return entries

@bp.route('/api/project/<project_id>/transcript/<claude_session_id>')
def get_project_transcript(project_id, claude_session_id):
    """Return parsed transcript for read-only display in the Runs panel viewer."""
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    f = _find_transcript_file(p.get('project_path', ''), claude_session_id)
    if not f:
        return jsonify({'error': 'transcript not found'}), 404
    try:
        size = f.stat().st_size
    except OSError:
        size = 0
    messages = _parse_transcript_messages(f)
    return jsonify({
        'csid': claude_session_id,
        'size': size,
        'message_count': len(messages),
        'messages': messages,
    })


def _wf_agent_label(wf_dir, agent_id, maxlen=90):
    """Best-effort one-line label for a workflow subagent, from the first user
    message of its agent-<id>.jsonl. Prefers a distinguishing 'KEY: value' clause
    over the shared mission preamble. Returns '' on any failure (label is
    cosmetic — never let it break the scan)."""
    f = wf_dir / f'agent-{agent_id}.jsonl'
    try:
        with open(f, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                m = o.get('message', {})
                if m.get('role') != 'user':
                    continue
                c = m.get('content')
                if isinstance(c, list):
                    c = ' '.join(x.get('text', '') for x in c if isinstance(x, dict))
                c = ' '.join(str(c).split())
                if not c:
                    return ''
                up = c.upper()
                for kw in ('ANGLE', 'METHOD', 'FAMILY', 'YOUR TASK', 'MANDATE',
                           'ASSIGNMENT', 'LENS'):
                    idx = up.find(kw + ':')
                    if idx != -1:
                        return c[idx + len(kw) + 1:].strip()[:maxlen]
                return c[:maxlen]
    except Exception:
        return ''
    return ''


def _wf_render_ascii(wf):
    """Render a workflow's reconstructed state as a monospace ASCII tree
    (Ron picked this over Mermaid — cheaper for a <pre> block, no graph lib)."""
    n, nd = wf['started'], wf['done']
    nr = n - nd
    filled = round(20 * nd / n) if n else 0
    bar = '#' * filled + '-' * (20 - filled)
    status = f'{nd}/{n} done' + (f', {nr} running' if nr else ', complete')
    lines = [
        f'workflow  {wf["wf_id"]}',
        f'status    [{bar}] {status}',
        '│',
        f'├─ fan-out: {n} parallel agents',
    ]
    agents = wf['agents']
    for i, a in enumerate(agents):
        last = (i == len(agents) - 1)
        branch = '│  └─ ' if last else '│  ├─ '
        mark = '[x]' if a['done'] else '[~]'
        lines.append(f'{branch}{mark} {a["label"] or ("agent %02d" % (i + 1))}')
    return '\n'.join(lines)


def _scan_project_workflows(project_path, max_age_h=24):
    """Reconstruct CC Workflow progress from on-disk journals (read-only).

    Workflow per-agent progress never reaches MC's agent stream (only the launch
    tool_use + the final <task-notification> do), so we tail the subagent
    journals directly:
        ~/.claude/projects/<enc>/<csid>/subagents/workflows/<wf_id>/journal.jsonl
    an append-only log of {"type":"started"|"result","agentId":..} events.
    Returns [] on any failure — cosmetic feature, never load-bearing."""
    encoded = _encode_project_path(project_path or '')
    if not encoded:
        return []
    dirs = [CLAUDE_HOME / encoded]
    alt = encoded.replace('_', '-')
    if alt != encoded:
        dirs.append(CLAUDE_HOME / alt)
    cutoff = _time.time() - max_age_h * 3600
    out, seen = [], set()
    for d in dirs:
        try:
            if not d.exists():
                continue
            journals = list(d.glob('*/subagents/workflows/*/journal.jsonl'))
        except OSError:
            continue
        for j in journals:
            try:
                wf_id = j.parent.name
                if wf_id in seen:
                    continue
                mtime = j.stat().st_mtime
                if mtime < cutoff:
                    continue
                seen.add(wf_id)
                started, done = [], set()
                with open(j, encoding='utf-8') as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            o = json.loads(line)
                        except Exception:
                            continue
                        t, aid = o.get('type'), o.get('agentId')
                        if not aid:
                            continue
                        if t == 'started' and aid not in started:
                            started.append(aid)
                        elif t == 'result':
                            done.add(aid)
                agents = [{'id': aid, 'done': aid in done,
                           'label': _wf_agent_label(j.parent, aid)}
                          for aid in started]
                wf = {
                    'wf_id': wf_id,
                    'csid': j.parents[3].name if len(j.parents) > 3 else '',
                    'started': len(started),
                    'done': len(done),
                    'running': len(done) < len(started),
                    'mtime': mtime,
                    'agents': agents,
                }
                wf['ascii'] = _wf_render_ascii(wf)
                out.append(wf)
            except Exception as e:
                _log(f"[workflows] scan failed for {j}: {e}")
                continue
    out.sort(key=lambda w: w['mtime'], reverse=True)
    return out


@bp.route('/api/project/<project_id>/workflows')
def get_project_workflows(project_id):
    """Live CC Workflow progress trees, reconstructed from on-disk subagent
    journals (MC's agent stream never carries per-agent workflow progress).
    Read-only, best-effort — returns {workflows: []} on any miss."""
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    try:
        wfs = _scan_project_workflows(p.get('project_path', ''))
    except Exception as e:
        _log(f"[workflows] endpoint failed for {project_id}: {e}")
        wfs = []
    return jsonify({'workflows': wfs})


@bp.route('/api/project/<project_id>/session/<session_id>/reconstruct')
def reconstruct_dead_session(project_id, session_id):
    """Rebuild a finalized/purged MC session's chat buffer from its transcript.

    Read-only path for a tapped push whose session is dead (server bounced,
    not yet revived because the user hasn't sent a follow-up). The status
    endpoint only knows live in-memory sessions, so without this the deep-link
    lands on an empty tab and the user sees nothing — the very symptom this
    closes. Maps MC session_id → agent_log entry → claude_session_id → the
    on-disk .jsonl, rendered with the same _transcript_buffer_lines() the
    revive path uses so the read-only view is byte-identical to the live one.

    Returns 404 when the session isn't a known dead session with a resolvable
    transcript (caller then falls back to its existing behaviour).
    """
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    # A live session should go through /agent/status, not here.
    if session_id in agent_sessions:
        return jsonify({'error': 'session is live'}), 409
    entries = [e for e in _load_agent_log(project_id) if e.get('session_id') == session_id]
    if not entries:
        return jsonify({'error': 'session not in agent log'}), 404
    entries.sort(key=lambda e: e.get('ts', ''))
    entry = entries[-1]
    claude_sid = entry.get('claude_session_id')
    if claude_sid:
        user_label = state.CONFIG.get('user_name') or 'User'
        lines = _transcript_buffer_lines(p.get('project_path', ''), claude_sid,
                                         user_label, max_messages=300)
        if not lines:
            return jsonify({'error': 'transcript not found or empty'}), 404
        lines.append('[— read-only history; send a message to resume this session —]')
        return jsonify({
            'session_id': session_id,
            'claude_session_id': claude_sid,
            'task': entry.get('task', ''),
            'started_at': entry.get('timestamp', '') or entry.get('started_at', ''),
            'log_lines': lines,
            'read_only': True,
            'resumable': True,
        })
    # No claude_session_id — this provider (Gemini, etc.) keeps no transcript
    # store (MC-929). Mode A respawns a process per turn, so the agent log has
    # one row per turn for this session_id; each row's `summary` is that turn's
    # real output. That is genuinely everything left on disk once the session
    # has aged out of `agent_sessions` — the live per-turn `log_lines` buffer a
    # running session builds is in-memory only and does not survive a purge.
    # BE HONEST: this is not a transcript and there is no native resume, so
    # render what we have and say plainly that a reply starts a NEW session
    # rather than offering a Resume control that would silently do that anyway.
    provider = (entry.get('provider') or 'claude').lower()
    if provider == 'claude':
        # A Claude row with no csid at all (e.g. interrupted before Claude
        # assigned one) — there is genuinely nothing to render, Claude or
        # otherwise. Same contract as before this fix: no resolvable transcript.
        return jsonify({'error': 'no claude_session_id to resume from'}), 404
    if not entries[0].get('task') and not any(e.get('summary') for e in entries):
        return jsonify({'error': 'no history to reconstruct'}), 404
    user_label = state.CONFIG.get('user_name') or 'User'
    lines = []
    first_task = ' '.join(str(entries[0].get('task') or '').split())
    if first_task:
        lines.append(f"\n> {user_label}: {first_task}\n")
    for e in entries:
        summary = ' '.join(str(e.get('summary') or '').split())
        if summary:
            lines.append(summary)
    lines.append(f'[— read-only history from the run log; {provider} keeps no full '
                 f'transcript and cannot be resumed, so only each turn\'s final output '
                 f'is shown here. Sending a message starts a brand-new session —]')
    return jsonify({
        'session_id': session_id,
        'claude_session_id': '',
        'task': entries[0].get('task', ''),
        'started_at': entries[0].get('started_at', '') or entries[0].get('ts', ''),
        'log_lines': lines,
        'read_only': True,
        'resumable': False,
        'provider': provider,
    })


@bp.route('/api/project/<project_id>/transcript/<claude_session_id>/reconstruct')
def reconstruct_from_transcript(project_id, claude_session_id):
    """Rebuild a chat buffer directly from a claude_session_id transcript.

    For transcript-only conversations — interrupted sessions or fresh-start
    continuations that never got an MC agent_log entry (mc_session_id == '') —
    so /session/<id>/reconstruct can't resolve them. Renders with the SAME
    _transcript_buffer_lines() the tracked chats use, so the caller can open it
    in the identical read-only thread view instead of the resume-compose path
    (which renders blank on Android WebView). Returns 404 when the transcript
    can't be resolved (caller falls back to arming a resume).
    """
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    # Defense-in-depth against the STOPPED-vs-Working desync: this transcript may
    # belong to a session that is STILL LIVE (a resume reuses the same
    # claude_session_id under a fresh mc id). Handing back a read-only buffer for
    # it fabricated a "STOPPED / send a message to resume" tab while the agent
    # kept working. If the csid maps to a live session, tell the client to open
    # THAT tab instead. The sibling /session/<id>/reconstruct has the same guard.
    for _sid, _sess in agent_sessions.items():
        if _sess.get('claude_session_id') == claude_session_id:
            return jsonify({'error': 'session is live', 'session_id': _sid}), 409
    user_label = state.CONFIG.get('user_name') or 'User'
    lines = _transcript_buffer_lines(p.get('project_path', ''), claude_session_id,
                                     user_label, max_messages=300)
    if not lines:
        return jsonify({'error': 'transcript not found or empty'}), 404
    lines.append('[— read-only history; send a message to resume this session —]')
    return jsonify({
        'session_id': '',
        'claude_session_id': claude_session_id,
        'task': '',
        'started_at': '',
        'log_lines': lines,
        'read_only': True,
    })


@bp.route('/api/project/<project_id>/conversation/<claude_session_id>', methods=['DELETE'])
def delete_conversation(project_id, claude_session_id):
    """Delete a conversation the user chose to remove (mobile long-press).

    The conversation list is transcript-derived (it globs *.jsonl), so we rename
    the transcript to <name>.jsonl.deleted — that drops it from the list on every
    device while keeping it trivially recoverable (rename back) and NOT touching
    the agent_log or already-written memory. Refuses a currently-live session
    (409) so we never yank a transcript out from under a running process.
    """
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    for s in agent_sessions.values():
        if s.get('project_id') == project_id and s.get('claude_session_id') == claude_session_id:
            return jsonify({'error': 'conversation is live — stop it first'}), 409
    f = _find_transcript_file(p.get('project_path', ''), claude_session_id)
    if not f:
        return jsonify({'error': 'transcript not found'}), 404
    try:
        src = Path(f)
        dst = src.parent / (src.name + '.deleted')
        if dst.exists():
            dst.unlink()
        src.rename(dst)
    except Exception as e:
        _log(f"[delete-conversation] failed for {claude_session_id}: {e}", flush=True)
        return jsonify({'error': 'delete failed'}), 500
    _log(f"[delete-conversation] {project_id} / {claude_session_id} → {dst.name}", flush=True)
    return jsonify({'ok': True, 'claude_session_id': claude_session_id})


@bp.route('/api/recent-runs')
def api_recent_runs():
    """Aggregate agent_log entries across all projects within a time window.

    Powers the "Recent" tab in the schedule banner dropdown — answers
    "what just ran across all my projects in the last N hours?"

    Query params:
      hours  window size in hours (default 2, max 168 = 1 week)
      limit  max rows to return (default 50, max 200)
    """
    try:
        hours = float(request.args.get('hours', 2))
    except Exception:
        hours = 2
    if hours <= 0:
        hours = 2
    if hours > 168:
        hours = 168
    try:
        limit = int(request.args.get('limit', 50))
    except Exception:
        limit = 50
    if limit < 1:
        limit = 50
    if limit > 200:
        limit = 200

    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Build project_id -> display name lookup once.
    proj_names = {}
    for p in load_projects():
        pid = p.get('id') or ''
        if pid:
            proj_names[pid] = p.get('name') or pid

    runs = []
    suffix = '_agent_log.json'
    for f in DATA_DIR.glob('*' + suffix):
        pid = f.name[:-len(suffix)]
        try:
            log = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        for entry in log:
            ts = entry.get('ts') or entry.get('started_at') or ''
            if not ts:
                continue
            try:
                entry_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except Exception:
                continue
            if entry_dt < cutoff_dt:
                continue
            e = dict(entry)
            e['project_id'] = pid
            e['project_name'] = proj_names.get(pid, pid)
            runs.append(e)

    runs.sort(key=lambda e: e.get('ts', ''), reverse=True)
    runs = runs[:limit]
    return jsonify({
        'runs': _enrich_run_entries(runs),
        'hours': hours,
        'count': len(runs),
    })


def _extract_msg_text_from_raw(raw):
    """Pull the human-readable text from one transcript JSONL line.

    Returns the user/assistant text content ('' for tool/meta/attachment lines).
    Mirrors the extraction in ClaudeRuntime.parse_transcript_file but works on a
    single raw line so the search scanner can parse only the lines it needs.
    """
    try:
        o = json.loads(raw)
    except Exception:
        return ''
    msg = o.get('message')
    if not isinstance(msg, dict):
        return ''
    content = msg.get('content', '')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [str(b.get('text', '')) for b in content
                 if isinstance(b, dict) and b.get('type') == 'text']
        return ' '.join(t.strip() for t in texts if t).strip()
    return ''


def _make_search_snippet(display_text, raw, query, window=90):
    """Build a context window around `query`. Prefer clean display text; fall
    back to a whitespace-collapsed window of the raw line when the hit was in
    text we don't normally surface (tool results / thinking)."""
    ql = query.lower()
    src = display_text if (display_text and ql in display_text.lower()) else None
    if src is None:
        # Fall back to the raw line, lightly de-noised.
        src = ' '.join(raw.split())
    low = src.lower()
    i = low.find(ql)
    if i < 0:
        return (display_text or src)[:window * 2].strip()
    start = max(0, i - window)
    end = min(len(src), i + len(query) + window)
    s = src[start:end].strip()
    if start > 0:
        s = '… ' + s
    if end < len(src):
        s = s + ' …'
    return s


def _search_project_transcripts(project, query, limit=50):
    """Full-text scan of a project's Claude .jsonl transcripts.

    Returns [{csid, label, snippet, matches, mtime, ts_relative}] sorted by
    recency. A fast raw-substring pass picks matching lines; only the first user
    line (for the label) and the first matching line (for the snippet) are
    JSON-parsed, so the whole project scans in ~scan-cost (benchmarked ~2s on a
    195 MB / 181-file project, sub-second elsewhere). Read-only, no locks.
    """
    pp = (project or {}).get('project_path', '')
    q = (query or '').strip()
    if not pp or len(q) < 2:
        return []
    ql = q.lower()
    encoded = _encode_project_path(pp)
    if not encoded:
        return []
    dirs = [CLAUDE_HOME / encoded]
    alt = encoded.replace('_', '-')
    if alt != encoded:
        dirs.append(CLAUDE_HOME / alt)

    seen = set()
    files = []
    for d in dirs:
        try:
            if not d.exists():
                continue
            for f in d.glob('*.jsonl'):
                if f.name in seen:
                    continue
                seen.add(f.name)
                try:
                    files.append((f, f.stat().st_mtime))
                except OSError:
                    continue
        except OSError:
            continue
    files.sort(key=lambda x: x[1], reverse=True)

    from datetime import datetime, timezone
    results = []
    for f, mtime in files:
        match_count = 0
        snippet = ''
        first_user = ''
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                for raw in fh:
                    if not first_user and '"type":"user"' in raw:
                        first_user = _extract_msg_text_from_raw(raw)[:200]
                    if ql in raw.lower():
                        match_count += 1
                        if not snippet:
                            snippet = _make_search_snippet(
                                _extract_msg_text_from_raw(raw), raw, q)
        except Exception:
            continue
        if not match_count:
            continue
        try:
            ts_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except Exception:
            ts_iso = ''
        results.append({
            'csid': f.stem,
            'label': ' '.join((first_user or '').split()) or '(no label)',
            'snippet': snippet,
            'matches': match_count,
            'mtime': mtime,
            'ts_relative': time_ago(ts_iso) if ts_iso else '',
        })
    results.sort(key=lambda r: r['mtime'], reverse=True)
    return results[:limit]


@bp.route('/api/project/<project_id>/search-chats')
def search_project_chats(project_id):
    """Search a project's prior conversations by transcript content.

    Query: ?q=<text>&limit=<N>. Returns {query, count, results:[...]}.
    """
    q = (request.args.get('q', '') or '').strip()
    try:
        limit = int(request.args.get('limit', 50))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 100))
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    if len(q) < 2:
        return jsonify({'query': q, 'count': 0, 'results': []})
    results = _search_project_transcripts(p, q, limit=limit)
    return jsonify({'query': q, 'count': len(results), 'results': results})


@bp.route('/api/search/global')
def search_global():
    """Cross-project transcript search — spec §1 Layer-1 "🔍 Search".

    Aggregates the existing per-project _search_project_transcripts over every
    project and tags each hit with its project id/name so the client can
    deep-link. Read-only, best-effort; caps per-project + total to stay snappy.
    """
    q = (request.args.get('q', '') or '').strip()
    try:
        limit = int(request.args.get('limit', 40))
    except Exception:
        limit = 40
    limit = max(1, min(limit, 100))
    if len(q) < 2:
        return jsonify({'query': q, 'count': 0, 'results': []})
    out = []
    for p in load_projects():
        if not p.get('project_path'):
            continue
        try:
            hits = _search_project_transcripts(p, q, limit=8)  # cap per project
        except Exception:
            continue
        pid = p.get('id') or ''
        pname = p.get('name') or pid
        for r in hits:
            r2 = dict(r)
            r2['project_id'] = pid
            r2['project'] = pname
            out.append(r2)
    out.sort(key=lambda r: r.get('mtime', 0), reverse=True)
    return jsonify({'query': q, 'count': len(out), 'results': out[:limit]})


def _is_auth_probe_transcript(c: dict) -> bool:
    """True for a transcript that is an `claude -p ok --max-turns 1` auth probe.

    Until 2026-08-06 the probe ran with no `cwd=`, so it inherited the server's
    working directory — the Clayrune checkout, which is itself a project — and
    left a real transcript in that project's chat history every time it fired.
    `_auth_probe_cwd()` stops new ones; this hides the ones already on disk
    (and any left by an older build, on every install).

    Deliberately narrow: exactly one user turn whose entire text is "ok". A
    genuine one-word "ok" chat is still shown if MC dispatched it — the caller
    keeps any transcript with a live session or an agent-log row, so this can
    only ever drop a transcript MC has no record of asking for.
    """
    if (c.get('turns') or 0) > 1:
        return False
    return (str(c.get('first_user') or '').strip().lower() == 'ok'
            and str(c.get('last_user') or '').strip().lower() in ('ok', ''))


def _conversation_character_display(log_entry, project):
    """{agent_name, display_name, avatar, scope} for a past chat's persona.

    RE-RESOLVED from disk rather than served out of the log row: the row is a
    snapshot of the persona as it was at spawn, so a re-faced or renamed
    persona would show its old face forever on every chat it ever ran. The
    stored row is the fallback for a persona that has since been deleted —
    which still names who the chat was with, and is the one case where the
    snapshot is the only truth left.
    """
    ch = (log_entry or {}).get('character') or {}
    if not (isinstance(ch, dict) and ch.get('name')):
        return None
    scope = (ch.get('scope') or 'global').strip().lower()
    try:
        from mc import characters as _chars
        rec = _chars.read_character(
            scope, ch['name'],
            project_path=((project or {}).get('project_path') if scope == 'project' else None))
    except Exception as e:
        _log(f"[conversations] persona lookup failed for {ch.get('name')!r}: {e}")
        rec = None
    src = rec or ch
    return {
        'name': ch['name'],
        'scope': scope,
        'display_name': src.get('display_name') or ch['name'],
        'agent_name': src.get('agent_name') or '',
        'avatar': src.get('avatar') or '',
        'deleted': rec is None,
    }


def _non_claude_conversation_rows(project_id, p, limit):
    """Agent-log rows for providers that leave no Claude transcript (MC-929).

    `GeminiRuntime.transcript_path()` (and every other non-Claude runtime)
    returns None by design — no transcript store — and a non-Claude turn never
    populates `claude_session_id` (MC-922). So those chats have no membership
    in `_recent_claude_transcripts()` at all, no matter how many of them run.
    The agent log is the only record of them, keyed on MC's OWN session_id.

    Mode A (every non-Claude runtime today) respawns one process per turn, and
    `_log_agent_completion` writes one agent-log row per process exit — so a
    multi-turn chat leaves several rows sharing one `session_id`, each row's
    `summary` being that turn's real output. Grouped and sorted by `ts`, that
    is the most complete history actually on disk; there is no fuller
    transcript to fall back to; the per-session `log_lines` buffer that WOULD
    have the verbatim exchange lives only in memory and is gone once the
    session leaves `agent_sessions` (restart / 24h purge). Callers must not
    claim more fidelity than this — see `reconstruct_dead_session`.
    """
    groups = {}
    for e in _load_agent_log(project_id):
        if e.get('claude_session_id'):
            continue  # has a real transcript — the Claude path already owns it
        provider = (e.get('provider') or 'claude').lower()
        if provider == 'claude':
            continue  # e.g. an in-progress row before Claude assigned a csid
        sid = e.get('session_id', '')
        if not sid or e.get('hivemind_ws_id'):
            continue  # hivemind worker turns aren't a chat the rail shows
        groups.setdefault(sid, []).append(e)

    from datetime import datetime
    rows = []
    for sid, entries in groups.items():
        entries.sort(key=lambda e: e.get('ts', ''))
        latest, first = entries[-1], entries[0]
        live = agent_sessions.get(sid)
        if live and live.get('project_id') != project_id:
            live = None
        status = live.get('status', 'unknown') if live else latest.get('status', 'completed')
        last_user = ' '.join(str(latest.get('summary') or latest.get('task') or '').split())
        first_user = ' '.join(str(first.get('task') or '').split())
        ts = latest.get('ts', '')
        try:
            mtime = datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
        except Exception:
            mtime = 0
        rows.append({
            'claude_session_id': '',
            'mc_session_id': sid,
            'status': status,
            'label': last_user or first_user or '(empty)',
            'first_user': first_user,
            'last_user': last_user,
            'turns': len(entries),
            'size': 0,
            'mtime': mtime,
            'ts': ts,
            'ts_relative': time_ago(ts) if ts else '',
            'live': bool(live),
            'waiting_for_question': bool(live.get('waiting_for_question')) if live else False,
            'waiting_for_plan_approval': bool(live.get('waiting_for_plan_approval')) if live else False,
            'trigger_type': latest.get('trigger_type') or '',
            'source': latest.get('source') or '',
            'steward': False,
            'steward_objective': '',
            'character': _conversation_character_display(latest, p),
            # Provider + resumability, so the UI never offers a Resume control
            # that silently starts a fresh session. Mode A has no native `-r`;
            # the best honest offer is a read-only history (see
            # reconstruct_dead_session), not a resume.
            'provider': provider,
            'resumable': False,
            'resume_mode': 'readonly',
        })
    rows.sort(key=lambda r: r['mtime'], reverse=True)
    return rows[:limit]


@bp.route('/api/project/<project_id>/conversations')
def get_project_conversations(project_id):
    """Return recent conversations for a project — Claude transcripts UNION
    agent-log rows for every other provider (MC-929).

    Claude transcripts (`_recent_claude_transcripts`) stay the primary source:
    they survive server reboots and capture interrupted / mid-flight sessions
    that never reached the completion log. Non-Claude runtimes leave no
    transcript at all (see `_non_claude_conversation_rows`), so without a
    second source those chats could never be listed, let alone reopened.
    Label defaults to the user's LAST message.
    """
    try:
        limit = int(request.args.get('limit', 10))
    except Exception:
        limit = 10
    limit = max(1, min(limit, 50))

    p = load_project(project_id)
    if not p:
        return jsonify([])
    project_path = p.get('project_path', '')
    convos = _recent_claude_transcripts(project_path, limit=limit)

    live_by_csid = {}
    for s in agent_sessions.values():
        if s.get('project_id') != project_id:
            continue
        csid = s.get('claude_session_id', '')
        if csid:
            live_by_csid[csid] = {
                'status': s.get('status', 'unknown'),
                'session_id': s.get('session_id', ''),
                'task': s.get('task', ''),
                'waiting_for_question': bool(s.get('waiting_for_question')),
                'waiting_for_plan_approval': bool(s.get('waiting_for_plan_approval')),
            }

    log_by_csid = {}
    for e in _load_agent_log(project_id):
        csid = e.get('claude_session_id', '')
        if csid and csid not in log_by_csid:
            log_by_csid[csid] = e

    # Drop auth-probe leftovers — but only ones MC has no record of asking for
    # (no live session, and no agent-log row that wasn't itself synthesized by
    # the transcript backfill). A real chat MC dispatched always has one of
    # those, so this can never hide the user's own conversation.
    convos = [c for c in convos
              if not (_is_auth_probe_transcript(c)
                      and c['session_id'] not in live_by_csid
                      and (log_by_csid.get(c['session_id']) or {}).get('synthesized', True))]

    from datetime import datetime, timezone
    out = []
    for c in convos:
        sid = c['session_id']
        live = live_by_csid.get(sid)
        log_entry = log_by_csid.get(sid, {})
        if live:
            status = live['status']
            mc_session_id = live.get('session_id', '')
        elif log_entry:
            status = log_entry.get('status', 'completed')
            mc_session_id = log_entry.get('session_id', '')
        else:
            status = 'interrupted' if c['turns'] > 0 else 'empty'
            mc_session_id = ''

        label = c['last_user'] or c['first_user'] or '(empty)'
        label = ' '.join(label.split())

        # A steward-cycle session's turn-1 message is the build_cycle_task prompt,
        # which always starts with the [Steward cycle] marker. It's dispatched with
        # trigger_type='schedule' → the conversation-tab's user-initiated filter
        # would hide it. Tag it so the list can make an exception + label it, and
        # so the Automation steward card can deep-link straight into its thread.
        is_steward = str(c.get('first_user') or '').lstrip().startswith('[Steward cycle]')

        try:
            ts_iso = datetime.fromtimestamp(c['mtime'], tz=timezone.utc).isoformat()
        except Exception:
            ts_iso = ''
        out.append({
            'claude_session_id': sid,
            'mc_session_id': mc_session_id,
            'status': status,
            'label': label,
            'first_user': c['first_user'],
            'last_user': c['last_user'],
            'turns': c['turns'],
            'size': c['size'],
            'mtime': c['mtime'],
            'ts': ts_iso,
            'ts_relative': time_ago(ts_iso) if ts_iso else '',
            'live': bool(live),
            # Awaiting-input state, so a list REBUILT after a page reload (before
            # the /agent/status poll repopulates agentStatusCache) still knows the
            # card needs the user — otherwise a waiting chat looks plain-idle and
            # loses its "Waiting for you" badge + top-of-list bubbling on refresh.
            'waiting_for_question': bool(live.get('waiting_for_question')) if live else False,
            'waiting_for_plan_approval': bool(live.get('waiting_for_plan_approval')) if live else False,
            # trigger_type + source let the mobile list show ONLY user-initiated
            # chats and route agent/scheduled runs to the Agent Log side flow.
            # Empty (transcript-only / manual, no 'agent' source) = user-initiated.
            'trigger_type': (log_entry.get('trigger_type') or '') if log_entry else '',
            'source': (log_entry.get('source') or '') if log_entry else '',
            'steward': is_steward,
            'steward_objective': (p.get('steward_objective') or '') if is_steward else '',
            # WHO the chat was with. The log entry has carried this all along;
            # nothing emitted it, so the transcript-derived lists (mobile
            # Layer 2, reconstructed past chats) had no way to draw a face and
            # fell back to a generic bubble on every row.
            'character': _conversation_character_display(log_entry, p),
            # Claude transcripts are Claude by construction; the CLI's own `-r`
            # is a real resume. Kept alongside the non-Claude rows' provider/
            # resumable/resume_mode fields below (union, not two shapes).
            'provider': 'claude',
            'resumable': True,
            'resume_mode': 'live',
        })

    # UNION: agent-log rows for every provider that leaves no transcript.
    # Dedup is structural, not a post-hoc filter — _non_claude_conversation_rows
    # only emits rows whose entries have NO claude_session_id, and every Claude
    # row above always carries one (its `sid` IS the transcript's csid), so the
    # two sets can't overlap on the same conversation.
    out.extend(_non_claude_conversation_rows(project_id, p, limit))
    out.sort(key=lambda r: r['mtime'], reverse=True)
    return jsonify(out[:limit])


# ── PLAN tab load cache ───────────────────────────────────────────────────────
# /api/project/<id>/plans re-read every plan file (read + regex for the title)
# and re-parsed the whole agent log on every tab open — both are I/O that grows
# with history, which is the slow tab load. Cache both by mtime. Payload assembly
# is cheap in-memory CPU, so it's rebuilt each call → titles are always fresh (no
# whole-payload staleness) while the I/O is skipped when nothing changed.
# Best-effort: a cache error falls back to the direct log read, and
# _plan_title_for is self-guarding. CPython dict ops are atomic under the GIL, so
# these module-level caches need no lock for the threaded server.
_plan_title_cache: dict = {}      # plan_file path -> (mtime, title)
_plans_log_cache: dict = {}       # project_id  -> (log_mtime, parsed_log)


def _plan_title_for(p: Path) -> str:
    """First '# ' heading of a plan file, memoized by (path, mtime)."""
    import re
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except Exception:
        return p.stem
    cached = _plan_title_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        content = p.read_text(encoding='utf-8')
        m = re.match(r'^#\s+(.+)', content, re.MULTILINE)
        title = m.group(1).strip() if m else p.stem
    except Exception:
        title = p.stem
    _plan_title_cache[key] = (mtime, title)
    return title


def _plans_cached_log(project_id):
    """_load_agent_log, but skip the JSON parse when the log file's mtime is unchanged."""
    filepath = DATA_DIR / f'{project_id}_agent_log.json'
    try:
        mtime = filepath.stat().st_mtime
    except Exception:
        _plans_log_cache.pop(project_id, None)
        return []
    cached = _plans_log_cache.get(project_id)
    if cached and cached[0] == mtime:
        return cached[1]
    log = _load_agent_log(project_id)
    _plans_log_cache[project_id] = (mtime, log)
    return log


@bp.route('/api/project/<project_id>/plans')
def get_project_plans(project_id):
    """Return all plan files for this project (live sessions + agent log).

    Titles and the agent-log parse are cached by mtime (_plan_title_for /
    _plans_cached_log) so repeated tab opens don't re-read unchanged files.
    """
    def _build(log):
        plans = []
        seen = set()

        def _add(pf, task, ts, session_id):
            if not pf or pf in seen:
                return
            p = Path(pf)
            if not p.is_file():
                return
            seen.add(pf)
            plans.append({
                'plan_file': pf,
                'filename': p.name,
                'title': _plan_title_for(p),
                'task': task or '',
                'ts': ts or '',
                'ts_relative': time_ago(ts),
                'session_id': session_id or '',
            })

        # Live sessions first (may not be in the log yet). Every plan the
        # session registered, not just the most recent — a session that wrote
        # three of them used to surface one and silently drop the rest.
        for sid, s in agent_sessions.items():
            if s.get('project_id') != project_id:
                continue
            for pf in _session_plan_files(s):
                _add(pf, s.get('task', ''), s.get('started_at', ''),
                     s.get('session_id', ''))
        for entry in log:
            for pf in _session_plan_files(entry):
                _add(pf, entry.get('task', ''), entry.get('ts', ''),
                     entry.get('session_id', ''))
        return plans

    try:
        log = _plans_cached_log(project_id)
    except Exception:
        log = _load_agent_log(project_id)
    return jsonify(_build(log))


# ── DOCUMENTS tab (MC-939) ───────────────────────────────────────────────────
# The tab used to be PLANS-only, scoped to the one directory _is_plan_path
# checks (~/.claude/plans/). That meant a spec an agent wrote to docs/ under
# the project itself — a real, committed deliverable — could never appear.
#
# "Document" = derived, not declared: no API for an agent to register a
# deliverable, no new instruction for it to follow (that discipline rots
# within a week — see the brief). Instead this merges two sources that already
# exist:
#   - plans:       ~/.claude/plans/*.md            (unchanged, _is_plan_path)
#   - agent docs:  Write/Edit tool calls on *.md targets scraped from this
#                   project's own Claude transcripts, via
#                   ClaudeRuntime.list_written_markdown() — cached per
#                   transcript file by (mtime, size), so a tab open only pays
#                   for transcripts that changed since the last call.
#
# 'doc' rows are read-only (no delete) — see _document_allowed_roots for why
# the read path is scoped this way, and the docstring on get_project_documents
# for why delete is plan-only.

_DOC_EXTS = {'.md', '.markdown'}


def _document_allowed_roots(project_path: str = '') -> list:
    """Allowlist for the Documents tab's read route.

    Same realpath + prefix-containment pattern as /api/serve-image and
    /api/serve-file (CLAUDE.md: broaden the guard the way the project already
    broadens file access safely, don't just loosen it). Unlike serve-file's
    cross-project reach, this is scoped to the ONE project asking (its own
    working dir), plus data/uploads/ and the plans dir every project shares.
    A drive-root project (C:\\, /, C:\\Users) is excluded, same guard as
    serve-image/serve-file, so this can't become a whole-disk read.
    """
    roots = [str(UPLOADS_DIR), str(_PLANS_DIR)]
    if project_path:
        try:
            real_pp = os.path.realpath(project_path)
            if len(Path(real_pp).parts) >= 3:
                roots.append(real_pp)
        except Exception:
            pass
    return roots


@bp.route('/api/document-file')
def read_document_file():
    """Read a Documents-tab file by path — a plan OR an agent-written doc.

    Broadens /api/plan-file's plans-only gate: resolves under the requesting
    project's own root, data/uploads/, or ~/.claude/plans/ instead of only the
    latter. A path outside all of those, a non-markdown extension, or a
    secret-looking file (mirrors /api/serve-file's denylist) is refused.
    """
    raw = (request.args.get('path') or '').strip()
    if not raw:
        return jsonify({'error': 'path required'}), 400
    try:
        real = os.path.realpath(raw)
    except Exception:
        return jsonify({'error': 'invalid path'}), 400
    if os.path.splitext(real)[1].lower() not in _DOC_EXTS:
        return jsonify({'error': 'unsupported file type'}), 415
    if _is_secret_file(real):
        return jsonify({'error': 'access denied'}), 403
    project_path = ''
    project_id = (request.args.get('project_id') or '').strip()
    if project_id:
        proj = load_project(project_id)
        if proj:
            project_path = proj.get('project_path', '')
    rn = os.path.normcase(real)
    ok = False
    for a in _document_allowed_roots(project_path):
        try:
            ar = os.path.normcase(os.path.realpath(a))
        except Exception:
            continue
        if rn == ar or rn.startswith(ar + os.sep):
            ok = True
            break
    if not ok:
        return jsonify({'error': 'access denied'}), 403
    p = Path(real)
    if not p.is_file():
        return jsonify({'error': 'file not found'}), 404
    try:
        content = p.read_text(encoding='utf-8')
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'path': str(p), 'filename': p.name, 'content': content})


@bp.route('/api/project/<project_id>/documents')
def get_project_documents(project_id):
    """Unified Documents tab: plans merged with markdown this project's agents
    actually wrote, per session (see the module docstring above).

    kind: 'plan' | 'doc'. Every row carries 'deletable': plans may be deleted
    (same semantics as /api/plans/delete — they're MC/CLI-scratch, never a
    tracked repo file); 'doc' rows never are. A doc row is, by construction,
    a file the transcript scan found inside the project's own working tree —
    exactly the files a project may have under git — and MC has no cheap way
    to tell "untracked scratch markdown under docs/" apart from "the spec you
    just committed" without shelling out to git per row per request. Rather
    than get that wrong in the destructive direction, delete is off for the
    whole kind. (Reading, unlike deleting, is safe either way.)
    """
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    project_path = p.get('project_path', '')

    docs = []
    seen_paths = set()

    try:
        log = _plans_cached_log(project_id)
    except Exception:
        log = _load_agent_log(project_id)

    def _add_plan(pf, task, ts, session_id):
        if not pf or pf in seen_paths:
            return
        path = Path(pf)
        if not path.is_file():
            return
        seen_paths.add(pf)
        docs.append({
            'path': pf,
            'filename': path.name,
            'title': _plan_title_for(path),
            'kind': 'plan',
            'location': 'plans',
            'task': task or '',
            'ts': ts or '',
            'ts_relative': time_ago(ts),
            'session_id': session_id or '',
            'deletable': True,
        })

    for sid, s in agent_sessions.items():
        if s.get('project_id') != project_id:
            continue
        for pf in _session_plan_files(s):
            _add_plan(pf, s.get('task', ''), s.get('started_at', ''),
                      s.get('session_id', ''))
    for entry in log:
        for pf in _session_plan_files(entry):
            _add_plan(pf, entry.get('task', ''), entry.get('ts', ''),
                      entry.get('session_id', ''))

    if project_path:
        try:
            hits = _agent_runtime.get_runtime('claude').list_written_markdown(project_path)  # pyright: ignore[reportAttributeAccessIssue]
        except Exception:
            hits = []
        proot = ''
        try:
            _proot = os.path.realpath(project_path)
            if len(Path(_proot).parts) >= 3:  # drive-root guard, matches serve-image
                proot = _proot
        except Exception:
            proot = ''
        if proot:
            ar = os.path.normcase(proot)
            for h in hits:
                fp = h.get('path') or ''
                if not fp or fp in seen_paths or _is_plan_path(fp):
                    continue  # plans already covered above
                try:
                    real = os.path.realpath(fp)
                except Exception:
                    continue
                rn = os.path.normcase(real)
                if not (rn == ar or rn.startswith(ar + os.sep)):
                    continue  # outside this project's own tree
                p_obj = Path(real)
                if not p_obj.is_file():
                    continue  # written then deleted/moved — nothing to show
                seen_paths.add(fp)
                try:
                    rel = str(p_obj.relative_to(proot))
                except Exception:
                    rel = p_obj.name
                ts = h.get('ts', '')
                docs.append({
                    'path': real,
                    'filename': p_obj.name,
                    'title': _plan_title_for(p_obj),
                    'kind': 'doc',
                    'location': rel,
                    'task': '',
                    'ts': ts,
                    'ts_relative': time_ago(ts),
                    'session_id': h.get('session_id', ''),
                    'deletable': False,
                })

    docs.sort(key=lambda d: d.get('ts') or '', reverse=True)
    return jsonify(docs)


# ── Usage / token tracking ──────────────────────────────────────────────────

@bp.route('/api/usage')
def api_usage():
    """Aggregate token usage across all agent log files and running sessions.

    Optional query param ?since=<ISO timestamp> to filter entries after a cutoff.

    Response shape (multi-provider aware):
      {
        by_provider: {
          claude: {input_tokens, output_tokens, total_tokens, cost_usd, num_turns, sessions},
          gemini: {...},
          ...
        },
        total: {input_tokens, output_tokens, total_tokens, cost_usd, sessions},
        # Legacy flat fields kept for backward compat:
        input_tokens, output_tokens, total_tokens, cost_usd, total_sessions
      }

    Providers that don't emit cost (emits_cost=False) will have cost_usd=null in
    their by_provider bucket to distinguish "zero cost" from "not applicable".
    """
    since = request.args.get('since', '')

    def _empty_bucket():
        return {'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0.0, 'sessions': 0}

    by_provider: dict = {}

    def _accumulate(bucket_key, usage_dict, cost, _has_cost):
        b = by_provider.setdefault(bucket_key, _empty_bucket())
        b['input_tokens'] += usage_dict.get('input_tokens', 0)
        b['output_tokens'] += usage_dict.get('output_tokens', 0)
        if _has_cost:
            b['cost_usd'] = (b['cost_usd'] or 0.0) + (cost or 0.0)
        else:
            # Mark as None only if we've never seen a cost for this provider.
            if b.get('cost_usd') == 0.0 and cost is None:
                b['cost_usd'] = None
        b['sessions'] += 1

    def _provider_emits_cost(provider_name):
        try:
            return _agent_runtime.get_runtime(provider_name).capabilities().emits_cost
        except Exception:
            return True  # conservative: don't discard data on unknown provider

    # Deduplicate by claude_session_id: Scribe checkpoints write multiple log
    # entries for the same session (each with cumulative totals). Keep only the
    # latest entry per csid to avoid counting the same session multiple times.
    _seen_csids: dict = {}   # csid -> (ts, entry, provider)
    _no_csid: list = []      # entries without a csid (counted individually)

    for f in DATA_DIR.glob('*_agent_log.json'):
        try:
            log = json.loads(f.read_text(encoding='utf-8'))
            for entry in log:
                if since and entry.get('ts', '') < since:
                    continue
                csid = entry.get('claude_session_id') or ''
                p = (entry.get('provider') or 'claude').lower()
                ts = entry.get('ts', '')
                if csid:
                    prev = _seen_csids.get(csid)
                    if prev is None or ts >= prev[0]:
                        _seen_csids[csid] = (ts, entry, p)
                else:
                    _no_csid.append((entry, p))
        except Exception:
            continue

    def _entry_usage(entry):
        """Return a usage-shaped dict for an agent_log entry.

        Prefers top-level input_tokens/output_tokens (set by
        _extract_transcript_telemetry — full-session JSONL sum) when non-zero.
        Falls back to the nested 'usage' dict (accumulated across turns for new
        entries; last-turn only for pre-fix legacy entries).
        """
        top_in  = int(entry.get('input_tokens') or 0)
        top_out = int(entry.get('output_tokens') or 0)
        if top_in or top_out:
            return {'input_tokens': top_in, 'output_tokens': top_out}
        nested = entry.get('usage') or {}
        return {'input_tokens': int(nested.get('input_tokens') or 0),
                'output_tokens': int(nested.get('output_tokens') or 0)}

    for ts, entry, p in _seen_csids.values():
        _accumulate(p, _entry_usage(entry), entry.get('cost_usd'), _provider_emits_cost(p))
    for entry, p in _no_csid:
        _accumulate(p, _entry_usage(entry), entry.get('cost_usd'), _provider_emits_cost(p))

    # Include running sessions that haven't been logged yet
    for s in agent_sessions.values():
        if since and s.get('started_at', '') < since:
            continue
        csid = s.get('claude_session_id') or ''
        if csid and csid in _seen_csids:
            continue  # already counted from log
        p = (s.get('provider') or 'claude').lower()
        usage = s.get('usage') or {}
        cost = s.get('cost_usd')
        _accumulate(p, usage, cost, _provider_emits_cost(p))

    # Build per-provider output with total_tokens computed
    by_provider_out = {}
    for pname, b in by_provider.items():
        by_provider_out[pname] = {
            'input_tokens': b['input_tokens'],
            'output_tokens': b['output_tokens'],
            'total_tokens': b['input_tokens'] + b['output_tokens'],
            'cost_usd': round(b['cost_usd'], 4) if b['cost_usd'] is not None else None,
            'sessions': b['sessions'],
        }

    # Grand total (sum across all providers; cost_usd sums only providers that emit it)
    total_input = sum(b['input_tokens'] for b in by_provider.values())
    total_output = sum(b['output_tokens'] for b in by_provider.values())
    total_cost = sum(
        (b['cost_usd'] or 0.0) for b in by_provider.values()
        if b['cost_usd'] is not None
    )
    total_sessions = sum(b['sessions'] for b in by_provider.values())

    return jsonify({
        'by_provider': by_provider_out,
        'total': {
            'input_tokens': total_input,
            'output_tokens': total_output,
            'total_tokens': total_input + total_output,
            'cost_usd': round(total_cost, 4),
            'sessions': total_sessions,
        },
        # Legacy flat fields — identical to current response for claude-only deployments.
        'input_tokens': total_input,
        'output_tokens': total_output,
        'total_tokens': total_input + total_output,
        'cost_usd': round(total_cost, 4),
        'total_sessions': total_sessions,
    })


# ── Session Guardian ─────────────────────────────────────────────────────────
# Replaces the old health monitor. Detects stuck sessions and auto-recovers
# them with exponential backoff, without discarding session context.

# _guardian_stop moved to mc/state.py (Phase 0).
GUARDIAN_CHECK_INTERVAL = 10
# Hung threshold: 10 minutes of *both* no stdout and no CPU progress.
# Claude can legitimately go silent for several minutes during long thinking,
# context loads, or tool calls — so we require CPU idleness as confirmation.
GUARDIAN_HUNG_TIMEOUT = 600
# Per-provider override. gemini-cli has an upstream non-interactive tool-call
# hang (google-gemini/gemini-cli#16567) where --yolo doesn't bypass the
# tool-confirmation-request and the JS event loop parks on the unresolved
# promise — no heartbeat, no exit, until killed. Gemini itself rarely needs
# more than ~60s for legitimate thinking, so a 90s watchdog catches the hang
# fast without false-positiving real work.
GUARDIAN_HUNG_TIMEOUT_BY_PROVIDER = {'gemini': 90}
GUARDIAN_STUCK_FLAG_TIMEOUT = 120
GUARDIAN_MAX_RECOVERIES = 3
GUARDIAN_BACKOFF_BASE = 5


def _proc_is_cpu_idle(session, proc, now):
    """Return True if the process appears CPU-idle (i.e. truly hung, not thinking).

    Compares cpu_times() across calls. If cpu time hasn't advanced by at least
    0.5s since the previous sample, treat as idle. First sample always returns
    False (not enough data — give the process the benefit of the doubt).
    """
    try:
        import psutil
    except ImportError:
        # Without psutil we cannot distinguish "thinking" from "hung". The safe
        # default is to NEVER auto-kill on silence — return False so the State 2
        # guardian skips the kill. The user can install psutil to enable it, or
        # manually stop a truly hung agent. Dead-process detection (State 1)
        # still works without psutil.
        return False
    try:
        p = psutil.Process(proc.pid)
        cpu = p.cpu_times()
        cur_total = cpu.user + cpu.system
        # Walk children too — Claude CLI spawns subprocesses
        for child in p.children(recursive=True):
            try:
                cc = child.cpu_times()
                cur_total += cc.user + cc.system
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True  # process is gone — let the dead-process check handle it
    prev_total = session.get('_guardian_prev_cpu_total')
    prev_time = session.get('_guardian_prev_cpu_time', now)
    session['_guardian_prev_cpu_total'] = cur_total
    session['_guardian_prev_cpu_time'] = now
    if prev_total is None:
        return False  # first sample — wait for next tick before declaring idle
    delta_cpu = cur_total - prev_total
    delta_wall = max(0.001, now - prev_time)
    # If the process burned less than 0.5s of CPU per ~10s of wall time, call it idle
    return (delta_cpu / delta_wall) < 0.05


def _guardian_should_recover(session):
    if session.get('circuit_breaker_tripped'):
        return False
    attempts = session.get('recovery_attempts', 0)
    if attempts >= GUARDIAN_MAX_RECOVERIES:
        session['circuit_breaker_tripped'] = True
        session['guardian_state'] = 'needs_attention'
        session['log_lines'].append(
            f'[Guardian: recovery exhausted after {attempts} attempts. '
            f'Use "Try Again" or "Start Fresh".]')
        return False
    last = session.get('last_recovery_time', 0)
    backoff = GUARDIAN_BACKOFF_BASE * (2 ** attempts)
    if _time.time() - last < backoff:
        return False
    return True


def _guardian_attempt_recovery(session):
    if not _guardian_should_recover(session):
        if session.get('guardian_state') == 'recovering':
            session['guardian_state'] = 'needs_attention'
        return
    message = session.get('pending_recovery_message')
    if not message:
        if session.get('guardian_state') == 'recovering':
            session['guardian_state'] = None
        return

    session['recovery_attempts'] = session.get('recovery_attempts', 0) + 1
    session['last_recovery_time'] = _time.time()
    session['guardian_state'] = 'recovering'
    session['log_lines'].append(
        f'[Guardian: recovery attempt {session["recovery_attempts"]}/{GUARDIAN_MAX_RECOVERIES}]')

    proc = session.get('proc')
    if proc:
        _kill_proc_background(proc)
        _time.sleep(2)

    _auto_dispatch_followup(session, message)

    with get_manager(session['project_id']).lock:
        if session['status'] == 'running':
            session['guardian_state'] = None
            session['pending_recovery_message'] = None
        else:
            if session.get('recovery_attempts', 0) >= GUARDIAN_MAX_RECOVERIES:
                session['circuit_breaker_tripped'] = True
                session['guardian_state'] = 'needs_attention'
            else:
                session['guardian_state'] = None


def _session_guardian_loop():
    """Removed — per-project guardians (ProjectAgentManager.ensure_guardian) now
    own session checking. This stub is kept only so _start_session_guardian()
    doesn't break older callers."""
    return


def _should_evict_idle_session(session, now, enabled, idle_minutes):
    """Pure predicate for guardian idle-eviction (kept separate so it's unit-
    testable without spawning real processes).

    True iff this is a warm Mode B session with a live process that has been
    `idle` (turn finished, waiting on the user) for longer than `idle_minutes`.
    Evicting it kills the claude.exe + MCP-server fleet to free resources; the
    next user message respawns it with `-r <csid>` (full context). Only `idle`
    sessions qualify — a `running` session is mid-work and never evicted."""
    if not enabled or not idle_minutes or idle_minutes <= 0:
        return False
    if session.get('status') != 'idle' or session.get('mode') != 'B':
        return False
    if session.get('evicted'):
        return False
    # Don't evict a session with queued work or one waiting on the user — those
    # carry pending state the next turn needs; let them resolve first.
    if (session.get('pending_followups') or session.get('_dispatching_followup')
            or session.get('waiting_for_question')
            or session.get('waiting_for_plan_approval')):
        return False
    proc = session.get('proc')
    if proc is None or proc.poll() is not None:
        return False
    return (now - session.get('last_output_time', now)) > idle_minutes * 60


def _guardian_check_session(sid, session, now):
    status = session['status']
    proc = session.get('proc')
    mode = session.get('mode', 'A')
    last_output = session.get('last_output_time', now)
    last_change = session.get('last_status_change_time', now)

    if session.get('guardian_state') == 'recovering':
        return

    # State 7: stuck 'running' with no/dead process (Popen failure)
    if status == 'running' and now - last_change > 15:
        proc_dead = proc is None or proc.poll() is not None
        if proc_dead:
            _log(f"[guardian] Session {sid[:8]}: stuck running, process dead/missing")
            with get_manager(session['project_id']).lock:
                session['status'] = 'error'
                session['last_status_change_time'] = now
                if mode == 'B':
                    session['process_alive'] = False
                session['log_lines'].append(
                    '[Guardian: process dead but status was running — recovered]')
            if session.get('pending_recovery_message'):
                _guardian_attempt_recovery(session)
            return

    # State 1: dead process, stale status (running/idle)
    if status in ('running', 'idle') and proc and now - last_change > 2:
        if proc.poll() is not None or not _pid_is_alive(proc.pid):
            # Safety net: if the session is waiting for user input (question /
            # plan approval), the process was killed intentionally by the reader
            # thread as part of that flow. Don't mark it 'error' — the follow-up
            # (user's answer) will respawn it.
            if (session.get('waiting_for_question') or session.get('waiting_for_plan_approval')
                    or session.get('evicted')):
                return
            old_status = status
            _log(f"[guardian] Session {sid[:8]}: PID {proc.pid} dead, was {old_status}")
            with get_manager(session['project_id']).lock:
                if mode == 'B':
                    session['process_alive'] = False
                if session['status'] in ('running', 'idle'):
                    session['status'] = 'error'
                    session['last_status_change_time'] = now
                    session['log_lines'].append(
                        f'[Guardian: process {proc.pid} found dead]')
            if session.get('pending_recovery_message'):
                _guardian_attempt_recovery(session)
            return

    # State 2: hung process (alive, no output for GUARDIAN_HUNG_TIMEOUT seconds AND no CPU progress)
    if status == 'running' and proc and proc.poll() is None:
        silent_secs = now - last_output
        provider = (session.get('provider') or 'claude').lower()
        hung_timeout = GUARDIAN_HUNG_TIMEOUT_BY_PROVIDER.get(provider, GUARDIAN_HUNG_TIMEOUT)
        if silent_secs > hung_timeout and _proc_is_cpu_idle(session, proc, now):
            _log(f"[guardian] Session {sid[:8]} ({provider}): no output for {silent_secs:.0f}s, killing")
            with get_manager(session['project_id']).lock:
                # Gemini's tool-call hang has a known upstream cause — surface
                # it instead of the generic message so the user knows retrying
                # often works and it isn't an MC fault.
                if provider == 'gemini':
                    session['log_lines'].append(
                        f'[Guardian: gemini stuck for {silent_secs:.0f}s — '
                        f'likely upstream tool-call hang '
                        f'(google-gemini/gemini-cli#16567). Retrying usually works.]')
                else:
                    session['log_lines'].append(
                        f'[Guardian: no output for {silent_secs:.0f}s — killing hung process]')
                session['guardian_state'] = 'needs_attention'
            # Snapshot pid; release lock before kill (process-tree walk can be slow on Windows)
            _kill_proc_background(proc)
            return

    # State 8: idle-session eviction. Reclaim a warm Mode B fleet (claude.exe +
    # its MCP-server tree) after long inactivity; the next user message respawns
    # it via the followup path with `-r <csid>`, so context is preserved. The
    # `evicted` flag makes State 1 skip the now-dead-proc session instead of
    # flagging it 'error'; it is cleared on respawn. Default OFF.
    if _should_evict_idle_session(session, now,
                                  state.CONFIG.get('idle_eviction_enabled', False),
                                  state.CONFIG.get('idle_eviction_minutes', 60)):
        proc_to_kill = None
        with get_manager(session['project_id']).lock:
            # Re-check under lock — status/proc may have changed since the snapshot.
            if _should_evict_idle_session(session, now,
                                          state.CONFIG.get('idle_eviction_enabled', False),
                                          state.CONFIG.get('idle_eviction_minutes', 60)):
                idle_min = (now - session.get('last_output_time', now)) / 60
                session['evicted'] = True
                session['process_alive'] = False
                session['last_status_change_time'] = now
                session['log_lines'].append(
                    f'[Guardian: idle {idle_min:.0f} min — process evicted to free '
                    f'resources; next message resumes with full context]')
                _unregister_process(proc.pid)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                proc_to_kill = proc
        if proc_to_kill is not None:
            _log(f"[guardian] Session {sid[:8]}: evicted after idle timeout")
            _kill_proc_background(proc_to_kill)
        return

    proj_lock = get_manager(session['project_id']).lock

    # State 3: stuck gate flags (approval/question)
    if session.get('waiting_for_plan_approval') and now - last_change > GUARDIAN_STUCK_FLAG_TIMEOUT:
        last_sse = session.get('_last_sse_poll_time', 0)
        if now - last_sse > 60:
            with proj_lock:
                session['log_lines'].append(
                    '[Guardian: plan approval may have been missed — re-check session]')
                session['guardian_state'] = 'needs_attention'

    if session.get('waiting_for_question') and now - last_change > GUARDIAN_STUCK_FLAG_TIMEOUT:
        last_sse = session.get('_last_sse_poll_time', 0)
        if now - last_sse > 60:
            with proj_lock:
                session['log_lines'].append(
                    '[Guardian: question may have been missed — re-check session]')
                session['guardian_state'] = 'needs_attention'

    # State 5: stuck _dispatching_followup flag
    if session.get('_dispatching_followup') and status != 'running':
        if now - last_change > 30:
            with proj_lock:
                session.pop('_dispatching_followup', None)
                session['log_lines'].append(
                    '[Guardian: cleared stuck dispatching flag]')

    # State 4: stuck pending_followups queue
    pending = session.get('pending_followups', [])
    if pending and status != 'running' and not session.get('_dispatching_followup'):
        if now - last_change > 30:
            with proj_lock:
                msg = pending.pop(0)
                session['log_lines'].append(
                    '[Guardian: dispatching stuck follow-up]')
            _auto_dispatch_followup(session, msg)

    # State 6: error session with pending recovery message — retry or trip breaker
    if status == 'error' and session.get('pending_recovery_message'):
        attempts = session.get('recovery_attempts', 0)
        last_recovery = session.get('last_recovery_time', 0)
        if attempts >= 2 and now - last_recovery < 60:
            if not session.get('circuit_breaker_tripped'):
                with proj_lock:
                    session['circuit_breaker_tripped'] = True
                    session['guardian_state'] = 'needs_attention'
                    session['log_lines'].append(
                        f'[Guardian: {attempts} rapid failures detected — '
                        f'auto-recovery disabled]')
        elif now - last_change > 10:
            _guardian_attempt_recovery(session)


def _start_session_guardian():
    """No-op: per-project guardians spawn lazily on first dispatch via
    ProjectAgentManager.ensure_guardian(). Kept for callers in startup code."""
    return None

