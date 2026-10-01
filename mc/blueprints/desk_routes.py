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
import re
from pathlib import Path
from typing import Callable, Optional

from flask import Blueprint, jsonify, request

from mc import characters as _chars
from mc import desk as _desk
from mc import desk_accounts as _accounts
from mc import desk_brief as _brief
from mc import desk_engines as _engines
from mc import desk_engagement as _engagement
from mc import desk_harvest as _harvest
from mc import desk_pieces as _pieces
from mc import desk_publish as _publish
from mc import desk_retro as _retro
from mc import desk_tick as _tick
from mc import desk_voice_seed as _seed
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.unattended import is_unattended_caller

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
# data/projects — server.py owns the real path (a frozen build resolves it
# elsewhere). The voice seeder needs it to compute the INCOGNITO exclusion,
# so a missing wire has to be visible rather than silently reading everything.
PROJECTS_DIR: Path = Path('data/projects')


def wire(*, load_projects_fn=None, load_project_fn=None, dispatch_fn=None,
         store_path=None, signals_path=None, projects_dir=None, uploads_root=None):
    global load_projects, load_project, dispatch_agent, PROJECTS_DIR
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
    if projects_dir is not None:
        PROJECTS_DIR = Path(projects_dir)
    if uploads_root is not None:
        _pieces.UPLOADS_ROOT = Path(uploads_root)


