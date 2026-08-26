"""Agent character endpoints — Prompt Builder Phase 1
(docs/PROMPT_BUILDER_DESIGN.md §5.2).

Thin glue over mc/characters.py, same shape as skills_routes: the logic
module owns IO/validation, routes wrap it. Characters are standard Claude
Code subagent files under `.claude/agents/` (global or project scope);
the UI word for them is "Characters" — never "agents", which is taken by
MC's dispatched session.
"""

from typing import Any, Callable

from flask import Blueprint, jsonify, request

import agent_runtime as _agent_runtime
from mc import characters as _chars
from mc import state
from mc.core import _log
from mc.memory import _scribe_call
from mc.blueprints.skills_routes import _resolve_project_path_or_400

bp = Blueprint('characters', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]


def wire(*, load_project_fn):
    """Late-bind the projects-family accessor (same pattern as 1.3/1.9)."""
    global load_project
    load_project = load_project_fn


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

    try:
        rec = _chars.write_character(scope, name, description, body,
                                     project_path=project_path,
                                     overwrite=overwrite, engine=engine,
                                     avatar=data.get('avatar'),
                                     skills=data.get('skills'))
    except FileExistsError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError as e:
        return jsonify({'error': f'write failed: {e}'}), 500
    return jsonify(rec), 201


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


def _taken_agent_names(project_path, exclude):
    """Names already in use, so a fresh pick does not collide.

    Measured 2026-08-22: naming three types independently produced "Marlow"
    and "Marlowe". Each call is blind to the others, so warning about the
    generic AI-name cluster is not enough — the model has to see the actual
    roster. Both pools are read: a global type shares a chat header with a
    project one, so a clash across scopes is just as unreadable.
    """
    out = []
    try:
        for rec in _chars.list_characters(project_path=project_path):
            if rec.get('name') == exclude:
                continue
            n = rec.get(_chars.AGENT_NAME_KEY)
            if n:
                out.append(n)
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
        model = ((rec.get('engine') or {}).get('model')
                 or state.CONFIG.get('agent_model') or 'sonnet')
        payload = (f"Role: {rec.get('description') or ''}\n\n"
                   f"{rec.get('body') or ''}")[:6000]
        prompt = _NAME_PROMPT
        taken = _taken_agent_names(project_path, name)
        if taken:
            prompt += (
                "\n- These names are ALREADY TAKEN by other agents on this "
                "machine: " + ", ".join(taken) + ". Do not reuse any of them, "
                "and do not pick anything that differs from one by only a "
                "letter or two — the roster has to be readable at a glance.")
        try:
            raw = _scribe_call(model, prompt, payload)
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
