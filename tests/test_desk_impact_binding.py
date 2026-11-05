"""MC-1152 P1: definition-to-Goal binding, dark.

Spec: docs/desk_v1/CHANNEL_IMPACT_SPEC.md section 5 (last paragraph) and
section 12 row P1: no-definition / manual-goal compatibility.
"""
from __future__ import annotations

import copy

import pytest

from mc import desk
from mc.desk_impact_binding import AUTO_PROGRESS_DARK, goal_binding
from mc.desk_impact_sources.registry import AdapterRegistry
from mc.desk_impact_store import ImpactStore, OwnershipError
from tests.desk_impact_fakes import CAMPAIGNS, CONNECTIONS, FakeSource, observation, spec


@pytest.fixture
def campaigns():
    return copy.deepcopy(CAMPAIGNS)


@pytest.fixture
def st(tmp_path, campaigns):
    reg = AdapterRegistry()
    reg.register(FakeSource())
    return ImpactStore(tmp_path / 'data' / 'desk_impact',
                       campaign_resolver=lambda cid: copy.deepcopy(campaigns.get(cid)),
                       connection_resolver=lambda c: copy.deepcopy(CONNECTIONS.get(c)),
                       registry=reg, data_dir=tmp_path / 'data' / 'projects')


def test_no_definition_returns_the_goal_untouched(st, campaigns):
    out = goal_binding(st, 'proj1', 'camp-a')
    assert out['definition'] is None and out['revisions'] == []
    assert out['goal'] == campaigns['camp-a']['goal']


def test_campaign_without_a_goal_binds_an_empty_goal(st):
    assert goal_binding(st, 'proj1', 'camp-b')['goal'] == {}


def test_attaching_a_definition_does_not_touch_the_manual_goal(st, campaigns):
    before = copy.deepcopy(campaigns['camp-a'])
    cur_before = desk.goal_current(before['goal'])
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    st.set_definition('proj1', 'camp-a', spec(metric='downloads', unit='downloads'),
                      expected_revision=1)
    st.retire_definition('proj1', 'camp-a', expected_revision=2)
    st.append_snapshot('proj1', observation(value=500))
    assert campaigns['camp-a'] == before                     # campaign dict untouched
    out = goal_binding(st, 'proj1', 'camp-a')
    assert out['goal'] == before['goal']
    assert desk.goal_current(out['goal']) == cur_before == 7  # still the manual entry
    assert out['goal']['source'] == 'manual'


def test_snapshot_values_never_become_goal_progress(st):
    r = st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    st.append_snapshot('proj1', observation(value=500, definition_id=r['id'],
                                            definition_revision=1))
    out = goal_binding(st, 'proj1', 'camp-a')
    assert out['auto_progress'] == AUTO_PROGRESS_DARK
    assert out['auto_progress']['state'] == 'dark' and out['auto_progress']['value'] is None
    assert out['goal']['entries'] == [{'at': '2026-11-01', 'value': 7}]


def test_active_definition_and_every_revision_are_exposed(st):
    st.set_definition('proj1', 'camp-a', spec(), expected_revision=0)
    st.set_definition('proj1', 'camp-a', spec(metric='downloads', unit='downloads'),
                      expected_revision=1)
    out = goal_binding(st, 'proj1', 'camp-a')
    assert out['definition']['metric'] == 'downloads'
    assert [r['metric'] for r in out['revisions']] == ['stars', 'downloads']


def test_returned_goal_is_a_copy(st, campaigns):
    out = goal_binding(st, 'proj1', 'camp-a')
    out['goal']['target'] = 1
    out['auto_progress']['state'] = 'live'
    assert campaigns['camp-a']['goal']['target'] == 100
    assert goal_binding(st, 'proj1', 'camp-a')['auto_progress']['state'] == 'dark'


def test_wrong_project_is_refused(st):
    with pytest.raises(OwnershipError):
        goal_binding(st, 'proj1', 'camp-other')


def test_goal_write_allowlist_does_not_accept_impact_fields():
    """The existing Goal PATCH allowlist (desk.v1_goal_in) must not become a
    side door for the definition or for automatic progress."""
    body = {'metric': 'Stars', 'target': 5, 'source': 'manual',
            'definition': {'source_id': 'x'}, 'impact': {}, 'current': 99,
            'auto_progress': {'value': 99}}
    stored = desk.v1_goal_in(body)
    assert not {'definition', 'impact', 'current', 'auto_progress'} & set(stored)
    with pytest.raises(ValueError):
        desk.v1_goal_in({'source': 'github'})                # not a manual-goal source
