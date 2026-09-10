"""The Desk — routes. Spec: `docs/THE_DESK_SPEC.md`.

The Desk is a workspace PEER TO THE FLOOR, reached from the sidebar, not a tab
inside each project — so these routes are `/api/desk/...`, not
`/api/project/<id>/...`. That is not cosmetic. A story about Clayrune's
scheduler and a story about the engulfing scanner come from different projects
and go out under the same voice, on the same calendar, against the same
audience; splitting that across project modals makes the calendar unviewable
and the voice incoherent. The per-project Social tab survives as a FILTERED
VIEW of the queue, which is why the queue routes stay where they are.

Four surfaces, and every route below serves one of them:

  BOARD     GET/POST/PATCH/DELETE /api/desk/campaigns[/<id>]
  QUEUE     (unchanged — /api/project/<id>/social/queue, five existing routes)
  CALENDAR  GET /api/desk/ledger  (scheduled + sent)
  LEDGER    GET /api/desk/ledger, POST /api/desk/ledger/<id>/outcome

Plus the two stores that feed them rather than being a surface themselves:
  GET  /api/desk/signals            — the feed, the thing a prompt box cannot have
  GET/PATCH /api/desk/voices[/<n>]  — the editable voice, the differentiator

WHAT IS DELIBERATELY ABSENT: a publish route. `POST /api/desk/ledger` records
that a human already released something; nothing here makes an outbound call.
The approval gate is a platform TERM (Pinterest requires per-item human choice,
YouTube prior express consent) and not our caution, and the March-2026 Meta
incident is what happens when a gate is *expected* but not *enforced* — the
agent posted anyway. Publishing, when it lands, goes in its own module behind
an explicit human release action, the way automation_routes.accept is the one
bridge to the scheduler.
"""
from typing import Callable, Optional

from flask import Blueprint, jsonify, request

from mc import desk as _desk
from mc import desk_brief as _brief
from mc import desk_harvest as _harvest
from mc.core import _log

bp = Blueprint('desk_routes', __name__)

# -- wired by server.py (see wire()) ------------------------------------------
load_projects: Callable[[], list] = None  # type: ignore[assignment]
load_project: Callable[[str], Optional[dict]] = None  # type: ignore[assignment]
# Late-bound so this module never imports agent_routes: the Desk is downstream
# of dispatch, and a cycle here would be a restart-time import error rather than
# a runtime one. Same shape as automation_routes' bridge to the scheduler, and
# for the same reason — one dispatch engine, not two.
# Returns the new session id.
dispatch_agent: Optional[Callable[..., str]] = None


def wire(*, load_projects_fn=None, load_project_fn=None, dispatch_fn=None,
         store_path=None, signals_path=None):
    global load_projects, load_project, dispatch_agent
    if load_projects_fn is not None:
        load_projects = load_projects_fn
        _harvest.wire(load_projects_fn=load_projects_fn)
    if load_project_fn is not None:
        load_project = load_project_fn
    if dispatch_fn is not None:
        dispatch_agent = dispatch_fn
    if store_path is not None:
        _desk.STORE_PATH = store_path
    if signals_path is not None:
        _desk.SIGNALS_PATH = signals_path


