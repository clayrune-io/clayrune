"""Containment tests for skill-staging ids (skills.staging_dir).

Regression cover for a path traversal found by a CodeAnt scan on 2026-08-22.
`staging_id` arrived from the request body and was joined straight onto
STAGING_SKILLS_DIR, so an id of `../../x` walked out of the staging root.
Two call sites then handed the result to `shutil.rmtree`, which made
arbitrary recursive directory deletion reachable by any authenticated
LAN/tunnel client — a boundary the product deliberately draws (that is why
/api/terminal/launch is loopback-only).

Note the sibling `rel_dir` parameter was already guarded with a
relative_to() check; only `staging_id` was missed. If someone adds a third
staging-derived path, it must go through staging_dir() too.
"""
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import skills  # noqa: E402


def test_valid_staging_id_resolves_under_the_staging_root():
    # Ids are minted as uuid4().hex[:12] — bare lowercase hex.
    sid = 'deadbeef1234'
    assert skills.staging_dir(sid) == skills.STAGING_SKILLS_DIR.resolve() / sid


@pytest.mark.parametrize('bad', [
    '..',
    '../x',
    '../../../../etc',
    'a/b',
    'deadbeef1234/../../..',
    'deadbeef1234\\..\\..',
    '/abs/path',
    'C:/Windows',
    'ABC123',          # uppercase is not a hex id we ever mint
    'deadbeef-1234',   # no punctuation
    '',
    '   ',
])
def test_traversal_and_malformed_ids_are_refused(bad):
    with pytest.raises(ValueError):
        skills.staging_dir(bad)


def test_traversal_cannot_reach_a_directory_outside_staging(tmp_path):
    """The concrete attack: an id whose join lands on someone else's data."""
    victim = tmp_path / 'victim'
    victim.mkdir()
    (victim / 'important.txt').write_text('do not delete', encoding='utf-8')

    root = skills.STAGING_SKILLS_DIR.resolve()
    traversal_id = os.path.relpath(victim, root).replace(os.sep, '/')
    assert traversal_id.startswith('..'), 'test setup: expected an escaping path'

    with pytest.raises(ValueError):
        skills.staging_dir(traversal_id)
    assert (victim / 'important.txt').exists()


def test_every_staging_path_in_the_routes_goes_through_the_helper():
    """Guard against a new call site re-introducing the raw join."""
    src = (PROJECT_ROOT / 'mc' / 'blueprints' / 'skills_routes.py').read_text(encoding='utf-8')
    assert 'STAGING_SKILLS_DIR /' not in src, (
        'skills_routes.py joins STAGING_SKILLS_DIR directly — use '
        '_skills.staging_dir(staging_id) so the id is validated')
