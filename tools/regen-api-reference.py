#!/usr/bin/env python3
"""Check data/agent_reference/CLAYRUNE_API.md against the route decorators in
the source.

This tool never writes the curated reference. It does two things:

1. Lists routes decorated in server.py and throughout mc/, including Flask's
   HTTP method shortcuts. A Blueprint's own url_prefix is joined. The scan
   reads source with ast because importing server starts background threads.
2. Flags drift: routes that exist in code but not in the curated reference,
   and routes referenced in the doc but no longer present in code.

The agent_reference doc stays human-curated — agents don't need every internal
route (push, CF, walkthrough, /sw.js, etc.). This tool just makes drift
visible so the curated doc can be updated deliberately.

Usage:
  python tools/regen-api-reference.py                 # report only
  python tools/regen-api-reference.py --list-all      # print every route
  python tools/regen-api-reference.py --json          # machine-readable

Exit code 0 = clean, 1 = drift detected.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / 'data' / 'agent_reference' / 'CLAYRUNE_API.md'

ROUTE_DECORATORS = frozenset({'route', 'get', 'post', 'put', 'patch', 'delete'})

# Routes intentionally NOT in the curated reference — UI-only, CF/remote
# internals, push notifications, walkthrough, internal callbacks. Keep this
# list narrow; default is "should be documented".
EXCLUDED_PREFIXES = (
    '/assets/',
    '/sw.js',
    '/manifest.json',
    '/_mc/',
    '/api/_mc/',
    '/api/_mock/',           # mock-connect (remote-access dev only)
    '/api/push/',
    '/api/remote/',
    '/api/tunnel-handshake',
    '/api/mc-callback',
    '/api/walkthrough/',
    '/api/browse/',
    '/api/list-directory',
    '/api/create-folder',
    '/api/grid-layout',
    '/api/projects/order',
    '/api/claude/',          # auth — user-managed via UI
    '/api/settings/domains',  # UI-only
    '/api/guide/',           # UI walkthrough
    '/api/plans/delete',     # UI-only (use a different mechanism)
    '/api/presence',         # passive heartbeat from UI
    '/api/popout/',          # pop-out window slot registry, UI-only
    '/v1/',                  # remote-access attestation
)

# Exact-match excludes (no prefix logic).
EXCLUDED_EXACT = frozenset({'/'})

# Path-param placeholders we want to normalize before comparing the doc to code.
# server.py uses Flask converters like `<project_id>`, `<int:pid>`, `<path:filename>`.
# The curated doc may use shorter names (`<ws>` vs `<ws_id>`); we compare shapes.
def _norm(path: str) -> str:
    path = re.sub(r'<[^>]*?:(\w+)>', r'<\1>', path)  # <int:pid> -> <pid>
    return path


def _shape(path: str) -> str:
    """Reduce path to its shape: all `<...>` placeholders become `<>`."""
    return re.sub(r'<[^>]*>', '<>', path)


def _source_files() -> list[Path]:
    return [ROOT / 'server.py'] + sorted((ROOT / 'mc').rglob('*.py'))


def _string_constants(tree: ast.AST) -> dict[str, str]:
    """Names assigned a single string literal anywhere in the module. A name
    assigned two different strings is dropped rather than guessed at."""
    seen: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            name, val = node.targets[0].id, node.value.value
            seen[name] = val if seen.get(name, val) == val else None
    return {k: v for k, v in seen.items() if v is not None}


def _resolve(node: ast.AST, consts: dict[str, str]) -> str | None:
    """Value of a route-path expression: a literal, a module string constant,
    or those joined with +. Anything else (an imported name) is None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _resolve(node.left, consts), _resolve(node.right, consts)
        return None if left is None or right is None else left + right
    return None


