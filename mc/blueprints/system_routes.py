"""System + processes endpoints — blueprint 1.6 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py (two source regions): 4 /api/processes +
11 /api/system routes (plan table said 3+11 — processes/cleanup grew), plus
the system-status passive cache, restart machinery, and the update-check
daemon loop. _LAST_SYSTEM_STATUS / _LAST_RESTART_TIME (the last two rebound
globals) now live in mc/state.py with every reference rewritten to state.*.

Phase 2 addition (deliberate NEW route, invariant 209 -> 210):
GET /api/system/loops exposes mc.obs heartbeats.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time as _time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Blueprint, jsonify, request

import mc.agent_runtime as _agent_runtime
from mc import allowance_state as _allowance_state
from mc import obs, process_sweep, state
from mc.blueprints.terminal_routes import launch_pty_session
from mc.blueprints.workflow_routes import _is_agent_caller
from mc import slash_commands as slash_cmds
from mc.atomic_json import write_json_atomic
from mc.core import _atomic_write_text, _log, now_iso, path_is_within, time_ago
from mc.state import (
    _UPDATE_CHECK_BOOT_DELAY_S,
    _hivemind_lock,
    _hivemind_sessions,
    terminal_lock,
    terminal_sessions,
    _UPDATE_CHECK_CACHE,
    _UPDATE_CHECK_INTERVAL_S,
    _UPDATE_CHECK_LOCK,
    agent_sessions,
    process_tracker_lock,
    tracked_processes,
)

bp = Blueprint('system_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
load_projects: Callable[..., Any] = None  # type: ignore[assignment]
DATA_DIR: Path = None  # type: ignore[assignment]
_DATA_ROOT: Path = None  # type: ignore[assignment]
_APP_DIR: Path = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None
_backfill_token_telemetry: Callable[..., Any] = None  # type: ignore[assignment]
_is_cf_tunneled_request: Callable[..., Any] = None  # type: ignore[assignment]
_kill_pid: Callable[..., Any] = None  # type: ignore[assignment]
_kill_proc_background: Callable[..., Any] = None  # type: ignore[assignment]
_pid_is_alive: Callable[..., Any] = None  # type: ignore[assignment]
_resolve_claude: Callable[..., Any] = None  # type: ignore[assignment]
_stop_session: Callable[..., Any] = None  # type: ignore[assignment]
get_manager: Callable[..., Any] = None  # type: ignore[assignment]
get_manager_for_session: Callable[..., Any] = None  # type: ignore[assignment]


def wire(*, load_project_fn, load_projects_fn, data_dir, data_root, app_dir,
         popen_flags, startupinfo, backfill_token_telemetry_fn, is_cf_tunneled_request_fn, kill_pid_fn, kill_proc_background_fn, pid_is_alive_fn, resolve_claude_fn, stop_session_fn, get_manager_fn, get_manager_for_session_fn):
    """Late-bind projects-family accessors (1.11) + path constants."""
    global load_project, load_projects, DATA_DIR, _DATA_ROOT, _APP_DIR
    load_project = load_project_fn
    load_projects = load_projects_fn
    DATA_DIR = data_dir
    _DATA_ROOT = data_root
    _APP_DIR = app_dir
    global _POPEN_FLAGS, _STARTUPINFO, RESTART_LOG_PATH, SYSTEM_STATUS_PATH, PROCESS_SWEEP_LOG_PATH
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    RESTART_LOG_PATH = data_root / 'data' / 'restart_log.json'
    SYSTEM_STATUS_PATH = data_root / 'data' / 'system_status.json'
    PROCESS_SWEEP_LOG_PATH = data_root / 'data' / 'process_sweep_log.json'
    global _backfill_token_telemetry
    _backfill_token_telemetry = backfill_token_telemetry_fn
    global _is_cf_tunneled_request
    _is_cf_tunneled_request = is_cf_tunneled_request_fn
    global _kill_pid
    _kill_pid = kill_pid_fn
    global _kill_proc_background
    _kill_proc_background = kill_proc_background_fn
    global _pid_is_alive
    _pid_is_alive = pid_is_alive_fn
    global _resolve_claude
    _resolve_claude = resolve_claude_fn
    global _stop_session
    _stop_session = stop_session_fn
    global get_manager
    get_manager = get_manager_fn
    global get_manager_for_session
    get_manager_for_session = get_manager_for_session_fn


@bp.route('/api/system/loops')
def system_loops():
    """Background-loop heartbeat ages (mc/obs.py, Phase 2). A loop missing
    from this map after boot, or with a runaway age, is silently dead."""
    return jsonify(obs.snapshot())


@bp.route('/api/slash-commands')
def slash_commands():
    """Built-in CLI slash commands the composer's `/` autocomplete may offer.

    Only `supportsNonInteractive` commands are returned: MC always spawns the
    CLI with `--print`, so anything else would be suggested and then silently
    do nothing. Read from the user's OWN binary (the list is version-specific)
    and cached against its fingerprint — see mc/slash_commands.py.

    `?all=1`   include interactive-only commands (diagnostics).
    `?refresh=1` force a re-scan, ignoring the cache.
    """
    try:
        binary = _agent_runtime.get_runtime('claude').resolve_binary()
    except Exception:
        binary = None
    inv = slash_cmds.load_inventory(
        Path(str(binary)) if binary else None,
        _DATA_ROOT / 'data' / 'slash_commands.json',
        force=request.args.get('refresh') in ('1', 'true'),
        log=_log,
    )
    want_all = request.args.get('all') in ('1', 'true')
    cmds = inv.get('commands', []) if want_all else slash_cmds.headless_commands(inv)
    return jsonify({
        'commands': cmds,
        'source': inv.get('source'),
        'extracted_at': inv.get('extracted_at'),
        'total_known': len(inv.get('commands', [])),
    })


# ── Process Tracker endpoints ─────────────────────────────────────────────────

@bp.route('/api/processes')
def list_processes():
    """Return all tracked processes with live status."""
    result = []
    with process_tracker_lock:
        snapshot = list(tracked_processes.items())
    for pid, entry in snapshot:
        proc = entry.get('proc')
        if proc is not None:
            alive = proc.poll() is None
            exit_code = proc.poll()
        else:
            # External process — check via OS
            alive = _pid_is_alive(entry['pid'])
            exit_code = None
        # Cross-reference agent/housekeeping entries to the matching session so the UI
        # can show running/idle/error/stopped distinct from raw process liveness.
        agent_status = None
        entry_type = entry.get('type', '')
        sid = entry.get('session_id', '')
        if sid and entry_type in ('agent', 'housekeeping'):
            session = agent_sessions.get(sid)
            if session:
                agent_status = session.get('status')
        elif sid and entry_type == 'terminal':
            term = terminal_sessions.get(sid)
            if term:
                agent_status = term.get('status')
        result.append({
            'pid': entry['pid'],
            'name': entry['name'],
            'type': entry_type,
            'session_id': sid,
            'project_id': entry['project_id'],
            'project_name': entry['project_name'],
            'command_preview': entry['command_preview'],
            'started_at': entry['started_at'],
            'alive': alive,
            'exit_code': exit_code,
            'agent_status': agent_status,
        })
    result.sort(key=lambda x: (0 if x['alive'] else 1, x.get('started_at', '')))
    return jsonify(result)


@bp.route('/api/processes/<int:pid>/kill', methods=['POST'])
def kill_tracked_process(pid):
    """Kill a specific tracked process by PID."""
    with process_tracker_lock:
        entry = tracked_processes.get(pid)
        if not entry:
            return jsonify({'error': 'process not found in tracker'}), 404
        proc = entry.get('proc')
        if proc:
            if proc.poll() is not None:
                tracked_processes.pop(pid, None)
                return jsonify({'ok': True, 'already_dead': True})
            _kill_pid(pid, tree=True)
            try:
                proc.kill()
            except Exception as e:
                return jsonify({'error': f'kill failed: {e}'}), 500
        else:
            # External process — kill via OS
            if not _kill_pid(pid, tree=True):
                tracked_processes.pop(pid, None)
                return jsonify({'ok': True, 'already_dead': True})
        tracked_processes.pop(pid, None)
        session_id = entry.get('session_id', '')
        entry_type = entry.get('type', '')

    # Update corresponding session status (outside tracker lock)
    if entry_type in ('agent', 'housekeeping'):
        mgr = get_manager_for_session(session_id)
        if mgr is not None:
            with mgr.lock:
                session = agent_sessions.get(session_id)
                if session and session['status'] in ('running', 'idle'):
                    session['status'] = 'stopped'
                    session['last_status_change_time'] = _time.time()
                    session['log_lines'].append('[Process killed via Process Manager]')
                if session and session.get('mode') == 'B':
                    session['process_alive'] = False
    elif entry_type == 'terminal':
        with terminal_lock:
            session = terminal_sessions.get(session_id)
            if session and session['status'] == 'running':
                session['status'] = 'stopped'
                session['output_lines'].append('\r\n[Process killed via Process Manager]')

    return jsonify({'ok': True})


@bp.route('/api/processes/register', methods=['POST'])
def register_external_process():
    """Register an externally-spawned process (e.g. from an agent)."""
    data = request.get_json() or {}
    pid = data.get('pid')
    name = data.get('name', 'External process')
    project_id = data.get('project_id', '')
    command_preview = data.get('command', '')
    if not pid or not isinstance(pid, int):
        return jsonify({'error': 'pid (integer) required'}), 400
    # Verify PID is actually running (warn but still register — process may have exited quickly)
    alive = _pid_is_alive(pid)
    if not alive:
        _log(f"[process-register] Warning: PID {pid} not detected as alive, registering anyway")
    project_name = project_id
    try:
        p = load_project(project_id)
        if p:
            project_name = p.get('name', project_id)
    except Exception:
        pass
    with process_tracker_lock:
        tracked_processes[pid] = {
            'pid': pid,
            'name': name,
            'type': 'external',
            'session_id': '',
            'project_id': project_id,
            'project_name': project_name,
            'command_preview': (command_preview or '')[:80],
            'started_at': now_iso(),
            'proc': None,
        }
    return jsonify({'ok': True, 'pid': pid})


@bp.route('/api/processes/cleanup', methods=['POST'])
def cleanup_processes():
    """Kill all orphaned processes (alive but session gone or completed)."""
    killed = 0
    with process_tracker_lock:
        to_kill = []
        for pid, entry in tracked_processes.items():
            proc = entry.get('proc')
            if not proc or proc.poll() is not None:
                continue
            sid = entry.get('session_id', '')
            orphaned = False
            if entry['type'] in ('agent', 'housekeeping'):
                session = agent_sessions.get(sid)
                if not session or session['status'] not in ('running', 'idle'):
                    orphaned = True
            elif entry['type'] == 'terminal':
                session = terminal_sessions.get(sid)
                if not session or session['status'] != 'running':
                    orphaned = True
            if orphaned:
                to_kill.append((pid, proc))
        for pid, proc in to_kill:
            try:
                proc.kill()
                killed += 1
            except Exception:
                pass
            tracked_processes.pop(pid, None)
    return jsonify({'ok': True, 'killed': killed})


# ── Orphan CLI process sweep (MC-991 Phase 2, mc/process_sweep.py) ──────────
# Server-side auto-kill of agent CLI processes (codex/claude/gemini/opencode/
# qwen) whose whole ancestor chain has died and that Clayrune has no other
# record of — see position_whetherclayrunemayautokillorphanedagentcliproces.md
# for the criteria and why Ron approved it. `run_process_sweep` is the single
# call seam: the periodic loop below uses it, and tools/cli-version-check.py's
# --apply path calls it (via POST, the model-upgrades/run precedent) before
# attempting an install, so both share one config toggle and one kill path.
PROCESS_SWEEP_LOG_PATH: Path = None  # type: ignore[assignment]  # wired


def _append_process_sweep_log(entry):
    """Append one sweep report to data/process_sweep_log.json, capped at the
    last 200 entries (mirrors `_append_restart_log`). Surfaced here (durable,
    inspectable) in addition to clayrune.log (`_log`, best-effort/rotates)."""
    try:
        log = []
        if PROCESS_SWEEP_LOG_PATH.exists():
            try:
                log = json.loads(PROCESS_SWEEP_LOG_PATH.read_text(encoding='utf-8'))
            except Exception:
                log = []
        log.append(entry)
        if len(log) > 200:
            log = log[-200:]
        PROCESS_SWEEP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(PROCESS_SWEEP_LOG_PATH, log, indent=2)
    except Exception as e:
        _log(f"[process-sweep] failed to append log: {e}")


def _process_sweep_root_pids():
    """Every PID the sweep must never touch: Clayrune's live in-memory
    registry (`tracked_processes` — covers every running agent/housekeeping/
    terminal/external process, including ones just re-registered by session
    revival after a restart) UNION whatever's still recorded in the on-disk
    child-PID ledger (`data/mc_child_pids.json` — covers the window right
    after a restart, before revival has re-populated `tracked_processes`, and
    covers tools/cli-version-check.py's own out-of-process callers of the
    sweep endpoint, which have no access to the live registry at all)."""
    with process_tracker_lock:
        pids = {pid for pid in tracked_processes.keys() if isinstance(pid, int)}
    ledger_path = _DATA_ROOT / 'data' / 'mc_child_pids.json'
    try:
        if ledger_path.exists():
            data = json.loads(ledger_path.read_text(encoding='utf-8'))
            for entry in (data.get('children') or []):
                pid = entry.get('pid')
                if isinstance(pid, int):
                    pids.add(pid)
    except Exception as e:
        _log(f"[process-sweep] could not read child-PID ledger: {e}")
    return pids


def run_process_sweep(dry_run=None):
    """Run the sweep once, honoring `process_sweep_enabled` (default True) —
    OFF means neither the periodic loop nor a manual/pre-update call kills
    anything, full stop; there is deliberately no way to bypass the toggle
    per-call. `dry_run` overrides `process_sweep_dry_run` (default False) when
    given explicitly (the /api/system/process-sweep POST body)."""
    if not bool(state.CONFIG.get('process_sweep_enabled', True)):
        return {'ok': True, 'skipped': True, 'reason': 'process_sweep_enabled is false'}
    effective_dry_run = bool(state.CONFIG.get('process_sweep_dry_run', False)) if dry_run is None else bool(dry_run)

    def _log_kill(entry):
        _log(f"[process-sweep] {entry.get('action')}: pid={entry.get('pid')} "
             f"cli={entry.get('cli_name')} age_hours={entry.get('age_hours')} "
             f"exe={entry.get('exe')} start_epoch={entry.get('start_epoch')}")

    report = process_sweep.run_sweep(
        dry_run=effective_dry_run,
        root_pids=_process_sweep_root_pids(),
        kill_fn=_kill_pid,
        log_fn=_log_kill,
    )
    if report.get('killed') or report.get('error'):
        _append_process_sweep_log({**report, 'ts': now_iso()})
    return report


def _process_sweep_loop():
    """Daemon thread: run the sweep every _PROCESS_SWEEP_INTERVAL_S seconds.
    First run fires after _PROCESS_SWEEP_BOOT_DELAY_S so a fresh restart's
    session revival has time to re-populate tracked_processes/the ledger
    before any orphan judgment is made."""
    _time.sleep(state._PROCESS_SWEEP_BOOT_DELAY_S)
    while True:
        obs.heartbeat('process-sweep')
        try:
            run_process_sweep()
        except Exception as e:
            _log(f"[process-sweep] loop error: {e}", flush=True)
        _time.sleep(state._PROCESS_SWEEP_INTERVAL_S)


@bp.route('/api/system/process-sweep', methods=['POST'])
def system_process_sweep():
    """Manual/pre-update trigger — tools/cli-version-check.py's --apply path
    calls this before attempting an install (same precedent as
    /api/model-upgrades/run: the caller asks the server to run its own gate,
    it does not decide anything itself). Body: {"dry_run": bool} optional."""
    body = request.get_json(silent=True) or {}
    dry_run = body.get('dry_run')
    report = run_process_sweep(dry_run=dry_run)
    return jsonify(report)


# ── Server restart (remote-triggered, graceful) ──────────────────────────────
# Lets the user restart the Mission Control Flask process from the dashboard
# (including over the clayrune.io tunnel from a phone or remote PC) so they can
# pick up new code/config without needing physical access. Two endpoints:
#   GET  /api/system/restart/status — list active sessions/hiveminds that would
#                                      be killed by a restart (UI shows a warning).
#   POST /api/system/restart        — re-check empty state server-side, then
#                                      stop everything cleanly and re-exec.
# Auth model: same as the rest of the app. Localhost is unauthenticated by
# design (your own machine); tunneled requests have already passed CF Access OTP.
RESTART_LOG_PATH: Path = None  # type: ignore[assignment]  # wired
# state._LAST_RESTART_TIME lives in mc/state.py (1.6).
_RESTART_RATE_LIMIT_SECONDS = 30
# Set once at module load. Changes every time the Python process is replaced,
# so any dashboard polling /api/system/heartbeat can detect a restart by
# comparing this against its cached value.
_SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat()
_SERVER_STARTED_MONOTONIC = _time.time()

# ── System status passive cache ─────────────────────────────────────────────
# Every `claude` session emits a `system/init` message and a `rate_limit_event`
# message at startup. Both contain account-global info: model, CLI version,
# auth source, rate-limit window state, connected MCP servers, etc. — exactly
# the same info CC's own `/status` slash command surfaces. We tap the two
# main stream readers (Mode A + Mode B) so every dispatched agent session
# refreshes this cache for free. Frontend reads it via /api/system/status.
SYSTEM_STATUS_PATH: Path = None  # type: ignore[assignment]  # wired
# state._LAST_SYSTEM_STATUS lives in mc/state.py (1.6).


def _load_system_status_from_disk():
    """Populate `state._LAST_SYSTEM_STATUS` on startup so the panel shows something
    immediately even if no agent has run since the restart."""
    try:
        if SYSTEM_STATUS_PATH.exists():
            state._LAST_SYSTEM_STATUS = json.loads(SYSTEM_STATUS_PATH.read_text(encoding='utf-8'))
    except Exception:
        state._LAST_SYSTEM_STATUS = {}


def _save_system_status_to_disk():
    try:
        SYSTEM_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SYSTEM_STATUS_PATH.write_text(
            json.dumps(state._LAST_SYSTEM_STATUS, indent=2), encoding='utf-8'
        )
    except Exception:
        pass  # Non-fatal — cache stays in memory.


def _capture_system_init(msg):
    """Extract account-global fields from a claude stream-json message and
    refresh the in-memory + on-disk system-status cache.

    Hooked into both `_read_agent_stream` (Mode A) and `_read_agent_stream_b`
    (Mode B) right after `msg = json.loads(line)`. Returns silently for any
    message type we don't care about.

    Handles two message types:
      - `system/init` — model, version, auth, MCP servers, tool/skill/plugin counts.
      - `rate_limit_event` — 5-hour or 1-hour rate-limit window state.
    """
    try:
        mtype = msg.get('type', '')
        now_iso = datetime.now(timezone.utc).isoformat()
        if mtype == 'system' and msg.get('subtype') == 'init':
            mcp = msg.get('mcp_servers') or []
            # `memory_paths` is a dict (`{"auto": "..."}`) — collapse to a list
            # of paths for display so the panel doesn't need to know the shape.
            mp_raw = msg.get('memory_paths') or {}
            if isinstance(mp_raw, dict):
                mp_list = [p for p in mp_raw.values() if isinstance(p, str) and p]
            elif isinstance(mp_raw, list):
                mp_list = [p for p in mp_raw if isinstance(p, str) and p]
            else:
                mp_list = []
            init_data = {
                'model': msg.get('model') or '',
                'claude_code_version': msg.get('claude_code_version') or '',
                'apiKeySource': msg.get('apiKeySource') or '',
                'permissionMode': msg.get('permissionMode') or '',
                'mcp_servers': [
                    {'name': m.get('name', ''), 'status': m.get('status', 'unknown')}
                    for m in mcp if isinstance(m, dict)
                ],
                'tools_count': len(msg.get('tools') or []),
                'skills_count': len(msg.get('skills') or []),
                'agents_count': len(msg.get('agents') or []),
                'plugins_count': len(msg.get('plugins') or []),
                'slash_commands_count': len(msg.get('slash_commands') or []),
                'output_style': msg.get('output_style') or '',
                'fast_mode_state': msg.get('fast_mode_state') or '',
                'analytics_disabled': bool(msg.get('analytics_disabled')),
                'cwd': msg.get('cwd') or '',
                'memory_paths': mp_list,
            }
            state._LAST_SYSTEM_STATUS.update(init_data)
            state._LAST_SYSTEM_STATUS['init_captured_at'] = now_iso
            state._LAST_SYSTEM_STATUS['captured_at'] = now_iso
            _save_system_status_to_disk()
        elif mtype == 'rate_limit_event':
            info = msg.get('rate_limit_info') or {}
            if isinstance(info, dict):
                state._LAST_SYSTEM_STATUS['rate_limit_info'] = {
                    'status': info.get('status', ''),
                    'resetsAt': info.get('resetsAt'),
                    'rateLimitType': info.get('rateLimitType', ''),
                    'overageStatus': info.get('overageStatus', ''),
                    'overageResetsAt': info.get('overageResetsAt'),
                    'isUsingOverage': bool(info.get('isUsingOverage')),
                }
                state._LAST_SYSTEM_STATUS['rate_limit_captured_at'] = now_iso
                state._LAST_SYSTEM_STATUS['captured_at'] = now_iso
                _save_system_status_to_disk()
                # VENDOR_AGNOSTIC_PROGRAM.md §4: a normalized
                # ALLOWANCE_EXHAUSTED record, kept separately from the raw
                # cache above (that one is a passive mirror of the last event;
                # this one is a claim a dispatch call site refuses on).
                if info.get('status') == 'allowed':
                    _allowance_state.clear_exhaustion('claude')
                else:
                    _allowance_state.observe('claude', msg)
    except Exception:
        pass  # Capture is best-effort; never break the reader on a parse error.


_load_system_status_from_disk()


@bp.route('/api/system/heartbeat')
def system_heartbeat():
    """Tiny endpoint dashboards poll to detect that the server has restarted.

    Cheap to call (no DB / disk read). The frontend caches `started_at` from
    its first response and reloads the page if a later response shows a
    different value — that means the Python process has been replaced (e.g.
    by /api/system/restart) and any in-memory session state the dashboard
    was tracking is stale.
    """
    return jsonify({
        'started_at': _SERVER_STARTED_AT,
        'pid': os.getpid(),
        'uptime_seconds': int(_time.time() - _SERVER_STARTED_MONOTONIC),
    })


def _build_system_status_payload():
    """Shape the cached system-status dict for /api/system/status responses.

    Returns the cache as-is plus a `cache_age_seconds` field computed from
    `captured_at`, so the frontend can render "stale" without re-parsing the
    timestamp. Returns an empty `{captured_at: null}` shape if the cache is
    still empty (no agent has run since first install / cache file deletion).
    """
    payload = dict(state._LAST_SYSTEM_STATUS)
    cap = payload.get('captured_at')
    age = None
    if cap:
        try:
            dt = datetime.fromisoformat(cap.replace('Z', '+00:00'))
            age = int((datetime.now(timezone.utc) - dt).total_seconds())
        except Exception:
            age = None
    payload['cache_age_seconds'] = age
    return payload


@bp.route('/api/system/status', methods=['GET'])
def system_status_get():
    """Return the cached system status (model, version, rate limit, MCP, etc.).

    Read-only and cheap — just serializes the in-memory dict. Cache is
    populated by both stream readers as agents run; falls back to disk after
    a restart via `_load_system_status_from_disk()` at module load.
    """
    return jsonify(_build_system_status_payload())


def _model_tokens_from_nested_usage(e):
    """Fallback for entries with no transcript-derived `model_tokens` — the
    case for every provider whose adapter doesn't write a Claude-shaped JSONL
    transcript (Codex: 119/119 retained rows have empty model_tokens despite
    61 having positive nested `usage`, per USAGE_BREAKDOWN_SPEC.md's baseline
    audit). Vendor-agnostic: reads whatever `usage.input_tokens` /
    `usage.output_tokens` the adapter recorded. `cached_input_tokens` (Codex)
    is a subset of `input_tokens`, never added again. Returns {} when there's
    no positive evidence — a missing/zero usage dict stays excluded, not 0.
    """
    usage = e.get('usage')
    if not isinstance(usage, dict):
        return {}
    inp = int(usage.get('input_tokens') or 0)
    out = int(usage.get('output_tokens') or 0)
    if not inp and not out:
        return {}
    model = e.get('observed_model') or e.get('model') or 'unknown'
    return {model: inp + out}


def _mc_usage_from_agent_logs():
    """Aggregate token usage from MC's own agent_log files.

    Returns {'today': {model: tokens}, 'week': {...}, 'month': {...},
             'all_time': {model: tokens}, 'last_data_date': str}
    Reads all *_agent_log.json in DATA_DIR. Entries without model_tokens fall
    back to nested `usage` via `_model_tokens_from_nested_usage` (vendor-
    agnostic — covers Codex and any other non-Claude adapter); entries with
    neither are skipped (no evidence). Never raises.

    Deduplicates by claude_session_id: Scribe checkpoints write multiple entries
    for the same session (each with the cumulative token total from session start).
    We keep only the latest entry per csid to avoid counting the same tokens N times.
    Sessions without a csid are counted individually (legacy / non-CC providers).
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    try:
        week_cutoff  = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        month_cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    except Exception:
        week_cutoff = month_cutoff = today_str

    today_t, week_t, month_t, all_t = {}, {}, {}, {}
    last_data_date = ''

    try:
        # First pass: collect all entries across all log files, deduplicated by csid.
        # For each csid, keep only the latest entry (highest ts = most complete snapshot).
        # Entries without a csid are kept as-is (keyed by a unique fallback).
        best_by_csid: dict = {}  # csid -> entry dict
        _no_csid_counter = 0
        for log_path in DATA_DIR.glob('*_agent_log.json'):
            try:
                entries = json.loads(log_path.read_text(encoding='utf-8',
                                                        errors='replace'))
            except Exception:
                continue
            if not isinstance(entries, list):
                continue
            for e in entries:
                if not isinstance(e, dict):
                    continue
                mt = e.get('model_tokens')
                if not mt or not isinstance(mt, dict):
                    mt = _model_tokens_from_nested_usage(e)
                    if not mt:
                        continue
                ts = (e.get('ts') or '')[:10]
                if not ts:
                    continue
                csid = e.get('claude_session_id') or ''
                if csid:
                    prev = best_by_csid.get(csid)
                    if prev is None or ts >= (prev.get('ts') or '')[:10]:
                        best_by_csid[csid] = e
                else:
                    # No csid — count individually (non-CC provider or legacy entry)
                    _no_csid_counter += 1
                    best_by_csid[f'__no_csid_{_no_csid_counter}'] = e

        for e in best_by_csid.values():
            mt = e.get('model_tokens') or _model_tokens_from_nested_usage(e)
            ts = (e.get('ts') or '')[:10]
            if ts > last_data_date:
                last_data_date = ts
            for model, tok in mt.items():
                tok = int(tok or 0)
                if not tok:
                    continue
                all_t[model] = int(all_t.get(model, 0)) + tok
                if ts >= month_cutoff:
                    month_t[model] = int(month_t.get(model, 0)) + tok
                if ts >= week_cutoff:
                    week_t[model] = int(week_t.get(model, 0)) + tok
                if ts == today_str:
                    today_t[model] = int(today_t.get(model, 0)) + tok
    except Exception:
        pass

    return {
        'today': today_t,
        'week': week_t,
        'month': month_t,
        'all_time': all_t,
        'last_data_date': last_data_date,
    }


