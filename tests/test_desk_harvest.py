"""The Desk — signal producers (mc/desk_harvest.py).

The properties worth guarding, and why each one earns a test:

  * IDEMPOTENCE BY `ref`. A watermark alone re-imports a project's whole history
    the first time it is lost or a store is rolled back to a restore point, and
    a feed that duplicates every commit is a feed nobody reads. Tested against a
    REAL git repo built in tmp_path, and tested again with the watermark wiped.
  * A BROKEN PROJECT IS NOT A BROKEN RUN. One unreadable repo must not stop the
    other nineteen projects harvesting.
  * INCOGNITO NEVER FEEDS THE MARKETING SURFACE. `_incognito` is a pseudo-project
    whose entire point is leaving no trace; a signal from it would be a leak.
  * NO NETWORK. The harvester reads local git and the local backlog. If it ever
    grows an outbound call, the "reads your own work" claim stops being local.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk, desk_harvest  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which('git') is None,
                                reason='git not on PATH')


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'desk_signals.jsonl')
    return desk


@pytest.fixture
def repo(tmp_path):
    """A real git repo — the harvester shells out, so a fake would prove nothing."""
    d = tmp_path / 'proj'
    d.mkdir()

    def git(*args):
        subprocess.run(['git', *args], cwd=d, check=True,
                       capture_output=True, text=True)

    git('init', '-q')
    git('config', 'user.email', 'test@example.com')
    git('config', 'user.name', 'Test')
    for i, subject in enumerate([
            'feat: shipped drag-to-hire, now live',
            'chore: bump dependency',
            'Merge branch "feature/x" into master']):
        (d / f'f{i}.txt').write_text(str(i), encoding='utf-8')
        git('add', '-A')
        git('commit', '-q', '-m', subject)
    return d


# -- commits ------------------------------------------------------------------

def test_harvests_real_commits(store, repo):
    made = desk_harvest.harvest_commits('p', repo)
    subjects = [m['summary'] for m in made]
    assert 'feat: shipped drag-to-hire, now live' in subjects
    assert 'chore: bump dependency' in subjects


def test_merge_commits_are_skipped(store, repo):
    made = desk_harvest.harvest_commits('p', repo)
    assert not any(m['summary'].lower().startswith('merge branch') for m in made)


def test_shipped_scores_above_chore(store, repo):
    made = {m['summary']: m['story_score'] for m in desk_harvest.harvest_commits('p', repo)}
    assert made['feat: shipped drag-to-hire, now live'] > made['chore: bump dependency']


def test_harvest_is_idempotent(store, repo):
    first = desk_harvest.harvest_commits('p', repo)
    assert first
    second = desk_harvest.harvest_commits('p', repo)
    assert second == [], 'a second harvest with no new commits adds nothing'
    assert len(desk.list_signals(project_id='p')) == len(first)


def test_a_lost_watermark_does_not_duplicate_the_feed(store, repo):
    first = desk_harvest.harvest_commits('p', repo)
    # Simulate a restore-point rollback that took the watermark with it.
    with desk._store_lock:
        s = desk._read_store()
        s['harvest'] = {}
        desk._write_store(s)
    again = desk_harvest.harvest_commits('p', repo)
    assert again == [], 'ref dedupe, not the watermark, is the correctness guard'
    assert len(desk.list_signals(project_id='p')) == len(first)


def test_new_commit_after_a_harvest_is_picked_up(store, repo):
    desk_harvest.harvest_commits('p', repo)
    (repo / 'new.txt').write_text('x', encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'feat: shipped the Desk'],
                   cwd=repo, check=True, capture_output=True)
    made = desk_harvest.harvest_commits('p', repo)
    assert [m['summary'] for m in made] == ['feat: shipped the Desk']


def test_a_stale_watermark_sha_falls_back_rather_than_failing(store, repo):
    desk_harvest.harvest_commits('p', repo)
    # A sha that is not in this repo — what a rebase or a force-push leaves behind.
    desk_harvest._set_watermark('p', 'git', 'deadbeef' * 5)
    (repo / 'n2.txt').write_text('x', encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=repo, check=True, capture_output=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'feat: survived a rebase'],
                   cwd=repo, check=True, capture_output=True)
    made = desk_harvest.harvest_commits('p', repo)
    assert [m['summary'] for m in made] == ['feat: survived a rebase']


def test_missing_path_is_not_an_error(store, tmp_path):
    assert desk_harvest.harvest_commits('p', tmp_path / 'nope') == []


def test_a_directory_that_is_not_a_repo_is_not_an_error(store, tmp_path):
    plain = tmp_path / 'plain'
    plain.mkdir()
    assert desk_harvest.harvest_commits('p', plain) == []


# -- backlog ------------------------------------------------------------------

def _project(**kw):
    return {'id': 'p', 'backlog': [
        {'key': 'MC-01', 'text': 'Ship the Desk', 'status': 'done',
         'done_at': '2026-09-01T00:00:00Z'},
        {'key': 'MC-02', 'text': 'Still open', 'status': 'open'},
        {'key': 'MC-03', 'text': 'Ship the harvester', 'status': 'done',
         'done_at': '2026-09-08T00:00:00Z'},
    ], **kw}


def test_only_done_items_become_signals(store):
    made = desk_harvest.harvest_backlog('p', _project())
    assert {m['ref'] for m in made} == {'MC-01', 'MC-03'}


def test_backlog_harvest_is_idempotent(store):
    desk_harvest.harvest_backlog('p', _project())
    assert desk_harvest.harvest_backlog('p', _project()) == []


def test_a_newly_done_item_is_picked_up(store):
    desk_harvest.harvest_backlog('p', _project())
    proj = _project()
    proj['backlog'].append({'key': 'MC-04', 'text': 'Ship the surfaces',
                            'status': 'done', 'done_at': '2026-09-09T00:00:00Z'})
    made = desk_harvest.harvest_backlog('p', proj)
    assert [m['ref'] for m in made] == ['MC-04']


def test_empty_backlog_is_fine(store):
    assert desk_harvest.harvest_backlog('p', {'id': 'p'}) == []


# -- the run ------------------------------------------------------------------

def test_harvest_all_skips_incognito_and_sidecars(store, repo):
    out = desk_harvest.harvest_all([
        {'id': 'real', 'project_path': str(repo), 'backlog': []},
        {'id': '_incognito', 'project_path': str(repo), 'backlog': []},
        {'id': None},
    ])
    ids = {r['project_id'] for r in out['projects']}
    assert ids == {'real'}, 'incognito must never feed the marketing surface'
    assert desk.list_signals(project_id='_incognito') == []


def test_one_bad_project_does_not_stop_the_run(store, repo, monkeypatch):
    real_harvest = desk_harvest.harvest_project

    def flaky(project):
        if project.get('id') == 'bad':
            raise RuntimeError('unreadable')
        return real_harvest(project)

    monkeypatch.setattr(desk_harvest, 'harvest_project', flaky)
    out = desk_harvest.harvest_all([
        {'id': 'bad'},
        {'id': 'good', 'project_path': str(repo), 'backlog': []},
    ])
    assert out['commits'] > 0, 'the good project still harvested'
    assert any(r.get('error') for r in out['projects'])


def test_harvest_all_unwired_reports_rather_than_raising(store, monkeypatch):
    monkeypatch.setattr(desk_harvest, 'load_projects', None)
    assert desk_harvest.harvest_all()['error'] == 'not wired'


def test_harvest_all_survives_a_broken_loader(store, monkeypatch):
    def boom():
        raise RuntimeError('projects gone')
    monkeypatch.setattr(desk_harvest, 'load_projects', boom)
    assert 'projects gone' in desk_harvest.harvest_all()['error']


def test_project_with_both_sources(store, repo):
    out = desk_harvest.harvest_project(
        {'id': 'p', 'project_path': str(repo), **_project()})
    assert out['commits'] > 0 and out['backlog'] == 2


# -- the structural guarantee -------------------------------------------------

def test_harvester_makes_no_network_call():
    src = (PROJECT_ROOT / 'mc' / 'desk_harvest.py').read_text(encoding='utf-8')
    for forbidden in ('requests.', 'urllib.request', 'httpx.', 'socket.'):
        assert forbidden not in src, \
            'the harvester reads YOUR work, locally — that is the whole claim'


# -- the two defects the first real run against this repo exposed -------------

def test_first_harvest_does_not_flood_the_feed(store):
    """Measured 2026-09-09: an uncapped harvest pulled 899 done items at once.

    Old history is not marketing material, and a feed nobody can scan is a feed
    nobody reads. Newest first, capped, watermark closes the door on the rest.
    """
    # 200 items on 200 distinct, ascending days.
    proj = {'id': 'p', 'backlog': [
        {'key': f'MC-{i:03}', 'text': f'Item {i}', 'status': 'done',
         'done_at': f'2026-01-01T00:00:{i:02}Z' if i < 60 else
                    f'2026-01-02T{(i - 60) // 60:02}:{(i - 60) % 60:02}:00Z'}
        for i in range(200)]}
    made = desk_harvest.harvest_backlog('p', proj)
    assert len(made) == desk_harvest.MAX_BACKLOG_PER_RUN

    # And the ones it took are the NEWEST, not an arbitrary slice.
    took = sorted(int(m['ref'].split('-')[1]) for m in made)
    assert took == list(range(160, 200)), 'newest 40, not the first 40 seen'

    # The rest are gone for good: the watermark closed the door behind them, so
    # a second harvest does not slowly re-import the archive one batch at a time.
    assert desk_harvest.harvest_backlog('p', proj) == []


def test_an_undateable_item_is_dropped_not_stamped_with_now(store):
    """A done item with no date used to inherit `now` and sit at the top of the
    feed forever, so a long-finished item read as today's news."""
    proj = {'id': 'p', 'backlog': [
        {'key': 'MC-01', 'text': 'No date at all', 'status': 'done'},
        {'key': 'MC-02', 'text': 'Has created_at', 'status': 'done',
         'created_at': '2026-01-01T00:00:00Z'},
        {'key': 'MC-03', 'text': 'Has done_at', 'status': 'done',
         'done_at': '2026-02-01T00:00:00Z'},
    ]}
    made = desk_harvest.harvest_backlog('p', proj)
    refs = {m['ref'] for m in made}
    assert 'MC-01' not in refs, 'an invented date is worse than no signal'
    assert refs == {'MC-02', 'MC-03'}
    assert dict((m['ref'], m['occurred_at']) for m in made)['MC-02'] \
        == '2026-01-01T00:00:00Z', 'falls back to created_at, does not fabricate'


def test_score_sort_disagrees_with_recency_and_the_board_wants_score(store):
    desk.append_signal('p', 'commit', 'chore: bump lint',
                       ref='a', occurred_at='2026-09-09T00:00:00Z')
    desk.append_signal('p', 'release', 'Shipped drag-to-hire, now live',
                       ref='b', occurred_at='2026-01-01T00:00:00Z')
    assert desk.list_signals(sort='recent')[0]['ref'] == 'a'
    assert desk.list_signals(sort='score')[0]['ref'] == 'b', \
        'the newest thing that happened is often a chore'
