"""Project Topics Digest — synthesize deduplicated topics from a project's chats.

A LOW-EFFORT agent review: gather per-chat signals (opening ask + Scribe recap +
turns + status) and run ONE cheap-model pass that clusters them into distinct
TOPICS. A subject discussed across many chats collapses to a single entry with a
gist — the unit the owner actually thinks in, not "one card per chat".

- Cache:  DATA_DIR/<pid>_topics.json        (topics + generated_at)
- State:  DATA_DIR/<pid>_topic_state.json   (per-topic done/archived, by slug)
  BOTH suffixes are registered in project_routes.EXCLUDED_SIDECAR_SUFFIXES so
  load_projects() never mistakes them for a project (the load-bearing DATA_DIR
  pollution rule).

Best-effort posture (same as Scribe/Distiller): a synthesis failure returns the
prior cache + an error field, and NEVER breaks the app.
"""

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

bp = Blueprint('topics_routes', __name__)

# ── wired by server.py (None until wire() runs) ──────────────────────────────
_scribe_call: Callable[..., Any] = None   # type: ignore[assignment]  # (model, instruction, body) -> text
_load_project: Callable[..., Any] = None  # type: ignore[assignment]
_recent_transcripts: Callable[..., Any] = None  # type: ignore[assignment]  # (path, limit)
_load_agent_log: Callable[..., Any] = None      # type: ignore[assignment]  # (project_id)
_DATA_DIR: Any = None
_MODEL = 'haiku'

_lock = threading.Lock()


def wire(*, scribe_call_fn, load_project_fn, recent_transcripts_fn,
         load_agent_log_fn, data_dir, model='haiku'):
    global _scribe_call, _load_project, _recent_transcripts, _load_agent_log, _DATA_DIR, _MODEL
    _scribe_call = scribe_call_fn
    _load_project = load_project_fn
    _recent_transcripts = recent_transcripts_fn
    _load_agent_log = load_agent_log_fn
    _DATA_DIR = Path(data_dir)
    _MODEL = model or 'haiku'


def _topics_path(pid):
    return _DATA_DIR / f'{pid}_topics.json'


def _state_path(pid):
    return _DATA_DIR / f'{pid}_topic_state.json'


def _slug(title):
    s = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')
    return s[:60] or 'topic'


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def _atomic_write(path, obj):
    try:
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
        tmp.replace(path)
    except Exception:
        pass


def _gather_signals(project_id, limit=50):
    """Per-chat signals for the synthesizer, with trivial one-turn noise dropped."""
    p = _load_project(project_id)
    if not p:
        return []
    convos = _recent_transcripts(p.get('project_path', ''), limit=limit) or []
    log_by_csid = {}
    try:
        for e in _load_agent_log(project_id):
            csid = e.get('claude_session_id', '')
            if csid and csid not in log_by_csid:
                log_by_csid[csid] = e
    except Exception:
        pass
    sigs = []
    for c in convos:
        csid = c.get('session_id', '') or ''
        ask = ' '.join((c.get('first_user') or '').split())
        last = ' '.join((c.get('last_user') or '').split())
        turns = c.get('turns', 0) or 0
        if not ask and not last:
            continue
        if turns < 2 and len(ask) < 12:        # trivial "ok" / stray fragments
            continue
        summary = (log_by_csid.get(csid, {}) or {}).get('summary', '') or ''
        ts = ''
        try:
            if c.get('mtime'):
                ts = datetime.fromtimestamp(c['mtime'], tz=timezone.utc).isoformat()
        except Exception:
            ts = ''
        sigs.append({
            'csid': csid,
            'ask': ask[:220],
            'summary': summary[:220],
            'turns': turns,
            # display label carried into the topic cache so the UI never has to
            # resolve a csid against its (limited) loaded conversation list.
            'label': (ask or last or '(chat)')[:90],
            'ts': ts,
        })
    return sigs