@bp.route('/api/system/usage/backfill', methods=['POST'])
def system_usage_backfill():
    """Trigger a one-shot telemetry backfill in the background.
    Populates model_tokens on existing agent_log entries from JSONL transcripts.
    """
    def _run():
        try:
            _backfill_token_telemetry()
        except Exception as e:
            _log(f"[telemetry-backfill] endpoint trigger failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True, 'msg': 'backfill started in background'})


# Authoritative subscription usage windows (5h / 7d / per-model %) come from
# Claude Code's own OAuth token hitting Anthropic's undocumented usage endpoint
# — the same call the CLI `/usage` command makes. No client-readable file or
# `--print` flag exposes these percentages, so this is the only programmatic
# source. Best-effort: any failure (missing/expired token, network, 401)
# returns None and the UI falls back to the header-derived rate-limit window.
# Cached briefly to avoid hammering the endpoint; the User-Agent MUST start with
# `claude-code/` or Anthropic routes the request to an aggressively throttled
# bucket (persistent 429s).
_OAUTH_USAGE_TTL = 60.0  # seconds
_oauth_usage_cache: dict = {'ts': 0.0, 'data': None}


def _fetch_oauth_usage_limits():
    """Return the parsed OAuth usage windows dict, or None on any failure.

    Shape: {five_hour, seven_day, seven_day_opus, seven_day_sonnet, extra_usage}
    where each window is {utilization: 0-100, resets_at: ISO8601} (per-model
    blocks are null when unused).
    """
    now = _time.time()
    cached = _oauth_usage_cache.get('data')
    if cached is not None and (now - _oauth_usage_cache.get('ts', 0.0)) < _OAUTH_USAGE_TTL:
        return cached
    try:
        cred_path = Path.home() / '.claude' / '.credentials.json'
        creds = json.loads(cred_path.read_text(encoding='utf-8'))
        oauth = creds.get('claudeAiOauth') or {}
        token = oauth.get('accessToken')
        if not token:
            return None
        ver = state._LAST_SYSTEM_STATUS.get('claude_code_version') or '2.0.0'
        req = urllib.request.Request(
            'https://api.anthropic.com/api/oauth/usage',
            headers={
                'Authorization': f'Bearer {token}',
                'anthropic-beta': 'oauth-2025-04-20',
                'User-Agent': f'claude-code/{ver}',
                'Content-Type': 'application/json',
            },
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, dict):
            _oauth_usage_cache['ts'] = now
            _oauth_usage_cache['data'] = data
            return data
        return None
    except Exception as e:
        _log(f"[system_usage] oauth usage fetch failed: {e}", flush=True)
        return None


# Codex weekly utilization — read from the CLI's OWN on-disk session record,
# not a network call (the OAuth-usage pattern above is Claude-only; Codex has
# no equivalent authenticated endpoint reachable without spending its own
# quota). Every Codex `token_count` event embeds the CLI's own live view of
# its rate limits, live-captured 2026-09-26 from real rollout files under
# ~/.codex/sessions:
#   "rate_limits":{"primary":{"used_percent":18.0,"window_minutes":300,
#     "resets_at":1789557088},"secondary":{"used_percent":41.0,
#     "window_minutes":10080,"resets_at":1789854189}, ...}
# `window_minutes` identifies the window (300 = 5h, 10080 = 7d = weekly) —
# which slot (primary/secondary) carries the weekly one depends on the
# account's plan (a `prolite` plan showed weekly-only as `primary`; a `plus`
# plan showed 5h as `primary` and weekly as `secondary`), so both slots are
# checked by window_minutes rather than assumed to be one or the other.
_CODEX_WEEKLY_WINDOW_MINUTES = 10080
_CODEX_USAGE_TTL = 60.0  # seconds — matches _OAUTH_USAGE_TTL's cadence
_codex_usage_cache: dict = {'ts': 0.0, 'data': None}
# Tail-read size: token_count lines are small (a few hundred bytes); 300KB
# comfortably covers many turns' worth even in a session with large tool
# outputs between them. Session files are append-only and can reach several
# MB, so the whole file is never read.
_CODEX_TAIL_BYTES = 300_000


def _fetch_codex_weekly_usage() -> Optional[dict]:
    """Return {'utilization': 0-100, 'resets_at': ISO8601, 'sampled_at':
    ISO8601} for Codex's weekly rate-limit window, or None if no rollout file
    yields a CURRENT one. Reads only the most-recently-modified rollout file
    (Codex's rate limit is account-wide, so any live session's own view of it
    is representative) and only its tail, scanned backwards for the latest
    `token_count` event.

    Freshness gate: if that event's own `resets_at` is missing or already in
    the past, the weekly window it describes has already reset — the CLI just
    hasn't run since, so there is no live reading, and showing the stale %
    as "now" would violate the real-numbers-only rule the same as inventing
    one. Omitted (returns None) rather than shown with a caveat, so the
    frontend needs no extra state beyond its existing "provider absent = no
    bar" contract. `sampled_at` (the rollout file's mtime) is carried on a
    FRESH reading too, so a caller can judge how old the underlying session
    is even when it's still within its window.
    """
    now = _time.time()
    cached = _codex_usage_cache.get('data')
    if cached is not None and (now - _codex_usage_cache.get('ts', 0.0)) < _CODEX_USAGE_TTL:
        return dict(cached) if cached else cached
    result = None
    try:
        files = _agent_runtime._codex_rollout_files()
        if files:
            latest = max(files, key=lambda f: f.stat().st_mtime)
            st = latest.stat()
            mtime = st.st_mtime
            size = st.st_size
            with open(latest, 'rb') as fh:
                if size > _CODEX_TAIL_BYTES:
                    fh.seek(size - _CODEX_TAIL_BYTES)
                chunk = fh.read()
            lines = chunk.decode('utf-8', errors='ignore').split('\n')
            for line in reversed(lines):
                if '"token_count"' not in line or '"rate_limits"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                info = (rec.get('payload') or {}) if isinstance(rec.get('payload'), dict) else rec
                rl = info.get('rate_limits') if isinstance(info, dict) else None
                if not isinstance(rl, dict):
                    continue
                for slot in ('primary', 'secondary'):
                    win = rl.get(slot)
                    if isinstance(win, dict) and win.get('window_minutes') == _CODEX_WEEKLY_WINDOW_MINUTES:
                        resets_at_raw = win.get('resets_at')
                        resets_epoch = None
                        if resets_at_raw is not None:
                            try:
                                resets_epoch = float(resets_at_raw)
                            except (TypeError, ValueError):
                                resets_epoch = None
                        if resets_epoch is not None and resets_epoch > now:
                            result = {
                                'utilization': win.get('used_percent'),
                                'resets_at': datetime.fromtimestamp(resets_epoch, tz=timezone.utc).isoformat(),
                                'sampled_at': datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                            }
                        # else: resets_at missing, unparseable, or already
                        # elapsed — stale reading, left as `result = None`
                        # (see docstring).
                        break
                if result is not None:
                    break
    except Exception as e:
        _log(f"[system_usage] codex weekly usage read failed: {e}", flush=True)
        result = None
    _codex_usage_cache['ts'] = now
    _codex_usage_cache['data'] = result
    return dict(result) if result else result


# MC-989 Part A — the usage POPUP wants more than the bottom strip's single
# weekly %: 5-hour window (when the record carries one), plan_type, and
# credits balance, plus how stale the reading is (Codex's number is a
# read of the CLI's own last turn, not a live fetch — it only updates when a
# Codex turn actually runs). A SIBLING of `_fetch_codex_weekly_usage` rather
# than an extension of it: that function's contract (used by the bottom
# strip + pinned by tests/test_codex_weekly_usage.py) is "weekly window or
# None", and a record with only a five-hour window but no weekly one must
# keep returning None there. This one scans independently and returns
# whatever detail the latest record actually carries, gated per-window (not
# all-or-nothing) by the same staleness rule as the weekly-only reader.
_CODEX_FIVE_HOUR_WINDOW_MINUTES = 300
_CODEX_DETAIL_TTL = 60.0  # seconds — matches the weekly reader's cadence
_codex_detail_cache: dict = {'ts': 0.0, 'data': None}


def _fetch_codex_usage_detail() -> Optional[dict]:
    """Return {'weekly': {...}|None, 'five_hour': {...}|None, 'plan_type':
    str|None, 'credits': {...}|None, 'sampled_at': ISO8601|None} from the
    latest Codex rollout file's own `rate_limits` block, or None if no
    rollout file yields anything at all. Each window dict is
    {utilization, resets_at} and is independently omitted if its own
    `resets_at` is missing or already elapsed (see `_fetch_codex_weekly_usage`
    docstring) — `plan_type`/`credits` carry no time window, so they're
    included whenever the record has them regardless of window staleness.
    """
    now = _time.time()
    cached = _codex_detail_cache.get('data')
    if cached is not None and (now - _codex_detail_cache.get('ts', 0.0)) < _CODEX_DETAIL_TTL:
        return dict(cached) if cached else cached
    result = None
    try:
        files = _agent_runtime._codex_rollout_files()
        if files:
            latest = max(files, key=lambda f: f.stat().st_mtime)
            st = latest.stat()
            mtime = st.st_mtime
            size = st.st_size
            with open(latest, 'rb') as fh:
                if size > _CODEX_TAIL_BYTES:
                    fh.seek(size - _CODEX_TAIL_BYTES)
                chunk = fh.read()
            lines = chunk.decode('utf-8', errors='ignore').split('\n')
            for line in reversed(lines):
                if '"token_count"' not in line or '"rate_limits"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                info = (rec.get('payload') or {}) if isinstance(rec.get('payload'), dict) else rec
                rl = info.get('rate_limits') if isinstance(info, dict) else None
                if not isinstance(rl, dict):
                    continue

                def _window(win) -> Optional[dict]:
                    if not isinstance(win, dict):
                        return None
                    resets_at_raw = win.get('resets_at')
                    try:
                        resets_epoch = float(resets_at_raw) if resets_at_raw is not None else None
                    except (TypeError, ValueError):
                        resets_epoch = None
                    if resets_epoch is None or resets_epoch <= now:
                        return None
                    return {
                        'utilization': win.get('used_percent'),
                        'resets_at': datetime.fromtimestamp(resets_epoch, tz=timezone.utc).isoformat(),
                    }

                weekly = five_hour = None
                for slot in ('primary', 'secondary'):
                    win = rl.get(slot)
                    wmin = win.get('window_minutes') if isinstance(win, dict) else None
                    if wmin == _CODEX_WEEKLY_WINDOW_MINUTES:
                        weekly = _window(win)
                    elif wmin == _CODEX_FIVE_HOUR_WINDOW_MINUTES:
                        five_hour = _window(win)

                plan_type = rl.get('plan_type')
                credits = rl.get('credits') if isinstance(rl.get('credits'), dict) else None
                if weekly or five_hour or plan_type or credits:
                    result = {
                        'weekly': weekly,
                        'five_hour': five_hour,
                        'plan_type': plan_type,
                        'credits': credits,
                        'sampled_at': datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    }
                break  # latest token_count record found — stop scanning
    except Exception as e:
        _log(f"[system_usage] codex usage detail read failed: {e}", flush=True)
        result = None
    _codex_detail_cache['ts'] = now
    _codex_detail_cache['data'] = result
    return dict(result) if result else result


@bp.route('/api/system/usage', methods=['GET'])
def system_usage_get():
    """Return local token-usage aggregates derived from ~/.claude/stats-cache.json.

    This is the file Claude Code maintains itself: a per-day breakdown of
    tokens by model + cumulative per-model totals. The CLI's interactive
    `/status` Usage tab shows server-side rate-limit *percentages* (5h
    window, weekly all-model, weekly Sonnet-only) that are NOT exposed via
    any client-readable file or `--print` invocation — those come from
    Anthropic's billing service. We surface what we CAN see locally:

      - today's tokens by model
      - last 7-day tokens by model
      - all-time top models
      - totalSessions / totalMessages
      - lastComputedDate (so the user knows when the cache last ticked)

    Plus the rate-limit reset time from the existing system-status cache.
    Frontend ties this off with a "see canonical usage" link to
    https://claude.ai/settings/usage.
    """
    # MC's own agent_log telemetry — primary source for period buckets.
    mc = _mc_usage_from_agent_logs()

    # CC stats-cache — used for all-time top_models and totalSessions/Messages
    # fallback. May be stale (only updates during interactive CC use).
    cc_data = {}
    cc_available = False
    try:
        cc_path = Path.home() / '.claude' / 'stats-cache.json'
        if cc_path.exists():
            cc_data = json.loads(cc_path.read_text(encoding='utf-8'))
            cc_available = True
    except Exception:
        pass

    # All-time top models: prefer MC aggregated if it has data, fall back to CC.
    mc_all = mc.get('all_time', {})
    if mc_all:
        ranked = sorted(mc_all.items(), key=lambda x: x[1], reverse=True)[:5]
        top_models = [{'model': m, 'tokens': t, 'cache_read': 0}
                      for m, t in ranked]
    else:
        model_usage = cc_data.get('modelUsage') or {}
        top_models = []
        if isinstance(model_usage, dict):
            ranked = []
            for m, mu in model_usage.items():
                if not isinstance(mu, dict):
                    continue
                total = int(mu.get('inputTokens') or 0) + int(mu.get('outputTokens') or 0)
                ranked.append((m, total, int(mu.get('cacheReadInputTokens') or 0)))
            ranked.sort(key=lambda x: x[1], reverse=True)
            for m, total, cache in ranked[:5]:
                top_models.append({'model': m, 'tokens': total, 'cache_read': cache})

    last_data_date = mc.get('last_data_date', '') or cc_data.get('lastComputedDate', '')

    usage_limits = _fetch_oauth_usage_limits()

    # MC-966 bottom usage strip: one real weekly % per provider, keyed for the
    # frontend to iterate directly (never invents a bar for a provider with no
    # real signal — Gemini's CLI exposes no rate-limit percentage locally, so
    # it is never a key here; see `_fetch_codex_weekly_usage` for Codex's
    # on-disk source). Claude's number is the same `usage_limits.seven_day`
    # the Usage-tab bars already draw, just re-keyed by provider name.
    provider_weekly_usage: dict = {}
    _claude_seven_day = (usage_limits or {}).get('seven_day')
    if _claude_seven_day and _claude_seven_day.get('utilization') is not None:
        provider_weekly_usage['claude'] = {
            'utilization': _claude_seven_day.get('utilization'),
            'resets_at': _claude_seven_day.get('resets_at'),
        }
    _codex_weekly = _fetch_codex_weekly_usage()
    if _codex_weekly and _codex_weekly.get('utilization') is not None:
        # A fresh dict, not the cached object itself — the exhaustion overlay
        # below mutates this entry, and mutating the cache's own dict would
        # leak 'exhausted' onto every read for the rest of its 60s TTL, even
        # after the vendor block clears.
        provider_weekly_usage['codex'] = dict(_codex_weekly)
    # A vendor mid-exhaustion should read as full/red even if its last-sampled
    # weekly % predates the block — but only for a provider that already has a
    # real bar; exhaustion alone is not a substitute for a missing weekly %.
    for _vendor, _entry in _allowance_state.all_states().items():
        if _vendor in provider_weekly_usage:
            provider_weekly_usage[_vendor]['exhausted'] = True
            provider_weekly_usage[_vendor]['exhausted_display'] = _allowance_state.resets_clause(_entry)

    # MC-989 Part A: Codex detail for the Usage tab (weekly + 5h + plan_type +
    # credits + sampled_at) — the bottom strip only ever wanted the one
    # weekly %, this is the fuller picture the popup shows. None when no
    # rollout file yields anything at all (see `_fetch_codex_usage_detail`).
    codex_usage_detail = _fetch_codex_usage_detail()

    return jsonify({
        'available': True,
        'today': mc.get('today', {}),
        'week': mc.get('week', {}),
        'month': mc.get('month', {}),
        'top_models': top_models,
        'total_sessions': int(cc_data.get('totalSessions') or 0),
        'total_messages': int(cc_data.get('totalMessages') or 0),
        'last_computed_date': cc_data.get('lastComputedDate') or '',
        'last_data_date': last_data_date,
        'rate_limit_info': state._LAST_SYSTEM_STATUS.get('rate_limit_info') or {},
        # Authoritative subscription usage windows (% + resets) from the OAuth
        # endpoint; None when unavailable (UI falls back to rate_limit_info).
        'usage_limits': usage_limits,
        # Per-provider weekly % for the bottom usage strip (MC-966). Only real
        # numbers, keyed by provider name; a provider with no real weekly %
        # signal is simply absent, never a 0%/placeholder entry.
        'provider_weekly_usage': provider_weekly_usage,
        # Fuller Codex breakdown for the Usage tab (MC-989 Part A). None when
        # no rollout file yields anything; see `_fetch_codex_usage_detail`.
        'codex_usage_detail': codex_usage_detail,
    })


@bp.route('/api/system/usage/refresh', methods=['POST'])
def system_usage_refresh():
    """Bust the OAuth + Codex usage caches, then return the same payload as
    GET /api/system/usage (MC-989). Codex's own number only changes when a
    Codex turn actually runs — this can't force a fresh sample, it just lets
    a sample that already landed since the last 60s window show immediately
    instead of waiting out the TTL.
    """
    _oauth_usage_cache['ts'] = 0.0
    _codex_usage_cache['ts'] = 0.0
    _codex_detail_cache['ts'] = 0.0
    return system_usage_get()


# MC-989 Part B — one bare-CLI terminal pop-out per provider that has an
# interactive, human-only reset command. NEVER auto-types or pipes the
# command itself: a banked Codex reset is one-time and belongs to the account
# holder, and Claude's /limit-reset is gated the same way — the human reads
# the instruction and types it. Both CLIs are full-screen raw-mode TUIs, not
# line-oriented REPLs — `codex` refuses to start at all with a piped stdin
# ("Refusing to start the interactive TUI because no terminal is available"),
# and `claude` with piped stdin drops into print mode, where a typed line
# becomes a prompt instead of a slash command. So this needs a REAL pty
# (`launch_pty_session`, MC-928's pywinpty-backed spawn), same as
# `/api/terminal/launch`'s `{"pty": true}` branch — never
# `launch_pipe_session`, which only fakes TTY-ness for Python subprocesses
# via its PYTHONPATH shim and does nothing for these non-Python CLIs.
_USAGE_RESET_INSTRUCTIONS = {
    'claude': ('Type /limit-reset and press Enter to reset the 5-hour session '
               'limit (once per week — the weekly cap still applies).'),
    'codex': ('Type /usage, then choose "Redeem usage limit reset" if your '
              'account has one banked.'),
}


def _usage_reset_cwd() -> Optional[str]:
    """Scratch cwd for the reset terminal. Outside the install/repo on
    purpose: `claude` started anywhere under the repo picks up its
    `.mcp.json` and opens on the project-MCP approval screen before the user
    can type /limit-reset. Never DATA_DIR (load_projects() treats anything
    there as a project record)."""
    try:
        d = Path.home() / '.clayrune' / 'usage_reset'
        d.mkdir(parents=True, exist_ok=True)
        return str(d.resolve())
    except Exception:
        return None


@bp.route('/api/system/usage/reset-terminal', methods=['POST'])
def system_usage_reset_terminal():
    """Open a terminal pop-out running the bare provider CLI so a human can
    type its own interactive reset command. Returns the instruction text for
    the frontend to show alongside the pop-out; never sends the command
    itself (see module comment above)."""
    data = request.get_json(silent=True) or {}
    provider = (data.get('provider') or '').strip().lower()
    instruction = _USAGE_RESET_INSTRUCTIONS.get(provider)
    if not instruction:
        return jsonify({'ok': False, 'error': f'no terminal reset flow for provider: {provider}'}), 400
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'ok': False, 'error': f'unknown provider: {provider}'}), 404
    bin_path = rt.resolve_binary()
    if not bin_path:
        return jsonify({'ok': False, 'error': f'{provider} CLI is not installed'}), 400
    session_id, err = launch_pty_session('_usage_reset', str(bin_path), cwd=_usage_reset_cwd())
    if err:
        return jsonify({'ok': False, 'error': err}), 500
    return jsonify({
        'ok': True,
        'session_id': session_id,
        'command': str(bin_path),
        'instruction': instruction,
        'is_pty': True,
    })


