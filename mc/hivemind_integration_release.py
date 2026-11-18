"""A person releases a held reviewed integration (MC-1179 OS33).

`held` is terminal and sticky by design: nothing automatic may pick another
attempt, replace a reviewer or reuse a branch. Before this module the only way
out was editing a digest-protected state file by hand. `release` is the one
human-chosen exit. It archives the held attempt and nothing else:

  * the integration checkout is moved, not deleted (`git worktree move`);
  * the integration branch is renamed, not deleted (`git branch -m`);
  * the state file is moved into `integration-archive/<stamp>/` beside a
    `release.json` saying who released what, when.

Nothing is merged, reset or removed, and the worker branches are untouched.
With the state file gone the next tick starts a fresh attempt from the pinned
base. Every step is conditional on the thing still existing, so a release that
died half way can simply be asked for again.

Caller gating (human only) lives on the route, not here.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mc import hivemind_integration as git, hivemind_integration_cycle as cycle, hivemind_roster as roster
from mc.atomic_json import write_json_atomic
from mc.core import _log, now_iso
from mc.landing_serialization import LandingBusy, landing
from mc.merge_review_gate import ReviewRefused


def _reviewer_still_owned(st: dict) -> bool:
    current = st.get('current') or {}
    review = current.get('reviewer') or {}
    return bool(review.get('state') == 'pending' or review.get('stop_error')
                or current.get('reviewer_cleanup_error'))


def release(api: Any, hid: str) -> dict:
    """Archive a held attempt. Raises ReviewRefused (status + message) when it must not."""
    manifest = api._hm_load_manifest(hid)
    if not manifest:
        raise ReviewRefused(404, 'hivemind not found')
    if not roster.is_roster_manifest(manifest):
        raise ReviewRefused(409, 'only a reviewed (roster) integration has a hold to release')
    with cycle._transaction(api, hid):
        st = cycle.read(api, hid)
        if st is None:
            raise ReviewRefused(404, 'no integration attempt to release')
        if st['status'] != 'held':
            raise ReviewRefused(409, f"only a held attempt can be released; this one is {st['status']}")
        if _reviewer_still_owned(st):
            raise ReviewRefused(409, 'the reviewer of this attempt is not stopped; release it after it is')
        project = api.load_project(st['project_id'])
        if not project or str(Path(project['project_path']).resolve()) != st['project_path']:
            raise ReviewRefused(409, 'project missing or moved; the held attempt stays as it is')
        repo = Path(st['project_path'])
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        archived_wt = archived_branch = None
        try:
            with landing(repo):
                wt = git.integration_path(project, hid)
                if wt is not None and wt.exists():
                    dest = wt.with_name(f'{wt.name}-released-{stamp}')
                    ok, detail = git._git(repo, ['worktree', 'move', str(wt), str(dest)], 60)
                    if not ok:
                        raise ReviewRefused(409, 'cannot archive the integration checkout: ' + detail)
                    archived_wt = str(dest)
                if git._git(repo, ['branch', '--list', st['branch']], 15)[1].strip():
                    archived_branch = f"{st['branch']}-released-{stamp}"
                    ok, detail = git._git(repo, ['branch', '-m', st['branch'], archived_branch], 15)
                    if not ok:
                        raise ReviewRefused(409, 'cannot archive the integration branch: ' + detail)
        except LandingBusy as exc:
            raise ReviewRefused(409, 'the landing lock is busy; try again shortly') from exc
        archive = api._hm_dir(hid) / 'integration-archive' / stamp
        archive.mkdir(parents=True, exist_ok=True)
        write_json_atomic(archive / 'release.json', {
            'released_at': now_iso(), 'from_status': st['status'], 'reason': st.get('reason'),
            'archived_branch': archived_branch, 'archived_worktree': archived_wt}, indent=2)
        os.replace(api._hm_dir(hid) / 'integration.json', archive / 'integration.json')
        _log(f'[hivemind-integration-cycle] {hid} held attempt released by a person; archived at {archive}', flush=True)
        return {'released_status': st['status'], 'released_reason': st.get('reason'),
                'archive_dir': str(archive), 'archived_branch': archived_branch,
                'archived_worktree': archived_wt}
