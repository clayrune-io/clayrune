"""The Desk — signal producers. Fills the feed from what the projects actually did.

Spec: `docs/THE_DESK_SPEC.md`. Scan: `docs/research/SOCIAL_WORKSPACE_FIELD_SCAN.md`.

THIS MODULE IS THE DIFFERENTIATOR, so it is worth being precise about why. The
2026-09-09 field scan went looking for products that derive content from the
user's OWN work and found the category almost empty, and uniformly shaped:

  * LangChain's social-media-agent takes a GitHub URL a human PASTED into Slack;
  * humanwhocodes/social-changelog reads ONE GitHub release and prints to stdout;
  * the n8n and GitHub Actions templates fire on a release webhook.

Every one is release-triggered and single-shot. None watches a project, none
sees more than one repo, and none sees anything that is not a repo — no backlog,
no journals, no agent activity. They are a webhook with a prompt attached.

Clayrune already holds all of that, for every project, continuously. That is the
whole advantage, and this module is where it becomes real. If a future refactor
reduces this to "on git push, draft a post", the Desk has become the thing the
scan found a dozen copies of.

WHAT IT READS (local only — no network, asserted by test):
  * git log, per project, since a per-project watermark
  * backlog items that reached `done` since a per-project watermark

WHAT IT DOES NOT DO: judge. Everything harvested lands in the feed with a
`story_score`, and MOST ENTRIES WILL NEVER BECOME POSTS. That is by design — the
feed is the evidence, the campaign board is the argument, and Ron's veto is the
filter. `desk.STORY_SCORE_FLOOR` only decides what gets surfaced first.

DEDUPE IS BY `ref`, NOT BY WATERMARK. A watermark alone re-imports the world the
first time it is lost or a store is restored from backup, and a feed that
duplicates every commit is a feed nobody reads. The watermark is the fast path;
the ref check is the correctness one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable
import subprocess

from mc import desk as _desk
from mc.core import _log, now_iso

# -- wired by server.py -------------------------------------------------------
load_projects: Callable[[], list] | None = None


def wire(*, load_projects_fn=None):
    global load_projects
    if load_projects_fn is not None:
        load_projects = load_projects_fn


# git log is bounded twice on purpose: a project that has been dark for a year
# should not dump 4,000 commits into the feed on first harvest, and a hung git
# must not wedge the harvest of every other project behind it.
MAX_COMMITS_PER_RUN = 40
GIT_TIMEOUT_S = 15

# The same bound for the backlog, and it is NOT theoretical: measured against
# this repo on 2026-09-09, an uncapped first harvest pulled 899 done items into
# the feed in one call. A feed nobody can scan is a feed nobody reads, and old
# history is not marketing material — nobody announces a feature from two years
# ago. Newest-first, capped, and the watermark then moves past the rest for good.
MAX_BACKLOG_PER_RUN = 40

# Commits that are pure plumbing. The scorer already demotes these, but keeping
# them out of the feed entirely matters more here than a low score would: the
# feed is meant to be readable by a human scanning for a story, and merge
# commits are the single largest source of noise in this repo's history.
_SKIP_SUBJECT = (
    'merge branch', 'merge remote', 'merge pull request', 'merge —', 'merge -',
)


def _git(project_path: Path, *args: str) -> str | None:
    """Run a read-only git command. Returns None rather than raising."""
    try:
        r = subprocess.run(
            ['git', *args], cwd=str(project_path), capture_output=True,
            text=True, timeout=GIT_TIMEOUT_S, encoding='utf-8', errors='replace')
    except Exception as e:
        # Not a git repo, no git on PATH, or a hung index. All are ordinary and
        # none should stop the other projects harvesting — but say so, because a
        # harvester that silently returns nothing looks identical to a quiet week.
        _log(f'[desk] git {args[0]} failed in {project_path}: {e}')
        return None
    if r.returncode != 0:
        return None
    return r.stdout


# -- watermarks ---------------------------------------------------------------

def _get_watermarks() -> dict:
    with _desk._store_lock:
        return dict(_desk._read_store().get('harvest') or {})


def _set_watermark(project_id: str, source: str, value: str) -> None:
    with _desk._store_lock:
        store = _desk._read_store()
        store.setdefault('harvest', {}).setdefault(project_id, {})[source] = value
        store['harvest'][project_id]['at'] = now_iso()
        _desk._write_store(store)


def _known_refs(project_id: str) -> set[str]:
    """Every ref already in the feed for this project.

    Reads the whole feed. It is a small append-only file and correctness here is
    worth more than the read: this is what stops a lost watermark from
    duplicating the project's entire history into the feed.
    """
    refs: set[str] = set()
    for r in _desk.list_signals(project_id=project_id, limit=100000):
        ref = r.get('ref')
        if ref:
            refs.add(str(ref))
    return refs


# -- git commits --------------------------------------------------------------

def harvest_commits(project_id: str, project_path: str | Path,
                    *, limit: int = MAX_COMMITS_PER_RUN) -> list[dict]:
    path = Path(project_path)
    if not path.exists():
        return []
    marks = _get_watermarks().get(project_id) or {}
    since = marks.get('git')

    # %x1f is an ASCII unit separator — a commit subject can contain anything
    # else, including the pipes and tabs a naive delimiter would pick.
    fmt = '%H%x1f%aI%x1f%s%x1f%b%x1e'
    args = ['log', f'--max-count={limit}', f'--pretty=format:{fmt}']
    if since:
        # `since..HEAD` is empty (not an error) when the sha is an ancestor of
        # HEAD, and errors when the sha is unknown — e.g. after a rebase. The
        # rc!=0 path below then falls back to an unbounded-but-capped read,
        # which the ref dedupe makes safe.
        args.append(f'{since}..HEAD')
    out = _git(path, *args)
    if out is None and since:
        out = _git(path, 'log', f'--max-count={limit}', f'--pretty=format:{fmt}')
    if not out:
        return []

    known = _known_refs(project_id)
    made: list[dict] = []
    newest_sha = None
    for chunk in out.split('\x1e'):
        chunk = chunk.strip('\n')
        if not chunk.strip():
            continue
        parts = chunk.split('\x1f')
        if len(parts) < 3:
            continue
        sha, when, subject = parts[0].strip(), parts[1].strip(), parts[2].strip()
        body = parts[3].strip() if len(parts) > 3 else ''
        if newest_sha is None:
            newest_sha = sha
        if not sha or sha in known:
            continue
        if any(subject.lower().startswith(s) for s in _SKIP_SUBJECT):
            continue
        made.append(_desk.append_signal(
            project_id, 'commit', subject, ref=sha,
            detail=body or None, occurred_at=when or None))
        known.add(sha)

    if newest_sha:
        _set_watermark(project_id, 'git', newest_sha)
    return made


# -- backlog ------------------------------------------------------------------

def harvest_backlog(project_id: str, project: dict) -> list[dict]:
    """Items that reached `done` since we last looked.

    A shipped backlog item is the highest-quality signal in the system, and the
    one no competitor can see: it carries the ASK in the human's own words, which
    is the story, where a commit subject carries the implementation.
    """
    marks = _get_watermarks().get(project_id) or {}
    since = marks.get('backlog') or ''
    known = _known_refs(project_id)

    # An item we cannot DATE cannot be placed on a timeline, and stamping it
    # with `now` — which is what append_signal's default does — makes a
    # long-finished item masquerade as today's news at the top of the feed
    # forever. Measured on this repo: hundreds of done items carry no `done_at`.
    # Fall back to created_at, and drop the item if neither exists. A signal
    # with an invented date is worse than no signal.
    candidates = []
    for item in project.get('backlog') or []:
        if item.get('status') != 'done':
            continue
        when = item.get('done_at') or item.get('created_at') or ''
        if not when:
            continue
        if since and when <= since:
            continue
        key = item.get('key') or item.get('id')
        if not key or key in known:
            continue
        candidates.append((when, key, item))

    # Newest first, then capped — so a first harvest takes the recent past, not
    # the whole archive, and the watermark closes the door behind it.
    candidates.sort(key=lambda c: c[0], reverse=True)
    dropped = max(0, len(candidates) - MAX_BACKLOG_PER_RUN)
    if dropped:
        _log(f'[desk] {project_id}: {dropped} older done items skipped this '
             f'harvest (cap {MAX_BACKLOG_PER_RUN}) — history, not news')
    candidates = candidates[:MAX_BACKLOG_PER_RUN]

    made: list[dict] = []
    newest = since
    for when, key, item in candidates:
        made.append(_desk.append_signal(
            project_id, 'backlog', (item.get('text') or '').strip()[:400],
            ref=key, occurred_at=when))
        known.add(key)
        if when > newest:
            newest = when

    if newest and newest != since:
        _set_watermark(project_id, 'backlog', newest)
    return made


# -- the run ------------------------------------------------------------------

def harvest_project(project: dict) -> dict:
    pid = project.get('id')
    if not pid:
        return {'project_id': None, 'commits': 0, 'backlog': 0}
    commits = []
    if project.get('project_path'):
        commits = harvest_commits(pid, project['project_path'])
    backlog = harvest_backlog(pid, project)
    return {'project_id': pid, 'commits': len(commits), 'backlog': len(backlog)}


def harvest_all(projects: Iterable[dict] | None = None) -> dict:
    """Harvest every project. Never raises — one bad project is not a bad run."""
    if projects is None:
        if load_projects is None:
            return {'projects': [], 'commits': 0, 'backlog': 0,
                    'error': 'not wired'}
        try:
            projects = load_projects()
        except Exception as e:
            _log(f'[desk] harvest could not load projects: {e}')
            return {'projects': [], 'commits': 0, 'backlog': 0, 'error': str(e)}

    rows = []
    for p in projects:
        pid = p.get('id') if isinstance(p, dict) else None
        # The incognito pseudo-project is not a project and must never feed the
        # marketing surface — its whole point is that it leaves no trace.
        if not pid or pid.startswith('_'):
            continue
        try:
            rows.append(harvest_project(p))
        except Exception as e:
            _log(f'[desk] harvest failed for {pid}: {e}')
            rows.append({'project_id': pid, 'commits': 0, 'backlog': 0,
                         'error': str(e)})
    return {
        'projects': rows,
        'commits': sum(r.get('commits') or 0 for r in rows),
        'backlog': sum(r.get('backlog') or 0 for r in rows),
        'at': now_iso(),
    }
