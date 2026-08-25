"""Agent character management — Prompt Builder Phase 1.

A "character" is a standard Claude Code subagent file: `<name>.md` with
YAML frontmatter (`name`, `description`) whose body is the subagent's
system prompt verbatim. CC reads them natively from `~/.claude/agents/`
(global) and `<project_path>/.claude/agents/` (project) — Mission Control
only provides the management surface, exactly like the Skills surface
does for `.claude/skills/`. Design: docs/PROMPT_BUILDER_DESIGN.md §3/§5.

Reuses skills.py's frontmatter parse/dump and kebab-case name validation
so the two surfaces never drift on format rules. Writes go ONLY under
`.claude/agents/` in either scope — never DATA_DIR.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import skills as _skills

GLOBAL_AGENTS_DIR = _skills.GLOBAL_AGENTS_DIR

# Characters ride --append-system-prompt in Phase 2 alongside MEMORY +
# rules + activity, under Windows' ~32 KB CreateProcess ceiling — hence a
# hard per-character cap well below it (design §8).
MAX_BODY_BYTES = 6 * 1024

# ── Engine keys (agent types, docs/AGENT_TYPES_DESIGN.md §3) ────────────────
# A character may pin the engine it wants to run on: "Fable for a PRD, Opus for
# market research". All three are OPTIONAL and an absent key means "behave
# exactly as before" — which is the entire migration story for the characters
# that already exist on disk.
#
# They live in the frontmatter rather than a Clayrune-side sidecar so the file
# stays the single artifact: copy it to another machine and the type keeps its
# engine. Claude Code ignores keys it does not know, so the file also stays a
# valid subagent and @-mention / auto-delegate keep working.
ENGINE_KEYS = ('provider', 'model', 'effort')

# ── The name the agent goes by (Ron, 2026-08-22) ────────────────────────────
# `name` is the file stem — an identifier: kebab-case, in URLs, unrenameable.
# `agent_name` is what the agent CALLS ITSELF, and it is chosen by the agent
# rather than typed by the user (see /api/characters/<scope>/<name>/name).
#
# Kept a separate key rather than reusing the display name because the two
# answer different questions. "prd-writer" says what the type is FOR, and the
# picker still needs that to be pickable; a self-chosen name says who is
# speaking, and that is what belongs in a chat header. Overloading one field
# would force a choice between a browsable library and an agent with a name.
AGENT_NAME_KEY = 'agent_name'

# Deliberately generous on charset (people and models pick names with accents,
# apostrophes, spaces) and tight on length — this renders inside a pill.
MAX_AGENT_NAME_LEN = 32

# ── The face it goes by (Ron, 2026-08-24) ───────────────────────────────────
# One emoji. Same home and the same reasoning as ENGINE_KEYS: in the frontmatter
# so the file stays the single artifact, and harmless to a Claude Code that has
# never heard of it.
#
# Capped at a handful of characters rather than one, because a single "emoji" is
# frequently several codepoints — a ZWJ sequence or a skin-tone modifier — and a
# 1-char cap silently truncates 👩‍💻 into 👩. Long enough for those, short
# enough that nobody fits a word in it.
AVATAR_KEY = 'avatar'
# TWO caps, because an avatar has two shapes and one number cannot serve both.
# An emoji stays SHORT — a handful of codepoints for a ZWJ sequence or a skin
# tone, short enough that nobody fits a word in it, which is the only thing
# stopping this becoming a second name field. A `fig:<name>` reference is a
# filename and needs room: a flat 8 truncated `fig:wizard` to `fig:wiza` and
# `fig:guard` to `fig:guar`, producing a face that simply never resolved.
MAX_EMOJI_LEN = 8
MAX_AVATAR_LEN = 40
# A figure from `assets/avatars/`, as opposed to an emoji. A prefix rather than
# a second frontmatter key: the face is ONE fact, and splitting it would double
# the precedence logic at every site that resolves it.
AVATAR_FIG_PREFIX = 'fig:'


def avatar_figure(value):
    """The figure NAME in `fig:<name>`, or '' when this avatar is not a figure.

    Kept deliberately strict — alphanumerics, dash and underscore only. This
    string reaches a filesystem lookup, and a name is a name.
    """
    v = str(value or '').strip()
    if not v.startswith(AVATAR_FIG_PREFIX):
        return ''
    n = v[len(AVATAR_FIG_PREFIX):].strip().lower()
    return n if n and all(c.isalnum() or c in '-_' for c in n) else ''


# ── The toolkit it works with (Ron, 2026-08-24) ─────────────────────────────
# A DECLARATION, never a gate. Claude Code decides which skills it exposes and
# nothing here narrows that — what this does is tell the agent which of them are
# its own (a list of sixty says nothing about who you are; three named ones do)
# and put a type's abilities on its bench card, which is what "abilities are not
# observable enough" was asking for.
#
# Comma-separated rather than a YAML list, for the reason the frontmatter
# already documents: the minimal parser has no list type and hands `['a','b']`
# back as a string that then iterates character by character.
SKILLS_KEY = 'skills'
MAX_SKILLS = 12


def clean_skills(value):
    """Normalise to a de-duplicated list of skill names, order preserved.

    Accepts a comma-separated string (what the file holds) or a list (what a
    JSON caller sends), because both arrive and rejecting either would just move
    the bug to the caller.
    """
    if isinstance(value, (list, tuple)):
        parts = [str(v) for v in value]
    else:
        parts = str(value or '').replace('\n', ',').split(',')
    out = []
    for raw in parts:
        n = ' '.join(str(raw or '').split()).strip().lower()
        if n and n not in out:
            out.append(n)
    return out[:MAX_SKILLS]


def clean_avatar(value):
    """Normalise an avatar, or '' if unusable. Whitespace-stripped, capped.

    Deliberately NOT validated as "is this really an emoji": the emoji set grows
    every year, any allowlist we write is wrong by the next Unicode release, and
    the failure mode of being wrong is refusing a face somebody picked. The cap
    is the real guard — it is what stops this becoming a second name field — so
    it only relaxes for the shape that is checkable: `fig:<name>` resolves
    against a real file, and a bogus one draws nothing rather than a sentence.
    """
    v = ' '.join(str(value or '').split())
    if v.startswith(AVATAR_FIG_PREFIX):
        return v[:MAX_AVATAR_LEN]
    return v[:MAX_EMOJI_LEN]


AVATARS_DIR = None  # wired by server.py; assets/avatars/


def list_figures():
    """Figure names available on this install. Never raises."""
    try:
        from pathlib import Path
        d = Path(AVATARS_DIR) if AVATARS_DIR else None
        if not d or not d.is_dir():
            return []
        return sorted(f.stem for f in d.glob('*.webp'))
    except Exception:
        return []


def clean_agent_name(value):
    """Normalise a self-chosen name, or return '' if it is unusable.

    Collapses whitespace and trims to MAX_AGENT_NAME_LEN. Strips wrapping
    quotes because a model asked for a single word very often answers with
    one in quotes, and a pill reading '"Vector"' looks like a bug.
    """
    v = ' '.join(str(value or '').split())
    # Smart quotes are ASYMMETRIC, so a naive first==last test misses the exact
    # shape a model is most likely to emit ("Vector" with typographic quotes).
    for _open, _close in (('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’')):
        if len(v) >= 2 and v[0] == _open and v[-1] == _close:
            v = v[1:-1].strip()
            break
    v = v.strip('.,;:!')
    # A model that ignores "one word" tends to answer in a sentence; there is
    # no good truncation of that, so refuse rather than pill a fragment.
    if len(v.split()) > 3:
        return ''
    return v[:MAX_AGENT_NAME_LEN]

# Mirrors MC_EFFORT_CHOICES in static/js/modal-manager.js. Validated here so a
# typo is refused at save time — an effort string the CLI does not understand
# is dropped silently downstream, which reads as "the dial does nothing".
VALID_EFFORT = ('low', 'medium', 'high', 'xhigh', 'max')


def _engine_from_meta(meta):
    """Pull the engine keys out of parsed frontmatter, skipping blanks.

    Absent and empty-string are deliberately the same thing: `model: ""` in a
    hand-edited file must not pin the engine to nothing and shadow the project
    default. Only a non-empty value counts as a pin.
    """
    out = {}
    for k in ENGINE_KEYS:
        v = meta.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def project_agents_dir(project_path: str | os.PathLike[str]) -> Path:
    return Path(project_path) / '.claude' / 'agents'


def _scope_dir(scope: str, project_path: str | None) -> Path:
    if scope == 'global':
        return GLOBAL_AGENTS_DIR
    if scope == 'project':
        if not project_path:
            raise ValueError('project_path required for project scope')
        return project_agents_dir(project_path)
    raise ValueError('scope must be global|project')


def _find_file(scope: str, name: str, project_path: str | None) -> Path | None:
    """Locate a character by name. New characters are written top-level as
    `<name>.md`, but CC scans recursively and imported community packs may
    nest files in subfolders — so lookup falls back to a recursive walk
    matching the file stem."""
    d = _scope_dir(scope, project_path)
    direct = d / f'{name}.md'
    if direct.is_file():
        return direct
    if not d.is_dir():
        return None
    try:
        for p in sorted(d.rglob('*.md')):
            if p.is_file() and p.stem == name:
                return p
    except OSError:
        return None
    return None


def _read_one(path: Path, scope: str, project_id: str | None,
              include_body: bool) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return None
    meta, body = _skills.parse_skill_md(text)
    rec: dict[str, Any] = {
        # Frontmatter `name` wins for display; the file stem is the
        # identity used in URLs (it's what delete/read look up).
        'name': path.stem,
        'display_name': str(meta.get('name') or path.stem),
        'description': str(meta.get('description') or ''),
        'scope': scope,
        'file': path.name,
        'size': len(text.encode('utf-8')),
    }
    engine = _engine_from_meta(meta)
    if engine:
        rec['engine'] = engine
    agent_name = clean_agent_name(meta.get(AGENT_NAME_KEY))
    if agent_name:
        rec[AGENT_NAME_KEY] = agent_name
    avatar = clean_avatar(meta.get(AVATAR_KEY))
    if avatar:
        rec[AVATAR_KEY] = avatar
    skills = clean_skills(meta.get(SKILLS_KEY))
    if skills:
        rec[SKILLS_KEY] = skills
    if project_id and scope == 'project':
        rec['project_id'] = project_id
    if include_body:
        rec['body'] = body
    return rec


def _scan_dir(dirpath: Path, scope: str, project_id: str | None,
              include_body: bool) -> list[dict[str, Any]]:
    if not dirpath.is_dir():
        return []
    out: list[dict[str, Any]] = []
    try:
        files = sorted(dirpath.rglob('*.md'))
    except OSError:
        return []
    for p in files:
        if not p.is_file():
            continue
        rec = _read_one(p, scope, project_id, include_body)
        if rec is not None:
            out.append(rec)
    return out


def list_characters(project_path: str | None = None,
                    project_id: str | None = None,
                    include_body: bool = False) -> list[dict[str, Any]]:
    """Global pool + (when a project is given) that project's pool.
    Project-scope entries shadow-flag a same-named global, mirroring the
    Skills surface semantics."""
    items = _scan_dir(GLOBAL_AGENTS_DIR, 'global', None, include_body)
    if project_path:
        proj = _scan_dir(project_agents_dir(project_path), 'project',
                         project_id, include_body)
        proj_names = {r['name'] for r in proj}
        for r in items:
            if r['name'] in proj_names:
                r['shadowed_by_project'] = True
        items = proj + items
    return items


def read_character(scope: str, name: str, project_path: str | None = None,
                   project_id: str | None = None,
                   include_body: bool = True) -> dict[str, Any] | None:
    path = _find_file(scope, name, project_path)
    if path is None:
        return None
    return _read_one(path, scope, project_id, include_body)


def write_character(scope: str, name: str, description: str, body: str,
                    project_path: str | None = None,
                    overwrite: bool = False,
                    engine: dict[str, Any] | None = None,
                    agent_name: str | None = None,
                    avatar: str | None = None,
                    skills: Any = None) -> dict[str, Any]:
    """Create or update `<scope agents dir>/<name>.md`. Raises ValueError on
    bad input, FileExistsError on collision without overwrite.

    `engine` optionally pins provider / model / effort (ENGINE_KEYS). Keys with
    an empty value are DROPPED rather than written blank, so clearing a field in
    the editor removes the pin instead of persisting a falsy one that would
    shadow the project default.
    """
    err = _skills.validate_name(name)
    if err:
        raise ValueError(err)
    description = (description or '').strip()
    if not description:
        raise ValueError('description is required (it drives auto-delegation)')
    body = (body or '').strip()
    if not body:
        raise ValueError('body is required — it is the character\'s system prompt')
    if len(body.encode('utf-8')) > MAX_BODY_BYTES:
        raise ValueError(
            f'body too large (max {MAX_BODY_BYTES // 1024} KB — characters '
            f'ride inside the agent system prompt)')

    existing = _find_file(scope, name, project_path)
    if existing is not None and not overwrite:
        raise FileExistsError(f'character "{name}" already exists in {scope} scope')

    front: dict[str, Any] = {'name': name, 'description': description}
    # None = leave the existing name alone, '' = clear it, a value = set it.
    # The file is rewritten whole on every save, so "leave alone" has to be an
    # explicit carry-forward — not setting the key DELETES it, which is how a
    # plain description edit used to wipe a name the editor never showed.
    if agent_name is None:
        prior = _read_one(existing, scope, None, include_body=False) if existing else None
        carried = (prior or {}).get(AGENT_NAME_KEY)
        if carried:
            front[AGENT_NAME_KEY] = carried
    else:
        cleaned = clean_agent_name(agent_name)
        if cleaned:
            front[AGENT_NAME_KEY] = cleaned
    # Same three-state contract as agent_name: None carries forward, '' clears,
    # a value sets. The file is rewritten whole, so "leave alone" has to be an
    # explicit carry — omitting the key DELETES it, which is how a plain
    # description edit once wiped a name the editor never showed.
    if avatar is None:
        prior = _read_one(existing, scope, None, include_body=False) if existing else None
        carried = (prior or {}).get(AVATAR_KEY)
        if carried:
            front[AVATAR_KEY] = carried
    else:
        cleaned_av = clean_avatar(avatar)
        if cleaned_av:
            front[AVATAR_KEY] = cleaned_av
    # Same three-state contract again: None carries forward, '' or [] clears.
    if skills is None:
        prior = _read_one(existing, scope, None, include_body=False) if existing else None
        carried = (prior or {}).get(SKILLS_KEY)
        if carried:
            front[SKILLS_KEY] = ', '.join(carried)
    else:
        cleaned_sk = clean_skills(skills)
        if cleaned_sk:
            front[SKILLS_KEY] = ', '.join(cleaned_sk)
    for k in ENGINE_KEYS:
        v = (engine or {}).get(k)
        v = v.strip() if isinstance(v, str) else ''
        if not v:
            continue
        if k == 'effort' and v not in VALID_EFFORT:
            raise ValueError(
                f'effort must be one of {", ".join(VALID_EFFORT)} (got {v!r})')
        front[k] = v

    d = _scope_dir(scope, project_path)
    d.mkdir(parents=True, exist_ok=True)
    # Updates land on the file we found (which may be nested); creates go
    # top-level.
    path = existing if existing is not None else d / f'{name}.md'
    text = _skills.dump_skill_md(front,
                                 body + ('\n' if not body.endswith('\n') else ''))
    path.write_text(text, encoding='utf-8')
    rec = _read_one(path, scope, None, include_body=False)
    return rec if rec is not None else {'name': name, 'scope': scope}


def delete_character(scope: str, name: str,
                     project_path: str | None = None) -> bool:
    path = _find_file(scope, name, project_path)
    if path is None:
        return False
    path.unlink()
    return True