@bp.route('/api/system/status/refresh', methods=['POST'])
def system_status_refresh():
    """Active refresh: spawn a minimal claude session purely to read its init
    message + rate-limit event, then return the freshly-updated cache.

    Costs roughly $0.001 (one tiny prompt, one tiny reply). Use sparingly:
    the cache auto-refreshes from any real agent activity, so this is only
    needed when the user wants live data after a long idle period.
    """
    try:
        # `--max-turns 1` with a one-word prompt is the cheapest valid call
        # that still emits the system/init + rate_limit_event we care about.
        # `--tools "" --strict-mcp-config --mcp-config {"mcpServers":{}}` is
        # NOT applied here — we WANT the full tool/MCP roster in the init so
        # the panel reflects the user's real environment, not a sandboxed
        # subset (which is why we don't reuse Claydo's flags).
        cmd = [_resolve_claude(),
               '--max-turns', '1',
               '--print', '--verbose',
               '--input-format', 'stream-json',
               '--output-format', 'stream-json']
        stdin_msg = json.dumps({
            'type': 'user',
            'message': {'role': 'user', 'content': 'ok'},
        }) + '\n'
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            input=stdin_msg,
            timeout=30, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        for line in (proc.stdout or '').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            _capture_system_init(obj)
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'refresh timed out (>30s)',
                        'status': _build_system_status_payload()}), 504
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found on this server',
                        'status': _build_system_status_payload()}), 500
    except Exception as e:
        return jsonify({'error': str(e),
                        'status': _build_system_status_payload()}), 500
    return jsonify(_build_system_status_payload())