def _int_arg(name: str, default: int, *, lo: int = 1, hi: int = 1000) -> int:
    try:
        return max(lo, min(hi, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


# ── Agent of choice (R1-A, MC-977 IA revision 2 §5.3) ────────────────────────
#
# Every dispatch below used to hardcode the same character — social-media
# -strategist, always — a silent default no project ever chose. Resolve the
# project's own pick
# instead: `presence.desk_agent` first, a campaign's own `how.agent` second
# (multi-project campaigns, or a caller with no project presence at hand) —
# same precedence as the frontend's `deskAgentRef` (static/js/desk-v1-kit.js).
# A project that never picked anyone gets a 409 naming the project, not a
# silent default — the same substitution the agent rules forbid.

def _desk_agent_ref(project: dict, campaign: Optional[dict] = None) -> Optional[str]:
    presence = _desk.get_presence(project.get('id') or '') or {}
    return presence.get('desk_agent') or ((campaign or {}).get('how') or {}).get('agent') or None


def _pick_agent_error(project: dict):
    name = project.get('name') or project.get('id')
    return jsonify({'error': f'no agent picked for {name} yet — pick who plans for this project',
                    'pick_agent': True, 'project_id': project.get('id')}), 409


def _valid_agent_ref(project: dict, ref: str) -> bool:
    """True if `ref` ("scope:name") resolves to a real character, the same
    check `_resolve_character(strict=True)` makes at dispatch time — so a
    presence PATCH can never store a pick that would 409 every draft/triage/
    accept the moment someone tries to use it."""
    scope, _, name = (ref or '').partition(':')
    scope = (scope or '').strip().lower()
    name = (name or '').strip()
    if scope not in ('project', 'global') or not name:
        return False
    try:
        pp = project.get('project_path') if scope == 'project' else None
        return _chars.read_character(scope, name, project_path=pp,
                                      include_body=False) is not None
    except Exception:
        return False


# ── Presence: who plans for this project (R1-A follow-up) ───────────────────
#
# `_desk_agent_ref`/`_pick_agent_error` above turn a missing pick into a 409
# instead of a silent default — but `upsert_presence` had no HTTP route, so
# nothing in the product could ever CLEAR that 409. These two close the loop:
# the picker the 409 sends the UI to open calls PATCH here.

@bp.route('/api/desk/presence/<project_id>', methods=['GET'])
def get_presence(project_id):
    return jsonify(_desk.get_presence(project_id) or {'project_id': project_id, 'desk_agent': None})


@bp.route('/api/desk/presence/<project_id>', methods=['PATCH'])
def patch_presence(project_id):
    if load_project is None:
        return jsonify({'error': 'not wired'}), 503
    project = load_project(project_id)
    if not project:
        return jsonify({'error': 'project not found'}), 404
    d = request.get_json(silent=True) or {}
    # Only `desk_agent` and `state` are writable here. `upsert_presence` would
    # take any key, including `budget`, whose earmark bounds are enforced
    # elsewhere — this route must not become a way around them.
    if set(d) - {'desk_agent', 'state'}:
        return jsonify({'error': 'only desk_agent and state may be set here'}), 400
    if 'state' in d and d['state'] not in _desk.PROJECT_STATES:
        return jsonify({'error': f"state must be one of {list(_desk.PROJECT_STATES)}"}), 400
    if d.get('desk_agent') is not None:
        ref = d['desk_agent']
        if not isinstance(ref, str) or not _valid_agent_ref(project, ref):
            return jsonify({'error': f'unknown agent {ref!r} — pick one from '
                                      f'/api/characters?project_id={project_id}'}), 400
    if 'state' not in d:
        return jsonify(_desk.upsert_presence(project_id, d))
    # Pausing/resuming a project cascades to its campaigns in the same write
    # (R1-W S1), so the state never lands without them. `desk_agent`, if sent
    # too, is merged first so one request is one outcome.
    if 'desk_agent' in d:
        _desk.upsert_presence(project_id, {'desk_agent': d['desk_agent']})
    result = _desk.set_project_state(project_id, d['state'])
    return jsonify(dict(result['presence'], cascade={
        'campaigns': result['campaigns'], 'changed': result['changed'],
        'held': result['held']}))


# ── Workspace read (R1-W S0, M1) ─────────────────────────────────────────────
#
# One bootstrap call for Desk v1's store (static/js/desk-v1-store.js): projects
# with their presence, campaigns in v1 words, workspace accounts, pieces.
# Read-only; the v1 surfaces switch to it behind the `desk_v1_live` flag.

@bp.route('/api/desk/workspace', methods=['GET'])
def workspace():
    if load_projects is None:
        return jsonify({'error': 'not wired'}), 503
    return jsonify(_desk.v1_workspace(load_projects()))


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


@bp.route('/api/desk/voices/<name>/seed', methods=['POST'])
def seed_voice(name):
    """Seed a voice from how the human already writes to their own agents.

    THE COLD START. `voice_brief` is built out of the human's real edits to real
    drafts, and that is the differentiator — but a fresh voice has none.
    Measured 2026-09-10 on this machine: both voices had 0 rewrites, so the brief
    was one line of register and nothing else. The loop only starts paying after
    ten corrections, which is backwards.

    Ron's framing, and it is the whole design: *"the way a user expresses himself
    in his requests is also part of who he is — is he paying more attention to
    details, more attention to actions, results."* That evidence exists in volume
    before a single draft is written: 21,578 typed messages across 16,272
    transcripts on this box.

    An AGENT characterises it, not a regex, for the same reason triage is an
    agent — word counts cannot tell you what someone ATTENDS to. It is told to
    describe a register and never to quote, because a transcript can contain
    anything the human pasted.
    """
    d = request.get_json(silent=True) or {}
    try:
        _desk.get_voice(name)
    except ValueError as e:
        return jsonify({'error': str(e)}), 404

    try:
        samples = _seed.collect(
            PROJECTS_DIR, limit=max(50, min(600, int(d.get('sample') or 300))))
    except _seed.IncognitoBoundaryUnresolved as e:
        # No partial-credit mode. If we cannot tell which transcripts are
        # private, we read none of them and say so — the UI promises
        # "Incognito sessions are never read" and that has to stay true.
        return jsonify({'error': f'cannot establish the incognito boundary: {e}',
                        'seeded': False}), 409
    if len(samples) < _seed.MIN_SAMPLES:
        # Not an error. A fresh install has no corpus, and saying so beats
        # characterising a voice off four messages and presenting it as learned.
        return jsonify({'ok': True, 'seeded': False, 'samples': len(samples),
                        'reason': f'need at least {_seed.MIN_SAMPLES} typed messages '
                                  f'to characterise a voice; found {len(samples)}'}), 200

    brief = _seed.build_seed_brief(samples, name)

    # Seeding has no signal pool to take a project from, so unlike triage there
    # is no natural home. Any real project will do — dispatch needs one to live
    # in, and the subject is the human, not the project. Pseudo-projects
    # (`_incognito`, `_steward_*`) are skipped: dispatching INTO incognito to
    # characterise a voice is the one place this must never run.
    pid = d.get('project_id')
    if not pid and load_projects:
        real = [p for p in load_projects()
                if not str(p.get('id', '')).startswith('_')]
        pid = real[0].get('id') if real else None
    project = load_project(pid) if (load_project and pid) else None
    if project is None:
        return jsonify({'error': f'project {pid!r} not found'}), 404
    agent_ref = _desk_agent_ref(project)
    if not agent_ref:
        return _pick_agent_error(project)
    if dispatch_agent is None:
        return jsonify({'error': 'dispatch not wired'}), 503

    try:
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Seed the "{name}" voice from {len(samples)} of your own messages',
            character=agent_ref,
            source='agent', strict_character=True)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[desk] voice seed dispatch failed: {e}')
        return jsonify({'error': f'dispatch failed: {e}'}), 502

    return jsonify({'ok': True, 'seeded': True, 'voice': name,
                    'samples': len(samples), 'session_id': session_id}), 202


# ── Platform rules ───────────────────────────────────────────────────────────
#
# The rules a writer is briefed with per platform — char limit, cost, what gets
# demoted. Used to be `desk_brief.PLATFORM_NOTES`, a hardcoded dict with exactly
# two keys; every other platform's brief got `.get(platform, '')`. Same shape
# as the voice routes above, minus the closed-name validation: a platform is
# not a fixed set Ron picks from, it is whatever he actually publishes to.

@bp.route('/api/desk/platforms', methods=['GET'])
def list_platform_rules():
    return jsonify(_desk.list_platform_rules())


@bp.route('/api/desk/platforms/<name>', methods=['GET'])
def get_platform_rules(name):
    rules = _desk.get_platform_rules(name)
    if rules is None:
        # Not a 404 — "no rules yet" is the true, expected state for an
        # unseeded platform, and the UI needs the empty shape to render a form.
        return jsonify({**_desk.empty_platform_rules(name), 'configured': False})
    return jsonify({**rules, 'configured': True})


@bp.route('/api/desk/platforms/<name>', methods=['PATCH'])
def update_platform_rules(name):
    try:
        return jsonify(_desk.update_platform_rules(name, request.get_json(silent=True) or {}))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/desk/platforms/<name>', methods=['DELETE'])
def delete_platform_rules(name):
    if not _desk.delete_platform_rules(name):
        return jsonify({'error': 'no rules set for that platform'}), 404
    return jsonify({'ok': True})


# ── Campaign board ───────────────────────────────────────────────────────────

@bp.route('/api/desk/campaigns', methods=['GET'])
def list_campaigns():
    return jsonify(_desk.list_campaigns(state=request.args.get('state')))


# `?shape=v1` marks a Desk v1 caller (static/js/desk-v1-store.js): the body is
# the v1 campaign object and the answer is the v1 shape, with the state words
# translated HERE while the store keeps the legacy ones (R1-W S1; plan §2.B,
# decision 1). Without it both directions are exactly what they always were, so
# the legacy Desk is untouched.
_CLIENT_CAMPAIGN_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def _wants_v1() -> bool:
    return request.args.get('shape') == 'v1'


def _create_campaign_v1(d: dict):
    """A v1 create is a draft or a proposal, never a started campaign: Start is
    its own gated route, so a body naming any other state is refused rather
    than quietly downgraded. The id may be the client's own (the optimistic
    draft already navigated to it); a taken id is a 409, not an overwrite."""
    body = _desk.v1_campaign_in(d)
    state = body.pop('state', 'proposed')
    if state not in ('draft', 'proposed'):
        return jsonify({'error': 'a new campaign starts as a draft or a proposal'}), 400
    cid = d.get('id')
    if cid is not None and not (isinstance(cid, str) and _CLIENT_CAMPAIGN_ID.match(cid)):
        return jsonify({'error': 'campaign id must be 1-80 letters, digits, - or _'}), 400
    try:
        camp = _desk.create_campaign(
            body.get('title') or '', body.get('thesis') or '',
            voices=body.get('voices') or body.get('voice'),
            agenda=body.get('agenda') or '', project_id=body.get('project_id'),
            plan=body.get('plan'), goal=body.get('goal'), term=body.get('term'),
            how=body.get('how'), map_=body.get('map'), subject=body.get('subject'),
            state=state, campaign_id=cid, voiceless_ok=True)
    except _desk.CampaignExists:
        return jsonify({'error': 'a campaign with that id already exists'}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(_desk.v1_campaign(camp)), 201


@bp.route('/api/desk/campaigns', methods=['POST'])
def create_campaign():
    d = request.get_json(silent=True) or {}
    if _wants_v1():
        return _create_campaign_v1(d)
    if not d.get('title') or not d.get('thesis'):
        # A campaign without a thesis is a folder. The thesis is what makes the
        # Board answer "why is this running now" instead of listing pending items.
        return jsonify({'error': 'title and thesis are required'}), 400
    try:
        camp = _desk.create_campaign(
            d['title'], d['thesis'],
            voices=d.get('voices') or d.get('voice'),
            agenda=d.get('agenda') or '', project_ids=d.get('project_ids') or [],
            planned=d.get('planned') or [], visual=d.get('visual'),
            project_id=d.get('project_id'), plan=d.get('plan'),
            goal=d.get('goal'), term=d.get('term'), how=d.get('how'),
            map_=d.get('map'), subject=d.get('subject'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(camp), 201


@bp.route('/api/desk/campaigns/<campaign_id>', methods=['PATCH'])
def update_campaign(campaign_id):
    d = request.get_json(silent=True) or {}
    v1 = _wants_v1()
    try:
        camp = _desk.update_campaign(campaign_id, _desk.v1_campaign_in(d) if v1 else d,
                                     forbid_start=v1)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if camp is None:
        return jsonify({'error': 'campaign not found'}), 404
    return jsonify(_desk.v1_campaign(camp) if v1 else camp)


# M11 (R1-W S3): the Goal stop's read. Derived on the server, so a figure with no
# data behind it arrives as null, never 0 (see `mc.desk.campaign_results`).
@bp.route('/api/desk/campaigns/<campaign_id>/results', methods=['GET'])
def campaign_results(campaign_id):
    out = _desk.campaign_results(campaign_id)
    if out is None:
        return jsonify({'error': 'campaign not found'}), 404
    return jsonify(out)


# M12 (R1-W S8): the Retro section's read. GET never writes: an interim retro
# never proposes (§10.1) and a closed term's findings are proposed by the POST
# below, which skips any dimension already proposed for that term, so opening
# the page twice cannot add a finding twice.
def _retro_args():
    term = request.args.get('term')
    if term in (None, ''):
        return None, request.args.get('metric') or 'clicks', None
    try:
        return int(term), request.args.get('metric') or 'clicks', None
    except ValueError:
        return None, '', (jsonify({'error': 'term must be a whole number'}), 400)


@bp.route('/api/desk/campaigns/<campaign_id>/retro', methods=['GET'])
def campaign_retro(campaign_id):
    term, metric, bad = _retro_args()
    if bad:
        return bad
    out = _retro.campaign_retro(campaign_id, term, metric=metric)
    if out is None:
        return jsonify({'error': 'campaign not found'}), 404
    return jsonify(out)


@bp.route('/api/desk/campaigns/<campaign_id>/retro', methods=['POST'])
def propose_campaign_retro(campaign_id):
    """Run the closed-term retro and propose its findings (origin 'unattended',
    like every retro finding: only a human click in the Retro section confirms
    one). Refused for a term still running."""
    term, metric, bad = _retro_args()
    if bad:
        return bad
    try:
        out = _retro.propose_campaign_retro(campaign_id, term, metric=metric)
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if out is None:
        return jsonify({'error': 'campaign not found'}), 404
    return jsonify(out)


# The three human approval actions (R1-W S2; plan M6/M7/M8). Each writes
# `camp['approved']`, the record the publisher checks, so each is refused for an
# unattended caller: an agent session may edit a campaign (PATCH) but can never
# approve, start or widen the approval of one. The answer is always the v1 shape.
#
# MC-995: `is_unattended_caller` alone is forgeable (any attended agent's curl can
# carry an Origin header, and manual-chat sessions are exempt by design), so each
# action ALSO requires the retyped dashboard passcode in the body, per call, the
# same `_require_human_passcode` gate the other human-only routes use. With no
# passcode configured it refuses 403 `passcode_required`, as everywhere else.

def _human_only(action: str, data: dict, what: str = 'a campaign'):
    if is_unattended_caller():
        return jsonify({'error': f'this action needs a human: an unattended agent session '
                                 f'cannot {action} {what}'}), 403
    return _require_human_passcode(data)


def _approval_action(campaign_id: str, action: str, fn, data: dict, **kw):
    refused = _human_only(action, data)
    if refused:
        return refused
    try:
        camp = fn(campaign_id, **kw)
    except _desk.ApprovalRefused as e:
        return jsonify({'error': str(e), 'problems': e.problems}), 409
    if camp is None:
        return jsonify({'error': 'campaign not found'}), 404
    return jsonify(_desk.v1_campaign(camp))


@bp.route('/api/desk/campaigns/<campaign_id>/start', methods=['POST'])
def start_campaign(campaign_id):
    d = request.get_json(silent=True) or {}
    return _approval_action(campaign_id, 'start', _desk.start_campaign, d,
                            policy_record=d.get('policy_record'))


@bp.route('/api/desk/campaigns/<campaign_id>/approve', methods=['POST'])
def approve_campaign(campaign_id):
    d = request.get_json(silent=True) or {}
    return _approval_action(campaign_id, 'approve', _desk.approve_campaign, d)


@bp.route('/api/desk/campaigns/<campaign_id>/renew', methods=['POST'])
def renew_campaign(campaign_id):
    d = request.get_json(silent=True) or {}
    return _approval_action(campaign_id, 'renew', _desk.renew_campaign, d)


# M9. Ask the campaign's picked agent to suggest what / when / where. 202 with the
# session id; the agent answers through M10 below. Nothing is started or approved.
@bp.route('/api/desk/campaigns/<campaign_id>/suggest', methods=['POST'])
def suggest_for_campaign(campaign_id):
    d = request.get_json(silent=True) or {}
    camp = next((c for c in _desk.list_campaigns() if c.get('id') == campaign_id), None)
    if camp is None:
        return jsonify({'error': 'campaign not found'}), 404
    pid = camp.get('project_id')
    project = load_project(pid) if (load_project and pid) else None
    if project is None:
        return jsonify({'error': 'pick a project for this campaign first: the agent works inside one'}), 409
    agent_ref = _desk_agent_ref(project, camp)
    if not agent_ref:
        return _pick_agent_error(project)
    if dispatch_agent is None:
        return jsonify({'error': 'dispatch not wired'}), 503
    chosen = (camp.get('plan') or {}).get('accounts') or []
    brief = _brief.build_suggest_brief(
        camp, accounts=[a for a in _accounts.list_accounts() if not chosen or a.get('id') in chosen],
        project_name=project.get('name'), note=_clip(d.get('note'), 1000))
    try:
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Suggest what, when and where for {camp.get("title") or campaign_id}',
            character=agent_ref,
            source='agent', strict_character=True)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[desk] suggest dispatch failed for {campaign_id}: {e}')
        return jsonify({'error': f'dispatch failed: {e}'}), 502
    return jsonify({'ok': True, 'session_id': session_id}), 202


# M10. Where the agent (or a human clearing a list) saves suggestions. Agent-
# callable on purpose: a suggestion is data a human accepts, never a commitment,
# and the store refuses any text that names a limit.
@bp.route('/api/desk/campaigns/<campaign_id>/suggestions', methods=['PUT'])
def put_campaign_suggestions(campaign_id):
    try:
        camp = _desk.set_suggestions(campaign_id, request.get_json(silent=True) or {})
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


# ── Pieces, versions, assets, materials (R1-W S4; plan M13-M18, M21, M22) ─────
#
# Every answer is the v1 piece (`mc.desk_pieces.v1_piece`, the fixture's
# `family` shape). These are reversible data writes, so an agent may make them
# (an agent suggesting a piece is the point); what NO route here can do is
# approve: a version PATCH refuses `approved`/`scheduled`/`sending`/... outright
# (the human approve route is slice S7).

def _piece_call(fn, *args, status=200, **kw):
    try:
        out = fn(*args, **kw)
    except _pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
    return jsonify(out), status


@bp.route('/api/desk/pieces', methods=['GET'])
def list_pieces():
    return jsonify(_pieces.list_pieces(campaign_id=request.args.get('campaign_id') or None))


@bp.route('/api/desk/pieces', methods=['POST'])
def create_piece():
    d = request.get_json(silent=True) or {}
    return _piece_call(_pieces.create_piece, d.get('campaign_id'), d.get('kind'), d.get('title'),
                       piece_id=d.get('id'), body=d.get('body'), word_count=d.get('word_count'),
                       source=d.get('source'), claims=d.get('claims'), status=201)


@bp.route('/api/desk/pieces/<piece_id>', methods=['PATCH'])
def update_piece(piece_id):
    return _piece_call(_pieces.update_piece, piece_id, request.get_json(silent=True) or {})


@bp.route('/api/desk/pieces/<piece_id>', methods=['DELETE'])
def delete_piece(piece_id):
    try:
        found = _pieces.delete_piece(piece_id)
    except _pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
    if not found:
        return jsonify({'error': 'piece not found'}), 404
    return jsonify({'ok': True})


@bp.route('/api/desk/pieces/<piece_id>/versions', methods=['POST'])
def add_piece_version(piece_id):
    d = request.get_json(silent=True) or {}
    return _piece_call(_pieces.add_version, piece_id, d.get('account_id'), body=d.get('body'),
                       version_id=d.get('id'), fmt=d.get('format'), status=201)


@bp.route('/api/desk/pieces/<piece_id>/versions/<version_id>', methods=['PATCH'])
def update_piece_version(piece_id, version_id):
    return _piece_call(_pieces.update_version, piece_id, version_id, request.get_json(silent=True) or {})


# M19. The approval gate, and the one route that can make a version go out. Human
# only, with the retyped dashboard passcode per call (MC-995), exactly like
# start/approve/renew above: an agent can write a draft, it can never approve it.
# `scheduled_at` absent keeps the version's own time (what When saved), an explicit
# null means "now". A future time is `scheduled` and the tick sends it; no time (or
# one already due) is sent here and now, as an attended caller, and the answer is
# the piece as it stands after that: `submitted`, `held` with the reason, `failed`...
@bp.route('/api/desk/pieces/<piece_id>/versions/<version_id>/approve', methods=['POST'])
def approve_piece_version(piece_id, version_id):
    d = request.get_json(silent=True) or {}
    refused = _human_only('approve', d, 'a version')
    if refused:
        return refused
    kw = {'scheduled_at': d['scheduled_at']} if 'scheduled_at' in d else {}
    try:
        return jsonify(_tick.approve_and_send(piece_id, version_id, **kw))
    except _pieces.PieceError as e:
        return jsonify({'error': str(e), 'problems': e.problems}), e.status


# "I posted it": the person says a manual version (or an unknown outcome they
# checked) is live. A statement, not a post, but it writes the story ledger, so it
# is human-only too.
@bp.route('/api/desk/pieces/<piece_id>/versions/<version_id>/posted', methods=['POST'])
def report_version_posted(piece_id, version_id):
    d = request.get_json(silent=True) or {}
    refused = _human_only('mark as posted', d, 'a version')
    if refused:
        return refused
    return _piece_call(_tick.report_posted, piece_id, version_id, url=d.get('url'))


# M20. Ask the campaign's picked agent to revise one version: a style, a selected
# passage, a claim, or the human's own note. 202 with the session id; the agent
# saves the result as the next revision through the plain version PATCH, back in
# `needs_review`. Nothing is approved or sent by this.
_REVISE_STYLES = ('shorter', 'less_technical', 'rephrase')


def _clip(value, n):
    return value.strip()[:n] if isinstance(value, str) and value.strip() else None


@bp.route('/api/desk/pieces/<piece_id>/versions/<version_id>/revise', methods=['POST'])
def revise_piece_version(piece_id, version_id):
    d = request.get_json(silent=True) or {}
    try:
        piece = _pieces.get_stored_piece(piece_id)
    except _pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
    ver = next((v for v in piece.get('versions') or [] if v.get('id') == version_id), None)
    if ver is None:
        return jsonify({'error': 'version not found'}), 404
    if ver.get('state') not in ('drafting', 'needs_review', 'held', 'failed', 'planned', 'blocked'):
        return jsonify({'error': f"this version is {ver.get('state')}: send it back to review before asking for a revision"}), 409
    style, claim_id = d.get('style'), d.get('claim_id')
    note, selection = _clip(d.get('note'), 1000), _clip(d.get('selection'), 2000)
    if style is not None and style not in _REVISE_STYLES and style != 'custom':
        return jsonify({'error': f'style must be one of {", ".join(_REVISE_STYLES)}, or custom with a note'}), 400
    if style == 'custom' and not note:
        return jsonify({'error': 'a custom revision needs a note saying what to change'}), 400
    claim = None
    if claim_id is not None:
        claim = next((c for c in piece.get('claims') or [] if c.get('id') == claim_id), None)
        if claim is None:
            return jsonify({'error': f'unknown claim {claim_id!r}'}), 400
    if style is None and claim is None and not note:
        return jsonify({'error': 'say what to revise: a style, a claim_id, or a note'}), 400
    pid = piece.get('project_id')
    project = load_project(pid) if (load_project and pid) else None
    if project is None:
        return jsonify({'error': f'project {pid!r} not found'}), 404
    camp = next((c for c in _desk.list_campaigns() if c.get('id') == piece.get('campaign_id')), None)
    agent_ref = _desk_agent_ref(project, camp)
    if not agent_ref:
        return _pick_agent_error(project)
    if dispatch_agent is None:
        return jsonify({'error': 'dispatch not wired'}), 503
    acc = next((a for a in _accounts.list_accounts() if a.get('id') == ver.get('account_id')), None)
    brief = _brief.build_revise_brief(
        piece, ver, account=acc, style=None if style == 'custom' else style, claim=claim,
        selection=selection, note=note, voice=(acc or {}).get('voice') or None,
        project_name=project.get('name'))
    try:
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Revise "{piece.get("title") or piece_id}"',
            character=agent_ref,
            source='agent', strict_character=True)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[desk] revise dispatch failed for {piece_id}/{version_id}: {e}')
        return jsonify({'error': f'dispatch failed: {e}'}), 502
    return jsonify({'ok': True, 'session_id': session_id}), 202


# M21. Two bodies: JSON `{path, title?, id?}` attaches a file already under
# data/uploads (a library file); multipart `file` saves an upload into the
# material library's Uploads folder and attaches it.
@bp.route('/api/desk/pieces/<piece_id>/assets', methods=['POST'])
def add_piece_asset(piece_id):
    if request.files.get('file') is not None:
        f = request.files['file']
        return _piece_call(_pieces.save_upload, piece_id, f.filename or '', f.stream,
                           title=request.form.get('title') or None,
                           asset_id=request.form.get('id') or None, status=201)
    d = request.get_json(silent=True) or {}
    return _piece_call(_pieces.add_asset, piece_id, path=d.get('path'), title=d.get('title'),
                       asset_id=d.get('id'), status=201)


@bp.route('/api/desk/pieces/<piece_id>/assets/<asset_id>', methods=['DELETE'])
def remove_piece_asset(piece_id, asset_id):
    return _piece_call(_pieces.remove_asset, piece_id, asset_id)


@bp.route('/api/desk/materials', methods=['GET'])
def materials():
    return _piece_call(_pieces.materials, request.args.get('campaign_id') or None)


# ── Workspace accounts (R1-W S5; plan M2-M5, §2.C) ────────────────────────────
#
# An account is a place the Desk can put a message (`mc/desk_accounts.py`). The
# answer is always the v1 account, whose `publish` is derived from the vault's
# metadata on every read. No route here takes, stores or returns a credential:
# an account names the vault entry it needs, and only a human creates one.
# Create and delete refuse an unattended caller (plan §2 "human-only"); so does
# a read-setting change, which can turn on paid reads (the presence route it
# replaces refused the same way).

def _account_call(fn, *args, status=200, **kw):
    try:
        out = fn(*args, **kw)
    except _accounts.AccountError as e:
        return jsonify({'error': str(e)}), e.status
    return jsonify(out), status


def _account_human_only(action: str):
    if is_unattended_caller():
        return jsonify({'error': f'this action needs a human: an unattended agent session '
                                 f'cannot {action} an account'}), 403
    return None


@bp.route('/api/desk/accounts', methods=['GET'])
def list_accounts():
    return jsonify(_accounts.list_accounts())


@bp.route('/api/desk/accounts', methods=['POST'])
def create_account():
    refused = _account_human_only('add')
    if refused:
        return refused
    d = request.get_json(silent=True) or {}
    return _account_call(_accounts.create_account, d.get('platform'), d.get('identity'),
                         label=d.get('label'), capability=d.get('capability'),
                         voice=d.get('voice'), account_id=d.get('id'), status=201)


@bp.route('/api/desk/accounts/<account_id>', methods=['PATCH'])
def update_account(account_id):
    d = request.get_json(silent=True) or {}
    if ('read_via' in d or 'browser_profile' in d) and is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent '
                                 'session cannot choose how an account is read'}), 403
    prof = d.get('browser_profile')
    if isinstance(prof, str) and prof.strip() and not _PROFILE_NAME.match(prof.strip().lower()):
        return jsonify({'error': 'browser_profile must be a saved profile name '
                                 '(lowercase letters, digits, . - _)'}), 400
    return _account_call(_accounts.update_account, account_id, d)


