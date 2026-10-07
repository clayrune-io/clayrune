"""Conservative picture evidence from structured MCP input schemas.

No descriptions, executable references, model-name guesses or upload parameters
are used. Unsupported JSON-Schema compositions stay unknown; evidence never
enables picture submission until the engine's media adapter is implemented.
"""
from __future__ import annotations

_FRAME_FIELDS = {'start_image', 'first_frame', 'start_image_url'}
_REFERENCE_FIELDS = {'image_references', 'reference_images', 'input_images'}
_REFERENCE_ROLES = {'image', 'reference_image', 'image_references'}
_TEXT_FIELDS = {'model', 'prompt', 'negative_prompt', 'count', 'aspect_ratio', 'duration',
                'resolution', 'audio', 'use_unlim', 'get_cost', 'mode', 'quality', 'sync', 'seed', 'params'}
MISSING = "Clayrune has not read Higgsfield's model list yet; it does so on the next price check"
PENDING = "Higgsfield's schema accepts pictures, but Clayrune's picture upload wiring is not implemented yet"


def _choices(node: dict, root: dict, depth: int = 0) -> list[dict]:
    if depth > 8 or any(k in node for k in ('allOf', 'if', 'not')):
        return []
    ref = node.get('$ref')
    if ref is not None:
        if not isinstance(ref, str) or not ref.startswith('#/') or len(node) != 1:
            return []
        target = root
        for part in ref[2:].split('/'):
            target = target.get(part.replace('~1', '/').replace('~0', '~')) if isinstance(target, dict) else None
        return _choices(target, root, depth + 1) if isinstance(target, dict) else []
    variants = node.get('oneOf', node.get('anyOf'))
    if variants is not None:
        # Sibling properties/compositions require intersection semantics, not a union guess.
        if set(node) - {'oneOf', 'anyOf', 'title', 'description', '$defs', 'definitions', 'type'}:
            return []
        if not isinstance(variants, list) or len(variants) > 32:
            return []
        return [v for child in variants if isinstance(child, dict) for v in _choices(child, root, depth + 1)]
    return [node]


def _models(node: dict) -> list:
    values = [node['const']] if 'const' in node else node.get('enum', [])
    return values if isinstance(values, list) else []


def derive(snapshot: dict | None, kind: str, model_id: str) -> dict:
    out = {'text': True, 'first_frame': False, 'last_frame': False,
           'reference_images_max': 0, 'reference_kinds': [], 'schema_state': 'missing',
           'picture_inputs': [], 'picture_refusal': MISSING}
    if snapshot is None:
        return out
    out.update(schema_state='unknown', picture_refusal=
               "Higgsfield's captured schema does not establish picture inputs for this model")
    tool_name = 'generate_video' if kind == 'video' else 'generate_image'
    tools = [t for t in snapshot.get('tools', []) if isinstance(t, dict) and t.get('name') == tool_name]
    # Duplicate definitions are ambiguous, not permission to choose a convenient one.
    if len(tools) != 1 or not isinstance(tools[0].get('inputSchema'), dict):
        return out
    root = tools[0]['inputSchema']
    matched = False
    evidence = []
    contracts = []
    closed = []
    for branch in _choices(root, root):
        props = branch.get('properties', {})
        if not isinstance(props, dict):
            continue
        params = props.get('params')
        for p in _choices(params, root) if isinstance(params, dict) else [branch]:
            pp = p.get('properties', {})
            if not isinstance(pp, dict):
                continue
            model = pp.get('model', props.get('model'))
            if not isinstance(model, dict) or model_id not in _models(model):
                continue
            matched = True
            branch_evidence = []
            slots = [(props, [], branch), (pp, ['params'], p)] if params is not None else [(pp, [], p)]
            for fields, prefix, owner in slots:
                for key, raw in fields.items():
                    if not isinstance(raw, dict):
                        continue
                    nodes = _choices(raw, root)
                    # Ambiguous media unions cannot establish one valid wire contract.
                    if len(nodes) != 1:
                        continue
                    node = nodes[0]
                    typ = node.get('type')
                    roles = []
                    if key in _FRAME_FIELDS and typ in ('string', 'object'):
                        roles = ['start_image']
                    elif key in _REFERENCE_FIELDS and typ == 'array':
                        roles = ['image_references']
                    elif key == 'medias' and typ == 'array':
                        item = node.get('items', {})
                        item_choices = _choices(item, root) if isinstance(item, dict) else []
                        if len(item_choices) == 1:
                            fields = item_choices[0].get('properties', {})
                            role = fields.get('role', {}) if isinstance(fields, dict) else {}
                            roles = _models(role) if isinstance(role, dict) else []
                    maximum = node.get('maxItems', 1) if typ == 'array' else 1
                    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100:
                        continue
                    for role in roles:
                        if isinstance(role, str) and (role == 'start_image' or role in _REFERENCE_ROLES):
                            branch_evidence.append({'tool': tool_name, 'path': prefix + [key],
                                                    'role': role, 'max_items': maximum,
                                                    'max_items_explicit': 'maxItems' in node,
                                                    'schema': node})
            # A closed model-specific object is evidence for a prompt-only contract.
            contracts.append(branch_evidence)
            evidence.extend(branch_evidence)
            closed.append(p.get('additionalProperties') is False and branch.get('additionalProperties') is False
                          and set(props) <= _TEXT_FIELDS and set(pp) <= _TEXT_FIELDS)
    if len(contracts) > 1 and any(c != contracts[0] for c in contracts[1:]):
        out['schema_state'] = 'unknown'
        return out
    if evidence:
        out.update(schema_state='documented', picture_inputs=evidence, picture_refusal=PENDING)
        out['first_frame'] = any(e['role'] == 'start_image' for e in evidence)
        refs = [e['max_items'] for e in evidence if e['role'] in _REFERENCE_ROLES]
        out['reference_images_max'] = min(refs) if refs else 0
        out['reference_kinds'] = ['asset'] if refs else []
    elif matched and all(closed):
        out['schema_state'] = 'none'
        out['picture_refusal'] = 'Higgsfield lists no picture input for this model'
    return out
