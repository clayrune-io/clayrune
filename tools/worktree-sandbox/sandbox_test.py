"""Sandbox proof for per-agent worktree isolation (b264200a).

Runs entirely inside a throwaway git repo in a temp dir — NEVER touches the
Clayrune working tree. Simulates two concurrent agents doing the thing that
actually breaks today: read a file, think for a moment, write it back.

Scenario A — SHARED TREE (today's behavior): both agents work in one checkout.
Scenario B — PER-AGENT WORKTREES (the fix): each agent gets its own checkout,
             then the work is merged back.

Prints a measured verdict for each: how many agents' edits SURVIVED.

Usage:
    python tools/worktree-sandbox/sandbox_test.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from mc import project_sync  # noqa: E402
import mc.agent_worktree as w  # noqa: E402


def git(cwd, *args, check=True):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} failed: {r.stderr.strip()}')
    return r.stdout.strip()


def make_sandbox_repo() -> Path:
    """A throwaway repo with one shared source file both agents will edit."""
    d = Path(tempfile.mkdtemp(prefix='clayrune_wt_sandbox_'))
    git(d, 'init', '-q')
    git(d, 'config', 'user.email', 'sandbox@test.local')
    git(d, 'config', 'user.name', 'Sandbox')
    # A file with clearly separate regions — the realistic case is two agents
    # working on DIFFERENT features that happen to live in one file.
    (d / 'app.py').write_text(
        'def feature_one():\n'
        '    return "original one"\n'
        '\n'
        '\n'
        'def feature_two():\n'
        '    return "original two"\n',
        encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n', encoding='utf-8')
    git(d, 'add', '.')
    git(d, 'commit', '-q', '-m', 'baseline')
    return d


def agent_edit(tree: Path, func: str, new_body: str, think_secs: float):
    """Simulate one agent turn: READ the whole file, pause (the LLM 'thinking'
    window where the other agent races in), then WRITE the whole file back.
    Whole-file rewrite is exactly what an Edit/Write tool does."""
    f = tree / 'app.py'
    content = f.read_text(encoding='utf-8')      # READ
    time.sleep(think_secs)                        # THINK (the race window)
    updated = content.replace(f'return "original {func}"',
                              f'return "{new_body}"')
    f.write_text(updated, encoding='utf-8')       # WRITE BACK


def scenario_shared(repo: Path) -> dict:
    """Both agents in ONE checkout — today's behavior."""
    t1 = threading.Thread(target=agent_edit, args=(repo, 'one', 'AGENT-A WORK', 0.30))
    t2 = threading.Thread(target=agent_edit, args=(repo, 'two', 'AGENT-B WORK', 0.15))
    t1.start(); t2.start(); t1.join(); t2.join()
    final = (repo / 'app.py').read_text(encoding='utf-8')
    return {
        'a_survived': 'AGENT-A WORK' in final,
        'b_survived': 'AGENT-B WORK' in final,
    }


def scenario_worktrees(repo: Path) -> dict:
    """Each agent in its OWN worktree, then merged back."""
    project = {'id': 'sandbox', 'project_path': str(repo)}
    okA, pA = w.create(project, 'agentA')
    okB, pB = w.create(project, 'agentB')
    if not (okA and okB):
        return {'error': f'worktree create failed: {pA} / {pB}'}
    pA, pB = Path(pA), Path(pB)

    t1 = threading.Thread(target=agent_edit, args=(pA, 'one', 'AGENT-A WORK', 0.30))
    t2 = threading.Thread(target=agent_edit, args=(pB, 'two', 'AGENT-B WORK', 0.15))
    t1.start(); t2.start(); t1.join(); t2.join()

    # Each agent commits in its own tree (what a real agent does at turn end).
    for p, who in ((pA, 'A'), (pB, 'B')):
        git(p, 'add', 'app.py')
        git(p, 'commit', '-q', '-m', f'agent {who} work')

    isolated = {
        'a_has_own': 'AGENT-A WORK' in (pA / 'app.py').read_text(encoding='utf-8'),
        'b_has_own': 'AGENT-B WORK' in (pB / 'app.py').read_text(encoding='utf-8'),
    }

    # Propagate A into B — the auto-rebase step.
    status, _detail = w.sync_into(project, 'agentB', w.branch_name('agentA'))
    merged = (pB / 'app.py').read_text(encoding='utf-8')

    result = {
        **isolated,
        'merge_status': status,
        'a_survived': 'AGENT-A WORK' in merged,
        'b_survived': 'AGENT-B WORK' in merged,
    }
    for sid in ('agentA', 'agentB'):
        w.remove(project, sid, delete_branch=True)
    return result


def main():
    project_sync.register(0, None, lambda p, m: None,
                          lambda p: None, lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    w.register(lambda p, m: None, lambda p: None, lambda m: None)

    print('=' * 62)
    print(' SANDBOX: does per-agent worktree isolation prevent clobbering?')
    print('=' * 62)

    repo = make_sandbox_repo()
    print(f'\nthrowaway repo: {repo}')
    print('two agents concurrently edit DIFFERENT functions in the SAME file\n')

    try:
        print('-' * 62)
        print('SCENARIO A — shared tree (today)')
        print('-' * 62)
        a = scenario_shared(repo)
        surv_a = sum([a['a_survived'], a['b_survived']])
        print(f"  agent A edit survived : {a['a_survived']}")
        print(f"  agent B edit survived : {a['b_survived']}")
        print(f"  >> {surv_a}/2 agents' work survived"
              f"{'   <-- SILENT DATA LOSS' if surv_a < 2 else ''}")

        # Reset the sandbox for a fair comparison.
        git(repo, 'checkout', '-q', '--', 'app.py')

        print()
        print('-' * 62)
        print('SCENARIO B — per-agent worktrees (the fix)')
        print('-' * 62)
        b = scenario_worktrees(repo)
        if 'error' in b:
            print('  ERROR:', b['error'])
            return 1
        surv_b = sum([b['a_survived'], b['b_survived']])
        print(f"  each agent kept own work : A={b['a_has_own']} B={b['b_has_own']}")
        print(f"  merge status             : {b['merge_status']}")
        print(f"  agent A edit survived    : {b['a_survived']}")
        print(f"  agent B edit survived    : {b['b_survived']}")
        print(f"  >> {surv_b}/2 agents' work survived")

        print()
        print('=' * 62)
        print(f'  VERDICT   shared tree: {surv_a}/2 survived'
              f'   |   worktrees: {surv_b}/2 survived')
        print('=' * 62)
        return 0 if surv_b == 2 else 1
    finally:
        shutil.rmtree(repo, ignore_errors=True)
        print(f'\nsandbox removed (Clayrune working tree untouched)')


if __name__ == '__main__':
    raise SystemExit(main())