@bp.route('/api/desk/accounts/<account_id>', methods=['DELETE'])
def delete_account(account_id):
    refused = _account_human_only('remove')
    if refused:
        return refused
    try:
        found = _accounts.delete_account(account_id)
    except _accounts.AccountError as e:
        return jsonify({'error': str(e)}), e.status
    if not found:
        return jsonify({'error': 'account not found'}), 404
    return jsonify({'ok': True})


# ── Generation engines (MC-1019; plan M26/M27) ───────────────────────────────
#
# Backend for the Studio / Video surfaces: which engines exist and whether the
# user has connected them, what a render would cost, and the job itself. The
# credentials are vault entries (`higgsfield`, `gemini-api`, `openai-api`) that
# a human creates; nothing here returns, logs or writes one.
#
# SUBMIT SPENDS THE USER'S MONEY, so it gets the same two gates as Start /
# Approve / Renew: refused for an unattended caller, and the retyped dashboard
# passcode (MC-995) on EVERY call. Estimate and poll spend nothing and take no
# passcode; poll is what downloads the output once a job is ready.

def _engine_refusal(e: '_engines.Refused'):
    body = {'error': str(e), 'code': e.code}
    body.update(e.extra)
    return jsonify(body), e.status


def _not_connected(e: '_engines.NotConnected'):
    return jsonify({'error': e.reason, 'code': 'not_connected', 'engine_id': e.engine_id,
                    'vault_entry': e.vault_entry}), 409