def _get_active_restart_blockers():
    """Snapshot of sessions/hiveminds that would be killed if we restarted now.

    "Active" = a live agent turn (status='running') or an active hivemind
    orchestrator. Idle/completed/error/stopped sessions are NOT blockers — their
    process is either dead or just waiting on stdin and is safe to drop.
    """
    # Defensive: never let a stray/malformed file in DATA_DIR (no 'id') crash
    # the restart path — it shares this helper with the GET status endpoint.
    project_names = {p['id']: p.get('name', p['id'])
                     for p in load_projects() if isinstance(p, dict) and p.get('id')}
    active_sessions = []
    for sid, sess in list(agent_sessions.items()):
        if sess.get('status') != 'running':
            continue
        pid = sess.get('project_id', '')
        task = (sess.get('task') or '').strip()
        active_sessions.append({
            'session_id': sid,
            'project_id': pid,
            'project_name': project_names.get(pid, pid),
            'status': sess.get('status'),
            'task_preview': (task[:80] + '…') if len(task) > 80 else task,
            'started_at': sess.get('started_at'),
        })
    active_hiveminds = []
    with _hivemind_lock:
        for hm_id, hm in list(_hivemind_sessions.items()):
            if hm.get('status') != 'active':
                continue
            workers = hm.get('worker_sessions', []) or []
            active_hiveminds.append({
                'hivemind_id': hm_id,
                'project_id': hm.get('project_id', ''),
                'project_name': project_names.get(hm.get('project_id', ''), hm.get('project_id', '')),
                'title': hm.get('title') or hm.get('goal', '')[:80],
                'workers_running': len(workers),
            })
    return {'active_sessions': active_sessions, 'active_hiveminds': active_hiveminds}


