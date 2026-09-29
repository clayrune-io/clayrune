#!/usr/bin/env python
"""Find installed SKILL.md files broken by the reframer duplicate-frontmatter
bug (MC-890) and show the proposed fix.

reframe_exploration_to_skill() (mc/distiller.py) used to feed the model's raw
output straight into a frontmatter splitter that only recognized a literal
leading ``---`` fence. When the model instead emitted a bare ``name:``/
``description:`` header with no fence, that header survived untouched as
body text, the caller's own real frontmatter (built from the correctly
extracted name) landed in front of it, and the description fallback picked
up the next markdown heading instead of the real TRIGGER line sitting
unparsed in the body. Fixed in mc/distiller.py (_split_frontmatter,
reframe_exploration_to_skill) — this tool repairs the skills that were
already installed before that fix landed.

Two independent symptoms, either one flags a file:
  - duplicate_header_in_body   — a second, unfenced name:/description: block
                                  sitting at the top of the body.
  - placeholder_description    — the frontmatter description is literally the
                                  body's own first heading (e.g. "Operating
                                  procedure", or a rename target used as a
                                  title) rather than a real TRIGGER line, or
                                  is too short (<12 chars) to be one.

The proposed fix always reuses whatever real "TRIGGER when ..." line already
exists somewhere in the file (leaked into the duplicate header, or sitting
as plain body prose) — this system's convention is that every skill
description starts with that phrase, so if one is present anywhere it is the
correct value, not a guess.

DRY RUN BY DEFAULT — prints every finding and its proposed fix, writes
nothing. Pass --apply to write. Do NOT run --apply against anything under
~/.claude/skills/ without a human decision; global skills are shared across
every project on the machine.

    python tools/repair-reframed-skill-frontmatter.py                # dry run, all scopes
    python tools/repair-reframed-skill-frontmatter.py --scope global # only ~/.claude/skills
    python tools/repair-reframed-skill-frontmatter.py --apply        # write fixes
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Skill bodies carry non-ASCII punctuation (em dashes, curly quotes); the
# Windows console's default cp1252 stdout mangles it into "?" — force utf-8
# so the printed report is readable, not just the file itself.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[union-attr]

# Sidecar files under data/projects/ that are not project records (same
# exclusion list distiller.py / project_routes.py apply to load_projects()).
_EXCLUDED_SIDECAR_SUFFIXES = (
    '_agent_log.json', '_scribe_stats.json', '_router_stats.json',
    '_skill_stats.json', '_skill_stats_summary.json',
    '_topics.json', '_topic_state.json',
)

_RE_UNFENCED_FM_LINE = re.compile(r'^(name|description):\s*(.*)$')
_RE_TRIGGER_LINE = re.compile(r'TRIGGER\s+when\b.*', re.IGNORECASE)


def _iter_project_paths(data_dir: Path):
    """Yield (project_id, project_path) for every real project record."""
    for f in sorted(data_dir.glob('*.json')):
        if any(f.name.endswith(suf) for suf in _EXCLUDED_SIDECAR_SUFFIXES):
            continue
        try:
            rec = json.loads(f.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'  ! cannot read {f.name}: {e}')
            continue
        if not isinstance(rec, dict):
            continue
        pp = rec.get('project_path')
        if pp:
            yield f.stem, Path(pp)


def _iter_skill_md(scope: str) -> list[tuple[str, Path]]:
    """Yield (scope_label, SKILL.md path) for installed skills in scope.

    scope: 'global' | 'project' | 'both'
    """
    out: list[tuple[str, Path]] = []
    if scope in ('global', 'both'):
        global_dir = Path.home() / '.claude' / 'skills'
        if global_dir.is_dir():
            out += [('global', p) for p in sorted(global_dir.glob('*/SKILL.md'))]
    if scope in ('project', 'both'):
        data_dir = REPO_ROOT / 'data' / 'projects'
        if data_dir.is_dir():
            for project_id, pp in _iter_project_paths(data_dir):
                skills_dir = pp / '.claude' / 'skills'
                if skills_dir.is_dir():
                    out += [(f'project:{project_id}', p)
                            for p in sorted(skills_dir.glob('*/SKILL.md'))]
    return out


def _split_real_frontmatter(text: str) -> tuple[dict, str] | None:
    """Split a properly `---`-fenced frontmatter block from an installed
    SKILL.md. Returns None if the file has no such block — every skill
    write_skill() ever wrote has one, so a miss means this isn't a file we
    know how to repair."""
    if not text.startswith('---'):
        return None
    end = text.find('\n---', 4)
    if end < 0:
        return None
    fm: dict[str, str] = {}
    for ln in text[4:end].splitlines():
        if ':' not in ln:
            continue
        k, v = ln.split(':', 1)
        fm[k.strip()] = v.strip().strip('"').strip("'")
    body = text[end + 4:].lstrip('\n')
    return fm, body


def _leading_unfenced_header(body: str) -> tuple[dict, int]:
    """Detect a leaked, unfenced name:/description: block at the top of an
    already-frontmatter-stripped body. Returns (fields, line_count_to_strip)."""
    lines = body.splitlines()
    i = 0
    fields: dict[str, str] = {}
    while i < len(lines):
        stripped = lines[i].strip()
        # Blank lines and a stray bare `---` (the model's own incomplete
        # second frontmatter attempt, seen in the real
        # distinguish-health-timeout-from-outage cohort file — an unmatched
        # opening fence with no closing one) are skipped, not treated as the
        # end of the header.
        if not stripped or stripped == '---':
            i += 1
            continue
        m = _RE_UNFENCED_FM_LINE.match(stripped)
        if not m:
            break
        fields[m.group(1)] = m.group(2).strip().strip('"').strip("'")
        i += 1
    if not fields:
        return {}, 0  # nothing but blanks/dashes before a real heading — not a leak
    return fields, i


def _find_trigger_line(*texts: str) -> str | None:
    """First 'TRIGGER when ...' sentence found across the given texts, with
    any leading 'description:' key stripped. This system's convention is
    that every skill description starts with this phrase, so wherever one
    survives in the file, it is the correct value."""
    for text in texts:
        for ln in text.splitlines():
            m = _RE_TRIGGER_LINE.search(ln)
            if m:
                return m.group(0).strip()
    return None


def analyze(path: Path) -> dict | None:
    """Return a finding dict, or None if this SKILL.md needs no repair."""
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return {'path': str(path), 'error': f'unreadable: {e}'}

    split = _split_real_frontmatter(text)
    if split is None:
        return None  # not a well-formed installed skill; not ours to touch
    fm, body = split

    # MC-890 scope: this bug lives specifically in reframe_exploration_to_
    # skill() (mc/distiller.py), stamped `promoted_from: exploration-
    # reframed`. Skills from other paths — preferences, direct skill-render,
    # hand-authored — are out of scope: their descriptions legitimately CAN
    # equal a body heading (that's just how a preference title reads), so
    # checking that shape project-wide floods the report with false
    # positives that have nothing to do with this bug.
    if fm.get('promoted_from') != 'exploration-reframed':
        return None

    name = fm.get('name', path.parent.name)
    description = (fm.get('description') or '').strip()

    dup_fields, dup_lines = _leading_unfenced_header(body)
    has_dup = bool(dup_fields)

    issues = []
    if has_dup:
        issues.append('duplicate_header_in_body')
    if not description or len(description) < 12:
        issues.append('description_too_short')
    elif not description.upper().startswith('TRIGGER'):
        # The reframe preamble requires description to start "TRIGGER
        # when ..." (mc/distiller.py _REFRAME_EXPLORATION_TO_SKILL_PREAMBLE).
        # Anything else installed under this provenance is the bug's
        # signature, whether it's a generic section title ("Operating
        # procedure") or a rename target used as a heading.
        issues.append('placeholder_description')
    if not issues:
        return None

    proposed_description = (
        dup_fields.get('description')
        or _find_trigger_line(body)
        or None
    )

    return {
        'path': str(path),
        'name': name,
        'current_description': description,
        'issues': issues,
        'proposed_description': proposed_description,
        'body_lines_removed': dup_lines if has_dup else 0,
        'fixable': bool(proposed_description),
    }


def apply_fix(path: Path, finding: dict) -> None:
    text = path.read_text(encoding='utf-8', errors='replace')
    fm, body = _split_real_frontmatter(text)  # type: ignore[misc]
    if finding['body_lines_removed']:
        body = '\n'.join(body.splitlines()[finding['body_lines_removed']:]).lstrip('\n')
    fm['description'] = finding['proposed_description']
    lines = ['---']
    for k, v in fm.items():
        lines.append(f'{k}: {v}')
    lines.append('---')
    new_text = '\n'.join(lines) + '\n\n' + body
    tmp = path.with_suffix('.md.tmp')
    tmp.write_text(new_text, encoding='utf-8')
    tmp.replace(path)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--scope', choices=['global', 'project', 'both'], default='both',
                    help='which installed-skill trees to scan (default: both)')
    ap.add_argument('--apply', action='store_true',
                    help='write fixes. Without this the run is a dry run. '
                         'Refuses --apply on global scope unless --confirm-global '
                         'is also passed.')
    ap.add_argument('--confirm-global', action='store_true',
                    help='required alongside --apply to touch ~/.claude/skills/ '
                         '(shared across every project on the machine).')
    args = ap.parse_args()

    targets = _iter_skill_md(args.scope)
    if not targets:
        print(f'no installed SKILL.md files found for scope={args.scope}')
        return 0

    print(f'{"APPLY" if args.apply else "DRY RUN"} — scanning {len(targets)} '
          f'installed SKILL.md file(s), scope={args.scope}\n')

    findings = []
    for scope_label, path in targets:
        finding = analyze(path)
        if finding is None:
            continue
        finding['scope_label'] = scope_label
        findings.append(finding)

    if not findings:
        print('no broken frontmatter found — nothing to repair')
        return 0

    for f in findings:
        print(f"[{f['scope_label']}] {f['name']}")
        print(f"  path: {f['path']}")
        if 'error' in f:
            print(f"  ! {f['error']}")
            continue
        print(f"  issues: {', '.join(f['issues'])}")
        print(f"  current description: {f['current_description']!r}")
        if f['proposed_description']:
            print(f"  proposed description: {f['proposed_description']!r}")
        else:
            print('  proposed description: NONE FOUND — needs a human rewrite, not auto-fixable')
        if f['body_lines_removed']:
            print(f"  body: strip {f['body_lines_removed']} leaked frontmatter "
                  f"line(s) from the top")
        print()

    fixable = [f for f in findings if f['fixable']]
    print(f'{len(findings)} file(s) flagged, {len(fixable)} auto-fixable '
          f'(have a real TRIGGER line to restore), '
          f'{len(findings) - len(fixable)} need a human rewrite.')

    if not args.apply:
        print('\nDRY RUN — nothing written. Re-run with --apply to write the '
              f'{len(fixable)} auto-fixable file(s).')
        return 0

    global_fixable = [f for f in fixable if f['scope_label'] == 'global']
    if global_fixable and not args.confirm_global:
        print(f'\nREFUSING to --apply: {len(global_fixable)} of the fixable '
              'file(s) are under ~/.claude/skills/ (global, shared across '
              'every project). Pass --confirm-global to include them, or '
              '--scope project to skip global entirely.')
        return 1

    written = 0
    for f in fixable:
        apply_fix(Path(f['path']), f)
        print(f"  wrote {f['path']}")
        written += 1
    print(f'\n{written} file(s) written.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