@bp.route('/api/desk/engines', methods=['GET'])
def list_engines():
    return jsonify({'engines': _engines.list_engines(request.args.get('project_id') or None)})


@bp.route('/api/desk/engines/estimate', methods=['POST'])
def estimate_engine_job():
    d = request.get_json(silent=True) or {}
    try:
        return jsonify(_engines.estimate(d, unattended=is_unattended_caller()))
    except _engines.Refused as e:
        return _engine_refusal(e)
    except _engines.NotConnected as e:
        return _not_connected(e)


@bp.route('/api/desk/engines/jobs', methods=['POST'])
def submit_engine_job():
    d = request.get_json(silent=True) or {}
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot submit a render job (it spends money)'}), 403
    refused = _require_human_passcode(d)
    if refused:
        return refused
    try:
        job, replay = _engines.submit(d, unattended=False)
    except _engines.Refused as e:
        return _engine_refusal(e)
    except _engines.NotConnected as e:
        return _not_connected(e)
    return jsonify({'job': job, 'replay': replay}), (200 if replay else 201)


@bp.route('/api/desk/engines/jobs/<job_id>', methods=['GET'])
def get_engine_job(job_id):
    job = _engines.poll(job_id, unattended=is_unattended_caller())
    if job is None:
        return jsonify({'error': 'job not found'}), 404
    return jsonify({'job': job})


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
        published_at=d.get('published_at'),
        piece_id=d.get('piece_id'), format=d.get('format'),
        account=d.get('account'), term=d.get('term'), cost=d.get('cost', 0))
    if d.get('signal_id'):
        _desk.mark_signal_consumed(d['signal_id'], entry['id'])
    return jsonify(entry), 201


