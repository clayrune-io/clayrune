"""Isolated installed-Aider audit, not a production bridge or certification.

Run via test_aider_interception_audit.py. All targets are caller-owned scratch
files, process launches are replaced by recorders, audio is synthetic, and no
model/provider request is made. Python audit hooks live only in this child.
"""
from __future__ import annotations

import contextlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def main() -> None:
    scenario, scratch = sys.argv[1:]
    root = Path(scratch).resolve()
    os.chdir(root)
    # Aider model metadata and analytics must not touch the operator's home.
    os.environ.update(HOME=str(root), USERPROFILE=str(root),
                      LITELLM_LOCAL_MODEL_COST_MAP='True')
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from steward import fence
    from aider.coders import Coder
    from aider.io import InputOutput

    target_name = 'audit-target.wav' if scenario == 'native_voice' else 'audit-target.txt'
    protected = root / '.claude' / target_name
    protected.parent.mkdir()
    if scenario != 'prepare_touch':
        protected.write_bytes(b'original\n')

    refusals = []

    def deny_write(filename, *args, **kwargs):
        decision = fence.classify_action('Write', {'file_path': str(filename)}, str(root))
        if decision.blocked:
            refusals.append(decision.reason)
            raise RuntimeError('AUDIT FENCE REFUSAL: ' + decision.reason)
        return original_write(filename, *args, **kwargs)

    output = io.StringIO()
    native_io = InputOutput(pretty=False, yes=True, output=output, fancy_input=False,
                            input_history_file=None, chat_history_file=None)
    original_write = native_io.write_text
    model = SimpleNamespace(reasoning_tag=None, streaming=False, cache_control=False,
                            info={}, weak_model=None, max_chat_history_tokens=1024,
                            token_count=lambda value: 1)
    model.weak_model = model
    coder = Coder.create(main_model=model, edit_format='whole', io=native_io,
                         use_git=False, map_tokens=0, auto_commits=False,
                         auto_lint=False, analytics=SimpleNamespace(event=lambda *a, **kw: None))
    coder.root = str(root)
    calls = []

    def deny_shell(command, *args, **kwargs):
        calls.append(command)
        decision = fence.classify_action('Bash', {'command': command}, str(root))
        if decision.blocked:
            refusals.append(decision.reason)
            raise RuntimeError('AUDIT FENCE REFUSAL: ' + decision.reason)
        raise AssertionError('This audit never executes a shell command')

    # Candidate: the central text writer and both imported common-shell aliases.
    # We test this candidate to reject it, not to present it as a working bridge.
    with patch.object(native_io, 'write_text', side_effect=deny_write), \
            patch.object(native_io, 'confirm_ask', return_value=True), \
            patch('aider.coders.base_coder.run_cmd', side_effect=deny_shell), \
            patch('aider.commands.run_cmd', side_effect=deny_shell):
        reached_subprocess = []
        if scenario == 'apply_updates':
            coder.abs_fnames.add(str(protected))
            coder.partial_response_content = '.claude/audit-target.txt\n```text\nchanged\n```\n'
            coder.choose_fence()
            try:
                coder.apply_updates()
            except RuntimeError as exc:
                assert 'AUDIT FENCE REFUSAL' in str(exc)
        elif scenario in ('handle_shell', 'cmd_run'):
            try:
                if scenario == 'handle_shell':
                    coder.handle_shell_commands('git push', None)
                else:
                    coder.commands.cmd_run('git push')
            except RuntimeError as exc:
                assert 'AUDIT FENCE REFUSAL' in str(exc)
        elif scenario == 'prepare_touch':
            coder.allowed_to_edit('.claude/audit-target.txt')
        elif scenario == 'patch_delete':
            from aider.coders.patch_coder import ActionType, PatchAction
            patch_coder = Coder.create(from_coder=coder, edit_format='patch',
                                       summarize_from_coder=False)
            patch_coder.apply_edits([('.claude/audit-target.txt',
                                     PatchAction(ActionType.DELETE, '.claude/audit-target.txt'))])
        elif scenario == 'save':
            coder.preproc_user_input('/save ' + str(protected))
        elif scenario == 'git':
            def record_process(command, **kwargs):
                reached_subprocess.append(command)
                return SimpleNamespace(stdout='not executed')
            with patch('aider.commands.subprocess.run', side_effect=record_process):
                coder.preproc_user_input('/git push')
        elif scenario == 'native_voice':
            import numpy as np
            from aider import voice

            recorder = voice.Voice.__new__(voice.Voice)
            recorder.device_id = None
            recorder.audio_format = 'wav'
            recorder.sd = SimpleNamespace(
                query_devices=lambda *a: {'default_samplerate': 16000},
                InputStream=lambda **kw: contextlib.nullcontext(),
                PortAudioError=RuntimeError)
            recorder.callback = lambda *a: None
            blocked_opens = []
            target = os.path.normcase(str(protected))

            def guard_open(event, args):
                if event != 'open' or not isinstance(args[0], (str, bytes)):
                    return
                if os.path.normcase(os.fsdecode(args[0])) != target:
                    return
                mode, flags = args[1:]
                if (mode and any(c in mode for c in 'wax+')) or \
                        (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                    blocked_opens.append(str(args[0]))
                    raise RuntimeError('AUDIT FENCE REFUSAL: protected open')

            # Import native dependencies first so this measures the actual
            # write path, not DLL discovery or import-time cache writes.
            protected.unlink()
            sys.addaudithook(guard_open)
            try:
                protected.open('wb')
            except RuntimeError as exc:
                assert 'AUDIT FENCE REFUSAL' in str(exc)
            else:
                raise AssertionError('Python open positive control did not refuse')
            assert blocked_opens == [str(protected)]
            assert not protected.exists()
            blocked_opens.clear()
            with patch.object(voice.tempfile, 'mktemp', return_value=str(protected)), \
                    patch.object(voice, 'prompt',
                                 side_effect=lambda *a, **kw: recorder.q.put(np.zeros((160, 1)))), \
                    patch.object(voice, 'litellm', SimpleNamespace(
                        transcription=lambda **kw: SimpleNamespace(text='synthetic'))):
                coder.commands.voice = recorder
                coder.commands.cmd_voice('')
            assert protected.read_bytes().startswith(b'RIFF')
            assert blocked_opens == [], blocked_opens
        else:
            raise AssertionError(scenario)

    print(json.dumps({'scenario': scenario, 'version': importlib.metadata.version('aider-chat'),
                      'coder_type': type(coder).__name__, 'refused': bool(refusals),
                      'exists': protected.exists(),
                      'unchanged': protected.exists() and protected.read_bytes() == b'original\n',
                      'shell_guard_calls': calls, 'subprocess_calls': reached_subprocess,
                      'io_output': output.getvalue()}))


if __name__ == '__main__':
    main()
