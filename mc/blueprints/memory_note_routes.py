"""G1 note-write gate endpoints (MEMORY_DESIGN_V2_SPEC.md §10) — thin HTTP
glue; `mc/memory_note_gate.py` owns the rules, the stamps and the counter.

  POST  /api/project/<id>/memory/note   mint a NEW topic note
  PATCH /api/project/<id>/memory/note   replace one in place (optional rename)

Body (both): {"slug": "<file stem>", "content": "<whole note: frontmatter +
body>", "note_type"?: user|feedback|project|reference, "actor"?: "<label>"}
PATCH adds {"new_slug"?: "<stem>"} to rename. `origin`, `generated` and
`verified` in the author's frontmatter are discarded: the server stamps them.

Nothing calls this route yet (the fix-B brief: do NOT wire any existing writer
through it in this unit), and it never touches MEMORY.md, position files, or
any dir but the addressed project's own vault.
"""

from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc import memory_note_gate as gate

bp = Blueprint('memory_note_routes', __name__)

load_project: Callable[[str], Any] = None  # type: ignore[assignment]


def wire(*, load_project_fn):
    """Late-bind the projects-family accessor (the distiller_routes pattern)."""
    global load_project
    load_project = load_project_fn


def _refuse(code, error, detail, **extra):
    return jsonify({'ok': False, 'error': error, 'detail': detail, **extra}), code


def _rejection(mode, violations):
    # 422 + machine-readable body; the on-disk file was never touched.
    return jsonify({
        'ok': False, 'error': 'memory_note_rejected', 'mode': mode,
        'violations': violations,
        'remedy': '; '.join(dict.fromkeys(v['remedy'] for v in violations)),
    }), 422


def _success(code, *, mode, origin, file, ev_violations, **extra):
    return jsonify({
        'ok': True, 'file': file, 'mode': mode, 'origin': origin,
        # report mode: the write landed AND these would have refused it.
        'would_reject': [v['class'] for v in ev_violations],
        'violations': ev_violations, **extra}), code


def _prepare(project_id):
    """-> ctx dict (project, body fields, caller stamp) or an error response."""
    project = load_project(project_id)
    if not project:
        return _refuse(404, 'not_found', 'unknown project')
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _refuse(400, 'bad_request', 'JSON object body required')
    content = data.get('content')
    if not isinstance(content, str) or not content.strip():
        return _refuse(400, 'bad_request', 'content (the whole note) is required')
    note_type = str(data.get('note_type') or gate._DEFAULT_TYPE).strip().lower()
    if note_type not in gate.NOTE_TYPES:
        return _refuse(400, 'bad_request',
                             f'note_type must be one of {list(gate.NOTE_TYPES)}')
    try:
        slug = gate.validate_slug(data.get('slug'))
        new_slug = (gate.validate_slug(data['new_slug'])
                    if data.get('new_slug') else None)
        mem_dir = gate.mem_dir_for(project)
    except gate.SlugRefused as e:
        gate.record(project_id, op=request.method.lower(),
                    slug=str(data.get('slug') or ''), mode=gate.gate_mode(),
                    origin='', result='refused', refusal=str(e))
        return _refuse(400, 'slug_refused', str(e))
    origin, refusal = gate.classify_caller(project_id)
    if refusal:
        gate.record(project_id, op=request.method.lower(), slug=slug,
                    mode=gate.gate_mode(), origin=origin, result='refused',
                    refusal=refusal)
        return _refuse(403, 'cross_project_unattended', refusal)
    return {'project': project, 'content': content, 'slug': slug,
            'new_slug': new_slug, 'mem_dir': mem_dir, 'origin': origin,
            'note_type': note_type,
            'actor': str(data.get('actor') or 'memory-note-api')[:80]}


