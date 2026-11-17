"""MC-1205 / FE21 / FE22: the Desk is loaded by static/js/desk-v1-entry.js, not by index.html.

Pins three things that are silent when they break:
  * index.html carries no Desk script or stylesheet tag (the Desk is off the boot path);
  * nothing is both an entry-loaded module and a static import target outside the
    entry's BARE set (that module would run twice, under two URLs: FE22);
  * no boot-time file statically imports a Desk module, which would pull the Desk
    back onto the boot path through the side door.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / 'static' / 'js'
CSS = ROOT / 'static' / 'css'
INDEX = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
ENTRY = (JS / 'desk-v1-entry.js').read_text(encoding='utf-8')

# Boot-time Desk pieces that stay in index.html on purpose (tiny, self-contained).
BOOT_DESK_FILES = {'desk-v1-loading'}
# A sheet its own module links when it is first used (desk-v1-render-scenes.js); it never had a tag.
SELF_LOADED_STYLES = {'desk-v1-render-scenes'}

_IMPORT = re.compile(
    r"""(?:^|\n)\s*(?:import|export)\s[^;'"]*?from\s*['"](\.{1,2}/[^'"]+)['"]|(?:^|\n)\s*import\s*['"](\.{1,2}/[^'"]+)['"]""")


def _array(name):
    m = re.search(r'const %s = \[(.*?)\];' % name, ENTRY, re.S)
    assert m, 'desk-v1-entry.js has no %s list' % name
    return re.findall(r"'([^']+)'", m.group(1))


MODULES = _array('MODULES')
STYLES = _array('STYLES')
_bare = re.search(r'const BARE = new Set\(\[(.*?)\]\);', ENTRY, re.S)
assert _bare, 'desk-v1-entry.js has no BARE set'
BARE = set(re.findall(r"'([^']+)'", _bare.group(1)))


def _static_imports():
    """{target module stem: [importer stems]} for every relative static import under static/js."""
    out = {}
    for f in JS.rglob('*.js'):
        text = f.read_text(encoding='utf-8')
        for m in _IMPORT.finditer(text):
            tgt = m.group(1) or m.group(2)
            stem = Path(tgt).name[:-3] if tgt.endswith('.js') else Path(tgt).name
            out.setdefault(stem, []).append(f.stem)
    return out


def test_index_html_has_no_desk_script_or_stylesheet_tags():
    tags = re.findall(r'<(?:script|link)[^>]+(?:src|href)="/static/(?:js|css)/(desk-v1-[^".?]+)\.(?:js|css)', INDEX)
    stray = [t for t in tags if t not in BOOT_DESK_FILES]
    assert not stray, 'Desk files must be added to desk-v1-entry.js, not index.html: %s' % stray[:5]


def test_boot_files_are_the_loader_and_the_loading_state():
    assert re.search(r'<script type="module" src="/static/js/desk-load\.js"', INDEX)
    assert re.search(r'<script type="module" src="/static/js/desk-v1-loading\.js"', INDEX)
    assert re.search(r'<link rel="stylesheet" href="/static/css/desk-v1-loading\.css"', INDEX)


def test_entry_lists_are_unique_and_every_file_exists():
    assert len(MODULES) == len(set(MODULES)), 'a module is listed twice'
    assert len(STYLES) == len(set(STYLES)), 'a stylesheet is listed twice'
    assert [m for m in MODULES if not (JS / (m + '.js')).exists()] == []
    assert [s for s in STYLES if not (CSS / (s + '.css')).exists()] == []


def test_every_desk_stylesheet_is_loaded_by_the_entry():
    on_disk = {p.stem for p in CSS.glob('desk-v1-*.css')} - BOOT_DESK_FILES - SELF_LOADED_STYLES
    assert on_disk - set(STYLES) == set(), 'stylesheet on disk but not in the entry: %s' % sorted(on_disk - set(STYLES))


def test_every_desk_script_is_reachable_from_the_entry():
    on_disk = {p.stem for p in JS.glob('desk-v1-*.js')} - BOOT_DESK_FILES - {'desk-v1-entry'}
    imports = _static_imports()
    reachable = set(MODULES)
    frontier = list(MODULES)
    while frontier:
        cur = frontier.pop()
        for tgt, importers in imports.items():
            if cur in importers and tgt not in reachable:
                reachable.add(tgt)
                frontier.append(tgt)
    assert on_disk - reachable == set(), 'desk script never loaded: %s' % sorted(on_disk - reachable)


def test_no_module_runs_twice_fe22():
    imports = _static_imports()
    twice = sorted(m for m in MODULES if m not in BARE and m in imports)
    assert twice == [], 'loaded by the entry (?v= url) AND statically imported (bare url): %s' % twice
    # a BARE entry must really be imported by a bare path from somewhere, or it should not be BARE
    assert sorted(b for b in BARE if b not in imports) == []
    assert BARE <= set(MODULES)


def test_boot_files_do_not_import_the_desk_back_in():
    imports = _static_imports()
    leaks = sorted({f'{imp} -> {m}' for m in MODULES for imp in imports.get(m, []) if not imp.startswith('desk-v1-')})
    assert leaks == [], 'a boot-time file statically imports a Desk module: %s' % leaks
