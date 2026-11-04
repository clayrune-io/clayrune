"""Dedicated, one-turn Aider entry. Never invokes aider.main or user config."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from types import SimpleNamespace
from typing import Any, Sequence

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def create_coder(guard: Any, *, model: Any, edit_format: str = 'whole',
                 read: Sequence[str] = (), dry_run: bool = False) -> Any:
    from aider.coders import Coder
    from aider.io import InputOutput
    native_io = InputOutput(pretty=False, yes=True, fancy_input=False,
                            input_history_file=None, chat_history_file=None,
                            llm_history_file=None, notifications=False,
                            dry_run=dry_run)
    coder = Coder.create(main_model=model, edit_format=edit_format, io=native_io,
                         use_git=True, read_only_fnames=list(read), dry_run=dry_run,
                         stream=False)
    coder.root = guard.cwd
    return coder


def probe(guard: Any) -> None:
    """Actual installed guard and fence calls, with no LLM request/tool run."""
    from mc.aider_guard import AiderRefusal
    from aider.coders import Coder
    from aider.io import InputOutput
    model = SimpleNamespace(reasoning_tag=None, streaming=False, cache_control=False,
                            info={}, max_chat_history_tokens=1024, token_count=lambda v: 1)
    model.weak_model = model
    io = InputOutput(pretty=False, yes=True, fancy_input=False)
    for fmt in ('whole', 'diff'):
        Coder.create(main_model=model, edit_format=fmt, io=io, use_git=False)
    if guard.refusals:
        raise RuntimeError('Aider initialization encountered an unexpected guard refusal')
    for tool, args in (
        ('Bash', {'command': 'git push'}),
        ('Write', {'file_path': str(Path(guard.cwd) / '.claude' / 'aider-probe')}),
    ):
        try:
            guard.check(tool, args)
        except AiderRefusal:
            continue
        raise RuntimeError(f'Aider fence probe did not refuse {tool}')
    guard.check('Bash', {'command': 'git status'})
    print(json.dumps({'restricted_aider': '0.86.2', 'guard': 'ready'}))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--self-test', action='store_true')
        parser.add_argument('--no-stream', action='store_true')
        parser.add_argument('--yes', action='store_true')
        parser.add_argument('--no-auto-commits', action='store_true')
        parser.add_argument('--model', default='gpt-4o')
        parser.add_argument('--edit-format', choices=('whole', 'diff'), default='whole')
        parser.add_argument('--read', action='append', default=[])
        parser.add_argument('--message-file')
        parser.add_argument('--dry-run', action='store_true')
        args = parser.parse_args(argv)
        if not args.self_test and not args.message_file:
            parser.error('restricted Aider requires --message-file')
        os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
        # Ignore installed bytecode and don't write fresh bytecode.
        sys.dont_write_bytecode = True
        bytecode_prefix = Path(tempfile.gettempdir()) / ('clayrune-aider-no-bytecode-' + uuid.uuid4().hex)
        if bytecode_prefix.exists():
            raise RuntimeError('unexpected bytecode prefix already exists')
        sys.pycache_prefix = str(bytecode_prefix)
        from mc.aider_source_pin import verify_installed
        verify_installed()
        from mc.aider_guard import Guard
        guard = Guard(os.getcwd())
        guard.install()
        if args.self_test:
            probe(guard)
            return 0
        message = Path(args.message_file).read_text(encoding='utf-8')
        if not message.strip():
            guard.refuse('empty task; interactive input is outside the reviewed allowlist')
        from aider.models import Model
        model = Model(args.model, weak_model=False, editor_model=False)
        model.weak_model = model
        model.editor_model = model
        coder = create_coder(guard, model=model, edit_format=args.edit_format,
                             read=args.read, dry_run=args.dry_run)
        coder.run(with_message=message)
        # Upstream catches some edit/run errors. Never turn that into success.
        return 2 if guard.refusals else 0
    except Exception as exc:
        print(f'Aider restricted adapter failed closed: {exc}', file=sys.stderr, flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