_INSTRUCTION = (
    "You review a software project's chat history and produce a concise TOPIC "
    "digest for the project owner. Input (stdin) is a JSON array of chats, each "
    "with: id (a short handle like \"c3\"), ask (the opening request), summary "
    "(a one-line recap if any), turns (message count).\n\n"
    "Cluster the chats into DISTINCT TOPICS — a subject / feature / issue / "
    "effort. A subject discussed across several chats MUST become ONE topic; "
    "merge aggressively and never emit two topics for the same subject. Collapse "
    "routine autonomous or scheduled maintenance runs (night-shift, cost-review, "
    "soak-checks, watchers, steward cycles) into a SINGLE topic titled "
    "\"Autonomous agents\" with status \"automated\".\n\n"
    "For each topic output:\n"
    "- title: 2-6 words, specific (e.g. \"In-app browser pane\", not \"UI work\")\n"
    "- gist: ONE sentence on the current state / what it is about\n"
    "- status: \"open\" (active or unresolved), \"resolved\" (looks done), or "
    "\"automated\" (recurring background work)\n"
    "- chat_ids: array of the id handles (e.g. [\"c1\",\"c7\"]) of the chats in "
    "this topic. EVERY input chat must appear in exactly one topic — copy the "
    "id values verbatim.\n\n"
    "Return ONLY a JSON object {\"topics\": [ ... ]} — no markdown, no prose. "
    "Order by importance: unresolved/active first, automated last. Aim for 6-15 "
    "topics."
)


def _synthesize(sigs):
    # Hand the model SHORT ids (c0, c1, …) not the 36-char session UUIDs — LLMs
    # mangle long ids, which silently zeroed every topic's chat list. Map back.
    idmap = {}
    meta = {}          # csid -> {label, ts} for the UI (no client-side lookup needed)
    payload = []
    for i, s in enumerate(sigs):
        sid = f'c{i}'
        idmap[sid] = s['csid']
        meta[s['csid']] = {'label': s.get('label', ''), 'ts': s.get('ts', '')}
        payload.append({'id': sid, 'ask': s['ask'], 'summary': s['summary'], 'turns': s['turns']})
    raw = _scribe_call(_MODEL, _INSTRUCTION, json.dumps(payload, ensure_ascii=False))
    txt = (raw or '').strip()
    m = re.search(r'\{.*\}', txt, re.S)       # tolerate ```json fences / stray prose
    if m:
        txt = m.group(0)
    data = json.loads(txt)
    topics = data.get('topics', []) if isinstance(data, dict) else []
    out, seen = [], set()
    for t in topics:
        title = str(t.get('title', '')).strip()
        if not title:
            continue
        slug = _slug(title)
        if slug in seen:
            continue
        seen.add(slug)
        status = t.get('status', 'open')
        if status not in ('open', 'resolved', 'automated'):
            status = 'open'
        ids = t.get('chat_ids') or t.get('chat_csids') or []
        csids = [idmap[x] for x in ids if x in idmap]
        out.append({
            'id': slug,
            'title': title[:80],
            'gist': str(t.get('gist', '')).strip()[:280],
            'status': status,
            'chat_csids': csids,
            'chat_count': len(csids),
            'chats': [{'csid': c, 'label': meta.get(c, {}).get('label', ''),
                       'ts': meta.get(c, {}).get('ts', '')} for c in csids],
        })
    return out


def _overlay_state(project_id, topics):
    state = _load_json(_state_path(project_id), {})
    for t in topics:
        t['user_state'] = (state.get(t['id']) or {}).get('state', 'open')
    return topics


@bp.route('/api/project/<project_id>/topics', methods=['GET'])
def get_topics(project_id):
    cache = _load_json(_topics_path(project_id), None)
    if not cache:
        return jsonify({'topics': [], 'generated_at': None, 'stale': True})
    topics = _overlay_state(project_id, cache.get('topics', []))
    return jsonify({'topics': topics, 'generated_at': cache.get('generated_at'),
                    'chat_count': cache.get('chat_count'), 'stale': False})


@bp.route('/api/project/<project_id>/topics/refresh', methods=['POST'])
def refresh_topics(project_id):
    """Run the cheap-model synthesis now and cache it. Synchronous (the caller
    shows a spinner); best-effort — on failure returns the prior cache."""
    with _lock:
        try:
            sigs = _gather_signals(project_id)
        except Exception as e:
            return jsonify({'topics': [], 'generated_at': None,
                            'error': f'gather failed: {e}'}), 200
        if not sigs:
            return jsonify({'topics': [], 'generated_at': None, 'error': 'no chats yet'}), 200
        try:
            topics = _synthesize(sigs)
        except Exception as e:
            prev = _load_json(_topics_path(project_id), None) or {}
            return jsonify({'topics': _overlay_state(project_id, prev.get('topics', [])),
                            'generated_at': prev.get('generated_at'),
                            'error': f'synthesis failed: {e}'}), 200
        cache = {'topics': topics, 'chat_count': len(sigs),
                 'generated_at': datetime.now(timezone.utc).isoformat()}
        _atomic_write(_topics_path(project_id), cache)
        return jsonify({'topics': _overlay_state(project_id, topics),
                        'generated_at': cache['generated_at'],
                        'chat_count': len(sigs), 'stale': False})


