"""MC-1077: a reviewer that descends from the branch owner by dispatch is not
independent. The ancestry walk (pure), its wiring over live sessions and the
agent log (production `spawner_of`), and fail-closed behaviour.

The route-level proof (the bypass through POST .../review) is in
tests/test_merge_review_gate.py next to the other route tests.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc.state as state  # noqa: E402

state.CONFIG.setdefault('port', 5199)

from mc import review_lineage as rl  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402
from mc.merge_review_gate import ReviewRefused  # noqa: E402


def _tree(**parents):
    """spawner_of over a literal {sid: [spawner, ...]} map; a sid missing from
    the map is unknown, like a session recorded nowhere."""
    def spawner_of(sid):
        if sid not in parents:
            raise rl.LineageUnreadable(f'no record for {sid}')
        return parents[sid]
    return spawner_of


# ── the walk ────────────────────────────────────────────────────────────────

def test_child_of_the_owner_is_found():
    assert rl.owner_in_ancestry('child', 'owner', _tree(child=['owner'], owner=[])) \
        == ['child', 'owner']


def test_grandchild_of_the_owner_is_found():
    t = _tree(grand=['child'], child=['owner'], owner=[])
    assert rl.owner_in_ancestry('grand', 'owner', t) == ['grand', 'child', 'owner']


def test_a_reviewer_dispatched_by_someone_else_is_independent():
    t = _tree(rev=['dave'], dave=[], owner=['dave'])   # owner is Dave's too: siblings
    assert rl.owner_in_ancestry('rev', 'owner', t) == []


def test_a_human_opened_reviewer_with_no_spawner_is_independent():
    assert rl.owner_in_ancestry('rev', 'owner', _tree(rev=[])) == []


def test_the_owner_being_an_ancestor_via_either_recorded_parent_counts():
    t = _tree(rev=['dave', 'mid'], dave=[], mid=['owner'], owner=[])
    assert rl.owner_in_ancestry('rev', 'owner', t) == ['rev', 'mid', 'owner']


def test_an_unknown_ancestor_fails_closed():
    with pytest.raises(rl.LineageUnreadable):
        rl.owner_in_ancestry('rev', 'owner', _tree(rev=['ghost']))


def test_a_polluted_cycle_terminates():
    assert rl.owner_in_ancestry('a', 'owner', _tree(a=['b'], b=['a'])) == []


def test_a_chain_longer_than_any_real_tree_fails_closed():
    chain = {f's{i}': [f's{i + 1}'] for i in range(rl.MAX_NODES + 5)}
    with pytest.raises(rl.LineageUnreadable):
        rl.owner_in_ancestry('s0', 'owner', _tree(**chain))


def test_check_refuses_a_descendant_and_names_the_path():
    with pytest.raises(ReviewRefused) as ei:
        rl.check('grand', 'owner', _tree(grand=['child'], child=['owner'], owner=[]))
    assert ei.value.status == 403
    assert 'descends from the branch owner' in ei.value.message
    assert ei.value.extra['lineage'] == ['owner', 'child', 'grand']


def test_check_refuses_when_lineage_is_unreadable():
    with pytest.raises(ReviewRefused) as ei:
        rl.check('rev', 'owner', _tree())
    assert ei.value.status == 403 and 'cannot establish' in ei.value.message


def test_check_refuses_when_the_walk_itself_raises():
    def boom(sid):
        raise RuntimeError('disk on fire')
    with pytest.raises(ReviewRefused) as ei:
        rl.check('rev', 'owner', boom)
    assert ei.value.status == 403


def test_check_leaves_the_owner_to_the_gates_self_review_refusal():
    rl.check('owner', 'owner', _tree())   # must not raise or consult lineage


def test_check_passes_an_independent_reviewer():
    rl.check('rev', 'owner', _tree(rev=['dave'], dave=[]))


# ── production wiring: live sessions + agent logs ───────────────────────────

@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(state, 'agent_sessions', {})
    def write(project, rows):
        (tmp_path / f'{project}_agent_log.json').write_text(json.dumps(rows), encoding='utf-8')
    return write


def test_live_child_of_the_owner_is_refused(logs):
    state.agent_sessions['rev'] = {'session_id': 'rev', '_spawned_by': 'owner'}
    with pytest.raises(ReviewRefused) as ei:
        rl.check('rev', 'owner')
    assert 'descends from the branch owner' in ei.value.message


def test_chain_through_a_finished_intermediate_in_the_log_is_refused(logs):
    """The builder's child has ended and lives only in the agent log."""
    state.agent_sessions['rev'] = {'session_id': 'rev', '_spawned_by': 'mid'}
    logs('p', [{'session_id': 'mid', 'trigger_type': 'dispatch',
                'dispatched_by_session_id': 'owner'}])
    with pytest.raises(ReviewRefused) as ei:
        rl.check('rev', 'owner')
    assert ei.value.status == 403 and 'descends' in ei.value.message


def test_a_spawner_in_another_projects_log_is_still_followed(logs):
    state.agent_sessions['rev'] = {'session_id': 'rev', '_spawned_by': 'mid'}
    logs('other', [{'session_id': 'mid', 'trigger_type': 'dispatch',
                    'dispatched_by_session_id': 'owner'}])
    with pytest.raises(ReviewRefused):
        rl.check('rev', 'owner')


def test_reviewer_known_only_from_its_log_row_is_followed(logs):
    logs('p', [{'session_id': 'rev', 'trigger_type': 'dispatch',
                'dispatched_by_session_id': 'owner'}])
    with pytest.raises(ReviewRefused):
        rl.check('rev', 'owner')


def test_the_rearmable_callback_target_alone_is_not_lineage(logs):
    """`spawned_by_session_id` on a 'manual' row is a re-armed callback target
    (MC-970), not a spawner. A human-opened reviewer that merely notified the
    owner must not be refused as its child."""
    state.agent_sessions['rev'] = {'session_id': 'rev'}
    logs('p', [{'session_id': 'rev', 'trigger_type': 'manual',
                'spawned_by_session_id': 'owner'}])
    rl.check('rev', 'owner')


def test_independent_reviewer_passes(logs):
    state.agent_sessions['rev'] = {'session_id': 'rev', '_spawned_by': 'dave'}
    logs('p', [{'session_id': 'dave', 'trigger_type': 'manual'}])
    rl.check('rev', 'owner')


def test_unknown_reviewer_fails_closed(logs):
    with pytest.raises(ReviewRefused) as ei:
        rl.check('rev', 'owner')
    assert 'cannot establish' in ei.value.message


def test_corrupt_log_hides_the_spawner_and_fails_closed(logs, tmp_path):
    """The only record of the intermediate session sits in a corrupt log."""
    state.agent_sessions['rev'] = {'session_id': 'rev', '_spawned_by': 'mid'}
    (tmp_path / 'p_agent_log.json').write_text('{"truncated', encoding='utf-8')
    with pytest.raises(ReviewRefused) as ei:
        rl.check('rev', 'owner')
    assert ei.value.status == 403 and 'cannot establish' in ei.value.message
    assert (tmp_path / 'p_agent_log.json').exists()   # the walk did not quarantine it


def test_missing_data_dir_fails_closed(monkeypatch):
    monkeypatch.setattr(ar, 'DATA_DIR', None)
    monkeypatch.setattr(state, 'agent_sessions',
                        {'rev': {'session_id': 'rev', '_spawned_by': 'mid'}})
    with pytest.raises(ReviewRefused):
        rl.check('rev', 'owner')