def _blueprint_prefixes(tree: ast.AST, consts: dict[str, str]) -> dict[str, str]:
    """{variable: url_prefix} for x = Blueprint(..., url_prefix=...)."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Call)):
            continue
        fn = node.value.func
        if (fn.id if isinstance(fn, ast.Name) else getattr(fn, 'attr', '')) != 'Blueprint':
            continue
        for kw in node.value.keywords:
            if kw.arg == 'url_prefix':
                prefix = _resolve(kw.value, consts)
                if prefix:
                    out[node.targets[0].id] = prefix
    return out


def _methods(call: ast.Call) -> list[str]:
    for kw in call.keywords:
        if kw.arg == 'methods' and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
            found = [e.value for e in kw.value.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if found:
                return found
    return ['GET']


def scan_routes_detailed(files: list[Path] | None = None):
    """Return ([(normalized_path, methods)], [unresolved "file:line" strings],
    [register_blueprint calls that carry a url_prefix this tool cannot apply]).
    Read from source with ast; nothing is imported."""
    routes: list[tuple[str, list[str]]] = []
    unresolved: list[str] = []
    unapplied: list[str] = []
    for path in (files if files is not None else _source_files()):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (OSError, SyntaxError, UnicodeDecodeError) as e:
            unresolved.append(f'{path.name}: unreadable ({e})')
            continue
        consts = _string_constants(tree)
        prefixes = _blueprint_prefixes(tree, consts)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'register_blueprint'
                    and any(kw.arg == 'url_prefix' for kw in node.keywords)):
                unapplied.append(f'{path.name}:{node.lineno}')
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr in ROUTE_DECORATORS and dec.args):
                    continue
                value = _resolve(dec.args[0], consts)
                if value is None:
                    unresolved.append(f'{path.name}:{dec.lineno}')
                    continue
                owner = dec.func.value
                prefix = prefixes.get(owner.id, '') if isinstance(owner, ast.Name) else ''
                decorator = dec.func.attr
                methods = _methods(dec) if decorator == 'route' else [decorator.upper()]
                routes.append((_norm(prefix + value), methods))
    return routes, unresolved, unapplied


def scan_routes(files: list[Path] | None = None) -> list[tuple[str, list[str]]]:
    """Return [(normalized_path, methods)] for server.py and mc/."""
    return scan_routes_detailed(files)[0]


def _expand_alternations(path: str) -> list[str]:
    """Expand compact `[a|b|c]` and `[a\\|b\\|c]` syntax into separate paths.

    The curated doc groups related endpoints like
    `/api/skills/import/[paste|folder|git]` for human readability; this
    unfolds them so drift comparison works.
    """
    if '[' not in path:
        return [path]
    m = re.search(r'\[([^\[\]]+)\]', path)
    if not m:
        return [path]
    alts_raw = m.group(1)
    # Strip backslash escapes used in markdown tables.
    alts = [a.strip().replace('\\', '') for a in alts_raw.split('|') if a.strip()]
    out: list[str] = []
    for a in alts:
        expanded = path[:m.start()] + a + path[m.end():]
        out.extend(_expand_alternations(expanded))
    return out


def doc_paths() -> set[str]:
    """Approximate set of paths mentioned in the curated reference."""
    if not DOC.exists():
        return set()
    text = DOC.read_text(encoding='utf-8')
    # Match anything that looks like an API path inside backticks.
    raw = re.findall(r'`(/api/[^`]+?)`', text)
    raw += re.findall(r'`(/_mc/[^`]+?)`', text)
    out: set[str] = set()
    for p in raw:
        base = p.split('?', 1)[0]
        for expanded in _expand_alternations(base):
            out.add(_norm(expanded))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--list-all', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    code, unresolved, unapplied = scan_routes_detailed()
    def _excluded(p: str) -> bool:
        if p in EXCLUDED_EXACT:
            return True
        return any(p.startswith(pref) for pref in EXCLUDED_PREFIXES)

    code_paths_documentable = sorted({p for p, _ in code if not _excluded(p)})
    code_paths_all = sorted({p for p, _ in code})
    documented = doc_paths()

    documented_shapes = {_shape(p) for p in documented}
    code_shapes_all = {_shape(p) for p in code_paths_all}

    missing_in_doc = [p for p in code_paths_documentable if _shape(p) not in documented_shapes]
    stale_in_doc = sorted({p for p in documented if _shape(p) not in code_shapes_all})

    if args.json:
        print(json.dumps({
            'total_routes': len(code),
            'unresolved': unresolved,
            'prefix_not_applied': unapplied,
            'documentable_routes': len(code_paths_documentable),
            'documented': sorted(documented),
            'missing_in_doc': missing_in_doc,
            'stale_in_doc': stale_in_doc,
        }, indent=2))
    elif args.list_all:
        for p, methods in code:
            tag = '  ' if _excluded(p) else '* '
            print(f'{tag}{",".join(methods):20s} {p}')
    else:
        print(f'routes in server.py + mc/: {len(code)} ({len(code_paths_documentable)} documentable paths, {len(code_paths_all) - len(code_paths_documentable)} excluded paths)')
        print(f'CLAYRUNE_API.md mentions: {len(documented)} paths')
        if unresolved:
            print(f'Not resolved to a path ({len(unresolved)}): ' + ', '.join(unresolved))
        if unapplied:
            print('register_blueprint(url_prefix=...) not applied at: ' + ', '.join(unapplied))
        if missing_in_doc:
            print(f'\nMissing in doc ({len(missing_in_doc)}):')
            for p in missing_in_doc:
                print(f'  + {p}')
        if stale_in_doc:
            print(f'\nIn doc but not in code ({len(stale_in_doc)}):')
            for p in stale_in_doc:
                print(f'  - {p}')
        if not missing_in_doc and not stale_in_doc:
            print('\nClean — reference is in sync with code.')

    return 1 if (missing_in_doc or stale_in_doc) else 0


if __name__ == '__main__':
    sys.exit(main())
