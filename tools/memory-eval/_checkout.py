"""Which project, and which transcripts, belong to THIS checkout (MC-1179 TL26).

Five probes used to find a project's Claude Code transcripts with
`'mission-control' in <dir name>`, and four tools carried the project id as a
literal. Both are true of one install only: a clone under another folder name
found no sessions and printed "0 tasks" as if that were a measurement.

Everything here is derived from the checkout:

  * transcript directories: the encoding `mc.memory._encode_project_path` gives
    this checkout's path (the helper `_harness.wire` already uses), in the three
    flattened variants the CLI is known to write (`_` -> `-`, `.` -> `-`, both;
    see `ClaudeRuntime._encoded_dir_candidates`), plus the per-agent worktree
    directories that share it as a prefix (`<encoded>--clayrune-agents-<sid>`).
  * project id: the project record whose `project_path` is this checkout, found
    by `find_project` in tools/merge-review-check.py. That is the one existing
    lookup; it is loaded by path (the file name has a hyphen) rather than copied.

Run from an agent worktree, "this checkout" means the project's own checkout, the
parent of `.clayrune/agents/<sid>`: a worktree has no transcripts or record of
its own that a measurement would want.

Never imports `server` (see _harness.py).
"""
import glob
import importlib.util
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_MRC = _REPO / 'tools' / 'merge-review-check.py'
_EXCLUDED = ('scratch', 'demo')   # same exclusion the five filters carried


class NoTranscriptDir(SystemExit):
    """No transcript directory for this checkout. A SystemExit so a probe's
    `main` stops with the message; fact_canary_probe catches it to report the
    one section as not measured and carry on."""


def canonical_checkout(path):
    """`path` with a trailing `.clayrune/agents/<sid>` worktree suffix removed."""
    p = Path(path).resolve()
    parts = p.parts
    for i in range(len(parts) - 2):
        if parts[i] == '.clayrune' and parts[i + 1] == 'agents':
            return Path(*parts[:i])
    return p


def default_checkout():
    return canonical_checkout(_REPO)


def _encode(path):
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    import mc.memory as m
    return m._encode_project_path(str(path))


def encoded_variants(checkout):
    enc = _encode(checkout)
    if not enc:
        return []
    out = []
    for v in (enc, enc.replace('_', '-'), enc.replace('.', '-'),
              enc.replace('_', '-').replace('.', '-')):
        if v not in out:
            out.append(v)
    return out


def transcript_dirs(claude_projects, checkout=None):
    """Directory names under `claude_projects` holding this checkout's sessions
    (the checkout's own and its agent worktrees). May be empty."""
    checkout = Path(checkout) if checkout else default_checkout()
    variants = [v.casefold() for v in encoded_variants(checkout)]
    out = []
    for name in sorted(os.listdir(claude_projects)):
        low = name.casefold()
        for v in variants:
            if low == v:
                break
            if low.startswith(v + '-') and not any(
                    x in low[len(v):] for x in _EXCLUDED):
                break
        else:
            continue
        out.append(name)
    return out


def transcript_files(claude_projects, checkout=None):
    """Every `*.jsonl` under this checkout's transcript directories.

    Raises NoTranscriptDir when no directory matched, so "this checkout has no
    sessions on disk" is reported as that and not as a measured zero."""
    checkout = Path(checkout) if checkout else default_checkout()
    root = str(claude_projects)
    dirs = transcript_dirs(root, checkout) if os.path.isdir(root) else []
    if not dirs:
        variants = encoded_variants(checkout)
        raise NoTranscriptDir(
            f'no transcript directory found for this checkout ({checkout}): '
            f'looked under {root} for {variants[0] if variants else "<unencodable path>"}'
            f' (and its flattened and worktree variants). Nothing was measured.')
    files = []
    for d in dirs:
        files += glob.glob(os.path.join(root, d, '*.jsonl'))
    return files


def _find_project():
    spec = importlib.util.spec_from_file_location('_mc_merge_review_check', _MRC)
    if not (spec and spec.loader):
        raise SystemExit(f'cannot load {_MRC}')
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.find_project


def project_id(project_path=None):
    """Id of the project record whose project_path is `project_path` (default:
    this checkout). Exits with a message naming `--project` when none matches."""
    checkout = canonical_checkout(project_path) if project_path else default_checkout()
    data_root = Path(os.environ.get('MC_DATA_DIR') or default_checkout())
    rec = _find_project()(data_root, checkout)
    if not rec:
        raise SystemExit(
            f'no project record under {data_root / "data" / "projects"} has '
            f'project_path {checkout}; pass --project <id> (or set MC_DATA_DIR '
            f'to the install that registers this checkout).')
    return rec['id']
