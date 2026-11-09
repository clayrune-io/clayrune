"""Private Git worktrees for resume provenance tests; no provider is started."""
import subprocess


REAL_POPEN = subprocess.Popen


def make_tree(project, tree, monkeypatch):
    project.mkdir(parents=True, exist_ok=True)
    with monkeypatch.context() as git_only:
        git_only.setattr(subprocess, 'Popen', REAL_POPEN)
        for args in (
            ['init', '-q', str(project)],
            ['-C', str(project), '-c', 'user.name=Test',
             '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty',
             '-qm', 'fixture'],
            ['-C', str(project), 'worktree', 'add', '-q', '-b', 'resume-fixture', str(tree)],
        ):
            subprocess.run(['git', *args], check=True, capture_output=True)
    guarded_popen = subprocess.Popen

    def git_or_guard(args, *a, **kw):
        if args and args[0] == 'git':
            return REAL_POPEN(args, *a, **kw)
        return guarded_popen(args, *a, **kw)

    monkeypatch.setattr(subprocess, 'Popen', git_or_guard)


def remove_tree(tree):
    (tree / '.git').unlink()
    tree.rmdir()
