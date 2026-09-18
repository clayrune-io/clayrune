"""Agent character endpoints — Prompt Builder Phase 1
(docs/PROMPT_BUILDER_DESIGN.md §5.2).

Thin glue over mc/characters.py, same shape as skills_routes: the logic
module owns IO/validation, routes wrap it. Characters are standard Claude
Code subagent files under `.claude/agents/` (global or project scope);
the UI word for them is "Characters" — never "agents", which is taken by
MC's dispatched session.
"""

import random
import re
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

import mc.agent_runtime as _agent_runtime
from mc import characters as _chars
from mc import engine_selection
from mc import state
from mc.core import _log, now_iso
from mc.memory import _scribe_call
from mc.blueprints.skills_routes import _resolve_project_path_or_400
from mc.blueprints.workflow_routes import _is_agent_caller

bp = Blueprint('characters', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
load_projects: Callable[[], list] = lambda: []
_APP_DIR: Path = None  # type: ignore[assignment]


def wire(*, load_project_fn, app_dir=None, load_projects_fn=None):
    """Late-bind the projects-family accessor (same pattern as 1.3/1.9).

    `app_dir` is optional so existing callers (tests) that don't need
    builtin-install keep working without an update.
    """
    global load_project, load_projects, _APP_DIR
    load_project = load_project_fn
    if load_projects_fn is not None:
        load_projects = load_projects_fn
    if app_dir is not None:
        _APP_DIR = app_dir


def _character_model_call(engine, project, prompt, payload):
    """Generate a character artifact with its selected engine.

    Claude remains on the existing Scribe choke point (and its established
    toolless safety guarantees).  Explicit non-Claude selections use the
    runtime text-transform seam instead of sending a foreign model ID to a
    Claude subprocess.  An omitted model means the selected provider's native
    default; no Claude tier is manufactured here.
    """
    raw = engine if isinstance(engine, dict) else {}
    model_override = raw.get('model') if 'model' in raw else None
    resolved = engine_selection.resolve_engine(
        state.CONFIG, project,
        provider_override=raw.get('provider') or '',
        model_override=model_override,
        character=None,
        legacy_default='claude',
    )
    effort = str(raw.get('effort') or '').strip()
    if resolved.provider == 'claude' and not effort:
        return _scribe_call(resolved.model, prompt, payload)
    return _agent_runtime.run_text_transform(
        resolved.provider,
        prompt=prompt,
        model=resolved.model,
        effort=effort,
        stdin_text=payload,
        cwd=str(Path.home()),
    )


def _install_builtin_characters():
    """Install/update built-in characters bundled with MC (e.g. Claydo) into
    ~/.claude/agents/. Mirrors `skills_routes._install_builtin_skills`.

    Called from __main__ on startup. Safe to run on every boot: checksum-aware
    and never touches a character it didn't install itself — a user's own
    agent with the same name is left alone (see
    `characters.install_builtin_characters`'s "no marker -> skipped" branch).
    """
    try:
        if _APP_DIR is None:
            return
        builtin_root = _APP_DIR / 'data' / 'agents' / 'builtin'
        if not builtin_root.exists():
            return
        result = _chars.install_builtin_characters(builtin_root)
        installed = result.get('installed') or []
        updated = result.get('updated') or []
        preserved = result.get('preserved') or []
        if installed or updated:
            _log(f"[characters] installed={installed} updated={updated}")
        if preserved:
            _log(f"[characters] preserved user-modified builtins: {preserved}")
    except Exception as e:
        _log(f"[characters] builtin install failed: {e}")


def _refuse_if_agent_caller():
    """Every character mutation is human-only.

    A character is a system prompt, an engine and a face that later dispatches
    run under, so an agent writing one is self-expansion (CLAUDE.md authority
    guard). The structural signal is workflow_routes._is_agent_caller: the SPA's
    fetch always carries an Origin header, an agent's curl does not, and nothing
    in the body can flip it. is_unattended_caller was the other candidate; it
    deliberately lets an attended manual chat through, which is exactly the
    agent this has to stop. Internal callers (the builtin installer) call
    mc.characters directly and never pass through here.
    """
    if _is_agent_caller():
        return jsonify({
            'error': ('creating or changing characters is human-only: an agent may '
                      'propose a team with a ```mc:team``` block, and a click in the '
                      'Clayrune UI creates it (CLAUDE.md authority guard).'),
        }), 403
    return None


def _project_path_for_list(project_id: str | None) -> str | None:
    if not project_id:
        return None
    p = load_project(project_id)
    if not p:
        return None
    return p.get('project_path') or None


def _validated_engine(data, existing=None):
    """Pull provider/model/effort off a request body, validated.

    Returns (engine_dict, error_response_or_None).

    A character that pins a provider which is not registered would spawn on
    whatever the project default happens to be — running silently on the wrong
    engine, which is worse than refusing to save. So an unknown provider is a
    400, not a fallback (docs/AGENT_TYPES_DESIGN.md §10.4).

    `existing` carries the record's current engine so a PUT that omits the keys
    entirely leaves them alone; sending a key with an empty string is how you
    CLEAR a pin. Those have to be distinguishable or the editor can never
    remove one.
    """
    engine = dict((existing or {}).get('engine') or {})
    for k in _chars.ENGINE_KEYS:
        if k not in data:
            continue
        v = data.get(k)
        v = v.strip() if isinstance(v, str) else ''
        if not v:
            engine.pop(k, None)
            continue
        engine[k] = v

    effort = engine.get('effort')
    if effort and effort not in _chars.VALID_EFFORT:
        return None, (jsonify({'error': f'effort must be one of '
                                        f'{", ".join(_chars.VALID_EFFORT)}'}), 400)

    provider = engine.get('provider')
    if provider:
        try:
            known = {r.name for r in _agent_runtime.available_runtimes()}
        except Exception:
            known = set()
        if known and provider.lower() not in known:
            return None, (jsonify({
                'error': f'unknown provider {provider!r} — available: '
                         f'{", ".join(sorted(known))}'}), 400)
        engine['provider'] = provider.lower()
    return engine, None


@bp.route('/api/characters')
def list_characters_route():
    """Global pool + (optionally) one project's pool.

    Query params:
      project_id: include this project's local characters (shadow-flags
                  same-named globals)
      q: substring filter on name+description
    """
    project_id = request.args.get('project_id')
    q = (request.args.get('q') or '').strip().lower()
    project_path = _project_path_for_list(project_id)
    items = _chars.list_characters(project_path=project_path,
                                   project_id=project_id)
    if q:
        items = [c for c in items
                 if q in (c.get('name', '') + ' ' + c.get('description', '')).lower()]
    return jsonify(items)


@bp.route('/api/characters', methods=['POST'])
def create_character_route():
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    body = data.get('body') or ''
    scope = (data.get('scope') or 'project').strip()
    project_id = data.get('project_id')
    overwrite = bool(data.get('overwrite'))

    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    engine, eng_err = _validated_engine(data)
    if eng_err:
        return eng_err

    chosen_name = _chars.clean_agent_name(data.get('agent_name'))
    taken = _taken_agent_names(project_path, name if overwrite else None, scope)
    if chosen_name and chosen_name.casefold() in {n.casefold() for n in taken}:
        return jsonify({'error': f'The name "{chosen_name}" is already taken by '
                                 'another agent. Choose a different Goes by name.'}), 400

    try:
        rec = _chars.write_character(scope, name, description, body,
                                     project_path=project_path,
                                     overwrite=overwrite, engine=engine,
                                     agent_name=data.get('agent_name'),
                                     avatar=data.get('avatar'),
                                     skills=data.get('skills'))
    except FileExistsError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError as e:
        return jsonify({'error': f'write failed: {e}'}), 500
    return jsonify(rec), 201


# ── Team creation (one-prompt team, 2026-09-14) ─────────────────────────────
# An agent may PROPOSE a team as a fenced ```mc:team``` block; the chat renders
# it as an editable card (static/js/team-card.js) and only the human's click
# reaches this route. It is the existing creation path (write_character) run
# once per member, with two properties a loop of POST /api/characters calls
# cannot give: nothing is written until every member has passed every check,
# and a write that still fails midway is rolled back, so the result is the
# whole team or none of it.

MAX_TEAM_SIZE = 12


def _error_text(resp):
    """The message out of a (jsonify(...), status) pair a shared validator
    returned, so member errors can be collected rather than returned one at a
    time."""
    try:
        return (resp[0].get_json() or {}).get('error') or 'invalid'
    except Exception:
        return 'invalid'


def _plan_team_member(m, project_id):
    """Validate one proposed member. Returns (plan, error_text)."""
    name = str(m.get('name') or '').strip().lower()
    description = m.get('description') or m.get('role') or ''
    body = m.get('body') or m.get('persona') or ''
    scope = str(m.get('scope') or 'project').strip().lower()
    if scope not in ('global', 'project'):
        return None, 'scope must be global|project'
    try:
        description, body = _chars.validate_fields(name, str(description), str(body))
    except ValueError as e:
        return None, str(e)

    project_path = None
    if scope == 'project':
        if not project_id:
            return None, 'project scope needs a project; pick global or open the team in a project'
        p = load_project(project_id)
        if not p:
            return None, 'project not found'
        project_path = p.get('project_path') or None
        if not project_path:
            return None, 'this project has no folder configured; pick global scope'

    # Accept the engine nested ({"engine": {...}}) or flat on the member; the
    # rules are the POST /api/characters rules, not a second copy.
    engine_src = m.get('engine') if isinstance(m.get('engine'), dict) else m
    engine, eng_err = _validated_engine(engine_src)
    if eng_err:
        return None, _error_text(eng_err)

    raw_name = str(m.get('agent_name') or '').strip()
    agent_name = _chars.clean_agent_name(raw_name)
    if raw_name and not agent_name:
        return None, 'the "goes by" name is unusable: one to three words'

    raw_avatar = str(m.get('avatar') or '').strip()
    avatar = _chars.clean_avatar(raw_avatar)
    if raw_avatar and not avatar:
        return None, 'the avatar is unusable: one emoji, or fig:<figure>'
    fig = _chars.avatar_figure(avatar)
    if avatar.startswith(_chars.AVATAR_FIG_PREFIX) and fig not in _chars.list_figures():
        return None, f'no figure named {raw_avatar!r} on this install'

    return {'name': name, 'scope': scope, 'project_path': project_path,
            'description': description, 'body': body, 'engine': engine,
            'agent_name': agent_name, 'avatar': avatar}, None


@bp.route('/api/characters/team', methods=['POST'])
def create_team_route():
    """Create a proposed team: every change, or none.

    Body: {members: [...], project_id, overwrite: ["<scope>:<name>"], hire_new}
    A member is either
      {mode: "reuse", ref: "<scope>:<name>"}  an existing character, hired into
                                              project_id and never written, or
      {mode: "new", name, agent_name, description|role, body|persona, avatar,
       scope, provider, model, effort | engine: {...}}  created here.
    `hire_new` also hires the created members into project_id.

    400 `member_errors` = per-member failures; 409 `conflicts` = new names that
    already exist and are not listed in `overwrite`. Neither writes anything.
    Roster hires go through project_routes.apply_roster_hires (the drag-drop
    hire's own write) in one project save, after the character files; if that
    save fails the files are rolled back too.
    """
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    data = request.get_json(silent=True) or {}
    members = data.get('members')
    project_id = data.get('project_id') or None
    overwrite = {str(k) for k in (data.get('overwrite') or [])}
    hire_new = bool(data.get('hire_new'))
    if not isinstance(members, list) or not members:
        return jsonify({'error': 'members must be a non-empty list'}), 400
    if len(members) > MAX_TEAM_SIZE:
        return jsonify({'error': f'a team is at most {MAX_TEAM_SIZE} members'}), 400

    plans, reuse_refs, member_errors, conflicts, seen = [], [], [], [], set()

    def _fail(i, name, err):
        member_errors.append({'index': i, 'name': name, 'error': err})

    for i, m in enumerate(members):
        if not isinstance(m, dict):
            _fail(i, '', 'member must be an object')
            continue
        mode = str(m.get('mode') or 'new').strip().lower()
        if mode == 'reuse':
            ref = str(m.get('ref') or '').strip()
            scope, _, name = ref.partition(':')
            scope, name = scope.strip().lower(), name.strip()
            if scope not in ('global', 'project') or not name:
                _fail(i, ref, "reuse needs ref '<scope>:<name>'")
                continue
            if not project_id:
                _fail(i, ref, 'reusing an agent hires it into a project; open the team in a project')
                continue
            pp = _project_path_for_list(project_id) if scope == 'project' else None
            if _chars.read_character(scope, name, project_path=pp, include_body=False) is None:
                _fail(i, ref, f'no existing character {ref} to reuse')
                continue
            key = f'{scope}:{name}'
            if key in seen:
                _fail(i, name, f'{key} appears twice in this team')
                continue
            seen.add(key)
            reuse_refs.append(key)
            continue
        if mode != 'new':
            _fail(i, str(m.get('name') or ''), 'mode must be new|reuse')
            continue
        plan, err = _plan_team_member(m, project_id)
        if err or plan is None:
            _fail(i, str(m.get('name') or ''), err or 'invalid')
            continue
        key = f"{plan['scope']}:{plan['name']}"
        if key in seen:
            _fail(i, plan['name'], f'{key} appears twice in this team')
            continue
        seen.add(key)
        plan['index'], plan['key'] = i, key
        plan['existing'] = _chars._find_file(plan['scope'], plan['name'], plan['project_path'])
        if plan['existing'] is not None and key not in overwrite:
            conflicts.append({'index': i, 'name': plan['name'], 'scope': plan['scope'], 'key': key})
        plans.append(plan)

    if member_errors:
        return jsonify({'error': f'{len(member_errors)} member(s) need fixing; nothing was created',
                        'member_errors': member_errors, 'conflicts': conflicts}), 400
    if conflicts:
        return jsonify({'error': f'{len(conflicts)} name(s) already exist; nothing was created. '
                                 'Rename them, reuse the existing agent, or confirm overwriting.',
                        'conflicts': conflicts}), 409

    hire_refs = reuse_refs + ([pl['key'] for pl in plans] if hire_new else [])
    project = None
    if hire_refs:
        project = load_project(project_id) if project_id else None
        if project is None:
            return jsonify({'error': 'hiring into a project needs a real project_id; nothing was created'}), 400

    created, snapshots, records, hired, already = [], [], [], [], []
    try:
        for plan in plans:
            existing = plan['existing']
            if existing is not None:
                snapshots.append((existing, existing.read_bytes()))
            rec = _chars.write_character(
                plan['scope'], plan['name'], plan['description'], plan['body'],
                project_path=plan['project_path'], overwrite=existing is not None,
                engine=plan['engine'], agent_name=plan['agent_name'],
                avatar=plan['avatar'])
            if existing is None:
                path = _chars._find_file(plan['scope'], plan['name'], plan['project_path'])
                if path is not None:
                    created.append(path)
            records.append(rec)
        if project is not None and project_id:
            from mc.blueprints import project_routes as _pr
            hired, already = _pr.apply_roster_hires(project, hire_refs, 'team')
            if hired:
                project['last_updated'] = now_iso()
                _pr.save_project(project_id, project)
    except Exception as e:
        for path in created:
            try:
                path.unlink()
            except OSError as re_:
                _log(f"[characters] team rollback could not remove {path}: {re_}")
        for path, blob in snapshots:
            try:
                path.write_bytes(blob)
            except OSError as re_:
                _log(f"[characters] team rollback could not restore {path}: {re_}")
        return jsonify({'error': f'team create failed, nothing kept: {e}'}), 500
    return jsonify({'created': records, 'hired': hired, 'already_hired': already}), 201


# ── Voice generation (MC-943) ────────────────────────────────────────────────
# 2026-09-01 split shared rules (conduct/content, ~46KB, byte-identical across
# every agent) from the character file (voice, 1.8-4.3KB) — because that shared
# floor legislates TONE ("always condense", "never narrate your own diligence")
# and nine hand-written characters had collapsed into one indistinguishable
# telegram until each got a concrete `## Voice` section. That section was
# authored by hand for the nine that exist; hiring a tenth through the UI
# produced a name, a role and a face, and then the same flattened voice this
# split was supposed to have killed. This generates the section AS PART OF the
# hire (docs/PROMPT_BUILDER_DESIGN.md's Claydo save-panel flow), so a
# newly-hired character is never voiceless-by-default again.
#
# No scope/name here, unlike self-naming/self-facing below: this runs while
# the character is still a DRAFT in the save panel, before the file exists —
# the whole point is showing the voice before the first write, not generating
# it after the fact on a manual click.
_VOICE_PROMPT = (
    "You are about to start work under the role definition below. Write the "
    "\"## Voice\" section of your OWN system prompt: the concrete speech "
    "habits that make you sound like nobody else on the roster.\n\n"
    "Every agent on this roster already shares one long system prompt that "
    "legislates tone in general — be concise, use bullet points, never "
    "narrate your own diligence. Your job here is everything ABOVE that "
    "floor: what you lead with, your rhythm, the phrasings you reach for, "
    "and the thing you flatly never say. A generic block like 'be concise "
    "and professional' is a FAILURE — if it could paste unchanged into a "
    "different role, it is wrong.\n\n"
    "Output format, exactly:\n"
    "## Voice\n\n"
    "<one sentence naming the single thing that opens every reply from "
    "you>\n\n"
    "- **<a named habit, bolded>.** <one or two sentences, including an "
    "invented example phrase in quotes, in this role's own words>\n"
    "(3 to 5 bullets shaped like that one)\n"
    "- A last bullet naming something you flatly never say or never do.\n\n"
    "Rules:\n"
    "- Output ONLY the section: start with '## Voice', nothing before it, "
    "nothing after the last bullet. No preamble, no fences.\n"
    "- Every bullet has to be something a reader could catch you breaking. "
    "'Be clear' is not checkable; 'location first, every time' is.\n"
    "- At least one bullet must include an invented example sentence in "
    "quotes, written the way THIS role would actually say it.\n"
    "- Ground it in this role's temperament and what it exists to catch — "
    "not in generic professionalism that would fit any role.\n"
    "- 120-220 words total."
)


def _clean_voice_section(raw):
    """Turn a model's answer into a bare `## Voice` section, or '' if unusable.

    Strips a fenced wrapper if the model added one despite the "no fences"
    rule, and supplies the heading if the model dropped it but the body is
    otherwise usable — a missing heading line is not worth refusing an
    otherwise-good section over.
    """
    text = (raw or '').strip()
    m = re.match(r'^```(?:[\w-]*)\n([\s\S]*?)\n?```$', text)
    if m:
        text = m.group(1).strip()
    if not text:
        return ''
    if not re.match(r'^##\s*Voice\b', text, re.IGNORECASE):
        text = '## Voice\n\n' + text
    return text


@bp.route('/api/characters/voice', methods=['POST'])
def generate_voice_route():
    """Generate a `## Voice` section for a character still being hired.

    Takes the draft's description + body directly (not scope/name — the
    character does not exist on disk yet at this point in the save-panel
    flow). Never persists anything; the caller folds the result into `body`
    and saves it through the normal create/update path, so this endpoint
    itself has no write-side effects to test beyond "returns text or a
    clear error."
    """
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    data = request.get_json(silent=True) or {}
    description = (data.get('description') or '').strip()
    body = (data.get('body') or '').strip()
    if not description and not body:
        return jsonify({'error': 'need a description or body to write a voice from'}), 400

    engine_raw = data.get('engine')
    engine = engine_raw if isinstance(engine_raw, dict) else {}
    project_id = str(data.get('project_id') or '').strip()
    project = load_project(project_id) if project_id else None
    payload = (f"Role: {description}\n\n{body}")[:6000]
    try:
        raw = _character_model_call(engine, project, _VOICE_PROMPT, payload)
    except Exception as e:
        _log(f"[characters] voice generation failed: {e}")
        return jsonify({'error': f'could not reach the model to write a voice: {e}'}), 502

    voice = _clean_voice_section(raw)
    if not voice:
        return jsonify({'error': 'the model did not return a usable voice section — '
                                 'edit one by hand, or try again'}), 502
    return jsonify({'voice': voice})


# ── Identity suggestion — a new hire arrives already named and faced ────────
# MC-871 defect B: the save panel above generated a Voice section automatically
# but left "Goes by" and "Face" (the persona editor's own fields, see
# openPersonaEditor in claydo.js) unset — a hire landed on the roster with no
# name and no avatar, and picking either was a manual trip into the editor
# after the fact. This runs the same self-naming/self-facing prompts as the
# `/name` and `/avatar` routes below (so a suggested identity and a
# later-repicked one come from the same voice), but for a DRAFT that has no
# scope/name yet — same reasoning as /voice above.
#
# Unlike /name and /avatar, this never 502s: those are a deliberate manual
# retry ("try again, or pick one by hand"), but a hire has to land with SOME
# identity, so a model failure or a same-name/same-face collision falls back to
# a deterministic pick from a curated pool rather than leaving the field blank.
# The fallback POOL was hand-picked to sit next to the existing roster's
# register (short, plain, human-sounding) rather than the generic AI-assistant
# names _NAME_PROMPT already warns the model away from.
_FALLBACK_NAMES = [
    'Odell', 'Fitch', 'Marnie', 'Callan', 'Rook', 'Sable', 'Perry', 'Wynn',
    'Blythe', 'Corin', 'Faye', 'Grady', 'Idris', 'Junot', 'Kestrel', 'Lowell',
    'Merrin', 'Nash', 'Orla', 'Piper', 'Rhys', 'Sloane', 'Tamsin', 'Vance',
    'Wilder', 'Yara', 'Zeke',
]


def _fallback_name(taken):
    taken_cf = {t.casefold() for t in taken}
    pool = [n for n in _FALLBACK_NAMES if n.casefold() not in taken_cf]
    if pool:
        return random.choice(pool)
    # An exhausted pool must not silently reuse an occupied name.
    suffix = 2
    while True:
        for name in _FALLBACK_NAMES:
            candidate = f'{name} {suffix}'
            if candidate.casefold() not in taken_cf:
                return candidate
        suffix += 1


def _fallback_avatar(figures, taken):
    taken_cf = {t.casefold() for t in taken}
    pool = [f for f in figures if f.casefold() not in taken_cf]
    fig = random.choice(pool or figures)
    return _chars.AVATAR_FIG_PREFIX + fig


@bp.route('/api/characters/identity', methods=['POST'])
def suggest_identity_route():
    """Suggest a display name + face for a character still being hired.

    Same draft-only shape as /voice: takes description/body directly, never
    persists, and the save panel folds the result into the create POST. Always
    returns a usable {agent_name, avatar} pair — see the fallback note above.
    """
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    data = request.get_json(silent=True) or {}
    description = (data.get('description') or '').strip()
    body = (data.get('body') or '').strip()
    if not description and not body:
        return jsonify({'error': 'need a description or body to suggest an identity from'}), 400

    scope = (data.get('scope') or 'global').strip()
    project_path, err = _resolve_project_path_or_400(scope, data.get('project_id'))
    if err:
        return err

    engine_raw = data.get('engine')
    engine = engine_raw if isinstance(engine_raw, dict) else {}
    project_id = str(data.get('project_id') or '').strip()
    project = load_project(project_id) if project_id else None
    payload = (f"Role: {description}\n\n{body}")[:6000]

    taken_names = _taken_agent_names(project_path, None)
    name_prompt = _NAME_PROMPT
    if taken_names:
        name_prompt += (
            "\n- These names are ALREADY TAKEN by other agents on this "
            "machine: " + ", ".join(taken_names) + ". Do not reuse any of "
            "them, and do not pick anything that differs from one by only a "
            "letter or two — the roster has to be readable at a glance.")
    try:
        raw_name = _character_model_call(engine, project, name_prompt, payload)
        agent_name = _chars.clean_agent_name(raw_name)
    except Exception as e:
        _log(f"[characters] identity suggestion (name) failed, falling back: {e}")
        agent_name = ''
    # Generation can take seconds; another hire may have saved meanwhile.
    taken_names = _taken_agent_names(project_path, None)
    if not agent_name or agent_name.casefold() in {t.casefold() for t in taken_names}:
        agent_name = _fallback_name(taken_names)

    figures = _chars.list_figures()
    taken_figs = _taken_avatars(project_path, None)
    avatar = ''
    if figures:
        face_prompt = _FACE_PROMPT + "\n\nFigures available: " + ", ".join(figures)
        if taken_figs:
            face_prompt += ("\n\nAlready worn by other agents on this machine "
                            "— do NOT reuse any of these: " + ", ".join(taken_figs))
        try:
            raw_face = _character_model_call(engine, project, face_prompt, payload)
            avatar = _resolve_face(raw_face, figures)
        except Exception as e:
            _log(f"[characters] identity suggestion (face) failed, falling back: {e}")
            avatar = ''
        chosen_fig = _chars.avatar_figure(avatar)
        if not avatar or (chosen_fig and chosen_fig.casefold() in {t.casefold() for t in taken_figs}):
            avatar = _fallback_avatar(figures, taken_figs)

    return jsonify({'agent_name': agent_name, 'avatar': avatar})


@bp.route('/api/characters/<scope>/<name>')
def read_character_route(scope, name):
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    project_id = request.args.get('project_id')
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err
    rec = _chars.read_character(scope, name, project_path=project_path,
                                project_id=project_id)
    if not rec:
        return jsonify({'error': 'character not found'}), 404
    return jsonify(rec)


@bp.route('/api/characters/<scope>/<name>', methods=['PUT'])
def update_character_route(scope, name):
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    data = request.get_json() or {}
    project_id = data.get('project_id')
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    existing = _chars.read_character(scope, name, project_path=project_path)
    if not existing:
        return jsonify({'error': 'character not found'}), 404
    description = (data.get('description') or existing.get('description') or '').strip()
    body = data.get('body')
    if body is None:
        body = existing.get('body') or ''

    engine, eng_err = _validated_engine(data, existing)
    if eng_err:
        return eng_err

    # None = leave the name alone; '' = clear it. Same absent-vs-empty split the
    # engine keys use, and for the same reason: a key the editor never sends
    # must not silently wipe a value the editor never showed.
    agent_name = data.get('agent_name') if 'agent_name' in data else None
    avatar = data.get('avatar') if 'avatar' in data else None
    skills = data.get('skills') if 'skills' in data else None

    try:
        rec = _chars.write_character(scope, name, description, body,
                                     project_path=project_path,
                                     overwrite=True, engine=engine,
                                     agent_name=agent_name, avatar=avatar,
                                     skills=skills)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError as e:
        return jsonify({'error': f'write failed: {e}'}), 500
    return jsonify(rec)


# The agent picks its OWN name (Ron, 2026-08-22). Not a label the user types —
# the type reads its own role and decides who it is, the way a person would.
#
# The prompt is deliberately narrow. Asked open-endedly, models reach for the
# same handful of nouns (Atlas, Nova, Sage, Echo) across every role, which
# produces a roster that all sounds alike and tells you nothing. Naming the
# failure mode in the prompt is what buys variety.
_NAME_PROMPT = (
    "You are about to start work under the role definition below. Choose the "
    "name you will go by.\n\n"
    "Rules:\n"
    "- Output ONLY the name. No quotes, no punctuation, no explanation.\n"
    "- One word, or two at most.\n"
    "- It should suit the role's temperament, not describe the job. A reviewer "
    "is not called Reviewer.\n"
    "- AVOID the obvious AI-assistant names: Atlas, Nova, Sage, Echo, Iris, "
    "Orion, Lumen, Aria, Cipher, Vertex. They are overused and every role ends "
    "up sounding the same.\n"
    "- Pick something a person could plausibly be called, or a short "
    "distinctive word. Make it memorable."
)


def _taken_agent_names(project_path, exclude, exclude_scope=None):
    """Names already in use, so a fresh pick does not collide.

    Measured 2026-08-22: naming three types independently produced "Marlow"
    and "Marlowe". Each call is blind to the others, so warning about the
    generic AI-name cluster is not enough — the model has to see the actual
    roster. Read globals and every registered project, matching the Floor's
    cross-project bench rather than only the draft's destination project.
    """
    out = []
    paths = {str(Path(project_path).resolve())} if project_path else set()
    try:
        for project in load_projects():
            if project.get('project_path'):
                paths.add(str(Path(project['project_path']).resolve()))
        # Globals once, then each registered project's local pool. The Floor
        # displays them together, so display names share one namespace.
        for path in [None, *sorted(paths)]:
            for rec in _chars.list_characters(project_path=path):
                if path and rec.get('scope') != 'project':
                    continue
                current_path = str(Path(project_path).resolve()) if project_path else None
                if (rec.get('name') == exclude and path == current_path
                        and (exclude_scope is None or rec.get('scope') == exclude_scope)):
                    continue
                n = rec.get(_chars.AGENT_NAME_KEY)
                if n:
                    out.append(n.strip())
    except Exception as e:
        _log(f"[characters] could not read the existing roster: {e}")
    return sorted(set(out))



@bp.route('/api/avatars')
def list_avatars():
    """Figure names this install can draw. Names only — the UI builds the URL."""
    return jsonify({'figures': _chars.list_figures(),
                    'prefix': _chars.AVATAR_FIG_PREFIX})


@bp.route('/api/avatars/<name>')
def serve_avatar(name):
    """Serve `assets/avatars/<name>.webp`.

    Its own route rather than `/api/serve-image?path=…`: that one takes an
    absolute path, which would mean the frontend knowing this machine's
    checkout location and an arbitrary filesystem string travelling over a
    public API. Here the name is checked against the directory listing, so a
    bad one is a 404 and never a traversal.
    """
    from flask import send_file
    n = _chars.avatar_figure(_chars.AVATAR_FIG_PREFIX + str(name or ''))
    if not n or n not in _chars.list_figures():
        return jsonify({'error': 'no such figure'}), 404
    from pathlib import Path
    return send_file(Path(_chars.AVATARS_DIR) / f'{n}.webp',
                     mimetype='image/webp', max_age=86400)


@bp.route('/api/characters/<scope>/<name>/name', methods=['POST'])
def name_character_route(scope, name):
    """Let the character name itself, and persist the answer.

    POST with {"agent_name": "..."} to set one directly (the editor's manual
    override); POST with no body to have the model choose. An empty string
    clears it and the type falls back to its file name.
    """
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id') or request.args.get('project_id')
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    rec = _chars.read_character(scope, name, project_path=project_path)
    if not rec:
        return jsonify({'error': 'character not found'}), 404

    if 'agent_name' in data:
        chosen = _chars.clean_agent_name(data.get('agent_name'))
        if data.get('agent_name') and not chosen:
            return jsonify({'error': 'that name is unusable — one or two words, '
                                     f'up to {_chars.MAX_AGENT_NAME_LEN} characters'}), 400
    else:
        # Ask the type itself. Run it on the model the type is PINNED to when
        # it has one: a name is a voice decision, and the engine that will do
        # the talking should be the one that picks.
        project = load_project(project_id) if project_id else None
        payload = (f"Role: {rec.get('description') or ''}\n\n"
                   f"{rec.get('body') or ''}")[:6000]
        prompt = _NAME_PROMPT
        taken = _taken_agent_names(project_path, name, scope)
        if taken:
            prompt += (
                "\n- These names are ALREADY TAKEN by other agents on this "
                "machine: " + ", ".join(taken) + ". Do not reuse any of them, "
                "and do not pick anything that differs from one by only a "
                "letter or two — the roster has to be readable at a glance.")
        try:
            raw = _character_model_call(rec.get('engine') or {}, project, prompt, payload)
        except Exception as e:
            _log(f"[characters] self-naming failed for {scope}:{name}: {e}")
            return jsonify({'error': f'could not reach the model to pick a name: {e}'}), 502
        chosen = _chars.clean_agent_name(raw)
        if not chosen:
            # A refusal here is honest: an un-cleanable answer means the model
            # wrote a sentence, and pilling a fragment of it would be worse
            # than leaving the type on its file name.
            return jsonify({'error': 'the model did not return a usable name — '
                                     'try again, or set one by hand'}), 502

    try:
        _chars.write_character(scope, name,
                               rec.get('description') or '',
                               rec.get('body') or '',
                               project_path=project_path, overwrite=True,
                               engine=(rec.get('engine') or {}),
                               agent_name=chosen,
                               # Carry these: this path rewrites the file
                               # whole, so naming itself would otherwise delete
                               # the face and the toolkit it already had.
                               avatar=rec.get('avatar'),
                               skills=rec.get('skills'))
    except (ValueError, OSError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(_chars.read_character(scope, name, project_path=project_path,
                                         include_body=False))


_FACE_PROMPT = (
    "You are about to start work under the role definition below. Choose the "
    "face you will wear on the board.\n\n"
    "Rules:\n"
    "- Output ONLY one figure name from the list. No quotes, no explanation.\n"
    "- Pick the figure whose CRAFT rhymes with how you work, not one that "
    "illustrates your job title. A researcher is not automatically the "
    "scholar.\n"
    "- If genuinely nothing on the list fits, output a single emoji instead."
)


def _taken_avatars(project_path, exclude):
    """Faces already worn, so a fresh pick does not collide.

    Same reasoning as _taken_agent_names: each call is blind to the others,
    and two agents sharing a face makes the Floor unreadable at a glance.
    """
    out = []
    try:
        for rec in _chars.list_characters(project_path=project_path):
            if rec.get('name') == exclude:
                continue
            fig = _chars.avatar_figure(rec.get(_chars.AVATAR_KEY) or '')
            if fig:
                out.append(fig)
    except Exception as e:
        _log(f"[characters] could not read the existing faces: {e}")
    return sorted(set(out))


def _resolve_face(raw, figures):
    """Turn a model's answer into `fig:<name>` or an emoji, or '' if unusable.

    A model asked for one word off a list still sometimes answers "the
    lamplighter" or "fig:lamplighter." — so match a figure name ANYWHERE in
    the reply before falling back to reading the whole thing as an emoji.
    """
    v = ' '.join(str(raw or '').split()).lower()
    if not v:
        return ''
    words = set(re.findall(r'[a-z]+', v))
    hits = [f for f in figures if f.lower() in words]
    if len(hits) == 1:
        return _chars.clean_avatar(_chars.AVATAR_FIG_PREFIX + hits[0])
    if len(hits) > 1:
        # Ambiguous: it listed options instead of choosing. Refusing is honest;
        # taking the first would be a coin flip wearing a decision.
        return ''
    cleaned = _chars.clean_avatar(str(raw or '').strip())
    # Latin letters in it means prose, not an emoji.
    if not cleaned or re.search(r'[A-Za-z]', cleaned):
        return ''
    return cleaned


@bp.route('/api/characters/<scope>/<name>/avatar', methods=['POST'])
def avatar_character_route(scope, name):
    """Let the character choose its own face, and persist the answer.

    Sibling of `/name`. POST with {"avatar": "..."} to set one directly (the
    editor's manual override); POST with no body to have the model pick from
    this install's figures. An empty string clears it.
    """
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    data = request.get_json(silent=True) or {}
    project_id = data.get('project_id') or request.args.get('project_id')
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    rec = _chars.read_character(scope, name, project_path=project_path)
    if not rec:
        return jsonify({'error': 'character not found'}), 404

    if 'avatar' in data:
        chosen = _chars.clean_avatar(data.get('avatar'))
        if data.get('avatar') and not chosen:
            return jsonify({'error': 'that face is unusable — one emoji, or '
                                     'fig:<name>'}), 400
    else:
        figures = _chars.list_figures()
        if not figures:
            return jsonify({'error': 'this install has no figures to '
                                     'choose from'}), 400
        # Same engine rule as self-naming: the model that will do the talking
        # is the one that should pick how it looks.
        project = load_project(project_id) if project_id else None
        payload = (f"Role: {rec.get('description') or ''}\n\n"
                   f"{rec.get('body') or ''}")[:6000]
        prompt = _FACE_PROMPT + "\n\nFigures available: " + ", ".join(figures)
        taken = _taken_avatars(project_path, name)
        if taken:
            prompt += ("\n\nAlready worn by other agents on this machine — do "
                       "NOT reuse any of these: " + ", ".join(taken))
        try:
            raw = _character_model_call(rec.get('engine') or {}, project, prompt, payload)
        except Exception as e:
            _log(f"[characters] self-facing failed for {scope}:{name}: {e}")
            return jsonify({'error': f'could not reach the model to pick a '
                                     f'face: {e}'}), 502
        chosen = _resolve_face(raw, figures)
        if not chosen:
            return jsonify({'error': 'the model did not return a usable face '
                                     '— try again, or pick one by hand'}), 502

    try:
        _chars.write_character(scope, name,
                               rec.get('description') or '',
                               rec.get('body') or '',
                               project_path=project_path, overwrite=True,
                               engine=(rec.get('engine') or {}),
                               # Carried for the same reason as in /name: this
                               # path rewrites the file whole, so choosing a
                               # face would otherwise delete the name and the
                               # toolkit it already had.
                               agent_name=rec.get('agent_name'),
                               avatar=chosen,
                               skills=rec.get('skills'))
    except (ValueError, OSError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(_chars.read_character(scope, name, project_path=project_path,
                                         include_body=False))


@bp.route('/api/characters/<scope>/<name>/move', methods=['POST'])
def move_character_route(scope, name):
    """Move a persona between scopes / projects.

    A character's home was decided once, at creation, and could never be
    changed after: Ron wrote a marketing persona into one project and had no
    way to promote it to global or hand it to another project short of
    retyping the whole thing somewhere else. Scope is a filing decision, and
    filing decisions get revised.

    Copy-then-delete rather than a rename, so a failure anywhere leaves the
    ORIGINAL standing. The alternative loses the file when the destination
    write fails, and the body is the part nobody can retype from memory.
    """
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    data = request.get_json() or {}
    to_scope = (data.get('to_scope') or '').strip()
    to_project_id = data.get('to_project_id')
    if scope not in ('global', 'project') or to_scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400

    from_path, err = _resolve_project_path_or_400(scope, request.args.get('project_id'))
    if err:
        return err
    to_path, err = _resolve_project_path_or_400(to_scope, to_project_id)
    if err:
        return err
    if scope == to_scope and (from_path or '') == (to_path or ''):
        return jsonify({'error': 'it is already there'}), 400

    rec = _chars.read_character(scope, name, project_path=from_path,
                                include_body=True)
    if not rec:
        return jsonify({'error': 'character not found'}), 404
    try:
        moved = _chars.write_character(
            to_scope, name, rec.get('description') or '', rec.get('body') or '',
            project_path=to_path, overwrite=False,
            engine=rec.get('engine'), agent_name=rec.get('agent_name'),
            avatar=rec.get('avatar'), skills=rec.get('skills'))
    except FileExistsError:
        return jsonify({
            'error': f'a character called {name!r} already lives there — '
                     'rename one of them first'}), 409
    except (ValueError, OSError) as e:
        return jsonify({'error': f'move failed: {e}'}), 400

    try:
        _chars.delete_character(scope, name, project_path=from_path)
    except OSError as e:
        # The copy landed. Say so plainly rather than reporting a clean move —
        # two files with the same name in two scopes is a shadowing surprise
        # the user has to know about to fix.
        return jsonify({
            'ok': False, 'copied': True, 'character': moved,
            'error': f'copied to the new home, but the original could not be '
                     f'removed ({e}) — it is now in both places'}), 500
    return jsonify({'ok': True, 'character': moved})


@bp.route('/api/characters/<scope>/<name>', methods=['DELETE'])
def delete_character_route(scope, name):
    refusal = _refuse_if_agent_caller()
    if refusal:
        return refusal
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    project_id = request.args.get('project_id')
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err
    try:
        ok = _chars.delete_character(scope, name, project_path=project_path)
    except OSError as e:
        return jsonify({'error': f'delete failed: {e}'}), 500
    if not ok:
        return jsonify({'error': 'character not found'}), 404
    return jsonify({'ok': True})
