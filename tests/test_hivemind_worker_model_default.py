"""W5 (2026-09-18): a Hivemind workstream's own `provider` override must not
inherit the manifest-level `worker_model` default that was baked in for a
DIFFERENT vendor.

Live-reproduced bug: `hivemind_create` computes `config.worker_model`
('sonnet' when the EFFECTIVE default provider is 'claude') ONCE, at create
time, for the hivemind-wide default -- but the STORED `config.worker_provider`
field is only what the caller explicitly passed (often ''). A workstream that
later names its own DIFFERENT `provider` and leaves `model` empty used to
inherit that baked-in 'sonnet' string regardless, and
`engine_selection.model_provider_mismatch` correctly refused it as belonging
to another vendor's catalog -- observed live as "Model 'sonnet' belongs to
provider 'aider', not 'gemini'". Every mixed-vendor workstream that didn't
also set its own `model` could not spawn at all.

Uses a picky fake runtime (`model_supported` returns False), unlike the
shared `test_hivemind_routes.py` suite's `FakeRuntime` (always True) which
never reaches the mismatch path this bug lives in.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import hivemind_routes as hm  # noqa: E402


class _PickyRuntime:
    name = 'fakevendor'

    def model_supported(self, model):
        return False

    def model_choices(self):
        return []

    def latest_for(self, tier):
        """No tier catalog for this fake vendor — native default, same as
        AgentRuntime's own base-class fallback (mc/agent_runtime.py)."""
        return ''


@pytest.fixture()
def rigged(monkeypatch, tmp_path):
    from mc import agent_runtime, state as mc_state

    monkeypatch.setattr(hm, 'HIVEMIND_DIR', tmp_path / 'hiveminds')
    monkeypatch.setattr(hm, 'PORT', 5377)
    monkeypatch.setattr(hm, '_clayrune_universal_capabilities',
                        lambda port=None: ['UNIVERSAL-CAPS port=%s' % port])
    monkeypatch.setattr(hm, '_clayrune_api_reference', lambda: 'API-REF-BODY')
    monkeypatch.setattr(hm, '_clayrune_api_pointer_card',
                        lambda port, pid: 'API-CARD pid=%s' % pid)
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'fakevendor', _PickyRuntime())
    monkeypatch.setitem(mc_state.CONFIG, 'default_provider', 'claude')
    monkeypatch.setitem(mc_state.CONFIG, 'agent_model', '')

    calls = []
    monkeypatch.setattr(
        hm, '_hm_runtime_dispatch',
        lambda **kw: calls.append(kw) or kw['session_id'])
    return calls


def _manifest(worker_provider='', worker_model='sonnet'):
    return {'id': 'hm1', 'goal': 'test goal', 'title': 'test',
            'project_generation': 1,
            'config': {'worker_provider': worker_provider,
                      'worker_model': worker_model, 'worker_effort': ''}}


def _spawn(manifest, ws, hivemind_id, ws_id, tmp_path):
    """Persist manifest/workstream to disk (`_hm_build_worker_context` reads
    them fresh, independent of the in-memory dicts passed to
    `_hm_spawn_worker_session`) then spawn."""
    hm._hm_ensure_dirs(hivemind_id)
    hm._hm_save_manifest(hivemind_id, manifest)
    hm._hm_save_workstream(hivemind_id, ws_id, ws)
    pp = tmp_path / 'proj'
    pp.mkdir(exist_ok=True)
    project = {'id': 'p1', 'project_path': str(pp)}
    return hm._hm_spawn_worker_session(manifest, ws, project, hivemind_id, ws_id)


class TestModelDefaultRespectsWorkstreamProvider:

    def test_own_provider_with_no_model_does_not_inherit_the_wrong_default(
            self, rigged, tmp_path):
        manifest = _manifest(worker_provider='', worker_model='sonnet')
        ws = {'id': 'ws1', 'provider': 'fakevendor', 'model': '', 'title': 't'}

        _spawn(manifest, ws, 'hm1', 'ws1', tmp_path)

        assert len(rigged) == 1
        assert rigged[0]['provider_name'] == 'fakevendor'
        assert rigged[0]['model'] != 'sonnet'

    def test_default_provider_workstream_still_gets_the_manifest_model(
            self, rigged, tmp_path):
        """Negative control: a workstream that does NOT name its own
        provider (using the hivemind's own default) still inherits the
        manifest's model default exactly as before -- this fix must not
        regress the ordinary, single-vendor case."""
        manifest = _manifest(worker_provider='claude', worker_model='sonnet')
        ws = {'id': 'ws2', 'provider': '', 'model': '', 'title': 't'}

        _spawn(manifest, ws, 'hm1', 'ws2', tmp_path)

        assert len(rigged) == 1
        assert rigged[0]['provider_name'] == 'claude'
        assert rigged[0]['model'] == 'sonnet'

    def test_workstream_own_model_always_wins(self, rigged, tmp_path):
        manifest = _manifest(worker_provider='', worker_model='sonnet')
        ws = {'id': 'ws3', 'provider': 'fakevendor', 'model': 'my-custom-model',
              'title': 't'}

        _spawn(manifest, ws, 'hm1', 'ws3', tmp_path)

        assert rigged[0]['model'] == 'my-custom-model'

    def test_own_provider_matching_the_manifest_default_still_inherits(
            self, rigged, tmp_path):
        """A workstream that explicitly names the SAME provider as the
        manifest default is not a mismatch -- it should behave exactly like
        the no-override case."""
        manifest = _manifest(worker_provider='claude', worker_model='sonnet')
        ws = {'id': 'ws4', 'provider': 'claude', 'model': '', 'title': 't'}

        _spawn(manifest, ws, 'hm1', 'ws4', tmp_path)

        assert rigged[0]['provider_name'] == 'claude'
        assert rigged[0]['model'] == 'sonnet'
