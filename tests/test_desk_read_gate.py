"""A browser-pane connection whose reading is not allowed yet is not a sign-in problem
(Ron 2026-10-28: LinkedIn "Clayrune", profile linked_ronlevy, signed in, card said "Needs sign-in").

`capability()` carries a structured `gate` beside its `reason`; `platform_coverage` forwards it and
words its `message` without the profile name. SYNTHETIC ONLY: no browser, network or model; the
policy lives in a temp CLAYRUNE_HOME.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import browser_agent_read as policy  # noqa: E402
from mc import desk as _desk  # noqa: E402
from mc import desk_engagement as eng  # noqa: E402
from mc import desk_engagement_pane_digest as pd  # noqa: E402
from mc import desk_read_gate as gate  # noqa: E402

PID = 'clayrune'
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
PROFILE = 'linked_ronlevy'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(_desk, 'SIGNALS_PATH', tmp_path / 'sig.jsonl')
    return tmp_path


def _reader(profile=PROFILE, exists=True, url='https://www.linkedin.com/company/clayrune/'):
    acc = {'platform': 'linkedin', 'browser_profile': profile, 'url': url}
    return pd.PaneDigestReader(PID, 'linkedin', acc, pages_factory=lambda _p, _n, _d: None,
                               profile_exists=lambda _n: exists, runtime=None, now=lambda: NOW)


def test_profile_switched_off_carries_a_reads_off_gate(env):
    policy.set_policy('main', True, ['x.com'])           # the live repro: only other profiles are on
    cap = _reader().capability()
    assert cap['connected'] is False
    assert cap['gate'] == {'kind': 'reads_off', 'profile': PROFILE, 'site': 'linkedin.com'}


def test_site_missing_from_an_enabled_profile_is_the_same_gate(env):
    policy.set_policy(PROFILE, True, ['x.com'])
    cap = _reader().capability()
    assert cap['connected'] is False
    assert cap['gate'] == {'kind': 'reads_off', 'profile': PROFILE, 'site': 'linkedin.com'}


def test_reads_on_has_no_gate(env):
    policy.set_policy(PROFILE, True, ['linkedin.com'])
    cap = _reader().capability()
    assert cap['connected'] is True and 'gate' not in cap


def test_missing_profile_or_none_chosen_is_not_a_reads_off_gate(env):
    # a profile that is not saved really is a sign-in problem: it must keep reading as one
    assert 'gate' not in _reader(exists=False).capability()
    assert 'gate' not in pd.PaneDigestReader(PID, 'linkedin', {}).capability()


def test_coverage_forwards_the_gate_and_words_the_message_plainly(env):
    policy.set_policy('main', True, ['x.com'])
    cov = eng.platform_coverage(PID, 'linkedin', _reader())
    assert cov['state'] == 'not_connected' and cov['via'] == 'pane'
    assert cov['gate'] == {'kind': 'reads_off', 'profile': PROFILE, 'site': 'linkedin.com'}
    assert cov['message'] == 'Reading is off for linkedin.com'
    assert PROFILE not in cov['message']
    assert PROFILE in cov['reason']                      # the technical sentence is still there, for Details


def test_coverage_without_a_gate_keeps_the_sign_in_wording(env):
    cov = eng.platform_coverage(PID, 'linkedin', _reader(exists=False))
    assert cov['state'] == 'not_connected' and cov['gate'] is None
    assert cov['message'].startswith('Not connected (')


def test_gate_helpers():
    assert gate.site_of('https://www.linkedin.com/in/x') == 'linkedin.com'
    assert gate.site_of('http://linkedin.com') == ''     # only plain https addresses name a site
    assert gate.message(None) is None and gate.message({'kind': 'other'}) is None
    assert gate.message(gate.reads_off('p', None)) == 'Reading is off'