def _append_restart_log(entry):
    try:
        log = []
        if RESTART_LOG_PATH.exists():
            try:
                log = json.loads(RESTART_LOG_PATH.read_text(encoding='utf-8'))
            except Exception:
                log = []
        log.append(entry)
        # Keep last 200 entries to bound the file
        if len(log) > 200:
            log = log[-200:]
        RESTART_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(RESTART_LOG_PATH, log, indent=2)
    except Exception as e:
        _log(f"[restart] failed to append log: {e}")


def _stop_all_sessions_for_restart(grace_seconds=3.0):
    """Best-effort graceful stop of every tracked session before re-exec.

    Iterates agent_sessions, sends graceful stop (Mode B closes stdin; both modes
    schedule a background kill of the proc tree). Then waits up to grace_seconds
    for processes to exit before letting the re-exec orphan/kill the rest.
    """
    procs = []
    for sid, sess in list(agent_sessions.items()):
        try:
            mgr = get_manager_for_session(sid)
            if mgr is None:
                # Fall back to a per-project lookup; if still not found, just touch the dict directly.
                pid = sess.get('project_id', '')
                mgr = get_manager(pid) if pid else None
            if mgr is not None:
                with mgr.lock:
                    if sess.get('status') in ('running', 'idle', 'error'):
                        proc = _stop_session(sess, sid)
                        if proc is not None:
                            procs.append(proc)
            else:
                # No manager — direct stop without lock as a last resort.
                if sess.get('status') in ('running', 'idle', 'error'):
                    proc = _stop_session(sess, sid)
                    if proc is not None:
                        procs.append(proc)
        except Exception as e:
            _log(f"[restart] graceful stop failed for {sid}: {e}")

    # Schedule background kills (existing helper handles tree-kill + wait).
    for proc in procs:
        _kill_proc_background(proc)

    # Stop the Cloudflare tunnel too. It's spawned outside the agent-session
    # tracker, so without this every restart/shutdown orphans cloudflared.exe
    # (observed: 29 leaked connectors accumulated across prior restarts).
    # Best-effort + bounded; a missing/disabled remote-access build just no-ops.
    # [leak fix 2026-06-03]
    try:
        from mc_remote import tunnel_supervisor as _tunnel_sup
        _tunnel_sup.get().stop(timeout=3.0)
    except Exception as e:
        try: _log(f"[restart] tunnel stop skipped: {e}")
        except Exception: pass

    # Close browser-pane Chromiums. The caller os._exit()s, so server.py's
    # atexit _cleanup_browsers never runs: each open pane's Chromium used to
    # outlive the restart holding its profile dir, and the next launch of that
    # profile died rc=21 -- a black pane (MC-976, 2026-09-25). After the
    # tunnel so a hung close can't push the tunnel stop past the caller's 4s.
    try:
        from mc.blueprints import browser_routes as _browser
        _n = _browser.close_all_sessions(timeout=2.0)
        if _n:
            _log(f"[restart] closed {_n} browser pane session(s)")
    except Exception as e:
        try: _log(f"[restart] browser close skipped: {e}")
        except Exception: pass

    # Brief wait so the children get a chance to die before exec replaces us.
    deadline = _time.time() + grace_seconds
    while _time.time() < deadline:
        alive = [p for p in procs if p.poll() is None]
        if not alive:
            break
        _time.sleep(0.1)


