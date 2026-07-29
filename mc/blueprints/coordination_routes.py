"""Cross-agent coordination layer — Phase 0 (awareness-only, shared tree).

Design: docs/COORDINATION_LAYER_DESIGN.md. Backlog 9518ec62.

Two or more agents on the SAME project are blind to each other today: Agent B
re-does or contradicts what Agent A just landed. This module gives siblings
AWARENESS without touching the working tree — the low-risk PoC slice:

  • INTENTIONS are derived passively from each live session's `task` (the task
    string IS "what that agent is about to do"). No publish call needed.
  • COMPLETIONS are derived from git commits: reconcile_commits() walks new
    commits on the project's HEAD and appends them to events.jsonl (deduped by
    sha), with the touched paths as `targets`.
  • sibling_activity() ranks those against the current agent's task by
    target/keyword overlap and returns the top-K for a read-floor section
    (`SIBLING ACTIVITY`) injected by _build_agent_context — same delivery shape
    as RELEVANT MEMORY.

Everything is best-effort and NEVER load-bearing (same posture as Scribe /
Distiller): a failure here never breaks a dispatch, a turn, or completion
logging. Gated behind CONFIG['coordination_enabled'] (default OFF).

State lives under data/coordination/<project_id>/ — OUTSIDE DATA_DIR
(data/projects/ is projects-records-only; the DATA_DIR-pollution load-bearing
rule in CLAUDE.md). Explicit publish/events/stream endpoints exist for the
enrichment tier + UI/testing; Phase 0's automatic path needs none of them.
"""

import json
import threading
import time as _time
import uuid
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request

from mc import state
from mc.core import _log, now_iso, time_ago