@bp.route('/api/desk/ledger/<post_id>/outcome', methods=['POST'])
def record_outcome(post_id):
    """Append one per-post outcome entry (§8 R1-L: `outcomes[]` replaces the
    free-form `outcome` dict)."""
    d = request.get_json(silent=True) or {}
    if not d.get('metric') or 'value' not in d:
        return jsonify({'error': 'metric and value are required'}), 400
    row = _desk.record_outcome(post_id, d['metric'], d['value'],
                               source=d.get('source', 'manual'), at=d.get('at'))
    if row is None:
        return jsonify({'error': 'post not found'}), 404
    return jsonify(row)


# ── Engagement feed (§6, §8 R1-E, §10.7) ─────────────────────────────────────
#
# The inbound side. Reads only — nothing here posts or replies. `poll` is the
# one route that can SPEND (paid platform reads, costed against the project's
# budget), so like the finding state changes it refuses an unattended caller.

@bp.route('/api/desk/engagement', methods=['GET'])
def list_engagement():
    return jsonify(_desk.list_engagement_items(
        project_id=request.args.get('project_id'),
        campaign_id=request.args.get('campaign_id'),
        platform=request.args.get('platform'),
        source=request.args.get('source'),
        state=request.args.get('state'),
        limit=_int_arg('limit', 200)))