def _has_visible_console() -> bool:
    """Windows: True only if this process owns a VISIBLE console window.

    A windowless (start-hidden.vbs) launch owns a HIDDEN console — GetConsoleWindow
    returns a handle but IsWindowVisible is false — so this returns False and the
    restart path keeps the new instance windowless instead of popping a console.
    Fail-safe returns True (preserve the prior new-console behaviour) on any error
    or on non-Windows."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return False
        return bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    except Exception:
        return True


def _perform_server_restart_async(audit_entry):
    """Run after the HTTP response flushes: stop everything, then re-exec.

    Re-exec replaces the current Python process in place. Same PID, fresh
    interpreter — picks up code changes on disk. Open SSE streams drop, the
    frontend's polling overlay reconnects when /api/projects starts answering
    again, and the localStorage open-modals snapshot restores the conversation
    layout.

    Hardening (2026-05-27): if `_stop_all_sessions_for_restart` deadlocks (e.g.
    on an SSE-held mgr.lock held by the very session that triggered the
    restart), the original implementation hung forever and never re-exec'd.
    The UI's "any 200 = back" poll then declared false success against the
    old process. Three guards: (a) spawn the new process FIRST so progress is
    made before any potentially-blocking work, (b) bound the graceful stop in
    its own thread with a hard timeout, (c) start a hard watchdog that forces
    os._exit(2) past an absolute deadline no matter what.
    """
    def _do_restart():
        # Watchdog: under any circumstance, terminate within 10s of being
        # asked to restart. Daemon thread won't be joined; os._exit is a hard
        # SIGKILL-equivalent that bypasses atexit hooks but that's the point.
        def _watchdog():
            _time.sleep(10.0)
            try: _log("[restart] watchdog tripped — forcing termination")
            except Exception: pass
            os._exit(2)
        threading.Thread(target=_watchdog, daemon=True).start()

        _time.sleep(0.4)  # let the HTTP 202 actually reach the client

        # (1) Spawn the new instance FIRST. Even if everything below hangs,
        # the user already has a fresh server starting up. The new instance's
        # port-conflict bypass will wait for the old socket to free.
        spawned = False
        new_env = os.environ.copy()
        new_env['MC_RESTART_FROM_PID'] = str(os.getpid())
        try:
            popen_kwargs = {
                'env': new_env,
                'cwd': os.getcwd(),
                'close_fds': True,
            }
            if sys.platform == 'win32':
                # CREATE_NEW_PROCESS_GROUP so Ctrl-C in the old terminal doesn't
                # propagate to the fresh instance. For the console flag we branch:
                #   - Windowless launch (end users, via start-hidden.vbs): the
                #     process owns a HIDDEN console, so CREATE_NEW_CONSOLE would
                #     pop a visible python.exe log window on EVERY restart / self-
                #     update. Stay windowless (CREATE_NO_WINDOW) and redirect the
                #     new instance's output to the same log start.bat uses.
                #   - Dev launch from a real terminal: keep CREATE_NEW_CONSOLE so
                #     the restarted server stays visible (matches expectation).
                _windowless = (os.environ.get('CLAYRUNE_HIDDEN') == '1'
                               or not _has_visible_console())
                popen_kwargs['creationflags'] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | (subprocess.CREATE_NO_WINDOW if _windowless
                       else subprocess.CREATE_NEW_CONSOLE)
                )
                if _windowless:
                    # No console to print into — persist logs like the VBS path.
                    #
                    # The launcher (start.bat / start-hidden.vbs) already holds
                    # clayrune.log open with a share mode that denies a second
                    # writer, so on Windows this open() reliably raises
                    # PermissionError — and until 2026-07-27 that meant the
                    # restarted server logged NOWHERE. Every restart silently
                    # blinded us to exactly the boot we most wanted to inspect.
                    # Fall back to a per-instance file rather than losing the
                    # output; the path is echoed into the OLD process's log so
                    # it's findable.
                    _log_dir = os.path.join(os.getcwd(), 'data', 'logs')
                    _candidates = [
                        os.path.join(_log_dir, 'clayrune.log'),
                        os.path.join(_log_dir, f'clayrune-restart-{os.getpid()}.log'),
                    ]
                    _restart_log = None
                    for _cand in _candidates:
                        try:
                            os.makedirs(_log_dir, exist_ok=True)
                            # leaked intentionally; we os._exit shortly
                            _restart_log = open(_cand, 'ab')
                            popen_kwargs['stdout'] = _restart_log
                            popen_kwargs['stderr'] = subprocess.STDOUT
                            if _cand != _candidates[0]:
                                _log(f"[restart] main log busy; new instance logs to {_cand}")
                            break
                        except Exception as e:
                            _log(f"[restart] log redirect to {_cand} failed: {e}")
                    if _restart_log is None:
                        _log("[restart] no writable log target; new instance output is discarded")
            else:
                popen_kwargs['start_new_session'] = True
            subprocess.Popen([sys.executable] + sys.argv, **popen_kwargs)
            spawned = True
            _log("[restart] spawned new server process")
        except Exception as e:
            _log(f"[restart] failed to spawn new instance: {e}")

        # (2) Best-effort graceful stop, bounded by a wall-clock timeout.
        # Run in its own thread so a deadlock cannot prevent the os._exit
        # below. Whether or not it finishes, we proceed.
        stop_done = threading.Event()
        def _bounded_stop():
            try: _stop_all_sessions_for_restart()
            except Exception as e:
                try: _log(f"[restart] stop-all failed: {e}")
                except Exception: pass
            finally:
                stop_done.set()
        threading.Thread(target=_bounded_stop, daemon=True).start()
        stop_done.wait(timeout=4.0)
        if not stop_done.is_set():
            try: _log("[restart] stop-all exceeded 4s — proceeding to exit anyway")
            except Exception: pass

        # (3) Audit log + exit. Log write is best-effort.
        try: _append_restart_log(audit_entry)
        except Exception: pass

        # Brief settle so the new process can claim the port if the OS is
        # quick about it; the new instance is allowed to wait longer.
        _time.sleep(0.25)
        try: _log(f"[restart] exiting old process (spawned={spawned})")
        except Exception: pass
        os._exit(0 if spawned else 1)

    threading.Thread(target=_do_restart, daemon=True).start()


def _perform_server_shutdown_async(audit_entry):
    """Run after the HTTP response flushes: stop everything, then exit for good.

    The power-off analog of _perform_server_restart_async — same bounded
    graceful-stop + hard watchdog, but it does NOT spawn a replacement
    process. The dashboard shows a terminal "powered off" overlay; the user
    relaunches via the Clayrune shortcut.
    """
    def _do_shutdown():
        # Hard watchdog: terminate within 10s no matter what (mirrors restart).
        def _watchdog():
            _time.sleep(10.0)
            try: _log("[shutdown] watchdog tripped — forcing termination")
            except Exception: pass
            os._exit(0)
        threading.Thread(target=_watchdog, daemon=True).start()

        _time.sleep(0.4)  # let the HTTP 202 actually reach the client

        # Best-effort graceful stop, bounded by a wall-clock timeout and run in
        # its own thread so a deadlock cannot prevent the os._exit below.
        stop_done = threading.Event()
        def _bounded_stop():
            try: _stop_all_sessions_for_restart()
            except Exception as e:
                try: _log(f"[shutdown] stop-all failed: {e}")
                except Exception: pass
            finally:
                stop_done.set()
        threading.Thread(target=_bounded_stop, daemon=True).start()
        stop_done.wait(timeout=4.0)
        if not stop_done.is_set():
            try: _log("[shutdown] stop-all exceeded 4s — exiting anyway")
            except Exception: pass

        try: _append_restart_log(audit_entry)
        except Exception: pass

        try: _log("[shutdown] exiting — powered off by user request")
        except Exception: pass
        os._exit(0)

    threading.Thread(target=_do_shutdown, daemon=True).start()


@bp.route('/api/system/restart/status')
def system_restart_status():
    """Return what's currently active so the UI can warn before restarting."""
    return jsonify(_get_active_restart_blockers())


# ── Update Clayrune (git pull from inside the dashboard) ───────────────────

# "Is the working tree dirty enough that updating would destroy the user's
# work?" — asked before every update, and used for the UI's update-available
# badge.
#
# `-uno` (don't list untracked files) is LOAD-BEARING. Clayrune's own user data
# lives INSIDE the checkout (data/projects/, config.json, data/logs/, .venv/),
# and so does anything the user happens to drop in the install dir. Plain
# `--porcelain` reports all of that as "local changes", so a single stray
# untracked file made this endpoint answer 409 forever and set
# update_available=False — the install could never update again, with no
# indication why. Untracked files are user data, not edits to our source; the
# question here is only about MODIFIED TRACKED files.
#
# Narrow accepted trade-off: if the user parks a file at a path a future commit
# also adds, the update overwrites it. That beats never updating at all.
_DIRTY_TREE_ARGS = ['status', '--porcelain', '-uno']


def _git(args, cwd, timeout=30):
    """Run git with the given args in cwd. Returns (returncode, stdout+stderr).

    Hardened against the most common hang on Windows: Git Credential Manager
    (GCM) popping a hidden auth dialog (we use STARTF_USESHOWWINDOW=SW_HIDE,
    so the dialog never appears, but git waits for it forever until our
    timeout). GIT_TERMINAL_PROMPT=0 + GCM_INTERACTIVE=Never make git fail
    fast instead of prompting — for a public repo no auth is needed anyway.
    """
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GCM_INTERACTIVE'] = 'Never'
    try:
        r = subprocess.run(
            ['git', *args],
            cwd=str(cwd),
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=timeout,
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            env=env,
        )
        out = (r.stdout or '') + (r.stderr or '')
        return r.returncode, out.strip()
    except FileNotFoundError:
        return -1, 'git not found on PATH'
    except subprocess.TimeoutExpired:
        return -2, f'git {args[0]} timed out'
    except Exception as e:
        return -3, str(e)


def _git_version(repo_root, committish):
    """Synthetic build number from the nearest `v*` semver tag.

    `git describe --tags --match v*` yields one of:
      - "v1.5.1"                 → exactly on a release tag
      - "v1.5.1-180-gc6d2fae"    → 180 commits past v1.5.1
      - "<sha>" (--always)       → no v* tag reachable (fresh clone / shallow)

    Returns {'display', 'base', 'build', 'sha'}. `display` is the
    human string the UI shows; the rest are structured for callers that
    want to compare without re-parsing.
    """
    import re
    rc, out = _git(
        ['describe', '--tags', '--match', 'v*', '--always', '--abbrev=7', committish],
        repo_root,
    )
    if rc != 0 or not out:
        return {'display': 'unknown', 'base': '', 'build': 0, 'sha': ''}
    m = re.match(r'^(v[0-9][0-9.]*)-(\d+)-g([0-9a-f]+)$', out)
    if m:
        base, build, sha = m.group(1), int(m.group(2)), m.group(3)
        return {'display': f'{base} build {build}', 'base': base,
                'build': build, 'sha': sha}
    if re.match(r'^v[0-9][0-9.]*$', out):
        return {'display': out, 'base': out, 'build': 0, 'sha': ''}
    # --always fallback: no reachable v* tag, `out` is a bare short SHA.
    return {'display': f'untagged ({out})', 'base': '', 'build': 0, 'sha': out}


