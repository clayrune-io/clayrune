"""Decide whether `POST /api/system/update` may hard-reset the checkout.

The route's fallback (`fetch` + `reset --hard origin/<branch>`) exists for one
case: the release branch was force-pushed upstream, so `pull --ff-only` can
never succeed again. The reset is also exactly what destroys unpushed work, so
before it runs this module answers one question: would the reset drop commits
that exist nowhere but this checkout? (MC-1179 pass 07, P43.)

"Local-only" is not simply `origin/<branch>..HEAD`. After an upstream
force-push, the commits the user pulled BEFORE the rewrite are no longer on
`origin/<branch>` either, yet they are not local work: they were on origin
once. The remote-tracking ref's reflog remembers every tip it pointed at, so
a commit counts as local-only only when it is reachable from neither the
current `origin/<branch>` nor any earlier tip of it. That keeps force-push
recovery working and still refuses a checkout carrying its own commits.

Pure git logic, no Flask: `git` is the route's `_git(args, cwd, timeout)`
callable, injected so this module imports nothing from the blueprints and a
test can drive it against a scratch repo.
"""

from pathlib import Path

# Newest reflog lines to consult. A remote-tracking ref's reflog gains one line
# per fetch that moved it; the cap keeps the rev-list command line short on
# Windows (each line contributes up to two 40-character shas).
_REFLOG_LIMIT = 100


def _count(git, repo_root, hide):
    """Commits reachable from HEAD but from none of `hide` (refs/shas).
    None when git could not say."""
    rc, out = git(['rev-list', '--count', 'HEAD', '--not', *hide], repo_root)
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _ref_exists(git, repo_root, ref):
    rc, _ = git(['rev-parse', '--verify', '--quiet', ref], repo_root)
    return rc == 0


def _old_tips(git, repo_root, ref):
    """Every sha the remote-tracking ref has pointed at, newest first, read
    from its reflog file ("<old> <new> <who> ..." per line). `git reflog show`
    cannot be used: it prints only the new value, and the value a force-push
    replaced exists only as the `<old>` of the line that replaced it (a fresh
    clone writes no entry of its own). Empty when there is no readable log."""
    rc, rel = git(['rev-parse', '--git-path', f'logs/{ref}'], repo_root)
    if rc != 0 or not rel.strip():
        return []
    path = Path(rel.strip().splitlines()[-1])
    if not path.is_absolute():
        path = Path(repo_root) / path
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    tips = []
    for line in reversed(lines[-_REFLOG_LIMIT:]):
        for sha in line.split(None, 2)[:2]:
            if len(sha) == 40 and sha.strip('0') and sha not in tips:
                tips.append(sha)
    return tips


def local_only_commits(git, repo_root, target):
    """How many commits on HEAD have never been on origin/<target>. None when
    that cannot be established (callers treat None as unsafe)."""
    ref = f'refs/remotes/origin/{target}'
    old_tips = _old_tips(git, repo_root, ref)
    count = _count(git, repo_root, [ref, *old_tips]) if old_tips else None
    if count is None:
        # No usable reflog, or an entry points at a collected object: fall
        # back to the strict count (every commit not on the current tip), which
        # can only over-refuse, never under-refuse.
        count = _count(git, repo_root, [ref])
    return count


def assess_resync(git, repo_root):
    """Resolve what `reset --hard` would target and whether it is safe.

    Returns {'target': <branch to reset to>, 'refusal': None | dict}. A refusal
    carries `error` (a plain sentence for the UI), `reason`, `branch`,
    `target` and `local_commits` (int, or None if unknown). Nothing is changed
    here; the caller must not reset when `refusal` is set.
    """
    rc, name = git(['rev-parse', '--abbrev-ref', 'HEAD'], repo_root)
    branch = (name or '').strip() if rc == 0 else ''
    detached = (not branch) or branch == 'HEAD'
    # Detached HEAD has always landed on the release channel.
    target = 'master' if detached else branch
    label = 'the checked-out commit' if detached else f'branch {branch}'

    if not _ref_exists(git, repo_root, f'refs/remotes/origin/{target}'):
        # A local-only branch: resetting "the current branch" to
        # origin/master (the old fallback target) would repoint it and orphan
        # its commits. Count against master where we can, for the message.
        count = (local_only_commits(git, repo_root, 'master')
                 if _ref_exists(git, repo_root, 'refs/remotes/origin/master') else None)
        lost = ('' if count is None else
                f' It carries {count} commit{"s" if count != 1 else ""} that '
                f'{"are" if count != 1 else "is"} not on origin/master.')
        return {'target': target, 'refusal': {
            'error': (f'Update refused: {label} has no origin/{target}, so a re-sync '
                      f'would reset it onto the release branch and drop its work.{lost} '
                      'Nothing was reset. Push or copy your work, or switch to master, then update.'),
            'reason': 'no_remote_branch', 'branch': branch, 'target': target,
            'local_commits': count,
        }}

    count = local_only_commits(git, repo_root, target)
    if count is None:
        return {'target': target, 'refusal': {
            'error': (f'Update refused: could not verify that {label} has no commits '
                      f'missing from origin/{target}. Nothing was reset.'),
            'reason': 'unverified', 'branch': branch, 'target': target,
            'local_commits': None,
        }}
    if count > 0:
        return {'target': target, 'refusal': {
            'error': (f'Update refused: {label} has {count} local commit'
                      f'{"s" if count != 1 else ""} that '
                      f'{"are" if count != 1 else "is"} not on origin/{target} and '
                      f'would be lost by a re-sync. Nothing was reset. Push them '
                      'or move them to another branch, then update.'),
            'reason': 'local_commits', 'branch': branch, 'target': target,
            'local_commits': count,
        }}
    return {'target': target, 'refusal': None}
