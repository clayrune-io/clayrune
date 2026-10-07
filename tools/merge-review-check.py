#!/usr/bin/env python3
"""Merge review gate check for hand merges (MC-1075).

`agent_worktree.merge_back` holds an unreviewed agent branch, but `git merge`
typed in the main checkout never goes through it. This is the same question,
asked from outside the server:

    python tools/merge-review-check.py clayrune/agent/<sid>
    python tools/merge-review-check.py --merge-head --respect-setting   # the hook

Exit 0  the branch tip has a `pass` review on record (or, with
        --respect-setting, the project has merge_requires_review off)
Exit 1  no pass on record for the CURRENT tip -- do not merge
Exit 2  could not decide (unknown project, unreadable repo): treated as a
        block by the hook, never as a pass

Reads the same store the server writes (`<data root>/data/merge_reviews/`),
by importing `mc.merge_review_gate`; it does not need the server running.

Not a lock. `git merge --no-verify` skips the hook, and a fast-forward merge
never runs `pre-merge-commit` at all -- see docs/MERGE_REVIEW_GATE.md.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mc.merge_review_gate as gate  # noqa: E402


def _git(cwd: Path, *args: str) -> tuple[bool, str]:
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', stdin=subprocess.DEVNULL)
    return r.returncode == 0, r.stdout.strip()


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def find_project(data_root: Path, repo: Path, project_id: str = '') -> dict | None:
    """The project record for this repo: by id when given, else the record whose
    project_path is this checkout."""
    pdir = data_root / 'data' / 'projects'
    if project_id:
        f = pdir / f'{project_id}.json'
        candidates = [f] if f.is_file() else []
    else:
        candidates = sorted(pdir.glob('*.json'))
    want = _norm(str(repo))
    for f in candidates:
        try:
            rec = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        rec.setdefault('id', f.stem)
        if project_id or _norm(rec.get('project_path') or '-') == want:
            return rec
    return None


def _merge_head_branches(repo: Path) -> list[str]:
    """clayrune/agent/* branches being merged into `repo` right now.

    Git has NOT written MERGE_HEAD yet when `pre-merge-commit` runs for a fresh
    auto-merge (measured on git 2.51: the file is absent, and so is MERGE_MSG);
    it exists only when a merge is being concluded with `git commit` (the
    commit-msg hook's case). So read every MERGE_HEAD line when the file
    exists, and otherwise the arguments git exports to the hook as
    GIT_REFLOG_ACTION -- "merge <args>", or "pull <args>" when the merge was
    started by `git pull` -- resolving each word and keeping those that are an
    agent branch's tip."""
    shas: list[str] = []
    ok, mh = _git(repo, 'rev-parse', '--git-path', 'MERGE_HEAD')
    if ok and mh:
        mh_path = Path(mh) if Path(mh).is_absolute() else repo / mh
        try:
            shas = [ln.strip() for ln in mh_path.read_text(encoding='utf-8').splitlines()
                    if ln.strip()]
        except OSError:
            pass                       # no merge in progress: fall through to the args
    words = os.environ.get('GIT_REFLOG_ACTION', '').split()
    if not shas and words[:1] in (['merge'], ['pull']):
        for word in words[1:]:
            if word.startswith('-'):
                continue
            ok, sha = _git(repo, 'rev-parse', '-q', '--verify', word + '^{commit}')
            if ok and sha:
                shas.append(sha)
    found: list[str] = []
    for sha in shas:
        ok, out = _git(repo, 'for-each-ref', '--points-at', sha,
                       '--format=%(refname:short)', f'refs/heads/{gate.BRANCH_PREFIX}')
        for b in (out.splitlines() if ok else []):
            if b.startswith(gate.BRANCH_PREFIX) and b not in found:
                found.append(b)
    return found


def _main_checkout(repo: Path) -> Path:
    """The main checkout `repo` belongs to. A linked worktree has its own
    toplevel, but the project record names the main checkout, and hooks are
    shared through the common git dir, so a merge typed in a linked worktree
    runs this check from the worktree's path."""
    ok, common = _git(repo, 'rev-parse', '--git-common-dir')
    if not ok or not common:
        return repo
    common_dir = Path(common) if Path(common).is_absolute() else repo / common
    common_dir = Path(os.path.realpath(common_dir))
    return common_dir.parent if common_dir.name == '.git' else repo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or '').split('\n\n')[0])
    ap.add_argument('branch', nargs='?', help='clayrune/agent/<session id>')
    ap.add_argument('--merge-head', action='store_true',
                    help='check every clayrune/agent/* branch being merged right now (hook mode)')
    ap.add_argument('--respect-setting', action='store_true',
                    help="exit 0 when the project's merge_requires_review is off")
    ap.add_argument('--repo', default='.', help='the checkout being merged into (default: cwd)')
    ap.add_argument('--project', default='', help='project id (default: match by project_path)')
    ap.add_argument('--data-root', default=str(ROOT),
                    help='Clayrune install dir holding data/ (default: this checkout)')
    a = ap.parse_args(argv)
    if bool(a.branch) == a.merge_head:
        ap.error('give exactly one of <branch> or --merge-head')

    ok, top = _git(Path(a.repo), 'rev-parse', '--show-toplevel')
    if not ok:
        print(f'merge-review-check: {a.repo} is not a git checkout', file=sys.stderr)
        return 2
    repo = Path(top)
    branches = _merge_head_branches(repo) if a.merge_head else [a.branch]
    if not branches:
        return 0                       # not a merge of an agent branch: nothing to gate

    data_root = Path(a.data_root)
    project = (find_project(data_root, repo, a.project)
               or find_project(data_root, _main_checkout(repo), a.project))
    if project is None:
        print(f'merge-review-check: no Clayrune project for {repo} under {data_root}; '
              f'pass --project/--data-root. Not treating this as a pass.', file=sys.stderr)
        return 2
    if a.respect_setting and not gate.enabled(project):
        return 0
    gate.STORE_DIR = data_root / 'data' / gate.STORE_DIRNAME

    status = 0
    for branch in branches:
        project = dict(project, project_path=str(repo))
        tip = gate.branch_tip(project, branch)
        held = gate.hold_reason(project['id'], branch, tip)
        if held:
            print(f'BLOCKED: {held}. Record a pass for this exact commit '
                  f'(POST /api/project/{project["id"]}/agent/'
                  f'{branch[len(gate.BRANCH_PREFIX):]}/review) before merging.',
                  file=sys.stderr)
            status = 1
        else:
            print(f'ok: {branch} at {tip[:8]} has a pass review on record')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