def _open_backlog(project_id):
    p = _load_project(project_id) or {}
    return [{'id': i.get('id'), 'text': (i.get('text') or '').strip()[:200]}
            for i in (p.get('backlog') or [])
            if i.get('status') != 'done' and i.get('id') and (i.get('text') or '').strip()]


def _done_topics(project_id):
    cache = _load_json(_topics_path(project_id), None) or {}
    state = _load_json(_state_path(project_id), {})
    out = []
    for t in cache.get('topics', []):
        if (state.get(t['id']) or {}).get('state') == 'done':
            out.append({'title': t.get('title', ''), 'gist': t.get('gist', '')})
    return out


_SWEEP_INSTRUCTION = (
    "You match COMPLETED work topics against a project's OPEN backlog. Input "
    "(stdin) is a JSON object {\"done_topics\": [{title, gist}], \"backlog\": "
    "[{id, text}]}.\n\n"
    "For each backlog item that is clearly addressed / completed by one of the "
    "done topics, output a match. Be CONSERVATIVE: only match when the item is "
    "genuinely covered by a done topic; when unsure, OMIT it. Never match an "
    "item to a topic it only loosely relates to.\n\n"
    "For each match output: id (the backlog id, copied verbatim), topic (the "
    "matching topic title), confidence (\"high\"|\"medium\"|\"low\"), reason "
    "(one short clause on why it's covered).\n\n"
    "Return ONLY {\"matches\": [ ... ]} — no markdown, no prose. Omit items with "
    "no clear match; an empty list is a valid answer."
)


def _match_backlog(done_topics, backlog):
    body = json.dumps({'done_topics': done_topics, 'backlog': backlog}, ensure_ascii=False)
    raw = _scribe_call(_MODEL, _SWEEP_INSTRUCTION, body)
    txt = (raw or '').strip()
    mm = re.search(r'\{.*\}', txt, re.S)
    if mm:
        txt = mm.group(0)
    data = json.loads(txt)
    matches = data.get('matches', []) if isinstance(data, dict) else []
    valid = {b['id']: b['text'] for b in backlog}
    out = []
    for mt in matches:
        bid = mt.get('id')
        if bid not in valid:
            continue
        conf = mt.get('confidence', 'medium')
        if conf not in ('high', 'medium', 'low'):
            conf = 'medium'
        out.append({'id': bid, 'text': valid[bid], 'topic': str(mt.get('topic', ''))[:80],
                    'confidence': conf, 'reason': str(mt.get('reason', ''))[:160]})
    return out


@bp.route('/api/project/<project_id>/topics/backlog-sweep', methods=['POST'])
def backlog_sweep(project_id):
    """PROPOSE (never close) backlog items covered by topics the user marked
    done. The caller confirms, then closes each via the existing
    PATCH /backlog/<id> {status:done}. Best-effort, cheap-model."""
    with _lock:
        done = _done_topics(project_id)
        backlog = _open_backlog(project_id)
    if not done:
        return jsonify({'matches': [], 'note': 'No topics marked done yet — mark a topic done first.'})
    if not backlog:
        return jsonify({'matches': [], 'note': 'No open backlog items.'})
    try:
        matches = _match_backlog(done, backlog)
    except Exception as e:
        return jsonify({'matches': [], 'error': f'sweep failed: {e}'}), 200
    return jsonify({'matches': matches, 'done_topics': len(done), 'open_backlog': len(backlog)})


@bp.route('/api/project/<project_id>/topics/<topic_id>/state', methods=['POST'])
def set_topic_state(project_id, topic_id):
    """Mark a topic done / archived / open (open clears it). Persists by slug so
    it survives re-synthesis."""
    data = request.get_json(silent=True) or {}
    new = data.get('state', 'open')
    if new not in ('open', 'done', 'archived'):
        return jsonify({'error': 'bad state'}), 400
    with _lock:
        state = _load_json(_state_path(project_id), {})
        if new == 'open':
            state.pop(topic_id, None)
        else:
            state[topic_id] = {'state': new, 'at': datetime.now(timezone.utc).isoformat()}
        _atomic_write(_state_path(project_id), state)
    return jsonify({'ok': True, 'state': new})
