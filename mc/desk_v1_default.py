"""One-time Desk v1 default rollout for fresh and previously saved configs."""
import sys
from pathlib import Path
from typing import Any

from mc.atomic_json import write_json_atomic


def migrate_desk_v1_default(config: dict[str, Any], path: Path) -> dict[str, Any]:
    """Replace historically pinned defaults once, then respect every choice.

    Settings saved the entire config, so an unmarked false cannot distinguish
    an explicit choice from the old default. The marker records this rollout,
    not a user-editable setting. Publish it in memory only after it is durable.
    """
    if 'desk_v1_default_migrated' in config:
        return config
    migrated = {**config, 'desk_v1': True, 'desk_v1_default_migrated': True}
    try:
        write_json_atomic(path, migrated, indent=2, ensure_ascii=False)
    except Exception as e:
        # Config loading runs before mc.core._log is wired to state.CONFIG.
        print(f"[config] failed to persist Desk v1 default migration to {path}: {e}",
              file=sys.stderr, flush=True)
        return config
    return migrated