@bp.route('/api/project/<project_id>/memory/note', methods=['POST'])
def post_memory_note(project_id):
    ctx = _prepare(project_id)
    if not isinstance(ctx, dict):
        return ctx
    mode, slug, mem_dir = gate.gate_mode(), ctx['slug'], ctx['mem_dir']

    exact, dup = gate.identity_check(mem_dir, slug)
    if exact:
        gate.record(project_id, op='post', slug=slug, mode=mode,
                    origin=ctx['origin'], result='refused',
                    violations=[{'class': gate.V_DUP, 'collides_with': slug}],
                    refusal='exists')
        return _refuse(409, 'exists', f'{slug}.md already exists; PATCH it, '
                       'or choose a different slug')
    ev = gate.evaluate(mem_dir, slug=slug, content=ctx['content'])
    violations = ev['violations'] + ([dup] if dup else [])

    if violations and mode == 'enforce':
        gate.record(project_id, op='post', slug=slug, mode=mode,
                    origin=ctx['origin'], result='rejected',
                    violations=violations, repairs=ev['repairs'])
        return _rejection(mode, violations)

    text = gate.compose(ev, content=ctx['content'], origin=ctx['origin'],
                        actor=ctx['actor'], note_type=ctx['note_type'])
    if not gate.atomic_write(project_id, mem_dir, f'{slug}.md', text,
                             must_exist=False):
        gate.record(project_id, op='post', slug=slug, mode=mode,
                    origin=ctx['origin'], result='refused', refusal='exists')
        return _refuse(409, 'exists', f'{slug}.md was created concurrently')
    gate.record(project_id, op='post', slug=slug, mode=mode,
                origin=ctx['origin'], result='written',
                violations=violations, repairs=ev['repairs'])
    return _success(201, mode=mode, origin=ctx['origin'], file=f'{slug}.md',
                    ev_violations=violations)


@bp.route('/api/project/<project_id>/memory/note', methods=['PATCH'])
def patch_memory_note(project_id):
    ctx = _prepare(project_id)
    if not isinstance(ctx, dict):
        return ctx
    mode, slug, mem_dir = gate.gate_mode(), ctx['slug'], ctx['mem_dir']
    new_slug = ctx['new_slug'] if ctx['new_slug'] != slug else None

    path = mem_dir / f'{slug}.md'
    if not path.is_file():
        gate.record(project_id, op='patch', slug=slug, mode=mode,
                    origin=ctx['origin'], result='refused', refusal='not_found')
        return _refuse(404, 'not_found', f'{slug}.md does not exist; POST to create it')
    existing = path.read_text(encoding='utf-8', errors='replace')

    dup = None
    if new_slug:
        exact, dup = gate.identity_check(mem_dir, new_slug, ignore_stem=slug)
        if exact:
            gate.record(project_id, op='patch', slug=slug, mode=mode,
                        origin=ctx['origin'], result='refused',
                        violations=[{'class': gate.V_DUP, 'collides_with': new_slug}],
                        refusal='rename_target_exists')
            return _refuse(409, 'exists', f'{new_slug}.md already exists')
    ev = gate.evaluate(mem_dir, slug=slug, content=ctx['content'],
                       existing_text=existing, new_slug=new_slug)
    violations = ev['violations'] + ([dup] if dup else [])

    if violations and mode == 'enforce':
        gate.record(project_id, op='patch', slug=slug, mode=mode,
                    origin=ctx['origin'], result='rejected',
                    violations=violations, repairs=ev['repairs'])
        return _rejection(mode, violations)

    origin = gate.merged_origin(ctx['origin'], existing)
    text = gate.compose(ev, content=ctx['content'], origin=origin,
                        actor=ctx['actor'], note_type=ctx['note_type'],
                        existing_text=existing)
    if new_slug:
        ok = gate.rename_file(project_id, mem_dir, f'{slug}.md',
                              f'{new_slug}.md', text)
    else:
        ok = gate.atomic_write(project_id, mem_dir, f'{slug}.md', text,
                               must_exist=True)
    if not ok:
        gate.record(project_id, op='patch', slug=slug, mode=mode, origin=origin,
                    result='refused', refusal='concurrent_change')
        return _refuse(409, 'conflict', 'the note changed or vanished mid-write')
    gate.record(project_id, op='patch', slug=slug, mode=mode, origin=origin,
                result='written', violations=violations, repairs=ev['repairs'])
    return _success(200, mode=mode, origin=origin,
                    file=f'{new_slug or slug}.md', ev_violations=violations,
                    **({'renamed_from': f'{slug}.md'} if new_slug else {}))