# ── Frozen (PyInstaller) update check — macOS .app has no .git ─────────────
# A git checkout updates via `git pull`; the notarized Mac .app is a frozen
# bundle with no .git, so the git-based logic above always hit the
# "not a git checkout" branch and Mac users got no update signal at all.
#
# We can't compare commits by tag/version: the release process re-uploads
# Clayrune-macOS.zip under the SAME tag when a build needs a fix (v2.3.0 was
# replaced 3 times in one day), so the tag alone can't tell "same build" from
# "newer build". Instead each build bakes its own commit + build time into
# build_info.json (installer/build-macos.spec), and each release publishes
# the identical file (copied from the built app, not recomputed — see
# tools/notarize-macos.sh) as the Clayrune-macOS.build.json release asset.
# Comparing those two is comparing the app's actual bundled identity, not a
# proxy for it.
_MACOS_GITHUB_REPO = 'clayrune-io/clayrune'
_MACOS_RELEASE_API = f'https://api.github.com/repos/{_MACOS_GITHUB_REPO}/releases/latest'
_MACOS_BUILD_MANIFEST_ASSET = 'Clayrune-macOS.build.json'
_MACOS_ZIP_ASSET = 'Clayrune-macOS.zip'
_MACOS_DOWNLOAD_URL = f'https://github.com/{_MACOS_GITHUB_REPO}/releases/latest/download/{_MACOS_ZIP_ASSET}'