def _int_arg(name: str, default: int, *, lo: int = 1, hi: int = 1000) -> int:
    try:
        return max(lo, min(hi, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


# ── Signal feed ──────────────────────────────────────────────────────────────

@bp.route('/api/desk/signals', methods=['GET'])
def list_signals():
    """?project_id= &limit= &min_score= &unconsumed=1"""
    min_score = request.args.get('min_score')
    try:
        min_score_f = float(min_score) if min_score is not None else None
    except ValueError:
        return jsonify({'error': 'min_score must be a number'}), 400
    return jsonify(_desk.list_signals(
        project_id=request.args.get('project_id'),
        limit=_int_arg('limit', 200),
        min_score=min_score_f,
        unconsumed_only=request.args.get('unconsumed') in ('1', 'true'),
        sort='score' if request.args.get('sort') == 'score' else 'recent',
    ))


@bp.route('/api/desk/signals/harvest', methods=['POST'])
def harvest():
    """Fill the feed from what the projects actually did — git log + shipped backlog.

    Reads local state only; `tests/test_desk_harvest.py` asserts no network.
    Idempotent by `ref`, so calling it twice does not duplicate the feed, and
    calling it after a restore-point rollback does not re-import the world.
    """
    d = request.get_json(silent=True) or {}
    pid = d.get('project_id')
    if pid:
        if load_project is None:
            return jsonify({'error': 'not wired'}), 503
        p = load_project(pid)
        if not p:
            return jsonify({'error': 'project not found'}), 404
        return jsonify(_harvest.harvest_all([p]))
    return jsonify(_harvest.harvest_all())


@bp.route('/api/desk/signals', methods=['POST'])
def add_signal():
    d = request.get_json(silent=True) or {}
    if not d.get('project_id') or not d.get('summary'):
        return jsonify({'error': 'project_id and summary are required'}), 400
    entry = _desk.append_signal(
        d['project_id'], d.get('kind') or 'note', d['summary'],
        ref=d.get('ref'), detail=d.get('detail'),
        story_score=d.get('story_score'), occurred_at=d.get('occurred_at'))
    return jsonify(entry), 201


# ── Voice profiles ───────────────────────────────────────────────────────────

@bp.route('/api/desk/voices', methods=['GET'])
def list_voices():
    return jsonify(_desk.list_voices(request.args.get('project_id')))


@bp.route('/api/desk/voices/<name>', methods=['GET'])
def get_voice(name):
    try:
        return jsonify(_desk.get_voice(name))
    except ValueError as e:
        return jsonify({'error': str(e)}), 404


@bp.route('/api/desk/voices/<name>', methods=['PATCH'])
def update_voice(name):
    try:
        return jsonify(_desk.update_voice(name, request.get_json(silent=True) or {}))
    except ValueError as e:
        return jsonify({'error': str(e)}), 404


@bp.route('/api/desk/voices/<name>/edit', methods=['POST'])
def record_edit(name):
    """Learn from a human edit to a draft.

    Called on SAVE of an edited draft. The 2026-09-09 field scan could not
    verify a closed learning loop in any surveyed product, and Typefully — the
    closest — infers voice from post history rather than exposing it. An edit is
    the human saying, in their own words, what the right output was, and today
    it is thrown away on save. Returns 200 with `{"learned": false}` when the
    edit was cosmetic, which is a real answer and not an error.
    """
    d = request.get_json(silent=True) or {}
    try:
        r = _desk.record_edit(name, d.get('before') or '', d.get('after') or '',
                              draft_id=d.get('draft_id'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 404
    return jsonify({'learned': r is not None, 'rewrite': r})


@bp.route('/api/desk/voices/<name>/brief', methods=['GET'])
def voice_brief(name):
    """The prompt text a drafting agent should be handed."""
    try:
        return jsonify({'voice': name,
                        'brief': _desk.voice_brief(name, recent=_int_arg('recent', 12, hi=200))})
    except ValueError as e:
        return jsonify({'error': str(e)}), 404


# ── Campaign board ───────────────────────────────────────────────────────────

@bp.route('/api/desk/campaigns', methods=['GET'])
def list_campaigns():
    return jsonify(_desk.list_campaigns(state=request.args.get('state')))


@bp.route('/api/desk/campaigns', methods=['POST'])
def create_campaign():
    d = request.get_json(silent=True) or {}
    if not d.get('title') or not d.get('thesis'):
        # A campaign without a thesis is a folder. The thesis is what makes the
        # Board answer "why is this running now" instead of listing pending items.
        return jsonify({'error': 'title and thesis are required'}), 400
    try:
        camp = _desk.create_campaign(
            d['title'], d['thesis'],
            voices=d.get('voices') or d.get('voice'),
            agenda=d.get('agenda') or '', project_ids=d.get('project_ids') or [],
            planned=d.get('planned') or [])
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(camp), 201


@bp.route('/api/desk/campaigns/<campaign_id>', methods=['PATCH'])
def update_campaign(campaign_id):
    try:
        camp = _desk.update_campaign(campaign_id, request.get_json(silent=True) or {})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if camp is None:
        return jsonify({'error': 'campaign not found'}), 404
    return jsonify(camp)


@bp.route('/api/desk/campaigns/<campaign_id>', methods=['DELETE'])
def delete_campaign(campaign_id):
    if not _desk.delete_campaign(campaign_id):
        return jsonify({'error': 'campaign not found'}), 404
    return jsonify({'ok': True})


# ── Story ledger ─────────────────────────────────────────────────────────────

@bp.route('/api/desk/ledger', methods=['GET'])
def list_ledger():
    return jsonify(_desk.list_ledger(
        limit=_int_arg('limit', 100),
        platform=request.args.get('platform'),
        project_id=request.args.get('project_id')))


@bp.route('/api/desk/ledger', methods=['POST'])
def record_published():
    """Record that a human RELEASED something. Not a publish path — see module docstring."""
    d = request.get_json(silent=True) or {}
    if not d.get('platform') or not d.get('body'):
        return jsonify({'error': 'platform and body are required'}), 400
    voice = d.get('voice') or _desk.default_voice() or ''
    if not _desk.is_voice(voice):
        return jsonify({'error': f'unknown voice {voice!r}'}), 400
    entry = _desk.record_published(
        platform=d['platform'], voice=voice, body=d['body'],
        signal_id=d.get('signal_id'), campaign_id=d.get('campaign_id'),
        project_id=d.get('project_id'), url=d.get('url'),
        published_at=d.get('published_at'))
    if d.get('signal_id'):
        _desk.mark_signal_consumed(d['signal_id'], entry['id'])
    return jsonify(entry), 201


@bp.route('/api/desk/ledger/<post_id>/outcome', methods=['POST'])
def record_outcome(post_id):
    row = _desk.record_outcome(post_id, request.get_json(silent=True) or {})
    if row is None:
        return jsonify({'error': 'post not found'}), 404
    return jsonify(row)


# ── Drafting ─────────────────────────────────────────────────────────────────

@bp.route('/api/desk/draft', methods=['POST'])
def draft():
    """Turn a signal into a PENDING draft, by dispatching the roster's writer.

    `{signal_id, voice?, campaign_id?}` -> a real agent session for **Posy**
    (`social-media-strategist`), briefed by `mc.desk_brief`, which POSTs its
    draft onto the project's existing social queue.

    Two things this route does NOT do, and both are deliberate:

      * It does not generate. The Desk is the office, not a persona — Posy holds
        the platform judgement, per the standing position of 2026-08-29 that
        declined a separate marketing agent.
      * It does not publish, and it cannot widen its own permission to. The
        draft lands as `pending` and a human releases it. This is a platform
        TERM, not our caution.

    It DOES refuse up front when the signal has already been consumed, because
    the cheapest re-announcement to prevent is the one that never gets drafted.
    """
    d = request.get_json(silent=True) or {}
    sig_id = d.get('signal_id')
    if not sig_id:
        return jsonify({'error': 'signal_id is required'}), 400
    voice = d.get('voice') or _desk.default_voice() or ''
    if not _desk.is_voice(voice):
        return jsonify({'error': f'unknown voice {voice!r}'}), 400

    signal = next((s for s in _desk.list_signals(limit=100000)
                   if s.get('id') == sig_id), None)
    if signal is None:
        return jsonify({'error': 'signal not found'}), 404
    if signal.get('consumed_by'):
        # Already drafted from. Say so rather than quietly making a second one —
        # duplicate drafts off one event is how the queue becomes noise.
        return jsonify({'error': 'signal already used',
                        'consumed_by': signal['consumed_by']}), 409

    campaign = None
    if d.get('campaign_id'):
        campaign = next((c for c in _desk.list_campaigns()
                         if c['id'] == d['campaign_id']), None)
        if campaign is None:
            return jsonify({'error': 'campaign not found'}), 404

    pid = signal.get('project_id')
    project = load_project(pid) if (load_project and pid) else None
    if project is None:
        return jsonify({'error': f'project {pid!r} not found'}), 404

    brief = _brief.build_brief(signal, voice=voice, campaign=campaign,
                               project_name=project.get('name'))
    if dispatch_agent is None:
        # Unwired (or a test): hand back the brief rather than pretending. A
        # caller that believes a draft was requested when none was is exactly
        # the substitution failure the agent rules forbid.
        return jsonify({'error': 'dispatch not wired', 'brief': brief}), 503

    try:
        # strict_character: a fresh, explicit pick, so an unresolvable Posy must
        # REFUSE rather than silently run personaless (MC-925). A draft written
        # in the default agent's voice, landing on the queue looking like hers,
        # is exactly the substitution the agent rules forbid.
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Draft a {_brief.platform_for(voice)} post: '
                         f'{(signal.get("summary") or "")[:70]}',
            character='global:social-media-strategist',
            source='agent', strict_character=True)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[desk] draft dispatch failed for {sig_id}: {e}')
        return jsonify({'error': f'dispatch failed: {e}'}), 502

    return jsonify({'ok': True, 'signal_id': sig_id, 'voice': voice,
                    'platform': _brief.platform_for(voice),
                    'project_id': pid, 'session_id': session_id}), 202


# ── Triage: Posy decides what is worth saying ────────────────────────────────

def _running_campaign() -> dict | None:
    running = [c for c in _desk.list_campaigns() if c.get('state') == 'running']
    return running[0] if len(running) == 1 else None


@bp.route('/api/desk/triage', methods=['POST'])
def triage():
    """Hand the unruled feed to Posy and ask which items deserve a post.

    This is the seat `score_signal` was occupying. A keyword regex cannot tell a
    shipped feature from a chore containing the word "shipped", and it cannot
    explain itself — so the human ended up reading all 120 rows, which is the
    thing this whole surface exists to avoid.

    Signals the human has already ruled on are withheld, so a dismissal is not
    re-offered next pass. Posy POSTs her picks to /api/desk/proposals.
    """
    d = request.get_json(silent=True) or {}
    voices = _desk.voice_names()
    if not voices:
        return jsonify({'error': 'create a voice first'}), 409

    pool = [s for s in _desk.list_signals(limit=400, unconsumed_only=True, sort='score')
            if not _desk.signal_is_ruled_on(s['id'])][:_int_arg('pool', 60, hi=200)]
    if not pool:
        return jsonify({'ok': True, 'nothing_to_triage': True,
                        'reason': 'every signal is already used or ruled on'}), 200

    camp = _running_campaign()
    brief = _brief.build_triage_brief(
        pool, voices=voices, campaign=camp,
        max_picks=max(1, min(10, int(d.get('max_picks') or 5))))

    # Triage runs against a project only because dispatch needs one to live in.
    # Its subject is every project's feed, which is the point of the Desk.
    pid = d.get('project_id') or (pool[0].get('project_id') if pool else None)
    project = load_project(pid) if (load_project and pid) else None
    if project is None:
        return jsonify({'error': f'project {pid!r} not found'}), 404
    if dispatch_agent is None:
        return jsonify({'error': 'dispatch not wired', 'brief': brief}), 503

    try:
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Triage {len(pool)} signals — what is worth posting?',
            character='global:social-media-strategist',
            source='agent', strict_character=True)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[desk] triage dispatch failed: {e}')
        return jsonify({'error': f'dispatch failed: {e}'}), 502

    return jsonify({'ok': True, 'considering': len(pool),
                    'campaign': (camp or {}).get('title'),
                    'session_id': session_id}), 202


@bp.route('/api/desk/proposals', methods=['GET'])
def list_proposals():
    """?state=proposed|accepted|dismissed|all"""
    state = request.args.get('state', 'proposed')
    rows = _desk.list_proposals(None if state == 'all' else state)
    # Join the signal in so the human can check the claim without a second call.
    by_id = {s['id']: s for s in _desk.list_signals(limit=100000)}
    for r in rows:
        r['signal'] = by_id.get(r.get('signal_id'))
    return jsonify(rows)


@bp.route('/api/desk/proposals', methods=['POST'])
def add_proposal():
    """Posy calls this, once per pick."""
    d = request.get_json(silent=True) or {}
    if not d.get('signal_id') or not d.get('why'):
        return jsonify({'error': 'signal_id and why are required'}), 400
    voice = d.get('voice') or _desk.default_voice() or ''
    if not _desk.is_voice(voice):
        return jsonify({'error': f'unknown voice {voice!r}'}), 400
    # A proposal must cite a REAL signal — the human checks the claim against it,
    # and an invented id makes that impossible.
    if not any(s['id'] == d['signal_id'] for s in _desk.list_signals(limit=100000)):
        return jsonify({'error': 'no such signal'}), 404
    camp = _running_campaign()
    prop = _desk.add_proposal(d['signal_id'], voice, d['why'],
                              campaign_id=(camp or {}).get('id'))
    if prop is None:
        return jsonify({'ok': True, 'skipped': True,
                        'reason': 'already proposed or already ruled on'}), 200
    return jsonify(prop), 201


@bp.route('/api/desk/proposals/<proposal_id>/accept', methods=['POST'])
def accept_proposal(proposal_id):
    """Accepting IS the instruction to write. One gate, then the Queue's gate.

    Two questions, asked in order and never merged: "is this worth saying"
    (here) and "is this the right way to say it" (releasing the draft).
    """
    prop = _desk.get_proposal(proposal_id)
    if not prop:
        return jsonify({'error': 'proposal not found'}), 404
    if prop.get('state') != 'proposed':
        return jsonify({'error': f"already {prop['state']}"}), 409

    signal = next((s for s in _desk.list_signals(limit=100000)
                   if s['id'] == prop['signal_id']), None)
    if signal is None:
        return jsonify({'error': 'the signal it cites is gone'}), 404

    pid = signal.get('project_id')
    project = load_project(pid) if (load_project and pid) else None
    if project is None:
        return jsonify({'error': f'project {pid!r} not found'}), 404

    camp = next((c for c in _desk.list_campaigns()
                 if c['id'] == prop.get('campaign_id')), None)
    brief = _brief.build_brief(signal, voice=prop['voice'], campaign=camp,
                               project_name=project.get('name'))
    if dispatch_agent is None:
        return jsonify({'error': 'dispatch not wired', 'brief': brief}), 503
    try:
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Draft a {_brief.platform_for(prop["voice"])} post: '
                         f'{(signal.get("summary") or "")[:70]}',
            character='global:social-media-strategist',
            source='agent', strict_character=True)
    except Exception as e:
        _log(f'[desk] accept dispatch failed for {proposal_id}: {e}')
        return jsonify({'error': f'dispatch failed: {e}'}), 502

    _desk.decide_proposal(proposal_id, 'accepted', draft_dispatch=session_id)
    return jsonify({'ok': True, 'session_id': session_id,
                    'platform': _brief.platform_for(prop['voice'])}), 202


@bp.route('/api/desk/proposals/<proposal_id>/dismiss', methods=['POST'])
def dismiss_proposal(proposal_id):
    """A latched no. The signal is never proposed again — see desk.signal_is_ruled_on."""
    prop = _desk.decide_proposal(proposal_id, 'dismissed')
    if prop is None:
        return jsonify({'error': 'proposal not found'}), 404
    return jsonify({'ok': True, 'proposal': prop})


@bp.route('/api/desk/brief', methods=['POST'])
def preview_brief():
    """The brief that WOULD be sent, without dispatching anything.

    Exists so the brief is inspectable — it is the highest-leverage text in the
    system and it should never be a black box Ron cannot read.
    """
    d = request.get_json(silent=True) or {}
    voice = d.get('voice') or _desk.default_voice() or ''
    if not _desk.is_voice(voice):
        return jsonify({'error': f'unknown voice {voice!r}'}), 400
    signal = next((s for s in _desk.list_signals(limit=100000)
                   if s.get('id') == d.get('signal_id')), None)
    if signal is None:
        return jsonify({'error': 'signal not found'}), 404
    campaign = next((c for c in _desk.list_campaigns()
                     if c['id'] == d.get('campaign_id')), None) if d.get('campaign_id') else None
    return jsonify({'brief': _brief.build_brief(signal, voice=voice, campaign=campaign)})


@bp.route('/api/desk/repeat-check', methods=['POST'])
def repeat_check():
    """Have we already said this?

    The ledger's job is not analytics, it is stopping the Desk repeating itself.
    This is the route that earns it: called before a draft reaches the Queue, so
    a re-announcement is caught before it costs Ron credibility with the exact
    B2B audience the field scan says punishes it hardest.
    """
    d = request.get_json(silent=True) or {}
    body = d.get('body') or ''
    if not body.strip():
        return jsonify({'repeat': False, 'matches': []})
    matches = _desk.similar_published(body)
    return jsonify({'repeat': bool(matches), 'matches': matches[:5]})


# ── Board summary ────────────────────────────────────────────────────────────

@bp.route('/api/desk/overview', methods=['GET'])
def overview():
    """One call for the Desk's landing state — what the Board renders."""
    try:
        pending = 0
        if load_projects is not None:
            for p in load_projects():
                pending += int(p.get('social_pending_count') or 0)
    except Exception as e:
        _log(f'[desk] overview could not count pending drafts: {e}')
        pending = 0
    # Sorted by SCORE, not recency: the Board's question is "what is worth
    # saying", and the newest thing that happened is often a chore.
    hot = _desk.list_signals(limit=20, min_score=_desk.STORY_SCORE_FLOOR,
                             unconsumed_only=True, sort='score')
    return jsonify({
        'campaigns': _desk.list_campaigns(),
        'running': len(_desk.list_campaigns(state='running')),
        'pending_drafts': pending,
        'hot_signals': hot,
        'recent_posts': _desk.list_ledger(limit=10),
        'voices': [v['name'] for v in _desk.list_voices()],
    })
