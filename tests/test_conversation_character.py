"""A chat list says WHO each conversation was with (Ron, 2026-08-26).

"If I want to return to a prior conversation with a specific agent it would be
easier to know who is who if the avatars are present next to the chat."

The persona was already on every agent_log row — nothing emitted it, so the
transcript-derived lists had no way to draw a face and every chat looked the
same until you opened it.

The load-bearing choice here is RE-RESOLVING from disk rather than serving the
stored snapshot: the row records the persona as it was at spawn, so a re-faced
or renamed persona would show its old face forever on every chat it ever ran.
The snapshot is the fallback for a persona that has since been DELETED, which
is the one case where it is the only truth left.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import agent_routes  # noqa: E402


@pytest.fixture()
def personas(tmp_path, monkeypatch):
    from mc import characters as ch
    global_dir = tmp_path / 'agents-global'
    global_dir.mkdir()
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', global_dir)
    proj_path = tmp_path / 'proj'
    (proj_path / '.claude' / 'agents').mkdir(parents=True)

    ch.write_character('global', 'dave', 'Program manager', 'You are Dave.',
                       project_path=None, overwrite=True,
                       agent_name='Dave', avatar='fig:guard')
    ch.write_character('project', 'scout', 'Looks around', 'You look around.',
                       project_path=str(proj_path), overwrite=True,
                       agent_name='Posy', avatar='fig:courier')
    return {'id': 'p1', 'project_path': str(proj_path)}


def _row(name, scope='global', **over):
    ch = {'name': name, 'scope': scope, 'display_name': name}
    ch.update(over)
    return {'character': ch}


class TestWhoWasThisChatWith:

    def test_a_global_persona_resolves_to_its_name_and_face(self, personas):
        out = agent_routes._conversation_character_display(_row('dave'), personas)
        assert out['agent_name'] == 'Dave' and out['avatar'] == 'fig:guard'
        assert out['deleted'] is False

    def test_a_project_persona_is_read_from_that_project(self, personas):
        out = agent_routes._conversation_character_display(
            _row('scout', scope='project'), personas)
        assert out['agent_name'] == 'Posy' and out['avatar'] == 'fig:courier'

    def test_the_current_face_wins_over_the_one_stored_at_spawn(self, personas):
        """A re-faced persona must not show its old face on every chat it ever
        ran — that is the whole reason this resolves instead of echoing."""
        stale = _row('dave', avatar='fig:newcomer', agent_name='Vector')
        out = agent_routes._conversation_character_display(stale, personas)
        assert out['avatar'] == 'fig:guard' and out['agent_name'] == 'Dave'

    def test_a_deleted_persona_falls_back_to_the_stored_snapshot(self, personas):
        """The snapshot is the only record left of who the chat was with."""
        out = agent_routes._conversation_character_display(
            _row('gone', agent_name='Ghost', avatar='fig:wizard'), personas)
        assert out['deleted'] is True
        assert out['agent_name'] == 'Ghost' and out['avatar'] == 'fig:wizard'

    @pytest.mark.parametrize('row', [
        {}, None, {'character': None}, {'character': {}},
        {'character': 'dave'}, {'character': {'scope': 'global'}},
    ])
    def test_a_chat_with_no_persona_reports_none(self, row, personas):
        assert agent_routes._conversation_character_display(row, personas) is None

    def test_a_project_persona_on_a_pathless_project_does_not_raise(self, personas):
        out = agent_routes._conversation_character_display(
            _row('scout', scope='project'), {'id': 'nopath'})
        assert out['deleted'] is True and out['name'] == 'scout'

    def test_an_unreadable_persona_dir_never_breaks_the_list(self, personas, monkeypatch):
        from mc import characters as ch

        def _boom(*a, **kw):
            raise OSError('disk gone')
        monkeypatch.setattr(ch, 'read_character', _boom)
        out = agent_routes._conversation_character_display(
            _row('dave', agent_name='Dave'), personas)
        assert out is not None and out['deleted'] is True