def _load_bundled_build_info(repo_root):
    """Read the commit identity baked into a frozen build at build time.

    Returns None if absent (a build from before this shipped, or a dev
    checkout) or malformed -- both are treated as "can't tell", never as an
    error.
    """
    path = repo_root / 'build_info.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _fetch_latest_macos_release_info(timeout=8):
    """Fetch the build manifest published alongside the latest GitHub release.

    Unauthenticated GitHub API, short timeout, fails quiet (returns None) on
    any error -- a network hiccup must degrade to "could not check", never to
    a raw exception surfaced to a Mac user clicking a menu item.
    """
    try:
        req = urllib.request.Request(
            _MACOS_RELEASE_API,
            headers={'User-Agent': 'Clayrune-update-check', 'Accept': 'application/vnd.github+json'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            release = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        _log(f"[update-check] GitHub release lookup failed: {e}", flush=True)
        return None

    assets = release.get('assets') or []
    manifest_url = next(
        (a.get('browser_download_url') for a in assets
         if a.get('name') == _MACOS_BUILD_MANIFEST_ASSET), None,
    )
    if not manifest_url:
        return None

    try:
        req = urllib.request.Request(manifest_url, headers={'User-Agent': 'Clayrune-update-check'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            manifest = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        _log(f"[update-check] GitHub build manifest fetch failed: {e}", flush=True)
        return None

    manifest['release_tag'] = release.get('tag_name', '')
    manifest['release_notes'] = (release.get('body') or '').strip()
    manifest['download_url'] = next(
        (a.get('browser_download_url') for a in assets if a.get('name') == _MACOS_ZIP_ASSET),
        _MACOS_DOWNLOAD_URL,
    )
    return manifest


def _remote_build_is_newer(remote_built_at, local_built_at):
    """True if the remote build's timestamp is strictly after the local one.

    If either timestamp is missing or unparseable we can't tell -- default to
    True (a differing commit is already the primary signal; this only guards
    against flagging a genuinely OLDER remote build as an update).
    """
    try:
        return datetime.fromisoformat(remote_built_at) > datetime.fromisoformat(local_built_at)
    except Exception:
        return True


def _frozen_update_status(bundled):
    """Update status for a frozen (non-git) install, given its bundled
    build_info.json. Mirrors the shape of the git-based status closely enough
    for the same frontend to branch on `frozen` and reuse most of its layout.
    """
    local_commit = (bundled.get('commit_full') or bundled.get('commit') or '').strip()
    base = {
        'is_git_repo': False,
        'frozen': True,
        'commit': bundled.get('commit', ''),
        'built_at': bundled.get('built_at', ''),
    }
    remote = _fetch_latest_macos_release_info()
    if remote is None:
        base.update({
            'update_available': False,
            'message': 'Could not reach GitHub to check for updates.',
        })
        return base

    remote_commit = (remote.get('commit_full') or remote.get('commit') or '').strip()
    same_commit = bool(local_commit) and bool(remote_commit) and (
        local_commit == remote_commit
        or local_commit.startswith(remote_commit) or remote_commit.startswith(local_commit)
    )
    update_available = (
        bool(remote_commit) and not same_commit
        and _remote_build_is_newer(remote.get('built_at', ''), bundled.get('built_at', ''))
    )
    base.update({
        'remote_commit': remote.get('commit', ''),
        'remote_built_at': remote.get('built_at', ''),
        'release_tag': remote.get('release_tag', ''),
        'release_notes': (remote.get('release_notes', '') or '')[:500],
        'download_url': remote.get('download_url', _MACOS_DOWNLOAD_URL),
        'update_available': update_available,
    })
    return base


@bp.route('/api/system/update/status')
def system_update_status():
    """Report whether the install dir is a git repo, current commit + branch,
    and how far behind origin master we are. The Settings UI uses this to
    show a "X commits behind" badge.

    A frozen (PyInstaller) install has no .git -- see _frozen_update_status.
    """
    repo_root = _APP_DIR  # repo root in dev, app dir frozen; __file__ here is mc/blueprints/ — not the checkout
    if not (repo_root / '.git').exists():
        if getattr(sys, 'frozen', False):
            bundled = _load_bundled_build_info(repo_root)
            if bundled:
                return jsonify(_frozen_update_status(bundled))
        return jsonify({
            'is_git_repo': False,
            'message': 'Install directory is not a git checkout — automatic updates not available.',
        })

    rc, sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
    current_commit = sha if rc == 0 else 'unknown'
    rc, branch = _git(['rev-parse', '--abbrev-ref', 'HEAD'], repo_root)
    current_branch = branch if rc == 0 else 'unknown'

    # Fetch silently to learn what's on the remote. Tighter timeout (12s)
    # so the Settings UI doesn't sit on "Checking for updates..." for half a
    # minute when the network is slow or git's credential helper is
    # misbehaving. If fetch fails, we still report local-tip behind=0 below
    # rather than blocking the whole status response.
    _git(['fetch', '--quiet', 'origin'], repo_root, timeout=12)
    rc, ahead_behind = _git(
        ['rev-list', '--left-right', '--count', f'origin/{current_branch}...HEAD'],
        repo_root,
    )
    behind = 0
    ahead = 0
    if rc == 0 and ahead_behind:
        try:
            behind, ahead = (int(x) for x in ahead_behind.split())
        except Exception:
            pass

    # Detect dirty working tree (uncommitted changes that would block pull).
    rc, status_out = _git(_DIRTY_TREE_ARGS, repo_root)
    has_local_changes = bool(status_out)

    # Remote tip SHA + commit dates, so the UI can show "installed X (date) →
    # latest Y (date)" instead of just an opaque behind-count.
    rc, remote_sha = _git(['rev-parse', '--short', f'origin/{current_branch}'], repo_root)
    remote_commit = remote_sha if rc == 0 else ''
    rc, ld = _git(['log', '-1', '--format=%cs', 'HEAD'], repo_root)
    local_commit_date = ld if rc == 0 else ''
    rc, rd = _git(['log', '-1', '--format=%cs', f'origin/{current_branch}'], repo_root)
    remote_commit_date = rd if rc == 0 else ''

    local_ver = _git_version(repo_root, 'HEAD')
    remote_ver = _git_version(repo_root, f'origin/{current_branch}')

    return jsonify({
        'is_git_repo': True,
        'install_dir': str(repo_root),
        'branch': current_branch,
        'commit': current_commit,
        'commit_date': local_commit_date,
        'version': local_ver['display'],
        'remote_commit': remote_commit,
        'remote_commit_date': remote_commit_date,
        'remote_version': remote_ver['display'],
        'behind': behind,
        'ahead': ahead,
        'has_local_changes': has_local_changes,
        'update_available': behind > 0 and not has_local_changes and ahead == 0,
        # Settings warning (2026-09-14, Amit's "update blocked" report): a
        # project whose workspace is this install's own source tree is how an
        # agent ends up editing Clayrune itself and freezing self-updates.
        # project_routes.update_project blocks NEW assignments of this kind
        # (unless allow_project_in_install_dir is on), but an install upgraded
        # from before that guard existed can already have one on disk — this
        # is what tells the human, rather than leaving it silent.
        'projects_in_install_dir': _projects_pointing_at_install_dir(repo_root),
    })


def _projects_pointing_at_install_dir(repo_root):
    """[{'id':..., 'name':...}] for every project whose project_path is the
    install dir or inside it. Empty once allow_project_in_install_dir is on —
    that flag is the human's explicit acknowledgement, so the warning would
    have nothing left to tell them."""
    if state.CONFIG.get('allow_project_in_install_dir') or load_projects is None:
        return []
    hits = []
    try:
        for p in load_projects():
            pp = (p.get('project_path') or '').strip()
            if pp and path_is_within(pp, repo_root):
                hits.append({'id': p.get('id'), 'name': p.get('name') or p.get('id')})
    except Exception as e:
        _log(f"[update] projects_in_install_dir scan failed: {e}", flush=True)
        return []
    return hits


# ── Background update-check daemon ──────────────────────────────────────────
# Runs `git fetch` every 6h and caches the answer. Lets the dashboard show a
# passive "update available" badge without doing a 12-second git operation on
# every page load. Settings -> Update Clayrune still does a fresh fetch via
# /api/system/update/status when the user actively asks.

# _UPDATE_CHECK_LOCK / _UPDATE_CHECK_CACHE / _UPDATE_CHECK_INTERVAL_S /
# _UPDATE_CHECK_BOOT_DELAY_S moved to mc/state.py (Phase 0).


def _refresh_update_cache():
    """Run git fetch + recompute the update status, store in
    _UPDATE_CHECK_CACHE. Idempotent; safe to call from any thread."""
    repo_root = _APP_DIR  # repo root in dev, app dir frozen; __file__ here is mc/blueprints/ — not the checkout
    if not (repo_root / '.git').exists():
        bundled = _load_bundled_build_info(repo_root) if getattr(sys, 'frozen', False) else None
        with _UPDATE_CHECK_LOCK:
            if bundled:
                _UPDATE_CHECK_CACHE.clear()
                _UPDATE_CHECK_CACHE.update(_frozen_update_status(bundled))
                _UPDATE_CHECK_CACHE['last_check_ts'] = _time.time()
            else:
                _UPDATE_CHECK_CACHE.update({
                    'last_check_ts': _time.time(),
                    'is_git_repo': False,
                })
        return

    rc, sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
    current_commit = sha if rc == 0 else 'unknown'
    rc, branch = _git(['rev-parse', '--abbrev-ref', 'HEAD'], repo_root)
    current_branch = branch if rc == 0 else 'unknown'

    _git(['fetch', '--quiet', 'origin'], repo_root, timeout=12)
    rc, ahead_behind = _git(
        ['rev-list', '--left-right', '--count', f'origin/{current_branch}...HEAD'],
        repo_root,
    )
    behind = ahead = 0
    if rc == 0 and ahead_behind:
        try:
            behind, ahead = (int(x) for x in ahead_behind.split())
        except Exception:
            pass

    rc, status_out = _git(_DIRTY_TREE_ARGS, repo_root)
    has_local_changes = bool(status_out)

    rc, remote_sha = _git(['rev-parse', '--short', f'origin/{current_branch}'], repo_root)
    remote_commit = remote_sha if rc == 0 else ''

    rc, log_out = _git(
        ['log', f'HEAD..origin/{current_branch}', '-5', '--pretty=format:%h %s'],
        repo_root,
    )
    recent_log = log_out if rc == 0 else ''

    local_ver = _git_version(repo_root, 'HEAD')
    remote_ver = _git_version(repo_root, f'origin/{current_branch}')

    with _UPDATE_CHECK_LOCK:
        _UPDATE_CHECK_CACHE.update({
            'last_check_ts': _time.time(),
            'is_git_repo': True,
            'branch': current_branch,
            'commit': current_commit,
            'version': local_ver['display'],
            'remote_version': remote_ver['display'],
            'remote_commit': remote_commit,
            'behind': behind,
            'ahead': ahead,
            'has_local_changes': has_local_changes,
            'update_available': behind > 0 and not has_local_changes and ahead == 0,
            'recent_log': recent_log,
        })


def _update_check_loop():
    """Daemon thread: refresh the update cache every _UPDATE_CHECK_INTERVAL_S
    seconds. First check fires after _UPDATE_CHECK_BOOT_DELAY_S so we don't
    fight server startup."""
    _time.sleep(_UPDATE_CHECK_BOOT_DELAY_S)
    while True:
        obs.heartbeat('update-check')  # Phase 2: loop liveness -> /api/system/loops
        try:
            _refresh_update_cache()
        except Exception as e:
            _log(f"[update-check] loop error: {e}", flush=True)
        _time.sleep(_UPDATE_CHECK_INTERVAL_S)


@bp.route('/api/system/update/cached')
def system_update_cached():
    """Cheap snapshot of the update cache. No git operations -- just reads
    memory. Frontend polls this on dashboard load to decide whether to show
    the "update available" badge / toast.

    For a fresh fetch (manual "Check now" path), use /api/system/update/status.
    """
    with _UPDATE_CHECK_LOCK:
        snap = dict(_UPDATE_CHECK_CACHE)
    snap['stale_seconds'] = int(_time.time() - snap['last_check_ts']) if snap['last_check_ts'] else None
    return jsonify(snap)


@bp.route('/api/system/update', methods=['POST'])
def system_update():
    """Update the install dir to the remote tip. The Settings UI calls this
    after the user confirms. Returns the git output so the user sees what
    changed. Does NOT auto-restart — the UI prompts the user separately.

    Body: {"stash": true} — self-service path for a dirty tree (2026-09-14,
    Amit's "Blocked" report). A non-developer has no way to run `git stash`
    themselves, so a dirty tree used to freeze updates forever with no path
    forward except asking someone who can use git. Human-only, same guard as
    every other update/config action: an agent must never silently discard or
    set aside its own or another session's uncommitted work.

    LOAD-BEARING: `git pull --ff-only` is tried first, but it is NOT sufficient
    on its own. When the release branch is force-pushed upstream, ff-only
    aborts ("fatal: Not possible to fast-forward, aborting") and this endpoint —
    the ONLY update channel most users have — fails forever, silently. So we
    fall back to `fetch` + `reset --hard origin/<branch>`.

    Safe because: (a) we already refused above if the working tree is dirty
    (unless stashed first), and (b) `reset --hard` rewrites TRACKED files only.
    All user data lives in untracked/gitignored paths (data/projects/,
    data/settings.json, config.json, data/logs/, .venv/) and is untouched.
    NEVER add `git clean` here — that WOULD delete it.
    """
    repo_root = _APP_DIR  # repo root in dev, app dir frozen; __file__ here is mc/blueprints/ — not the checkout
    if not (repo_root / '.git').exists():
        if getattr(sys, 'frozen', False):
            bundled = _load_bundled_build_info(repo_root)
            if bundled:
                frozen_status = _frozen_update_status(bundled)
                return jsonify({
                    'ok': False,
                    'frozen': True,
                    'download_required': True,
                    'download_url': frozen_status.get('download_url', _MACOS_DOWNLOAD_URL),
                    'message': 'This is a downloaded Mac build, not a git checkout. Quit '
                               'Clayrune, download the new build, and replace the app in '
                               'Applications.',
                })
        return jsonify({'error': 'install dir is not a git checkout'}), 400

    data = request.get_json(silent=True) or {}
    want_stash = bool(data.get('stash'))

    rc, status_out = _git(_DIRTY_TREE_ARGS, repo_root)
    if rc != 0:
        return jsonify({'error': f'git status failed: {status_out}'}), 500

    stash_ref = ''
    if status_out:
        if not want_stash:
            return jsonify({
                'error': 'Working tree has local changes — pull would conflict.',
                'detail': status_out[:500],
                'hint': 'Stash or commit local changes, then re-try.',
            }), 409
        if _is_agent_caller():
            return jsonify({
                'error': ('setting aside local changes to update is human-only: an agent '
                          'must never discard or shelve uncommitted work (its own or '
                          'someone else\'s) without a person confirming it in the UI.'),
            }), 403
        rc_pre, pre_sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
        stash_msg = f"clayrune-auto-stash {now_iso()} {pre_sha if rc_pre == 0 else 'unknown'}"
        # No -u: this only shelves TRACKED changes (the dirty-check above is
        # -uno, i.e. tracked-only too) — untracked user data must never be
        # swept into a stash entry.
        rc_stash, stash_out = _git(['stash', 'push', '-m', stash_msg], repo_root, timeout=30)
        if rc_stash != 0:
            return jsonify({
                'error': f'git stash failed (rc={rc_stash})',
                'detail': stash_out[:500],
            }), 500
        stash_ref = stash_msg
        _log(f"[update] stashed local changes before update: {stash_msg}", flush=True)

    rc_old, old_sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
    previous_commit = old_sha if rc_old == 0 else ''

    resynced = False
    rc, pull_out = _git(['pull', '--ff-only', '--quiet'], repo_root, timeout=60)
    if rc != 0:
        _log(f"[update] ff-only pull failed (rc={rc}); falling back to hard "
             f"re-sync. git said: {pull_out[:300]}", flush=True)

        rc_f, fetch_out = _git(['fetch', '--prune', 'origin'], repo_root, timeout=60)
        if rc_f != 0:
            return jsonify({
                'error': f'git fetch failed (rc={rc_f})',
                'detail': fetch_out[:1000],
                'hint': 'Check network connectivity to github.com.',
                'stashed': stash_ref,  # changes already set aside: tell the user where
            }), 500

        rc_b, branch = _git(['rev-parse', '--abbrev-ref', 'HEAD'], repo_root)
        branch = (branch or '').strip() if rc_b == 0 else ''
        if not branch or branch == 'HEAD':
            branch = 'master'  # detached HEAD → land on the release channel
        rc_v, _ = _git(['rev-parse', '--verify', '--quiet',
                        f'refs/remotes/origin/{branch}'], repo_root)
        if rc_v != 0:
            branch = 'master'

        rc_r, reset_out = _git(['reset', '--hard', f'origin/{branch}'],
                               repo_root, timeout=60)
        if rc_r != 0:
            return jsonify({
                'error': f'git pull failed (rc={rc}) and re-sync to '
                         f'origin/{branch} failed (rc={rc_r})',
                'detail': (pull_out + '\n---\n' + reset_out)[:1000],
                'hint': 'The checkout may be damaged. Re-run the Clayrune '
                        'installer, or re-clone and copy your data/ dir over.',
                'stashed': stash_ref,
            }), 500
        resynced = True

    rc, new_sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
    rc2, log_out = _git(['log', '-5', '--pretty=format:%h %s'], repo_root)
    return jsonify({
        'ok': True,
        'new_commit': new_sha if rc == 0 else 'unknown',
        'previous_commit': previous_commit,
        # True when ff-only was impossible (upstream force-push) and we had to
        # reset --hard. Surfaced so the UI can say so, and so previous_commit is
        # meaningful for recovery: git reset --hard <previous_commit>.
        'resynced': resynced,
        'recent_log': log_out if rc2 == 0 else '',
        'restart_recommended': True,  # FE should prompt for restart after pull
        # Non-empty only when we stashed local changes first (want_stash=True).
        # The UI shows this verbatim so the user knows how to get the work
        # back: `git stash list` to find it, `git stash apply` to restore.
        'stashed': stash_ref,
    })


@bp.route('/api/system/restart', methods=['POST'])
def system_restart():
    """Restart the Mission Control server process.

    Body: {"confirmed": true, "force": bool}. We always re-check active state
    on the server to close the GET → POST race window (a cron or hivemind
    could have spawned a fresh session in between). If active and force is
    falsy, return 409 with the live blocker list so the UI can re-prompt.
    """
    data = request.get_json(silent=True) or {}
    if not data.get('confirmed'):
        return jsonify({'error': 'confirmation required (set "confirmed": true)'}), 400

    now = _time.time()
    if now - state._LAST_RESTART_TIME < _RESTART_RATE_LIMIT_SECONDS:
        wait = int(_RESTART_RATE_LIMIT_SECONDS - (now - state._LAST_RESTART_TIME))
        return jsonify({'error': f'restart was triggered recently; try again in {wait}s'}), 429

    blockers = _get_active_restart_blockers()
    if (blockers['active_sessions'] or blockers['active_hiveminds']) and not data.get('force'):
        return jsonify({
            'error': 'active flows present; stop them or pass "force": true',
            **blockers,
        }), 409

    state._LAST_RESTART_TIME = now
    audit_entry = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'source_ip': request.remote_addr or '',
        'user_agent': request.headers.get('User-Agent', ''),
        'tunneled': _is_cf_tunneled_request(),
        'blockers_at_request': blockers,
        'forced': bool(data.get('force')),
    }
    _perform_server_restart_async(audit_entry)
    return jsonify({'ok': True, 'restarting': True}), 202


@bp.route('/api/system/shutdown', methods=['POST'])
def system_shutdown():
    """Shut down (power off) the Mission Control server process.

    Same confirmation + active-flow blocker semantics as /api/system/restart,
    but the process exits WITHOUT spawning a replacement. Body:
    {"confirmed": true, "force": bool}. Not rate-limited — it's a one-way,
    terminal action, so a double-submit is harmless (the process is already
    on its way out).
    """
    data = request.get_json(silent=True) or {}
    if not data.get('confirmed'):
        return jsonify({'error': 'confirmation required (set "confirmed": true)'}), 400

    blockers = _get_active_restart_blockers()
    if (blockers['active_sessions'] or blockers['active_hiveminds']) and not data.get('force'):
        return jsonify({
            'error': 'active flows present; stop them or pass "force": true',
            **blockers,
        }), 409

    audit_entry = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'source_ip': request.remote_addr or '',
        'user_agent': request.headers.get('User-Agent', ''),
        'tunneled': _is_cf_tunneled_request(),
        'blockers_at_request': blockers,
        'forced': bool(data.get('force')),
        'action': 'shutdown',
    }
    _perform_server_shutdown_async(audit_entry)
    return jsonify({'ok': True, 'shutting_down': True}), 202


