"""Own Aider's prompt files until the consuming process finishes."""
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from mc.core import _log


def prepare(command: list[str], message: str, context: str) -> Callable[[], None]:
    directory = TemporaryDirectory(prefix='mc_aider_')
    root = Path(directory.name)

    def cleanup() -> None:
        try:
            directory.cleanup()
        except OSError as exc:
            _log(f'[runtime:aider] prompt file cleanup failed: {exc}')

    try:
        if context:
            context_file = root / 'context.md'
            context_file.write_text(context, encoding='utf-8')
            command.extend(['--read', str(context_file)])
        message_file = root / 'message.md'
        message_file.write_text(message, encoding='utf-8')
        command.extend(['--message-file', str(message_file)])
    except BaseException:
        cleanup()
        raise
    return cleanup