@bp.route('/api/desk/engagement/overview', methods=['GET'])
def engagement_overview():
    """Per-project landing bundles. A project nothing reads for comes back
    `status: 'not_connected'` with `null` counts — never `0`."""
    period = request.args.get('period', 'week')
    if period not in ('week', 'month'):
        return jsonify({'error': "period must be 'week' or 'month'"}), 400
    return jsonify(_engagement.overview(period=period))


@bp.route('/api/desk/engagement/<item_id>/read', methods=['POST'])
def mark_engagement_read(item_id):
    row = _desk.mark_engagement_read(item_id)
    if row is None:
        return jsonify({'error': 'engagement item not found'}), 404
    return jsonify(row)


# ── Conversations (R1-W S8, plan M23 / M23b / M24) ───────────────────────────
#
# The reading side above never writes. These are the three writes a person makes
# on one feed row. What may cost or reach a stranger is human-only: Send posts to
# a platform (a retyped dashboard passcode per call, like Start/Approve/Renew),
# and `state`, `assigned_to` and `taken_over` are a person's. An agent may do one
# thing here, `draft`, because a draft is a proposal a human still has to send.

_AGENT_MAY_PATCH = {'draft'}


def _parent_post(item: dict) -> Optional[dict]:
    """The ledger row of OUR post this row answers, or None. `post_id` is only
    set by the reader for a reply that sits under one of our own posts."""
    pid = item.get('post_id')
    if not pid:
        return None
    return next((r for r in _desk.list_ledger(limit=100000) if r.get('id') == pid), None)


@bp.route('/api/desk/engagement/<item_id>', methods=['GET'])
def get_engagement(item_id):
    """One feed row with what the thread view needs next to it: our post it
    answers (None when it is a mention or a discussion), and the draft."""
    item = _desk.get_engagement_item(item_id)
    if item is None:
        return jsonify({'error': 'engagement item not found'}), 404
    parent = _parent_post(item)
    item['parent_post'] = ({'id': parent.get('id'), 'body': parent.get('body'),
                            'url': parent.get('url'), 'published_at': parent.get('published_at'),
                            'platform': parent.get('platform'), 'account': parent.get('account')}
                           if parent else None)
    return jsonify(item)


@bp.route('/api/desk/engagement/<item_id>', methods=['PATCH'])
def patch_engagement(item_id):
    d = request.get_json(silent=True) or {}
    unattended = is_unattended_caller()
    if unattended and set(d) - _AGENT_MAY_PATCH:
        return jsonify({'error': 'an unattended agent session may only save a draft on a '
                                 'conversation: state, assignment and take-over are a '
                                 'person\'s'}), 403
    try:
        row = _desk.update_engagement_item(item_id, d, by='agent' if unattended else 'human')
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    if row is None:
        return jsonify({'error': 'engagement item not found'}), 404
    return jsonify(row)


# Which platforms a reply can go out on from here. The publisher is X-only
# (`desk_publish.publish` refuses anything else), so a LinkedIn or web row has no
# send path yet and says so rather than pretending to.
_REPLY_PLATFORMS = ('x',)


@bp.route('/api/desk/engagement/<item_id>/reply', methods=['POST'])
def send_engagement_reply(item_id):
    """Send a reply for real. Human-only, retyped passcode, one reply per row
    (the publisher keys its receipt on the row, so a double click cannot post
    twice). The text sent is `text` if given, else the saved draft.

    A reply is not gated on its campaign being running: it is a person's own
    click, authorised by the passcode, not a scheduled post toward the cadence a
    campaign approval bounds."""
    d = request.get_json(silent=True) or {}
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot send a reply'}), 403
    refused = _require_human_passcode(d)
    if refused:
        return refused
    item = _desk.get_engagement_item(item_id)
    if item is None:
        return jsonify({'error': 'engagement item not found'}), 404
    if item.get('state') == 'sent':
        return jsonify({'error': 'this reply was already sent'}), 409
    if item.get('taken_over'):
        return jsonify({'error': 'this thread is taken over: resume it before sending'}), 409
    if item.get('platform') not in _REPLY_PLATFORMS:
        return jsonify({'error': f'replies cannot be sent to {item.get("platform")} from here yet: '
                                 'open the post on the platform and reply there',
                        'open_url': item.get('url')}), 409
    text = d.get('text')
    if text is None:
        text = (item.get('draft') or {}).get('text')
    if not isinstance(text, str) or not text.strip():
        return jsonify({'error': 'there is no reply to send: write one or ask for a draft'}), 400
    text = text.strip()
    if len(text) > _desk._ENGAGEMENT_DRAFT_MAX:
        return jsonify({'error': f'a reply is at most {_desk._ENGAGEMENT_DRAFT_MAX} characters'}), 400
    try:
        receipt = _publish.publish(
            {'id': f'reply-{item_id}', 'platform': item['platform'], 'body': text,
             'in_reply_to': item.get('external_id')},
            consumer='desk_reply', project_id=item.get('project_id'), unattended=False)
    except _publish.PublishError as e:
        return jsonify({'error': str(e)}), 502
    row = _desk.record_engagement_reply(item_id, receipt, text)
    return jsonify(row)


@bp.route('/api/desk/engagement/<item_id>/suggest-reply', methods=['POST'])
def suggest_engagement_reply(item_id):
    """Ask the project's picked agent to draft (or redraft) a reply. 202 with the
    session id; the agent saves its draft with PATCH, nothing is sent."""
    d = request.get_json(silent=True) or {}
    item = _desk.get_engagement_item(item_id)
    if item is None:
        return jsonify({'error': 'engagement item not found'}), 404
    if item.get('state') == 'sent':
        return jsonify({'error': 'this reply was already sent'}), 409
    if item.get('taken_over'):
        return jsonify({'error': 'this thread is taken over: resume it before asking for a draft'}), 409
    pid = item.get('project_id')
    project = load_project(pid) if (load_project and pid) else None
    if project is None:
        return jsonify({'error': f'project {pid!r} not found'}), 404
    camp = next((c for c in _desk.list_campaigns() if c.get('id') == item.get('campaign_id')), None)
    agent_ref = _desk_agent_ref(project, camp)
    if not agent_ref:
        return _pick_agent_error(project)
    if dispatch_agent is None:
        return jsonify({'error': 'dispatch not wired'}), 503
    voice = next((v for v in _desk.voice_names() if _brief.platform_for(v) == item.get('platform')), None)
    parent = _parent_post(item)
    note = d.get('note')
    brief = _brief.build_reply_brief(
        item, parent_text=(parent or {}).get('body'), voice=voice, campaign=camp,
        project_name=project.get('name'),
        note=note.strip()[:1000] if isinstance(note, str) and note.strip() else None)
    try:
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Draft a reply to {item.get("author") or "a conversation"}',
            character=agent_ref,
            source='agent', strict_character=True)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[desk] reply-draft dispatch failed for {item_id}: {e}')
        return jsonify({'error': f'dispatch failed: {e}'}), 502
    return jsonify({'ok': True, 'session_id': session_id}), 202


