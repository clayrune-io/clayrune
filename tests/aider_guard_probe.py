"""Real Aider positive controls for the previously reproduced five bypasses."""
from __future__ import annotations

import contextlib
import importlib.metadata
import importlib.machinery
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def main():
    scenario, scratch = sys.argv[1:]
    root = Path(scratch).resolve()
    os.chdir(root)
    os.environ.update(HOME=str(root), USERPROFILE=str(root),
                      LITELLM_LOCAL_MODEL_COST_MAP='True')
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from mc.aider_source_pin import verify_installed
    if scenario == 'import_shadow':
        marker = root / 'shadow-executed'
        (root / 'aider.py').write_text(
            "from pathlib import Path\nPath(__file__).with_name('shadow-executed').write_text('executed')\n")
        sys.path.insert(0, str(root))
        try:
            verify_installed()
        except RuntimeError as exc:
            print(json.dumps({'refused': str(exc), 'executed': marker.exists()}))
            return
        raise AssertionError('shadowed package accepted')
    package = verify_installed()
    if scenario in ('source_drift', 'resource_drift', 'extension_drift'):
        import shutil
        copy = root / 'aider-copy'
        shutil.copytree(package, copy, ignore=shutil.ignore_patterns('__pycache__'))
        target = {'source_drift': copy / 'utils.py',
                  'resource_drift': copy / 'resources' / 'model-settings.yml',
                  'extension_drift': copy / ('commands' + importlib.machinery.EXTENSION_SUFFIXES[0])}[scenario]
        with target.open('a') as source:
            source.write('\n# deliberate audit-shape drift\n')
        from mc.aider_source_pin import verify_tree
        try:
            verify_tree(copy)
        except RuntimeError as exc:
            print(json.dumps({'refused': str(exc)}))
            return
        raise AssertionError('drift accepted')

    protected = root / '.claude' / ('audit-target.wav' if scenario == 'native_voice'
                                  else 'audit-target.txt')
    protected.parent.mkdir()
    if scenario not in ('prepare_touch', 'native_voice'):
        protected.write_bytes(b'original\n')
    unprotected = root / 'allowed.txt'
    unprotected.write_text('original\n')
    from mc.aider_guard import Guard, AiderRefusal
    guard = Guard(str(root))
    guard.install()
    from aider.coders import Coder
    from aider.io import InputOutput
    output = io.StringIO()
    native_io = InputOutput(pretty=False, yes=True, output=output, fancy_input=False,
                            input_history_file=None, chat_history_file=None)
    model = SimpleNamespace(reasoning_tag=None, streaming=False, cache_control=False,
                            info={}, weak_model=None, max_chat_history_tokens=1024,
                            token_count=lambda v: 1)
    model.weak_model = model
    coder = Coder.create(main_model=model, edit_format='whole', io=native_io, use_git=False)
    coder.root = str(root)
    reached = []

    with patch.object(native_io, 'confirm_ask', return_value=True):
        try:
            if scenario == 'prepare_touch':
                coder.allowed_to_edit('.claude/audit-target.txt')
            elif scenario == 'initial_touch':
                Coder.create(main_model=model, edit_format='whole', io=native_io, use_git=False,
                             fnames=[str(root / '.claude' / 'new.txt')])
            elif scenario in ('patch_delete', 'udiff'):
                # Same real factory and delete body as the negative proof.
                from aider.coders.patch_coder import ActionType, PatchAction
                patch_coder = Coder.create(from_coder=coder,
                                           edit_format='patch' if scenario == 'patch_delete' else 'udiff',
                                           summarize_from_coder=False)
                patch_coder.apply_edits([('.claude/audit-target.txt',
                                         PatchAction(ActionType.DELETE, '.claude/audit-target.txt'))])
            elif scenario == 'direct_patch_delete':
                from aider.coders.patch_coder import PatchCoder, ActionType, PatchAction
                # Even a direct invocation bypassing Coder.create refuses.
                patch_coder = PatchCoder.__new__(PatchCoder)
                patch_coder.root = str(root)
                patch_coder.apply_edits([('.claude/audit-target.txt',
                                         PatchAction(ActionType.DELETE, '.claude/audit-target.txt'))])
            elif scenario == 'save':
                coder.preproc_user_input('/save ' + str(protected))
            elif scenario == 'git':
                # The old proof recorded subprocess.run here; record the
                # OS event instead, leaving the real guarded Popen intact.
                def recorder(event, args):
                    if event == 'subprocess.Popen':
                        reached.append(args[1])
                        raise AssertionError('refused command reached subprocess')
                sys.addaudithook(recorder)
                coder.preproc_user_input('/git push')
            elif scenario == 'native_voice':
                import numpy as np
                from aider import voice
                recorder = voice.Voice.__new__(voice.Voice)
                recorder.device_id = None
                recorder.audio_format = 'wav'
                recorder.sd = SimpleNamespace(
                    query_devices=lambda *a: {'default_samplerate': 16000},
                    InputStream=lambda **kw: contextlib.nullcontext(), PortAudioError=RuntimeError)
                recorder.callback = lambda *a: None
                with patch.object(voice.tempfile, 'mktemp', return_value=str(protected)), \
                        patch.object(voice, 'prompt', side_effect=lambda *a, **kw:
                                     recorder.q.put(np.zeros((160, 1)))), \
                        patch.object(voice, 'litellm', SimpleNamespace(
                            transcription=lambda **kw: SimpleNamespace(text='synthetic'))):
                    coder.commands.voice = recorder
                    coder.commands.cmd_voice('')
            elif scenario in ('handle_shell', 'cmd_run'):
                if scenario == 'handle_shell':
                    coder.handle_shell_commands('git push', None)
                else:
                    coder.commands.cmd_run('git push')
            elif scenario in ('subprocess_git', 'gitpython_push'):
                if scenario == 'subprocess_git':
                    import subprocess
                    subprocess.run(['git', 'push'], cwd=root)
                else:
                    import git
                    git.Git(str(root)).push()
            elif scenario in ('allow_whole', 'allow_diff', 'protected_whole', 'protected_diff'):
                fmt = 'diff' if scenario.endswith('diff') else 'whole'
                coder = Coder.create(main_model=model, edit_format=fmt, io=native_io, use_git=False)
                coder.root = str(root)
                name = '.claude/audit-target.txt' if scenario.startswith('protected') else 'allowed.txt'
                coder.abs_fnames.add(str(root / name))
                if fmt == 'whole':
                    coder.partial_response_content = name + '\n```text\nchanged\n```\n'
                else:
                    coder.partial_response_content = (name + '\n```text\n<<<<<<< SEARCH\n'
                                                      'original\n=======\nchanged\n>>>>>>> REPLACE\n```\n')
                coder.choose_fence()
                coder.apply_updates()
            elif scenario == 'allow_prepare':
                coder.allowed_to_edit('new.txt')
                assert (root / 'new.txt').exists()
            elif scenario == 'allow_shell':
                coder.commands.cmd_run('echo aider-allowed')
            elif scenario.startswith('command:'):
                coder.preproc_user_input(scenario.partition(':')[2])
            elif scenario == 'installer':
                from aider import utils
                utils.run_install(['never-run'])
            elif scenario == 'browser':
                import webbrowser
                webbrowser.open('https://example.invalid')
            elif scenario == 'allow_gitpython':
                import git
                assert git.Git(str(root)).version().startswith('git version')
            elif scenario == 'fence_error':
                guard.classify = lambda *a: (_ for _ in ()).throw(ValueError('synthetic fence error'))
                native_io.write_text(unprotected, 'changed')
            else:
                raise AssertionError(scenario)
        except AiderRefusal:
            pass

    print(json.dumps({'version': importlib.metadata.version('aider-chat'),
                      'scenario': scenario, 'refusals': guard.refusals,
                      'exists': protected.exists(),
                      'unchanged': protected.exists() and protected.read_bytes() == b'original\n',
                      'allowed_content': unprotected.read_text(),
                      'subprocess_calls': reached, 'checks': guard.checks,
                      'io_output': output.getvalue()}))


if __name__ == '__main__':
    main()