bp = Blueprint('coordination_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
get_manager: Callable[[str], Any] = None  # type: ignore[assignment]
git_run: Callable[..., Any] = None  # type: ignore[assignment]  # project_sync.git_run
COORD_DIR: Path = None  # type: ignore[assignment]

# SSE listeners per project (browser + future server consumers).
_sse_lock = threading.Lock()
_sse_queues: dict[str, list] = {}

# How many recent commits to backfill on the very first reconcile of a project
# (so a fresh coordination session isn't blind to work that landed seconds ago),
# and the cap on any single reconcile walk.
_FIRST_SEEN_BACKFILL = 5
_RECONCILE_WALK_LIMIT = 50
# A completion older than this is stale for read-floor purposes (the change has
# long since propagated; showing it just adds noise).
_COMPLETION_TTL_SECS = 6 * 3600


def wire(*, coord_dir, load_project_fn, get_manager_fn, git_run_fn):
    """Late-bind cross-family deps. Called once from server.py at import,
    BEFORE app.register_blueprint(bp). git_run comes from project_sync (its
    argv-guarded subprocess wrapper); get_manager (dispatch family) yields the
    live per-project session roster for liveness gating."""
    global COORD_DIR, load_project, get_manager, git_run
    COORD_DIR = coord_dir
    COORD_DIR.mkdir(parents=True, exist_ok=True)
    load_project = load_project_fn
    get_manager = get_manager_fn
    git_run = git_run_fn


def enabled() -> bool:
    """Master gate — Phase 0 ships default-OFF."""
    return bool(state.CONFIG.get('coordination_enabled', False))


# ── Storage helpers ──────────────────────────────────────────────────────────

def _coord_dir(project_id: str) -> Path:
    d = COORD_DIR / project_id
    return d


def _events_path(project_id: str) -> Path:
    return _coord_dir(project_id) / 'events.jsonl'


def _state_path(project_id: str) -> Path:
    """Sidecar tracking the last git sha we turned into COMPLETION events, so
    reconcile is idempotent across calls and restarts."""
    return _coord_dir(project_id) / 'state.json'


def _append_event(project_id: str, event: dict) -> None:
    d = _coord_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    with open(_events_path(project_id), 'a', encoding='utf-8') as f:
        f.write(json.dumps(event, ensure_ascii=False) + '\n')


def _read_events(project_id: str, last_n: int = 100) -> list[dict]:
    p = _events_path(project_id)
    if not p.exists():
        return []
    lines: list[str] = []
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    except Exception:
        return []
    out: list[dict] = []
    for line in (lines[-last_n:] if last_n else lines):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _load_state(project_id: str) -> dict:
    p = _state_path(project_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_state(project_id: str, st: dict) -> None:
    d = _coord_dir(project_id)
    d.mkdir(parents=True, exist_ok=True)
    try:
        _state_path(project_id).write_text(
            json.dumps(st, ensure_ascii=False), encoding='utf-8')
    except Exception as e:
        _log(f"[coord] save_state failed for {project_id}: {e}")


def _push_sse(project_id: str, event: dict) -> None:
    with _sse_lock:
        for q in _sse_queues.get(project_id, []):
            try:
                q.append(event)
            except Exception:
                pass


# ── Liveness / roster ────────────────────────────────────────────────────────

def _live_sessions(project_id: str) -> list[tuple[str, dict]]:
    """Currently-running/idle sessions for a project (the live agents whose
    tasks count as open INTENTIONS). Best-effort — empty on any failure."""
    if get_manager is None:
        return []
    try:
        pairs = get_manager(project_id).iter_sessions()
    except Exception:
        return []
    out = []
    for sid, s in pairs:
        if not isinstance(s, dict):
            continue
        if s.get('status') in ('running', 'idle'):
            out.append((sid, s))
    return out


# ── Commit reconciliation → COMPLETION events ────────────────────────────────

_COMMIT_FMT = '%H%x1f%h%x1f%an%x1f%aI%x1f%s'


def _new_commits(project_path: str, since_sha: str) -> list[dict]:
    """Commits on HEAD newer than since_sha (exclusive). If since_sha is empty
    or unknown, returns the last _FIRST_SEEN_BACKFILL commits."""
    if since_sha:
        rng = f'{since_sha}..HEAD'
        args = ['log', f'--max-count={_RECONCILE_WALK_LIMIT}',
                f'--format={_COMMIT_FMT}', '--name-only', rng]
    else:
        args = ['log', f'--max-count={_FIRST_SEEN_BACKFILL}',
                f'--format={_COMMIT_FMT}', '--name-only', 'HEAD']
    ok, out = git_run(project_path, args, timeout=15)
    if not ok:
        # since_sha is gone (rebased/gc'd) — git errors on the bad range. Fall
        # back to a shallow backfill so we re-anchor instead of going silent.
        # A VALID range with no new commits returns ok=True, out='' — that is
        # the common idempotent case and must NOT re-backfill.
        if since_sha:
            return _new_commits(project_path, '')
        return []
    if not out:
        return []
    commits: list[dict] = []
    cur: dict | None = None
    for raw in out.splitlines():
        if '\x1f' in raw:
            parts = raw.split('\x1f')
            if len(parts) == 5:
                if cur:
                    commits.append(cur)
                cur = {
                    'sha': parts[0], 'short': parts[1], 'author': parts[2],
                    'authored_at': parts[3], 'subject': parts[4], 'targets': [],
                }
            continue
        line = raw.strip()
        if line and cur is not None:
            cur['targets'].append(line)
    if cur:
        commits.append(cur)
    return commits


def reconcile_commits(project: dict) -> int:
    """Turn new git commits into COMPLETION events (deduped by sha). Cheap,
    idempotent, best-effort. Returns count published. Safe to call on every
    read-floor build — a `git log` since the last-seen sha is near-free."""
    if not enabled() or git_run is None:
        return 0
    pid = project.get('id', '')
    pp = project.get('project_path', '')
    if not pid or not pp or not Path(pp).is_dir():
        return 0
    try:
        ok, _ = git_run(pp, ['rev-parse', '--is-inside-work-tree'], timeout=5)
        if not ok:
            return 0
        st = _load_state(pid)
        since = st.get('last_seen_sha', '')
        commits = _new_commits(pp, since)
        if not commits:
            return 0
        # git log returns newest-first; publish oldest-first so the log reads
        # chronologically, and record the newest as the new watermark.
        published = 0
        for c in reversed(commits):
            if c['sha'] == since:
                continue
            ev = {
                'id': 'c_' + uuid.uuid4().hex[:8],
                'ts': now_iso(),
                'type': 'completion',
                'agent': c.get('author', ''),
                'session_id': '',           # commit-derived, not session-scoped
                'commit_sha': c['sha'],
                'short': c.get('short', ''),
                'summary': c.get('subject', ''),
                'targets': c.get('targets', []),
                'authored_at': c.get('authored_at', ''),
            }
            _append_event(pid, ev)
            _push_sse(pid, {'type': 'coord_completion', 'event': ev})
            published += 1
        st['last_seen_sha'] = commits[0]['sha']  # newest
        _save_state(pid, st)
        return published
    except Exception as e:
        _log(f"[coord] reconcile failed for {pid}: {e}")
        return 0


# ── Relevance ranking ────────────────────────────────────────────────────────

_STOP = {
    'the', 'a', 'an', 'and', 'or', 'to', 'of', 'in', 'on', 'for', 'with', 'is',
    'are', 'be', 'at', 'it', 'this', 'that', 'add', 'fix', 'update', 'change',
    'make', 'use', 'new', 'set', 'get', 'run', 'do', 'we', 'i', 'you', 'not',
}


def _tokens(text: str) -> set[str]:
    out = set()
    for w in ''.join(c.lower() if (c.isalnum() or c in '/._-') else ' '
                     for c in (text or '')).split():
        w = w.strip('/._-')
        if len(w) >= 3 and w not in _STOP:
            out.add(w)
            # also index path basenames so a task mentioning a file matches a
            # commit touching it.
            if '/' in w:
                out.add(w.rsplit('/', 1)[-1])
    return out


def _overlap(a: set[str], b: set[str]) -> int:
    return len(a & b)


def sibling_activity(project_id: str, my_session_id: str, task: str,
                     topk: int = 3) -> list[dict]:
    """Ranked awareness feed for the read-floor: other live agents' current
    tasks (open INTENTIONS) + recent commit COMPLETIONS, scored by target /
    keyword overlap with THIS agent's task. Excludes my own session. Best-effort.
    """
    if not enabled():
        return []
    try:
        my_tokens = _tokens(task)
        items: list[dict] = []

        # (1) Live siblings' tasks = open intentions.
        for sid, s in _live_sessions(project_id):
            if sid == my_session_id:
                continue
            if s.get('housekeeping'):
                continue
            stask = (s.get('task') or '').strip()
            if not stask:
                continue
            label = _short_label(sid, s)
            items.append({
                'kind': 'intention',
                'agent': label,
                'summary': stask[:200],
                'score': _overlap(my_tokens, _tokens(stask)),
                'ts': s.get('started_at', ''),
            })

        # (2) Recent commit completions (durable — from live OR finished agents).
        now = _time.time()
        for ev in _read_events(project_id, last_n=100):
            if ev.get('type') != 'completion':
                continue
            age = _age_secs(ev.get('ts', ''), now)
            if age is not None and age > _COMPLETION_TTL_SECS:
                continue
            tgt = ev.get('targets', []) or []
            score = _overlap(my_tokens, _tokens(
                (ev.get('summary', '') + ' ' + ' '.join(tgt))))
            items.append({
                'kind': 'completion',
                'agent': ev.get('agent', ''),
                'summary': ev.get('summary', '')[:200],
                'targets': tgt[:6],
                'short': ev.get('short', ''),
                'score': score,
                'ts': ev.get('ts', ''),
            })

        # Rank: overlap desc, then recency desc. Keep at least the most-recent
        # completions even at zero overlap (a just-landed change is worth
        # knowing about); drop zero-overlap intentions (too noisy).
        items = [it for it in items
                 if it['score'] > 0 or it['kind'] == 'completion']
        items.sort(key=lambda it: (it['score'], it.get('ts', '')), reverse=True)
        return items[:topk]
    except Exception as e:
        _log(f"[coord] sibling_activity failed for {project_id}: {e}")
        return []


def render_readfloor(items: list[dict]) -> str:
    """Format sibling_activity() items as the SIBLING ACTIVITY read-floor block.
    Returns '' when there's nothing worth showing."""
    if not items:
        return ''
    lines = []
    for it in items:
        if it['kind'] == 'intention':
            lines.append(f"  • [working now] {it['agent']}: {it['summary']}")
        else:
            tgt = it.get('targets') or []
            tgt_s = f" (touched: {', '.join(tgt)})" if tgt else ''
            who = f"{it['agent']} " if it.get('agent') else ''
            lines.append(
                f"  • [landed {it.get('short','')}] {who}{it['summary']}{tgt_s}")
    return (
        "--- SIBLING ACTIVITY (other agents on THIS project right now — do NOT "
        "duplicate or contradict their in-flight work; coordinate via the "
        "coord bus if you overlap) ---\n" + "\n".join(lines)
    )


def _short_label(sid: str, s: dict) -> str:
    return (s.get('trigger_type') or 'agent') + ':' + sid[:6]


def _age_secs(ts: str, now: float):
    if not ts:
        return None
    try:
        from datetime import datetime
        t = datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
        return max(0.0, now - t)
    except Exception:
        return None


# ── Endpoints (explicit enrichment tier + UI/testing) ────────────────────────

@bp.route('/api/project/<project_id>/coord/publish', methods=['POST'])
def coord_publish(project_id):
    """Explicit publish — the enrichment tier. Lets an agent post a richer,
    forward-looking INTENTION the server can't infer from the task string, or a
    manual COMPLETION. Auto-derivation (task-as-intention + commit-as-completion)
    covers the common case with no call at all."""
    if load_project(project_id) is None:
        return jsonify({'error': 'project not found'}), 404
    data = request.get_json() or {}
    ev_type = (data.get('type') or '').strip()
    if ev_type not in ('intention', 'completion'):
        return jsonify({'error': "type must be 'intention' or 'completion'"}), 400
    ev = {
        'id': ('i_' if ev_type == 'intention' else 'c_') + uuid.uuid4().hex[:8],
        'ts': now_iso(),
        'type': ev_type,
        'agent': (data.get('agent') or '').strip(),
        'session_id': (data.get('session_id') or '').strip(),
        'summary': (data.get('summary') or '').strip(),
        'targets': data.get('targets') or [],
        'explicit': True,
    }
    if ev_type == 'completion':
        ev['commit_sha'] = (data.get('commit_sha') or '').strip()
    _append_event(project_id, ev)
    _push_sse(project_id, {'type': f'coord_{ev_type}', 'event': ev})
    return jsonify({'ok': True, 'event': ev})


@bp.route('/api/project/<project_id>/coord/events')
def coord_events(project_id):
    """Recent coordination events (optionally reconcile fresh commits first)."""
    if load_project(project_id) is None:
        return jsonify({'error': 'project not found'}), 404
    if request.args.get('reconcile') == '1':
        p = load_project(project_id)
        if p:
            reconcile_commits(p)
    try:
        limit = int(request.args.get('limit', 100))
    except Exception:
        limit = 100
    return jsonify(_read_events(project_id, last_n=limit))


@bp.route('/api/project/<project_id>/coord/roster')
def coord_roster(project_id):
    """Live agents on this project + a rendered awareness preview."""
    if load_project(project_id) is None:
        return jsonify({'error': 'project not found'}), 404
    roster = [
        {'session_id': sid, 'label': _short_label(sid, s),
         'task': (s.get('task') or '')[:200], 'status': s.get('status'),
         'started_at': s.get('started_at', ''),
         'started_relative': time_ago(s.get('started_at', ''))}
        for sid, s in _live_sessions(project_id)
    ]
    return jsonify({'enabled': enabled(), 'live_agents': roster})


@bp.route('/api/project/<project_id>/coord/stream')
def coord_stream(project_id):
    """SSE stream of coordination events for the UI."""
    if load_project(project_id) is None:
        return jsonify({'error': 'project not found'}), 404
    queue: list = []
    with _sse_lock:
        _sse_queues.setdefault(project_id, []).append(queue)

    def generate():
        try:
            tick = 0
            while True:
                while queue:
                    yield f"data: {json.dumps(queue.pop(0))}\n\n"
                tick += 1
                if tick % 50 == 0:
                    yield ": heartbeat\n\n"
                _time.sleep(0.3)
        finally:
            with _sse_lock:
                qs = _sse_queues.get(project_id, [])
                if queue in qs:
                    qs.remove(queue)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})