@bp.route('/api/desk/engagement/poll', methods=['POST'])
def poll_engagement():
    """Read replies + per-post metrics for one project. May cost money."""
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human — an unattended agent '
                                 'session cannot run a paid platform read'}), 403
    d = request.get_json(silent=True) or {}
    pid = d.get('project_id')
    if not pid:
        return jsonify({'error': 'project_id is required'}), 400
    return jsonify(_engagement.poll_project(pid))


_PROFILE_NAME = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')


@bp.route('/api/desk/engagement/coverage/<project_id>', methods=['GET'])
def engagement_coverage(project_id):
    """Per-platform read state for one project, for the Presence account rows:
    which route each account is read by and, if it is not being read, why. No
    network call: capability checks only look at the vault / the profile dir."""
    readers = _engagement.readers_for_project(project_id)
    return jsonify({'project_id': project_id, 'coverage': [
        _engagement.platform_coverage(project_id, p, readers.get(p))
        for p in _engagement.project_platforms(project_id)]})


@bp.route('/api/desk/presence/<project_id>/accounts/<channel_id>/read', methods=['PATCH'])
def patch_account_read(project_id, channel_id):
    """How the Desk reads one account's own mentions and post stats:
    `read_via` = `pane` (free, needs a signed-in browser profile, named by
    `browser_profile`) or `api` (paid per read, charged to the budget, needs the
    vault token). The user's choice per account; `api` turns on spend, so like
    `poll` this refuses an unattended caller. `platform` is only needed the
    first time, when the account has no stored record yet."""
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent '
                                 'session cannot choose how an account is read'}), 403
    d = request.get_json(silent=True) or {}
    if set(d) - {'read_via', 'browser_profile', 'platform'}:
        return jsonify({'error': 'only read_via, browser_profile and platform may be set here'}), 400
    prof = d.get('browser_profile')
    if prof is not None and (not isinstance(prof, str)
                             or (prof.strip() and not _PROFILE_NAME.match(prof.strip().lower()))):
        return jsonify({'error': 'browser_profile must be a saved profile name '
                                 '(lowercase letters, digits, . - _)'}), 400
    try:
        acc = _desk.set_account_read_settings(
            project_id, channel_id, platform=d.get('platform'),
            read_via=d.get('read_via'), browser_profile=prof)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(acc)


# ── Playbook / outcome learning loop (§10, MC-977 R1-L) ─────────────────────
#
# §10.5.1: a finding schema has no field that can name an approval bound.
# Structural enforcement, not trust in whoever built the request body: any of
# these keys in a finding state-change body is refused outright, whatever its
# value, so a bound can never ride in on a field named after one.
_BOUND_FIELD_NAMES = {'cadence', 'budget', 'accounts', 'approval', 'end', 'post_cap'}


def _bound_field_violation(d: dict):
    hit = _BOUND_FIELD_NAMES & set(d or {})
    if hit:
        return jsonify({'error': f'field(s) {sorted(hit)} name an approval bound; '
                                 'a finding may never carry one'}), 400
    return None


def _unattended_refusal():
    """§10.5.2 + §10.2 "Only Ron moves a finding between states": every
    state-change route below refuses an unattended caller, the same posture
    `secrets_routes._unattended_refusal` takes for vault writes."""
    return jsonify({'error': 'this action needs a human — an unattended agent '
                             'session cannot confirm, edit, reject, or undo a '
                             'playbook finding'}), 403


@bp.route('/api/desk/retro', methods=['POST'])
def run_retro():
    """Compute a retro over caller-supplied per-dimension evidence (§10.1).
    Code-only and deterministic; this route itself may run unattended (a
    scheduled term-end retro) — proposed findings always land `origin:
    'unattended'` regardless (mc.desk.propose_finding), so this route is NOT
    gated on `is_unattended_caller`, unlike the finding state-change routes."""
    d = request.get_json(silent=True) or {}
    if not d.get('project_id') or not d.get('dimension_arms'):
        return jsonify({'error': 'project_id and dimension_arms are required'}), 400
    result = _retro.run_retro(
        d['project_id'], dimension_arms=d['dimension_arms'],
        account=d.get('account'), metric=d.get('metric', 'clicks'),
        interim=bool(d.get('interim')))
    return jsonify(result)


@bp.route('/api/desk/findings', methods=['GET'])
def list_findings():
    return jsonify(_desk.list_findings(
        project_id=request.args.get('project_id'), state=request.args.get('state')))


@bp.route('/api/desk/findings/<finding_id>', methods=['GET'])
def get_finding(finding_id):
    f = _desk.get_finding(finding_id)
    if f is None:
        return jsonify({'error': 'finding not found'}), 404
    return jsonify(f)


@bp.route('/api/desk/findings/<finding_id>/confirm', methods=['POST'])
def confirm_finding(finding_id):
    if is_unattended_caller():
        return _unattended_refusal()
    d = request.get_json(silent=True) or {}
    violation = _bound_field_violation(d)
    if violation is not None:
        return violation
    f = _desk.confirm_finding(finding_id, edited_text=d.get('edited_text'),
                              decided_by=d.get('decided_by'))
    if f is None:
        return jsonify({'error': 'finding not found'}), 404
    return jsonify(f)


@bp.route('/api/desk/findings/<finding_id>/reject', methods=['POST'])
def reject_finding(finding_id):
    if is_unattended_caller():
        return _unattended_refusal()
    d = request.get_json(silent=True) or {}
    violation = _bound_field_violation(d)
    if violation is not None:
        return violation
    f = _desk.reject_finding(finding_id, decided_by=d.get('decided_by'))
    if f is None:
        return jsonify({'error': 'finding not found'}), 404
    return jsonify(f)


@bp.route('/api/desk/findings/<finding_id>/dont-suggest-again', methods=['POST'])
def dont_suggest_again(finding_id):
    if is_unattended_caller():
        return _unattended_refusal()
    d = request.get_json(silent=True) or {}
    violation = _bound_field_violation(d)
    if violation is not None:
        return violation
    f = _desk.dont_suggest_again(finding_id, decided_by=d.get('decided_by'))
    if f is None:
        return jsonify({'error': 'finding not found'}), 404
    return jsonify(f)


@bp.route('/api/desk/findings/<finding_id>/undo-reject', methods=['POST'])
def undo_reject(finding_id):
    if is_unattended_caller():
        return _unattended_refusal()
    f = _desk.undo_reject(finding_id)
    if f is None:
        return jsonify({'error': 'finding not found or not rejected'}), 404
    return jsonify(f)


