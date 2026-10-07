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


def _merge_head_commits(repo: Path) -> list[tuple[str, list[str]]]:
    """(commit, [clayrune/agent/* branches containing it]) for every commit
    being merged into `repo` right now that some agent branch owns.

    Git has NOT written MERGE_HEAD yet when `pre-merge-commit` runs for a fresh
    auto-merge (measured on git 2.51: the file is absent, and so is MERGE_MSG);
    it exists only when a merge is being concluded with `git commit` (the
    commit-msg hook's case). So read every MERGE_HEAD line when the file
    exists, and otherwise the arguments git exports to the hook as
    GIT_REFLOG_ACTION -- "merge <args>", or "pull <args>" when the merge was
    started by `git pull` -- resolving each word to a commit.

    The check is of that exact commit, not of any branch's tip. Matching only
    branch tips let a rejected merge be finished by advancing the agent branch
    first (the pending MERGE_HEAD then pointed at no tip), and let a merge of
    `<agent branch>~1` or a copy-named branch through.

    Owners are the agent branches that contain the commit and were not
    created at or after it (`_created_at`): a commit a branch was merely forked
    FROM is not its work, and it could never record a review for it (the
    review route accepts tip SHAs only). A commit no agent branch produced is
    ungated only when a non-agent branch other than HEAD also holds it (an
    ordinary feature branch an agent forked from); otherwise every containing
    agent branch owns it, so deleting and recreating a branch at a rejected
    commit does not launder it."""
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
    _, head_ref = _git(repo, 'symbolic-ref', '-q', '--short', 'HEAD')
    found: list[tuple[str, list[str]]] = []
    for sha in dict.fromkeys(s.lower() for s in shas):
        if _git(repo, 'merge-base', '--is-ancestor', sha, 'HEAD')[0]:
            continue                   # already on the base: lands nothing new
        ok, out = _git(repo, 'for-each-ref', '--contains', sha,
                       '--format=%(refname:short)', 'refs/heads/')
        names = out.splitlines() if ok else []
        agents = [b for b in names if b.startswith(gate.BRANCH_PREFIX)]
        if not agents:
            continue
        owners = [b for b in agents if not _forked_from(repo, b, sha)]
        if not owners:
            if any(b != head_ref and not b.startswith(gate.BRANCH_PREFIX) for b in names):
                continue
            owners = agents            # nobody else holds it: fail closed
        found.append((sha, owners))
    return found


def _created_at(repo: Path, branch: str) -> str:
    """Commit `branch` was created at: its oldest reflog entry, '' when the
    reflog is gone."""
    ok, out = _git(repo, 'reflog', 'show', '--format=%H', f'refs/heads/{branch}', '--')
    lines = out.split() if ok else []
    return lines[-1] if lines else ''


def _forked_from(repo: Path, branch: str, sha: str) -> bool:
    """True when `branch` already contained `sha` the moment it was created.
    No reflog = False, so the branch is treated as owning it (fails closed)."""
    start = _created_at(repo, branch)
    return bool(start) and _git(repo, 'merge-base', '--is-ancestor', sha, start)[0]


def commit_hold_reason(project_id: str, sha: str, owners: list[str]) -> str:
    """'' when some owning agent branch has a pass for exactly `sha` and none
    requested changes on it. A commit an agent branch was forked FROM (another
    agent's reviewed tip) is owned by both branches; the review under the
    branch that produced it is enough."""
    verdicts = {b: gate.verdict_for(project_id, b, sha) for b in owners}
    rejected = [b for b, v in verdicts.items() if v == gate.CHANGES_REQUESTED]
    if rejected:
        return gate.hold_reason(project_id, rejected[0], sha)
    if any(v == gate.PASS for v in verdicts.values()):
        return ''
    return gate.hold_reason(project_id, owners[0], sha)


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
    if a.merge_head:
        commits = _merge_head_commits(repo)
        if not commits:
            return 0                   # not a merge of an agent branch: nothing to gate
    else:
        commits = []

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
    project = dict(project, project_path=str(repo))
    if not a.merge_head:               # CLI mode: the branch's current tip
        commits = [(gate.branch_tip(project, a.branch), [a.branch])]

    status = 0
    for sha, owners in commits:
        held = (commit_hold_reason(project['id'], sha, owners) if sha
                else gate.hold_reason(project['id'], owners[0], sha))
        if held:
            print(f'BLOCKED: {held}. Record a pass for this exact commit '
                  f'(POST /api/project/{project["id"]}/agent/'
                  f'{owners[0][len(gate.BRANCH_PREFIX):]}/review) before merging.',
                  file=sys.stderr)
            status = 1
        else:
            print(f'ok: {sha[:8]} ({", ".join(owners)}) has a pass review on record')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
