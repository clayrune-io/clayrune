"""Model media roles from the captured catalogue, joined to the MCP wire schema."""
from __future__ import annotations

from mc.desk_connect.higgsfield_mcp_catalogue import cached
from mc.desk_mcp_schema_validation import tool, matches


def inputs(doc: dict | None, kind: str, model_id: str) -> dict | None:
    if not doc or not cached(doc, kind, model_id):
        return None
    result = doc['models'][model_id]['result']
    if result.get('id') != model_id or result.get('output_type') != kind:
        return None
    medias = result.get('medias')
    if not isinstance(medias, list):
        return None
    out = {'text': True, 'first_frame': False, 'last_frame': False,
           'reference_images_max': 0, 'reference_kinds': [], 'schema_state': 'unknown',
           'picture_inputs': [], 'picture_refusal': 'Higgsfield has not supplied a usable picture contract for this model'}
    if not medias:
        out.update(schema_state='none', picture_refusal='This Higgsfield model takes no picture')
        return out
    image_slots = [m for m in medias if isinstance(m, dict) and m.get('type') == 'image']
    if not image_slots:
        if all(isinstance(m, dict) and isinstance(m.get('type'), str) for m in medias):
            out.update(schema_state='none', picture_refusal='This Higgsfield model takes no picture')
        return out
    if len(image_slots) != 1 or image_slots[0].get('name') != 'medias':
        return out
    roles = image_slots[0].get('roles')
    if not isinstance(roles, list) or not roles or not all(isinstance(r, str) for r in roles) or len(set(roles)) != len(roles):
        return out
    name = 'generate_' + kind
    try:
        root = tool(doc, name)['inputSchema']
        params = root['properties']['params']
        branches = params.get('anyOf', [params])
        objects = [b for b in branches if isinstance(b, dict) and b.get('type') == 'object']
        if len(objects) != 1:
            return out
        schema = objects[0]['properties']['medias']
        fields = schema['items']['properties']
        required = schema['items'].get('required', [])
        if schema.get('type') != 'array' or not {'value', 'role'} <= set(required) \
                or fields['value'].get('type') != 'string' or fields['role'].get('type') != 'string':
            return out
        supported = [r for r in roles if r in ('start_image', 'end_image', 'image_references', 'reference_image', 'image')]
        if not supported:
            return out
        # Check the full shape with a local type witness, never sent as a media identifier.
        witnesses = [{'params': {'model': model_id, 'medias': [{'value': 'schema-type-witness', 'role': r}]}}
                     for r in supported]
        if not all(matches(w, root) for w in witnesses):
            return out
        for n in ('media_upload', 'media_confirm', name):
            if not isinstance(tool(doc, n).get('outputSchema'), dict):
                return out
        modes = [p for p in result.get('parameters', []) if isinstance(p, dict) and p.get('name') == 'mode']
        mode = None
        if len(modes) > 1:
            return out
        if modes:
            options = modes[0].get('options')
            if modes[0].get('type') != 'string' or not isinstance(options, list) \
                    or not all(isinstance(o, str) for o in options):
                return out
            if 'omni_reference' in options:
                mode = 'omni_reference'
        out.update(schema_state='documented', picture_ready=True, picture_refusal=None,
                   first_frame='start_image' in supported, last_frame='end_image' in supported,
                   reference_images_max=1 if any(r in supported for r in ('image_references', 'reference_image', 'image')) else 0,
                   picture_inputs=[{'tool': name, 'path': ['params', 'medias'], 'role': r} for r in supported],
                   picture_mode=mode, picture_quote='text_only')
        if out['reference_images_max']:
            out['reference_kinds'] = ['asset']
        return out
    except (KeyError, TypeError, ValueError):
        return out


def bindings(model, req) -> list[tuple[dict, str]]:
    """Map local pictures to declared roles. No model-id switches, URLs or invented IDs."""
    from mc import desk_engines as eng
    if not model.inputs.get('picture_ready'):
        raise eng.EngineError('invalid_input', model.inputs.get('picture_refusal') or
                              'Higgsfield has not supplied a usable picture contract for this model')
    roles = {p['role'] for p in model.inputs['picture_inputs']}
    rows = []
    if req.first_frame:
        rows.append((req.first_frame, 'start_image'))
    if req.last_frame:
        rows.append((req.last_frame, 'end_image'))
    if req.reference_images:
        ref_roles = [r for r in ('image_references', 'reference_image', 'image') if r in roles]
        if len(ref_roles) != 1 or len(req.reference_images) > model.inputs['reference_images_max']:
            raise eng.EngineError('invalid_input', 'This model has no unambiguous slot for these reference pictures')
        rows.extend((ref, ref_roles[0]) for ref in req.reference_images)
    if any(role not in roles for _, role in rows):
        raise eng.EngineError('invalid_input', 'This model does not accept the selected picture role')
    return rows
