"""Per-agent skill scoping (config `agent_skill_scoping_enabled`, default off).

Skills belong to the AGENT: a character's `skills` frontmatter list
(mc/characters.py SKILLS_KEY) travels with it into every project. The project
adds its own project-local skills (`<project>/.claude/skills/`) on top. For one
session:

    full description  = declared skills  UNION  the project's own skills
    name-only         = every other installed skill (still listed, still
                        callable through the Skill tool — no ability is lost,
                        only the ~220 chars of description per skill that the
                        CLI re-reads on every turn)

A character that declares no skills is left exactly as it is today: no
override is produced, so nothing is demoted. Evidence and the token arithmetic:
docs/TOKEN_ECONOMY_AUDIT.md §4.4.

Claude gets this as `skillOverrides` in a per-launch `--settings` file
(ClaudeRuntime.build_command); the non-Claude catalog block
(agent_routes._skills_catalog_block) applies the same `effective_full_set`.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

import mc.skills as _skills
from mc.core import _log

CONFIG_KEY = 'agent_skill_scoping_enabled'
NAME_ONLY = 'name-only'  # never 'off': that hides the skill from the model


def _installed(project_path: Optional[str], project_id: Optional[str],
               list_skills: Optional[Callable[..., list]] = None) -> List[dict]:
    fn = list_skills or _skills.list_skills
    return [s for s in fn(project_path or None, project_id)
            if s.get('scope') != 'archive']


PREFERENCE_PREFIX = 'preference-'


def effective_full_set(declared: Iterable[str], skills: List[dict]) -> Set[str]:
    """Names that keep their full description: declared ∪ project-local ∪
    every `preference-*` skill.

    Preference skills are the user's conduct rules, not a toolkit. Demoting
    them would silently drop a rule from any agent with a declared list, so
    they are never scoped (Ron, 2026-09-18).
    """
    full = {str(n).strip().lower() for n in declared or [] if str(n).strip()}
    full.update(str(s.get('name') or '').lower()
                for s in skills if s.get('scope') == 'project')
    full.update(str(s.get('name') or '').lower() for s in skills
                if str(s.get('name') or '').lower().startswith(PREFERENCE_PREFIX))
    return full


def skill_overrides(project_path: Optional[str], project_id: Optional[str],
                    declared: Iterable[str],
                    list_skills: Optional[Callable[..., list]] = None
                    ) -> Dict[str, str]:
    """`{skill_name: 'name-only'}` for every installed skill outside the
    effective set, or `{}` when nothing should change.

    `{}` for an empty `declared` is the compatibility contract: an agent nobody
    assigned skills to behaves exactly as before. Never raises — scoping is an
    optimisation, so any failure to enumerate skills means "demote nothing".
    """
    declared = [d for d in (declared or []) if str(d).strip()]
    if not declared:
        return {}
    try:
        skills = _installed(project_path, project_id, list_skills)
    except Exception as e:
        _log(f"[skill-scoping] could not list skills, demoting none: {e}", flush=True)
        return {}
    full = effective_full_set(declared, skills)
    out: Dict[str, str] = {}
    for s in skills:
        name = str(s.get('name') or '')
        if name and name.lower() not in full:
            out[name] = NAME_ONLY
    return out


def scoped_settings_path(base_settings: Optional[Path],
                         overrides: Dict[str, str],
                         out_dir: Path) -> Optional[Path]:
    """Write `base_settings` (the guardrail file, if any) + `skillOverrides`
    to a content-addressed file and return its path; None if it can't be written.

    `--settings` takes ONE file, so the overrides have to ride in the same file
    as the guardrail hooks rather than as a second flag. Content-addressed so an
    identical (character, project) always resolves to the identical path — a
    respawn, revive or rollover therefore hands the CLI byte-for-byte the same
    settings, and concurrent sessions never rewrite each other's file.
    """
    if not overrides:
        return None
    try:
        data: Dict[str, Any] = {}
        if base_settings is not None:
            loaded = json.loads(Path(base_settings).read_text(encoding='utf-8'))
            if isinstance(loaded, dict):
                data = loaded
        data['skillOverrides'] = dict(sorted(overrides.items()))
        text = json.dumps(data, indent=2, sort_keys=True)
        digest = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f'claude-settings-{digest}.json'
        # Rewrite on ANY mismatch, not just absence: this file carries the
        # guardrail hooks, and the base file self-heals at every boot
        # (install_hooks.py). A reused-if-present scoped copy would keep an
        # agent's edit (hooks stripped, permissions added) across restarts.
        try:
            current = path.read_text(encoding='utf-8') if path.is_file() else None
        except OSError:
            current = None
        if current != text:
            # Per-writer temp name: two first launches of the same combination
            # must not truncate each other's half-written file.
            tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
            tmp.write_text(text, encoding='utf-8')
            tmp.replace(path)
        return path
    except Exception as e:
        _log(f"[skill-scoping] could not write scoped settings: {e}", flush=True)
        return None
