"""Server-free wiring for the memory-eval probes. NO PROBE MAY IMPORT `server`.

WHY THIS FILE EXISTS (S0, 2026-08-16). `scorer_ab.py` used to call
`importlib.import_module("server")` just to get `mc.memory` initialised. That
import has a side effect nobody looked for, and it was verified end-to-end during
the redesign review:

    scorer_ab main -> import server -> mc/blueprints/remote_routes.py:65
        import mc_remote -> tunnel autostart daemon thread -> supervisor
        -> mc_remote/cloudflared.py reap_orphans() with keep_pid=None
        -> kills every cloudflared PID in the shared ledger

So running the eval killed the operator's tunnel. That was survivable while the
probes were run by hand; the plan's nightly eval schedule (S11) would have taken
the tunnel down every night. Hence: the probes wire `mc.memory` directly and
`assert_no_server()` fails loudly if anything drags `server` back in.

The underlying `reap_orphans(keep_pid=None)` behaviour is a remote-access defect
and is deliberately NOT fixed here — filed separately. This file only stops the
memory probes from tripping it.

Also fixes two things that made the old probe measure the wrong system:

  * It passed neither `expand=` nor the live `read_floor_topk`, so it reported the
    corpus as far darker than production actually sees it (27 dark vs 15).
    `live_signature()` reads what production reads, from the same config.
  * It hardcoded one operator's absolute project path into a tracked file.
    `repo_root()` derives it.

`--corpus-snapshot` support: pass a snapshot directory (as written by
`tools/memory-snapshot.py`) and both arms of a paired run read one FROZEN corpus,
so an A/B is not silently comparing two different sets of notes.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

_TMP_ROOTS = []


def repo_root():
    """The repo this file lives in. Derived, never hardcoded — a tracked file
    must not carry one machine's absolute paths (CLAUDE.md repo rule)."""
    return Path(__file__).resolve().parent.parent.parent


def assert_no_server():
    """Fail loudly if `server` reached sys.modules.

    This is the PRIMARY gate for S0, not a `grep -c cloudflared` over the output:
    the reaper runs on a daemon thread, so a grep races it and can pass while the
    import genuinely happened. Module presence is deterministic.
    """
    if 'server' in sys.modules:
        raise RuntimeError(
            "server was imported — the tunnel reaper may have fired. "
            "A memory probe must never import server; see this file's docstring.")


def wire(corpus_snapshot=None):
    """Initialise `mc.memory` without importing `server`. Returns (module, project).

    corpus_snapshot: a directory written by tools/memory-snapshot.py. When given,
    the memory corpus is read from a frozen copy instead of the live dir.
    """
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from mc import state
    cfg_path = root / 'config.json'
    if cfg_path.exists():
        state.CONFIG.update(json.loads(cfg_path.read_text(encoding='utf-8')))

    import mc.memory as m

    claude_home = Path.home() / '.claude' / 'projects'
    project = {'id': 'mission_control', 'project_path': str(root)}

    def noop(*a, **k):
        return None

    m.wire(data_dir=root / 'data' / 'projects',
           memory_dir=root / 'data' / 'memory',
           claude_home=claude_home,
           session_size_limit=10 * 1024 * 1024,
           popen_flags=0, startupinfo=None,
           load_project_fn=noop, get_manager_fn=noop, resolve_claude_fn=noop,
           register_process_fn=noop, read_agent_stream_fn=noop,
           hide_windows_delayed_fn=noop)

    if corpus_snapshot:
        # _get_memory_path resolves CLAUDE_HOME/<encoded>/memory/MEMORY.md, so a
        # snapshot is mounted by rebuilding that layout under a temp CLAUDE_HOME
        # and re-wiring. Copying ~1 MB is cheaper than monkeypatching the path
        # resolver, and it exercises the real resolver rather than a stand-in.
        snap = Path(corpus_snapshot)
        src = snap / 'memory' if (snap / 'memory').is_dir() else snap
        if not src.is_dir():
            raise SystemExit(f'no corpus at {src}')
        tmp_home = Path(tempfile.mkdtemp(prefix='mc-eval-corpus-'))
        _TMP_ROOTS.append(tmp_home)
        encoded = m._encode_project_path(str(root))
        if not encoded:
            raise SystemExit(f'could not encode project path {root}')
        dest = tmp_home / encoded / 'memory'
        dest.mkdir(parents=True)
        for f in src.glob('*.md'):
            shutil.copy2(f, dest / f.name)
        m.wire(data_dir=root / 'data' / 'projects',
               memory_dir=root / 'data' / 'memory',
               claude_home=tmp_home,
               session_size_limit=10 * 1024 * 1024,
               popen_flags=0, startupinfo=None,
               load_project_fn=noop, get_manager_fn=noop, resolve_claude_fn=noop,
               register_process_fn=noop, read_agent_stream_fn=noop,
               hide_windows_delayed_fn=noop)

    assert_no_server()
    return m, project


def live_signature():
    """(topk, expand) exactly as production passes them.

    agent_routes.py reads state.CONFIG per context build, defaulting to 6 and 2.
    A probe that hardcodes topk=3 and omits expand is measuring a system nobody
    runs — that is how the dark set was over-reported as 30.
    """
    from mc import state
    return (int(state.CONFIG.get('read_floor_topk', 6)),
            int(state.CONFIG.get('read_floor_link_expand', 2)))


def cleanup():
    for p in _TMP_ROOTS:
        shutil.rmtree(p, ignore_errors=True)
    _TMP_ROOTS.clear()


def topic_files(m, project):
    """Topic-file names — the denominator for reachability. Excludes MEMORY.md
    and the archive, which are not topic files."""
    mem_path = m._get_memory_path(project)
    arch = m._get_archive_path(project).name
    return sorted(f.name for f in mem_path.parent.glob('*.md')
                  if f.name not in (mem_path.name, arch))