@bp.route('/api/desk/findings/<finding_id>/reconfirm', methods=['POST'])
def reconfirm_finding(finding_id):
    """R2-16 (§10.2): Ron's response to a `stale` finding that still holds."""
    if is_unattended_caller():
        return _unattended_refusal()
    d = request.get_json(silent=True) or {}
    violation = _bound_field_violation(d)
    if violation is not None:
        return violation
    f = _desk.reconfirm_finding(finding_id, edited_text=d.get('edited_text'),
                                decided_by=d.get('decided_by'))
    if f is None:
        return jsonify({'error': 'finding not found or not stale'}), 404
    return jsonify(f)


@bp.route('/api/desk/findings/<finding_id>/retire', methods=['POST'])
def retire_finding(finding_id):
    """R2-16 (§10.2): Ron's other response to a `stale` finding — drop it for
    good, distinct from Reject (§10.5.3's durable "no" is for a finding that
    never earned confidence; this one already did)."""
    if is_unattended_caller():
        return _unattended_refusal()
    d = request.get_json(silent=True) or {}
    violation = _bound_field_violation(d)
    if violation is not None:
        return violation
    f = _desk.retire_finding(finding_id, decided_by=d.get('decided_by'))
    if f is None:
        return jsonify({'error': 'finding not found or not stale'}), 404
    return jsonify(f)


# ── Drafting ─────────────────────────────────────────────────────────────────

@bp.route('/api/desk/draft', methods=['POST'])
def draft():
    """Turn a signal into a PENDING draft, by dispatching the roster's writer.

    `{signal_id, voice?, campaign_id?}` -> a real agent session for whoever the
    project picked to plan/write for it (R1-A: `presence.desk_agent`, or a
    campaign's own `how.agent` — see `_desk_agent_ref`), briefed by
    `mc.desk_brief`, which POSTs its draft onto the project's existing social
    queue.

    Two things this route does NOT do, and both are deliberate:

      * It does not generate. The Desk is the office, not a persona — the
        chosen agent holds the platform judgement, per the standing position
        of 2026-08-29 that declined a separate marketing agent.
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
    agent_ref = _desk_agent_ref(project, campaign)
    if not agent_ref:
        return _pick_agent_error(project)

    brief = _brief.build_brief(signal, voice=voice, campaign=campaign,
                               project_name=project.get('name'))
    if dispatch_agent is None:
        # Unwired (or a test): hand back the brief rather than pretending. A
        # caller that believes a draft was requested when none was is exactly
        # the substitution failure the agent rules forbid.
        return jsonify({'error': 'dispatch not wired', 'brief': brief}), 503

    try:
        # strict_character: a fresh, explicit pick, so an unresolvable agent
        # ref must REFUSE rather than silently run personaless (MC-925). A
        # draft written in the default agent's voice, landing on the queue
        # looking like the project's chosen writer's, is exactly the
        # substitution the agent rules forbid.
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Draft a {_brief.platform_for(voice)} post: '
                         f'{(signal.get("summary") or "")[:70]}',
            character=agent_ref,
            source='agent', strict_character=True)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        _log(f'[desk] draft dispatch failed for {sig_id}: {e}')
        return jsonify({'error': f'dispatch failed: {e}'}), 502

    return jsonify({'ok': True, 'signal_id': sig_id, 'voice': voice,
                    'platform': _brief.platform_for(voice),
                    'project_id': pid, 'session_id': session_id}), 202


def dispatch_rework(project_id: str, item: dict, note: str) -> dict:
    """Redraft a pushed-back queue item — the bridge `project_routes.reject_social_queue_item`
    calls so a push-back is not the end of the story.

    Never raises. Returns `{'dispatched': bool, 'session_id': str | None,
    'reason': str | None}` — `reason` explains either why no rework was
    started (not an error: pre-Desk draft, unknown voice, signal gone) or why
    a dispatch attempt failed. Exactly one dispatch per item: a draft that
    already carries a `rework_session_id` is reported back as already in
    flight rather than dispatched a second time.

    Deliberately called AFTER the caller has already saved the push-back
    itself — a dispatch failure here must never cost the human's note.
    """
    if item.get('rework_session_id'):
        return {'dispatched': False, 'session_id': item['rework_session_id'],
                'reason': 'a rework is already in flight for this draft'}

    sig_id = item.get('signal_id')
    voice = item.get('voice')
    if not sig_id or not voice:
        return {'dispatched': False, 'session_id': None,
                'reason': 'this draft has no signal_id/voice (a pre-Desk draft) '
                          '— rework must be started by hand'}
    if not _desk.is_voice(voice):
        return {'dispatched': False, 'session_id': None,
                'reason': f'voice {voice!r} no longer exists — rework must be '
                          'started by hand'}

    signal = next((s for s in _desk.list_signals(limit=100000) if s.get('id') == sig_id), None)
    if signal is None:
        return {'dispatched': False, 'session_id': None,
                'reason': 'the signal this draft came from is gone — rework '
                          'must be started by hand'}

    project = load_project(project_id) if load_project else None
    if project is None:
        return {'dispatched': False, 'session_id': None,
                'reason': f'project {project_id!r} not found'}

    agent_ref = _desk_agent_ref(project)
    if not agent_ref:
        return {'dispatched': False, 'session_id': None,
                'reason': f'no agent picked for {project.get("name") or project_id} yet '
                          '— pick who plans for this project, then push back again'}

    if dispatch_agent is None:
        return {'dispatched': False, 'session_id': None,
                'reason': 'dispatch not wired'}

    brief = _brief.build_rework_brief(signal, item=item, note=note,
                                      project_name=project.get('name'))
    try:
        session_id = dispatch_agent(
            project_id, brief, '',
            display_task=f'Rework a {item.get("platform") or "draft"} post '
                         'after push-back',
            character=agent_ref,
            source='agent', strict_character=True)
    except Exception as e:
        _log(f'[desk] rework dispatch failed for {item.get("id")}: {e}')
        return {'dispatched': False, 'session_id': None,
                'reason': f'dispatch failed: {e}'}

    return {'dispatched': True, 'session_id': session_id, 'reason': None}


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
    agent_ref = _desk_agent_ref(project, camp)
    if not agent_ref:
        return _pick_agent_error(project)
    if dispatch_agent is None:
        return jsonify({'error': 'dispatch not wired', 'brief': brief}), 503

    try:
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Triage {len(pool)} signals — what is worth posting?',
            character=agent_ref,
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
    agent_ref = _desk_agent_ref(project, camp)
    if not agent_ref:
        return _pick_agent_error(project)
    brief = _brief.build_brief(signal, voice=prop['voice'], campaign=camp,
                               project_name=project.get('name'))
    if dispatch_agent is None:
        return jsonify({'error': 'dispatch not wired', 'brief': brief}), 503
    try:
        session_id = dispatch_agent(
            pid, brief, '',
            display_task=f'Draft a {_brief.platform_for(prop["voice"])} post: '
                         f'{(signal.get("summary") or "")[:70]}',
            character=agent_ref,
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
